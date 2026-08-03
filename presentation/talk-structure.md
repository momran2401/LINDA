# Talk Structure — LINDA / NIST SURF 2026 End-of-Year Presentation

**Format:** 12–13 min talk + 2–3 min Q&A, live demo at the center.
**Status:** DECIDED. This replaces the earlier candidate-weighing draft. Every striqt claim below was re-verified against the striqt source tree (evidence in §7).
**Room:** Aric Sanders and Dan Kuester are expected to be present. striqt is credited generously and accurately throughout — nothing here overstates LINDA at striqt's expense.
**Hardware on the day:** AIR-T with **both antennas live** (omni reference + fixed directional array on the cart) + a second screen running the same software on a Pluto/USRP + Pi. **The dish is fixed — no rotation gesture.**
**Written:** 2026-08-01

---

## 1. The spine — And / But / Therefore

One thread: open on it, touch it twice in the middle, close on it.

**AND** — NIST has to tell regulators how far apart new mid-band 5G must sit from the
federal point-to-point links already there — the ones carrying dam telemetry, grid SCADA,
and national-security traffic. That distance is the *guard band*. Too narrow risks the
infrastructure; too wide forgoes billions in auction value. And the honest answer depends on
**what antenna you measure with**: a standard omni averages interference spikes away, while
the directional dish actually being protected (often >30 dBi of gain) takes them head-on. So
the lab records an omni reference *and* a directional array **at the same time** and compares
the two distributions with Wasserstein distance — using striqt.

**BUT** — striqt is a serious real-time 5G measurement instrument (13 measurements,
Y-factor-calibrated to real dBm, publication-grade figures) — and it's built for **batch**.
It sees everything and shows you none of it *live*. And a perfectly clean run can't tell you
whether the dish was pointed at the sky or at a wall: a loose cable, a dead front end, or a
genuinely quiet band all archive as flawless data. On a rooftop mid-campaign, you can't tell
whether what you captured is what you *meant* to capture.

**THEREFORE** — LINDA: a live, honest, run-anywhere window on that measurement. Both
antennas side by side on **one shared colour scale**, so the same colour means the same
power and the two can be compared by eye. Every setting **verified against the hardware**
before LINDA believes it — and the whole capture form **generated from striqt's own schema**,
not hand-written. On an instrument-grade radio or a hobby one, the same software.

**Through-line object:** the two waterfall panes = the two antennas = the lab's experiment,
made visible.

> On "blind": do **not** call striqt blind. Its own README says "batched **real-time** signal
> analysis." It is real-time capable; what it lacks is a real-time *view*. "Sees everything,
> shows you none of it live" gets the same beat without a factual opening Dan would correct.

---

## 2. Section-by-section (≈11:30 talk, buffer inside the 12–13 slot)

Assertion-style headline per slide (states the conclusion, not a category). ~1 idea/slide.

### 1 · Cold open — the stakes · 0:00–1:15
- **No title/outline slide.** Open on the dilemma made concrete.
- Beat: the government wants to auction spectrum next door to federal radios running dams,
  the grid, defense. How far apart must they sit? Too close, you break a dam's telemetry;
  too far, you burn billions.
- The hook that motivates everything: *"and the honest answer depends on what antenna you're
  listening with."*
- **Headline:** "How far apart must 5G and federal radios sit? Billions — and a dam — ride on the number."

### 2 · The science + striqt · 1:15–3:15 (~2 min, the whole background budget)
- One diagram: omni **averages the spike away**; the directional antenna **takes the spike**
  (reasoning doc p.9). So the lab records **both at once** and compares them (Wasserstein).
- striqt, credited plainly — **Dan's library, the measurement instrument**: turns 125 MS/s of
  raw IQ into calibrated, real-dBm spectra and archives it. Say what makes it *metrology, not
  pictures*: **Y-factor calibration** (noise temperature → real dBm), **13 measurements**
  including five 5G-specific ones (PSS/SSS correlation, SSB spectrogram, cyclic
  autocorrelation, resource-power histogram), and **publication-grade figure styles** (IEEE,
  NIST report). One command runs it: `sensor-sweep spec.yaml` → a zarr archive.
- Land the turn: *"It's a real-time instrument, built for batch. It sees everything — and
  shows you none of it live."* The publication figures are your proof that batch is a
  **design choice**, not an oversight — frame the gap as a compliment.
- **Headline:** "striqt measures the spectrum to metrology grade — in batch."

### 3 · The gap LINDA fills · 3:15–4:00 (~45 s)
- The unfalsifiable one, and your best line: *a perfectly clean run can't tell you whether
  the dish was pointed at the sky or at a wall.* Loose cable, dead front end, genuinely quiet
  band — all archive clean. During a campaign you're committing hours of capture on faith.
- One transition sentence into the demo. **No UI tour** — the interface will explain itself
  as you drive it.
- **Headline:** "A clean recording doesn't mean you recorded what you meant to."

### 4 · Live demo — the heart · 4:00–9:30 (~5.5 min)
A story in beats, engineering woven in as justification. Detail in §3.
- Open **cold** on both panes, one scale — state it as a property: *"same colour, same power."*
- **Peak (fixed-dish version):** the burst contrast. On a bursty signal in the array's look
  direction, the directional pane **catches spikes the omni averages away**, in real time.
  *"That spike the directional just caught? An omni-based test says it isn't there. That's the
  false all-clear this lab exists to prevent."*
- Audience signal: a **phone speed test** (never a transmitter — see §6).
- Drive one control (retune / FFT size): narrate the **ring buffer** holding continuity under
  retune, **snap-and-tell** on FFT size, and the **driver readback** that verifies Apply
  ("it didn't take — here's exactly why").
- The strongest "I built a UI for striqt" beat, on screen: a capture field that is
  **auto-generated from striqt's schema** — *"I didn't hand-write this form; striqt describes
  its own settings and LINDA renders them. Add a field to striqt and it shows up here."*
- Second screen: the **same LINDA** on a Pluto/USRP + Pi. *"Same software — a radio an order
  of magnitude cheaper."* (Order-of-magnitude, not dollar figures — see §6.)
- **Headline:** near-empty; the screen is the demo.

### 5 · Close — callback + reach · 9:30–11:30 (~2 min)
- Callback to the guard band: the measurement that answers it now has **eyes**, **honesty**,
  and **runs anywhere**.
- The one rigour sentence (this is where "it was hard" lives, in 8 seconds): a **26-finding
  audit, every finding fixed, each with a written verification step.** No standalone bug
  story — momentum stays on the demo.
- Reach: any crowded-spectrum coexistence fight — CBRS vs. radar, 6 GHz Wi-Fi, radar
  altimeters.
- End on **your contribution.** Never on "future work," "questions?", or a contact slide.
- **Headline:** "LINDA makes a trustworthy spectrum measurement you can watch — on any radio."

---

## 3. The demo, choreographed (the one part worth over-preparing)

Order = a story, not a feature tour.

1. **Both panes already live before you start talking** (viewer running; do not boot on stage).
2. **State the scale property:** left pane omni, right pane the array, one shared scale —
   *same colour is the same power.* This is a designed correctness property, said as a
   feature, not recovered from a bug.
3. **The peak — burst contrast (no rotation):** point at a bursty signal in the dish's look
   direction. Over a few seconds the array pane catches spikes the omni pane misses. Deliver
   the "false all-clear" line on a spike the audience just watched appear.
4. **Audience beat:** phone speed test; watch it register differently across the two panes.
5. **One control, with the engineering:** retune or change FFT size; narrate ring buffer /
   readback / snap-and-tell as the *reason* the thing on screen behaves well.
6. **Schema-generated form:** show a field; deliver the "striqt describes its own settings" line.
7. **Second screen:** same software on the cheaper radio + Pi.

**De-risking (rehearse the tech, not just the talk):**
- Pre-record a clean run of the burst contrast full-screen; rehearse narrating over it so a
  hardware hiccup is invisible.
- `--demo` synthetic mode as the third fallback (still looks live).
- A calm fallback earns more trust than an awkward silence; visible panic is what costs the room.

---

## 4. Open items — resolve in rehearsal

1. **[MAKE-OR-BREAK] Is there a bursty signal in the fixed dish's look direction in the actual
   room?** The peak now depends on this, since the dish can't be rotated. If the dish points
   at a wall or dead air, the burst contrast won't appear — fall back to the phone-speed-test-
   toward-the-dish version, or the pre-recorded backup. Confirm this on site before the talk.
2. **Is the standing omni-vs-array power difference visible** on the room's ambient RF, on one
   scale, at a glance? If yes, you can open the peak statically ("same signal, same instant,
   directional says loud, omni says quiet") even before a burst lands.
3. Aric/Dan present ⇒ credit **Dan for striqt**, **Aric for the measurement framing / URSI
   paper** explicitly, at least once each.

---

## 5. Deliberately cut

- **The "what LINDA is" section before the demo** — explain-then-show. The demo opens cold.
- **The standalone war story after the demo** — it follows your peak with a retrospective and
  kills momentum. Engineering is woven into the controls instead; rigour is one close sentence.
- **Dish rotation as the peak** — the dish is fixed. Replaced by the burst contrast.
- **A transmitter for the audience beat** — out of scope and a bad idea at a federal facility.
  Phone speed test only.
- **Dollar prices on slides** — "instrument-grade vs. a couple-hundred-dollar radio"; the
  order of magnitude is the point and it survives being vague.
- **"Tower-synced" describing the live view** — striqt has PSS/SSS machinery, but that's a
  separate measurement from the calibrated spectrogram the live display runs. Don't imply it.

---

## 6. striqt talking points — the accurate phrasings

- "**Batched real-time** signal analysis" — real-time capable; lacks a real-time *view*.
- "**Sees everything, shows you none of it live**" — the correct replacement for "blind."
- "**13 measurements, five of them 5G-specific**" — a serious instrument, not a plotting library.
- "**Y-factor calibrated to real dBm**" — metrology, not pictures. Most respectful credit to Dan.
- "**Publication-grade figure styles**" — batch is a design choice.
- "**`sensor-sweep spec.yaml` → zarr**" — what running a measurement looks like, in one line.
- "**LINDA's capture form is generated from striqt's own schema**" — the single best
  "I built a UI for striqt" sentence, and it's demonstrable on screen.

---

## 7. Grounding — every striqt claim re-verified against source

Per the project's own rule (nothing asserted that isn't in a document, the source, or read off
the device):

| Claim | Verified in source |
|---|---|
| Real-time capable, not "blind" | `striqt/README.md` line 1: "batched **real-time** signal analysis on CPU or GPU" |
| Overflow raises immediately (not "hours later") | `on_overflow='except'` default — `sources/base.py:56`, `soapy.py:316/481`, `controller.py:484` |
| Pre-commit validation exists | `pyproject.toml:67` → `check-sweep = "striqt.cli.check_sweep:cli"` |
| Capture form from striqt schema | `capture_editor_schema()` (`striqt_web_server.py:633`) imports `json_schema` from `striqt.analysis.specs.helpers`, runs it over the sweep spec |
| Y-factor calibration → real dBm | `sensor/lib/calibration.py:28 compute_y_factor_corrections(dataset, Tref=290.0)` |
| 13 measurements, 5 cellular | 13 files in `analysis/measurements/`; cellular: PSS, SSS, SSB spectrogram, cyclic autocorrelation, resource-power histogram |
| Publication figure styles | `figures/ieee.mplstyle`, `nist_report.mplstyle`, `presentation_full_width.mplstyle`, +3 |
| Guard-band stakes, >30 dBi, omni-averages-spikes, Wasserstein, 125 MS/s | `Project_Reasoning.pdf` pp. 1, 3, 5, 8–10 |
| Only proven on Deepwave; silent sample-drop at high rate | `CLAUDE.md` (striqt 0.7.0 patches; `QUALIFIED_MAX_RATE_HZ`) |

**Caveat:** the mounted striqt tree is a *later* snapshot than the v0.7.0 pinned on the radio.
The talk-level claims above hold in both — but do not quote fine API details from it on a slide.

---

## 8. Sources on presentation craft

- Naegle et al. (2021), "Ten Simple Rules for Effective Presentation Slides," PLOS Comput Biol
  — [PMC8638955](https://pmc.ncbi.nlm.nih.gov/articles/PMC8638955/): one idea/slide, ~1 min/slide,
  assertion headlines, export to PDF, keep screenshot backups for anything that plays.
- Randy Olson, **ABT** (And/But/Therefore) narrative framework — the spine tool used above.
- "The art of the 15-minute talk" (EGU): ≤2–3 min on background; 3–4 conclusions max.
- Michael Ernst, "How to give a technical presentation": first content slide is motivation, not
  an outline; end on contributions.
- Live-demo de-risking: record a backup, rehearse the tech, per-risk fallbacks, a calm recovery.
