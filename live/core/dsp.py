"""DSP compute backends and the frame-header contract for the live viewer.

This module is the single place that turns raw IQ samples pulled from the
ring buffer (`core.acquisition`) into the (channels, rows, bins) float32
blocks the web UI renders, plus the JSON header that discloses exactly how
those blocks were produced. It implements four display backends:

- `db_spectrogram` ("quicklook") — a dependency-free Hann/FFT spectrogram
  that works even when striqt is not importable.
- `calibrated_spectrogram` — striqt's calibrated STFT, driven by the
  Analysis param block in `RadioConfig` (window/overlap/fill/integration
  bandwidth/LO bandstop/stopband trim).
- `psd_traces` — striqt's power_spectral_density Welch-method statistic
  traces (rms/mean/percentiles over time, one row per statistic).
- `ssb_spectrogram` — striqt's symbol-aligned 5G SSB spectrogram, one row
  per OFDM symbol of each discovered burst set.

`compute_blocks` is the single dispatch point frontends call; it degrades a
backend honestly (SSB off-grid -> calibrated, striqt unavailable ->
quicklook) and always reports both the requested and executed backend so the
client never renders a phantom view. `build_header` assembles the frame
header (frequency axis, fft_nfft/bin_avg/hop_size disclosure, backend
identity) that ships alongside every frame — see `core.serialization` for
how blocks + header become wire bytes.

The module also owns AHAWI mode: a third display mode that grabs ONE
contiguous multi-segment capture from the ring, analyzes it in a single
striqt pass (`ahawi_capture`, geometry from `ahawi_plan`), and ships it as one
frame the client replays segment-by-segment. AHAWI additionally runs the
full striqt measurement bundle (PSD statistics + channel power time series)
over the displayed span and can fold segments into phase alignment
(`ahawi_align_offset`) so a periodic burst (e.g. a 20 ms 5G SSB) holds still
across segments instead of swimming the way the rolling live view does.

Everything here was extracted verbatim from striqt_web_server.py during the
2026-07 core/ refactor; see CLAUDE.md for the current architecture map.
"""
from __future__ import annotations

import math
import time
from dataclasses import replace
from fractions import Fraction

import numpy as np

from . import state
from .constants import (
    DEFAULT_FRACTIONAL_OVERLAP, AVG_BIN_GROUPS, MAX_TAIL, RING_ROW_FILL,
    MAX_ROWS_ABS, RATES_HZ, SSB_LO_BANDSTOP, SSB_MAX_RATE,
    SSB_SUBCARRIER_SPACING, CALIBRATED_GRID_BACKENDS,
    AHAWI_ALIGN_MIN_DB, AHAWI_ALIGN_TARGET, AHAWI_MAX_SEGMENTS,
    AHAWI_MIN_CAPTURE_MS, AHAWI_MAX_CAPTURE_MS,
)
from .striqt_compat import (
    analysis_specs, striqt_measurements, striqt_shared,
    _ANALYSIS_OK, _ANALYSIS_ERR,
)

def _snap(value, choices):
    """Return the entry of `choices` closest to `value`.

    Args:
        value: Target value to snap.
        choices: Iterable of candidate values.

    Returns:
        The element of `choices` with the smallest absolute distance to
        `value`.
    """
    return min(choices, key=lambda c: abs(c - value))


def allowed_rates(env):
    """LTE-grid sample rates within a device's capability envelope (P3-3).

    The rate grid itself (`RATES_HZ`, cellular multiples of 1.92 MHz) is
    domain logic, not a device property; `env` only filters which grid
    entries a given radio can actually run.

    Args:
        env: Device capability envelope with `rate_min`/`rate_max` bounds
            (Hz), as returned by the device adapter layer.

    Returns:
        Tuple of rates from `RATES_HZ` within `[env["rate_min"],
        env["rate_max"]]`. Falls back to the full `RATES_HZ` grid if that
        intersection is empty, so callers snapping to the result never face
        an empty choice list.
    """
    rates = tuple(r for r in RATES_HZ
                  if env["rate_min"] <= r <= env["rate_max"])
    return rates or RATES_HZ
# ---------------------------------------------------------------------------
# Spectrogram compute backends
# ---------------------------------------------------------------------------

def db_spectrogram(samples: np.ndarray, nfft: int, rows: int) -> np.ndarray:
    """Dependency-free "quicklook" spectrogram: Hann window, FFT, power in dB.

    Non-overlapping rows (hop == nfft), so this is the backend `compute_blocks`
    falls back to when striqt is not importable — it needs no striqt spec at
    all, only a reshape and an FFT.

    Args:
        samples: (channels, n) complex IQ samples.
        nfft: FFT size; also the hop between rows (no overlap).
        rows: Number of output rows to produce. `samples` is right-cropped to
            the most recent `rows * nfft` samples, or zero-padded at the front
            if shorter.

    Returns:
        Tuple of (spg, meta): spg is (channels, rows, nfft) float32, dB,
        fftshifted (bin 0 = -fs/2), oldest row first. meta is
        {"fft_nfft": nfft, "bin_avg": 1, "hop_size": nfft} — the axis
        parameters `build_header` needs; quicklook never averages bins.
    """
    samples = np.asarray(samples, dtype=np.complex64)
    needed  = rows * nfft
    if samples.shape[1] < needed:
        pad = np.zeros((samples.shape[0], needed - samples.shape[1]), dtype=np.complex64)
        samples = np.concatenate([samples, pad], axis=1)
    else:
        samples = samples[:, -needed:]
    x      = samples.reshape(samples.shape[0], rows, nfft)
    window = np.hanning(nfft).astype(np.float32)
    x      = x * window[None, None, :]
    spec   = np.fft.fftshift(np.fft.fft(x, axis=-1), axes=-1)
    # Normalize by window power (proper PSD estimate)
    power  = (np.abs(spec) ** 2) / max(float(np.sum(window ** 2)), 1.0)
    spg = (10.0 * np.log10(power + 1e-20)).astype(np.float32)
    return spg, {"fft_nfft": int(nfft), "bin_avg": 1, "hop_size": int(nfft)}


def analysis_hop(nfft: int, fractional_overlap=DEFAULT_FRACTIONAL_OVERLAP) -> int:
    """Samples the STFT advances per displayed row.

    Computed as `nfft - noverlap`, where `noverlap` is derived exactly as
    striqt does internally (`round(fractional_overlap * nfft)` on the exact
    `Fraction`), so this stays bit-for-bit consistent with striqt's own row
    geometry. At the default 13/28 overlap this is the familiar `nfft*15/28`.

    Args:
        nfft: STFT size.
        fractional_overlap: Overlap fraction (0..1), typically a `Fraction`.

    Returns:
        Hop size in samples, at least 1.
    """
    nfft = int(nfft)
    noverlap = round(Fraction(fractional_overlap) * nfft)
    return max(1, nfft - int(noverlap))


def resolve_integration_bandwidth(value, nfft: int, sample_rate: float):
    """Map a cfg integration_bandwidth setting to the value striqt expects.

    Args:
        value: `None` (no integration), the literal string `"auto"`, or a
            bandwidth in Hz.
        nfft: FFT size, used only when `value == "auto"`.
        sample_rate: Capture sample rate in Hz, used only when
            `value == "auto"`.

    Returns:
        `None` if `value` is `None`; otherwise a bandwidth in Hz. `"auto"`
        reproduces the pre-P2a behavior of `frequency_resolution *
        averaging_factor(nfft)` — the one choice that tracks nfft changes.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return (sample_rate / float(nfft)) * averaging_factor(nfft)   # "auto"
    return float(value)


def make_analysis_spec(cfg: "RadioConfig", nfft: int, sample_rate: float):
    """Build the striqt `Spectrogram` spec from cfg's analysis param block (P2a-1).

    Args:
        cfg: `RadioConfig` supplying window/overlap/fill/integration-bandwidth/
            LO-bandstop/stopband-trim/time-aperture settings.
        nfft: FFT size to target; `frequency_resolution` is derived from it so
            striqt's internal `nfft = round(sample_rate / frequency_resolution)`
            round-trips back to this value.
        sample_rate: Capture sample rate in Hz.

    Returns:
        A `striqt.analysis.specs.Spectrogram` instance ready to pass to
        `evaluate_spectrogram`.
    """
    frequency_resolution = float(sample_rate) / float(nfft)
    integration = resolve_integration_bandwidth(
        cfg.integration_bandwidth, nfft, sample_rate
    )
    lo = cfg.lo_bandstop
    aperture = cfg.time_aperture
    return analysis_specs.Spectrogram(
        window=cfg.window,
        frequency_resolution=frequency_resolution,
        fractional_overlap=Fraction(cfg.fractional_overlap),
        window_fill=Fraction(cfg.window_fill),
        integration_bandwidth=integration,
        trim_stopband=bool(cfg.trim_stopband),
        lo_bandstop=(float(lo) if lo else None),
        time_aperture=(float(aperture) if aperture else None),
    )


def time_aperture_bins(cfg: "RadioConfig", hop: int) -> int:
    """STFT rows striqt averages into one output row for `cfg.time_aperture`.

    Mirrors striqt's own `round(time_aperture / hop_period)` so the disclosed
    hop and the actual averaging stay consistent.

    Args:
        cfg: `RadioConfig`; `time_aperture` of 0/None means no time averaging.
        hop: STFT hop size in samples (one un-averaged row's advance).

    Returns:
        Number of STFT rows folded into one output row; 1 when
        `cfg.time_aperture` is falsy.
    """
    if not cfg.time_aperture:
        return 1
    return max(1, round(float(cfg.time_aperture) * float(cfg.sample_rate) / max(1, hop)))


def calibrated_spectrogram(samples: np.ndarray, cfg: "RadioConfig",
                           prefer_gpu: bool = False) -> tuple:
    """striqt-calibrated PSD spectrogram driven by cfg's analysis param block (P2a-1).

    Runs striqt's `evaluate_spectrogram` at the configured window, overlap,
    window fill, integration bandwidth, LO bandstop, and stopband trim; snaps
    the FFT size to the `aligned_nfft` grid and right-sizes the input to
    exactly `cfg.rows` STFT rows under the configured overlap (LV-W2) rather
    than computing extra rows and discarding them. When `cfg.time_aperture`
    folds multiple STFT rows into one displayed row, the output row count and
    disclosed hop are widened to match.

    Args:
        samples: (channels, n) complex IQ samples.
        cfg: `RadioConfig` supplying `rows`, `sample_rate`, `nfft`, and the
            analysis param block consumed by `make_analysis_spec`.
        prefer_gpu: Offload the STFT to cupy when available (AHAWI/capture-
            sized work); the rolling live path leaves this False and always
            runs numpy.

    Returns:
        Tuple of (blocks, meta): blocks is (channels, rows, bins) float32 dB.
        meta has keys `fft_nfft`, `bin_avg`, `hop_size`, `compute_backend`
        ("numpy" or "cupy"), and — when striqt's coordinate factory succeeds —
        `freqs_hz_f0`/`freqs_hz_step` for the exact frequency axis.

    Raises:
        RuntimeError: If the striqt analysis backend failed to import
            (`_ANALYSIS_OK` is False).
    """
    if not _ANALYSIS_OK:
        raise RuntimeError(f"calibrated backend unavailable: {_ANALYSIS_ERR!r}")

    samples = np.asarray(samples, dtype=np.complex64)
    rows        = int(cfg.rows)
    sample_rate = float(cfg.sample_rate)
    nfft        = aligned_nfft(cfg.nfft)
    hop         = analysis_hop(nfft, cfg.fractional_overlap)
    # Right-size to exactly `rows` STFT rows under the configured overlap, rather
    # than computing extra rows and discarding all but the last `rows` (LV-W2).
    needed = calibrated_sample_count(nfft, rows, hop)
    if samples.shape[1] < needed:
        pad = np.zeros((samples.shape[0], needed - samples.shape[1]), dtype=np.complex64)
        samples = np.concatenate([samples, pad], axis=1)
    else:
        samples = samples[:, -needed:]

    # Carry the real analysis_bandwidth so trim_stopband=True has something to
    # trim to; with the default trim=False / bandwidth=inf this is inert.
    capture = analysis_specs.Capture(
        sample_rate=sample_rate,
        duration=needed / sample_rate,
        analysis_bandwidth=float(cfg.analysis_bandwidth),
    )
    spec = make_analysis_spec(cfg, nfft, sample_rate)
    integration = resolve_integration_bandwidth(
        cfg.integration_bandwidth, nfft, sample_rate
    )
    average_bins = (
        1 if integration is None
        else max(1, round(integration / (sample_rate / nfft)))
    )
    striqt_shared.spectrogram_cache.clear()
    spg, gpu_backend = _run_array_fn(
        lambda iq: striqt_shared.evaluate_spectrogram(
            iq, capture, spec, dtype="float32", dB=True)[0],
        samples, prefer_gpu)
    # time_aperture averages time_bins STFT rows into one output row (P2b-2):
    # fewer rows come back, and each spans time_bins hops of signal. Fit to the
    # honest averaged count and disclose the widened hop so the client's time
    # labels stay exact.
    time_bins = time_aperture_bins(cfg, hop)
    rows_out  = max(1, rows // time_bins) if time_bins > 1 else rows
    blocks = fit_display_rows(
        np.asarray(spg, dtype=np.float32), rows_out,
        bin_avg=average_bins, fft_nfft=nfft, sample_rate=sample_rate,
        lo_null=cfg.lo_null, lo_bandstop=cfg.lo_bandstop,
    )
    meta = {"fft_nfft": int(nfft), "bin_avg": int(average_bins),
            "hop_size": int(hop * time_bins), "compute_backend": gpu_backend}
    # Ship striqt's own frequency coordinates so the header axis is exact for ANY
    # analysis params (trim/averaging change the bin grid in ways the header's
    # symmetric-about-DC fallback can only approximate). Additive: build_header
    # uses these when present, keeping the LV-F1 axis contract.
    try:
        freqs = striqt_shared.spectrogram_freqs(capture, spec)
        freqs = np.asarray(freqs, dtype=np.float64)
        if freqs.size >= 2:
            meta["freqs_hz_f0"]   = float(freqs[0])
            meta["freqs_hz_step"] = float(freqs[1] - freqs[0])
    except Exception:
        pass   # fall back to build_header's symmetric axis
    return blocks, meta


def make_psd_kwargs(cfg: "RadioConfig", nfft: int, sample_rate: float) -> dict:
    """Keyword arguments for striqt's `power_spectral_density` from cfg's PSD
    param block (P2b-3) — the exact spec shared by the live compute path and
    the tier-2 scratch validator, so both agree on what the backend will run.

    Args:
        cfg: `RadioConfig` supplying the `psd_*` param block (window, overlap,
            fill, integration bandwidth, LO bandstop, trim, time statistics).
        nfft: FFT size to target.
        sample_rate: Capture sample rate in Hz.

    Returns:
        Dict of kwargs ready to splat into
        `striqt_measurements.power_spectral_density`.
    """
    integration = resolve_integration_bandwidth(
        cfg.psd_integration_bandwidth, nfft, sample_rate
    )
    lo = cfg.psd_lo_bandstop
    return dict(
        window=cfg.psd_window,
        frequency_resolution=float(sample_rate) / float(nfft),
        fractional_overlap=Fraction(cfg.psd_fractional_overlap),
        window_fill=Fraction(cfg.psd_window_fill),
        integration_bandwidth=integration,
        trim_stopband=bool(cfg.psd_trim_stopband),
        lo_bandstop=(float(lo) if lo else None),
        time_statistic=tuple(cfg.psd_time_statistic),
    )


def psd_traces(samples: np.ndarray, cfg: "RadioConfig",
               prefer_gpu: bool = False) -> tuple:
    """striqt `power_spectral_density` backend (P2b-3).

    Runs a Welch-method statistic trace over the frame's whole time span, one
    output row per entry in `cfg.psd_time_statistic` (e.g. mean/max/rms) —
    unlike the other backends, rows here are statistics, not time.

    Args:
        samples: (channels, n) complex IQ samples.
        cfg: `RadioConfig` supplying `rows`, `sample_rate`, `nfft`, and the
            `psd_*` param block consumed by `make_psd_kwargs`.
        prefer_gpu: Offload the Welch computation to cupy when available.

    Returns:
        Tuple of (blocks, meta): blocks is (channels, n_statistics, bins)
        float32 dB. meta has `fft_nfft`, `bin_avg`, `hop_size`, `psd_stats`
        (the statistic names, in row order), `time_span_ms` (the true
        integrated span), and — when available — `freqs_hz_f0`/`freqs_hz_step`.

    Raises:
        RuntimeError: If the striqt analysis backend failed to import
            (`_ANALYSIS_OK` is False).
    """
    if not _ANALYSIS_OK:
        raise RuntimeError(f"PSD backend unavailable: {_ANALYSIS_ERR!r}")

    samples = np.asarray(samples, dtype=np.complex64)
    rows        = int(cfg.rows)
    sample_rate = float(cfg.sample_rate)
    nfft        = aligned_nfft(cfg.nfft)
    hop         = analysis_hop(nfft, cfg.psd_fractional_overlap)
    needed      = calibrated_sample_count(nfft, rows, hop)
    if samples.shape[1] < needed:
        pad = np.zeros((samples.shape[0], needed - samples.shape[1]), dtype=np.complex64)
        samples = np.concatenate([samples, pad], axis=1)
    else:
        samples = samples[:, -needed:]

    capture = analysis_specs.Capture(
        sample_rate=sample_rate,
        duration=needed / sample_rate,
        analysis_bandwidth=float(cfg.analysis_bandwidth),
    )
    kwargs = make_psd_kwargs(cfg, nfft, sample_rate)
    integration = kwargs["integration_bandwidth"]
    average_bins = (
        1 if integration is None
        else max(1, round(integration / (sample_rate / nfft)))
    )
    psd, _gpu = _run_array_fn(
        lambda iq: striqt_measurements.power_spectral_density(
            iq, capture, as_xarray=False, **kwargs)[0],
        samples, prefer_gpu)
    psd = np.asarray(psd, dtype=np.float32)   # (channels, n_stats, bins), dB
    blocks = fit_display_rows(
        psd, psd.shape[1],
        bin_avg=average_bins, fft_nfft=nfft, sample_rate=sample_rate,
        lo_null=cfg.lo_null, lo_bandstop=cfg.psd_lo_bandstop,
    )
    meta = {
        "fft_nfft": int(nfft), "bin_avg": int(average_bins), "hop_size": int(hop),
        "psd_stats": [str(s) for s in cfg.psd_time_statistic],
        "time_span_ms": 1e3 * needed / sample_rate,
    }
    # Exact striqt frequency coordinates, same contract as the calibrated path.
    try:
        spg_kwargs = {k: v for k, v in kwargs.items() if k != "time_statistic"}
        spg_spec = analysis_specs.Spectrogram(**spg_kwargs)
        freqs = np.asarray(striqt_shared.spectrogram_freqs(capture, spg_spec),
                           dtype=np.float64)
        if freqs.size >= 2:
            meta["freqs_hz_f0"]   = float(freqs[0])
            meta["freqs_hz_step"] = float(freqs[1] - freqs[0])
    except Exception:
        pass   # fall back to build_header's symmetric axis
    return blocks, meta


def ssb_spectrogram(samples: np.ndarray, cfg: "RadioConfig") -> tuple:
    """True symbol-aligned 5G SSB spectrogram (P2b-5).

    Runs striqt's `cellular_5g_ssb_spectrogram`, driven by cfg's SSB param
    block, one row per OFDM symbol of each discovered burst set, flattened
    (blocks*symbols) to the dashboard's flat row contract. Only reachable when
    the capture rate is on the SSB grid — `compute_blocks` pre-checks
    `ssb_grid_compatible` and runs calibrated honestly otherwise, so a grid
    mismatch here would be a caller bug; it is allowed to propagate to the
    tier-3 backstop rather than being silently swallowed into another
    analysis.

    Args:
        samples: (channels, n) complex IQ samples.
        cfg: `RadioConfig` supplying `sample_rate` and the SSB param block
            consumed by `ssb_geometry`/`make_ssb_kwargs`.

    Returns:
        Tuple of (blocks, meta): blocks is (channels, rows, bins) float32 dB,
        one row per kept OFDM symbol. meta has `fft_nfft`, `bin_avg` (always
        2, striqt integrates adjacent subcarrier-spacing bin pairs),
        `hop_size`, and — when available — `freqs_hz_f0`/`freqs_hz_step` for
        the truncated, possibly frequency-offset SSB band.

    Raises:
        RuntimeError: If the striqt analysis backend failed to import
            (`_ANALYSIS_OK` is False).
        ValueError: If `cfg.sample_rate` is not on the SSB grid for
            `cfg.ssb_subcarrier_spacing` (raised by `ssb_geometry`).
    """
    if not _ANALYSIS_OK:
        raise RuntimeError(f"SSB backend unavailable: {_ANALYSIS_ERR!r}")

    samples = np.asarray(samples, dtype=np.complex64)
    sample_rate = float(cfg.sample_rate)
    geo = ssb_geometry(cfg)   # raises off-grid — backstop-visible, never phantom

    # Trim to whole burst sets: striqt keeps the first symbol_rows of every
    # discovery period, and its blockwise reshape needs the kept row count to
    # be an exact multiple of symbol_rows.
    q = 1 + max(0, (samples.shape[1] - ssb_block_samples(geo, 1))
                // (geo["discovery_rows"] * geo["hop"]))
    q = min(q, ssb_max_blocks(cfg, geo))
    needed = ssb_block_samples(geo, q)
    if samples.shape[1] < needed:
        pad = np.zeros((samples.shape[0], needed - samples.shape[1]), dtype=np.complex64)
        samples = np.concatenate([samples, pad], axis=1)
    else:
        samples = samples[:, -needed:]

    capture = analysis_specs.Capture(
        sample_rate=sample_rate,
        duration=needed / sample_rate,
        analysis_bandwidth=float("inf"),
    )
    kwargs = make_ssb_kwargs(cfg)
    spg, _ = striqt_measurements.cellular_5g_ssb_spectrogram(
        samples, capture, as_xarray=False, **kwargs
    )
    spg = np.asarray(spg, dtype=np.float32)
    if spg.ndim == 4:
        spg = spg.reshape(spg.shape[0], spg.shape[1] * spg.shape[2], spg.shape[3])

    # Axis disclosure: the STFT runs nfft = 2·fs/scs at scs/2 resolution and
    # integrates pairs of bins (integration_bandwidth = scs), so bin_avg = 2.
    # The display-side LO null assumes DC sits at the band center, which only
    # holds at zero frequency_offset — striqt's own lo_bandstop (NaN-nulled,
    # then scrubbed) covers the true LO region in the offset case.
    blocks = fit_display_rows(
        spg, spg.shape[1],
        bin_avg=2, fft_nfft=geo["nfft"], sample_rate=sample_rate,
        lo_null=(cfg.lo_null and not cfg.ssb_frequency_offset),
        lo_bandstop=cfg.ssb_lo_bandstop,
    )
    # One display row = one OFDM symbol (hop samples). Rows across burst-set
    # boundaries jump a discovery period — the view is a burst montage, so the
    # hop labels the signal time actually shown.
    meta = {"fft_nfft": int(geo["nfft"]), "bin_avg": 2, "hop_size": int(geo["hop"])}
    # Exact striqt frequency coordinates for the truncated, offset SSB band.
    # The coordinate factory lives in a private module whose path may differ in
    # the installed striqt build — fall back to the symmetric header axis then.
    try:
        from striqt.analysis.measurements import (
            _cellular_5g_ssb_spectrogram as _ssb_mod,
        )
        spec_obj = analysis_specs.Cellular5GNRSSBSpectrogram(**kwargs)
        freqs = np.asarray(
            _ssb_mod.cellular_ssb_baseband_frequency(capture, spec_obj),
            dtype=np.float64,
        )
        if freqs.size >= 2:
            meta["freqs_hz_f0"]   = float(freqs[0])
            meta["freqs_hz_step"] = float(freqs[1] - freqs[0])
    except Exception:
        pass
    return blocks, meta


# Snap the requested FFT size to a smooth multiple of 28 that is ALSO divisible
# by 12 (so averaging_factor returns 12 consistently) and 7-smooth (2^a·3^b·7 —
# fast scipy/pocketfft sizes). Avoids the slow non-power-of-2 sizes the old
# round(n/28)·28 produced (1024→1036=2^2·7·37, 2048→2044=2^2·7·73), which drove
# the calibrated cadence and made the bin-averaging factor non-monotonic.
ALIGNED_NFFTS = (252, 504, 1008, 2016, 4032)   # 28·{9,18,36,72,144}


def aligned_nfft(nfft: int) -> int:
    """Snap a requested FFT size to the nearest entry in `ALIGNED_NFFTS`.

    Args:
        nfft: Requested FFT size.

    Returns:
        The closest value in `ALIGNED_NFFTS`.
    """
    return min(ALIGNED_NFFTS, key=lambda n: abs(n - int(nfft)))


def averaging_factor(nfft: int) -> int:
    """Largest bin-averaging factor (<= `AVG_BIN_GROUPS`) that evenly divides `nfft`.

    Args:
        nfft: FFT size.

    Returns:
        The largest integer in `[2, min(AVG_BIN_GROUPS, nfft)]` that divides
        `nfft` evenly, or 1 if none does.
    """
    for factor in range(min(AVG_BIN_GROUPS, nfft), 1, -1):
        if nfft % factor == 0:
            return factor
    return 1


def calibrated_sample_count(nfft: int, rows: int, hop=None) -> int:
    """Samples needed to produce exactly `rows` STFT rows under a given overlap.

    Each displayed row advances the STFT by `hop` samples (`nfft*15/28` at the
    default 13/28 overlap), so `rows*hop + (nfft-hop)` samples suffice —
    instead of the ~1.87x that `rows*nfft` would compute and then discard (see
    AUDIT_REPORT.md LV-W2). The formula reproduces striqt's own row count
    `int((nfft/hop)*(N/nfft-1)+1) == rows` for any hop that divides its terms.

    Args:
        nfft: STFT size.
        rows: Desired number of output rows.
        hop: Hop size in samples; defaults to the 13/28-overlap hop
            (`nfft*15/28`) when omitted.

    Returns:
        Required input sample count.
    """
    nfft = int(nfft)
    if hop is None:
        hop = (nfft * 15) // 28
    hop = max(1, int(hop))
    return int(rows * hop + (nfft - hop))


def backend_overlap(cfg: RadioConfig):
    """The fractional_overlap the executing backend's STFT actually uses (P2b-3).

    The PSD backend runs its own param block (`cfg.psd_fractional_overlap`);
    calibrated and SSB share the main spectrogram param block
    (`cfg.fractional_overlap`).

    Args:
        cfg: `RadioConfig` with `backend` set to the executing backend.

    Returns:
        The relevant fractional-overlap value from `cfg`.
    """
    return cfg.psd_fractional_overlap if cfg.backend == "psd" else cfg.fractional_overlap


def row_hop(cfg: RadioConfig) -> int:
    """Samples of signal one display row spans for cfg's backend (P2a-1).

    For the PSD backend a "row" is one STFT row feeding the statistics, so the
    duration->rows mapping controls the integrated time span (P2b-3). For the
    SSB view, `symbol_rows` display rows come from every discovery period, so
    the duration->rows mapping instead picks the burst count (P2b-5).

    Args:
        cfg: `RadioConfig` with `backend`, `sample_rate`, `nfft`, and (for SSB)
            the SSB param block.

    Returns:
        Samples of signal spanned by one display row.
    """
    if cfg.backend == "ssb" and ssb_grid_compatible(cfg.sample_rate,
                                                    cfg.ssb_subcarrier_spacing):
        geo = ssb_geometry(cfg)
        return max(1, round(geo["discovery_rows"] * geo["hop"] / geo["symbol_rows"]))
    if cfg.backend in CALIBRATED_GRID_BACKENDS:
        nfft = aligned_nfft(cfg.nfft)
        return analysis_hop(nfft, backend_overlap(cfg))
    return max(1, int(cfg.nfft))


def max_live_rows(cfg: RadioConfig) -> int:
    """Largest number of display rows the IQ ring can actually supply (P1-5).

    Replaces the old flat 300-row clamp, which pinned every long duration to
    300 rows and made the Duration control inert past ~10-20 ms. The bound is
    honest, not cosmetic: `samples_needed(rows)` must stay within
    `RING_ROW_FILL * MAX_TAIL` so the Computer's `avail >= need` gate is
    reached promptly (otherwise a too-large request would starve the
    display), and it must never exceed the absolute `MAX_ROWS_ABS` ceiling. A
    longer duration therefore renders more rows (and, on the calibrated path,
    costs more FFTs, so fps may fall — expected and left honest, since the cap
    protects the radio, not the fps).

    Args:
        cfg: `RadioConfig` with `backend`, `sample_rate`, `nfft`, and (for SSB)
            the SSB param block.

    Returns:
        Maximum row count for `cfg`'s backend/FFT size, clamped to
        `[1, MAX_ROWS_ABS]`.
    """
    limit = int(MAX_TAIL * RING_ROW_FILL)
    if cfg.backend == "ssb" and ssb_grid_compatible(cfg.sample_rate,
                                                    cfg.ssb_subcarrier_spacing):
        geo = ssb_geometry(cfg)
        rows = ssb_max_blocks(cfg, geo) * geo["symbol_rows"]
    elif cfg.backend in CALIBRATED_GRID_BACKENDS:
        nfft = aligned_nfft(cfg.nfft)
        hop  = analysis_hop(nfft, backend_overlap(cfg))
        rows = (limit - (nfft - hop)) // hop
    else:
        rows = limit // max(1, int(cfg.nfft))
    return int(max(1, min(rows, MAX_ROWS_ABS)))


def fit_display_rows(
    spg: np.ndarray,
    rows: int,
    *,
    bin_avg: int = 1,
    fft_nfft=None,
    sample_rate=None,
    lo_null: bool = True,
    lo_bandstop=SSB_LO_BANDSTOP,
) -> np.ndarray:
    """Crop/pad a striqt spectrogram to the dashboard row contract and null the LO.

    Right-crops or front-pads `spg` to exactly `rows` rows, optionally nulls
    the LO-leakage region around DC (sized to the actual configured
    bandstop rather than a fixed bin count), and always scrubs any remaining
    NaNs so downstream quantization/serialization never sees them.

    Args:
        spg: (channels, rows_in, bins) spectrogram, any dtype coercible to
            float32.
        rows: Target row count.
        bin_avg: Bins averaged per output column, used to size the LO null in
            Hz-per-bin terms.
        fft_nfft: STFT size backing `spg`'s bin axis; required (with
            `sample_rate`) to size the LO null.
        sample_rate: Capture sample rate in Hz; required (with `fft_nfft`) to
            size the LO null.
        lo_null: Whether to null the LO-leakage region at all (LV-F8); when
            False the raw DC leak is shown.
        lo_bandstop: Width in Hz of the LO region to null. `None` (or falsy)
            disables the null even if `lo_null` is True, since there is
            nothing to size it to.

    Returns:
        (channels, rows, bins) float32 array, NaN-free.

    Raises:
        RuntimeError: If `spg` is not 3-dimensional.
    """
    spg = np.asarray(spg, dtype=np.float32)
    if spg.ndim != 3:
        raise RuntimeError(f"spectrogram shape {spg.shape} is not channels x rows x bins")
    if spg.shape[1] != rows:
        spg = spg[:, -rows:, :]
        if spg.shape[1] < rows:
            fill = float(np.nanmin(spg)) if spg.size > 0 else -200.0
            pad = np.full(
                (spg.shape[0], rows - spg.shape[1], spg.shape[2]),
                fill,
                dtype=np.float32,
            )
            spg = np.concatenate([pad, spg], axis=1)

    # Null the LO leakage region, sized to the configured striqt bandstop instead
    # of a fixed ±2 bins (which hid up to ~3.7 MHz of real spectrum at coarse
    # FFTs). Optional via the lo_null flag so the center can be revealed (LV-F8).
    # With lo_bandstop None ("none" in the Analysis panel) there is no bandstop to
    # size, so the display null is skipped too — the raw DC leak shows, honestly.
    if lo_null and lo_bandstop and spg.shape[2] >= 3 and fft_nfft and sample_rate:
        step = max(1, bin_avg) * float(sample_rate) / float(fft_nfft)   # Hz per averaged bin
        half = max(1, math.ceil((float(lo_bandstop) / 2) / step))
        c = spg.shape[-1] // 2
        lo = max(0, c - half)
        hi = min(spg.shape[-1], c + half + 1)
        spg[:, :, lo:hi] = np.nanmin(spg, axis=-1, keepdims=True)

    # ALWAYS scrub remaining NaNs (striqt's null_lo leaves an all-NaN DC group) to
    # the per-row min so the quantizer and client never see NaN garbage (LV-F8/R4).
    if np.isnan(spg).any():
        row_min = np.nanmin(np.where(np.isnan(spg), np.float32(np.inf), spg), axis=-1, keepdims=True)
        row_min = np.where(np.isfinite(row_min), row_min, np.float32(-200.0))
        spg = np.where(np.isnan(spg), row_min, spg).astype(np.float32)
    return spg


def samples_needed(cfg: RadioConfig) -> int:
    """Input sample count required to produce `cfg.rows` display rows.

    Dispatches per backend: SSB needs whole burst sets (a boundary-aligned
    span), the calibrated/PSD grid backends need only the overlapped-STFT
    minimum (LV-W2), and quicklook needs exactly `nfft * rows` (no overlap).

    Args:
        cfg: `RadioConfig` with `backend`, `sample_rate`, `nfft`, `rows`, and
            (for SSB) the SSB param block.

    Returns:
        Required input sample count for `cfg`'s backend.
    """
    if cfg.backend == "ssb" and ssb_grid_compatible(cfg.sample_rate,
                                                    cfg.ssb_subcarrier_spacing):
        # Whole burst sets only (P2b-5): striqt keeps symbol_rows rows per
        # discovery period and reshapes blockwise, so the supplied span must
        # end exactly at a burst boundary. cfg.rows (duration-derived at
        # discovery_periodicity/symbol_rows per row) picks the burst count.
        geo = ssb_geometry(cfg)
        q = max(1, round(cfg.rows / geo["symbol_rows"]))
        q = min(q, ssb_max_blocks(cfg, geo))
        return ssb_block_samples(geo, q)
    if cfg.backend in CALIBRATED_GRID_BACKENDS:
        # Overlapped STFT: only rows·hop + (nfft-hop) samples are needed to
        # produce cfg.rows display rows (LV-W2), not the full nfft·rows.
        nfft = aligned_nfft(cfg.nfft)
        return calibrated_sample_count(
            nfft, cfg.rows, analysis_hop(nfft, backend_overlap(cfg))
        )
    return int(cfg.nfft * cfg.rows)


def ssb_grid_compatible(sample_rate: float,
                        subcarrier_spacing: float = SSB_SUBCARRIER_SPACING) -> bool:
    """Whether a capture rate supports the symbol-aligned SSB view at this subcarrier spacing.

    The SSB spectrogram runs at `frequency_resolution = scs/2` with
    `window_fill = 15/28`, so `nfft = 2*fs/scs` must be an integer AND a
    multiple of 28 (the `(1-15/28)*nfft` integer-zero-fill constraint — the
    audit's "30 kHz grid"). Equivalently: `fs` must be a multiple of `14*scs`.

    Args:
        sample_rate: Capture sample rate in Hz.
        subcarrier_spacing: SSB subcarrier spacing in Hz.

    Returns:
        True if `sample_rate` is on the SSB grid for `subcarrier_spacing`.
    """
    ratio = 2.0 * float(sample_rate) / float(subcarrier_spacing)
    nfft = round(ratio)
    return nfft >= 28 and abs(ratio - nfft) < 1e-6 and nfft % 28 == 0


def ssb_compatible_rate(sample_rate: float, subcarrier_spacing: float):
    """Nearest capture sample rate that satisfies the SSB grid for this spacing.

    This is the retune target used when the SSB view is selected at an
    incompatible rate (P2b-5). Candidates are multiples of `14*scs`,
    preferring those also on the radio's 1.92 MHz LTE-family grid (most
    plausibly armable — e.g. 13.44 MS/s = 7*1.92 MHz for all standard SCS),
    clamped to `SSB_MAX_RATE`.

    Args:
        sample_rate: Current/desired capture sample rate in Hz (nearest
            multiple of the grid step is chosen).
        subcarrier_spacing: SSB subcarrier spacing in Hz.

    Returns:
        The nearest SSB-grid-compatible rate in Hz, or `None` if no such rate
        exists at or below `SSB_MAX_RATE` (subcarrier spacing too large).
    """
    base = 14.0 * float(subcarrier_spacing)
    if not (base > 0 and math.isfinite(base)) or base > SSB_MAX_RATE:
        return None
    step = base
    if abs(base - round(base)) < 1e-6:
        g = math.gcd(int(round(base)), 1920000)
        lcm = int(round(base)) * (1920000 // g)
        if lcm <= SSB_MAX_RATE:
            step = float(lcm)
    k = max(1, round(float(sample_rate) / step))
    while k > 1 and k * step > SSB_MAX_RATE:
        k -= 1
    rate = k * step
    return float(rate) if rate <= SSB_MAX_RATE else None


def make_ssb_kwargs(cfg: "RadioConfig") -> dict:
    """Keyword arguments for striqt's `cellular_5g_ssb_spectrogram` from cfg's
    SSB param block (P2b-5) — shared by the live compute path and the tier-2
    scratch validator, so both agree on what the backend will run.

    Args:
        cfg: `RadioConfig` supplying the `ssb_*` param block (subcarrier
            spacing, output sample rate, discovery periodicity, frequency
            offset, max block count, window, LO bandstop).

    Returns:
        Dict of kwargs ready to splat into
        `striqt_measurements.cellular_5g_ssb_spectrogram`.
    """
    return dict(
        subcarrier_spacing=float(cfg.ssb_subcarrier_spacing),
        # striqt truncates the frequency axis to this output rate; it can never
        # exceed the sampled span.
        sample_rate=min(float(cfg.ssb_sample_rate), float(cfg.sample_rate)),
        discovery_periodicity=float(cfg.ssb_discovery_periodicity),
        frequency_offset=float(cfg.ssb_frequency_offset),
        max_block_count=(int(cfg.ssb_max_block_count)
                         if cfg.ssb_max_block_count else None),
        window=cfg.ssb_window,
        lo_bandstop=(float(cfg.ssb_lo_bandstop) if cfg.ssb_lo_bandstop else None),
    )


def ssb_geometry(cfg: "RadioConfig", sample_rate=None) -> dict:
    """Row/sample geometry of the symbol-aligned SSB spectrogram (P2b-5).

    striqt runs the STFT at `frequency_resolution = scs/2` with a 13/28
    overlap, making one row per OFDM symbol; each discovery period
    contributes the first `symbol_rows` symbols (one burst set, always 2 ms
    of signal).

    Args:
        cfg: `RadioConfig` supplying `ssb_subcarrier_spacing` and
            `ssb_discovery_periodicity` (and `sample_rate` when the
            `sample_rate` arg is omitted).
        sample_rate: Capture sample rate in Hz; defaults to `cfg.sample_rate`.

    Returns:
        Dict with:

        - `nfft`: STFT size, `2*fs/scs`.
        - `hop`: Samples per symbol row, `nfft*15/28`.
        - `symbol_rows`: Rows kept per burst set, `28*scs/15e3`.
        - `discovery_rows`: Rows spanning one discovery period.

    Raises:
        ValueError: If the rate/subcarrier-spacing combination is off the
            SSB grid (see `ssb_grid_compatible`).
    """
    fs  = float(sample_rate if sample_rate is not None else cfg.sample_rate)
    scs = float(cfg.ssb_subcarrier_spacing)
    if not ssb_grid_compatible(fs, scs):
        raise ValueError(
            f"sample rate {fs/1e6:g} MS/s is not on the SSB grid for "
            f"subcarrier spacing {scs/1e3:g} kHz (2·fs/scs must be a 28-multiple)"
        )
    nfft = round(2.0 * fs / scs)
    hop  = (nfft * 15) // 28
    symbol_rows = max(1, round(28.0 * scs / 15e3))
    discovery_rows = max(symbol_rows,
                         round(float(cfg.ssb_discovery_periodicity) * fs / hop))
    return {"nfft": nfft, "hop": hop,
            "symbol_rows": symbol_rows, "discovery_rows": discovery_rows}


def ssb_block_samples(geo: dict, blocks: int) -> int:
    """Samples that yield exactly `blocks` complete burst sets.

    Computed as `(blocks-1)` full discovery periods, plus the final burst's
    kept symbol rows, plus the STFT tail (`nfft - hop`).

    Args:
        geo: Geometry dict from `ssb_geometry`.
        blocks: Number of burst sets desired; clamped to at least 1.

    Returns:
        Required input sample count.
    """
    q = max(1, int(blocks))
    return int((q - 1) * geo["discovery_rows"] * geo["hop"]
               + geo["symbol_rows"] * geo["hop"]
               + (geo["nfft"] - geo["hop"]))


def ssb_max_blocks(cfg: "RadioConfig", geo: dict) -> int:
    """Most burst sets one frame can hold.

    Bounded by the ring (same `RING_ROW_FILL` budget as `max_live_rows`) and
    by `cfg.ssb_max_block_count` when set.

    Args:
        cfg: `RadioConfig`; `ssb_max_block_count` caps the result when truthy.
        geo: Geometry dict from `ssb_geometry`.

    Returns:
        Maximum burst-set count, at least 1.
    """
    limit = int(MAX_TAIL * RING_ROW_FILL)
    per_extra = geo["discovery_rows"] * geo["hop"]
    q = 1 + max(0, (limit - ssb_block_samples(geo, 1)) // max(1, per_extra))
    if cfg.ssb_max_block_count:
        q = min(q, max(1, int(cfg.ssb_max_block_count)))
    return int(max(1, q))


def compute_blocks(samples: np.ndarray, cfg: RadioConfig):
    """Dispatch to the configured backend, degrading honestly when it can't run.

    Two substitutions are possible, both disclosed via `meta["backend"]` /
    `meta["backend_requested"]` rather than silently rendered as the
    requested view: SSB falls back to calibrated when the capture rate isn't
    on the SSB grid (transient during a retune, or an unreachable rate); any
    striqt-backed backend falls back to quicklook when striqt itself failed
    to import.

    Args:
        samples: (channels, n) complex IQ samples.
        cfg: `RadioConfig` naming the requested `backend` plus that backend's
            param block.

    Returns:
        Tuple of (blocks, meta): blocks is (channels, rows, bins) float32.
        meta carries the per-frame axis parameters (`fft_nfft`, `bin_avg`,
        etc.) plus `backend` (what actually ran) and `backend_requested`
        (what was asked for), consumed by `build_header` to ship an honest
        frame header (LV-F1/F2).
    """
    requested = cfg.backend
    effective = requested
    if requested == "ssb" and not ssb_grid_compatible(cfg.sample_rate,
                                                      cfg.ssb_subcarrier_spacing):
        # SSB needs the capture rate on the 14·scs grid. Selecting SSB retunes
        # to a compatible rate (P2b-5), so this only covers the transient (or a
        # rate the retune could not reach): run calibrated and REPORT it via
        # backend/backend_requested — never a phantom SSB view (LV-F2).
        effective = "calibrated"
    if effective in CALIBRATED_GRID_BACKENDS and not _ANALYSIS_OK:
        # striqt isn't importable on this host (demo laptops, broken installs).
        # The old dispatch raised here on EVERY tick; the compute backstop had
        # no analysis param to revert, so the viewer froze in a silent error
        # loop. Run the dependency-free quicklook and REPORT the substitution
        # (LV-F2) — the client banner already renders backend_requested.
        effective = "quicklook"
    if effective == "calibrated":
        blocks, meta = calibrated_spectrogram(samples, cfg)
        executed = "calibrated"
    elif effective == "psd":
        blocks, meta = psd_traces(samples, cfg)
        executed = "psd"
    elif effective == "ssb":
        blocks, meta = ssb_spectrogram(samples, cfg)
        executed = "ssb"
    else:
        # When quicklook substitutes for a striqt backend, cfg.rows was derived
        # for the OVERLAPPED grid and needs more samples than quicklook's
        # non-overlapping rows·nfft — honest row count instead of zero-padding
        # the bottom of the display dark.
        rows = (max(1, samples.shape[1] // int(cfg.nfft))
                if requested != "quicklook" else cfg.rows)
        blocks, meta = db_spectrogram(samples, cfg.nfft, rows)
        executed = "quicklook"
    meta["backend"] = executed
    meta["backend_requested"] = requested
    return blocks, meta


# ---------------------------------------------------------------------------
# AHAWI: one coherent capture, analyzed once, replayed in aligned segments
# ---------------------------------------------------------------------------
# The rolling live view recomputes a short window per display tick, so
# consecutive frames are neither contiguous nor phase-aligned with the signal's
# frame structure — TDD slots and SSB bursts "swim". AHAWI instead analyzes one
# contiguous multi-segment capture with striqt (its intended usage) and the
# client replays the result one viewing window at a time. With the segment
# length equal to the burst period (5G SSB: 20 ms), every segment shows the
# burst at the same row.

# Backends AHAWI wraps. PSD's rows are statistics (not time) and SSB has its
# own burst geometry — both are already coherent views, so AHAWI stays out.
AHAWI_BACKENDS = frozenset({"calibrated", "quicklook"})

# ── GPU offload for capture-sized striqt analysis ───────────────────────────
# The AIR-T analyzes captures orders of magnitude faster on cupy; a laptop has
# no cupy and must silently use numpy. Availability is probed once; ANY GPU
# failure at compute time falls back to numpy for that call and disables cupy
# for the rest of the process (never trade the viewer for an optimization).
_GPU = {"probed": False, "cp": None}


def _gpu_module():
    """Lazily probe for a working cupy install and cache the result.

    The probe runs at most once per process: `cupy.zeros(1)` touches the
    device so import-time or driver failures surface here rather than mid
    computation. A failed probe caches `None`, so later calls skip straight
    to numpy without re-attempting the import.

    Returns:
        The `cupy` module if import and device touch succeeded, else `None`.
    """
    if not _GPU["probed"]:
        _GPU["probed"] = True
        try:
            import cupy
            cupy.zeros(1)   # touch the device so failures surface here
            _GPU["cp"] = cupy
        except Exception:
            _GPU["cp"] = None
    return _GPU["cp"]


def _run_array_fn(fn, samples, prefer_gpu):
    """Run `fn(iq_array) -> array` on the GPU when asked and available.

    Any GPU failure at call time falls back to numpy for that call AND
    disables cupy for the rest of the process (`_GPU["cp"] = None`) — a GPU
    hiccup must never repeatedly cost the viewer a frame.

    Args:
        fn: Callable taking one array (numpy or cupy) and returning an array
            in the same namespace; true for striqt measurements, which
            follow their input's array module.
        samples: Input IQ array (numpy).
        prefer_gpu: Whether to attempt cupy at all for this call.

    Returns:
        Tuple of (numpy_result, backend_str) where `backend_str` is `"cupy"`
        or `"numpy"`.
    """
    if prefer_gpu:
        cp = _gpu_module()
        if cp is not None:
            try:
                return cp.asnumpy(fn(cp.asarray(samples))), "cupy"
            except Exception as e:
                _GPU["cp"] = None
                print(f"[dsp] cupy analysis failed ({e!r}); numpy from now on")
    return np.asarray(fn(samples)), "numpy"


def ahawi_executed_backend(cfg: RadioConfig) -> str:
    """The backend an AHAWI capture will actually run (honest fallback).

    Mirrors `compute_blocks`'s striqt-unavailable fallback for the two
    backends AHAWI wraps: calibrated when requested and striqt is importable,
    quicklook otherwise.

    Args:
        cfg: `RadioConfig` with `backend`.

    Returns:
        `"calibrated"` or `"quicklook"`.
    """
    if cfg.backend == "calibrated" and _ANALYSIS_OK:
        return "calibrated"
    return "quicklook"


def ahawi_plan(cfg: RadioConfig) -> dict:
    """Segmentation plan for one AHAWI capture. Pure arithmetic, no I/O.

    Segment length is `cfg.duration` (the viewing window; 20 ms when unset).
    Rows-per-segment is hop-exact so segment boundaries land on STFT row
    boundaries; the capture is `segments` windows plus — when alignment is
    on — one extra segment of slack the align step may trim from the front.
    Everything is clamped to fit the ring under the same honest bound the
    live view uses (`RING_ROW_FILL * MAX_TAIL`, `MAX_ROWS_ABS`), so an
    oversized custom duration can never plan a capture the ring could never
    supply.

    Args:
        cfg: `RadioConfig` supplying `sample_rate`, `nfft`, `duration`,
            `fractional_overlap`, `ahawi_align`, and `ahawi_capture_ms`.

    Returns:
        Dict with `backend`, `nfft`, `hop`, `rows_per_seg`, `segments`,
        `align` (possibly downgraded to False when segments < 2),
        `extra_rows`, `total_rows`, `need_samples`, and the disclosed
        executed spans `segment_ms`/`capture_ms` (which may differ from the
        requested `ahawi_capture_ms` after clamping).
    """
    executed = ahawi_executed_backend(cfg)
    fs = float(cfg.sample_rate)
    if executed == "calibrated":
        nfft = aligned_nfft(cfg.nfft)
        hop  = analysis_hop(nfft, cfg.fractional_overlap)
    else:
        nfft = int(cfg.nfft)
        hop  = nfft

    seg_s = float(cfg.duration) if float(cfg.duration) > 0 else 0.02
    rows_per_seg = max(1, round(seg_s * fs / hop))

    limit = int(MAX_TAIL * RING_ROW_FILL)
    max_total_rows = max(1, min((limit - (nfft - hop)) // hop, MAX_ROWS_ABS))

    # A huge viewing window (DAN's custom duration accepts anything) must not
    # plan a capture the ring can never hold — the Computer's avail >= need
    # gate would simply never pass and AHAWI would starve with no message.
    # Clamp the SEGMENT to the ring like the live view clamps rows (P1-5).
    rows_per_seg = min(rows_per_seg, max_total_rows)

    align = bool(cfg.ahawi_align)
    extra_rows = rows_per_seg if align else 0
    max_segments = max(1, (max_total_rows - extra_rows) // rows_per_seg)

    capture_ms = min(max(float(cfg.ahawi_capture_ms), AHAWI_MIN_CAPTURE_MS),
                     AHAWI_MAX_CAPTURE_MS)
    requested = max(1, round((capture_ms / 1e3) / seg_s))
    segments = int(min(requested, max_segments, AHAWI_MAX_SEGMENTS))
    if segments < 2:
        # One segment can't be aligned (nothing to fold against) and the extra
        # slack would be pure waste.
        align, extra_rows = False, 0

    total_rows = segments * rows_per_seg + extra_rows
    if executed == "calibrated":
        need = calibrated_sample_count(nfft, total_rows, hop)
    else:
        need = total_rows * nfft
    return {
        "backend":       executed,
        "nfft":          nfft,
        "hop":           hop,
        "rows_per_seg":  int(rows_per_seg),
        "segments":      segments,
        "align":         align,
        "extra_rows":    int(extra_rows),
        "total_rows":    int(total_rows),
        "need_samples":  int(need),
        # Executed spans (disclosed — may differ from the requested ms).
        "segment_ms":    1e3 * rows_per_seg * hop / fs,
        "capture_ms":    1e3 * segments * rows_per_seg * hop / fs,
    }


def ahawi_power_plan(segments: int, seg_samples: int, sample_rate: float):
    """Detector geometry for the capture's channel-power time series.

    Aims for ~64 points per segment (0.3 ms resolution at 20 ms segments —
    NR slots are 0.5 ms) but caps the whole series near 2048 points so the
    header JSON stays small at high segment counts.

    Args:
        segments: Number of AHAWI segments in the capture.
        seg_samples: Samples per segment (`rows_per_seg * hop`).
        sample_rate: Capture sample rate in Hz.

    Returns:
        Tuple of (detector_period, window_samples): `detector_period` is an
        exact `Fraction` of seconds per detector sample, suitable for striqt's
        `detector_period` kwarg; `window_samples` is the same period in
        samples.
    """
    per_seg = int(min(64, max(16, 2048 // max(1, segments))))
    window = max(1, seg_samples // per_seg)
    return Fraction(window, int(round(sample_rate))), window


def channel_power_series(samples: np.ndarray, cfg: "RadioConfig",
                         detector_period: Fraction,
                         prefer_gpu: bool = False) -> tuple:
    """striqt `channel_power_time_series` over the given IQ span.

    Computes per-channel rms/peak power in dB at `detector_period`
    resolution — the series that drives the AHAWI power strip.

    Args:
        samples: (channels, n) complex IQ samples.
        cfg: `RadioConfig` supplying `sample_rate` and `analysis_bandwidth`.
        detector_period: Detector sample period as an exact `Fraction` of
            seconds (from `ahawi_power_plan`).
        prefer_gpu: Offload the computation to cupy when available.

    Returns:
        Tuple of (series, detectors): series is (channels, detectors, points)
        float32 dB; detectors is `("rms", "peak")`.

    Raises:
        RuntimeError: If the striqt analysis backend failed to import
            (`_ANALYSIS_OK` is False).
    """
    if not _ANALYSIS_OK:
        raise RuntimeError(f"channel power unavailable: {_ANALYSIS_ERR!r}")
    samples = np.asarray(samples, dtype=np.complex64)
    fs = float(cfg.sample_rate)
    capture = analysis_specs.Capture(
        sample_rate=fs,
        duration=samples.shape[1] / fs,
        analysis_bandwidth=float(cfg.analysis_bandwidth),
    )
    detectors = ("rms", "peak")
    series, _gpu = _run_array_fn(
        lambda iq: striqt_measurements.channel_power_time_series(
            iq, capture, as_xarray=False,
            detector_period=detector_period, power_detectors=detectors)[0],
        samples, prefer_gpu)
    return np.asarray(series, dtype=np.float32), detectors


def ahawi_align_offset(blocks: np.ndarray, rows_per_seg: int, segments: int):
    """Burst-phase alignment for AHAWI: find the row offset that lines up a periodic burst.

    Folds per-row power modulo the segment length and locates the periodic
    burst so it can be shifted to sit at the same row in every segment.

    The fold runs on RESIDUAL power — each bin's stationary level (median
    over time) is subtracted first. Constant carriers (CW tones, an occupied
    channel) otherwise raise every row's mean power and bury a genuine burst
    below any contrast gate: the demo's default tones left the old
    mean-over-bins metric at ~3.3 dB against a 3.0 dB gate, i.e. aligned by
    luck. In the residual domain only time-VARYING energy survives, so a real
    burst clears the gate by orders of magnitude and pure noise folds flat.

    Args:
        blocks: (channels, rows, bins) dB spectrogram; only the first
            `rows_per_seg * segments` rows are used.
        rows_per_seg: Rows per AHAWI segment.
        segments: Number of segments in the capture.

    Returns:
        Tuple of (offset_rows, aligned, contrast_db). `offset_rows` is in
        `[0, rows_per_seg)` and is how many rows to trim from the capture's
        front. `aligned` is False (offset forced to 0) when there are too few
        segments/rows to fold, or when the folded residual has no convincing
        periodic burst — aligning then would just add caprice. `contrast_db`
        is always reported (even when unaligned) so the verdict is
        diagnosable from the frame header instead of a mystery.
    """
    if segments < 2 or rows_per_seg < 4:
        return 0, False, 0.0
    usable = rows_per_seg * segments
    if blocks.ndim != 3 or blocks.shape[1] < usable:
        return 0, False, 0.0
    # dB→linear matters: a 30 dB burst must dominate the fold, not average
    # away. Per-channel float32 keeps the transient footprint to one channel's
    # worth of arrays — the whole-capture float64 version peaked at several
    # hundred MB at max geometry (4096 rows × 4096 bins × 2ch), which is real
    # memory pressure on the Jetson's shared CPU/GPU pool.
    row_power = np.zeros(usable, dtype=np.float64)
    for ch in range(blocks.shape[0]):
        lin = np.power(10.0, blocks[ch, :usable, :].astype(np.float32) / 10.0)
        stationary = np.median(lin, axis=0, keepdims=True)   # per bin
        np.clip(lin - stationary, 0.0, None, out=lin)
        row_power += lin.mean(axis=1, dtype=np.float64)
    folded = row_power.reshape(segments, rows_per_seg).mean(axis=0)
    # Light circular smoothing so a one-row glitch can't win the argmax.
    k = max(1, rows_per_seg // 64)
    if k > 1:
        kernel = np.ones(k) / k
        wrapped = np.concatenate([folded[-k:], folded, folded[:k]])
        folded = np.convolve(wrapped, kernel, mode="same")[k:-k]
    peak = int(np.argmax(folded))
    baseline = float(np.median(folded))
    contrast_db = 10.0 * math.log10(
        max(float(folded[peak]), 1e-30) / max(baseline, 1e-30))
    if contrast_db < AHAWI_ALIGN_MIN_DB:
        return 0, False, round(contrast_db, 1)
    target = int(rows_per_seg * AHAWI_ALIGN_TARGET)
    return int((peak - target) % rows_per_seg), True, round(contrast_db, 1)


def ahawi_capture(samples: np.ndarray, cfg: RadioConfig, plan: dict):
    """Analyze one coherent AHAWI capture the way striqt is meant to be used.

    Three steps:

      1. Full-span spectrogram (striqt calibrated when available).
      2. Burst alignment + trim to exactly `segments * rows_per_seg` rows.
      3. The striqt measurement BUNDLE over the trimmed (displayed) span —
         power_spectral_density statistics and channel_power_time_series —
         mirroring what the recorder computes per capture.

    Args:
        samples: (channels, n) complex IQ samples spanning the whole
            coherent capture (`plan["need_samples"]` or more).
        cfg: `RadioConfig` for the requested backend and its param block.
        plan: Geometry dict from `ahawi_plan`.

    Returns:
        Tuple of (blocks, meta) in the normal `compute_blocks` contract, with
        `meta["ahawi"]` additionally carrying replay geometry, the bundle
        results, the list of measurements that actually ran, and the compute
        backend (cupy/numpy). Bundle failures never kill the capture — a
        failing measurement just drops off the disclosed `measurements` list
        (and logs why) instead of raising.
    """
    started = time.perf_counter()
    total_rows = plan["total_rows"]
    if plan["backend"] == "calibrated":
        # time_aperture would average away exactly the time structure AHAWI
        # exists to show; duration=0 so nothing re-derives rows underneath us.
        cfg_capture = replace(cfg, rows=total_rows, backend="calibrated",
                              time_aperture=None, duration=0.0)
        blocks, meta = calibrated_spectrogram(samples, cfg_capture,
                                              prefer_gpu=True)
        measurements = ["spectrogram"]
    else:
        blocks, meta = db_spectrogram(samples, plan["nfft"], total_rows)
        meta["compute_backend"] = "numpy"
        measurements = ["quicklook"]
    blocks = np.asarray(blocks, dtype=np.float32)

    rows_per_seg, segments = plan["rows_per_seg"], plan["segments"]
    offset, aligned, contrast_db = (
        ahawi_align_offset(blocks, rows_per_seg, segments)
        if plan["align"] else (0, False, 0.0))
    if offset > plan["extra_rows"]:
        # Never read past the capture: alignment is capped by the slack rows.
        offset, aligned = plan["extra_rows"], aligned
    rows_disp = segments * rows_per_seg
    blocks = blocks[:, offset:offset + rows_disp, :]

    meta["backend"] = plan["backend"]
    meta["backend_requested"] = cfg.backend
    ahawi = {
        "segments":          segments,
        "rows_per_segment":  rows_per_seg,
        "segment_ms":        round(plan["segment_ms"], 3),
        "capture_ms":        round(plan["capture_ms"], 3),
        "aligned":           bool(aligned),
        # User INTENT, not the plan's degraded value — the badge distinguishes
        # "align off" (unchecked) from "n/a — single segment" (nothing to fold).
        "align_requested":   bool(cfg.ahawi_align),
        "align_offset_rows": int(offset),
        "align_contrast_db": float(contrast_db),
        "compute_backend":   str(meta.get("compute_backend", "numpy")),
    }

    # ── striqt measurement bundle over the DISPLAYED span ──────────────────
    # Row r covers samples [r·hop, r·hop+nfft), so the trimmed rows map to an
    # exact sample slice: every bundled measurement describes precisely the
    # signal on screen, not the pre-alignment slack.
    if _ANALYSIS_OK:
        hop = plan["hop"]
        start = offset * hop
        span = (calibrated_sample_count(plan["nfft"], rows_disp, hop)
                if plan["backend"] == "calibrated" else rows_disp * plan["nfft"])
        span_samples = samples[:, start:start + span]
        seg_samples = rows_per_seg * hop

        try:
            psd_blocks, psd_meta = psd_traces(
                span_samples,
                replace(cfg, rows=rows_disp, duration=0.0), prefer_gpu=True)
            psd_blocks = np.asarray(psd_blocks, dtype=np.float32)
            if psd_blocks.shape[2] <= 2048:   # keep the JSON header sane
                ahawi["psd"] = {
                    "stats":  list(psd_meta.get("psd_stats") or []),
                    "bins":   int(psd_blocks.shape[2]),
                    "f0":     psd_meta.get("freqs_hz_f0"),
                    "step":   psd_meta.get("freqs_hz_step"),
                    "traces": np.round(psd_blocks, 2).tolist(),
                }
            measurements.append("psd")
        except Exception as e:
            print(f"[ahawi] psd measurement skipped: {e}")

        try:
            detector_period, _win = ahawi_power_plan(
                segments, seg_samples, cfg.sample_rate)
            series, detectors = channel_power_series(
                span_samples, cfg, detector_period, prefer_gpu=True)
            ahawi["power"] = {
                "period_ms": round(1e3 * float(detector_period), 4),
                "detectors": list(detectors),
                "series":    np.round(series, 1).tolist(),
            }
            measurements.append("channel_power")
        except Exception as e:
            print(f"[ahawi] channel power skipped: {e}")

    ahawi["measurements"] = measurements
    ahawi["compute_ms"] = round(1e3 * (time.perf_counter() - started), 1)
    meta["ahawi"] = ahawi
    return blocks, meta


def build_header(cfg: RadioConfig, blocks: list, meta: dict, demo: bool = False) -> dict:
    """Assemble the frame header from cfg plus the per-frame backend meta.

    Ships the TRUE frequency axis (`freqs_hz_f0`/`freqs_hz_step`) and the
    executed backend so the client never has to guess it (LV-F1/F2);
    `fft_nfft`/`bin_avg` disclose the real FFT size and bin-averaging behind
    the reported `nfft` bin count. This is the frame-header contract every
    live frontend relies on — see `core.serialization` for how it's packed
    onto the wire alongside `blocks`.

    Args:
        cfg: `RadioConfig` for the frame (center, sample_rate, gain, backend).
        blocks: List of per-channel (rows, bins) arrays; only `blocks[0]`'s
            shape is inspected (all channels share shape).
        meta: Backend meta dict from `compute_blocks`/`ahawi_capture` (keys
            like `backend`, `backend_requested`, `fft_nfft`, `bin_avg`,
            `hop_size`, `freqs_hz_f0`/`freqs_hz_step`, `psd_stats`,
            `time_span_ms`, `ahawi`).
        demo: Whether this frame came from the demo (no-hardware) source;
            when True, adds `header["demo"] = True`.

    Returns:
        Header dict with `center`, `fs`, `gain`, `nfft`, `rows`, `shape`,
        `channels`, `device`, `backend`, `fft_nfft`, `bin_avg`,
        `freqs_hz_f0`, `freqs_hz_step`, `hop_size`, `time`, plus the additive
        keys `psd_stats`/`time_span_ms` (PSD backend), `ahawi` (AHAWI
        capture), and `backend_requested` (only when it differs from the
        executed backend).
    """
    first = np.asarray(blocks[0], dtype=np.float32)
    rows, bins = first.shape
    fs = float(cfg.sample_rate)
    executed = str(meta.get("backend", cfg.backend))
    fft_nfft = int(meta.get("fft_nfft", bins)) or int(bins)
    bin_avg  = int(meta.get("bin_avg", 1)) or 1

    # Frequency axis. Prefer the exact coordinates striqt computed for the
    # executed spec (calibrated path, P2a-1) — correct for any overlap/averaging/
    # trim combination. Fallback: quicklook is a plain fftshifted FFT (bin 0 =
    # -fs/2); the calibrated/ssb path DC-centers bin_avg-wide averaged groups, so
    # their centers are symmetric about DC with step = bin_avg*fs/fft_nfft.
    if meta.get("freqs_hz_f0") is not None and meta.get("freqs_hz_step") is not None:
        f0   = float(meta["freqs_hz_f0"])
        step = float(meta["freqs_hz_step"])
    else:
        step = bin_avg * fs / fft_nfft
        if executed == "quicklook":
            f0 = -fs / 2.0
        else:
            f0 = -(bins - 1) / 2.0 * step

    header = {
        "center":        float(cfg.center),
        "fs":            fs,
        "gain":          float(cfg.gain),
        "nfft":          int(bins),
        "rows":          int(rows),
        "shape":         [int(rows), int(bins)],
        "channels":      list(state.CHANNELS),
        "device":        state.DEVICE_LABEL,
        "backend":       executed,
        "fft_nfft":      fft_nfft,
        "bin_avg":       bin_avg,
        "freqs_hz_f0":   float(f0),
        "freqs_hz_step": float(step),
        # Samples of signal one display row spans (additive, P2a-1). Lets the
        # client label the time axis exactly for any fractional_overlap instead
        # of assuming the 15/28 hop.
        "hop_size":      int(meta.get("hop_size", fft_nfft) or fft_nfft),
        "time":          time.time(),
    }
    # PSD-backend extras (P2b-3, additive): the statistic behind each block row
    # and the true integrated time span (block rows are statistics, not time,
    # so the hop-based window label doesn't apply).
    if meta.get("psd_stats") is not None:
        header["psd_stats"] = list(meta["psd_stats"])
    if meta.get("time_span_ms") is not None:
        header["time_span_ms"] = float(meta["time_span_ms"])
    # AHAWI replay geometry (additive): tells the client this frame is one
    # coherent capture to slice into rows_per_segment windows client-side.
    if meta.get("ahawi") is not None:
        header["ahawi"] = dict(meta["ahawi"])
    requested = str(meta.get("backend_requested", executed))
    if requested != executed:
        header["backend_requested"] = requested
    if demo:
        header["demo"] = True
    return header

