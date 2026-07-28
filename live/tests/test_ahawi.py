"""AHAWI: coherent capture → aligned segmented replay.

Covers the segmentation plan arithmetic, burst alignment, config validation,
and the demo end-to-end path (DemoAcquirer publishing honest capture frames).
All quicklook — no striqt or hardware needed.
"""
import time

import numpy as np
import pytest

from core import state
from core.acquisition import DemoAcquirer
from core.config import SharedConfig
from core.constants import (
    AHAWI_MAX_SEGMENTS, DEMO_BURST, MAX_ROWS_ABS, MAX_TAIL, RING_ROW_FILL,
)
from core.dsp import ahawi_align_offset, ahawi_capture, ahawi_plan


@pytest.fixture(autouse=True)
def demo_device():
    state.configure_device("demo")
    state.set_backend("quicklook")


def _cfg(**overrides):
    shared = SharedConfig()
    shared.update(dict({"backend": "quicklook", "ahawi": True}, **overrides))
    return shared.snapshot()


# ── Plan arithmetic ─────────────────────────────────────────────────────────

def test_plan_default_is_five_aligned_20ms_segments():
    cfg = _cfg(**{"capture": {"duration": 0.02}, "ahawi_capture_ms": 100})
    plan = ahawi_plan(cfg)
    assert plan["segments"] == 5
    assert plan["align"] is True
    # One extra segment of slack for the alignment trim.
    assert plan["total_rows"] == 6 * plan["rows_per_seg"]
    assert plan["need_samples"] == plan["total_rows"] * plan["nfft"]
    assert plan["segment_ms"] == pytest.approx(20.0, rel=0.05)
    assert plan["capture_ms"] == pytest.approx(100.0, rel=0.05)


def test_plan_segment_length_follows_the_duration_control():
    cfg = _cfg(**{"capture": {"duration": 0.01}, "ahawi_capture_ms": 100})
    plan = ahawi_plan(cfg)
    assert plan["segments"] == 10
    assert plan["segment_ms"] == pytest.approx(10.0, rel=0.05)


def test_plan_fits_the_ring_honestly():
    # A capture request the ring cannot hold must clamp, never overflow the
    # Computer's avail >= need gate.
    cfg = _cfg(**{"capture": {"duration": 0.02}, "ahawi_capture_ms": 1000})
    plan = ahawi_plan(cfg)
    assert plan["need_samples"] <= int(MAX_TAIL * RING_ROW_FILL)
    assert plan["total_rows"] <= MAX_ROWS_ABS
    assert plan["segments"] >= 1


def test_plan_caps_the_segment_count_for_the_scrubber():
    cfg = _cfg(**{"capture": {"duration": 0.002}, "ahawi_capture_ms": 1000})
    assert ahawi_plan(cfg)["segments"] <= AHAWI_MAX_SEGMENTS


def test_plan_disables_alignment_when_only_one_segment_fits():
    cfg = _cfg(**{"capture": {"duration": 0.02}, "ahawi_capture_ms": 20})
    plan = ahawi_plan(cfg)
    assert plan["segments"] == 1
    assert plan["align"] is False
    assert plan["extra_rows"] == 0


def test_plan_respects_the_align_toggle():
    cfg = _cfg(ahawi_align=False, **{"capture": {"duration": 0.02}})
    plan = ahawi_plan(cfg)
    assert plan["align"] is False
    assert plan["total_rows"] == plan["segments"] * plan["rows_per_seg"]


# ── Burst alignment ─────────────────────────────────────────────────────────

def _synthetic_capture(segments, rps, bins, burst_at, burst_rows=3,
                       burst_db=20.0):
    """Noise-floor spectrogram with a periodic burst at row `burst_at` of
    every segment period (dB units, like real blocks)."""
    rng = np.random.default_rng(7)
    blocks = rng.normal(-90.0, 0.5, size=(2, segments * rps, bins)).astype(np.float32)
    for s in range(segments):
        r = s * rps + burst_at
        blocks[:, r:r + burst_rows, :] += burst_db
    return blocks


def test_align_offset_puts_the_burst_at_the_target_row():
    rps, segments = 300, 5
    # Narrow burst: the fold's peak is unambiguous, so this pins the offset
    # arithmetic precisely. (Wide bursts land within their own width — the
    # demo end-to-end test covers that consistency property.)
    blocks = _synthetic_capture(segments, rps, 64, burst_at=200, burst_rows=3)
    offset, aligned = ahawi_align_offset(blocks, rps, segments)
    assert aligned is True
    # After trimming `offset` rows, the burst should land at ~rps/4.
    landed = (200 - offset) % rps
    assert abs(landed - rps // 4) <= max(3, rps // 32)


def test_align_reports_unaligned_on_flat_noise():
    rng = np.random.default_rng(11)
    blocks = rng.normal(-90.0, 0.5, size=(2, 1500, 64)).astype(np.float32)
    offset, aligned = ahawi_align_offset(blocks, 300, 5)
    assert aligned is False
    assert offset == 0


def test_capture_trims_to_exact_segment_rows_and_discloses_geometry():
    cfg = _cfg(**{"capture": {"duration": 0.02}, "ahawi_capture_ms": 100})
    plan = ahawi_plan(cfg)
    rng = np.random.default_rng(3)
    samples = (rng.standard_normal((2, plan["need_samples"]))
               + 1j * rng.standard_normal((2, plan["need_samples"]))
               ).astype(np.complex64) * 0.05
    blocks, meta = ahawi_capture(samples, cfg, plan)
    a = meta["ahawi"]
    assert blocks.shape[1] == a["segments"] * a["rows_per_segment"]
    assert meta["backend"] == "quicklook"
    assert a["align_offset_rows"] <= plan["extra_rows"]
    assert a["compute_ms"] >= 0


# ── Config validation ───────────────────────────────────────────────────────

def test_config_clamps_capture_ms_and_coerces_bools():
    shared = SharedConfig()
    shared.update({"ahawi": 1, "ahawi_capture_ms": 999999, "ahawi_align": 0})
    cfg = shared.snapshot()
    assert cfg.ahawi is True
    assert cfg.ahawi_align is False
    assert cfg.ahawi_capture_ms == 1000.0
    shared.update({"ahawi_capture_ms": 1})
    assert shared.snapshot().ahawi_capture_ms == 20.0


def test_config_rejects_garbage_capture_ms_quietly():
    shared = SharedConfig()
    before = shared.snapshot().ahawi_capture_ms
    ack = shared.update({"ahawi_capture_ms": "not-a-number"})
    assert "ahawi_capture_ms" not in ack["applied"]
    assert shared.snapshot().ahawi_capture_ms == before


# ── Demo end-to-end ─────────────────────────────────────────────────────────

def wait_for(predicate, timeout=12.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = predicate()
        if v:
            return v
        time.sleep(0.05)
    return None


def test_demo_publishes_aligned_coherent_captures():
    shared = SharedConfig()
    shared.update({"backend": "quicklook", "ahawi": True,
                   "ahawi_capture_ms": 100, "capture": {"duration": 0.02}})
    acq = DemoAcquirer(shared)
    acq.start()
    try:
        got = wait_for(lambda: (
            (h_b := acq.latest()) and h_b[0] and h_b[0].get("ahawi") and h_b) or None)
        assert got, "no AHAWI capture frame produced"
        header, blocks = got
        a = header["ahawi"]
        assert header["rows"] == a["segments"] * a["rows_per_segment"]
        assert a["segments"] == 5
        assert a["coherent"] is True
        assert "capture_t0" in a

        # The demo burst is 20 ms-periodic and each segment is 20 ms, so after
        # alignment the burst must sit at (nearly) the same row in EVERY
        # segment — the entire point of AHAWI.
        assert a["aligned"] is True
        b = np.asarray(blocks[0])
        rps = a["rows_per_segment"]
        positions = []
        for s in range(a["segments"]):
            seg = b[s * rps:(s + 1) * rps]
            row_power = np.power(10.0, seg / 10.0).mean(axis=1)
            positions.append(int(np.argmax(row_power)))
        burst_rows = max(1, round(DEMO_BURST["duty_s"] * header["fs"]
                                  / header["nfft"]))
        assert max(positions) - min(positions) <= burst_rows + 2, positions
    finally:
        shared.stop()
        acq.join(timeout=3.0)


def test_demo_ahawi_switches_cleanly_back_to_rolling():
    shared = SharedConfig()
    shared.update({"backend": "quicklook", "ahawi": True,
                   "capture": {"duration": 0.02}})
    acq = DemoAcquirer(shared)
    acq.start()
    try:
        assert wait_for(lambda: (h := acq.latest()[0]) and h.get("ahawi"))
        shared.update({"ahawi": False})
        assert wait_for(lambda: (
            (h := acq.latest()[0]) and not h.get("ahawi") and h) or None), \
            "never returned to rolling frames after ahawi off"
    finally:
        shared.stop()
        acq.join(timeout=3.0)


def test_striqtless_calibrated_backend_falls_back_honestly():
    """Regression: with striqt absent, requesting the calibrated backend used
    to raise on every compute tick — nothing for the backstop to revert, so
    the viewer froze in a silent error loop (found via the AHAWI browser
    walkthrough, but reachable in plain rolling mode too). It must instead run
    quicklook and DISCLOSE the substitution (LV-F2)."""
    shared = SharedConfig()
    shared.update({"backend": "calibrated", "capture": {"duration": 0.02}})
    acq = DemoAcquirer(shared)
    acq.start()
    try:
        hdr = wait_for(lambda: acq.latest()[0])
        assert hdr, "no frames at all with the calibrated backend requested"
        assert hdr["backend"] == "quicklook"
        assert hdr["backend_requested"] == "calibrated"
        # Honest row count: no zero-padded dark band from the overlapped-grid
        # rows estimate.
        assert hdr["rows"] > 0

        # The same substitution must hold coming OUT of AHAWI — the original
        # failing sequence.
        shared.update({"ahawi": True})
        assert wait_for(lambda: (h := acq.latest()[0]) and h.get("ahawi"))
        shared.update({"ahawi": False})
        assert wait_for(lambda: (
            (h := acq.latest()[0]) and not h.get("ahawi") and h) or None), \
            "frames never resumed after leaving AHAWI on a striqt-less host"
    finally:
        shared.stop()
        acq.join(timeout=3.0)
