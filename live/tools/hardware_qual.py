#!/usr/bin/env python3
"""
Hardware qualification: prove every radio setting ACTUALLY applies.

Run ON the radio host, against real hardware:

    python3 live/tools/hardware_qual.py                     # AIR8201B
    python3 live/tools/hardware_qual.py --device pluto
    python3 live/tools/hardware_qual.py --device auto
    python3 live/tools/hardware_qual.py --quick             # fewer points

Transmit qualification is opt-in and RADIATES. It refuses to pick a frequency
for you — terminate the TX port into a 50 ohm load, or use one you are
licensed for:

    python3 live/tools/hardware_qual.py --tx --tx-freq-mhz 2450

For each test point it applies the setting through the SAME validated path the
UI uses (SharedConfig.update), then requires:
  1. the operation to reach a terminal state (hardware apply + readback ran),
  2. driver readback to match within adapter tolerance (VERIFIED) — or the
     adapter to declare readback unsupported (UNVERIFIED, reported as such),
  3. a fresh frame whose header echoes the applied value (data-path proof).

Exit code 0 only when no test point FAILED or MISMATCHED. "unverified" points
are warnings (driver can't answer), not failures.

Unlike radioctl.py (which drives a server over HTTP), this tool imports
`live/core/` directly and drives `SharedConfig`/`Acquirer`/`Computer` in
the same process, so it needs exclusive ownership of the radio — it cannot
run alongside radio-web on hardware (e.g. an AIR-T) that only lets one
process hold its FPGA descriptors at a time.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import devices, health, state                       # noqa: E402
from core.constants import (DEFAULT_CENTER, DEFAULT_SAMPLE_RATE,  # noqa: E402
                            DEVICE_PROFILES, RATES_HZ)
from core.acquisition import Acquirer, Computer, DemoAcquirer  # noqa: E402
from core.config import SharedConfig                           # noqa: E402
from core.operations import OPERATIONS                         # noqa: E402
from core.shims import query_device_envelope                   # noqa: E402
from core.striqt_compat import _SENSOR_OK                      # noqa: E402


def wait_for(predicate, timeout, poll=0.1):
    """Poll a zero-arg predicate until it returns a truthy value or the timeout elapses.

    Args:
        predicate: Zero-argument callable, polled repeatedly.
        timeout: Seconds to keep polling before giving up.
        poll: Seconds to sleep between polls.

    Returns:
        The first truthy value returned by `predicate`, or None on timeout.
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = predicate()
        if v:
            return v
        time.sleep(poll)
    return None


def run_point(shared, acquirer, field, value, header_key, timeout):
    """Apply one setting through SharedConfig.update and confirm it end-to-end.

    Applies the change, waits for its operation to leave "running", then
    waits for a fresh frame whose header field `header_key` echoes the
    applied value — proving the setting didn't just validate but actually
    reached the data path.

    Args:
        shared: The SharedConfig instance driving this radio.
        acquirer: The Acquirer (or DemoAcquirer) supplying frames.
        field: SharedConfig field name to change (e.g. "center", "gain").
        value: New value for that field.
        header_key: Frame-header key expected to echo `value` once applied.
        timeout: Seconds to wait for the operation and for the echoing frame.

    Returns:
        tuple[str, str]: (state, detail) — state is the operation's
        terminal state ("success"/"verified"/"unverified"/"mismatch"/
        "failed"), or "failed" if it never reached one; detail is a
        human-readable explanation.
    """
    ack = shared.update({field: value})
    if ack["rejected"]:
        return "failed", f"rejected: {ack['rejected']}"
    op_id = ack["op_id"]
    if op_id is None:
        return "success", "no net change (already at this value)"

    op = wait_for(lambda: (
        (o := OPERATIONS.get(op_id)) and o["state"] != "running" and o) or None,
        timeout)
    if not op:
        return "failed", "operation never reached a terminal state"

    applied = shared.snapshot()
    want = getattr(applied, field)
    hdr = wait_for(lambda: (
        (h := acquirer.latest()[0])
        and abs(float(h.get(header_key, float("nan"))) - float(want)) < 1e-3
        and h) or None, timeout)
    if not hdr:
        return "failed", (f"op finished '{op['state']}' but no frame echoed "
                          f"{header_key}={want}")
    return op["state"], f"applied {want}, frame echoed, op {op['state']}"


def qualify_tx(acquirer, shared, args, is_demo):
    """Closed-loop TX qualification: transmit, then look for the carrier on RX.

    This is the only test in this file that RADIATES, which is why it is
    opt-in (`--tx`) and why it refuses to pick a frequency for the caller.
    What it proves, in order: the driver accepts the tuning and reads it
    back; the writer thread actually streams at close to full duty cycle;
    and — the part no readback alone can tell you — the carrier shows up in
    the receiver at the commanded offset, through an antenna or a loopback
    cable. A radio that reports a perfect readback while emitting nothing
    passes every other check in this file and fails only this one.

    Args:
        acquirer: The live Acquirer (or DemoAcquirer) to bind the TX
            controller to and to read the closed-loop RX frame from.
        shared: The SharedConfig instance, used to retune the receiver onto
            the TX frequency before checking for it.
        args: Parsed CLI namespace; uses `tx_freq_mhz`, `tx_seconds`, and
            `timeout`.
        is_demo: True when running against the synthetic demo source rather
            than real hardware.

    Returns:
        list[tuple[str, str, str]]: (label, verdict, detail) rows — verdicts
        are one of "success"/"verified"/"unverified"/"mismatch"/"failed" —
        covering transmit path availability, transmit readback, streaming
        duty cycle, whether the carrier was seen on RX, and clean shutdown.
    """
    from core import tx as txmod          # local: TX is optional at import time

    out = []
    txmod.TX.bind(acquirer, demo=is_demo)
    caps = txmod.TX.capabilities(refresh=True)
    if not caps["available"]:
        return [("transmit path", "failed", caps["reason"] or "unavailable")]

    freq = args.tx_freq_mhz * 1e6
    # Offset the carrier from band centre so it cannot be confused with LO
    # leakage, which sits exactly at DC and is present whether or not the PA
    # is keyed. A qualification that mistakes LO feedthrough for its own
    # transmission is worse than no qualification.
    offset = 1e6

    # Point the RECEIVER at the transmit frequency first. Without this the
    # closed-loop check looks for a 2450 MHz carrier in a 3750 MHz frame and
    # can only ever report "could not locate the commanded bin".
    env = shared.envelope()
    if env["freq_min"] <= freq <= env["freq_max"]:
        ack = shared.update({"center": freq})
        if ack.get("op_id") is not None:
            wait_for(lambda: OPERATIONS.get(ack["op_id"])["state"] != "running",
                     args.timeout)
        print(f"   receiver tuned to {freq/1e6:.6g} MHz for the loopback check")
    else:
        out.append(("transmit seen on RX", "unverified",
                    f"{freq/1e6:.6g} MHz is outside this radio's RX range — "
                    f"cannot check its own emission"))
    print(f"→ transmit {args.tx_freq_mhz:.6g} MHz "
          f"(+{offset/1e6:g} MHz offset) for {args.tx_seconds:g} s")
    print("   *** THIS RADIATES — confirm the load/antenna and your authority ***")

    # The acknowledgment subject must be the SAME one start() is called with —
    # the gate is per-subject, so acknowledging as someone else is not
    # acknowledging at all.
    subject = "hardware_qual"
    txmod.TX.acknowledge(subject)
    try:
        txmod.TX.start({"waveform": "cw", "frequency_hz": freq,
                        "offset_hz": offset, "duration_s": args.tx_seconds},
                       subject)
    except Exception as exc:                       # noqa: BLE001
        return [("transmit start", "failed", str(exc))]

    # Generous: on a radio that cannot receive while transmitting, arming has
    # to wait for the live acquirer to release the stream (up to 15 s by
    # itself). A timeout here cancels the transmission mid-arm, which is a
    # confusing way to fail a radio that was about to work.
    if not wait_for(lambda: txmod.TX.status()["state"] == "transmitting", 40.0):
        txmod.TX.stop("qualification timed out waiting for the carrier")
        return [("transmit start", "failed", "never reached the transmitting state")]

    status = txmod.TX.status()
    actual = (status["plan"] or {}).get("actual") or {}
    if actual.get("frequency_hz") is None:
        out.append(("transmit readback", "unverified",
                    "driver returned no TX frequency"))
    elif abs(actual["frequency_hz"] - freq) <= max(10.0, 1e-6 * freq):
        out.append(("transmit readback", "verified",
                    f"driver tuned TX to {actual['frequency_hz']/1e6:.6g} MHz"))
    else:
        out.append(("transmit readback", "mismatch",
                    f"asked {freq/1e6:.6g} MHz, driver reports "
                    f"{actual['frequency_hz']/1e6:.6g} MHz"))

    time.sleep(min(args.tx_seconds, 2.0))
    status = txmod.TX.status()
    samples = status["samples_written"]
    # Duty cycle is the difference between a carrier and a burst train. A
    # transmitter that only gets 20% of its samples into the DAC still reports
    # a large sample count and a perfect readback.
    rate = (status["plan"] or {}).get("sample_rate_hz") or 1.0
    elapsed = max(status["elapsed_s"] or 0.0, 1e-6)
    duty = samples / (rate * elapsed)
    if samples <= 0:
        out.append(("transmit streaming", "failed", "0 samples written"))
    elif duty < 0.9:
        out.append(("transmit streaming", "mismatch",
                    f"{samples} samples but only {duty:.0%} duty — the DAC was "
                    f"starved, so the output is a gappy burst train, not a "
                    f"continuous carrier"))
    else:
        out.append(("transmit streaming", "success",
                    f"{samples} samples written, {duty:.0%} duty"
                    + (f", {status['underflows']} underflow(s)"
                       if status["underflows"] else "")))

    # Closed loop: is the carrier actually in the receiver's band?
    rx_mode = (status["plan"] or {}).get("rx_mode")
    if rx_mode == txmod.TX_RX_RELEASED:
        # This radio had to shut its receiver down to transmit, so it cannot
        # hear itself, ever. Say so plainly instead of reporting a failure the
        # operator cannot act on.
        out.append((
            "transmit seen on RX", "unverified",
            "this radio cannot receive while transmitting, so it cannot "
            "verify its own emission. Confirm the carrier with a second "
            "receiver or a spectrum analyser — driver readback alone does "
            "NOT prove RF left the connector"))
    else:
        header, blocks = acquirer.latest()
        seen = None
        if header is not None and blocks:
            import numpy as np
            row = np.asarray(blocks[0])
            row = row[0] if row.ndim > 1 else row
            f0 = header.get("freqs_hz_f0")
            step = header.get("freqs_hz_step")
            if f0 is not None and step:
                want = freq + offset - float(header.get("center", 0.0))
                bins = f0 + step * np.arange(row.size)
                near = np.abs(bins - want) <= max(3 * abs(step), 100e3)
                if near.any():
                    seen = float(row[near].max() - np.median(row))
        if seen is None:
            out.append(("transmit seen on RX", "unverified",
                        "could not locate the commanded bin in the frame "
                        "header — check the RX centre covers the TX frequency"))
        elif seen >= 6.0:
            out.append(("transmit seen on RX", "verified",
                        f"carrier is {seen:.1f} dB over the in-band median at "
                        f"the commanded offset"))
        else:
            out.append(("transmit seen on RX", "mismatch",
                        f"only {seen:.1f} dB over median at the commanded "
                        f"offset — driver accepted the tuning but nothing is "
                        f"coming out (no antenna/loopback, or the PA is not "
                        f"keyed)"))

    txmod.TX.stop("qualification complete")
    out.append(("transmit stops on request",
                "success" if txmod.TX.status()["state"] == "idle" else "failed",
                f"state is {txmod.TX.status()['state']}"))
    return out


def main():
    """Run the full on-radio qualification sweep and report a pass/fail summary.

    Resolves and opens the requested device, waits for a first frame, then
    walks through center-frequency, sample-rate, and gain test points (plus
    an optional TX closed-loop point) via `run_point`/`qualify_tx`, restores
    the defaults, confirms streaming survives the whole sequence, and prints
    a summary table.

    Exit codes: 0 if every point verified; 1 if any point mismatched or
    failed; 2 if every point applied but real hardware left some points
    unverified (no readback support) — demo mode never returns 2, since it
    has no readback by design.
    """
    parser = argparse.ArgumentParser(description="on-radio settings qualification")
    parser.add_argument("--device", default="air8201b")
    parser.add_argument("--demo", action="store_true",
                        help="dry-run the harness against the synthetic source")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--timeout", type=float, default=20.0,
                        help="per-point timeout (s)")
    parser.add_argument("--tx", action="store_true",
                        help="ALSO qualify the transmit path — this RADIATES. "
                             "Requires --tx-freq-mhz and a dummy load or a "
                             "frequency you are licensed to use.")
    parser.add_argument("--tx-freq-mhz", type=float,
                        help="frequency for the TX qualification point")
    parser.add_argument("--tx-seconds", type=float, default=3.0,
                        help="how long the TX qualification point transmits")
    args = parser.parse_args()

    if args.tx and args.tx_freq_mhz is None:
        parser.error("--tx requires --tx-freq-mhz. There is no safe default "
                     "transmit frequency: pick one you are licensed for, or "
                     "terminate the TX port into a 50 ohm load first.")

    selector = "demo" if args.demo else args.device
    name, adapter = devices.resolve_device(selector)
    devices.set_adapter(adapter)
    state.configure_device(name)
    state.set_device_label(adapter.label)
    is_demo = name == "demo"
    if not is_demo and not _SENSOR_OK:
        print("ERROR: striqt.sensor not importable on this host", file=sys.stderr)
        sys.exit(2)
    if is_demo:
        state.set_backend("quicklook")

    shared = SharedConfig()
    if is_demo:
        shared.update({"backend": "quicklook"})
        acquirer, computer = DemoAcquirer(shared), None
    else:
        acquirer = Acquirer(shared)
        computer = Computer(acquirer, shared)
    health.bind(acquirer, shared)
    acquirer.start()
    if computer is not None:
        computer.start()

    print(f"\n=== hardware qualification: {state.DEVICE_LABEL} "
          f"(channels {state.CHANNELS}) ===\n")
    if not wait_for(lambda: acquirer.latest()[0], args.timeout * 2):
        # The overwhelmingly common cause is that the radio-web service is
        # running and already owns the radio. The AIR-T allows exactly one
        # process to hold its FPGA descriptors, so the failure surfaces as
        # "Failed to open FPGA registers (errno = 16)" — EBUSY — which reads
        # like broken hardware if you do not know to look for the service.
        print("FAILED: no first frame — the radio never streamed.",
              file=sys.stderr)
        if not is_demo:
            print(
                "\n  This tool needs EXCLUSIVE use of the radio, and only one\n"
                "  process can hold an AIR-T at a time. If the log above says\n"
                "  'Failed to open FPGA registers (errno = 16)', the radio-web\n"
                "  service already owns it. Stop it, qualify, then start it:\n"
                "\n"
                "      sudo systemctl stop radio-web\n"
                "      python3 live/tools/hardware_qual.py "
                + (f"--tx --tx-freq-mhz {args.tx_freq_mhz:g}" if args.tx
                   else "--device auto") + "\n"
                "      sudo systemctl start radio-web\n"
                "\n"
                "  To transmit WITHOUT stopping the service, use the web UI or\n"
                "  'radioctl.py tx start' — both drive the running server.",
                file=sys.stderr)
        shared.stop()
        sys.exit(1)

    env = shared.envelope()
    centers = [1955e6, 2155e6, 751e6, 3550e6]
    if args.quick:
        centers = centers[:2]
    centers = [c for c in centers if env["freq_min"] <= c <= env["freq_max"]]
    # Qualify at the rate THIS radio actually defaults to, plus one neighbour
    # to prove a rate change applies. The old hard-coded 15.36 MS/s is fine for
    # an AIR-T but wrong for a PlutoSDR, whose own profile calls that rate
    # optimistic over its USB link and defaults to 3.84 MS/s — the qualifier
    # would have failed a perfectly healthy radio at a rate nobody runs it at.
    home_rate = DEVICE_PROFILES.get(name, {}).get("defaults", {}).get(
        "sample_rate", DEFAULT_SAMPLE_RATE)
    legal_rates = [r for r in RATES_HZ
                   if env["rate_min"] <= r <= env["rate_max"]]
    if home_rate not in legal_rates:
        home_rate = legal_rates[0] if legal_rates else home_rate
    neighbours = [r for r in legal_rates if r != home_rate]
    rates = [home_rate] + (neighbours[:1] if args.quick else neighbours)
    # Gain points come from the LIVE DRIVER, not the profile envelope.
    # air8201b declares -60..10 dB (a striqt calibrated-gain convention, and
    # deliberately not queried at runtime — see CLAUDE.md), but SoapyAIRT
    # rejects -60 and -50 outright:
    #   Invalid parameter passed to SoapyAIRT::setGain()! gain (outside range)
    # Qualifying a radio against bounds it does not implement fails a healthy
    # radio, so ask it and intersect with the profile.
    gain_lo, gain_hi = env["gain_min"], env["gain_max"]
    probed = query_device_envelope(acquirer.source) if not is_demo else {}
    if probed.get("gain_min") is not None:
        if (probed["gain_min"], probed["gain_max"]) != (gain_lo, gain_hi):
            print(f"[gain] driver reports {probed['gain_min']:g}..."
                  f"{probed['gain_max']:g} dB; profile declares "
                  f"{gain_lo:g}...{gain_hi:g} — qualifying against the driver")
        gain_lo = max(gain_lo, probed["gain_min"])
        gain_hi = min(gain_hi, probed["gain_max"])
    gains = [gain_lo, min(gain_hi, gain_lo + 10)]

    points = ([("center", c, "center") for c in centers]
              + [("sample_rate", r, "fs") for r in rates]
              + [("gain", g, "gain") for g in gains]
              + [("center", DEFAULT_CENTER, "center"),  # return to defaults
                 ("sample_rate", home_rate, "fs")])

    results = []
    for field, value, hkey in points:
        label = f"{field} = {value/1e6:.4g} M" if value > 1e4 else f"{field} = {value:g}"
        print(f"→ {label}")
        verdict, detail = run_point(shared, acquirer, field, value, hkey,
                                    args.timeout)
        print(f"   {verdict.upper()}: {detail}\n")
        results.append((label, verdict, detail))

    if args.tx:
        for row in qualify_tx(acquirer, shared, args, is_demo):
            print(f"   {row[1].upper()}: {row[2]}")
            results.append(row)
        print()

    # Sustained streaming check after all changes.
    # Wait for a FRESH frame rather than sleeping a fixed 3 s and comparing.
    # A radio that had to be released for a transmission is still re-arming
    # when a fixed sleep expires, so the old check failed a radio that was
    # merely still coming back.
    hdr0 = acquirer.latest()[0]
    t0 = (hdr0 or {}).get("time", 0.0)
    streaming = wait_for(
        lambda: (acquirer.latest()[0] or {}).get("time", 0.0) > t0, 20.0)
    results.append(("sustained streaming after all changes",
                    "success" if streaming else "failed",
                    "frames still advancing" if streaming else "stream stalled"))

    print("=== summary ===")
    bad = unverified = 0
    for label, verdict, _ in results:
        mark = {"verified": "✓", "success": "✓", "unverified": "~",
                "mismatch": "✗", "failed": "✗"}.get(verdict, "?")
        if verdict in ("mismatch", "failed"):
            bad += 1
        elif verdict == "unverified":
            unverified += 1
        print(f"  {mark} {verdict.upper():10s} {label}")
    print(f"\n{len(results) - bad}/{len(results)} points OK"
          + (f" ({unverified} unverified — driver gave no readback)"
             if unverified else ""))

    shared.stop()
    acquirer.join(timeout=3.0)
    if computer is not None:
        computer.join(timeout=3.0)
    # Exit contract: 0 = all verified; 1 = mismatch/failure;
    # 2 = applied but required readback unsupported on REAL hardware
    # (demo has no readback by design and stays exit 0).
    if bad:
        sys.exit(1)
    if unverified and not is_demo:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
