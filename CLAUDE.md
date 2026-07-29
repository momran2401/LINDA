# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

NIST SURF project: live two-channel RF visualization for SDRs (Deepwave
AIR8201B primary; PlutoSDR and generic SoapySDR supported). Project code lives
in `live/`. The `striqt/` subdirectory is an upstream NIST library (Dr. Dan
Kuester & Aric Sanders) included as a dependency — treat it as **read-only**
unless explicitly told otherwise.

> **`striqt/` is NOT the striqt that runs on the radio.** `setup.sh` installs a
> pinned, radio-verified v0.7.0 (`STRIQT_COMMIT` = `2e7696d`); the vendored
> directory is a *later* snapshot with a different source API — `arm_spec`,
> `_read_stream`, `setup_spec`, `RxStream.open`, and one-step `from_spec()`
> construction all exist only in the installed build. **Never infer runtime
> behaviour from `striqt/`.** Check `INSTALLED_STRIQT_API.txt` (root) for the
> divergence table, or read the pinned commit directly. Writing code against
> the vendored tree has already caused two silent production bugs — see the
> recording notes below.

## Architecture (2026-07 refactor)

All radio/DSP/config logic is in the shared package **`live/core/`**; the
scripts in `live/` are thin frontends over it. **Never fix a backend bug in a
frontend script — fix it once in `live/core/`.**

- `core/constants.py` — profiles (`DEVICE_PROFILES`), rates/nfft grids, defaults
- `core/state.py` — runtime device/channels/backend/fps; set once at startup via
  `state.configure_device()` etc., read at call time everywhere
- `core/striqt_compat.py` — defensive striqt imports + AIR-T pixi libstdc++ re-exec
  (must be imported first; `import core` guarantees it)
- `core/parsing.py` — freedom-model parsers, `ANALYSIS_TARGETS`, striqt scratch validators
- `core/config.py` — `RadioConfig` + `SharedConfig` (tier-1 clamp/snap, tier-2
  scratch probe, tier-3 compute backstop). `update()` returns the ack **plus
  `op_id`**; `take_dirty()` returns `(dirty, cfg, op_id, reconnect, changed_fields)`
- `core/dsp.py` — quicklook/calibrated/psd/ssb backends, `aligned_nfft` grid,
  `build_header` (frame-header contract)
- `core/devices/` — adapter layer: `resolve_device(selector)` handles
  `air8201b|air7201b|air7101b|pluto|soapy|demo|auto|driver=X,serial=Y`
  (SoapyAIRT rows are refined to the Deepwave model via `identify_deepwave`);
  adapters expose `create_source(source_config)`, `read_back()`,
  `hardware_expectations()` (accounts for striqt's intentional lo_shift LO
  offset + backend_sample_rate before judging readback), `verify()`,
  `describe_capabilities()`; `probe_channels()` discovers the real RX count.
  `_probe_device_facts()` briefly opens each enumerated radio to read its RX
  channel count **and its master clock rate** — striqt applies
  `master_clock_rate` verbatim (`setMasterClockRate`), so a value the radio
  cannot run stops it opening at all. Never hand a radio a clock from another
  radio's profile: a USRP B2xx rejects the AIR-T's 125 MHz outright.
- `core/acquisition.py` — `Acquirer` (ring buffer + rearm + readback),
  `Computer`, `DemoAcquirer` (demo tones are fixed *stations*: they move across
  the band on retune, so tuning is testable without hardware)
- `core/operations.py` — `OPERATIONS` log: every radio-affecting change is an
  operation `requested → validated → applying → applied → readback → data-path
  → verdict {success|verified|unverified|mismatch|failed}`; events stream to
  stdout, `/operations`, and WS `{"op": ...}` messages
- `core/health.py` — `BOOT_ID` (restart proof), `health_snapshot()`
- `core/serialization.py` — `serialize_frame` / `parse_frame`

Frontends: `striqt_web_server.py` (canonical web UI; auth + routes + WS only),
`striqt_kiosk.py` (web UI fullscreen locally), `striqt_standalone_terminal.py`
(curses over SSH). Every live frontend goes through `live/core/`; the kiosk is
the standalone (it launches the web server + a fullscreen browser).

**`live/legacy/` is frozen — do not extend or fix.** It holds the four pre-`core`
scripts (`striqt_standalone.py`, `pluto_standalone.py`, `striqt_server_TCP.py`,
`striqt_frontend_TCP.py`), each with its own duplicated backend that imports
`striqt` directly. Nothing imports or launches them. Fix bugs in `live/core/`
instead — see `live/legacy/README.md`.

## Running

```sh
python3 live/striqt_web_server.py --demo            # no hardware, http://localhost:8000
python3 live/striqt_web_server.py                   # AIR8201B
python3 live/striqt_web_server.py --device auto     # SoapySDR enumeration
python3 live/striqt_web_server.py --ports 0         # explicit RX port list
bash live/run_web.sh [--tunnel]                     # launcher (tunnel optional)
python3 live/striqt_kiosk.py --demo                 # local fullscreen browser
python3 live/striqt_standalone_terminal.py --demo --backend quicklook
python3 live/radioctl.py status                     # SSH client for a RUNNING server
sudo bash setup.sh                                  # full installer + TUI
```

Copying recordings off the radio (both run on YOUR machine, never on the radio):

```sh
bash live/tools/fetch_recordings.sh user@radio.local --dest ~/data/radio
bash live/tools/fetch_recordings.sh user@radio.local --watch
python3 live/tools/pull_recordings.py --url <tunnel-url> --user admin --watch
```

## Tests

```sh
cd live && python3 -m pytest tests/     # unit + fake-radio pipeline (no hardware)
python3 live/tools/hardware_qual.py --device auto   # ON the radio host: real readback qual
cd striqt && pytest tests/              # upstream library tests
```

The demo pipeline tests use the quicklook backend so they pass without striqt.
They also run ON the radio host (Python 3.9 + installed striqt): striqt_compat
skips its LD_LIBRARY_PATH re-exec under pytest (execv used to kill the runner
silently mid-collection), and RecordingManager creates its asyncio primitives
lazily because 3.9 binds them to the current event loop at construction.
`radioctl.py self-test` qualifies settings THROUGH a running server and
restores the starting configuration afterwards.

The web UI is CDN-free: uPlot is vendored in `live/web/vendor/` (setup.sh
re-fetches it if missing) so hotspot/ethernet modes work fully offline.

## AHAWI mode (coherent capture → segmented replay)

- Third display mode next to Boring/Cool: the Computer grabs one CONTIGUOUS
  multi-segment chunk from the ring (`segments × duration`, ~100 ms), analyzes
  it in ONE striqt pass, and ships it as a single frame with `header.ahawi`
  geometry; the CLIENT replays it one viewing window at a time
  (play/pause/step/scrub/dwell — all client-side, whitelisted for read-only
  roles). The rolling modes recompute per display tick, so TDD slots/SSB
  bursts swim; AHAWI's segments are phase-coherent, so they hold still.
- Segment length = the existing `duration` control. `dsp.ahawi_plan()` owns the
  geometry (hop-exact rows per segment, ring-fit clamps, +1 segment of slack
  for alignment); the frame header discloses the EXECUTED spans.
- Each capture runs the full striqt measurement BUNDLE over the trimmed
  (displayed) span — spectrogram + power_spectral_density statistics +
  channel_power_time_series, mirroring the recorder — on cupy when present
  (`dsp._run_array_fn`: any GPU failure falls back to numpy and disables cupy
  for the process). `header.ahawi.measurements` lists exactly what ran;
  `compute_backend` says where. The client renders the striqt PSD stats in
  the PSD pane (float precision, bypasses wire quantization) and drives the
  power strip from the channel-power series; both fall back to client-side
  derivations when the bundle is absent (striqt-less hosts).
- Burst alignment (`ahawi_align_offset`): subtract each bin's stationary power
  (median over time) so constant carriers can't bury the burst, fold the
  RESIDUAL per-row power at the segment period, shift so the burst (e.g. 20 ms
  5G SSB at 3750 MHz) sits at the same row in every segment. Reports
  `aligned=false` + `align_contrast_db` on flat spectra instead of aligning to
  noise — the badge explains the verdict either way.
- AHAWI capture knobs (capture length / segment duration / burst align) are
  STAGED in the UI and shipped together by the mode's Apply button; any
  applied config change marks the replayed capture stale ("recapturing…") and
  the next capture loads immediately, jumping the queue/pause hold.
- Honesty: color scale pinned per capture (client); `coherent=false` flags a
  drain gap inside the capture (`Acquirer.last_gap_time`); backend fallback is
  disclosed via `backend`/`backend_requested`. AHAWI wraps calibrated/quicklook
  only — PSD/SSB bypass it (client hints instead of silently ignoring).
- AHAWI frames are ALWAYS quantized (uint8 + disclosed scale) regardless of
  --quantize: a float32 multi-segment capture is ~12 MB per message. The
  broadcaster copies blocks only for NEW frames (`latest_if_newer`); /health
  reads `latest_header()` — never copy a capture to read a timestamp.
- Demo: `DEMO_BURST` is a fake SSB (20 ms period) gated by a wall-clock
  sample counter, so it honestly swims in Cool mode and pins in AHAWI. Tone
  phase is computed as fractional cycles mod 1 in float64 — an absolute
  float32 time axis scrambles MHz-tone phase within a minute of uptime.
  `compute_blocks` substitutes quicklook (disclosed) when striqt is absent —
  a striqt-less host must never freeze in a compute-error loop.

## GPS position in recordings (`core/gps.py`)

- The radios run gpsd on localhost:2947. `GpsReader` is a stdlib socket client
  (the pixi env has NO python `gps` module) held process-wide; it reconnects
  with backoff and tracks fix staleness. gpsd 3.17 emits `alt`; 3.20+ splits it
  into `altMSL`/`altHAE` — all three are accepted.
- `gps_peripherals_class()` builds the striqt `Peripherals` subclass that
  `sweep_runner` uses in place of `NoPeripherals`. striqt merges `acquire()`'s
  dict into each capture's `extra_data`, which becomes per-capture variables in
  the archived Dataset (`compute/datasets.py` broadcasts scalars along the
  capture dim). Fields: `gps_{latitude_deg,longitude_deg,altitude_m,fix_mode,
  satellites_used,time_unix,fix_age_s,error_horizontal_m,error_vertical_m,valid}`.
- **No fix ⇒ NaN + `gps_valid=0`, never 0.0/0.0** — null-island coordinates in a
  research dataset are worse than an honest gap. A 2-D fix records position but
  NaN altitude. `acquire()` only reads a cached snapshot, so a wedged receiver
  can never slow or fail a sweep.
- `GET /gps`, `radioctl.py gps` (exit 0 only on a real fix — scriptable), and
  the Record tab all show the live fix and name WHY it isn't valid: daemon
  unreachable / no device attached / no fix yet / stale. The reader starts with
  the server, not on first request. `RADIO_GPS=0` disables the integration;
  `RADIO_GPS_HOST`/`RADIO_GPS_PORT` relocate gpsd.
- `setup.sh install_gps()` provisions gpsd on a FRESH host: installs it, probes
  `ttyACM*`/`ttyUSB*` for a device actually emitting NMEA (never claims an
  Arduino or FTDI cable found on the same port class), binds it in
  `/etc/default/gpsd` so it survives reboot, and warns without failing setup
  when there is no receiver. `RADIO_GPS_DEVICE=/dev/ttyTHS1 bash setup.sh`
  names a UART-wired module explicitly.
- Recordings therefore embed the precise site location — relevant to any
  data-release decision (see the recordings note below).

## Recording (Record tab → `core/recording.py` → `sweep_runner.py`)

- The live viewer opens the radio **gapless** (`core/devices/sources.py`), where
  striqt treats any receive overflow as fatal and forbids receive retries. A
  recording sweep analyzes and archives *between* captures, so the stream
  overflows in those gaps by construction. `core.shims.finite_capture_mode()`
  swaps the source to `gapless=False, receive_retries=2` for the sweep and
  restores the live spec on exit; `sweep_runner` also disables the RX stream
  after each pipeline step. Without both, recording dies after ~1 capture.
- Any spec handed to a sweep must be registered in striqt's spec→source map
  (`finite_capture_mode` does this) or sink path formatting blocks, then raises.
- The sweep runs **in-process** on the live source object: AIR-T retains FPGA
  descriptors for the process lifetime, so a subprocess cannot acquire it.
- `Acquirer._resume_rearm()` retries on the SAME source for Deepwave models.
  Never call `close_source()` on an AIR-T to recover — it deinitializes the
  AD9371 management sensors for the rest of the process and the viewer stays
  dark until a service restart.
- Recordings stay on the radio under `recordings/` (gitignored — capture output
  is never source, and this repo's remote is a personal GitHub account). Two
  workstation-side pullers mirror them off; both are one-way and never delete:
  - `tools/fetch_recordings.sh` — rsync over SSH. The default. Incremental and
    resumable, no size limit, no auth beyond your existing SSH access.
  - `tools/pull_recordings.py` — same job over the authenticated `/recordings`
    HTTP endpoints, stdlib-only. Use it when SSH can't reach the radio but the
    `run_web.sh --tunnel` URL can.

  Both skip `*.partial.zarr.zip`: the server renames a recording to its final
  name only after validating it, so a skipped partial is a recording still
  being written. Do NOT commit archives or add an upload path without a
  data-release decision.

## Verified operations / Reset Radio

- Config changes are only trusted after driver readback + a fresh frame; the
  verdict is in the op log (terminal + web OPS tab). Readback is judged
  against `hardware_expectations()` (striqt's programmed LO/rate), so
  lo_shift/backend_sample_rate never false-mismatch. Demo devices report
  `success` with "no hardware readback" honestly.
- Source-spec fields (`{"source": {...}}`) genuinely APPLY via a verified
  device reconnect (`take_dirty` returns a 4-tuple with the reconnect flag;
  the Acquirer closes + reopens with `cfg.source_config` overrides, filtered
  by the spec class's `__struct_fields__`). Explicit JSON null CLEARS an
  override; a failing source config auto-reverts to the last-good set.
- `POST /admin/reset-radio` PREFLIGHTS the sudoers rule (`sudo -n -l`),
  writes stderr to RADIO_RESET_LOG (persistent under systemd), and returns
  `{op_id, boot_id}`; the browser polls `/health` until `boot_id` changes.
- `POST /config` is the HTTP twin of the WS control path (admin only) —
  `live/radioctl.py` uses it for `set` and the reversible `self-test`.
- `/ws/logs` (admin only) streams the journalctl tail into the OPS tab —
  a log view, never a shell.

## Auth & deployment

- Three roles (`admin`/`viewer`/`interns`); only admin mutates config. Entering
  the configured username selects its role; there is no password. Browser auth
  uses a signed cookie (`/login` form); Basic username auth remains accepted
  for curl/API (`curl -u admin:`).
  `RADIO_AUTH_DISABLE=1` for demo/dev.
- Production: `setup.sh` configures role usernames and generates
  `RADIO_SESSION_SECRET` into
  `/etc/radio-web/radio.env` (0600) and installs the systemd unit
  (`deploy/radio-web.service.template` → `deploy/run_service.sh`, mode from
  `RADIO_MODE`: web/hotspot/ethernet/kiosk).
- `/health` is auth-exempt but returns only `{status, boot_id, uptime_s}` to
  anonymous callers.

## Frontend (live/web/)

- DAN (`pro`) / ARIC (`noob`) are CSS-visibility modes; both tune through the
  same server path. Historical bug: `collectSettings()` targeted a nonexistent
  `#settings-editor`, so DAN's Apply sent an empty payload — fixed to the real
  form containers; keep selectors in sync with index.html.
- Capture form shows MHz / MS/s (converted to Hz on send via
  `FIELD_UNITS`/`dataset.unitScale`); Apply sends only fields changed vs the
  last server seed (`formBaseline`).
- PSD (uPlot): wheel zoom / drag pan / Shift-drag box zoom / Alt-drag band
  selection / double-click reset; zoom survives frames via
  `setData(data, psdZoomX === null)`.
- Rail nav is a collapsing tab bar: a 38 px `.rail-strip` names the active tool
  (label + one tick per visible tab + chevron) and the 3-column `.rail-menu`
  drops over the panel on hover/click, collapsing again on select. Tabs keep
  the `.rail-tab[data-tab=…]` hooks app.js binds to and stay plain divs so the
  read-only guard never blocks them. `.rail-menu` sits at `top: calc(100% - 1px)`
  so each cell's `border-top` merges with the strip's bottom border instead of
  double-ruling — that keeps row separators right however many tabs a mode hides.
- OPS rail tab renders `{"op": ...}` WS events + `/operations` backfill.
- Waterfall panes are cloned per header `channels`; one channel = full width.
- Read-only roles: `SAFE_SELECTOR` whitelist in app.js gates what they may touch.

## Key constants (core/constants.py)

| Constant | Value | Meaning |
|---|---|---|
| `MAX_TAIL` | `1 << 22` | Ring buffer capacity (4M samples) |
| `READ_SIZE` | `1 << 18` | Chunk size per `_read_stream` call |
| `RATES_HZ` | 3.84/7.68/15.36/30.72 MS/s | LTE/5G-NR grid; incoming rates snap to this |
| `NFFT_CHOICES` | 256…4096 | Valid FFT sizes; always snap |
| `ALIGNED_NFFTS` | 252/504/1008/2016/4032 | 28-multiples the calibrated STFT actually runs |
| `MASTER_CLOCK_RATE` | 125e6 | AIR-T reference clock. **Not** a universal default — each profile declares its own `master_clock_rate` |
| `SOAPY_FALLBACK_MASTER_CLOCK` | 61.44e6 | generic-SoapySDR clock used only when `_probe_device_facts` could not ask the driver |

## striqt.analysis spectrogram contract

`evaluate_spectrogram` sets `nfft = round(sample_rate / frequency_resolution)`
internally. To guarantee `spg.shape == (channels, rows, nfft)`, pass
`frequency_resolution = sample_rate / nfft` and `duration = rows * nfft /
sample_rate`. The calibrated path snaps FFT size to `aligned_nfft` (multiple of
28 → integer zero-fill at `window_fill = 15/28`; also multiple of 12 for
consistent bin-averaging).

## Historical docs

`docs/` (AUDIT_REPORT, FIXLOG, bug_report, REPO_*) documents the pre-refactor
single-file era. Still useful for rationale (LV-*/P*-* references in comments),
but line numbers and file layout there predate `live/core/`.
