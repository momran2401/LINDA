"""Mutable runtime device/channel/backend/fps state, configured once at startup.

The frontends call `configure_device()` / `set_fps()` / `set_backend()` (and,
after live discovery, `set_device_label()` / `set_channels()`) before starting
any thread; every other core module reads these module-level attributes at
call time (never `from state import CHANNELS`), so the one assignment made
here at startup is visible everywhere without passing state through every
call.
"""
from __future__ import annotations

import os

from .constants import BACKENDS, DEVICE_PROFILES

DEVICE       = "air8201b"
DEVICE_LABEL = DEVICE_PROFILES[DEVICE]["label"]
CHANNELS     = tuple(DEVICE_PROFILES[DEVICE]["channels"])

BROADCAST_FPS = 15        # default max frames/sec to browsers/clients

# Backend: "calibrated" (striqt PSD/ENBW dB spectrogram), "quicklook" (simple
# FFT dB), "psd" (striqt power_spectral_density statistic traces), or "ssb"
# (striqt 5G SSB spectrogram).
SPEC_BACKEND = os.environ.get("SPEC_BACKEND", "calibrated").strip().lower()
if SPEC_BACKEND not in BACKENDS:
    SPEC_BACKEND = "calibrated"


def configure_device(name: str, channels=None):
    """Select the active device profile, replacing DEVICE/DEVICE_LABEL/CHANNELS.

    Args:
        name: Device profile key present in `core.constants.DEVICE_PROFILES`
            (e.g. "air8201b", "pluto", "soapy", "demo").
        channels: Optional RX port tuple overriding the profile's default
            channel list — used for demo multi-channel testing or when a
            live driver has already discovered the real channel count.

    Raises:
        ValueError: If `name` is not a known device profile.
    """
    global DEVICE, DEVICE_LABEL, CHANNELS
    if name not in DEVICE_PROFILES:
        raise ValueError(f"unknown device {name!r} (known: {sorted(DEVICE_PROFILES)})")
    DEVICE = name
    DEVICE_LABEL = DEVICE_PROFILES[name]["label"]
    CHANNELS = tuple(channels) if channels is not None else tuple(
        DEVICE_PROFILES[name]["channels"]
    )


def set_device_label(label: str):
    """Overwrite DEVICE_LABEL after discovery.

    Args:
        label: New display label (e.g. the profile label with a serial
            number appended).
    """
    global DEVICE_LABEL
    DEVICE_LABEL = str(label)


def set_channels(channels):
    """Overwrite CHANNELS with a live-discovered RX port tuple.

    Args:
        channels: Iterable of RX channel indices reported by the live driver.
    """
    global CHANNELS
    CHANNELS = tuple(channels)


def set_fps(fps: float):
    """Set the broadcast frame-rate ceiling, floored at 0.5 fps.

    Args:
        fps: Requested maximum frames/sec to send to browsers/clients.
    """
    global BROADCAST_FPS
    BROADCAST_FPS = max(float(fps), 0.5)


def set_backend(backend: str):
    """Select the active spectrogram backend.

    Unrecognized values are silently ignored and SPEC_BACKEND is left
    unchanged — this setter never raises.

    Args:
        backend: One of the names in `core.constants.BACKENDS`
            ("calibrated", "quicklook", "psd", "ssb"); matched
            case-insensitively.
    """
    global SPEC_BACKEND
    backend = str(backend).strip().lower()
    if backend in BACKENDS:
        SPEC_BACKEND = backend
