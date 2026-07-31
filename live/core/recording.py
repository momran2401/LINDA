"""Recording orchestration for the web UI's Record tab.

Owns the `RecordingManager`, the backend behind `POST /record/start` and
friends: it releases the live radio from the running `Acquirer`, supervises a
striqt sweep (in-process for hardware, via `sweep_runner.run_sweep`; a fake
in-process loop for demo mode), validates the resulting Zarr-zip archive, and
hands the radio back to the live viewer.

The live view keeps the source open `gapless=True`, where striqt treats any
receive overflow as fatal. A recording sweep does analysis/archive work
*between* captures, so the RX stream necessarily overflows in those gaps —
`core.shims.finite_capture_mode()` (invoked from `sweep_runner`) swaps the
source to `gapless=False, receive_retries=2` for the duration of the sweep
and restores the live spec on exit. Recordings are written under
`recordings/` (gitignored; pulled off-radio by separate one-way tools, not
this module) as `<stamp>.partial.zarr.zip` and atomically renamed to
`<stamp>.zarr.zip` only after their CRC has been verified, so a `.partial`
file on disk always means a recording still in progress or abandoned
mid-write.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from . import state
from .dsp import aligned_nfft
from .operations import OPERATIONS

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECORDINGS_DIR = Path(
    os.environ.get("RADIO_RECORDINGS_DIR", REPO_ROOT / "recordings")
).expanduser()


class RecordingManager:
    """Single-recording-at-a-time supervisor bridging the live viewer and a sweep.

    One instance lives for the life of the web server. It tracks recording
    state in `self._status` (polled via `status()`/broadcast to the UI),
    drives the release/record/resume handoff with the shared `Acquirer`, and
    builds the striqt sweep spec (either a caller-supplied YAML or one
    synthesized from the shared live config via `_default_spec`). Only one
    recording may be `active()` at a time; `start()` enforces this under
    `self._lock`.
    """

    def __init__(self, acquirer, shared, *, demo=False):
        """Bind the manager to the live acquirer and shared config.

        Args:
            acquirer: The server's live `Acquirer`, whose radio handle is
                paused and released for the duration of a recording and
                resumed afterward.
            shared: The `SharedConfig` snapshot source used to seed capture
                defaults (`defaults()`) and the synthesized sweep spec
                (`_default_spec()`).
            demo: When True, skip real hardware acquisition and run a fake
                in-process capture loop (`_run_demo`) that writes a stub
                archive, so recording can be exercised without a radio.
        """
        self.acquirer = acquirer
        self.shared = shared
        self.demo = demo
        # asyncio primitives are created lazily in start(): on Python 3.9 (the
        # radio host) their constructors bind the CURRENT event loop, so a
        # plain synchronous construction — tests, tooling — raises "no current
        # event loop" whenever an earlier asyncio.run() cleared it. start()
        # always runs inside the server's loop, where creation is safe.
        self._lock = None
        self._task = None
        self._process = None
        self._stop = None
        self._thread_stop = threading.Event()
        self._status = {"state": "idle"}

    def status(self):
        """Return a shallow copy of the current recording status dict.

        Returns:
            dict: Snapshot of `self._status` (state, op_id, output path,
            capture/elapsed counters, etc.) safe for the caller to read
            without racing further mutation.
        """
        return dict(self._status)

    def active(self):
        """Report whether a recording is currently starting, running, or stopping.

        Returns:
            bool: True if `self._status["state"]` is one of
            `{"starting", "recording", "stopping"}`.
        """
        return self._status.get("state") in {"starting", "recording", "stopping"}

    def catalog(self, limit=100, *, verify=False):
        """Read-only recording inventory.

        Reads only each archive's central directory, so cost is independent of
        recording size. `verify=True` additionally CRC-checks every complete
        archive, which reads each one end to end — that belongs off the request
        path; use inspect() for a single recording instead. Without it `valid`
        stays None, meaning "not checked".
        """
        root = DEFAULT_RECORDINGS_DIR
        rows = []
        if not root.exists():
            return rows
        paths = sorted(root.rglob("*.zarr.zip"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for path in paths[:max(1, min(int(limit), 500))]:
            state_name = "partial" if ".partial.zarr.zip" in path.name else "complete"
            item = {"path": str(path), "name": path.name,
                    "id": str(path.relative_to(root)),
                    "state": state_name, "bytes": path.stat().st_size,
                    "modified_at": path.stat().st_mtime, "valid": None}
            if state_name == "complete":
                try:
                    with zipfile.ZipFile(path) as archive:
                        item["entries"] = len(archive.infolist())
                        if verify:
                            item["valid"] = archive.testzip() is None
                except (OSError, zipfile.BadZipFile):
                    item["valid"] = False
            rows.append(item)
        return rows

    def resolve_catalog_item(self, recording_id):
        """Resolve a catalog-relative recording id to a validated path on disk.

        Guards against path traversal (`../`) by requiring the resolved
        candidate to sit under `DEFAULT_RECORDINGS_DIR`, and only accepts
        complete `.zarr.zip` archives (never a `.partial.zarr.zip` still being
        written).

        Args:
            recording_id: Path of the recording relative to
                `DEFAULT_RECORDINGS_DIR`, as returned by `catalog()`.

        Returns:
            Path: The resolved, existing archive path.

        Raises:
            ValueError: If the resolved path escapes the catalog root.
            FileNotFoundError: If the path is not a file or does not end in
                `.zarr.zip`.
        """
        root = DEFAULT_RECORDINGS_DIR.resolve()
        candidate = (root / str(recording_id)).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("recording path escapes the catalog root")
        if not candidate.is_file() or not candidate.name.endswith(".zarr.zip"):
            raise FileNotFoundError("recording not found")
        return candidate

    def inspect(self, recording_id):
        """Return detail for a single recording, including a full CRC check.

        Unlike `catalog()` (which is cheap and skips CRC by default), this
        always reads the archive end to end via `ZipFile.testzip()` — it is
        meant for a single-recording detail view, not the list endpoint.

        Args:
            recording_id: Path of the recording relative to
                `DEFAULT_RECORDINGS_DIR`.

        Returns:
            dict: `id`, `bytes`, `valid` (bool CRC result), `entries` (member
            count), and `members` (up to the first 500 member filenames).
        """
        path = self.resolve_catalog_item(recording_id)
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            return {"id": str(path.relative_to(DEFAULT_RECORDINGS_DIR.resolve())),
                    "bytes": path.stat().st_size, "valid": archive.testzip() is None,
                    "entries": len(infos),
                    "members": [i.filename for i in infos[:500]]}

    def defaults(self):
        """Build the Record-tab's default form values from the live shared config.

        Returns:
            dict: `center_frequency`, `sample_rate`, `gain` (Hz/Hz/dB, mirrored
            from the live config), `capture_duration` (seconds; see NOTE
            below), `directory` (`DEFAULT_RECORDINGS_DIR`), and
            `include_raw_iq` (False by default).
        """
        cfg = self.shared.snapshot()
        # Rolling live view intentionally uses duration=0 (12-row chunks), but
        # the recorder still needs a finite acquisition.  Twenty milliseconds
        # keeps a two-channel AIR-T read below its DMA buffering limit; the old
        # 100 ms fallback could overflow mid-capture at 15.36 MS/s.
        capture_duration = float(cfg.duration) if float(cfg.duration) > 0 else 0.02
        return {
            "center_frequency": float(cfg.center),
            "sample_rate": float(cfg.sample_rate),
            "gain": float(cfg.gain),
            "capture_duration": max(capture_duration, 0.001),
            "directory": str(DEFAULT_RECORDINGS_DIR),
            "include_raw_iq": False,
        }

    async def start(self, request):
        """Validate a recording request, reserve the output path, and launch it.

        Refuses to start a second recording while one is `active()`. Computes
        a timestamped output path under `directory`/`radio_id`, refusing to
        overwrite an existing final or in-progress (`.partial.zarr.zip`)
        archive, then hands off to `self._run()` as a background asyncio task
        and returns immediately with the initial status.

        Args:
            request: Dict of recording options from the client — any of
                `duration` (seconds, blank/None for unbounded), `directory`,
                `radio_id`, plus whatever `_default_spec()`/advanced `yaml`
                consumes.

        Returns:
            dict: The initial `status()` snapshot (state `"starting"`).

        Raises:
            RuntimeError: If a recording is already active.
            ValueError: If `duration` is present and not positive.
            FileExistsError: If the computed output or working path already
                exists on disk.
        """
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self.active():
                raise RuntimeError("a recording is already running")
            duration = request.get("duration")
            duration = float(duration) if duration not in (None, "") else None
            if duration is not None and duration <= 0:
                raise ValueError("duration must be positive or blank")
            directory = Path(request.get("directory") or DEFAULT_RECORDINGS_DIR).expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            radio_id = str(request.get("radio_id") or state.DEVICE).replace("/", "_")
            output = directory / radio_id / f"{stamp}.zarr.zip"
            # Keep the final `.zip` suffix: this striqt release selects its ZIP
            # wrapper from the path suffix even though the inner Zarr store is
            # configured as a directory.
            working = output.with_name(output.name.removesuffix(".zarr.zip")
                                       + ".partial.zarr.zip")
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists():
                raise FileExistsError(f"recording output already exists: {output}")
            if working.exists():
                raise FileExistsError(f"recording work output already exists: {working}")
            op_id = OPERATIONS.begin("record", f"record sweep to {output}")
            self._stop = asyncio.Event()
            self._thread_stop = threading.Event()
            self._status = {"state": "starting", "op_id": op_id,
                            "output": str(output), "started_at": time.time(),
                            "working_output": str(working),
                            "duration": duration, "captures": 0, "elapsed_s": 0.0,
                            "phase": "releasing the live radio"}
            self._task = asyncio.create_task(
                self._run(request, output, working, duration, op_id), name="radio-recording")
            return self.status()

    async def stop(self):
        """Request a cooperative stop of the active recording, if any.

        Sets both the asyncio (`self._stop`) and thread (`self._thread_stop`)
        stop signals so the running task/thread notice on their next
        cooperative check, and signals `SIGINT` to a live subprocess handle if
        one is tracked. Does not block for the recording to actually finish —
        `_run()` observes the flags and settles asynchronously.

        Returns:
            dict: The current `status()` snapshot (state `"stopping"`, or
            unchanged if no recording was active).
        """
        if not self.active():
            return self.status()
        self._status["state"] = "stopping"
        self._stop.set()
        self._thread_stop.set()
        proc = self._process
        if proc is not None and proc.returncode is None:
            proc.send_signal(signal.SIGINT)
        return self.status()

    async def shutdown(self):
        """Stop any active recording and wait (up to 15s) for it to settle.

        Intended for server shutdown: unlike `stop()`, this blocks until the
        background `_run()` task finishes or the timeout elapses, swallowing
        any exception/timeout so shutdown always proceeds.
        """
        await self.stop()
        if self._task:
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(self._task, 15)

    def _default_spec(self, request, output):
        """Synthesize a striqt sweep YAML spec from the request and live config.

        Used when the client does not supply an advanced `yaml` override.
        Mirrors the live shared config's capture geometry (frequency, rate,
        gain, spectrogram/PSD windowing) into a `sensor.read_yaml_spec`-
        compatible document, selecting `sensor_binding`/`array_backend`
        appropriately for demo vs. hardware mode, and restricts the analysis
        block to whichever of spectrogram/psd/channel_power the caller
        requested (defaulting to all three).

        Args:
            request: The recording request dict (see `start()`); reads
                `center_frequency`, `sample_rate`, `gain`, `analyses`,
                `include_raw_iq`, `capture_duration`.
            output: Destination path written into the spec's `sink.path`.

        Returns:
            str: A complete YAML sweep spec.
        """
        cfg = self.shared.snapshot()
        center = float(request.get("center_frequency", cfg.center))
        sample_rate = float(request.get("sample_rate", cfg.sample_rate))
        gain = float(request.get("gain", cfg.gain))
        requested_analyses = set(request.get("analyses") or
                                 ("spectrogram", "psd", "channel_power"))
        raw = "\n  iq_waveform: {}" if request.get("include_raw_iq") else ""
        freq_res = sample_rate / max(aligned_nfft(int(cfg.nfft)), 1)
        capture_duration = max(
            float(request.get("capture_duration") or cfg.duration or 0.02),
            0.001,
        )
        ports = ", ".join(str(p) for p in state.CHANNELS)
        backend = "numpy" if self.demo else "cupy"
        binding = "noise" if self.demo else (state.DEVICE if state.DEVICE.startswith("air") else "air8201b")
        source_extra = "\n  num_rx_ports: %d" % len(state.CHANNELS) if self.demo else ""
        capture_extra = "\n    noise_psd: 1e-17" if self.demo else f"\n    center_frequency: {center!r}\n    gain: {gain!r}"
        analysis_lines = []
        if "spectrogram" in requested_analyses:
            analysis_lines.append(f'''  spectrogram:
    frequency_resolution: {freq_res!r}
    fractional_overlap: {str(cfg.fractional_overlap)!r}
    window_fill: {str(cfg.window_fill)!r}
    window: {json.dumps(cfg.window)}
    trim_stopband: {str(bool(cfg.trim_stopband)).lower()}''')
        if "psd" in requested_analyses:
            analysis_lines.append(f'''  power_spectral_density:
    frequency_resolution: {freq_res!r}
    fractional_overlap: {str(cfg.psd_fractional_overlap)!r}
    window_fill: {str(cfg.psd_window_fill)!r}
    window: {json.dumps(cfg.psd_window)}
    trim_stopband: {str(bool(cfg.psd_trim_stopband)).lower()}
    time_statistic: {json.dumps(list(cfg.psd_time_statistic))}''')
        if "channel_power" in requested_analyses:
            analysis_lines.append(f'''  channel_power_time_series:
    detector_period: 0.01
    power_detectors: [rms, peak]{raw}''')
        elif raw:
            analysis_lines.append("  iq_waveform: {}")
        analysis_yaml = "\n".join(analysis_lines) or "  iq_waveform: {}"
        return f'''sensor_binding: {binding}
source:
  master_clock_rate: 125e6
  array_backend: {backend}{source_extra}
captures:
  - port: [{ports}]
    duration: {capture_duration!r}
    sample_rate: {sample_rate!r}
    backend_sample_rate: {float(cfg.backend_sample_rate or sample_rate)!r}
    analysis_bandwidth: {float(cfg.analysis_bandwidth)!r}
    lo_shift: {str(cfg.lo_shift)!r}
    host_resample: {str(bool(cfg.host_resample)).lower()}{capture_extra}
analysis:
{analysis_yaml}
sink:
  path: {json.dumps(str(output))}
  store: zip
'''

    async def _run(self, request, output, working, duration, op_id):
        """Drive one recording end to end: release, capture, validate, resume.

        Runs as the background task created by `start()`. Sequence: pause and
        release the live `Acquirer`'s radio handle (fatal if it doesn't
        release within 15s), let hardware handles settle
        (`RADIO_RECORDING_SETTLE_SEC`, skipped in demo mode), write the sweep
        spec to a temp YAML file, run the sweep (`_run_hardware` or
        `_run_demo`) into the `.partial.zarr.zip` working path, verify the
        resulting archive is non-empty and CRC-clean, and atomically rename it
        to the final output path. The live radio is always resumed in the
        `finally` block regardless of outcome, and the operation is recorded
        in `OPERATIONS` throughout.

        Args:
            request: The original recording request dict from `start()`.
            output: Final archive path (only used after validation).
            working: `.partial.zarr.zip` path the sweep writes into.
            duration: Requested capture duration in seconds, or None for
                unbounded (stopped only via `stop()`).
            op_id: Operation id from `OPERATIONS.begin()`, staged/finished
                here to drive the OPS log and UI status.
        """
        spec_path = None
        terminal = "success"
        detail = "recording completed"
        try:
            OPERATIONS.stage(op_id, "applying", "stopping live acquisition and releasing radio")
            released = await asyncio.to_thread(self.acquirer.pause_and_release, 15.0)
            if not released:
                raise RuntimeError("live acquirer did not release the radio within 15 seconds")
            self._status["phase"] = "waiting for the radio handle to settle"
            OPERATIONS.stage(op_id, "released", "live radio released; allowing hardware handles to settle")
            if not self.demo:
                await asyncio.sleep(float(os.environ.get("RADIO_RECORDING_SETTLE_SEC", "2.0")))
            OPERATIONS.stage(op_id, "applied", "sweep runner starting")
            self._status["state"] = "recording"
            self._status["phase"] = "warming up capture → analysis → archive pipeline"
            advanced = str(request.get("yaml") or "").strip()
            spec_text = advanced or self._default_spec(request, working)
            fd, spec_name = tempfile.mkstemp(prefix="radio-record-", suffix=".yaml")
            os.close(fd)
            spec_path = Path(spec_name)
            spec_path.write_text(spec_text, encoding="utf-8")
            if self.demo:
                await self._run_demo(working, duration, op_id, spec_text)
            else:
                await self._run_hardware(spec_path, working, duration, op_id)
            if not working.is_file() or working.stat().st_size == 0:
                raise RuntimeError("recording finished without a non-empty archive")
            with zipfile.ZipFile(working) as archive:
                bad = archive.testzip()
                if bad:
                    raise RuntimeError(f"recording archive CRC failed at {bad}")
                self._status["archive_entries"] = len(archive.infolist())
            os.replace(working, output)
            self._status.pop("working_output", None)
            self._status["bytes"] = output.stat().st_size
            self._status["validated"] = True
            if self._stop.is_set():
                detail = "recording stopped by operator"
            elif duration:
                detail = "recording duration reached"
        except Exception as exc:
            terminal, detail = "failed", str(exc)
            self._status["error"] = detail
        finally:
            self._process = None
            if spec_path:
                with contextlib.suppress(OSError):
                    spec_path.unlink()
            self.acquirer.resume()
            OPERATIONS.stage(op_id, "resume-live", "live acquisition resume requested")
            if terminal == "success":
                self._status["state"] = "idle"
            else:
                self._status["state"] = "failed"
            self._status["finished_at"] = time.time()
            OPERATIONS.finish(op_id, terminal, detail)

    async def _run_hardware(self, spec_path, output, duration, op_id):
        """Run the real sweep in a worker thread and fold its progress into status.

        Delegates to `sweep_runner.run_sweep`, passed the live acquirer's
        `source` object so the sweep operates in-process on the already-open
        radio handle (required for AIR-T, which retains FPGA descriptors for
        the importing process's lifetime — a subprocess cannot acquire it even
        after `Device.close()`). `run_sweep` itself is blocking, so it is
        supervised via `asyncio.to_thread` rather than awaited directly. The
        `progress` callback translates `sweep_runner`'s `"opened"`/`"progress"`
        events into `self._status` updates and `OPERATIONS` stage entries.

        Args:
            spec_path: Path to the temp YAML sweep spec written by `_run()`.
            output: `.partial.zarr.zip` working path passed through to
                `run_sweep` as its destination.
            duration: Requested capture duration in seconds, or None.
            op_id: Operation id for `OPERATIONS.stage()` progress entries.
        """
        # AIR-T retains FPGA descriptors for the importing process lifetime;
        # a subprocess cannot acquire it even after Device.close(). Supervise
        # the blocking wrapper in a worker thread inside this process instead.
        from sweep_runner import run_sweep

        def progress(kind, **event):
            if kind == "opened":
                self._status["phase"] = "acquiring/analyzing the first capture"
                self._status.update({k: v for k, v in event.items()
                                     if k in {"effective_backend", "gapless"}})
            elif kind == "progress":
                self._status.update(captures=event["captures"],
                                    elapsed_s=event["elapsed_s"],
                                    pipeline_steps=event.get("pipeline_step", 0),
                                    mean_step_s=event.get("step_interval_s"))
                self._status["phase"] = (
                    "writing captures" if event["captures"]
                    else "first capture is still in the analysis/write pipeline"
                )
                OPERATIONS.stage(
                    op_id, "progress",
                    f'{event["captures"]} captures · {event["elapsed_s"]:.1f} s')

        result = await asyncio.to_thread(
            run_sweep, str(spec_path), str(output), duration,
            self._thread_stop.is_set, progress, self.acquirer.source)
        self._status.update(result)

    async def _run_demo(self, output, duration, op_id, spec_text):
        """Fake a recording sweep in demo mode with no hardware or striqt involved.

        Loops incrementing a synthetic capture counter every 0.25s (or the
        full duration if shorter) until stopped or the requested duration
        elapses, then writes a minimal stub archive (`demo-recording.json`
        containing the spec text) to `output` so the rest of `_run()`'s
        validate/rename pipeline behaves identically to the hardware path.

        Args:
            output: `.partial.zarr.zip` working path to write the stub
                archive into.
            duration: Requested duration in seconds, or None for unbounded
                (stopped only via `self._stop`).
            op_id: Operation id for `OPERATIONS.stage()` progress entries.
            spec_text: The sweep YAML text, embedded in the stub archive for
                traceability.
        """
        started = time.monotonic()
        self._status["phase"] = "writing demo captures"
        while not self._stop.is_set() and (duration is None or time.monotonic() - started < duration):
            await asyncio.sleep(min(0.25, duration or 0.25))
            self._status["captures"] += 1
            self._status["elapsed_s"] = round(time.monotonic() - started, 3)
            OPERATIONS.stage(op_id, "progress", f'{self._status["captures"]} captures · {self._status["elapsed_s"]:.1f} s')
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("demo-recording.json", json.dumps({"demo": True, "spec": spec_text}))
