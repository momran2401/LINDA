"""Device adapter contract shared by every radio family Linda can drive.

`DeviceAdapter` is the base class that `core/devices/__init__.py` registers
one subclass of per supported radio family (Deepwave AIR-T models, PlutoSDR,
generic SoapySDR, demo). It gives every frontend the same handles regardless
of which radio is actually attached:

  - create_source()          Open a striqt source for this device.
  - describe_capabilities()  Identity, channels, envelope, readback support.
  - read_back(source, cfg)   Query the LIVE driver for the actually-applied
                             center/sample_rate/gain (None per field when the
                             driver can't answer).
  - hardware_expectations()  What striqt actually programmed into the driver,
                             accounting for its own LO-shift/backend-rate
                             tricks.
  - verify(cfg, actuals)     Compare requested vs read-back values with
                             adapter-specific tolerances.

Readback is the heart of the verified-settings pipeline (`core/operations.py`):
a config change is only reported as VERIFIED when the driver's own answer
matches the request within tolerance, rather than trusting that a call which
didn't raise actually took effect.
"""
from __future__ import annotations

from .. import state
from ..constants import DEVICE_PROFILES, envelope_query_groups
from ..shims import get_device

try:
    from SoapySDR import SOAPY_SDR_RX as _RX_DIR
except Exception:
    _RX_DIR = 1   # SoapySDR's RX direction constant


class DeviceAdapter:
    """Base adapter contract. Subclasses set `name` and override
    `create_source()`; most also inherit `read_back()`/`verify()` as-is and
    only need to override tolerances or `supports_readback` when a family
    behaves differently."""

    name = None                 # profile key in constants.DEVICE_PROFILES
    # Verification tolerances. Frequency tolerance is the max of the absolute
    # floor and the relative fraction — synthesizer step sizes differ per
    # radio, so exact equality is the wrong test.
    freq_tol_hz   = 10.0        # absolute floor
    freq_tol_rel  = 1e-6        # relative to the requested value
    rate_tol_rel  = 1e-4
    gain_tol_db   = 0.5
    # Whether `cfg.gain` and the driver's gain readback are the SAME quantity.
    # Default False: striqt hands the radio a calibrated gain, and most
    # drivers report a raw composite gain on their own scale, so comparing
    # them manufactures a mismatch on a perfectly healthy radio (see
    # verify()). Adapters that know the two agree opt in.
    gain_readback_comparable = False
    # Whether the driver supports config readback at all (demo says no and
    # reports "readback_unsupported" honestly instead of faking agreement).
    supports_readback = True

    def __init__(self, info=None):
        """Construct the adapter.

        Args:
            info: The SoapySDR enumeration dict when discovery picked this
                device (may carry `serial`/`label`/driver-probed facts like
                `_num_channels`); None for an explicitly-selected profile with
                no enumeration performed.
        """
        self.info = dict(info) if info else {}

    # -- identity ----------------------------------------------------------

    @property
    def profile(self):
        """dict: This adapter's entry in `constants.DEVICE_PROFILES`."""
        return DEVICE_PROFILES[self.name]

    @property
    def label(self):
        """str: Human-readable device label, with serial suffix if known."""
        base = self.profile["label"]
        serial = self.info.get("serial")
        return f"{base} ({serial})" if serial else base

    def tx_channels(self):
        """Return the TX channel count learned at enumeration, if any.

        This is only an early hint so the UI can hide the transmit control on
        a receive-only radio before the source is even open; `core.tx`
        re-probes the live device for the authoritative answer and the gain
        ranges that go with it.

        Returns:
            int | None: The probed TX channel count, or None if this adapter
            was never enumerated (`discover()`/`_probe_device_facts`) and so
            never learned one.
        """
        n = self.info.get("_num_tx_channels")
        return int(n) if n is not None else None

    def describe_capabilities(self):
        """Summarize this device's identity and capabilities for the frontend.

        The reported envelope folds in any rate limits learned at
        enumeration (`_probe_rate_limits`). Without that it would keep
        serving the profile's static guess next to the live, driver-derived
        envelope `/config` ships separately — two numbers for one radio,
        which is exactly the kind of quiet disagreement this layer exists to
        prevent.

        Returns:
            dict: Static/near-static capability info — name, label, serial,
            driver, active channels, TX channel count, sample-rate/frequency
            envelope, which envelope groups are taken from a live device
            query, whether readback is supported, and the verification
            tolerances.
        """
        prof = self.profile
        envelope = dict(prof["envelope"])
        for key in ("rate_min", "rate_max", "rate_list"):
            probed = self.info.get("_" + key)
            if probed:
                envelope[key] = probed
        return {
            "name":              self.name,
            "label":             self.label,
            "serial":            self.info.get("serial"),
            "driver":            self.info.get("driver"),
            "channels":          list(state.CHANNELS),
            "tx_channels":       self.tx_channels(),
            "envelope":          envelope,
            "query_envelope":    sorted(envelope_query_groups(prof)),
            "supports_readback": bool(self.supports_readback),
            "tolerances": {
                "freq_hz":  self.freq_tol_hz,
                "freq_rel": self.freq_tol_rel,
                "rate_rel": self.rate_tol_rel,
                "gain_db":  self.gain_tol_db,
            },
        }

    # -- lifecycle ---------------------------------------------------------

    def create_source(self):
        """Open and return a striqt source for this device.

        Subclasses override this; the base implementation always raises.

        Raises:
            NotImplementedError: Always, in the base class.
        """
        raise NotImplementedError

    # -- readback ----------------------------------------------------------

    def read_back(self, source, cfg):
        """Query the live driver for the actually-applied tuning.

        Never raises: a readback failure is reported as data
        ("readback_unsupported" per field), not an exception, since demo
        devices and some drivers simply cannot answer.

        Args:
            source: The opened striqt source to query.
            cfg: The current `RadioConfig`/`SharedConfig`, used to size the
                per-channel gain list against `state.CHANNELS`.

        Returns:
            dict: {"center": Hz | None, "sample_rate": Hz | None,
            "gain": [dB | None, ...]} — None per field (or per channel) when
            this adapter doesn't support readback, `get_device()` yields no
            SoapySDR device, or the individual getter call raises.
        """
        if not self.supports_readback:
            return {"center": None, "sample_rate": None,
                    "gain": [None] * len(state.CHANNELS)}
        dev = get_device(source)
        out = {"center": None, "sample_rate": None,
               "gain": [None] * len(state.CHANNELS)}
        if dev is None:
            return out
        ch0 = state.CHANNELS[0] if state.CHANNELS else 0
        try:
            out["center"] = float(dev.getFrequency(_RX_DIR, ch0))
        except Exception:
            pass
        try:
            out["sample_rate"] = float(dev.getSampleRate(_RX_DIR, ch0))
        except Exception:
            pass
        gains = []
        for ch in state.CHANNELS:
            try:
                gains.append(float(dev.getGain(_RX_DIR, ch)))
            except Exception:
                gains.append(None)
        out["gain"] = gains
        return out

    # -- verification ------------------------------------------------------

    def hardware_expectations(self, source, capture, cfg):
        """Compute the values striqt is expected to have PROGRAMMED into the
        driver, which legitimately differ from the user-facing capture values.

        A non-"none" `lo_shift` intentionally offsets the hardware LO, and
        `backend_sample_rate`/host resampling can run the SDR at a different
        rate than the delivered capture rate. Comparing raw driver readback
        against `cfg` directly would falsely report both valid cases as a
        mismatch, so this asks striqt's own resampler design when discoverable
        (`source.get_resampler()` or `source.backend.get_resampler()`) and
        falls back to the declared `cfg.backend_sample_rate` otherwise.

        Args:
            source: The opened striqt source.
            capture: The capture spec passed to striqt's resampler-design
                lookup.
            cfg: The current `RadioConfig`/`SharedConfig` holding the
                user-facing `center`/`sample_rate`/`backend_sample_rate`.

        Returns:
            dict: {"center": Hz, "sample_rate": Hz} — the values `verify()`
            should compare driver readback against.
        """
        center = float(cfg.center)
        rate = float(cfg.sample_rate)
        for obj in (source, getattr(source, "backend", None)):
            fn = getattr(obj, "get_resampler", None)
            if fn is None:
                continue
            try:
                design = fn(capture)
                center = float(cfg.center) - float(design.get("lo_offset", 0.0))
                rate = float(design.get("fs_sdr", rate))
                return {"center": center, "sample_rate": rate}
            except Exception:
                pass
        if float(getattr(cfg, "backend_sample_rate", 0.0) or 0.0) > 0:
            rate = float(cfg.backend_sample_rate)
        return {"center": center, "sample_rate": rate}

    def verify(self, cfg, actuals, expected=None):
        """Compare the requested cfg against driver readback, field by field.

        Center frequency and sample rate are judged against `expected`
        (from `hardware_expectations()`) when given, else against `cfg`
        directly.

        Gain is only judged when this adapter can say what the driver OUGHT
        to report (`gain_readback_comparable`). It usually cannot: on the
        AIR-T `cfg.gain` is striqt's CALIBRATED gain (the −60…10 dB window in
        the profile) while the driver reports its own raw composite gain on a
        different scale entirely — the two disagree by construction, and
        `tools/hardware_qual.py` already documents SoapyAIRT rejecting gains
        the profile declares legal. Judging them against each other made
        every config change on a healthy AIR-T collapse to `mismatch`
        (`verdict_state` treats any mismatched field as fatal), which is
        worse than useless: an alarm that is always on is an alarm nobody
        reads.

        So an incomparable gain is reported `readback_unsupported` — "we did
        not verify this" — rather than `mismatch` — "the radio disagreed."
        That is the honest claim, and it leaves a center/rate-verified
        operation reading `verified` instead of being dragged to `mismatch`
        by a number that was never comparable. Adapters whose driver does
        report the same quantity set `gain_readback_comparable = True` and
        get a real verdict.

        Args:
            cfg: The current `RadioConfig`/`SharedConfig` holding the
                requested `center`/`sample_rate`/`gain`.
            actuals: The dict returned by `read_back()`.
            expected: Optional dict from `hardware_expectations()`, giving the
                center/sample_rate striqt was actually expected to program
                (accounting for lo_shift/backend_sample_rate).

        Returns:
            list[dict]: One verdict per field/channel:
            {"field": str, "requested": float | None, "actual": float | None,
            "state": "verified" | "mismatch" | "readback_unsupported"}.
        """
        verdicts = []
        expected = dict(expected or {})
        exp_center = float(expected.get("center", cfg.center))
        exp_rate = float(expected.get("sample_rate", cfg.sample_rate))

        def judge(field, requested, actual, tol):
            """Build one verdict dict for a single field's readback."""
            if actual is None:
                return {"field": field, "requested": requested,
                        "actual": None, "state": "readback_unsupported"}
            ok = abs(float(actual) - float(requested)) <= tol
            return {"field": field, "requested": float(requested),
                    "actual": float(actual),
                    "state": "verified" if ok else "mismatch"}

        freq_tol = max(self.freq_tol_hz, self.freq_tol_rel * abs(exp_center))
        verdicts.append(judge("center", exp_center, actuals.get("center"), freq_tol))
        rate_tol = max(1.0, self.rate_tol_rel * abs(exp_rate))
        verdicts.append(judge("sample_rate", exp_rate,
                              actuals.get("sample_rate"), rate_tol))
        gains = actuals.get("gain") or []
        for i, ch in enumerate(state.CHANNELS):
            actual = gains[i] if i < len(gains) else None
            if not self.gain_readback_comparable:
                # Reported, never judged — see the docstring. `actual` is
                # still carried so the op log can show what the driver said.
                verdicts.append({"field": f"gain[ch{ch}]",
                                 "requested": float(cfg.gain),
                                 "actual": (None if actual is None
                                            else float(actual)),
                                 "state": "readback_unsupported"})
                continue
            verdicts.append(
                judge(f"gain[ch{ch}]", cfg.gain, actual, self.gain_tol_db))
        return verdicts
