# LINDA — Live IQ Navigation and Display Application

A live RF visualization suite for software-defined radios, built by Mustafa Omran, Aric Sanders, and Dan Kuester.

Streams real-time spectrogram waterfalls and power
spectral density (PSD) — one pane per RX channel — to any web browser, a
terminal over SSH, or fullscreen on the radio's own display.

A built-in demo mode also synthesizes realistic signals so everything can be
developed and tested on a laptop without hardware

---

## Requirements

- **Linux** (automated full setup: Debian/Raspberry Pi OS 12–13 or Ubuntu
  22.04/24.04 on x86-64 or 64-bit ARM). Other platforms can use manual demo
  setup.
- **Python 3.9–3.13**

## Installation

### One-shot installer

Plug the radio in first, then:

```sh
git clone https://github.com/momran2401/LINDA && cd LINDA
sudo bash setup.sh
```

### Uninstalling

```sh
sudo bash uninstall_linda.sh   
```

## Quick start
If you used the installer, the service is already running — just open
`http://<hostname>.local:8000` and enter `admin`, `viewer`, or `intern` as the
username. There is no password.

## Deployment modes

All modes serve the **same web UI**; they differ only in how you reach it.

**To change mode later, re-run the installer and pick the new one** — it
switches cleanly in both directions - Re-running removes the previous mode's system state before installing the new
one

---

Internet access from anywhere (optional): `bash live/run_web.sh --tunnel`
starts the server plus a Cloudflare Tunnel and prints a public URL.

---

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

## Troubleshooting
- **Setup fails after Installing GPS support** — no GPS serial device was
    detected. Re-run with sudo env RADIO_GPS_DEVICE=none bash setup.sh; this
    skips GPS probing for that run and recordings will set gps_valid=0.
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
