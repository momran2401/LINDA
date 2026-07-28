#!/usr/bin/env bash
# ============================================================================
# NIST-Omran radio viewer — one-shot installer / setup TUI.
#
#   sudo bash setup.sh              # interactive (whiptail TUI when available)
#   sudo bash setup.sh --defaults   # no questions: web mode, port 8000
#   bash setup.sh --deps-only       # just python deps into ./.venv (no root)
#   sudo bash setup.sh --skip-hardware-check  # provision before radio arrives
#   sudo bash setup.sh --defaults --device=pluto --mode=kiosk
#
# What it does (idempotent — safe to re-run):
#   1. Detects distro/arch; installs system deps via apt when available
#      (SoapySDR + common SDR driver modules, avahi mDNS, NetworkManager).
#   2. Creates ./.venv and installs live/requirements.txt (+ striqt, optional).
#   3. Asks (TUI) for: default mode (web / hotspot / ethernet / kiosk /
#      terminal), port, mDNS hostname, radio, hotspot SSID/password,
#      autostart. --defaults answers everything with safe defaults.
#   4. Writes /etc/radio-web/radio.env (0600) with role usernames and a
#      generated session-signing secret. Login is username-only.
#   5. Installs + enables the radio-web systemd unit, the Reset-Radio sudoers
#      rule, and (mode-dependent) a NetworkManager hotspot or shared-ethernet
#      profile so a connected laptop gets an address automatically.
#   6. Runs a post-install health check against /health.
#
# Never touches striqt/ (read-only upstream library).
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
ENV_DIR="/etc/radio-web"
ENV_FILE="$ENV_DIR/radio.env"
UNIT_FILE="/etc/systemd/system/radio-web.service"
SERVICE_NAME="radio-web"

MODE="web"           # web | hotspot | ethernet | kiosk | terminal
PORT="8000"
MDNS_HOST="radio"
DEVICE=""            # empty = server default (air8201b); or pluto/auto/...
AUTOSTART="yes"
HOTSPOT_SSID="radio-viewer"
HOTSPOT_PASS=""
INSTALL_STRIQT="yes"
STRIQT_COMMIT="2e7696d3cd7c9f710f406b4b83148476ead8c20f"  # v0.7.0; verified on the radio
ASSUME_DEFAULTS=0
DEPS_ONLY=0
SKIP_HARDWARE_CHECK=0
REBOOT_REQUIRED=0
SETUP_COMPLETE=0
WAS_SERVICE_ACTIVE=0

for arg in "$@"; do
    case "$arg" in
        --defaults)  ASSUME_DEFAULTS=1 ;;
        --deps-only) DEPS_ONLY=1 ;;
        --skip-hardware-check) SKIP_HARDWARE_CHECK=1 ;;
        --mode=*) MODE="${arg#*=}" ;;
        --device=*)
            DEVICE="${arg#*=}"
            if [[ "$DEVICE" == "air8201b" ]]; then DEVICE=""; fi
            ;;
        --port=*) PORT="${arg#*=}" ;;
        --hostname=*) MDNS_HOST="${arg#*=}" ;;
        --hotspot-ssid=*) HOTSPOT_SSID="${arg#*=}" ;;
        --help|-h)   grep '^#' "$0" | head -25; exit 0 ;;
        *) echo "unknown option: $arg (see --help)" >&2; exit 1 ;;
    esac
done

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARNING: %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

failure_report() {
    local rc=$?
    [[ $rc -eq 0 || $SETUP_COMPLETE -eq 1 ]] && return
    printf '\n\033[1;31mSETUP FAILED (exit %s).\033[0m\n' "$rc" >&2
    echo "  Re-run after correcting the error above; setup is idempotent." >&2
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --no-pager --full status "$SERVICE_NAME" 2>/dev/null | tail -20 >&2 || true
        journalctl -u "$SERVICE_NAME" -n 40 --no-pager 2>/dev/null >&2 || true
    fi
    if [[ $WAS_SERVICE_ACTIVE -eq 1 ]]; then
        systemctl restart "$SERVICE_NAME" 2>/dev/null || true
        echo "  Previous service restart was attempted after setup failure." >&2
    fi
}
trap failure_report EXIT

# ── 0. Environment detection ────────────────────────────────────────────────
HAVE_APT=0;     command -v apt-get   >/dev/null && HAVE_APT=1
HAVE_SYSTEMD=0; command -v systemctl >/dev/null && HAVE_SYSTEMD=1
HAVE_NMCLI=0;   command -v nmcli     >/dev/null && HAVE_NMCLI=1
IS_ROOT=0;      [[ ${EUID} -eq 0 ]] && IS_ROOT=1
ARCH="$(uname -m)"
SERVICE_USER="${SUDO_USER:-$(id -un)}"

say "NIST-Omran radio viewer setup  (arch: $ARCH, user: $SERVICE_USER)"
[[ $HAVE_APT -eq 1 ]]     || warn "no apt-get — system packages must be installed manually"
[[ $HAVE_SYSTEMD -eq 1 ]] || warn "no systemd — service autostart will be skipped"

bootstrap_prompter() {
    [[ $ASSUME_DEFAULTS -eq 0 && $DEPS_ONLY -eq 0 && $IS_ROOT -eq 1 && $HAVE_APT -eq 1 ]] || return 0
    if ! command -v whiptail >/dev/null; then
        apt-get update
        apt-get install -y whiptail || die "could not install the setup prompt UI"
    fi
}

validate_configuration() {
    [[ $DEPS_ONLY -eq 1 ]] && return
    [[ $IS_ROOT -eq 1 ]] || die "full setup must run as root: sudo bash setup.sh"
    id "$SERVICE_USER" >/dev/null 2>&1 || die "service user does not exist: $SERVICE_USER"
    [[ "$MODE" =~ ^(web|hotspot|ethernet|kiosk|terminal)$ ]] \
        || die "invalid mode: $MODE"
    [[ "$AUTOSTART" =~ ^(yes|no)$ ]] || die "autostart must be yes or no"
    [[ "$DEVICE" =~ ^(|air8201b|air7201b|air7101b|pluto|auto|demo)$ ]] \
        || die "invalid device: $DEVICE"
    [[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1024 && PORT <= 65535 )) \
        || die "port must be an integer from 1024 through 65535"
    [[ "$MDNS_HOST" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]] \
        || die "hostname must be 1-63 letters, digits, or hyphens"
    if [[ "$MODE" == "hotspot" ]]; then
        [[ ! "$HOTSPOT_SSID" =~ [[:cntrl:]] ]] \
            || die "hotspot SSID cannot contain control characters"
        (( ${#HOTSPOT_SSID} >= 1 && ${#HOTSPOT_SSID} <= 32 )) \
            || die "hotspot SSID must be 1-32 characters"
        [[ -z "$HOTSPOT_PASS" || ( ${#HOTSPOT_PASS} -ge 8 && ${#HOTSPOT_PASS} -le 63 ) ]] \
            || die "hotspot password must be empty (generate) or 8-63 characters"
    fi
    [[ "$DEVICE" == "demo" || "$INSTALL_STRIQT" != "no" ]] \
        || die "striqt is mandatory for every real-radio service"

    [[ -r /etc/os-release ]] || die "cannot identify the operating system"
    # shellcheck disable=SC1091
    . /etc/os-release
    case " ${ID:-} ${ID_LIKE:-} " in
        *" debian "*|*" ubuntu "*) ;;
        *) die "automated full setup supports Debian-family Linux only (found ${ID:-unknown})" ;;
    esac
    case "${ID:-}:${VERSION_ID:-}" in
        debian:12|debian:13|raspbian:12|raspbian:13|ubuntu:22.04|ubuntu:24.04) ;;
        *) die "unqualified OS release: ${ID:-unknown} ${VERSION_ID:-unknown} (supported: Debian/Raspberry Pi OS 12-13, Ubuntu 22.04/24.04)" ;;
    esac
    case "$ARCH" in x86_64|aarch64|arm64) ;; *)
        die "unsupported architecture: $ARCH (supported: x86_64, aarch64/arm64)"
    esac
    python3 - <<'PY' || die "Python 3.9 through 3.12 is required"
import sys
raise SystemExit(0 if (3, 9) <= sys.version_info[:2] <= (3, 12) else 1)
PY
    local free_kb mem_kb
    free_kb="$(df -Pk "$REPO_ROOT" | awk 'NR==2 {print $4}')"
    mem_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
    (( free_kb >= 5 * 1024 * 1024 )) \
        || die "at least 5 GiB free disk space is required"
    (( mem_kb >= 2 * 1024 * 1024 )) \
        || warn "less than 2 GiB RAM detected; Chromium/scientific workloads may be unreliable"

    if command -v ss >/dev/null && ss -H -ltn "sport = :$PORT" | grep -q .; then
        if ! systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
            die "TCP port $PORT is already in use"
        fi
        echo "  port $PORT is occupied by the existing $SERVICE_NAME service (safe rerun)"
    fi
}

# ── 1. System packages (apt) ────────────────────────────────────────────────
install_system_deps() {
    [[ $HAVE_APT -eq 1 && $IS_ROOT -eq 1 ]] || {
        warn "skipping apt packages (need root + apt). Required: python3-venv,"
        warn "python3-soapysdr + your radio's soapysdr-module-*, avahi-daemon."
        return 0
    }
    say "Installing system packages (apt)…"
    apt-get update
    # These are required on a clean Debian-family host.  Do not hide failures:
    # a half-created venv is much harder to diagnose than a failed installer.
    apt-get install -y ca-certificates curl git openssl sudo \
        python3 python3-venv python3-pip python3-dev \
        build-essential pkg-config cmake \
        whiptail avahi-daemon libgl1 iproute2 usbutils pciutils
    apt-get install -y python3-soapysdr soapysdr-tools libsoapysdr-dev \
        python3-pyqt5 python3-pyqt6 python3-pyqtgraph \
        || die "required SoapySDR/Qt runtime packages are unavailable from this distro's repositories"

    # Radio modules differ between Debian/Ubuntu/Raspberry Pi releases. Install
    # every module that exists in the configured repositories; absence is
    # reported later if it matters for the selected radio.
    local mod
    for mod in soapysdr-module-plutosdr soapysdr-module-rtlsdr \
               soapysdr-module-uhd soapysdr-module-hackrf \
               soapysdr-module-airspy soapysdr-module-bladerf \
               soapysdr-module-lms7 soapysdr-module-mirisdr \
               soapysdr-module-osmosdr soapysdr-module-remote \
               libiio-utils libiio-dev libad9361-dev; do
        if apt-cache show "$mod" >/dev/null 2>&1; then
            apt-get install -y "$mod"
        else
            echo "  optional radio package unavailable: $mod"
        fi
    done
    for mod in uhd-host hackrf rtl-sdr bladerf; do
        if apt-cache show "$mod" >/dev/null 2>&1; then
            apt-get install -y "$mod"
        fi
    done
    if command -v uhd_images_downloader >/dev/null 2>&1; then
        timeout 300s uhd_images_downloader || die "UHD firmware/image download failed"
    fi
    # Network modes need NetworkManager.
    if [[ "$MODE" == "hotspot" || "$MODE" == "ethernet" ]]; then
        if [[ -f /etc/network/interfaces ]] \
                && awk '$1=="iface" && $2!="lo" {found=1} END {exit !found}' /etc/network/interfaces; then
            die "/etc/network/interfaces configures a non-loopback interface; remove that legacy configuration before using NetworkManager $MODE mode"
        fi
        apt-get install -y network-manager \
            || die "NetworkManager is required for $MODE mode"
        command -v nmcli >/dev/null && HAVE_NMCLI=1
        if systemctl is-active --quiet dhcpcd 2>/dev/null; then
            say "Disabling dhcpcd to avoid conflict with NetworkManager…"
            systemctl disable --now dhcpcd
            REBOOT_REQUIRED=1
        fi
        systemctl enable --now NetworkManager \
            || die "NetworkManager could not be enabled"
    fi
    # Kiosk mode shows the web UI in a local Chromium-family browser.
    if [[ "$MODE" == "kiosk" ]] && ! command -v chromium >/dev/null \
            && ! command -v chromium-browser >/dev/null; then
        apt-get install -y chromium \
            || apt-get install -y chromium-browser \
            || die "kiosk mode requires Chromium, which this distro did not provide"
    fi
    if [[ "$MODE" == "kiosk" ]]; then
        apt-get install -y xserver-xorg xinit openbox lightdm dbus-x11 \
            || die "kiosk mode requires an X server, Openbox, and LightDM"
        install -d -m 0755 /etc/lightdm/lightdm.conf.d
        cat > /etc/lightdm/lightdm.conf.d/50-radio-kiosk.conf <<EOF
[Seat:*]
autologin-user=$SERVICE_USER
autologin-user-timeout=0
user-session=openbox
EOF
        systemctl set-default graphical.target
        systemctl enable --now lightdm
        REBOOT_REQUIRED=1
    fi
}

install_radio_permissions() {
    [[ $IS_ROOT -eq 1 && "$DEVICE" != "demo" ]] || return 0
    say "Installing SDR USB permissions…"
    getent group plugdev >/dev/null || groupadd --system plugdev
    local group
    for group in plugdev dialout; do
        getent group "$group" >/dev/null && usermod -aG "$group" "$SERVICE_USER"
    done
    install -d -m 0755 /etc/udev/rules.d
    cat > /etc/udev/rules.d/70-nist-omran-sdr.rules <<'EOF'
# Managed by NIST-Omran setup.sh. Common USB SDR devices.
SUBSYSTEM=="usb", ATTR{idVendor}=="0456", ATTR{idProduct}=="b673", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="0bda", ATTR{idProduct}=="2838", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="6089", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="6108", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="60a1", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="2cf0", MODE="0660", GROUP="plugdev", TAG+="uaccess"
EOF
    udevadm control --reload-rules
    udevadm trigger --subsystem-match=usb
    REBOOT_REQUIRED=1
}

# ── 2. Python virtualenv ────────────────────────────────────────────────────
install_python_deps() {
    say "Creating venv + installing Python deps…"
    local marker="$REPO_ROOT/.venv/.nist-omran-dependency-id"
    local wanted current backup
    wanted="$(
        sha256sum "$REPO_ROOT/live/requirements.txt" "$REPO_ROOT/live/constraints.txt"
        printf '%s\n' "$STRIQT_COMMIT"
        python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'
    )"
    wanted="$(printf '%s' "$wanted" | sha256sum | awk '{print $1}')"
    current="$(cat "$marker" 2>/dev/null || true)"
    if [[ -d "$REPO_ROOT/.venv" && "$current" != "$wanted" ]]; then
        backup="$REPO_ROOT/.venv.backup.$(date +%Y%m%d%H%M%S)"
        say "Existing venv is unqualified; preserving it at $backup"
        mv "$REPO_ROOT/.venv" "$backup"
    fi
    if [[ ! -d "$REPO_ROOT/.venv" ]]; then
        python3 -m venv --system-site-packages "$REPO_ROOT/.venv"
    fi
    # --system-site-packages so the venv sees the apt python3-soapysdr binding.
    "$REPO_ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
    if [[ "$INSTALL_STRIQT" != "no" ]]; then
        say "Installing radio-verified striqt 0.7.0 (commit ${STRIQT_COMMIT}) — may take a while…"
        # One transaction lets pip choose a mutually compatible numpy,
        # pandas, seaborn, FastAPI, uvicorn and striqt stack.
        "$REPO_ROOT/.venv/bin/python" -m pip install --upgrade \
            -r "$REPO_ROOT/live/requirements.txt" \
            -c "$REPO_ROOT/live/constraints.txt" \
            "striqt @ git+https://github.com/usnistgov/striqt@${STRIQT_COMMIT}" \
            || die "Python/radio stack installation failed (the environment was not left marked complete)"
    else
        "$REPO_ROOT/.venv/bin/python" -m pip install --upgrade \
            -r "$REPO_ROOT/live/requirements.txt" \
            -c "$REPO_ROOT/live/constraints.txt" \
            || die "Python dependency installation failed"
    fi
    "$REPO_ROOT/.venv/bin/python" -m pip check \
        || die "Python dependency consistency check failed"
    # Offline plot assets: the repo vendors uPlot; restore it when missing so
    # hotspot/ethernet modes never depend on a CDN.
    if [[ ! -s "$REPO_ROOT/live/web/vendor/uPlot.min.js" || ! -s "$REPO_ROOT/live/web/vendor/uPlot.min.css" ]]; then
        say "Fetching missing vendored uPlot assets…"
        mkdir -p "$REPO_ROOT/live/web/vendor"
        if [[ ! -s "$REPO_ROOT/live/web/vendor/uPlot.min.js" ]]; then
            curl -fsSL https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.iife.min.js \
                -o "$REPO_ROOT/live/web/vendor/uPlot.min.js"
        fi
        if [[ ! -s "$REPO_ROOT/live/web/vendor/uPlot.min.css" ]]; then
            curl -fsSL https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.min.css \
                -o "$REPO_ROOT/live/web/vendor/uPlot.min.css"
        fi
    fi
    echo "2d27e8ad3d228164525ce213f9dc716f39b4e3aee0cc773fb3491c96cf4921a2  $REPO_ROOT/live/web/vendor/uPlot.min.js" \
        | sha256sum -c - >/dev/null \
        || die "uPlot JavaScript asset is missing or has an invalid checksum"
    echo "df630c6a8d6f8eeaff264b50f73ce5b114f646ffd9a0bb74f049b0a00135fa04  $REPO_ROOT/live/web/vendor/uPlot.min.css" \
        | sha256sum -c - >/dev/null \
        || die "uPlot CSS asset is missing or has an invalid checksum"
    # Sanity: can the core import?
    "$REPO_ROOT/.venv/bin/python3" - <<'PYCHECK' || die "live/core import check failed"
import sys
sys.path.insert(0, "live")
import core
from core import devices
print("  live/core import OK")
try:
    found = devices.discover()
    print(f"  radios detected: {[f['label'] for f in found] or 'none'}")
except RuntimeError as e:
    print(f"  (device discovery unavailable: {e})")
PYCHECK
    printf '%s\n' "$wanted" > "$marker"
    if [[ $IS_ROOT -eq 1 ]]; then
        chown -R "$SERVICE_USER:$SERVICE_USER" "$REPO_ROOT/.venv"
    fi
}

verify_install() {
    say "Verifying installed software…"
    local py="$REPO_ROOT/.venv/bin/python"
    "$py" - <<'PYCHECK' || die "required Python imports failed"
import fastapi, numpy, pandas, seaborn, uvicorn, websockets
import PyQt5, PyQt6, pyqtgraph
print("  web/scientific Python imports OK")
print("  legacy PyQt5/PyQt6 frontend imports OK")
PYCHECK

    if [[ "$INSTALL_STRIQT" != "no" ]]; then
        "$py" - <<'PYCHECK' || die "striqt is not importable in .venv"
import striqt
from striqt.sensor import specs
print("  striqt.sensor import OK")
PYCHECK
    fi

    if [[ "$DEVICE" != "demo" ]]; then
        "$py" - <<'PYCHECK' || die "SoapySDR Python bindings are unavailable in .venv"
import SoapySDR
print("  SoapySDR Python import OK")
PYCHECK
        command -v SoapySDRUtil >/dev/null \
            || die "SoapySDRUtil is missing after installation"
    fi

    if [[ "$DEVICE" == "pluto" ]]; then
        SoapySDRUtil --info 2>&1 | grep -qiE 'pluto|libiio' \
            || die "Pluto selected, but the SoapyPlutoSDR/libiio driver is not installed by this distro"
    elif [[ -z "$DEVICE" || "$DEVICE" == air* ]]; then
        SoapySDRUtil --info 2>&1 | grep -qi 'SoapyAIRT' \
            || die "Deepwave selected, but proprietary SoapyAIRT is absent. Use the Deepwave AIR-T software image/installer, then rerun setup.sh"
    fi
    if [[ "$MODE" == "kiosk" ]]; then
        command -v chromium >/dev/null || command -v chromium-browser >/dev/null \
            || die "kiosk browser executable is missing"
        systemctl is-enabled --quiet lightdm \
            || die "kiosk display manager is not enabled"
    fi
}

qualify_hardware() {
    [[ $SKIP_HARDWARE_CHECK -eq 0 ]] || {
        warn "hardware qualification skipped by request"
        return 0
    }
    local selector="${DEVICE:-air8201b}"
    local py="$REPO_ROOT/.venv/bin/python"
    say "Running short end-to-end ${selector} qualification…"
    if [[ "$selector" == "demo" ]]; then
        "$py" "$REPO_ROOT/live/tools/hardware_qual.py" --demo --quick --timeout 10
    else
        timeout 180s runuser -u "$SERVICE_USER" -- \
            "$py" "$REPO_ROOT/live/tools/hardware_qual.py" \
            --device "$selector" --quick --timeout 10 \
            || die "radio qualification failed; connect/power the selected radio, inspect SoapySDRUtil --find, or provision with --skip-hardware-check"
    fi
}

stop_existing_service() {
    if [[ $HAVE_SYSTEMD -eq 1 && $IS_ROOT -eq 1 ]] \
            && systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        WAS_SERVICE_ACTIVE=1
        say "Stopping existing $SERVICE_NAME during installation/qualification…"
        systemctl stop "$SERVICE_NAME"
    fi
}

# ── 3. Interactive questions ────────────────────────────────────────────────
ask_tui() {
    [[ $ASSUME_DEFAULTS -eq 1 ]] && return 0
    if command -v whiptail >/dev/null && [[ -t 0 ]]; then
        MODE=$(whiptail --title "Radio viewer setup" --nocancel --menu \
            "Default mode (started on boot / by 'systemctl start radio-web'):" \
            18 72 5 \
            web      "Web server on the existing network (default)" \
            hotspot  "Web server + own Wi-Fi access point (no internet needed)" \
            ethernet "Web server + plug-and-play Ethernet (laptop direct)" \
            kiosk    "Web UI fullscreen on the radio's own display" \
            terminal "No service — run the curses monitor manually" \
            3>&1 1>&2 2>&3) || true
        PORT=$(whiptail --title "Port" --nocancel --inputbox \
            "Web server port:" 9 50 "$PORT" 3>&1 1>&2 2>&3) || true
        MDNS_HOST=$(whiptail --title "Hostname" --nocancel --inputbox \
            "mDNS hostname (reach the radio at <name>.local):" 9 60 \
            "$MDNS_HOST" 3>&1 1>&2 2>&3) || true
        DEVICE=$(whiptail --title "Radio" --nocancel --menu \
            "Which radio will this host drive?" 17 64 6 \
            ""       "AIR8201B (default)" \
            air7201b "AIR7201B" \
            air7101b "AIR7101B" \
            pluto    "PlutoSDR" \
            auto     "Auto-detect at startup (SoapySDR enumeration)" \
            demo     "Demo (synthetic IQ, no hardware)" \
            3>&1 1>&2 2>&3) || true
        if [[ "$MODE" == "hotspot" ]]; then
            HOTSPOT_SSID=$(whiptail --nocancel --inputbox \
                "Hotspot SSID:" 9 50 "$HOTSPOT_SSID" 3>&1 1>&2 2>&3) || true
            HOTSPOT_PASS=$(whiptail --nocancel --passwordbox \
                "Hotspot password (min 8 chars; empty = generate):" 9 60 \
                3>&1 1>&2 2>&3) || true
        fi
        if whiptail --title "Autostart" --yesno \
            "Enable the radio-web service to start on boot?" 8 55; then
            AUTOSTART="yes"; else AUTOSTART="no"; fi
    else
        echo "(whiptail/tty unavailable — plain prompts; Enter accepts defaults)"
        read -rp "Mode [web/hotspot/ethernet/kiosk/terminal] ($MODE): " a || true
        MODE="${a:-$MODE}"
        read -rp "Port ($PORT): " a || true;             PORT="${a:-$PORT}"
        read -rp "mDNS hostname ($MDNS_HOST): " a || true; MDNS_HOST="${a:-$MDNS_HOST}"
        read -rp "Device [air8201b default/air7201b/air7101b/pluto/auto/demo] (): " a || true
        DEVICE="${a:-}"; [[ "$DEVICE" == "air8201b" ]] && DEVICE=""
        read -rp "Autostart on boot? [yes/no] ($AUTOSTART): " a || true
        AUTOSTART="${a:-$AUTOSTART}"
    fi
    return 0
}

# ── 4. Credentials + environment file ──────────────────────────────────────
genpw() { openssl rand -hex 12 2>/dev/null || head -c24 /dev/urandom | base64 | tr -d '+/=' ; }

write_env_file() {
    [[ $IS_ROOT -eq 1 ]] || { warn "not root — skipping $ENV_FILE"; return 0; }
    say "Writing $ENV_FILE (username-only role login + signed sessions)…"
    mkdir -p "$ENV_DIR"
    local secret
    if [[ -f "$ENV_FILE" ]] && grep -q RADIO_SESSION_SECRET "$ENV_FILE"; then
        echo "  existing session-signing secret kept"
        # Refresh mode/device settings, migrate old files away from passwords,
        # and retain any customized role usernames.
        sed -i -e "s/^RADIO_MODE=.*/RADIO_MODE=\"$MODE\"/" \
               -e "s/^RADIO_PORT=.*/RADIO_PORT=\"$PORT\"/" \
               -e "s/^RADIO_DEVICE=.*/RADIO_DEVICE=\"$DEVICE\"/" \
               -e '/^\(ADMIN\|VIEWER\|INTERN\)_PASS=/d' "$ENV_FILE"
        grep -q '^ADMIN_USER=' "$ENV_FILE" || echo 'ADMIN_USER="admin"' >> "$ENV_FILE"
        grep -q '^VIEWER_USER=' "$ENV_FILE" || echo 'VIEWER_USER="viewer"' >> "$ENV_FILE"
        grep -q '^INTERN_USER=' "$ENV_FILE" || echo 'INTERN_USER="intern"' >> "$ENV_FILE"
        chmod 600 "$ENV_FILE"
        return 0
    fi
    secret="$(openssl rand -hex 32 2>/dev/null || genpw)"
    cat > "$ENV_FILE" <<EOF
# Generated by setup.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ) — mode/creds for radio-web.
# Edit + 'systemctl restart radio-web' to apply. chmod 600 — keep it that way.
# Values are quoted for systemd EnvironmentFile parsing.
RADIO_MODE="$MODE"
RADIO_PORT="$PORT"
RADIO_DEVICE="$DEVICE"
RADIO_SERVICE_NAME="$SERVICE_NAME"
RADIO_EXTRA_ARGS=""
ADMIN_USER="admin"
VIEWER_USER="viewer"
INTERN_USER="intern"
RADIO_SESSION_SECRET="$secret"
EOF
    chmod 600 "$ENV_FILE"
    CREDS_NOTE="admin (admin role)   viewer (viewer role)   intern (intern role)"
}

# ── 5. systemd unit + sudoers + mDNS ───────────────────────────────────────
install_service() {
    [[ $IS_ROOT -eq 1 && $HAVE_SYSTEMD -eq 1 ]] || { warn "skipping systemd unit"; return 0; }
    [[ "$MODE" == "terminal" ]] && { echo "  terminal mode — no service installed"; return 0; }
    say "Installing systemd unit $UNIT_FILE…"
    sed -e "s|@REPO_ROOT@|$REPO_ROOT|g" \
        -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
        -e "s|@SERVICE_UID@|$(id -u "$SERVICE_USER")|g" \
        -e "s|@SERVICE_HOME@|$(getent passwd "$SERVICE_USER" | cut -d: -f6)|g" \
        -e "s|@RADIO_MODE@|$MODE|g" \
        "$REPO_ROOT/deploy/radio-web.service.template" > "$UNIT_FILE"
    chmod +x "$REPO_ROOT/deploy/run_service.sh"
    systemctl daemon-reload
    say "Installing Reset-Radio sudoers rule…"
    bash "$REPO_ROOT/live/install_radio_web_sudoers.sh" "$SERVICE_USER" "$SERVICE_NAME" \
        || warn "sudoers install failed — the Reset Radio button won't work"
    if [[ -n "$MDNS_HOST" ]]; then
        say "mDNS: radio will be reachable at ${MDNS_HOST}.local"
        hostnamectl set-hostname "$MDNS_HOST" 2>/dev/null \
            || warn "could not set hostname (set it manually for ${MDNS_HOST}.local)"
        # hostnamectl does not update /etc/hosts.  A stale 127.0.1.1 entry
        # makes every subsequent sudo invocation print "unable to resolve
        # host".  Replace the Debian host alias while keeping localhost/IPv6.
        if grep -q '^127\.0\.1\.1[[:space:]]' /etc/hosts; then
            sed -i "s/^127\\.0\\.1\\.1.*/127.0.1.1 $MDNS_HOST/" /etc/hosts
        else
            printf '127.0.1.1 %s\n' "$MDNS_HOST" >> /etc/hosts
        fi
        if [[ -d /etc/cloud/cloud.cfg.d ]]; then
            cat > /etc/cloud/cloud.cfg.d/99-radio-hostname.cfg <<'EOF'
# Managed by NIST-Omran setup.sh.
preserve_hostname: true
manage_etc_hosts: false
EOF
        fi
        systemctl enable --now avahi-daemon 2>/dev/null || warn "avahi not available"
    fi
    # Open the port when UFW is enforcing (common on Ubuntu images).
    if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "^Status: active"; then
        ufw allow "$PORT/tcp" >/dev/null 2>&1 \
            && echo "  ufw: allowed $PORT/tcp" \
            || warn "could not add the ufw rule for $PORT/tcp"
    fi
    if [[ "$AUTOSTART" == "yes" ]]; then
        systemctl enable "$SERVICE_NAME" >/dev/null
        systemctl restart "$SERVICE_NAME"
        echo "  service enabled + started"
    else
        systemctl disable "$SERVICE_NAME" >/dev/null 2>&1 || true
        echo "  autostart disabled (start manually: systemctl start $SERVICE_NAME)"
    fi
}

# ── 6. Network profiles (hotspot / plug-and-play ethernet) ─────────────────
setup_network() {
    [[ $IS_ROOT -eq 1 ]] || return 0
    case "$MODE" in
    hotspot)
        [[ $HAVE_NMCLI -eq 1 ]] || die "hotspot needs NetworkManager (nmcli)"
        local wifi_dev
        wifi_dev="$(nmcli -t -f DEVICE,TYPE device | awk -F: '$2=="wifi"{print $1; exit}')"
        [[ -n "$wifi_dev" ]] || die "no Wi-Fi interface found for hotspot mode"
        nmcli -f WIFI-PROPERTIES.AP device show "$wifi_dev" 2>/dev/null | grep -qi 'yes' \
            || die "Wi-Fi interface $wifi_dev does not advertise access-point support"
        [[ -n "$HOTSPOT_PASS" ]] || HOTSPOT_PASS="$(genpw)"
        say "Configuring Wi-Fi access point '$HOTSPOT_SSID' on $wifi_dev…"
        nmcli connection delete radio-hotspot >/dev/null 2>&1 || true
        nmcli connection add type wifi ifname "$wifi_dev" con-name radio-hotspot \
            autoconnect yes ssid "$HOTSPOT_SSID" \
            802-11-wireless.mode ap 802-11-wireless.band bg \
            ipv4.method shared wifi-sec.key-mgmt wpa-psk \
            wifi-sec.psk "$HOTSPOT_PASS" >/dev/null
        nmcli connection show radio-hotspot >/dev/null \
            || die "NetworkManager did not retain the hotspot profile"
        REBOOT_REQUIRED=1
        HOTSPOT_NOTE="SSID: $HOTSPOT_SSID   password: $HOTSPOT_PASS   URL: http://10.42.0.1:$PORT"
        ;;
    ethernet)
        [[ $HAVE_NMCLI -eq 1 ]] || die "ethernet mode needs NetworkManager (nmcli)"
        local eth_dev
        eth_dev="$(nmcli -t -f DEVICE,TYPE device | awk -F: '$2=="ethernet"{print $1; exit}')"
        [[ -n "$eth_dev" ]] || die "no ethernet interface found for shared-ethernet mode"
        say "Configuring plug-and-play (shared) Ethernet on $eth_dev…"
        # ipv4.method=shared: the radio serves DHCP on this port, so a directly
        # connected laptop configures itself — open http://10.42.0.1:PORT (or
        # http://<hostname>.local:PORT via mDNS).
        nmcli connection delete radio-ethernet >/dev/null 2>&1 || true
        nmcli connection add type ethernet ifname "$eth_dev" con-name radio-ethernet \
            autoconnect yes ipv4.method shared >/dev/null
        nmcli connection show radio-ethernet >/dev/null \
            || die "NetworkManager did not retain the shared-ethernet profile"
        REBOOT_REQUIRED=1
        ETHERNET_NOTE="plug a laptop into $eth_dev and open http://10.42.0.1:$PORT (or http://${MDNS_HOST}.local:$PORT)"
        ;;
    esac
}

# ── 7. Health check ────────────────────────────────────────────────────────
health_check() {
    [[ "$MODE" == "terminal" ]] && return 0
    [[ $HAVE_SYSTEMD -eq 1 && $IS_ROOT -eq 1 && "$AUTOSTART" == "yes" ]] || return 0
    say "Post-install health check…"
    for _ in $(seq 1 20); do
        if curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1; then
            curl -fsS "http://localhost:$PORT/health" | head -c 400; echo
            echo "  HEALTHY."
            return 0
        fi
        sleep 1
    done
    systemctl --no-pager --full status "$SERVICE_NAME" 2>&1 | tail -30 >&2 || true
    journalctl -u "$SERVICE_NAME" -n 80 --no-pager >&2 || true
    die "service did not answer /health in 20 seconds"
}

# ── main ────────────────────────────────────────────────────────────────────
if [[ $DEPS_ONLY -eq 1 ]]; then
    install_python_deps
    DEVICE=demo verify_install
    SETUP_COMPLETE=1
    exit 0
fi

bootstrap_prompter
ask_tui
validate_configuration
stop_existing_service
install_system_deps
install_radio_permissions
install_python_deps
verify_install
qualify_hardware
write_env_file
setup_network
install_service
health_check

say "Setup complete."
echo "  mode:      $MODE"
echo "  device:    ${DEVICE:-air8201b (default)}"
[[ "$MODE" != "terminal" ]] && echo "  URL:       http://${MDNS_HOST}.local:$PORT  (or the host's IP)"
[[ -n "${CREDS_NOTE:-}" ]]    && echo "  logins:    $CREDS_NOTE"
[[ -n "${HOTSPOT_NOTE:-}" ]]  && echo "  hotspot:   $HOTSPOT_NOTE"
[[ -n "${ETHERNET_NOTE:-}" ]] && echo "  ethernet:  $ETHERNET_NOTE"
echo "  login:      enter admin, viewer, or intern as the username; no password"
echo "  logs:      journalctl -u $SERVICE_NAME -f"
echo "  terminal:  ./.venv/bin/python live/striqt_standalone_terminal.py --demo"
if [[ -e /var/run/reboot-required ]]; then REBOOT_REQUIRED=1; fi
if [[ $REBOOT_REQUIRED -eq 1 ]]; then
    echo "  reboot:    REQUIRED (udev/groups/network/display or package updates changed)"
    echo "             sudo reboot"
fi
SETUP_COMPLETE=1
