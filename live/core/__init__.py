"""live/core — shared backend for every LINDA live viewer frontend.

All radio/DSP/config logic used by the web server, kiosk, and standalone
terminal frontends lives under this package; the frontend scripts are thin
wrappers that call into it rather than duplicating backend logic.

Import order matters only in that `striqt_compat` must load first (it
re-execs once to fix LD_LIBRARY_PATH on the AIR-T pixi env before scipy/striqt
import, and applies SoapySDR compatibility patches for non-AIR-T radios);
importing this package guarantees that, since it is imported here before
anything else.

Frontends use:
    from core import state, devices
    from core.config import SharedConfig
    from core.acquisition import Acquirer, Computer, DemoAcquirer
    from core.serialization import serialize_frame, parse_frame
    from core.operations import OPERATIONS
    from core.health import health_snapshot
"""
from . import striqt_compat  # noqa: F401  (must be first — see docstring)

__version__ = "1.0.0"  # core package version; not currently read anywhere else
