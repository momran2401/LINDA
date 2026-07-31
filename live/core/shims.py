"""striqt hardware accessor shims.

getattr-based accessors that work against the installed striqt build, whose
method/attribute names may differ from the vendored source tree. Extracted
verbatim from striqt_web_server.py.
"""
from __future__ import annotations

import contextlib
import os

import numpy as np

from . import state

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
    return getattr(source, "_device", getattr(source, "device", None))

def get_rx_stream(source):
    return getattr(source, "_rx_stream", getattr(source, "rx_stream", None))

def get_stream_ports(source):
    return tuple(getattr(get_rx_stream(source), "ports", state.CHANNELS))

def get_stream_mtu(source):
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
    rx  = get_rx_stream(source)
    dev = get_device(source)
    if rx is None or dev is None:
        raise RuntimeError("striqt source has no RX stream/device")
    if getattr(rx, "stream", None) is None:
        rx.open(dev)
    seal_open_fds_for_exec()

def enable_stream(source, enabled):
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
    for action in [lambda: enable_stream(source, False),
                   lambda: _close_rx_stream(source),
                   lambda: source.close()]:
        try:
            action()
        except Exception:
            pass

def _close_rx_stream(source):
    rx = get_rx_stream(source)
    if rx is not None:
        dev = get_device(source)
        if dev is not None and getattr(rx, "stream", None) is not None:
            rx.close(dev)

def stream_buffers_for(source, samples):
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
    """Names in REQUIRED_SOURCE_API that `source` does not provide.

    A non-empty result means the installed striqt is not the API live/core is
    written against; the caller should say so plainly instead of waiting for a
    downstream AttributeError.
    """
    return tuple(name for name in REQUIRED_SOURCE_API
                 if getattr(source, name, None) is None)


def get_setup_spec(source):
    """The immutable source spec this source was opened with (None if absent)."""
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
    """
    source.__setup__ = spec
    source.__dict__["setup_spec"] = spec
    if getattr(source, "setup_spec", None) is not spec:
        raise RuntimeError(
            "striqt source spec swap did not take effect — the installed "
            "striqt does not expose setup_spec the way live/core expects "
            "(see INSTALLED_STRIQT_API.txt)")


def _spec_registry():
    """striqt's spec → source map, used to resolve source IDs for sink paths."""
    try:
        from striqt.sensor.lib.sources import base as _base
    except Exception:
        return None, None
    return getattr(_base, "_source_id_map", None), getattr(_base, "_map_source", None)


def _register_source_spec(source, spec):
    """Make `spec` resolve to `source` in striqt's registry.

    Sink path formatting looks the sweep's source spec up by identity to get a
    radio ID; a spec striqt has never seen blocks for the lookup timeout and
    then raises. Any spec handed to a sweep must be registered first.
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

    Yields the spec now in force (the live one when nothing needed changing).
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


def query_device_envelope(source):
    """
    Ask the open SoapySDR device for its real capability ranges (P3-3).
    Returns a partial envelope dict — only the keys the device answered — to
    be merged over the profile fallback by SharedConfig.set_envelope. Every
    step is defensive: a missing method, failed call, or odd range-object
    shape just drops that key (the fallback bound stays in force).
    """
    dev = get_device(source)
    if dev is None:
        return {}
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
    for method, lo_key, hi_key in (
        ("getFrequencyRange",  "freq_min", "freq_max"),
        ("getGainRange",       "gain_min", "gain_max"),
        ("getSampleRateRange", "rate_min", "rate_max"),
    ):
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
    return env
