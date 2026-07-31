#!/usr/bin/env python3
"""
Web-based live spectrogram + PSD viewer server (frontend).

This is Linda's canonical web UI backend. All radio/DSP/config logic lives in
live/core/ — this file only wires that logic to the outside world: role-based
auth (signed session cookie + HTTP Basic), the FastAPI HTTP routes (config,
presets, GPS, recording, transmit, admin reset), the `/ws` WebSocket that
streams live frames and control acks, the `/ws/logs` journal tail, and the
`main()` CLI entry point that resolves the device and starts uvicorn. Any
other frontend (terminal, kiosk standalone) drives the same live/core/ objects
through its own thin wrapper; per the repo's architecture rule, a backend bug
belongs in live/core/, never patched here.

Usage:
    python live/striqt_web_server.py                     # AIR8201B radio
    python live/striqt_web_server.py --demo              # synthetic IQ
    python live/striqt_web_server.py --device auto       # enumerate SoapySDR
    python live/striqt_web_server.py --device pluto      # PlutoSDR
    python live/striqt_web_server.py --device driver=plutosdr,serial=XYZ
    python live/striqt_web_server.py --quantize          # uint8 frames

Convenience launcher (adds optional Cloudflare Tunnel):
    bash live/run_web.sh
"""

import argparse
import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from fractions import Fraction
from contextlib import asynccontextmanager
from pathlib import Path

# live/core is importable relative to this file, regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# core.striqt_compat (imported first, via the package) handles the AIR-T pixi
# LD_LIBRARY_PATH re-exec before any scipy/striqt import.
from core import devices, gps, health, state, tx
from core.acquisition import Acquirer, Computer, DemoAcquirer
from core.config import SharedConfig
from core.constants import (BACKENDS, CALIBRATED_GRID_BACKENDS, DEVICE_PROFILES,
                            QUALIFIED_MAX_RATE_HZ)
from core.dsp import aligned_nfft, allowed_rates
from core.operations import OPERATIONS
from core.recording import RecordingManager
from core.insights import InsightService, calibration_status
from core.presets import PRESETS, public_presets
from core.serialization import serialize_frame
from core.shims import seal_open_fds_for_exec
from core.striqt_compat import _ANALYSIS_OK, _SENSOR_OK

# FastAPI
try:
    from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import (
        HTMLResponse,
        FileResponse,
        JSONResponse,
        PlainTextResponse,
        RedirectResponse,
    )
    from fastapi.staticfiles import StaticFiles
except ImportError:
    print(
        "FastAPI not installed. Run:\n"
        "  pip install fastapi 'uvicorn[standard]'",
        file=sys.stderr,
    )
    sys.exit(1)

WEB_DIR = Path(__file__).parent / "web"

# ---------------------------------------------------------------------------
# Username-to-role authentication
# ---------------------------------------------------------------------------
#
# The viewer (static page, assets, and the /ws WebSocket) is gated behind one of
# three logins, each mapping to a role:
#
#   admin   → full control (only ONE admin connected at a time)
#   viewer  → read-only; every control shows an "access denied" popup
#   interns → read-only; same, with a different popup message
#
# Each username maps directly to a role. Passwords are intentionally not used:
# entering "admin", "viewer", or "intern" selects that role. The resulting
# session cookie is still HMAC-signed, so its role cannot be edited client-side.
_ROLE_USERS = {
    "admin":   os.environ.get("ADMIN_USER")  or "admin",
    "viewer":  os.environ.get("VIEWER_USER") or "viewer",
    "interns": os.environ.get("INTERN_USER") or "intern",
}
WRITE_ROLES   = frozenset({"admin"})            # roles allowed to mutate config
AUTH_DISABLED = os.environ.get("RADIO_AUTH_DISABLE") == "1"
DEFAULT_ROLE  = "admin"                          # role granted when auth disabled
AUTH_ENABLED  = not AUTH_DISABLED
AUTH_REALM    = "striqt live viewer"

# systemd unit the "Reset Radio" admin action restarts (overridable per host).
RADIO_SERVICE_NAME = os.environ.get("RADIO_SERVICE_NAME") or "radio-web"


def match_username(user) -> "str | None":
    """Resolve a plain username to its role, in constant time.

    There are no passwords: the username alone selects the role. Every row in
    `_ROLE_USERS` is compared with `secrets.compare_digest` and none is
    skipped early, so the loop's timing does not leak which row (if any)
    matched.

    Args:
        user: The username string to resolve.

    Returns:
        The matching role name (e.g. "admin"), or None if it matches no
        configured login.
    """
    matched_role = None
    for role, known_user in _ROLE_USERS.items():
        if secrets.compare_digest(user, known_user):
            matched_role = role
    return matched_role


def authenticate(auth_header) -> "str | None":
    """Resolve an HTTP `Authorization` header to a role name.

    Only the `Basic` scheme is accepted; the password field is decoded but
    intentionally ignored since usernames alone carry the role. When auth is
    globally disabled (`RADIO_AUTH_DISABLE=1`), always returns `DEFAULT_ROLE`
    so `--demo` / local dev keeps full control without any header at all.

    Args:
        auth_header: The header value, as a str (from a Starlette `Request`)
            or bytes (from a raw ASGI scope).

    Returns:
        The resolved role name, or None if the header is missing, not Basic,
        malformed, or names an unknown user.
    """
    if AUTH_DISABLED:
        return DEFAULT_ROLE
    if not auth_header:
        return None
    if isinstance(auth_header, bytes):
        auth_header = auth_header.decode("latin-1")

    scheme, _, param = auth_header.partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        user, _, _ignored_password = base64.b64decode(param).decode("utf-8").partition(":")
    except Exception:
        return None

    return match_username(user)


# ---------------------------------------------------------------------------
# Signed session cookie
# ---------------------------------------------------------------------------
#
# Safari and every iOS browser refuse to replay HTTP Basic credentials on the
# WebSocket upgrade handshake, so a Basic-Auth-only gate locks those clients out
# of /ws even after they log in for the page. To fix this, once an HTTP request
# authenticates we hand the browser a signed "radio_auth" cookie; the cookie is
# carried automatically on the subsequent WS handshake and accepted there.
#
# The token now carries the authenticated ROLE (not just an expiry) so the role
# survives the cookie-only path that Safari/iOS use for the WS upgrade. The role
# is inside the HMAC, so a viewer cannot self-elevate by editing the cookie.
#
# The signing secret comes from RADIO_SESSION_SECRET when set; otherwise a
# RANDOM per-process key is generated.
#
# The old fallback derived the key from the role/user mapping — i.e. from
# "admin", "viewer", "intern", the documented defaults. Anyone who read the
# README could recompute the key and mint a valid admin cookie, which was
# verified in practice against a demo server: a forged cookie retuned the
# radio. A random key cannot be guessed. The cost is that sessions do not
# survive a restart (everyone signs in again), which is a visible, harmless
# inconvenience — unlike a signing key that is public by construction.
#
# Production setup still generates RADIO_SESSION_SECRET so sessions persist
# across service restarts.
_SESSION_SECRET_IS_EPHEMERAL = not os.environ.get("RADIO_SESSION_SECRET")
_SESSION_SECRET = hashlib.sha256(
    (os.environ.get("RADIO_SESSION_SECRET")
     or secrets.token_hex(32)
    ).encode()
).digest()
SESSION_TTL = 86400


def make_session_token(role: str, ttl_seconds: int = SESSION_TTL) -> str:
    """Build a signed session token for the `radio_auth` cookie.

    Token format is `"<role>.<exp>.<hex_hmac>"`, where `exp` is a Unix
    timestamp and `hex_hmac = HMAC-SHA256(_SESSION_SECRET, "<role>.<exp>")`.
    Covering the role with the MAC means a client cannot self-elevate by
    editing the cookie value.

    Args:
        role: The authenticated role to embed ("admin", "viewer", "interns").
        ttl_seconds: Seconds until the token expires. Defaults to
            `SESSION_TTL` (24 hours).

    Returns:
        The signed token string.
    """
    exp = int(time.time()) + ttl_seconds
    payload = f"{role}.{exp}"
    mac = hmac.new(_SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"

def verify_session_token(token) -> "str | None":
    """Validate a `"<role>.<exp>.<hex_hmac>"` session token.

    Recomputes the HMAC with a constant-time comparison, confirms the role is
    one of the known logins, and confirms the expiry is still in the future.

    Args:
        token: The raw cookie value to validate.

    Returns:
        The embedded role on success, or None if the token is missing,
        malformed, tampered with, names an unknown role, or has expired.
    """
    if not token:
        return None
    if isinstance(token, bytes):
        token = token.decode("latin-1")

    role, _, rest = token.partition(".")
    exp_str, _, mac = rest.partition(".")
    if not role or not mac:
        return None
    if role not in _ROLE_USERS:          # reject forged / unknown roles
        return None
    try:
        exp = int(exp_str)
    except ValueError:
        return None

    payload = f"{role}.{exp_str}"
    expected = hmac.new(
        _SESSION_SECRET, payload.encode(), hashlib.sha256
    ).hexdigest()
    if not secrets.compare_digest(mac, expected):
        return None
    if exp <= int(time.time()):
        return None
    return role


def _session_cookie_from_scope(scope) -> "str | None":
    """Extract and validate the `radio_auth` cookie from a raw ASGI scope.

    Args:
        scope: The ASGI connection scope (http or websocket), whose `headers`
            list is scanned for a `Cookie` header.

    Returns:
        The role from the cookie's session token when present and valid
        (see `verify_session_token`), else None.
    """
    headers = dict(scope.get("headers") or [])
    raw_cookie = headers.get(b"cookie")
    if not raw_cookie:
        return None
    cookie_str = raw_cookie.decode("latin-1")
    for part in cookie_str.split(";"):
        name, _, value = part.strip().partition("=")
        if name == "radio_auth":
            return verify_session_token(value)
    return None


class BasicAuthMiddleware:
    """Pure-ASGI middleware gating every http and websocket request.

    Wraps the whole FastAPI app, so mounted static files and the `/ws`
    endpoint are covered along with every route. Accepts either an HTTP Basic
    `Authorization` header (username only; password ignored — see
    `authenticate`) or a signed `radio_auth` session cookie (see
    `_session_cookie_from_scope`), and resolves both to a role stashed on
    `scope["role"]`/`scope["user"]` for downstream handlers.

    On failure:
      - http      → redirect to the username login form (or a plain 401 for
                    non-GET/API-ish requests).
      - websocket → the handshake is rejected with close code 1008 before
                    `accept()`.
    """

    def __init__(self, app):
        """Store the wrapped ASGI application.

        Args:
            app: The next ASGI callable in the middleware stack.
        """
        self.app = app

    @staticmethod
    def _set_cookie_send(scope, send, role):
        """Wrap an ASGI `send` to attach a fresh session cookie on success.

        Appends a `Set-Cookie` header carrying a new role-bearing session
        token to the HTTP response start message. Only called on the
        authenticated path, so the cookie is never attached to a 401. The
        `Secure` attribute is omitted over plain HTTP (LAN) so Safari/iOS —
        which refuse to store a `Secure` cookie without TLS and won't replay
        Basic auth on the WS upgrade — can still reach `/ws`. `HttpOnly` and
        `SameSite=Lax` are always set.

        Args:
            scope: The current ASGI scope, used to detect https.
            send: The ASGI `send` callable to wrap.
            role: The role to encode into the new session token.

        Returns:
            An async callable with the same signature as `send`.
        """
        headers_in = dict(scope.get("headers") or [])
        is_https = (
            scope.get("scheme") == "https"
            or headers_in.get(b"x-forwarded-proto") == b"https"
        )
        secure_attr = "Secure; " if is_https else ""

        async def wrapped(message):
            """Forward `message`, injecting the Set-Cookie header on response start."""
            if message["type"] == "http.response.start":
                cookie = (
                    f"radio_auth={make_session_token(role)}; Path=/; HttpOnly; "
                    f"{secure_attr}SameSite=Lax; Max-Age={SESSION_TTL}"
                )
                headers = list(message.get("headers") or [])
                headers.append((b"set-cookie", cookie.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        return wrapped

    # Paths that must be reachable WITHOUT authentication so the login flow can
    # work: the login form/handler and the logout endpoint. Everything else is
    # gated. (The WS 1008 path and page redirect below both skip these.)
    # /health is public for monitoring + restart polling, but the endpoint
    # returns only the minimal liveness triple when no role resolved.
    _PUBLIC_PATHS = frozenset({"/login", "/logout", "/health"})

    async def __call__(self, scope, receive, send):
        """Resolve a role for this connection and dispatch or reject it.

        Order of resolution: auth-disabled bypass, then the always-reachable
        public paths (`_PUBLIC_PATHS`), then Basic header, then session
        cookie. HTTP responses on the authenticated path get a refreshed
        session cookie attached (see `_set_cookie_send`); websocket scopes
        never get one. Unauthenticated websockets are closed with code 1008;
        unauthenticated HTML GETs are redirected to `/login`; everything else
        unauthenticated gets a 401.

        Args:
            scope: The ASGI connection scope (`scope["type"]` is `"http"`,
                `"websocket"`, or `"lifespan"`).
            receive: The ASGI receive callable, passed through unchanged.
            send: The ASGI send callable, possibly wrapped to attach a cookie.
        """
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if AUTH_DISABLED:
            # Auth off (demo/local): everyone gets DEFAULT_ROLE so the endpoint
            # always sees a role and controls aren't silently locked out.
            scope["role"] = DEFAULT_ROLE
            scope["user"] = DEFAULT_ROLE
            await self.app(scope, receive, send)
            return

        # The login/logout routes are always reachable so an unauthenticated (or
        # signing-out) browser can complete the flow. They set/clear the cookie
        # themselves; the middleware just gets out of the way. A role is still
        # resolved opportunistically so /health can answer richly for
        # authenticated callers while staying reachable for anonymous ones.
        if scope["type"] == "http" and scope.get("path") in self._PUBLIC_PATHS:
            headers = dict(scope.get("headers") or [])
            role = (authenticate(headers.get(b"authorization"))
                    or _session_cookie_from_scope(scope))
            if role:
                scope["role"] = role
                scope["user"] = role
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        # Resolve the role from the Basic username, falling back to a valid
        # signed session cookie. The cookie path lets browsers that drop Basic
        # creds on the WS upgrade (Safari / all iOS) still connect to /ws after
        # logging in for the page.
        role = authenticate(headers.get(b"authorization")) or _session_cookie_from_scope(scope)
        if role:
            # The same dict is ws.scope / request.scope in the endpoint, so this
            # is how the role reaches ws_endpoint.
            scope["role"] = role
            scope["user"] = role
            if scope["type"] == "http":
                # Refresh the role-bearing cookie so the browser carries it on
                # the WS handshake. Never set it on websocket scopes.
                await self.app(scope, receive, self._set_cookie_send(scope, send, role))
            else:
                await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # Reject the upgrade before accept(); no credentials means no frames.
            await send({"type": "websocket.close", "code": 1008})
            return

        # Unauthenticated page/asset request. Browsers get redirected to the
        # login FORM (303) instead of a Basic 401 challenge — that way browsers
        # never cache Basic credentials and the signed cookie becomes their sole
        # credential, which makes sign-out / switch-user reliable. A Basic header
        # is still ACCEPTED above (so `curl -u` and API clients keep working); we
        # just no longer CHALLENGE with it. Non-GET / API-ish requests get a plain
        # 401 rather than a redirect they can't follow.
        method = (scope.get("method") or "GET").upper()
        accept = dict(scope.get("headers") or []).get(b"accept", b"").decode("latin-1")
        wants_html = method == "GET" and ("text/html" in accept or accept in ("", "*/*"))
        if wants_html:
            await send({
                "type": "http.response.start",
                "status": 303,
                "headers": [
                    (b"location", b"/login"),
                    (b"content-length", b"0"),
                ],
            })
            await send({"type": "http.response.body", "body": b""})
            return

        body = b"401 Unauthorized"
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"www-authenticate", f'Basic realm="{AUTH_REALM}"'.encode("latin-1")),
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        })
        await send({"type": "http.response.body", "body": body})


class NoCacheMiddleware:
    """Pure-ASGI middleware that disables HTTP caching on every response.

    Stamps `Cache-Control: no-store`, `Pragma: no-cache`, and `Expires: 0` on
    every HTTP response so browsers always refetch the page and static
    assets instead of serving a stale cached copy. WebSocket and other scope
    types pass straight through untouched.
    """

    def __init__(self, app):
        """Store the wrapped ASGI application.

        Args:
            app: The next ASGI callable in the middleware stack.
        """
        self.app = app

    async def __call__(self, scope, receive, send):
        """Pass the request through, rewriting cache headers on the response.

        Args:
            scope: The ASGI connection scope.
            receive: The ASGI receive callable, passed through unchanged.
            send: The ASGI send callable, wrapped for http scopes to strip
                any existing cache headers and add the no-store set.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            """Forward `message`, replacing any cache headers on response start."""
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for (k, v) in message.get("headers") or []
                    if k.lower() not in (b"cache-control", b"expires", b"pragma")
                ]
                headers.append((b"cache-control", b"no-store, max-age=0"))
                headers.append((b"pragma", b"no-cache"))
                headers.append((b"expires", b"0"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

# Module-level globals set in main() before uvicorn starts
_acquirer = None            # Acquirer | DemoAcquirer
_computer = None            # Computer | None
_shared   = None            # SharedConfig
_quantize = False
_connections: set = set()   # ALL clients (broadcast fan-out set)
_slot_lock = asyncio.Lock() # guards the single-admin slot
_admin_ws  = None           # the one active admin socket, or None
_recording = None           # RecordingManager
_insights  = None           # InsightService

# Recording/TX status is broadcast on change; this is the slow keepalive that
# still refreshes elapsed/remaining counters when nothing else has moved.
STATUS_KEEPALIVE_S = 2.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: start acquisition/compute/broadcast, then tear down.

    On startup, starts `_acquirer` (and `_computer`, when the device is not a
    self-computing `DemoAcquirer`), connects the GPS reader, waits briefly for
    the first frame, and spawns the `_broadcaster` task. On shutdown, kills
    any active transmission first (a dying process cannot retract radiated
    RF, unlike every other shutdown step, which can be retried), then stops
    recording, GPS, and config threads, cancels the broadcaster, and joins
    the acquisition/compute threads.

    `asyncio.CancelledError` is swallowed explicitly during the broadcaster
    join: it is a `BaseException`, so a bare `except Exception` here used to
    let Ctrl-C surface as "Application shutdown failed" with a traceback.

    Args:
        app: The FastAPI application instance (required by the lifespan
            protocol; unused directly here since state lives in module
            globals set by `main()`).

    Yields:
        None. Control returns to FastAPI while the app serves requests.
    """
    _acquirer.start()
    if _computer is not None:
        _computer.start()
    # Connect to gpsd now rather than on the first /gps request, so the Record
    # tab shows a real fix (or a real error) instead of "connecting" to
    # whoever happens to look first. Recordings never wait on this.
    gps_reader = gps.get_reader()
    if gps_reader is not None:
        print(f"[gps] watching gpsd at {gps_reader.host}:{gps_reader.port}")
    # Give the radio (or demo) a moment to produce the first frame
    await asyncio.sleep(1.2)
    task = asyncio.create_task(_broadcaster())
    print(f"[ws] broadcaster running at {state.BROADCAST_FPS} fps")
    try:
        yield
    finally:
        # Kill the carrier FIRST. Every other shutdown step can be retried; a
        # transmitter left keyed by a dying process cannot.
        tx.TX.shutdown()
        if _recording is not None:
            await _recording.shutdown()
        if gps_reader is not None:
            gps_reader.stop()
        _shared.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError,
                                 Exception):
            await asyncio.wait_for(task, timeout=0.5)
        if _computer is not None:
            _computer.join(timeout=3.0)
        _acquirer.join(timeout=3.0)


app = FastAPI(title="striqt live viewer", lifespan=lifespan)

# Gate the whole app (static page, assets, and /ws) behind the auth middleware.
app.add_middleware(BasicAuthMiddleware)
app.add_middleware(NoCacheMiddleware)


def _json_safe(obj):
    """Recursively coerce a value tree into plain JSON-serializable types.

    Handles dicts, lists/tuples, non-finite floats (NaN/inf → None, since
    JSON has no representation for them), `Fraction` (→ str), and any object
    exposing a numpy-style `.item()` (→ its Python scalar). Used to sanitize
    every JSON response and outgoing WS message built from striqt/numpy
    values.

    Args:
        obj: Any value, typically a dict/list tree possibly containing numpy
            scalars, `Fraction`s, or non-finite floats.

    Returns:
        An equivalent structure containing only JSON-safe types.
    """
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, Fraction):
        return str(obj)
    if hasattr(obj, "item"):
        try:
            return _json_safe(obj.item())
        except (TypeError, ValueError):
            pass
    return obj


def capture_editor_schema():
    """Build the JSON schema of the striqt sweep-spec class for `/schema`.

    Looks up the sweep-spec dataclass for the current device binding (falling
    back to the air8201b binding for non-AIR-T devices, since the capture
    editor's fields are shared across radios) and converts it to a JSON
    schema via striqt's own helper, then runs it through `_json_safe`.

    Returns:
        A JSON-safe dict: the schema the browser's capture-settings editor
        is built from.

    Raises:
        RuntimeError: If no sweep-spec class can be located on the binding.
    """
    from striqt.sensor import bindings
    from striqt.analysis.specs.helpers import json_schema

    binding_name = state.DEVICE if state.DEVICE.startswith("air") else "air8201b"
    binding = getattr(bindings, binding_name, bindings.air8201b)
    sweep_cls = getattr(binding, "sweep_spec", None)
    if sweep_cls is None:
        sensor = getattr(binding, "sensor", None)
        sweep_cls = getattr(sensor, "sweep_spec_cls", None)
    if sweep_cls is None:
        raise RuntimeError("Unable to locate air8201b sweep schema")
    return _json_safe(json_schema(sweep_cls))


@app.get("/schema")
async def schema_endpoint():
    """Serve the capture-editor JSON schema (see `capture_editor_schema`).

    striqt may be absent when running `--demo` on a machine without the SDR
    stack, so failures are answered with a clean 503 (the client logs it and
    skips the capture editor) instead of an unhandled 500 traceback on every
    page load.

    Returns:
        JSONResponse: The schema on success (200), or `{"error": ...}` with
        status 503 when the schema cannot be built.
    """
    try:
        return JSONResponse(capture_editor_schema())
    except Exception as exc:
        return JSONResponse(
            {"error": f"capture schema unavailable: {exc}"}, status_code=503
        )


def current_config():
    """Build a JSON snapshot of the live `SharedConfig` for the browser.

    The browser seeds its settings forms from this instead of the striqt
    schema defaults, so a bare Apply re-sends the server's own current
    values — this avoids silently flipping untouched fields whose schema
    default differs from the server's actual default (e.g. `host_resample`
    true vs false). Also used as the re-sync source after every
    settings/analysis ack, and embedded in `/record` and preset-apply
    responses.

    Returns:
        A JSON-safe dict with `capture`, `analysis`, `analysis_psd`,
        `analysis_ssb`, `source`, `device`, `envelope`, `backend`, `rows`,
        `lo_null`, `ahawi`, and `calibration` keys describing the current
        radio configuration.
    """
    cfg = _shared.snapshot()
    # The analysis pipelines always execute on the aligned 28-multiple grid, so
    # the resolutions reported for their blocks use it regardless of backend.
    nfft_exec = aligned_nfft(cfg.nfft)
    window = list(cfg.window) if isinstance(cfg.window, tuple) else cfg.window
    integration = cfg.integration_bandwidth
    if not (integration is None or isinstance(integration, str)):
        integration = float(integration)
    psd_window = (list(cfg.psd_window) if isinstance(cfg.psd_window, tuple)
                  else cfg.psd_window)
    psd_integration = cfg.psd_integration_bandwidth
    if not (psd_integration is None or isinstance(psd_integration, str)):
        psd_integration = float(psd_integration)
    return _json_safe({
        "capture": {
            "center_frequency":    float(cfg.center),
            "sample_rate":         float(cfg.sample_rate),
            "gain":                float(cfg.gain),
            "analysis_bandwidth":  float(cfg.analysis_bandwidth),
            "lo_shift":            str(cfg.lo_shift),
            "host_resample":       bool(cfg.host_resample),
            "backend_sample_rate": float(cfg.backend_sample_rate),
            "duration":            float(cfg.duration),
            "nfft":                int(cfg.nfft),
        },
        "analysis": {
            "window":                window,
            "frequency_resolution":  float(cfg.sample_rate) / nfft_exec,
            "fractional_overlap":    str(cfg.fractional_overlap),
            "window_fill":           str(cfg.window_fill),
            "integration_bandwidth": integration,
            "lo_bandstop":           float(cfg.lo_bandstop) if cfg.lo_bandstop else None,
            "trim_stopband":         bool(cfg.trim_stopband),
            "time_aperture":         float(cfg.time_aperture) if cfg.time_aperture else None,
        },
        "analysis_psd": {
            "window":                psd_window,
            "frequency_resolution":  float(cfg.sample_rate) / nfft_exec,
            "fractional_overlap":    str(cfg.psd_fractional_overlap),
            "window_fill":           str(cfg.psd_window_fill),
            "integration_bandwidth": psd_integration,
            "lo_bandstop":           float(cfg.psd_lo_bandstop) if cfg.psd_lo_bandstop else None,
            "trim_stopband":         bool(cfg.psd_trim_stopband),
            "time_statistic":        [s if isinstance(s, str) else float(s)
                                      for s in cfg.psd_time_statistic],
        },
        "analysis_ssb": {
            "subcarrier_spacing":    float(cfg.ssb_subcarrier_spacing),
            "sample_rate":           float(cfg.ssb_sample_rate),
            "discovery_periodicity": float(cfg.ssb_discovery_periodicity),
            "frequency_offset":      float(cfg.ssb_frequency_offset),
            "max_block_count":       (int(cfg.ssb_max_block_count)
                                      if cfg.ssb_max_block_count else None),
            "window":                (list(cfg.ssb_window)
                                      if isinstance(cfg.ssb_window, tuple)
                                      else cfg.ssb_window),
            "lo_bandstop":           (float(cfg.ssb_lo_bandstop)
                                      if cfg.ssb_lo_bandstop else None),
        },
        "source": dict(cfg.source_config or {}),
        "device": devices.get_adapter().describe_capabilities(),
        "envelope": _shared.envelope(),
        # The sample rates this radio will actually accept — the driver's own
        # discrete list when it enumerates one, else the cellular grid clipped
        # to its envelope. The client offers exactly these and warns above
        # `qualified_max_rate`, the highest rate hardware_qual has sustained.
        "rates": list(allowed_rates(_shared.envelope())),
        "qualified_max_rate": QUALIFIED_MAX_RATE_HZ,
        "backend": str(cfg.backend),
        "rows":    int(cfg.rows),
        "lo_null": bool(cfg.lo_null),
        "ahawi": {
            "enabled":    bool(cfg.ahawi),
            "capture_ms": float(cfg.ahawi_capture_ms),
            "align":      bool(cfg.ahawi_align),
        },
        "calibration": calibration_status(cfg),
    })


@app.get("/config")
async def config_endpoint():
    """Return the current radio/analysis configuration as JSON.

    Returns:
        JSONResponse: The `current_config()` snapshot, readable by any
        authenticated role.
    """
    return JSONResponse(current_config())


@app.get("/health")
async def health_endpoint(request: Request):
    """Report liveness and process identity.

    `boot_id` changes on every process start, which is the browser's proof
    that Reset Radio actually restarted the service (it polls this endpoint
    until `boot_id` differs from the value it started with). Auth-exempt so
    monitoring and restart polling from the login page can always reach it,
    but an anonymous caller (no resolved role, auth enabled) only gets the
    minimal liveness triple — richer health detail requires being logged in.

    Args:
        request: The incoming request; `request.scope["role"]` (set by
            `BasicAuthMiddleware`) gates how much detail is returned.

    Returns:
        JSONResponse: The full `health.health_snapshot()` plus `service` for
        an authenticated caller, or `{"status", "boot_id", "uptime_s"}` only
        for an anonymous one.
    """
    snap = health.health_snapshot()
    snap["service"] = RADIO_SERVICE_NAME
    if request.scope.get("role") is None and not AUTH_DISABLED:
        snap = {"status": snap["status"], "boot_id": snap["boot_id"],
                "uptime_s": snap["uptime_s"]}
    return JSONResponse(_json_safe(snap))


@app.get("/operations")
async def operations_endpoint():
    """Return the most recent verified-operations log entries.

    Backs the OPS tab's backfill (live updates arrive separately over `/ws`
    as `{"op": ...}` messages). Readable by any authenticated role.

    Returns:
        JSONResponse: `{"operations": [...]}`, the last 50 entries from
        `OPERATIONS`.
    """
    return JSONResponse(_json_safe({"operations": OPERATIONS.recent(50)}))


@app.get("/insights")
async def insights_endpoint():
    """Return the latest native striqt power/occupancy/cell/provenance results.

    Returns:
        JSONResponse: The current `InsightService` snapshot.
    """
    return JSONResponse(_json_safe(_insights.snapshot()))


@app.get("/presets")
async def presets_endpoint():
    """List the presets exposed to the browser's preset picker.

    Returns:
        JSONResponse: `{"presets": [...]}` from `public_presets()`.
    """
    return JSONResponse(_json_safe({"presets": public_presets()}))


@app.post("/presets/{preset_id}/apply")
async def preset_apply_endpoint(preset_id: str, request: Request):
    """Apply a named preset's control payload to the shared config.

    Admin only, and refused while a recording is in progress (the same
    controls-lock every config-mutating route enforces).

    Args:
        preset_id: Key into `PRESETS`.
        request: The incoming request; only `request.scope["role"]` is read.

    Returns:
        JSONResponse: `{"preset", "ack", "effective"}` (200) on success;
        `{"error": ...}` with status 403 (not admin), 409 (recording active),
        404 (unknown preset id), or 400 (config update rejected the preset's
        control payload).
    """
    if request.scope.get("role", DEFAULT_ROLE) not in WRITE_ROLES:
        return JSONResponse({"error": "admin privileges required"}, status_code=403)
    if _recording.active():
        return JSONResponse({"error": "controls are locked while recording"}, status_code=409)
    preset = PRESETS.get(preset_id)
    if preset is None:
        return JSONResponse({"error": "unknown preset"}, status_code=404)
    try:
        ack = await asyncio.to_thread(_shared.update, preset["control"])
        _insights.configure(cell_enabled=preset.get("cell_detection", False))
        return JSONResponse(_json_safe({"preset": preset_id, "ack": ack,
                                        "effective": current_config()}))
    except (ValueError, TypeError, AttributeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/gps")
async def gps_status_endpoint():
    """Return the live GPS fix and, when invalid, why.

    Recordings stamp every capture with this position (or `gps_valid=0` when
    there is no fix, never null-island 0.0/0.0) — check this endpoint before
    starting a long recording run.

    Returns:
        JSONResponse: `{"gps": ...}` from `gps.status()`, including the fix
        fields or an explanation (daemon unreachable / no device / no fix
        yet / stale).
    """
    return JSONResponse(_json_safe({"gps": gps.status()}))


# ---------------------------------------------------------------------------
# Transmit mode
# ---------------------------------------------------------------------------
#
# The legal notice lives HERE, server-side, and acknowledging it is a real API
# call — a modal the browser can delete from the DOM is not a gate. /tx/start
# refuses until the caller has acknowledged in this server process.

TX_DISCLAIMER = {
    "title": "Transmit mode",
    "body": [
        "This keys the radio's TX port and radiates real RF into whatever is "
        "connected to it.",

        "Transmitting on frequencies you are not licensed or authorized to use "
        "is a federal offense. The FCC will come for your ass and they WILL "
        "find you. Unlicensed emissions can also jam safety-of-life services — "
        "GPS, aviation, public safety — which carries consequences well past a "
        "fine.",

        "You alone are responsible for making every transmission lawful where "
        "you are: an appropriate license, an ISM band inside its power limits, "
        "or a shielded enclosure or dummy load. NIST and this repository's "
        "maintainers accept no responsibility for what you do with this "
        "button.",

        "Connect an antenna or a 50 Ω load before transmitting. Keying a power "
        "amplifier into an open port can damage the radio.",

        "Every transmission is logged: frequency, power, waveform, duration, "
        "and operator.",
    ],
    "accept": "I have a license or a dummy load — arm TX",
    "decline": "Take me back",
}


@app.get("/tx")
async def tx_status_endpoint(request: Request):
    """Return TX capability, live transmission state, and the legal notice.

    Any authenticated role may read this — a shared instrument that is
    radiating should say so to everyone connected to it, not just the
    operator driving it.

    Args:
        request: The incoming request; `request.scope["role"]` determines
            `acknowledged` (per-role, see `tx.TX.is_acknowledged`) and
            `may_transmit`.

    Returns:
        JSONResponse: `{"tx": {..., "acknowledged", "may_transmit",
        "disclaimer"}}`.
    """
    role = request.scope.get("role", DEFAULT_ROLE)
    status = tx.TX.status()
    status["acknowledged"] = tx.TX.is_acknowledged(role)
    status["may_transmit"] = role in WRITE_ROLES
    status["disclaimer"] = TX_DISCLAIMER
    return JSONResponse(_json_safe({"tx": status}))


@app.post("/tx/acknowledge")
async def tx_acknowledge_endpoint(request: Request):
    """Record that the calling admin accepted the transmit legal notice.

    This is the real gate behind the "bad boy" button's disclaimer modal: a
    modal the browser could delete from the DOM would not be one, so
    `/tx/start` checks server-side acknowledgment (via `tx.TX.is_acknowledged`)
    rather than trusting the client. Logs a `validated` operation entry.

    Args:
        request: The incoming request; only `request.scope["role"]` is read.

    Returns:
        JSONResponse: `{"tx": {"acknowledged": True}}` (200), or
        `{"error": ...}` with 403 if the caller is not admin.
    """
    if request.scope.get("role", DEFAULT_ROLE) not in WRITE_ROLES:
        return JSONResponse({"error": "admin privileges required"}, status_code=403)
    role = request.scope.get("role", DEFAULT_ROLE)
    tx.TX.acknowledge(role)
    OPERATIONS.stage(
        OPERATIONS.begin("tx", f"transmit legal notice acknowledged by {role}"),
        "validated", "operator accepted responsibility for lawful transmission")
    return JSONResponse(_json_safe({"tx": {"acknowledged": True}}))


@app.post("/tx/start")
async def tx_start_endpoint(request: Request):
    """Start a transmission with the requested waveform/frequency/gain.

    Admin only. Mutually exclusive with recording — a recording sweep
    reconfigures the very source object a TX stream would ride on, so both
    directions are refused with 409. Delegates the actual arm/tune sequence
    to `tx.TX.start`, which runs in a worker thread since it blocks on driver
    calls.

    Args:
        request: The incoming request; the JSON body is the waveform/start
            payload (`tx.TX.start`'s `payload` argument), and
            `request.scope["role"]` gates access.

    Returns:
        JSONResponse: `{"tx": status}` with status 202 once transmission has
        started; `{"error": ...}` with status 403 (not admin), 409
        (recording active, or `tx.TX.start` raised `RuntimeError`), 428 (the
        legal notice has not been acknowledged in this process — see
        `tx_acknowledge_endpoint`), or 400 (malformed body or `ValueError`
        from `tx.TX.start`, e.g. an out-of-range frequency).
    """
    role = request.scope.get("role", DEFAULT_ROLE)
    if role not in WRITE_ROLES:
        return JSONResponse({"error": "admin privileges required"}, status_code=403)
    if _recording.active():
        # One owner of the radio at a time. Recording swaps the source spec out
        # from under the live view (core.shims.finite_capture_mode); a TX stream
        # opened across that is a handle to a source being reconfigured.
        return JSONResponse(
            {"error": "cannot transmit while a recording is running"},
            status_code=409)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        status = await asyncio.to_thread(tx.TX.start, payload, role)
        return JSONResponse(_json_safe({"tx": status}), status_code=202)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=428)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)


@app.post("/tx/stop")
async def tx_stop_endpoint(request: Request):
    """Stop any active transmission.

    Admin only.

    Args:
        request: The incoming request; only `request.scope["role"]` is read.

    Returns:
        JSONResponse: `{"tx": status}` with status 202, or `{"error": ...}`
        with 403 if the caller is not admin.
    """
    if request.scope.get("role", DEFAULT_ROLE) not in WRITE_ROLES:
        return JSONResponse({"error": "admin privileges required"}, status_code=403)
    status = await asyncio.to_thread(tx.TX.stop, "stopped by operator")
    return JSONResponse(_json_safe({"tx": status}), status_code=202)


@app.get("/record")
async def record_status_endpoint():
    """Return recording state plus a form seed for the Record tab.

    Returns:
        JSONResponse: `{"recording", "defaults", "gps", "config"}` — current
        `RecordingManager` status, its default sweep parameters, the live GPS
        fix, and the current radio config (so the Record form can seed
        itself from what the live view is already doing).
    """
    return JSONResponse(_json_safe({
        "recording": _recording.status(),
        "defaults": _recording.defaults(),
        "gps": gps.status(),
        "config": current_config(),
    }))


@app.post("/record")
async def record_start_endpoint(request: Request):
    """Start a recording sweep from the JSON body's parameters.

    Admin only. Mutually exclusive with transmitting — a recording sweep
    reconfigures the same source object a TX stream would ride on, mirroring
    the guard in `tx_start_endpoint`.

    Args:
        request: The incoming request; the JSON body is passed to
            `RecordingManager.start`.

    Returns:
        JSONResponse: `{"recording": result}` with status 202 once the sweep
        has started; `{"error": ...}` with status 403 (not admin), 409
        (already transmitting, or already recording), or 400 (malformed body
        or a validation error from `RecordingManager.start`).
    """
    if request.scope.get("role", DEFAULT_ROLE) not in WRITE_ROLES:
        return JSONResponse({"error": "admin privileges required"}, status_code=403)
    if tx.TX.active():
        # Mirror of the guard in /tx/start — the recording sweep reconfigures
        # the very source object the TX stream is riding on.
        return JSONResponse(
            {"error": "cannot record while transmitting — stop TX first"},
            status_code=409)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        result = await _recording.start(payload)
        return JSONResponse({"recording": _json_safe(result)}, status_code=202)
    except (ValueError, TypeError, RuntimeError, OSError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=409 if _recording.active() else 400)


@app.post("/record/stop")
async def record_stop_endpoint(request: Request):
    """Stop the active recording sweep, if any.

    Admin only.

    Args:
        request: The incoming request; only `request.scope["role"]` is read.

    Returns:
        JSONResponse: `{"recording": status}` with status 202, or
        `{"error": ...}` with 403 if the caller is not admin.
    """
    if request.scope.get("role", DEFAULT_ROLE) not in WRITE_ROLES:
        return JSONResponse({"error": "admin privileges required"}, status_code=403)
    return JSONResponse({"recording": _json_safe(await _recording.stop())}, status_code=202)


@app.get("/recordings")
async def recordings_endpoint():
    """List archived recordings for the pull tools and the Recordings tab.

    The directory walk + per-archive stat runs in a worker thread
    (`asyncio.to_thread`) so a large populated recordings tree cannot stall
    every other client's event-loop turn.

    Returns:
        JSONResponse: `{"recordings": [...]}` from `RecordingManager.catalog`.
    """
    rows = await asyncio.to_thread(_recording.catalog)
    return JSONResponse(_json_safe({"recordings": rows}))


@app.get("/recordings/{recording_id:path}/inspect")
async def recording_inspect_endpoint(recording_id: str):
    """Verify one archived recording end to end and report its contents.

    Runs `RecordingManager.inspect` (a CRC verification of the whole zip) in
    a worker thread, since it is a full-archive read.

    Args:
        recording_id: Catalog id / relative path of the recording.

    Returns:
        JSONResponse: The inspection result on success, or `{"error": ...}`
        with status 404 if the id is unknown, the file is missing, or the
        archive is corrupt.
    """
    try:
        result = await asyncio.to_thread(_recording.inspect, recording_id)
        return JSONResponse(_json_safe(result))
    except (ValueError, FileNotFoundError, OSError, zipfile.BadZipFile) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)


@app.get("/recordings/{recording_id:path}/download")
async def recording_download_endpoint(recording_id: str):
    """Stream one archived recording's zip file to the caller.

    Backs `tools/pull_recordings.py`'s HTTP pull path (the alternative to
    the rsync-over-SSH `tools/fetch_recordings.sh`).

    Args:
        recording_id: Catalog id / relative path of the recording.

    Returns:
        FileResponse: The zip archive, or a JSONResponse `{"error": ...}`
        with status 404 if the id cannot be resolved to a file.
    """
    try:
        path = _recording.resolve_catalog_item(recording_id)
        return FileResponse(path, filename=path.name,
                            media_type="application/zip")
    except (ValueError, FileNotFoundError, OSError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)


@app.post("/config")
async def config_apply_endpoint(request: Request):
    """Apply a control payload to the shared config over plain HTTP.

    HTTP twin of the WebSocket control path (admin only) — the same
    validated `SharedConfig.update` call, producing the same ack (including
    `op_id`). Exists for scripted clients: `live/radioctl.py` drives its
    `set` and `self-test` commands through this rather than opening a
    WebSocket.

    Args:
        request: The incoming request; the JSON body is the control payload
            passed to `SharedConfig.update`.

    Returns:
        JSONResponse: `{"ack": ack}` on success; `{"error": ...}` with
        status 403 (not admin), 409 (recording active), or 400 (malformed
        body or a validation error from `SharedConfig.update`).
    """
    if request.scope.get("role", DEFAULT_ROLE) not in WRITE_ROLES:
        return JSONResponse({"error": "admin privileges required"}, status_code=403)
    if _recording.active():
        return JSONResponse({"error": "controls are locked while recording"}, status_code=409)
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
    except Exception as exc:  # malformed body
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        ack = await asyncio.get_running_loop().run_in_executor(
            None, _shared.update, payload
        )
        return JSONResponse({"ack": _json_safe(ack)})
    except (ValueError, TypeError, AttributeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.websocket("/ws/logs")
async def logs_ws_endpoint(ws: WebSocket):
    """Stream the systemd journal tail for the OPS tab (admin only).

    Follows `journalctl -u <RADIO_SERVICE_NAME> -f` and forwards each line as
    a `{"journal": "..."}` text message. Structured operation events already
    arrive on the main `/ws` socket as `{"op": ...}`; this adds the raw
    service log the user asked to see — a log view, never a shell. When
    `journalctl` is unavailable, sends one explanatory message and then holds
    the socket open with periodic pings so the client does not reconnect-spin.

    Args:
        ws: The WebSocket connection. Closed with code 1008 immediately if
            `ws.scope["role"]` is not in `WRITE_ROLES`.
    """
    role = ws.scope.get("role", DEFAULT_ROLE)
    if role not in WRITE_ROLES:
        await ws.close(code=1008)
        return
    await ws.accept()
    journalctl = shutil.which("journalctl")
    if not journalctl:
        await ws.send_text(json.dumps(
            {"journal": "(journalctl not available on this host — "
                        "operation events above are the full log)"}))
        # Keep the socket open but idle so the client doesn't reconnect-spin.
        try:
            while True:
                await asyncio.sleep(30)
                await ws.send_text(json.dumps({"journal_ping": True}))
        except Exception:
            return
    proc = None
    read_task = None
    disconnect_task = None
    try:
        # The deployed AIR-T runtime has been observed to ignore Popen's
        # close_fds boundary for driver-created inheritable descriptors.  Seal
        # everything already open before forking; CLOEXEC then makes the kernel
        # enforce the boundary and prevents journalctl from owning XDMA.
        seal_open_fds_for_exec()
        proc = await asyncio.create_subprocess_exec(
            journalctl, "-u", RADIO_SERVICE_NAME, "-n", "200", "-f",
            "--no-pager", "-o", "short-iso",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # The AIR-T driver does not mark every XDMA descriptor CLOEXEC.
            # Without this explicit boundary the journal follower can retain
            # /dev/xdma0_c2h_0 after a retune closes the live RX stream,
            # causing the replacement stream to fail with EBUSY.
            close_fds=True,
        )
        read_task = asyncio.create_task(proc.stdout.readline())
        disconnect_task = asyncio.create_task(ws.receive())
        while True:
            done, _ = await asyncio.wait(
                {read_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_task in done:
                break
            raw = read_task.result()
            if not raw:
                if proc.returncode is not None:
                    await ws.send_text(json.dumps(
                        {"journal": f"(journal tail ended, rc={proc.returncode})"}))
                    break
                await asyncio.sleep(0.3)
                read_task = asyncio.create_task(proc.stdout.readline())
                continue
            await ws.send_text(json.dumps(
                {"journal": raw.decode("utf-8", "replace").rstrip()}))
            read_task = asyncio.create_task(proc.stdout.readline())
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as exc:  # noqa: BLE001
        with contextlib.suppress(Exception):
            await ws.send_text(json.dumps({"journal": f"journal unavailable: {exc}"}))
    finally:
        for task in (read_task, disconnect_task):
            if task is not None and not task.done():
                task.cancel()
        if proc is not None and proc.returncode is None:
            proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=1.0)

# ---------------------------------------------------------------------------
# Login / logout (cookie-based session; see BasicAuthMiddleware)
# ---------------------------------------------------------------------------
#
# The browser path is cookie-only: unauthenticated page loads are redirected to
# /login (by the middleware) instead of a Basic-Auth 401 challenge, so browsers
# never cache Basic credentials. That makes sign-out / switch-user reliable —
# /logout just clears the cookie. A Basic header is still accepted for curl/API.

def _login_page(error: str = "") -> str:
    """Render the standalone login page HTML.

    Self-contained (styles inlined) because the app's own `style.css` is
    served behind the auth gate this page sits in front of. The form posts
    the username only — there is no password field.

    Args:
        error: Optional error message to display above the form (e.g.
            "Unknown username.").

    Returns:
        A complete HTML document as a string.
    """
    err_html = (
        f'<p class="err">{error}</p>' if error else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>Sign in · Live IQ Navigation & Display Application</title>
<style>
  :root {{ --bg:#0b0f14; --panel:#111823; --border:#22303f; --text:#e6edf3;
          --dim:#8aa0b3; --accent:#4ea3ff; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center;
          justify-content:center; background:var(--bg); color:var(--text);
          font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  .card {{ width:min(92vw,360px); background:var(--panel);
           border:1px solid var(--border); border-radius:14px; padding:26px 24px;
           box-shadow:0 12px 48px rgba(0,0,0,0.5); }}
  h1 {{ font-size:19px; margin:0 0 2px; letter-spacing:0.01em; }}
  /* LINDA lockup, 16px variant — mirrors .brand-lockup--lg in web/style.css:
     a five-bar PSD envelope and the wordmark standing on one shared axis. */
  .lockup {{ display:flex; align-items:flex-end; gap:9px; padding-bottom:4px;
             margin:0 0 18px; border-bottom:1px solid var(--accent); }}
  .mark {{ display:flex; align-items:flex-end; gap:2px; height:14px; flex:0 0 auto; }}
  .mark i {{ width:2.5px; background:var(--accent); }}
  .mark i:nth-child(1) {{ height:5px;  opacity:0.40; }}
  .mark i:nth-child(2) {{ height:8px;  opacity:0.62; }}
  .mark i:nth-child(3) {{ height:14px; }}
  .mark i:nth-child(4) {{ height:7px;  opacity:0.62; }}
  .mark i:nth-child(5) {{ height:4px;  opacity:0.40; }}
  .wordmark {{ font-size:16px; font-weight:700; letter-spacing:0.16em;
               line-height:0.86; color:var(--text); }}
  .sub {{ color:var(--dim); font-size:12px; margin:0 0 20px; }}
  label {{ display:block; font-size:12px; color:var(--dim); margin:14px 0 5px; }}
  input {{ width:100%; padding:10px 12px; background:var(--bg);
           border:1px solid var(--border); border-radius:8px; color:var(--text);
           font-size:15px; }}
  input:focus {{ outline:none; border-color:var(--accent); }}
  button {{ width:100%; margin-top:20px; padding:11px; background:var(--accent);
            border:none; border-radius:8px; color:#04121f; font-size:15px;
            font-weight:700; cursor:pointer; }}
  .err {{ background:rgba(255,96,96,0.12); border:1px solid #ff6060; color:#ffb3b3;
          padding:8px 10px; border-radius:8px; font-size:13px; margin:0 0 4px; }}
</style></head><body>
  <form class="card" method="post" action="/login" autocomplete="off">
    <div class="lockup">
      <span class="mark" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span>
      <span class="wordmark">LINDA</span>
    </div>
    <h1>Live IQ Navigation & Display Application</h1>
    <p class="sub">National Institute of Standards and Technology</p>
    {err_html}
    <label for="u">Username</label>
    <input id="u" name="username" type="text" autofocus>
    <button type="submit">Sign in</button>
  </form>
</body></html>"""


def _cookie_kwargs(request: "Request") -> dict:
    """Build `Response.set_cookie` kwargs for the session cookie.

    Mirrors `BasicAuthMiddleware._set_cookie_send`: `HttpOnly`, `SameSite=Lax`,
    and `Secure` only over HTTPS (omitted on plain-HTTP LAN so Safari/iOS
    still store the cookie).

    Args:
        request: The incoming request, used to detect https via
            `request.url.scheme` or the `X-Forwarded-Proto` header.

    Returns:
        A dict of keyword arguments for `Response.set_cookie`.
    """
    is_https = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
    )
    return dict(
        path="/", httponly=True, samesite="lax",
        secure=is_https, max_age=SESSION_TTL,
    )


@app.get("/login")
async def login_form(request: "Request"):
    """Serve the login form, or bounce straight to the viewer if unneeded.

    Args:
        request: The incoming request, used to check for an existing valid
            session cookie.

    Returns:
        A redirect to `/` when auth is disabled or a valid session cookie is
        already present; otherwise the rendered login page.
    """
    if AUTH_DISABLED:
        return RedirectResponse("/", status_code=303)
    if _session_cookie_from_scope(request.scope):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(_login_page())


@app.post("/login")
async def login_submit(request: "Request"):
    """Handle the login form submission: resolve a role and set the cookie.

    Parses the urlencoded body directly rather than via `request.form()`, to
    avoid pulling in the `python-multipart` dependency that method requires
    (the login form only ever posts
    `application/x-www-form-urlencoded`).

    Args:
        request: The incoming request; its body is the urlencoded
            `username` field.

    Returns:
        A redirect to `/` with a signed `radio_auth` cookie set, on a known
        username; an 401 re-render of the login page with an error message
        otherwise. Redirects straight to `/` with no cookie when auth is
        disabled.
    """
    if AUTH_DISABLED:
        return RedirectResponse("/", status_code=303)
    from urllib.parse import parse_qs

    raw = (await request.body()).decode("utf-8", "replace")
    form = parse_qs(raw, keep_blank_values=True)
    role = match_username((form.get("username") or [""])[0])
    if not role:
        return HTMLResponse(
            _login_page("Unknown username."), status_code=401
        )
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("radio_auth", make_session_token(role), **_cookie_kwargs(request))
    return resp


@app.get("/logout")
async def logout(request: "Request"):
    """Clear the session cookie and redirect to the login form.

    Args:
        request: The incoming request (unused beyond routing).

    Returns:
        A redirect to `/login` with the `radio_auth` cookie deleted.
    """
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("radio_auth", path="/")
    return resp


@app.post("/admin/reset-radio")
async def reset_radio(request: Request):
    """Restart the radio's systemd service, as a verified operation.

    Admin only. Where the old implementation spawned
    `sudo -n systemctl restart …` with both pipes on `/dev/null` and answered
    202 unconditionally (proving only that `Popen()` succeeded), this:

      1. Preflights a matching NOPASSWD sudoers rule via `systemctl restart`.
         When the rule is absent but this process is verifiably inside the
         requested systemd unit (`_supervised_by_requested_unit`), it falls
         back to a self-SIGTERM and lets `Restart=always` replace it —
         manually launched processes never take that fallback.
      2. Returns the 202 response with an operation id and THIS process's
         `boot_id`.
      3. Relies on the browser polling `/health` until it sees a DIFFERENT
         `boot_id` (proof the service really restarted and came back), or
         timing out and reporting exactly which stage failed.

    The restart still detaches (`start_new_session`) because it tears down
    this very process — final confirmation necessarily happens in the NEW
    process, via the boot_id change. Any active transmission is stopped
    first, since a restart would otherwise leave it keyed with nothing left
    to unkey it.

    Args:
        request: The incoming request; only `request.scope["role"]` and
            `request.client` (for the operation log) are read.

    Returns:
        JSONResponse: `{"message", "op_id", "boot_id"}` with status 202 once
        the restart is confirmed permitted and under way (either via sudo or
        the self-restart fallback); `{"error": ..., "op_id"}` with status
        403 (not admin) or 500 (sudo/systemctl missing, preflight denied, or
        the restart command failed to spawn or exited non-zero).
    """
    role = request.scope.get("role", DEFAULT_ROLE)
    if role not in WRITE_ROLES:
        return JSONResponse({"error": "admin privileges required"}, status_code=403)

    # A restart tears this process down; anything still keyed goes with it, so
    # unkey deliberately and log it rather than letting SIGTERM decide.
    if tx.TX.active():
        await asyncio.to_thread(tx.TX.stop, "radio reset requested")

    op_id = OPERATIONS.begin("reset", f"restart service {RADIO_SERVICE_NAME}")

    def _supervised_by_requested_unit():
        """Check whether this process's systemd cgroup matches the target unit.

        Returns:
            True only when `/proc/self/cgroup` shows this process running
            inside `RADIO_SERVICE_NAME`'s systemd unit; False on any read
            failure or mismatch.
        """
        unit = RADIO_SERVICE_NAME
        if not unit.endswith(".service"):
            unit += ".service"
        try:
            cgroups = Path("/proc/self/cgroup").read_text(encoding="utf-8")
        except OSError:
            return False
        return any(line.rsplit(":", 1)[-1].rstrip("/").endswith("/" + unit)
                   for line in cgroups.splitlines())

    def _supervised_self_restart(reason):
        """Fall back to a self-SIGTERM restart under systemd's `Restart=always`.

        Used when sudo/systemctl are unavailable or unpermitted but this
        process is confirmed (`_supervised_by_requested_unit`) to run inside
        the target systemd unit, so systemd itself can replace it — no
        host-specific sudoers rule required. A manually launched server is
        never killed by this path, since the cgroup check would fail for it.
        Returns before the scheduled SIGTERM fires so the browser still
        receives this (old) process's `boot_id` in the response.

        Args:
            reason: Why the sudo path was not used, logged to the operation.

        Returns:
            JSONResponse: `{"message", "op_id", "boot_id"}` with status 202.
        """
        OPERATIONS.stage(
            op_id, "validated",
            f"systemd supervises this process; using self-restart fallback ({reason})")
        OPERATIONS.stage(
            op_id, "detached",
            "SIGTERM scheduled; systemd will replace this process and the browser will verify boot_id")
        asyncio.get_running_loop().call_later(
            0.5, os.kill, os.getpid(), signal.SIGTERM)
        return JSONResponse(
            {"message": f"restarting {RADIO_SERVICE_NAME}…",
             "op_id": op_id, "boot_id": health.BOOT_ID},
            status_code=202,
        )

    sudo_path = shutil.which("sudo")
    systemctl_path = shutil.which("systemctl")
    if not sudo_path or not systemctl_path:
        if _supervised_by_requested_unit():
            return _supervised_self_restart("sudo/systemctl unavailable")
        OPERATIONS.finish(op_id, "failed", "sudo/systemctl not found on this host")
        return JSONResponse(
            {"error": "sudo/systemctl not found on this host", "op_id": op_id},
            status_code=500,
        )
    cmd = [sudo_path, "-n", systemctl_path, "restart", RADIO_SERVICE_NAME]
    OPERATIONS.stage(op_id, "applying",
                     f"{' '.join(cmd)} (requested by {request.client})")

    def _preflight():
        """Run `sudo -n -l` to list this user's complete sudo rule set.

        `sudo -l <command>` returning 0 only proves the command is permitted
        *with a password*; it does not prove `sudo -n` (non-interactive) can
        run it. Listing every rule and pattern-matching for an explicit
        NOPASSWD entry (below) is the only way to confirm that.

        Returns:
            The completed `subprocess.CompletedProcess`, or the caught
            exception instance if the subprocess call itself failed.
        """
        try:
            return subprocess.run(
                [sudo_path, "-n", "-l"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=3,
            )
        except Exception as e:  # noqa: BLE001
            return e

    pre = await asyncio.get_running_loop().run_in_executor(None, _preflight)
    listing = "" if isinstance(pre, Exception) else (pre.stdout or "")
    # Match the COMMAND, not the absolute path it was written with. The sudoers
    # installer resolves systemctl with `command -v` at install time and this
    # process resolves it with shutil.which at run time; on a host where those
    # disagree (/bin/systemctl vs /usr/bin/systemctl) a perfectly good rule was
    # reported as "no matching NOPASSWD sudoers rule".
    _rule = re.compile(
        r"NOPASSWD:.*(?:^|/)systemctl\s+restart\s+"
        + re.escape(RADIO_SERVICE_NAME) + r"(?:\.service)?\s*$"
    )
    passwordless = any(_rule.search(line.strip()) for line in listing.splitlines())
    if isinstance(pre, Exception) or pre.returncode != 0 or not passwordless:
        reason = (str(pre) if isinstance(pre, Exception)
                  else "no matching NOPASSWD sudoers rule")
        if _supervised_by_requested_unit():
            return _supervised_self_restart(reason)
        OPERATIONS.finish(op_id, "failed", f"sudo preflight: {reason}")
        return JSONResponse(
            {"error": f"not permitted: {reason} — run "
                      f"live/install_radio_web_sudoers.sh on the host",
             "op_id": op_id},
            status_code=500,
        )
    OPERATIONS.stage(op_id, "validated", "sudoers rule allows the restart")

    # stderr goes to a PERSISTENT log (survives this process being replaced):
    # RADIO_RESET_LOG, set by the systemd unit to /var/log/radio-web/reset.log.
    log_path = Path(os.environ.get(
        "RADIO_RESET_LOG",
        "/tmp/{}-reset.log".format(RADIO_SERVICE_NAME.replace("/", "_")),
    ))
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "ab", buffering=0)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
            start_new_session=True,
        )
        log_file.close()
    except Exception as e:  # noqa: BLE001 — surface any spawn failure
        OPERATIONS.finish(op_id, "failed", f"spawn failed: {e}")
        return JSONResponse({"error": f"restart failed: {e}", "op_id": op_id},
                            status_code=500)

    def _probe():
        """Give the spawned restart command a short window to fail fast.

        If `proc` is still running once the window elapses, the restart is
        genuinely in flight (and about to kill this very process) — that is
        the success path. If it already exited, its logged stderr tail is
        read back for the error response.

        Returns:
            A `(returncode, stderr_tail)` tuple: `(None, b"")` if the process
            is still running after the timeout, else `(rc, last ~2000 bytes
            of the log file)`.
        """
        try:
            rc = proc.wait(timeout=1.2)
        except subprocess.TimeoutExpired:
            return None, b""
        err = b""
        try:
            err = log_path.read_bytes()[-2000:]
        except Exception:
            pass
        return rc, err

    rc, err_bytes = await asyncio.get_running_loop().run_in_executor(None, _probe)
    err_text = err_bytes.decode("utf-8", "replace").strip()

    if rc is not None and rc != 0:
        detail = f"systemctl exited {rc}" + (f": {err_text}" if err_text else "")
        OPERATIONS.finish(op_id, "failed", detail)
        return JSONResponse({"error": detail, "op_id": op_id}, status_code=500)

    if rc == 0:
        # systemctl returned success but this process is still alive — the
        # restarted unit may not be the one serving this page. The client's
        # boot_id poll settles it either way; disclose the ambiguity.
        OPERATIONS.stage(op_id, "applied",
                         "systemctl returned 0 while this process is still "
                         "alive — if the boot_id below never changes, "
                         "RADIO_SERVICE_NAME does not match this server's unit",
                         level="warn")
    else:
        OPERATIONS.stage(op_id, "detached",
                         "restart in flight; this process is about to be "
                         "replaced — the browser verifies via /health boot_id")
    return JSONResponse(
        {
            "message": f"restarting {RADIO_SERVICE_NAME}…",
            "op_id": op_id,
            "boot_id": health.BOOT_ID,
        },
        status_code=202,
    )


async def _broadcaster():
    """Background task: pace frames, serialize once, fan out to all clients.

    Runs for the lifetime of the app (spawned by `lifespan`). Each tick:
    drains and sends queued server notices (`SharedConfig.drain_notices`)
    and structured operation events (`OPERATIONS.drain_events`) as JSON text;
    sends recording/TX status as JSON text, but only on change, on a slow
    keepalive interval (`STATUS_KEEPALIVE_S`), or to a newly joined client —
    resending identical status at the full broadcast rate was pure overhead
    on the hotspot/tunnel links these modes exist for; then pulls the latest
    frame via `_acquirer.latest_if_newer` (skipping the multi-MB block copy
    when nothing changed), serializes it once with `serialize_frame`, and
    sends it as bytes to every connection. Dead connections found during
    sending are pruned from `_connections` at the end of the tick.

    Pacing is against an absolute deadline (`next_tick`) rather than a fixed
    post-work sleep, since the latter folded serialization/send time into the
    frame period and turned a requested 15 FPS into roughly 13-14 FPS; a
    stall larger than one interval resets the deadline instead of trying to
    repay it with a burst of ticks.
    """
    interval   = 1.0 / max(state.BROADCAST_FPS, 1)
    loop       = asyncio.get_running_loop()
    next_tick  = loop.time()
    last_t     = 0.0
    last_diag  = 0.0   # throttle the heartbeat log to ~once/sec
    # Change-detection state for the recording/TX status messages (see below).
    last_recording_json = None
    last_tx_json        = None
    last_status_push    = 0.0
    status_sent_to      = set()

    while True:
        # Pace against an absolute deadline.  Sleeping ``interval`` after all
        # serialization/send work made that work part of the frame period and
        # consistently turned a requested 15 FPS into roughly 13–14 FPS.
        next_tick += interval
        delay = next_tick - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        else:
            # Do not spin trying to repay a large scheduling stall.  Resume
            # from the current clock while retaining normal one-frame jitter.
            if delay < -interval:
                next_tick = loop.time()
            await asyncio.sleep(0)

        if not _connections:
            # Drain event queues even with no viewers so they don't go stale.
            OPERATIONS.drain_events()
            _shared.drain_notices()
            continue

        texts = []
        # Queued server notices (compute-backstop reverts etc.) — P2a-3.
        for notice in _shared.drain_notices():
            texts.append(json.dumps({"message": f"[server] {notice}"}))
        # Structured operation stage events for the Operations tab.
        for ev in OPERATIONS.drain_events():
            texts.append(json.dumps({"op": _json_safe(ev)}))

        # Recording + TX status are sent ON CHANGE, not on every tick. Re-sending
        # both unconditionally at BROADCAST_FPS was ~30 identical JSON messages
        # per second per client — pure overhead on exactly the hotspot/tunnel
        # links these modes exist for. A slow keepalive still refreshes elapsed
        # counters, and a newly joined client forces a resend so it never waits
        # for the banner that says the radio is transmitting.
        joined = set(_connections) - status_sent_to
        status_sent_to = set(_connections)
        recording_json = json.dumps({"recording": _json_safe(_recording.status())})
        tx_json = json.dumps({"tx": _json_safe(tx.TX.status())})
        now_mono = loop.time()
        due = now_mono - last_status_push >= STATUS_KEEPALIVE_S
        if joined or due or recording_json != last_recording_json:
            texts.append(recording_json)
            last_recording_json = recording_json
        # TX state goes to EVERY client, not just the admin driving it. People
        # sharing an instrument are entitled to know it is radiating, and
        # read-only roles need it to raise their standby banner.
        if joined or due or tx_json != last_tx_json:
            texts.append(tx_json)
            last_tx_json = tx_json
        if due or joined:
            last_status_push = now_mono
        for text in texts:
            for ws in list(_connections):
                try:
                    await ws.send_text(text)
                except Exception:
                    pass   # dropped clients are pruned by the frame loop below

        # Copy-on-new-frame only: latest_if_newer skips the (potentially
        # multi-MB, AHAWI) block copy on the common same-frame tick.
        header, blocks = _acquirer.latest_if_newer(last_t)

        now    = time.time()
        diag   = now - last_diag > 1.0   # throttled heartbeat this tick?
        if diag:
            last_diag = now

        if header is None:
            if diag:
                print(f"[ws] tick: no new frame  clients={len(_connections)}")
            continue
        last_t = frame_t = header.get("time", 0.0)

        try:
            # AHAWI captures are always quantized regardless of --quantize: a
            # float32 multi-segment capture is ~12 MB per message (~70 Mb/s at
            # the capture cadence) — hostile to the hotspot/tunnel modes for
            # zero display benefit. The header discloses dtype + scale as
            # always, and one scale per message IS the per-capture pinned
            # color contract.
            msg = serialize_frame(header, blocks,
                                  _quantize or bool(header.get("ahawi")))
        except Exception as e:
            print(f"[ws] serialize error: {e}")
            continue

        dead = set()
        sent = 0
        for ws in list(_connections):
            try:
                await ws.send_bytes(msg)
                sent += 1
            except Exception as e:
                print(f"[ws] send failed, dropping client: {e}")
                dead.add(ws)

        if diag:
            print(
                f"[ws] tick: frame t={frame_t:.3f}  blocks={len(blocks)}  "
                f"bytes={len(msg)}  sent={sent}/{len(_connections)}"
            )
        # NOTE: mutate in place — rebinding the name would shadow the global.
        if dead:
            _connections.difference_update(dead)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    """Main live-viewer WebSocket: control in, frames + status out.

    One connection per browser tab. Frames themselves are pushed by the
    shared `_broadcaster` task; this handler only accepts the connection,
    enforces the single-admin slot, and loops reading control messages sent
    by the client as text JSON, e.g.:

        {"center": Hz, "sample_rate": Hz, "gain": dB, "nfft": int, "rows": int}

    Only one admin connection may hold the "admin slot" at a time (guarded by
    `_slot_lock`); a second admin handshake is refused with close code 4001
    (distinct from 1008/auth-failure so the client can tell "someone else is
    driving" from "you're not authorized" and retry as a takeover). The slot
    is not enforced when auth is disabled, since every connection shares
    `DEFAULT_ROLE` there and enforcing it would limit a demo to one browser.

    Non-admin control messages are acknowledged with `denied: true` and
    otherwise ignored (defense in depth — the UI already disables controls
    for read-only roles) so the client keeps receiving frames; likewise
    while a recording is active. Valid config-changing messages run through
    `SharedConfig.update` in a worker thread (an analysis apply can block on
    tier-2 scratch probes) and the resulting ack is sent back describing what
    was applied, rounded, rejected, or ignored. A 15 s idle timeout triggers
    a `ping`; two consecutive missed pings drop the connection so a stale
    slot frees up promptly instead of waiting for a TCP-level timeout.

    Args:
        ws: The WebSocket connection. Its `scope["role"]` (set by
            `BasicAuthMiddleware`) determines write access.
    """
    global _admin_ws
    # Role resolved by BasicAuthMiddleware and stashed on the ASGI scope. Falls
    # back to DEFAULT_ROLE (auth-disabled/demo) so a missing key never locks a
    # client out.
    role = ws.scope.get("role", DEFAULT_ROLE)

    # Viewers/interns are unlimited; only ONE admin may hold the slot at a time.
    # The check-and-set is under _slot_lock so two interleaving admin handshakes
    # can't both see the slot free. A busy refusal uses a distinct 4001 code (vs
    # 1008 for auth) so the client can tell "another admin connected" from
    # "unauthorized"; the browser's auto-retry then acts as a takeover queue.
    #
    # The slot exists to stop two DIFFERENT people fighting over one radio. With
    # auth disabled there is no second identity for it to arbitrate — everyone is
    # DEFAULT_ROLE — so enforcing it there just meant a demo could be shown to
    # exactly one browser, contradicting both the README's "shares one radio
    # stream with multiple browser clients" and demo mode's whole purpose.
    async with _slot_lock:
        if role == "admin" and _admin_ws is not None and not AUTH_DISABLED:
            await ws.accept()
            await ws.send_text(json.dumps(
                {"role": role, "auth_enabled": AUTH_ENABLED, "error": "admin-busy"}
            ))
            await ws.close(code=4001)
            print(f"[ws] refused extra admin (slot busy): {ws.client}")
            return
        await ws.accept()
        _connections.add(ws)
        if role == "admin" and not AUTH_DISABLED:
            _admin_ws = ws
    # Tell the client its role immediately so app.js can enable/lock controls.
    # auth_enabled lets the UI hide the sign-out button in --demo / auth-off mode.
    await ws.send_text(json.dumps({"role": role, "auth_enabled": AUTH_ENABLED}))
    client = ws.client
    print(f"[ws] client connected: {client} (role={role})")
    misses = 0
    try:
        while True:
            try:
                text = await asyncio.wait_for(ws.receive_text(), timeout=15.0)
            except asyncio.TimeoutError:
                # Liveness probe: if the client is gone, free the slot promptly (so a
                # waiting viewer's reconnect can take over) instead of holding it until
                # TCP times out minutes later (LV-R3).
                try:
                    await ws.send_text('{"message":"ping"}')
                    misses = 0
                except Exception:
                    misses += 1
                    if misses >= 2:
                        print(f"[ws] client {client} unresponsive; dropping")
                        break
                continue
            try:
                ctrl = json.loads(text)
                # Role gate (defense in depth): read-only roles may never mutate
                # the shared config. The UI already blocks their controls, but a
                # crafted frame must be ignored here too. Stay connected so the
                # client keeps receiving live frames.
                if role not in WRITE_ROLES:
                    await ws.send_text(json.dumps(
                        {"message": "read-only role: control ignored", "denied": True}
                    ))
                    continue
                if _recording.active():
                    await ws.send_text(json.dumps(
                        {"message": "controls are locked while recording", "denied": True}
                    ))
                    continue
                # Run in a worker thread: an analysis apply blocks on tier-2
                # probes serviced by the compute thread (up to ~0.1 s per
                # field), which must not stall the event loop / broadcaster.
                ack = await asyncio.get_running_loop().run_in_executor(
                    None, _shared.update, ctrl
                )
                # Acknowledge settings/analysis applies so the UI can show what
                # took effect vs what was rounded, rejected, ignored, or needs a
                # reconnect (LV-F6, P2a-2). The structured ack rides along so
                # app.js can surface rounded/rejected in the status line.
                # Also ack any message the freedom model adjusted (rounded/
                # rejected) — e.g. a bare {"backend":"ssb"} that retuned the
                # sample rate (P2b-5) must be reported, not just applied.
                if isinstance(ctrl, dict) and (
                    "capture" in ctrl or "source" in ctrl or "analysis" in ctrl
                    or ack.get("rounded") or ack.get("rejected")
                ):
                    parts = [f"applied {ack['applied']}"]
                    for r in ack.get("rounded", []):
                        parts.append(
                            f"rounded {r['field']}: {r['requested']} → {r['used']} ({r['reason']})"
                        )
                    for r in ack.get("rejected", []):
                        parts.append(f"rejected {r['field']}: {r['reason']}")
                    if ack.get("ignored"):
                        parts.append(f"ignored {ack['ignored']}")
                    if ack.get("reconnect"):
                        parts.append(f"reconnect-only {ack['reconnect']}")
                    await ws.send_text(json.dumps(
                        {"message": "settings — " + "; ".join(parts), "ack": ack}
                    ))
            except (json.JSONDecodeError, ValueError, TypeError, AttributeError) as e:
                # A single malformed control message must never drop the (only)
                # viewer connection (LV-R2).
                await ws.send_text(json.dumps({"message": f"bad control ignored: {e}"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[ws] client {client} error: {e}")
    finally:
        _connections.discard(ws)
        # Free the admin slot only if this socket owned it (verify identity to
        # survive a takeover race). Under the lock so it can't clobber a fresh
        # admin that grabbed the slot between our break and here. The liveness
        # ping above doubles as dead-admin eviction, funnelling through here.
        if role == "admin":
            async with _slot_lock:
                if _admin_ws is ws:
                    _admin_ws = None
        print(f"[ws] client disconnected: {client} (role={role})")


# Mount static files last so the /ws route takes priority
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="static")
else:
    @app.get("/")
    async def root():
        """Fallback root route used only when `live/web/` is missing.

        Returns:
            dict: An error payload naming the expected directory, in place
            of the mounted static-file app.
        """
        return {
            "error": f"Web assets not found at {WEB_DIR}",
            "hint": "Did you create live/web/index.html?",
        }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """CLI entry point: parse args, wire up the device and core state, serve.

    Resolves the requested device (`--device`/`--demo`/`--ports`/`--channels`)
    via `core.devices.resolve_device`, configures `core.state` (device label,
    channel set, backend, FPS), builds the module-level `SharedConfig`,
    `InsightService`, acquirer (`Acquirer` for real radios, self-computing
    `DemoAcquirer` for `--demo`), optional `Computer`, `RecordingManager`, and
    binds `core.tx.TX` to the acquirer's live device handle. Prints a startup
    banner (mode, backend/fps, boot_id, auth status, transmit capability) and
    then hands control to `uvicorn.run`, which drives `app` and its
    `lifespan` context manager for the rest of the process's life.

    Exits the process (via `parser.error` or `sys.exit`) on invalid
    argument combinations, a missing striqt sensor stack for a non-demo
    device, or a missing `uvicorn` install.
    """
    global _acquirer, _computer, _shared, _quantize, _recording, _insights

    parser = argparse.ArgumentParser(
        description="striqt WebSocket live viewer server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--device",   default="air8201b",
                        help="SDR to drive: air8201b | pluto | soapy | demo | "
                             "auto (enumerate, need exactly one) | "
                             "driver=X[,serial=Y] (pick one of several)")
    parser.add_argument("--demo",     action="store_true",
                        help="Use synthetic IQ (no radio hardware); alias for "
                             "--device demo")
    # 1-4 channels: the frontend builds its panes/series from the header's
    # channel list (P3-4); 4 is just a sane demo ceiling, not a hard limit.
    parser.add_argument("--channels", type=int, default=None, choices=(1, 2, 3, 4),
                        help="use the first N RX channels (demo: creates N; "
                             "real devices: trims the discovered set)")
    parser.add_argument("--ports", default="auto",
                        help="explicit RX port list such as 0 or 0,1; "
                             "auto probes the device (profile fallback)")
    parser.add_argument("--quantize", action="store_true",
                        help="Encode waterfall as uint8 (~4x smaller frames)")
    parser.add_argument("--fps",      type=float, default=state.BROADCAST_FPS,
                        help="Max broadcast frame rate (fps)")
    parser.add_argument("--backend",  default=state.SPEC_BACKEND,
                        choices=sorted(BACKENDS),
                        help="Spectrogram backend")
    parser.add_argument("--host",     default="0.0.0.0",
                        help="Bind address")
    parser.add_argument("--port",     type=int, default=8000,
                        help="Listen port")
    args = parser.parse_args()

    # Resolve the device first (P3-1): --demo remains the historical alias and
    # may not contradict an explicit real --device choice.
    selector = args.device
    if args.demo:
        if selector not in ("air8201b", "demo"):
            parser.error(f"--demo conflicts with --device {selector}")
        selector = "demo"
    name, adapter = devices.resolve_device(selector)
    devices.set_adapter(adapter)

    # Channel plan: profile default → live discovery (every real device is
    # asked getNumChannels when reachable) → explicit --ports → --channels trim.
    is_demo = name == "demo"
    channels = None
    if args.ports != "auto":
        try:
            channels = tuple(dict.fromkeys(
                int(p.strip()) for p in args.ports.split(",") if p.strip()))
        except ValueError:
            parser.error("--ports must be 'auto' or a comma-separated integer list")
        if not channels or min(channels) < 0:
            parser.error("--ports must contain non-negative RX port numbers")
    elif not is_demo:
        channels = devices.probe_channels(name, adapter)   # None → profile
    state.configure_device(name, channels)
    state.set_device_label(adapter.label)
    if args.channels is not None:
        if is_demo:
            state.set_channels(tuple(range(args.channels)))
        else:
            have = state.CHANNELS
            if args.channels > len(have):
                parser.error(f"requested {args.channels} channels but the "
                             f"device has {have}")
            state.set_channels(have[:args.channels])

    if is_demo and not _ANALYSIS_OK and args.backend in CALIBRATED_GRID_BACKENDS:
        print("[demo] striqt.analysis unavailable; falling back to quicklook backend")
        state.set_backend("quicklook")
    else:
        state.set_backend(args.backend)

    if not is_demo and not _SENSOR_OK:
        print(
            "ERROR: striqt.sensor not importable (radio hardware deps missing).\n"
            "  Run with --demo for synthetic IQ, or install the striqt radio stack.",
            file=sys.stderr,
        )
        sys.exit(1)

    state.set_fps(args.fps)
    _quantize     = args.quantize
    _shared       = SharedConfig()
    _insights     = InsightService()
    if is_demo:
        # DemoAcquirer generates synthetic IQ and self-publishes — no DMA to
        # overflow, so it keeps the inline-compute path and needs no Computer.
        _acquirer = DemoAcquirer(_shared, _insights)
        _computer = None
    else:
        _acquirer = Acquirer(_shared)
        _computer = Computer(_acquirer, _shared, _insights)
    health.bind(_acquirer, _shared)
    _recording = RecordingManager(_acquirer, _shared, demo=is_demo)
    # TX borrows the acquirer's live device handle — it never opens its own.
    tx.TX.bind(_acquirer, demo=is_demo)

    try:
        import uvicorn
    except ImportError:
        print(
            "uvicorn not installed. Run:\n  pip install 'uvicorn[standard]'",
            file=sys.stderr,
        )
        sys.exit(1)

    mode    = "DEMO (synthetic IQ)" if is_demo else f"{state.DEVICE_LABEL} radio"
    q_note  = " + uint8 quantization" if _quantize else ""
    print(f"\nstriqt web viewer — {mode}")
    print(f"  backend={state.SPEC_BACKEND}, fps={state.BROADCAST_FPS:.0f}{q_note}")
    print(f"  boot_id={health.BOOT_ID}")

    # Report auth status loudly so an unintentionally-open public server is obvious.
    if AUTH_DISABLED:
        print(
            "  auth:     *** WARNING: RADIO_AUTH_DISABLE=1 — auth DISABLED, "
            f"everyone gets role '{DEFAULT_ROLE}'. Do NOT use in production. ***"
        )
    else:
        users = ", ".join(f"{user}={role}" for role, user in _ROLE_USERS.items())
        print(f"  auth:     username-only role login ENABLED ({users})")
        if _SESSION_SECRET_IS_EPHEMERAL:
            print(
                "            note: RADIO_SESSION_SECRET unset — using a random "
                "per-process signing key, so logins do NOT survive a restart. "
                "Set it to keep sessions across restarts."
            )

    # Transmit is the one feature that can get a person fined; say plainly at
    # boot whether this host can key its PA, and why not when it can't.
    tx_caps = tx.TX.capabilities()
    if tx_caps["available"]:
        print("  transmit: ENABLED"
              + (" (simulated — demo radiates nothing)" if tx_caps["simulated"]
                 else f" — {tx_caps['channels']} TX channel(s); "
                      f"set RADIO_TX=0 to remove it"))
    else:
        print(f"  transmit: unavailable — {tx_caps['reason']}")

    print(f"  listening on http://{args.host}:{args.port}")
    if args.host in ("0.0.0.0", "::"):
        print(f"  local:    http://localhost:{args.port}")
    print(
        f"  tunnel:   cloudflared tunnel --url http://localhost:{args.port}\n"
        f"            (or run:  bash live/run_web.sh --tunnel)\n"
    )

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
