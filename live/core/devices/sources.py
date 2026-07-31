"""striqt source classes for non-AIR-T SoapySDR radios (PlutoSDR + generic).

This is the low-level half of the `core/devices/` adapter layer: `__init__.py`
picks a profile and an adapter, and the adapter's `create_source()` calls into
here to actually build a striqt source object for that hardware.

`PlutoSource` is ported from `live/legacy/pluto_standalone.py` (P3-1);
`GenericSoapySource` extends the same trick to any SoapySDR driver string,
best-effort. Both subclass striqt's Deepwave `Airstack1Source` to reuse its
stream/arm/read machinery, and both reuse the `Air8201BSourceSpec` CLASS for
their spec shape — but never its VALUES: the assumption that "the Soapy
drivers ignore the AirStack master-clock/time-source fields they don't
implement" is false. SoapyUHD implements `setMasterClockRate`, and a USRP B2xx
rejects the AIR-T's 125 MHz outright, so the source never opened. Anything the
driver genuinely acts on has to be right for the radio in hand — see
`master_clock_rate` in `make_source_spec()` below. `striqt/` itself (the
vendored, read-only upstream tree) is never modified; these classes live
entirely in Linda's own code.
"""
from __future__ import annotations

from ..constants import DEVICE_PROFILES, SOAPY_FALLBACK_MASTER_CLOCK
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
    """Build the striqt source spec for `device`, ready to hand to `.from_spec()`.

    Starts from a set of safe defaults (see below), applies any
    applied-source-config `overrides` (the verified-reconnect path in
    `core/config.py`), then drops any override key the target spec class
    doesn't declare, so a stale or foreign key can never crash source
    construction.

    Args:
        device: Profile name (e.g. "air8201b", "pluto", "soapy"); selects
            which per-model striqt spec class to build and which profile's
            `master_clock_rate` to default to. Falls back to
            `Air8201BSourceSpec` when unrecognized.
        overrides: Optional dict of spec-field overrides, typically from an
            applied source-config reconnect.

    Returns:
        An instance of the resolved spec class (e.g. `Air8201BSourceSpec`),
        constructed with the merged, field-filtered options.
    """
    spec_cls = SPEC_CLASSES.get(device, Air8201BSourceSpec)
    # Every profile now declares its own master clock; only a radio with no
    # profile entry (the generic "soapy" catch-all) falls back. It must still be
    # set explicitly rather than left out: the fallback spec class here is the
    # AIR-T's, whose own default is 125 MHz, so omitting the option would
    # silently reintroduce the value that a USRP B2xx refuses to accept. The
    # adapter overrides this with the rate the device reported at enumeration.
    profile_clock = DEVICE_PROFILES.get(device, {}).get("master_clock_rate")
    options = {
        "master_clock_rate": profile_clock or SOAPY_FALLBACK_MASTER_CLOCK,
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
    """Best-effort SoapySDR hardware key for a source, with a stable fallback.

    Args:
        source: A striqt source instance; its underlying SoapySDR device
            object is looked up via `_device` or `device`.
        fallback: Value to return if the device is missing or
            `getHardwareKey()` fails or returns a falsy value.

    Returns:
        str: The device's hardware key, or `fallback`.
    """
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
            """Connect via striqt's `_SoapySource`, forcing this class's
            `_soapy_driver` instead of the AIR-T's "SoapyAIRT"."""
            _SoapySource._connect(self, spec, driver=self._soapy_driver, **kwargs)

        @property
        def id(self):
            """str: Stable source identifier, read by striqt v0.7.0 as a
            cached_property.

            Neither inherited path works here: `Airstack1Source` resolves both
            `id` and `get_id()` from the Jetson eth0 MAC, which a Pluto/SoapySDR
            host does not have, and upstream `SoapySource.id` has a
            `raise`/`return` typo. `run_sweep` looks the source ID up, so
            without this override a recording dies at startup.
            """
            return _hardware_key(self, self._soapy_driver)

        def get_id(self):
            """str: Same as `id`, for newer striqt builds that call `get_id()`
            instead of reading the `id` cached_property."""
            return _hardware_key(self, self._soapy_driver)

        def read_peripherals(self):
            """dict: Empty — the AirStack transceiver temperature sensor this
            hooks in Deepwave sources doesn't exist on non-AIR-T radios."""
            return {}

    class PlutoSource(_NonAirstackSoapySource):
        """PlutoSDR source (ported from live/legacy/pluto_standalone.py, P3-1)."""

        _soapy_driver = "plutosdr"

    class GenericSoapySource(_NonAirstackSoapySource):
        """Best-effort source for any other SoapySDR radio.

        Same shape as `PlutoSource` but with `_soapy_driver` chosen from
        enumeration via `generic_soapy_class()` rather than hardcoded. Works
        wherever the driver tolerates the AirStack spec fields it doesn't
        implement.
        """

    def generic_soapy_class(driver):
        """Build a `GenericSoapySource` subclass bound to a specific driver.

        v0.7.0 builds sources through `.from_spec()`, which forwards no device
        kwargs, so the driver string has to live on the class rather than
        being passed at construction time.

        Args:
            driver: The SoapySDR driver string (e.g. "uhd", "rtlsdr").

        Returns:
            type: A new `GenericSoapySource` subclass with `_soapy_driver` set
            to `driver`.
        """
        safe = "".join(ch if ch.isalnum() else "_" for ch in str(driver))
        return type(f"GenericSoapySource_{safe}", (GenericSoapySource,),
                    {"_soapy_driver": str(driver)})
else:
    PlutoSource = None
    GenericSoapySource = None

    def generic_soapy_class(driver):
        """Fallback used when striqt's SoapySource base isn't importable.

        Args:
            driver: Unused; present to match the real `generic_soapy_class()`
                signature.

        Raises:
            RuntimeError: Always — no SoapySDR-backed source class can be
                built in this environment.
        """
        raise RuntimeError(
            "striqt SoapySource unavailable — cannot drive a SoapySDR device")
