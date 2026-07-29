"""GPS position for recordings: a gpsd client and the striqt peripheral.

The radio hosts run gpsd (localhost:2947). This module speaks gpsd's JSON
protocol over a plain socket — the Python `gps` module is NOT installed in the
radio's pixi environment, and a stdlib client is one less thing to deploy.

Two pieces:

  * ``GpsReader`` — background thread holding the latest fix, with reconnect
    and staleness tracking. Never raises into its caller; a dead or absent GPS
    degrades to "no fix", never to a stalled recording.
  * ``gps_peripherals_class()`` — builds the striqt ``Peripherals`` subclass
    the recording sweep uses in place of ``NoPeripherals``. striqt merges what
    ``acquire()`` returns into each capture's ``extra_data``, which becomes
    per-capture variables in the archived xarray Dataset.

Honesty rule, enforced here rather than left to the reader of the data: with
no fix the coordinates are NaN and ``gps_valid`` is 0 — never 0.0/0.0, which
would silently place every measurement off the coast of Africa.
"""
from __future__ import annotations

import json
import math
import os
import socket
import threading
import time

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2947
#: A fix older than this is reported but flagged stale (a receiver that stops
#: updating must not keep decorating captures with an old position).
DEFAULT_STALE_AFTER_S = 15.0
_WATCH = b'?WATCH={"enable":true,"json":true};\n'

#: Variables added to every capture. Order is the documentation.
CAPTURE_FIELDS = (
    "gps_latitude_deg",
    "gps_longitude_deg",
    "gps_altitude_m",
    "gps_fix_mode",         # gpsd TPV mode: 0 unknown, 1 none, 2 2-D, 3 3-D
    "gps_satellites_used",
    "gps_time_unix",
    "gps_fix_age_s",
    "gps_error_horizontal_m",
    "gps_error_vertical_m",
    "gps_valid",            # 1 only for a fresh 2-D/3-D fix
)


def _f(value):
    """float() that maps missing/garbage to NaN instead of raising."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return math.nan
    return out if math.isfinite(out) else math.nan


def _parse_gps_time(value):
    """gpsd ISO-8601 UTC timestamp -> unix seconds (NaN when absent)."""
    if not isinstance(value, str) or not value:
        return math.nan
    text = value.replace("Z", "+00:00")
    try:
        from datetime import datetime
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return math.nan


class GpsReader(threading.Thread):
    """Background gpsd client holding the most recent fix.

    Connection failures are expected and non-fatal: an unbound gpsd (no
    receiver attached), a stopped daemon, or a host without GPS all resolve to
    snapshots with ``valid=False`` and a human-readable ``error``.
    """

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT,
                 stale_after=DEFAULT_STALE_AFTER_S):
        super().__init__(daemon=True, name="gps-reader")
        self.host = str(host)
        self.port = int(port)
        self.stale_after = float(stale_after)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._tpv = {}            # last TPV message
        self._tpv_at = 0.0        # monotonic time it arrived
        self._sats = None         # satellites used, from SKY
        self._device = None
        self._connected = False
        # None means "nothing has gone wrong yet" — during the first moments
        # that reads as connecting, not as a failure. Only a real error or a
        # deviceless gpsd fills this in.
        self._error = None

    # --- lifecycle ---

    def stop(self):
        self._stop.set()

    def run(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._session()
                backoff = 1.0
            except Exception as exc:
                with self._lock:
                    self._connected = False
                    self._error = f"{type(exc).__name__}: {exc}"
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 10.0)

    def _session(self):
        with socket.create_connection((self.host, self.port), timeout=5.0) as sock:
            sock.sendall(_WATCH)
            sock.settimeout(1.0)
            with self._lock:
                self._connected = True
                self._error = None
            buf = b""
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue          # quiet link is normal between fixes
                if not chunk:
                    raise ConnectionError("gpsd closed the connection")
                buf += chunk
                if len(buf) > 1 << 20:
                    buf = b""         # desync guard; never grow without bound
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._consume(line.strip())
        with self._lock:
            self._connected = False

    def _consume(self, line):
        if not line:
            return
        try:
            msg = json.loads(line)
        except Exception:
            return
        cls = msg.get("class")
        if cls == "TPV":
            with self._lock:
                self._tpv = msg
                self._tpv_at = time.monotonic()
                self._device = msg.get("device") or self._device
        elif cls == "SKY":
            # uSat is the count actually used in the solution; fall back to
            # counting flagged satellites on older gpsd builds.
            used = msg.get("uSat")
            if used is None:
                sats = msg.get("satellites")
                if isinstance(sats, list):
                    used = sum(1 for s in sats if isinstance(s, dict)
                               and s.get("used"))
            with self._lock:
                if used is not None:
                    self._sats = int(used)
        elif cls == "DEVICES":
            devices = msg.get("devices") or []
            with self._lock:
                if not devices:
                    # gpsd is up but has no receiver attached — the single most
                    # likely misconfiguration, so name it precisely.
                    self._error = ("gpsd has no device attached "
                                   "(gpsdctl add /dev/ttyACM0)")
                else:
                    self._device = devices[0].get("path") or self._device
                    self._error = None

    # --- reading ---

    def snapshot(self):
        """Current GPS state. Pure data; safe to call from any thread."""
        with self._lock:
            tpv, at = dict(self._tpv), self._tpv_at
            sats, device = self._sats, self._device
            connected, error = self._connected, self._error
        age = (time.monotonic() - at) if at else math.inf
        mode = int(tpv.get("mode") or 0)
        # gpsd < 3.20 reports `alt`; newer builds split it into altMSL/altHAE.
        altitude = math.nan
        for key in ("altMSL", "alt", "altHAE"):
            if key in tpv:
                altitude = _f(tpv[key])
                if math.isfinite(altitude):
                    break
        fresh = age <= self.stale_after
        latitude, longitude = _f(tpv.get("lat")), _f(tpv.get("lon"))
        valid = bool(mode >= 2 and fresh
                     and math.isfinite(latitude) and math.isfinite(longitude))
        return {
            "connected":     bool(connected),
            "device":        device,
            "error":         error,
            "mode":          mode,
            "valid":         valid,
            "stale":         bool(at and not fresh),
            "latitude":      latitude,
            "longitude":     longitude,
            "altitude_m":    altitude,
            "time_unix":     _parse_gps_time(tpv.get("time")),
            "satellites_used": sats,
            "error_horizontal_m": _f(tpv.get("eph")),
            "error_vertical_m":   _f(tpv.get("epv")),
            "age_s":         (round(age, 3) if math.isfinite(age) else None),
        }

    def capture_fields(self):
        """The per-capture variable dict striqt merges into each capture.

        Every value is a float so xarray/zarr store them without object
        dtypes. Missing values are NaN — never zero, which would read as a
        real position at 0°N 0°E.
        """
        snap = self.snapshot()
        valid = snap["valid"]
        sats = snap["satellites_used"]
        age = snap["age_s"]
        return {
            "gps_latitude_deg":  float(snap["latitude"]) if valid else math.nan,
            "gps_longitude_deg": float(snap["longitude"]) if valid else math.nan,
            # Altitude needs a 3-D fix; a 2-D fix's altitude is meaningless.
            "gps_altitude_m":    float(snap["altitude_m"]) if (valid and snap["mode"] >= 3) else math.nan,
            "gps_fix_mode":      float(snap["mode"]),
            "gps_satellites_used": float(sats) if sats is not None else math.nan,
            "gps_time_unix":     float(snap["time_unix"]),
            "gps_fix_age_s":     float(age) if age is not None else math.nan,
            "gps_error_horizontal_m": float(snap["error_horizontal_m"]),
            "gps_error_vertical_m":   float(snap["error_vertical_m"]),
            "gps_valid":         1.0 if valid else 0.0,
        }

    @staticmethod
    def absent_fields():
        """capture_fields() for a run with GPS disabled or unavailable."""
        fields = {name: math.nan for name in CAPTURE_FIELDS}
        fields["gps_fix_mode"] = 0.0
        fields["gps_valid"] = 0.0
        return fields


# ---------------------------------------------------------------------------
# Process-wide reader (the recorder and the status endpoint share one)
# ---------------------------------------------------------------------------

_reader = None
_reader_lock = threading.Lock()


def gps_enabled():
    """False when RADIO_GPS=0/off/false disables the integration entirely."""
    return str(os.environ.get("RADIO_GPS", "1")).strip().lower() not in (
        "0", "off", "false", "no")


def get_reader(start=True):
    """The shared GpsReader, started on first use. None when disabled."""
    global _reader
    if not gps_enabled():
        return None
    with _reader_lock:
        if _reader is None:
            _reader = GpsReader(
                host=os.environ.get("RADIO_GPS_HOST", DEFAULT_HOST),
                port=int(os.environ.get("RADIO_GPS_PORT", DEFAULT_PORT)),
            )
            if start:
                _reader.start()
        return _reader


def status():
    """GPS status for /health and the Record tab."""
    if not gps_enabled():
        return {"enabled": False, "connected": False, "valid": False,
                "error": "disabled by RADIO_GPS"}
    reader = get_reader()
    snap = reader.snapshot()
    snap["enabled"] = True
    return snap


# ---------------------------------------------------------------------------
# striqt peripheral
# ---------------------------------------------------------------------------

def gps_peripherals_class():
    """Build the striqt Peripherals subclass that stamps captures with GPS.

    Returns None when striqt is unavailable, so callers fall back to
    NoPeripherals. Imported lazily: core/ must stay importable without striqt.
    """
    try:
        from striqt.sensor.lib import peripherals as _peripherals
    except Exception:
        return None

    class GpsPeripherals(_peripherals.PeripheralsBase):
        """Adds the current GPS fix to every capture in the sweep.

        striqt merges acquire()'s dict into the capture's extra_data, which
        becomes per-capture variables in the archived Dataset. acquire() only
        reads a cached snapshot — a wedged receiver can never slow a sweep.
        """

        def open(self):
            self._reader = get_reader()

        def close(self):
            return   # the reader is process-wide; other users may still need it

        def setup(self, captures, loops):
            return

        def arm(self, capture):
            return

        def acquire(self, capture):
            reader = getattr(self, "_reader", None)
            if reader is None:
                return GpsReader.absent_fields()
            try:
                return reader.capture_fields()
            except Exception:
                # A recording must never die over metadata.
                return GpsReader.absent_fields()

    return GpsPeripherals
