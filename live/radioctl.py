#!/usr/bin/env python3
"""
radioctl — SSH-friendly client for the running radio-web backend.

Talks HTTP to the same server the browser uses, so every change goes through
the identical validated + hardware-verified pipeline (operation IDs, driver
readback, fresh-frame confirmation).

Examples:
    python3 live/radioctl.py status
    python3 live/radioctl.py watch
    python3 live/radioctl.py logs
    python3 live/radioctl.py set --center-mhz 2593 --gain 5
    python3 live/radioctl.py set --json '{"analysis":{"target":"psd","time_statistic":"mean,max"}}'
    python3 live/radioctl.py self-test          # reversible on-radio settings qual
    python3 live/radioctl.py tx status          # is the PA keyed? (exit 0 = yes)
    python3 live/radioctl.py tx start --freq-mhz 2450 --seconds 5 --i-have-a-license
    python3 live/radioctl.py tx stop

Auth: --user or RADIOCTL_USER. The username selects the role; no password is
used. Local RADIO_AUTH_DISABLE=1 servers need no username.
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

PASS_STATES = {"success", "verified"}
WARN_STATES = {"unverified"}


class Client:
    def __init__(self, base, user=None):
        self.base = base.rstrip("/")
        self.auth = None
        if user:
            raw = "{}:".format(user).encode("utf-8")
            self.auth = "Basic " + base64.b64encode(raw).decode("ascii")

    def _headers(self, extra=None):
        headers = dict(extra or {})
        if self.auth:
            headers["Authorization"] = self.auth
        return headers

    def get(self, path):
        req = urllib.request.Request(self.base + path, headers=self._headers())
        with urllib.request.urlopen(req, timeout=6) as response:
            return json.load(response)

    def post(self, path, payload):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode("utf-8"),
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)


def print_status(client):
    health = client.get("/health")
    config = client.get("/config")
    cap = config["capture"]
    dev = config["device"]
    print("device       : {}  channels={}".format(dev["label"], dev["channels"]))
    print("service      : {}  boot={}  up {:.0f}s".format(
        health["status"], str(health["boot_id"])[:10], health.get("uptime_s") or 0))
    radio = health.get("radio")
    if radio:
        print("radio        : open={}  healthy={}  ring={:.0%}".format(
            radio["open"], radio["healthy"], radio.get("ring_fill") or 0))
    age = health.get("last_frame_age_s")
    print("last frame   : {}".format(f"{age:.2f} s ago" if age is not None else "none yet"))
    print("capture      : {:.6f} MHz  {:.4f} MS/s  gain={:.2f} dB  nfft={}".format(
        cap["center_frequency"] / 1e6, cap["sample_rate"] / 1e6,
        cap["gain"], cap["nfft"]))
    print("analysis     : {}  rows={}".format(config["backend"], config["rows"]))
    if config.get("source"):
        print("source ovr   : {}".format(config["source"]))
    last = health.get("last_operation")
    if last:
        print("last op      : #{} {} → {} ({})".format(
            last["id"], last["kind"], last["state"], last["summary"]))


def wait_operation(client, op_id, timeout=30.0):
    """Poll /operations until op_id reaches a terminal state; return the op."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for op in client.get("/operations")["operations"]:
            if op["id"] == op_id and op["state"] != "running":
                return op
        time.sleep(0.4)
    raise RuntimeError("operation #{} did not finish within {:.0f} s".format(
        op_id, timeout))


def stream_logs(client, interval=1.0):
    """Incrementally print operation stages as they happen."""
    seen = {}   # op_id -> stages printed
    while True:
        for op in client.get("/operations")["operations"]:
            start = seen.get(op["id"], 0)
            for stage in op["stages"][start:]:
                stamp = time.strftime("%H:%M:%S", time.localtime(stage["t"]))
                print("[{}] op#{:<4} {:10} {}".format(
                    stamp, op["id"], stage["stage"], stage["detail"]), flush=True)
            seen[op["id"]] = len(op["stages"])
        time.sleep(interval)


def apply_and_wait(client, name, payload, timeout=30.0):
    print("TEST {:22} {}".format(name, json.dumps(payload, sort_keys=True)))
    result = client.post("/config", payload)
    ack = result.get("ack", {})
    if ack.get("rejected"):
        raise RuntimeError("rejected: {}".format(ack["rejected"]))
    op_id = ack.get("op_id")
    if op_id is None:
        print("SKIP {:22} value produced no change".format(name))
        return "success"
    op = wait_operation(client, op_id, timeout)
    verdict = op["state"]
    tag = ("PASS" if verdict in PASS_STATES
           else "WARN" if verdict in WARN_STATES else "FAIL")
    print("{} {:22} op #{} → {}".format(tag, name, op_id, verdict.upper()))
    if tag == "FAIL":
        raise RuntimeError("op #{} finished {}".format(op_id, verdict))
    return verdict


def self_test(client, timeout=30.0):
    """
    Exercise every portable live control through the verified pipeline, then
    restore the starting recipe. Source/clock settings are deliberately
    excluded — there is no universally safe alternate value without knowing
    what is physically cabled; they still verify when changed explicitly.
    """
    config = client.get("/config")
    cap, env = config["capture"], config["envelope"]
    center = float(cap["center_frequency"])
    step = 1e6 if center + 1e6 <= env["freq_max"] else -1e6
    # Prefer a higher LTE-grid rate: AIR-T's current CV firmware accepts
    # 15.36/30.72 MS/s but rejects the nominal lower grid points.
    rates = [r for r in (30.72e6, 15.36e6, 7.68e6, 3.84e6)
             if env["rate_min"] <= r <= env["rate_max"]
             and r != cap["sample_rate"]]
    if env["gain_min"] < 0 and cap["gain"] <= 0:
        # AIR-T calibrated gain is attenuation-like; positive values in the
        # broad profile envelope are rejected by this firmware.
        alt_gain = max(env["gain_min"], cap["gain"] - 1.0)
    else:
        alt_gain = (min(env["gain_max"], cap["gain"] + 1.0)
                    if cap["gain"] + 1.0 <= env["gain_max"]
                    else max(env["gain_min"], cap["gain"] - 1.0))
    alt_nfft = next(n for n in (256, 512, 1024, 2048, 4096)
                    if n != int(cap["nfft"]))

    cases = [("center frequency", {"center": center + step}),
             ("gain", {"gain": alt_gain}),
             ("FFT size", {"nfft": alt_nfft}),
             ("frame rows", {"rows": int(config["rows"]) + 1}),
             ("LO-null toggle", {"lo_null": not bool(config["lo_null"])})]
    if rates:
        cases.insert(1, ("sample rate", {"sample_rate": rates[0]}))
    if config["backend"] != "quicklook":
        # quicklook always exists, even without the striqt analysis stack.
        cases.append(("analysis backend", {"backend": "quicklook"}))

    restore = {
        "capture": {"center_frequency": cap["center_frequency"],
                    "sample_rate": cap["sample_rate"],
                    "gain": cap["gain"], "nfft": cap["nfft"]},
        "rows": config["rows"],
        "backend": config["backend"],
        "lo_null": config["lo_null"],
    }
    failures = []
    try:
        for name, payload in cases:
            try:
                apply_and_wait(client, name, payload, timeout)
            except Exception as exc:
                failures.append((name, str(exc)))
                print("FAIL {:22} {}".format(name, exc), file=sys.stderr)
    finally:
        print("RESTORE                restoring the starting configuration")
        try:
            apply_and_wait(client, "starting config", restore, timeout)
        except Exception as exc:
            failures.append(("restore", str(exc)))
            print("FAIL restore            {}".format(exc), file=sys.stderr)

    if failures:
        print("\n{} self-test failure(s):".format(len(failures)), file=sys.stderr)
        for name, reason in failures:
            print("  {}: {}".format(name, reason), file=sys.stderr)
        return 1
    print("\nAll portable settings verified through the live pipeline.")
    return 0


def print_gps(client):
    """Show the fix recordings will stamp on every capture.

    Exit status is the useful part for scripts: 0 only when a recording
    started now would carry real coordinates.
    """
    try:
        gps = client.get("/gps")["gps"]
    except Exception as exc:
        print(f"gps: cannot reach the server — {exc}", file=sys.stderr)
        return 2
    if not gps.get("enabled"):
        print("gps          : disabled (RADIO_GPS=0)")
        return 1
    lat, lon = gps.get("latitude"), gps.get("longitude")
    if gps.get("valid") and lat is not None and lon is not None:
        alt = gps.get("altitude_m")
        print(f"gps          : {gps['mode']}-D fix")
        print(f"position     : {lat:.6f}, {lon:.6f}"
              + (f"  alt {alt:.1f} m" if isinstance(alt, (int, float)) else ""))
        sats = gps.get("satellites_used")
        eph = gps.get("error_horizontal_m")
        print(f"quality      : {sats if sats is not None else '?'} satellites"
              + (f", ±{eph:.1f} m horizontal" if isinstance(eph, (int, float)) else ""))
        print(f"age          : {gps.get('age_s')} s   device: {gps.get('device') or '?'}")
        print("\nRecordings will stamp these coordinates on every capture.")
        return 0
    # Not valid: say which of the several reasons applies.
    if not gps.get("connected"):
        reason = (f"gpsd unreachable ({gps['error']})" if gps.get("error")
                  else "connecting to gpsd…")
    elif gps.get("error"):
        reason = gps["error"]
    elif gps.get("stale"):
        reason = f"fix is stale ({gps.get('age_s')} s old)"
    elif (gps.get("mode") or 0) <= 1:
        reason = "no fix yet — the receiver needs a view of the sky"
    else:
        reason = "no position"
    print(f"gps          : NO FIX — {reason}")
    print("\nRecordings will still run; every capture records gps_valid=0 and")
    print("NaN coordinates (never 0.0/0.0).")
    return 1


def print_tx(client):
    """Show whether this radio is transmitting, and what it can transmit.

    Exit 0 only when a carrier is actually up, so a script can gate on it —
    and so `radioctl tx status` is a usable "is the PA keyed?" check from a
    terminal with no browser anywhere near it.
    """
    try:
        status = client.get("/tx")["tx"]
    except Exception as exc:
        print(f"tx: cannot reach the server — {exc}", file=sys.stderr)
        return 2
    if not status.get("available"):
        print("transmit     : unavailable — {}".format(
            status.get("reason") or "unknown reason"))
        return 1
    caps = status.get("capabilities") or {}
    env = caps.get("envelope") or {}
    sim = " (SIMULATED — nothing is radiated)" if status.get("simulated") else ""
    print("transmit     : available{}  {} channel(s)".format(
        sim, caps.get("channels")))
    if env.get("freq_min") is not None:
        print("tx range     : {:.6g}–{:.6g} MHz".format(
            env["freq_min"] / 1e6, env["freq_max"] / 1e6))
    if env.get("gain_min") is not None:
        print("tx gain      : {:g}–{:g} dB".format(env["gain_min"], env["gain_max"]))
    plan = status.get("plan")
    if not status.get("active") or not plan:
        print("state        : idle — nothing is being transmitted")
        return 1
    print("state        : {} ({})".format(status["state"], plan["waveform"]))
    print("carrier      : {:.6g} MHz  {:g} dB  {:.6g} MS/s  ch{}".format(
        plan["frequency_hz"] / 1e6, plan["gain_db"],
        plan["sample_rate_hz"] / 1e6, plan["channel"]))
    print("elapsed      : {:.1f} s{}".format(
        status.get("elapsed_s") or 0.0,
        "  remaining {:.1f} s".format(status["remaining_s"])
        if status.get("remaining_s") is not None else "  (until Stop)"))
    print("samples      : {}  underflows: {}".format(
        status.get("samples_written"), status.get("underflows")))
    return 0


def tx_start(client, args):
    payload = {
        "waveform": args.waveform,
        "frequency_hz": args.freq_mhz * 1e6,
        "offset_hz": (args.offset_khz or 0.0) * 1e3,
        "channel": args.channel,
    }
    if args.gain is not None:
        payload["gain_db"] = args.gain
    if args.amplitude is not None:
        payload["amplitude"] = args.amplitude
    if args.seconds is not None:
        payload["duration_s"] = args.seconds
    # The legal notice is a server-side gate, not a UI decoration; a CLI
    # transmitter accepts the same terms the browser does.
    if not args.i_have_a_license:
        print("radioctl: refusing to transmit without --i-have-a-license\n"
              "  Transmitting on frequencies you are not authorized to use is a\n"
              "  federal offense. Pass the flag only if you hold a license for\n"
              "  this frequency or the output is going into a shielded load.",
              file=sys.stderr)
        return 2
    client.post("/tx/acknowledge", {})
    result = client.post("/tx/start", payload)["tx"]
    plan = result.get("plan") or {}
    print("TX started: {} at {:.6g} MHz, {:g} dB{} (op #{})".format(
        plan.get("waveform"), plan.get("frequency_hz", 0) / 1e6,
        plan.get("gain_db", 0),
        ", {:g} s".format(plan["duration_s"]) if plan.get("duration_s")
        else ", until stop",
        result.get("op_id")))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="control/inspect the running radio-web backend")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--user", default=os.environ.get("RADIOCTL_USER"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=float, default=1.0)
    logs = sub.add_parser("logs")
    logs.add_argument("--interval", type=float, default=1.0)
    check = sub.add_parser("self-test")
    check.add_argument("--timeout", type=float, default=30.0)
    gps_cmd = sub.add_parser(
        "gps", help="show the GPS fix that recordings will stamp on captures")
    gps_cmd.add_argument("--watch", action="store_true",
                         help="keep polling until interrupted")
    gps_cmd.add_argument("--interval", type=float, default=2.0)
    tx_cmd = sub.add_parser("tx", help="transmit mode: status / start / stop")
    tx_sub = tx_cmd.add_subparsers(dest="tx_command", required=True)
    tx_sub.add_parser("status", help="is this radio transmitting? (exit 0 if yes)")
    tx_sub.add_parser("stop", help="stop transmitting immediately")
    tx_go = tx_sub.add_parser("start", help="begin transmitting")
    tx_go.add_argument("--waveform", default="cw",
                       choices=("cw", "two_tone", "chirp", "noise"))
    tx_go.add_argument("--freq-mhz", type=float, required=True)
    tx_go.add_argument("--offset-khz", type=float, default=0.0,
                       help="baseband offset of the tone from the TX centre")
    tx_go.add_argument("--gain", type=float,
                       help="TX gain in dB (default: the radio's minimum)")
    tx_go.add_argument("--amplitude", type=float,
                       help="IQ amplitude, 0-1 of full scale (default 0.5)")
    tx_go.add_argument("--seconds", type=float,
                       help="stop after this long (omit to transmit until stop)")
    tx_go.add_argument("--channel", type=int, default=0)
    tx_go.add_argument("--i-have-a-license", action="store_true",
                       help="required: you are licensed for this frequency, or "
                            "the output goes into a shielded load")

    setting = sub.add_parser("set")
    setting.add_argument("--center-mhz", type=float)
    setting.add_argument("--rate-msps", type=float)
    setting.add_argument("--gain", type=float)
    setting.add_argument("--nfft", type=int)
    setting.add_argument("--backend",
                         choices=("calibrated", "quicklook", "psd", "ssb"))
    setting.add_argument("--json", help="raw control payload (merged last)")
    args = parser.parse_args()

    client = Client(args.url, args.user)

    if args.command == "status":
        print_status(client)
    elif args.command == "watch":
        while True:
            print("\033[2J\033[H", end="")
            print_status(client)
            time.sleep(args.interval)
    elif args.command == "logs":
        stream_logs(client, args.interval)
    elif args.command == "self-test":
        return self_test(client, args.timeout)
    elif args.command == "tx":
        if args.tx_command == "status":
            return print_tx(client)
        if args.tx_command == "stop":
            client.post("/tx/stop", {})
            print("TX stop requested")
            return 0
        return tx_start(client, args)
    elif args.command == "gps":
        if not args.watch:
            return print_gps(client)
        try:
            while True:
                print("\033[2J\033[H", end="")
                print_gps(client)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0
    else:  # set
        payload = json.loads(args.json) if args.json else {}
        capture = {}
        if args.center_mhz is not None:
            capture["center_frequency"] = args.center_mhz * 1e6
        if args.rate_msps is not None:
            capture["sample_rate"] = args.rate_msps * 1e6
        if args.gain is not None:
            capture["gain"] = args.gain
        if args.nfft is not None:
            capture["nfft"] = args.nfft
        if capture:
            payload.setdefault("capture", {}).update(capture)
        if args.backend:
            payload["backend"] = args.backend
        if not payload:
            print("nothing to set (see --help)", file=sys.stderr)
            return 2
        return 0 if apply_and_wait(client, "set", payload) else 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        print("radioctl: HTTP {} {}".format(exc.code, body), file=sys.stderr)
        raise SystemExit(1)
    except (urllib.error.URLError, RuntimeError) as exc:
        print("radioctl: {}".format(exc), file=sys.stderr)
        raise SystemExit(1)
