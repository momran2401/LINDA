# striqt UI Expansion Implementation Log

Started: 2026-07-22 (America/Denver)

Scope approved by the user:

1. Stabilize and expand browser recording.
2. Add PSS/SSS cell detection and a cell-summary panel.
3. Add native channel-power RMS/peak time series.
4. Surface calibration state and honest physical units.
5. Add histogram/occupancy analysis.
6. Preserve and export richer striqt metadata.
7. Add server-owned analysis presets.

Constraints:

- `striqt/` is upstream and remains read-only.
- No commits or pushes are made by Codex.
- The pre-existing edit in `live/web/app.js` (clearing stale hidden sweep
  settings after a live config seed) is user work and will be preserved.

## 2026-07-22 — Baseline and design decisions

- Confirmed that browser recording is a real `striqt.sensor.iterate_sweep`
  pipeline producing Zarr, not a display-only capture.
- Found that hardware recording reuses the live AIR-T source, whose setup spec
  is gapless and NumPy-backed. `sweep_runner.run_sweep` replaces the YAML source
  with that setup spec, so a YAML request for CuPy does not describe the
  effective runtime. This can leave a continuously active DMA stream unread
  while foreground analysis and sink work finish, consistent with the reported
  overflow after pipeline warm-up.
- Implementation direction: keep all compatibility and orchestration changes
  in `live/`; make the effective backend visible; add durable-write telemetry
  and transactional output; separate live structured analysis results from the
  high-rate waterfall frame contract.

## 2026-07-22 — Implemented shared measurements and provenance

- Added `live/core/insights.py`, a thread-safe low-rate result service invoked
  by the existing compute worker (never by the DMA drain thread).
- Added native `striqt.channel_power_time_series` RMS/peak results and
  `striqt.channel_power_histogram` occupancy. Occupancy is explicitly defined
  as the fraction of detector readings at or above the disclosed threshold.
- Added optional, cadence-limited PSS and SSS correlation. Results are labelled
  as candidates and PCI is intentionally not invented when the public raw-array
  call does not expose an unambiguous NID1 coordinate.
- Added calibration-file existence, SHA-256, timestamp, state, and unit
  provenance. Even with a valid file, the direct-stream live path remains
  labelled `dB relative` because it does not construct `AcquiredIQ` and run
  `correct_iq`; the UI reports that calibration is configured for recording
  but not applied live. It does not claim dBm merely because the striqt
  spectral backend is selected.
- Added `/insights`, richer JSON export, and a browser MEASURE panel.
- Demo verification exposed striqt's exact detector-bin divisibility contract;
  detector periods now snap to the greatest sample-count divisor shared by the
  frame and the requested 1 ms period, and the executed period is disclosed.

## 2026-07-22 — Implemented presets

- Added versioned server-owned presets in `live/core/presets.py` and guarded
  list/apply endpoints. Presets apply through `SharedConfig.update`, retaining
  existing validation, operation logging, hardware readback, and fresh-frame
  verification. The 5G preset also enables low-rate PSS/SSS candidate analysis.

## 2026-07-22 — Recording reliability and product surface

- Added an explicit `prepare_retrigger()` boundary after each complete finite
  sweep pipeline step. This disables the reused gapless stream while analysis
  and sink work are between reads; the next striqt acquisition trigger enables
  it again. The purpose is to prevent unread XDMA accumulation that previously
  surfaced after pipeline warm-up.
- Report the effective array backend and gapless state rather than claiming the
  generated YAML's CuPy request survived reuse of the live NumPy source.
- Added pipeline-step timing/status fields.
- Recording now targets a `.partial.zarr.zip`, validates the ZIP CRC and entry
  count, and atomically renames it to the final path only on success. Failed
  partial output remains available for diagnosis.
- Added selectable spectrogram/PSD/channel-power analyses, capture length, and
  a read-only recording catalog with integrity state, bounded member
  inspection, and root-confined archive downloads.

## 2026-07-22 — Verification

- `python3 -m py_compile live/core/*.py live/sweep_runner.py live/striqt_web_server.py`
  passed.
- `node --check live/web/app.js` passed.
- `python3 -m pytest live/tests/ -q` passed, including expanded authenticated
  endpoint, native measurement, preset authorization, recording-product
  selection, and catalog traversal tests.
- `git diff --check` passed.
- A running authenticated-off demo server returned native channel-power shape
  `[2, 2, 4]` and occupancy-histogram shape `[2, 2, 93]` from `/insights`.
- Confirmed `git status --short striqt` is empty: upstream `striqt/` was not
  modified.

## Required hardware qualification

The AIR8201B overflow fix cannot be proven in the demo environment. On the
radio, run recordings at every supported sample rate/channel count, beginning
with the previously failing recipe, and require at least ten minutes at the
maximum sustained configuration. Confirm: no overflow, monotonically growing
durable capture count, valid finalized archive, and successful automatic live
resume. Effective backend, gapless state, pipeline steps, and mean step time are
now visible in recording status to support that qualification.
