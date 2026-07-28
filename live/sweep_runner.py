#!/usr/bin/env python3
"""Supervised striqt sweep child used by the web recording controller."""
import argparse
import json
import signal
import time


def emit(kind, **fields):
    print(json.dumps({"event": kind, **fields}), flush=True)


def run_sweep(spec_path, output, duration=None, should_stop=lambda: False,
              progress=emit, source=None):
    """Run a repeating sweep with cooperative Stop and progress callbacks."""
    import contextlib

    # core must import before striqt: core.striqt_compat re-execs once to fix
    # LD_LIBRARY_PATH on the AIR-T pixi env before scipy/striqt load.
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
            # Entered first so it unwinds last, after the sink has closed.
            source_spec = stack.enter_context(finite_capture_mode(source))

            # The source registry keys by the exact immutable source spec. Use
            # the spec now in force so sink path formatting and capture
            # expansion resolve the radio ID without constructing a new device.
            spec = spec.replace(source=source_spec)

            if hasattr(bindings, "get_binding"):
                sink_cls = bindings.get_binding(spec).sink
            else:
                sink_cls = bindings.get_controller(spec).sensor.sink_cls
            sink = stack.enter_context(_open_sink(spec, sink_cls, None))
            peripheral = stack.enter_context(peripherals.NoPeripherals(spec))
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
