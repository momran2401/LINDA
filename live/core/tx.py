"""Transmit mode — the radio's TX port, driven straight from SoapySDR.

striqt does not transmit. The vendored tree and the installed v0.7.0 both use
`SOAPY_SDR_TX` in exactly one place — `_probe_channel`, which *counts* TX
channels for capability metadata and never opens one. striqt is a sensor
library. So the whole TX path here is ours, written against the SoapySDR device
API (`setupStream`/`activateStream`/`writeStream`), which is the same API on
every radio LINDA supports: that is what makes one implementation cover the
AIR-T, the Pluto, a USRP, and anything else Soapy enumerates.

Two facts shape the design:

1. **TX borrows the LIVE device handle; it never opens its own.** The AIR-T
   retains FPGA descriptors for the process lifetime (the same reason recording
   runs in-process — see core/recording.py), so a second process could not
   acquire the radio while the viewer holds it, and a second `SoapySDR.Device`
   for the same hardware is not safe in general. RX and TX are independent
   streams on ONE device; the waterfall keeps running while transmitting, which
   means you can watch your own signal.

2. **The device can vanish underneath us.** A retune-recovery or a source
   reconnect calls `close_source()`, and any TX stream open across that becomes
   a handle to freed driver state. The writer thread therefore re-checks device
   identity every chunk and aborts the transmission (disclosed, logged) the
   moment it changes.

Every transmission is an entry in the OPERATIONS log — frequency, power,
waveform, duration, and the driver's own readback of what it actually tuned to.
That log is the point, not decoration: it is the record of what this radio
radiated and when.

`RADIO_TX=0` removes the feature at the process level.
"""
from __future__ import annotations

import math
import os
import threading
import time

import numpy as np

from . import state
from .operations import OPERATIONS
from .shims import enable_stream, get_device

# ---------------------------------------------------------------------------
# Waveforms
# ---------------------------------------------------------------------------

#: Waveform kind → human label. The UI renders this map directly, so adding a
#: generator below and an entry here is the whole job of adding a waveform.
TX_WAVEFORMS = {
    "cw":       "CW tone",
    "two_tone": "Two tone (IMD test)",
    "chirp":    "Linear chirp",
    "noise":    "Band noise",
}

#: Default IQ amplitude as a fraction of full scale. -6 dBFS leaves headroom so
#: the two-tone sum (which peaks at 2x a single tone) cannot clip the DAC.
DEFAULT_AMPLITUDE = 0.5

#: Samples per writeStream call when the driver reports no MTU.
DEFAULT_CHUNK = 16384

# How the TX stream had to be obtained, best case first. Observed on a real
# AIR8201B: `setupStream(SOAPY_SDR_TX)` fails with
#
#     Trigger in use, can't set up new stream!
#
# while the live RX stream exists. AirStack's SoapyAIRT arms every stream from
# ONE FPGA trigger block, and the running viewer already holds it — so on this
# radio "RX and TX are independent streams on one device" is true of the AD9371
# but NOT of the driver above it. The ladder below tries the cheapest thing
# first and discloses which rung it actually needed, because the last one costs
# the operator their live view.
TX_COEXIST      = "coexist"       # true full duplex; viewer never notices
TX_RX_RELEASED  = "rx_released"   # RX stream closed for the whole transmission

# There is deliberately NO middle rung that merely deactivates the RX stream.
# It was tried, and it is unsafe on principle: the Acquirer thread is sitting
# in a blocking read on that stream, so disabling it underneath produces
#
#     [WARNING] Inactive RF hardware detected, ignoring data transfer request!
#     [radio] recovering after: TIMEOUT (error code -1)
#
# — the Acquirer concludes the radio is broken and calls _recover(), which
# calls TX.shutdown() and kills the very transmission that caused it. It then
# leaves the RX channel in a state where re-arming fails with "Invalid RX
# channel state to set up triggering!". A stream another thread is actively
# reading must be taken away by ASKING that thread (pause_and_release), never
# by pulling it out from under it.

#: Human wording for each rung — used in the op log and shown in the UI.
TX_RX_MODE_NOTES = {
    TX_COEXIST: "live view keeps running (radio does full duplex)",
    TX_RX_RELEASED: "LIVE VIEW IS DOWN — this radio cannot receive while "
                    "transmitting; it resumes when you press Stop",
}

#: Driver messages that mean "another stream holds the resource", not "your
#: request was wrong". Only these escalate the ladder; a genuinely bad request
#: must fail fast instead of costing the operator their live view.
_STREAM_CONFLICT_MARKERS = ("trigger", "in use", "busy", "already", "resource")


def _is_stream_conflict(exc):
    text = str(exc).lower()
    return any(marker in text for marker in _STREAM_CONFLICT_MARKERS)


def _close_stream(dev, stream):
    """Unkey and release a TX stream. Never raises: this runs on failure paths
    where a stream left active would keep the PA keyed."""
    if stream is None:
        return
    for action in (lambda: dev.deactivateStream(stream),
                   lambda: dev.closeStream(stream)):
        try:
            action()
        except Exception:
            pass


class Waveform:
    """Chunked IQ generator with continuous phase across chunks.

    Phase is carried as fractional CYCLES mod 1 in float64 and only converted
    to radians per chunk. An absolute float32 time axis scrambles a MHz tone
    within a minute of uptime — the same trap `DemoAcquirer._synth_chunk`
    documents — and a transmitter runs far longer than a demo frame.
    """

    def __init__(self, kind, sample_rate, params=None, seed=None):
        if kind not in TX_WAVEFORMS:
            raise ValueError(f"unknown waveform {kind!r} "
                             f"(known: {', '.join(sorted(TX_WAVEFORMS))})")
        self.kind = kind
        self.fs = float(sample_rate)
        p = dict(params or {})
        self.amplitude = float(p.get("amplitude", DEFAULT_AMPLITUDE))
        self.offset_hz = float(p.get("offset_hz", 0.0))
        self.spacing_hz = float(p.get("spacing_hz", 100e3))
        self.chirp_bandwidth_hz = float(p.get("chirp_bandwidth_hz", 1e6))
        self.chirp_period_s = float(p.get("chirp_period_s", 0.01))
        self._idx = 0
        self._rng = np.random.default_rng(seed)

    def _tone(self, idx, amp, off_hz):
        frac = np.mod(idx * (off_hz / self.fs), 1.0)
        return (amp * np.exp(2j * np.pi * frac)).astype(np.complex64)

    def next(self, n):
        """The next `n` samples, complex64, scaled to |amplitude| full scale."""
        idx = self._idx + np.arange(n, dtype=np.int64)
        self._idx += n
        a = self.amplitude

        if self.kind == "cw":
            out = self._tone(idx, a, self.offset_hz)
        elif self.kind == "two_tone":
            half = self.spacing_hz / 2.0
            # Each tone at half amplitude so the sum still peaks at `a`.
            out = (self._tone(idx, a / 2.0, self.offset_hz - half)
                   + self._tone(idx, a / 2.0, self.offset_hz + half))
        elif self.kind == "chirp":
            # Sawtooth chirp: phase is evaluated analytically WITHIN each sweep
            # period, so the sweep restarts exactly where a real repeating
            # chirp restarts. The retrace is a genuine discontinuity in the
            # waveform, not a bookkeeping error.
            period_n = max(1, int(round(self.chirp_period_s * self.fs)))
            t = (idx % period_n) / self.fs
            span = self.chirp_period_s
            rate = self.chirp_bandwidth_hz / span if span > 0 else 0.0
            f0 = self.offset_hz - self.chirp_bandwidth_hz / 2.0
            cycles = np.mod(f0 * t + 0.5 * rate * t * t, 1.0)
            out = (a * np.exp(2j * np.pi * cycles)).astype(np.complex64)
        else:  # noise
            # White complex Gaussian fills the ENTIRE TX sample rate — this is
            # not a shaped or band-limited source, and the UI says so. Scaled
            # so ~3 sigma sits at the requested amplitude; the clip below caps
            # the rare excursion rather than letting the DAC wrap.
            raw = (self._rng.standard_normal(n) + 1j * self._rng.standard_normal(n))
            out = (raw * (a / 3.0)).astype(np.complex64)
            np.clip(out.view(np.float32), -1.0, 1.0, out=out.view(np.float32))

        return np.ascontiguousarray(out, dtype=np.complex64)

    def describe(self):
        d = {"kind": self.kind, "label": TX_WAVEFORMS[self.kind],
             "amplitude": self.amplitude, "offset_hz": self.offset_hz}
        if self.kind == "two_tone":
            d["spacing_hz"] = self.spacing_hz
        if self.kind == "chirp":
            d["chirp_bandwidth_hz"] = self.chirp_bandwidth_hz
            d["chirp_period_s"] = self.chirp_period_s
        return d

    def occupied_bandwidth_hz(self):
        """Roughly what this waveform lights up, for the disclosure line."""
        if self.kind == "cw":
            return 0.0
        if self.kind == "two_tone":
            return abs(self.spacing_hz)
        if self.kind == "chirp":
            return abs(self.chirp_bandwidth_hz)
        return self.fs


# ---------------------------------------------------------------------------
# Feature gate
# ---------------------------------------------------------------------------

def tx_enabled():
    """False when RADIO_TX=0/off/false disables transmit for this process."""
    return str(os.environ.get("RADIO_TX", "1")).strip().lower() not in (
        "0", "off", "false", "no", "")


# ---------------------------------------------------------------------------
# SoapySDR helpers (all best-effort: a missing method is data, not a crash)
# ---------------------------------------------------------------------------

def _soapy():
    try:
        import SoapySDR
        return SoapySDR
    except Exception:
        return None


def _tx_dir():
    try:
        from SoapySDR import SOAPY_SDR_TX
        return SOAPY_SDR_TX
    except Exception:
        return 0   # SoapySDR's TX direction constant


def _cf32():
    """SoapySDR's complex-float32 stream format.

    Resolved through a function so tests can drive the controller without the
    SoapySDR module installed. The fallback is not a guess: SOAPY_SDR_CF32 *is*
    the string "CF32" in the SoapySDR bindings.
    """
    try:
        from SoapySDR import SOAPY_SDR_CF32
        return SOAPY_SDR_CF32
    except Exception:
        return "CF32"


#: Wire formats we can produce, best first.
#
# CS16 leads because it is what the AIR-T's DMA actually wants — Deepwave's own
# TX example uses it — and because asking for CF32 there is a SILENT failure:
# `setupStream` returns a stream, `activateStream` succeeds, and then
# `writeStream` times out forever. Observed on hardware as a transmission that
# ran for five minutes reporting 0 samples with no error at all. Always ask the
# radio what it wants rather than assuming the convenient format.
_TX_FORMAT_PREFERENCE = ("CS16", "CF32")


def _pick_tx_format(dev, d, ch):
    """(format, full_scale, how_we_decided) for this radio's TX stream."""
    try:
        fmt, full_scale = dev.getNativeStreamFormat(d, ch)
        fmt = str(fmt)
        scale = float(full_scale) or 32767.0
        if fmt in _TX_FORMAT_PREFERENCE:
            return fmt, scale, "driver's native format"
    except Exception:
        pass
    try:
        supported = [str(f) for f in dev.getStreamFormats(d, ch)]
    except Exception:
        supported = []
    for fmt in _TX_FORMAT_PREFERENCE:
        if fmt in supported:
            return fmt, (32767.0 if fmt == "CS16" else 1.0), "driver's format list"
    return _cf32(), 1.0, "fallback (driver named no TX formats)"


def _encode_tx(buf, fmt, full_scale):
    """complex64 → the wire format this radio wants. Returns (array, stride).

    `stride` is array elements per IQ sample, because writeStream counts
    SAMPLES while a CS16 buffer is interleaved int16 — getting that wrong
    transmits half a buffer of garbage.
    """
    if fmt == "CS16":
        # complex64.view(float32) is already [I, Q, I, Q, …] — exactly the CS16
        # interleaving, so no reshaping is needed.
        inter = buf.view(np.float32) * full_scale
        np.clip(inter, -full_scale, full_scale, out=inter)
        return np.ascontiguousarray(inter.astype(np.int16)), 2
    return buf, 1


def _bounds(ranges):
    """min/max across a SoapySDR range list, tolerating Range objects or pairs.

    Drivers are inconsistent here: getFrequencyRange returns a LIST of Range
    objects, getGainRange returns ONE Range, and some bindings hand back a bare
    (min, max) numeric pair. Treating that last shape as a list of two ranges
    silently yields no bounds at all — and a missing gain range means the UI
    cannot default the gain to the radio's quietest setting.
    """
    if (isinstance(ranges, (list, tuple)) and len(ranges) == 2
            and all(isinstance(v, (int, float)) for v in ranges)):
        return float(ranges[0]), float(ranges[1])
    lows, highs = [], []
    if not isinstance(ranges, (list, tuple)):
        ranges = [ranges]
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
    return (min(lows), max(highs)) if lows and highs else None


def probe_tx(device):
    """Ask an open SoapySDR device what its TX side can do.

    Returns {"channels": int, "freq_min"/"freq_max"/"gain_min"/"gain_max"/
    "rate_min"/"rate_max": float} — only the keys the driver answered.
    `channels == 0` means this radio cannot transmit (an RTL-SDR, say), which
    is the signal the whole feature hides itself behind.
    """
    out = {"channels": 0}
    if device is None:
        return out
    d = _tx_dir()
    try:
        out["channels"] = int(device.getNumChannels(d))
    except Exception:
        return out
    if out["channels"] <= 0:
        return out
    for method, lo, hi in (("getFrequencyRange",  "freq_min", "freq_max"),
                           ("getGainRange",       "gain_min", "gain_max"),
                           ("getSampleRateRange", "rate_min", "rate_max")):
        fn = getattr(device, method, None)
        if fn is None:
            continue
        try:
            got = _bounds(fn(d, 0))
        except Exception:
            continue
        if got:
            out[lo], out[hi] = got
    return out


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

class TxController:
    """Owns the transmit lifecycle: idle → arming → transmitting → stopping.

    One instance per process. `start()` validates, tunes, opens a TX stream and
    hands the writer thread a `Waveform`; `stop()` is idempotent and safe from
    any thread. Nothing here touches the RX path — the only shared object is the
    SoapySDR device handle, and RX/TX streams on one device are independent.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._acquirer = None
        self._is_demo = False
        self._thread = None
        self._stop_evt = threading.Event()
        self._state = "idle"
        self._error = None
        self._plan = None          # the validated, as-executed request
        self._started_at = None
        self._samples = 0
        self._underflows = 0
        self._op_id = None
        self._stop_reason = "not stopped"
        self._acknowledged = set()  # subjects that have seen the legal notice
        self._caps_cache = None

    # -- wiring ------------------------------------------------------------

    def bind(self, acquirer, demo=False):
        """Called once by the frontend after the acquisition stack is built."""
        self._acquirer = acquirer
        self._is_demo = bool(demo)

    def _device(self):
        src = getattr(self._acquirer, "source", None)
        return get_device(src) if src is not None else None

    # -- legal acknowledgment ---------------------------------------------

    def acknowledge(self, subject):
        """Record that `subject` has been shown the legal notice.

        Server-side because a modal the client can delete from the DOM is not a
        gate. Cleared by a process restart, which is the honest scope: a new
        server is a new session.
        """
        with self._lock:
            self._acknowledged.add(str(subject))

    def is_acknowledged(self, subject):
        with self._lock:
            return str(subject) in self._acknowledged

    # -- capabilities ------------------------------------------------------

    def capabilities(self, refresh=False):
        """What this radio's TX side can do, or why it can't.

        Cached: probing walks several driver calls and the answer cannot change
        without the source being reopened. `refresh=True` re-probes.
        """
        with self._lock:
            if self._caps_cache is not None and not refresh:
                return dict(self._caps_cache)

        caps = {"available": False, "reason": None, "simulated": False,
                "channels": 0, "waveforms": dict(TX_WAVEFORMS),
                "envelope": {}, "device": state.DEVICE_LABEL}

        if not tx_enabled():
            caps["reason"] = "transmit disabled on this host (RADIO_TX=0)"
        elif self._is_demo:
            # The demo radiates nothing, but the whole flow — menu, legal
            # notice, animation, ops log — must be exercisable without
            # hardware, and interns must be able to rehearse it harmlessly.
            caps.update(available=True, simulated=True, channels=1)
            caps["envelope"] = {"freq_min": 300e6, "freq_max": 6e9,
                                "gain_min": -60.0, "gain_max": 0.0,
                                "rate_min": 1e6, "rate_max": 125e6}
        else:
            dev = self._device()
            if dev is None:
                # Enumeration already asked this radio how many TX channels it
                # has. Use that so a receive-only device says so plainly
                # instead of "not open yet" forever.
                from . import devices as _devices
                hint = _devices.get_adapter().tx_channels()
                caps["reason"] = (
                    f"{state.DEVICE_LABEL} reports no TX channels — this "
                    f"radio is receive-only" if hint == 0
                    else "radio is not open yet")
            else:
                probed = probe_tx(dev)
                n = int(probed.pop("channels", 0) or 0)
                if n <= 0:
                    caps["reason"] = (f"{state.DEVICE_LABEL} reports no TX "
                                      f"channels — this radio is receive-only")
                else:
                    caps.update(available=True, channels=n)
                    caps["envelope"] = probed
        with self._lock:
            self._caps_cache = dict(caps)
        return caps

    def invalidate_capabilities(self):
        """Drop the cache — call after the source is (re)opened."""
        with self._lock:
            self._caps_cache = None

    # -- status ------------------------------------------------------------

    def active(self):
        with self._lock:
            return self._state in ("arming", "transmitting", "stopping")

    def status(self):
        caps = self.capabilities()
        with self._lock:
            plan = dict(self._plan) if self._plan else None
            started = self._started_at
            elapsed = (time.time() - started) if started else 0.0
            duration = plan.get("duration_s") if plan else None
            out = {
                "state": self._state,
                "active": self._state in ("arming", "transmitting", "stopping"),
                "error": self._error,
                "op_id": self._op_id,
                "started_at": started,
                "elapsed_s": round(elapsed, 2),
                "remaining_s": (round(max(0.0, duration - elapsed), 2)
                                if duration else None),
                "samples_written": self._samples,
                "underflows": self._underflows,
                "plan": plan,
            }
        out["available"] = caps["available"]
        out["reason"] = caps["reason"]
        out["simulated"] = caps["simulated"]
        out["capabilities"] = caps
        return out

    # -- demo injection ----------------------------------------------------

    def demo_injection(self):
        """(offset_hz, amplitude) the DemoAcquirer should add to its synth.

        Simulated transmit still has to SHOW something: the demo waterfall
        grows the transmitted tone at the right offset, so the whole feature —
        including "did I tune where I meant to" — is verifiable with no radio
        and no radiation. Returns None when not transmitting.
        """
        with self._lock:
            if self._state != "transmitting" or not self._plan:
                return None
            plan = self._plan
        if plan["waveform"] not in ("cw", "two_tone"):
            return None
        # Where the transmitted carrier lands in the RECEIVER's baseband.
        offset = (plan["frequency_hz"] + plan["params"].get("offset_hz", 0.0)) \
            - float(getattr(self._acquirer.shared.snapshot(), "center", 0.0))
        return offset, float(plan["params"].get("amplitude", DEFAULT_AMPLITUDE))

    # -- start / stop ------------------------------------------------------

    def _validate(self, payload, caps):
        """Turn a request dict into an executable plan, or raise ValueError.

        Frequency is REJECTED rather than clamped when it falls outside the
        radio's range. Silently transmitting somewhere other than where the
        operator asked is the one failure mode this feature cannot have.
        """
        if not isinstance(payload, dict):
            raise ValueError("TX request must be a JSON object")
        env = caps.get("envelope") or {}

        kind = str(payload.get("waveform", "cw")).strip().lower()
        if kind not in TX_WAVEFORMS:
            raise ValueError(f"unknown waveform {kind!r} "
                             f"(known: {', '.join(sorted(TX_WAVEFORMS))})")

        if payload.get("frequency_hz") is None:
            raise ValueError("frequency_hz is required")
        freq = float(payload["frequency_hz"])
        if not math.isfinite(freq) or freq <= 0:
            raise ValueError("frequency_hz must be a positive number")
        lo, hi = env.get("freq_min"), env.get("freq_max")
        if lo is not None and hi is not None and not (lo <= freq <= hi):
            raise ValueError(
                f"{freq/1e6:.6g} MHz is outside this radio's TX range "
                f"({lo/1e6:.6g}–{hi/1e6:.6g} MHz)")

        # Gain defaults to the quietest the radio can manage — never to
        # whatever was used last, and never to the middle of the range.
        gain_min = env.get("gain_min")
        gain = payload.get("gain_db")
        gain = float(gain) if gain is not None else (
            float(gain_min) if gain_min is not None else 0.0)
        g_lo, g_hi = env.get("gain_min"), env.get("gain_max")
        if g_lo is not None and g_hi is not None and not (g_lo <= gain <= g_hi):
            raise ValueError(f"gain {gain:g} dB is outside this radio's TX "
                             f"range ({g_lo:g}–{g_hi:g} dB)")

        amplitude = float(payload.get("amplitude", DEFAULT_AMPLITUDE))
        if not (0.0 < amplitude <= 1.0):
            raise ValueError("amplitude must be in (0, 1] of full scale")

        # Blank duration means "until Stop" — an explicit product decision, so
        # the operator holding a carrier up is doing it deliberately. Every
        # other stop path (process shutdown, source reconnect, recording start)
        # still applies.
        duration = payload.get("duration_s")
        if duration in (None, "", 0):
            duration = None
        else:
            duration = float(duration)
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("duration_s must be a positive number, or "
                                 "blank to transmit until Stop")

        channel = int(payload.get("channel", 0))
        if not (0 <= channel < max(1, int(caps.get("channels", 1)))):
            raise ValueError(f"TX channel {channel} does not exist "
                             f"(radio has {caps.get('channels')})")

        params = {"amplitude": amplitude,
                  "offset_hz": float(payload.get("offset_hz", 0.0) or 0.0)}
        if kind == "two_tone":
            params["spacing_hz"] = float(payload.get("spacing_hz", 100e3))
        if kind == "chirp":
            params["chirp_bandwidth_hz"] = float(
                payload.get("chirp_bandwidth_hz", 1e6))
            period = float(payload.get("chirp_period_s", 0.01))
            if period <= 0:
                raise ValueError("chirp_period_s must be positive")
            params["chirp_period_s"] = period

        # TX rate follows the live RX rate unless asked otherwise: on AD936x-
        # class radios (Pluto, AIR-T, B2xx) the two sides share converter
        # plumbing, and matching them is the least surprising thing to do.
        rate = payload.get("sample_rate_hz")
        if rate in (None, "", 0):
            cfg = self._acquirer.shared.snapshot()
            rate = float(cfg.sample_rate)
        rate = float(rate)
        if rate <= 0:
            raise ValueError("sample_rate_hz must be positive")

        return {"waveform": kind, "frequency_hz": freq, "gain_db": gain,
                "sample_rate_hz": rate, "channel": channel,
                "duration_s": duration, "params": params}

    def start(self, payload, requested_by="admin"):
        """Validate, tune, and begin transmitting. Returns the status dict."""
        caps = self.capabilities(refresh=True)
        if not caps["available"]:
            raise RuntimeError(caps["reason"] or "transmit is not available")
        if not self.is_acknowledged(requested_by):
            raise PermissionError(
                "the transmit legal notice has not been acknowledged")
        with self._lock:
            if self._state != "idle":
                raise RuntimeError(f"transmit is already {self._state}")

        plan = self._validate(payload, caps)
        wave = Waveform(plan["waveform"], plan["sample_rate_hz"], plan["params"])
        bw = wave.occupied_bandwidth_hz()
        summary = (
            f"TX {TX_WAVEFORMS[plan['waveform']]} at "
            f"{plan['frequency_hz']/1e6:.6g} MHz, {plan['gain_db']:g} dB, "
            f"{plan['sample_rate_hz']/1e6:.6g} MS/s, "
            f"≈{bw/1e6:.4g} MHz occupied, "
            + (f"{plan['duration_s']:g} s" if plan["duration_s"] else "until Stop")
            + f" [{requested_by}]"
        )
        op_id = OPERATIONS.begin("tx", summary)

        with self._lock:
            self._state = "arming"
            self._error = None
            self._plan = dict(plan)
            self._samples = 0
            self._underflows = 0
            self._op_id = op_id
            self._started_at = None
            self._stop_evt.clear()

        self._thread = threading.Thread(
            target=self._run, args=(plan, wave, op_id), daemon=True,
            name="tx-writer")
        self._thread.start()
        return self.status()

    def stop(self, reason="stopped by operator", timeout=5.0):
        # NOTHING that re-enters this object may run under _lock: it is a plain
        # (non-reentrant) Lock, and status() takes it too. Stopping an already
        # idle transmitter used to call status() from inside the critical
        # section and wedge the lock for the life of the process — every later
        # /tx request then hung forever.
        with self._lock:
            already_idle = self._state == "idle"
            if not already_idle and self._state != "stopping":
                self._state = "stopping"
            op_id = self._op_id
            # Remember WHO asked. A transmission cancelled during arming is
            # otherwise unattributable, and the usual culprit — Acquirer
            # recovery calling TX.shutdown() — looks like nothing at all.
            self._stop_reason = reason
        if already_idle:
            return self.status()
        OPERATIONS.stage(op_id, "stopping", reason)
        self._stop_evt.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        return self.status()

    def shutdown(self, reason="the radio is being released or shut down"):
        """Stop unconditionally — process exit, reset-radio, source teardown."""
        if self.active():
            self.stop(reason)

    # -- writer thread -----------------------------------------------------

    def _finish(self, op_id, verdict, detail):
        with self._lock:
            self._state = "idle"
            self._started_at = None
            if verdict == "failed":
                self._error = detail
        OPERATIONS.finish(op_id, verdict, detail)

    def _run(self, plan, wave, op_id):
        if self._is_demo:
            self._run_simulated(plan, op_id)
            return
        try:
            self._run_hardware(plan, wave, op_id)
        except Exception as exc:  # noqa: BLE001 — any failure must end the op
            self._finish(op_id, "failed", f"transmit failed: {exc}")

    def _run_simulated(self, plan, op_id):
        OPERATIONS.stage(op_id, "applying",
                         "demo device — nothing is radiated; the transmitted "
                         "tone is injected into the synthetic IQ so the whole "
                         "path stays verifiable")
        OPERATIONS.stage(op_id, "readback", "demo device has no driver to query")
        with self._lock:
            self._state = "transmitting"
            self._started_at = time.time()
        OPERATIONS.stage(op_id, "data-path", "simulated carrier is live")
        duration = plan["duration_s"]
        started = time.time()
        while not self._stop_evt.is_set():
            if duration and (time.time() - started) >= duration:
                self._finish(op_id, "success",
                             f"simulated transmission complete "
                             f"({duration:g} s, nothing radiated)")
                return
            with self._lock:
                self._samples += int(plan["sample_rate_hz"] * 0.1)
            time.sleep(0.1)
        self._finish(op_id, "success",
                     f"simulated transmission stopped after "
                     f"{time.time() - started:.1f} s (nothing radiated)")

    def _run_hardware(self, plan, wave, op_id):
        SoapySDR = _soapy()
        if SoapySDR is None:
            self._finish(op_id, "failed", "SoapySDR is not importable")
            return
        dev = self._device()
        if dev is None:
            self._finish(op_id, "failed", "the radio is not open")
            return
        d = _tx_dir()
        ch = plan["channel"]

        stream = None
        rx_mode = TX_COEXIST
        try:
            (stream, actual, mismatched, rx_mode, fmt,
             full_scale) = self._arm_with_escalation(dev, d, ch, plan, op_id)
            with self._lock:
                self._plan["actual"] = dict(actual)
                self._plan["rx_mode"] = rx_mode
                self._plan["rx_note"] = TX_RX_MODE_NOTES[rx_mode]
                self._plan["stream_format"] = fmt
            if actual.get("sample_rate_hz") and abs(
                    actual["sample_rate_hz"] - wave.fs) > 1.0:
                # Regenerate against the real rate so a driver that snapped the
                # rate does not silently shift every offset in the waveform.
                wave = Waveform(plan["waveform"], actual["sample_rate_hz"],
                                plan["params"])
            try:
                mtu = int(dev.getStreamMTU(stream))
            except Exception:
                mtu = 0
            chunk = mtu if mtu > 0 else DEFAULT_CHUNK
            dev.activateStream(stream)
            with self._lock:
                self._state = "transmitting"
                self._started_at = time.time()
            OPERATIONS.stage(
                op_id, "data-path",
                f"TX stream active ({fmt}, {chunk} samples/write, "
                f"{TX_RX_MODE_NOTES[rx_mode]})"
                + (f" — WARNING: driver did not honour {', '.join(mismatched)}"
                   if mismatched else ""),
                level="warn" if mismatched else "info")

            why = self._pump(dev, stream, wave, plan, chunk, op_id,
                             fmt, full_scale)

            elapsed = time.time() - (self._started_at or time.time())
            verdict = "mismatch" if mismatched else (
                "unverified" if actual.get("frequency_hz") is None else "verified")
            with self._lock:
                samples, unders = self._samples, self._underflows
            self._finish(
                op_id, verdict,
                f"transmitted {elapsed:.1f} s, {samples} samples "
                f"({why})"
                + (f", {unders} underflow(s)" if unders else "")
                + (f" — readback disagreed on {', '.join(mismatched)}"
                   if mismatched else ""))
        finally:
            # Deactivate/close even on the failure path: a TX stream left
            # active keeps the PA keyed. This MUST happen before the RX stream
            # is restored — the TX stream holds the same trigger the RX stream
            # needs back, so resuming first would just move the conflict.
            _close_stream(dev, stream)
            self._restore_rx(rx_mode, op_id)

    #: The ladder, cheapest first.
    _RX_RUNGS = (TX_COEXIST, TX_RX_RELEASED)

    def _source(self):
        return getattr(self._acquirer, "source", None)

    def _arm_with_escalation(self, dev, d, ch, plan, op_id):
        """Free the trigger, program the TX chain, and open its stream.

        Returns (stream, actual, mismatched, rx_mode).

        On the AIR-T the FPGA trigger gates the TUNING CALLS as well as
        `setupStream`. Both failures were observed on real hardware, in this
        order, as the ladder was built:

            Trigger in use, can't set up new stream!
            Trigger in use, can't change frequency!

        The second one is why arming is one atomic unit here. An earlier version
        tuned first and only escalated around `setupStream`, so `setFrequency`
        still ran while the live RX stream held the trigger — sometimes
        silently failing its readback (a MISMATCH verdict with the rate
        unchanged), sometimes raising outright, depending on whether the
        Computer happened to have the RX stream disabled at that instant. That
        race is the whole reason the ladder retries the ENTIRE tune + setup
        sequence at each rung instead of just the stream call.
        """
        last = None
        for index, rung in enumerate(self._RX_RUNGS):
            final = index == len(self._RX_RUNGS) - 1
            try:
                self._enter_rung(rung, op_id)
            except Exception as exc:                      # could not even free it
                last = exc
                if final:
                    raise
                continue
            try:
                actual, mismatched = self._tune_tx(dev, d, ch, plan, op_id)
                stream, fmt, full_scale, how = self._setup_tx_stream(dev, d, ch)
            except Exception as exc:
                self._abandon_rung(rung, op_id)
                last = exc
                if final or not _is_stream_conflict(exc):
                    raise
                OPERATIONS.stage(
                    op_id, "applying",
                    f"the radio refused while the live RX stream held the "
                    f"trigger ({exc}) — giving up more of the live view and "
                    f"retrying", level="warn")
                continue
            OPERATIONS.stage(
                op_id, "applied",
                f"TX stream open in {fmt} (full scale {full_scale:g}, chosen "
                f"from the {how})")
            return stream, actual, mismatched, rung, fmt, full_scale
        raise last or RuntimeError("could not arm the transmitter")

    def _enter_rung(self, rung, op_id):
        """Free as much of the trigger as this rung calls for."""
        if rung == TX_COEXIST:
            return
        if self._stop_evt.is_set():
            # Stop was requested while we were climbing. Do not take the
            # viewer down for a transmission nobody is waiting for.
            raise RuntimeError(
                f"transmission cancelled while arming — {self._stop_reason}")
        # TX_RX_RELEASED — hand the radio over exactly the way a recording
        # does. The Acquirer's pause path closes the RX stream while KEEPING
        # the AIR-T device initialized (source.close() would deinitialize the
        # AD9371 management sensors for the life of the process); resume()
        # rearms it afterwards.
        OPERATIONS.stage(
            op_id, "applying",
            "this radio cannot tune or stream TX while it is receiving — "
            "pausing live acquisition for the duration of the transmission",
            level="warn")
        if not self._acquirer.pause_and_release(15.0):
            raise RuntimeError(
                "live acquisition did not release the radio within 15 s, so "
                "the transmitter cannot be armed (this radio has one trigger)")

    def _abandon_rung(self, rung, op_id):
        """Undo whatever _enter_rung took, so the next rung starts clean."""
        try:
            if rung == TX_RX_RELEASED:
                self._acquirer.resume()
        except Exception:
            pass

    def _tune_tx(self, dev, d, ch, plan, op_id):
        """Program the TX chain, then require the driver to confirm it.

        Only writes a setting the radio is not ALREADY on. That is not an
        optimization: on this hardware TX and RX share converter plumbing, and
        asking the driver to "change" the sample rate to the value it is
        already running is both pointless and a way to earn a
        "Trigger in use, can't change ..." for nothing. Since the TX rate
        defaults to the live RX rate, the common case now touches the rate not
        at all.
        """
        OPERATIONS.stage(op_id, "applying",
                         f"tuning TX{ch}: {plan['frequency_hz']/1e6:.6g} MHz, "
                         f"{plan['gain_db']:g} dB, "
                         f"{plan['sample_rate_hz']/1e6:.6g} MS/s")
        # Sample rate FIRST: on AD936x parts the rate reprograms the filter
        # chain, which can move the achievable frequency/gain settings.
        plan_keys = (
            ("sample_rate_hz", "getSampleRate", "setSampleRate",
             max(1.0, 1e-4 * abs(plan["sample_rate_hz"]))),
            ("frequency_hz", "getFrequency", "setFrequency",
             max(10.0, 1e-6 * abs(plan["frequency_hz"]))),
            ("gain_db", "getGain", "setGain", 0.01),
        )
        untouched = []
        for key, getter, setter, tol in plan_keys:
            want = float(plan[key])
            current = None
            try:
                current = float(getattr(dev, getter)(d, ch))
            except Exception:
                pass
            if current is not None and abs(current - want) <= tol:
                untouched.append(key)
                continue
            fn = getattr(dev, setter, None)
            if fn is None:
                raise RuntimeError(f"driver cannot set TX {key}")
            fn(d, ch, want)
        if untouched:
            OPERATIONS.stage(op_id, "applying",
                             "already correct, left alone: "
                             + ", ".join(untouched))

        # Readback — the same contract as every other operation in this app: a
        # setting is not trusted because the setter returned, it is trusted
        # because the driver says so.
        actual = {}
        for key, getter, _setter, _tol in plan_keys:
            try:
                actual[key] = float(getattr(dev, getter)(d, ch))
            except Exception:
                actual[key] = None

        def _fmt(key, value):
            if value is None:
                return f"{key}=?"
            if key == "gain_db":
                return f"{key}={value:g} dB"
            unit = "MHz" if key == "frequency_hz" else "MS/s"
            return f"{key}={value/1e6:.6g} {unit}"

        OPERATIONS.stage(op_id, "readback", "TX driver reports "
                         + ", ".join(_fmt(k, v) for k, v in actual.items()))
        mismatched = [
            key for key, _g, _s, tol in plan_keys
            if key != "gain_db" and actual[key] is not None
            and abs(actual[key] - float(plan[key])) > tol
        ]
        return actual, mismatched

    def _setup_tx_stream(self, dev, d, ch):
        """Open the TX stream in the format the RADIO wants.

        Returns (stream, format, full_scale, how). Deepwave's own TX example
        passes tx_buffer_size; drivers that do not know the key ignore it, and
        the no-args form is retried for bindings whose setupStream takes no
        kwargs at all.
        """
        fmt, full_scale, how = _pick_tx_format(dev, d, ch)
        try:
            stream = dev.setupStream(d, fmt, [ch],
                                     {"tx_buffer_size": str(DEFAULT_CHUNK)})
        except TypeError:
            stream = dev.setupStream(d, fmt, [ch])
        return stream, fmt, full_scale, how

    def _restore_rx(self, rx_mode, op_id):
        """Give the live view back. Only rung 2 actually took it away."""
        if rx_mode != TX_RX_RELEASED:
            return
        try:
            self._acquirer.resume()
            OPERATIONS.stage(op_id, "resume-live",
                             "live acquisition resumed after the transmission")
        except Exception as exc:  # noqa: BLE001
            OPERATIONS.stage(op_id, "resume-live",
                             f"could not resume live acquisition: {exc}",
                             level="error")

    def _pump(self, dev, stream, wave, plan, chunk, op_id,
              fmt="CF32", full_scale=1.0):
        """Feed the TX stream until stop, duration, or the device disappears.

        Returns a short reason for WHY it returned. A transmission that reports
        "0 samples" is otherwise unexplainable from the log, and guessing at it
        after the fact wastes a trip to the radio.
        """
        try:
            from SoapySDR import SOAPY_SDR_TIMEOUT
        except Exception:
            SOAPY_SDR_TIMEOUT = -1
        try:
            from SoapySDR import SOAPY_SDR_UNDERFLOW
        except Exception:
            SOAPY_SDR_UNDERFLOW = -5

        duration = plan["duration_s"]
        started = time.time()
        my_dev = dev
        stalled_since = None

        if self._stop_evt.is_set():
            # Something asked us to stop between arming and the first write —
            # an operator Stop, or Acquirer recovery calling TX.shutdown().
            # Name it, because "0 samples" with no reason is a mystery.
            OPERATIONS.stage(op_id, "stopping",
                             "stop was already requested before the first "
                             "write — nothing was transmitted", level="warn")
            return "stopped before the first write"

        while not self._stop_evt.is_set():
            if duration and (time.time() - started) >= duration:
                OPERATIONS.stage(op_id, "stopping",
                                 f"requested duration of {duration:g} s elapsed")
                return "duration elapsed"
            # The Acquirer can close and reopen the source under us (retune
            # recovery, source-spec reconnect). Writing into a stream on a
            # freed device is undefined; notice and get out.
            if self._device() is not my_dev:
                OPERATIONS.stage(
                    op_id, "stopping",
                    "the radio was reopened underneath the transmission "
                    "(retune recovery or source reconnect) — TX aborted",
                    level="warn")
                return "radio was reopened underneath the transmission"

            wire, stride = _encode_tx(wave.next(chunk), fmt, full_scale)
            written = 0
            while written < chunk and not self._stop_evt.is_set():
                try:
                    res = dev.writeStream(stream, [wire[written * stride:]],
                                          chunk - written, timeoutUs=200000)
                except Exception as exc:
                    raise RuntimeError(f"writeStream failed: {exc}") from exc
                ret = getattr(res, "ret", None)
                if ret is None:
                    ret = res[0] if isinstance(res, (list, tuple)) else int(res)
                if ret > 0:
                    written += int(ret)
                elif ret == SOAPY_SDR_TIMEOUT:
                    break      # re-check stop/duration, then retry this chunk
                else:
                    raise RuntimeError(f"writeStream returned {ret}")
            with self._lock:
                self._samples += written
                total = self._samples

            # A radio that accepts the stream but never consumes a sample is
            # the worst failure this feature can have: it looks like it is
            # transmitting, forever, at 0 samples. Give it a few seconds and
            # then say so instead of spinning silently.
            if total == 0:
                if stalled_since is None:
                    stalled_since = time.time()
                elif time.time() - stalled_since > 5.0:
                    raise RuntimeError(
                        f"the TX stream accepted no samples for 5 s in {fmt} "
                        f"format — the radio opened and activated the stream "
                        f"but is not consuming it. This is what a wrong wire "
                        f"format looks like on this driver.")
            else:
                stalled_since = None

            status_fn = getattr(dev, "readStreamStatus", None)
            if status_fn is not None:
                try:
                    st = status_fn(stream, timeoutUs=0)
                    flags = getattr(st, "flags", 0)
                    if flags & SOAPY_SDR_UNDERFLOW:
                        with self._lock:
                            self._underflows += 1
                except Exception:
                    pass
        return "stopped by request"


#: Process-wide controller. Frontends call TX.bind() once at startup.
TX = TxController()
