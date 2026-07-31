#!/usr/bin/env python3
"""
pull_recordings — copy finished recordings off the radio, onto this machine.

Runs on YOUR laptop, not on the radio. It talks to the same authenticated HTTP
endpoints the browser's Record tab uses (`/recordings` and
`/recordings/<id>/download`), so it needs no SSH key, no credentials stored on
the radio, and no new service. It works over a LAN address or a
`run_web.sh --tunnel` URL identically.

Nothing is ever uploaded or published: this only pulls, and only into --dest.

Examples:
    # one pass: fetch anything not already here
    python3 live/tools/pull_recordings.py --url http://radio.local:8000 --user admin

    # stay running and grab each recording as soon as it finishes
    python3 live/tools/pull_recordings.py --url https://x.trycloudflare.com \
        --user admin --watch

    # just show what's on the radio
    python3 live/tools/pull_recordings.py --user admin --list

Auth: --user or RADIO_PULL_USER / RADIOCTL_USER. The username selects the role;
no password is used (matching radioctl.py). Servers running with
RADIO_AUTH_DISABLE=1 need no username at all.

Requires only the Python 3 standard library, so it runs on any machine without
installing this project.
"""

import argparse
import base64
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8000"
DEFAULT_DEST = "./radio-recordings"
DEFAULT_INTERVAL = 15.0


class _NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    """Surface the browser-auth redirect instead of silently following it.

    An unauthenticated API call is answered with `303 → /login`, and /login
    serves the HTML login form with status 200. Following that would mean
    parsing a web page as JSON — or worse, writing it into a .zarr.zip.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RadioClient:
    """Thin stdlib-only HTTP client for the radio-web `/recordings` API.

    Mirrors radioctl.py's auth model (username-only Basic auth, no
    password) but additionally installs `_NoAuthRedirect` so a missing or
    wrong username surfaces as an explicit auth error instead of silently
    downloading the HTML login page as if it were a recording.
    """

    def __init__(self, base, user=None, timeout=30.0):
        """Args:
            base: Base URL of the radio-web server, e.g.
                "http://127.0.0.1:8000" or a `run_web.sh --tunnel` URL.
            user: Username selecting the caller's role. Omit against a
                server running with RADIO_AUTH_DISABLE=1.
            timeout: Per-request timeout in seconds.
        """
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.auth = None
        if user:
            raw = "{}:".format(user).encode("utf-8")
            self.auth = "Basic " + base64.b64encode(raw).decode("ascii")
        self._opener = urllib.request.build_opener(_NoAuthRedirect)

    def _open(self, path):
        """Open an authenticated GET request against the server.

        Args:
            path: Server path beginning with "/".

        Returns:
            The open response object (a context manager).
        """
        headers = {"Authorization": self.auth} if self.auth else {}
        request = urllib.request.Request(self.base + path, headers=headers)
        return self._opener.open(request, timeout=self.timeout)

    def catalog(self):
        """Fetch the server's list of recordings (complete and in-progress).

        Returns:
            list[dict]: Each entry has at least "id", "state"
            ("complete"/"partial"), and "bytes".

        Raises:
            RuntimeError: If the response body isn't the expected JSON
            shape (e.g. because auth failed and this is the login page,
            though `_NoAuthRedirect` normally turns that into an HTTPError
            first).
        """
        with self._open("/recordings") as response:
            payload = response.read()
        try:
            return json.loads(payload).get("recordings") or []
        except (ValueError, AttributeError):
            raise RuntimeError(
                "{} did not return a recording list — is it a radio web "
                "server?".format(self.base))

    def open_download(self, recording_id):
        """Open a streaming download of one recording's archive.

        Args:
            recording_id: Catalog id, e.g.
                "air8201b/20260728T101500Z.zarr.zip". Each path segment is
                quoted separately since the slash is structural, not part
                of a single segment to escape.

        Returns:
            The open response object (a context manager) to stream-copy
            from.
        """
        quoted = "/".join(urllib.parse.quote(part, safe="")
                          for part in str(recording_id).split("/"))
        return self._open("/recordings/{}/download".format(quoted))


def human_bytes(count):
    """Format a byte count as a human-readable string (e.g. "12.3 MiB").

    Args:
        count: Byte count; None is treated as 0.

    Returns:
        str: Value scaled to B/KiB/MiB/GiB with one decimal place.
    """
    value = float(count or 0)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return "{:.1f} {}".format(value, unit)
        value /= 1024


def already_have(destination, expected_bytes):
    """Check whether a previous run already finished downloading this exact recording.

    Size is the whole check: recordings are immutable once complete (the
    server writes to a `.partial` name and renames on success), so a local
    file of the right size is the right file. A short file is a torn
    download and gets replaced.

    Args:
        destination: Local path the recording would be saved at.
        expected_bytes: Size reported by the server's catalog, or None if
            unknown (in which case mere existence is treated as complete).

    Returns:
        bool: True if `destination` exists and (when known) matches
        `expected_bytes`.
    """
    if not destination.is_file():
        return False
    if expected_bytes is None:
        return True
    return destination.stat().st_size == int(expected_bytes)


def verify_archive(path):
    """CRC-check a downloaded zip archive for corruption.

    Args:
        path: Path to the local .zarr.zip file.

    Returns:
        str or None: None when the archive is intact; otherwise a
        human-readable description of the problem (bad CRC member, or the
        underlying OSError/BadZipFile message).
    """
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
        return None if bad is None else "CRC failed at {}".format(bad)
    except (OSError, zipfile.BadZipFile) as exc:
        return str(exc)


def download_one(client, item, dest_root, verify=True, quiet=False):
    """Fetch a single recording into `dest_root`, skipping it if already present.

    Downloads to a `.part` sibling file and only renames it into place once
    the transfer is complete and (optionally) CRC-verified, so an
    interrupted transfer can never be mistaken for a finished recording by
    a later run.

    Args:
        client: Connected RadioClient instance.
        item: One catalog entry from `RadioClient.catalog()` (needs "id"
            and, optionally, "bytes").
        dest_root: Local directory to mirror recordings into.
        verify: Whether to CRC-check the archive after downloading.
        quiet: Suppress the per-file progress line.

    Returns:
        str: "downloaded", "skipped" (already had a complete copy), or
        "failed" (network error, size mismatch, or failed CRC check).
    """
    recording_id = item["id"]
    destination = dest_root / recording_id
    expected = item.get("bytes")

    if already_have(destination, expected):
        return "skipped"

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Download beside the target, then rename: an interrupted transfer must
    # never look like a finished recording to the next run.
    partial = destination.with_name(destination.name + ".part")

    if not quiet:
        print("  ↓ {} ({})".format(recording_id, human_bytes(expected)), flush=True)

    try:
        with client.open_download(recording_id) as response, \
                open(partial, "wb") as handle:
            shutil.copyfileobj(response, handle, length=1 << 20)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        partial.unlink(missing_ok=True)
        print("  ! {}: {}".format(recording_id, exc), file=sys.stderr)
        return "failed"

    written = partial.stat().st_size
    if expected is not None and written != int(expected):
        partial.unlink(missing_ok=True)
        print("  ! {}: truncated ({} of {} bytes)".format(
            recording_id, written, expected), file=sys.stderr)
        return "failed"

    if verify:
        problem = verify_archive(partial)
        if problem is not None:
            partial.unlink(missing_ok=True)
            print("  ! {}: {}".format(recording_id, problem), file=sys.stderr)
            return "failed"

    os.replace(partial, destination)
    return "downloaded"


def fetch_complete(client, dest_root, verify=True, quiet=False):
    """Run one pass over the server's catalog, downloading every complete recording not already local.

    A recording still being written is listed with state "partial"; it
    becomes "complete" only after the server has validated and renamed it,
    so partials are skipped here rather than downloaded mid-write.

    Args:
        client: Connected RadioClient instance.
        dest_root: Local directory to mirror recordings into.
        verify: Whether to CRC-check each downloaded archive.
        quiet: Suppress per-file progress lines.

    Returns:
        tuple[int, int, int]: (downloaded, skipped, failed) counts for
        this pass.
    """
    catalog = client.catalog()
    ready = [item for item in catalog if item.get("state") == "complete"]

    counts = {"downloaded": 0, "skipped": 0, "failed": 0}
    for item in ready:
        counts[download_one(client, item, dest_root, verify, quiet)] += 1
    return counts["downloaded"], counts["skipped"], counts["failed"]


def print_catalog(client):
    """Print every recording on the radio (any state) with size and state.

    Backs the `--list` flag.

    Args:
        client: Connected RadioClient instance.
    """
    catalog = client.catalog()
    if not catalog:
        print("No recordings on the radio.")
        return
    for item in catalog:
        print("{:<10} {:>10}  {}".format(
            item.get("state", "?"), human_bytes(item.get("bytes")), item["id"]))
    print("\n{} recording(s).".format(len(catalog)))


NEEDS_AUTH_HINT = ("pass --user with a username the radio knows "
                   "(admin / viewer / interns), or set RADIO_PULL_USER")


def describe_connection_error(exc, url):
    """Turn a raw urllib/RuntimeError exception into an actionable message.

    Distinguishes auth failures (401/403, or a redirect to /login) from a
    plain 404 or an unreachable host, so a user sees "pass --user" rather
    than a bare HTTP status code.

    Args:
        exc: The caught exception (HTTPError, URLError, OSError, or
            RuntimeError).
        url: The URL that was being contacted, for the error message.

    Returns:
        str: Human-readable description of the failure.
    """
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return "{} {} — {}".format(exc.code, exc.reason, NEEDS_AUTH_HINT)
        if exc.code in (301, 302, 303, 307, 308):
            # The server bounced us to its login form: this is auth, not a
            # missing endpoint.
            return "authentication required — {}".format(NEEDS_AUTH_HINT)
        if exc.code == 404:
            return ("404 Not Found — {} does not look like a radio web server"
                    .format(url))
        return "{} {}".format(exc.code, exc.reason)
    if isinstance(exc, RuntimeError):
        return str(exc)
    return "cannot reach {} — {}".format(url, getattr(exc, "reason", exc))


def main():
    """Parse CLI arguments and run one pass or a continuous watch loop over the radio's recordings.

    Contacts the server first (catalog or list) before touching the local
    filesystem, so a bad --url or missing --user fails cleanly instead of
    creating an empty --dest directory. In one-shot mode, downloads every
    complete recording not already present and exits; in --watch mode, polls
    forever (Ctrl-C to stop), tolerating transient connection errors after
    the first successful contact.

    Returns:
        int: 0 on success; 1 if any download failed in one-shot mode; 2 if
        the initial contact with the server failed.
    """
    parser = argparse.ArgumentParser(
        description="Copy finished recordings off the radio onto this machine.")
    parser.add_argument("--url", default=os.environ.get("RADIO_PULL_URL", DEFAULT_URL),
                        help="radio web server base URL (default %(default)s)")
    parser.add_argument("--user", default=os.environ.get("RADIO_PULL_USER")
                        or os.environ.get("RADIOCTL_USER"),
                        help="username selecting the role; no password is used")
    parser.add_argument("--dest", default=os.environ.get("RADIO_PULL_DEST", DEFAULT_DEST),
                        help="local directory to mirror into (default %(default)s)")
    parser.add_argument("--watch", action="store_true",
                        help="keep running and fetch each recording as it finishes")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help="seconds between polls in --watch (default %(default)s)")
    parser.add_argument("--list", action="store_true",
                        help="show what is on the radio and exit")
    parser.add_argument("--no-verify", action="store_true",
                        help="skip the CRC check of each downloaded archive")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="per-request timeout in seconds (default %(default)s)")
    args = parser.parse_args()

    client = RadioClient(args.url, args.user, timeout=args.timeout)

    # Contact the radio before touching the filesystem, so a typo in --url or a
    # missing --user fails cleanly instead of leaving an empty directory behind.
    # Later drops are tolerated in --watch; only the first contact is required.
    try:
        if args.list:
            print_catalog(client)
            return 0
        client.catalog()
    except (urllib.error.URLError, OSError, RuntimeError) as exc:
        print("error: {}".format(describe_connection_error(exc, args.url)),
              file=sys.stderr)
        return 2

    dest_root = Path(args.dest).expanduser().resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    verify = not args.no_verify

    print("radio : {}".format(args.url))
    print("dest  : {}".format(dest_root))

    if not args.watch:
        try:
            got, skipped, failed = fetch_complete(client, dest_root, verify)
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            print("error: {}".format(describe_connection_error(exc, args.url)),
                  file=sys.stderr)
            return 2
        print("{} new, {} already here, {} failed".format(got, skipped, failed))
        return 1 if failed else 0

    print("watching every {:.0f}s — Ctrl-C to stop".format(args.interval))
    total = 0
    warned = False
    while True:
        try:
            got, _skipped, failed = fetch_complete(
                client, dest_root, verify, quiet=False)
            total += got
            if got:
                print("  {} new ({} total this session)".format(got, total), flush=True)
            warned = False
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            # The radio rebooting or a tunnel blipping is normal during a long
            # watch; say so once and keep polling rather than exiting.
            if not warned:
                print("  … {} (still watching)".format(
                    describe_connection_error(exc, args.url)),
                    file=sys.stderr, flush=True)
                warned = True
        except KeyboardInterrupt:
            break
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            break
    print("\nstopped — {} recording(s) pulled this session".format(total))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
