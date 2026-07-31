"""Accessors and mode-switching helpers for the live striqt source object.

Two responsibilities:

* getattr-based accessors (``get_device``, ``get_rx_stream``,
  ``enable_stream``, ``stream_buffers_for``, etc.) that reach into a striqt
  source's semi-private attributes without hardcoding one naming convention.
  The installed striqt build (pinned v0.7.0) can expose different
  attribute/method names than the newer API of the `striqt/` tree vendored in
  this repo, so these shims probe several candidate names — the same
  defensive pattern used in `live/legacy/striqt_server_TCP.py`. Extracted
  verbatim from `striqt_web_server.py`.
* :func:`finite_capture_mode`, the context manager that lets a recording
  sweep run safely on the SAME source object the live viewer already has open
  in ``gapless=True`` mode. The live view opens gapless because striqt treats
  any receive overflow there as fatal and forbids retries; a recording sweep
  analyzes and archives data *between* captures, so the stream is guaranteed
  to overflow in those gaps by construction. Inside the context, the source's
  spec is swapped to ``gapless=False, receive_retries=...`` (and optionally a
  different ``array_backend``) so striqt tolerates the expected
  between-capture overflow; the live spec is restored on exit, including when
  the sweep raises.

Everything from ``REQUIRED_SOURCE_API`` onward targets the INSTALLED striqt
API (``arm_spec``/``_read_stream``/``setup_spec``) rather than the vendored
tree's renamed equivalents (see `INSTALLED_STRIQT_API.txt`), and fails loudly
(``missing_source_api``, explicit ``RuntimeError``s) on an unexpected API
rather than silently no-opping — a ``hasattr()`` guard around a method that
only exists upstream was previously how a recording-overflow bug went
unnoticed.
"""
from __future__ import annotations

import contextlib
import os

import numpy as np

from . import state
from .constants import ENVELOPE_QUERY_GROUPS

# ---------------------------------------------------------------------------
# striqt hardware shims
# (match the getattr pattern used in legacy/striqt_server_TCP.py so this works
#  against the installed striqt build which may differ from the vendored source)
# ---------------------------------------------------------------------------

def seal_open_fds_for_exec():
    """Set close-on-exec on every currently open non-stdio descriptor.

    Some AIR-T driver descriptors are created inheritable.  Python's
    ``close_fds=True`` did not close them in the radio's deployed runtime, so a
    journal follower retained XDMA after the live stream released it.  CLOEXEC
    is enforced by the kernel and leaves the parent process completely
    unchanged.  The helper is intentionally device-agnostic: server sockets,
    USB/IIO handles, and future SDR backends must not leak into child tools
    either.

    Returns:
        None. Silently skips descriptors it fails to read or set (races with
        another thread closing the same fd are expected and harmless).
    """
    fd_dir = "/proc/self/fd"
    try:
        names = os.listdir(fd_dir)
    except OSError:
        return
    for name in names:
        try:
            fd = int(name)
            if fd > 2:
                os.set_inheritable(fd, False)
        except (OSError, ValueError):
            # The directory iterator itself and other threads can close an fd
            # between listdir() and fcntl(); that race is harmless.
            pass

def get_device(source):
    """Return the underlying SoapySDR device object for a striqt source.

    Args:
        source: An open striqt source object.

    Returns:
        The device object (checking ``_device`` then ``device``), or None if
        neither attribute is set.
    """
    return getattr(source, "_device", getattr(source, "device", None))

def get_rx_stream(source):
    """Return the striqt RX stream wrapper for a source.

    Args:
        source: An open striqt source object.

    Returns:
        The RX stream object (checking ``_rx_stream`` then ``rx_stream``), or
        None if neither attribute is set.
    """
    return getattr(source, "_rx_stream", getattr(source, "rx_stream", None))

def get_stream_ports(source):
    """Return the RX port order the source's stream was set up with.

    Args:
        source: An open striqt source object.

    Returns:
        tuple: The stream's ``ports`` attribute, or ``state.CHANNELS`` if the
        stream has none (e.g. not yet opened).
    """
    return tuple(getattr(get_rx_stream(source), "ports", state.CHANNELS))

def get_stream_mtu(source):
    """Return the RX stream's MTU (max samples returned per read call).

    Tries several attribute names on the stream wrapper first, then falls
    back to asking the device directly (``getStreamMTU``/``get_stream_mtu``).

    Args:
        source: An open striqt source object.

    Returns:
        int or None: The MTU in samples, or None if it could not be
        determined by any of the above.
    """
    rx = get_rx_stream(source)
    if rx is None:
        return None
    for name in ("mtu", "_mtu", "stream_mtu"):
        val = getattr(rx, name, None)
        if val is not None:
            try:
                return int(val)
            except Exception:
                pass
    stream = getattr(rx, "stream", None)
    dev    = get_device(source)
    if dev is not None and stream is not None:
        for meth in ("getStreamMTU", "get_stream_mtu"):
            fn = getattr(dev, meth, None)
            if fn is not None:
                try:
                    return int(fn(stream))
                except Exception:
                    pass
    return None

def open_stream(source):
    """Open `source`'s RX stream if it is not already open, then seal fds.

    Args:
        source: An open striqt source object.

    Raises:
        RuntimeError: If `source` has no RX stream/device to open.
    """
    rx  = get_rx_stream(source)
    dev = get_device(source)
    if rx is None or dev is None:
        raise RuntimeError("striqt source has no RX stream/device")
    if getattr(rx, "stream", None) is None:
        rx.open(dev)
    seal_open_fds_for_exec()

def enable_stream(source, enabled):
    """Activate or deactivate `source`'s RX stream.

    Prefers striqt's own ``RxStream.enable()`` wrapper, which — besides
    calling into SoapySDR — maintains ``RxStream._enabled``; bypassing it
    left that flag false after activation and caused later arm/recovery
    transitions to issue duplicate activate/deactivate calls (a failed
    duplicate activation reports XDMA EBUSY on SoapyAIRT). Falls back to the
    raw ``activateStream``/``deactivateStream`` device calls, retrying
    no-args if the 3-arg form raises ``TypeError``, when the wrapper is
    unavailable.

    Args:
        source: An open striqt source object.
        enabled: True to activate the stream, False to deactivate it.

    Raises:
        Exception: The last driver error encountered, if every enable/disable
            path failed. No-ops (returns) if the source has no stream/device.
    """
    rx     = get_rx_stream(source)
    if rx is None:
        return
    dev    = get_device(source)
    stream = getattr(rx, "stream", None)
    if dev is None or stream is None:
        return
    # Prefer striqt's stream wrapper.  Besides invoking SoapySDR it maintains
    # RxStream._enabled; bypassing it left that flag false after activation and
    # made later arm/recovery transitions issue duplicate activate/deactivate
    # calls.  On SoapyAIRT a failed duplicate activation reports XDMA EBUSY.
    wrapper_enable = getattr(rx, "enable", None)
    if callable(wrapper_enable):
        wrapper_enable(dev, bool(enabled))
        if enabled:
            seal_open_fds_for_exec()
        return
    methods = (("activateStream", "activate_stream") if enabled
               else ("deactivateStream", "deactivate_stream"))
    last_error = None
    for meth in methods:
        fn = getattr(dev, meth, None)
        if fn is not None:
            try:
                fn(stream)
                if enabled:
                    seal_open_fds_for_exec()
                return
            except TypeError:
                try:
                    fn(stream, 0, 0, 0)
                    if enabled:
                        seal_open_fds_for_exec()
                    return
                except Exception as exc:
                    last_error = exc
            except Exception as exc:
                last_error = exc
    if last_error is not None:
        raise last_error

def close_source(source):
    """Best-effort full teardown of a striqt source.

    Deactivates the RX stream, closes the RX stream, then closes the source
    itself, ignoring any exception from each step so a failure at one stage
    does not block the remaining teardown steps.

    Args:
        source: An open striqt source object.
    """
    for action in [lambda: enable_stream(source, False),
                   lambda: _close_rx_stream(source),
                   lambda: source.close()]:
        try:
            action()
        except Exception:
            pass

def _close_rx_stream(source):
    """Close `source`'s RX stream via the striqt stream wrapper, if open."""
    rx = get_rx_stream(source)
    if rx is not None:
        dev = get_device(source)
        if dev is not None and getattr(rx, "stream", None) is not None:
            rx.close(dev)

def stream_buffers_for(source, samples):
    """Reorder a per-channel sample array into the stream's own port order.

    `samples` is indexed by ``state.CHANNELS`` order; SoapySDR's
    ``readStream`` expects buffers in the stream's own port order, which can
    differ.

    Args:
        source: An open striqt source object.
        samples: Per-channel sample array indexed in ``state.CHANNELS`` order.

    Returns:
        tuple: ``(buffers, ports)`` where `buffers` is a list of float32
        views into `samples`, one per stream port, and `ports` is the
        stream's port tuple.
    """
    rx    = get_rx_stream(source)
    ports = tuple(getattr(rx, "ports", state.CHANNELS))
    return [samples[state.CHANNELS.index(p)].view(np.float32) for p in ports], ports


# ---------------------------------------------------------------------------
# Source-spec accessors + the finite-capture (recording) mode swap
#
# These target the INSTALLED striqt — v0.7.0, pinned by commit in install_linda.sh —
# whose source objects expose __setup__/setup_spec/arm_spec/_read_stream. The
# NEWER striqt snapshot vendored under striqt/ renamed every one of those (see
# INSTALLED_STRIQT_API.txt). Code written against the vendored tree therefore
# fails here, so everything below fails LOUDLY on an unexpected API rather than
# degrading into a silent no-op — which is exactly how the recording overflow
# bug hid: a hasattr() guard around a method that only exists upstream.
# ---------------------------------------------------------------------------

#: Attributes live/core drives directly on a striqt source object.
REQUIRED_SOURCE_API = ("arm_spec", "_read_stream", "setup_spec")


def missing_source_api(source):
    """Find which names in REQUIRED_SOURCE_API `source` does not provide.

    A non-empty result means the installed striqt is not the API live/core is
    written against; the caller should say so plainly instead of waiting for a
    downstream AttributeError.

    Args:
        source: An open striqt source object.

    Returns:
        tuple: Names from ``REQUIRED_SOURCE_API`` that are missing or None on
        `source`. Empty if the source provides all of them.
    """
    return tuple(name for name in REQUIRED_SOURCE_API
                 if getattr(source, name, None) is None)


def get_setup_spec(source):
    """Return the immutable source spec `source` was opened with.

    Args:
        source: An open striqt source object.

    Returns:
        The spec object (checking ``__setup__``, then ``setup_spec``, then
        ``spec``), or None if none of those attributes are set.
    """
    for name in ("__setup__", "setup_spec", "spec"):
        spec = getattr(source, name, None)
        if spec is not None:
            return spec
    return None


def _set_setup_spec(source, spec):
    """Rebind the source spec striqt consults at acquire time.

    `setup_spec` is a functools.cached_property over `__setup__`, so the
    backing attribute and the instance cache have to move together: striqt
    re-reads `setup_spec` on every read_iq / arm_spec / overlap calculation.

    Args:
        source: An open striqt source object.
        spec: The spec to install as `source`'s active setup spec.

    Raises:
        RuntimeError: If, after the assignment, `source.setup_spec` is not
            `spec` — meaning the installed striqt doesn't expose
            `setup_spec` the way live/core expects.
    """
    source.__setup__ = spec
    source.__dict__["setup_spec"] = spec
    if getattr(source, "setup_spec", None) is not spec:
        raise RuntimeError(
            "striqt source spec swap did not take effect — the installed "
            "striqt does not expose setup_spec the way live/core expects "
            "(see INSTALLED_STRIQT_API.txt)")


def _spec_registry():
    """Look up striqt's spec-to-source map, used to resolve source IDs for sink paths.

    Returns:
        tuple: ``(registry, mapper)`` — the dict-like spec→source registry
        and its optional setter function, both from
        ``striqt.sensor.lib.sources.base``; ``(None, None)`` if that module
        isn't importable.
    """
    try:
        from striqt.sensor.lib.sources import base as _base
    except Exception:
        return None, None
    return getattr(_base, "_source_id_map", None), getattr(_base, "_map_source", None)


def _register_source_spec(source, spec):
    """Make `spec` resolve to `source` in striqt's spec→source registry.

    Sink path formatting looks the sweep's source spec up by identity to get a
    radio ID; a spec striqt has never seen blocks for the lookup timeout and
    then raises. Any spec handed to a sweep must be registered first.

    Args:
        source: The striqt source object `spec` should resolve to.
        spec: The spec to register.

    Returns:
        bool: True if the registry was found and updated, False if striqt's
        registry module was not importable.
    """
    registry, mapper = _spec_registry()
    if registry is None:
        return False
    if callable(mapper):
        mapper(spec, source)
    else:
        registry[spec] = source
    return True


def _unregister_source_spec(spec):
    """Remove `spec`'s entry from striqt's spec→source registry, if present."""
    registry, _ = _spec_registry()
    if registry is not None:
        registry.pop(spec, None)


@contextlib.contextmanager
def finite_capture_mode(source, *, receive_retries=2, array_backend=None):
    """Run a finite (recording) sweep on a source opened for the gapless live view.

    The live viewer opens the radio with ``gapless=True``. In that mode striqt
    treats every receive overflow as fatal — a capture's first read uses
    ``on_overflow='except'`` — and refuses receive retries. But a recording
    sweep analyzes and archives *between* captures, so unread data piles up in
    those gaps and the next read overflows by construction. Under gapless that
    ends the recording after roughly one capture.

    Inside this context the source reports an ordinary finite-capture spec:
    striqt swallows the expected between-capture overflow and retries a
    mid-capture one. ``array_backend`` optionally overrides the analysis array
    module for the sweep — the recording YAML asks for cupy on hardware, but
    the live source spec hardcodes numpy and used to clobber that request the
    same way it clobbered gapless (observed: 4.6 s of CPU analysis per 20 ms
    capture). The live spec is restored on exit, before the viewer resumes,
    including when the sweep raises.

    Args:
        source: The live striqt source object (already open, gapless).
        receive_retries: Retry count to set when swapping off gapless mode.
        array_backend: Optional array module name (e.g. ``"cupy"``) to force
            for the sweep, if different from the live spec's.

    Yields:
        The spec now in force on `source` — the live one unchanged if neither
        gapless nor array_backend needed to change, otherwise the replaced
        (finite-capture) spec.

    Raises:
        RuntimeError: If `source` exposes no setup spec at all.
    """
    live = get_setup_spec(source)
    if live is None:
        raise RuntimeError(
            "striqt source exposes no setup spec — cannot make it safe for "
            "finite captures (see INSTALLED_STRIQT_API.txt)")
    changes = {}
    if getattr(live, "gapless", False):
        changes.update(gapless=False, receive_retries=receive_retries)
    if array_backend and getattr(live, "array_backend", None) != array_backend:
        changes["array_backend"] = str(array_backend)
    if not changes:
        yield live
        return
    if changes.get("array_backend") == "cupy":
        # The source was constructed under numpy, so striqt's one-time cupy
        # setup (pinned-memory pools) never ran. Best-effort — cupy works
        # with default pools too, and a failure here must not stop the sweep.
        try:
            from striqt import waveform as _sw
            _sw.arrays.configure_cupy()
        except Exception:
            pass
    record = live.replace(**changes)
    _register_source_spec(source, record)
    _set_setup_spec(source, record)
    try:
        yield record
    finally:
        _set_setup_spec(source, live)
        _unregister_source_spec(record)


def query_device_envelope(source, groups=None):
    """Ask the open SoapySDR device for its real frequency/gain/rate ranges (P3-3).

    Every step is defensive: a missing method, a failed call, or an
    unexpected range-object shape just drops that one key, leaving the
    profile fallback bound in force for it.

    Args:
        source: An open striqt source object.
        groups: Optional iterable of bound groups to ask about
            (``"freq"``/``"gain"``/``"rate"``, see
            ``constants.ENVELOPE_QUERY_GROUPS``). ``None`` asks for all
            three. Callers pass a subset when a profile's fallback is more
            truthful than the driver for some bound — the AIR-T's calibrated
            gain window being the standing example.

    Returns:
        dict: A partial envelope — only the keys the device actually
        answered (``freq_min``/``freq_max``, ``gain_min``/``gain_max``,
        ``rate_min``/``rate_max``, plus ``rate_list`` when the driver
        enumerates discrete sample rates) — meant to be merged over the
        profile fallback by ``SharedConfig.set_envelope``. Empty if the
        source has no device.
    """
    dev = get_device(source)
    if dev is None:
        return {}
    want = frozenset(groups) if groups is not None else frozenset(
        ENVELOPE_QUERY_GROUPS)
    try:
        from SoapySDR import SOAPY_SDR_RX as _rx_dir
    except Exception:
        _rx_dir = 1   # SoapySDR's RX direction constant
    ch = state.CHANNELS[0] if state.CHANNELS else 0

    def _bounds(ranges):
        lows, highs = [], []
        for r in ranges:
            try:
                lows.append(float(r.minimum()))
                highs.append(float(r.maximum()))
            except Exception:
                try:
                    lows.append(float(r[0]))
                    highs.append(float(r[1]))
                except Exception:
                    pass
        if lows and highs:
            return min(lows), max(highs)
        return None

    env = {}
    for group, method, lo_key, hi_key in (
        ("freq", "getFrequencyRange",  "freq_min", "freq_max"),
        ("gain", "getGainRange",       "gain_min", "gain_max"),
        ("rate", "getSampleRateRange", "rate_min", "rate_max"),
    ):
        if group not in want:
            continue
        fn = getattr(dev, method, None)
        if fn is None:
            continue
        try:
            ranges = fn(_rx_dir, ch)
        except Exception:
            continue
        if not isinstance(ranges, (list, tuple)):
            ranges = [ranges]   # getGainRange returns a single Range object
        got = _bounds(ranges)
        if got:
            env[lo_key], env[hi_key] = got
    # Discrete rates, when the driver enumerates them. This is the strongest
    # answer available to the question "what can this radio actually run" —
    # `getSampleRateRange` only gives endpoints, and a continuous-looking
    # range routinely hides a driver that accepts a handful of values. When
    # present it overrides the static grid entirely (see dsp.allowed_rates).
    if "rate" in want:
        lister = getattr(dev, "listSampleRates", None)
        if lister is not None:
            try:
                listed = sorted({float(r) for r in lister(_rx_dir, ch)
                                 if float(r) > 0})
            except Exception:
                listed = []
            if listed:
                env["rate_list"] = listed
                # Endpoints implied by the list are more trustworthy than a
                # range the same driver may report loosely.
                env.setdefault("rate_min", listed[0])
                env.setdefault("rate_max", listed[-1])
    return env
