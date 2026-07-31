"""Shared constants and device profile data for every LINDA core module.

Pure data — no imports from other core modules, so anything in `core/` can
import this without creating a cycle. Holds the per-device capability tables
(`DEVICE_PROFILES`), the sample-rate/FFT-size grids that incoming control
values are snapped to (`RATES_HZ`, `NFFT_CHOICES`, `ALIGNED_NFFTS`), ring
buffer sizing (`MAX_TAIL`, `READ_SIZE`), and the default acquisition/analysis
parameters (AHAWI timing, demo tone/burst plans, striqt spectrogram recipe
defaults). `DEVICE_PROFILES` is consumed by `core.devices` (which wraps each
profile in an adapter) and by `SharedConfig` (tier-1 clamp/capability
envelopes).
"""
from __future__ import annotations

from fractions import Fraction

# ---------------------------------------------------------------------------
# Device profiles (P3-1). One entry per supported SDR; data only — the source
# factories live in core.devices. DEVICE/DEVICE_LABEL/CHANNELS are resolved
# once at startup (core.state.configure_device) before any thread or
# SharedConfig exists; every later read is runtime, so set-once is safe.
#
#   channels        RX port tuple the acquirer streams
#   defaults        RadioConfig seeds (center / sample_rate / gain)
#   envelope        capability fallback: tier-1 clamp bounds (P3-3)
#   query_envelope  Which bound GROUPS to ask the live SoapySDR device for
#                   after open, merged over the fallback. True = all of
#                   them, False = none, or a tuple naming the subset
#                   ("freq" / "gain" / "rate") — see ENVELOPE_QUERY_GROUPS.
#
#                   Per-group, not all-or-nothing, because the two halves of
#                   the AIR-T's envelope have opposite truth values. Its
#                   −60..10 dB gain window is a striqt CALIBRATED-gain
#                   convention, not the raw SoapyAIRT range: querying it
#                   would shift legal bounds on the existing deployment (and
#                   the driver rejects −60/−50 outright — see
#                   tools/hardware_qual.py). Its RATE bounds are the
#                   opposite: the static numbers here are a guess, and the
#                   driver is the only honest source. So air8201b queries
#                   "rate" and nothing else.
# ---------------------------------------------------------------------------

# Envelope bound groups a device may be asked about (see query_envelope
# above). core.shims.query_device_envelope maps each to its SoapySDR getter.
ENVELOPE_QUERY_GROUPS = ("freq", "gain", "rate")


def envelope_query_groups(profile):
    """Resolve a profile's `query_envelope` setting to a set of group names.

    Args:
        profile: A `DEVICE_PROFILES` entry (or any mapping with the key).

    Returns:
        frozenset[str]: The subset of `ENVELOPE_QUERY_GROUPS` this profile
        wants queried from the live driver. `True` means all of them,
        `False`/absent means none, and a tuple/list/set is taken verbatim
        (unknown names dropped).
    """
    want = (profile or {}).get("query_envelope", False)
    if want is True:
        return frozenset(ENVELOPE_QUERY_GROUPS)
    if not want:
        return frozenset()
    return frozenset(str(g).strip().lower() for g in want) & frozenset(
        ENVELOPE_QUERY_GROUPS)


DEVICE_PROFILES = {
    "air8201b": {
        "label": "AIR8201B",
        "channels": (0, 1),
        # This model's own converter clock. Declared per profile so it can
        # never be inherited by a radio that cannot run it.
        "master_clock_rate": 125e6,
        "defaults": {"center": 3750e6, "sample_rate": 15.36e6, "gain": 0.0},
        "envelope": {
            "freq_min": 300e6, "freq_max": 6e9,
            "gain_min": -60.0, "gain_max": 10.0,
            "rate_min": 1e6,   "rate_max": 125e6,
        },
        # Rate ONLY. The gain window above is a striqt calibrated-gain
        # convention the driver would overwrite with its raw range; the rate
        # bounds are a guess only the driver can correct.
        "query_envelope": ("rate",),
    },
    # Other Deepwave AIR-T models: same SoapyAIRT driver + AirStack stack.
    # Their striqt spec classes are used when the installed build ships them
    # (see core.striqt_compat); the AIR8201B numbers are the safe fallback
    # envelope until the live device is queried.
    "air7101b": {
        "label": "AIR7101B",
        "channels": (0, 1),
        # This model's own converter clock. Declared per profile so it can
        # never be inherited by a radio that cannot run it.
        "master_clock_rate": 125e6,
        "defaults": {"center": 3750e6, "sample_rate": 15.36e6, "gain": 0.0},
        "envelope": {
            "freq_min": 300e6, "freq_max": 6e9,
            "gain_min": -60.0, "gain_max": 10.0,
            "rate_min": 1e6,   "rate_max": 125e6,
        },
        "query_envelope": True,
    },
    "air7201b": {
        "label": "AIR7201B",
        "channels": (0, 1),
        # This model's own converter clock. Declared per profile so it can
        # never be inherited by a radio that cannot run it.
        "master_clock_rate": 125e6,
        "defaults": {"center": 3750e6, "sample_rate": 15.36e6, "gain": 0.0},
        "envelope": {
            "freq_min": 300e6, "freq_max": 6e9,
            "gain_min": -60.0, "gain_max": 10.0,
            "rate_min": 1e6,   "rate_max": 125e6,
        },
        "query_envelope": True,
    },
    "pluto": {
        "label": "PlutoSDR",
        "channels": (0,),
        # AD936x reference — NOT the AIR-T's 125 MHz (bug_report P-1). The
        # plutosdr Soapy driver may ignore the field entirely; a correct value
        # is harmless either way.
        "master_clock_rate": 61.44e6,
        # 3.84 MS/s default: sustained 15.36 MS/s over the Pluto's USB link is
        # optimistic; start on the safe LTE grid point and let the user go up.
        # 3750 MHz is the project-wide default center, and it is legal here —
        # but only 50 MHz below the AD936x ceiling, so a Pluto starts with very
        # little room to tune up. Nudge it down before sweeping.
        "defaults": {"center": 3750e6, "sample_rate": 3.84e6, "gain": 0.0},
        "envelope": {
            "freq_min": 325e6,  "freq_max": 3.8e9,
            "gain_min": 0.0,    "gain_max": 73.0,
            "rate_min": 0.52e6, "rate_max": 61.44e6,
        },
        "query_envelope": True,
    },
    "soapy": {
        # Generic SoapySDR device (best-effort): channels and capability
        # ranges are discovered from the live driver after open; these
        # fallbacks only exist so the UI has sane bounds until then.
        "label": "SoapySDR device",
        "channels": (0,),
        "defaults": {"center": 3750e6, "sample_rate": 3.84e6, "gain": 0.0},
        "envelope": {
            "freq_min": 1e6,   "freq_max": 6e9,
            "gain_min": 0.0,   "gain_max": 76.0,
            "rate_min": 0.25e6, "rate_max": 61.44e6,
        },
        "query_envelope": True,
    },
    "demo": {
        "label": "Demo (synthetic IQ)",
        "channels": (0, 1),
        "defaults": {"center": 3750e6, "sample_rate": 15.36e6, "gain": 0.0},
        "envelope": {
            "freq_min": 300e6, "freq_max": 6e9,
            "gain_min": -60.0, "gain_max": 10.0,
            "rate_min": 1e6,   "rate_max": 125e6,
        },
        "query_envelope": False,
    },
}

DEFAULT_CENTER      = 3750e6
DEFAULT_SAMPLE_RATE = 15.36e6
DEFAULT_GAIN        = 0.0
DEFAULT_NFFT        = 1024
DEFAULT_ROWS        = 12      # rows per frame (window_ms drives this from browser)

MASTER_CLOCK_RATE   = 125e6   # AIR-T reference clock. NOT a universal default:
                              # see SOAPY_FALLBACK_MASTER_CLOCK below.
# Master clock for a generic SoapySDR radio when the device could not be probed
# (core.devices._probe_device_facts asks the driver first and almost always
# answers). 125 MHz is the AIR-T's converter clock and used to be applied to
# every radio: a USRP B205mini rejects it outright —
#   "current master clock rate (125.000000 MHz) exceeds maximum possible
#    master clock rate (61.440000 MHz)"
# — and the source never opened. 61.44 MHz is the AD936x-family clock shared by
# the Pluto, the USRP B2xx and most SoapySDR radios, and it is an exact integer
# multiple of every rate in RATES_HZ.
SOAPY_FALLBACK_MASTER_CLOCK = 61.44e6
READ_SIZE           = 1 << 18   # max IQ samples per _read_stream call (262144)
MAX_TAIL            = 1 << 22   # per-channel ring buffer capacity (4M samples)
DATA_STALE_SEC      = 1.0       # get_latest() returns None if the ring is older

SCROLL_ROWS         = 12        # rows per frame in Cool (scroll/waterfall) mode
# Rows are bounded by what the IQ ring can actually supply (see max_live_rows()),
# not a flat cap. MAX_ROWS_ABS is an absolute ceiling protecting browser render
# + ring depth; RING_ROW_FILL leaves headroom so the Computer's avail>=need gate
# is reached promptly.
MAX_ROWS_ABS        = 4096      # absolute safety ceiling on requested rows
RING_ROW_FILL       = 0.9       # fraction of MAX_TAIL usable for one frame's need

# Allowed sample rates (LTE/5G-NR multiples of 1.92 MHz) and FFT sizes. Incoming
# control values are snapped to the nearest of these so an off-list value can't
# reach arm_spec or trip the calibrated ValueError guard (LV-R2).
#
# This is the cellular FAMILY, not any one radio's capability: the doubling
# ladder off the 1.92 MHz base. `core.dsp.allowed_rates()` narrows it per
# device — preferring the discrete list the DRIVER reports (`rate_list`,
# from SoapySDR's listSampleRates) and falling back to this grid clipped to
# the envelope. The top two entries exist so a radio that can genuinely run
# them is not capped by a constant; whether a given radio accepts them is
# the driver's answer, never this tuple's.
RATE_BASE_HZ  = 1.92e6
RATES_HZ      = tuple(RATE_BASE_HZ * (2 ** k) for k in range(1, 7))
#             = (3.84e6, 7.68e6, 15.36e6, 30.72e6, 61.44e6, 122.88e6)

# Highest rate anything in this repo has actually QUALIFIED on hardware.
#
# Not a limit — nothing clamps to it. It is the line between "proven" and
# "the driver said yes". Above it the radio may accept the rate, read it
# back perfectly, and still starve the display: at 122.88 MS/s two channels
# of complex64 is ~2 GB/s into the ring, and the whole MAX_TAIL ring is only
# ~34 ms deep. Both failures are silent — a gappy waterfall looks like a
# quiet band. So a rate above this line raises a banner that says so
# (`SharedConfig.update` queues the notice; the client shows the banner)
# rather than letting the operator find out from the data later.
#
# Raise this only after tools/hardware_qual.py sustains frames at the higher
# rate on the radio in question — the same standard every other verified
# number in this project is held to.
QUALIFIED_MAX_RATE_HZ = 30.72e6

NFFT_CHOICES  = (256, 512, 1024, 2048, 4096)

# Demo tone plan (P3-2): per-channel CW tone sets of (amplitude, offset_hz),
# cycled when the demo runs with more channels than entries. Entries 0/1 are
# the historical two-channel tone sets, unchanged.
DEMO_TONES = (
    ((0.30,  2.5e6), (0.12, -1.8e6)),
    ((0.20, -3.2e6), (0.08,  4.1e6)),
    ((0.25,  1.1e6), (0.10, -4.6e6)),
    ((0.15, -0.9e6), (0.09,  3.3e6)),
)

# Demo periodic burst (AHAWI): a fake SSB — amplitude, RF offset from the
# default center, period, and on-time. 20 ms period matches the 5G NR SSB
# default, so the AHAWI demo behaves like the real 3750 MHz signal: pinned in
# aligned replay, swimming in the rolling live view. Gating uses the demo's
# persistent sample counter, so burst timing is continuous across frames.
DEMO_BURST = {"amp": 0.5, "offset_hz": 0.6e6, "period_s": 0.020, "duty_s": 0.002}

# AHAWI mode (coherent capture → segmented replay).
AHAWI_MIN_CAPTURE_MS     = 20.0     # below this a "capture" is just one segment
AHAWI_MAX_CAPTURE_MS     = 1000.0   # sanity ceiling; the ring clamp is stricter
AHAWI_DEFAULT_CAPTURE_MS = 100.0
AHAWI_MAX_SEGMENTS       = 64       # keeps the client scrubber sane
AHAWI_ALIGN_TARGET       = 0.25     # burst sits at this fraction of each segment
AHAWI_ALIGN_MIN_DB       = 3.0      # folded peak-over-median needed to trust alignment
AHAWI_REFRESH_S          = 1.0      # min seconds between published captures

# Spectrogram backends. Selection lives in core.state.SPEC_BACKEND.
BACKENDS = {"calibrated", "quicklook", "ssb", "psd"}

# Backends whose STFT runs on the 28-multiple aligned_nfft grid.
CALIBRATED_GRID_BACKENDS = frozenset({"calibrated", "ssb", "psd"})

AVG_BIN_GROUPS = 12
SSB_SUBCARRIER_SPACING = 30e3
SSB_SAMPLE_RATE = 7.68e6
SSB_DISCOVERY_PERIOD = 20e-3
SSB_LO_BANDSTOP = 120e3
SSB_WINDOW = "blackmanharris"
# Ceiling for SSB-grid capture retunes (P2b-5): the top of the radio's LTE-rate
# family. The grid rule (2·fs/scs a 28-multiple) admits no rate above this that
# we would trust the AIR8201B to arm.
SSB_MAX_RATE = 30.72e6

# Default striqt Spectrogram recipe — the exact values calibrated_spectrogram
# hardcoded before P2a-1. These seed the editable analysis params in RadioConfig,
# so behaviour is unchanged until the user edits them from the Analysis panel.
# integration_bandwidth "auto" reproduces the old frequency_resolution ×
# averaging_factor(nfft) coupling (the only value that tracks nfft changes).
DEFAULT_WINDOW             = ("kaiser", 11.88)
DEFAULT_FRACTIONAL_OVERLAP = Fraction(13, 28)
DEFAULT_WINDOW_FILL        = Fraction(15, 28)
DEFAULT_INTEGRATION_BW     = "auto"
DEFAULT_LO_BANDSTOP        = SSB_LO_BANDSTOP
DEFAULT_TRIM_STOPBAND      = False

# Default PSD time_statistic (P2b-3) — reproduces the mean+max trace pair the
# client has always drawn, so behaviour is unchanged until the user edits it.
DEFAULT_PSD_TIME_STATISTIC = ("mean", "max")
