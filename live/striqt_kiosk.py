#!/usr/bin/env python3
"""
Standalone (kiosk) viewer: the full web UI on the radio's own display.

Instead of maintaining a separate PyQt UI (which permanently lagged the web
viewer's features), standalone mode runs the web server bound to localhost and
opens the SAME browser UI fullscreen. Pixel-identical to the web version, zero
duplicated frontend code.

Usage:
    python3 live/striqt_kiosk.py                 # AIR8201B, fullscreen browser
    python3 live/striqt_kiosk.py --demo          # synthetic IQ
    python3 live/striqt_kiosk.py --no-kiosk      # normal browser window
    python3 live/striqt_kiosk.py -- --quantize   # extra args → web server

Any argument after `--` is passed through to striqt_web_server.py verbatim.
The browser is auto-detected: chromium (kiosk) → google-chrome → firefox →
the system default opener. Closing the browser window shuts the server down.
"""

import argparse
import atexit
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def wait_for_health(url, timeout=120.0):
    """Wait for the server to serve /health.

    120 s, not 30 s: a first start imports striqt (numba, llvmlite, scipy,
    matplotlib) and then opens the radio, and a USRP alone spends ~8 s loading
    its FPGA image over USB. On a Raspberry Pi the old 30 s budget expired
    while the server was still coming up healthy, so kiosk mode killed a
    working install and systemd recorded it as a failure.
    """
    t0 = time.time()
    last_note = 0.0
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            elapsed = time.time() - t0
            if elapsed - last_note >= 15.0:
                last_note = elapsed
                print(f"[kiosk] waiting for the server… {elapsed:.0f}s", flush=True)
            time.sleep(0.5)
    return False


def browser_command(url, kiosk=True):
    """Best available browser, preferring a chromium-family kiosk.
    RADIO_KIOSK_BROWSER overrides auto-detection. The Chromium profile lives
    under /tmp because the hardened systemd unit mounts the home directory
    read-only (ProtectHome) — the default profile path would fail there."""
    import os, tempfile
    override = os.environ.get("RADIO_KIOSK_BROWSER")
    names = ([override] if override else []) + [
        "chromium-browser", "chromium", "google-chrome", "chrome"]
    profile = Path(tempfile.gettempdir()) / "radio-kiosk-profile"
    for name in names:
        exe = shutil.which(name) if name else None
        if exe:
            args = [exe, "--noerrdialogs", "--disable-session-crashed-bubble",
                    "--no-first-run", "--disable-infobars",
                    "--ozone-platform-hint=auto",
                    f"--user-data-dir={profile}",
                    f"--app={url}"]
            if kiosk:
                args.insert(1, "--kiosk")
            return args
    exe = shutil.which("firefox")
    if exe:
        return [exe, "--kiosk", url] if kiosk else [exe, url]
    for opener in ("xdg-open", "open"):
        exe = shutil.which(opener)
        if exe:
            return [exe, url]
    return None


def main():
    parser = argparse.ArgumentParser(
        description="standalone kiosk viewer (web UI on the local display)")
    parser.add_argument("--device", default=None,
                        help="forwarded to the web server (air8201b/pluto/auto/…)")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-kiosk", action="store_true",
                        help="open a normal browser window instead of fullscreen")
    parser.add_argument("server_args", nargs="*",
                        help="extra args after -- go to striqt_web_server.py")
    args = parser.parse_args()

    server_cmd = [sys.executable, str(HERE / "striqt_web_server.py"),
                  "--host", "127.0.0.1", "--port", str(args.port)]
    if args.device:
        server_cmd += ["--device", args.device]
    if args.demo:
        server_cmd += ["--demo"]
    server_cmd += args.server_args

    print(f"[kiosk] starting server: {' '.join(server_cmd)}")
    server = subprocess.Popen(server_cmd)

    def cleanup(*_):
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, lambda *a: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))

    url = f"http://127.0.0.1:{args.port}"
    if not wait_for_health(f"{url}/health"):
        print("[kiosk] ERROR: server never became healthy — see its output above",
              file=sys.stderr)
        cleanup()
        sys.exit(1)
    print(f"[kiosk] server healthy at {url}")

    cmd = browser_command(url, kiosk=not args.no_kiosk)
    if cmd is None:
        print(f"[kiosk] no browser found — open {url} manually "
              f"(server keeps running; Ctrl-C to stop)")
        server.wait()
        return
    print(f"[kiosk] launching browser: {' '.join(cmd)}")
    browser = subprocess.Popen(cmd)

    def stop_browser():
        if browser.poll() is None:
            browser.terminate()
            try:
                browser.wait(timeout=5)
            except subprocess.TimeoutExpired:
                browser.kill()

    # Registered so a systemd stop (SIGTERM → sys.exit) takes the browser down
    # as well; cleanup() above only ever handled the server.
    atexit.register(stop_browser)

    # Exit when EITHER side goes away, and always leave the machine with no
    # stray processes.
    while True:
        if browser.poll() is not None:
            print("[kiosk] browser closed — stopping server")
            cleanup()
            return
        if server.poll() is not None:
            # This used to "leave the browser open", which stranded one
            # Chromium per cycle: the unit is Restart=always, so systemd
            # relaunches this script, it starts a fresh server and a SECOND
            # browser, and they pile up until the Pi runs out of memory.
            # Take the browser down with the server so each restart begins
            # from a clean slate.
            print("[kiosk] server exited — stopping the browser too",
                  file=sys.stderr)
            stop_browser()
            return
        time.sleep(0.5)


if __name__ == "__main__":
    main()
