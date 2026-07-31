"""Freedom-model input parsing and tier-2 scratch validators.

Part of the shared `live/core/` package: this module backs the "freedom
model" that lets DAN mode accept arbitrary analysis-panel input without a
built-in guardrail. It supplies two kinds of helpers that `core.config`'s
`SharedConfig._validate_analysis` composes into its three-tier legality
check:

  * Structure-only parsers (`_parse_window`, `_parse_fraction`,
    `_parse_optional_hz`, `_parse_time_statistic`, `_parse_optional_seconds`)
    that normalize wire values into the shapes striqt expects. They never
    judge legality — only whether the input has the right shape — so tier 1
    (knowable clamp/snap rules) and tier 2 can each apply their own rules to
    a well-formed value.
  * Tier-2 "scratch" validators (`scratch_validate_spectrogram`,
    `scratch_validate_psd`, `scratch_validate_ssb`, and the dispatcher
    `scratch_validate_analysis`) that judge a proposed `RadioConfig` the only
    way that is always right: by handing it to the real striqt analysis
    functions against a tiny synthetic buffer, never the live ring or
    acquirer.

`ANALYSIS_TARGETS` is the freedom-model's per-analysis registry (which wire
fields map to which `RadioConfig` attributes, and the tier-2 application
order); `ANALYSIS_CFG_KEYS` and `ANALYSIS_DEFAULTS` are derived from it.
Extracted verbatim from striqt_web_server.py during the 2026-07 refactor.

Note: this module drives striqt's `analysis_specs`/`striqt_shared`/
`striqt_measurements` entry points via `core.striqt_compat`. Per the repo's
top-level CLAUDE.md, the vendored `striqt/` tree in this repo is a later
snapshot than what actually runs on the radio (pinned v0.7.0, commit
2e7696d) — the behavior documented here is the observed behavior of the
pinned build, not necessarily the vendored source.
"""
from __future__ import annotations

import math
import warnings
from fractions import Fraction

import numpy as np

from .constants import (
    DEFAULT_WINDOW, DEFAULT_FRACTIONAL_OVERLAP, DEFAULT_WINDOW_FILL,
    DEFAULT_INTEGRATION_BW, DEFAULT_LO_BANDSTOP, DEFAULT_TRIM_STOPBAND,
    DEFAULT_PSD_TIME_STATISTIC, SSB_SUBCARRIER_SPACING, SSB_SAMPLE_RATE,
    SSB_DISCOVERY_PERIOD, SSB_WINDOW, SSB_LO_BANDSTOP,
)
from .striqt_compat import (
    analysis_specs, striqt_measurements, striqt_shared, _ANALYSIS_OK,
)
from .dsp import (
    aligned_nfft, analysis_hop, calibrated_sample_count, make_analysis_spec,
    make_psd_kwargs, make_ssb_kwargs, ssb_geometry, ssb_block_samples,
)

# ---------------------------------------------------------------------------
# Freedom-model input parsing (P2a-2)
# ---------------------------------------------------------------------------
#
# DAN mode has no input guardrail — the user can type anything — so these
# parsers only normalize *structure* (they never judge legality). Legality is
# decided by tier 1 (knowable rules → snap and tell) and tier 2 (striqt itself,
# via scratch_validate_analysis) in SharedConfig._validate_analysis.

# Freedom-model analysis targets (P2b-1). Each target names one striqt analysis
# whose parameter block is editable from the DAN-mode Analysis panel; the same
# three tiers (snap & tell / scratch-validate / compute backstop) govern all of
# them. A control message routes with {"analysis": {"target": <name>, ...}};
# no target means "spectrogram" (the P2a wire format, unchanged).
#   fields:  message field name -> RadioConfig attribute
#   virtual: message fields validated here that map onto a non-analysis cfg key
#            (frequency_resolution is the second view of nfft)
#   order:   tier-2 one-at-a-time application order (RadioConfig keys)
ANALYSIS_TARGETS = {
    "spectrogram": {
        "fields": {
            "window":                "window",
            "fractional_overlap":    "fractional_overlap",
            "window_fill":           "window_fill",
            "integration_bandwidth": "integration_bandwidth",
            "lo_bandstop":           "lo_bandstop",
            "trim_stopband":         "trim_stopband",
            "time_aperture":         "time_aperture",
        },
        "virtual": ("frequency_resolution",),
        # time_aperture goes last: its legality depends on the overlap/nfft this
        # same message may be changing (the hop grid).
        "order": ("nfft", "window", "fractional_overlap", "window_fill",
                  "integration_bandwidth", "lo_bandstop", "trim_stopband",
                  "time_aperture"),
        # Cleared on the tier-2 working copy while earlier fields probe, when a
        # replacement value is accepted: time_aperture rides the hop grid that
        # nfft/overlap define, so probing those with the STALE aperture attached
        # would falsely reject them; the fresh aperture re-probes at its own turn.
        "probe_reset": ("time_aperture",),
    },
    # striqt power_spectral_density (P2b-3): the Welch-method statistic traces.
    # Own parameter block (psd_* cfg keys) so tuning the PSD view never
    # disturbs the spectrogram recipe, per-analysis-panel intent.
    "psd": {
        "fields": {
            "window":                "psd_window",
            "fractional_overlap":    "psd_fractional_overlap",
            "window_fill":           "psd_window_fill",
            "integration_bandwidth": "psd_integration_bandwidth",
            "lo_bandstop":           "psd_lo_bandstop",
            "trim_stopband":         "psd_trim_stopband",
            "time_statistic":        "psd_time_statistic",
        },
        "virtual": ("frequency_resolution",),
        "order": ("nfft", "psd_window", "psd_fractional_overlap",
                  "psd_window_fill", "psd_integration_bandwidth",
                  "psd_lo_bandstop", "psd_trim_stopband", "psd_time_statistic"),
    },
    # striqt cellular_5g_ssb_spectrogram (P2b-5): the symbol-aligned SSB burst
    # view. subcarrier_spacing goes first — it defines the grid every other
    # field (and the capture sample-rate retune) is judged against.
    "ssb": {
        "fields": {
            "subcarrier_spacing":    "ssb_subcarrier_spacing",
            "sample_rate":           "ssb_sample_rate",
            "discovery_periodicity": "ssb_discovery_periodicity",
            "frequency_offset":      "ssb_frequency_offset",
            "max_block_count":       "ssb_max_block_count",
            "window":                "ssb_window",
            "lo_bandstop":           "ssb_lo_bandstop",
        },
        "virtual": (),
        "order": ("ssb_subcarrier_spacing", "ssb_sample_rate",
                  "ssb_discovery_periodicity", "ssb_frequency_offset",
                  "ssb_max_block_count", "ssb_window", "ssb_lo_bandstop"),
    },
}

# RadioConfig fields that are only settable through the validated "analysis"
# block (the union across targets). Stripped from the top level of every
# control message so no client can bypass the freedom model.
ANALYSIS_CFG_KEYS = frozenset(
    cfg_key
    for target in ANALYSIS_TARGETS.values()
    for cfg_key in target["fields"].values()
)

# Hard-default analysis values — the final revert target for the P2a-3 backstop
# (identical to the RadioConfig field defaults).
ANALYSIS_DEFAULTS = {
    "window":                DEFAULT_WINDOW,
    "fractional_overlap":    DEFAULT_FRACTIONAL_OVERLAP,
    "window_fill":           DEFAULT_WINDOW_FILL,
    "integration_bandwidth": DEFAULT_INTEGRATION_BW,
    "lo_bandstop":           DEFAULT_LO_BANDSTOP,
    "trim_stopband":         DEFAULT_TRIM_STOPBAND,
    "time_aperture":         None,
    "psd_window":                DEFAULT_WINDOW,
    "psd_fractional_overlap":    DEFAULT_FRACTIONAL_OVERLAP,
    "psd_window_fill":           DEFAULT_WINDOW_FILL,
    "psd_integration_bandwidth": DEFAULT_INTEGRATION_BW,
    "psd_lo_bandstop":           DEFAULT_LO_BANDSTOP,
    "psd_trim_stopband":         DEFAULT_TRIM_STOPBAND,
    "psd_time_statistic":        DEFAULT_PSD_TIME_STATISTIC,
    "ssb_subcarrier_spacing":    SSB_SUBCARRIER_SPACING,
    "ssb_sample_rate":           SSB_SAMPLE_RATE,
    "ssb_discovery_periodicity": SSB_DISCOVERY_PERIOD,
    "ssb_frequency_offset":      0.0,
    "ssb_max_block_count":       None,
    "ssb_window":                SSB_WINDOW,
    "ssb_lo_bandstop":           SSB_LO_BANDSTOP,
}


def _parse_window(value):
    """Normalize a window spec to what scipy's ``get_window`` accepts.

    Accepts a bare name string, the "name, param" shorthand (e.g.
    "kaiser, 11.88"), or the JSON list form ``["kaiser", 11.88]``. This is
    structure-only normalization — it does not check that the window name
    itself is valid; that is left to striqt (tier 2).

    Args:
        value: A window name string, a "name,param" string, or a 2-element
            (name, param) list/tuple.

    Returns:
        Either a plain name string, or a ``(name, float)`` tuple for
        parametrized windows.

    Raises:
        ValueError: If `value` is empty, or does not match one of the
            accepted structures, or a supplied parameter is not a number.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("window must not be empty")
        if "," in text:
            name, _, param = text.partition(",")
            name, param = name.strip(), param.strip()
            try:
                return (name, float(param))
            except ValueError:
                raise ValueError(f"window parameter {param!r} is not a number")
        return text
    if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[0], str):
        try:
            return (str(value[0]), float(value[1]))
        except (TypeError, ValueError):
            raise ValueError(f"window parameter {value[1]!r} is not a number")
    raise ValueError("window must be a name or name,parameter (scipy get_window spec)")


def _parse_fraction(value) -> Fraction:
    """Parse a fractional-overlap style value into an exact `Fraction`.

    Args:
        value: A ratio string (e.g. "13/28"), a decimal string/float (e.g.
            0.464), or an int.

    Returns:
        The parsed value as a `fractions.Fraction`.

    Raises:
        ValueError: If `value` cannot be parsed as a fraction (including a
            "n/0" style division by zero).
    """
    if isinstance(value, str):
        value = value.strip()
    try:
        return Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError):
        raise ValueError(f"{value!r} is not a fraction (use e.g. 13/28 or 0.464)")


def _parse_optional_hz(value, *, auto_ok: bool = False):
    """Parse a nullable frequency/bandwidth field expressed in Hz.

    Args:
        value: The raw wire value. `None`, `""`, `"none"`, `"null"`, `"off"`,
            or the number `0` are all treated as "not set". The string
            `"auto"` is accepted only when `auto_ok` is True. Anything else
            must be convertible to `float`.
        auto_ok: Whether the literal string "auto" is a valid result for
            this field (e.g. lo_bandstop's "auto" mode).

    Returns:
        `None` if the value means "not set", the string `"auto"` if that was
        given and allowed, otherwise a `float` Hz value.

    Raises:
        ValueError: If `value` is not one of the accepted null/auto tokens
            and cannot be converted to `float`.
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("", "none", "null", "off"):
            return None
        if auto_ok and text == "auto":
            return "auto"
        value = text
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{value!r} is not a bandwidth in Hz"
                         + (" (or 'auto'/'none')" if auto_ok else " (or 'none')"))
    if value == 0:
        return None
    return value


def _parse_time_statistic(value):
    """Parse the PSD `time_statistic` surface into a validated tuple.

    Accepts a comma-separated string or a list/tuple of named statistics
    (e.g. "mean", "max") and/or quantiles in [0, 1], e.g.
    "mean, 0.5, 0.95, max". Structure and quantile *range* are judged here
    (they are knowable without striqt); unknown statistic *names* are left
    for striqt itself to reject in tier 2.

    Args:
        value: A comma-separated string, or a list/tuple whose entries are
            each a statistic-name string or a numeric quantile.

    Returns:
        A de-duplicated tuple of `str` (statistic names, lowercased) and/or
        `float` (quantiles), preserving first-seen order.

    Raises:
        ValueError: If `value` has the wrong container type, an entry is
            neither a string nor a number, a quantile falls outside
            [0, 1], or the result would be empty.
    """
    if isinstance(value, str):
        tokens = [t.strip() for t in value.split(",")]
    elif isinstance(value, (list, tuple)):
        tokens = list(value)
    else:
        raise ValueError("time_statistic must be a list like mean, 0.95, max")
    out = []
    for tok in tokens:
        if isinstance(tok, str):
            tok = tok.strip().lower()
            if not tok:
                continue
            try:
                tok = float(tok)
            except ValueError:
                out.append(tok)
                continue
        if isinstance(tok, bool) or not isinstance(tok, (int, float)):
            raise ValueError(f"{tok!r} is not a statistic name or quantile")
        q = float(tok)
        if not (0.0 <= q <= 1.0):
            raise ValueError(
                f"quantile {q!r} is out of range — entries must be statistic "
                f"names (mean/max/…) or quantiles in [0, 1]"
            )
        out.append(q)
    seen, dedup = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            dedup.append(s)
    if not dedup:
        raise ValueError("time_statistic needs at least one entry (e.g. mean)")
    return tuple(dedup)


def _parse_optional_seconds(value):
    """Parse a nullable duration field expressed in seconds (e.g. time_aperture).

    Args:
        value: The raw wire value. `None`, `""`, `"none"`, `"null"`, `"off"`,
            or the number `0` are all treated as "not set". Anything else
            must be convertible to a positive, finite `float`.

    Returns:
        `None` if the value means "not set", otherwise a positive, finite
        `float` number of seconds.

    Raises:
        ValueError: If `value` cannot be converted to `float`, or converts
            to a non-positive or non-finite number.
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("", "none", "null", "off"):
            return None
        value = text
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{value!r} is not a duration in seconds (or 'none')")
    if value == 0:
        return None
    if not (value > 0 and math.isfinite(value)):
        raise ValueError("must be a positive, finite duration in seconds (or 'none')")
    return value


def scratch_validate_spectrogram(cfg: "RadioConfig"):
    """Tier-2 judge for the "spectrogram" analysis target.

    Judges a proposed analysis config the only way that is always right — by
    asking striqt. Builds the exact Spectrogram spec the live Computer would
    run and evaluates it on a tiny synthetic buffer (zeros, single channel,
    enough rows for one averaged output row if `cfg.time_aperture` is set)
    WITHOUT touching the live ring or acquirer.

    Args:
        cfg: The candidate `RadioConfig`, already past tier-1 clamp/snap.

    Returns:
        `None` if striqt is unavailable (nothing to judge) or the config
        evaluated cleanly (safe to swap into the live stream); otherwise the
        striqt error text (or exception type name if the error has no
        message) describing why the config is illegal.
    """
    if not _ANALYSIS_OK:
        return None   # nothing to judge without striqt (quicklook-only install)
    try:
        sample_rate = float(cfg.sample_rate)
        nfft   = aligned_nfft(cfg.nfft)
        hop    = analysis_hop(nfft, cfg.fractional_overlap)
        # Give the scratch run enough STFT rows that a configured time_aperture
        # produces at least one averaged output row — otherwise a legal aperture
        # would be judged on an empty result instead of striqt's real verdict.
        rows_scratch = 2
        if cfg.time_aperture:
            rows_scratch = max(2, round(float(cfg.time_aperture) * sample_rate / hop))
        needed = calibrated_sample_count(nfft, rows_scratch, hop)
        spec   = make_analysis_spec(cfg, nfft, sample_rate)   # construction may raise
        capture = analysis_specs.Capture(
            sample_rate=sample_rate,
            duration=needed / sample_rate,
            analysis_bandwidth=float(cfg.analysis_bandwidth),
        )
        tiny = np.zeros((1, needed), dtype=np.complex64)
        with warnings.catch_warnings():
            # The 2-row zero buffer is degenerate on purpose; numeric warnings
            # (empty-slice means etc.) are expected noise, not verdicts.
            warnings.simplefilter("ignore")
            striqt_shared.evaluate_spectrogram(tiny, capture, spec, dtype="float32", dB=True)
    except Exception as e:
        return str(e).strip() or type(e).__name__
    return None


def scratch_validate_psd(cfg: "RadioConfig"):
    """Tier-2 judge for the "psd" analysis target (power_spectral_density).

    Runs striqt's real `power_spectral_density` on a tiny synthetic buffer
    (2 STFT rows, single channel, zeros) with the exact kwargs the live
    compute would use, WITHOUT touching the live ring or acquirer.

    Args:
        cfg: The candidate `RadioConfig`, already past tier-1 clamp/snap.

    Returns:
        `None` if striqt is unavailable or the config evaluated cleanly
        (safe to go live); otherwise the striqt error text (e.g. for an
        unknown statistic name) describing why the config is illegal.
    """
    if not _ANALYSIS_OK:
        return None
    try:
        sample_rate = float(cfg.sample_rate)
        nfft   = aligned_nfft(cfg.nfft)
        hop    = analysis_hop(nfft, cfg.psd_fractional_overlap)
        needed = calibrated_sample_count(nfft, 2, hop)
        kwargs = make_psd_kwargs(cfg, nfft, sample_rate)   # construction may raise
        capture = analysis_specs.Capture(
            sample_rate=sample_rate,
            duration=needed / sample_rate,
            analysis_bandwidth=float(cfg.analysis_bandwidth),
        )
        tiny = np.zeros((1, needed), dtype=np.complex64)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            striqt_measurements.power_spectral_density(
                tiny, capture, as_xarray=False, **kwargs
            )
    except Exception as e:
        return str(e).strip() or type(e).__name__
    return None


def scratch_validate_ssb(cfg: "RadioConfig"):
    """Tier-2 judge for the "ssb" analysis target (cellular_5g_ssb_spectrogram).

    Runs striqt's real `cellular_5g_ssb_spectrogram` on a one-burst-set
    synthetic buffer (zeros) with the exact kwargs the live compute would
    use, WITHOUT touching the live ring or acquirer. Requires
    `cfg.sample_rate` to already be on the SSB grid for `cfg`'s subcarrier
    spacing — the tier-1 branch retunes the effective rate before this is
    called.

    Args:
        cfg: The candidate `RadioConfig`, already past tier-1 clamp/snap
            (including the SSB-grid rate retune).

    Returns:
        `None` if striqt is unavailable or the config evaluated cleanly
        (safe to go live); otherwise the striqt error text describing why
        the config is illegal (including an off-grid rejection from
        `ssb_geometry`).
    """
    if not _ANALYSIS_OK:
        return None
    try:
        sample_rate = float(cfg.sample_rate)
        geo = ssb_geometry(cfg)   # off-grid raises → worded rejection
        needed = ssb_block_samples(geo, 1)
        kwargs = make_ssb_kwargs(cfg)
        capture = analysis_specs.Capture(
            sample_rate=sample_rate,
            duration=needed / sample_rate,
            analysis_bandwidth=float("inf"),
        )
        tiny = np.zeros((1, needed), dtype=np.complex64)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            striqt_measurements.cellular_5g_ssb_spectrogram(
                tiny, capture, as_xarray=False, **kwargs
            )
    except Exception as e:
        return str(e).strip() or type(e).__name__
    return None


# Tier-2 scratch validators, one per analysis target (P2b-1). Each judges a
# proposed RadioConfig by running the target's real striqt pipeline on a tiny
# synthetic buffer — never the live ring.
SCRATCH_VALIDATORS = {
    "spectrogram": scratch_validate_spectrogram,
    "psd":         scratch_validate_psd,
    "ssb":         scratch_validate_ssb,
}


def scratch_validate_analysis(cfg: "RadioConfig", target: str = "spectrogram"):
    """Dispatch to the tier-2 scratch validator for the given analysis target.

    This is the single entry point `SharedConfig._validate_analysis` calls
    for tier 2 of the freedom model; it looks up the target's validator in
    `SCRATCH_VALIDATORS` and delegates to it.

    Args:
        cfg: The candidate `RadioConfig`, already past tier-1 clamp/snap.
        target: One of the keys in `ANALYSIS_TARGETS`/`SCRATCH_VALIDATORS`
            (e.g. "spectrogram", "psd", "ssb"). Defaults to "spectrogram"
            for the unchanged P2a wire format.

    Returns:
        `None` if `target` is unknown or the underlying validator found the
        config legal (or striqt is unavailable); otherwise the striqt error
        text explaining why the config is illegal.
    """
    fn = SCRATCH_VALIDATORS.get(target)
    return fn(cfg) if fn else None

