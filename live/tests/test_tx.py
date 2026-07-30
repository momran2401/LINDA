"""Transmit mode: waveforms, the controller state machine, and the HTTP gate.

No hardware and no SoapySDR: the waveform tests are pure numpy, the controller
tests drive a fake device that records every driver call, and the HTTP tests
spawn the demo server (which simulates TX and radiates nothing).

The rule these tests exist to hold: a transmitter must never come up somewhere
other than where it was told to, must never come up without the operator having
accepted the legal notice, and must always be able to be stopped.
"""
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

LIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIVE))

from core import tx as txmod   # noqa: E402


# ---------------------------------------------------------------------------
# Waveforms
# ---------------------------------------------------------------------------

def _peak_offset_hz(samples, fs):
    """Frequency of the strongest bin, in Hz relative to baseband centre."""
    spec = np.abs(np.fft.fft(samples))
    freqs = np.fft.fftfreq(len(samples), 1.0 / fs)
    return float(freqs[int(np.argmax(spec))])


def test_cw_lands_on_the_requested_offset():
    fs = 4e6
    w = txmod.Waveform("cw", fs, {"offset_hz": 750e3, "amplitude": 0.5})
    x = w.next(8192)
    assert abs(_peak_offset_hz(x, fs) - 750e3) < fs / 8192


def test_phase_is_continuous_across_chunks():
    """A tone generated in pieces must be the same tone as one generated whole.

    This is the float64-fractional-cycles rule the demo synth documents: an
    absolute float32 time axis scrambles a MHz tone within a minute, and a
    transmitter runs far longer than a demo frame.
    """
    fs = 4e6
    w = txmod.Waveform("cw", fs, {"offset_hz": 300e3, "amplitude": 0.5})
    pieces = [w.next(1024) for _ in range(8)]
    chunked = np.concatenate(pieces)
    whole = txmod.Waveform("cw", fs, {"offset_hz": 300e3, "amplitude": 0.5}).next(8192)
    assert np.allclose(chunked, whole, atol=1e-6)
    # And the spectrum is still a single clean line, not a smeared one.
    assert abs(_peak_offset_hz(chunked, fs) - 300e3) < fs / 8192


def test_two_tone_produces_two_lines_at_the_requested_spacing():
    fs = 4e6
    w = txmod.Waveform("two_tone", fs,
                       {"offset_hz": 0.0, "spacing_hz": 400e3, "amplitude": 0.6})
    x = w.next(8192)
    spec = np.abs(np.fft.fftshift(np.fft.fft(x)))
    freqs = np.fft.fftshift(np.fft.fftfreq(len(x), 1.0 / fs))
    top = freqs[np.argsort(spec)[-2:]]
    assert abs(abs(top[0] - top[1]) - 400e3) < 2 * fs / 8192


def test_chirp_covers_its_requested_bandwidth():
    fs = 8e6
    bw = 2e6
    w = txmod.Waveform("chirp", fs, {"chirp_bandwidth_hz": bw,
                                     "chirp_period_s": 0.002, "amplitude": 0.5})
    x = w.next(int(fs * 0.002))
    spec = np.abs(np.fft.fftshift(np.fft.fft(x)))
    freqs = np.fft.fftshift(np.fft.fftfreq(len(x), 1.0 / fs))
    lit = freqs[spec > 0.25 * spec.max()]
    # The occupied span should be close to the requested bandwidth, and must
    # not exceed it by more than a little leakage.
    assert bw * 0.7 <= (lit.max() - lit.min()) <= bw * 1.4


@pytest.mark.parametrize("kind", sorted(txmod.TX_WAVEFORMS))
def test_no_waveform_exceeds_digital_full_scale(kind):
    """Clipping the DAC is a hardware fault, not a cosmetic one."""
    w = txmod.Waveform(kind, 4e6, {"amplitude": 1.0})
    x = w.next(16384)
    assert x.dtype == np.complex64
    assert np.abs(x.real).max() <= 1.0 and np.abs(x.imag).max() <= 1.0


def test_unknown_waveform_is_rejected():
    with pytest.raises(ValueError):
        txmod.Waveform("jam_everything", 4e6)


# ---------------------------------------------------------------------------
# Controller against a fake SoapySDR device
# ---------------------------------------------------------------------------

class FakeDevice:
    """Records every driver call and honours whatever it is told, so a
    readback mismatch in a test means the CODE disagreed, not the fake."""

    def __init__(self, tx_channels=2, snap_rate=None):
        self.tx_channels = tx_channels
        self.calls = []
        self.set_values = {}
        self._snap_rate = snap_rate        # simulate a driver rounding the rate
        self.activated = False
        self.closed = False
        self.written = 0

    def getNumChannels(self, direction):
        return self.tx_channels

    def getFrequencyRange(self, d, ch):
        return [(70e6, 6e9)]

    def getGainRange(self, d, ch):
        return (-40.0, 20.0)

    def getSampleRateRange(self, d, ch):
        return [(1e6, 60e6)]

    def setSampleRate(self, d, ch, v):
        self.calls.append(("setSampleRate", ch, v))
        self.set_values["rate"] = self._snap_rate or v

    def setFrequency(self, d, ch, v):
        self.calls.append(("setFrequency", ch, v))
        self.set_values["freq"] = v

    def setGain(self, d, ch, v):
        self.calls.append(("setGain", ch, v))
        self.set_values["gain"] = v

    def getSampleRate(self, d, ch):
        return self.set_values.get("rate", 0.0)

    def getFrequency(self, d, ch):
        return self.set_values.get("freq", 0.0)

    def getGain(self, d, ch):
        return self.set_values.get("gain", 0.0)

    #: What this radio says it wants on the wire. The AIR-T answers CS16.
    native_format = ("CF32", 1.0)

    def getNativeStreamFormat(self, d, ch):
        return self.native_format

    def setupStream(self, d, fmt, chans, args=None):
        self.stream_format = fmt
        self.calls.append(("setupStream", tuple(chans)))
        return "stream"

    def getStreamMTU(self, s):
        return 4096

    def activateStream(self, s):
        self.activated = True

    def deactivateStream(self, s):
        self.activated = False

    def closeStream(self, s):
        self.closed = True

    def writeStream(self, s, buffers, n, timeoutUs=0):
        self.written += n
        self.wire_dtype = getattr(buffers[0], "dtype", None)

        class R:
            ret = n
        return R()

    def readStreamStatus(self, s, timeoutUs=0):
        raise RuntimeError("no status")


class TriggerBoundDevice(FakeDevice):
    """Reproduces the AIR8201B's real refusals, both of them.

    AirStack's SoapyAIRT arms every stream from ONE FPGA trigger block, and the
    live RX stream holds it. Two distinct errors were observed on hardware:

        Trigger in use, can't set up new stream!     (setupStream)
        Trigger in use, can't change frequency!     (setFrequency)

    The second is the important one — the trigger gates TUNING, not just stream
    creation — so this fake gates both. `rx_stream_open` is cleared only when
    the acquirer releases the radio, matching the real driver, where
    deactivating the stream is not enough.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.rx_stream_open = True
        self.setup_attempts = 0
        self.tune_attempts = 0
        self.stream_args = None

    def _guard_tuning(self, what):
        self.tune_attempts += 1
        if self.rx_stream_open:
            raise RuntimeError(f"Trigger in use, can't change {what}!")

    def setSampleRate(self, d, ch, v):
        self._guard_tuning("sample rate")
        super().setSampleRate(d, ch, v)

    def setFrequency(self, d, ch, v):
        self._guard_tuning("frequency")
        super().setFrequency(d, ch, v)

    def setGain(self, d, ch, v):
        self._guard_tuning("gain")
        super().setGain(d, ch, v)

    def setupStream(self, d, fmt, chans, args=None):
        self.setup_attempts += 1
        if self.rx_stream_open:
            raise RuntimeError("Trigger in use, can't set up new stream!")
        self.stream_args = args
        self.calls.append(("setupStream", tuple(chans)))
        return "stream"


class FakeSource:
    def __init__(self, device):
        self._device = device


class FakeShared:
    class _Cfg:
        center = 3750e6
        sample_rate = 15.36e6

    def snapshot(self):
        return self._Cfg()


class FakeAcquirer:
    def __init__(self, device):
        self.source = FakeSource(device) if device else None
        self.shared = FakeShared()
        self.paused = 0
        self.resumed = 0

    def pause_and_release(self, timeout=10.0):
        """Mirrors Acquirer's pause path: closes the RX stream but KEEPS the
        device initialized (source.close() would deinitialize the AIR-T's
        AD9371 management sensors for the life of the process)."""
        self.paused += 1
        dev = self.source._device if self.source else None
        if isinstance(dev, TriggerBoundDevice):
            dev.rx_stream_open = False
        return True

    def resume(self):
        self.resumed += 1
        dev = self.source._device if self.source else None
        if isinstance(dev, TriggerBoundDevice):
            dev.rx_stream_open = True


@pytest.fixture
def controller(monkeypatch):
    """A fresh controller bound to a fake radio, with SoapySDR stubbed out."""
    dev = FakeDevice()
    ctl = txmod.TxController()
    ctl.bind(FakeAcquirer(dev), demo=False)
    monkeypatch.setattr(txmod, "_soapy", lambda: object())
    monkeypatch.setattr(txmod, "_tx_dir", lambda: 0)
    return ctl, dev


def _wait_state(ctl, state, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if ctl.status()["state"] == state:
            return True
        time.sleep(0.02)
    return False


def test_capabilities_report_the_drivers_real_limits(controller):
    ctl, _dev = controller
    caps = ctl.capabilities()
    assert caps["available"] and caps["channels"] == 2
    assert caps["envelope"]["freq_min"] == 70e6
    assert caps["envelope"]["gain_max"] == 20.0


def test_receive_only_radio_reports_unavailable(monkeypatch):
    ctl = txmod.TxController()
    ctl.bind(FakeAcquirer(FakeDevice(tx_channels=0)), demo=False)
    caps = ctl.capabilities()
    assert not caps["available"]
    assert "receive-only" in caps["reason"]


def test_start_requires_the_legal_notice(controller):
    ctl, _dev = controller
    with pytest.raises(PermissionError):
        ctl.start({"waveform": "cw", "frequency_hz": 2450e6})


def test_frequency_outside_the_radios_range_is_rejected_not_clamped(controller):
    """The one failure this feature cannot have: transmitting somewhere other
    than where the operator asked."""
    ctl, dev = controller
    ctl.acknowledge("admin")
    with pytest.raises(ValueError, match="outside this radio's TX range"):
        ctl.start({"waveform": "cw", "frequency_hz": 30e9})
    assert dev.calls == []          # nothing was programmed into the driver


def test_gain_defaults_to_the_quietest_the_radio_supports(controller):
    ctl, dev = controller
    ctl.acknowledge("admin")
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6, "duration_s": 0.3})
    assert _wait_state(ctl, "idle")
    gains = [c for c in dev.calls if c[0] == "setGain"]
    assert gains and gains[0][2] == -40.0


def test_transmission_tunes_then_streams_then_unkeys(controller):
    ctl, dev = controller
    ctl.acknowledge("admin")
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6, "gain_db": 0.0,
               "duration_s": 0.4})
    assert _wait_state(ctl, "idle", timeout=8)
    names = [c[0] for c in dev.calls]
    # Rate first: it reprograms the filter chain on AD936x parts.
    assert names.index("setSampleRate") < names.index("setFrequency")
    assert dev.written > 0
    assert dev.activated is False and dev.closed is True


def test_stop_is_idempotent_and_never_deadlocks(controller):
    """Regression: stop() used to call status() while holding the lock, and a
    stop on an already-idle controller wedged it for the life of the process."""
    ctl, _dev = controller
    ctl.acknowledge("admin")
    for _ in range(3):
        assert ctl.stop()["state"] == "idle"
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6})
    assert _wait_state(ctl, "transmitting")
    assert ctl.stop()["state"] == "idle"
    assert ctl.stop()["state"] == "idle"


def test_blank_duration_transmits_until_stopped(controller):
    ctl, _dev = controller
    ctl.acknowledge("admin")
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6, "duration_s": None})
    assert _wait_state(ctl, "transmitting")
    time.sleep(0.5)
    assert ctl.status()["state"] == "transmitting"     # no automatic cutoff
    assert ctl.status()["remaining_s"] is None
    ctl.stop()
    assert ctl.status()["state"] == "idle"


def test_waveform_follows_the_rate_the_driver_actually_applied(controller):
    """A driver that snaps the sample rate must not silently shift every
    offset in the transmitted waveform."""
    ctl, dev = controller
    dev._snap_rate = 20e6
    ctl.acknowledge("admin")
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6,
               "sample_rate_hz": 15.36e6, "duration_s": 0.3})
    assert _wait_state(ctl, "transmitting")
    assert ctl.status()["plan"]["actual"]["sample_rate_hz"] == 20e6
    ctl.stop()


def test_second_start_while_transmitting_is_refused(controller):
    ctl, _dev = controller
    ctl.acknowledge("admin")
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6})
    assert _wait_state(ctl, "transmitting")
    with pytest.raises(RuntimeError, match="already"):
        ctl.start({"waveform": "cw", "frequency_hz": 900e6})
    ctl.stop()


def test_losing_the_device_aborts_the_transmission(controller):
    """A retune-recovery or source reconnect swaps the device handle; writing
    into a stream on freed driver state is undefined."""
    ctl, _dev = controller
    ctl.acknowledge("admin")
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6})
    assert _wait_state(ctl, "transmitting")
    ctl._acquirer.source = FakeSource(FakeDevice())     # a different handle
    assert _wait_state(ctl, "idle", timeout=8)


def test_radio_tx_env_removes_the_feature(monkeypatch):
    monkeypatch.setenv("RADIO_TX", "0")
    ctl = txmod.TxController()
    ctl.bind(FakeAcquirer(FakeDevice()), demo=False)
    caps = ctl.capabilities()
    assert not caps["available"]
    assert "RADIO_TX=0" in caps["reason"]


def test_demo_mode_is_simulated_and_injects_a_visible_carrier():
    ctl = txmod.TxController()
    ctl.bind(FakeAcquirer(None), demo=True)
    caps = ctl.capabilities()
    assert caps["available"] and caps["simulated"]
    ctl.acknowledge("admin")
    ctl.start({"waveform": "cw", "frequency_hz": 3750e6, "offset_hz": 2e6})
    assert _wait_state(ctl, "transmitting")
    off, amp = ctl.demo_injection()
    # Centre is 3750 MHz in FakeShared, so a 3750 MHz carrier with a +2 MHz
    # baseband offset must show up 2 MHz from the receiver's centre.
    assert abs(off - 2e6) < 1.0 and amp == txmod.DEFAULT_AMPLITUDE
    ctl.stop()
    assert ctl.demo_injection() is None


# ---------------------------------------------------------------------------
# The AIR-T trigger conflict: one FPGA trigger, two streams that both want it
# ---------------------------------------------------------------------------

@pytest.fixture
def trigger_bound(monkeypatch):
    dev = TriggerBoundDevice()
    ctl = txmod.TxController()
    ctl.bind(FakeAcquirer(dev), demo=False)
    monkeypatch.setattr(txmod, "_soapy", lambda: object())
    monkeypatch.setattr(txmod, "_tx_dir", lambda: 0)
    ctl.acknowledge("admin")
    return ctl, dev


def test_full_duplex_radio_never_disturbs_the_live_view(controller):
    """A driver that allows both streams must not cost the viewer anything."""
    ctl, _dev = controller
    ctl.acknowledge("admin")
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6})
    assert _wait_state(ctl, "transmitting")
    assert ctl.status()["plan"]["rx_mode"] == txmod.TX_COEXIST
    assert ctl._acquirer.paused == 0
    ctl.stop()


def test_trigger_conflict_falls_back_to_releasing_the_receiver(trigger_bound):
    """The real AIR8201B failure: TX setup is refused while RX holds the
    trigger. The transmission must still happen — by handing the radio over
    the same way a recording does — and must say that it did."""
    ctl, dev = trigger_bound
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6})
    assert _wait_state(ctl, "transmitting")
    status = ctl.status()
    assert status["plan"]["rx_mode"] == txmod.TX_RX_RELEASED
    assert "LIVE VIEW IS DOWN" in status["plan"]["rx_note"]
    assert ctl._acquirer.paused == 1
    # It tried coexisting first, then released. There is deliberately no middle
    # "just deactivate RX" rung — see the comment on TX_RX_MODE_NOTES.
    assert dev.tune_attempts >= 2         # rung 0, then the real one
    assert dev.setup_attempts == 1        # only once, after the radio was free
    ctl.stop()


def test_the_receiver_stream_is_never_pulled_from_under_the_acquirer(trigger_bound):
    """Regression for a cascade seen on hardware.

    An earlier middle rung deactivated the RX stream directly. The Acquirer
    thread was blocked reading it, so it got a TIMEOUT, decided the radio was
    broken, ran _recover() — which calls TX.shutdown() and killed the very
    transmission that caused it — and left the channel in a state where
    re-arming failed with "Invalid RX channel state to set up triggering!".
    The only sanctioned way to take the stream is to ASK the Acquirer.
    """
    ctl, dev = trigger_bound
    disabled = []
    import core.shims as shims
    real = shims.enable_stream
    # core.tx imported enable_stream by name, so patch it there.
    txmod.enable_stream = lambda src, on: disabled.append(on)
    try:
        ctl.start({"waveform": "cw", "frequency_hz": 2450e6, "duration_s": 0.3})
        assert _wait_state(ctl, "idle", timeout=8)
        assert disabled == [], (
            "TX disabled the RX stream directly instead of asking the acquirer")
        assert ctl._acquirer.paused == 1
    finally:
        txmod.enable_stream = real


def test_the_receiver_comes_back_after_the_transmission(trigger_bound):
    """Whatever the ladder took away it has to give back — including on the
    failure path, or one transmission leaves the viewer dark forever."""
    ctl, dev = trigger_bound
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6, "duration_s": 0.3})
    assert _wait_state(ctl, "idle", timeout=8)
    assert ctl._acquirer.resumed == 1
    assert dev.rx_stream_open is True
    assert dev.closed is True             # TX stream released the trigger


def test_tx_stream_is_closed_before_the_receiver_is_restored(trigger_bound):
    """Ordering matters: the TX stream holds the very trigger the RX stream
    needs back, so resuming first would just move the conflict."""
    ctl, dev = trigger_bound
    order = []
    real_close = dev.closeStream
    dev.closeStream = lambda s: (order.append("tx_closed"), real_close(s))[1]
    real_resume = ctl._acquirer.resume
    ctl._acquirer.resume = lambda: (order.append("rx_resumed"), real_resume())[1]
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6, "duration_s": 0.3})
    assert _wait_state(ctl, "idle", timeout=8)
    assert order == ["tx_closed", "rx_resumed"]


def test_tuning_never_runs_while_the_receiver_holds_the_trigger(trigger_bound):
    """The second hardware failure: "Trigger in use, can't change frequency!".

    An earlier version tuned BEFORE climbing the ladder, so setFrequency ran
    against a busy trigger — sometimes raising, sometimes just failing its
    readback and reporting MISMATCH with 0 samples. Every successful tuning
    call must happen after the radio has been freed.
    """
    ctl, dev = trigger_bound
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6, "gain_db": 0.0,
               "sample_rate_hz": 20e6, "duration_s": 0.3})
    assert _wait_state(ctl, "transmitting")
    # The plan's values are what the driver ended up holding, which can only
    # happen if the setters ran inside the freed window.
    actual = ctl.status()["plan"]["actual"]
    assert actual["frequency_hz"] == 2450e6
    assert actual["sample_rate_hz"] == 20e6
    assert ctl.status()["plan"]["rx_mode"] == txmod.TX_RX_RELEASED
    ctl.stop()


def test_a_setting_already_correct_is_not_rewritten(controller):
    """Asking this radio to "change" the rate to the value it already runs is
    both pointless and a way to earn a trigger conflict for nothing."""
    ctl, dev = controller
    ctl.acknowledge("admin")
    # Put the driver on the exact values the plan will ask for.
    dev.set_values.update({"rate": 15.36e6, "freq": 2450e6, "gain": -40.0})
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6, "gain_db": -40.0,
               "sample_rate_hz": 15.36e6, "duration_s": 0.3})
    assert _wait_state(ctl, "idle", timeout=8)
    setters = [c[0] for c in dev.calls
               if c[0] in ("setSampleRate", "setFrequency", "setGain")]
    assert setters == [], f"rewrote settings that were already correct: {setters}"


def test_pump_reports_why_it_stopped(controller):
    """A transmission that reports 0 samples must say why, or the log is a
    mystery that costs a trip to the radio to resolve."""
    ctl, _dev = controller
    ctl.acknowledge("admin")
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6, "duration_s": 0.3})
    assert _wait_state(ctl, "idle", timeout=8)
    stages = " ".join(s["detail"] for s in _op_stages(ctl))
    assert "duration" in stages


def _op_stages(ctl):
    from core.operations import OPERATIONS
    op = OPERATIONS.get(ctl.status()["op_id"])
    return op["stages"] if op else []


def test_deepwave_tx_buffer_size_arg_is_passed(trigger_bound):
    """Deepwave's own TX example passes tx_buffer_size; drivers that don't
    know the key ignore it."""
    ctl, dev = trigger_bound
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6, "duration_s": 0.3})
    assert _wait_state(ctl, "idle", timeout=8)
    assert dev.stream_args and "tx_buffer_size" in dev.stream_args


def test_a_bad_request_fails_fast_instead_of_dropping_the_receiver():
    """Only a RESOURCE conflict may escalate. A driver rejecting the request
    itself must not cost the operator their live view."""
    # A plain device: tuning works, so the ONLY failure is the stream request
    # itself, and it is not a resource conflict.
    dev = FakeDevice()

    def refuse(d, fmt, chans, args=None):
        raise RuntimeError("invalid channel index")

    dev.setupStream = refuse
    ctl = txmod.TxController()
    ctl.bind(FakeAcquirer(dev), demo=False)
    ctl.acknowledge("admin")
    import core.tx as _t
    orig_soapy, orig_dir = _t._soapy, _t._tx_dir
    _t._soapy, _t._tx_dir = (lambda: object()), (lambda: 0)
    try:
        ctl.start({"waveform": "cw", "frequency_hz": 2450e6})
        assert _wait_state(ctl, "idle", timeout=8)
        assert ctl._acquirer.paused == 0        # viewer was never touched
        assert "invalid channel index" in (ctl.status()["error"] or "")
    finally:
        _t._soapy, _t._tx_dir = orig_soapy, orig_dir


# ---------------------------------------------------------------------------
# Wire format: ask the radio, never assume
# ---------------------------------------------------------------------------

def test_waveform_is_encoded_in_the_format_the_radio_asked_for(controller):
    """The AIR-T's DMA wants CS16. Handing it CF32 sets up and activates
    cleanly and then never consumes a sample — five minutes of "transmitting"
    at 0 samples with no error, which is what the hardware actually did."""
    ctl, dev = controller
    dev.native_format = ("CS16", 32767.0)
    ctl.acknowledge("admin")
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6, "duration_s": 0.3})
    assert _wait_state(ctl, "idle", timeout=8)
    assert dev.stream_format == "CS16"
    assert dev.wire_dtype == np.int16
    assert ctl.status()["plan"]["stream_format"] == "CS16"


def test_cf32_radio_still_gets_cf32(controller):
    ctl, dev = controller
    dev.native_format = ("CF32", 1.0)
    ctl.acknowledge("admin")
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6, "duration_s": 0.3})
    assert _wait_state(ctl, "idle", timeout=8)
    assert dev.stream_format == "CF32"
    assert dev.wire_dtype == np.complex64


def test_cs16_encoding_preserves_the_tone_and_fills_the_dac_range():
    """Interleaving and scaling have to be right or the radio transmits
    garbage that still 'works' as far as every status field is concerned."""
    fs = 4e6
    w = txmod.Waveform("cw", fs, {"offset_hz": 500e3, "amplitude": 1.0})
    buf = w.next(4096)
    wire, stride = txmod._encode_tx(buf, "CS16", 32767.0)
    assert stride == 2 and wire.dtype == np.int16 and wire.size == buf.size * 2
    # Round-trip and confirm it is still the same tone at the same offset.
    back = (wire[0::2].astype(np.float32)
            + 1j * wire[1::2].astype(np.float32)) / 32767.0
    assert abs(_peak_offset_hz(back, fs) - 500e3) < fs / 4096
    assert 0.9 <= np.abs(back).max() <= 1.0        # uses the DAC's range


def test_a_radio_that_never_consumes_samples_fails_instead_of_spinning(controller):
    """Regression for the five-minute silent no-op: a stream that accepts
    nothing must be reported, not pumped forever at 0 samples."""
    ctl, dev = controller
    try:
        from SoapySDR import SOAPY_SDR_TIMEOUT
    except Exception:
        SOAPY_SDR_TIMEOUT = -1

    def always_timeout(s, buffers, n, timeoutUs=0):
        class R:
            ret = SOAPY_SDR_TIMEOUT
        return R()

    dev.writeStream = always_timeout
    ctl.acknowledge("admin")
    ctl.start({"waveform": "cw", "frequency_hz": 2450e6})
    assert _wait_state(ctl, "idle", timeout=20)
    err = ctl.status()["error"] or ""
    assert "accepted no samples" in err, err
    assert ctl.status()["samples_written"] == 0


# ---------------------------------------------------------------------------
# HTTP surface (demo server — simulated TX, nothing radiated)
# ---------------------------------------------------------------------------

ADMIN = "tx-admin"
VIEWER = "tx-viewer"


def _req(base, path, auth=None, payload=None, method=None):
    headers = {}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(
            f"{auth}:".encode()).decode()
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base + path, data=data, headers=headers,
        method=method or ("POST" if payload is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, None


@pytest.fixture(scope="module")
def server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, ADMIN_USER=ADMIN, VIEWER_USER=VIEWER,
               INTERN_USER="tx-intern", RADIO_SESSION_SECRET="tx-secret")
    env.pop("RADIO_AUTH_DISABLE", None)
    env.pop("RADIO_TX", None)
    proc = subprocess.Popen(
        [sys.executable, str(LIVE / "striqt_web_server.py"),
         "--demo", "--backend", "quicklook", "--port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(80):
            try:
                if _req(base, "/health")[0] == 200:
                    break
            except Exception:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("tx demo server never became healthy")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_read_only_role_cannot_transmit(server):
    assert _req(server, "/tx/start", VIEWER,
                {"waveform": "cw", "frequency_hz": 2450e6})[0] == 403
    assert _req(server, "/tx/stop", VIEWER, {})[0] == 403
    assert _req(server, "/tx/acknowledge", VIEWER, {})[0] == 403


def test_read_only_role_can_still_see_that_the_radio_is_transmitting(server):
    """A shared instrument that is radiating must say so to everyone on it."""
    status, body = _req(server, "/tx", VIEWER)
    assert status == 200
    assert body["tx"]["may_transmit"] is False
    assert "state" in body["tx"]


def test_start_is_refused_until_the_notice_is_acknowledged(server):
    status, body = _req(server, "/tx/start", ADMIN,
                        {"waveform": "cw", "frequency_hz": 2450e6})
    assert status == 428, body
    assert "acknowledge" in body["error"]


def test_disclaimer_is_served_by_the_api(server):
    _status, body = _req(server, "/tx", ADMIN)
    text = " ".join(body["tx"]["disclaimer"]["body"])
    assert "FCC will come for your ass and they WILL find you" in text
    assert "no responsibility" in text


def test_acknowledged_admin_can_transmit_and_stop(server):
    assert _req(server, "/tx/acknowledge", ADMIN, {})[0] == 200
    status, body = _req(server, "/tx/start", ADMIN,
                        {"waveform": "cw", "frequency_hz": 2450e6,
                         "offset_hz": 1e6})
    assert status == 202, body
    assert body["tx"]["simulated"] is True          # demo radiates nothing
    assert _req(server, "/tx", ADMIN)[1]["tx"]["active"] is True
    assert _req(server, "/tx/stop", ADMIN, {})[0] == 202
    assert _req(server, "/tx", ADMIN)[1]["tx"]["active"] is False


def test_recording_and_transmitting_are_mutually_exclusive(server):
    _req(server, "/tx/acknowledge", ADMIN, {})
    _req(server, "/tx/start", ADMIN, {"waveform": "cw", "frequency_hz": 2450e6})
    status, body = _req(server, "/record", ADMIN, {})
    assert status == 409
    assert "transmitting" in body["error"]
    _req(server, "/tx/stop", ADMIN, {})


def test_every_transmission_lands_in_the_operations_log(server):
    """The audit trail is the point, not decoration."""
    _req(server, "/tx/acknowledge", ADMIN, {})
    _req(server, "/tx/start", ADMIN,
         {"waveform": "two_tone", "frequency_hz": 915e6, "duration_s": 0.4})
    time.sleep(1.2)
    _status, body = _req(server, "/operations", ADMIN)
    tx_ops = [o for o in body["operations"] if o["kind"] == "tx"]
    assert tx_ops, "no tx operation was recorded"
    latest = [o for o in tx_ops if "915" in o["summary"]]
    assert latest, [o["summary"] for o in tx_ops]
    assert "two_tone" in latest[-1]["summary"] or "Two tone" in latest[-1]["summary"]
