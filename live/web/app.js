/**
 * app.js — striqt WebSocket live viewer
 *
 * Connects to /ws, receives binary spectrogram frames, renders one waterfall
 * canvas per channel (the set follows the frame header, P3-4) + an overlaid
 * PSD chart (uPlot), and sends radio control messages back to the server.
 *
 * Wire format (binary WebSocket message, server → browser):
 *   [4-byte LE uint32 : JSON header byte length]
 *   [JSON header bytes]
 *   [block-0 raw bytes]   rows×nfft float32-LE (or uint8 with "scale" header)
 *   [block-1 raw bytes]   … one block per header channel
 *
 * Control message (text JSON, browser → server):
 *   { center, sample_rate, gain, nfft, rows }   (any subset of these keys)
 */

"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let ws          = null;
let paused      = false;
let replaceMode = true;     // Boring Mode (full-window snapshots) vs Cool Mode (fast scroll)
                            // — must match the `selected` option on #mode-sel
let absRF       = true;     // absolute RF freq vs baseband offset
let autoColor   = true;
let showDiff    = false;    // RX1−RX2 difference on PSD
let peakMarker  = true;
let peakHold    = false;
let showMin     = false;
let psdYspan    = null;     // null = auto; number = fixed dB span
let windowMs    = 20;
let analysisMode = "spectrogram";

// ── AHAWI (coherent capture → segmented replay) ─────────────────────────
// ahawiSelected mirrors the local Mode select; ahawiActive follows the frames
// actually arriving (every client — including read-only viewers — replays
// whatever the server is really producing, LV-F2-style honesty).
let ahawiSelected = false;
let ahawiActive   = false;
let ahawiCap      = null;   // current capture {header, a, channels, blocks, …}
let ahawiPending  = null;   // newer capture waiting for the pass to finish
let ahawiSeg      = 0;
let ahawiPlaying  = true;
let ahawiDwell    = 200;    // ms per segment (client-side replay speed)
let ahawiTimer    = null;
let maxFps      = 15;       // client-side render-rate cap (LV-U1a)
let nextRender  = 0;        // absolute render deadline (performance.now ms)
let lastPsdRender = 0;      // standard rolling PSD is intentionally ~5 Hz

// Role-based access. The server sends {"role": "admin"|"viewer"|"interns"} as
// the first WS text frame. null = not yet known (pre-connect); non-admin roles
// are read-only and get an "access denied" popup on any control interaction.
let currentRole = null;
let isAdmin     = false;
// Popup message per read-only role.
const DENY_MESSAGES = {
    viewer:  "access denied 🚫 admin privileges required",
    interns: "fuck you 🖕",
};

// Current frame metadata (updated on each frame)
let curCenter   = 3750e6;   // = DEFAULT_CENTER in core/constants.py
let curFs       = 15.36e6;
let curGain     = null;     // header "gain" (dB) — shown in the applied-config readout
let radioNfft   = 1024;     // requested radio FFT size (from #nfft-sel); NEVER set from frame headers
let curBins     = 1024;     // bins in the current frame's blocks (from header "nfft")
let curRows     = 12;
let curBackend  = "calibrated";
let freqsMHz    = null;     // Float32Array(nfft)
let curF0       = null;     // header freqs_hz_f0 (true axis origin, Hz baseband)
let curStep     = null;     // header freqs_hz_step (true bin spacing, Hz)
let curFftNfft  = 1024;     // header fft_nfft (real FFT size behind the bin count)
let curBinAvg   = 1;        // header bin_avg (frequency-bin averaging factor)
let curHopSize  = null;     // header hop_size (samples of signal per display row, P2a-4)
let lastBackendWarn = null; // dedups the "SSB unavailable" status warning
let levels      = [-90, -10];
// PSD-backend state (P2b-4): server-computed statistic traces
let serverStats = null;     // header psd_stats — statistic behind each block row
let curSpanMs   = null;     // header time_span_ms — true integrated span
let uplotKind   = "std";    // which uPlot layout is built: "std" | "psd:<stats>"

// Active channel list from the frame header (P3-4). null until ensureChannels
// runs; the display index i (RX label, colors) is the position in this list,
// the value is the server-side port number used to key the buffers below.
let channelList = null;

// Per-channel display buffers [rows_displayed × nfft], newest row at index 0.
// Keys are channel numbers; entries are (re)created by ensureChannels.
const wfBuf   = {};
// Peak-hold and min-trace per channel (Float32Array of length nfft)
const holdBuf = {};
const minBuf  = {};
// Last raw PSD data (mean+max per channel) for exports and band monitor
const psdData = {
    mean: {},
    max:  {},
    // PSD backend (P2b-4): server statistic traces
    // { stats: [...], traces: {ch: [Float32Array per stat], …} }
    server: null,
};

// Null every per-channel entry of the given buffer objects (mode/analysis
// switches and hold/min clears — replaces the old fixed  buf[0]=buf[1]=null).
function clearChannelBufs(...bufs) {
    for (const b of bufs) {
        for (const k of Object.keys(b)) b[k] = null;
    }
}

function channelsKey(list) {
    return list.join(",");
}

// Device identity (P3-5): the server ships its device label in every frame
// header ("device") and in /config (device.label). The page title, brand
// heading, and subtitle follow it; a cheap key check skips the DOM writes on
// the (typical) unchanged frame.
let curDevice = null;        // raw device label, e.g. "AIR8201B"
let deviceLabelKey = null;   // "<label>|<nchannels>" of the last DOM update

function updateDeviceLabel(label) {
    if (!label) return;
    const n = channelList ? channelList.length : null;
    const key = `${label}|${n}`;
    if (deviceLabelKey === key) return;
    deviceLabelKey = key;
    curDevice = label;
    // The header title is now static ("SDR LIVE Viewer" / NIST) — the device
    // name and channel count live in the Applied Settings band via updateMeta().
    // We still set the browser tab title so the device is identifiable there.
    document.title = `${label} · SDR LIVE Viewer`;
    // Brand sub-line in the header shows the live device + channel count.
    const brandSub = document.getElementById("brand-device");
    if (brandSub) brandSub.textContent = n ? `${label} · ${n}ch` : label;
}

function firstWfBuf() {
    const chans = channelList || [];
    return chans.length ? wfBuf[chans[0]] : null;
}

// FPS counter
let frameCount  = 0;
let lastFpsTime = performance.now();
let renderedFps = 0;

// Band selection (MHz) — draggable region over the PSD
let bandLo = null;
let bandHi = null;
let bandDrag = null;   // null | "lo" | "hi" | "body"

// uPlot instance
let uplot = null;

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------

const logPre = document.getElementById("log-pre");
const MAX_LOG_LINES = 150;

function logMsg(msg, level = "INFO") {
    const ts  = new Date().toTimeString().slice(0, 8);
    const lvl = String(level).toUpperCase();
    // Per-line element so each level can be colored (INFO blue / WARN yellow /
    // ERROR red) — the old single-textContent blob couldn't style individual
    // lines. Format is unchanged: "[HH:MM:SS] LEVEL msg".
    const line = document.createElement("div");
    line.className   = "log-line log-" + lvl.toLowerCase();
    line.textContent = `[${ts}] ${lvl.padEnd(5)} ${msg}`;
    logPre.appendChild(line);
    while (logPre.childElementCount > MAX_LOG_LINES) {
        logPre.removeChild(logPre.firstElementChild);
    }
    logPre.scrollTop = logPre.scrollHeight;
}

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------

const statusEl = document.getElementById("status-text");
const metaEl   = document.getElementById("applied-settings");
const freqMhzEl  = document.getElementById("freq-mhz");
const bandPillEl = document.getElementById("band-pill");
let   metaKey    = null;   // change-key so the applied-config DOM only rebuilds on change

// Best-effort RF band label for the header pill. Ranges are approximate
// (downlink-centric) and only cover common, recognizable allocations; returns
// null when the center frequency isn't in a known band (pill stays hidden).
function bandName(mhz) {
    const B = [
        [88, 108,   "FM broadcast"],
        [174, 216,  "VHF-Hi TV"],
        [470, 698,  "UHF TV"],
        [617, 652,  "n71 \u00b7 600"],
        [728, 757,  "700 MHz"],
        [758, 768,  "n14 \u00b7 FirstNet"],
        [869, 894,  "Band 5 \u00b7 850"],
        [1176, 1177,"GPS L5"],
        [1227, 1228,"GPS L2"],
        [1559, 1610,"GNSS L1"],
        [1805, 1880,"Band 3 \u00b7 1800"],
        [1930, 1995,"Band 2/25 \u00b7 PCS"],
        [2110, 2200,"Band 4/66 \u00b7 AWS"],
        [2300, 2400,"Band 30 \u00b7 WCS"],
        [2400, 2500,"2.4 GHz ISM"],
        [2496, 2690,"Band 41 \u00b7 n41"],
        [3300, 3550,"n77 \u00b7 3.4"],
        [3550, 3700,"n48 \u00b7 CBRS"],
        [3700, 3980,"n77 \u00b7 C-band"],
        [5150, 5895,"5 GHz Wi-Fi"],
    ];
    for (const [lo, hi, name] of B) if (mhz >= lo && mhz <= hi) return name;
    return null;
}

function setStatus(text, cls = "") {
    statusEl.textContent = text;
    statusEl.className   = cls;
}

function updateMeta() {
    if (!curBins || !curFs) return;
    const buf0 = firstWfBuf();
    const depthRows = buf0 ? buf0.length / curBins : curRows;
    const winMs     = (depthRows * rowHopSamples() / curFs * 1e3).toFixed(0);
    const mode      = ahawiActive ? "ahawi" : replaceMode ? "flicker" : "waterfall";
    const scale     = autoColor ? "auto" : "manual";
    const analysis  = curBackend;   // executed backend from the header (honest — LV-F2)
    // FFT label discloses radio size → real FFT size (bins × averaging) for the
    // calibrated/ssb averaged grid; plain radio size for the per-bin quicklook.
    const fftLabel  = curBackend === "quicklook"
        ? `${radioNfft}`
        : `${radioNfft}→${curFftNfft} (${curBins} bins × ${curBinAvg})`;
    // PSD backend: block rows are statistics, not time — label the true
    // integrated span from the header instead of the hop-derived window.
    const winLabel  = (serverStats && curSpanMs != null)
        ? `integration ${curSpanMs.toFixed(0)} ms (${serverStats.map(statLabel).join("/")})`
        : `window ${winMs} ms (${depthRows} rows)`;
    // ── Header: big frequency readout + band pill ─────────────────────────
    const centerMHz = curCenter / 1e6;
    if (freqMhzEl) freqMhzEl.textContent = centerMHz.toFixed(3);
    if (bandPillEl) {
        const bn = bandName(centerMHz);
        bandPillEl.hidden = !bn;
        if (bn) bandPillEl.textContent = bn;
    }

    // ── Applied-config rows (rebuilt only when a value changes) ───────────
    const fftTxt   = curBackend === "quicklook" ? `${radioNfft}` : `${radioNfft}\u2192${curFftNfft}`;
    const freqResHz = curFs / (curFftNfft || curBins);
    const freqResTxt = freqResHz >= 1e3
        ? (freqResHz / 1e3).toFixed(3).replace(/0+$/, "").replace(/\.$/, "") + " kHz"
        : freqResHz.toFixed(1) + " Hz";
    const durTxt = (serverStats && curSpanMs != null)
        ? `${curSpanMs.toFixed(0)} ms int` : `${winMs} ms`;
    const chTxt  = (channelList || []).map((_, i) => `RX${i + 1}`).join("+") || "\u2014";
    const rfTxt  = absRF ? "absolute" : "baseband";
    const gainTxt = (curGain !== null && curGain !== undefined) ? `${curGain} dB` : "\u2014";
    const loEl   = document.getElementById("lo-null");
    const loOn   = !!(loEl && loEl.checked);
    const key = [centerMHz, curFs, gainTxt, fftTxt, freqResTxt, depthRows, durTxt,
                 analysis, mode, scale, levels[0].toFixed(0), levels[1].toFixed(0),
                 rfTxt, chTxt, loOn, renderedFps.toFixed(0)].join("|");
    if (key !== metaKey) {
        metaKey = key;
        const F = (k, v) => `<span><span class="ap-k">${k} </span>${v}</span>`;
        metaEl.className = "";
        metaEl.innerHTML =
            `<div class="ap-row">` +
                F("rate", (curFs / 1e6).toFixed(2) + " MS/s") + F("gain", gainTxt) +
                F("fft", fftTxt) + F("freq-res", freqResTxt) +
            `</div>` +
            `<div class="ap-row">` +
                F("rows", depthRows) + F("duration", durTxt) +
                F("analysis", analysis) + F("mode", mode) +
            `</div>` +
            `<div class="ap-row">` +
                F("scale", `${scale} [${levels[0].toFixed(0)},${levels[1].toFixed(0)}]`) +
                F("RF", rfTxt) + F("ch", chTxt) +
                F("LO-null", loOn ? `<span class="ap-on">on</span>` : "off") +
                F("fps", renderedFps.toFixed(0)) +
            `</div>`;
    }
    renderWfAxis();
}

// ---------------------------------------------------------------------------
// Frequency axis helpers
// ---------------------------------------------------------------------------

function buildFreqsMHz(center, fs, nfft, absoluteRF, f0, step) {
    const f = new Float32Array(nfft);
    if (f0 != null && step != null) {
        // Server-supplied true axis: correct for the calibrated DC-centered bin
        // groups (which drop edge bins) as well as the quicklook per-bin FFT.
        for (let i = 0; i < nfft; i++) {
            const baseHz = f0 + i * step;
            f[i] = absoluteRF ? (center + baseHz) / 1e6 : baseHz / 1e6;
        }
        return f;
    }
    for (let i = 0; i < nfft; i++) {
        // fftshifted fallback (old servers): bin 0 = most-negative, nfft/2 = DC
        const baseHz = ((i - nfft / 2) / nfft) * fs;
        f[i] = absoluteRF ? (center + baseHz) / 1e6 : baseHz / 1e6;
    }
    return f;
}

// Samples of signal one displayed STFT row spans. The server ships the exact
// value in the frame header (hop_size, P2a-4) — correct for any FFT size and
// fractional_overlap. Fallback for old headers: quicklook takes non-overlapping
// full-length FFTs (hop = nfft); calibrated/ssb use the default 13/28 overlap,
// so the hop is nfft·15/28.
function rowHopSamples() {
    if (curHopSize) return curHopSize;
    return Math.max(1, Math.round(radioNfft * (curBackend === "quicklook" ? 1 : 15 / 28)));
}

// Absolute ceiling on client-side display rows — matches the server's
// MAX_ROWS_ABS (P1-5). Protects browser render/memory; the old 300 clamp pinned
// every long duration to the same span and made the Duration control inert.
const CLIENT_MAX_ROWS = 4096;

// Rows the display window spans. windowMs of signal advances by rowHopSamples()
// per STFT row, so rows = windowMs·fs / hop. The cap is a generous safety
// ceiling (not a low clamp), so a longer duration honestly renders more rows —
// the meta/axis ms label reflects the actual rows shown.
function rowsForWindowMs(ms) {
    return Math.max(1, Math.min(Math.round(ms / 1000 * curFs / rowHopSamples()), CLIENT_MAX_ROWS));
}

// Send the time-axis control (P2a-4). Duration stays the single owner (P1-4):
// in replace (Boring) mode the SERVER derives rows hop-aware from a first-class
// capture.duration — so the JSON drives the radio honestly and the ↕ ms label
// (computed from header hop_size) matches exactly. In scroll (Cool) mode the
// client display depth follows windowMs and the server streams fixed 12-row
// frame chunks (an explicit rows control, which reclaims rows ownership).
function sendTimeControl() {
    if (ahawiSelected) {
        // Re-assert AHAWI on reconnect: the segment length rides on duration.
        sendControl({ ahawi: true, capture: { duration: windowMs / 1000 } });
    } else if (replaceMode) {
        sendControl({ capture: { duration: windowMs / 1000 } });
    } else {
        sendControl({ rows: 12 });
    }
}

// SSB is always selectable now (P2b-5): when the current rate is off the SSB
// symbol grid, the SERVER retunes the capture rate to the nearest compatible
// one and reports the change through the settings ack (handleAck shows it) —
// no silent fallback, no disabled option guessing at the server's grid.
function updateSsbOption() {
    const opt = document.querySelector('#analysis-sel option[value="ssb"]');
    if (!opt) return;
    opt.disabled = false;
    opt.title = "May retune the capture sample rate onto the SSB symbol grid (reported in the log)";
}

// ---------------------------------------------------------------------------
// WebSocket connection
// ---------------------------------------------------------------------------

function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
        setStatus("connected", "ok");
        logMsg("WebSocket connected");
        // The initial time window is sent once the ROLE arrives (see
        // applyRole). Sending it here raced the role message, so sendControl's
        // read-only guard could not suppress it and every viewer's log opened
        // with a server "read-only role: control ignored" reply on each
        // connect and reconnect.
    };

    ws.onmessage = (e) => {
        if (typeof e.data === "string") {
            try {
                const msg = JSON.parse(e.data);
                // First text frame carries the role. An "admin-busy" error means
                // this admin login is queued behind the active one (4001 close
                // follows); a plain {role} sets our capability level.
                if (msg.role !== undefined) {
                    if (msg.error === "admin-busy") {
                        setStatus("another admin is connected — waiting for the slot…", "warn");
                        logMsg("Admin slot busy; retrying until it frees", "WARN");
                    } else {
                        applyRole(msg.role, msg.auth_enabled);
                    }
                    return;
                }
                if (msg.recording) { updateRecordingUI(msg.recording); return; }
                if (msg.tx) { updateTxUI(msg.tx); return; }
                if (msg.op) { handleOpEvent(msg.op); return; }
                if (msg.message && msg.message !== "ping") logMsg(msg.message);
                if (msg.ack) {
                    handleAck(msg.ack);
                    // Re-sync forms + radioNfft with what the server actually
                    // runs (it may have rounded or rejected inputs) — P2a-5.
                    scheduleConfigRefresh();
                }
            } catch (_) {}
            return;
        }
        if (!paused) onFrame(e.data);
    };

    ws.onclose = (event) => {
        // Distinct close codes (LV-R3): 1008 = auth failed, 4001 = viewer slot busy.
        if (event && event.code === 1008) {
            setStatus("session expired — redirecting to sign in…", "error");
            logMsg("WebSocket closed: authentication failed (1008)", "ERROR");
            // The signed cookie is missing/expired — send the browser to the
            // login form rather than looping on a doomed reconnect.
            setTimeout(() => { window.location.href = "/login"; }, 800);
            return;   // do NOT reconnect on an auth failure
        }
        if (event && event.code === 4001) {
            setStatus("another admin is connected — retrying…", "warn");
            logMsg("Admin slot busy (4001); retrying in 1.2 s", "WARN");
        } else {
            setStatus("disconnected — reconnecting…", "warn");
            logMsg("WebSocket disconnected; retrying in 1.2 s", "WARN");
        }
        setTimeout(connect, 1200);
    };

    ws.onerror = () => ws.close();
}

function sendControl(ctrl) {
    // Secondary guard: read-only roles must never emit a control frame even if
    // something bypasses the capture-phase interceptor. The server also ignores
    // these, but blocking here avoids a pointless round-trip + denial log.
    if (currentRole && !isAdmin) {
        showAccessDenied();
        return;
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(ctrl));
    }
}

// ---------------------------------------------------------------------------
// Role-based access control
// ---------------------------------------------------------------------------

function applyRole(role, authEnabled = true) {
    currentRole = role;
    isAdmin     = (role === "admin");
    document.body.classList.toggle("role-viewer",  role === "viewer");
    document.body.classList.toggle("role-interns", role === "interns");
    document.body.classList.toggle("role-readonly", !isAdmin);
    // Admin-only affordances (e.g. Reset Radio) are shown via this body class.
    document.body.classList.toggle("is-admin", isAdmin);
    const badge = document.getElementById("role-badge");
    if (badge) {
        badge.textContent = isAdmin ? "ADMIN" : (role + " · read-only");
        badge.className   = isAdmin ? "role-badge admin" : "role-badge readonly";
        badge.hidden      = false;
    }
    // Sign-out / switch-user button. Only meaningful when auth is enabled — in
    // --demo / RADIO_AUTH_DISABLE mode there is nothing to sign out of.
    const signout = document.getElementById("signout-btn");
    if (signout) signout.hidden = !authEnabled;
    logMsg(`Signed in as '${role}'${isAdmin ? " (full control)" : " (read-only)"}`);
    if (isAdmin && typeof connectJournal === "function") connectJournal();
    // Whether the TX button exists at all depends on the role AND on the radio
    // having a TX port — re-ask now that we know which role we are.
    if (typeof refreshTx === "function") refreshTx();
    // Now that the role is known, send the initial time window — admins only,
    // so a read-only client never provokes a "control ignored" reply it could
    // not have avoided.
    if (isAdmin && typeof sendTimeControl === "function") sendTimeControl();
}

let _denyHideTimer = null;
let _internHideTimer = null;
function showInternBlock() {
    const el = document.getElementById("intern-block");
    if (!el) return;
    el.hidden = false;
    // restart the CSS fade
    el.classList.remove("show");
    void el.offsetWidth;
    el.classList.add("show");
    clearTimeout(_internHideTimer);
    _internHideTimer = setTimeout(hideInternBlock, 3000);
}
function hideInternBlock() {
    const el = document.getElementById("intern-block");
    if (!el) return;
    el.classList.remove("show");
    el.hidden = true;
}
function showAccessDenied() {
    // Interns get a full-screen image takeover instead of the text popup.
    if (currentRole === "interns") { showInternBlock(); return; }
    const pop = document.getElementById("access-denied");
    if (!pop) return;
    pop.textContent = DENY_MESSAGES[currentRole] || DENY_MESSAGES.viewer;
    pop.hidden = false;
    // restart the CSS pop animation
    pop.classList.remove("show");
    void pop.offsetWidth;
    pop.classList.add("show");
    clearTimeout(_denyHideTimer);
    _denyHideTimer = setTimeout(hideAccessDenied, 2000);
}
function hideAccessDenied() {
    const pop = document.getElementById("access-denied");
    if (!pop) return;
    pop.classList.remove("show");
    pop.hidden = true;
}

// Capture-phase interceptor: for a known read-only role, any interaction with an
// interactive control anywhere on the page is swallowed and shows the popup —
// the strict "view only, touch nothing" behaviour. Runs in the CAPTURE phase so
// it fires before each control's own listener. While currentRole is null
// (pre-connect, sub-second) nothing is blocked; the server enforces anyway.
const CONTROL_SELECTOR =
    "button, input, select, textarea, label, .freq-chip, .mode-opt, #ctrl-toggle";
// Controls a read-only role (viewer/intern) MAY use: purely cosmetic / layout, or
// local-only display toggles that render client-side and send NOTHING to the
// server (verified: none of these call sendControl). Anything not listed here —
// center/rate/gain/FFT/duration/mode/analysis/LO-null/station tuner/apply/JSON —
// changes the shared radio or other viewers and stays blocked.
const SAFE_SELECTOR =
    ".mode-opt, #ctrl-toggle, #signout-btn, #theme-toggle, " +
    "#peak-chk, #hold-chk, #diff-chk, #min-chk, #clear-hold-btn, #cross-chk, " +
    "#yspan-sel, #pause-btn, #fps-sel, #auto-color, #abs-rf, #csv-btn, #png-btn, " +
    "#ops-refresh, " +
    // Client-only readouts: #metadata-export builds a Blob download in the
    // browser and #preset-select only fills in a description — neither sends
    // anything. They were denied while the equivalent #csv-btn/#png-btn were
    // allowed, which just looked broken.
    "#metadata-export, #preset-select, " +
    // AHAWI replay controls are pure client-side display (verified: none call
    // sendControl) — viewers may scrub the capture the admin enabled.
    "#ahawi-play, #ahawi-prev, #ahawi-next, #ahawi-scrub, #ahawi-dwell, " +
    "#ahawi-golive, .wf-strip";
function installReadOnlyGuard() {
    const block = (ev) => {
        if (!currentRole || isAdmin) return;              // admin or not-yet-known
        // Only real user interaction is gated. A synthetic event dispatched by
        // our own code is not someone trying to touch the radio, and treating
        // it as one produced "access denied" popups nobody asked for.
        if (ev.isTrusted === false) return;
        const t = ev.target;
        if (t && t.closest && t.closest("#access-denied")) return;  // popup itself
        // Allow the whitelisted safe controls through untouched — including a
        // <label> that wraps one (clicking the label text targets the label, not
        // the input inside it).
        if (t && t.closest) {
            if (t.closest(SAFE_SELECTOR)) return;
            const lbl = t.closest("label");
            if (lbl && lbl.querySelector(SAFE_SELECTOR)) return;
        }
        if (!t || !t.closest || !t.closest(CONTROL_SELECTOR)) return;
        ev.preventDefault();
        ev.stopPropagation();
        if (typeof ev.stopImmediatePropagation === "function") ev.stopImmediatePropagation();
        showAccessDenied();
    };
    for (const type of ["pointerdown", "click", "change", "input", "keydown"]) {
        document.addEventListener(type, block, true);   // capture phase
    }
    // Dismiss the popup by clicking it or pressing Escape.
    const pop = document.getElementById("access-denied");
    if (pop) pop.addEventListener("click", hideAccessDenied);
    const internBlock = document.getElementById("intern-block");
    if (internBlock) internBlock.addEventListener("click", hideInternBlock);
    document.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") { hideAccessDenied(); hideInternBlock(); }
    });
}

// Surface the server's structured settings ack (P2a-2): what applied cleanly,
// what was rounded to a legal value ("invalid X → using Y"), what striqt
// rejected (last-good config kept). Rounded/rejected also land in the status
// line so the user sees it without watching the log.
function fmtAckValue(v) {
    if (typeof v === "number" && isFinite(v) && Math.abs(v) >= 1000) {
        return v.toLocaleString("en-US", { maximumFractionDigits: 1 });
    }
    return String(v);
}

function handleAck(ack) {
    const rounded  = ack.rounded  || [];
    const rejected = ack.rejected || [];
    // Any applied change makes the capture AHAWI is currently replaying stale
    // — mark it so the badge says "recapturing…" and the next capture loads
    // immediately instead of waiting behind the queue/pause policy.
    if ((ahawiActive || ahawiSelected) && (ack.applied || []).length) {
        ahawiMarkStale();
    }
    for (const r of rounded) {
        logMsg(`invalid ${r.field}=${fmtAckValue(r.requested)} → using ${fmtAckValue(r.used)} (${r.reason})`, "WARN");
    }
    for (const r of rejected) {
        logMsg(`rejected ${r.field}=${fmtAckValue(r.requested)}: ${r.reason}`, "ERROR");
    }
    if (rejected.length) {
        setStatus(`rejected ${rejected.map((r) => r.field).join(", ")} — kept last-good config`, "error");
    } else if (rounded.length) {
        setStatus(`adjusted ${rounded.map((r) => r.field).join(", ")} to legal values`, "warn");
    }
}

// ---------------------------------------------------------------------------
// Operations tab — the verified-operations pipeline, live + history
// ---------------------------------------------------------------------------
//
// Every radio-affecting action (config change, radio open/rearm, reset) is an
// Operation on the server: requested → validated → applying → readback →
// data-path → verdict. Stage events stream over the WebSocket as {"op": ...}
// and accumulate here; /operations backfills history for late joiners.

const OPS_LIMIT = 50;
const opsEntries = new Map();   // op_id -> {root, stagesEl, stateEl}
const OP_TERMINAL = new Set(["SUCCESS", "VERIFIED", "UNVERIFIED", "MISMATCH", "FAILED", "SUPERSEDED"]);

function opStateClass(state) {
    if (state === "failed" || state === "mismatch") return "op-bad";
    if (state === "unverified") return "op-warn";
    if (state === "running") return "op-running";
    if (state === "superseded") return "op-dim";
    return "op-ok";
}

function ensureOpEntry(opId, kind, summary) {
    let e = opsEntries.get(opId);
    if (e) return e;
    const list = document.getElementById("ops-list");
    if (!list) return null;
    const root = document.createElement("div");
    root.className = "op-entry";
    root.innerHTML =
        `<div class="op-head"><span class="op-id">#${opId}</span>` +
        `<span class="op-kind"></span>` +
        `<span class="op-state op-running">running</span></div>` +
        `<div class="op-summary"></div><div class="op-stages"></div>`;
    root.querySelector(".op-kind").textContent = kind || "";
    root.querySelector(".op-summary").textContent = summary || "";
    list.prepend(root);
    while (list.children.length > OPS_LIMIT) list.removeChild(list.lastChild);
    e = { root, stagesEl: root.querySelector(".op-stages"),
          stateEl: root.querySelector(".op-state") };
    opsEntries.set(opId, e);
    if (opsEntries.size > OPS_LIMIT * 2) {
        for (const [k, v] of opsEntries) {
            if (!v.root.isConnected) opsEntries.delete(k);
        }
    }
    return e;
}

function appendOpStage(e, stage, detail, level) {
    const line = document.createElement("div");
    line.className = `op-stage op-lvl-${level || "info"}`;
    line.textContent = `${stage}${detail ? ": " + detail : ""}`;
    e.stagesEl.appendChild(line);
}

function handleOpEvent(ev) {
    const e = ensureOpEntry(ev.op_id, ev.kind,
                            ev.stage === "requested" ? ev.detail : null);
    if (!e) return;
    if (ev.stage === "requested" && ev.detail) {
        e.root.querySelector(".op-summary").textContent = ev.detail;
    }
    appendOpStage(e, ev.stage, ev.detail, ev.level);
    if (OP_TERMINAL.has(ev.stage)) {
        const state = ev.stage.toLowerCase();
        e.stateEl.textContent = state;
        e.stateEl.className = "op-state " + opStateClass(state);
        const lvl = state === "failed" ? "ERROR"
                  : (state === "mismatch" || state === "unverified") ? "WARN" : null;
        if (lvl) logMsg(`[op #${ev.op_id}] ${ev.stage}: ${ev.detail || ""}`, lvl);
    }
}

function renderOpsFromHistory(ops) {
    const list = document.getElementById("ops-list");
    if (!list) return;
    list.textContent = "";
    opsEntries.clear();
    for (const op of ops) {          // oldest→newest; prepend puts newest on top
        const e = ensureOpEntry(op.id, op.kind, op.summary);
        if (!e) continue;
        for (const st of op.stages || []) appendOpStage(e, st.stage, st.detail, st.level);
        e.stateEl.textContent = op.state;
        e.stateEl.className = "op-state " + opStateClass(op.state);
    }
}

function fmtUptime(sec) {
    if (sec == null) return "—";
    if (sec < 90) return sec.toFixed(0) + " s";
    if (sec < 5400) return (sec / 60).toFixed(0) + " min";
    return (sec / 3600).toFixed(1) + " h";
}

function renderOpsHealth(h) {
    const el = document.getElementById("ops-health");
    if (!el || !h) return;
    const radio = h.radio
        ? `radio ${h.radio.open ? "open" : "CLOSED"} · ring ${(100 * (h.radio.ring_fill || 0)).toFixed(0)}%`
        : "synthetic source (demo)";
    el.innerHTML =
        `<span class="oph ${h.status === "ok" ? "oph-ok" : "oph-warn"}"></span>` +
        `<span class="oph-status"></span> · <span class="oph-dev"></span>` +
        `<br>boot ${String(h.boot_id || "").slice(0, 8)} · up ${fmtUptime(h.uptime_s)}` +
        `<br>${radio}` +
        `<br>last frame ${h.last_frame_age_s != null ? h.last_frame_age_s.toFixed(1) + " s ago" : "—"}`;
    el.querySelector(".oph-status").textContent = h.status;
    el.querySelector(".oph-dev").textContent = h.device ? h.device.label : "";
}

async function refreshOps() {
    try {
        const r = await fetch("/operations", { cache: "no-store" });
        if (r.ok) renderOpsFromHistory((await r.json()).operations || []);
    } catch (_) {}
    try {
        const r = await fetch("/health", { cache: "no-store" });
        if (r.ok) renderOpsHealth(await r.json());
    } catch (_) {}
}

(function initOpsTab() {
    const tab = document.querySelector('.rail-tab[data-tab="ops"]');
    if (tab) tab.addEventListener("click", refreshOps);
    const btn = document.getElementById("ops-refresh");
    if (btn) btn.addEventListener("click", refreshOps);
    setTimeout(refreshOps, 800);   // backfill ops that predate this page load
})();

// ── Supervised recording -------------------------------------------------
let recordingSeeded = false;
let latestInsights = null;

function updateRecordingUI(rec) {
    if (!rec) return;
    const active = ["starting", "recording", "stopping"].includes(rec.state);
    document.body.classList.toggle("recording", active);
    const banner = document.getElementById("recording-banner");
    if (banner) {
        banner.hidden = !active;
        if (active) {
            const count = rec.captures || 0;
            const phase = rec.phase || (count ? "writing captures" : "preparing first capture");
            banner.textContent = `Recording in progress · ${phase} · ${count} completed captures · ${(rec.elapsed_s || 0).toFixed(1)} s — live display resumes automatically`;
        }
    }
    const status = document.getElementById("record-status");
    if (status) status.textContent = `${rec.state || "idle"}${rec.phase ? " · " + rec.phase : ""}${rec.captures !== undefined ? "\n" + rec.captures + " completed capture(s)" : ""}${rec.output ? "\n" + rec.output : ""}${rec.error ? "\n" + rec.error : ""}`;
    const start = document.getElementById("record-start");
    const stop = document.getElementById("record-stop");
    if (start) start.disabled = active;
    if (stop) stop.disabled = !active || rec.state === "stopping";
}

// ---------------------------------------------------------------------------
// Transmit mode
// ---------------------------------------------------------------------------
//
// Two views in one modal: the legal notice, then the controls. The notice is
// server-enforced — POST /tx/acknowledge must succeed before /tx/start will do
// anything — so this is the presentation of a gate, not the gate itself.
//
// TX state arrives on the SAME broadcast every client receives, so the banner
// is honest for everyone: the admin driving it sees what is being transmitted,
// read-only roles see that the instrument is busy.

let txCaps        = null;   // capabilities from /tx
let txAcked       = false;  // has THIS session acknowledged the notice?
let txActive      = false;
let txWaveKind    = "cw";   // waveform the animation should draw
let txWaveRaf     = null;
let txWavePhase   = 0;

function txEl(id) { return document.getElementById(id); }

// The animation draws the SHAPE of the selected waveform — a sine for CW, a
// beat for two-tone, a sweep for chirp, noise for noise. It is deliberately
// not to scale (the note under the canvas says so): a real 2.4 GHz carrier
// cannot be drawn at 60 fps, and pretending otherwise would be a lie on a
// screen whose whole job is honest measurement.
function drawTxWave() {
    const cv = txEl("tx-wave");
    if (!cv || !txActive) { txWaveRaf = null; return; }
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth || 640, h = cv.clientHeight || 96;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
        cv.width = Math.round(w * dpr);
        cv.height = Math.round(h * dpr);
    }
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);

    // Centre line
    g.strokeStyle = "#22262c";
    g.lineWidth = 1;
    g.beginPath(); g.moveTo(0, h / 2); g.lineTo(w, h / 2); g.stroke();

    txWavePhase += 0.06;
    const mid = h / 2, amp = h * 0.36;
    g.strokeStyle = "#e5766e";
    g.lineWidth = 2;
    g.beginPath();
    for (let x = 0; x <= w; x++) {
        const u = x / w;                      // 0..1 across the canvas
        let y;
        if (txWaveKind === "two_tone") {
            // Two closely spaced tones = a visible beat envelope.
            const t = u * 18 + txWavePhase;
            y = 0.5 * Math.sin(t) + 0.5 * Math.sin(t * 1.18);
        } else if (txWaveKind === "chirp") {
            // Frequency rising left→right, retracing each sweep.
            const t = (u + txWavePhase * 0.05) % 1;
            y = Math.sin(2 * Math.PI * (4 * t + 14 * t * t));
        } else if (txWaveKind === "noise") {
            // Deterministic hash so the trace scrolls instead of flickering.
            const s = Math.sin((x + txWavePhase * 40) * 12.9898) * 43758.5453;
            y = ((s - Math.floor(s)) * 2 - 1) * 0.85;
        } else {
            y = Math.sin(u * 22 + txWavePhase);
        }
        const py = mid - y * amp;
        if (x === 0) g.moveTo(x, py); else g.lineTo(x, py);
    }
    g.stroke();
    txWaveRaf = requestAnimationFrame(drawTxWave);
}

function startTxWave() { if (txWaveRaf === null) drawTxWave(); }
function stopTxWave() {
    if (txWaveRaf !== null) { cancelAnimationFrame(txWaveRaf); txWaveRaf = null; }
}

function fmtTxPlan(plan) {
    if (!plan) return "";
    const p = plan.params || {};
    const lines = [
        `${plan.waveform}  ${(plan.frequency_hz / 1e6).toFixed(6)} MHz` +
        (p.offset_hz ? `  (offset ${(p.offset_hz / 1e3).toFixed(1)} kHz)` : ""),
        `gain ${plan.gain_db} dB   amplitude ${p.amplitude}   ` +
        `${(plan.sample_rate_hz / 1e6).toFixed(4)} MS/s   ch${plan.channel}`,
    ];
    if (p.spacing_hz) lines.push(`tone spacing ${(p.spacing_hz / 1e3).toFixed(1)} kHz`);
    if (p.chirp_bandwidth_hz) {
        lines.push(`chirp ${(p.chirp_bandwidth_hz / 1e6).toFixed(3)} MHz over ` +
                   `${(p.chirp_period_s * 1e3).toFixed(2)} ms`);
    }
    // Some radios (the AIR-T among them) cannot hold an RX and a TX stream at
    // once, so transmitting costs the live view. A frozen waterfall must
    // explain itself rather than looking like a crash.
    if (plan.rx_note) lines.push(plan.rx_note);
    // Readback: what the DRIVER says it tuned to, which is the only number
    // worth trusting. Shown whenever it disagrees with the request.
    const a = plan.actual;
    if (a && a.frequency_hz) {
        const off = Math.abs(a.frequency_hz - plan.frequency_hz);
        lines.push(`driver readback ${(a.frequency_hz / 1e6).toFixed(6)} MHz` +
                   (off > 10 ? `  ⚠ requested ${(plan.frequency_hz / 1e6).toFixed(6)}` : " ✓"));
    }
    return lines.join("\n");
}

function updateTxUI(tx) {
    if (!tx) return;
    txCaps = tx.capabilities || txCaps;
    txActive = !!tx.active;
    document.body.classList.toggle("transmitting", txActive);

    // The button only exists on a radio that can actually transmit.
    const openBtn = txEl("tx-open-btn");
    if (openBtn) openBtn.hidden = !(tx.available && isAdmin);

    // Banner for EVERY role.
    const banner = txEl("tx-banner");
    if (banner) {
        banner.hidden = !txActive;
        if (txActive) {
            const plan = tx.plan || {};
            if (isAdmin) {
                banner.classList.remove("tx-standby");
                banner.textContent =
                    (tx.simulated ? "⚡ SIMULATED TRANSMIT · " : "⚡ TRANSMITTING · ") +
                    `${(plan.frequency_hz / 1e6).toFixed(3)} MHz · ${plan.gain_db} dB · ` +
                    `${plan.waveform} · ${(tx.elapsed_s || 0).toFixed(0)} s` +
                    (tx.remaining_s != null ? ` (${tx.remaining_s.toFixed(0)} s left)` : "") +
                    // The radio may have had to give up receiving to transmit;
                    // say so in the banner, not just in the dialog the operator
                    // may have closed.
                    (plan.rx_mode === "rx_released" ? " · LIVE VIEW PAUSED" : "");
            } else {
                // Read-only roles are told to stand by, not what is radiating
                // — they cannot act on it either way, and the instrument being
                // busy is the operative fact.
                banner.classList.add("tx-standby");
                banner.textContent = "VIEWER BUSY — STANDBY";
            }
        }
    }

    if (!isAdmin) return;   // the rest is the admin's control panel

    if (tx.acknowledged) txAcked = true;
    const start = txEl("tx-start"), stop = txEl("tx-stop");
    if (start) start.disabled = txActive || !tx.available;
    if (stop) stop.disabled = !txActive;

    const live = txEl("tx-live");
    if (live) {
        live.hidden = !txActive;
        if (txActive) {
            txWaveKind = (tx.plan && tx.plan.waveform) || "cw";
            const word = txEl("tx-live-word");
            if (word) word.textContent = tx.simulated ? "Transmitting (simulated)"
                                                      : "Transmitting";
            const s = txEl("tx-live-settings");
            if (s) {
                // Duty cycle = samples the DAC actually took vs what a
                // continuous carrier needs. Well under 100% means the output
                // is a gappy burst train, which the sample count alone would
                // happily report as a healthy carrier.
                const plan = tx.plan || {};
                const rate = (plan.actual && plan.actual.sample_rate_hz)
                    || plan.sample_rate_hz || 0;
                const el = tx.elapsed_s || 0;
                const duty = (rate > 0 && el > 0)
                    ? (tx.samples_written || 0) / (rate * el) : null;
                s.textContent = fmtTxPlan(tx.plan) +
                    `\nelapsed ${el.toFixed(1)} s   ` +
                    `${tx.samples_written || 0} samples` +
                    (duty !== null ? `   ${(duty * 100).toFixed(0)}% duty` : "") +
                    (duty !== null && duty < 0.9
                        ? "   ⚠ DAC STARVED — output is not a continuous carrier"
                        : "") +
                    (tx.underflows ? `   ⚠ ${tx.underflows} underflow(s)` : "");
            }
            startTxWave();
        } else {
            stopTxWave();
        }
    }

    const st = txEl("tx-status");
    if (st) {
        st.textContent = tx.available
            ? `${tx.state}${tx.error ? "\n" + tx.error : ""}` +
              (tx.simulated ? "\ndemo device — nothing is radiated" : "")
            : `unavailable — ${tx.reason || "unknown"}`;
    }
}

// Populate the form from server capabilities: waveform list, TX channels, and
// the radio's real frequency/gain limits. Defaults come from the RADIO, never
// from a hardcoded guess — gain starts at the quietest the hardware supports.
function seedTxForm() {
    if (!txCaps) return;
    const sel = txEl("tx-waveform");
    if (sel && !sel.options.length) {
        for (const [k, label] of Object.entries(txCaps.waveforms || {})) {
            const o = document.createElement("option");
            o.value = k; o.textContent = label;
            sel.appendChild(o);
        }
    }
    const chField = txEl("tx-channel-field"), chSel = txEl("tx-channel");
    if (chSel && chField) {
        const n = txCaps.channels || 1;
        chField.hidden = n <= 1;
        if (!chSel.options.length) {
            for (let i = 0; i < n; i++) {
                const o = document.createElement("option");
                o.value = String(i); o.textContent = `TX${i}`;
                chSel.appendChild(o);
            }
        }
    }
    const env = txCaps.envelope || {};
    const gain = txEl("tx-gain");
    if (gain && gain.value === "" && env.gain_min != null) {
        gain.value = env.gain_min;
        gain.min = env.gain_min;
        gain.max = env.gain_max;
    }
    const freq = txEl("tx-frequency");
    if (freq && env.freq_min != null) {
        freq.min = (env.freq_min / 1e6).toFixed(3);
        freq.max = (env.freq_max / 1e6).toFixed(3);
        if (freq.value === "") freq.value = (curCenter / 1e6).toFixed(3);
    }
    const envEl = txEl("tx-envelope");
    if (envEl) {
        const parts = [`${txCaps.device} · ${txCaps.channels} TX channel(s)`];
        if (env.freq_min != null) {
            parts.push(`frequency ${(env.freq_min / 1e6).toFixed(3)}–` +
                       `${(env.freq_max / 1e6).toFixed(3)} MHz`);
        }
        if (env.gain_min != null) parts.push(`gain ${env.gain_min}–${env.gain_max} dB`);
        if (txCaps.simulated) parts.push("SIMULATED — nothing is radiated");
        envEl.textContent = parts.join("\n");
    }
    txSyncWaveformFields();
}

// Only show the parameters the selected waveform actually uses.
function txSyncWaveformFields() {
    const kind = (txEl("tx-waveform") || {}).value || "cw";
    txWaveKind = kind;
    const show = (id, on) => { const e = txEl(id); if (e) e.hidden = !on; };
    show("tx-spacing-field", kind === "two_tone");
    show("tx-chirp-bw-field", kind === "chirp");
    show("tx-chirp-period-field", kind === "chirp");
}

async function refreshTx() {
    try {
        const r = await fetch("/tx", { cache: "no-store" });
        if (!r.ok) return;
        const data = (await r.json()).tx;
        txCaps = data.capabilities || null;
        if (data.acknowledged) txAcked = true;
        renderTxDisclaimer(data.disclaimer);
        updateTxUI(data);
        seedTxForm();
    } catch (_) {}
}

// The legal text is served by the API, not hardcoded here — one copy, and the
// server can never enforce terms the operator was not actually shown.
function renderTxDisclaimer(d) {
    if (!d) return;
    const title = txEl("tx-legal-title");
    if (title) title.textContent = "⚠ " + d.title;
    const body = txEl("tx-legal-body");
    if (body && !body.childElementCount) {
        for (const para of d.body || []) {
            const p = document.createElement("p");
            p.textContent = para;
            body.appendChild(p);
        }
    }
    const accept = txEl("tx-accept"), decline = txEl("tx-decline");
    if (accept && d.accept) accept.textContent = d.accept;
    if (decline && d.decline) decline.textContent = d.decline;
}

function openTxModal() {
    const modal = txEl("tx-modal");
    if (!modal) return;
    // The notice shows on the FIRST open of a session; after that the operator
    // goes straight to the controls.
    txEl("tx-legal").hidden = txAcked;
    txEl("tx-control").hidden = !txAcked;
    modal.hidden = false;
    seedTxForm();
}

function closeTxModal() {
    const modal = txEl("tx-modal");
    if (modal) modal.hidden = true;
}

function txPayload() {
    const num = (id) => {
        const v = (txEl(id) || {}).value;
        return v === "" || v == null ? null : Number(v);
    };
    const kind = (txEl("tx-waveform") || {}).value || "cw";
    const payload = {
        waveform: kind,
        frequency_hz: (num("tx-frequency") || 0) * 1e6,
        offset_hz: (num("tx-offset") || 0) * 1e3,
        gain_db: num("tx-gain"),
        amplitude: num("tx-amplitude"),
        duration_s: num("tx-duration"),          // null ⇒ until Stop
        channel: Number((txEl("tx-channel") || {}).value || 0),
    };
    const rate = num("tx-rate");
    if (rate) payload.sample_rate_hz = rate * 1e6;
    if (kind === "two_tone") payload.spacing_hz = (num("tx-spacing") || 100) * 1e3;
    if (kind === "chirp") {
        payload.chirp_bandwidth_hz = (num("tx-chirp-bw") || 1) * 1e6;
        payload.chirp_period_s = (num("tx-chirp-period") || 10) / 1e3;
    }
    return payload;
}

(function installTxHandlers() {
    const open = txEl("tx-open-btn");
    if (open) open.addEventListener("click", () => { if (isAdmin) openTxModal(); });
    const decline = txEl("tx-decline");
    if (decline) decline.addEventListener("click", closeTxModal);
    const close = txEl("tx-close");
    if (close) close.addEventListener("click", closeTxModal);

    const accept = txEl("tx-accept");
    if (accept) accept.addEventListener("click", async () => {
        try {
            const r = await fetch("/tx/acknowledge", { method: "POST" });
            if (!r.ok) {
                logMsg(`[tx] acknowledge failed (${r.status})`, "ERROR");
                return;
            }
            txAcked = true;
            logMsg("[tx] transmit legal notice acknowledged", "WARN");
            txEl("tx-legal").hidden = true;
            txEl("tx-control").hidden = false;
            seedTxForm();
        } catch (err) {
            logMsg(`[tx] acknowledge failed: ${err.message}`, "ERROR");
        }
    });

    const wf = txEl("tx-waveform");
    if (wf) wf.addEventListener("change", txSyncWaveformFields);

    const start = txEl("tx-start");
    if (start) start.addEventListener("click", async () => {
        if (!isAdmin) return;
        const payload = txPayload();
        if (!payload.frequency_hz) {
            logMsg("[tx] enter a transmit frequency first", "ERROR");
            return;
        }
        const dur = payload.duration_s
            ? `${payload.duration_s} s`
            : "until you press Stop (no automatic cutoff)";
        if (!window.confirm(
            `Transmit ${payload.waveform} at ` +
            `${(payload.frequency_hz / 1e6).toFixed(3)} MHz, ` +
            `${payload.gain_db} dB, for ${dur}?\n\n` +
            "Confirm the antenna or dummy load is connected and you are " +
            "authorized to transmit on this frequency.")) return;
        try {
            const r = await fetch("/tx/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const data = await r.json();
            if (!r.ok) {
                logMsg(`[tx] start refused (${r.status}): ${data.error}`, "ERROR");
                setStatus(`transmit refused — ${data.error}`, "error");
                return;
            }
            logMsg(`[tx] transmitting (op #${data.tx.op_id})`, "WARN");
            updateTxUI(data.tx);
        } catch (err) {
            logMsg(`[tx] start failed: ${err.message}`, "ERROR");
        }
    });

    const stop = txEl("tx-stop");
    if (stop) stop.addEventListener("click", async () => {
        try {
            const r = await fetch("/tx/stop", { method: "POST" });
            const data = await r.json();
            logMsg("[tx] stop requested", "WARN");
            if (data.tx) updateTxUI(data.tx);
        } catch (err) {
            logMsg(`[tx] stop failed: ${err.message}`, "ERROR");
        }
    });

    // Escape closes the dialog — but never stops a transmission. Unkeying is
    // an explicit act, not a side effect of dismissing a window.
    document.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") closeTxModal();
    });
})();

// GPS fix shown in the Record panel: recordings stamp every capture with this
// (gps_valid=0 when there is no fix), so it must be checkable BEFORE a run.
function updateGpsStatus(g) {
    const el = document.getElementById("gps-status");
    if (!el) return;
    if (!g) { el.textContent = "GPS: unknown"; return; }
    if (!g.enabled) { el.textContent = "GPS: disabled — captures record gps_valid=0"; return; }
    if (g.valid) {
        // NaN serializes to null, and isFinite(null) is TRUE in JS (Number(null)
        // is 0) — a 2-D fix would otherwise claim an altitude of "0 m".
        const num = (v) => typeof v === "number" && isFinite(v);
        const alt = num(g.altitude_m) ? `, ${g.altitude_m.toFixed(0)} m` : "";
        const sats = g.satellites_used != null ? ` · ${g.satellites_used} sats` : "";
        const acc = num(g.error_horizontal_m) ? ` · ±${g.error_horizontal_m.toFixed(1)} m` : "";
        el.textContent = `GPS: ${g.mode}-D fix — ${g.latitude.toFixed(5)}, ` +
                         `${g.longitude.toFixed(5)}${alt}${sats}${acc}`;
        el.classList.remove("gps-bad");
        return;
    }
    // Every not-valid case names itself: the operator should know whether to
    // wait for satellites or go fix the daemon.
    let why;
    if (!g.connected) why = g.error ? `gpsd unreachable (${g.error})`
                                    : "connecting to gpsd…";
    else if (g.error) why = g.error;
    else if (g.stale) why = `fix is stale (${g.age_s}s old)`;
    else if (g.mode <= 1) why = "no fix yet — receiver needs sky view";
    else why = "no position";
    el.textContent = `GPS: ${why} — captures will record gps_valid=0`;
    el.classList.add("gps-bad");
}

async function refreshGpsStatus() {
    try {
        const r = await fetch("/gps", {cache: "no-store"});
        if (r.ok) updateGpsStatus((await r.json()).gps);
    } catch (_) { /* transient; the panel keeps its last text */ }
}

async function loadRecordingPanel() {
    const r = await fetch("/record", {cache: "no-store"});
    if (!r.ok) throw new Error(`record status HTTP ${r.status}`);
    const data = await r.json();
    updateRecordingUI(data.recording);
    updateGpsStatus(data.gps);
    if (recordingSeeded) return;
    recordingSeeded = true;
    const d = data.defaults || {};
    const c = data.config || {};
    document.getElementById("record-center").value = (d.center_frequency / 1e6).toFixed(6);
    document.getElementById("record-rate").value = (d.sample_rate / 1e6).toFixed(6);
    document.getElementById("record-gain").value = d.gain;
    document.getElementById("record-directory").value = d.directory || "";
    document.getElementById("record-capture-ms").value = ((d.capture_duration || 0.02) * 1000).toFixed(1);
    const backend = c.backend || "spectrogram";
    document.getElementById("record-summary").textContent =
        `Seeded from live view · ${backend} · spectrogram + PSD + channel power`;
}

document.querySelector('.rail-tab[data-tab="record"]')?.addEventListener("click", () => {
    loadRecordingPanel().catch(e => logMsg(e.message, "ERROR"));
    // A fix can arrive (or drop) while the panel is open — keep it live while
    // the operator is deciding whether to start a run.
    refreshGpsStatus();
    if (!window._gpsTimer) {
        window._gpsTimer = setInterval(() => {
            const panel = document.querySelector('.rail-panel[data-panel="record"]');
            if (panel && panel.classList.contains("active")) refreshGpsStatus();
        }, 5000);
    }
});
document.getElementById("record-start")?.addEventListener("click", async () => {
    const durationText = document.getElementById("record-duration").value.trim();
    const payload = {
        center_frequency: Number(document.getElementById("record-center").value) * 1e6,
        sample_rate: Number(document.getElementById("record-rate").value) * 1e6,
        gain: Number(document.getElementById("record-gain").value),
        duration: durationText === "" ? null : Number(durationText),
        capture_duration: Number(document.getElementById("record-capture-ms").value) / 1000,
        directory: document.getElementById("record-directory").value.trim(),
        include_raw_iq: document.getElementById("record-raw-iq").checked,
        analyses: [
            document.getElementById("record-analysis-spg").checked ? "spectrogram" : null,
            document.getElementById("record-analysis-psd").checked ? "psd" : null,
            document.getElementById("record-analysis-power").checked ? "channel_power" : null,
        ].filter(Boolean),
        yaml: document.getElementById("record-yaml").value
    };
    const r = await fetch("/record", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const data = await r.json();
    if (!r.ok) { logMsg(`Record failed: ${data.error || r.status}`, "ERROR"); return; }
    updateRecordingUI(data.recording);
});
document.getElementById("record-stop")?.addEventListener("click", async () => {
    const r = await fetch("/record/stop", {method: "POST"});
    const data = await r.json();
    if (data.recording) updateRecordingUI(data.recording);
});

async function refreshRecordingCatalog() {
    const r = await fetch("/recordings", {cache: "no-store"});
    if (!r.ok) return;
    const rows = (await r.json()).recordings || [];
    const el = document.getElementById("record-catalog");
    if (!el) return;
    el.textContent = "";
    if (!rows.length) { el.textContent = "No recordings found."; return; }
    rows.forEach(x => {
        const row = document.createElement("div");
        row.textContent = `${x.valid === true ? "✓" : x.valid === false ? "✗" : "…"} ${x.name} · ${(x.bytes / 1048576).toFixed(1)} MiB · ${x.state} `;
        if (x.state === "complete") {
            const a = document.createElement("a");
            a.href = `/recordings/${x.id.split("/").map(encodeURIComponent).join("/")}/download`;
            a.textContent = "download"; row.appendChild(a);
        }
        el.appendChild(row);
    });
}
document.getElementById("record-refresh")?.addEventListener("click", () =>
    refreshRecordingCatalog().catch(e => logMsg(e.message, "ERROR")));

function lastDetector(values, channel, detector) {
    const row = values?.[channel]?.[detector];
    return row?.length ? Number(row[row.length - 1]) : null;
}

function renderInsights(data) {
    latestInsights = data;
    const cal = data.calibration || {};
    const badge = document.getElementById("calibration-badge");
    if (badge) {
        badge.textContent = cal.active ? "CALIBRATED" : cal.available ? "CAL CONFIGURED" : cal.state === "invalid" ? "CAL INVALID" : "UNCALIBRATED";
        badge.title = cal.message || "";
    }
    const csum = document.getElementById("calibration-summary");
    if (csum) csum.textContent = `${cal.state || "unknown"} · ${cal.message || ""}${cal.name ? `\n${cal.name}\nSHA-256 ${cal.sha256 || "unavailable"}` : ""}`;

    const power = data.channel_power;
    const pel = document.getElementById("power-summary");
    if (pel && power) {
        const lines = power.values.map((_, ch) => {
            const rms = lastDetector(power.values, ch, 0);
            const peak = lastDetector(power.values, ch, 1);
            // A missing detector renders as an em-dash, not the string "NaN dB".
            const crest = (peak !== null && rms !== null)
                ? `${(peak - rms).toFixed(2)} dB` : "—";
            return `RX${ch + 1}  RMS ${rms?.toFixed(2) ?? "—"} ${power.units}  ·  peak ${peak?.toFixed(2) ?? "—"}  ·  crest ${crest}`;
        });
        pel.textContent = lines.join("\n") + `\n${power.detector_period_s * 1000} ms native detector bins`;
    }
    const occ = data.occupancy;
    const oel = document.getElementById("occupancy-summary");
    if (oel && occ) {
        oel.textContent = occ.fraction_above_threshold.map((row, ch) =>
            `RX${ch + 1}  RMS ${(100 * row[0]).toFixed(1)}%  ·  peak ${(100 * row[1]).toFixed(1)}%`
        ).join("\n") + `\nFraction of native detector readings ≥ ${occ.threshold} ${occ.power_units}`;
    }
    const cell = data.cell;
    const cel = document.getElementById("cell-summary");
    if (cel && cell) cel.textContent = cell.error ? `Unavailable: ${cell.error}` :
        `${cell.persistent ? "Persistent candidate" : cell.detected ? "Candidate — awaiting persistence" : "No persistent candidate"}\nNID2 ${cell.nid2 ?? "—"} · PSS peak/median ${cell.pss_peak_to_median?.toFixed(2) ?? "—"} · hits ${cell.consecutive_hits || 0}/3\n${cell.physical_cell_id == null ? "PCI pending unambiguous NID1 coordinate metadata" : `PCI ${cell.physical_cell_id}`}`;
}

async function refreshInsights() {
    const r = await fetch("/insights", {cache: "no-store"});
    if (r.ok) renderInsights(await r.json());
}

let analysisPresets = [];
async function loadPresets() {
    const r = await fetch("/presets", {cache: "no-store"});
    if (!r.ok) return;
    analysisPresets = (await r.json()).presets || [];
    const select = document.getElementById("preset-select");
    if (!select) return;
    select.textContent = "";
    analysisPresets.forEach(p => {
        const opt = document.createElement("option"); opt.value = p.id; opt.textContent = p.label; select.appendChild(opt);
    });
    // Call the handler directly instead of dispatching a synthetic "change".
    // The read-only guard listens for change in the capture phase and
    // #preset-select is not whitelisted, so the synthetic event was blocked
    // like a real click: every viewer got an unprovoked "access denied" popup
    // (a full-screen takeover for interns) ~1 s after page load, and the
    // stopImmediatePropagation also meant the description never filled in.
    showPresetDescription(select.value);
}
function showPresetDescription(id) {
    const p = analysisPresets.find(x => x.id === id);
    const el = document.getElementById("preset-description");
    if (el) el.textContent = p?.description || "";
}
document.getElementById("preset-select")?.addEventListener("change", e => {
    showPresetDescription(e.target.value);
});
document.getElementById("preset-apply")?.addEventListener("click", async () => {
    const id = document.getElementById("preset-select").value;
    const r = await fetch(`/presets/${encodeURIComponent(id)}/apply`, {method: "POST"});
    const data = await r.json();
    if (!r.ok) return logMsg(`Preset failed: ${data.error || r.status}`, "ERROR");
    logMsg(`Preset ${id} applied`);
    await loadSchema();
});
document.getElementById("metadata-export")?.addEventListener("click", () => {
    if (!latestInsights) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(latestInsights, null, 2)], {type: "application/json"}));
    a.download = `striqt_metadata_${Date.now()}.json`; a.click(); URL.revokeObjectURL(a.href);
});
document.querySelector('.rail-tab[data-tab="insights"]')?.addEventListener("click", () => {
    refreshInsights(); loadPresets();
});
setInterval(refreshInsights, 2000);
setTimeout(() => { refreshInsights(); loadPresets(); refreshTx(); }, 900);

// ── Service journal tail (admin only): journalctl over /ws/logs ──────────
let journalWs = null;
function connectJournal() {
    const pre = document.getElementById("ops-journal");
    if (!isAdmin || !pre || (journalWs && journalWs.readyState <= WebSocket.OPEN)) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    journalWs = new WebSocket(`${proto}//${location.host}/ws/logs`);
    journalWs.onopen = () => { pre.textContent = ""; };
    journalWs.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.journal === undefined) return;
            pre.textContent += msg.journal + "\n";
            const lines = pre.textContent.split("\n");
            if (lines.length > 400) pre.textContent = lines.slice(-400).join("\n");
            pre.scrollTop = pre.scrollHeight;
        } catch (_) {}
    };
    journalWs.onclose = () => {
        journalWs = null;
        if (isAdmin) setTimeout(connectJournal, 2000);
    };
}

// ---------------------------------------------------------------------------
// Frame parsing
// ---------------------------------------------------------------------------

function onFrame(data) {
    // ── Parse header ──────────────────────────────────────────────────────
    const dv      = new DataView(data);
    const hdrLen  = dv.getUint32(0, /*littleEndian=*/true);
    const hdrText = new TextDecoder().decode(new Uint8Array(data, 4, hdrLen));
    const header  = JSON.parse(hdrText);

    // Absolute-deadline throttle. Comparing only against the previous actual
    // render caused normal ±1–2 ms WebSocket jitter at a 15 FPS cap to skip a
    // frame, then render the next one 133 ms later (the UI reported ~11 FPS
    // while the wire delivered exactly 15). A small jitter allowance preserves
    // every at-cap frame while still enforcing lower 10/5/2/1 FPS selections.
    const nowRender = performance.now();
    const renderInterval = 1000 / maxFps;
    if (!nextRender) nextRender = nowRender;
    if (nowRender < nextRender - 2) return;
    nextRender = nowRender - nextRender > renderInterval
        ? nowRender + renderInterval
        : nextRender + renderInterval;

    const { nfft, rows, channels, center, fs, gain, dtype, scale, backend,
            backend_requested, freqs_hz_f0, freqs_hz_step, fft_nfft, bin_avg,
            hop_size, psd_stats, time_span_ms } = header;
    // (Re)build the per-channel panes/buffers when the header's channel set
    // differs from the current display (P3-4) — a no-op on every other frame.
    ensureChannels(channels);
    updateDeviceLabel(header.device);
    let offset = 4 + hdrLen;

    // ── Parse blocks ──────────────────────────────────────────────────────
    const blocks = {};
    for (const ch of channels) {
        if (dtype === "uint8") {
            const nbytes = rows * nfft;
            const u8     = new Uint8Array(data, offset, nbytes);
            const f32    = new Float32Array(rows * nfft);
            const [vmin, vmax] = scale;
            const rng = vmax - vmin;
            for (let i = 0; i < nbytes; i++) f32[i] = vmin + (u8[i] / 255) * rng;
            blocks[ch] = f32;
            offset    += nbytes;
        } else {
            const nbytes = rows * nfft * 4;
            // slice() copies the bytes out of the message buffer
            blocks[ch] = new Float32Array(data.slice(offset, offset + nbytes));
            offset    += nbytes;
        }
    }

    // ── Update state when tuning changes ──────────────────────────────────
    const stepVal = (freqs_hz_step !== undefined && freqs_hz_step !== null) ? freqs_hz_step : null;
    const f0Val   = (freqs_hz_f0   !== undefined && freqs_hz_f0   !== null) ? freqs_hz_f0   : null;
    const tuningChanged = (
        nfft !== curBins || center !== curCenter || fs !== curFs || stepVal !== curStep
    );
    curBackend = backend || curBackend;
    curFftNfft = (fft_nfft !== undefined && fft_nfft !== null) ? fft_nfft : nfft;
    curBinAvg  = (bin_avg  !== undefined && bin_avg  !== null) ? bin_avg  : 1;
    curHopSize = (hop_size !== undefined && hop_size !== null) ? hop_size : null;
    serverStats = (curBackend === "psd" && psd_stats && psd_stats.length) ? psd_stats : null;
    curSpanMs   = (time_span_ms !== undefined && time_span_ms !== null) ? time_span_ms : null;

    // Honest backend reporting: warn once when the server had to substitute a
    // backend (e.g. SSB is unavailable at this sample rate) — LV-F2.
    if (backend_requested && backend && backend !== backend_requested) {
        const key = `${backend_requested}->${backend}`;
        if (lastBackendWarn !== key) {
            lastBackendWarn = key;
            setStatus(`${backend_requested.toUpperCase()} unavailable at this rate — showing ${backend}`, "warn");
            logMsg(`${backend_requested} unavailable at ${(fs / 1e6).toFixed(2)} MS/s — showing ${backend}`, "WARN");
        }
    } else if (lastBackendWarn !== null) {
        lastBackendWarn = null;
        setStatus("connected", "ok");
    }

    if (tuningChanged) {
        curBins   = nfft;
        curCenter = center;
        curFs     = fs;
        curF0     = f0Val;
        curStep   = stepVal;
        freqsMHz  = buildFreqsMHz(center, fs, nfft, absRF, curF0, curStep);
        updateSsbOption();
        // Clear hold/min on tuning change (freq-axis specific)
        clearChannelBufs(holdBuf, minBuf);
        uplotKind = null;   // force the renderer below to rebuild the right plot
        resetBand(freqsMHz);
    }
    curRows = rows;
    if (gain !== undefined && gain !== null) curGain = gain;

    // ── Render ────────────────────────────────────────────────────────────
    if (header.ahawi) {
        // AHAWI capture: one coherent multi-segment frame. Store it and let
        // the replay engine drive the waterfalls/PSD one segment at a time.
        ahawiIngest(header, blocks, channels);
    } else if (serverStats) {
        if (ahawiActive) ahawiDeactivate();
        // PSD backend (P2b-4): block rows are statistic traces, not time —
        // draw them directly; no waterfall to update.
        renderServerPsd(channels, blocks, rows, nfft);
    } else {
        if (ahawiActive) ahawiDeactivate();
        psdData.server = null;
        const stdKind = "std:" + channelsKey(channelList);
        if (uplotKind !== stdKind) initUplot(freqsMHz);
        for (const ch of channels) {
            updateWaterfall(ch, blocks[ch], rows, nfft, center, fs);
        }
        // A rolling-window PSD scans roughly 1.1 million values for the
        // two-channel 20 ms view. Updating that trace at the 15 FPS waterfall
        // rate needlessly starved canvas rendering; 5 Hz is responsive for a
        // summary trace and leaves every waterfall frame visible.
        if (nowRender - lastPsdRender >= 200) {
            updatePSD(channels, blocks, rows, nfft);
            lastPsdRender = nowRender;
        }
    }
    if (!header.ahawi) updateBandMonitor(channels, blocks, rows, nfft);
    // AHAWI wraps the spectrogram/quicklook analyses only; PSD and SSB are
    // already coherent views. Say so instead of silently ignoring the mode.
    if (ahawiSelected && !header.ahawi
            && (curBackend === "psd" || curBackend === "ssb")) {
        if (ahawiBackendHint !== curBackend) {
            ahawiBackendHint = curBackend;
            setStatus(`AHAWI needs the Spectrogram or Quicklook analysis — the ${curBackend.toUpperCase()} view bypasses it`, "warn");
            logMsg(`AHAWI is on but the ${curBackend} analysis bypasses it — choose Spectrogram or Quicklook`, "WARN");
        }
    } else if (ahawiBackendHint !== null && (header.ahawi || !ahawiSelected)) {
        ahawiBackendHint = null;
    }
    updateMeta();

    // ── FPS counter ───────────────────────────────────────────────────────
    frameCount++;
    const now = performance.now();
    if (now - lastFpsTime >= 1000) {
        renderedFps = frameCount / ((now - lastFpsTime) / 1000);
        frameCount  = 0;
        lastFpsTime = now;
    }
}

// ---------------------------------------------------------------------------
// Waterfall rendering
// ---------------------------------------------------------------------------

// Canvas maps are populated by ensureChannels (P3-4), which clones the
// #wf-pane-tpl template once per header channel.
let wfCanvas    = {};
let wfCtx       = {};
let wfImageData = {};

// Per-channel trace/dot colors. Indices 0/1 are the historical RX1/RX2 colors
// verbatim (so the two-channel AIR-T view is pixel-identical); 2+ cycle
// distinct hues for future multi-channel devices.
const CH_COLORS = [
    { mean: "#4ea3ff", max: "#ff5252", hold: "rgba(255,82,82,0.45)",
      min: "rgba(78,163,255,0.6)",   dot: "#4ea3ff" },
    { mean: "#9ac8ff", max: "#ff9a9a", hold: "rgba(255,154,154,0.45)",
      min: "rgba(154,200,255,0.6)",  dot: "#9ac8ff" },
    { mean: "#ffb74d", max: "#ba68c8", hold: "rgba(186,104,200,0.45)",
      min: "rgba(255,183,77,0.6)",   dot: "#ffb74d" },
    { mean: "#4db6ac", max: "#f06292", hold: "rgba(240,98,146,0.45)",
      min: "rgba(77,182,172,0.6)",   dot: "#4db6ac" },
];
// Light-theme counterparts: same hues, darkened until they hold up on a white
// plot background (#4ea3ff and #ff5252 wash out completely there).
const CH_COLORS_LIGHT = [
    { mean: "#1a63c8", max: "#c92f2f", hold: "rgba(201,47,47,0.45)",
      min: "rgba(26,99,200,0.6)",     dot: "#1a63c8" },
    { mean: "#3f7fd4", max: "#d95f5f", hold: "rgba(217,95,95,0.45)",
      min: "rgba(63,127,212,0.6)",    dot: "#3f7fd4" },
    { mean: "#a06a00", max: "#7b3fa0", hold: "rgba(123,63,160,0.45)",
      min: "rgba(160,106,0,0.6)",     dot: "#a06a00" },
    { mean: "#1c7f76", max: "#c04070", hold: "rgba(192,64,112,0.45)",
      min: "rgba(28,127,118,0.6)",    dot: "#1c7f76" },
];
function isLightTheme() {
    return document.body.classList.contains("light-theme");
}
function chColors(i) {
    const set = isLightTheme() ? CH_COLORS_LIGHT : CH_COLORS;
    return set[i % set.length];
}

// Build (or rebuild) the per-channel display: one waterfall pane per header
// channel, fresh buffers, and a forced uPlot rebuild. No-op when the channel
// set is unchanged — the common case, checked with a cheap string compare.
function ensureChannels(channels) {
    const list = (channels && channels.length) ? Array.from(channels) : [0];
    if (channelList && channelsKey(channelList) === channelsKey(list)) return;
    channelList = list;

    const row = document.getElementById("waterfall-row");
    const tpl = document.getElementById("wf-pane-tpl");
    row.textContent = "";
    wfCanvas = {}; wfCtx = {}; wfImageData = {};
    clearChannelBufs(wfBuf, holdBuf, minBuf, psdData.mean, psdData.max);

    channelList.forEach((ch, i) => {
        const pane = tpl.content.firstElementChild.cloneNode(true);
        pane.id = `wf-pane-${ch}`;
        const dot = pane.querySelector(".dot");
        dot.style.background = chColors(i).dot;
        dot.style.boxShadow  = `0 0 6px ${chColors(i).dot}`;
        pane.querySelector(".wf-title-text").textContent =
            `Spectrogram Port ${ch} — RX${i + 1}`;
        const canvas = pane.querySelector("canvas");
        canvas.id = `wf${ch}`;
        row.appendChild(pane);
        wfCanvas[ch]    = canvas;
        wfCtx[ch]       = canvas.getContext("2d");
        wfImageData[ch] = null;
        wfBuf[ch] = holdBuf[ch] = minBuf[ch] = null;
        psdData.mean[ch] = psdData.max[ch] = null;
    });
    // Column count via a custom property so the max-width:1000px media query
    // (grid-template-columns: 1fr) still wins on small screens.
    row.style.setProperty("--wf-cols", String(channelList.length));

    // The RX1−RX2 diff trace only exists with exactly two channels.
    if (channelList.length !== 2) showDiff = false;
    const diffChk = document.getElementById("diff-chk");
    if (diffChk) {
        const label = diffChk.closest("label");
        if (label) label.style.display = channelList.length === 2 ? "" : "none";
        if (channelList.length !== 2) diffChk.checked = false;
    }

    uplotKind = null;   // series set depends on the channel list — rebuild
}

function computeDisplayDepth(rows, nfft, fs) {
    if (replaceMode || ahawiActive) return rows;
    return rowsForWindowMs(windowMs);
}

function updateWaterfall(ch, block, rows, nfft, center, fs) {
    const depth = computeDisplayDepth(rows, nfft, fs);
    const size  = depth * nfft;
    let reallocated = false;

    // Reallocate if dimensions changed
    if (!wfBuf[ch] || wfBuf[ch].length !== size) {
        wfBuf[ch]           = new Float32Array(size).fill(-150);
        wfImageData[ch]     = new ImageData(nfft, depth);
        wfCanvas[ch].width  = nfft;
        wfCanvas[ch].height = depth;
        reallocated = true;
    }

    const buf  = wfBuf[ch];
    const bLen = block.length;   // rows × nfft samples in the new block

    if (replaceMode || ahawiActive) {
        // Replace entire display buffer with the new frame (AHAWI: one segment)
        buf.fill(-150);
        buf.set(block.subarray(0, Math.min(bLen, size)));
    } else {
        // Scroll mode: shift existing rows down, prepend new rows at [0].
        const newRows = Math.min(bLen / nfft, depth);
        const keep    = (depth - newRows) * nfft;
        if (keep > 0) buf.copyWithin(newRows * nfft, 0, keep);
        // Write the block's rows reversed: the block is oldest-first, but row 0 of a
        // downward-scrolling waterfall must be the newest row — otherwise each frame
        // band is internally time-reversed (zigzag on bursty signals) — LV-R7.
        for (let r = 0; r < newRows; r++) {
            buf.set(block.subarray((newRows - 1 - r) * nfft, (newRows - r) * nfft), r * nfft);
        }
    }

    // ── Auto color levels (5th / 99th percentile of a subsample) ──────────
    // AHAWI pins one scale per CAPTURE (set in renderAhawiSegment): a
    // per-segment recompute would pump the brightness as bursts enter and
    // leave segments, exactly the flicker artifact the mode exists to remove.
    if (autoColor && !ahawiActive) {
        const step = Math.max(1, Math.floor(size / 2000));
        const samp = [];
        for (let i = 0; i < size; i += step) samp.push(buf[i]);
        samp.sort((a, b) => a - b);
        const vmin = samp[Math.floor(samp.length * 0.05)];
        const vmax = samp[Math.floor(samp.length * 0.99)];
        levels = [vmin, vmax - vmin < 5 ? vmin + 5 : vmax];
    }

    // ── Render buffer → ImageData via viridis LUT ─────────────────────────
    // In rolling mode only the incoming rows need color conversion. Shift the
    // existing canvas in its native bitmap, then upload the small dirty strip.
    // The old full-buffer conversion touched >1 million pixels per frame and
    // limited Chromium to ~11 FPS even though the wire delivered exactly 15.
    const imgData  = wfImageData[ch].data;
    const LUT      = window.VIRIDIS_LUT;
    const [vmin, vmax] = levels;
    const rng      = vmax - vmin || 1;

    const fullRender = replaceMode || ahawiActive || reallocated;
    const newRows = fullRender ? depth : Math.min(bLen / nfft, depth);
    const renderSize = newRows * nfft;
    for (let i = 0; i < renderSize; i++) {
        const t  = Math.max(0, Math.min(1, (buf[i] - vmin) / rng));
        const li = Math.round(t * 255) * 4;
        imgData[i * 4]     = LUT[li];
        imgData[i * 4 + 1] = LUT[li + 1];
        imgData[i * 4 + 2] = LUT[li + 2];
        imgData[i * 4 + 3] = 255;
    }
    if (fullRender) {
        wfCtx[ch].putImageData(wfImageData[ch], 0, 0);
    } else if (newRows > 0) {
        const keepRows = depth - newRows;
        if (keepRows > 0) {
            wfCtx[ch].drawImage(
                wfCanvas[ch], 0, 0, nfft, keepRows,
                0, newRows, nfft, keepRows);
        }
        wfCtx[ch].putImageData(wfImageData[ch], 0, 0, 0, 0, nfft, newRows);
    }
}

// ---------------------------------------------------------------------------
// AHAWI replay engine (coherent capture → client-side segmented replay)
// ---------------------------------------------------------------------------
// The server analyzes one contiguous multi-segment capture with striqt and
// ships it as a single frame with header.ahawi geometry. This engine slices it
// into viewing windows and flips through them: play/pause/step/scrub run
// entirely client-side, so read-only roles can use them too. The color scale
// and the power strip are pinned per CAPTURE, never per segment.

let ahawiUserModeChangeAt = 0;   // suppress select flip-flop right after a local change
let ahawiBackendHint      = null;
let ahawiStaleAt          = 0;   // >0: settings changed; replayed capture is stale
let ahawiStaged           = false;   // capture/align/duration edits not yet applied

// Settings changed on the server: whatever AHAWI is replaying no longer
// reflects them. Say so, and let the NEXT capture jump the queue/pause hold.
function ahawiMarkStale() {
    ahawiStaleAt = performance.now();
    updateAhawiBadge();
}

// Capture/align/duration edits are STAGED in AHAWI mode and shipped together
// by the Apply button — so one deliberate action starts the recapture, instead
// of three selects racing three separate captures.
function ahawiSetStaged(on) {
    ahawiStaged = on;
    const btn = document.getElementById("ahawi-apply");
    if (btn) {
        btn.classList.toggle("staged", on);
        btn.textContent = on ? "Apply •" : "Apply";
    }
}

function ahawiSendSettings() {
    const capSel  = document.getElementById("ahawi-capture-sel");
    const alignCk = document.getElementById("ahawi-align-chk");
    sendControl({
        ahawi: true,
        ahawi_capture_ms: capSel ? parseFloat(capSel.value) : 100,
        ahawi_align: alignCk ? alignCk.checked : true,
        capture: { duration: windowMs / 1000 },
    });
    ahawiSetStaged(false);
}

function ahawiEls() {
    return {
        bar:    document.getElementById("ahawi-bar"),
        play:   document.getElementById("ahawi-play"),
        prev:   document.getElementById("ahawi-prev"),
        next:   document.getElementById("ahawi-next"),
        scrub:  document.getElementById("ahawi-scrub"),
        dwell:  document.getElementById("ahawi-dwell"),
        badge:  document.getElementById("ahawi-badge"),
        golive: document.getElementById("ahawi-golive"),
    };
}

// Per-row channel power in dB across the whole capture (linear mean over
// bins — a 30 dB burst must dominate, not average away in dB space).
function ahawiRowPowerDb(block, rows, bins) {
    const out = new Float32Array(rows);
    for (let r = 0; r < rows; r++) {
        let acc = 0;
        const base = r * bins;
        for (let b = 0; b < bins; b++) acc += Math.pow(10, block[base + b] / 10);
        out[r] = 10 * Math.log10(acc / bins + 1e-30);
    }
    return out;
}

// Capture-wide auto-color levels (5th/99th percentile of a subsample) —
// computed ONCE per capture so replay brightness cannot pump.
function ahawiCaptureLevels(blocks, channels) {
    const samp = [];
    for (const ch of channels) {
        const b = blocks[ch];
        const step = Math.max(1, Math.floor(b.length / 4000));
        for (let i = 0; i < b.length; i += step) samp.push(b[i]);
    }
    samp.sort((x, y) => x - y);
    const vmin = samp[Math.floor(samp.length * 0.05)];
    const vmax = samp[Math.floor(samp.length * 0.99)];
    return [vmin, vmax - vmin < 5 ? vmin + 5 : vmax];
}

function ahawiIngest(header, blocks, channels) {
    ahawiActivate();
    const chans = Array.from(channels);
    const cap = {
        header, blocks,
        a:        header.ahawi,
        channels: chans,
        rows:     header.rows,
        bins:     header.nfft,
        center:   header.center,
        fs:       header.fs,
        levels:   ahawiCaptureLevels(blocks, chans),
        strip:    {},
        psd:      null,
    };
    // Power strip data: striqt's channel_power_time_series when the server
    // bundled it (a real measurement over the displayed span), else the
    // client-side per-row derivation as fallback.
    const pw = cap.a.power;
    chans.forEach((ch, i) => {
        let data = null;
        if (pw && pw.series && pw.series[i]) {
            const di = Math.max(0, (pw.detectors || []).indexOf("rms"));
            const src = pw.series[i][di] || pw.series[i][0];
            if (src && src.length) data = Float32Array.from(src);
        }
        const striqtPower = !!data;
        if (!data) data = ahawiRowPowerDb(blocks[ch], cap.rows, cap.bins);
        let lo = Infinity, hi = -Infinity;
        for (let k = 0; k < data.length; k++) {
            if (data[k] < lo) lo = data[k];
            if (data[k] > hi) hi = data[k];
        }
        cap.strip[ch] = { data, lo, hi: hi - lo < 3 ? lo + 3 : hi,
                          striqt: striqtPower };
    });
    // Capture-wide striqt PSD statistics — rendered in the PSD pane when the
    // bundle ran on the same frequency grid as the spectrogram (float
    // precision, unaffected by the uint8 wire quantization).
    const ps = cap.a.psd;
    if (ps && ps.traces && ps.stats && ps.stats.length
            && ps.bins === cap.bins
            && (ps.f0 == null || header.freqs_hz_f0 == null
                || Math.abs(ps.f0 - header.freqs_hz_f0)
                   < Math.abs(header.freqs_hz_step || 1) * 0.5)) {
        const stats = ps.stats.map(String);
        const psdBlocks = {};
        chans.forEach((ch, i) => {
            const flat = new Float32Array(stats.length * ps.bins).fill(NaN);
            (ps.traces[i] || []).forEach((tr, s) => {
                if (tr && tr.length) flat.set(tr.slice(0, ps.bins), s * ps.bins);
            });
            psdBlocks[ch] = flat;
        });
        cap.psd = { stats, blocks: psdBlocks };
    }
    // A capture computed after a settings change clears the stale flag and
    // loads IMMEDIATELY — the user just changed something and wants to see it,
    // not wait behind the queue/pause policy. The 300 ms guard skips at most
    // one already-in-flight capture from before the change.
    if (ahawiStaleAt && performance.now() - ahawiStaleAt > 300) {
        ahawiStaleAt = 0;
        ahawiLoadCapture(cap);
        return;
    }
    if (!ahawiCap) {
        ahawiLoadCapture(cap);
    } else {
        // Never yank the view mid-pass: queue and swap at the wrap (playing)
        // or on explicit "go live" (paused/scrubbing).
        ahawiPending = cap;
        updateAhawiBadge();
    }
}

function ahawiLoadCapture(cap) {
    ahawiCap     = cap;
    ahawiPending = null;
    ahawiSeg     = 0;
    const els = ahawiEls();
    if (els.scrub) {
        els.scrub.max   = String(cap.a.segments - 1);
        els.scrub.value = "0";
    }
    if (els.golive) els.golive.hidden = true;
    renderAhawiSegment();
    restartAhawiTimer();
}

function renderAhawiSegment() {
    if (!ahawiCap) return;
    const { blocks, bins, a, channels, center, fs } = ahawiCap;
    const rps = a.rows_per_segment;
    if (autoColor) levels = ahawiCap.levels;   // pinned per capture
    const segBlocks = {};
    for (const ch of channels) {
        segBlocks[ch] = blocks[ch].subarray(ahawiSeg * rps * bins,
                                            (ahawiSeg + 1) * rps * bins);
    }
    for (const ch of channels) {
        updateWaterfall(ch, segBlocks[ch], rps, bins, center, fs);
    }
    if (ahawiCap.psd) {
        // striqt capture-wide PSD statistics from the bundle — same renderer
        // as the PSD backend. serverStats is frame-derived global state;
        // set-and-restore keeps updateMeta's waterfall labels untouched.
        serverStats = ahawiCap.psd.stats;
        renderServerPsd(channels, ahawiCap.psd.blocks,
                        ahawiCap.psd.stats.length, bins);
        serverStats = null;
    } else {
        psdData.server = null;
        const stdKind = "std:" + channelsKey(channelList);
        if (uplotKind !== stdKind) initUplot(freqsMHz);
        updatePSD(channels, segBlocks, rps, bins);
    }
    updateBandMonitor(channels, segBlocks, rps, bins);
    const els = ahawiEls();
    if (els.scrub) els.scrub.value = String(ahawiSeg);
    drawAhawiStrips();
    updateAhawiBadge();
}

function drawAhawiStrips() {
    if (!ahawiCap) return;
    ahawiCap.channels.forEach((ch, i) => drawAhawiStrip(ch, i));
}

function drawAhawiStrip(ch, colorIdx) {
    const canvas = document.querySelector(`#wf-pane-${ch} .wf-strip`);
    if (!canvas || !ahawiCap) return;
    const W = canvas.clientWidth || 600;
    const H = canvas.clientHeight || 46;
    if (canvas.width !== W)  canvas.width  = W;
    if (canvas.height !== H) canvas.height = H;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, W, H);

    const { a } = ahawiCap;
    const strip = ahawiCap.strip[ch];
    if (!strip) return;
    const power = strip.data;
    const { lo, hi } = strip;
    const rows = power.length;   // rows OR striqt detector points — same map
    const segs = a.segments;
    const rps  = rows / segs;

    // Current-segment highlight + boundary ticks.
    const segW = W * rps / rows;
    ctx.fillStyle = "rgba(120,160,255,0.20)";
    ctx.fillRect(ahawiSeg * segW, 0, segW, H);
    ctx.strokeStyle = "rgba(128,128,128,0.30)";
    ctx.lineWidth = 1;
    for (let s = 1; s < segs; s++) {
        const x = Math.round(s * segW) + 0.5;
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
    }
    // Power trace, max-aggregated per pixel column so a 2 ms burst can't
    // vanish between pixels at narrow widths.
    ctx.strokeStyle = chColors(colorIdx).mean;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    const rng = hi - lo || 1;
    for (let x = 0; x < W; x++) {
        const r0 = Math.floor(x / W * rows);
        const r1 = Math.max(r0 + 1, Math.floor((x + 1) / W * rows));
        let v = -Infinity;
        for (let r = r0; r < r1 && r < rows; r++) if (power[r] > v) v = power[r];
        const y = H - 3 - (H - 6) * Math.max(0, Math.min(1, (v - lo) / rng));
        if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
}

function updateAhawiBadge() {
    const els = ahawiEls();
    if (!els.badge) return;
    if (!ahawiCap) {
        els.badge.textContent = "waiting for the first capture…";
        els.badge.title = "";
        return;
    }
    const a = ahawiCap.a;
    const t0 = (ahawiSeg * a.segment_ms).toFixed(0);
    const t1 = ((ahawiSeg + 1) * a.segment_ms).toFixed(0);
    // Unaligned is a finding, not a failure — explain WHICH finding. Off =
    // the toggle; single segment = nothing to fold against; otherwise
    // alignment looked and found nothing periodic (contrast shown so the
    // verdict is checkable, not a mystery).
    let alignTxt;
    if (a.aligned) {
        alignTxt = "burst-aligned";
    } else if (a.align_requested === false) {
        alignTxt = "align off";
    } else if (a.segments < 2) {
        alignTxt = "align n/a — single segment";
    } else {
        alignTxt = `no periodic burst found (${a.align_contrast_db ?? "?"} dB)`;
    }
    const meas = (a.measurements || []).join("+") || "spectrogram";
    const gpu  = a.compute_backend === "cupy" ? " · GPU" : "";
    let text = `seg ${ahawiSeg + 1}/${a.segments} · +${t0}–${t1} ms · ` +
               `${alignTxt} · ${meas}${gpu} · compute ${a.compute_ms} ms`;
    // Two boxes so the bar's width never follows the text: `detail` is the
    // elastic one and ellipsises, `flags` never shrinks. The tooltip carries
    // whatever the ellipsis ate.
    els.badge.innerHTML = "";
    const detail = document.createElement("span");
    detail.className = "ahawi-badge-text";
    detail.textContent = text;
    const flags = document.createElement("span");
    flags.className = "ahawi-badge-flags";
    els.badge.append(detail, flags);
    const addFlag = (txt, warn) => {
        const el = document.createElement("span");
        if (warn) el.className = "warn";
        el.textContent = txt;
        flags.appendChild(el);
        text += txt;
    };
    if (ahawiStaleAt)         addFlag(" · settings changed — recapturing…", true);
    if (a.coherent === false) addFlag(" · ⚠ possible gap in this capture", true);
    if (ahawiPending)         addFlag(ahawiPlaying ? " · new capture queued"
                                                   : " · newer capture waiting", false);
    els.badge.title = text;
    if (els.golive) els.golive.hidden = !(ahawiPending && !ahawiPlaying);
}

function ahawiTick() {
    if (!ahawiActive || !ahawiCap || !ahawiPlaying || paused || document.hidden) return;
    if (ahawiSeg + 1 >= ahawiCap.a.segments) {
        if (ahawiPending) { ahawiLoadCapture(ahawiPending); return; }
        ahawiSeg = 0;
    } else {
        ahawiSeg += 1;
    }
    renderAhawiSegment();
}

function restartAhawiTimer() {
    if (ahawiTimer) clearInterval(ahawiTimer);
    ahawiTimer = setInterval(ahawiTick, ahawiDwell);
}

function ahawiSetPlaying(on) {
    ahawiPlaying = on;
    const els = ahawiEls();
    if (els.play) {
        els.play.textContent = on ? "⏸" : "▶";
        els.play.title = on ? "Pause the segment replay" : "Resume the segment replay";
    }
    updateAhawiBadge();
}

function ahawiStep(delta) {
    if (!ahawiCap) return;
    ahawiSetPlaying(false);
    const segs = ahawiCap.a.segments;
    ahawiSeg = ((ahawiSeg + delta) % segs + segs) % segs;
    renderAhawiSegment();
}

function ahawiActivate() {
    if (!ahawiActive) {
        ahawiActive = true;
        document.body.classList.add("mode-ahawi");
        restartAhawiTimer();
        logMsg("AHAWI replay active — coherent captures, client-side scrubbing");
    }
    // Keep the Mode select honest for every client (viewers can't change it
    // themselves), but never fight a mode change the user made seconds ago —
    // one stale capture frame can arrive while the server switches over.
    const sel = document.getElementById("mode-sel");
    if (sel && sel.value !== "ahawi"
            && performance.now() - ahawiUserModeChangeAt > 3000) {
        sel.value = "ahawi";
        ahawiSelected = true;
    }
}

function ahawiDeactivate() {
    if (!ahawiActive) return;
    ahawiActive  = false;
    ahawiCap     = null;
    ahawiPending = null;
    ahawiSeg     = 0;
    ahawiStaleAt = 0;
    if (ahawiTimer) { clearInterval(ahawiTimer); ahawiTimer = null; }
    document.body.classList.remove("mode-ahawi");
    clearChannelBufs(wfBuf);
    const golive = document.getElementById("ahawi-golive");
    if (golive) golive.hidden = true;
    // Keep the Mode select honest — EXCEPT when this deactivation is the
    // PSD/SSB analysis bypassing a still-selected AHAWI (server cfg.ahawi
    // stays on; the hint banner explains). Flipping the select there would
    // contradict the user's standing choice and fight the resurrect on
    // switching the analysis back.
    const bypass = ahawiSelected && (curBackend === "psd" || curBackend === "ssb");
    const sel = document.getElementById("mode-sel");
    if (sel && sel.value === "ahawi" && !bypass
            && performance.now() - ahawiUserModeChangeAt > 3000) {
        sel.value = replaceMode ? "replace" : "scroll";
        ahawiSelected = false;
    }
    updateAhawiBadge();
}

function wireAhawiControls() {
    const els = ahawiEls();
    if (els.play) els.play.addEventListener("click", () => {
        ahawiSetPlaying(!ahawiPlaying);
        if (ahawiPlaying) restartAhawiTimer();
    });
    if (els.prev) els.prev.addEventListener("click", () => ahawiStep(-1));
    if (els.next) els.next.addEventListener("click", () => ahawiStep(1));
    if (els.scrub) els.scrub.addEventListener("input", (e) => {
        if (!ahawiCap) return;
        ahawiSetPlaying(false);
        ahawiSeg = Math.max(0, Math.min(ahawiCap.a.segments - 1,
                                        parseInt(e.target.value, 10) || 0));
        renderAhawiSegment();
    });
    if (els.dwell) els.dwell.addEventListener("change", (e) => {
        ahawiDwell = parseInt(e.target.value, 10) || 200;
        restartAhawiTimer();
    });
    if (els.golive) els.golive.addEventListener("click", () => {
        if (ahawiPending) ahawiLoadCapture(ahawiPending);
        ahawiSetPlaying(true);
    });
    // Strip click → jump to that segment (delegated: panes are re-cloned on
    // channel-set changes and would lose direct listeners).
    document.addEventListener("click", (ev) => {
        const strip = ev.target && ev.target.closest && ev.target.closest(".wf-strip");
        if (!strip || !ahawiCap) return;
        const rect = strip.getBoundingClientRect();
        const frac = (ev.clientX - rect.left) / Math.max(1, rect.width);
        ahawiSetPlaying(false);
        ahawiSeg = Math.max(0, Math.min(ahawiCap.a.segments - 1,
                                        Math.floor(frac * ahawiCap.a.segments)));
        renderAhawiSegment();
    });
}

// Populate the waterfall frequency-axis overlays (LV-F7). Five evenly spaced
// ticks from the true axis (LV-F1) plus the current hop-aware window on the right.
function renderWfAxis() {
    if (!freqsMHz || !freqsMHz.length) return;
    const divs = document.querySelectorAll(".wf-freq-axis");
    if (!divs.length) return;
    const n = freqsMHz.length;
    let spans = "";
    for (let k = 0; k < 5; k++) {
        const i = Math.round((k / 4) * (n - 1));
        const v = freqsMHz[i];
        // Baseband (Absolute RF off) is labeled as a signed offset from the
        // tuned center so it can never be mistaken for a mistune.
        spans += absRF
            ? `<span>${v.toFixed(1)} MHz</span>`
            : `<span>Δ${v >= 0 ? "+" : ""}${v.toFixed(1)} MHz</span>`;
    }
    const buf0 = firstWfBuf();
    const depthRows = buf0 ? buf0.length / curBins : curRows;
    const winMs = (depthRows * rowHopSamples() / curFs * 1e3).toFixed(0);
    spans += `<span class="wf-axis-win">↕ ${winMs} ms</span>`;
    divs.forEach((d) => { d.innerHTML = spans; });
}

// ---------------------------------------------------------------------------
// PSD (uPlot)
// ---------------------------------------------------------------------------

// Plot chrome is theme-dependent, so it can no longer be constant: read the
// tokens style.css owns (--plot-bg / --plot-grid / --text-dim) at build time.
// Dark values are the historical #000000 / #8b97a8 / #22262c.
function cssToken(name, fallback) {
    const v = getComputedStyle(document.body).getPropertyValue(name).trim();
    return v || fallback;
}
function psdBg()   { return cssToken("--plot-bg",   "#000000"); }
function psdFg()   { return cssToken("--text-dim",  "#8b97a8"); }
function psdGrid() { return cssToken("--plot-grid", "#22262c"); }

// Per-channel PSD trace colors live in CH_COLORS (P3-4); only the two-channel
// difference trace keeps a dedicated color (near-white on black, near-black on
// white).
function diffCol() { return isLightTheme() ? "#2a323d" : "#e6e9ef"; }

// PSD y-axis label depends on the backend: calibrated/ssb values are band-
// integrated over one averaged bin (~+8.5 dB vs per-bin); quicklook is per-bin.
function psdYLabel() {
    return curBackend === "quicklook"
        ? "Power (dB rel. FS / bin)"
        : "Integrated power (dB rel. FS)";
}

// Height of the #psd-legend band plus its 4px inset — reserved out of the plot
// so the canvas, its x ticks, and the key row all fit inside #psd-container.
// Replaces measuring uPlot's own legend, which is now off.
const PSD_LEGEND_H = 16;

// uPlot reserves `size + labelSize` per axis and defaults to 50 + 30 = 80 px.
// Fresh objects per call — uPlot mutates the axis descriptors it is handed.
function psdAxis(opts) {
    return Object.assign({
        gap:    3,
        stroke: psdFg(),
        ticks:  { stroke: psdFg(), size: 4 },
        grid:   { stroke: psdGrid() },
        font:   "11px Menlo,monospace",
    }, opts);
}

function psdAxes() {
    return [
        // No x label here: "Frequency (MHz)" lives in the #psd-legend row below,
        // flanked by the trace keys, so the label band is not paid for twice.
        // 22 px = 4 px ticks + 3 px gap + one row of 11 px tick text.
        psdAxis({ size: 22 }),
        // The y gutter carries the tick values, so it stays wide; 14 px of it is
        // the rotated axis label (vs uPlot's default 30).
        psdAxis({ label: psdYLabel(), size: 54,
                  labelSize: 14, labelFont: "10px Menlo,monospace" }),
    ];
}

function psdPlotDimensions() {
    const container = document.getElementById("psd-container");
    const style = getComputedStyle(container);
    const innerWidth = container.clientWidth
        - parseFloat(style.paddingLeft || 0) - parseFloat(style.paddingRight || 0);
    const innerHeight = container.clientHeight
        - parseFloat(style.paddingTop || 0) - parseFloat(style.paddingBottom || 0);
    return {
        width: Math.max(240, Math.floor(innerWidth || 900)),
        height: Math.max(120, Math.floor((innerHeight || 300) - PSD_LEGEND_H)),
    };
}

// ── PSD trace keys (the DOM legend) ──────────────────────────────────────────
// Built from the series descriptors we hand uPlot, where `stroke` is still a
// plain colour string (uPlot normalises it into a function afterwards).
let psdSeriesSpec = [];

function buildPsdLegend() {
    const left  = document.getElementById("psd-keys-left");
    const right = document.getElementById("psd-keys-right");
    if (!left || !right) return;
    left.innerHTML = "";
    right.innerHTML = "";

    // "RX1 Mean" → group 1. Anything without an RXn prefix (the RX1−RX2 diff)
    // is group 0 and rides along on the right, keeping its full label.
    const groupOf = (label) => {
        const m = /^RX(\d+)\s+/.exec(label || "");
        return m ? Number(m[1]) : 0;
    };

    const entries = [];
    psdSeriesSpec.forEach((s, i) => {
        if (i === 0 || !s.label) return;
        entries.push({ i, label: s.label, stroke: s.stroke, group: groupOf(s.label) });
    });

    const firstGroup = entries.find((e) => e.group > 0)?.group ?? 0;
    const seen = new Set();
    for (const e of entries) {
        const host = e.group === firstGroup && e.group > 0 ? left : right;
        // Only the first key of a channel spells out "RX1 Mean"; the rest drop
        // the prefix ("Max", "Hold", "Min") the way the design row does.
        const bare = seen.has(e.group) && e.group > 0;
        seen.add(e.group);

        const el = document.createElement("span");
        el.className = "psd-key";
        el.dataset.series = String(e.i);
        el.title = e.label;
        const swatch = document.createElement("i");
        swatch.style.background = e.stroke || psdFg();
        const text = document.createElement("span");
        text.textContent = bare ? e.label.replace(/^RX\d+\s+/, "") : e.label;
        el.append(swatch, text);
        host.appendChild(el);
    }
    paintPsdLegend();
}

// Dim the keys whose series is hidden. renderPsd() re-derives visibility from
// the Hold/Min/diff checkboxes every frame, so this runs there too.
function paintPsdLegend() {
    if (!uplot) return;
    document.querySelectorAll("#psd-legend .psd-key").forEach((el) => {
        const s = uplot.series[Number(el.dataset.series)];
        el.classList.toggle("is-off", !s || s.show === false);
    });
}

document.getElementById("psd-legend")?.addEventListener("click", (ev) => {
    const key = ev.target.closest && ev.target.closest(".psd-key");
    if (!key || !uplot) return;
    const i = Number(key.dataset.series);
    const s = uplot.series[i];
    if (!s) return;
    uplot.setSeries(i, { show: s.show === false });
    paintPsdLegend();
});

let psdResizeQueued = false;
function fitUplotToContainer() {
    if (!uplot || psdResizeQueued) return;
    psdResizeQueued = true;
    requestAnimationFrame(() => {
        psdResizeQueued = false;
        if (!uplot) return;
        // uPlot temporarily clears axis ranges while setData() rebuilds scales.
        // A ResizeObserver callback in that window makes setSize() draw axes
        // whose internal tick state is still null ("object null is not
        // iterable").  The next frame/resize will fit it once both scales are
        // ready; the constructor already received the correct initial size.
        if (uplot.scales.x.min == null || uplot.scales.y.min == null) return;
        const size = psdPlotDimensions();
        if (uplot.width !== size.width || uplot.height !== size.height) {
            uplot.setSize(size);
        }
    });
}

function initUplot(freqs) {
    const container = document.getElementById("psd-plot");
    if (uplot) {
        const previous = uplot;
        uplot = null;
        previous.destroy();
    }
    container.innerHTML = "";  // clear previous instance

    const size = psdPlotDimensions();

    // Series set follows the channel list (P3-4). Order for two channels is
    // the historical layout exactly: mean/max per channel, then holds, then
    // mins, then the RX1−RX2 diff (which only exists with two channels).
    const chans  = channelList || [0, 1];
    const rxName = (i) => `RX${i + 1}`;
    const series = [{}];   // x (freqs)
    chans.forEach((ch, i) => {
        series.push({ label: `${rxName(i)} Mean`, stroke: chColors(i).mean,
                      width: 2, show: true });
        series.push({ label: `${rxName(i)} Max`,  stroke: chColors(i).max,
                      width: 2, show: true });
    });
    chans.forEach((ch, i) => {
        series.push({ label: `${rxName(i)} Hold`, stroke: chColors(i).hold,
                      width: 1, dash: [4, 4], show: false });
    });
    chans.forEach((ch, i) => {
        series.push({ label: `${rxName(i)} Min`,  stroke: chColors(i).min,
                      width: 1, dash: [2, 4], show: false });
    });
    if (chans.length === 2) {
        series.push({ label: "RX1−RX2", stroke: diffCol(), width: 2, show: false });
    }

    const opts = {
        width:  size.width,
        height: size.height,
        title:  `Power Spectral Density (${chans.map((c, i) => rxName(i)).join(" + ")})`,
        background: psdBg(),
        cursor: {
            show:  true,
            drag:  { x: false, y: false },
            focus: { prox: 32 },
        },
        legend: { show: false },   // replaced by the #psd-legend key row
        scales: {
            x: { time: false },
            y: { auto: true },
        },
        axes: psdAxes(),
        series,
        hooks: {
            draw: [drawPsdOverlays],
        },
    };

    const nfft   = freqs.length;
    // Each y-series must be an array the same length as the x-axis. uPlot reads
    // data[i].length on every series, so a bare null throws at construction —
    // initialize with all-null arrays (rendered as gaps) until the first frame.
    const empty  = Array.from({ length: series.length - 1 },
                              () => new Array(nfft).fill(null));
    uplot = new uPlot(opts, [Array.from(freqs), ...empty], container);
    uplotKind = "std:" + channelsKey(chans);
    psdSeriesSpec = series;
    buildPsdLegend();

    // Preserve the crosshair toggle across re-inits (a retune rebuilds the plot,
    // which would otherwise silently reset the cursor to "on") — LV-R9a.
    const crossChk = document.getElementById("cross-chk");
    if (crossChk) uplot.cursor.show = crossChk.checked;

    // Set up band dragging on the uPlot canvas
    setupBandDrag();
    fitUplotToContainer();
}

// ---------------------------------------------------------------------------
// PSD backend — server statistic traces (P2b-4)
// ---------------------------------------------------------------------------
//
// With backend "psd" the server runs striqt's power_spectral_density and each
// block row is one time_statistic trace (header psd_stats names them). The
// plot is rebuilt with one series per (channel, statistic); uPlot's clickable
// legend entries are the trace toggles, so the drawn set always reflects the
// REAL statistic list instead of a fixed mean/max pair.

function statLabel(stat) {
    const q = parseFloat(stat);
    if (isFinite(q) && String(q) === String(stat).trim()) {
        return "p" + (q * 100).toFixed(q * 100 % 1 ? 1 : 0);
    }
    const s = String(stat);
    return s.charAt(0).toUpperCase() + s.slice(1);
}

// Trace colors: mean stays the blue family, max the red family (matching the
// classic pair); other statistics cycle a distinct palette. Per statistic the
// two shades alternate over the channel index (0 = saturated, 1 = light).
const STAT_COLS = {
    mean: ["#4ea3ff", "#9ac8ff"],
    max:  ["#ff5252", "#ff9a9a"],
    peak: ["#ff5252", "#ff9a9a"],
    min:  ["#7986cb", "#c5cae9"],
};
const QUANT_COLS = [
    ["#ffb74d", "#ffe0b2"],   // orange
    ["#ba68c8", "#e1bee7"],   // violet
    ["#4db6ac", "#b2dfdb"],   // teal
    ["#f06292", "#f8bbd0"],   // pink
    ["#dce775", "#f0f4c3"],   // lime
    ["#90a4ae", "#cfd8dc"],   // blue-grey
];

function statColors(stats) {
    let cycle = 0;
    return stats.map((s) => {
        const named = STAT_COLS[String(s).toLowerCase()];
        if (named) return named;
        return QUANT_COLS[cycle++ % QUANT_COLS.length];
    });
}

function initUplotPsdStats(freqs, stats) {
    const container = document.getElementById("psd-plot");
    if (uplot) {
        const previous = uplot;
        uplot = null;
        previous.destroy();
    }
    container.innerHTML = "";
    const size = psdPlotDimensions();
    const cols = stats ? statColors(stats) : [];
    const chans = channelList || [0, 1];

    const series = [{}];
    chans.forEach((ch, c) => {
        stats.forEach((s, i) => {
            series.push({
                label:  `RX${c + 1} ${statLabel(s)}`,
                stroke: cols[i][c % cols[i].length],
                width:  2,
                show:   true,
            });
        });
    });

    const opts = {
        width:  size.width,
        height: size.height,
        title:  `Power Spectral Density — striqt statistics (${chans.map((_, i) => `RX${i + 1}`).join(" + ")})`,
        background: psdBg(),
        cursor: {
            show:  true,
            drag:  { x: false, y: false },
            focus: { prox: 32 },
        },
        legend: { show: false },   // replaced by the #psd-legend key row
        scales: { x: { time: false }, y: { auto: true } },
        axes: psdAxes(),
        series,
        hooks: { draw: [drawPsdOverlays] },
    };

    const nfft  = freqs.length;
    const empty = Array.from({ length: series.length - 1 },
                             () => new Array(nfft).fill(null));
    uplot = new uPlot(opts, [Array.from(freqs), ...empty], container);
    uplotKind = "psd:" + channelsKey(chans) + ":" + stats.join(",");
    psdSeriesSpec = series;
    buildPsdLegend();

    const crossChk = document.getElementById("cross-chk");
    if (crossChk) uplot.cursor.show = crossChk.checked;
    setupBandDrag();
    fitUplotToContainer();
}

function renderServerPsd(channels, blocks, rows, nfft) {
    if (!freqsMHz || !serverStats) return;
    const stats = serverStats;
    const chans = channelList || [0, 1];
    const kind  = "psd:" + channelsKey(chans) + ":" + stats.join(",");
    if (!uplot || uplotKind !== kind) initUplotPsdStats(freqsMHz, stats);

    const nStats  = Math.min(stats.length, rows);
    const freqArr = Array.from(freqsMHz);
    const gaps    = new Array(nfft).fill(null);
    const data    = [freqArr];
    const traces  = {};
    for (const ch of chans) {
        traces[ch] = [];
        const block = blocks[ch];
        for (let s = 0; s < stats.length; s++) {
            if (!block || s >= nStats) {
                data.push(gaps);
                traces[ch].push(null);
                continue;
            }
            const tr = block.subarray(s * nfft, (s + 1) * nfft);
            traces[ch].push(tr);
            data.push(Array.from(tr));
        }
    }
    uplot.setData(data);
    psdData.server = { stats, traces };

    // Peak markers from the most peak-like trace (max if present, else the
    // last statistic), respecting the existing Peak marker checkbox.
    if (peakMarker) {
        let idx = stats.findIndex((s) => String(s).toLowerCase() === "max");
        if (idx < 0) idx = stats.length - 1;
        peakMarkerData = chans.map((ch) => bestBin(traces[ch][idx], freqArr));
    }
    applyYspan();
}

function psdSeries(channels, blocks, rows, nfft) {
    /**
     * Compute mean and max PSD curves from the current display buffers
     * (not just the latest frame), so the PSD reflects the same window
     * that's shown in the waterfall.
     */
    const mean = {}, max = {}, min = {};

    for (const ch of (channelList || [])) {
        const buf = wfBuf[ch];
        if (!buf) continue;
        const depth = buf.length / nfft;
        const m = new Float32Array(nfft);
        const x = new Float32Array(nfft).fill(-Infinity);
        const n = new Float32Array(nfft).fill(Infinity);

        for (let r = 0; r < depth; r++) {
            const off = r * nfft;
            for (let f = 0; f < nfft; f++) {
                const v = buf[off + f];
                m[f] += Math.pow(10, v / 10);   // accumulate LINEAR power (LV-F3)
                if (v > x[f]) x[f] = v;
                if (v < n[f]) n[f] = v;
            }
        }
        // Convert the linear-power mean back to dB. Averaging dB directly
        // underreports the time-averaged power of fluctuating signals; this
        // mirrors the band monitor's (correct) linear convention.
        for (let f = 0; f < nfft; f++) m[f] = 10 * Math.log10(Math.max(m[f] / depth, 1e-20));

        mean[ch] = m;
        max[ch]  = x;
        min[ch]  = n;

        // Cache for band monitor + exports
        psdData.mean[ch] = m;
        psdData.max[ch]  = x;
    }

    return { mean, max, min };
}

function updatePSD(channels, blocks, rows, nfft) {
    if (!uplot || !freqsMHz) return;

    const chans = channelList || [0, 1];
    const twoCh = chans.length === 2;
    const diffActive = twoCh && showDiff;
    const { mean, max, min } = psdSeries(channels, blocks, rows, nfft);

    // Update peak hold
    for (const ch of chans) {
        if (!max[ch]) continue;
        if (peakHold) {
            if (!holdBuf[ch] || holdBuf[ch].length !== nfft) {
                holdBuf[ch] = new Float32Array(max[ch]);
            } else {
                for (let i = 0; i < nfft; i++) {
                    if (max[ch][i] > holdBuf[ch][i]) holdBuf[ch][i] = max[ch][i];
                }
            }
        }
        if (showMin) {
            if (!minBuf[ch] || minBuf[ch].length !== nfft) {
                minBuf[ch] = new Float32Array(min[ch]);
            } else {
                for (let i = 0; i < nfft; i++) {
                    if (min[ch][i] < minBuf[ch][i]) minBuf[ch][i] = min[ch][i];
                }
            }
        }
    }

    const freqArr = Array.from(freqsMHz);

    // Never hand uPlot a bare null series — it reads data[i].length. Any series
    // that is toggled off or not yet available becomes a length-nfft array of
    // nulls, which uPlot renders as gaps (drawing nothing).
    // Data/vis order mirrors initUplot's series order exactly: mean/max per
    // channel, then holds, then mins, then (two channels only) the diff.
    const gaps = new Array(nfft).fill(null);
    const data = [freqArr];
    const vis  = [true];
    for (const ch of chans) {
        data.push(mean[ch] ? Array.from(mean[ch]) : gaps);
        data.push(max[ch]  ? Array.from(max[ch])  : gaps);
        vis.push(!diffActive, !diffActive);
    }
    for (const ch of chans) {
        data.push((peakHold && holdBuf[ch]) ? Array.from(holdBuf[ch]) : gaps);
        vis.push(peakHold && !diffActive);
    }
    for (const ch of chans) {
        data.push((showMin && minBuf[ch]) ? Array.from(minBuf[ch]) : gaps);
        vis.push(showMin && !diffActive);
    }
    if (twoCh) {
        const m0 = mean[chans[0]], m1 = mean[chans[1]];
        data.push((diffActive && m0 && m1)
            ? Array.from(m0).map((v, i) => v - m1[i]) : gaps);
        vis.push(diffActive);
    }

    uplot.setData(data);
    vis.forEach((v, i) => { if (i > 0) uplot.setSeries(i, { show: v }); });
    paintPsdLegend();

    // Peak markers (strongest bin per visible channel) — LV-U1b
    if (peakMarker && !diffActive) {
        peakMarkerData = chans.map((ch) => bestBin(max[ch] || null, freqArr));
    }

    // Fixed Y-span
    applyYspan();
}

// Peak markers: computed per frame, drawn via uPlot's redraw hook. One entry
// per channel (display order matches channelList) — LV-U1b / P3-4.
let peakMarkerData = null;   // null | array of ({freq, power} | null)
function bestBin(arr, freqArr) {
    if (!arr) return null;
    let bestI = 0;
    for (let i = 1; i < arr.length; i++) {
        if (arr[i] !== null && (arr[bestI] === null || arr[i] > arr[bestI])) bestI = i;
    }
    const v = arr[bestI];
    return (v === null || v === undefined) ? null : { freq: freqArr[bestI], power: v };
}

// uPlot draw hook — overlays: peak marker, band selection
function drawPsdOverlays(u) {
    const ctx = u.ctx;
    ctx.save();

    // ── Peak markers (one per visible channel) ────────────────────────────
    if (peakMarker && peakMarkerData && !showDiff) {
        const drawOne = (pm, color, tag) => {
            if (!pm) return;
            const px = u.valToPos(pm.freq,  "x", true);
            const py = u.valToPos(pm.power, "y", true);
            if (!px || !py) return;
            ctx.beginPath();
            ctx.arc(px, py, 5, 0, 2 * Math.PI);
            ctx.fillStyle   = color;
            ctx.strokeStyle = "#000";
            ctx.lineWidth   = 1;
            ctx.fill();
            ctx.stroke();
            ctx.fillStyle = color;
            ctx.font      = "bold 11px Menlo,monospace";
            ctx.fillText(`${tag} ${pm.freq.toFixed(3)} MHz  ${pm.power.toFixed(1)} dB`, px + 8, py - 5);
        };
        peakMarkerData.forEach((pm, i) => drawOne(pm, chColors(i).max, `RX${i + 1}`));
    }

    // ── Band selection region ─────────────────────────────────────────────
    if (bandLo !== null && bandHi !== null) {
        const lo = Math.min(bandLo, bandHi);
        const hi = Math.max(bandLo, bandHi);
        const lx = u.valToPos(lo, "x", true);
        const rx = u.valToPos(hi, "x", true);
        const yt = u.bbox.top;
        const yh = u.bbox.height;
        if (lx !== null && rx !== null) {
            ctx.fillStyle   = "rgba(120,255,160,0.10)";
            ctx.strokeStyle = "rgba(120,255,160,0.85)";
            ctx.lineWidth   = 2;
            ctx.fillRect(lx, yt, rx - lx, yh);
            ctx.strokeRect(lx, yt, rx - lx, yh);
        }
    }

    ctx.restore();
}

function applyYspan() {
    if (!uplot) return;
    if (psdYspan === null) {
        // uPlot normalizes scale.auto into a function (fnOrSelf) at construction
        // and *calls* it as auto(self, resetScales) on every rescale. Assigning a
        // bare boolean here makes uPlot throw "e.auto is not a function" on the
        // next draw and the plot never paints — so always assign a function.
        uplot.scales.y.auto = () => true;
        return;
    }
    // Find highest displayed value across all visible curves and lock a span
    let peak = null;
    const d  = uplot.data;
    for (let s = 1; s < d.length; s++) {
        if (!d[s] || !uplot.series[s].show) continue;
        for (const v of d[s]) {
            if (v !== null && (peak === null || v > peak)) peak = v;
        }
    }
    if (peak !== null) {
        const head = psdYspan * 0.05;
        uplot.scales.y.auto = () => false;
        uplot.setScale("y", { min: peak - psdYspan + head, max: peak + head });
    }
}

// ---------------------------------------------------------------------------
// Band monitor
// ---------------------------------------------------------------------------

const bandMonitorEl = document.getElementById("band-monitor");

function updateBandMonitor(channels, blocks, rows, nfft) {
    if (!freqsMHz || bandLo === null || bandHi === null) {
        bandMonitorEl.textContent = "Band monitor: --";
        return;
    }
    const lo = Math.min(bandLo, bandHi);
    const hi = Math.max(bandLo, bandHi);

    // freqsMHz is sorted ascending — find the in-band index range once, instead of
    // an O(rows·nfft·bins) mask.includes() scan that froze at nfft 4096 (LV-R6).
    let loIdx = 0;
    while (loIdx < nfft && freqsMHz[loIdx] < lo) loIdx++;
    let hiIdx = nfft - 1;
    while (hiIdx >= 0 && freqsMHz[hiIdx] > hi) hiIdx--;
    if (loIdx > hiIdx) {
        bandMonitorEl.textContent = `Band ${lo.toFixed(3)}–${hi.toFixed(3)} MHz: no bins`;
        return;
    }
    const nBins = hiIdx - loIdx + 1;

    const chans = channelList || [0, 1];
    const primary = chans[0];
    const band = {}, qual = {}, noise = {};
    let peakDb = -Infinity, peakIdx = loIdx;
    for (const ch of chans) {
        // PSD backend (P2b-4): no waterfall window — integrate the mean trace
        // (or the first statistic) instead of the display buffer.
        let buf = null, depth = 0;
        if (psdData.server) {
            const stats = psdData.server.stats;
            let idx = stats.findIndex((s) => String(s).toLowerCase() === "mean");
            if (idx < 0) idx = 0;
            buf = psdData.server.traces[ch] ? psdData.server.traces[ch][idx] : null;
            depth = 1;
        } else {
            buf = wfBuf[ch];
            depth = buf ? buf.length / nfft : 0;
        }
        if (!buf) continue;

        // Correct linear-domain averaging (avoids the dB-averaging error).
        let sumInBand = 0, sumAll = 0;
        for (let r = 0; r < depth; r++) {
            const off = r * nfft;
            for (let i = 0; i < nfft; i++) {
                const v   = buf[off + i];
                const lin = Math.pow(10, v / 10);
                sumAll += lin;
                if (i >= loIdx && i <= hiIdx) {
                    sumInBand += lin;
                    if (ch === primary && v > peakDb) { peakDb = v; peakIdx = i; }
                }
            }
        }
        const linBand = sumInBand / (nBins * depth);
        const linAll  = sumAll    / (nfft  * depth);
        const nOut    = (nfft - nBins) * depth;
        const linOut  = nOut > 0 ? (sumAll - sumInBand) / nOut : linAll;
        band[ch]  = 10 * Math.log10(Math.max(linBand, 1e-20));
        qual[ch]  = band[ch] - 10 * Math.log10(Math.max(linAll, 1e-20));
        noise[ch] = 10 * Math.log10(Math.max(linOut, 1e-20));
    }

    if (band[primary] === undefined) {
        bandMonitorEl.textContent = "Band monitor: --";
        return;
    }

    // Uncalibrated dB rel. FS — honest units (quicklook is per-bin).
    const unit    = curBackend === "quicklook" ? "dB/bin" : "dB";
    const bandDb   = band[primary];
    const rx2      = chans[1];
    const peakFreq = freqsMHz[peakIdx];
    const pct = Math.max(0, Math.min(100, (bandDb + 100) / 80 * 100));   // -100..-20 dB → 0..100%
    const num = (x, u) => (x === undefined || !isFinite(x))
        ? "\u2014" : `${x.toFixed(1)}<small> ${u || unit}</small>`;
    const V = (k, v, col) =>
        `<div><div class="bm-k">${k}</div><div class="bm-v"${col ? ` style="color:${col}"` : ""}>${v}</div></div>`;

    bandMonitorEl.textContent = "";
    bandMonitorEl.innerHTML =
        `<div class="bm-head"><span class="bm-title">BAND MONITOR</span>` +
        `<span class="bm-span">${lo.toFixed(1)}\u2013${hi.toFixed(1)} MHz \u00b7 ${nBins} bins</span></div>` +
        `<div class="bm-big"><b>${bandDb.toFixed(1)}</b><span>${unit} in band</span></div>` +
        `<div class="bm-bar"><i style="width:${pct.toFixed(0)}%"></i></div>` +
        `<div class="bm-grid">` +
            V("RX1", num(band[primary]), "var(--mean)") +
            V("RX2", rx2 !== undefined ? num(band[rx2]) : "\u2014", "var(--ch2)") +
            V("PEAK", num(peakDb), "var(--max)") +
            V("PEAK FREQ", `${peakFreq.toFixed(2)}<small> MHz</small>`) +
            (rx2 !== undefined
                ? V("\u0394 RX1\u2212RX2", `${(band[primary] - band[rx2]).toFixed(1)}<small> dB</small>`)
                : V("QUALITY", `${qual[primary] >= 0 ? "+" : ""}${qual[primary].toFixed(1)}<small> dB</small>`)) +
            V("NOISE", num(noise[primary])) +
        `</div>`;
}

// ---------------------------------------------------------------------------
// Band selection drag (on the uPlot canvas)
// ---------------------------------------------------------------------------

function resetBand(freqs) {
    if (!freqs) return;
    const lo = freqs[Math.floor(freqs.length * 0.45)];
    const hi = freqs[Math.floor(freqs.length * 0.55)];
    bandLo = Math.min(lo, hi);
    bandHi = Math.max(lo, hi);
    if (uplot) uplot.redraw();
}

// PSD band-monitor selection: drag to move/resize the analysis band.
// No x-axis zoom/pan/box-zoom — the PSD always live-follows the full span.
let bandDragAbort = null;      // AbortController for the CURRENT plot's listeners
function setupBandDrag() {
    if (!uplot) return;
    const over = uplot.over;   // the event-capture div over the uPlot canvas

    // Drop the previous plot's window listeners. setupBandDrag runs on every
    // uPlot rebuild — retune, theme toggle, Absolute-RF toggle, channel change,
    // std↔psd-stats swap — and the old anonymous window listeners were never
    // removed, so a long session accumulated one live closure set per rebuild,
    // each recomputing the band on every pointermove.
    if (bandDragAbort) bandDragAbort.abort();
    bandDragAbort = new AbortController();
    const sig = bandDragAbort.signal;

    let dragStart = null;
    let origLo, origHi;

    // Pointer events fire for mouse, touch and pen — touch-action:none keeps a
    // drag on a phone from scrolling the page instead of moving the band.
    over.style.touchAction = "none";

    function freqAtX(clientX) {
        const rect = over.getBoundingClientRect();
        const px   = clientX - rect.left;
        return uplot.posToVal(px, "x");
    }

    function hitTest(freq) {
        if (bandLo === null) return null;
        const lo = Math.min(bandLo, bandHi);
        const hi = Math.max(bandLo, bandHi);
        const tol = (hi - lo) * 0.12 + 0.05;   // MHz tolerance for handle grab
        if (Math.abs(freq - lo) < tol) return "lo";
        if (Math.abs(freq - hi) < tol) return "hi";
        if (freq > lo && freq < hi)    return "body";
        return null;
    }

    over.style.cursor = "crosshair";

    over.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        const f = freqAtX(e.clientX);
        if (f === null) return;
        const hit = hitTest(f);
        if (hit) {
            // Drag existing band
            dragStart = f;
            bandDrag  = hit;
            origLo    = bandLo;
            origHi    = bandHi;
            over.style.cursor = hit === "body" ? "grab" : "ew-resize";
        } else {
            // Draw new band
            bandLo = f;
            bandHi = f;
            bandDrag = "new";
            dragStart = f;
        }
        try { over.setPointerCapture(e.pointerId); } catch (_) {}
        e.preventDefault();
    });

    window.addEventListener("pointermove", (e) => {
        if (!bandDrag) return;
        const f = freqAtX(e.clientX);
        if (f === null) return;
        const delta = f - dragStart;
        if (bandDrag === "lo")   { bandLo = origLo + delta; }
        else if (bandDrag === "hi")  { bandHi = origHi + delta; }
        else if (bandDrag === "body"){ bandLo = origLo + delta; bandHi = origHi + delta; }
        else if (bandDrag === "new") { bandHi = f; }
        if (uplot) uplot.redraw();
    }, { signal: sig });

    window.addEventListener("pointerup", () => {
        if (bandDrag) {
            bandDrag = null;
            over.style.cursor = "crosshair";
        }
    }, { signal: sig });
}

// ---------------------------------------------------------------------------
// Export helpers
// ---------------------------------------------------------------------------

function savePsdCsv() {
    // PSD backend (P2b-4): export the server statistic traces, one column per
    // (channel, statistic).
    const chans = channelList || [0, 1];
    if (psdData.server && freqsMHz) {
        const { stats, traces } = psdData.server;
        const nfft = freqsMHz.length;
        const cols = [];
        chans.forEach((ch, i) => {
            for (const s of stats) cols.push(`rx${i + 1}_${statLabel(s).toLowerCase()}_db`);
        });
        const rows = [
            `# backend=psd`,
            `# fft_nfft=${curFftNfft}`,
            `# bin_avg=${curBinAvg}`,
            `# time_statistic=${stats.join(";")}`,
            `# integration_span_ms=${curSpanMs != null ? curSpanMs.toFixed(3) : ""}`,
            "freq_mhz," + cols.join(","),
        ];
        for (let i = 0; i < nfft; i++) {
            const vals = [];
            for (const ch of chans) {
                for (let s = 0; s < stats.length; s++) {
                    const tr = traces[ch] ? traces[ch][s] : null;
                    vals.push(tr ? tr[i].toFixed(3) : "");
                }
            }
            rows.push(`${freqsMHz[i].toFixed(6)},${vals.join(",")}`);
        }
        const blob = new Blob([rows.join("\n")], { type: "text/csv" });
        const a    = document.createElement("a");
        a.href     = URL.createObjectURL(blob);
        a.download = `live_psd_stats_${Date.now()}.csv`;
        a.click();
        logMsg("PSD statistics CSV saved");
        return;
    }
    if (!freqsMHz || !psdData.mean[chans[0]]) {
        logMsg("No PSD data yet — try again after the first frame", "WARN");
        return;
    }
    const nfft = freqsMHz.length;
    const cols = [];
    chans.forEach((ch, i) => cols.push(`rx${i + 1}_mean_db`, `rx${i + 1}_max_db`));
    const rows = [
        `# backend=${curBackend}`,
        `# fft_nfft=${curFftNfft}`,
        `# bin_avg=${curBinAvg}`,
        `# units=dB (uncalibrated, ${curBackend === "quicklook" ? "per-bin" : "band-integrated"})`,
        "freq_mhz," + cols.join(","),
    ];
    for (let i = 0; i < nfft; i++) {
        const vals = [];
        for (const ch of chans) {
            vals.push(psdData.mean[ch] ? psdData.mean[ch][i].toFixed(3) : "");
            vals.push(psdData.max[ch]  ? psdData.max[ch][i].toFixed(3)  : "");
        }
        rows.push(`${freqsMHz[i].toFixed(6)},${vals.join(",")}`);
    }
    const blob = new Blob([rows.join("\n")], { type: "text/csv" });
    const a    = document.createElement("a");
    a.href     = URL.createObjectURL(blob);
    a.download = `live_psd_${Date.now()}.csv`;
    a.click();
    logMsg("PSD CSV saved");
}

function exportPng() {
    // Composite the waterfalls side by side (one per channel), then the PSD below
    const chans    = channelList || [];
    const canvases = chans.map((ch) => wfCanvas[ch]).filter(Boolean);
    const psdCanvas = uplot ? uplot.root.querySelector("canvas") : null;

    const wfW = canvases.reduce((sum, c) => sum + c.width, 0);
    const wfH = canvases.reduce((m, c) => Math.max(m, c.height), 0);
    const W  = Math.max(wfW, psdCanvas ? psdCanvas.width : 0);
    const H  = wfH + (psdCanvas ? psdCanvas.height : 0) + 30;
    const out = document.createElement("canvas");
    out.width  = W;
    out.height = H;
    const ctx = out.getContext("2d");
    ctx.fillStyle = psdBg();
    ctx.fillRect(0, 0, W, H);
    let x = 0;
    for (const c of canvases) {
        ctx.drawImage(c, x, 0);
        x += c.width;
    }
    if (psdCanvas) ctx.drawImage(psdCanvas, 0, wfH);

    // Settings caption
    const ts  = new Date().toLocaleString();
    const buf0 = firstWfBuf();
    const capDepth = buf0 ? buf0.length / curBins : curRows;
    const capWinMs = (capDepth * rowHopSamples() / curFs * 1e3).toFixed(0);
    const capFft   = curBackend === "quicklook" ? `${radioNfft}` : `${radioNfft}→${curFftNfft}`;
    const cap = `${ts}  center ${(curCenter / 1e6).toFixed(3)} MHz  span ${(curFs / 1e6).toFixed(2)} MS/s  FFT ${capFft}  window ${capWinMs} ms`;
    ctx.fillStyle = isLightTheme() ? "#3a434f" : "#d0d0d0";
    ctx.font      = "11px Menlo,monospace";
    ctx.fillText(cap, 10, H - 8);

    const a    = document.createElement("a");
    a.href     = out.toDataURL("image/png");
    a.download = `live_view_${Date.now()}.png`;
    a.click();
    logMsg("PNG exported");
}

// ---------------------------------------------------------------------------
// Control wiring
// ---------------------------------------------------------------------------

function applyAnalysisMode() {
    document.body.classList.toggle("analysis-psd", analysisMode === "psd");
    document.body.classList.toggle("analysis-ssb", analysisMode === "ssb");
    // "PSD view" now selects the real striqt power_spectral_density backend
    // (P2b-4) — server-computed statistic traces — instead of the old client-only
    // waterfall-hide over the calibrated backend.
    const backend = analysisMode === "ssb" ? "ssb"
                  : analysisMode === "quicklook" ? "quicklook"
                  : analysisMode === "psd" ? "psd"
                  : "calibrated";
    clearChannelBufs(wfBuf, holdBuf, minBuf);
    sendControl({ backend });
    // Swap the Analysis panel to the selected analysis' parameter set (P2b-6).
    if (typeof renderAnalysisPanel === "function") renderAnalysisPanel();
    updateMeta();
}

// Center / span (sample_rate) / gain are set from the schema Capture Settings
// form now (they map to live radio params in SharedConfig.update) — the old
// "Radio (AIR-T)" bar and its handlers were removed in P1-3. FFT keeps a home
// as a static select in the Capture panel, wired below.
document.getElementById("nfft-sel").addEventListener("change", (e) => {
    const nfft = parseInt(e.target.value, 10);
    radioNfft = nfft;   // updated here and by the /config re-sync (P2a-5)
    // No client-side rows math: the server re-derives rows hop-aware from the
    // stored first-class duration whenever nfft changes (P2a-4).
    sendControl({ nfft });
});

// (P1-3) "Tune to band" was removed with the Radio bar. The PSD band-drag
// selection stays — it still drives the band monitor; only the tune action is
// gone.

const pauseBtn = document.getElementById("pause-btn");
pauseBtn.addEventListener("click", () => {
    paused = !paused;
    pauseBtn.textContent = paused ? "Resume" : "Pause";
    pauseBtn.classList.toggle("active", paused);
});

document.getElementById("mode-sel").addEventListener("change", (e) => {
    const value    = e.target.value;
    const wasAhawi = ahawiSelected || ahawiActive;
    ahawiSelected  = value === "ahawi";
    replaceMode    = value === "replace";
    ahawiUserModeChangeAt = performance.now();
    // Clear display buffers so mode switch starts clean
    clearChannelBufs(wfBuf);
    if (ahawiSelected) {
        // Entering the mode applies the currently staged settings in one shot.
        ahawiSendSettings();
        ahawiMarkStale();
    } else {
        if (ahawiActive) ahawiDeactivate();
        // One message: turn AHAWI off (only if it was on — keeps the op log
        // quiet) and assert the new mode's time control.
        const ctrl = replaceMode ? { capture: { duration: windowMs / 1000 } }
                                 : { rows: 12 };
        if (wasAhawi) ctrl.ahawi = false;
        sendControl(ctrl);
    }
});

// AHAWI capture knobs are STAGED (admin-only — read-only roles are blocked by
// the guard): edits light up Apply instead of racing one recapture per select.
document.getElementById("ahawi-capture-sel")?.addEventListener("change", () => {
    ahawiSetStaged(true);
});
document.getElementById("ahawi-align-chk")?.addEventListener("change", () => {
    ahawiSetStaged(true);
});
document.getElementById("ahawi-apply")?.addEventListener("click", () => {
    ahawiSendSettings();
    ahawiMarkStale();
});

document.getElementById("analysis-sel").addEventListener("change", (e) => {
    analysisMode = e.target.value;
    applyAnalysisMode();
});

// Duration control (P1-4/P2a-4) — the single owner of the time axis. Presets
// are available in both modes; the "custom…" option + number box are DAN-only.
// The value is in ms; it drives `windowMs`. In replace (Boring) mode it is sent
// as a first-class `capture.duration` and the SERVER derives rows hop-aware; in
// scroll (Cool) mode the client display depth follows `windowMs` via
// computeDisplayDepth.
const durSel         = document.getElementById("dur-sel");
const durCustomLabel = document.getElementById("dur-custom-label");
const durCustom      = document.getElementById("dur-custom");

function applyDuration() {
    const proMode = document.body.classList.contains("mode-pro");
    let ms;
    if (durSel.value === "custom" && proMode) {
        durCustomLabel.style.display = "";
        ms = parseFloat(durCustom.value);
    } else {
        durCustomLabel.style.display = "none";
        ms = parseFloat(durSel.value);   // NaN if "custom" reached outside DAN
    }
    if (!isFinite(ms) || ms <= 0) return;
    windowMs = ms;
    // Replace mode: ship the duration itself — the server owns the hop-aware
    // duration→rows mapping (P2a-4). AHAWI: duration is the segment length —
    // STAGED with the other capture knobs and shipped by Apply. Scroll mode:
    // the display depth follows windowMs client-side; the server keeps
    // streaming fixed 12-row chunks.
    if (ahawiSelected) {
        ahawiSetStaged(true);
    } else if (replaceMode) {
        sendControl({ capture: { duration: windowMs / 1000 } });
    }
    updateMeta();
}

durSel.addEventListener("change", applyDuration);
durCustom.addEventListener("change", applyDuration);
// Debounced, NOT applied per keystroke. Bound directly to "input", typing
// "150" sent three control messages (1 ms, 15 ms, 150 ms) — three server
// operations, each clearing the IQ ring — and each ack scheduled a /config
// re-seed that could rewrite this very box mid-typing.
let durCustomTimer = null;
durCustom.addEventListener("input", () => {
    clearTimeout(durCustomTimer);
    durCustomTimer = setTimeout(applyDuration, 500);
});
durCustom.addEventListener("blur", () => {
    clearTimeout(durCustomTimer);
    applyDuration();
});

document.getElementById("fps-sel").addEventListener("change", (e) => {
    maxFps = parseFloat(e.target.value) || 15;   // client-side render cap (LV-U1a)
    nextRender = 0;                              // restart the absolute schedule
});

document.getElementById("auto-color").addEventListener("change", (e) => {
    autoColor = e.target.checked;
});

document.getElementById("lo-null").addEventListener("change", (e) => {
    sendControl({ lo_null: e.target.checked });   // server-side DC-null toggle (LV-F8)
});

document.getElementById("abs-rf").addEventListener("change", (e) => {
    absRF    = e.target.checked;
    freqsMHz = buildFreqsMHz(curCenter, curFs, curBins, absRF, curF0, curStep);
    if (uplot && freqsMHz) initUplot(freqsMHz);
    resetBand(freqsMHz);
    renderWfAxis();
});

// Dark / light theme toggle. Client-only cosmetic preference, persisted in
// localStorage so it survives reloads and reconnects. Available to every role.
const THEME_KEY = "striqt-theme";
// The PSD canvas and the per-channel dots are painted with resolved colour
// strings, so a token flip alone cannot reach them: rebuild the plot (uPlot has
// no live background/stroke setter) and re-stroke the dots after the class
// change. uplotKind = null makes the next frame rebuild the right layout even
// if the plot is the striqt-statistics variant.
function repaintThemedGraphics() {
    (channelList || []).forEach((ch, i) => {
        const dot = document.querySelector(`#wf-pane-${ch} .dot`);
        if (!dot) return;
        dot.style.background = chColors(i).dot;
        dot.style.boxShadow  = `0 0 6px ${chColors(i).dot}`;
    });
    if (typeof uplot !== "undefined" && uplot && freqsMHz) {
        uplotKind = null;
        initUplot(freqsMHz);
    }
}

function applyTheme(theme) {
    const light = theme === "light";
    document.body.classList.toggle("light-theme", light);
    const btn = document.getElementById("theme-toggle");
    if (btn) {
        const icon  = btn.querySelector(".theme-icon");
        const label = btn.querySelector(".theme-label");
        if (icon)  icon.textContent  = light ? "☀️" : "🌙";
        if (label) label.textContent = light ? "Light" : "Dark";
    }
    repaintThemedGraphics();
}
(function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (_) {}
    applyTheme(saved === "light" ? "light" : "dark");
    const btn = document.getElementById("theme-toggle");
    if (btn) {
        btn.addEventListener("click", () => {
            const next = document.body.classList.contains("light-theme") ? "dark" : "light";
            applyTheme(next);
            try { localStorage.setItem(THEME_KEY, next); } catch (_) {}
        });
    }
})();

// Sign out / switch user: clear the session cookie (server-side) and land on
// the login form, where a different role can sign in.
const signoutBtn = document.getElementById("signout-btn");
if (signoutBtn) {
    signoutBtn.addEventListener("click", () => {
        window.location.href = "/logout";
    });
}

// Reset Radio (admin-only): restart the radio-web systemd service on the host
// as a VERIFIED operation. The 202 only proves the restart command launched;
// real confirmation is the /health boot_id CHANGING — that can only happen if
// a new server process actually came up. Every phase is logged to the
// Operations/Log surface so a failure names its stage instead of silently
// pretending success.
function verifyRestart(oldBootId) {
    const startT = Date.now();
    const timeoutMs = 60000;
    let sawDown = false;
    logMsg("[reset] verifying: polling /health for a new boot_id…", "WARN");
    const timer = setInterval(async () => {
        const elapsed = Date.now() - startT;
        if (elapsed > timeoutMs) {
            clearInterval(timer);
            logMsg(
                sawDown
                    ? "[reset] FAILED: service went down but never came back " +
                      "within 60 s — check `journalctl -u radio-web` on the host"
                    : "[reset] FAILED: service never went down and boot_id " +
                      "never changed — the restart likely did not reach this " +
                      "server (is RADIO_SERVICE_NAME right? is the sudoers " +
                      "rule installed?)",
                "ERROR"
            );
            setStatus("reset NOT verified — see log", "error");
            return;
        }
        let health = null;
        try {
            const r = await fetch("/health", { cache: "no-store" });
            if (r.ok) health = await r.json();
        } catch (_) {
            if (!sawDown) logMsg("[reset] service went down (expected)…", "WARN");
            sawDown = true;
            return;
        }
        if (!health) { sawDown = true; return; }
        // Known old boot_id → require a different one. Unknown (the 202 was
        // lost mid-restart) → require we at least saw the service go down.
        const restarted = health.boot_id &&
            (oldBootId ? health.boot_id !== oldBootId : sawDown);
        if (restarted) {
            clearInterval(timer);
            logMsg(
                `[reset] VERIFIED: service restarted (new boot ${String(health.boot_id).slice(0, 8)}, ` +
                `status ${health.status}, ${Math.round(elapsed / 1000)} s)`,
                "WARN"
            );
            setStatus("radio restarted — reconnecting…", "warn");
        }
        // Same boot_id and still up: keep polling; the timeout above reports
        // the RADIO_SERVICE_NAME mismatch case honestly.
    }, 1000);
}

const resetRadioBtn = document.getElementById("reset-radio-btn");
if (resetRadioBtn) {
    resetRadioBtn.addEventListener("click", () => {
        if (!isAdmin) return;   // guard also blocks it, but be explicit
        const ok = window.confirm(
            "Restart the radio service?\n\n" +
            "This disconnects all viewers for a few seconds while the radio " +
            "pipeline restarts."
        );
        if (!ok) return;
        logMsg("[reset] requested — restarting service…", "WARN");
        fetch("/admin/reset-radio", { method: "POST" })
            .then((r) => r.json().then((j) => ({ status: r.status, j })))
            .then(({ status, j }) => {
                if (status === 202) {
                    logMsg(`[reset] ${j.message || "restarting…"} (op #${j.op_id})`, "WARN");
                    setStatus("radio restarting — verifying…", "warn");
                    verifyRestart(j.boot_id || null);
                } else {
                    logMsg(`[reset] FAILED (${status}): ${j.error || "unknown"}`, "ERROR");
                    setStatus("reset failed — see log", "error");
                }
            })
            .catch((err) => {
                // A dropped connection mid-restart is possible if the restart
                // outraces the 202 — verification still settles it.
                logMsg(`[reset] ${err.message} — verifying via /health anyway`, "WARN");
                verifyRestart(null);
            });
    });
}

document.getElementById("csv-btn").addEventListener("click", savePsdCsv);
document.getElementById("png-btn").addEventListener("click", exportPng);

document.getElementById("diff-chk").addEventListener("change", (e) => {
    showDiff = e.target.checked;
});

document.getElementById("peak-chk").addEventListener("change", (e) => {
    peakMarker = e.target.checked;
    if (!peakMarker) peakMarkerData = null;
});

document.getElementById("hold-chk").addEventListener("change", (e) => {
    peakHold = e.target.checked;
    if (!peakHold) clearChannelBufs(holdBuf);
});

document.getElementById("clear-hold-btn").addEventListener("click", () => {
    clearChannelBufs(holdBuf);
    logMsg("Peak hold cleared");
});

document.getElementById("min-chk").addEventListener("change", (e) => {
    showMin = e.target.checked;
    if (!showMin) clearChannelBufs(minBuf);
});

document.getElementById("cross-chk").addEventListener("change", (e) => {
    if (uplot) uplot.cursor.show = e.target.checked;
});

document.getElementById("yspan-sel").addEventListener("change", (e) => {
    psdYspan = e.target.value === "auto" ? null : parseFloat(e.target.value);
    if (psdYspan === null && uplot) {
        uplot.scales.y.auto = () => true;
    }
});

// ---------------------------------------------------------------------------
// Schema-driven settings editor
// ---------------------------------------------------------------------------

const SOURCE_SKIP = new Set(["receive_retries", "adc_overload_limit", "if_overload_limit", "gapless"]);
// `port` is intentionally excluded — it is fixed at both RX ports server-side
// (make_capture) because the two-waterfall UI depends on it (P1-2). The four
// analysis knobs are now wired through to the radio on the next re-arm.
// `duration` is intentionally excluded — the Display "Duration (ms)" control is
// the single owner of the time axis (P1-4). Keeping it here too would let two
// controls fight over `rows` (the old Window-vs-duration bug).
const captureFields = [
    "center_frequency", "sample_rate", "gain", "analysis_bandwidth",
    "lo_shift", "host_resample", "backend_sample_rate",
];

// Display units for the numeric radio knobs: shown/edited in the friendly
// unit, converted to the wire unit (Hz / S/s) on send. Guards against the
// classic "typed 1955 meaning MHz, server clamped 1955 Hz to the 300 MHz
// floor" silent mistune.
const FIELD_UNITS = {
    center_frequency:    { unit: "MHz",  scale: 1e6 },
    sample_rate:         { unit: "MS/s", scale: 1e6 },
    backend_sample_rate: { unit: "MS/s (0 = track rate)", scale: 1e6 },
};
const sourceFields = [
    "master_clock_rate", "trigger_strobe", "signal_trigger", "array_backend",
    "calibration", "time_source", "time_sync_at", "clock_source",
];
let schemaDoc = null;
let hiddenSweepSettings = {};

function schemaDefs() {
    return schemaDoc && (schemaDoc.$defs || schemaDoc.definitions) || {};
}

function resolveSchema(schema) {
    if (!schema || !schema.$ref) return schema || {};
    const name = schema.$ref.split("/").pop();
    return schemaDefs()[name] || schema;
}

function scalarSchema(schema) {
    schema = resolveSchema(schema);
    if (schema.anyOf) {
        return resolveSchema(schema.anyOf.find((item) => item.type !== "null") || schema.anyOf[0]);
    }
    return schema;
}

function defaultFor(schema, fallback = "") {
    if (!schema) return fallback;
    if (Object.prototype.hasOwnProperty.call(schema, "default")) return schema.default;
    return fallback;
}

function makeField(group, name, schema, value) {
    const spec = scalarSchema(schema);
    const label = document.createElement("label");
    const units = group === "capture" ? FIELD_UNITS[name] : null;
    label.textContent = name.replaceAll("_", " ") + (units ? ` (${units.unit})` : "");

    let input;
    if (spec.enum) {
        input = document.createElement("select");
        for (const opt of spec.enum) {
            const option = document.createElement("option");
            option.value = opt;
            option.textContent = String(opt);
            input.appendChild(option);
        }
    } else if (spec.type === "boolean") {
        input = document.createElement("input");
        input.type = "checkbox";
    } else {
        input = document.createElement("input");
        input.type = spec.type === "integer" || spec.type === "number" ? "number" : "text";
        if (spec.type === "integer") input.step = "1";
        if (spec.type === "number") input.step = "any";
        if (typeof spec.minimum === "number") input.min = spec.minimum;
        if (typeof spec.maximum === "number") input.max = spec.maximum;
        if (typeof spec.exclusiveMinimum === "number") input.min = spec.exclusiveMinimum;
    }

    input.dataset.group = group;
    input.dataset.field = name;
    input.dataset.type = spec.type || "";
    if (units && input.type === "number") {
        input.dataset.unitScale = String(units.scale);
        if (input.min !== "") input.min = Number(input.min) / units.scale;
        if (input.max !== "") input.max = Number(input.max) / units.scale;
    }
    setFieldValue(input, value ?? defaultFor(spec));
    label.appendChild(input);
    return label;
}

function setFieldValue(input, value) {
    if (value === null || value === undefined) value = "";
    if (Array.isArray(value)) value = value.join(",");
    if (input.type === "checkbox") {
        input.checked = Boolean(value);
        return;
    }
    if (input.dataset && input.dataset.unitScale && value !== "") {
        const n = Number(value);
        if (isFinite(n)) value = n / Number(input.dataset.unitScale);
    }
    input.value = String(value);
}

function readFieldValue(input) {
    if (input.type === "checkbox") return input.checked;
    const raw = input.value.trim();
    if (raw === "") return null;
    const unitScale = input.dataset.unitScale ? Number(input.dataset.unitScale) : 1;
    if (input.dataset.type === "integer") return parseInt(raw, 10) * unitScale;
    if (input.dataset.type === "number") {
        const v = parseFloat(raw);
        return isFinite(v) ? v * unitScale : v;
    }
    if (raw.includes(",") && input.dataset.field === "port") {
        return raw.split(",").map((item) => parseInt(item.trim(), 10)).filter((item) => !Number.isNaN(item));
    }
    return raw;
}

function renderSettings(schema, seed = {}) {
    schemaDoc = schema;
    hiddenSweepSettings = seed;
    const defs = schemaDefs();
    const sweep = defs.air8201b || resolveSchema(schema);
    const source = resolveSchema(sweep.properties.source);
    const capture = resolveSchema((sweep.properties.captures || {}).items);
    const sourceValues = seed.source || {};
    const captureValues = (seed.captures && seed.captures[0]) || {};

    const captureForm = document.getElementById("capture-settings-form");
    const sourceForm = document.getElementById("source-settings-form");
    captureForm.textContent = "";
    sourceForm.textContent = "";

    for (const name of captureFields) {
        if (capture.properties && capture.properties[name]) {
            captureForm.appendChild(makeField("capture", name, capture.properties[name], captureValues[name]));
        }
    }
    for (const name of sourceFields) {
        if (!SOURCE_SKIP.has(name) && source.properties && source.properties[name]) {
            sourceForm.appendChild(makeField("source", name, source.properties[name], sourceValues[name]));
        }
    }
    snapshotFormBaseline();
}

function settingsInputs() {
    // NOTE: this selector previously targeted "#settings-editor", an element
    // that does not exist (the panel is #settings-panel) — so DAN's Apply
    // collected NOTHING and sent an empty payload: the server acked
    // "applied []" and the radio never tuned. ARIC's station chips bypass
    // this path, which is why ARIC tuned and DAN didn't.
    return document.querySelectorAll(
        "#capture-settings-form input, #capture-settings-form select, " +
        "#source-settings-form input, #source-settings-form select");
}

function collectSettings() {
    const payload = { capture: {}, source: {} };
    settingsInputs().forEach((input) => {
        payload[input.dataset.group][input.dataset.field] = readFieldValue(input);
    });
    return payload;
}

// Baseline of what the forms held after the last server seed — Apply only
// sends fields the user actually CHANGED, so tuning the center can no longer
// drag lo_shift / backend_sample_rate / source fields along with it.
let formBaseline = { capture: {}, source: {} };

function snapshotFormBaseline() {
    formBaseline = { capture: {}, source: {} };
    settingsInputs().forEach((input) => {
        formBaseline[input.dataset.group][input.dataset.field] = readFieldValue(input);
    });
}

function sameValue(a, b) {
    if (a === null || a === undefined || b === null || b === undefined) {
        return (a === null || a === undefined) && (b === null || b === undefined);
    }
    const x = Number(a), y = Number(b);
    if (isFinite(x) && isFinite(y) && String(a).trim() !== "" && String(b).trim() !== "") {
        return Math.abs(x - y) <= 1e-9 * Math.max(1, Math.abs(x), Math.abs(y));
    }
    return String(a) === String(b);
}

// ---------------------------------------------------------------------------
// Server-config seeding (P2a-5)
// ---------------------------------------------------------------------------
//
// Forms seed from the server's CURRENT config (/config), not the striqt schema
// defaults, so a bare Apply re-sends exactly what the server already runs — no
// silent flips of untouched fields (e.g. schema host_resample=true vs server
// false). Also the re-sync path after every settings/analysis ack, which keeps
// radioNfft and the panel values honest when the server rounds an input.

async function fetchConfig() {
    const resp = await fetch("/config", { cache: "no-store" });
    if (!resp.ok) throw new Error(`config HTTP ${resp.status}`);
    return resp.json();
}

// The ARIC station chips are gated by the ACTIVE device's tuning envelope —
// a Pluto (325 MHz–3.8 GHz) greys out chips an AIR-T could tune.
function gateStationChips(env) {
    if (!env) return;
    document.querySelectorAll(".freq-chip[data-mhz]").forEach((chip) => {
        const hz = parseFloat(chip.dataset.mhz) * 1e6;
        if (!isFinite(hz)) return;
        const legal = hz >= env.freq_min && hz <= env.freq_max;
        chip.disabled = !legal;
        chip.classList.toggle("is-disabled", !legal);
        // Keep the caption honest too: a chip re-enabled for a wider-tuning
        // radio used to keep reading "below radio range" while being clickable.
        const mhzEl = chip.querySelector(".fc-mhz");
        if (mhzEl) {
            mhzEl.textContent = legal
                ? `${(hz / 1e6) >= 1000 ? (hz / 1e9).toFixed(3) + " GHz"
                                        : (hz / 1e6).toFixed(3) + " MHz"}`
                : "outside radio range";
        }
        if (!legal) {
            chip.title = `Outside this device's ${(env.freq_min / 1e6).toFixed(0)}–` +
                         `${(env.freq_max / 1e6).toFixed(0)} MHz tuning range`;
        } else if (chip.title) {
            chip.title = "";
        }
    });
}

function seedStaticControls(config) {
    if (config && config.device) updateDeviceLabel(config.device.label);
    gateStationChips(config && config.envelope);
    const cap = (config && config.capture) || {};
    if (cap.nfft) {
        radioNfft = cap.nfft;   // /config re-sync — the other radioNfft updater
        const sel = document.getElementById("nfft-sel");
        if (sel) sel.value = String(cap.nfft);
    }
    if (cap.duration && !(ahawiStaged && ahawiSelected)) {
        // In AHAWI a staged (not yet applied) segment duration must survive
        // this resync, or Apply would ship the server's old value back.
        const ms = cap.duration * 1000;
        windowMs = ms;
        const preset = Array.from(durSel.options)
            .map((o) => o.value)
            .find((v) => parseFloat(v) === ms);
        if (preset) {
            durSel.value = preset;
            durCustomLabel.style.display = "none";
        } else {
            durSel.value = "custom";
            durCustom.value = String(ms);
            if (document.body.classList.contains("mode-pro")) {
                durCustomLabel.style.display = "";
            }
        }
    }
    // AHAWI server state → controls (re-sync path, same contract as nfft/
    // duration above). Frame arrival — not this — drives the actual replay UI.
    // STAGED edits win over the resync: this runs after every ack, and
    // clobbering a not-yet-applied selection back to the server value would
    // silently discard what the user just chose.
    const ah = config && config.ahawi;
    if (ah && !ahawiStaged) {
        const capSel = document.getElementById("ahawi-capture-sel");
        if (capSel && ah.capture_ms) {
            const match = Array.from(capSel.options)
                .find((o) => parseFloat(o.value) === ah.capture_ms);
            if (match) capSel.value = match.value;
        }
        const alignCk = document.getElementById("ahawi-align-chk");
        if (alignCk) alignCk.checked = !!ah.align;
        const modeSel = document.getElementById("mode-sel");
        if (ah.enabled && modeSel && modeSel.value !== "ahawi"
                && performance.now() - ahawiUserModeChangeAt > 3000) {
            modeSel.value = "ahawi";
            ahawiSelected = true;
        }
    }
}

function seedCaptureForm(config) {
    const cap = (config && config.capture) || {};
    // Device capability envelope (P3-5): display-only min/max attributes +
    // tooltips on the live radio knobs. The server's freedom-model clamps
    // remain authoritative — an out-of-range entry is still sent and comes
    // back as a "rounded" ack; these hints just make the range visible.
    const env = (config && config.envelope) || null;
    const hints = env ? {
        center_frequency: [env.freq_min, env.freq_max, "Hz"],
        gain:             [env.gain_min, env.gain_max, "dB"],
        sample_rate:      [env.rate_min, env.rate_max, "S/s"],
    } : null;
    document.querySelectorAll("#capture-settings-form input, #capture-settings-form select")
        .forEach((input) => {
            const name = input.dataset.field;
            if (name in cap) setFieldValue(input, cap[name]);
            const hint = hints && hints[name];
            if (hint && input.tagName === "INPUT" && input.type === "number") {
                let [lo, hi, unit] = hint;
                const units = FIELD_UNITS[name];
                if (units) {
                    if (lo != null) lo = lo / units.scale;
                    if (hi != null) hi = hi / units.scale;
                    unit = units.unit;
                }
                if (lo !== undefined && lo !== null) input.min = lo;
                if (hi !== undefined && hi !== null) input.max = hi;
                input.title = `device range: ${lo} – ${hi} ${unit}`;
            }
        });
    snapshotFormBaseline();
}

// Applied source-spec overrides (verified-reconnect path): seed the Source
// form from what the server actually runs, like the capture form.
function seedSourceForm(config) {
    const source = (config && config.source) || {};
    document.querySelectorAll("#source-settings-form input, #source-settings-form select")
        .forEach((input) => {
            const name = input.dataset.field;
            if (name in source) setFieldValue(input, source[name]);
        });
}

let configRefreshTimer = null;
function scheduleConfigRefresh() {
    if (configRefreshTimer) return;
    configRefreshTimer = setTimeout(async () => {
        configRefreshTimer = null;
        try {
            const config = await fetchConfig();
            seedStaticControls(config);
            seedSourceForm(config);
            seedCaptureForm(config);   // snapshots the form baseline last
            if (typeof seedAnalysisForm === "function") seedAnalysisForm(config);
        } catch (_) { /* transient — next ack retries */ }
    }, 250);
}

async function loadSchema(seed = null) {
    const resp = await fetch("/schema", { cache: "no-store" });
    if (!resp.ok) throw new Error(`schema HTTP ${resp.status}`);
    const schema = await resp.json();
    let effSeed = seed;
    if (!effSeed) {
        try {
            const config = await fetchConfig();
            effSeed = { captures: [config.capture || {}], source: config.source || {} };
            seedStaticControls(config);
            if (typeof seedAnalysisForm === "function") seedAnalysisForm(config);
        } catch (err) {
            logMsg(`Config load failed (${err.message}); using schema defaults`, "WARN");
            effSeed = {};
        }
    }
    renderSettings(schema, effSeed);
    // The live /config response is only a form seed.  It must not become a
    // hidden sweep overlay: if the page loaded at 1955 MHz and was later tuned
    // to 3700 MHz, applying a gain edit would otherwise merge the stale hidden
    // 1955 MHz center back into the control payload.  Only an explicitly
    // uploaded sweep JSON is allowed to preserve hidden capture/source fields.
    if (seed === null) hiddenSweepSettings = {};
}

document.getElementById("settings-apply").addEventListener("click", () => {
    // Merge the hidden lower-level params from an uploaded sweep JSON under the
    // visible form values (form wins), so uploading a sweep actually seeds them
    // instead of being silently dropped (LV-F6). Form fields the user did NOT
    // change since the last server seed are dropped, so e.g. a center change
    // can never re-submit lo_shift / backend_sample_rate / source fields as a
    // side effect.
    const form = collectSettings();
    for (const group of ["capture", "source"]) {
        for (const key of Object.keys(form[group])) {
            if (key in formBaseline[group] && sameValue(form[group][key], formBaseline[group][key])) {
                delete form[group][key];
            }
        }
    }
    const hiddenCapture = (hiddenSweepSettings.captures && hiddenSweepSettings.captures[0]) || {};
    const hiddenSource  = hiddenSweepSettings.source || {};
    const payload = {
        capture: { ...hiddenCapture, ...form.capture },
        source:  { ...hiddenSource,  ...form.source  },
    };
    if (!Object.keys(payload.capture).length && !Object.keys(payload.source).length) {
        logMsg("Apply: no fields changed — nothing sent");
        return;
    }
    const sentKeys = [...Object.keys(payload.capture), ...Object.keys(payload.source)];
    sendControl(payload);
    logMsg(`Settings sent (${sentKeys.join(", ")})`);
});

document.getElementById("settings-upload").addEventListener("change", async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    try {
        const seed = JSON.parse(await file.text());
        await loadSchema(seed);
        logMsg("Settings JSON loaded");
    } catch (err) {
        logMsg(`Settings JSON failed: ${err.message}`, "ERROR");
    }
});

// ---------------------------------------------------------------------------
// Analysis panel (P2a-6, per-analysis P2b-6) — DAN-mode editors for the striqt
// analysis params. The rendered field set follows the Analysis dropdown
// (spectrogram / PSD / SSB), so the config always targets the shown analysis.
// ---------------------------------------------------------------------------
//
// Free-text by design (the freedom model): values are sent raw as
// {"analysis": {"target": …, …}} and the SERVER snaps knowable constraints
// ("invalid X → using Y" via handleAck) or lets striqt scratch-validate the
// rest before the live stream sees anything. Fields seed from /config and
// re-seed after every ack, so the panel always shows what the server runs.

const SHARED_FREQ_FIELDS = [
    { key: "window", label: "window", ph: "kaiser, 11.88",
      title: "scipy get_window spec: a name (hann, blackmanharris, …) or name, parameter (kaiser, 11.88)" },
    { key: "frequency_resolution", label: "frequency resolution (Hz)", ph: "15238.1",
      title: "Hz per FFT bin — the other view of FFT size; snaps to the nearest legal FFT size" },
    { key: "fractional_overlap", label: "fractional overlap", ph: "13/28",
      title: "fraction of each FFT window shared with its neighbor, e.g. 13/28 or 0.46; snaps to k/nfft" },
    { key: "window_fill", label: "window fill", ph: "15/28",
      title: "fraction of the window filled by the taper (rest zeroed), e.g. 15/28; snaps to k/nfft" },
    { key: "integration_bandwidth", label: "integration bandwidth (Hz)", ph: "auto | none | Hz",
      title: "RMS frequency-bin averaging width: auto (tracks FFT size), none, or Hz (snaps to a multiple of the resolution)" },
    { key: "lo_bandstop", label: "LO bandstop (Hz)", ph: "none | Hz",
      title: "width nulled at DC by striqt: none, or Hz" },
];
const TRIM_FIELD = {
    key: "trim_stopband", label: "trim stopband", checkbox: true,
    title: "trim the frequency axis to the capture analysis_bandwidth (needs a finite analysis_bandwidth)",
};

const ANALYSIS_PANELS = {
    spectrogram: {
        target: "spectrogram", configKey: "analysis",
        badge: "calibrated spectrogram — validated before going live",
        fields: [
            ...SHARED_FREQ_FIELDS,
            { key: "time_aperture", label: "time aperture (s)", ph: "none | s",
              title: "binned RMS averaging along the time axis: none, or seconds (snaps to a multiple of the row hop)" },
            TRIM_FIELD,
        ],
    },
    psd: {
        target: "psd", configKey: "analysis_psd",
        badge: "striqt power_spectral_density — one trace per statistic",
        fields: [
            ...SHARED_FREQ_FIELDS,
            { key: "time_statistic", label: "time statistics", ph: "mean, 0.95, max",
              title: "statistics evaluated along the time axis — names (mean/max/min/rms/median) and/or quantiles in [0,1]; one PSD trace each" },
            TRIM_FIELD,
        ],
    },
    ssb: {
        target: "ssb", configKey: "analysis_ssb",
        badge: "5G SSB burst view — may retune the capture rate onto the symbol grid",
        fields: [
            { key: "subcarrier_spacing", label: "subcarrier spacing (Hz)", ph: "30000",
              title: "3GPP SCS (15000/30000/60000 …); selecting SSB retunes the capture rate onto the 14·scs grid (reported)" },
            { key: "sample_rate", label: "SSB output rate (S/s)", ph: "7680000",
              title: "output rate of the recentered SSB band; cannot exceed the sampled span" },
            { key: "discovery_periodicity", label: "discovery period (s)", ph: "0.02",
              title: "time between synchronization bursts; ≥ one 2 ms burst set and one period must fit the IQ ring" },
            { key: "frequency_offset", label: "frequency offset (Hz)", ph: "0",
              title: "SSB center offset from the capture center; snaps to the subcarrier grid and must keep the band in the span" },
            { key: "max_block_count", label: "max burst sets", ph: "none | count",
              title: "cap on synchronization bursts evaluated per frame, or none" },
            { key: "window", label: "window", ph: "blackmanharris",
              title: "scipy get_window spec for the SSB STFT" },
            { key: "lo_bandstop", label: "LO bandstop (Hz)", ph: "none | Hz",
              title: "width nulled at DC by striqt: none, or Hz" },
        ],
    },
    quicklook: {
        target: null, configKey: null,
        badge: "raw per-bin FFT — no analysis parameters",
        fields: [],
    },
};

let lastConfig = null;      // latest /config payload — seeds panel switches
let renderedPanel = null;   // key into ANALYSIS_PANELS currently in the DOM

function analysisFieldValue(v) {
    if (v === null || v === undefined) return "none";
    if (Array.isArray(v)) return v.join(", ");   // ["kaiser", 11.88] → "kaiser, 11.88"
    return String(v);
}

function renderAnalysisPanel() {
    const key = ANALYSIS_PANELS[analysisMode] ? analysisMode : "spectrogram";
    const panel = ANALYSIS_PANELS[key];
    const form  = document.getElementById("analysis-form");
    const badge = document.getElementById("analysis-badge");
    const apply = document.getElementById("analysis-apply");
    if (!form) return;
    renderedPanel = key;
    if (badge) badge.textContent = panel.badge;
    if (apply) apply.style.display = panel.fields.length ? "" : "none";
    form.textContent = "";
    for (const f of panel.fields) {
        const label = document.createElement("label");
        if (f.title) label.title = f.title;
        const input = document.createElement("input");
        input.dataset.key = f.key;
        if (f.checkbox) {
            label.className = "check";
            input.type = "checkbox";
            label.appendChild(input);
            label.appendChild(document.createTextNode(" " + f.label));
        } else {
            label.textContent = f.label;
            input.type = "text";
            input.placeholder = f.ph || "";
            label.appendChild(input);
        }
        form.appendChild(label);
    }
    seedAnalysisForm(lastConfig);
}

function seedAnalysisForm(config) {
    if (config) lastConfig = config;
    const panel = ANALYSIS_PANELS[renderedPanel];
    if (!panel || !panel.configKey || !lastConfig) return;
    const an = lastConfig[panel.configKey] || {};
    document.querySelectorAll("#analysis-form input").forEach((el) => {
        const key = el.dataset.key;
        if (!(key in an)) return;
        if (el.type === "checkbox") el.checked = Boolean(an[key]);
        else el.value = analysisFieldValue(an[key]);
    });
}

document.getElementById("analysis-apply").addEventListener("click", () => {
    const panel = ANALYSIS_PANELS[renderedPanel];
    if (!panel || !panel.target) return;
    const analysis = { target: panel.target };
    document.querySelectorAll("#analysis-form input").forEach((el) => {
        if (el.type === "checkbox") {
            analysis[el.dataset.key] = el.checked;
        } else if (el.value.trim() !== "") {   // cleared fields are not sent
            analysis[el.dataset.key] = el.value.trim();
        }
    });
    sendControl({ analysis });
    logMsg(`Analysis settings sent (${panel.target})`);
});

renderAnalysisPanel();

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

// Default band to middle 10% of span (reset once we have freq data)
bandLo = -curFs / 1e6 * 0.05;
bandHi =  curFs / 1e6 * 0.05;

// Build the default two-pane layout before the first frame arrives (the
// classic AIR-T view); the first header rebuilds it if the device differs.
ensureChannels([0, 1]);

// Init PSD with placeholder data so layout is in place
freqsMHz = buildFreqsMHz(curCenter, curFs, curBins, absRF, curF0, curStep);
initUplot(freqsMHz);
const psdContainer = document.getElementById("psd-container");
if (typeof ResizeObserver !== "undefined" && psdContainer) {
    new ResizeObserver(fitUplotToContainer).observe(psdContainer);
} else {
    window.addEventListener("resize", fitUplotToContainer);
}
updateSsbOption();
installReadOnlyGuard();
wireAhawiControls();

connect();
loadSchema().catch((err) => logMsg(`Schema load failed: ${err.message}`, "ERROR"));
logMsg("App initialised. Connecting to server…");
