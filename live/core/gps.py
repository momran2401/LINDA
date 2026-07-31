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
    """Coerce a value to float, mapping missing/non-finite input to NaN.

    Args:
        value: Any value; typically a gpsd JSON field that may be absent,
            None, or a non-numeric type.

    Returns:
        `float(value)`, or `math.nan` if that raises or the result isn't
        finite (inf/NaN in, NaN out).
    """
    try:
        out = float(value)
    except (TypeError, ValueError):
        return math.nan
    return out if math.isfinite(out) else math.nan


def _parse_gps_time(value):
    """Parse a gpsd ISO-8601 UTC timestamp into Unix seconds.

    Args:
        value: The TPV message's "time" field, e.g. "2026-07-31T12:00:00.000Z".

    Returns:
        Seconds since the Unix epoch, or `math.nan` if `value` is absent,
        empty, or not a valid ISO-8601 string.
    """
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
        """Args:
            host: gpsd host to connect to.
            port: gpsd port to connect to.
            stale_after: Seconds after which a held fix is reported as
                "stale" rather than dropped outright.
        """
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
        """Signal the background thread to exit at its next wait point."""
        self._stop.set()

    def run(self):
        """Thread entry point: reconnect to gpsd forever with backoff.

        Each dropped/failed `_session` is caught and recorded as `_error`
        rather than propagated, then retried after an exponential backoff
        (capped at 10 s, reset to 1 s on any successful session) until `stop`
        is called.
        """
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
        """Open one gpsd connection, enable JSON watch mode, and consume
        newline-delimited messages until the socket drops or `stop` fires.

        Raises:
            ConnectionError: gpsd closed the connection.
            OSError: the connection could not be established or was lost.
        """
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
        """Parse one gpsd JSON line and fold it into the held state.

        Args:
            line: A single stripped line from the gpsd socket (one JSON
                object per gpsd's protocol); blank or unparseable lines are
                ignored.
        """
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
        """Current GPS state, computed fresh from the last-held TPV/SKY data.

        Pure data; safe to call from any thread without side effects.

        Returns:
            A dict with "connected", "device", "error", "mode", "valid",
            "stale", "latitude", "longitude", "altitude_m", "time_unix",
            "satellites_used", "error_horizontal_m", "error_vertical_m", and
            "age_s". "valid" requires mode >= 2 (a 2-D or 3-D fix), a fresh
            (non-stale) fix, and finite lat/lon.
        """
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
        """Build the per-capture GPS variable dict striqt merges into a capture.

        Every value is a float so xarray/zarr store them without object
        dtypes. Missing or invalid values are NaN — never zero, which would
        read as a real position at 0°N 0°E.

        Returns:
            A dict keyed by the names in `CAPTURE_FIELDS`, all float values.
            `gps_altitude_m` is NaN unless the fix is valid AND 3-D (a 2-D
            fix's altitude is meaningless). `gps_valid` is 1.0 or 0.0.
        """
        snap = self.snapshot()
        valid = snap["valid"]
        sats = snap["satellites_used"]
        age = snap["age_s"]
        return {
            "gps_latitude_deg":  float(snap["latitude"]) if valid else math.nan,
            "gps_longitude_deg": float(snap["longitude"]) if valid else math.nan,
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
        """The `capture_fields()` equivalent for a run with GPS disabled or
        unavailable, so every archived Dataset carries the same variables
        whether or not GPS was in play.

        Returns:
            A dict keyed by `CAPTURE_FIELDS`: all NaN except `gps_fix_mode`
            and `gps_valid`, which are 0.0.
        """
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
    """Check whether the GPS integration is enabled.

    Returns:
        False when the `RADIO_GPS` environment variable is "0", "off",
        "false", or "no" (case-insensitive); True otherwise (the default).
    """
    return str(os.environ.get("RADIO_GPS", "1")).strip().lower() not in (
        "0", "off", "false", "no")


def get_reader(start=True):
    """Get the process-wide GpsReader, creating and starting it on first use.

    Args:
        start: Whether to start the reader thread if this call creates it.
            Set False in tests that only need the object, not the thread.

    Returns:
        The shared `GpsReader`, honoring `RADIO_GPS_HOST`/`RADIO_GPS_PORT`,
        or None if GPS is disabled via `gps_enabled()`.
    """
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
    """Build the GPS status payload for `/health` and the Record tab.

    Returns:
        A dict matching `GpsReader.snapshot()` plus an "enabled" key; when
        disabled, a minimal dict with "enabled": False and an explanatory
        "error" instead (the reader is never started in that case).
    """
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

    striqt is imported lazily here (rather than at module load) so that
    `core/` stays importable on hosts without striqt installed.

    Returns:
        The `GpsPeripherals` class, or None when striqt is unavailable — in
        which case callers should fall back to striqt's `NoPeripherals`.
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
            """Attach the process-wide GpsReader for the sweep's lifetime."""
            self._reader = get_reader()

        def close(self):
            """No-op: the reader is process-wide and other users (the
            status endpoint, another sweep) may still need it, so this
            method must not stop or tear it down.
            """
            return

        def setup(self, captures, loops):
            """No-op: GPS needs no per-sweep setup beyond `open`."""
            return

        def arm(self, capture):
            """No-op: GPS needs no per-capture arming."""
            return

        def acquire(self, capture):
            """Return the GPS fields to merge into this capture's extra_data.

            Args:
                capture: The striqt capture being armed (unused; GPS fields
                    are the same regardless of capture parameters).

            Returns:
                `GpsReader.capture_fields()` from the cached snapshot, or
                `GpsReader.absent_fields()` if no reader is attached or
                reading it raises — a recording must never die over metadata.
            """
            reader = getattr(self, "_reader", None)
            if reader is None:
                return GpsReader.absent_fields()
            try:
                return reader.capture_fields()
            except Exception:
                return GpsReader.absent_fields()

    return GpsPeripherals
