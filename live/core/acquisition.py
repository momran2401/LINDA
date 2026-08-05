"""Background threads that turn radio (or synthetic) IQ into published frames.

This module is the runtime heart of Linda's live view: three daemon threads
built on top of `core.devices` (hardware adapters), `core.dsp` (spectrogram /
AHAWI compute), `core.operations` (the verified-operations log), and
`core.shims` (striqt-version-tolerant stream calls).

- `Acquirer` owns the real SDR: it drains raw IQ off the DMA/stream API into a
  per-channel ring buffer as fast as possible, with no compute in that loop,
  and handles all hardware lifecycle (open/rearm/recover/pause-and-release).
- `Computer` is the compute-side twin: it pulls the latest ring samples,
  invokes the DSP backends to build a frame, and publishes it for the
  broadcaster to fan out over WebSocket.
- `DemoAcquirer` combines both roles for `--demo` mode, synthesizing IQ (fixed
  CW "stations" plus a periodic fake-SSB burst) instead of reading hardware,
  so tuning/AHAWI/TX behavior is testable with no radio attached.

Keeping the raw-IQ drain and the spectrogram math in separate threads is what
prevents DMA overflow on the real radio: the Acquirer keeps draining while a
frame is being computed. Originally extracted from striqt_web_server.py; see
CLAUDE.md's Architecture section for how this fits into the rest of `core/`.
"""
from __future__ import annotations

import threading
import time

import numpy as np

from . import devices, state
from .config import RadioConfig, SharedConfig
from .constants import (
    DEVICE_PROFILES, MAX_TAIL, READ_SIZE, DATA_STALE_SEC, DEMO_TONES,
    DEFAULT_CENTER, DEMO_BURST, AHAWI_REFRESH_S, envelope_query_groups,
)
from .dsp import (
    AHAWI_BACKENDS, ahawi_capture, ahawi_plan, build_header, compute_blocks,
    samples_needed,
)
from .operations import OPERATIONS, verdict_state
from .shims import (
    _close_rx_stream, close_source, enable_stream, get_stream_mtu, get_stream_ports,
    get_rx_stream, missing_source_api, open_stream, query_device_envelope,
    stream_buffers_for,
)
from .striqt_compat import ReceiveStreamError, specs
from .tx import TX

def make_capture(cfg):
    """Build the striqt capture spec that `arm_spec()` programs onto the source.

    Port is pinned to `state.CHANNELS` (not derived from `cfg`) because the
    two-waterfall UI depends on both RX ports being armed together; every
    other knob comes from the schema editor / `cfg`. When `cfg.duration`
    doesn't own the time axis, duration is derived from `rows * nfft /
    sample_rate` instead, then snapped so `duration * sample_rate` is an
    integer sample count — striqt's Capture validation requires that.

    Args:
        cfg: RadioConfig snapshot describing the requested capture.

    Returns:
        specs.SoapyCapture: the spec to pass to `source.arm_spec()`.
    """
    duration = cfg.duration if cfg.duration > 0 else cfg.rows * cfg.nfft / cfg.sample_rate
    duration = max(duration, 1e-3)
    duration = round(duration * cfg.sample_rate) / cfg.sample_rate
    return specs.SoapyCapture(
        port=state.CHANNELS,
        center_frequency=cfg.center,
        gain=tuple([cfg.gain] * len(state.CHANNELS)),
        duration=duration,
        sample_rate=cfg.sample_rate,
        backend_sample_rate=(cfg.backend_sample_rate or cfg.sample_rate),
        host_resample=cfg.host_resample,
        analysis_bandwidth=cfg.analysis_bandwidth,
        lo_shift=cfg.lo_shift,
    )
# ---------------------------------------------------------------------------
# Acquirer thread (real AIR8201B hardware)
# ---------------------------------------------------------------------------

class Acquirer(threading.Thread):
    """Drains raw IQ from the live SDR into a ring buffer and owns its lifecycle.

    Runs a tight read loop (no spectrogram math here) pulling from the
    hardware source in `READ_SIZE`-sized chunks into a `MAX_TAIL`-sample
    per-channel ring buffer. The separate `Computer` thread pulls the latest
    samples via `get_latest()`, computes blocks, and calls `publish()`; the
    broadcaster reads `latest()`/`latest_if_newer()` at `state.BROADCAST_FPS`
    to fan out frames to all clients.

    Beyond draining, this thread also owns opening, retuning, recovering, and
    pausing/releasing the device (for handoffs to a recording sweep or the TX
    ladder's `rx_released` rung), and stages every hardware-affecting change
    through the verified-operations log in `core.operations`.

    Keeping compute off this loop is what prevents DMA overflow: while a frame
    is being computed, `_read_stream` keeps draining the radio. This mirrors
    the Acquirer/LocalReceiver split in `legacy/striqt_standalone.py`.
    """

    def __init__(self, shared: SharedConfig):
        """Initialize an idle Acquirer bound to a shared config.

        The thread owns no hardware until `run()` calls `open_radio()`.

        Args:
            shared: The process-wide SharedConfig this thread reads dirty
                config changes from and reports analysis/notice feedback to.
        """
        super().__init__(daemon=True)
        self.shared       = shared
        self.source       = None
        self.stream_mtu   = None
        self.stream_ports = state.CHANNELS

        # Latest computed-frame slot (written by Computer, read by broadcaster).
        self._pub_lock        = threading.Lock()
        self._latest_header   = None
        self._latest_blocks   = None

        # Raw IQ ring buffer (complex64). One write pointer + sample count shared
        # across channels since every read fills all channels equally.
        self._lock        = threading.Lock()
        self._ring        = np.zeros((len(state.CHANNELS), MAX_TAIL), dtype=np.complex64)
        self._write       = 0      # next write index (mod MAX_TAIL)
        self._count       = 0      # total samples written (saturates at MAX_TAIL)
        self._last_write  = 0.0
        self._healthy     = False
        self._gen         = 0      # bumped on every ring clear (retune/recover) — LV-R5
        # Wall time of the last suspected drain gap (a zero-sample read while
        # the stream was healthy — overflow drops surface this way with
        # on_overflow="log"). AHAWI marks captures overlapping one as
        # coherent=false instead of pretending.
        self._last_gap    = 0.0

        # Last source_config that demonstrably opened the device — the
        # revert target when a source-spec reconnect fails.
        self._last_good_source = {}
        # Verified-operations handoff: (op_id, ring_generation, verdict_state)
        # set by rearm/open_radio after readback; the Computer finishes the op
        # when the first frame of that generation is actually computed.
        self._verify_lock = threading.Lock()
        self._verify      = None
        self._pause_requested = threading.Event()
        self._paused = threading.Event()

    def pause_and_release(self, timeout=10.0):
        """Ask the run loop to release the RX stream and wait for it to do so.

        This is the cooperative handoff other consumers of the same device
        (a recording sweep, or the TX ladder's `rx_released` rung) must use
        instead of disabling/closing the stream themselves: the Acquirer
        thread is blocked in a blocking read on that stream, so pulling it
        out from underneath produces an `Inactive RF hardware detected`
        timeout rather than a clean release. The run loop notices the
        request, disables and closes the RX stream, clears the ring, and
        sets `_paused`.

        Args:
            timeout: Seconds to wait for the loop to confirm it paused.

        Returns:
            bool: True if the loop paused within `timeout`, else False.
        """
        self._pause_requested.set()
        return self._paused.wait(timeout)

    def resume(self):
        """Clear the pause request so the run loop reopens/rearms the device."""
        self._pause_requested.clear()

    def is_paused(self):
        """Return True once the run loop has confirmed it released the stream."""
        return self._paused.is_set()

    # --- Latest-frame slot (thread-safe) ---

    def latest(self):
        """Return (header_dict, [block_array, ...]) of the most recent frame."""
        with self._pub_lock:
            if self._latest_header is None:
                return None, None
            return dict(self._latest_header), [b.copy() for b in self._latest_blocks]

    def latest_header(self):
        """Header of the most recent frame WITHOUT copying its blocks."""
        with self._pub_lock:
            return dict(self._latest_header) if self._latest_header else None

    def latest_if_newer(self, than: float):
        """latest(), but (None, None) unless the frame is newer than `than`.

        The broadcaster polls at BROADCAST_FPS and mostly sees the same frame
        it just sent; copying the blocks before noticing that was ~180 MB/s of
        pure memcpy with AHAWI's multi-segment captures.
        """
        with self._pub_lock:
            header = self._latest_header
            if header is None or header.get("time", 0.0) == than:
                return None, None
            return dict(header), [b.copy() for b in self._latest_blocks]

    def publish(self, cfg: RadioConfig, blocks: list, meta: dict):
        """Store a freshly computed frame as the latest one (called by Computer).

        Args:
            cfg: RadioConfig the frame was computed under (used to build the
                frame header).
            blocks: Per-channel computed arrays for this frame.
            meta: Backend-specific metadata merged into the header.
        """
        header = build_header(cfg, blocks, meta, demo=False)
        with self._pub_lock:
            self._latest_header = header
            self._latest_blocks = [np.asarray(b, dtype=np.float32) for b in blocks]

    # --- Ring buffer (thread-safe; ported from legacy/striqt_standalone.py) ---

    def _clear_ring_locked(self):
        """Reset the ring to empty and bump its generation counter.

        Must be called with `self._lock` held. Bumping `_gen` invalidates any
        in-flight frame computation that started against the pre-clear ring
        (LV-R5) — the Computer checks `gen` against the ring's current
        generation before publishing so a retune/recover can never let old
        and new IQ mix into the same frame.
        """
        self._write      = 0
        self._count      = 0
        self._last_write = 0.0
        self._healthy    = False
        self._gen       += 1   # invalidate frames straddling this retune/recover (LV-R5)

    def _ring_write(self, iq):
        """Append raw IQ into the ring buffer, overwriting the oldest samples.

        Args:
            iq: complex64 array shaped (channels, n) to append. If n exceeds
                the ring capacity (`MAX_TAIL`), only the newest `MAX_TAIL`
                samples survive.
        """
        n = iq.shape[1]
        if n <= 0:
            return
        with self._lock:
            cap = MAX_TAIL
            if n >= cap:
                # Only the newest `cap` samples can survive.
                self._ring[:, :]  = iq[:, -cap:]
                self._write       = 0
                self._count       = cap
                self._last_write  = time.time()
                self._healthy     = True
                return
            end = self._write + n
            if end <= cap:
                self._ring[:, self._write:end] = iq
            else:
                first = cap - self._write
                self._ring[:, self._write:] = iq[:, :first]
                self._ring[:, : n - first]  = iq[:, first:]
            self._write      = end % cap
            self._count      = min(self._count + n, cap)
            self._last_write = time.time()
            self._healthy    = True

    def generation(self):
        """Return the ring's current generation counter (bumped on every clear)."""
        with self._lock:
            return self._gen

    def last_gap_time(self):
        """Wall time of the last suspected drain gap (0.0 = none seen).

        A "gap" is a zero-sample read observed while the stream was
        otherwise healthy — how an overflow drop surfaces with
        `on_overflow="log"`. AHAWI mode compares a capture's time span
        against this to report `coherent=false` instead of pretending the
        capture had no seam.
        """
        return self._last_gap

    def get_latest(self, n):
        """Return the most recent `n` samples per channel from the ring.

        Args:
            n: Number of complex samples to return per channel.

        Returns:
            None if the ring is empty or stale (`DATA_STALE_SEC` since the
            last write) — the caller must wait rather than compute from
            dead data. Otherwise a 3-tuple:
                out: complex64 array shaped (channels, n), chronological
                    (oldest -> newest), front-padded with zeros if fewer
                    than `n` samples exist yet.
                gen: the ring generation at read time — callers compare this
                    to a `generation()` taken before the read to detect a
                    retune/recover that happened mid-read, so a frame never
                    mixes old-tuning samples with new (LV-R5).
                avail: the real (unpadded) sample count available.
        """
        n = int(n)
        if n <= 0:
            return None
        with self._lock:
            if (not self._healthy or self._count == 0
                    or time.time() - self._last_write > DATA_STALE_SEC):
                return None
            cap   = MAX_TAIL
            avail = min(self._count, cap)
            take  = min(n, avail)
            out   = np.zeros((len(state.CHANNELS), n), dtype=np.complex64)
            start = (self._write - take) % cap
            end   = start + take
            if end <= cap:
                out[:, n - take:] = self._ring[:, start:end]
            else:
                first = cap - start
                out[:, n - take:n - take + first] = self._ring[:, start:]
                out[:, n - take + first:]         = self._ring[:, : take - first]
            gen = self._gen
        return out, gen, avail

    # --- Verified operations (readback + data-path) ---

    def ring_status(self):
        """Summarize radio/stream liveness for the `/health` endpoint.

        Returns:
            dict: `open` (source exists), `healthy` (ring has fresh data),
            `last_write_age_s` (seconds since the last ring write, or None
            if never written), `ring_fill` (fraction of `MAX_TAIL` filled).
        """
        with self._lock:
            age = (time.time() - self._last_write) if self._last_write else None
            return {
                "open":             self.source is not None,
                "healthy":          bool(self._healthy),
                "last_write_age_s": round(age, 3) if age is not None else None,
                "ring_fill":        round(min(self._count, MAX_TAIL) / MAX_TAIL, 4),
            }

    # Which config fields make each hardware aspect worth judging. An op
    # that touched none of them (rows, backend, analysis…) is proven by
    # validation + the data-path frame alone — an unrelated missing driver
    # getter must not downgrade it to "unverified".
    _FREQ_FIELDS = frozenset({"center", "lo_shift"})
    _RATE_FIELDS = frozenset({"sample_rate", "backend_sample_rate",
                              "host_resample"})
    _GAIN_FIELDS = frozenset({"gain"})

    def _readback_and_verify(self, cfg: RadioConfig, op_id):
        """Ask the live driver what it actually applied and judge it against the request.

        Only judges the aspects (frequency/rate/gain) this operation
        actually changed — a full recipe check runs instead when the
        changed-field list is unknown (radio open/recovery, or a source
        reconnect), so an operation that never touched an aspect (e.g. rows,
        backend, analysis knobs) can't be downgraded by an unrelated missing
        driver getter. Expected values are computed via the device adapter's
        `hardware_expectations()` so striqt's own intentional LO
        offset/resample choices are never flagged as a mismatch. Logs one
        readback stage per judged field to the operations log.

        Args:
            cfg: The RadioConfig this operation applied.
            op_id: The operations-log id to stage readback results against.

        Returns:
            str: The collapsed verdict state (see `operations.verdict_state`),
            e.g. "success", "verified", "unverified", or "mismatch".
        """
        fields = OPERATIONS.fields(op_id)
        if fields is None or any(f.startswith("source.") for f in fields):
            check_freq = check_rate = check_gain = True     # full recipe
        else:
            fset = set(fields)
            check_freq = bool(fset & self._FREQ_FIELDS)
            check_rate = bool(fset & self._RATE_FIELDS)
            check_gain = bool(fset & self._GAIN_FIELDS)
        if not (check_freq or check_rate or check_gain):
            OPERATIONS.stage(op_id, "readback",
                             "not applicable — no hardware-facing field "
                             "changed (validated + frame-confirmed only)")
            return "success"
        adapter = devices.get_adapter()
        try:
            actuals  = adapter.read_back(self.source, cfg)
            # Expected values come from striqt's own resampler/LO design when
            # discoverable — an intentional lo_shift or backend_sample_rate
            # must not read as a mismatch.
            expected = adapter.hardware_expectations(
                self.source, make_capture(cfg), cfg)
            verdicts = adapter.verify(cfg, actuals, expected)
            verdicts = [v for v in verdicts
                        if (v["field"] == "center" and check_freq)
                        or (v["field"] == "sample_rate" and check_rate)
                        or (v["field"].startswith("gain") and check_gain)]
            if check_freq and abs(expected["center"] - float(cfg.center)) > 1.0:
                OPERATIONS.stage(
                    op_id, "readback",
                    f"note: hardware LO intentionally offset to "
                    f"{expected['center']/1e6:.6g} MHz (lo_shift={cfg.lo_shift})")
        except Exception as e:
            OPERATIONS.stage(op_id, "readback", f"query failed: {e}", level="warn")
            return "unverified"
        for v in verdicts:
            if v["state"] == "readback_unsupported":
                OPERATIONS.stage(op_id, "readback",
                                 f"{v['field']}: driver gave no answer",
                                 level="warn")
            else:
                mark = "OK" if v["state"] == "verified" else "MISMATCH"
                OPERATIONS.stage(
                    op_id, "readback",
                    f"{v['field']}: requested {v['requested']:.6g}, "
                    f"actual {v['actual']:.6g} — {mark}",
                    level=("info" if v["state"] == "verified" else "warn"),
                )
        return verdict_state(verdicts)

    def _arm_verification(self, op_id, vstate):
        """Register a pending op for data-path proof by the Computer thread.

        The op finishes only once the Computer actually publishes a frame
        computed from the current ring generation (`complete_verification`),
        which is the real proof that IQ is flowing under the new config —
        readback alone can't prove the data path works.

        Args:
            op_id: Operations-log id to verify, or None for recovery/resume
                rearms that don't own an operation (a None op_id is a no-op
                here — see below for why).
            vstate: The verdict state computed by `_readback_and_verify`,
                applied when the op finishes.
        """
        # Recovery/resume rearms don't own an operation.  They must never
        # replace a real user operation that is still awaiting its fresh-frame
        # proof (the old behavior marked every such operation superseded).
        if op_id is None:
            return
        with self._lock:
            gen = self._gen
        with self._verify_lock:
            stale = self._verify
            self._verify = (op_id, gen, vstate)
        if stale is not None and stale[0] != op_id:
            OPERATIONS.finish(stale[0], "superseded",
                              "a newer apply replaced this operation before a "
                              "frame confirmed its data path")

    def complete_verification(self, gen):
        """Finish the pending operation once a frame of its generation publishes.

        Called by the Computer thread after each successfully published
        frame. A no-op if there is no pending verification or the pending
        one is for a different ring generation.

        Args:
            gen: The ring generation the just-published frame was computed
                from.
        """
        with self._verify_lock:
            if self._verify is None or self._verify[1] != gen:
                return
            op_id, _, vstate = self._verify
            self._verify = None
        OPERATIONS.stage(op_id, "data-path",
                         "fresh IQ received and first frame computed with the "
                         "new configuration")
        OPERATIONS.finish(op_id, vstate)

    # --- Hardware management ---

    def open_radio(self, cfg: RadioConfig, op_id=None):
        """Create the device source, arm it, and enable the RX stream.

        Used both for the initial open in `run()` and for a full
        close-then-reopen recovery. Validates the source exposes the
        striqt source API `core/devices` and this module depend on
        (fails fast, naming the missing methods, rather than surfacing an
        AttributeError several layers down — method names differ across
        striqt releases, see INSTALLED_STRIQT_API.txt). After a successful
        open, invalidates TX's cached capability probe (a new handle may
        expose different TX channels) and, for profiles that opt in, queries
        the device's real capability envelope so later config clamps use
        live bounds instead of the static profile fallback.

        Args:
            cfg: RadioConfig to open the device with.
            op_id: Existing operations-log id to stage against, or None to
                begin (and own) a new "radio" operation for this open.

        Raises:
            RuntimeError: If the source is missing an API method this
                module requires.
            Exception: Any error from source creation, stream setup, or
                `arm_spec` is re-raised after being logged to the operation.
        """
        own_op = op_id is None
        if own_op:
            op_id = OPERATIONS.begin(
                "radio", f"open {state.DEVICE_LABEL} "
                         f"(center {cfg.center/1e6:.6g} MHz, "
                         f"{cfg.sample_rate/1e6:.6g} MS/s)")
        OPERATIONS.stage(op_id, "applying", "creating source + opening stream"
                         + (f" (source overrides: {sorted(cfg.source_config)})"
                            if cfg.source_config else ""))
        try:
            self.source = devices.make_source(cfg.source_config)
            # Fail here, naming the problem, rather than several layers down in
            # an AttributeError: live/core drives the striqt source object
            # directly, and those method names differ between striqt releases
            # (see INSTALLED_STRIQT_API.txt).
            absent = missing_source_api(self.source)
            if absent:
                raise RuntimeError(
                    f"installed striqt source is missing {', '.join(absent)} — "
                    f"live/core targets the pinned striqt build; see "
                    f"INSTALLED_STRIQT_API.txt")
            open_stream(self.source)
            self.source.arm_spec(make_capture(cfg))
            enable_stream(self.source, True)
        except Exception:
            if own_op:
                OPERATIONS.finish(op_id, "failed", "source open/arm raised")
            raise
        OPERATIONS.stage(op_id, "applied", "arm_spec completed, stream enabled")
        # A new device handle means new TX capabilities (and invalidates any
        # cached "not open yet"). core.tx re-probes on the next query.
        TX.invalidate_capabilities()
        self.stream_mtu   = get_stream_mtu(self.source)
        self.stream_ports = get_stream_ports(self.source)
        # Capability envelope (P3-3): profiles that opt in get their tier-1
        # clamp bounds from the live device. Failure is non-fatal — the
        # profile fallback stays in force. _recover() reopens through here,
        # so the envelope survives recovery cycles.
        groups = envelope_query_groups(DEVICE_PROFILES[state.DEVICE])
        if groups:
            try:
                self.shared.set_envelope(
                    query_device_envelope(self.source, groups))
            except Exception as e:
                print(f"[device] envelope query failed (profile fallback kept): {e}")
        print(
            f"[radio] armed: center {cfg.center/1e6:.2f} MHz, "
            f"{cfg.sample_rate/1e6:.3f} MS/s, channels {state.CHANNELS}, "
            f"backend={cfg.backend}"
        )
        vstate = self._readback_and_verify(cfg, op_id)
        self._arm_verification(op_id, vstate)

    def rearm(self, cfg: RadioConfig, op_id=None):
        """Apply a new capture recipe to the already-open device, in place.

        Disables the stream, reprograms gain/frequency/rate/capture via
        `arm_spec`, and re-enables the stream — without closing and
        recreating the underlying DMA stream. That close/recreate path was
        tried and abandoned: on the AIR-T it left the `/dev/xdma0_c2h_0`
        handle busy, so every setting change blocked for ~6.5 s and fell
        into recovery. The in-place path is portable to Pluto/generic Soapy
        too. Falls back to `open_radio()` if no device is open yet.

        On AIR-T, stream (re)activation retries up to 6 times: activation
        claims an exclusive XDMA channel, and a rapid
        deactivate/reconfigure/activate cycle can transiently return EBUSY
        while the kernel finishes releasing the prior activation — retried
        on the same stream, never by rebuilding the device. Other radios get
        one attempt. On success the ring is cleared so stale IQ from the old
        tuning can never mix into a frame under the new config, then
        readback verification runs.

        Args:
            cfg: RadioConfig describing the new capture recipe.
            op_id: Operations-log id to stage progress against, or None for
                an unowned (recovery/resume) rearm.

        Raises:
            Exception: If stream activation never succeeds within the retry
                budget, the last activation error is re-raised.
        """
        if self.source is None:
            self.open_radio(cfg, op_id)
            return
        OPERATIONS.stage(
            op_id, "applying",
            f"rearm: center {cfg.center/1e6:.6g} MHz, "
            f"{cfg.sample_rate/1e6:.6g} MS/s, gain {cfg.gain:.1f} dB, "
            f"nfft={cfg.nfft}, rows={cfg.rows}")
        # Apply the capture recipe to the existing stream.  striqt's Soapy
        # backend disables the stream, programs gain/frequency/rate, and then
        # this method re-enables it.  Closing and immediately recreating the
        # DMA stream here used to leave AIR-T's /dev/xdma0_c2h_0 handle busy;
        # every setting change then blocked for ~6.5 s and entered recovery.
        # The same in-place arm path is portable to Pluto/generic Soapy.
        enable_stream(self.source, False)
        rx = get_rx_stream(self.source)
        # Recording deliberately closes the live stream before handing the
        # source to the sweep runner.  If it is still closed on resume, reopen
        # exactly once; ordinary settings changes never enter this branch.
        if rx is not None and getattr(rx, "stream", None) is None:
            open_stream(self.source)
        self.source.arm_spec(make_capture(cfg))
        # AIR-T's activation opens an exclusive XDMA channel.  A rapid
        # deactivate/reconfigure/activate can transiently return EBUSY while
        # the kernel releases the prior activation.  Retry activation on the
        # same stream (never rebuild the device); other radios get one attempt.
        attempts = 6 if state.DEVICE in devices.DEEPWAVE_MODELS else 1
        activate_error = None
        for attempt in range(attempts):
            try:
                enable_stream(self.source, True)
                activate_error = None
                break
            except Exception as exc:
                activate_error = exc
                if attempt + 1 < attempts:
                    time.sleep(0.05 * (attempt + 1))
        if activate_error is not None:
            raise activate_error
        # Drop stale samples from the old tuning so they never mix into a frame.
        with self._lock:
            self._clear_ring_locked()
        OPERATIONS.stage(op_id, "applied",
                         "arm_spec completed, ring cleared of old-tuning IQ")
        print(
            f"[radio] retune: center {cfg.center/1e6:.2f} MHz, "
            f"{cfg.sample_rate/1e6:.3f} MS/s, gain {cfg.gain:.1f} dB, "
            f"nfft={cfg.nfft}, rows={cfg.rows}"
        )
        vstate = self._readback_and_verify(cfg, op_id)
        self._arm_verification(op_id, vstate)

    def _make_read_buffers(self):
        """Allocate the scratch buffers the read loop hands to `_read_stream`.

        Sized to the smaller of the stream's reported MTU and `READ_SIZE`
        so a single `_read_stream` call never asks for more than either
        the driver or the read-loop's designed chunk size supports.

        Returns:
            tuple: (read_size, tmp, buffers) — the chunk size in samples,
            the complex64 scratch array shaped (channels, read_size), and
            the driver-specific buffer view(s) built from it.
        """
        read_size     = min(self.stream_mtu or READ_SIZE, READ_SIZE)
        tmp           = np.empty((len(state.CHANNELS), read_size), dtype=np.complex64)
        buffers, _    = stream_buffers_for(self.source, tmp)
        return read_size, tmp, buffers

    def _resume_rearm(self, cfg: RadioConfig, attempts=None):
        """Re-arm the existing source after a recording handoff.

        AIR-T keeps one process-lifetime device singleton: close_source()
        deinitializes its AD9371 management sensors and no reopen inside this
        process can rebuild them, so a transient failure here must be retried
        on the SAME source rather than escalated into a close/reopen that would
        leave the viewer dark until a service restart. Other radios get one
        attempt and then fall back to a clean reopen.

        Args:
            cfg: RadioConfig to rearm the resumed source with.
            attempts: Override for the retry count; defaults to 10 for
                Deepwave models (AIR8201B/AIR7201B/AIR7101B) and 1 otherwise.

        Returns:
            bool: True if `rearm()` eventually succeeded, False if all
            attempts were exhausted or a pause/stop request interrupted
            retrying.
        """
        deepwave = state.DEVICE in devices.DEEPWAVE_MODELS
        tries = attempts if attempts is not None else (10 if deepwave else 1)
        for attempt in range(tries):
            try:
                self.rearm(cfg, None)
                return True
            except Exception as exc:
                print(f"[radio] resume rearm failed "
                      f"(attempt {attempt + 1}/{tries}): {exc}")
                if self._pause_requested.is_set() or self.shared.stopped():
                    return False
                if attempt + 1 < tries:
                    time.sleep(min(0.2 * (attempt + 1), 1.0))
        return False

    def _recover(self, cfg: RadioConfig, reason: str):
        """Recover the radio after a read/apply error, then rebuild read buffers.

        On Deepwave models, never closes the source (see class docs on why
        `close_source()` is unsafe on AIR-T) — instead clears the ring and
        rearms in place, replacing only the RX stream. Other radios get a
        full close, ring clear, and reopen. Shuts down any active TX
        transmission first, since keying a PA into a stream whose device is
        being torn down should never be left to a race.

        Args:
            cfg: RadioConfig to reopen/rearm the device with.
            reason: Human-readable cause, logged and passed to TX.shutdown()
                so an aborted transmission is traceable to this recovery.

        Returns:
            tuple: (read_size, tmp, buffers) from `_make_read_buffers()`.
        """
        print(f"[radio] recovering after: {reason}")
        # Stop transmitting BEFORE the device handle is disturbed. The writer
        # thread notices a swapped handle on its own, but that is the backstop;
        # keying a PA into a stream whose device is being torn down is not
        # something to leave to a race. Name the reason: a transmission killed
        # by a recovery it did not cause is otherwise untraceable.
        TX.shutdown(f"the acquirer is recovering the radio after: {reason}")
        if state.DEVICE in devices.DEEPWAVE_MODELS and self.source is not None:
            # source.close() deinitializes the AD9371 management sensors for
            # this process. Recover AIR-T by replacing only its RX stream.
            with self._lock:
                self._clear_ring_locked()
            time.sleep(0.1)
            self.rearm(cfg)
            return self._make_read_buffers()
        if self.source is not None:
            close_source(self.source)
            self.source = None
        with self._lock:
            self._clear_ring_locked()
        time.sleep(0.25)
        self.open_radio(cfg)
        return self._make_read_buffers()

    # --- Main loop ---

    def run(self):
        """Thread entry point: open the radio, then drain IQ until stopped.

        Each loop iteration, in priority order: services a pause request
        (release the stream for another consumer, then wait to resume);
        applies any pending config change from `shared.take_dirty()`
        (a verified reconnect, an in-place rearm, or — for compute-only
        fields — just a ring clear with the stream left alone); reads one
        chunk of IQ and appends it to the ring; and, on any receive error,
        recovers the device via `_recover()`. All exceptions from a bad
        config apply are handled by reverting to the last-known-good config
        (or source) and retrying rather than propagating, since this thread
        must outlive any single bad request. Always closes the source on
        exit (normal shutdown, not the AIR-T recovery path where closing is
        avoided).
        """
        cfg = self.shared.snapshot()
        try:
            self.open_radio(cfg)
            self._last_good_source = dict(cfg.source_config or {})
            last_good_cfg = cfg.snapshot()
            read_size, tmp, buffers = self._make_read_buffers()
            last_log = 0.0

            while not self.shared.stopped():
                if self._pause_requested.is_set():
                    if self.source is not None:
                        # Keep the process-lifetime AIR-T device initialized.
                        # source.close() deinitializes its AD9371 management
                        # sensors and the driver cannot rebuild them without a
                        # process restart. The recording runner takes ownership
                        # of this exact source object while live reads pause.
                        enable_stream(self.source, False)
                        _close_rx_stream(self.source)
                    with self._lock:
                        self._clear_ring_locked()
                    self._paused.set()
                    while (self._pause_requested.is_set()
                           and not self.shared.stopped()):
                        time.sleep(0.05)
                    self._paused.clear()
                    if self.shared.stopped():
                        break
                    cfg = self.shared.snapshot()
                    if self.source is not None:
                        if self._resume_rearm(cfg):
                            read_size, tmp, buffers = self._make_read_buffers()
                            continue
                        if state.DEVICE in devices.DEEPWAVE_MODELS:
                            # Never close an AIR-T here — that would cost the
                            # AD9371 management sensors for the rest of this
                            # process. Loop back and keep trying on the same
                            # open device; the read path's _recover() also
                            # rebuilds only the RX stream for this family.
                            print("[radio] keeping the AIR-T device initialized; "
                                  "retrying from the main loop")
                            time.sleep(0.5)
                            continue
                        print("[radio] reopening the device after resume failure")
                        close_source(self.source)
                        self.source = None
                    # AIR-T management sensors can remain unavailable briefly
                    # after another process closes the device. A transient
                    # reopen error must not kill this long-lived thread and
                    # leave the web viewer permanently degraded.
                    while (not self._pause_requested.is_set()
                           and not self.shared.stopped()):
                        try:
                            self.open_radio(cfg)
                            read_size, tmp, buffers = self._make_read_buffers()
                            break
                        except Exception as exc:
                            if self.source is not None:
                                close_source(self.source)
                                self.source = None
                            print(f"[radio] resume open failed: {exc}; retry in 1s")
                            time.sleep(1.0)
                    continue
                dirty, new_cfg, op_id, reconnect, changed_fields = self.shared.take_dirty()
                if dirty:
                    cfg = new_cfg
                    try:
                        if reconnect:
                            # Source-spec overrides only take effect at open:
                            # verified reconnect (close → rebuild → reopen).
                            OPERATIONS.stage(
                                op_id, "applying",
                                "source settings changed — closing and "
                                "reopening the device")
                            if self.source is not None:
                                close_source(self.source)
                                self.source = None
                            with self._lock:
                                self._clear_ring_locked()
                            self.open_radio(cfg, op_id)
                        elif changed_fields & (
                            self._FREQ_FIELDS | self._RATE_FIELDS |
                            self._GAIN_FIELDS |
                            {"analysis_bandwidth", "host_resample",
                             "backend_sample_rate"}
                        ):
                            self.rearm(cfg, op_id)
                        else:
                            # FFT/rows/backend/analysis/LO-display changes only
                            # affect the compute path.  Invalidate any in-flight
                            # old-config frame, but leave the SDR stream alone.
                            OPERATIONS.stage(
                                op_id, "applying",
                                "compute/display settings changed — radio stream kept open")
                            with self._lock:
                                self._clear_ring_locked()
                            OPERATIONS.stage(
                                op_id, "readback",
                                "not applicable — no hardware-facing field changed")
                            self._arm_verification(op_id, "success")
                        if reconnect or changed_fields & (
                            self._FREQ_FIELDS | self._RATE_FIELDS |
                            self._GAIN_FIELDS |
                            {"analysis_bandwidth", "host_resample",
                             "backend_sample_rate"}
                        ):
                            read_size, tmp, buffers = self._make_read_buffers()
                        self._last_good_source = dict(cfg.source_config or {})
                        last_good_cfg = cfg.snapshot()
                    except Exception as e:
                        OPERATIONS.finish(op_id, "failed",
                                          f"hardware apply raised: {e}")
                        if reconnect:
                            # A bad source override would loop recovery forever
                            # — revert to the last set that actually opened.
                            cfg = self.shared.restore_source(
                                self._last_good_source, reason=str(e))
                        else:
                            # A rejected arm must not destroy AIR-T's
                            # process-lifetime device singleton. Restore the
                            # last recipe on the same initialized source.
                            cfg = self.shared.restore_config(
                                last_good_cfg, reason=str(e))
                            try:
                                self.rearm(cfg, None)
                                read_size, tmp, buffers = self._make_read_buffers()
                                OPERATIONS.stage(
                                    op_id, "recovered",
                                    "rolled back to the last-good config after "
                                    "the failed apply")
                                continue
                            except Exception as rollback_error:
                                print(f"[radio] rollback rearm failed: "
                                      f"{rollback_error}; full recovery needed")
                        try:
                            read_size, tmp, buffers = self._recover(cfg, str(e))
                        except Exception as re:
                            print(f"[radio] recovery failed: {re}; retry in 1s")
                            time.sleep(1.0)
                        continue

                # Guard: if source is None (recovery failed and we slept), retry
                if self.source is None:
                    time.sleep(0.1)
                    continue

                try:
                    got, _ = self.source._read_stream(
                        buffers,
                        offset=0,
                        count=read_size,
                        timeout_sec=read_size / cfg.sample_rate + 0.1,
                        on_overflow="log",
                    )
                except (ReceiveStreamError, OverflowError, OSError, RuntimeError) as e:
                    try:
                        read_size, tmp, buffers = self._recover(cfg, str(e))
                    except Exception as re:
                        print(f"[radio] recovery failed: {re}; retry in 1s")
                        time.sleep(1.0)
                    continue

                if got <= 0:
                    if self._healthy:
                        self._last_gap = time.time()
                    time.sleep(0.001)
                    continue

                # Drain-only: push raw IQ into the ring and loop back to read
                # again immediately. The Computer thread does the spectrogram.
                iq = tmp[:, :got].copy()
                self._ring_write(iq)

                now = time.time()
                if now - last_log > 5.0:
                    print(
                        f"[radio] IQ {iq.shape} {iq.dtype}  "
                        f"ring {min(self._count, MAX_TAIL)}/{MAX_TAIL}  "
                        f"backend={cfg.backend}"
                    )
                    last_log = now

        finally:
            if self.source is not None:
                close_source(self.source)


# ---------------------------------------------------------------------------
# Compute thread (spectrogram worker, decoupled from the DMA drain)
# ---------------------------------------------------------------------------

class Computer(threading.Thread):
    """Turns the Acquirer's raw IQ into published frames, off the DMA drain loop.

    Pulls the latest raw IQ from the Acquirer's ring buffer, computes the
    spectrogram (rolling backends) or a full AHAWI capture, and publishes the
    frame — all off the DMA drain loop so the radio keeps draining while a
    frame is being computed. Paced to roughly `state.BROADCAST_FPS` so it
    doesn't compute frames the broadcaster would only drop. Also owns
    servicing the shared config's tier-2 validation probe, since it holds
    striqt's thread-bound persistent window cache.
    """

    def __init__(self, acquirer: "Acquirer", shared: SharedConfig, insights=None):
        """Initialize a Computer bound to an Acquirer and the shared config.

        Args:
            acquirer: The Acquirer instance to pull ring samples from and
                report data-path verification completions to.
            shared: The process-wide SharedConfig read for the current
                config snapshot and used to service tier-2 probes.
            insights: Optional insights collector updated with each capture's
                raw samples and config, if provided.
        """
        super().__init__(daemon=True)
        self.acquirer = acquirer
        self.shared   = shared
        self.insights = insights
        self._last_err_notice = 0.0
        self._last_err_print  = ("", 0.0)   # (message, time) of the last log line

    def _log_compute_error(self, prefix: str, e: Exception) -> None:
        """Log a compute failure without flooding the journal.

        The compute loop runs per display tick, so a PERSISTENT fault — one
        the backstop cannot revert, e.g. a striqt spec invalid at the current
        sample rate — used to print identically at frame rate, forever.
        Observed on a Pi 5 + Pluto: hundreds of `offset - bandwidth/2 < fs/2`
        lines burying the op log and the installer transcript. A repeat of
        the SAME message is suppressed for 5 s; a DIFFERENT error always
        prints immediately, so a changing failure is never masked.
        """
        msg = f"{prefix}{e}"
        last_msg, last_t = self._last_err_print
        now = time.time()
        if msg == last_msg and now - last_t <= 5.0:
            return
        suffix = " (repeating — logged at most every 5 s)" if msg == last_msg else ""
        print(f"[compute] {msg}{suffix}")
        self._last_err_print = (msg, now)

    def _ahawi_cycle(self, cfg):
        """One AHAWI round: coherent chunk -> analyze once -> publish -> pace.

        Unlike the rolling path (recompute a short window per display tick),
        this pulls one phase-coherent `segments * duration` span from the
        ring and analyzes it in a single striqt pass; the CLIENT replays the
        segments one viewing window at a time. Waits (without consuming ring
        state) if the ring generation changed mid-read or isn't yet full
        enough — i.e. still catching up after startup/retune. On success,
        publishing is paced to hold for `AHAWI_REFRESH_S` so replay isn't
        flooded with new captures; a config change breaks the pacing hold
        early so a new capture starts right away. On a compute error, reverts
        to the last-good analysis config (or notifies) and backs off for a
        full second rather than respinning an expensive full-capture compute
        at display-tick rate.

        Args:
            cfg: Current RadioConfig snapshot (must have `cfg.ahawi` set and
                `cfg.backend` in `AHAWI_BACKENDS` — checked by the caller).
        """
        plan = ahawi_plan(cfg)
        need = plan["need_samples"]
        g0     = self.acquirer.generation()
        latest = self.acquirer.get_latest(need)
        if latest is None:
            time.sleep(0.03)
            return
        samples, gen, avail = latest
        if gen != g0 or avail < need:
            # Ring not yet coherent across the whole span (startup/retune).
            time.sleep(0.03)
            return
        published = time.time()
        capture_t0 = published - need / float(cfg.sample_rate)
        try:
            blocks, meta = ahawi_capture(samples, cfg, plan)
            # Honesty about drain gaps: a zero-sample read inside this span
            # means the "coherent" capture may have a seam.
            gap = getattr(self.acquirer, "last_gap_time", lambda: 0.0)()
            meta["ahawi"]["coherent"] = not (capture_t0 <= gap <= published)
            meta["ahawi"]["capture_t0"] = round(capture_t0, 3)
            self.acquirer.publish(
                cfg, [blocks[i] for i in range(blocks.shape[0])], meta)
            self.shared.note_good_analysis(cfg)
            self.acquirer.complete_verification(gen)
        except Exception as e:
            # Same backstop as the rolling path (P2a-3): revert bad analysis
            # params, keep the viewer alive, surface the reason.
            self._log_compute_error("ahawi error: ", e)
            reverted = self.shared.revert_analysis(str(e))
            if reverted:
                print(f"[compute] reverted analysis params: {reverted}")
            elif time.time() - self._last_err_notice > 5.0:
                self.shared.push_notice(f"AHAWI compute error: {e}")
                self._last_err_notice = time.time()
            # A failing full-capture compute is expensive — don't respin
            # it at 4 Hz; once per capture cadence is plenty.
            time.sleep(1.0)
            return
        while (time.time() - published < AHAWI_REFRESH_S
               and not self.shared.stopped()):
            self.shared.service_probe()
            if self.shared.snapshot() != cfg:
                return   # settings changed — replan immediately
            time.sleep(0.05)

    def run(self):
        """Thread entry point: compute and publish frames from ring samples.

        Each iteration services any pending tier-2 validation probe, then
        either runs one AHAWI cycle (`_ahawi_cycle`) or the rolling path:
        wait for enough fresh ring samples (skipping frames that straddle a
        retune, per `get_latest()`'s generation check), compute blocks via
        `compute_blocks`, publish, and report the fresh frame as data-path
        proof to the Acquirer's pending verification. A compute error
        reverts to the last-good analysis config (or notifies) rather than
        killing the loop — the viewer must never freeze. Paced to
        `state.BROADCAST_FPS`, sleeping only when compute finished early.
        """
        interval = 1.0 / max(state.BROADCAST_FPS, 1.0)
        next_t   = time.time()
        while not self.shared.stopped():
            # Serve any pending tier-2 validation probe first: this thread owns
            # striqt's thread-bound persistent window cache (P2a-5).
            self.shared.service_probe()
            cfg     = self.shared.snapshot()
            if cfg.ahawi and cfg.backend in AHAWI_BACKENDS:
                self._ahawi_cycle(cfg)
                next_t = time.time()
                continue
            need    = samples_needed(cfg)
            g0      = self.acquirer.generation()
            latest  = self.acquirer.get_latest(need)
            if latest is None:
                # Ring empty/stale (startup or just after a retune) — wait.
                time.sleep(0.03)
                next_t = time.time()
                continue
            samples, gen, avail = latest
            # Skip frames straddling a retune: the ring was cleared (gen bumped) or
            # hasn't refilled yet (avail < need). Either would publish zero-padded
            # dark rows or mislabel old-band energy with the new header (LV-R5).
            if gen != g0 or avail < need:
                time.sleep(0.03)
                next_t = time.time()
                continue

            try:
                blocks, meta = compute_blocks(samples, cfg)
                self.acquirer.publish(cfg, [blocks[i] for i in range(blocks.shape[0])], meta)
                if self.insights is not None:
                    self.insights.update(samples, cfg)
                self.shared.note_good_analysis(cfg)
                # Data-path proof for the pending verified operation (if any):
                # a frame of this ring generation actually computed.
                self.acquirer.complete_verification(gen)
            except Exception as e:
                # Backstop (P2a-3): even if a bad analysis param somehow reached
                # the live compute, catch it, revert to the last-good analysis
                # config, keep streaming, and surface the reason — the viewer
                # must never freeze.
                self._log_compute_error("error: ", e)
                reverted = self.shared.revert_analysis(str(e))
                if reverted:
                    print(f"[compute] reverted analysis params: {reverted}")
                elif time.time() - self._last_err_notice > 5.0:
                    # Not analysis-induced (nothing to revert) — tell the viewer
                    # anyway, throttled so a persistent fault can't spam.
                    self.shared.push_notice(f"compute error: {e}")
                    self._last_err_notice = time.time()
                time.sleep(0.1)

            # Pace to the broadcast rate; never busy-spin if compute outran it.
            next_t += interval
            dt = next_t - time.time()
            if dt > 0:
                time.sleep(dt)
            else:
                next_t = time.time()


# ---------------------------------------------------------------------------
# Demo acquirer (synthetic IQ — no hardware needed)
# ---------------------------------------------------------------------------

class DemoAcquirer(threading.Thread):
    """Acquirer + Computer combined, synthesizing IQ instead of reading hardware.

    Generates synthetic IQ (Gaussian noise + CW tones, plus a periodic fake
    burst) and feeds it through the same `compute_blocks`/AHAWI paths as the
    real Acquirer/Computer pair, exposing the same `latest()`/`pause_and_
    release()` interface so frontends and the broadcaster don't need to know
    demo mode is active. The tones are fixed STATIONS at absolute RF: they
    move across the band on retune (rather than staying at a fixed offset
    from center), so tuning behavior is testable with no radio attached.
    """

    def __init__(self, shared: SharedConfig, insights=None):
        """Initialize a DemoAcquirer bound to the shared config.

        Args:
            shared: The process-wide SharedConfig this thread reads config
                snapshots from and reports analysis/notice feedback to.
            insights: Optional insights collector updated with each
                synthesized capture's samples and config, if provided.
        """
        super().__init__(daemon=True)
        self.shared           = shared
        self.insights         = insights
        self._lock            = threading.Lock()
        self._latest_header   = None
        self._latest_blocks   = None
        self._pause_requested = threading.Event()
        self._paused          = threading.Event()
        # Wall-clock sample counter: each synth models "the latest n samples
        # from a radio that never stopped sampling", exactly like the hardware
        # ring. Advancing by the chunk length instead used to alias the demo
        # burst stationary in the rolling views whenever the display window
        # equaled the burst period (20 ms window ≡ 20 ms burst → the burst sat
        # still in Boring mode too, faking AHAWI's whole point). Samples stay
        # contiguous WITHIN a chunk — all AHAWI alignment needs.
        self._t0              = time.monotonic()
        self._pos             = 0
        self._rng             = np.random.default_rng(42)

    def pause_and_release(self, timeout=10.0):
        """Ask the run loop to pause (mirrors Acquirer's handoff interface).

        There is no real device to release in demo mode; this exists so
        callers (recording, TX) can treat DemoAcquirer and Acquirer
        interchangeably.

        Args:
            timeout: Seconds to wait for the loop to confirm it paused.

        Returns:
            bool: True if the loop paused within `timeout`, else False.
        """
        self._pause_requested.set()
        return self._paused.wait(timeout)

    def resume(self):
        """Clear the pause request so the run loop resumes synthesizing IQ."""
        self._pause_requested.clear()

    def is_paused(self):
        """Return True once the run loop has confirmed it paused."""
        return self._paused.is_set()

    def latest(self):
        """Return (header_dict, [block_array, ...]) of the most recent frame."""
        with self._lock:
            if self._latest_header is None:
                return None, None
            return dict(self._latest_header), [b.copy() for b in self._latest_blocks]

    def latest_header(self):
        """Header of the most recent frame WITHOUT copying its blocks."""
        with self._lock:
            return dict(self._latest_header) if self._latest_header else None

    def latest_if_newer(self, than: float):
        """See Acquirer.latest_if_newer — same broadcaster-side copy saver."""
        with self._lock:
            header = self._latest_header
            if header is None or header.get("time", 0.0) == than:
                return None, None
            return dict(header), [b.copy() for b in self._latest_blocks]

    def _publish(self, cfg: RadioConfig, blocks: list, meta: dict):
        """Store a freshly computed synthetic frame as the latest one.

        Args:
            cfg: RadioConfig the frame was computed under.
            blocks: Per-channel computed arrays for this frame.
            meta: Backend-specific metadata merged into the header.
        """
        header = build_header(cfg, blocks, meta, demo=True)
        with self._lock:
            self._latest_header = header
            self._latest_blocks = [np.asarray(b, dtype=np.float32) for b in blocks]

    def _synth_chunk(self, cfg: RadioConfig, n: int) -> np.ndarray:
        """Synthesize the next n contiguous samples for every channel.

        Fixed STATIONS (tones at absolute RF, P3-2) plus the periodic DEMO_BURST
        — a fake SSB gated by the running sample counter, so its timing is
        continuous across chunks regardless of chunk size. Also injects any
        active demo TX carrier (`TX.demo_injection()`) at the offset a real
        receiver would see it.

        Args:
            cfg: Current RadioConfig (used for center frequency and sample
                rate — tone offsets are computed relative to these).
            n: Number of samples to synthesize per channel.

        Returns:
            np.ndarray: complex64 array shaped (channels, n).
        """
        fs  = float(cfg.sample_rate)
        # Resync to wall clock: the position a continuously-sampling radio
        # would have reached by now (see __init__). Chunks are contiguous
        # internally; between chunks time honestly passes.
        pos = round((time.monotonic() - self._t0) * fs)
        self._pos = pos + n
        idx = pos + np.arange(n, dtype=np.int64)
        detune = float(DEFAULT_CENTER) - float(cfg.center)

        def tone(amp, off_hz):
            # Phase as fractional CYCLES mod 1 in float64. Never build an
            # absolute float32 time axis: its resolution at t=60 s is already
            # ~4 µs, a 60-radian phase step for a MHz tone — the whole demo
            # spectrum dissolved into noise within minutes of server uptime.
            frac = np.mod(idx * (off_hz / fs), 1.0)
            return (amp * np.exp(2j * np.pi * frac.astype(np.float32))
                    ).astype(np.complex64)

        burst_off = DEMO_BURST["offset_hz"] + detune
        burst = None
        if abs(burst_off) <= 0.48 * fs:
            period = max(1, round(DEMO_BURST["period_s"] * fs))
            duty   = max(1, round(DEMO_BURST["duty_s"] * fs))
            burst = tone(DEMO_BURST["amp"], burst_off) * ((idx % period) < duty)

        # Simulated transmit: a demo TX has to SHOW something, or the operator
        # cannot tell a correctly-tuned transmission from a no-op. The carrier
        # is injected into the synthetic IQ at the offset a real receiver would
        # see it, so "did I tune where I meant to" is answerable with no radio
        # and nothing radiated.
        inject = TX.demo_injection()
        tx_tone = None
        if inject is not None:
            tx_off, tx_amp = inject
            if abs(tx_off) <= 0.48 * fs:
                tx_tone = tone(tx_amp, tx_off)

        chans = []
        for i in range(len(state.CHANNELS)):
            tones = DEMO_TONES[i % len(DEMO_TONES)]
            sig = np.zeros(n, dtype=np.complex64)
            for amp, offset_hz in tones:
                off = offset_hz + detune
                if abs(off) <= 0.48 * fs:
                    sig += tone(amp, off)
            if burst is not None:
                sig += burst
            if tx_tone is not None:
                sig += tx_tone
            noise = (self._rng.standard_normal(n)
                     + 1j * self._rng.standard_normal(n)
                     ).astype(np.complex64) * 0.04
            chans.append(sig + noise)
        return np.stack(chans)

    def _ahawi_cycle(self, cfg, pending_op):
        """Demo AHAWI round: synth one coherent chunk, analyze, publish, pace.

        Mirrors `Computer._ahawi_cycle` but synthesizes the coherent span
        instead of reading it from a ring, so it is always `coherent=True`
        (nothing to drain from, nothing to gap). Finishes any pending
        operation once the first capture under the new config publishes,
        with an honest note that demo mode has no hardware readback.

        Args:
            cfg: Current RadioConfig snapshot (must have `cfg.ahawi` set and
                `cfg.backend` in `AHAWI_BACKENDS` — checked by the caller).
            pending_op: Operations-log id awaiting data-path confirmation,
                or None.

        Returns:
            The still-pending op id, or None once finished by this frame
            (or if there was none to begin with).
        """
        plan = ahawi_plan(cfg)
        samples = self._synth_chunk(cfg, plan["need_samples"])
        try:
            blocks, meta = ahawi_capture(samples, cfg, plan)
            meta["ahawi"]["coherent"] = True   # synthesized — no drain to gap
            meta["ahawi"]["capture_t0"] = round(
                time.time() - plan["need_samples"] / float(cfg.sample_rate), 3)
            self._publish(cfg, [blocks[i] for i in range(blocks.shape[0])], meta)
            self.shared.note_good_analysis(cfg)
            if pending_op is not None:
                OPERATIONS.stage(pending_op, "data-path",
                                 "first AHAWI capture computed with the new "
                                 "configuration")
                OPERATIONS.finish(pending_op, "success",
                                  "demo apply confirmed by capture frame "
                                  "(no hardware readback in demo)")
                pending_op = None
        except Exception as e:
            print(f"[demo] ahawi compute error: {e}")
            reverted = self.shared.revert_analysis(str(e))
            if reverted:
                print(f"[demo] reverted analysis params: {reverted}")
            # A failing full-capture compute is expensive — don't respin
            # it at 4 Hz; once per capture cadence is plenty.
            time.sleep(1.0)
            return pending_op
        published = time.time()
        while (time.time() - published < AHAWI_REFRESH_S
               and not self.shared.stopped()
               and not self._pause_requested.is_set()):
            self.shared.service_probe()
            if self.shared.snapshot() != cfg:
                break   # settings changed — replan immediately
            time.sleep(0.05)
        return pending_op

    def run(self):
        """Thread entry point: synthesize IQ and publish frames until stopped.

        Services pause requests (no device to release, just holds until
        resumed) and pending config changes (demo retunes are instantaneous,
        so the operation is staged and finished directly rather than going
        through readback/rearm). Each cycle synthesizes one chunk via
        `_synth_chunk` and runs either the AHAWI path (`_ahawi_cycle`) or the
        rolling `compute_blocks` path, publishing the result and finishing
        any pending operation as data-path proof. A compute error reverts to
        the last-good analysis config (or notifies) rather than stopping the
        loop. Paced to `state.BROADCAST_FPS`.
        """
        last_err_notice = 0.0
        pending_op = None
        print("[demo] Synthetic IQ mode — no radio hardware used.")
        print("[demo] Two CW tones per channel + noise. Controls work normally.")
        print("[demo] Tones are fixed STATIONS near the default center — "
              "retuning moves them across the band like a real signal.")
        print("[demo] A 20 ms-periodic burst (fake SSB) swims in the rolling "
              "view and sits still in AHAWI replay.")

        interval = 1.0 / max(state.BROADCAST_FPS, 1.0)
        next_t = time.time()
        while not self.shared.stopped():
            if self._pause_requested.is_set():
                self._paused.set()
                while (self._pause_requested.is_set()
                       and not self.shared.stopped()):
                    time.sleep(0.05)
                self._paused.clear()
                next_t = time.time()
                continue
            # This is the compute thread in demo mode — serve tier-2 probes here
            # for the same thread-bound-cache reason as the Computer (P2a-5).
            self.shared.service_probe()
            dirty, cfg, op_id, _reconnect, _changed = self.shared.take_dirty()
            if dirty and op_id is not None:
                if pending_op is not None:
                    OPERATIONS.finish(pending_op, "superseded",
                                      f"replaced by op #{op_id}")
                OPERATIONS.stage(op_id, "applying",
                                 "demo device — synthetic source retunes "
                                 "instantly (no hardware)")
                OPERATIONS.stage(op_id, "readback",
                                 "demo device has no driver to query")
                pending_op = op_id

            if cfg.ahawi and cfg.backend in AHAWI_BACKENDS:
                pending_op = self._ahawi_cycle(cfg, pending_op)
                next_t = time.time()
                continue

            # One tone set + noise per channel (P3-2), synthesized with a
            # persistent sample counter (see _synth_chunk).
            samples = self._synth_chunk(cfg, samples_needed(cfg))
            try:
                blocks, meta = compute_blocks(samples, cfg)
                self._publish(cfg, [blocks[i] for i in range(blocks.shape[0])], meta)
                if self.insights is not None:
                    self.insights.update(samples, cfg)
                self.shared.note_good_analysis(cfg)
                if pending_op is not None:
                    OPERATIONS.stage(pending_op, "data-path",
                                     "first frame computed with the new "
                                     "configuration")
                    OPERATIONS.finish(pending_op, "success",
                                      "demo apply confirmed by frame "
                                      "(no hardware readback in demo)")
                    pending_op = None
            except Exception as e:
                # Same backstop as the hardware Computer (P2a-3): revert to the
                # last-good analysis config and keep the demo stream alive.
                print(f"[demo] compute error: {e}")
                reverted = self.shared.revert_analysis(str(e))
                if reverted:
                    print(f"[demo] reverted analysis params: {reverted}")
                elif time.time() - last_err_notice > 5.0:
                    self.shared.push_notice(f"compute error: {e}")
                    last_err_notice = time.time()

            next_t += interval
            dt = next_t - time.time()
            if dt > 0:
                time.sleep(dt)
            else:
                next_t = time.time()
