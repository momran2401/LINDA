"""striqt source classes for non-AIR-T SoapySDR radios.

PlutoSource is ported from live/legacy/pluto_standalone.py (P3-1); GenericSoapySource
extends the same trick to any SoapySDR driver string, best-effort. Both reuse
the Air8201BSourceSpec values — the Soapy drivers ignore the AirStack
master-clock/time-source fields they don't implement. striqt/ itself is never
modified.
"""
from __future__ import annotations

from ..constants import DEVICE_PROFILES, MASTER_CLOCK_RATE
from ..striqt_compat import (
    Air7101BSourceSpec, Air7201BSourceSpec, Air8201BSourceSpec,
    Airstack1Source, _SENSOR_OK, _SoapySource,
)

# Per-model striqt spec classes (striqt_compat falls back to Air8201BSourceSpec
# when the installed build doesn't ship a model's own class).
SPEC_CLASSES = {
    "air7101b": Air7101BSourceSpec,
    "air7201b": Air7201BSourceSpec,
    "air8201b": Air8201BSourceSpec,
}


def make_source_spec(device=None, overrides=None):
    """
    Build the striqt source spec for `device` (profile name), applying any
    applied-source-config `overrides` (the verified-reconnect path). Unknown
    override keys are dropped against the spec class's declared fields so a
    stale/foreign key can never crash source construction.
    """
    spec_cls = SPEC_CLASSES.get(device, Air8201BSourceSpec)
    profile_clock = DEVICE_PROFILES.get(device, {}).get("master_clock_rate")
    options = {
        "master_clock_rate": profile_clock or MASTER_CLOCK_RATE,
        "array_backend": "numpy",
        "time_source": "host",
        "time_sync_at": "open",
        "clock_source": "internal",
        "gapless": True,
        "receive_retries": 0,
    }
    options.update(dict(overrides or {}))
    fields = set(getattr(spec_cls, "__struct_fields__", ()) or ())
    if fields:
        options = {k: v for k, v in options.items() if k in fields}
    return spec_cls(**options)


def _hardware_key(source, fallback):
    """Best-effort SoapySDR hardware key, with a stable fallback."""
    dev = getattr(source, "_device", getattr(source, "device", None))
    try:
        key = dev.getHardwareKey()
    except Exception:
        key = None
    return str(key) if key else fallback


if _SENSOR_OK and _SoapySource is not None:
    class _NonAirstackSoapySource(Airstack1Source):
        """
        Shared base for non-Deepwave SoapySDR radios (P3-1).

        Subclasses Airstack1Source to reuse striqt's stream/arm/read machinery
        while replacing the two things in it that are AIR-T specific and fatal
        elsewhere:
          1. driver='SoapyAIRT'        -- replaced with `_soapy_driver`
          2. _set_jesd_sysref_delay()  -- AIR-T FPGA register write, absent here

        Construct these with ``.from_spec(spec)``, the way the Deepwave adapter
        does. The installed striqt (v0.7.0) supplies SoapySDR device kwargs ONLY
        through _connect(); its SourceBase.__init__ signature is
        ``(reuse_iq=False, **spec_fields)``, so passing a spec positionally binds
        it to `reuse_iq` and construction fails before the radio is touched.
        """

        #: Driver string handed to SoapySDR; bound per subclass.
        _soapy_driver = "soapy"

        def _connect(self, spec, **kwargs):
            _SoapySource._connect(self, spec, driver=self._soapy_driver, **kwargs)

        # striqt v0.7.0 reads `source.id` (a cached_property); newer builds call
        # get_id(). Neither inherited path works here: Airstack1Source resolves
        # both from the Jetson eth0 MAC, which a Pluto host does not have, and
        # upstream SoapySource.id has a `raise`/`return` typo. run_sweep looks
        # the source ID up, so without this a recording dies at startup.
        @property
        def id(self):
            return _hardware_key(self, self._soapy_driver)

        def get_id(self):
            return _hardware_key(self, self._soapy_driver)

        def read_peripherals(self):
            # AirStack-only transceiver temperature sensor.
            return {}

    class PlutoSource(_NonAirstackSoapySource):
        """PlutoSDR adapter (ported from live/legacy/pluto_standalone.py, P3-1)."""

        _soapy_driver = "plutosdr"

    class GenericSoapySource(_NonAirstackSoapySource):
        """
        Best-effort adapter for any other SoapySDR radio: same shape as
        PlutoSource but with the driver string chosen from enumeration. Works
        wherever the driver tolerates the AirStack spec fields it doesn't
        implement.
        """

    def generic_soapy_class(driver):
        """A GenericSoapySource subclass bound to `driver`.

        v0.7.0 builds sources through .from_spec(), which forwards no device
        kwargs, so the driver string has to live on the class.
        """
        safe = "".join(ch if ch.isalnum() else "_" for ch in str(driver))
        return type(f"GenericSoapySource_{safe}", (GenericSoapySource,),
                    {"_soapy_driver": str(driver)})
else:
    PlutoSource = None
    GenericSoapySource = None

    def generic_soapy_class(driver):
        raise RuntimeError(
            "striqt SoapySource unavailable — cannot drive a SoapySDR device")
