"""Process health model.

BOOT_ID is minted once per process — the browser's Reset Radio verification
polls /health until it sees a DIFFERENT boot_id, which is proof the service
actually restarted (a 202 from the old process proves nothing). The snapshot
also reports radio/stream/frame liveness so the Operations tab can show
service health without guessing from frame arrival.
"""
from __future__ import annotations

import time
import uuid

from . import state
from .operations import OPERATIONS

BOOT_ID = uuid.uuid4().hex
STARTED_AT = time.time()

_acquirer = None
_shared = None


def bind(acquirer, shared):
    """Register the live Acquirer/SharedConfig for health_snapshot() to read.

    Called once by the frontend after building the acquisition stack. Before
    this is called, health_snapshot() reports only boot/device/status with no
    radio detail (`_acquirer` is None).

    Args:
        acquirer: The running `core.acquisition.Acquirer` (or `DemoAcquirer`).
        shared: The process's `core.config.SharedConfig` instance.
    """
    global _acquirer, _shared
    _acquirer = acquirer
    _shared = shared


def health_snapshot():
    """Build the current `/health` payload.

    Reports boot id/uptime, the active device/channels, ring-buffer status
    (via `acquirer.ring_status()` when bound), the age of the most recently
    delivered frame, and the last terminal entry from
    `core.operations.OPERATIONS`. Reads only the frame header — never a full
    frame body — so checking health never copies a multi-MB AHAWI capture
    just to read its timestamp.

    Status is "ok" once a frame has arrived within the last 5 seconds;
    otherwise "starting" while no Acquirer is bound or the process is under
    10 seconds old, and "degraded" beyond that with no fresh frame.

    Returns:
        dict: JSON-serializable snapshot for the `/health` endpoint and the
        Operations tab.
    """
    now = time.time()
    out = {
        "status": "starting",
        "boot_id": BOOT_ID,
        "started_at": STARTED_AT,
        "uptime_s": round(now - STARTED_AT, 3),
        "device": {
            "name": state.DEVICE,
            "label": state.DEVICE_LABEL,
            "channels": list(state.CHANNELS),
        },
        "radio": None,
        "last_frame_age_s": None,
        "last_operation": OPERATIONS.last_terminal(),
    }
    if _acquirer is None:
        return out

    ring = getattr(_acquirer, "ring_status", lambda: None)()
    if ring is not None:
        out["radio"] = ring

    header = None
    try:
        # Header only — /health must not copy a multi-MB AHAWI capture just
        # to read a timestamp.
        if hasattr(_acquirer, "latest_header"):
            header = _acquirer.latest_header()
        else:
            header, _ = _acquirer.latest()
    except Exception:
        pass
    if header is not None and header.get("time"):
        out["last_frame_age_s"] = round(now - float(header["time"]), 3)

    if out["last_frame_age_s"] is not None and out["last_frame_age_s"] < 5.0:
        out["status"] = "ok"
    elif now - STARTED_AT < 10.0:
        out["status"] = "starting"
    else:
        out["status"] = "degraded"
    return out
