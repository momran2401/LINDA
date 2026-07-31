"""Low-rate structured measurements computed alongside the high-rate waterfall.

`InsightService` maintains a thread-safe "latest results" snapshot — channel
power time series/histogram (via striqt's native measurement kernels) and,
optionally, a 5G PSS/SSS cell-detection candidate search — that clients read
separately from the per-frame waterfall/PSD data pushed over the WebSocket.
The two measurement kinds are throttled independently (power at most every
0.5 s, cell search at most every 2 s): these are meant to update at a
human-legible rate, not once per acquired frame.

This module only consumes public striqt measurement functions
(`striqt.analysis.measurements`, reached via `core/striqt_compat.py`) and
degrades honestly: if `striqt.analysis` failed to import (`_ANALYSIS_OK` is
False), `InsightService.update()` records a warning instead of raising. It
deliberately runs on the existing compute worker, never on the DMA drain
thread, so a slow measurement cannot stall sample acquisition.
"""
from __future__ import annotations

import hashlib
import math
import os
import threading
import time
from fractions import Fraction

import numpy as np

from . import state
from .striqt_compat import analysis_specs, striqt_measurements, _ANALYSIS_OK


def calibration_status(cfg) -> dict:
    """Describe whether/how input-power calibration is configured and applied.

    Reads the calibration file path from ``cfg.source_config["calibration"]``
    and reports its status. Even with a valid file configured, live values
    are never labeled calibrated: the rolling live path reads the source
    stream directly and does not construct an AcquiredIQ or call
    ``striqt.correct_iq`` — only the recording sweep actually applies the
    calibration.

    Args:
        cfg: The active radio config, consulted for `source_config`.

    Returns:
        dict: JSON-serializable status with at least `active`, `configured`,
        `state`, `units`, `message`. When a calibration value is configured,
        also includes `path`/`name`, and either `sha256`/`modified_at`/
        `available` (file read succeeded) or `state="invalid"` with a message
        (file could not be read).
    """
    value = (cfg.source_config or {}).get("calibration")
    result = {
        "active": False,
        "configured": bool(value),
        "state": "uncalibrated",
        "units": {"power": "dB relative", "psd": "dB relative/Hz"},
        "message": "No input-power calibration is configured",
    }
    if not value:
        return result
    path = os.path.abspath(os.path.expanduser(str(value)))
    result.update(path=path, name=os.path.basename(path))
    try:
        stat = os.stat(path)
        with open(path, "rb") as stream:
            digest = hashlib.sha256(stream.read()).hexdigest()
        result.update(
            # The rolling live path reads the source stream directly and does
            # not construct AcquiredIQ/call striqt.correct_iq.  A valid file is
            # therefore configured and available to the recording sweep, but
            # it is not honest to label live values dBm yet.
            active=False, available=True, applied_live=False,
            state="configured-not-applied-live", sha256=digest,
            modified_at=stat.st_mtime,
            units={"power": "dB relative", "psd": "dB relative/Hz"},
            message="Calibration is available to striqt recording; live direct-stream correction is not applied",
        )
    except OSError as exc:
        result.update(state="invalid", message=f"Calibration unavailable: {exc}")
    return result


class InsightService:
    """Thread-safe latest-value store for native power/statistical/cell results.

    Instances hold no per-request state — `update()` is called once per
    compute cycle with the newly acquired samples, and `snapshot()` is called
    by request handlers to read back whatever was computed most recently.

    Attributes:
        _latest: The most recent JSON-serializable result dict, replaced
            wholesale (never mutated) by `update()`.
        _cell_enabled: Whether the periodic 5G PSS/SSS candidate search runs.
        _last_power: Monotonic timestamp of the last power computation, used
            to throttle `update()` to at most once every 0.5 s.
        _last_cell: Monotonic timestamp of the last cell-search attempt,
            throttled to at most once every 2 s.
        _cell_hits: Consecutive successful-detection count, used to flag a
            candidate as `persistent` after 3 consecutive hits.
        _cell_first_seen: Wall-clock time the current hit streak began, or
            None between streaks.
    """

    def __init__(self):
        """Initialize an empty snapshot and cell-detection tracking state."""
        self._lock = threading.Lock()
        self._latest = {"schema_version": 1, "updated_at": None}
        self._cell_enabled = False
        self._last_cell = 0.0
        self._last_power = 0.0
        self._cell_hits = 0
        self._cell_first_seen = None

    def configure(self, *, cell_enabled=None):
        """Update runtime toggles.

        Args:
            cell_enabled: If not None, enable or disable the periodic 5G
                PSS/SSS cell-detection candidate search.
        """
        if cell_enabled is not None:
            self._cell_enabled = bool(cell_enabled)

    def snapshot(self):
        """Return the most recently computed result snapshot.

        Returns:
            dict: A shallow copy of the latest snapshot (or the initial
            empty one if `update()` has never run). A shallow copy suffices
            because `update()` always replaces whole nested result objects
            rather than mutating them in place — the JSON-compatible content
            is never mutated after being stored.
        """
        with self._lock:
            return dict(self._latest)

    def update(self, samples, cfg):
        """Compute and store the latest low-rate measurement snapshot.

        Throttled to run at most once every 0.5 s; calls within that window
        return immediately without recomputing. Always computes channel
        power time series/histogram (recording a `warning` or `power_error`
        key on failure instead of raising); additionally runs the 5G PSS/SSS
        cell-detection candidate search at most once every 2 s when
        `_cell_enabled` is set via `configure()` — otherwise, or between
        search intervals, the previous cell result is carried forward
        unchanged.

        Args:
            samples: Acquired IQ array, shape (channels, num_samples).
            cfg: The active radio config — consulted for sample_rate, center,
                analysis_bandwidth, ssb_* fields, and calibration status.
        """
        if time.monotonic() - self._last_power < 0.5:
            return
        self._last_power = time.monotonic()
        cal = calibration_status(cfg)
        result = {
            "schema_version": 1,
            "updated_at": time.time(),
            "capture": {
                "sample_rate": float(cfg.sample_rate),
                "center_frequency": float(cfg.center),
                "channels": list(state.CHANNELS),
                "sample_count": int(samples.shape[1]),
            },
            "calibration": cal,
        }
        if not _ANALYSIS_OK:
            result["warning"] = "striqt.analysis is unavailable"
            with self._lock:
                self._latest = result
            return

        capture = analysis_specs.Capture(
            sample_rate=float(cfg.sample_rate),
            duration=float(samples.shape[1]) / float(cfg.sample_rate),
            analysis_bandwidth=float(cfg.analysis_bandwidth),
        )
        fs_int = round(cfg.sample_rate)
        # striqt's native bin-power kernel requires the IQ length to be an
        # exact multiple of the detector block. Pick the greatest divisor
        # shared with the desired 1 ms block (at least one sample).
        detector_samples = max(1, math.gcd(int(samples.shape[1]),
                                          max(1, round(0.001 * fs_int))))
        detector_period = Fraction(detector_samples, fs_int)
        try:
            values, attrs = striqt_measurements.channel_power_time_series(
                samples, capture, detector_period=detector_period,
                power_detectors=("rms", "peak"), as_xarray=False,
            )
            values = np.asarray(values, dtype=np.float32)
            result["channel_power"] = {
                "name": "channel_power_time_series",
                "standard_name": "Channel Power",
                "units": cal["units"]["power"],
                "calibrated": bool(cal["active"]),
                "detectors": ["rms", "peak"],
                "detector_period_s": float(detector_period),
                "shape": list(values.shape),
                "values": values.tolist(),
                "attrs": attrs,
            }
            hist, hattrs = striqt_measurements.channel_power_histogram(
                samples, capture, detector_period=detector_period,
                power_detectors=("rms", "peak"), power_low=-160,
                power_high=20, power_resolution=2, as_xarray=False,
            )
            hist = np.asarray(hist, dtype=np.float32)
            centers_array = np.concatenate(([-np.inf],
                                            np.arange(-160, 20, 2, dtype=np.float32),
                                            [20.0],
                                            [np.inf]))
            centers = centers_array.tolist()
            # Occupancy is explicit and reproducible: fraction above -80 in the
            # native channel-power histogram, per channel and detector.
            threshold = -80.0
            mask = centers_array >= threshold
            occupancy = np.sum(hist[..., mask], axis=-1)
            result["occupancy"] = {
                "name": "channel_power_histogram",
                "standard_name": "Fraction of channel power readings",
                "units": "fraction",
                "power_units": cal["units"]["power"],
                "threshold": threshold,
                "detectors": ["rms", "peak"],
                "power_bin_centers": centers,
                "shape": list(hist.shape),
                "values": hist.tolist(),
                "fraction_above_threshold": occupancy.tolist(),
                "attrs": hattrs,
            }
        except Exception as exc:
            result["power_error"] = str(exc)

        if self._cell_enabled and time.monotonic() - self._last_cell >= 2.0:
            self._last_cell = time.monotonic()
            cell = self._cell_summary(samples, capture, cfg)
            if cell.get("detected"):
                self._cell_hits += 1
                self._cell_first_seen = self._cell_first_seen or time.time()
                cell.update(first_seen=self._cell_first_seen,
                            last_seen=time.time(), consecutive_hits=self._cell_hits,
                            persistent=self._cell_hits >= 3)
            else:
                self._cell_hits = 0
                self._cell_first_seen = None
                cell.update(consecutive_hits=0, persistent=False)
            result["cell"] = cell
        else:
            with self._lock:
                prior_cell = self._latest.get("cell")
            if prior_cell is not None:
                result["cell"] = prior_cell

        with self._lock:
            self._latest = result

    @staticmethod
    def _cell_summary(samples, capture, cfg):
        """Run a best-effort 5G SSB PSS/SSS correlation search.

        Args:
            samples: Acquired IQ array, shape (channels, num_samples).
            capture: A striqt `analysis_specs.Capture` describing
                sample_rate/duration/analysis_bandwidth for this array.
            cfg: The active radio config, used for
                ssb_subcarrier_spacing/ssb_sample_rate/
                ssb_discovery_periodicity/ssb_frequency_offset.

        Returns:
            dict: `detected` is True when the PSS peak-to-median ratio is
            finite and >= 6.0; only then is the SSS correlation also run.
            `physical_cell_id` is always None — the SSS result gives a
            candidate peak, but PCI is left unset until axis metadata
            unambiguously identifies NID1. Any exception during correlation
            is caught and reported via an `error` key rather than
            propagated.
        """
        kwargs = dict(
            subcarrier_spacing=float(cfg.ssb_subcarrier_spacing),
            sample_rate=min(float(cfg.ssb_sample_rate), float(cfg.sample_rate)),
            discovery_periodicity=float(cfg.ssb_discovery_periodicity),
            frequency_offset=float(cfg.ssb_frequency_offset),
            max_block_count=1,
        )
        out = {"name": "5g_pss_sss_candidate", "detected": False,
               "candidate": True, "updated_at": time.time()}
        try:
            pss, pmeta = striqt_measurements.cellular_5g_pss_correlation(
                samples, capture, as_xarray=False, **kwargs)
            pss = np.abs(np.asarray(pss))
            pidx = np.unravel_index(int(np.argmax(pss)), pss.shape)
            peak = float(pss[pidx])
            floor = float(np.median(pss))
            ratio = peak / max(floor, np.finfo(float).tiny)
            # NID2 is the only three-valued dimension in the public result.
            nid2_axis = next((i for i, n in enumerate(pss.shape) if n == 3), None)
            nid2 = int(pidx[nid2_axis]) if nid2_axis is not None else None
            out.update(pss_peak=peak, pss_peak_to_median=ratio, nid2=nid2,
                       pss_shape=list(pss.shape), pss_attrs=pmeta,
                       detected=bool(math.isfinite(ratio) and ratio >= 6.0))
            if out["detected"]:
                sss, smeta = striqt_measurements.cellular_5g_sss_correlation(
                    samples, capture, as_xarray=False, **kwargs)
                sss = np.abs(np.asarray(sss))
                sidx = np.unravel_index(int(np.argmax(sss)), sss.shape)
                out.update(sss_peak=float(sss[sidx]), sss_shape=list(sss.shape),
                           sss_attrs=smeta, physical_cell_id=None,
                           note="Candidate PSS/SSS peaks; PCI remains unset until axis metadata identifies NID1 unambiguously")
        except Exception as exc:
            out["error"] = str(exc)
        return out
