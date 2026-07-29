"""Device adapters, discovery, and selection.

Public surface:
  discover()               enumerate SoapySDR, return recognized radios
  resolve_device(selector) "auto" | profile name | "driver=...[,serial=...]"
                           → (profile_name, adapter). Configures nothing.
  get_adapter()/set_adapter()  the active adapter for this process
  make_source()            open a striqt source via the active adapter
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
    """Resolve a SoapyAIRT enumeration row to a known Deepwave model by
    scanning every value for a model string (AIR8201B / AIR-7201B / AIR 7101B
    …). Historical deployments identify only as SoapyAIRT → AIR8201B."""
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
        return Airstack1Source.from_spec(
            make_source_spec(self.name, source_config))


class Air8201BAdapter(DeepwaveAdapter):
    name = "air8201b"


class Air7101BAdapter(DeepwaveAdapter):
    name = "air7101b"


class Air7201BAdapter(DeepwaveAdapter):
    name = "air7201b"


class PlutoAdapter(DeviceAdapter):
    name = "pluto"

    def create_source(self, source_config=None):
        if PlutoSource is None:
            raise RuntimeError("striqt SoapySource unavailable — cannot drive a PlutoSDR")
        # from_spec() connects AND configures in one step on the installed
        # striqt; there is no separate setup() to call.
        return PlutoSource.from_spec(make_source_spec("pluto", source_config))


class GenericSoapyAdapter(DeviceAdapter):
    name = "soapy"

    def create_source(self, source_config=None):
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
    name = "demo"
    supports_readback = False

    def create_source(self, source_config=None):
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
    global _active_adapter
    _active_adapter = adapter


def get_adapter() -> DeviceAdapter:
    """The active adapter; lazily built from state.DEVICE when the frontend
    skipped explicit resolution (keeps old call sites working)."""
    global _active_adapter
    if _active_adapter is None or _active_adapter.name != state.DEVICE:
        _active_adapter = ADAPTER_CLASSES[state.DEVICE]()
    return _active_adapter


def make_source(source_config=None):
    return get_adapter().create_source(source_config)


def probe_channels(profile_name, adapter=None):
    """
    Best-effort RX channel discovery for a real device selected WITHOUT going
    through enumeration (e.g. --device air8201b). Briefly enumerates, matches
    the profile's driver family (and Deepwave model, when identifiable), and
    asks the one matching device for getNumChannels. Returns a port tuple or
    None (profile channels stay in force).
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
    """
    Enumerate SoapySDR devices. Returns a list of dicts:
      {"device": profile_name, "driver": str, "label": str, "serial": str|None,
       "info": {...}, "num_channels": int|None}
    Unrecognized drivers map to the generic "soapy" profile. Raises RuntimeError
    when SoapySDR itself is unavailable.
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
            "master_clock_rate": facts["master_clock_rate"],
        })
    return found


def _probe_master_clock(dev):
    """Largest master clock rate the driver admits to, or None.

    getMasterClockRates() may yield floats or SoapySDR Range objects depending
    on the driver, so both shapes are accepted. Falls back to whatever rate the
    device has already selected for itself, which is always legal.
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
    """Briefly open the device to ask what it actually is.

    Returns {"num_channels": int|None, "master_clock_rate": float|None}. Every
    field is best-effort: a busy device or a driver quirk yields None and the
    profile defaults stay in force.

    The master clock matters because striqt applies it verbatim
    (sources/soapy.py: setMasterClockRate) and LINDA used to hand every radio
    the AIR-T's 125 MHz, which a USRP B2xx rejects outright. Asking the driver
    is the only answer that generalises past the radios we have profiles for.
    """
    facts = {"num_channels": None, "master_clock_rate": None}
    try:
        from SoapySDR import SOAPY_SDR_RX as rx_dir
    except Exception:
        rx_dir = 1
    dev = None
    try:
        dev = SoapySDR.Device(info)
        try:
            facts["num_channels"] = int(dev.getNumChannels(rx_dir))
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
    """Parse "driver=plutosdr,serial=104473..." into a dict, else None."""
    if "=" not in selector:
        return None
    out = {}
    for part in selector.split(","):
        key, _, value = part.partition("=")
        if key.strip() and value.strip():
            out[key.strip()] = value.strip()
    return out or None


def resolve_device(selector: str):
    """
    Resolve a --device selector to (profile_name, adapter).

      "air8201b" | "pluto" | "demo" | "soapy"   explicit profile
      "auto"                                    enumerate; exactly one radio
      "driver=X[,serial=Y]"                     match one enumerated radio

    Exits with a clear device list when auto/selector matching is ambiguous,
    mirroring the old _resolve_auto_device behaviour.
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
