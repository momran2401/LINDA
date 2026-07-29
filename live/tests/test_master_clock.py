"""Master-clock selection per radio (fake devices — no hardware).

Regression cover for the bug that stopped a USRP B205mini from ever opening:
LINDA handed every radio the AIR-T's 125 MHz converter clock, striqt applied it
verbatim (sources/soapy.py: setMasterClockRate), and UHD refused with

    current master clock rate (125.000000 MHz) exceeds maximum possible
    master clock rate (61.440000 MHz)

The rule these tests pin down: a radio only ever receives a clock its own
profile declares, or one the device itself reported at enumeration.
"""
import pytest

from core import devices
from core.constants import DEVICE_PROFILES, SOAPY_FALLBACK_MASTER_CLOCK
from core.devices import sources


# --------------------------------------------------------------------------
# _probe_master_clock: drivers answer in more than one shape
# --------------------------------------------------------------------------
class _Range:
    """Stands in for a SoapySDR Range object."""
    def __init__(self, lo, hi):
        self._lo, self._hi = lo, hi

    def minimum(self):
        return self._lo

    def maximum(self):
        return self._hi


class _Dev:
    def __init__(self, rates=None, current=None, raises=False):
        self._rates, self._current, self._raises = rates, current, raises

    def getMasterClockRates(self):
        if self._raises or self._rates is None:
            raise RuntimeError("driver does not implement this")
        return self._rates

    def getMasterClockRate(self):
        if self._current is None:
            raise RuntimeError("driver does not implement this")
        return self._current


def test_probe_reads_a_range_list():
    # SoapyUHD reports the B2xx clock as a Range, not a float.
    assert devices._probe_master_clock(_Dev(rates=[_Range(5e6, 61.44e6)])) == 61.44e6


def test_probe_reads_a_plain_float_list():
    assert devices._probe_master_clock(_Dev(rates=[16e6, 61.44e6, 30.72e6])) == 61.44e6


def test_probe_falls_back_to_the_current_rate():
    # No rate list, but the device has already picked something legal.
    assert devices._probe_master_clock(_Dev(rates=None, current=16e6)) == 16e6


def test_probe_returns_none_when_the_driver_answers_nothing():
    # Must be None, not a guess: the caller then uses the documented fallback.
    assert devices._probe_master_clock(_Dev(raises=True)) is None
    assert devices._probe_master_clock(_Dev(rates=[])) is None


def test_probe_ignores_junk_entries():
    assert devices._probe_master_clock(_Dev(rates=["nonsense", 0, 61.44e6])) == 61.44e6


# --------------------------------------------------------------------------
# Which clock each radio actually receives
# --------------------------------------------------------------------------
class _SpecStub:
    """Captures the kwargs make_source_spec would hand striqt."""
    __struct_fields__ = ("master_clock_rate", "array_backend", "time_source",
                         "time_sync_at", "clock_source", "gapless",
                         "receive_retries")

    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture()
def spec_stub(monkeypatch):
    monkeypatch.setattr(sources, "Air8201BSourceSpec", _SpecStub)
    monkeypatch.setattr(sources, "SPEC_CLASSES",
                        {k: _SpecStub for k in ("air7101b", "air7201b", "air8201b")})
    return _SpecStub


@pytest.mark.parametrize("device, expected", [
    ("air8201b", 125e6),   # the AIR-T's own converter clock
    ("air7101b", 125e6),
    ("air7201b", 125e6),
    ("pluto",    61.44e6),  # AD936x, explicitly declared
])
def test_profiled_radios_get_their_own_clock(spec_stub, device, expected):
    spec = sources.make_source_spec(device)
    assert spec.kwargs["master_clock_rate"] == expected


def test_unprofiled_radio_never_inherits_the_air_t_clock(spec_stub):
    # The exact regression: "soapy" has no profile clock, and the fallback spec
    # class is the AIR-T's, so this used to come out as 125e6.
    spec = sources.make_source_spec("soapy")
    assert spec.kwargs["master_clock_rate"] == SOAPY_FALLBACK_MASTER_CLOCK
    assert spec.kwargs["master_clock_rate"] != 125e6
    assert spec.kwargs["master_clock_rate"] <= 61.44e6


def test_probed_clock_overrides_the_fallback(spec_stub):
    spec = sources.make_source_spec("soapy", {"master_clock_rate": 40e6})
    assert spec.kwargs["master_clock_rate"] == 40e6


def test_every_profile_declares_its_own_clock_or_is_generic():
    """No profile may rely on an inherited clock again."""
    for name, profile in DEVICE_PROFILES.items():
        if name in ("soapy", "demo"):
            continue  # generic/synthetic: the device or the fallback decides
        assert profile.get("master_clock_rate"), f"{name} declares no master clock"


def test_generic_adapter_injects_the_probed_clock(monkeypatch, spec_stub):
    """The rate the radio reported at enumeration reaches the source spec."""
    captured = {}

    class _Src:
        @classmethod
        def from_spec(cls, spec):
            captured.update(spec.kwargs)
            return cls()

    monkeypatch.setattr(devices, "GenericSoapySource", _Src)
    monkeypatch.setattr(devices, "generic_soapy_class", lambda driver: _Src)

    adapter = devices.GenericSoapyAdapter({"driver": "uhd"})
    adapter.info["_master_clock_rate"] = 61.44e6
    adapter.create_source()
    assert captured["master_clock_rate"] == 61.44e6

    # An explicit source-config value still wins over the probe.
    captured.clear()
    adapter.create_source({"master_clock_rate": 30.72e6})
    assert captured["master_clock_rate"] == 30.72e6
