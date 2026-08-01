# Project Retrospective — **Linda**
### Live RF Spectrogram / PSD Viewer · NIST Communications Technology Laboratory · SURF 2026

| | |
|---|---|
| **Project names (chronological)** | `NIST-Omran` → `NIST-Omran-Sandbox` (parallel sandbox) → **LINDA** (*Live IQ Navigation and Display Application*) |
| **Lab** | Communications Technology Laboratory, Spectrum Technology and Research, Division 675 (Boulder, CO) |
| **Mentors** | **Aric Sanders** — primary mentor, measurement framing, hardware host, lead on the URSI 2026 paper the tooling supports · **Dan Kuester** — author of `striqt`, technical authority on acquisition architecture |
| **Hardware** | Deepwave AIR-T **AIR8201-B** on host `radio05` (Jetson, aarch64, Ubuntu 18.04, CUDA 10.2) — 30 MHz–6 GHz, 2×2 MIMO, 125 MSPS. Second platform: Raspberry Pi 5 (Debian Trixie) + ADALM-Pluto |
| **RF paths** | RX0 omnidirectional reference · RX1 directional 32-element linear dipole array |
| **Core stack** | `striqt` (NIST DSP/acquisition library, on SoapySDR) → FastAPI/uvicorn + WebSocket → uPlot browser frontend, behind a Cloudflare named tunnel |
| **Deployment** | `https://radio.mustafaomran.com` (production, port 8000, `radio-web.service`) · sandbox on port 8001 (`radio-web-sandbox.service`, demo mode) |
| **Repos** | `github.com/momran2401/NIST-Omran` (production) · `NIST-Omran-Sandbox` · Mac clones under `~/merge/` |
| **Period covered** | late May 2026 → 31 July 2026 |

> **Scope note.** Everything below is drawn only from sessions touching this project. Unrelated threads in the same chat history — NSF CAREER proposal work, UHPC/mill-scale manuscript reviews, the lab coordinator search, Port of Corpus Christi work, insurance and scholarship items — were filtered out entirely.

---

## 1. The Timeline

### Phase 0 — Onboarding and ground truth *(late May – early June)*

The project began before any code, with an extended concept-building arc: radio and DSP fundamentals, Shannon's information theory, and a medium-depth walkthrough of the `striqt` codebase. The teaching style was story-first — every concept anchored to something already experienced (earlier PlutoSDR captures, a hand-built waterfall, the WWV signal).

The single most consequential decision of the whole project was made here, and it was **yours, not a mentor's**: *nothing gets invented or assumed — every instruction must be grounded in a document, a manual, the `striqt` source, or output read directly off the device. Gaps get flagged as questions for Dan, not filled with guesses.* That rule governed every hardware and deployment sequence that followed.

The first artifact was therefore `airt_diagnostic.py` — deliberately **read-only**: no transmit, no arming, no streaming. It dumped the radio's real capabilities and the installed `striqt` API surface, establishing ground truth that was still being cited two months later:

- AIR8201-B, 30 MHz–6 GHz, 2×2 MIMO, 125 MSPS
- SoapySDR 0.8.1 / SoapyAIRT 1.0.0
- **pixi**, not conda, as the environment manager: `/home/sensor/aggregate-directivity-acquisition/.pixi/envs/default/`, Python 3.9.23, `cupy-cuda102`
- Existing hardware configs at `sensors/air8201b/`
- `calibration.yaml` flagged as **unsafe to run with TX ports unterminated**

The phase closed on the first successful end-to-end capture on the AIR-T, with a saved zarr file as the next inspection target.

### Phase 1 — First signals and the client/server era *(early–mid June)*

A pure-SoapySDR spectrum sweep answered "where are the signals" — deliberately *not* using `striqt`, to avoid guessing at a signature hidden behind a ParamSpec.

Architecture v1 followed: an acquisition **server on the AIR-T** streaming to a **PyQt5/pyqtgraph viewer on the Mac** over direct Ethernet (`192.168.50.1` ↔ `192.168.50.2`, port 5005). The canonical pair was `airt_live_server_full.py` + `live_viewer_full.py`. This phase produced two lasting things — the ring-buffer redesign and a very long networking fight — plus:

- A full **GitHub README** (architecture, per-side requirements, network setup, usage, config tables, UI reference, wire protocol, performance notes, troubleshooting), with Acknowledgments and License left as explicit placeholders
- Dan's **"Replace (full window)" mode** — capture length equal to history length so the spectrogram refreshes wholesale instead of scrolling — shipped as a **default-OFF checkbox**, honoring his standing rule that *new capability goes in as a toggle, never as a rewrite*
- A `rows` field added to the wire protocol's control channel
- A CLI host argument on the viewer (`python3 live_viewer_full.py <ip> [--port N]`) so switching networks needed no file edits
- Wi-Fi bring-up: `wlan0` onto NIST-Guest via `nmcli`, DHCP address `132.163.141.154/22`; Mac on the same `/22`; 0% packet loss at ~5 ms; `nc -vz 132.163.141.154 5005` confirmed port 5005 unblocked on the NIST network

### Phase 2 — Consolidation *(mid-June, ~17 June)*

The client/server split was collapsed into a single file — `striqt_standalone.py` (earlier `airt_live_standalone.py`) — running entirely on the Deepwave with **no TCP or socket code at all**. `striqt` now owned both halves of the pipeline:

- **Acquisition:** `Airstack1Source.from_spec()` → `open_stream()` → `arm_spec()` → `_read_stream()` in a continuous loop
- **Computation:** `striqt.analysis.evaluate_spectrogram(..., dB=True, dtype='float32')`

PyQt5 came in via `pixi add pyqt pyqtgraph`. The viewer code was reduced to visualization only.

### Phase 2.5 — Second platform: Pi 5 + PlutoSDR *(23 June)*

A parallel porting track opened to prove the tool wasn't welded to one radio. A read-only Claude Code audit enumerated every AIR-T-specific line (device strings, SoapySDR driver names, `striqt` calls, channel configs, sample rates, gains) and ranked migration options from one-line change → config file → runtime auto-detect. Three files landed: `live/pluto_standalone.py`, `setup.sh`, and a PlutoSDR README section with a hardware-differences table (gain range, sample rate, master clock). The open design question — single-RX Pluto vs. the AIR-T's dual-channel GUI — was deliberately left for a decision rather than guessed at.

### Phase 3 — From desktop to browser *(late June – 1 July)*

The Qt viewer was superseded by `live/striqt_web_server.py` (FastAPI/uvicorn), serving a static frontend (`index.html`, `app.js`, `style.css`, `colormap.js`, uPlot via CDN) and streaming **binary spectrogram frames over a `/ws` WebSocket**.

Infrastructure came with it: DNS for `mustafaomran.com` migrated **IONOS → Cloudflare** (the named-tunnel flow is blocked without the zone on Cloudflare — this was hit directly, with an empty zone list at `cloudflared tunnel login`); a **named tunnel** (`radio-viewer`) replaced throwaway `*.trycloudflare.com` URLs; **Tailscale** was added separately for private admin reach (`100.91.57.48`).

A governance decision belongs here too: before wiring a permanent public address to a federal lab's RF sensor, you took the "is it OK that this runs when I'm not there" question to Dan and Aric and got the nod. Both mentors also approved taking the radio on demand under the single-holder rule.

### Phase 4 — Always-on and access-controlled *(1–2 July)*

This is where the viewer stopped being a script you babysat and became a service:

- **HTTP Basic Auth** via a pure-ASGI `BasicAuthMiddleware` gating all HTTP *and* WebSocket requests, reading `RADIO_USER`/`RADIO_PASS` from the environment, using `secrets.compare_digest` for constant-time comparison, and rejecting unauthenticated upgrades **before `accept()`** so the stream can't be pulled around the login. Committed as `bbb7605` "Auth Addition."
- Cloudflare Access with email one-time PINs was evaluated and **rejected** — the dashboard itself needs SSO/PIV that doesn't work from everywhere. Single shared credential chosen with the tradeoffs stated openly (no per-person revocation).
- **Signed session cookie** issued after login, carried by the WebSocket — the fix for iOS.
- **No-cache middleware** on the static files.
- **Two systemd services** (`radio-web` + `cloudflared`), both `Restart=always`, with credentials in a root-only `/etc/radio-web.env` via `EnvironmentFile` — chosen over inline `Environment=` lines specifically so `systemctl cat`/`status` never surfaces the password, and never in the repo.
- **Full frontend redesign:** PRO/NOOB mode toggle renamed **DAN MODE / ARIC MODE**, mobile-responsive layout, custom dark theme, an ARIC-mode **station tuner** (one-click presets for Wi-Fi, cellular bands, GPS, FM), an amber "For authorized NIST users only" access banner, and a footer credit.
- In parallel, a feasibility study for Dan's **schema-driven capture-settings editor** (from his gist + a NiceGUI `json_editor` reference) produced `docs/CAPTURE_EDITOR_FEASIBILITY.md`. Findings: `striqt`'s `json_schema()` genuinely exists and emits types, ranges, enums, and required fields; all three AIR models share the same capture spec; NiceGUI 3.x needs Python ≥3.10 against `radio05`'s 3.9; the schema drops units and carries one `Infinity` default needing a hand-authored label map (~10 fields).

### Phase 5 — Audit, correctness, and the freedom model *(7–8 July)*

A **sandbox-first deployment workflow** was built from scratch: `NIST-Omran-Sandbox`, port 8001, `radio-web-sandbox.service` in demo mode (synthetic signals, no hardware needed), SSH keys, tunnel management. Critically, the sandbox's different behavior lives in its **systemd unit**, not in divergent source — so sandbox and production stay diffable.

A read-only audit produced a **960-line `AUDIT_REPORT.md`**, and **22+ fixes (the LV-\* series)** were executed against it. Then:

- **UI consolidation, Phase 1** — deleted the redundant "Radio (AIR-T)" bar, wired four rendered-but-ignored capture fields, replaced "Window ms" with a real Duration control, removed the row cap, fixed the inverted LO-null checkbox. One change = one commit (`P1-<n>: <title>`), local until reviewed, with `py_compile` + `node --check` gates and a `FIXLOG.md` entry carrying a `[demo]`/`[hardware]` verify step per change.
- **Phase 2a/2b analysis-block wiring** — the **"freedom model"**: snap-and-tell + scratch-validate + a compute backstop, so a bad analysis parameter can never freeze the live feed.
- **Phase 3 planning** — a multi-SDR assessment for adding PlutoSDR alongside the Deepwave.

### Phase 6 — Physical move and promotion *(8 July)*

The radio was physically moved off Aric's site, which produced a **Cloudflare Error 1033** (Argo Tunnel failure) until the new location's internet was fixed. The "Multi-Device Support" batch (`striqt_web_server.py`, `app.js`, `index.html`, `style.css`, `run_web.sh`; sandbox `e21626b`→`8d6048c`) was promoted to production as commit `2288bcc` — executed at the radio's physical terminal rather than over SSH, a new working pattern. The push failed on HTTPS password auth; an ed25519 SSH-key migration was laid out, along with a `pre-individualphase` tag and a `git reset --hard pre-individualphase` rollback path.

### Phase 7 — Reskin and the role-lock bug *(14–15 July)*

`NIST-Omran-Sandbox` was made an exact copy of production via `rsync --delete` with `--exclude` protecting `deploy/radio-web-sandbox.service` (commit `0e07a33`). A **CSS-only reskin** to a dense pro-dark instrument-dashboard aesthetic (MATLAB / Logic Pro / Wireshark register: 4px base grid, tabular-nums monospace for every numeral, matte palette, asymmetric side dock) was specified with hard protections — element IDs, `.pro-only`/`.noob-only`/`.admin-only`/`.role-readonly` guards, the `#wf-pane-tpl` template, and all JS untouched. Promoted to production behind a `pre-reskin` tag. Known ceiling documented: the Viridis LUT and PSD trace colors are JS constants (`CH_COLORS`, `STAT_COLS`, `PSD_BG`, `PSD_FG`) and cannot be reached from CSS.

Then the **admin-busy** bug: settings silently refused to apply in one mode. Traced to the single-admin-slot rule — the server accepts a second `admin` socket, sends `error: "admin-busy"`, closes with code **4001**; the client then skips `applyRole()` and drops into a 1.2 s reconnect loop that never tells the user why.

### Phase 8 — Public demo and repo housekeeping *(21–22 July)*

Aric asked for a simulator on GitHub Pages. Pages serves static files only — no Python, no WebSocket — so the `--demo` flag couldn't run there, and a notebook would only be a dead snapshot. Three options were weighed (Hugging Face Spaces with Docker; Binder; a full JS port of the synthetic generator); **option three was chosen** for a fully client-side demo, and a mentor email was drafted that committed to it while leaving room for him to redirect toward a hosted instance.

Housekeeping in the same window: a repo restore to commit `dbc3700` with `git push --force-with-lease` (discarding `348bf91`…`fe4f035`, including "Add citation.cff" and a `usnist.gov/striqt` commit), with a flag that the `sensor05` account appears to auto-commit and must not be running during a force-push. Tailscale + SSH were also brought up cleanly between the Pi 5 and the Mac so the pair no longer depended on a phone hotspot.

### Phase 9 — Identity, hardening, and the presentation *(28–31 July)*

- **Naming.** Six candidates (ITWORKS, VISARA, RIZZ, MUSTAFAR, SUSDR, FINALLY) → a pivot to "recognizable human first name + honest backronym" (CLARA, NADIA, WILMA, NORMA, MAVIS, LINDA) → three finalists (VISARA, SPECTACLE, LINDA) → a detour through ARIC, argued down on the grounds that a wrapper carrying one mentor's name while wrapping the other's library is a small permanent oddity. **LINDA** — *Live IQ Navigation and Display Application* — was settled, with the humor coming from register mismatch (a mundane human name on a precision RF tool) rather than a joke backronym. Namespace check against GitHub/PyPI recommended before committing; the README Acknowledgments placeholder was named as the correct venue for crediting both mentors.
- **Pi setup unblocked.** `setup.sh` enforced Python 3.9–3.12 against a system Python of 3.13.5.
- **Git divergence** across both `NIST-Omran` and `LINDA` from editing on GitHub without pulling first, including a mid-rebase README conflict in detached HEAD.
- **Repo hardening prompts written.** A four-phase read-only Claude Code prompt: inventory of every control point (UI toggles, env vars, WebSocket params, CLI flags, flagged hardware-vs-mock), combinatorial/tree testing (exhaustive where small, pairwise/boundary-value where not, always exhaustive on min/max/invalid, simultaneous state changes, and hard constraints like the connection locks), `BUG_REPORT.md`, and `CLEANUP_CANDIDATES.md` — the latter explicitly hunting leftovers from the NIST-Omran → Linda rename.
- **Documentation pass.** Aric's three words — *pdoc, function comments, docstrings* — decoded as one workflow: Google-style docstrings on every function, class, and module so `pdoc` can auto-generate API reference; strip noise comments but **preserve load-bearing ones** (hardware quirks, workarounds, `striqt` constraints). Goal confirmed as docstring-readiness rather than running `pdoc` now. Prompt written with a pre-edit git checkpoint, a hard no-logic-changes constraint, and a `DOCS_SUMMARY.md` deliverable.
- **End-of-year SURF presentation.** Rather than pre-compiling logs, the chosen workflow is to open each old Claude Code chat, paste an extraction prompt, have it recall that session's own history, and **append** the entry to `presentation/project-history.md` inside the Linda repo — `---` separators, auto-create on first run, and explicit instruction that *Linda / NIST-Omran / NIST-Omran-Sandbox are the same project* so old-named sessions aren't treated as unrelated work. Each entry displays in chat for a sanity check before moving on. Known limitation flagged: any chat that hit a context limit in its original session may already have truncated visible history.

---

## 2. Key Accomplishments

**Architecture and DSP**
- Read-only diagnostic plus a pure-SoapySDR sweep established hardware and API ground truth before a single line of production code existed.
- **Acquirer/Computer thread split** behind a fixed ring buffer (`MAX_TAIL = 1<<22` samples/channel, ~33 MB, O(read) wraparound writes) — the design that made large captures safe and became the backbone of everything after.
- Full consolidation onto the radio: `striqt_standalone.py` deleted the entire network layer for the desktop path.
- Calibrated `striqt` path wired end to end — `evaluate_spectrogram`, kaiser window, 13/28 overlap, 15/28 window fill, a 1024-point FFT collapsing to ~147 output bins.
- **Replace (full window) mode** delivered exactly as Dan specified, as a default-OFF toggle.

**Web platform**
- `striqt_web_server.py`: FastAPI + WebSocket binary frame streaming to a uPlot frontend.
- Public HTTPS at `radio.mustafaomran.com` through a Cloudflare named tunnel, with DNS migrated to Cloudflare; Tailscale for private admin reach.
- Always-on deployment: `radio-web.service` + `cloudflared` service, both `Restart=always`, secrets in a root-only env file, never in the repo.
- Auth that actually holds at the socket layer, not just the page layer — plus a cookie path so phones work.
- Role system: DAN (pro) / ARIC (noob) modes, admin/viewer/intern gating, station-tuner presets, mobile-responsive layout, NIST access banner.

**Process and engineering discipline**
- A **sandbox-first promotion pipeline** (sandbox 8001 → verify → production 8000) with rollback tags at every risky step (`pre-individualphase`, `pre-reskin`).
- A stable division of labor: **Mac edits, GitHub is the intermediary, the radio pulls and runs** — the Mac never serves the app.
- A 960-line independent audit followed by 22+ tracked fixes, each with a compile gate and a verify step.
- Second-platform port (Pi 5 + Pluto) proving the tool isn't welded to one radio.
- A public demo path chosen with full knowledge of what GitHub Pages can and cannot do.
- Repo brought toward professional review standard: naming, docstring readiness, combinatorial test plan, cleanup inventory, presentation history.

---

## 3. Challenges & Roadblocks

**Networking and links**
1. **Ethernet instability**, three independent causes stacked: the Mac's `en5` reverting to a self-assigned `169.254.x.x` APIPA address because no DHCP server existed; the Deepwave's `eth0` IP not persisting across reboots; and the USB-C dongle's PHY entering low-power sleep and **dropping the first TCP packet after idle**.
2. **The version-mismatch trap.** A Wi-Fi test appeared to succeed for an entire session while actually running over Ethernet — stale local copies of the scripts had a hardcoded `AIRT_HOST="192.168.50.1"` and no `argparse`, so the Wi-Fi IP passed on the command line was silently ignored. Only unplugging the cable revealed it.
3. **Cloudflare Error 1033** after the radio was physically relocated.
4. **GitHub Pages cannot run Python or hold a WebSocket** — the `--demo` mode could not be hosted there as-is.

**Acquisition and rendering**
5. **DMA overflow / corruption loop.** On-demand reads for large windows starved the SDR — exactly the failure mode Dan had warned about.
6. **Broadcaster `UnboundLocalError`** from `_connections -= dead`, which silently killed frame sending.
7. **uPlot null-length startup crash** and `e.auto is not a function`.
8. **`np.hann`** — a typo for `np.hanning`.
9. **A 1-frame-per-5-second cadence**, caused by an FFT size of **1036** carrying a slow prime factor, compounded by a **300-row cap that made the duration control inert**.
10. **libstdc++/GLIBCXX mismatch** at server startup.

**Correctness and UI**
11. Audit findings: an **inverted LO-null checkbox**, a **phantom SSB button**, **misrepresented frequency axes**, four **rendered-but-ignored capture fields** (`host_resample`, `analysis_bandwidth`, `lo_shift`, `backend_sample_rate` all hardcoded in `make_capture`), and a broken schema settings editor.
12. **The true SSB spectrogram needs a 30 kHz grid** incompatible with the default sample rate.
13. **NiceGUI 3.x requires Python ≥3.10** against `radio05`'s 3.9 — blocking the most obvious route to the schema editor.

**Access, auth, and concurrency**
14. **The WebSocket-won't-connect blocker.** The page loaded, the status read *disconnected — reconnecting*, and **no login popup ever appeared**. Two candidate causes had to be eliminated in order: a still-live **Cloudflare Access *application*** (deleting the *policy* fails with a conflict error precisely because the application still exists — a recurring point of confusion), and then the real cause.
15. **Safari/iOS never attach Basic Auth credentials to a JavaScript WebSocket handshake**, even after the page authenticates.
16. **The single-holder rule** — only one process can own the SDR, so the desktop Qt viewer and the web server can never run at once.
17. **The single-admin-slot rule** — a second admin socket is refused with `admin-busy` / close code 4001, and the client dropped into a silent 1.2 s retry loop. This also explained a remote viewer (family in Egypt) seeing the page load with no stream.

**Second platform (Pi 5 + Pluto)**
18. **xrdp black screen** through multiple failed fix cycles — `xfwm4` never launching despite `xfce4-session`, `xfdesktop`, and `xfce4-panel` running.
19. **`soapysdr-module-plutosdr` does not exist** as a Debian Trixie package.
20. **PEP 668** blocking pip installs on Trixie.
21. A **`sed` patch that mangled `setup.sh`**'s apt line-continuation backslashes, turning package names into shell commands.
22. **Pluto not enumerating** (`no device found`) after a clean GUI launch.
23. **`setup.sh`'s Python 3.9–3.12 cap** against a system Python of 3.13.5 — with the real trap underneath: the script uses `venv --system-site-packages` specifically to inherit the apt-installed `python3-soapysdr`, so any `uv`-managed interpreter yields an empty site-packages and a broken `import SoapySDR`.

**Version control**
24. **GitHub removed HTTPS password auth**, breaking the push from the radio.
25. **Divergent branches** across both repos, from editing on GitHub without pulling first — once landing mid-rebase with a README conflict in detached HEAD.
26. A **phantom "claude" contributor** on the GitHub contributor graph with zero matching commits in `git log`.
27. A **`sensor05` account making automated commits** directly to the repo — a live hazard during any force-push.

---

## 4. Solutions Implemented

| # | Problem | Resolution |
|---|---|---|
| 1 | Ethernet instability | Manual IP set in **System Settings → Network** (not ephemeral `ifconfig`); `sudo ip addr add 192.168.50.1/24 dev eth0` re-run per session on the radio; dongle sleep worked around with a continuous ping plus a **static ARP entry** (`sudo arp -s`). Ultimately superseded — the standalone consolidation removed the link from the critical path entirely. |
| 2 | Version-mismatch trap | `argparse` host/port arguments on the viewer, so the network target is explicit and never silently defaulted. Reinforced the standing rule: verify which copy of the code is actually running. |
| 3 | Cloudflare 1033 | Restored internet at the new site; tunnel reconnected on its own. |
| 4 | GitHub Pages limits | Rejected the notebook and hosted-server paths; committed to a **JavaScript port of the synthetic data generator** for a fully client-side demo, with the constraint explained to the mentor in writing and the door left open to a hosted instance. |
| 5 | DMA overflow | Replaced the rolling-tail acquirer with a **fixed ring buffer** (`MAX_TAIL = 1<<22`, ~33 MB/channel, O(read) wraparound) and split acquisition from computation into two threads — the **Acquirer never pauses**, so the radio's buffer never floods regardless of what the compute side is doing. |
| 6 | Broadcaster crash | `_connections.difference_update(dead)`. |
| 7 | uPlot crashes | PSD series initialized as length-`nfft` arrays with gap arrays on the update path; `scale.auto` assigned **functions** (`() => true` / `() => false`) rather than booleans. |
| 8 | `np.hann` | → `np.hanning`. |
| 9 | 5-second cadence | FFT size moved off the slow-prime-factor value and the **300-row cap removed**, which also made the Duration control meaningful for the first time. |
| 10 | GLIBCXX mismatch | A **re-exec workaround at server startup**. |
| 11 | Audit findings | The **LV-\* fix series** (22+), then **Phase 1 consolidation**: LO-null sense corrected so `checked ⇒ lo_null=true ⇒ spike hidden` (default checked), the four dead capture fields wired through `make_capture`, the redundant AIR-T bar deleted, "Window ms" replaced by Duration. One change per commit, `python3 -m py_compile` + `node --check` gates, a `FIXLOG.md` entry per change with an explicit `[demo]` or `[hardware]` verification step. |
| 12–13 | SSB grid / NiceGUI | Both documented as constraints rather than papered over. The schema editor was routed toward a small hand-rolled option compatible with Python 3.9, with the units gap covered by a ~10-field hand-authored label map. |
| 14 | WebSocket blocker | The decisive diagnostic was **`curl -i http://localhost:8000/`** — it returned `401 Unauthorized` with `WWW-Authenticate: Basic` without credentials and `200 OK` with them, proving the origin was correct and moving the search browser-side. Root cause: a **stale browser cache** serving a pre-auth copy of the page, so the browser never made a challenged request, held no credentials, and got the socket rejected with close code 1008. Fixed permanently server-side with **no-cache headers** on the static assets rather than by asking users to clear caches. |
| 15 | Safari/iOS auth | A **signed session cookie** issued after login and carried by the WebSocket — the one credential mechanism mobile browsers handle correctly. |
| 16 | Single-holder rule | Accepted as a hardware truth and made operationally explicit: stop `radio-web`, use the desktop app, start it again. Mentor approval obtained for taking the radio on demand. |
| 17 | admin-busy lock | Root-caused precisely to the server's `_admin_ws` slot and the client's skipped `applyRole()` on the busy path — turning a silent, invisible failure into a known, explainable state. |
| 18 | xrdp black screen | Diagnosed by process inspection (`ps aux \| grep -E "xfwm4\|xfdesktop\|xfce4-session"`) rather than more blind fixes. Root cause: the primary account's session config polluted by the Pi's native Wayland desktop (`rpd-labwc`, `wf-panel-pi`). Fix: a **fresh `rdpuser` account** with no Wayland autostart. |
| 19–21 | Pi packaging | The PlutoSDR SoapySDR plugin built from source; `--break-system-packages` added for PEP 668; `setup.sh` **rewritten from scratch** after the `sed` damage, with a clean apt block and simplified Python checks. |
| 22 | Pluto not found | Isolated to hardware-vs-permissions with `lsusb \| grep -i analog`, a `plugdev` group-membership check, and `SoapySDRUtil --probe="driver=plutosdr"`. |
| 23 | Python 3.13 cap | Evidence first: `apt-cache` showed Debian Trixie's `python3-soapysdr` **hard-pinned to `python3 (>= 3.13~, << 3.14)`** with no 3.11/3.12 apt packages existing at all — proving the script's cap was **stale, not meaningful**, and that the system interpreter is the only one the binding exists for. The `uv`-installed 3.12.13 was correctly abandoned. Resolution: patch the two version lines and run against system Python, with a flagged residual risk that some `requirements.txt` packages may lack cp313 aarch64 wheels. |
| 24 | HTTPS auth removed | **ed25519 SSH key** generated on the radio, registered on GitHub, both remotes switched from HTTPS to `git@github.com:`. |
| 25 | Divergent branches | `git config pull.rebase false` → `pull` → `push` for the merge case; for the mid-rebase case, manual conflict resolution in README, `git add`, `git rebase --continue`, with `git rebase --abort` as the escape hatch. Detached HEAD explained as expected, not broken. Root cause named and a habit prescribed: **`git pull` or `git fetch && git status` before starting local work.** |
| 26 | Phantom contributor | Verified against reality first (`git log --all` for author, email, `Co-Authored-By` trailers, and git notes), then a branch-rename reset attempt, with GitHub Support as the documented fallback for what is a known false positive. |
| 27 | `sensor05` auto-commits | Flagged explicitly as a precondition to check before any force-push. |

**Cross-cutting safety practices** that solved whole classes of problems rather than single bugs: rollback tags before every risky promotion; sandbox-before-production for every change; read-only audit passes before any edit pass; one-change-one-commit with compile gates; and secrets in a root-only `EnvironmentFile` chosen specifically because `systemctl cat`/`status` would leak inline `Environment=` values.

---

## 5. Overall Impact

**Stability.** The ring buffer plus the Acquirer/Computer split is the single highest-leverage change in the project: it converted a system that corrupted itself under exactly the workload the lead mentor wanted (large replace-mode captures) into one where the radio is drained unconditionally and compute pressure can never reach the DMA. Everything downstream — web streaming, multi-client, demo mode — is only possible because that invariant holds. The freedom model extended the same philosophy to user input: a bad analysis parameter is now snapped, reported, and validated against scratch state, so no setting a user can type will freeze the live feed. The two systemd services with `Restart=always` mean crashes and reboots self-heal instead of requiring an SSH session.

**Capability.** The tool went from *one Mac, on one Ethernet cable, in one room* to *any browser, anywhere, on any device, over HTTPS* — with the mobile path specifically engineered rather than assumed, since the cookie fallback was the difference between "works on my laptop" and "works on a phone." The DAN/ARIC dual-mode UI made one codebase serve two genuinely different users: full manual control for the instrument expert, a one-click station tuner for everyone else. The Pi 5 + Pluto port and the multi-SDR planning pass mean the work has a life past the specific Deepwave it was written on. And the audit-driven fixes were not cosmetic — the frame rate went from **one frame every five seconds** to a genuinely live feed, and the Duration control went from decorative to functional.

**Correctness.** This may be the most durable contribution. Before the audit the viewer *looked* right while showing an inverted null control, mislabeled frequency axes, a button wired to nothing, and four settings the user could change with no effect whatsoever. In a metrology laboratory a display that lies is worse than one that fails, because nobody knows to distrust it. The audit converted a confident-looking instrument into a verified one, and the `FIXLOG.md` convention — every fix carrying a `[demo]`/`[hardware]` verification step — means the next person can re-derive that trust instead of taking it on faith.

**Security posture.** The auth was designed at the right layer. Rejecting unauthenticated WebSocket upgrades **before `accept()`** means the data stream cannot be pulled by anyone who skips the page, which is the mistake most homegrown auth makes. `secrets.compare_digest` closes the timing side channel. Secrets living in a root-only env file, never in the repo, survived multiple public pushes and repo renames without incident. And the fact that mentor approval was obtained *before* pointing a permanent public address at a federal RF sensor — not after — is the part a NIST reviewer will care about most.

**Efficiency and reproducibility.** The sandbox → verify → production pipeline with rollback tags turned deployment from a nerve-racking event into a routine one; demo mode means UI work no longer requires the radio at all, which decouples frontend iteration from hardware availability and from the single-holder lock. The Mac-edits / GitHub-mediates / radio-pulls discipline eliminated an entire recurring class of "which copy is actually running" failures — the same class that cost a full session in the Wi-Fi version-mismatch trap.

**Professional readiness.** The last stretch of work is what turns a summer project into something that outlives the fellowship: a real name with an honest expansion, docstring coverage designed for `pdoc` API generation, a combinatorial test plan that treats the hard constraints (locks, singleton connections, hardware-only branches) as first-class test targets, a cleanup inventory for rename leftovers, and `presentation/project-history.md` capturing the *why* behind decisions — including the dead ends — rather than just the final diff. Dan's standing rule (*new capability as a toggle, never a rewrite*) was honored throughout, which is why a rapidly-evolving tool never broke his existing workflow.

**The underlying method.** The rule set in week one — nothing invented, everything grounded in a document, the source, or device output — is visible in every hard problem that got solved cleanly. The diagnostic before the sweep. `curl -i` against localhost to split origin from browser. `apt-cache` to prove a version cap was stale rather than assuming it. `ps aux` before another xrdp fix. `SoapySDRUtil --probe` to separate hardware failure from permissions. Nearly every root cause in section 3 was found by measuring rather than guessing, which is why the fixes in section 4 mostly stayed fixed.

---

## Appendix A — Open Threads (as of 31 July 2026)

- README **Acknowledgments and License** still placeholders — Acknowledgments identified as the right venue for crediting Dan (striqt, acquisition architecture) and Aric (measurement framing). License needs a decision: NIST-authored work may be public domain rather than MIT — confirm before asserting.
- **Namespace check** on `linda sdr` / `linda spectrum` against GitHub and PyPI, ideally before the name propagates further.
- **Rename leftovers** from NIST-Omran → Linda — the `CLEANUP_CANDIDATES.md` pass is written but its output not yet triaged.
- **`BUG_REPORT.md` / combinatorial test results** — prompt written, results not yet reviewed.
- **Docstring pass** — prompt written with checkpoint and no-logic-changes constraint; `DOCS_SUMMARY.md` not yet produced.
- **`presentation/project-history.md`** — extraction workflow defined, entries still being appended chat by chat.
- **JS port of the synthetic generator** for the GitHub Pages demo — chosen, not yet built.
- **Pi 5 `setup.sh` run** against patched Python 3.13 — outcome unconfirmed; residual risk of missing cp313 aarch64 wheels in `live/requirements.txt`.
- **PlutoSDR channel decision** — single-RX vs. dual-RX, and auto-detect vs. config selector vs. hardcode.
- **Schema-driven capture editor** — feasibility done, implementation not started; blocked on the live-tuning vs. config-authoring question and on Python 3.9.
- **Longer-term items from Dan's whiteboard** — direct YAML configuration, on-device UI, additional analysis tools (he specifically asked which tools *you* want to see).
- **VisPy GPU-texture rendering** — optional migration to push spectrogram resolution past the `makeARGB` bottleneck.
- **`striqt air8201b.source` integration** for fully calibrated output.
- **admin-busy UX** — root cause understood; the silent retry loop still doesn't tell the user what's happening.
- **Zarr file inspection** from the first end-to-end capture — never circled back to.
- A 27-minute mentor-meeting audio recording was uploaded early on and **could not be transcribed** (no ASR tooling available); its contents were reconstructed from notes.

## Appendix B — Reference Data

**Key commits & tags:** `bbb7605` (Auth Addition) · `e21626b` → `8d6048c` (sandbox Multi-Device Support) · `2288bcc` (production promotion) · `0e07a33` (sandbox sync) · `dbc3700` (restore target) · tags `pre-individualphase`, `pre-reskin`

**Key files:** `airt_diagnostic.py` · `airt_live_server_full.py` + `live_viewer_full.py` (v1 pair) · `striqt_standalone.py` · `live/striqt_web_server.py` · `live/web/{index.html, app.js, style.css, colormap.js}` · `live/pluto_standalone.py` · `setup.sh` · `run_web.sh` · `deploy/radio-web-sandbox.service` · `/etc/radio-web.env` · `docs/AUDIT_REPORT.md` · `docs/CAPTURE_EDITOR_FEASIBILITY.md` · `FIXLOG.md` · `presentation/project-history.md`

**Key constants:** `MAX_TAIL = 1<<22` samples/channel (~33 MB) · kaiser window, 13/28 overlap, 15/28 window fill · 1024-point FFT → ~147 output bins · span presets 3.84 / 7.68 / 15.36 / 30.72 / 61.44 MHz (multiples of 1.92 MHz, standard LTE/5G-NR rates, chosen to align with the cellular bands the rig monitors) · WebSocket close codes 1008 (auth reject) and 4001 (admin-busy)

**Endpoints:** production `localhost:8000` / `radio.mustafaomran.com` · sandbox `localhost:8001` · legacy TCP `0.0.0.0:5005` · Tailscale `100.91.57.48`
