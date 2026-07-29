#!/usr/bin/env bash
# ============================================================================
# LINDA — one-command installer for the live two-channel RF viewer.
#
#     sudo bash setup.sh            # detect the radio, ask 2 questions, done
#     sudo bash setup.sh --yes      # same, but ask nothing (all defaults)
#     sudo bash setup.sh --demo     # synthetic IQ; no radio required
#     bash setup.sh --deps-only     # just the Python env, no root, no service
#
# Overrides (rarely needed — everything below is auto-detected):
#     --device=auto|uhd|pluto|rtlsdr|hackrf|airspy|bladerf|limesdr
#     --device=air8201b|air7201b|air7101b|demo|driver=X[,serial=Y]
#     --mode=web|kiosk|hotspot|ethernet|terminal
#     --port=8000  --hostname=<name>  --hotspot-ssid=X  --hotspot-pass=X
#     --skip-radio-check            provision before the radio arrives
#
# What it does, in order (idempotent — re-running is always safe):
#     1.  preflight: root, distro, arch, Python, disk, port
#     2.  identify the attached radio over USB *before* choosing any driver
#     3.  apt: base tools + ONLY the driver stack that radio needs
#     4.  radio enablement: UHD images + UHD_IMAGES_DIR, usbfs buffer, udev
#     5.  an ISOLATED .venv (+ the system SoapySDR binding linked in) and the
#         pinned striqt build
#     6.  prove it: imports, driver enumeration, a real capture
#     7.  role logins, systemd unit, sudoers, mDNS, optional network profile
#     8.  health check, then print the URL to open
#
# A full transcript is written to the log named at startup — attach that file
# to any bug report. striqt/ is upstream and is never touched.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
ENV_DIR="/etc/radio-web"
ENV_FILE="$ENV_DIR/radio.env"
UNIT_FILE="/etc/systemd/system/radio-web.service"
SERVICE_NAME="radio-web"
VENV="$REPO_ROOT/.venv"
VENV_PY="$VENV/bin/python"

# striqt 0.7.0, the exact commit verified against the radio. Do not float this.
STRIQT_COMMIT="2e7696d3cd7c9f710f406b4b83148476ead8c20f"

MODE="web"
PORT="8000"
MDNS_HOST=""                 # empty → keep this host's current name
DEVICE=""                    # empty → decide from USB detection
RADIO_KIND=""                # uhd|pluto|rtlsdr|hackrf|airspy|bladerf|limesdr|airt|demo|unknown
RADIO_LABEL=""
HOTSPOT_SSID="radio-viewer"
HOTSPOT_PASS=""
ASK=1
SKIP_RADIO_CHECK=0
DEPS_ONLY=0
REBOOT_REQUIRED=0
SETUP_COMPLETE=0
WAS_SERVICE_ACTIVE=0
RADIO_CHECK_STATUS="not run"
UHD_IMAGES_PATH=""

# ── 0. Output helpers and the failure trap ──────────────────────────────────
# The trap is installed before ANY other logic runs. A `set -e` abort with no
# trap in place exits silently, which on a console is indistinguishable from
# "the installer did nothing" — that exact failure mode cost a debugging
# session once already. Nothing below this block may run before it.
say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '\033[1;32m    ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m    ! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

failure_report() {
    local rc=$?
    if [[ $rc -eq 0 || $SETUP_COMPLETE -eq 1 ]]; then
        return 0
    fi
    printf '\n\033[1;31m─── SETUP FAILED (exit %s) ───\033[0m\n' "$rc" >&2
    echo "  Fix the error above and re-run; setup is idempotent." >&2
    [[ -n "${LOG_FILE:-}" ]] && echo "  Full transcript: $LOG_FILE" >&2
    if command -v systemctl >/dev/null 2>&1; then
        journalctl -u "$SERVICE_NAME" -n 30 --no-pager 2>/dev/null >&2 || true
    fi
    if [[ $WAS_SERVICE_ACTIVE -eq 1 ]]; then
        systemctl restart "$SERVICE_NAME" 2>/dev/null || true
        echo "  The previously running service was restarted." >&2
    fi
    return 0
}
trap failure_report EXIT

# ── 1. Arguments ────────────────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --yes|-y)            ASK=0 ;;
        --demo)              DEVICE="demo"; ASK=0 ;;
        --deps-only)         DEPS_ONLY=1 ;;
        --skip-radio-check)  SKIP_RADIO_CHECK=1 ;;
        --mode=*)            MODE="${arg#*=}" ;;
        --device=*)          DEVICE="${arg#*=}" ;;
        --port=*)            PORT="${arg#*=}" ;;
        --hostname=*)        MDNS_HOST="${arg#*=}" ;;
        --hotspot-ssid=*)    HOTSPOT_SSID="${arg#*=}" ;;
        --hotspot-pass=*)    HOTSPOT_PASS="${arg#*=}" ;;
        --help|-h)
            # awk (ERE) rather than sed: `\?` in a BRE is a GNU extension and
            # silently fails to strip anywhere else.
            awk 'NR>1 && /^#/ {sub(/^# ?/, ""); if ($0 !~ /^=+$/) print; next}
                 NR>1 {exit}' "$0"
            SETUP_COMPLETE=1; exit 0 ;;
        *) die "unknown option: $arg  (run: bash setup.sh --help)" ;;
    esac
done

IS_ROOT=0; [[ ${EUID} -eq 0 ]] && IS_ROOT=1
ARCH="$(uname -m)"
SERVICE_USER="${SUDO_USER:-$(id -un)}"
HAVE_APT=0;     command -v apt-get   >/dev/null 2>&1 && HAVE_APT=1
HAVE_SYSTEMD=0; command -v systemctl >/dev/null 2>&1 && HAVE_SYSTEMD=1

# Transcript. Everything after this point lands in the log as well as on the
# terminal, so "it printed something weird 10 minutes ago" is recoverable.
if [[ $IS_ROOT -eq 1 ]]; then
    LOG_FILE="/var/log/radio-web-setup.log"
else
    LOG_FILE="$REPO_ROOT/setup.log"
fi
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || LOG_FILE="/tmp/radio-web-setup.log"
# Keep a handle on the REAL terminal before stdout/stderr become a pipe into
# tee. whiptail paints its dialog on stdout and returns the selection on
# stderr; with both teed, the dialog would be scribbled into the log file and
# the user would see nothing. fd 9 stays connected to the terminal so the
# questions below can still draw (see ask_questions).
exec 9>&2
exec > >(tee -a "$LOG_FILE") 2>&1

printf '\n\033[1;36m╔══════════════════════════════════════════════════════════════════╗\033[0m\n'
printf '\033[1;36m║  LINDA installer — live IQ Navigation and Display Application    ║\033[0m\n'
printf '\033[1;36m╚══════════════════════════════════════════════════════════════════╝\033[0m\n'
info "host $(uname -n)   arch $ARCH   user $SERVICE_USER"
info "log  $LOG_FILE"

# ── 2. Preflight ────────────────────────────────────────────────────────────
preflight() {
    [[ $DEPS_ONLY -eq 1 ]] && return 0
    [[ $IS_ROOT -eq 1 ]] || die "run as root:  sudo bash setup.sh"
    id "$SERVICE_USER" >/dev/null 2>&1 || die "no such user: $SERVICE_USER"
    [[ "$MODE" =~ ^(web|hotspot|ethernet|kiosk|terminal)$ ]] || die "invalid --mode: $MODE"
    [[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1024 && PORT <= 65535 )) \
        || die "--port must be 1024-65535"

    [[ -r /etc/os-release ]] || die "cannot identify this operating system"
    # shellcheck disable=SC1091
    . /etc/os-release
    case " ${ID:-} ${ID_LIKE:-} " in
        *" debian "*|*" ubuntu "*) ;;
        *) die "automated setup supports Debian-family Linux (found: ${ID:-unknown}).
       Everything else can still run demo mode:  bash setup.sh --deps-only" ;;
    esac
    case "$ARCH" in
        x86_64|aarch64|arm64) ;;
        *) die "unsupported architecture: $ARCH (need x86_64 or 64-bit ARM)" ;;
    esac
    python3 - <<'PY' || die "need Python 3.9-3.13 (found: $(python3 -V 2>&1))"
import sys
raise SystemExit(0 if (3, 9) <= sys.version_info[:2] <= (3, 13) else 1)
PY
    local free_kb
    free_kb="$(df -Pk "$REPO_ROOT" | awk 'NR==2 {print $4}')"
    (( free_kb >= 4 * 1024 * 1024 )) || die "need at least 4 GiB free on $REPO_ROOT"

    if command -v ss >/dev/null 2>&1 && ss -H -ltn "sport = :$PORT" 2>/dev/null | grep -q .; then
        systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null \
            || die "TCP port $PORT is already used by something else"
        info "port $PORT held by the existing $SERVICE_NAME (safe re-run)"
    fi
    ok "preflight passed"
    return 0
}

# ── 3. apt helper ───────────────────────────────────────────────────────────
# --no-install-recommends is not an optimisation here, it is a correctness
# fix. Debian's uhd-host and soapysdr-tools recommend GNU Radio, Qt5+Qt6,
# GDAL, MariaDB clients and the soapysdr module-all bundle: a plain
# `apt-get install uhd-host` pulled 180 packages and 974 MB onto a Pi, and the
# audio module in that bundle spews ALSA/Jack/PulseAudio errors over every
# device enumeration afterwards. We install what we actually use.
APT_UPDATED=0
PKG_MANIFEST="/etc/radio-web/installed-packages"

pkg_installed() {
    dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'ok installed'
}

# Record packages that were NOT already on this machine before we asked for
# them. uninstall_linda.sh removes exactly this set, so it can never rip out
# something the operating system shipped with — the difference between a clean
# uninstall and a broken Pi.
record_new_packages() {
    [[ $IS_ROOT -eq 1 ]] || return 0
    mkdir -p "$(dirname "$PKG_MANIFEST")" 2>/dev/null || return 0
    local p
    for p in "$@"; do
        if ! pkg_installed "$p"; then
            grep -qxF "$p" "$PKG_MANIFEST" 2>/dev/null || echo "$p" >> "$PKG_MANIFEST"
        fi
    done
    return 0
}

apt_install() {
    [[ $HAVE_APT -eq 1 && $IS_ROOT -eq 1 ]] || return 0
    record_new_packages "$@"
    if [[ $APT_UPDATED -eq 0 ]]; then
        DEBIAN_FRONTEND=noninteractive apt-get update -qq
        APT_UPDATED=1
    fi
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "$@"
}

apt_has() {
    apt-cache show "$1" >/dev/null 2>&1
}

# ── 4. Radio identification (before any driver exists) ──────────────────────
# Chicken-and-egg: SoapySDR cannot enumerate a radio whose driver is not
# installed, and we do not want to install every driver. USB IDs are readable
# with no driver at all, so the hardware tells us which stack to fetch.
# The trailing colon on a vendor-only entry is REQUIRED: lsusb -d takes
# "vendor:product" and refuses to parse a bare "2500", so a vendor-wide match
# must be written "2500:". Dropping it silently detects nothing.
declare -a USB_RADIO_TABLE=(
    # vid:[pid]   kind      apt package(s)                       label
    "2500:|uhd|soapysdr-module-uhd uhd-host|Ettus USRP (B2xx family)"
    "0456:b673|pluto|-|ADALM-Pluto"
    "0bda:2832|rtlsdr|soapysdr-module-rtlsdr|RTL-SDR"
    "0bda:2838|rtlsdr|soapysdr-module-rtlsdr|RTL-SDR"
    "1d50:6089|hackrf|soapysdr-module-hackrf|HackRF One"
    "1d50:60a1|airspy|soapysdr-module-airspy|Airspy"
    "1d50:6108|limesdr|soapysdr-module-lms7|LimeSDR"
    "2cf0:|bladerf|soapysdr-module-bladerf|Nuand bladeRF"
)
RADIO_PKGS=""

usb_present() {
    # Belt and braces against the bug above: normalise a bare vendor id to the
    # "vendor:" form lsusb actually accepts. Returns non-zero when nothing
    # matches, so callers must keep it inside a condition (errexit-exempt).
    local id="$1"
    [[ "$id" == *:* ]] || id="${id}:"
    lsusb -d "$id" >/dev/null 2>&1
}

# Every SoapySDR driver module the distribution ships, EXCEPT the audio one:
# that probes ALSA/Jack/PulseAudio on every single enumeration and buries the
# log on a headless Pi. Installed only as a fallback for a radio the USB table
# does not recognise.
SOAPY_EXTRA_MODULES="soapysdr-module-rtlsdr soapysdr-module-hackrf
soapysdr-module-airspy soapysdr-module-bladerf soapysdr-module-lms7
soapysdr-module-mirisdr soapysdr-module-osmosdr soapysdr-module-redpitaya
soapysdr-module-remote soapysdr-module-rfspace soapysdr-module-uhd"

soapy_sees_a_radio() {
    command -v SoapySDRUtil >/dev/null 2>&1 || return 1
    SoapySDRUtil --find 2>/dev/null | grep -qi 'Found device'
}

# The USB table below can only ever list radios we thought of. This is the
# catch-all that makes "any SoapySDR device" true: ask SoapySDR itself, and if
# it still sees nothing, install the rest of the driver modules and ask again.
# It also covers radios that are not on USB at all (SoapyRemote, networked
# receivers), which no USB table could ever match.
broaden_driver_search() {
    case "$RADIO_KIND" in
        none|unknown) ;;
        *) return 0 ;;
    esac
    [[ $HAVE_APT -eq 1 && $IS_ROOT -eq 1 ]] || return 0
    if soapy_sees_a_radio; then
        RADIO_KIND="soapy"
        RADIO_LABEL="SoapySDR device (its driver was already installed)"
        ok "$RADIO_LABEL"
        return 0
    fi
    say "Radio not in the known list — installing the remaining SoapySDR drivers"
    # shellcheck disable=SC2086
    apt_install $SOAPY_EXTRA_MODULES || warn "some driver modules were unavailable"
    if soapy_sees_a_radio; then
        RADIO_KIND="soapy"
        RADIO_LABEL="SoapySDR device (found once every driver was present)"
        ok "$RADIO_LABEL"
    else
        warn "no radio is visible to SoapySDR even with every driver installed"
    fi
    return 0
}

detect_radio() {
    # An explicit --demo settles it; probing hardware we are told to ignore
    # would only install drivers for a radio nobody asked to use.
    if [[ "${DEVICE:-}" == "demo" ]]; then
        RADIO_KIND="demo"
        RADIO_LABEL="demo (synthetic IQ, no hardware)"
        return 0
    fi
    say "Looking for a radio"
    # An AIR-T carries its SDR on-board with the proprietary SoapyAIRT driver
    # already in the vendor image — nothing to install, just recognise it.
    if command -v SoapySDRUtil >/dev/null 2>&1 \
            && SoapySDRUtil --info 2>/dev/null | grep -qi 'SoapyAIRT'; then
        RADIO_KIND="airt"; RADIO_LABEL="Deepwave AIR-T (SoapyAIRT present)"
        DEVICE="${DEVICE:-auto}"
        ok "$RADIO_LABEL"
        return 0
    fi
    if ! command -v lsusb >/dev/null 2>&1; then
        RADIO_KIND="unknown"; RADIO_LABEL="cannot probe USB (lsusb missing)"
        warn "$RADIO_LABEL"
        return 0
    fi
    local row id kind pkgs label
    for row in "${USB_RADIO_TABLE[@]}"; do
        IFS='|' read -r id kind pkgs label <<<"$row"
        if usb_present "$id"; then
            RADIO_KIND="$kind"; RADIO_LABEL="$label"
            [[ "$pkgs" != "-" ]] && RADIO_PKGS="$pkgs"
            ok "found: $label  (USB $id)"
            return 0
        fi
    done
    RADIO_KIND="none"; RADIO_LABEL="no supported radio on USB"
    warn "$RADIO_LABEL"
    # Show what IS attached. When detection is wrong — and it has been — the
    # difference between "nothing is plugged in" and "we failed to recognise
    # the thing that is plugged in" has to be visible without a second run.
    warn "USB devices seen (excluding root hubs):"
    lsusb 2>/dev/null | grep -v '1d6b:000' | sed 's/^/      /' || true
    return 0
}

# Map the detected kind (or an explicit --device) onto the selector the server
# takes. Written as an if/return-0 function on purpose: a bare `[[ … ]] && …`
# tail returns the failed test's status and `set -e` kills the installer.
resolve_selector() {
    case "${DEVICE:-}" in
        "" )        ;;                              # fall through to detection
        uhd|usrp)   DEVICE="driver=uhd"; RADIO_KIND="uhd"; return 0 ;;
        rtlsdr)     DEVICE="driver=rtlsdr";   RADIO_KIND="rtlsdr";  return 0 ;;
        hackrf)     DEVICE="driver=hackrf";   RADIO_KIND="hackrf";  return 0 ;;
        airspy)     DEVICE="driver=airspy";   RADIO_KIND="airspy";  return 0 ;;
        bladerf)    DEVICE="driver=bladerf";  RADIO_KIND="bladerf"; return 0 ;;
        limesdr)    DEVICE="driver=lime";     RADIO_KIND="limesdr"; return 0 ;;
        pluto)      RADIO_KIND="pluto"; return 0 ;;
        demo)       RADIO_KIND="demo";  return 0 ;;
        air*)       RADIO_KIND="airt";  return 0 ;;
        *)          return 0 ;;                     # driver=…/auto: keep as-is
    esac
    case "$RADIO_KIND" in
        uhd)      DEVICE="driver=uhd" ;;
        rtlsdr)   DEVICE="driver=rtlsdr" ;;
        hackrf)   DEVICE="driver=hackrf" ;;
        airspy)   DEVICE="driver=airspy" ;;
        bladerf)  DEVICE="driver=bladerf" ;;
        limesdr)  DEVICE="driver=lime" ;;
        pluto)    DEVICE="pluto" ;;
        airt)     DEVICE="auto" ;;
        # Recognised by SoapySDR but not by our USB table: let the enumeration
        # pick it, exactly as --device auto would.
        soapy)    DEVICE="auto" ;;
        *)        DEVICE="demo" ;;
    esac
    return 0
}

# ── 5. The two questions ────────────────────────────────────────────────────
ask_questions() {
    [[ $ASK -eq 1 && $DEPS_ONLY -eq 0 ]] || return 0
    local have_tui=0
    command -v whiptail >/dev/null 2>&1 && [[ -t 0 && -t 9 ]] && have_tui=1

    if [[ $have_tui -eq 1 ]]; then
        # `2>&1 1>&9`, in that order: the selection (whiptail's stderr) goes to
        # the capturing pipe, the dialog (its stdout) goes to fd 9 — the real
        # terminal saved before the tee. The usual `3>&1 1>&2 2>&3` idiom
        # assumes stderr IS the terminal, which it is not once we are logging.
        MODE=$(whiptail --title "LINDA setup (1 of 2)" --nocancel --menu \
            "How should this machine serve the viewer?" 17 74 5 \
            web      "Web server on your existing network  (recommended)" \
            kiosk    "Web server + fullscreen browser on the local display" \
            hotspot  "Web server + its own Wi-Fi access point (no network)" \
            ethernet "Web server + direct Ethernet to a laptop (auto DHCP)" \
            terminal "No service; run the curses viewer by hand" \
            2>&1 1>&9) || true
        local choice
        choice=$(whiptail --title "LINDA setup (2 of 2)" --nocancel --menu \
            "Which radio should it drive?\n\nDetected: $RADIO_LABEL" 18 74 4 \
            detected "Use the detected radio (recommended)" \
            uhd      "Ettus USRP B2xx / B205mini" \
            pluto    "ADALM-Pluto" \
            demo     "Demo — synthetic IQ, no hardware" \
            2>&1 1>&9) || true
        [[ "$choice" != "detected" && -n "$choice" ]] && DEVICE="$choice"
        if [[ "$MODE" == "hotspot" && -z "$HOTSPOT_PASS" ]]; then
            HOTSPOT_PASS=$(whiptail --title "Hotspot password" --nocancel \
                --passwordbox "8-63 characters (blank = generate one):" 9 60 \
                2>&1 1>&9) || true
        fi
    else
        echo
        read -rp "  Mode [web/kiosk/hotspot/ethernet/terminal] ($MODE): " a || true
        MODE="${a:-$MODE}"
        read -rp "  Radio [detected/uhd/pluto/demo] (detected → $RADIO_LABEL): " a || true
        [[ -n "${a:-}" && "$a" != "detected" ]] && DEVICE="$a"
    fi
    return 0
}

# ── 6. Base system packages ─────────────────────────────────────────────────
install_base() {
    [[ $HAVE_APT -eq 1 && $IS_ROOT -eq 1 ]] || {
        warn "no apt/root — install manually: python3-venv python3-soapysdr avahi-daemon"
        return 0
    }
    say "Installing base packages"
    #  git            pip fetches striqt from a git URL
    #  curl           health probe + vendored asset restore
    #  usbutils       lsusb, the radio detection above
    #  ca-certificates/openssl  TLS + the session-signing secret
    #  avahi-daemon   reach the box at <hostname>.local
    #  whiptail       the two questions
    apt_install ca-certificates curl git openssl sudo usbutils iproute2 \
                python3 python3-venv python3-pip whiptail avahi-daemon \
        || die "base package installation failed"
    ok "base packages installed"
    return 0
}

install_soapy_core() {
    [[ "$RADIO_KIND" != "demo" ]] || return 0
    [[ $HAVE_APT -eq 1 && $IS_ROOT -eq 1 ]] || return 0
    # A Deepwave AIR-T ships SoapySDR and the proprietary SoapyAIRT module in
    # its vendor image, generally outside apt. Installing the distribution's
    # packages on top would leave the machine with two SoapySDR stacks whose
    # bindings can shadow each other — so a working vendor install is left
    # exactly as it is.
    if [[ "$RADIO_KIND" == "airt" ]] && python3 -c 'import SoapySDR' 2>/dev/null; then
        ok "using the AIR-T's own SoapySDR (leaving the vendor stack untouched)"
        return 0
    fi
    say "Installing SoapySDR"
    # soapysdr-tools is SoapySDRUtil (enumeration + diagnostics). Its
    # Recommends pull the whole module-all bundle; --no-install-recommends
    # (in apt_install) keeps that out.
    apt_install python3-soapysdr soapysdr-tools \
        || die "SoapySDR is not available from this distribution's repositories"
    ok "SoapySDR core installed"
    return 0
}

install_radio_driver() {
    [[ $HAVE_APT -eq 1 && $IS_ROOT -eq 1 ]] || return 0
    case "$RADIO_KIND" in
        demo|none|unknown) return 0 ;;
        airt)
            info "AIR-T uses the vendor's SoapyAIRT driver — nothing to install"
            return 0
            ;;
        pluto)
            install_pluto_driver
            return 0
            ;;
    esac
    [[ -n "$RADIO_PKGS" ]] || return 0
    say "Installing the $RADIO_LABEL driver"
    # shellcheck disable=SC2086
    apt_install $RADIO_PKGS || die "could not install: $RADIO_PKGS"
    ok "driver installed: $RADIO_PKGS"
    if [[ "$RADIO_KIND" == "uhd" ]]; then
        install_uhd_images
        tune_usbfs
    fi
    return 0
}

# SoapyPlutoSDR is not packaged before Debian forky. Building it is the only
# way a Pluto works on the releases this installer supports, so build it
# rather than telling the user to go do it themselves.
install_pluto_driver() {
    say "Installing the ADALM-Pluto driver"
    apt_install libiio-utils libiio0 libad9361-0 || true
    if apt_has soapysdr-module-plutosdr && apt_install soapysdr-module-plutosdr; then
        ok "SoapyPlutoSDR installed from apt"
        return 0
    fi
    info "not packaged in this release — building SoapyPlutoSDR from source"
    # libsoapysdr-dev is REQUIRED here even though nothing else in this repo
    # compiles: SoapyPlutoSDR's CMakeLists looks for the SoapySDR headers and
    # its CMake config, and stops with "Soapy SDR development files not
    # found..." without them.
    apt_install build-essential cmake git libsoapysdr-dev libiio-dev libad9361-dev \
        || die "cannot install the toolchain needed to build SoapyPlutoSDR"

    # Install into the prefix SoapySDR itself searches. A module built with
    # CMake's default /usr/local lands in /usr/local/lib/SoapySDR/modules0.8,
    # which the DISTRO SoapySDR (rooted at /usr) never looks in — the build
    # would succeed and the driver would still be invisible.
    local prefix
    prefix="$(SoapySDRUtil --info 2>/dev/null \
              | sed -n 's/^Install root:[[:space:]]*//p' | head -1 || true)"
    [[ -n "$prefix" ]] || prefix="/usr"
    info "installing the module under $prefix (SoapySDR's own search root)"

    local src="/usr/local/src/SoapyPlutoSDR"
    rm -rf "$src"
    git clone --depth 1 https://github.com/pothosware/SoapyPlutoSDR.git "$src" \
        || die "could not fetch SoapyPlutoSDR"
    cmake -S "$src" -B "$src/build" -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_INSTALL_PREFIX="$prefix" >/dev/null \
        && cmake --build "$src/build" -j"$(nproc)" >/dev/null \
        && cmake --install "$src/build" >/dev/null \
        || die "SoapyPlutoSDR build failed (see $LOG_FILE)"
    ldconfig
    # Report where the module landed rather than guessing whether it works:
    # an earlier version grepped SoapySDRUtil --info for "plutosdr" and cried
    # wolf on a build that was in fact fine — the Pluto enumerated correctly
    # moments later. The authoritative check is verify_radio, which actually
    # enumerates and captures.
    local module
    module="$(grep -m1 'SoapySDR/modules' "$src/build/install_manifest.txt" 2>/dev/null || true)"
    if [[ -n "$module" ]]; then
        ok "SoapyPlutoSDR installed: $module"
    else
        ok "SoapyPlutoSDR built and installed under $prefix"
    fi
    return 0
}

# ── 7. USRP enablement ──────────────────────────────────────────────────────
# A B2xx has no on-board flash: UHD uploads firmware and an FPGA image over
# USB at every open. Debian ships no images, and — the part that actually bit
# us — /usr/bin/uhd_images_downloader writes to a VERSIONED directory
# (/usr/share/uhd/4.8.0/images) that the installed libuhd does not search, so
# the download "succeeds" and the radio still reports
# "Using images directory: <no images directory located>". Rather than guess
# the layout, download, then find the firmware on disk and pin UHD_IMAGES_DIR
# to whatever directory actually holds it.
install_uhd_images() {
    say "Fetching UHD firmware/FPGA images"
    if ! locate_uhd_images; then
        local dl
        for dl in /usr/libexec/uhd/utils/uhd_images_downloader.py \
                  /usr/lib/uhd/utils/uhd_images_downloader.py \
                  "$(command -v uhd_images_downloader 2>/dev/null || true)"; do
            [[ -n "$dl" && -x "$dl" ]] || continue
            info "running $dl"
            "$dl" -t b2xx >/dev/null 2>&1 || "$dl" >/dev/null 2>&1 || true
            locate_uhd_images && break
        done
    fi
    if locate_uhd_images; then
        ok "UHD images at $UHD_IMAGES_PATH"
        export UHD_IMAGES_DIR="$UHD_IMAGES_PATH"
    else
        warn "UHD images could not be downloaded (offline?). The USRP will not"
        warn "open until they exist. Retry with:  sudo uhd_images_downloader"
    fi
    return 0
}

locate_uhd_images() {
    local hit
    hit="$(find /usr/share/uhd /usr/local/share/uhd /usr/lib/uhd /lib/uhd \
              -name 'usrp_b2*_fw*.hex' -o -name 'usrp_b200_fw.hex' 2>/dev/null \
           | head -1)"
    if [[ -n "$hit" ]]; then
        UHD_IMAGES_PATH="$(dirname "$hit")"
        return 0
    fi
    return 1
}

# A B2xx streaming over USB 3 overruns the kernel's default 16 MB usbfs
# buffer within seconds. usbcore is built into the Raspberry Pi kernel, so
# /etc/modprobe.d has no effect — this has to be a kernel command-line option.
tune_usbfs() {
    local want=1000 sysfs=/sys/module/usbcore/parameters/usbfs_memory_mb cur=""
    [[ -r "$sysfs" ]] && cur="$(cat "$sysfs" 2>/dev/null || true)"
    if [[ "$cur" =~ ^[0-9]+$ ]] && (( cur >= want )); then
        ok "usbfs buffer already ${cur} MB"
        return 0
    fi
    if [[ -w "$sysfs" ]] && echo "$want" > "$sysfs" 2>/dev/null; then
        info "usbfs buffer raised to ${want} MB for this boot"
    fi
    local f cmdline=""
    for f in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
        [[ -f "$f" ]] && { cmdline="$f"; break; }
    done
    if [[ -z "$cmdline" ]]; then
        warn "add 'usbcore.usbfs_memory_mb=$want' to this host's kernel command"
        warn "line to make the USB buffer increase survive a reboot"
        return 0
    fi
    if grep -q 'usbcore\.usbfs_memory_mb=' "$cmdline"; then
        sed -i "s/usbcore\.usbfs_memory_mb=[0-9]*/usbcore.usbfs_memory_mb=$want/" "$cmdline"
    else
        cp -n "$cmdline" "$cmdline.linda.bak" 2>/dev/null || true
        # cmdline.txt must stay ONE line: append to it, never add a line.
        sed -i "1s|\$| usbcore.usbfs_memory_mb=$want|" "$cmdline"
        REBOOT_REQUIRED=1
    fi
    ok "usbfs buffer persisted in $cmdline"
    return 0
}

# ── 8. USB permissions ──────────────────────────────────────────────────────
install_udev_rules() {
    [[ $IS_ROOT -eq 1 && "$RADIO_KIND" != "demo" ]] || return 0
    say "Granting USB access to $SERVICE_USER"
    getent group plugdev >/dev/null || groupadd --system plugdev
    local g
    for g in plugdev dialout; do
        getent group "$g" >/dev/null && usermod -aG "$g" "$SERVICE_USER" || true
    done
    install -d -m 0755 /etc/udev/rules.d
    cat > /etc/udev/rules.d/70-linda-sdr.rules <<'EOF'
# Managed by LINDA setup.sh — USB SDRs usable without root.
SUBSYSTEM=="usb", ATTR{idVendor}=="2500", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="0456", ATTR{idProduct}=="b673", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="0bda", ATTR{idProduct}=="2832", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="0bda", ATTR{idProduct}=="2838", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="2cf0", MODE="0660", GROUP="plugdev", TAG+="uaccess"
EOF
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=usb 2>/dev/null || true
    ok "udev rules installed"
    return 0
}

# ── 9. GPS (optional; recordings embed the fix, the viewer never needs it) ──
install_gps() {
    [[ $IS_ROOT -eq 1 && $HAVE_APT -eq 1 ]] || return 0
    [[ "$RADIO_KIND" != "demo" ]] || return 0
    say "Installing GPS support (optional)"
    # gpsd only, plus the ~330 kB CLI tools (cgps/gpsmon) for diagnosing a
    # receiver. NOT gpsd-clients: it hard-depends on python3-matplotlib and
    # python3-scipy and drags 229 MB of graphing stack onto the Pi for
    # utilities we never call — core/gps.py talks to gpsd over a plain socket.
    if ! apt_install gpsd gpsd-tools; then
        warn "gpsd unavailable — recordings will record gps_valid=0"
        return 0
    fi
    local device="${RADIO_GPS_DEVICE:-}" candidate
    if [[ -z "$device" ]]; then
        for candidate in /dev/ttyACM* /dev/ttyUSB*; do
            [[ -e "$candidate" ]] || continue
            # Only claim a port that actually speaks NMEA — Arduinos and FTDI
            # cables live on these same device names.
            if timeout 4 grep -qam1 '^\$G[PNLAB]' "$candidate" 2>/dev/null; then
                device="$candidate"; break
            fi
        done
    fi
    if [[ -n "$device" && -e "$device" ]]; then
        if [[ -f /etc/default/gpsd ]]; then
            sed -i "s|^DEVICES=.*|DEVICES=\"$device\"|" /etc/default/gpsd || true
            grep -q '^DEVICES=' /etc/default/gpsd || echo "DEVICES=\"$device\"" >> /etc/default/gpsd
        fi
        systemctl enable gpsd  >/dev/null 2>&1 || true
        systemctl restart gpsd >/dev/null 2>&1 || true
        ok "GPS receiver on $device"
    else
        info "no GPS receiver attached — recordings will set gps_valid=0"
    fi
    return 0
}

# ── 10. Python environment ──────────────────────────────────────────────────
# The venv is ISOLATED (no --system-site-packages). The old installer exposed
# the distro's site-packages so the apt SoapySDR binding was importable, but
# that dragged apt's numpy/scipy/matplotlib in alongside pip's: on this Pi the
# system carried numpy 2.2.4 and apt's scipy was compiled against it, while
# pip installed numpy 2.1.3 into the venv — a NumPy ABI mismatch that only
# detonates deep inside an analysis call. Instead: isolate everything, then
# link in the one module that genuinely has no wheel, SoapySDR.
link_soapysdr() {
    [[ "$RADIO_KIND" != "demo" ]] || return 0
    local src_dir site f
    src_dir="$(python3 -c 'import SoapySDR, os; print(os.path.dirname(SoapySDR.__file__))' 2>/dev/null || true)"
    if [[ -z "$src_dir" ]]; then
        warn "the system SoapySDR binding is not importable; real radios will not work"
        return 0
    fi
    site="$("$VENV_PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
    # Debian ships SoapySDR.py + _SoapySDR*.so side by side; other builds (the
    # AIR-T's pixi environment among them) ship it as a package directory.
    # Link whichever shape is present.
    if [[ -d "$src_dir" && -f "$src_dir/__init__.py" ]]; then
        ln -sfn "$src_dir" "$site/$(basename "$src_dir")"
        for f in "$(dirname "$src_dir")"/_SoapySDR*.so; do
            [[ -e "$f" ]] && ln -sf "$f" "$site/$(basename "$f")"
        done
    else
        for f in "$src_dir"/SoapySDR.py "$src_dir"/_SoapySDR*.so; do
            [[ -e "$f" ]] || continue
            ln -sf "$f" "$site/$(basename "$f")"
        done
    fi
    "$VENV_PY" -c 'import SoapySDR' 2>/dev/null \
        && ok "SoapySDR linked into the venv" \
        || warn "SoapySDR still not importable inside the venv"
    return 0
}

install_python() {
    say "Building the Python environment"
    local marker="$VENV/.linda-env-id" wanted current backup
    # Only the inputs that decide what pip installs belong here. The radio
    # kind deliberately does NOT: it changes the moment a radio is plugged in,
    # and including it threw away a perfectly good venv and re-downloaded the
    # entire scientific stack on the very re-run that fixed the detection.
    wanted="$(
        sha256sum "$REPO_ROOT/live/requirements.txt" "$REPO_ROOT/live/constraints.txt"
        printf '%s\n' "$STRIQT_COMMIT"
        python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'
    )"
    wanted="$(printf '%s' "$wanted" | sha256sum | awk '{print $1}')"
    current="$(cat "$marker" 2>/dev/null || true)"
    if [[ -d "$VENV" && "$current" != "$wanted" ]]; then
        backup="$VENV.backup.$(date +%Y%m%d%H%M%S)"
        info "existing venv does not match this configuration; moved to $backup"
        mv "$VENV" "$backup"
    fi
    [[ -d "$VENV" ]] || python3 -m venv "$VENV"
    "$VENV_PY" -m pip install --upgrade -q pip setuptools wheel \
        || die "could not bootstrap pip in the venv"
    link_soapysdr

    say "Installing striqt 0.7.0 (${STRIQT_COMMIT:0:7}) and the web runtime"
    info "this is the slow step — several minutes on a Pi"
    # One transaction so pip resolves numpy/pandas/xarray/zarr/striqt/FastAPI
    # against each other exactly once.
    "$VENV_PY" -m pip install --upgrade \
        -r "$REPO_ROOT/live/requirements.txt" \
        -c "$REPO_ROOT/live/constraints.txt" \
        "striqt @ git+https://github.com/usnistgov/striqt@${STRIQT_COMMIT}" \
        || die "Python dependency installation failed (transcript: $LOG_FILE)"
    printf '%s\n' "$wanted" > "$marker"
    [[ $IS_ROOT -eq 1 ]] && chown -R "$SERVICE_USER:$SERVICE_USER" "$VENV"
    ok "Python environment ready"
    return 0
}

# Offline plot assets: the repo vendors uPlot so hotspot/ethernet modes never
# reach for a CDN. Restore it if it went missing, and verify what we ship.
install_web_assets() {
    local dir="$REPO_ROOT/live/web/vendor"
    mkdir -p "$dir"
    [[ -s "$dir/uPlot.min.js" ]] || curl -fsSL \
        https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.iife.min.js -o "$dir/uPlot.min.js"
    [[ -s "$dir/uPlot.min.css" ]] || curl -fsSL \
        https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.min.css -o "$dir/uPlot.min.css"
    echo "2d27e8ad3d228164525ce213f9dc716f39b4e3aee0cc773fb3491c96cf4921a2  $dir/uPlot.min.js" \
        | sha256sum -c - >/dev/null || die "uPlot JS asset missing or corrupt"
    echo "df630c6a8d6f8eeaff264b50f73ce5b114f646ffd9a0bb74f049b0a00135fa04  $dir/uPlot.min.css" \
        | sha256sum -c - >/dev/null || die "uPlot CSS asset missing or corrupt"
    return 0
}

# ── 11. Proof that it works ─────────────────────────────────────────────────
verify_software() {
    say "Verifying the installation"
    "$VENV_PY" - <<'PY' || die "the web runtime is not importable"
import fastapi, numpy, uvicorn
print(f"    fastapi {fastapi.__version__}  uvicorn {uvicorn.__version__}  numpy {numpy.__version__}")
PY
    "$VENV_PY" - <<'PY' || die "striqt is not importable — the radio pipeline cannot run"
import striqt
from striqt.sensor import specs
print(f"    striqt {getattr(striqt, '__version__', '?')} + striqt.sensor")
PY
    ( cd "$REPO_ROOT" && "$VENV_PY" -c '
import sys; sys.path.insert(0, "live")
import core
print("    live/core imports clean")
' ) || die "live/core failed to import"
    ok "software verified"
    return 0
}

verify_radio() {
    # Guard on the SELECTOR, not the detected kind: when nothing was found we
    # deliberately configured demo, and reporting that as a radio failure
    # would be wrong.
    [[ "$DEVICE" != "demo" ]] || { RADIO_CHECK_STATUS="demo (no hardware)"; return 0; }
    if [[ $SKIP_RADIO_CHECK -eq 1 ]]; then
        RADIO_CHECK_STATUS="skipped by request"
        warn "radio check skipped"
        return 0
    fi
    say "Talking to the radio"
    # Build the environment as an array: UHD_IMAGES_DIR is set only when images
    # were actually located. Exporting it EMPTY points UHD at nowhere and is
    # worse than leaving it to its own search.
    local -a runner=("$VENV_PY")
    if [[ -n "$UHD_IMAGES_PATH" ]]; then
        runner=(env "UHD_IMAGES_DIR=$UHD_IMAGES_PATH" "$VENV_PY")
    fi
    # stderr is CAPTURED, not discarded. Hiding it once already turned a
    # diagnosable enumeration failure into a guess.
    local out found
    out="$( cd "$REPO_ROOT" && "${runner[@]}" -c '
import sys; sys.path.insert(0, "live")
from core import devices
try:
    rows = devices.discover()
except RuntimeError as e:
    print("ERR", e); raise SystemExit
for r in rows:
    print("HIT", r["device"], r["label"])
' 2>&1 || true )"
    found="$(printf '%s\n' "$out" | grep '^HIT' || true)"
    if [[ -z "$found" ]]; then
        RADIO_CHECK_STATUS="FAILED — no radio enumerated"
        warn "SoapySDR enumerated no radios. It said:"
        printf '%s\n' "$out" | sed 's/^/        /'
        # Cross-check with the driver's own tool. If SoapySDRUtil sees the
        # radio and we do not, the fault is in LINDA; if neither sees it, the
        # radio is absent, unpowered, or still held by another process.
        if command -v SoapySDRUtil >/dev/null 2>&1; then
            warn "SoapySDRUtil --find says:"
            SoapySDRUtil --find 2>&1 | sed 's/^/        /' || true
        fi
        if command -v lsusb >/dev/null 2>&1 && lsusb -d 2500: >/dev/null 2>&1; then
            warn "the USRP IS present on USB, so it is most likely still open in"
            warn "another process. Check with:  sudo fuser -v /dev/bus/usb/*/*"
        fi
        return 0
    fi
    info "enumerated:"
    printf '%s\n' "$found" | sed 's/^HIT /      /'

    # Enumeration only proves the driver loaded. Capture proves the whole
    # path — open, tune, stream, and echo the setting back in a frame header.
    # UHD_IMAGES_DIR is passed explicitly: the qualification runs as the
    # service user, who does not inherit this shell's exports. It is only set
    # when we actually found images — pointing UHD at an empty or bogus path
    # is worse than leaving it to its own search.
    local -a runner=(timeout 240s runuser -u "$SERVICE_USER" --)
    if [[ -n "$UHD_IMAGES_PATH" ]]; then
        runner+=(env "UHD_IMAGES_DIR=$UHD_IMAGES_PATH")
    fi
    if "${runner[@]}" \
            "$VENV_PY" "$REPO_ROOT/live/tools/hardware_qual.py" \
            --device "$DEVICE" --quick --timeout 15; then
        RADIO_CHECK_STATUS="passed (captured and verified)"
        ok "the radio streams and settings apply"
    else
        RADIO_CHECK_STATUS="FAILED — enumerated but did not capture"
        warn "the radio enumerated but the capture test failed (see above)"
    fi
    # The qualification just had the radio open, and a USB SDR does not always
    # release its interface the instant the process exits. Starting the
    # service one second later made a PlutoSDR report
    #   "Unable to claim interface 3:3:5: Device or resource busy (16)"
    # after passing 9/9 points moments earlier — the radio was fine, it was
    # simply not free yet. Give the kernel a moment to tear the handle down.
    wait_for_radio_release
    return 0
}

# Poll until no process holds the radio any more, so install_service does not
# hand a still-busy device to the viewer. Bounded: this is a settle wait, not
# a guarantee, and the service retries on its own besides.
wait_for_radio_release() {
    local i
    for i in $(seq 1 10); do
        if ! pgrep -f 'hardware_qual\.py' >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done
    sleep 3   # kernel-side USB interface teardown after the process is gone
    return 0
}

# ── 12. Service configuration ───────────────────────────────────────────────
genpw() { openssl rand -hex 12 2>/dev/null || head -c24 /dev/urandom | base64 | tr -d '+/='; }

write_env_file() {
    [[ $IS_ROOT -eq 1 ]] || return 0
    say "Writing $ENV_FILE"
    mkdir -p "$ENV_DIR"
    local secret
    if [[ -f "$ENV_FILE" ]] && grep -q RADIO_SESSION_SECRET "$ENV_FILE"; then
        secret="$(grep '^RADIO_SESSION_SECRET=' "$ENV_FILE" | cut -d'"' -f2)"
        info "keeping the existing session-signing secret"
    else
        secret="$(openssl rand -hex 32 2>/dev/null || genpw)"
        CREDS_NOTE="admin · viewer · intern  (username only, no password)"
    fi
    cat > "$ENV_FILE" <<EOF
# Generated by LINDA setup.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ).
# Edit, then: systemctl restart $SERVICE_NAME.  Keep this file mode 0600.
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
    # UHD locates a B2xx FPGA image through this variable. Debian's downloader
    # and Debian's libuhd disagree about the default path, so state it.
    [[ -n "$UHD_IMAGES_PATH" ]] && echo "UHD_IMAGES_DIR=\"$UHD_IMAGES_PATH\"" >> "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok "service configuration written"
    return 0
}

# Re-running with a DIFFERENT mode has to change how the machine behaves, not
# just what the env file says. Both network profiles are created with
# autoconnect=yes, so a radio-hotspot profile left behind after switching to
# web keeps the Pi broadcasting an access point and off your network entirely;
# radio-ethernet keeps the wired port serving DHCP instead of acting as a
# client; and the kiosk autologin survives a switch away from kiosk. Remove
# whatever does not belong to the mode now being installed.
clear_other_mode_state() {
    [[ $IS_ROOT -eq 1 ]] || return 0
    local changed=0 keep="" profile
    case "$MODE" in
        hotspot)  keep="radio-hotspot" ;;
        ethernet) keep="radio-ethernet" ;;
    esac
    if command -v nmcli >/dev/null 2>&1; then
        for profile in radio-hotspot radio-ethernet; do
            [[ "$profile" == "$keep" ]] && continue
            if nmcli -t -f NAME connection show 2>/dev/null | grep -qx "$profile"; then
                nmcli connection delete "$profile" >/dev/null 2>&1 || true
                info "removed the $profile profile left by the previous mode"
                changed=1
            fi
        done
    fi
    local kiosk_conf=/etc/lightdm/lightdm.conf.d/50-radio-kiosk.conf
    if [[ "$MODE" != "kiosk" && -f "$kiosk_conf" ]]; then
        rm -f "$kiosk_conf"
        info "removed the kiosk autologin left by the previous mode"
        changed=1
    fi
    if [[ $changed -eq 1 ]]; then
        REBOOT_REQUIRED=1
    fi
    return 0
}

setup_network() {
    [[ $IS_ROOT -eq 1 ]] || return 0
    case "$MODE" in
      hotspot|ethernet) ;;
      *) return 0 ;;
    esac
    apt_install network-manager || die "$MODE mode needs NetworkManager"
    command -v nmcli >/dev/null 2>&1 || die "nmcli missing after installing NetworkManager"
    systemctl enable --now NetworkManager >/dev/null 2>&1 || true
    if systemctl is-active --quiet dhcpcd 2>/dev/null; then
        info "disabling dhcpcd (it conflicts with NetworkManager)"
        systemctl disable --now dhcpcd || true
        REBOOT_REQUIRED=1
    fi
    if [[ "$MODE" == "hotspot" ]]; then
        local wifi
        wifi="$(nmcli -t -f DEVICE,TYPE device | awk -F: '$2=="wifi"{print $1; exit}')"
        [[ -n "$wifi" ]] || die "no Wi-Fi interface for hotspot mode"
        [[ -n "$HOTSPOT_PASS" ]] || HOTSPOT_PASS="$(genpw)"
        (( ${#HOTSPOT_PASS} >= 8 )) || die "hotspot password must be 8-63 characters"
        say "Configuring the Wi-Fi access point on $wifi"
        nmcli connection delete radio-hotspot >/dev/null 2>&1 || true
        nmcli connection add type wifi ifname "$wifi" con-name radio-hotspot \
            autoconnect yes ssid "$HOTSPOT_SSID" \
            802-11-wireless.mode ap 802-11-wireless.band bg \
            ipv4.method shared wifi-sec.key-mgmt wpa-psk \
            wifi-sec.psk "$HOTSPOT_PASS" >/dev/null \
            || die "NetworkManager rejected the hotspot profile"
        HOTSPOT_NOTE="SSID $HOTSPOT_SSID   password $HOTSPOT_PASS   http://10.42.0.1:$PORT"
        REBOOT_REQUIRED=1
    else
        local eth
        eth="$(nmcli -t -f DEVICE,TYPE device | awk -F: '$2=="ethernet"{print $1; exit}')"
        [[ -n "$eth" ]] || die "no Ethernet interface for shared-ethernet mode"
        say "Configuring shared Ethernet on $eth"
        nmcli connection delete radio-ethernet >/dev/null 2>&1 || true
        nmcli connection add type ethernet ifname "$eth" con-name radio-ethernet \
            autoconnect yes ipv4.method shared >/dev/null \
            || die "NetworkManager rejected the shared-ethernet profile"
        ETHERNET_NOTE="plug a laptop into $eth → http://10.42.0.1:$PORT"
        REBOOT_REQUIRED=1
    fi
    return 0
}

install_kiosk() {
    [[ "$MODE" == "kiosk" && $IS_ROOT -eq 1 ]] || return 0
    say "Configuring kiosk display"
    command -v chromium >/dev/null 2>&1 || command -v chromium-browser >/dev/null 2>&1 \
        || apt_install chromium || apt_install chromium-browser \
        || die "kiosk mode needs Chromium and this distribution has none"
    apt_install xserver-xorg xinit openbox lightdm dbus-x11 \
        || die "kiosk mode needs X, Openbox and LightDM"
    install -d -m 0755 /etc/lightdm/lightdm.conf.d
    cat > /etc/lightdm/lightdm.conf.d/50-radio-kiosk.conf <<EOF
[Seat:*]
autologin-user=$SERVICE_USER
autologin-user-timeout=0
user-session=openbox
EOF
    systemctl set-default graphical.target >/dev/null 2>&1 || true
    systemctl enable lightdm >/dev/null 2>&1 || true
    REBOOT_REQUIRED=1
    ok "kiosk display configured"
    return 0
}

install_service() {
    [[ $IS_ROOT -eq 1 && $HAVE_SYSTEMD -eq 1 ]] || { warn "no systemd — skipping the service"; return 0; }
    if [[ "$MODE" == "terminal" ]]; then
        info "terminal mode — no background service installed"
        return 0
    fi
    say "Installing the $SERVICE_NAME service"
    install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$REPO_ROOT/recordings"
    sed -e "s|@REPO_ROOT@|$REPO_ROOT|g" \
        -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
        -e "s|@SERVICE_UID@|$(id -u "$SERVICE_USER")|g" \
        -e "s|@SERVICE_HOME@|$(getent passwd "$SERVICE_USER" | cut -d: -f6)|g" \
        -e "s|@RADIO_MODE@|$MODE|g" \
        "$REPO_ROOT/deploy/radio-web.service.template" > "$UNIT_FILE"
    chmod +x "$REPO_ROOT/deploy/run_service.sh"
    systemctl daemon-reload
    bash "$REPO_ROOT/live/install_radio_web_sudoers.sh" "$SERVICE_USER" "$SERVICE_NAME" >/dev/null \
        || warn "sudoers rule failed — the Reset Radio button will not work"

    # mDNS. Note we do NOT rename the machine unless asked: silently changing
    # someone's hostname to "radio" is a hostile default.
    if [[ -n "$MDNS_HOST" ]]; then
        hostnamectl set-hostname "$MDNS_HOST" 2>/dev/null || warn "could not set the hostname"
        if grep -q '^127\.0\.1\.1[[:space:]]' /etc/hosts; then
            sed -i "s/^127\\.0\\.1\\.1.*/127.0.1.1 $MDNS_HOST/" /etc/hosts
        else
            printf '127.0.1.1 %s\n' "$MDNS_HOST" >> /etc/hosts
        fi
    else
        MDNS_HOST="$(hostname -s)"
    fi
    systemctl enable --now avahi-daemon >/dev/null 2>&1 || warn "avahi unavailable — .local will not resolve"
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
        ufw allow "$PORT/tcp" >/dev/null 2>&1 && info "ufw: opened $PORT/tcp" || true
    fi
    systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
    systemctl restart "$SERVICE_NAME"
    ok "service installed and started"
    return 0
}

health_check() {
    [[ "$MODE" != "terminal" ]] || return 0
    [[ $IS_ROOT -eq 1 && $HAVE_SYSTEMD -eq 1 ]] || return 0
    say "Waiting for the viewer to answer"
    # 30 s was not enough on a Pi and produced a false failure on a install that
    # was actually fine: the first start imports striqt (numba, llvmlite, scipy,
    # matplotlib) and then a USRP loads its FPGA image over USB, which alone
    # takes ~8 s. Give it two minutes, but report progress so a slow start does
    # not look like a hang.
    local i
    for i in $(seq 1 120); do
        if curl -fsS "http://localhost:$PORT/health" >/dev/null 2>&1; then
            ok "$(curl -fsS "http://localhost:$PORT/health" | head -c 200)"
            return 0
        fi
        if ! systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
            journalctl -u "$SERVICE_NAME" -n 60 --no-pager >&2 || true
            die "the service stopped while starting up (journal above)"
        fi
        (( i % 15 == 0 )) && info "still starting… ${i}s"
        sleep 1
    done
    journalctl -u "$SERVICE_NAME" -n 60 --no-pager >&2 || true
    die "the service did not answer /health within 120 seconds"
}

stop_existing_service() {
    if [[ $HAVE_SYSTEMD -eq 1 && $IS_ROOT -eq 1 ]] \
            && systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        WAS_SERVICE_ACTIVE=1
        systemctl stop "$SERVICE_NAME"
        info "stopped the running $SERVICE_NAME for the duration of setup"
    fi
    release_radio
    return 0
}

# A USRP that another process still has open does not show up in SoapySDR's
# enumeration AT ALL, so one stale viewer makes a perfectly good radio look
# absent. Kiosk mode is the way to get one: it runs the web server as a CHILD
# process and relaunches it whenever its health probe times out, so a failed
# run can leave a server behind that is still holding the USB device.
LINDA_PROCS='striqt_web_server\.py|striqt_kiosk\.py|striqt_standalone_terminal\.py'
release_radio() {
    [[ $IS_ROOT -eq 1 ]] || return 0
    command -v pgrep >/dev/null 2>&1 || return 0
    local pids
    pids="$(pgrep -f "$LINDA_PROCS" 2>/dev/null | tr '\n' ' ' || true)"
    [[ -n "${pids// /}" ]] || return 0
    warn "other LINDA viewer processes still hold the radio (pids:${pids%% })"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    local i
    for i in $(seq 1 10); do
        pgrep -f "$LINDA_PROCS" >/dev/null 2>&1 || break
        sleep 1
    done
    pids="$(pgrep -f "$LINDA_PROCS" 2>/dev/null | tr '\n' ' ' || true)"
    if [[ -n "${pids// /}" ]]; then
        # shellcheck disable=SC2086
        kill -9 $pids 2>/dev/null || true
        sleep 1
    fi
    ok "radio released by the previous viewer"
    return 0
}

# ── main ────────────────────────────────────────────────────────────────────
if [[ $DEPS_ONLY -eq 1 ]]; then
    RADIO_KIND="demo"
    install_python
    install_web_assets
    verify_software
    SETUP_COMPLETE=1
    say "Dependencies installed."
    echo "    demo:  ./.venv/bin/python live/striqt_web_server.py --demo"
    exit 0
fi

preflight
install_base                 # lsusb must exist before we can detect anything
detect_radio                 # USB vendor IDs: works with no driver installed
install_soapy_core           # SoapySDRUtil must exist for the catch-all below
broaden_driver_search        # anything the USB table did not recognise
ask_questions                # asked last, so the radio it names is the real one
resolve_selector
install_radio_driver
install_udev_rules
install_gps
clear_other_mode_state       # undo the PREVIOUS mode before installing this one
install_kiosk
install_python
install_web_assets
stop_existing_service
verify_software
verify_radio
write_env_file
setup_network
install_service
health_check

SETUP_COMPLETE=1
printf '\n\033[1;32m╔══════════════════════════════════════════════════╗\033[0m\n'
printf '\033[1;32m║  LINDA is installed and running                  ║\033[0m\n'
printf '\033[1;32m╚══════════════════════════════════════════════════╝\033[0m\n'
echo "    open        http://${MDNS_HOST}.local:$PORT     (or this host's IP)"
echo "    sign in as  admin        — username only, no password"
echo "    radio       $DEVICE  ($RADIO_LABEL)"
echo "    radio check $RADIO_CHECK_STATUS"
echo "    mode        $MODE"
[[ -n "${HOTSPOT_NOTE:-}" ]]  && echo "    hotspot     $HOTSPOT_NOTE"
[[ -n "${ETHERNET_NOTE:-}" ]] && echo "    ethernet    $ETHERNET_NOTE"
echo "    logs        journalctl -u $SERVICE_NAME -f"
echo "    transcript  $LOG_FILE"
if [[ "$RADIO_CHECK_STATUS" == FAILED* ]]; then
    printf '\n\033[1;33m    The software is installed, but the radio did not pass its check.\n'
    printf '    The UI will load; live data will not until the radio works.\n'
    printf '    Diagnose with:  SoapySDRUtil --find\033[0m\n'
fi
if [[ "$DEVICE" == "demo" && "$RADIO_KIND" != "demo" ]]; then
    printf '\n\033[1;33m    No radio was detected, so demo mode was configured.\n'
    printf '    Plug the radio in and re-run: sudo bash setup.sh\033[0m\n'
fi
[[ -e /var/run/reboot-required ]] && REBOOT_REQUIRED=1
if [[ $REBOOT_REQUIRED -eq 1 ]]; then
    printf '\n\033[1;33m    A reboot is required (USB buffer / groups / network / display).\n'
    printf '    sudo reboot\033[0m\n'
fi
echo
