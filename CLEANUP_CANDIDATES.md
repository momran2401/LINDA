# LINDA — Cleanup Candidates

Read-only inventory, 2026-07-30. **Nothing was deleted, moved, or modified.**
This is a list for you to act on (or reject) — several entries are deliberate
design decisions that only *look* like leftovers, and they are marked as such.

Confidence reflects how certain the "unused" claim is, not how much you should
want it gone.

---

## A. Confirmed duplicates — safe to delete

### A1. `web/vendor/` (repository root)

| | |
|---|---|
| **Path** | `web/vendor/uPlot.min.js`, `web/vendor/uPlot.min.css` |
| **Why unused** | Byte-identical duplicate of `live/web/vendor/`. `diff -q` reports no difference for either file. |
| **Confidence** | **High** |
| **Referenced by** | Nothing. `grep` across `*.py`, `*.sh`, `*.html`, `*.js` finds zero references outside `live/web/`. The page loads `vendor/uPlot.min.js` relative to `live/web/index.html` ([index.html:13](live/web/index.html:13), [:476](live/web/index.html:476)), and `install_linda.sh:801-811` restores assets into `live/web/vendor/`. |
| **Note** | The real copy at `live/web/vendor/` also carries `LICENSE-uPlot.txt`, which this one lacks — another sign it is the stray. Both files are git-tracked, so removal is a real commit. Almost certainly a merge/rename artifact. |

---

## B. Orphaned code — unreferenced, but check intent first

### B2. `live/web_sim/index.html` (789 lines)

| | |
|---|---|
| **Why unused** | A self-contained browser simulation of the standalone viewer (synthetic IQ, inline CSS/JS). Not served by the web server, not mounted by `StaticFiles` (which serves `live/web/` only), not opened by any script. |
| **Confidence** | **High** that it is unreferenced by code; **low** that you want it gone. |
| **Referenced by** | Documentation only — `docs/REPO_ANALYSIS.md:50,53,340,356` and `docs/REPO_OVERVIEW.md:41,56,122,351,411`. `REPO_ANALYSIS.md` already flagged it as "orphaned demo-ware" and asked whether it is a deliberate hand-out. |
| **Recommendation** | This is the open question from the previous analysis, still unanswered. It is a parallel UI that will drift from `app.js` — either delete it or add a header saying it is a frozen standalone demo and not the real client. |

### B3. `live/legacy/` (4 scripts, ~5.1k lines)

| | |
|---|---|
| **Paths** | `striqt_standalone.py`, `pluto_standalone.py`, `striqt_server_TCP.py`, `striqt_frontend_TCP.py` (+ `README.md`, `oldREADME.md`) |
| **Why unused** | Nothing imports or launches them; each carries a duplicated copy of the radio/DSP/config code and imports `striqt` directly. |
| **Confidence** | **High** that they are dead code; **high** that they should stay anyway. |
| **Referenced by** | `live/legacy/README.md` (which freezes them explicitly), `CLAUDE.md`, and rationale comments in live code: `core/shims.py:18`, `core/acquisition.py:66,151`, `core/devices/sources.py:3,111` cite them as the source of ported logic. |
| **Recommendation** | **Keep.** This is a documented, deliberate freeze with a README explaining what replaced each file, and live code cites them for provenance. Listed here only for completeness. |

---

## C. Rename fallout (`NIST-Omran` → LINDA, `setup.sh` → `install_linda.sh`)

### C4. `setup.sh` references throughout the tree — **also a functional bug**

The installer was renamed to `install_linda.sh`, but **no file anywhere refers to
it by its real name**, while 60+ references to the nonexistent `setup.sh` remain.
This is filed as bug #6 in [BUG_REPORT.md](BUG_REPORT.md) because some of these
are printed to users as instructions.

| Location | Kind |
|---|---|
| `install_linda.sh:5-8` | **Its own `--help` output** (the header block is dumped by `--help`) |
| `install_linda.sh:111,146,158,1299` | **Error messages telling users to run `setup.sh`** |
| `install_linda.sh:627,1009` | generated-file comments |
| `live/run_web.sh:17,21,36,49` | line 49 is **printed to the user** on missing dependencies |
| `README.md:31,39,68,99` | install commands |
| `docs/README_MANUAL.md:42,58,71,87,90,114-126,460,481` | the whole installer reference section |
| `CLAUDE.md:13,94,121,191,194,344,379` | including the GPS `RADIO_GPS_DEVICE` flow |
| `INSTALLED_STRIQT_API.txt:5,8` | "STRIQT_COMMIT in setup.sh is the source of truth" |
| `deploy/radio-web.service.template:1`, `deploy/run_service.sh:3`, `.gitignore:7`, `live/requirements.txt:2,8`, `live/constraints.txt:2`, `live/core/shims.py:156` | comments only |
| `uninstall_linda.sh:3,18,20,50,107,233` | comments; line 233's "older setup.sh" is intentional legacy wording |

**Confidence:** High (verified: no `setup.sh` exists at the repository root).
**Cheapest fix:** a `setup.sh` shim that execs `install_linda.sh` would make every
document above correct without touching them.

### C5. `NIST-Omran` strings in shipped files

| Path | Line | Note |
|---|---|---|
| `deploy/radio-web.service.template` | 5 | `Description=NIST-Omran live radio viewer` — **visible in `systemctl status radio-web` on every deployment** |
| `live/core/__init__.py` | 1 | docstring: "shared backend for every NIST-Omran live viewer frontend" |
| `uninstall_linda.sh` | 222, 307, 381 | **Keep** — deliberate cleanup of legacy `70-nist-omran-sdr.rules` / `.nist-omran.bak` artifacts on previously-installed hosts |
| `docs/*` | many | historical records; leave as-is |

**Confidence:** High. The first two are user-visible and worth renaming; the
uninstaller ones must stay.

### C6. `README.md:116` — broken relative link

`[Repository overview](REPO_OVERVIEW.md)` resolves relative to the repository
root, but the file lives at `docs/REPO_OVERVIEW.md`. The adjacent link on line
115 correctly uses `docs/README_MANUAL.md`. **Confidence: High.**

---

## D. Stale references to the vendored `striqt/` directory

`striqt/` **does not exist in this checkout** (verified: `ls striqt` → no such
file). Nothing breaks functionally — `install_linda.sh:43,790` pins
`striqt @ git+https://github.com/usnistgov/striqt@2e7696d` and installs from
GitHub — but a large amount of documentation describes a directory that is gone.

| Path | What it claims |
|---|---|
| `CLAUDE.md:13-22` | The entire "`striqt/` is NOT the striqt that runs on the radio" warning block, premised on a vendored tree being present |
| `CLAUDE.md` (Repository overview) | "`striqt/` subdirectory is an upstream NIST library … treat it as **read-only**" |
| `CLAUDE.md` (Tests) | `cd striqt && pytest tests/` — cannot run |
| `INSTALLED_STRIQT_API.txt:2,11` | The file's whole framing is "installed vs *vendored*" |
| `README.md:131` | "The `striqt/` library included in this repository" |
| `install_linda.sh:29`, `uninstall_linda.sh:27` | "striqt/ is upstream and is never touched" |

**Confidence: High** that the directory is absent. **Recommendation:** do not
delete `INSTALLED_STRIQT_API.txt` — its API divergence table is still the thing
that stops code being written against the wrong striqt API, which `CLAUDE.md`
says has already caused two production bugs. Re-frame it as "installed striqt
v0.7.0 API reference" rather than deleting it. Whether `striqt/` should be
restored as a submodule or the docs updated to match its absence is a decision
for you.

---

## E. Superseded / unreferenced documentation

`docs/` is explicitly described in `CLAUDE.md` as historical, so none of these are
bugs — this is a "does it still earn its place" list.

| Path | Inbound refs | Assessment | Confidence |
|---|---|---|---|
| `docs/MERGE_REPORT_2026-07-18.md` | **0** | Describes the merge in a path that no longer exists (`/Users/mustafaomran/merge/NIST-Omran`, line 4) and reports "40 tests, all passing" against today's 179. Fully superseded. | High |
| `docs/STRIQT_UI_IMPLEMENTATION_LOG.md` | **0** | Nothing links to it. | Medium — check whether it holds rationale not captured elsewhere |
| `docs/REPO_ANALYSIS.md` | 1 | A prior analysis pass; its open questions (web_sim, `.claude/settings.local.json`) are partly resolved. Overlaps this report. | Medium |
| `docs/REPO_OVERVIEW.md` | 15 | Linked from `README.md` (via the broken path in C6) and cited widely — but documents a `.claude/settings.local.json` that does not exist (line 41) and pre-`core` file layout. | **Keep, but stale** |
| `docs/bug_report.md` | 21 | Superseded as a *report*, but `live/core/constants.py` cites its `P-1` identifier for the Pluto master-clock rationale. | **Keep** — code depends on its identifiers |
| `docs/AUDIT_REPORT.md` | 29 | Same: `live/core/dsp.py` cites `AUDIT_REPORT.md LV-W2` for the sample-count formula. | **Keep** — code depends on its identifiers |
| `docs/FIXLOG.md` | 11 | Referenced from docs only. | Keep (history) |
| `docs/SANDBOX_REPORT.md` | 9 | Refers to a radio host path `/home/sensor/NIST-Omran` — pre-rename. | Keep (history), note the stale path |

---

## F. Minor / informational

| Item | Detail | Confidence |
|---|---|---|
| `.pytest_cache/` (root) and `live/.pytest_cache/` | Present on disk, **untracked** (`git ls-files` finds nothing), but **not** in `.gitignore`. They show up in `git status --others` noise. Adding `.pytest_cache/` to `.gitignore` would settle it. | High |
| `.claude/launch.json` | Runs `python3 live/striqt_web_server.py --demo --port 8092 --quantize` using the *system* `python3`, not `.venv/bin/python3` as `run_web.sh` prefers. Works here, but will miss the installed dependencies on a host where only the venv has them. | High |
| `citation.cff` | `date-released: 2026-08-07` is in the future (today is 2026-07-30). Presumably a planned release date; flagging only so it is not an accident. | High |
| `install_linda.sh` venv backups | Every requirements/commit change leaves a multi-GB `.venv.backup.<TIMESTAMP>/` (lines 774-776) that only the uninstaller ever removes. `.gitignore` covers them, so this is disk creep on the radio, not repo clutter. | High |
| `install_linda.sh:1006` — `CREDS_NOTE` | Assigned and never read anywhere (grep-verified). Dead variable. | High |
| `install_radio_web_sudoers.sh:59-61` | The `sudo -n -u … true` check discards its result. Dead code. | High |
| `live/web/fortheinterns.jpg` | **Not orphaned** — referenced at `live/web/index.html:471`. Listed only to pre-empt the assumption. | High |
| `live/web/colormap.js` | **Not orphaned** — loaded at `live/web/index.html:475`. | High |

---

## Suggested order, if you act on this

1. **C4 / C5** — the rename fallout, because two of those strings are printed to
   users as instructions that cannot work (see bug #6). A `setup.sh` shim is the
   one-line version.
2. **A1** — delete the duplicate `web/vendor/`; zero risk, verified identical.
3. **D** — decide whether `striqt/` returns as a submodule or the docs stop
   describing it. Keep `INSTALLED_STRIQT_API.txt` either way.
4. **B2** — answer the `web_sim` question one way or the other; it has been open
   since the previous analysis pass.
5. **E / F** — documentation pruning and the small dead-code items, whenever
   convenient. Do **not** remove `docs/AUDIT_REPORT.md` or `docs/bug_report.md`:
   live code cites their identifiers.
