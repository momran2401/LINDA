#!/usr/bin/env bash
#
# fetch_recordings.sh — copy finished recordings off the radio, over SSH.
#
# Runs on YOUR laptop, not on the radio. It is a ONE-WAY PULL: nothing is
# written to or deleted from the radio, and nothing is uploaded anywhere.
# Re-running transfers only what is new, so run it as often as you like.
#
# Usage:
#   bash live/tools/fetch_recordings.sh mustafa@radio.local
#   bash live/tools/fetch_recordings.sh mustafa@radio.local --dest ~/data/radio
#   bash live/tools/fetch_recordings.sh mustafa@radio.local --watch
#
# Options:
#   --dest DIR         where to put them locally (default ./radio-recordings)
#   --remote-dir DIR   recordings dir on the radio (default LINDA/recordings,
#                      interpreted relative to your SSH login home directory)
#   --port N           SSH port (default 22)
#   --watch            keep running, re-checking periodically
#   --interval N       seconds between checks with --watch (default 30)
#   --include-partial  also copy recordings that are still being written
#   --dry-run          list what would transfer, copy nothing
#   -h, --help         this message
#
# Recordings still being written are named *.partial.zarr.zip and are skipped
# by default: the server renames one to its final name only after it has
# validated the archive, so what you get is always a complete recording.

set -euo pipefail

HOST=""
DEST="./radio-recordings"
REMOTE_DIR="LINDA/recordings"
PORT="22"
WATCH=0
INTERVAL=30
INCLUDE_PARTIAL=0
DRY_RUN=0

# Print the header comment block (line 2 up to the first non-comment line).
usage() { awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest)            DEST="$2"; shift 2 ;;
        --remote-dir)      REMOTE_DIR="$2"; shift 2 ;;
        --port)            PORT="$2"; shift 2 ;;
        --interval)        INTERVAL="$2"; shift 2 ;;
        --watch)           WATCH=1; shift ;;
        --include-partial) INCLUDE_PARTIAL=1; shift ;;
        --dry-run)         DRY_RUN=1; shift ;;
        -h|--help)         usage; exit 0 ;;
        -*)                echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
        *)
            if [[ -n "$HOST" ]]; then
                echo "unexpected argument: $1" >&2; exit 2
            fi
            HOST="$1"; shift ;;
    esac
done

if [[ -z "$HOST" ]]; then
    echo "error: no radio given." >&2
    echo "usage: bash $0 user@radio.local [options]" >&2
    echo "run with --help for the full option list." >&2
    exit 2
fi

if ! command -v rsync >/dev/null 2>&1; then
    echo "error: rsync is not installed on this machine." >&2
    echo "macOS ships it by default; on Linux try: sudo apt install rsync" >&2
    exit 2
fi

SSH_CMD="ssh -p $PORT -o ConnectTimeout=15"

# Fail with a useful message rather than an opaque rsync error. ssh reports its
# OWN failures (host unreachable, auth refused) as exit 255, so one round trip
# distinguishes "can't reach the radio" from "wrong path on the radio".
if $SSH_CMD "$HOST" "test -d '$REMOTE_DIR'" 2>/dev/null; then
    :
else
    status=$?
    if [[ $status -eq 255 ]]; then
        echo "error: cannot connect to $HOST over SSH." >&2
        echo "Check the hostname or IP, that 'ssh $HOST' works by hand," >&2
        echo "and --port if the radio does not use port 22." >&2
    else
        echo "error: '$REMOTE_DIR' is not a directory on $HOST." >&2
        echo "Point at the right place with --remote-dir, e.g." >&2
        echo "  --remote-dir /home/deepwave/LINDA/recordings" >&2
        echo "On a systemd deployment check RADIO_RECORDINGS_DIR in" >&2
        echo "  /etc/radio-web/radio.env" >&2
    fi
    exit 2
fi

mkdir -p "$DEST"

# -rltvh  recurse, keep symlinks/timestamps, verbose, human-readable sizes.
#         Deliberately not -a: preserving owner/group across machines only
#         produces warnings when you are not root on both ends.
# No --delete, ever: this must never remove your local copies, and it never
# touches the radio.
RSYNC_ARGS=(-rltvh --prune-empty-dirs -e "$SSH_CMD")

if [[ $INCLUDE_PARTIAL -eq 0 ]]; then
    RSYNC_ARGS+=(--exclude='*.partial.zarr.zip')
fi

[[ $DRY_RUN -eq 1 ]] && RSYNC_ARGS+=(--dry-run)

# Trailing slashes matter: copy the CONTENTS of the remote directory into DEST.
SRC="${HOST}:${REMOTE_DIR}/"

run_once() {
    rsync "${RSYNC_ARGS[@]}" "$SRC" "${DEST%/}/"
}

echo "radio : $HOST:$REMOTE_DIR"
echo "dest  : $DEST"
[[ $DRY_RUN -eq 1 ]] && echo "mode  : dry run (nothing will be copied)"

if [[ $WATCH -eq 0 ]]; then
    run_once
    echo "done."
    exit 0
fi

echo "watching every ${INTERVAL}s — Ctrl-C to stop"
trap 'echo; echo "stopped."; exit 0' INT
while true; do
    # A dropped link or a rebooting radio should not end the watch.
    run_once || echo "  … transfer failed, will retry in ${INTERVAL}s" >&2
    sleep "$INTERVAL"
done
