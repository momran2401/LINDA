# LINDA — Live IQ Navigation and Display Application

A live RF visualization suite for software-defined radios, built by Mustafa Omran, Aric Sanders, and Dan Kuester.

Streams real-time spectrogram waterfalls and power
spectral density (PSD) — one pane per RX channel — to any web browser, a
terminal over SSH, or fullscreen on the radio's own display.

A built-in demo mode also synthesizes realistic signals so everything can be
developed and tested on a laptop without hardware

**Supported radios**

| Radio | Notes |
|---|---|
| Deepwave **AIR8201B / AIR7201B / AIR7101B** | dual-channel; auto-identified from SoapySDR enumeration |
| ADALM **PlutoSDR** | single-channel |
| Any other SoapySDR radio | best-effort generic adapter |
| **Demo** (synthetic IQ) | no hardware needed; signals retune realistically |

**Highlights**

- **Verified settings** — every change (frequency, rate, gain, source config)
  is a tracked *operation*: validated → applied to hardware → **read back from
  the driver** → confirmed by a fresh frame → verdict. No more "the log said
  so"; the radio itself has to agree.
- **One shared backend** (`live/core/`) drives every frontend, so fixes and
  features land everywhere at once.
- **Plug-and-play deployment** — a one-shot installer sets up the service,
  username-only roles, mDNS, and (optionally) a Wi-Fi hotspot or direct-Ethernet mode
  so the viewer works with **zero internet and zero network configuration**.
- Three login roles (admin / viewer / intern), calibrated striqt analysis
  backends, interactive PSD, CSV/PNG export, and a live operations/journal
  view in the browser.

---

## Contents

1. [Requirements](#requirements)
2. [Installation](#installation)
3. [Quick start](#quick-start)
4. [Deployment modes](#deployment-modes)
5. [Using the web UI](#using-the-web-ui)
6. [Command-line reference](#command-line-reference)
7. [Configuration](#configuration)
8. [Testing & hardware qualification](#testing--hardware-qualification)
9. [Repository layout](#repository-layout)
10. [Troubleshooting](#troubleshooting)
11. [Further documentation](#further-documentation)
12. [License and Attribution](#license-and-attribution)

---

## Requirements

- **Linux** (automated full setup: Debian/Raspberry Pi OS 12–13 or Ubuntu
  22.04/24.04 on x86-64 or 64-bit ARM). Other platforms can use manual demo
  setup.
- **Python 3.9–3.13**
- Python packages: `fastapi`, `uvicorn`, `numpy` (see
  [`live/requirements.txt`](live/requirements.txt))
- For real radios: **SoapySDR** with your radio's driver module
  (`SoapyAIRT` for Deepwave; `soapysdr-module-uhd` + `uhd-host` **plus the UHD
  images** — `sudo uhd_images_downloader` — for an Ettus USRP B205mini/B2xx;
  `SoapyPlutoSDR` for Pluto, which must be built from source before Debian
  forky packages it) and the
  **striqt** acquisition/analysis library:

  ```sh
  pip install 'striqt @ git+https://github.com/usnistgov/striqt@2e7696d3cd7c9f710f406b4b83148476ead8c20f'
  ```

  This pins striqt 0.7.0 to the exact commit verified in the AIR-T's working
  Pixi environment.

Demo mode needs only Python + the pip packages — no SDR stack at all.

## Installation

### Option A — one-shot installer (recommended on the radio host)

```sh
git clone <this-repo> && cd LINDA
sudo bash setup.sh              # interactive TUI: mode, port, hostname, radio…
sudo bash setup.sh --defaults   # no questions: web mode + one attached radio
# Ettus USRP B205mini/B2xx (recommended explicit one-liner):
sudo bash setup.sh --defaults --device=uhd
# Provision before the selected radio is physically available:
sudo bash setup.sh --skip-hardware-check
```

The installer is **idempotent** (safe to re-run) and takes care of:

- apt packages: build tooling, SoapySDR + **only** the driver stack the
  selected radio uses (UHD/SoapyUHD for `--device=uhd`), avahi (mDNS),
  whiptail, USB/udev permissions, NetworkManager (for hotspot/ethernet modes),
  and Chromium/X/LightDM (for kiosk mode)
- for a USRP: the UHD firmware/FPGA images (a B2xx uploads its image over USB
  at every open, so it cannot stream without them) and
  `usbcore.usbfs_memory_mb=1000`, without which USB 3 capture overflows
  continuously on a Pi
- a Python virtualenv at `.venv/` with one consistently resolved
  FastAPI/uvicorn/numpy/striqt dependency set; the installer
  preserves and rebuilds stale environments, then runs targeted import checks
  before configuring the service
- username-only role logins and a generated session-signing secret written to
  `/etc/radio-web/radio.env` (mode 0600)
- the `radio-web` systemd service (auto-start on boot, hardened, journald
  logging) and the scoped sudoers rule that powers the Reset Radio button
- mDNS hostname (reach the radio at `http://<name>.local:8000`), a firewall
  rule when UFW is active, and the selected network mode (see below)
- selected-radio enumeration plus a short streaming/settings qualification
  (unless `--skip-hardware-check` was given)
- a strict post-install health check; failures include recent systemd logs and
  do not print “Setup complete”
- a reboot-required notice when group, udev, network, display, or package
  changes need a fresh boot

Deepwave's SoapyAIRT/CUDA software is proprietary and is not published in the
Debian or Python package repositories. A clean AIR-T must first use Deepwave's
supported system image/driver installer. `setup.sh` detects this case and stops
with a specific error instead of reporting a working AIR-T installation.

### Option B — just the Python deps (no root, e.g. a dev laptop)

```sh
bash setup.sh --deps-only       # creates .venv and installs live/requirements.txt
# or simply:
pip install -r live/requirements.txt
```

`setup.sh` also applies [`live/constraints.txt`](live/constraints.txt), which
records the qualified top-level runtime versions. Directly running the plain
`pip install` command is intended for development, not reproducible appliance
provisioning.

## Quick start

```sh
# No hardware — synthetic signals, open http://localhost:8000
./.venv/bin/python live/striqt_web_server.py --demo

# Real radio: enumerate one attached SDR automatically
./.venv/bin/python live/striqt_web_server.py --device auto

# Ettus USRP B205mini/B2xx (select the SoapyUHD driver explicitly)
./.venv/bin/python live/striqt_web_server.py --device driver=uhd

# Terminal waterfall over SSH (no browser, no GUI)
./.venv/bin/python live/striqt_standalone_terminal.py --demo --backend quicklook

# Fullscreen on the radio's own display
./.venv/bin/python live/striqt_kiosk.py --demo
```

If you used the installer, the service is already running — just open
`http://<hostname>.local:8000` and enter `admin`, `viewer`, or `intern` as the
username. There is no password.

## Deployment modes

All modes serve the **same web UI**; they differ only in how you reach it.
The installer configures whichever you pick (and you can change it later by
editing `RADIO_MODE` in `/etc/radio-web/radio.env` and restarting).

| Mode       | What you get |
|------------|--------------|
| `web`      | web server on the existing network — `http://<host>.local:8000` |
| `hotspot`  | the radio broadcasts its **own Wi-Fi access point**; connect to it and browse to `http://10.42.0.1:8000`. Works with no internet at all. |
| `ethernet` | plug a laptop **directly into the radio's Ethernet port**; it gets an address automatically (shared DHCP) — `http://10.42.0.1:8000` or `http://<host>.local:8000` |
| `kiosk`    | the web UI fullscreen in Chromium on the radio's attached display |
| `terminal` | no service; run the curses monitor or `radioctl.py` by hand over SSH |

Internet access from anywhere (optional): `bash live/run_web.sh --tunnel`
starts the server plus a Cloudflare Tunnel and prints a public URL.

## Using the web UI

**Sign in.** Three roles: **admin** (full control, one at a time),
**viewer** and **intern** (read-only — display-local toggles only). The
header's *Sign out* button switches users.

**Header modes.** **DAN** shows the full control surface (capture settings,
analysis parameters, PSD tools); **ARIC** is a simplified station tuner —
click a named station (FM, GPS L1, B41 5G, …) to tune to it. Both modes use
the identical validated control path; stations outside the connected radio's
tuning range are greyed out.

**Left rail tabs**

- **DISPLAY** — pause, duration/time window, analysis backend
  (calibrated / quicklook / PSD statistics / 5G SSB), color and axis options,
  and the admin-only **Reset Radio** button (restarts the service and
  *verifies* the restart happened before claiming success).
- **PSD** — trace toggles (peak marker/hold, min trace, RX1−RX2 diff),
  crosshair, Y span.
- **CAPTURE** (DAN, admin) — the full capture/source/analysis editor. Fields
  are in **MHz / MS/s**, seeded from what the server actually runs, and Apply
  sends **only what you changed**. Source-spec fields (clock source,
  calibration, …) apply through a verified device reconnect.
- **RECORD** (DAN, admin) — a supervised striqt sweep seeded from the live
  center/rate/gain and analyses. Raw IQ is opt-in; advanced YAML supports
  multi-frequency sweeps. Controls lock while the radio records, all viewers
  see a banner, and live acquisition resumes after Stop, duration, or error.
  Capture length and the spectrogram/PSD/native-power products are selectable.
  Output is written transactionally, integrity-checked before finalization, and
  listed in a recording catalog with guarded downloads.
- **MEASURE** (DAN) — native striqt RMS/peak channel power, an explicitly
  thresholded occupancy histogram, calibration provenance and honest units,
  low-rate PSS/SSS cell-candidate status, rich metadata export, and versioned
  server-owned analysis presets. A configured calibration file is shown as
  available to recording but live direct-stream values remain labelled
  relative until correction is actually applied to that path.
- **OPS** — the verified-operations trail (every change with its
  validation → readback → verdict stages), service health, and — for admins —
  a live tail of the service journal.

**PSD interaction:** mouse-wheel zoom · drag to pan · Shift-drag box zoom ·
Alt-drag to draw a band-monitor selection · double-click to reset. Zoom
survives incoming frames and resets on retune.

**Waterfalls** adapt to the radio: a dual-channel AIR-T shows two panes side
by side; a single-channel Pluto gets one full-width pane.

## Command-line reference

`striqt_web_server.py` (the same flags pass through `run_web.sh` and
`striqt_kiosk.py -- …`):

| Flag | Meaning |
|---|---|
| `--device X` | `air8201b` (default) · `air7201b` · `air7101b` · `pluto` · `demo` · `auto` (enumerate; must find exactly one) · `driver=X[,serial=Y]` (pick one of several; a USRP B205mini uses `driver=uhd`) |
| `--demo` | alias for `--device demo` |
| `--ports 0,1` | explicit RX port list (default `auto`: probe the driver, fall back to the profile) |
| `--channels N` | use the first N channels (demo: create N) |
| `--backend X` | `calibrated` (default) · `quicklook` · `psd` · `ssb` |
| `--quantize` | uint8 waterfall frames (~4× smaller, good for slow links) |
| `--fps N` | max broadcast frame rate |
| `--host` / `--port` | bind address / port (default 0.0.0.0:8000) |

Other entry points:

```sh
bash live/run_web.sh [--tunnel]                    # launcher (+ optional Cloudflare tunnel)
python3 live/striqt_kiosk.py [--no-kiosk] [-- …]   # local fullscreen browser
python3 live/striqt_standalone_terminal.py --help  # curses monitor (arrow keys tune)
python3 live/radioctl.py --user admin status       # inspect a RUNNING server
python3 live/radioctl.py --user admin set --center-mhz 2593 --gain 5
python3 live/radioctl.py --user admin logs         # stream the operation trail
```

## Configuration

Production configuration lives in `/etc/radio-web/radio.env` (written by the
installer, read by the systemd unit). For ad-hoc runs, the same variables work
as environment variables:

| Variable | Purpose |
|---|---|
| `ADMIN_USER` | admin-role username (default `admin`) |
| `VIEWER_USER`, `INTERN_USER` | read-only usernames (defaults `viewer`, `intern`) |
| `RADIO_SESSION_SECRET` | cookie-signing key (generate: `openssl rand -hex 32`) |
| `RADIO_AUTH_DISABLE=1` | disable auth entirely (demo/dev only — never in production) |
| `RADIO_MODE` | `web` / `hotspot` / `ethernet` / `kiosk` / `terminal` (service entrypoint) |
| `RADIO_DEVICE`, `RADIO_PORT`, `RADIO_EXTRA_ARGS` | service device/port/extra flags |
| `RADIO_SERVICE_NAME` | systemd unit the Reset Radio button restarts (default `radio-web`) |
| `RADIO_RECORDINGS_DIR` | recording root (default `<repo>/recordings`) |
| `RADIO_RECORDING_SETTLE_SEC` | radio-release delay before sweep startup (default `2.0`) |
| `SPEC_BACKEND` | default analysis backend |
| `RADIO_KIOSK_BROWSER` | kiosk browser executable override |

The server warns loudly if the session-signing secret is missing or the auth
gate is disabled. Username-only roles are convenience access control, not a
security boundary: anyone who enters the admin username receives admin access.

## Testing & hardware qualification

```sh
# Unit + integration tests (no hardware; includes an auth-enabled server test)
cd live && python3 -m pytest tests/

# Bench qualification ON the radio host (exclusive access, no server running):
# applies each setting and requires driver readback + a fresh frame to agree.
# Exit codes: 0 = verified · 1 = mismatch/failure · 2 = readback unsupported
python3 live/tools/hardware_qual.py --device auto

# Qualify THROUGH the running service (reversible; restores the config after)
python3 live/radioctl.py --user admin self-test

# Upstream library tests
cd striqt && pytest tests/
```

## Repository layout

```
live/core/                  shared backend (Python 3.9+)
├── config.py               RadioConfig + SharedConfig (validated control path)
├── acquisition.py          Acquirer / Computer / DemoAcquirer threads
├── dsp.py                  calibrated / quicklook / psd / ssb backends, frame header
├── parsing.py              freedom-model parsers + striqt scratch validators
├── devices/                device adapters: Deepwave models, Pluto, generic
│                           soapy, demo (discovery, envelopes, hardware readback)
├── operations.py           verified-operations log (requested → … → verdict)
├── health.py               /health model (boot_id, radio + frame liveness)
└── serialization.py        binary frame wire format (serialize/parse)

live frontends:
├── striqt_web_server.py    web UI server (FastAPI + WebSocket)  ← canonical
├── web/                    browser app (index.html, app.js, vendored uPlot)
├── striqt_kiosk.py         same web UI fullscreen on the local display
├── striqt_standalone_terminal.py   curses monitor for SSH (ASCII waterfall)
├── radioctl.py             SSH client for the running server
└── tools/hardware_qual.py  on-radio settings qualification

deployment:
├── setup.sh                one-shot installer + setup TUI (repo root)
├── deploy/                 systemd unit template + service entrypoint
├── live/run_web.sh         manual launcher (optional Cloudflare tunnel)
└── live/install_radio_web_sudoers.sh   scoped sudo rule for Reset Radio

live/legacy/                pre-core scripts, frozen and unlaunched — the two
                            PyQt standalones and the TCP server/client pair.
                            Superseded by the kiosk + web server; see
                            live/legacy/README.md

striqt/                     upstream NIST acquisition/analysis library (READ-ONLY)
docs/                       audit history + implementation reports
```

The web UI has **no CDN dependencies** — uPlot is vendored in
`live/web/vendor/` — so hotspot/ethernet deployments work fully offline.

## Troubleshooting

- **No frames / "waiting for first frame"** — check `journalctl -u radio-web
  -f` (or the OPS tab's journal pane). The operation log names the exact
  failing stage.
- **`--device auto` errors with a device list** — more than one radio is
  attached; pick one with the printed `--device driver=…,serial=…` selector.
- **Reset Radio fails immediately** — the sudoers rule is missing; run
  `sudo bash live/install_radio_web_sudoers.sh <service-user>` once (the
  installer does this automatically).
- **striqt won't import** — run with `--demo` to verify everything else, then
  install striqt (see Requirements). On the AIR-T, use its pixi environment.
- **A setting "didn't take"** — open the OPS tab: every change shows its
  validation, hardware readback, and verdict. `mismatch` means the driver
  disagreed with the request; `unverified` means the driver could not answer.
- **Health check from scripts** — `curl http://<host>:8000/health` works
  without credentials (minimal liveness info only).

## AI Assistance
Parts of this project were built with the help of generative AI tools, such as Claude Code, and OpenAI Codex. 
All AI-generated outputs were reviewed, tested, and edited by the human project maintainer.

## License and Attribution

This work was developed in connection with the NIST SURF project “Development
of visualization frontends for cellular 5G-NR measurements.” Reuse,
redistribution, or derivative work should be approved by the appropriate NIST
project mentors (Dr. Aric Sanders & Dr. Dan Kuester) and the repository
maintainer (Mustafa Omran) before use outside the intended NIST research context.

This repository is currently not licensed for public reuse. Unless a separate
license is added, all rights are reserved for the project-specific code in
this repository.

The `striqt/` library included in this repository was developed separately and
is not authored by Mustafa Omran. Its own README, notices, and license terms
should be preserved and followed.
