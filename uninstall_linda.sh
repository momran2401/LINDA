#!/usr/bin/env bash
# ============================================================================
# LINDA — uninstaller. Removes what setup.sh installed, and nothing else.
#
#     sudo bash uninstall_linda.sh              # show the plan, then confirm
#     sudo bash uninstall_linda.sh --dry-run    # show the plan and stop
#     sudo bash uninstall_linda.sh --yes        # no prompt
#
# Extra removals, each off by default because they can break unrelated things:
#     --purge-recordings   delete captured data under recordings/  (NO BACKUP)
#     --purge-desktop      remove Chromium/X/Openbox/LightDM (kiosk mode)
#     --purge-network      remove NetworkManager and avahi (mDNS)
#     --purge-groups       drop the user from the plugdev/dialout groups
#     --purge-pip-cache    delete root's pip wheel cache
#     --keep-packages      touch no apt packages at all
#
# How it decides what to remove:
#   setup.sh writes /etc/radio-web/installed-packages listing every package
#   that was NOT already present before it ran. Those are removed. If that
#   manifest is missing (installed by an older setup.sh) it falls back to a
#   conservative list of SDR-only packages and says so.
#
#   Packages whose removal could leave the machine unbootable, unreachable or
#   without a desktop are NEVER removed silently — see PROTECTED below.
#
# It does not touch: this git clone (delete it yourself when done), the
# machine's hostname, or striqt/ upstream sources.
# ============================================================================
set -euo pipefail

SERVICE_NAME="radio-web"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_DIR="/etc/radio-web"
ENV_FILE="$ENV_DIR/radio.env"
PKG_MANIFEST="$ENV_DIR/installed-packages"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"

ASSUME_YES=0
DRY_RUN=0
KEEP_PACKAGES=0
PURGE_RECORDINGS=0
PURGE_DESKTOP=0
PURGE_NETWORK=0
PURGE_GROUPS=0
PURGE_PIP_CACHE=0
DONE=0
STARTED_REMOVING=0   # flips once the first destructive step runs

# ── Output + failure trap (installed before any logic, same as setup.sh) ────
say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '\033[1;32m    ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m    ! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

bail() {
    local rc=$?
    if [[ $rc -eq 0 || $DONE -eq 1 ]]; then
        return 0
    fi
    printf '\n\033[1;31m─── UNINSTALL STOPPED (exit %s) ───\033[0m\n' "$rc" >&2
    if [[ $STARTED_REMOVING -eq 1 ]]; then
        echo "  Removal was already in progress; some items are gone." >&2
        echo "  Re-running is safe and will finish the job." >&2
    else
        echo "  Nothing was changed." >&2
    fi
    return 0
}
trap bail EXIT

for arg in "$@"; do
    case "$arg" in
        --yes|-y)           ASSUME_YES=1 ;;
        --dry-run|-n)       DRY_RUN=1 ;;
        --keep-packages)    KEEP_PACKAGES=1 ;;
        --purge-recordings) PURGE_RECORDINGS=1 ;;
        --purge-desktop)    PURGE_DESKTOP=1 ;;
        --purge-network)    PURGE_NETWORK=1 ;;
        --purge-groups)     PURGE_GROUPS=1 ;;
        --purge-pip-cache)  PURGE_PIP_CACHE=1 ;;
        --help|-h)
            awk 'NR>1 && /^#/ {sub(/^# ?/, ""); if ($0 !~ /^=+$/) print; next}
                 NR>1 {exit}' "$0"
            DONE=1; exit 0 ;;
        *) die "unknown option: $arg  (run: bash uninstall_linda.sh --help)" ;;
    esac
done

[[ ${EUID} -eq 0 ]] || die "run as root:  sudo bash uninstall_linda.sh"

# ── Never remove these, whatever the manifest says ──────────────────────────
# Removing python3 or sudo bricks the machine; the rest are things a Debian or
# Raspberry Pi OS image ships with, so their presence is not evidence that
# LINDA put them there.
PROTECTED=(
    python3 python3-minimal python3-venv python3-pip
    sudo ca-certificates curl git openssl iproute2 usbutils whiptail
    systemd base-files libc6 dpkg apt
)
# Shared infrastructure: real LINDA dependencies, but pulling them can cost
# the user their desktop session or their network. Opt-in only.
DESKTOP_PKGS=(chromium chromium-browser xserver-xorg xinit openbox lightdm dbus-x11)
NETWORK_PKGS=(network-manager avahi-daemon)

# Used only when setup.sh left no manifest (older installs). Deliberately
# limited to packages that exist for software-defined radio and nothing else.
FALLBACK_PKGS=(
    python3-soapysdr soapysdr-tools libsoapysdr0.8
    soapysdr-module-uhd soapysdr0.8-module-uhd uhd-host python3-uhd
    soapysdr-module-rtlsdr soapysdr-module-hackrf soapysdr-module-airspy
    soapysdr-module-bladerf soapysdr-module-lms7 soapysdr-module-plutosdr
    soapysdr0.8-module-all soapysdr-module-all
    gpsd gpsd-tools gpsd-clients libiio-utils
)

is_protected() {
    local p="$1" x
    for x in "${PROTECTED[@]}"; do [[ "$p" == "$x" ]] && return 0; done
    if [[ $PURGE_DESKTOP -eq 0 ]]; then
        for x in "${DESKTOP_PKGS[@]}"; do [[ "$p" == "$x" ]] && return 0; done
    fi
    if [[ $PURGE_NETWORK -eq 0 ]]; then
        for x in "${NETWORK_PKGS[@]}"; do [[ "$p" == "$x" ]] && return 0; done
    fi
    return 1
}

pkg_installed() {
    dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'ok installed'
}

# Guarded delete. Every removal in this script goes through here so a variable
# that unexpectedly expands to empty can never turn into `rm -rf /`.
safe_rm() {
    local p="${1:-}"
    [[ -n "$p" ]] || return 0
    [[ "$p" == /* ]] || { warn "refusing non-absolute path: $p"; return 0; }
    case "$p" in
        /|/usr|/usr/*bin|/etc|/var|/home|/boot|/bin|/sbin|/lib|/opt|/root|/srv|/tmp|/dev|/proc|/sys)
            warn "refusing to remove system path: $p"; return 0 ;;
    esac
    [[ -e "$p" || -L "$p" ]] || return 0
    if [[ $DRY_RUN -eq 1 ]]; then
        info "would remove  $p"
        return 0
    fi
    rm -rf -- "$p" && info "removed  $p"
    return 0
}

run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        info "would run     $*"
        return 0
    fi
    "$@" >/dev/null 2>&1 || true
    return 0
}

printf '\n\033[1;36m╔══════════════════════════════════════════════════════════════════╗\033[0m\n'
printf '\033[1;36m║  LINDA uninstaller                                               ║\033[0m\n'
printf '\033[1;36m╚══════════════════════════════════════════════════════════════════╝\033[0m\n'

# ── Discover the installation ───────────────────────────────────────────────
PORT=""
RECORDINGS_DIR=""
SERVICE_USER=""
if [[ -f "$UNIT_FILE" ]]; then
    d="$(awk -F= '/^WorkingDirectory=/{print $2; exit}' "$UNIT_FILE" 2>/dev/null || true)"
    [[ -n "$d" && -d "$d" ]] && REPO_ROOT="$d"
    SERVICE_USER="$(awk -F= '/^User=/{print $2; exit}' "$UNIT_FILE" 2>/dev/null || true)"
fi
if [[ -f "$ENV_FILE" ]]; then
    # `|| true` on each: under `set -o pipefail`, `head -1` closing the pipe
    # early can fail the whole substitution, and a failed assignment under
    # `set -e` would abort the uninstall before it removed anything.
    PORT="$(sed -n 's/^RADIO_PORT="\?\([0-9]*\)"\?.*/\1/p' "$ENV_FILE" | head -1 || true)"
    RECORDINGS_DIR="$(sed -n 's/^RADIO_RECORDINGS_DIR="\?\([^"]*\)"\?.*/\1/p' "$ENV_FILE" | head -1 || true)"
fi
[[ -n "$SERVICE_USER" ]] || SERVICE_USER="${SUDO_USER:-root}"
[[ -n "$RECORDINGS_DIR" ]] || RECORDINGS_DIR="$REPO_ROOT/recordings"

# Packages to remove
declare -a REMOVE_PKGS=() SKIPPED_PKGS=()
MANIFEST_FOUND=0
if [[ $KEEP_PACKAGES -eq 0 ]]; then
    declare -a candidates=()
    if [[ -f "$PKG_MANIFEST" ]]; then
        MANIFEST_FOUND=1
        while IFS= read -r line; do
            [[ -n "$line" ]] && candidates+=("$line")
        done < "$PKG_MANIFEST"
    else
        candidates=("${FALLBACK_PKGS[@]}")
    fi
    for p in "${candidates[@]}"; do
        if is_protected "$p"; then
            pkg_installed "$p" && SKIPPED_PKGS+=("$p")
            continue
        fi
        pkg_installed "$p" && REMOVE_PKGS+=("$p")
    done
fi

# Recording inventory — this is research data, so it gets counted and shown
# rather than quietly deleted.
REC_COUNT=0; REC_SIZE="0"
if [[ -d "$RECORDINGS_DIR" ]]; then
    REC_COUNT="$(find "$RECORDINGS_DIR" -type f 2>/dev/null | wc -l | tr -d ' ' || true)"
    REC_SIZE="$(du -sh "$RECORDINGS_DIR" 2>/dev/null | awk '{print $1}' || true)"
    [[ "$REC_COUNT" =~ ^[0-9]+$ ]] || REC_COUNT=0
    [[ -n "$REC_SIZE" ]] || REC_SIZE="unknown"
fi

# ── The plan ────────────────────────────────────────────────────────────────
say "This will remove"
info "service      $SERVICE_NAME (stop, disable, delete unit)"
info "config       $ENV_DIR/  (incl. the session secret and role logins)"
info "sudoers      /etc/sudoers.d/radio-web"
info "udev         /etc/udev/rules.d/70-linda-sdr.rules (+ the old nist-omran rule)"
info "state        /var/log/radio-web/  /var/cache/radio-web/  setup transcript"
info "python env   $REPO_ROOT/.venv  (+ any .venv.backup.*)"
info "kiosk        /etc/lightdm/lightdm.conf.d/50-radio-kiosk.conf"
info "network      NetworkManager profiles radio-hotspot / radio-ethernet"
info "kernel arg   usbcore.usbfs_memory_mb from cmdline.txt"
[[ -n "$PORT" ]] && info "firewall     ufw rule for ${PORT}/tcp (if present)"
if [[ ${#REMOVE_PKGS[@]} -gt 0 ]]; then
    if [[ $MANIFEST_FOUND -eq 1 ]]; then
        info "packages     ${#REMOVE_PKGS[@]} from the install manifest, plus orphaned dependencies:"
    else
        warn "no install manifest (older setup.sh) — falling back to the known SDR package list"
        info "packages     ${#REMOVE_PKGS[@]} SDR packages, plus orphaned dependencies:"
    fi
    printf '                 %s\n' "${REMOVE_PKGS[*]}"
elif [[ $KEEP_PACKAGES -eq 1 ]]; then
    info "packages     none (--keep-packages)"
else
    info "packages     none found to remove"
fi

say "This will KEEP"
info "the git clone at $REPO_ROOT  (remove it yourself when you are done)"
info "the machine's hostname and /etc/hosts"
info "the default systemd target (setup may have set graphical.target for kiosk)"
if [[ ${#SKIPPED_PKGS[@]} -gt 0 ]]; then
    info "shared/system packages — removing these can cost you the desktop,"
    info "the network or the machine itself:"
    printf '                 %s\n' "${SKIPPED_PKGS[*]}"
    info "add --purge-desktop / --purge-network if you really want them gone"
fi
if [[ $REC_COUNT -gt 0 && $PURGE_RECORDINGS -eq 0 ]]; then
    printf '\033[1;33m    recordings   %s files (%s) in %s — KEPT\033[0m\n' \
        "$REC_COUNT" "$REC_SIZE" "$RECORDINGS_DIR"
    info "             captured data is not deleted by default; use --purge-recordings"
fi

if [[ $DRY_RUN -eq 1 ]]; then
    say "Dry run — nothing was changed."
    DONE=1
    exit 0
fi

if [[ $ASSUME_YES -eq 0 ]]; then
    printf '\n\033[1;33mProceed? type "yes" to continue: \033[0m'
    read -r reply || true
    if [[ "$reply" != "yes" ]]; then
        say "Aborted; nothing was changed."
        DONE=1
        exit 0
    fi
fi

# A second, separate confirmation for irreplaceable capture data.
if [[ $PURGE_RECORDINGS -eq 1 && $REC_COUNT -gt 0 && $ASSUME_YES -eq 0 ]]; then
    printf '\n\033[1;31mDelete %s recording files (%s)? This cannot be undone.\033[0m\n' \
        "$REC_COUNT" "$REC_SIZE"
    printf '\033[1;31mType "delete recordings" to confirm: \033[0m'
    read -r reply2 || true
    if [[ "$reply2" != "delete recordings" ]]; then
        PURGE_RECORDINGS=0
        warn "recordings will be kept"
    fi
fi

# ── 1. Service ──────────────────────────────────────────────────────────────
STARTED_REMOVING=1
say "Stopping the service"
if command -v systemctl >/dev/null 2>&1; then
    run systemctl stop "$SERVICE_NAME"
    run systemctl disable "$SERVICE_NAME"
    safe_rm "$UNIT_FILE"
    safe_rm "/etc/systemd/system/multi-user.target.wants/${SERVICE_NAME}.service"
    run systemctl daemon-reload
    run systemctl reset-failed
    ok "service removed"
else
    info "no systemd on this host"
fi

# ── 2. Files and state ──────────────────────────────────────────────────────
say "Removing configuration and state"
safe_rm "$ENV_DIR"
safe_rm "/etc/sudoers.d/radio-web"
safe_rm "/etc/udev/rules.d/70-linda-sdr.rules"
safe_rm "/etc/udev/rules.d/70-nist-omran-sdr.rules"   # older setup.sh
safe_rm "/etc/lightdm/lightdm.conf.d/50-radio-kiosk.conf"
safe_rm "/etc/cloud/cloud.cfg.d/99-radio-hostname.cfg" # older setup.sh
safe_rm "/var/log/radio-web"
safe_rm "/var/cache/radio-web"
safe_rm "/var/log/radio-web-setup.log"
if command -v udevadm >/dev/null 2>&1; then
    run udevadm control --reload-rules
fi
ok "configuration removed"

# ── 3. Repository artifacts ─────────────────────────────────────────────────
say "Removing generated files in $REPO_ROOT"
safe_rm "$REPO_ROOT/.venv"
for d in "$REPO_ROOT"/.venv.backup.*; do
    [[ -e "$d" ]] && safe_rm "$d"
done
safe_rm "$REPO_ROOT/setup.log"
if [[ $DRY_RUN -eq 0 ]]; then
    find "$REPO_ROOT" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
    find "$REPO_ROOT" -type d -name '.pytest_cache' -prune -exec rm -rf {} + 2>/dev/null || true
fi
if [[ $PURGE_RECORDINGS -eq 1 ]]; then
    safe_rm "$RECORDINGS_DIR"
elif [[ $REC_COUNT -gt 0 ]]; then
    info "kept  $RECORDINGS_DIR  ($REC_COUNT files, $REC_SIZE)"
fi
if [[ $PURGE_PIP_CACHE -eq 1 ]]; then
    safe_rm "/root/.cache/pip"
    [[ -n "${SUDO_USER:-}" ]] && safe_rm "$(getent passwd "$SUDO_USER" | cut -d: -f6)/.cache/pip"
fi
ok "generated files removed"

# ── 4. Network profiles ─────────────────────────────────────────────────────
if command -v nmcli >/dev/null 2>&1; then
    say "Removing NetworkManager profiles"
    for c in radio-hotspot radio-ethernet; do
        if nmcli -t -f NAME connection show 2>/dev/null | grep -qx "$c"; then
            run nmcli connection delete "$c"
            info "deleted profile $c"
        fi
    done
    ok "network profiles removed"
fi

# ── 5. Firewall ─────────────────────────────────────────────────────────────
if [[ -n "$PORT" ]] && command -v ufw >/dev/null 2>&1 \
        && ufw status 2>/dev/null | grep -q "^Status: active"; then
    say "Removing the firewall rule"
    run ufw delete allow "${PORT}/tcp"
    ok "ufw rule for ${PORT}/tcp removed"
fi

# ── 6. Kernel command line ──────────────────────────────────────────────────
# Strip only OUR token and collapse the resulting double space. cmdline.txt
# must remain exactly one line, so this never rewrites the file wholesale.
say "Restoring the kernel command line"
cmdline=""
for f in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
    [[ -f "$f" ]] && { cmdline="$f"; break; }
done
if [[ -n "$cmdline" ]] && grep -q 'usbcore\.usbfs_memory_mb=' "$cmdline"; then
    if [[ $DRY_RUN -eq 1 ]]; then
        info "would strip usbcore.usbfs_memory_mb= from $cmdline"
    else
        cp "$cmdline" "$cmdline.pre-uninstall.bak"
        sed -i -e 's/ *usbcore\.usbfs_memory_mb=[0-9]*//' -e 's/  */ /g' -e 's/ *$//' "$cmdline"
        ok "stripped from $cmdline (backup: $cmdline.pre-uninstall.bak)"
        info "$(cat "$cmdline")"
    fi
else
    info "nothing to restore"
fi
safe_rm "${cmdline:-/nonexistent}.linda.bak"
safe_rm "${cmdline:-/nonexistent}.nist-omran.bak"

# ── 7. Locally built drivers ────────────────────────────────────────────────
PLUTO_SRC="/usr/local/src/SoapyPlutoSDR"
if [[ -d "$PLUTO_SRC" ]]; then
    say "Removing the locally built SoapyPlutoSDR"
    if [[ -f "$PLUTO_SRC/build/install_manifest.txt" && $DRY_RUN -eq 0 ]]; then
        while IFS= read -r installed; do
            safe_rm "$installed"
        done < "$PLUTO_SRC/build/install_manifest.txt"
    fi
    safe_rm "$PLUTO_SRC"
    run ldconfig
    ok "SoapyPlutoSDR removed"
fi

# ── 8. Groups ───────────────────────────────────────────────────────────────
if [[ $PURGE_GROUPS -eq 1 ]]; then
    say "Removing group membership for $SERVICE_USER"
    for g in plugdev dialout; do
        if id -nG "$SERVICE_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$g"; then
            run gpasswd -d "$SERVICE_USER" "$g"
            info "removed $SERVICE_USER from $g"
        fi
    done
    ok "group membership updated"
fi

# ── 9. Packages ─────────────────────────────────────────────────────────────
if [[ ${#REMOVE_PKGS[@]} -gt 0 ]]; then
    say "Removing packages"
    if [[ $DRY_RUN -eq 1 ]]; then
        info "would purge: ${REMOVE_PKGS[*]}"
    else
        DEBIAN_FRONTEND=noninteractive apt-get purge -y -q "${REMOVE_PKGS[@]}" \
            || warn "apt purge reported problems; see the output above"
        DEBIAN_FRONTEND=noninteractive apt-get autoremove --purge -y -q \
            || warn "apt autoremove reported problems"
        ok "packages removed"
    fi
fi

# ── Done ────────────────────────────────────────────────────────────────────
DONE=1
printf '\n\033[1;32m╔══════════════════════════════════════════════════════════════════╗\033[0m\n'
printf '\033[1;32m║  LINDA has been removed                                          ║\033[0m\n'
printf '\033[1;32m╚══════════════════════════════════════════════════════════════════╝\033[0m\n'
echo "    The git clone is still at: $REPO_ROOT"
echo "    Delete it with:            sudo rm -rf $REPO_ROOT"
if [[ $REC_COUNT -gt 0 && $PURGE_RECORDINGS -eq 0 ]]; then
    echo "    Recordings kept at:        $RECORDINGS_DIR ($REC_COUNT files, $REC_SIZE)"
fi
if [[ -n "$cmdline" ]]; then
    echo "    Reboot to drop the raised USB buffer:  sudo reboot"
fi
echo
