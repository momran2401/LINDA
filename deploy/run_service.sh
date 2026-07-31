#!/usr/bin/env bash
# Service entrypoint — picks the frontend from RADIO_MODE (set in
# /etc/radio-web/radio.env by install_linda.sh). Runs inside the repo's venv
# when one exists, else the system python3.
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

PY="$REPO_ROOT/.venv/bin/python3"
[[ -x "$PY" ]] || PY="$(command -v python3)"

PORT="${RADIO_PORT:-8000}"
DEVICE_ARGS=()
[[ -n "${RADIO_DEVICE:-}" ]] && DEVICE_ARGS=(--device "$RADIO_DEVICE")
# RADIO_EXTRA_ARGS: optional extra CLI flags (e.g. "--quantize --fps 10").
# Split with shell quoting rules rather than bare IFS word-splitting, so a
# quoted value survives: `--title "My Radio"` used to reach argparse as three
# arguments (--title, "My, Radio"). `xargs` understands the quoting a user
# writing a shell-style string in an env file reasonably expects.
split_extra_args() {
    EXTRA=()
    local raw="${1:-}"
    [[ -n "$raw" ]] || return 0
    local line
    while IFS= read -r line; do
        [[ -n "$line" ]] && EXTRA+=("$line")
    done < <(printf '%s' "$raw" | xargs -n1 printf '%s\n' 2>/dev/null)
}
split_extra_args "${RADIO_EXTRA_ARGS:-}"

case "${RADIO_MODE:-web}" in
    kiosk)
        exec "$PY" "$REPO_ROOT/live/striqt_kiosk.py" \
            --port "$PORT" "${DEVICE_ARGS[@]}" -- "${EXTRA[@]}"
        ;;
    web|hotspot|ethernet|*)
        # hotspot/ethernet differ only in NETWORK config (done at setup time);
        # the service itself is always the web server.
        exec "$PY" "$REPO_ROOT/live/striqt_web_server.py" \
            --port "$PORT" "${DEVICE_ARGS[@]}" "${EXTRA[@]}"
        ;;
esac
