"""striqt import compatibility layer — the single gateway `live/core` uses to reach striqt.

`core/__init__.py` imports this module first, guaranteeing all three of its
effects happen before any other core module touches striqt or scipy:

1. On the AIR-T's pixi environment, re-execs the current process once with a
   newer libstdc++ on ``LD_LIBRARY_PATH`` (:func:`_ensure_pixi_runtime_libs`),
   before scipy's/striqt's compiled waveform extensions are ever imported.
2. Imports the striqt sensor and analysis stacks defensively. Every other core
   module gets its striqt symbols, plus the ``_SENSOR_OK``/``_ANALYSIS_OK``
   flags, from here — so a missing hardware or analysis dependency degrades
   exactly one way (these two flags), everywhere, instead of raising at
   arbitrary import sites.
3. Applies two runtime monkeypatches that fix bugs in the striqt v0.7.0 build
   actually installed on the radio (pinned commit ``2e7696d`` — NOT the same
   API as the `striqt/` tree vendored in this repo). Both patches only affect
   non-Deepwave SoapySDR radios and are no-ops against a striqt build that has
   already fixed the underlying issue upstream: see
   :func:`_patch_soapy_arginfo_subscript` and
   :func:`_patch_soapy_hardware_time_optional`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

def _ensure_pixi_runtime_libs():
    """Re-exec the process once with the pixi env's libstdc++ on LD_LIBRARY_PATH.

    The AIR-T pixi env ships a newer libstdc++ than the one the dynamic linker
    would otherwise find, and scipy's/striqt's compiled waveform extensions
    need it. If the required lib is present and not already on
    ``LD_LIBRARY_PATH``, this adds it and calls ``os.execv`` to restart the
    current process with the same argv, exactly once (guarded by the
    ``RADIO_WEB_LD_REEXEC`` env var so the re-exec doesn't loop). No-ops on
    non-POSIX platforms, under pytest, or when the lib dir/file isn't found.

    Returns:
        None. Either returns normally (nothing needed changing, or this is a
        no-op environment) or never returns because the process was replaced
        via ``os.execv``.
    """
    if os.name != "posix":
        return
    if "pytest" in sys.modules:
        # Never re-exec a test runner: execv replaces the pytest process
        # mid-collection and the suite dies without printing a byte (observed
        # on the radio host). The pixi shell that runs tests already has the
        # right libraries; anything import-level that still breaks will fail
        # loudly in the tests themselves.
        return
    try:
        lib_dir = Path(sys.executable).resolve().parents[1] / "lib"
    except Exception:
        return
    if not (lib_dir / "libstdc++.so.6").exists():
        return
    current = os.environ.get("LD_LIBRARY_PATH", "")
    parts = [p for p in current.split(":") if p]
    lib_s = str(lib_dir)
    if lib_s in parts:
        return
    os.environ["LD_LIBRARY_PATH"] = ":".join([lib_s] + parts)
    if os.environ.get("RADIO_WEB_LD_REEXEC") == "1":
        return
    os.environ["RADIO_WEB_LD_REEXEC"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)


_ensure_pixi_runtime_libs()

# striqt hardware imports (only needed for real radio mode)
try:
    from striqt.sensor import specs
    from striqt.sensor.lib.sources import deepwave as _deepwave_sources
    Air8201BSourceSpec = _deepwave_sources.Air8201BSourceSpec
    Airstack1Source = _deepwave_sources.Airstack1Source
    # Older/other Deepwave models: not every installed striqt build ships their
    # spec classes — fall back to the AIR8201B spec, which the SoapyAIRT driver
    # accepts for the shared AirStack fields.
    Air7101BSourceSpec = getattr(_deepwave_sources, "Air7101BSourceSpec",
                                 Air8201BSourceSpec)
    Air7201BSourceSpec = getattr(_deepwave_sources, "Air7201BSourceSpec",
                                 Air8201BSourceSpec)
    try:
        from striqt.sensor.lib.sources.soapy import SoapySource as _SoapySource
    except Exception:
        _SoapySource = None
    try:
        from striqt.sensor.lib.sources.soapy import ReceiveStreamError
    except Exception:
        try:
            from striqt.sensor.lib.sources.base import ReceiveStreamError
        except Exception:
            ReceiveStreamError = OSError
    _SENSOR_OK = True
except Exception as _sensor_err:
    _SENSOR_OK = False
    specs = None
    Air8201BSourceSpec = None
    Air7101BSourceSpec = None
    Air7201BSourceSpec = None
    Airstack1Source = None
    _SoapySource = None
    ReceiveStreamError = OSError

# striqt analysis (calibrated spectrogram — optional, falls back to quicklook)
try:
    from striqt.analysis import specs as analysis_specs
    from striqt.analysis import measurements as striqt_measurements
    from striqt.analysis.measurements import shared as striqt_shared
    _ANALYSIS_OK = True
    _ANALYSIS_ERR = None
except Exception as e:
    analysis_specs = None
    striqt_measurements = None
    striqt_shared = None
    _ANALYSIS_OK = False
    _ANALYSIS_ERR = e


# ---------------------------------------------------------------------------
# SoapySDR compatibility: any radio that reports PER-CHANNEL sensors
# ---------------------------------------------------------------------------
def _patch_soapy_arginfo_subscript():
    """Let ``ArgInfo[0]`` return the ArgInfo, working around a striqt 0.7.0 bug.

    ``sources/soapy.py::_probe_channel`` builds each channel's sensor table as

        name=device.getSensorInfo(*args, key).name,          # line 179
        info=_SoapyArgInfo.from_soapy(device.getSensorInfo(*args, key)[0]),

    The first line treats the return value as an object; the second subscripts
    it. ``Device.getSensorInfo()`` returns a single ``SoapySDR.ArgInfo``, so the
    ``[0]`` raises ``TypeError: 'ArgInfo' object is not subscriptable`` and the
    source never opens. The device-level copy of the same code (line 248) has no
    ``[0]``, which confirms the stray index is the typo. It only fires on radios
    that expose per-channel sensors — the AIR-T does not, a USRP B2xx does
    (lo_locked, rssi), so this went unnoticed upstream and made every generic
    SoapySDR radio unusable.

    Teaching ArgInfo that ``x[0] is x`` is the smallest correct fix: it hands
    ``_SoapyArgInfo.from_soapy()`` exactly the ArgInfo it already expects,
    without copying striqt's probe logic into this repo. Any index other than
    0/-1 raises IndexError, so an accidental iteration terminates after one
    item instead of spinning.

    Remove this once the installed striqt drops the stray index.

    Returns:
        None. No-ops silently if SoapySDR is unavailable, ``ArgInfo`` already
        supports subscripting (already patched, or a fixed striqt), or the
        attribute assignment itself fails.
    """
    try:
        import SoapySDR
    except Exception:
        return  # demo hosts have no SoapySDR; nothing to patch
    arg_info = getattr(SoapySDR, 'ArgInfo', None)
    if arg_info is None or hasattr(arg_info, '__getitem__'):
        return
    def _getitem(self, index):
        """Return ``self`` for index 0/-1; raise IndexError otherwise (see enclosing docstring)."""
        if index in (0, -1):
            return self
        raise IndexError(index)
    try:
        arg_info.__getitem__ = _getitem
    except Exception:
        pass  # never let a compatibility patch break the import


_patch_soapy_arginfo_subscript()


def _patch_soapy_hardware_time_optional():
    """Let radios with no hardware clock finish opening.

    striqt's SoapySource disciplines the device clock during setup:

        HardwareTimeSync.to_host_os() ->
            if not device.hasHardwareTime():
                raise IOError('device does not expose hardware time')

    BOTH ``time_sync_at`` settings reach it — 'open' from ``_apply_setup`` and
    'acquire' from ``_prepare_capture`` — and ``gapless=True`` forces 'open',
    so no configuration avoids it. A USRP implements hardware time; a
    PlutoSDR, RTL-SDR, HackRF and most low-cost SDRs do not, so the source
    could never be constructed for them at all.

    That the raise is an oversight rather than a requirement is settled by
    striqt's own READ path, which already guards the identical case:

        if not self.checked_timestamp and device.hasHardwareTime():
            sync_time_ns = last_sync_time
        else:
            sync_time_ns = None

    So a missing clock simply means "no sync time" — exactly what everything
    downstream already expects, since ``last_sync_time`` is typed
    ``int | None``. We return None instead of raising.

    Only the 'host'/'internal' path is relaxed. 'external'/'gps' still raise:
    quietly skipping a PPS discipline the operator asked for would be a lie
    about the timing of the data, which is worse than refusing to run.

    Returns:
        None. No-ops if ``striqt.sensor`` isn't importable, the patch was
        already applied (``_linda_optional_hw_time`` flag), or
        ``HardwareTimeSync.to_host_os`` doesn't exist to patch.
    """
    try:
        from striqt.sensor.lib.sources.soapy import HardwareTimeSync
    except Exception:
        return  # no striqt.sensor on this host; nothing to patch
    if getattr(HardwareTimeSync, '_linda_optional_hw_time', False):
        return
    original = getattr(HardwareTimeSync, 'to_host_os', None)
    if original is None:
        return

    def to_host_os(self, device):
        """Return None instead of raising when `device` has no hardware clock (see enclosing docstring)."""
        try:
            has_time = device.hasHardwareTime()
        except Exception:
            has_time = False
        if not has_time:
            return None
        return original(self, device)

    try:
        HardwareTimeSync.to_host_os = to_host_os
        HardwareTimeSync._linda_optional_hw_time = True
    except Exception:
        pass  # a compatibility patch must never break the import


_patch_soapy_hardware_time_optional()
