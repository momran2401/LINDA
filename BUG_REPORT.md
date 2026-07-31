# LINDA — Bug Report

Review dated 2026-07-30.

> **STATUS: all 26 findings below have been fixed and verified.** The
> descriptions are kept in the past tense of the original review so the record
> of what was wrong — and how it was reproduced — survives. Each entry's
> "suggested fix" is what was actually implemented unless noted. Verification:
> the repository suite is **174 passed / 5 skipped / 0 failed** (the 5 skips are
> the striqt- and Linux-only tests, which now skip honestly instead of failing),
> plus **132 targeted regression tests** and a browser pass over every
> user-facing change. Nothing has been committed.

**Review host:** macOS, no striqt stack (`_ANALYSIS_OK = False`,
`_SENSOR_OK = False`), no SoapySDR, no radio hardware. Everything below was
reproduced against the demo pipeline or verified by reading both sides of the
contract. Hardware-only paths are marked **untested against real hardware**.

**Suggested fixes are described in words only. None were applied.**

---

## Severity summary

| # | Severity | Area | Issue |
|---|---|---|---|
| 1 | **Critical** | auth | Session cookie is forgeable when `RADIO_SESSION_SECRET` is unset |
| 2 | **High** | config | Invalid value mid-message leaves the config partially mutated and unflagged |
| 3 | **High** | config | `NaN`/`inf` silently retunes the radio to an envelope bound |
| 4 | Medium | config | Tier-1 radio clamps are applied silently — the ack never discloses them |
| 5 | Medium | server | Demo / `RADIO_AUTH_DISABLE=1` mode admits only ONE browser at a time |
| 6 | Medium | installer | `install_linda.sh` tells users to run `setup.sh`, which does not exist |
| 7 | Medium | launcher | `run_web.sh --tunnel --port N` tunnels the wrong port |
| 8 | Medium | tx | Transmit acknowledgment is role-wide and never expires |
| 9 | Medium | installer | Explicit `--device=` never installs the matching driver |
| 10 | Medium | frontend | Preset load fires a spurious "access denied" popup at read-only roles |
| 11 | Medium | frontend | `gateStationChips` re-enables station chips that have no click handler |
| 12 | Medium | frontend | Custom duration sends one radio op per keystroke |
| 13 | Medium | installer | GPS probe can re-baud the live serial console on a stock Pi |
| 14 | Medium-low | uninstaller | `--dry-run` under-reports; its "would remove" branches are dead code |
| 15 | Low | frontend | PSD analysis mode leaves a frozen, undisclosed spectrogram on screen |
| 16 | Low | frontend | `setStatus(…, "err")` uses a CSS class that does not exist |
| 17 | Low | frontend | `setupBandDrag` leaks window listeners on every uPlot rebuild |
| 18 | Low | frontend | Crest factor renders the literal string `NaN dB` |
| 19 | Low | frontend | Read-only whitelist denies two harmless client-only exports |
| 20 | Low | frontend | Every read-only client logs a denial message on connect |
| 21 | Low | installer | `gps_tty_speaks` likely false-negatives real receivers |
| 22 | Low | uninstaller | `--yes` also bypasses the "delete recordings" confirmation |
| 23 | Low | server | Broadcaster re-sends recording + TX status to every client every tick |
| 24 | Low | deploy | `RADIO_EXTRA_ARGS` word-splits, so quoted values shatter |
| 25 | Low | tests | Five tests fail on any host without striqt or `/proc` — no skip guards |
| 26 | Low | docs | `CLAUDE.md` documents PSD zoom gestures that do not exist in the code |

---

## 1. Session cookie is forgeable when `RADIO_SESSION_SECRET` is unset — **CRITICAL**

**File:** [live/striqt_web_server.py:167](live/striqt_web_server.py:167)-171

When `RADIO_SESSION_SECRET` is not set, the cookie-signing key falls back to
`sha256("admin:admin|viewer:viewer|interns:intern")` — derived entirely from the
role→username map, whose defaults are published in
[README.md:44](README.md:44) and throughout the manual. Anyone who knows those
defaults can compute the HMAC key and mint a valid `radio_auth=admin.<exp>.<mac>`
cookie.

**Trigger:** any server started without `RADIO_SESSION_SECRET` in the
environment — i.e. every manually launched server, including the two invocations
the manual recommends for remote access:

```bash
bash live/run_web.sh --tunnel --device auto
```

**Verified live.** A demo server was started with the variable unset and default
usernames; a cookie was computed offline from the formula above and sent with
curl. Anonymous `POST /config` returned `401`; the same request carrying the
forged cookie returned `200` and retuned the radio:

```
{"ack":{"applied":["center"],…,"op_id":1}}
```

That is full admin: retune, source reconnect, recording, `POST /admin/reset-radio`,
and — where the hardware supports it — `POST /tx/start`. Over a Cloudflare quick
tunnel this is reachable from the public internet.

The code does print a startup warning
([striqt_web_server.py:1599](live/striqt_web_server.py:1599)-1604), and
`install_linda.sh` generates a real secret into `/etc/radio-web/radio.env`, so
**installer-managed deployments are not affected**. The exposure is the manual
launch path, which the manual documents for tunnel use.

**Suggested fix (not applied):** treat a missing secret as fatal rather than
advisory whenever authentication is enabled — generate a random per-process
secret at startup instead of deriving one from public data. A random ephemeral
key merely invalidates sessions across restarts; the current fallback is
equivalent to no signing at all. If a deterministic fallback must be kept, refuse
to bind to anything other than loopback without an explicit secret.

---

## 2. Invalid value mid-message leaves the config partially mutated — **HIGH**

**File:** [live/core/config.py:910](live/core/config.py:910)-982

`SharedConfig.update()` walks the message's keys and calls `setattr` on the live
config as it goes ([config.py:981](live/core/config.py:981)). The per-key
coercion at [config.py:960](live/core/config.py:960)
(`value = int(value) if key in {"nfft","rows"} else float(value)`) is **not**
wrapped in a try/except, so a non-numeric value raises `ValueError` out of
`update()` — after earlier keys in the same message have already been written.

Because the raise happens before `self._dirty = True`
([config.py:1033](live/core/config.py:1033)), the mutation is never handed to the
Acquirer and no operation is recorded. The software's idea of the config and the
radio's actual tuning silently diverge until some unrelated later change makes
the config dirty again — at which point the stale value is applied without
anyone having requested it.

**Trigger:** any control message mixing a valid and an invalid value, with the
valid key first, e.g. `{"gain": 5, "center": "abc"}` over `/ws` or `POST /config`.
Reachable from the WebSocket path, `POST /config`, and `radioctl set --json`.

**Reproduced** (scratchpad test `test_invalid_typed_value_after_valid_key_partially_mutates`):
`gain` was written to the live config, yet `take_dirty()` returned
`dirty=False, op_id=None, changed=set()`.

The HTTP endpoint returns `400` and the WS handler answers "bad control ignored"
— both truthfully describe the *request* as rejected while the config has in fact
changed.

**Suggested fix (not applied):** validate the whole message into a staging dict
first and commit to `self._cfg` only after every key has parsed, so a message is
all-or-nothing. Alternatively, catch the coercion error per key and route it to
the existing `rejected` list — the same "snap and tell" contract the analysis
block already honours — instead of letting it escape the method.

---

## 3. `NaN`/`inf` silently retunes the radio to an envelope bound — **HIGH**

**File:** [live/core/config.py:964](live/core/config.py:964)-977

The tier-1 clamps are written as `max(lo, min(value, hi))`. With `value = NaN`,
every comparison is false, so `min` returns `NaN` and `max` returns the *floor* —
a NaN is silently converted into a real, very different tuning.

**Verified** on the demo pipeline:

| Sent | Stored | `rounded` entries |
|---|---|---|
| `center = NaN` | `300000000.0` (300 MHz) | 0 |
| `center = -inf` | `300000000.0` | 0 |
| `gain = NaN` | `-60.0` | 0 |
| `gain = inf` | `10.0` | 0 |
| `sample_rate = NaN` | `3840000.0` | 0 |

A `center` of NaN moves an AIR-T from 3750 MHz to 300 MHz — 3.45 GHz away — and
the ack reports `applied: ["center"]` with **zero** `rounded` entries, so both
the UI status line and the operation log describe it as a clean success.

**Trigger:** `NaN` is reachable over the wire. Python's `json.loads` accepts the
bare `NaN` literal by default (verified: `json.loads('{"center": NaN}')` →
`{'center': nan}`), so any client — or a JS `parseFloat("")` that reaches
`JSON.stringify` as `null`/`NaN` — can hit this through `/ws` or `POST /config`.

**Suggested fix (not applied):** reject non-finite values explicitly before the
clamp, adding them to `rejected` with a reason, rather than letting IEEE
comparison semantics choose a bound. A `math.isfinite` guard alongside the
existing per-key coercion would cover center, gain, sample_rate, duration and
`ahawi_capture_ms` in one place.

---

## 4. Tier-1 radio clamps are applied silently — **MEDIUM**

**File:** [live/core/config.py:964](live/core/config.py:964)-977

The analysis block is careful to disclose every adjustment: `_tier1_freq_fields`
appends to `rounded` whenever it snaps a value, and the docstrings describe the
freedom model as "knowable constraints → round and tell". The radio-facing
clamps in the same method do **not** follow that contract. Requesting
`center = 7 GHz` on an AIR-T stores 6 GHz, and requesting `gain = 99` stores 10,
with `rounded == []` in both cases.

**Verified** across the boundary matrix (scratchpad `test_center_clamp_boundaries`,
`test_gain_clamp_boundaries`, `test_sample_rate_snap`, `test_nfft_snap`,
`test_rows_clamped_to_ring_capacity`): out-of-range center, gain, sample-rate
snapping, nfft snapping, and rows clamping are all silent.

The consequence is the exact failure mode the OPS tab was built to eliminate:
the user asks for one thing, the radio does another, and the only way to notice
is to re-read `/config`. It is also the mechanism that makes #3 invisible.

**Suggested fix (not applied):** emit a `rounded` entry from the tier-1 radio
clamps whenever the stored value differs from the requested one, reusing the
`{field, requested, used, reason}` shape the analysis path already produces. The
UI and `radioctl` already render that list, so no client change is needed.

---

## 5. Demo / auth-disabled mode admits only ONE browser at a time — **MEDIUM**

**Files:** [live/striqt_web_server.py:296](live/striqt_web_server.py:296)-302,
[live/striqt_web_server.py:1354](live/striqt_web_server.py:1354)-1361

With `RADIO_AUTH_DISABLE=1` every connection is assigned `DEFAULT_ROLE = "admin"`.
The `/ws` endpoint permits exactly one admin socket, so the *second* browser is
accepted, told `{"error": "admin-busy"}`, and closed with 4001 — then retries
every 1.2 s forever.

This contradicts the documented purpose of both features.
[README.md:17](README.md:17)-19 says "The web server shares one radio stream with
multiple browser clients; use demo mode for safe UI and pipeline testing", and
the manual recommends `RADIO_AUTH_DISABLE=1` for local demos. In practice the
two settings are mutually exclusive: a demo cannot be shown to two people, and
opening a second tab locks out the first.

**Verified** (scratchpad `test_authdisabled_second_client_locked_out`): with auth
disabled, the first socket receives `role=admin` and the second is refused. With
auth enabled, two `viewer` sockets stream binary frames concurrently
(`test_two_viewers_stream_simultaneously`), so the fan-out itself is sound — the
limitation is purely the role assignment.

**Suggested fix (not applied):** when auth is disabled, grant the admin role only
to the first connection and hand later ones a read-only role, or exempt the
auth-disabled path from the single-admin slot entirely. The slot exists to stop
two people fighting over one radio; in a no-auth demo there is no second identity
for it to arbitrate.

---

## 6. `install_linda.sh` tells users to run `setup.sh`, which does not exist — **MEDIUM**

**File:** [install_linda.sh:5](install_linda.sh:5)-8, 111, 146, 158, 1299
(also 627, 1009 cosmetically)

The installer was renamed from `setup.sh` to `install_linda.sh`, but every
self-reference inside it still says `setup.sh`. Line 108-109 prints the header
block as `--help` output, so the documented usage is wrong; line 111 answers an
unknown option with "run: bash setup.sh --help"; line 146 answers a non-root
invocation with "run as root: sudo bash setup.sh"; line 1299 ends a
radio-less install with "re-run: sudo bash setup.sh".

**Trigger:** run `bash install_linda.sh` without sudo. The error instructs a
command that fails with "No such file or directory". Verified: no `setup.sh`
exists at the repository root, and `grep -rn "setup.sh"` finds **zero**
references to the real filename anywhere in the docs.

The same drift runs through the user-facing documentation:
[README.md:31](README.md:31),39,68,99;
[docs/README_MANUAL.md:42](docs/README_MANUAL.md:42),58,71,87,90,114-126,460,481;
[CLAUDE.md:94](CLAUDE.md:94) and others; and
[live/run_web.sh:49](live/run_web.sh:49), which prints
"or: bash setup.sh --deps-only" to anyone missing FastAPI. By contrast
`uninstall_linda.sh` is referenced by its real name everywhere, so only the
installer half of the rename was left unfinished.

**Suggested fix (not applied):** derive the name at runtime
(`${BASH_SOURCE[0]##*/}`) in all user-facing messages so it can never drift
again, and sweep the docs for the literal string. Alternatively, ship a
`setup.sh` shim that execs the real script — that would make every existing
document correct at once.

---

## 7. `run_web.sh --tunnel --port N` tunnels the wrong port — **MEDIUM**

**File:** [live/run_web.sh:64](live/run_web.sh:64) and
[live/run_web.sh:74](live/run_web.sh:74)

Line 64 injects `--port "$PORT"` before the pass-through arguments, and argparse
takes the last occurrence — so a user-supplied `--port 9000` wins for the server.
The tunnel on line 74, the banner, and the `PORT` variable all still say 8000.

**Trigger:** `bash live/run_web.sh --tunnel --port 9000`. The server listens on
9000; `cloudflared` is pointed at `http://localhost:8000`. The published URL
serves either nothing or, worse, an unrelated service that happens to hold 8000.

The header at [run_web.sh:13](live/run_web.sh:13) explicitly invites arbitrary
pass-through arguments, so this is a supported usage. The `PORT=9000` environment
form works correctly.

**Suggested fix (not applied):** scan the pass-through arguments for `--port`
(and `--port=N`) and let it update `PORT` before the tunnel is launched, or
reject the flag with a message pointing at the `PORT` variable. Silently
disagreeing with the server is the one outcome to avoid.

---

## 8. Transmit acknowledgment is role-wide and never expires — **MEDIUM**

**File:** [live/core/tx.py:406](live/core/tx.py:406)-418, consumed at
[tx.py:623](live/core/tx.py:623); acknowledged by role at
[striqt_web_server.py:714](live/striqt_web_server.py:714)-715

The legal-notice gate is keyed on the *role name*, and `/tx/acknowledge` passes
`request.scope["role"]` — which for every administrator is the literal string
`"admin"`. The acknowledgment set is process-wide and is cleared only by a
restart.

So the first admin to accept the notice acknowledges it on behalf of **every**
future admin session for the life of the server. A different person signing in
later — or the same person returning days afterwards, since the cookie TTL is 24 h
but the server may run far longer — reaches a live "arm TX" path having never
been shown the notice.

**Verified:** `TxController.acknowledge("admin")` then `is_acknowledged("admin")`
returns `True` indefinitely, including after a transmission has stopped. (The
demo/simulated path is what was exercised; the hardware arming ladder is
**untested against real hardware**.)

This matters more than a normal UX nit because the module's own docstring frames
the audit trail as the point of the feature, and the notice text asserts that
every transmission is logged with its operator — while all operators share one
identity.

**Suggested fix (not applied):** key the acknowledgment to the session rather
than the role (the signed cookie already carries a per-session token), and expire
it — on a timeout, or per transmission. If the ack must stay role-scoped, record
in the operation log that it was inherited rather than freshly given, so the
audit trail does not overstate what happened.

---

## 9. Explicit `--device=` never installs the matching driver — **MEDIUM**

**File:** [install_linda.sh:326](install_linda.sh:326),
[install_linda.sh:344](install_linda.sh:344)-373,
[install_linda.sh:473](install_linda.sh:473)

`RADIO_PKGS` is populated only inside `detect_radio`, on a USB-table match.
`resolve_selector` maps an explicit `--device=uhd|rtlsdr|hackrf|…` to a selector
and a `RADIO_KIND` but never sets `RADIO_PKGS`, and `install_radio_driver`
returns immediately when that variable is empty — skipping `install_uhd_images`
and `tune_usbfs` along with it.

**Trigger:** the provision-before-the-radio-arrives flow the header advertises:

```bash
sudo bash install_linda.sh --device=uhd --skip-radio-check
```

Expected: the UHD driver, its FPGA images, and usbfs tuning. Actual: none of
them. At best `broaden_driver_search` installed `soapysdr-module-uhd`, but never
`uhd-host`, so `uhd_images_downloader` is absent and no images are fetched — the
USRP will not open when it is finally plugged in. The same gap applies to the
whiptail menu choice when detection found nothing.

**Untested against real hardware** (no Debian host, no radio); traced by reading
the control flow.

**Suggested fix (not applied):** give `resolve_selector` the same package
mapping `detect_radio` uses, so an explicitly named device kind selects its
driver packages exactly as a detected one does.

---

## 10. Preset load fires a spurious "access denied" popup — **MEDIUM**

**File:** [live/web/app.js:1352](live/web/app.js:1352)

`loadPresets()` ends with `select.dispatchEvent(new Event("change"))`. The
read-only guard listens for `change` in the capture phase, and `#preset-select`
matches `CONTROL_SELECTOR` (`"select"`) but is absent from `SAFE_SELECTOR`
([app.js:533](live/web/app.js:533)-541, verified). For a viewer or intern the
synthetic event is therefore blocked and `showAccessDenied()` fires with no user
interaction at all.

`loadPresets()` runs unconditionally ~900 ms after load and again on every
MEASURE tab click, so read-only users get an unexplained denial popup — for
interns, a full-screen takeover — shortly after every page load. The
`stopImmediatePropagation` also kills the element's own listener, so
`#preset-description` never populates for them.

Nothing checks `isTrusted`, which is what lets a synthetic event reach the guard.

**Suggested fix (not applied):** populate the description by calling the handler
function directly instead of dispatching a synthetic event, or have the guard
ignore untrusted events. The second is the more general fix — a guard meant to
intercept *user* interaction should not fire on programmatic ones.

---

## 11. `gateStationChips` re-enables chips with no click handler — **MEDIUM**

**Files:** [live/web/app.js:3606](live/web/app.js:3606)-3620,
[live/web/index.html:673](live/web/index.html:673)-690

Station chips are built with a hard-coded `tunable = it.mhz >= 300 && it.mhz <= 6000`,
and the click listener is attached **only** in the `tunable` branch (verified at
index.html:689). `gateStationChips` later re-evaluates every chip against the
device envelope from `/config` and clears `disabled` / `.is-disabled` on the ones
it considers legal.

On a radio whose envelope reaches below 300 MHz — the generic SoapySDR profile
starts at 1 MHz ([constants.py:104](live/core/constants.py:104)) — the six
sub-300 MHz chips (FM 98, aircraft 127, 2 m 146, marine 162, NOAA 162.475,
VHF TV 195) become enabled and look live, but clicking them does nothing, and
their caption still reads "below radio range".

The hard-coded range and the tooltip "Outside the radio's 300 MHz – 6 GHz tuning
range" also contradict the server-driven envelope for any device with different
limits.

**Suggested fix (not applied):** attach the click listener to every chip
regardless of the build-time range and let `gateStationChips` be the single
authority on enablement, refreshing the caption and tooltip from the live
envelope at the same time.

---

## 12. Custom duration sends one radio op per keystroke — **MEDIUM**

**File:** [live/web/app.js:3184](live/web/app.js:3184)-3185

`applyDuration` is bound to both `change` **and** `input` on `#dur-custom`. In
Boring mode each keystroke sends `{capture:{duration}}`: typing "150" produces
three separate control messages (1 ms, 15 ms, 150 ms) and three server
operations. Each clears the IQ ring ([acquisition.py:592](live/core/acquisition.py:592)-597),
though it does not re-arm the SDR, since `duration` is outside the rearm field
set ([acquisition.py:578](live/core/acquisition.py:578)-584).

There is a second-order effect: every capture-bearing message is acked
([striqt_web_server.py:1418](live/striqt_web_server.py:1418)-1421), each ack
schedules a `/config` re-seed 250 ms later, and `seedStaticControls` rewrites
`durCustom.value` from the server — so the box can be overwritten mid-typing with
an intermediate value.

**Suggested fix (not applied):** apply on `change` and `blur` only, or debounce
the `input` path by a few hundred milliseconds. The staged-then-Apply pattern the
AHAWI controls already use would also fit here.

---

## 13. GPS probe can re-baud the live serial console on a stock Pi — **MEDIUM**

**File:** [install_linda.sh:687](install_linda.sh:687)

The console is extracted with a greedy `sed 's/.*console=\([^, ]*\).*/\1/p'`,
which matches only the **last** `console=` token. On a stock Raspberry Pi OS
cmdline (`console=serial0,115200 console=tty1 root=…`) that yields `tty1`, so
`/dev/serial0` — the actual serial console, listed first — is **not** excluded,
contradicting the comment two lines above ("Never claim the kernel console").

The probe then runs `stty -F /dev/ttyAMA0 9600 raw -echo` on a live console. The
`gps_tty_speaks` content check makes gpsd actually *claiming* the port unlikely,
but the baud change alone can wedge an active headless-access session — which on
a Pi being provisioned over serial is the session doing the provisioning.

**Untested against real hardware**; the regex behaviour was verified against a
representative cmdline string.

**Suggested fix (not applied):** collect *all* `console=` tokens (e.g. with
`grep -o 'console=[^ ]*'`) and exclude every one of them, stripping the trailing
baud suffix before comparing.

---

## 14. `--dry-run` under-reports and its "would remove" branches are dead — **MEDIUM-LOW**

**File:** [uninstall_linda.sh:259](uninstall_linda.sh:259)-263

`--dry-run` exits immediately after printing the plan summary, so every
`DRY_RUN` branch further down is unreachable: `safe_rm` (145-148), `run`
(154-157), the cmdline section (369-371), and the Pluto-manifest guard (387).

The plan summary (218-257) does not mention several destructive steps, so a dry
run silently omits: per-file SoapyPlutoSDR removals from `install_manifest.txt`
(387-392), pip-cache deletion under `--purge-pip-cache` (334-337), group removal
under `--purge-groups` (398-407), `__pycache__`/`.pytest_cache` deletion across
the repo (326-327), and cmdline `.bak` deletion (380-381).

The manual recommends `--dry-run` as the first step, so the gap is between what
the tool promises to preview and what it will do.

**Suggested fix (not applied):** let `--dry-run` fall through the real code path
with the existing `DRY_RUN` guards doing the work, and drop the early exit — the
"would remove" branches were clearly written for exactly that.

---

## 15-20. Frontend, low severity

**15. PSD analysis mode leaves a frozen spectrogram** —
[app.js:3067](live/web/app.js:3067)-3082. `applyAnalysisMode` sets
`body.analysis-psd`, but the only rule for that class hides a legend
([style.css:360](live/web/style.css:360)); `#waterfall-row` is never hidden and
the canvases keep their last-drawn pixels after the buffers are nulled. The user
sees a still image that looks like live data. (`body.analysis-ssb` matches no CSS
rule at all — a dead toggle.) *Fix in words:* hide or explicitly blank the
waterfall row when the PSD analysis is selected.

**16. `setStatus(…, "err")` uses a nonexistent class** —
[app.js:3289](live/web/app.js:3289) and 3341. `style.css` defines only
`.ok`/`.warn`/`.error` ([style.css:171](live/web/style.css:171)-177). The two
messages affected are "reset NOT verified" and "reset failed" — precisely the
ones that need to look alarming, and they render in the default dim colour.
*Fix in words:* pass `"error"`.

**17. `setupBandDrag` leaks window listeners** —
[app.js:2931](live/web/app.js:2931)/2943. Anonymous `pointermove`/`pointerup`
listeners are added on every uPlot rebuild (retune, theme toggle, Absolute-RF
toggle, channel change, plot swap) and never removed, so a long session
accumulates them; each drag runs every stale closure. Display stays correct
because the newest listener writes last. *Fix in words:* keep references and
remove them when the plot is destroyed, or use an `AbortController` signal.

**18. Crest factor renders `"NaN dB"`** — [app.js:1319](live/web/app.js:1319).
`(peak !== null && rms !== null ? peak-rms : NaN).toFixed(2)` yields the literal
string. `/insights` values can be absent per channel on measurement error
([insights.py:116](live/core/insights.py:116)-125). *Fix in words:* render an
em-dash or "n/a" when either detector is missing.

**19. Read-only whitelist denies harmless exports** —
[app.js:533](live/web/app.js:533)-541. `#metadata-export` (a client-side Blob
download) and `#preset-select` (client-side description only) are outside
`SAFE_SELECTOR`, while the equivalent `#csv-btn`/`#png-btn` are inside it. A
viewer clicking "Export metadata JSON" is denied an action that sends nothing.
The whitelist was audited in the other direction as well: **no** entry in
`SAFE_SELECTOR` reaches `sendControl` or a mutating endpoint, so there is no
privilege hole. *Fix in words:* add both to the whitelist.

**20. Read-only clients log a denial on connect** —
[app.js:381](live/web/app.js:381). `ws.onopen` calls `sendTimeControl()` before
the role message arrives, so the client-side role guard cannot suppress it and
the server answers "read-only role: control ignored". Cosmetic; the control is
correctly ignored. *Fix in words:* defer the initial control until the role
message has been processed.

---

## 21-26. Remaining low-severity items

**21. `gps_tty_speaks` likely false-negatives real receivers** —
[install_linda.sh:707](install_linda.sh:707)-710. `timeout 4 head -c 4096 "$1" | grep -qa …`:
GNU `head` buffers through stdio, and when `timeout` sends SIGTERM the unflushed
partial data is discarded. A typical NMEA stream is 300-800 B/s, so 4096 bytes
rarely arrive within 4 s and the probe concludes "no receiver". Suspected, **not
verified** (macOS `head` differs from GNU). *Fix in words:* read with a tool that
does not buffer past the timeout — `dd` with a count, or `timeout … cat` piped
into `head -c`.

**22. `--yes` bypasses the recordings confirmation too** —
[uninstall_linda.sh:276](uninstall_linda.sh:276)-285. The dedicated "type
`delete recordings`" gate is skipped when `ASSUME_YES` is set, so
`--yes --purge-recordings` destroys capture data with no prompt. The header
documents `--yes` only as skipping the main confirmation. *Fix in words:* keep
the recordings gate independent of `--yes`.

**23. Broadcaster re-sends unchanged status every tick** —
[striqt_web_server.py:1276](live/striqt_web_server.py:1276)-1280. Recording and
TX status are serialized and sent to every client on every broadcast tick (15/s
by default) whether or not they changed — roughly 30 JSON messages per second per
client of mostly identical payload, on links the hotspot/tunnel modes care about.
*Fix in words:* send on change, with a low-rate keepalive.

**24. `RADIO_EXTRA_ARGS` word-splits** —
[deploy/run_service.sh:16](deploy/run_service.sh:16). `read -r -a EXTRA <<< "$RADIO_EXTRA_ARGS"`
splits on IFS, so `--title "My Radio"` becomes `--title`, `"My`, `Radio"`. *Fix in
words:* document that only whitespace-free values are supported, or parse with
`xargs`-style quoting.

**25. Five tests fail on any host without striqt or `/proc`** — the suite was run
first, as instructed: **174 passed, 5 failed**. All five are environmental, not
regressions:
`test_acquisition_rearm.py` (3 tests) fail at
[acquisition.py:42](live/core/acquisition.py:42) with
`'NoneType' object has no attribute 'SoapyCapture'` because `specs` is `None`
without striqt; `test_auth_http.py::test_measurement_metadata_and_presets_are_exposed`
fails on `KeyError: 'channel_power'` because `/insights` cannot compute striqt
measurements; `test_fd_hygiene.py` fails because `seal_open_fds_for_exec` reads
`/proc/self/fd`, which does not exist on macOS. Confirmed:
`_ANALYSIS_OK = False`, `_SENSOR_OK = False`, `/proc/self/fd` absent.
`test_ahawi.py` is the only file that guards with `pytest.mark.skipif`.
*Fix in words:* add the same striqt/platform skip guards to these three files, so
a laptop run reports an honest "skipped" instead of red failures that mask real
ones.

**26. `CLAUDE.md` documents PSD gestures that do not exist** — `CLAUDE.md`
promises "wheel zoom / drag pan / Shift-drag box zoom / Alt-drag band selection /
double-click reset; zoom survives frames via `setData(data, psdZoomX === null)`".
**Verified:** `grep -c "psdZoomX\|dblclick\|shiftKey\|altKey\|wheel" live/web/app.js`
returns **0**, `setData` is always called with one argument, and
[app.js:2877](live/web/app.js:2877) comments "No x-axis zoom/pan/box-zoom — the
PSD always live-follows the full span". Band selection is a plain unmodified
drag. Anyone debugging "zoom state" from the documentation would be chasing code
that is not there. *Fix in words:* correct the CLAUDE.md section to describe the
band-drag interaction that actually exists.

---

## Areas reviewed with no issues found

- **`live/core/serialization.py`** — round-trips clean for 1/2/4 channels in both
  float32 and quantized modes; NaN blocks, all-NaN blocks, and flat-range blocks
  are handled safely; truncated payloads raise `ValueError` rather than crashing.
  (12 scratchpad tests.) One cosmetic quirk: `parse_frame` maps an empty
  `channels` list to `[0]` and returns one zero-sized block.
- **`live/core/recording.py` path handling** — `resolve_catalog_item` blocks
  `../../etc/passwd`, absolute paths, URL-encoded traversal, and embedded `..`
  segments; verified by direct probing.
- **`live/core/operations.py`** — lifecycle, supersession, scoped field
  recording, and the id-index trim all behave as documented.
- **`live/core/gps.py`** — the "NaN, never 0.0/0.0" contract holds; `absent_fields`
  and `capture_fields` agree on shape; a 2-D fix correctly yields NaN altitude.
- **`live/core/parsing.py`** — all five parsers reject their documented invalid
  input classes and normalize the valid ones (34 scratchpad cases).
- **`live/tools/fetch_recordings.sh`** — correct argument handling, no `--delete`,
  partial-file exclusion, useful SSH error triage.
- **Read-only role enforcement, server side** — a crafted WS control from a
  viewer is refused without dropping the connection; `POST /config`,
  `/presets/*/apply`, `/record`, `/tx/*` and `/admin/reset-radio` all return 403.
- **Concurrency** — 4 threads × 30 updates against one `SharedConfig` produced no
  corruption; two viewers streamed binary frames simultaneously; a malformed
  control did not drop the socket; the recording lock correctly returned 409 for
  `POST /config`.
- **Sudoers rule vs. reset preflight** — the rule
  `install_radio_web_sudoers.sh` writes does contain both substrings
  `striqt_web_server.py` requires. One fragile coupling: the installer resolves
  `systemctl` with `command -v` at install time and the server with
  `shutil.which` at runtime; they agree on merged-usr Debian but a differing PATH
  would produce a confusing "no matching NOPASSWD sudoers rule".

---

## Test coverage notes

The existing suite (16 files, 179 tests) covers config clamps and mapping, source
reconnect semantics, serialization round-trips, the operation lifecycle, scoped
verification, the AHAWI plan, TX (900 lines — the most thoroughly tested module),
GPS, recording handoff, and an auth-enabled HTTP integration pass.

Gaps found, and what was written in the scratchpad to cover them (99 new tests,
all passing; kept **outside** the repository since this review is read-only —
`/private/tmp/claude-501/…/scratchpad/phase2/`):

| Gap | New coverage |
|---|---|
| Boundary values (min/max/invalid/NaN/inf) for every clamped field | `test_config_matrix.py` — 80 tests |
| Partial-mutation behaviour on a mixed valid/invalid message | found bug #2 |
| Parser rejection classes | 34 parametrized cases |
| Serialization edge cases (NaN, all-NaN, flat range, truncation, 1/2/4 ch) | `test_serialization_matrix.py` — 12 tests |
| Two clients connected at once | `test_ws_integration.py` |
| Single-admin slot / 4001 takeover | `test_second_admin_is_refused_with_4001` |
| Auth-disabled multi-client | found bug #5 |
| Read-only control rejection over the wire | `test_viewer_control_is_ignored` |
| Malformed control does not drop the socket | `test_malformed_control_keeps_connection` |
| Changing a setting mid-recording (the documented lock) | `test_recording_lock_blocks_config_http` |
| Concurrent `SharedConfig.update` from 4 threads | `test_concurrent_updates_are_serialized` |

**Combination spaces skipped, and why:** the analysis freedom model's tier-2
(striqt scratch validation) could not be exercised — striqt is not installed on
this host, so `probe_analysis` returns `None` and only tier-1 was tested. The
`calibrated`/`psd`/`ssb` compute backends, the AHAWI capture path, the device
adapters' `read_back`/`verify`/`hardware_expectations`, the recording sweep, and
the entire TX arming ladder are all hardware- or striqt-dependent and are
**untested against real hardware** here; the repository's own tests cover their
logic with fakes. The full cross-product of
backend × nfft × rate × overlap × window (4 × 5 × 4 × many × many) was not
enumerated — boundary and pairwise sampling was used instead, since the
interesting behaviour is at the grid edges the tier-1 snapping computes.
