# LINDA Manual

This is the operational reference for LINDA (Live IQ Navigation and Display
Application). The short project overview and quick start are in the
[root README](../README.md). Run commands from the repository root unless a
command says otherwise.

## Contents

- [System model](#system-model)
- [Requirements and installation](#requirements-and-installation)
- [Deployment modes](#deployment-modes)
- [Web server and launcher reference](#web-server-and-launcher-reference)
- [Control, recording, and qualification tools](#control-recording-and-qualification-tools)
- [Configuration, roles, and service management](#configuration-roles-and-service-management)
- [Troubleshooting](#troubleshooting)
- [Removal](#removal)

## System model

LINDA has one process that owns the SDR and fans live frames out to browser
clients. One or more browsers may connect to the web UI, but do not start a
second SDR-acquiring program against the same radio. Choose one of the web
server, kiosk, or terminal front ends at a time.

Supported selector forms are `air8201b`, `air7201b`, `air7101b`, `pluto`,
`uhd`, `rtlsdr`, `hackrf`, `airspy`, `bladerf`, `limesdr`, `soapy`, `demo`,
`auto`, and a precise SoapySDR selector such as `driver=plutosdr,serial=XYZ`.
`auto` requires exactly one discoverable radio; use a precise selector when
more than one radio is listed. `--demo` is an alias for `--device demo`.

The main components are:

- `live/striqt_web_server.py`: FastAPI/uvicorn server and browser UI backend.
- `live/run_web.sh`: convenient server launcher; it can start a Cloudflare
  quick tunnel.
- `live/radioctl.py`: command-line client for status, settings, logs, GPS,
  self-test, and transmit controls.
- `live/striqt_standalone_terminal.py`: SSH-friendly terminal monitor.
- `live/striqt_kiosk.py`: launches the web UI on the host display.
- `live/tools/`: recording transfer and hardware qualification tools.
- `install_linda.sh`: system installer. `uninstall_linda.sh`: conservative remover.

## Requirements and installation

### Supported automatic installation

The automated installer supports Debian/Raspberry Pi OS 12–13 and Ubuntu
22.04/24.04 on x86-64 and 64-bit ARM, with Python 3.9–3.13. It detects the
connected radio before selecting a driver stack, creates an isolated `.venv`,
installs LINDA dependencies, configures the `radio-web` service, and validates
the resulting setup. A transcript path is printed at startup; include it in a
bug report.

```sh
git clone https://github.com/momran2401/LINDA.git
cd LINDA
sudo bash install_linda.sh
```

Re-running the installer is intended to be safe and changes an existing
deployment mode cleanly. `striqt/` is vendored upstream code and is not
modified by the installer.

### Demo or development environment

This installs the Python environment only and does not need root privileges or
radio hardware:

```sh
bash install_linda.sh --deps-only
./.venv/bin/python live/striqt_web_server.py --demo
```

For an existing environment, install the browser-server requirements directly:

```sh
python3 -m pip install -r live/requirements.txt
python3 live/striqt_web_server.py --demo
```

Real-radio operation additionally needs the `striqt` acquisition stack and
the radio's SoapySDR driver. The full installer arranges these on supported
hosts. The included `striqt/README.md` explains its independent environments
and CLI tools.

### `install_linda.sh` reference

```text
sudo bash install_linda.sh [OPTIONS]
```

| Option | Meaning |
| --- | --- |
| `--yes`, `-y` | Accept defaults without interactive questions. |
| `--demo` | Use synthetic IQ and skip hardware selection. |
| `--deps-only` | Create/update only the Python environment; do not configure a service. |
| `--skip-radio-check` | Provision before the radio is connected; the capture validation is skipped. |
| `--mode=web` | Normal LAN/web deployment. |
| `--mode=kiosk` | Local fullscreen web display. |
| `--mode=hotspot` | Configure a hotspot deployment. |
| `--mode=ethernet` | Configure an Ethernet deployment. |
| `--mode=terminal` | Terminal-focused deployment. |
| `--device=VALUE` | Override detection with a selector listed in [System model](#system-model). |
| `--port=8000` | Set the web-service TCP port; allowed range is 1024–65535. |
| `--hostname=NAME` | Set the advertised mDNS hostname. |
| `--hotspot-ssid=NAME` | Set the hotspot SSID when using hotspot mode. |
| `--hotspot-pass=PASSWORD` | Set the hotspot password when using hotspot mode. |
| `--help`, `-h` | Print the installer help. |

Examples:

```sh
sudo bash install_linda.sh --yes
sudo bash install_linda.sh --demo --mode=web
bash install_linda.sh --deps-only
sudo bash install_linda.sh --device=driver=plutosdr,serial=XYZ --mode=kiosk
sudo bash install_linda.sh --skip-radio-check --yes
```

Do not combine `--deps-only` with expectations of a systemd service: it is for
the local environment only. For a GPS-less installation, set the installer
environment variable for that one invocation:

```sh
sudo env RADIO_GPS_DEVICE=none bash install_linda.sh
```

## Deployment modes

### Installed `radio-web` service

The full installer starts the service. Browse to:

```text
http://<hostname>.local:8000
```

The port is the value selected with `--port`. Service inspection and restart:

```sh
sudo systemctl status radio-web
sudo systemctl restart radio-web
journalctl -u radio-web -f
curl http://localhost:8000/health
```

`/health` supplies minimal unauthenticated liveness information for monitoring.

### Manual web launch

```sh
python3 live/striqt_web_server.py --demo
bash live/run_web.sh --demo
bash live/run_web.sh --device auto
```

For a LAN listener, the server defaults to `0.0.0.0:8000`. A loopback-only
development listener is:

```sh
python3 live/striqt_web_server.py --demo --host 127.0.0.1 --port 8000
```

### Cloudflare quick tunnel

Install `cloudflared` and ensure it is on `PATH`, then:

```sh
bash live/run_web.sh --tunnel --demo
```

The launcher prints a public URL. A public endpoint must use normal
authentication and a strong `RADIO_SESSION_SECRET`; do not set
`RADIO_AUTH_DISABLE=1` for an Internet-facing deployment.

### Terminal monitor

```sh
python3 live/striqt_standalone_terminal.py --demo
python3 live/striqt_standalone_terminal.py --device pluto --center-mhz 3750 \
  --rate-msps 15.36 --nfft 1024 --fps 3 --backend quicklook
```

| Option | Meaning |
| --- | --- |
| `--device SELECTOR` | SDR selector; default `air8201b`. |
| `--demo` | Synthetic IQ. |
| `--center-mhz NUMBER` | Initial center frequency in MHz. |
| `--rate-msps NUMBER` | Initial sample rate in MS/s. |
| `--gain NUMBER` | Initial gain in dB. |
| `--nfft INTEGER` | FFT size. |
| `--rows INTEGER` | Waterfall history rows. |
| `--fps NUMBER` | Terminal refresh rate; default 3.0. |
| `--backend {calibrated,psd,quicklook,ssb}` | Spectrogram backend. |
| `--help`, `-h` | Print help. |

### Kiosk display

```sh
python3 live/striqt_kiosk.py --demo
python3 live/striqt_kiosk.py --device auto --port 8080
python3 live/striqt_kiosk.py --demo --no-kiosk -- --quantize --fps 10
```

`--device` and `--demo` go to the server. `--port` chooses the local server
port. `--no-kiosk` opens a normal browser window. Any arguments after `--` are
passed directly to `striqt_web_server.py`. Set `RADIO_KIOSK_BROWSER` to select
a browser executable when automatic browser discovery is unsuitable.

## Web server and launcher reference

### `striqt_web_server.py`

```text
python3 live/striqt_web_server.py [OPTIONS]
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--device SELECTOR` | `air8201b` | Radio selector. `auto` enumerates and requires exactly one radio. |
| `--demo` | off | Synthetic IQ; cannot be combined with an explicit non-demo device. |
| `--channels {1,2,3,4}` | discovered | Use the first N channels; creates N demo channels in demo mode. |
| `--ports LIST` | `auto` | Explicit non-negative RX port list, e.g. `0` or `0,1`; otherwise probe the device. |
| `--quantize` | off | Send uint8 waterfall frames, roughly four times smaller. |
| `--fps NUMBER` | `15` | Maximum broadcast frame rate. |
| `--backend {calibrated,psd,quicklook,ssb}` | `calibrated` | Spectrogram computation backend. |
| `--host ADDRESS` | `0.0.0.0` | Bind address. |
| `--port INTEGER` | `8000` | Listen port. |
| `--help`, `-h` | — | Print help. |

Examples:

```sh
python3 live/striqt_web_server.py --demo --channels 2 --quantize --fps 10
python3 live/striqt_web_server.py --device auto --ports 0,1
python3 live/striqt_web_server.py --device driver=plutosdr,serial=XYZ --backend psd
```

If `striqt.analysis` is absent in demo mode, calibrated-grid backends fall back
to `quicklook`. Real-radio mode exits when `striqt.sensor` cannot be imported.

### `run_web.sh`

```text
bash live/run_web.sh [--tunnel] [SERVER OPTIONS]
```

`--tunnel` is the only launcher-specific flag. Every other argument is passed
to the web server, so these are equivalent in intent:

```sh
bash live/run_web.sh --demo --fps 10 --quantize --channels 1
PORT=8080 bash live/run_web.sh --tunnel --device auto
```

The `PORT` environment variable chooses the port that the launcher passes to
the server (default 8000). If `cloudflared` is absent, `--tunnel` warns and
continues LAN-only. Stop the foreground launcher with `Ctrl-C`; it stops both
the server and tunnel it started.

## Control, recording, and qualification tools

### Browser roles

The browser login is username-only: the configured name selects a role and
there is no password. The installed defaults are `admin`, `viewer`, and
`intern`. Use the UI and role-specific controls for normal operation. The OPS
tab records validation, hardware readback, and the verdict of a setting change:
`mismatch` means the driver disagreed with the requested setting;
`unverified` means the driver could not return a readback.

### `radioctl.py`

Use this client against a running server:

```text
python3 live/radioctl.py [--url URL] [--user USER] COMMAND [COMMAND OPTIONS]
```

Global flags: `--url` defaults to `http://127.0.0.1:8000`; `--user` defaults
to `RADIOCTL_USER` if set. The supplied user must be a configured role name.

| Command | Options | Purpose |
| --- | --- | --- |
| `status` | none | Print current server/radio status. |
| `watch` | `--interval SECONDS` (default 1.0) | Repeatedly redraw status. |
| `logs` | `--interval SECONDS` (default 1.0) | Stream operation logs. |
| `self-test` | `--timeout SECONDS` (default 30.0) | Request and wait for server self-test. |
| `gps` | `--watch`, `--interval SECONDS` (default 2.0) | Show GPS data used to stamp recordings. |
| `set` | settings below | Apply capture/backend settings and wait for verification. |
| `tx status` | none | Report whether TX is active; exit 0 when active. |
| `tx stop` | none | Stop transmitting immediately. |
| `tx start` | TX settings below | Begin an explicitly authorized transmission. |

`set` flags are `--center-mhz NUMBER`, `--rate-msps NUMBER`, `--gain NUMBER`,
`--nfft INTEGER`, `--backend {calibrated,quicklook,psd,ssb}`, and
`--json JSON`. `--json` is a raw control payload merged last, so it can
override values assembled from the individual flags.

```sh
python3 live/radioctl.py --user admin status
python3 live/radioctl.py --url http://radio.local:8000 --user admin set \
  --center-mhz 2593 --gain 5 --backend psd
python3 live/radioctl.py --user admin gps --watch
python3 live/radioctl.py --user admin self-test --timeout 60
```

Transmit is potentially regulated and can radiate. `tx start` requires
`--i-have-a-license`; use a shielded/dummy load or a frequency for which you
are authorized. Its flags are:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--waveform {cw,two_tone,chirp,noise}` | `cw` | Generated waveform. |
| `--freq-mhz NUMBER` | required | TX center frequency in MHz. |
| `--offset-khz NUMBER` | `0` | Tone baseband offset from TX center. |
| `--gain NUMBER` | radio minimum | TX gain in dB. |
| `--amplitude NUMBER` | `0.5` | IQ amplitude, from 0 to 1 full scale. |
| `--seconds NUMBER` | unlimited | Stop automatically after this many seconds. |
| `--channel INTEGER` | `0` | TX channel. |
| `--i-have-a-license` | required | Acknowledges authorized/shielded operation. |

```sh
python3 live/radioctl.py --user admin tx start --waveform cw \
  --freq-mhz 2450 --seconds 5 --i-have-a-license
python3 live/radioctl.py --user admin tx stop
```

### Pull completed recordings

```text
python3 live/tools/pull_recordings.py [OPTIONS]
```

The tool only copies finished recordings to `--dest`; it does not upload or
publish them.

| Option | Default | Meaning |
| --- | --- | --- |
| `--url URL` | `http://127.0.0.1:8000` | Radio server URL. |
| `--user USER` | unset | Username selecting the role. |
| `--dest DIRECTORY` | `./radio-recordings` | Local mirror destination. |
| `--watch` | off | Keep polling and download new completed recordings. |
| `--interval SECONDS` | `15.0` | Poll interval with `--watch`. |
| `--list` | off | List available recordings and exit. |
| `--no-verify` | off | Skip per-archive CRC verification. |
| `--timeout SECONDS` | `30.0` | Per-request timeout. |

```sh
python3 live/tools/pull_recordings.py --url http://radio.local:8000 --user admin --list
python3 live/tools/pull_recordings.py --url http://radio.local:8000 --user admin \
  --dest ./captures --watch
```

Keep verification enabled unless diagnosing an integrity-check problem. Make
sure `--dest` has sufficient local storage before running `--watch`.

### Hardware qualification

```text
python3 live/tools/hardware_qual.py [OPTIONS]
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--device SELECTOR` | `air8201b` | Radio selector. |
| `--demo` | off | Exercise the harness with synthetic data. |
| `--quick` | off | Use fewer qualification points. |
| `--timeout SECONDS` | `20.0` | Per-point timeout. |
| `--tx` | off | Also test TX; this can radiate. |
| `--tx-freq-mhz NUMBER` | required with `--tx` | TX test frequency. |
| `--tx-seconds NUMBER` | `3.0` | Duration of a TX test point. |

```sh
python3 live/tools/hardware_qual.py --device auto --quick
python3 live/tools/hardware_qual.py --device auto --tx --tx-freq-mhz 2450 \
  --tx-seconds 3
```

TX qualification requires a dummy load or authorization to use the frequency.

### Sweep runner

```text
python3 live/sweep_runner.py SPEC --output OUTPUT [--duration SECONDS]
```

`SPEC` is the sweep specification path; `--output` is required; `--duration`
optionally limits capture time. Use the `striqt` documentation and example
YAML specifications for the expected specification schema.

## Configuration, roles, and service management

The installer writes service configuration under `/etc/radio-web/` and installs
the `radio-web` systemd unit. Inspect the effective configuration before
editing it, protect secrets, then restart the service after a supported change:

```sh
sudo systemctl cat radio-web
sudo systemctl restart radio-web
journalctl -u radio-web -n 100 --no-pager
```

Environment variables used by the project include:

| Variable | Purpose |
| --- | --- |
| `RADIO_MODE` | Service deployment mode. |
| `RADIO_PORT` | Service listen port. |
| `RADIO_DEVICE` | SDR selector for the installed service. |
| `RADIO_EXTRA_ARGS` | Extra server arguments used by the service launcher. |
| `RADIO_SESSION_SECRET` | Cookie-signing secret; required for a safe production/public deployment. |
| `RADIO_AUTH_DISABLE=1` | Disable authentication and make everyone an administrator. Local/demo only. |
| `RADIO_SERVICE_NAME` | Service name used by reset/management logic; default `radio-web`. |
| `RADIO_RECORDINGS_DIR` | Recording directory. |
| `RADIO_RECORDING_SETTLE_SEC` | Time allowed for recording finalization. |
| `RADIO_GPS_DEVICE` | GPS serial device; `none` skips GPS probing during setup. |
| `RADIO_GPS_HOST`, `RADIO_GPS_PORT` | GPSD endpoint overrides. |
| `RADIO_TX` | Enable/disable TX capability. Set `0` to remove TX controls. |
| `RADIO_TX_DEFAULT` | Installer-selected TX default. |
| `RADIO_RESET_LOG` | Reset-operation log path. |
| `RADIOCTL_USER` | Default `radioctl --user` value. |
| `RADIO_KIOSK_BROWSER` | Browser executable for kiosk mode. |
| `PORT` | Port consumed by `live/run_web.sh`, default 8000. |

For an ad-hoc local demo without browser authentication:

```sh
RADIO_AUTH_DISABLE=1 python3 live/striqt_web_server.py --demo --host 127.0.0.1
```

Never use that environment setting with a LAN-wide or tunnel-exposed listener.
For a manual authenticated invocation, provide an unguessable secret, for
example `RADIO_SESSION_SECRET="$(openssl rand -hex 32)"` in a protected service
environment file.

The Reset Radio UI action is admin-only. It needs the installed sudoers rule;
for a manual service user setup, run:

```sh
sudo bash live/install_radio_web_sudoers.sh <service-user>
```

## Troubleshooting

Start with the least invasive check:

```sh
curl http://localhost:8000/health
sudo systemctl status radio-web
journalctl -u radio-web -n 100 --no-pager
journalctl -u radio-web -f
python3 live/radioctl.py --user admin status
```

| Symptom | Checks and resolution |
| --- | --- |
| Installer rejects the host | Use a supported Debian/Raspberry Pi OS or Ubuntu release for the full installer. For a different platform, create the demo environment manually. |
| GPS setup fails because no serial device is found | Re-run the installer with `sudo env RADIO_GPS_DEVICE=none bash install_linda.sh`. Recordings then have `gps_valid=0`. |
| Browser says “waiting for first frame” | Follow `journalctl -u radio-web -f`; the operation log identifies the failing stage. Confirm the radio is attached and no other acquisition process owns it. |
| `--device auto` lists several radios | Specify the selector printed by discovery, such as `--device driver=...,serial=...`. |
| No radio is discovered | Run `SoapySDRUtil --find`; reconnect/power the radio; then re-run setup so the matching driver is installed. |
| Real server reports that `striqt.sensor` is not importable | Verify demo first with `--demo`; then use the supported installer or the correct AIR-T `striqt`/pixi environment. |
| Settings seem ignored | Review the OPS tab. `mismatch` is a driver readback disagreement; `unverified` means no readback was available. |
| Reset Radio fails immediately | Install the sudoers rule with `sudo bash live/install_radio_web_sudoers.sh <service-user>` and confirm `RADIO_SERVICE_NAME` matches the unit. |
| Port is already in use | Check `sudo ss -ltnp 'sport = :8000'`; stop the conflicting process or choose a different `--port`. The installer permits the existing `radio-web` service to own its configured port on a safe re-run. |
| `.local` hostname does not resolve | Confirm the service is running; use the host IP temporarily; inspect `avahi-daemon` on installer-managed hosts. |
| `run_web.sh --tunnel` stays LAN-only | Install `cloudflared` and put it on `PATH`; the launcher warns and intentionally continues without the tunnel if it is missing. |
| Browser login fails or is unsafe | Use a configured username exactly; do not expose a service with `RADIO_AUTH_DISABLE=1`; set a strong `RADIO_SESSION_SECRET`. |
| Recordings do not transfer | Confirm the server and role login, run `pull_recordings.py --list`, check the target directory and free space, and retry with CRC verification enabled. |
| A recording appears incomplete | Wait for it to be marked finished; increase/inspect `RADIO_RECORDING_SETTLE_SEC`; use the pull tool’s default CRC verification. |
| Transmit controls are unavailable | Check server startup output and `RADIO_TX`; only a radio with TX support can transmit. Demo TX is simulated. |
| Transmit command is refused | Supply `--i-have-a-license`, a required `--freq-mhz`, and use a shielded load or authorized frequency. |
| Kiosk opens the wrong browser or none | Set `RADIO_KIOSK_BROWSER` to the intended browser executable, then run the kiosk command again. |
| Need reproducible diagnostics | Save the installer transcript, `systemctl status`, the last 100 journal lines, server command/flags, radio selector, and the exact observed error. |

Run every CLI’s authoritative help for the installed revision:

```sh
bash install_linda.sh --help
bash uninstall_linda.sh --help
python3 live/striqt_web_server.py --help
python3 live/radioctl.py --help
python3 live/tools/pull_recordings.py --help
python3 live/tools/hardware_qual.py --help
python3 live/striqt_standalone_terminal.py --help
python3 live/striqt_kiosk.py --help
```

## Removal

The uninstaller removes LINDA's service, environment, configuration, driver
packages tracked as installed by setup, and related host integration. It does
not remove this Git clone, change the machine hostname, or modify `striqt/`.
It shows a plan before changes unless `--yes` is supplied.

```text
sudo bash uninstall_linda.sh [OPTIONS]
```

| Option | Meaning |
| --- | --- |
| `--yes`, `-y` | Do not prompt for confirmation. |
| `--dry-run`, `-n` | Show the removal plan without changing the system. |
| `--keep-packages` | Do not remove any apt packages. |
| `--purge-recordings` | Delete captured recordings. This is irreversible; copy data first. |
| `--purge-desktop` | Also remove installer-managed Chromium/X/Openbox/LightDM packages. |
| `--purge-network` | Also remove installer-managed NetworkManager and Avahi packages. |
| `--purge-groups` | Remove the user from `plugdev`/`dialout` groups. |
| `--purge-pip-cache` | Delete root's pip wheel cache. |
| `--help`, `-h` | Print help. |

Recommended first step:

```sh
sudo bash uninstall_linda.sh --dry-run
```

Then use the narrowest command that meets the goal:

```sh
sudo bash uninstall_linda.sh
sudo bash uninstall_linda.sh --keep-packages
sudo bash uninstall_linda.sh --purge-recordings
```

`--purge-recordings` is destructive. Copy recordings off the host before using
it, or omit the flag to preserve them.

## Additional references

- [Root README](../README.md) — concise overview and quick start.
- [Repository overview](REPO_OVERVIEW.md) — codebase architecture and source map.
- [striqt README](https://github.com/usnistgov/striqt/blob/main/README.md) — upstream package install and CLI notes.

