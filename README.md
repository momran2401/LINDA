<p align="center">
  <img src="live/web/linda-logo.svg" alt="LINDA" width="400">
</p>

# LINDA — Live IQ Navigation and Display Application

LINDA is a live RF visualization suite for software-defined radios. It streams
real-time spectrogram waterfalls and power spectral density (PSD) displays—one
pane per receive channel—to a web browser, SSH terminal, or the radio's local
display. A synthetic demo mode supports development without radio hardware.

Built by *Mustafa Omran, Aric Sanders, and Dan Kuester*.

## Installation

### Method 1 — one-command radio installation

On a supported Linux host (Debian/Raspberry Pi OS 12–13 or Ubuntu 22.04/24.04;
x86-64 or 64-bit ARM), clone the repository, connect the radio, then run:

```sh
git clone https://github.com/momran2401/LINDA.git
cd LINDA
sudo bash setup.sh
```

### Method 2 — laptop/demo installation

Create the Python environment only, then start synthetic IQ data:

```sh
bash setup.sh --deps-only
./.venv/bin/python live/striqt_web_server.py --demo
```

Open `http://localhost:8000`. After the full installer completes, open
`http://<hostname>.local:8000` and sign in as `admin`, `viewer`, or `intern`.
There is no password.

## Use case

Use LINDA to inspect live RF activity from a supported SDR: tune a receive
channel, observe its waterfall and PSD, adjust capture settings, record data,
and inspect operating status from the same browser interface. The web server
shares one radio stream with multiple browser clients; use demo mode for safe
UI and pipeline testing when no SDR is attached.

```sh
# Start a local synthetic viewer
bash live/run_web.sh --demo

# Start a real device selected automatically by SoapySDR
bash live/run_web.sh --device auto
```

## Workflow

1. Install LINDA or create the demo environment.
2. Start the `radio-web` service (the installer does this) or run the web
   server manually.
3. Open the server URL in a browser and choose the appropriate username role.
4. Tune and configure the receive path, then monitor, record, or export the
   resulting data.

<<<<<<< HEAD
## Troubleshooting
- **Setup fails after Installing GPS support** — no GPS serial device was
    detected. Re-run with `sudo env RADIO_GPS_DEVICE=none bash setup.sh`; this
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
=======
## Detailed manual

The [LINDA manual](docs/README_MANUAL.md) contains the complete operational
reference: supported deployment modes, all installer and command-line flags,
roles, configuration variables, service operations, recording workflows,
transmit safety, diagnostics, and troubleshooting.

## Code structure

- `live/` — LINDA's web server, terminal/kiosk front ends, RF control tools,
  recordings, and tests.
- `striqt/` — separately developed upstream RF acquisition and analysis
  library; see its [README](striqt/README.md) and documentation.
- `setup.sh` / `uninstall_linda.sh` — idempotent installation and careful
  removal of the host integration.

## Testing

```sh
pytest -q live/tests
```
>>>>>>> 0a7f2a1 (README)

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
