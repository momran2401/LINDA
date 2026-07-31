"""Device discovery and the adapter registry for every radio Linda can drive.

This is the top of the `core/devices/` adapter layer described in the repo's
architecture notes: it maps a `--device` selector (or live SoapySDR
enumeration) to one `DeviceAdapter` subclass (see `base.py`) per supported
radio family, and hands frontends a single `make_source()` entry point that
hides which family is actually plugged in. Deepwave AIR-T models are the
primary target; PlutoSDR and generic SoapySDR devices are supported via
`sources.py`; `demo` provides a hardware-free adapter for the synthetic
pipeline.

Public surface:
  discover()               Enumerate SoapySDR, return recognized radios.
  resolve_device(selector) "auto" | profile name | "driver=...[,serial=...]"
                           -> (profile_name, adapter). Configures nothing.
  get_adapter()/set_adapter()  The active adapter for this process.
  make_source()            Open a striqt source via the active adapter.
"""
from __future__ import annotations

import sys

from .. import state
from ..striqt_compat import Airstack1Source
from .base import DeviceAdapter
from .sources import (
    GenericSoapySource, PlutoSource, generic_soapy_class, make_source_spec,
)

# SoapySDR driver string → profile name. SoapyAIRT rows are refined to the
# actual Deepwave model via identify_deepwave(); anything else enumerable
# falls back to the generic "soapy" adapter (best-effort).
DRIVER_TO_DEVICE = {"plutosdr": "pluto"}

DEEPWAVE_MODELS = ("air7101b", "air7201b", "air8201b")


def identify_deepwave(info):
    """Resolve a SoapyAIRT enumeration row to a specific Deepwave model.

    Scans every value in the enumeration dict for a model string (AIR8201B /
    AIR-7201B / AIR 7101B / ...), ignoring punctuation and case. Historical
    deployments identify only as generic "SoapyAIRT" with no model string
    anywhere in the row, so those fall back to AIR8201B.

    Args:
        info: SoapySDR enumeration dict (or mapping-like) for one device row.

    Returns:
        str: One of `DEEPWAVE_MODELS`, defaulting to "air8201b" when no model
        string is found.
    """
    text = " ".join(str(v) for v in dict(info or {}).values()).lower()
    compact = "".join(ch for ch in text if ch.isalnum())
    for model in DEEPWAVE_MODELS:
        if model in compact:
            return model
    return "air8201b"


class DeepwaveAdapter(DeviceAdapter):
    """Deepwave AIR-T family (SoapyAIRT driver). Subclasses pin the model."""
    name = "air8201b"

    def create_source(self, source_config=None):
        """Open a striqt Airstack1 source for this Deepwave model.

        Args:
            source_config: Optional source-spec field overrides (e.g. from a
                verified reconnect). Passed through to `make_source_spec()`.

        Returns:
            An opened `Airstack1Source` instance.
        """
        return Airstack1Source.from_spec(
            make_source_spec(self.name, source_config))


class Air8201BAdapter(DeepwaveAdapter):
    """Deepwave AIR8201B adapter."""
    name = "air8201b"


class Air7101BAdapter(DeepwaveAdapter):
    """Deepwave AIR-7101B adapter."""
    name = "air7101b"


class Air7201BAdapter(DeepwaveAdapter):
    """Deepwave AIR-7201B adapter."""
    name = "air7201b"


class PlutoAdapter(DeviceAdapter):
    """Analog Devices PlutoSDR adapter (SoapySDR "plutosdr" driver)."""
    name = "pluto"

    def create_source(self, source_config=None):
        """Open a striqt source for a PlutoSDR.

        Args:
            source_config: Optional source-spec field overrides.

        Returns:
            An opened `PlutoSource` instance.

        Raises:
            RuntimeError: If striqt's SoapySource base is unavailable, so no
                Pluto-capable source class exists to construct.
        """
        if PlutoSource is None:
            raise RuntimeError("striqt SoapySource unavailable — cannot drive a PlutoSDR")
        # from_spec() connects AND configures in one step on the installed
        # striqt; there is no separate setup() to call.
        return PlutoSource.from_spec(make_source_spec("pluto", source_config))


class GenericSoapyAdapter(DeviceAdapter):
    """Best-effort adapter for any other SoapySDR-enumerable radio."""
    name = "soapy"

    def create_source(self, source_config=None):
        """Open a striqt source for a generic (non-Deepwave, non-Pluto) radio.

        Args:
            source_config: Optional source-spec field overrides; an explicit
                `master_clock_rate` here wins over the enumeration-probed
                value below.

        Returns:
            An opened source instance from a `driver`-bound
            `GenericSoapySource` subclass.

        Raises:
            RuntimeError: If striqt's SoapySource base is unavailable, or if
                this adapter was built without a driver string (i.e. not via
                `--device auto` enumeration).
        """
        if GenericSoapySource is None:
            raise RuntimeError("striqt SoapySource unavailable — cannot drive a SoapySDR device")
        driver = self.info.get("driver")
        if not driver:
            raise RuntimeError("generic soapy adapter needs a driver string "
                               "(select the device via --device auto)")
        # Hand the spec the clock this radio told us about at enumeration.
        # An explicit source-config override still wins.
        overrides = dict(source_config or {})
        probed = self.info.get("_master_clock_rate")
        if probed and not overrides.get("master_clock_rate"):
            overrides["master_clock_rate"] = float(probed)
        return generic_soapy_class(driver).from_spec(
            make_source_spec("soapy", overrides))


class DemoAdapter(DeviceAdapter):
    """Hardware-free adapter backing the synthetic `--demo` pipeline.

    Never opens a real source; `DemoAcquirer` (in `core/acquisition.py`)
    synthesizes IQ instead. `supports_readback = False` so capability
    reporting and verification honestly show "readback_unsupported" rather
    than faking driver agreement.
    """
    name = "demo"
    supports_readback = False

    def create_source(self, source_config=None):
        """Always raise: the demo device has no real striqt source to open.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("demo device has no hardware source")


ADAPTER_CLASSES = {
    "air8201b": Air8201BAdapter,
    "air7101b": Air7101BAdapter,
    "air7201b": Air7201BAdapter,
    "pluto":    PlutoAdapter,
    "soapy":    GenericSoapyAdapter,
    "demo":     DemoAdapter,
}

_active_adapter = None


def set_adapter(adapter: DeviceAdapter):
    """Install `adapter` as the active adapter for this process.

    Args:
        adapter: The `DeviceAdapter` instance to make active.
    """
    global _active_adapter
    _active_adapter = adapter


def get_adapter() -> DeviceAdapter:
    """Return the active adapter, building it lazily if needed.

    If no adapter was explicitly set via `set_adapter()`, or the cached one no
    longer matches `state.DEVICE`, a fresh instance of the registered class is
    built from `state.DEVICE`. This keeps older call sites that never called
    `resolve_device()`/`set_adapter()` explicitly working.

    Returns:
        DeviceAdapter: The current process-wide active adapter.
    """
    global _active_adapter
    if _active_adapter is None or _active_adapter.name != state.DEVICE:
        _active_adapter = ADAPTER_CLASSES[state.DEVICE]()
    return _active_adapter


def make_source(source_config=None):
    """Open a striqt source via the currently active adapter.

    Args:
        source_config: Optional source-spec field overrides, forwarded to the
            adapter's `create_source()`.

    Returns:
        The opened striqt source instance.
    """
    return get_adapter().create_source(source_config)


def probe_channels(profile_name, adapter=None):
    """Best-effort RX channel discovery for an explicitly-selected device.

    Used when a real device was selected WITHOUT going through enumeration
    (e.g. `--device air8201b` rather than `--device auto`). Briefly
    enumerates, matches the profile's driver family (and Deepwave model, when
    identifiable), and asks the one matching device for `getNumChannels`.

    Args:
        profile_name: The resolved profile name (e.g. "air8201b", "pluto").
        adapter: The adapter instance for this profile, if already resolved;
            if it already carries a probed `_num_channels`, enumeration is
            skipped entirely.

    Returns:
        tuple[int, ...] | None: The discovered RX port indices, or None if
        discovery was inconclusive (the profile's default channels stay in
        force).
    """
    if profile_name in ("demo",):
        return None
    if adapter is not None and adapter.info.get("_num_channels"):
        return tuple(range(int(adapter.info["_num_channels"])))
    try:
        found = discover()
    except RuntimeError:
        return None
    if profile_name in DEEPWAVE_MODELS:
        matches = [f for f in found if f["driver"] == "SoapyAIRT"
                   and f["device"] == profile_name]
        # An anonymous SoapyAIRT row identifies as air8201b; accept it for any
        # requested Deepwave model when it is the only AIR-T present.
        if not matches:
            airt = [f for f in found if f["driver"] == "SoapyAIRT"]
            matches = airt if len(airt) == 1 else []
    else:
        matches = [f for f in found if f["device"] == profile_name]
    if len(matches) == 1 and matches[0]["num_channels"]:
        ports = tuple(range(int(matches[0]["num_channels"])))
        print(f"[device] discovered RX channels {ports}")
        return ports
    return None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover():
    """Enumerate every SoapySDR-visible device and classify it into a profile.

    SoapyAIRT rows are refined to a specific Deepwave model via
    `identify_deepwave()`; any other recognized driver maps through
    `DRIVER_TO_DEVICE`; anything else falls back to the generic "soapy"
    profile. Each matching device is also briefly opened (`_probe_device_facts`)
    to learn its real RX/TX channel counts and master clock rate.

    Returns:
        list[dict]: One dict per enumerated device:
            {"device": profile_name, "driver": str, "label": str,
             "serial": str | None, "info": dict, "num_channels": int | None,
             "num_tx_channels": int | None, "master_clock_rate": float | None}.

    Raises:
        RuntimeError: If the SoapySDR module cannot be imported, or if
            `SoapySDR.Device.enumerate()` itself fails.
    """
    try:
        import SoapySDR
    except Exception as e:
        raise RuntimeError(f"SoapySDR unavailable: {e}")
    try:
        results = SoapySDR.Device.enumerate()
    except Exception as e:
        raise RuntimeError(f"SoapySDR enumeration failed: {e}")
    found = []
    for r in results:
        try:
            info = dict(r)
        except Exception:
            info = {}
        driver = str(info.get("driver", ""))
        if not driver:
            continue
        if driver == "SoapyAIRT":
            device = identify_deepwave(info)
        else:
            device = DRIVER_TO_DEVICE.get(driver, "soapy")
        facts = _probe_device_facts(SoapySDR, info)
        found.append({
            "device":       device,
            "driver":       driver,
            "label":        info.get("label") or driver,
            "serial":       info.get("serial"),
            "info":         info,
            "num_channels": facts["num_channels"],
            "num_tx_channels": facts["num_tx_channels"],
            "master_clock_rate": facts["master_clock_rate"],
        })
    return found


def _probe_master_clock(dev):
    """Find the largest master clock rate an open SoapySDR device admits to.

    `getMasterClockRates()` may yield plain floats or SoapySDR Range objects
    depending on the driver, so both shapes are accepted. Falls back to
    whatever rate the device has already selected for itself, which is always
    legal for that device.

    Args:
        dev: An already-opened `SoapySDR.Device` instance.

    Returns:
        float | None: The largest admissible master clock rate in Hz, or None
        if the driver offers nothing usable.
    """
    best = None
    try:
        rates = list(dev.getMasterClockRates())
    except Exception:
        rates = []
    for entry in rates:
        try:
            value = float(entry.maximum()) if hasattr(entry, "maximum") else float(entry)
        except Exception:
            continue
        if value > 0 and (best is None or value > best):
            best = value
    if best is None:
        try:
            current = float(dev.getMasterClockRate())
            best = current if current > 0 else None
        except Exception:
            best = None
    return best


def _probe_device_facts(SoapySDR, info):
    """Briefly open one enumerated device to ask what it actually is.

    Opens and immediately closes (`SoapySDR.Device.unmake`) the device to read
    its real RX/TX channel counts and its master clock rate, before any
    profile-based defaults are trusted. Every field is best-effort: a busy
    device or a driver quirk yields None for that field and the caller's
    profile defaults stay in force.

    The master clock matters because striqt applies it verbatim
    (`sources/soapy.py`: `setMasterClockRate`), and LINDA used to hand every
    radio the AIR-T's 125 MHz (`MASTER_CLOCK_RATE`), which a USRP B2xx rejects
    outright and refuses to open at all. Asking the driver here is the only
    answer that generalizes past the radios Linda has dedicated profiles for.
    The TX channel count is probed here too so the UI can decide, before any
    source is even opened, whether to offer transmit at all — an RTL-SDR must
    never be shown a TX button that only fails once clicked.

    Args:
        SoapySDR: The imported SoapySDR module (passed in so callers only
            import it once).
        info: The SoapySDR enumeration dict for this device.

    Returns:
        dict: {"num_channels": int | None, "num_tx_channels": int | None,
        "master_clock_rate": float | None}.
    """
    facts = {"num_channels": None, "num_tx_channels": None,
             "master_clock_rate": None}
    try:
        from SoapySDR import SOAPY_SDR_RX as rx_dir
    except Exception:
        rx_dir = 1
    try:
        from SoapySDR import SOAPY_SDR_TX as tx_dir
    except Exception:
        tx_dir = 0
    dev = None
    try:
        dev = SoapySDR.Device(info)
        try:
            facts["num_channels"] = int(dev.getNumChannels(rx_dir))
        except Exception:
            pass
        try:
            facts["num_tx_channels"] = int(dev.getNumChannels(tx_dir))
        except Exception:
            pass
        facts["master_clock_rate"] = _probe_master_clock(dev)
    except Exception:
        pass
    finally:
        try:
            if dev is not None:
                SoapySDR.Device.unmake(dev)
        except Exception:
            pass
    return facts


def _parse_selector(selector: str):
    """Parse a "key=value[,key=value...]" selector string into a dict.

    Args:
        selector: e.g. "driver=plutosdr,serial=104473...".

    Returns:
        dict[str, str] | None: The parsed key/value pairs, or None if
        `selector` contains no "=" (i.e. it isn't this kind of selector) or no
        valid pair was found.
    """
    if "=" not in selector:
        return None
    out = {}
    for part in selector.split(","):
        key, _, value = part.partition("=")
        if key.strip() and value.strip():
            out[key.strip()] = value.strip()
    return out or None


def resolve_device(selector: str):
    """Resolve a `--device` selector to a `(profile_name, adapter)` pair.

    Accepted forms:
      "air8201b" | "air7201b" | "air7101b" | "pluto" | "soapy" | "demo"
          Explicit profile — no enumeration needed.
      "auto"
          Enumerate; succeeds only if exactly one radio is found.
      "driver=X[,serial=Y]"
          Enumerate and match against exactly one radio.

    Configures nothing on the device itself — this only decides which
    adapter class governs it and seeds any enumeration-probed facts
    (`_num_channels`, `_num_tx_channels`, `_master_clock_rate`) onto it.

    Args:
        selector: The raw `--device` value.

    Returns:
        tuple[str, DeviceAdapter]: The resolved profile name and a
        constructed adapter instance for it.

    Raises:
        SystemExit: Via `sys.exit(1)`, after printing a diagnostic to stderr,
            when SoapySDR is unavailable, or when enumeration matches zero or
            more than one radio (mirroring the old `_resolve_auto_device`
            behavior of failing loudly with a device list rather than
            guessing).
    """
    selector = str(selector).strip()
    if selector in ADAPTER_CLASSES:
        return selector, ADAPTER_CLASSES[selector]()

    wanted = _parse_selector(selector)
    try:
        found = discover()
    except RuntimeError as e:
        print(f"ERROR: --device {selector} needs SoapySDR ({e})", file=sys.stderr)
        sys.exit(1)

    if wanted:
        matches = [
            f for f in found
            if all(str(f["info"].get(k, "")) == v for k, v in wanted.items())
        ]
    else:  # "auto"
        matches = found

    if len(matches) == 1:
        m = matches[0]
        adapter = ADAPTER_CLASSES[m["device"]](m["info"])
        if m["num_channels"]:
            adapter.info["_num_channels"] = int(m["num_channels"])
        if m.get("num_tx_channels") is not None:
            adapter.info["_num_tx_channels"] = int(m["num_tx_channels"])
        if m.get("master_clock_rate"):
            adapter.info["_master_clock_rate"] = float(m["master_clock_rate"])
        print(f"[device] selected {m['device']} ({m['label']}"
              + (f", serial {m['serial']}" if m["serial"] else "") + ")")
        return m["device"], adapter

    print(
        f"ERROR: --device {selector} matched {len(matches)} radios "
        f"(need exactly 1). Enumeration:",
        file=sys.stderr,
    )
    for f in found:
        sel = f"driver={f['driver']}" + (f",serial={f['serial']}" if f["serial"] else "")
        print(f"  {f['device']:9s} {f['label']}  →  --device {sel}", file=sys.stderr)
    print("  Or pick a profile explicitly: --device air8201b | air7201b | "
          "air7101b | pluto | soapy | demo", file=sys.stderr)
    sys.exit(1)
