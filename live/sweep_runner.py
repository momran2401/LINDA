#!/usr/bin/env python3
"""Runs the striqt capture/analyze/archive sweep behind a Linda recording.

`core/recording.py`'s `RecordingManager` is the caller: on hardware it invokes
`run_sweep()` in a worker thread (via `asyncio.to_thread`), passing the live
`Acquirer`'s already-open `source` object so the sweep runs IN-PROCESS on the
same radio handle — the AIR-T retains FPGA descriptors for the process
lifetime, so a subprocess could never acquire the device. Because the live
view otherwise runs the source `gapless=True` (where striqt treats any
receive overflow as fatal and forbids retries), this module leans on
`core.shims.finite_capture_mode()` to swap in `gapless=False,
receive_retries=2` for the sweep's duration, and disables the RX stream
between pipeline steps so unread DMA data can't accumulate into an overflow
during the analysis/sink gap. This file can also run standalone as a CLI
(`main()`), in which case it opens its own resources from the spec instead of
borrowing a live source.
"""
import argparse
import json
import signal
import time


def emit(kind, **fields):
    """Default progress callback: print one JSON event per line to stdout.

    Used by the standalone CLI entry point (`main()`); `core/recording.py`
    substitutes its own callback when driving `run_sweep()` as a library call.

    Args:
        kind: Event name (`"opened"`, `"progress"`, or `"stopped"`).
        **fields: Event-specific payload, serialized alongside `kind` as a
            single JSON object per line.
    """
    print(json.dumps({"event": kind, **fields}), flush=True)


def run_sweep(spec_path, output, duration=None, should_stop=lambda: False,
              progress=emit, source=None):
    """Run a repeating striqt sweep to `output`, honoring cooperative stop.

    Reads the YAML spec at `spec_path`, retargets its sink to `output` as a
    directory-backed Zarr store (the caller wraps the directory into a
    `.zip` archive by naming `output` with a `.zarr.zip`/`.partial.zarr.zip`
    suffix — this striqt release picks its ZIP wrapper from that suffix), and
    iterates `sensor.iterate_sweep` in a loop until `should_stop()` returns
    True or `duration` seconds have elapsed. When `source` is given (the live
    radio handle), builds a lightweight resource set around it via
    `finite_capture_mode()` instead of opening a second device; otherwise
    opens its own resources from the spec (`sensor.open_resources`), which is
    what the standalone CLI path uses.

    The sink is always closed (via the `ExitStack`) before this function
    returns, because the caller CRC-validates the archive the moment control
    comes back.

    Args:
        spec_path: Path to the YAML sweep spec (`sensor.read_yaml_spec`).
        output: Destination path for the sink; the directory-store target
            whose name determines the archive's final ZIP suffix.
        duration: Total wall-clock seconds to sweep for, or None to run until
            `should_stop()` signals True.
        should_stop: Zero-arg callable polled once per pipeline step;
            returning True requests a stop (honored only once at least 3
            pipeline steps have run, so the first in-flight capture is never
            discarded mid-pipeline).
        progress: Callback invoked as `progress(kind, **fields)` for the
            `"opened"`, `"progress"`, and `"stopped"` events; defaults to
            `emit` (prints JSON to stdout).
        source: An already-open live radio source object to run the sweep
            against in-process, or None to have this function open its own
            resources from the spec.

    Returns:
        dict: `captures` (completed capture count), `pipeline_steps` (total
        generator steps taken), `effective_backend` (array backend actually
        used), and `elapsed_s` (wall-clock seconds spent).
    """
    import contextlib

    # core must import before striqt: core.striqt_compat re-execs once to fix
    # LD_LIBRARY_PATH on the AIR-T pixi env before scipy/striqt load.
    from core.gps import gps_peripherals_class
    from core.shims import enable_stream, finite_capture_mode, open_stream

    import striqt.sensor as sensor

    spec = sensor.read_yaml_spec(spec_path)
    # This striqt release chooses its ZIP wrapper from the suffix while the
    # intermediate Zarr store itself must remain a directory.
    spec = spec.replace(
        sink=spec.sink.replace(path=str(output), store="directory"))
    started = time.monotonic()
    steps = 0

    # The sink must be closed before this function returns: the caller
    # validates the archive's CRC the moment run_sweep hands back.
    with contextlib.ExitStack() as stack:
        if source is None:
            source_spec = spec.source
            resource_context = sensor.open_resources(spec, spec_path)
        else:
            # AIR-T keeps one initialized device singleton per process. Build the
            # remaining lightweight resources around the live source object rather
            # than trying to construct a second radio controller.
            from striqt.sensor.lib import bindings, peripherals
            from striqt.sensor.lib.resources import ConnectionManager, _open_sink

            # The live viewer runs the radio gapless, where striqt raises on any
            # receive overflow and refuses retries. A recording sweep does
            # analysis and archive work between captures, so overflow in those
            # gaps is expected rather than exceptional — run the sweep as an
            # ordinary finite-capture sweep and restore the live spec on exit.
            # The YAML's array_backend request (cupy on hardware) is honored
            # instead of being clobbered by the live spec's numpy.
            # Entered first so it unwinds last, after the sink has closed.
            source_spec = stack.enter_context(finite_capture_mode(
                source,
                array_backend=getattr(spec.source, "array_backend", None)))

            # The source registry keys by the exact immutable source spec. Use
            # the spec now in force so sink path formatting and capture
            # expansion resolve the radio ID without constructing a new device.
            spec = spec.replace(source=source_spec)

            if hasattr(bindings, "get_binding"):
                sink_cls = bindings.get_binding(spec).sink
            else:
                sink_cls = bindings.get_controller(spec).sensor.sink_cls
            sink = stack.enter_context(_open_sink(spec, sink_cls, None))
            # GPS stamps every capture with the current fix through striqt's
            # peripheral slot (extra_data → per-capture xarray variables). It
            # reads a cached snapshot, so an absent or wedged receiver costs
            # the sweep nothing and simply records gps_valid=0.
            peripheral_cls = gps_peripherals_class() or peripherals.NoPeripherals
            peripheral = stack.enter_context(peripheral_cls(spec))
            connection = ConnectionManager(spec)
            connection._resources.update(
                source=source, sink=sink, peripherals=peripheral,
                calibration=None, alias_func=None)
            resources = connection.resources
            captures = sensor.specs.helpers.loop_captures(
                spec, source_id=source.id)
            if captures:
                source.arm_spec(captures[0])
                # arm_spec skips stream recreation when the capture recipe is
                # unchanged; live handoff deliberately closed that stream.
                open_stream(source)

            resource_context = contextlib.nullcontext(resources)

        effective_backend = getattr(source_spec, "array_backend", None)
        progress("opened", effective_backend=effective_backend,
                 gapless=bool(getattr(source_spec, "gapless", False)))

        with resource_context as resources:
            sweep = sensor.iterate_sweep(
                resources, yield_values=False, always_yield=True, loop=True)
            try:
                for _ in sweep:
                    step_started = time.monotonic()
                    steps += 1
                    # iterate_sweep is a three-stage acquire/analyze/sink pipeline.
                    # The first durable capture appears after the third yielded
                    # step; stopping earlier discards in-flight analysis.
                    count = max(0, steps - 2)
                    elapsed = time.monotonic() - started
                    progress("progress", captures=count,
                             elapsed_s=round(elapsed, 3),
                             pipeline_step=steps,
                             step_interval_s=round(step_started - started if steps == 1 else elapsed / steps, 6))
                    # Leaving the stream enabled across the analysis/sink gap
                    # lets unread XDMA data accumulate until the next read
                    # overflows, so quiesce it here; striqt re-enables on the
                    # next acquire. Every future for this pipeline step has
                    # already been joined by the time the generator yields, so
                    # no read is in flight.
                    if source is not None:
                        enable_stream(source, False)
                    limit_hit = should_stop() or (duration and elapsed >= duration)
                    if limit_hit and steps >= 3:
                        break
            finally:
                sweep.close()

        result = {"captures": max(0, steps - 2), "pipeline_steps": steps,
                  "effective_backend": effective_backend,
                  "elapsed_s": round(time.monotonic() - started, 3)}

    progress("stopped", **result)
    return result


def main():
    """Standalone CLI entry point: run a sweep from the command line.

    Parses `spec` (positional YAML path), `--output`, and `--duration`, wires
    `SIGINT`/`SIGTERM` to a cooperative stop flag, and runs `run_sweep()` with
    the default `emit` progress callback (one JSON line per event) and no
    live `source` — i.e. this path always opens its own radio resources from
    the spec rather than borrowing an in-process handle from a running
    server.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("spec")
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration", type=float)
    args = parser.parse_args()
    stopping = False

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    run_sweep(args.spec, args.output, args.duration,
              should_stop=lambda: stopping, progress=emit)


if __name__ == "__main__":
    main()
