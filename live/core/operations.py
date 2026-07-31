"""Structured operation log: every radio-affecting action is an Operation.

An Operation is a sequence of timestamped stages ending in a verdict:

    requested → validated → applying → applied → readback → data-path →
    SUCCESS | VERIFIED | MISMATCH | UNVERIFIED | FAILED

Stages print to the terminal as they happen ("[op #7] readback: center
1955.000 MHz") AND are queued as structured events for the frontends (the web
server drains drain_events() into WebSocket {"op": ...} messages; the
Operations tab renders them). The ring keeps the most recent operations for
the /operations endpoint, so a client that connects late still sees history.

Terminal verdict states:
    success      completed; no hardware verification applicable (e.g. rows)
    verified     hardware readback matched the request within tolerance
    unverified   applied, but the driver could not answer a readback query
    mismatch     hardware readback disagreed with the request
    failed       the operation itself errored (arm failed, restart failed...)
    superseded   a newer operation replaced this one before it completed
"""
from __future__ import annotations

import itertools
import threading
import time
from collections import deque

TERMINAL_STATES = {"success", "verified", "unverified", "mismatch", "failed",
                   "superseded"}


class OperationLog:
    """Thread-safe ring buffer of in-flight and completed Operations.

    One process-wide instance (``OPERATIONS`` below) is shared by every
    module that touches the radio. Callers open an operation with `begin`,
    narrate its progress with `stage`, and close it with `finish`; the log
    keeps the most recent ones for the `/operations` endpoint and queues
    stage events for WebSocket fan-out via `drain_events`.
    """

    def __init__(self, keep: int = 200):
        """Args:
            keep: Maximum number of operations retained (oldest evicted
                first, both from the deque and the id index).
        """
        self._lock = threading.Lock()
        self._seq = itertools.count(1)
        self._ops = deque(maxlen=keep)          # completed + running op dicts
        self._by_id = {}
        self._events = []                       # queued events for broadcast

    # -- lifecycle ---------------------------------------------------------

    def begin(self, kind: str, summary: str) -> int:
        """Start a new operation and record its "requested" stage.

        Args:
            kind: Short machine-readable category (e.g. "config", "tx").
            summary: Human-readable one-line description of the request.

        Returns:
            The new operation's id, used for subsequent `stage`/`finish` calls.
        """
        op_id = next(self._seq)
        op = {
            "id": op_id,
            "kind": str(kind),
            "summary": str(summary),
            "state": "running",
            "t_start": time.time(),
            "t_end": None,
            "stages": [],
        }
        with self._lock:
            self._ops.append(op)
            self._by_id[op_id] = op
            # Trim the id index alongside the deque.
            while len(self._by_id) > self._ops.maxlen:
                oldest = min(self._by_id)
                self._by_id.pop(oldest, None)
        self.stage(op_id, "requested", summary)
        return op_id

    def stage(self, op_id, stage: str, detail: str = "", level: str = "info"):
        """Record and print one stage of an operation's progress.

        Unknown or already-evicted op ids are silently tolerated — logging
        must never raise into the radio control path.

        Args:
            op_id: Operation id from `begin`, or None to no-op.
            stage: Stage name (e.g. "validated", "applying", "readback").
            detail: Human-readable detail printed alongside the stage.
            level: "info", "warn", or "error"; only affects the printed tag
                and the level recorded on the queued event.
        """
        if op_id is None:
            return
        entry = {"t": time.time(), "stage": str(stage),
                 "detail": str(detail), "level": str(level)}
        with self._lock:
            op = self._by_id.get(op_id)
            if op is None:
                return
            op["stages"].append(entry)
            self._events.append({"op_id": op_id, "kind": op["kind"],
                                 "state": op["state"], **entry})
            del self._events[:-100]
        tag = "" if level == "info" else f" [{level.upper()}]"
        print(f"[op #{op_id}] {stage}: {detail}{tag}" if detail
              else f"[op #{op_id}] {stage}{tag}")

    def finish(self, op_id, state: str, detail: str = ""):
        """Close an operation with its terminal verdict.

        Args:
            op_id: Operation id from `begin`, or None to no-op.
            state: Verdict; must be one of `TERMINAL_STATES` or it is coerced
                to "success". `finish` also derives the stage's print level
                from it (error for "failed", warn for "mismatch"/"unverified").
            detail: Human-readable detail for the verdict stage.
        """
        if op_id is None:
            return
        state = state if state in TERMINAL_STATES else "success"
        level = ("error" if state == "failed"
                 else "warn" if state in ("mismatch", "unverified")
                 else "info")
        with self._lock:
            op = self._by_id.get(op_id)
            if op is not None:
                op["state"] = state
                op["t_end"] = time.time()
        self.stage(op_id, state.upper(), detail, level=level)

    # -- readout -----------------------------------------------------------

    def recent(self, n: int = 50):
        """Return the most recent operations, newest last.

        Args:
            n: Maximum number of operations to return.

        Returns:
            A list of shallow-copied operation dicts (each with its own
            "stages" list copy), safe for callers to serialize or mutate.
        """
        with self._lock:
            return [dict(op, stages=list(op["stages"]))
                    for op in list(self._ops)[-n:]]

    def get(self, op_id):
        """Look up a single operation by id.

        Args:
            op_id: Operation id from `begin`.

        Returns:
            A shallow-copied operation dict, or None if unknown/evicted.
        """
        with self._lock:
            op = self._by_id.get(op_id)
            return dict(op, stages=list(op["stages"])) if op else None

    def drain_events(self):
        """Take and clear the queued stage events, for WS fan-out.

        Same drain-and-clear contract as `SharedConfig.drain_notices`: each
        call empties the internal queue, so events are delivered at most once.

        Returns:
            The list of queued event dicts (each has "op_id", "kind", "state"
            plus the stage's "t"/"stage"/"detail"/"level").
        """
        with self._lock:
            events, self._events = self._events, []
            return events

    def set_fields(self, op_id, fields):
        """Record which config fields this operation changed.

        The Acquirer scopes hardware readback verification to this list, so
        e.g. a rows-only change is never judged by an unrelated (and
        possibly missing) gain getter.

        Args:
            op_id: Operation id from `begin`, or None to no-op.
            fields: Iterable of changed field names; stored as strings.
        """
        with self._lock:
            op = self._by_id.get(op_id)
            if op is not None:
                op["fields"] = [str(f) for f in fields]

    def fields(self, op_id):
        """Look up the changed-field list recorded by `set_fields`.

        Args:
            op_id: Operation id from `begin`.

        Returns:
            A list of field names, or None if unknown or never set (callers
            treat None as "no scoping info available" and do a full check).
        """
        with self._lock:
            op = self._by_id.get(op_id)
            return list(op["fields"]) if op and "fields" in op else None

    def last_terminal(self):
        """Find the most recently finished operation, for `/health`.

        Returns:
            A dict with "id", "kind", "summary", "state", "t_end" for the
            newest operation whose state is in `TERMINAL_STATES`, or None if
            no operation has finished yet.
        """
        with self._lock:
            for op in reversed(self._ops):
                if op["state"] in TERMINAL_STATES:
                    return {"id": op["id"], "kind": op["kind"],
                            "summary": op["summary"], "state": op["state"],
                            "t_end": op["t_end"]}
        return None


OPERATIONS = OperationLog()


# -- shared helpers used by config/acquisition ------------------------------

def fmt_value(key, value):
    """Format a config value with human-readable units for op-log detail text.

    Args:
        key: Config field name (e.g. "center", "sample_rate", "gain").
        value: The field's value; coerced to float for the known keys.

    Returns:
        A unit-suffixed string (e.g. "1955 MHz") for recognized keys, else
        `repr(value)` unchanged.
    """
    try:
        if key in ("center", "center_frequency"):
            return f"{float(value)/1e6:.6g} MHz"
        if key in ("sample_rate", "backend_sample_rate"):
            return f"{float(value)/1e6:.6g} MS/s"
        if key == "gain":
            return f"{float(value):.1f} dB"
    except (TypeError, ValueError):
        pass
    return repr(value)


def verdict_state(verdicts):
    """Collapse per-field readback verdicts into one overall operation state.

    Args:
        verdicts: Iterable of per-field verdict dicts, each with a "state"
            key (e.g. "verified", "mismatch", "readback_unsupported").

    Returns:
        "mismatch" if any field mismatched; "unverified" if every field
        reported "readback_unsupported"; otherwise "verified".
    """
    states = {v["state"] for v in verdicts}
    if "mismatch" in states:
        return "mismatch"
    if states == {"readback_unsupported"}:
        return "unverified"
    return "verified"
