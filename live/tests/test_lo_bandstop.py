"""LO bandstop vs sample rate: the notch must never be wider than the span.

`DEFAULT_LO_BANDSTOP` (120 kHz) is config, so it survives a retune — and a
retune can move the sample rate underneath it. The Pluto's driver floor is
65105 Hz: striqt then rejects the filter design on every frame
("offset - bandwidth/2 < fs/2") and the compute backstop cannot recover,
because the analysis params never changed — the RATE did, so there is nothing
to revert. Observed on a Pi 5 + Pluto: the hardware qual's rate sweep wedged
at its 65105 Hz point, erroring once per tick forever, with op verification
waiting on a frame that could never compute.

`resolve_lo_bandstop` disables the notch (disclosed) when it cannot fit;
these tests pin that boundary and prove the wedge rate now computes frames.
"""

import numpy as np

from core.config import RadioConfig
from core.dsp import compute_blocks, make_psd_kwargs, resolve_lo_bandstop
from core.constants import DEFAULT_LO_BANDSTOP

PLUTO_FLOOR_HZ = 65105.0   # the exact rate the wedge was observed at


def test_disabled_below_the_notch():
    """The observed failure: 120 kHz notch on a 65 kHz span."""
    assert resolve_lo_bandstop(DEFAULT_LO_BANDSTOP, PLUTO_FLOOR_HZ) is None


def test_kept_at_normal_rates():
    """3.84 MS/s (the Pluto default) holds the default notch untouched."""
    assert resolve_lo_bandstop(DEFAULT_LO_BANDSTOP, 3.84e6) == DEFAULT_LO_BANDSTOP


def test_boundary_is_half_the_span():
    """The notch may be at most fs/2 — half-span each side of DC, with room
    left for striqt's transition band."""
    assert resolve_lo_bandstop(100e3, 200e3) == 100e3
    assert resolve_lo_bandstop(100.1e3, 200e3) is None


def test_none_and_zero_stay_disabled():
    assert resolve_lo_bandstop(None, 3.84e6) is None
    assert resolve_lo_bandstop(0, 3.84e6) is None


def test_psd_kwargs_carry_the_resolved_value():
    """The tier-2 validators and the compute path share the kwarg builders,
    so resolving inside them must cover both."""
    cfg = RadioConfig(sample_rate=PLUTO_FLOOR_HZ)
    kwargs = make_psd_kwargs(cfg, 1024, PLUTO_FLOOR_HZ)
    assert kwargs["lo_bandstop"] is None
    kwargs = make_psd_kwargs(cfg, 1024, 3.84e6)
    assert kwargs["lo_bandstop"] == DEFAULT_LO_BANDSTOP


def test_wedge_rate_computes_a_frame():
    """End-to-end at the exact rate that wedged the qual on hardware.

    Every backend the fake-radio pipeline can run must produce finite blocks
    at the Pluto floor rate with the DEFAULT analysis params — this is the
    frame op #4's verification was waiting on.
    """
    rng = np.random.default_rng(7)
    n = 1 << 17
    samples = (rng.standard_normal((1, n)) +
               1j * rng.standard_normal((1, n))).astype(np.complex64)
    for backend in ("quicklook", "calibrated", "psd"):
        cfg = RadioConfig(sample_rate=PLUTO_FLOOR_HZ, backend=backend,
                          nfft=1024, rows=12)
        blocks, meta = compute_blocks(samples, cfg)
        assert np.isfinite(blocks).all(), (
            f"{backend} produced non-finite output at the Pluto floor rate")
        # Honesty contract: what ran is disclosed either way (striqt-less
        # hosts substitute quicklook and say so).
        assert meta["backend_requested"] == backend
