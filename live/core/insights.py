"""Low-rate structured measurements alongside the high-rate waterfall.

This module only consumes public striqt measurement functions.  It deliberately
runs on the existing compute worker, never on the DMA drain thread.
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
    """Thread-safe latest-value store for native power/statistical/cell results."""

    def __init__(self):
        self._lock = threading.Lock()
        self._latest = {"schema_version": 1, "updated_at": None}
        self._cell_enabled = False
        self._last_cell = 0.0
        self._last_power = 0.0
        self._cell_hits = 0
        self._cell_first_seen = None

    def configure(self, *, cell_enabled=None):
        if cell_enabled is not None:
            self._cell_enabled = bool(cell_enabled)

    def snapshot(self):
        with self._lock:
            # JSON-compatible content only; a shallow copy is sufficient because
            # updates replace complete nested result objects.
            return dict(self._latest)

    def update(self, samples, cfg):
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
