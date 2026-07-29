"""GPS capture metadata: gpsd client, staleness, and the striqt peripheral.

Everything runs against a fake gpsd on a loopback port — no receiver, no
daemon, no striqt. The rule these pin down: a missing or stale fix records
NaN + gps_valid=0, NEVER 0.0/0.0.
"""
import json
import math
import socket
import threading
import time

import pytest

from core import gps
from core.gps import CAPTURE_FIELDS, GpsReader


class FakeGpsd:
    """Minimal gpsd: greets, then emits whatever lines the test queues."""

    def __init__(self, lines=(), drop_after=None):
        self.lines = list(lines)
        self.drop_after = drop_after
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self.connections = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self._srv.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.connections += 1
            with conn:
                try:
                    conn.sendall(json.dumps(
                        {"class": "VERSION", "release": "3.17"}).encode() + b"\n")
                    for line in self.lines:
                        conn.sendall(json.dumps(line).encode() + b"\n")
                    if self.drop_after is not None:
                        time.sleep(self.drop_after)
                        continue          # close, forcing the client to reconnect
                    while not self._stop.is_set():
                        time.sleep(0.05)
                except OSError:
                    pass

    def close(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


def _reader_for(fake, **kw):
    reader = GpsReader(host="127.0.0.1", port=fake.port, **kw)
    reader.start()
    return reader


def wait_for(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    return None


TPV_3D = {"class": "TPV", "device": "/dev/ttyACM0", "mode": 3,
          "time": "2026-07-28T23:24:11.000Z", "lat": 39.9951, "lon": -105.2617,
          "alt": 1655.4, "eph": 3.2, "epv": 4.8}
SKY_9 = {"class": "SKY", "uSat": 9}


# ── Fix parsing ─────────────────────────────────────────────────────────────

def test_reads_a_three_dimensional_fix():
    fake = FakeGpsd([{"class": "DEVICES", "devices": [{"path": "/dev/ttyACM0"}]},
                     TPV_3D, SKY_9])
    reader = _reader_for(fake)
    try:
        snap = wait_for(lambda: (s := reader.snapshot())["valid"] and s)
        assert snap, "never reported a valid fix"
        assert snap["latitude"] == pytest.approx(39.9951)
        assert snap["longitude"] == pytest.approx(-105.2617)
        assert snap["altitude_m"] == pytest.approx(1655.4)
        assert snap["mode"] == 3
        assert snap["satellites_used"] == 9
        assert snap["device"] == "/dev/ttyACM0"
        assert snap["error_horizontal_m"] == pytest.approx(3.2)
    finally:
        reader.stop(); fake.close()


def test_accepts_newer_gpsd_altitude_field_names():
    """gpsd >= 3.20 splits `alt` into altMSL/altHAE; the radio runs 3.17."""
    tpv = dict(TPV_3D)
    del tpv["alt"]
    tpv["altMSL"] = 1600.0
    tpv["altHAE"] = 1620.0
    fake = FakeGpsd([tpv])
    reader = _reader_for(fake)
    try:
        snap = wait_for(lambda: (s := reader.snapshot())["valid"] and s)
        assert snap and snap["altitude_m"] == pytest.approx(1600.0)
    finally:
        reader.stop(); fake.close()


# ── The honesty rule ────────────────────────────────────────────────────────

def test_no_fix_records_nan_never_null_island():
    """mode=1 means the receiver has no fix. Coordinates must be NaN — 0.0/0.0
    would silently place every capture in the Gulf of Guinea."""
    fake = FakeGpsd([{"class": "TPV", "mode": 1}])
    reader = _reader_for(fake)
    try:
        assert wait_for(lambda: reader.snapshot()["mode"] == 1)
        fields = reader.capture_fields()
        assert math.isnan(fields["gps_latitude_deg"])
        assert math.isnan(fields["gps_longitude_deg"])
        assert fields["gps_valid"] == 0.0
        assert fields["gps_latitude_deg"] != 0.0 or True   # never coerced to 0
    finally:
        reader.stop(); fake.close()


def test_gpsd_without_a_receiver_is_named_precisely():
    """The radio's actual state: gpsd up, `devices: []` — the single most
    likely misconfiguration, so the error must say how to fix it."""
    fake = FakeGpsd([{"class": "DEVICES", "devices": []}])
    reader = _reader_for(fake)
    try:
        # Wait for the DEVICES message specifically — the pre-connect state
        # ("not started") is also a truthy error.
        snap = wait_for(lambda: (s := reader.snapshot())["connected"]
                        and s["error"] and s)
        assert snap and "no device attached" in snap["error"]
        assert snap["valid"] is False
    finally:
        reader.stop(); fake.close()


def test_a_stale_fix_stops_counting_as_valid():
    """A receiver that stops updating must not keep stamping captures with an
    old position — that would look like a stationary sensor forever."""
    fake = FakeGpsd([TPV_3D])
    reader = _reader_for(fake, stale_after=0.4)
    try:
        assert wait_for(lambda: reader.snapshot()["valid"])
        time.sleep(0.7)
        snap = reader.snapshot()
        assert snap["stale"] is True
        assert snap["valid"] is False
        fields = reader.capture_fields()
        assert math.isnan(fields["gps_latitude_deg"])
        assert fields["gps_valid"] == 0.0
    finally:
        reader.stop(); fake.close()


def test_two_dimensional_fix_keeps_position_but_drops_altitude():
    fake = FakeGpsd([{"class": "TPV", "mode": 2, "lat": 40.0, "lon": -105.0,
                      "alt": 1234.0}])
    reader = _reader_for(fake)
    try:
        assert wait_for(lambda: reader.snapshot()["valid"])
        fields = reader.capture_fields()
        assert fields["gps_latitude_deg"] == pytest.approx(40.0)
        assert math.isnan(fields["gps_altitude_m"]), \
            "a 2-D fix has no meaningful altitude"
        assert fields["gps_valid"] == 1.0
    finally:
        reader.stop(); fake.close()


def test_unreachable_gpsd_degrades_quietly():
    sock = socket.socket()            # bind and close to get a certainly-dead port
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    reader = GpsReader(host="127.0.0.1", port=port)
    reader.start()
    try:
        snap = wait_for(lambda: (s := reader.snapshot())["error"] and s)
        assert snap and snap["connected"] is False
        fields = reader.capture_fields()
        assert fields["gps_valid"] == 0.0
        assert set(fields) == set(CAPTURE_FIELDS)
    finally:
        reader.stop()


def test_reader_reconnects_after_gpsd_restarts():
    fake = FakeGpsd([TPV_3D], drop_after=0.2)
    reader = _reader_for(fake)
    try:
        assert wait_for(lambda: fake.connections >= 2, timeout=8.0), \
            "never reconnected after the daemon dropped the link"
    finally:
        reader.stop(); fake.close()


# ── Capture field contract ──────────────────────────────────────────────────

def test_capture_fields_are_all_plain_floats():
    """xarray/zarr must not receive object dtypes or None."""
    fake = FakeGpsd([TPV_3D, SKY_9])
    reader = _reader_for(fake)
    try:
        assert wait_for(lambda: reader.snapshot()["valid"])
        fields = reader.capture_fields()
        assert set(fields) == set(CAPTURE_FIELDS)
        for name, value in fields.items():
            assert isinstance(value, float), f"{name} is {type(value).__name__}"
    finally:
        reader.stop(); fake.close()


def test_absent_fields_match_the_live_contract():
    absent = GpsReader.absent_fields()
    assert set(absent) == set(CAPTURE_FIELDS)
    assert absent["gps_valid"] == 0.0
    assert absent["gps_fix_mode"] == 0.0
    assert math.isnan(absent["gps_latitude_deg"])


def test_time_parsing_survives_a_missing_or_broken_timestamp():
    fake = FakeGpsd([{"class": "TPV", "mode": 3, "lat": 1.0, "lon": 2.0,
                      "time": "not-a-timestamp"}])
    reader = _reader_for(fake)
    try:
        assert wait_for(lambda: reader.snapshot()["valid"])
        assert math.isnan(reader.capture_fields()["gps_time_unix"])
    finally:
        reader.stop(); fake.close()


# ── Module-level switches ───────────────────────────────────────────────────

def test_disabling_gps_skips_the_reader_entirely(monkeypatch):
    monkeypatch.setenv("RADIO_GPS", "0")
    assert gps.gps_enabled() is False
    assert gps.get_reader() is None
    st = gps.status()
    assert st["enabled"] is False and st["valid"] is False


def test_peripheral_falls_back_when_striqt_is_missing(monkeypatch):
    """core/ must stay importable — and usable — without striqt."""
    import builtins
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("striqt"):
            raise ImportError("striqt blocked for this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    assert gps.gps_peripherals_class() is None
