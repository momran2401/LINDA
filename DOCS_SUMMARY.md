# Documentation pass summary (2026-07-31)

Docstring/comment-only pass over `live/core/`, the top-level frontend scripts,
and `live/tools/`, done in preparation for pdoc API doc generation. No pdoc
config was added, installed, or run — this is purely documentation content.

Scope excluded: `live/legacy/` (frozen), `live/striqt/` (vendored, read-only),
and `live/tests/`.

**Verification performed**: every touched file was parsed with `ast` before
and after, with docstrings stripped, and confirmed byte-for-byte identical at
the AST level — i.e. no logic, control flow, names, or imports changed
anywhere in this pass, only docstrings and comments. `python3 -m pytest
live/tests/` passes (174 passed, 5 skipped), same as before the pass.

## Files touched (27)

```
live/core/__init__.py            live/core/gps.py                 live/core/tx.py
live/core/constants.py            live/core/health.py               live/radioctl.py
live/core/state.py                live/core/insights.py             live/striqt_kiosk.py
live/core/presets.py              live/core/operations.py           live/striqt_standalone_terminal.py
live/core/serialization.py        live/core/parsing.py              live/striqt_web_server.py
live/core/striqt_compat.py        live/core/recording.py            live/sweep_runner.py
live/core/shims.py                 live/core/devices/__init__.py     live/tools/hardware_qual.py
live/core/config.py                live/core/devices/base.py         live/tools/pull_recordings.py
live/core/dsp.py                   live/core/devices/sources.py
live/core/acquisition.py
```

## Rough counts

- **Docstrings added** (previously undocumented module/class/function/method): **~219**
- **Docstrings improved** (existing docstring rewritten to Google-style / corrected): **~193**
- Every module, class, function, and method across the 27 files now has a
  Google-style (Args/Returns/Raises) docstring.
- Two inline comments were deleted as pure restatement (their content was
  folded verbatim into the new docstrings): one in `live/core/dsp.py`
  (`db_spectrogram`'s trailing note) and one in `live/core/gps.py`
  (`capture_fields`'s altitude note). No other comments were deleted — every
  other existing inline `#`/`NOTE:` comment was judged load-bearing (hardware
  quirks, magic numbers, "why," known limitations) and left in place, per the
  keep-if-unsure rule.

## Comments left in place but flagged for your review

- **`live/core/shims.py`** — `query_device_envelope`'s docstring keeps a
  `"(P3-3)"` ticket-style tag that looks like an intentional rationale-tracing
  reference, but its meaning couldn't be verified from the code alone.
- **`live/core/config.py:1218`** — `"Read directly — self._lock is already
  held (envelope() would re-take it)."` — kept, explains a non-obvious inline
  read pattern.
- **`live/core/config.py:1071`** — `"Follow-along: the hop grid moved..."` —
  kept; terse enough that it could be mistaken for noise, but it's rationale
  tied to a specific branch, not restatement.
- **`live/core/devices/sources.py:~50-56`** (`make_source_spec`) — a
  multi-line profile_clock/fallback comment overlaps somewhat with its new
  docstring; both were kept since the comment nails down the specific "why
  AIR-T's default is 125 MHz" reasoning at the exact line it applies to.
- **`live/core/devices/base.py:33`** — `"# SoapySDR's RX direction constant"`
  kept, tied to one specific fallback-assignment line.
- **`live/striqt_web_server.py:1099-1100`** — a context comment above the
  `tx.TX.active()`/`stop()` call near `reset_radio` ("A restart tears this
  process down...") was kept alongside the new docstring that also captures
  the gist.

## Bugs noticed but NOT touched (per instructions)

1. **`live/core/parsing.py:310-311`** — `_parse_time_statistic`'s
   `isinstance(tok, bool)` guard rejects `True`/`False` tokens, but appears
   unreachable for string input (strings go through `float(tok)` first); only
   reachable via a list/tuple path like `[True, "mean"]`. Worth confirming
   whether this is intentional.
2. **`live/core/recording.py:262-278`** — `_run`'s `finally` block calls
   `self.acquirer.resume()` unconditionally, including on the hardware path
   where `run_sweep` may have left the source mid-`finite_capture_mode`.
   Likely fine (the context manager restores the live spec on its own exit)
   but worth double-checking `resume()` can't race that teardown if
   `run_sweep` raised partway through `ExitStack` unwind.
3. **`live/sweep_runner.py:82-85`** — after `source.arm_spec(captures[0])`,
   `open_stream(source)` is called explicitly because live handoff already
   closed the stream. If a future live-handoff path ever left the stream
   open, this call would operate on an already-open stream — a latent
   coupling, not an active bug.
4. **`live/core/tx.py` — `_arm_with_escalation`** — its docstring (pre-pass)
   claimed a 4-tuple return `(stream, actual, mismatched, rx_mode)`, but the
   function actually returns a 6-tuple `(stream, actual, mismatched, rung,
   fmt, full_scale)`. This was a stale-docstring bug, now corrected in the
   docstring; the code itself was untouched and is presumably correct.
5. **`live/core/tx.py` — `_tune_tx` mismatch check**} — excludes `gain_db`
   from the `mismatched` list by design (`key != "gain_db"`), so a TX gain
   readback that disagrees with the requested gain can never trigger a
   `mismatch` verdict, unlike frequency/rate. May be intentional (gain
   doesn't read back to exact tolerance) but it's a silent asymmetry with the
   rest of the verdict logic.
6. **`live/core/acquisition.py:855-880`** — on a non-reconnect apply failure,
   `OPERATIONS.finish(op_id, "failed", ...)` runs *before* the
   `self.rearm(cfg, None)` rollback attempt; if rollback then succeeds, the
   op log still shows "failed" with no second `finish` call reflecting the
   recovery. Probably intentional, but worth a second look.
7. **`live/core/acquisition.py:279-283`** — `_readback_and_verify` only emits
   the "hardware LO intentionally offset" note when `check_freq` is true; a
   rate mismatch without frequency in `changed_fields` would skip that
   explanatory note even though the offset is still part of `expected`.
   Display-only, low impact.
8. **`live/core/devices/__init__.py` — `probe_channels()`** — for a Deepwave
   profile, accepts a lone anonymous SoapyAIRT enumeration row as a match for
   *any* requested Deepwave model name (air7101b/air7201b/air8201b) when it's
   the only AIR-T present — could silently attribute channel counts to the
   wrong variant if a different model is attached but its row lacks a
   recognizable model string.
9. **`live/core/devices/base.py` — `verify()`'s `judge()`** — treats any
   non-`None` actual value outside tolerance as a hard "mismatch" for gain
   too, even though nearby documentation says gain mismatches are only
   "warning-grade." The verdict state doesn't actually let callers
   distinguish a gain mismatch from a center/rate mismatch.
10. **`live/core/gps.py:203-208`** — the SKY message's satellite-count
    fallback silently yields `used = 0` when the `satellites` list is empty,
    indistinguishable from "0 satellites used" vs. "field genuinely absent."
11. **`live/striqt_web_server.py:1153` — `preset_apply_endpoint`** — its
    `except (ValueError, TypeError, AttributeError)` returns 400 for any
    update-rejection error, but never distinguishes "recording became active
    between the initial check and `_shared.update`" (a race) from a plain
    bad-payload 400.
12. **`live/striqt_web_server.py:1657` — `_login_page`** — the page's
    `<title>` reads "Sign in · Live IQ Navigation & Display Application"
    while docstrings/comments elsewhere call the product "striqt live
    viewer" — a naming inconsistency, not functional.
13. **`live/striqt_standalone_terminal.py:~163`** — the `b`/`B` key handler's
    `order.index(cfg.backend)` raises `ValueError` if `cfg.backend` is ever
    not a member of `BACKENDS` (e.g. a demo/no-analysis fallback value); not
    observed to trigger, a latent fragility.
14. **`live/radioctl.py:394` — `print_gps`** — the "not valid" branch checks
    `gps.get("stale")` before the "no fix yet" (`mode <= 1`) case (likely
    correct priority), and truthy-checks `gps.get("connected")` without
    confirming `/gps` always includes that key — worth a quick cross-check
    against `core/gps.py`.
