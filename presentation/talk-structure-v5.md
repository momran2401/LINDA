# LINDA — Structure v5

Structure only. No script yet. Approve or change this and I'll write the words against it.

**The rule this is built on: the deck explains, the radio proves.** Every "here's what you're
looking at" moment happens on a screenshot, before you go live. Live time is spent only on
things a still image cannot do — that it's happening now, that it's honest, and that it breaks
when you unplug it.

**Two switches. That's it.** Deck → radio → deck.

---

## Three flags from your screenshot, before anything else

**1. Your panes are the other way round.** In the screenshot, **RX1 (left) is the bright one**
and RX2 (right) is nearly empty — `Δ RX1−RX2 = +18.7 dB`, so RX1 is the antenna hearing more.
The script currently says "left is the omni, right is the dish" and "the right pane is
brighter." Confirm which physical antenna is on Port 0 before you write a word of narration
around it. If the dish is on Port 0, the script has to say left.

**2. The delta is 18.7 dB, not eight.** Use the real number off the screenshot when you teach
it, and read the live one when you get there. Eight was my placeholder and it's wrong.

**3. The footer is in the screenshot.** Slides 19 and 20 are the full-bleed UI at 10 inches
wide, and along the bottom it reads *"ur tax dollars paid for labor behind the gorilla."* That
is going on a projector in front of your division. Change the footer string and retake the
screenshot.

---

# BLOCK 1 — DECK (~4:00)

| # | Slide | Its one job |
|---|---|---|
| 1 | Title | — |
| 2 | Spectrum is finite | Someone owns it, carves it, sells it |
| 3 | The mid-band conflict | A federal band that can't drop out is being sold to 5G |
| 4 | The guard band | One number, wrong in both directions, and NIST doesn't pick it |
| 5 | **The antenna problem** | Omni is the standard tool and it smooths the beam away. The dish is what you're protecting. **You need both, and here's why** |
| 6 | The hardware | The cart |
| 7 | The measurement rig | Signal path into the Deepwave |
| 8 | **NEW — the field campaign** | 100 ms every 5 minutes for a week. Roof, point, walk away |
| 9 | **NEW — what IQ is** | One sentence and a small diagram. Not intelligence quotient |
| 10 | striqt | IQ → resample → calibrate → analyse → archive. Dan's library, three sentences |
| 11 | **NEW — the gap** | striqt doesn't know what the antenna was pointed at |
| 12 | LINDA | The name, and what it is |
| 13 | Devices | Browser · kiosk · **shell** (you're adding the shell device — do it, it's a free wow) |
| 14 | **Screenshot — build 1** | Layout: two panes, axes, PSD |
| 15 | **Screenshot — build 2** | The difference: bright vs dark, Δ 18.7 dB, TDD blocks |
| 16 | **Screenshot — build 3** | The control surface: config header, log, six tabs |
| 17 | **URL + username: viewer** | They join. Last thing before you go live |

**Why the reorder in 8–11.** You were right. The workflow has to come *before* the pipeline,
because "a week of data" is what makes the gap hurt. Right now you describe striqt's excellence
and then reveal a problem the room has no stake in yet. Reordered: they learn you get one shot
at a week → they learn what striqt does with it → *and striqt has no idea what the antenna was
pointed at.* The gap lands because the week is already on the table.

**Slide 5 needs the "why not just a dish" answer.** Your instinct plus what you've heard your
mentors say — survey vs victim's-eye, and the omni as *reference* — aren't in tension. They're
the same idea: **the omni is the reference and the dish is the device under test.** The
measurement isn't either number, it's the difference between them, and a difference needs
something to be a difference *from*. That single sentence answers "why not just a dish," makes
the two-antenna rig obviously necessary, and sets up the band monitor demo, which literally
prints a difference.

You also have NTIA's own words for the omni's weakness: with an omnidirectional antenna the
reported level *"would appear uniform, and directional variations would tend to be smoothed
out."* That replaces "the omni's answer is the optimistic one" with a citation.

---

# BLOCK 2 — LIVE (~5:30)

**Projector on the radio. No slides at all until the end.**

| # | Beat | What it proves | Time |
|---|---|---|---|
| L1 | They're in, it's moving | It's real and it's now | 0:20 |
| L2 | Retune to 2593 | It retuned · it didn't tear · every screen moved together | 0:40 |
| L3 | Ask for a rate it can't run | It snaps, says why. **Two ways it knows**: the radio's own limits, and striqt's rules tested on a buffer of zeros | 1:10 |
| L4 | The verdict in the log | On your screen and all of theirs | 0:25 |
| L5 | Both panes live + the delta | The thing slide 15 taught, now happening | 0:40 |
| L6 | Everyone drags their own band | Thirty measurements, one antenna, running in their browsers | 0:35 |
| L7 | AHAWI | **This is a preview of exactly what the recorder writes** | 0:45 |
| L8 | Record | It writes the real archive · pull it with one command | 0:35 |
| L9 | **Unplug the dish** | The whole thesis, in silence | 0:30 |
| L10 | Switch input → the Pi + Pluto | Different radio, different maker, different computer. Installer line spoken here, no slide | 0:50 |

**The speed test is gone.** Slide 15 teaches TDD and the brightness difference off a still where
both are unmistakable. Live, the band just has to have the tower's sync burst in it — which
broadcasts whether anyone is using it or not. Nothing in Block 2 now depends on making the band
loud on cue.

**L7's justification, which the script was missing.** The field recording *is* 100 ms. So AHAWI
isn't a third viewing mode — it's the recorder's exact capture, held still so you can look at
it. That's why it sits immediately before Record, and it's the answer to "why does this matter."

**L9 → L10 is your pivot.** Unplug, "not next Tuesday," walk to the second display while
plugging the dish back in. No slide in between.

**Slide 22 (`git clone`) is deleted.** The line survives as speech at L10, and "two-command
install" becomes one tile on the feature wall. That deletion is what buys you the two-switch
structure — it was the only thing dragging you back to the deck mid-demo.

---

# BLOCK 3 — DECK (~1:30)

| # | Slide | Its one job |
|---|---|---|
| 18 | **Feature wall** | Everything you didn't have time for. One line, then wallpaper |
| 19 | This is everywhere | CBRS and naval radar, 6 GHz Wi-Fi over microwave links |
| 20 | Close | The before, the after, the tax-dollars line |
| 21 | Acknowledgements | — |

---

# The screenshot walkthrough — three builds, one image

Same full-bleed screenshot, three slides, callouts appearing in sequence. Every value below is
real and already in your image, so nothing needs faking.

### Build 1 — "what am I looking at"

- **`SPECTROGRAM PORT 0 — RX1`** and **`PORT 1 — RX2`** → one pane per antenna
- **The axes** → frequency across the bottom (`2585.5 → 2600.5 MHz`), time downward, colour is power
- **`POWER SPECTRAL DENSITY`** → same data, power against frequency instead of time
- **`2593.000 MHz` + the `Band 41 · n41` pill** → where the radio is, and what that band is

### Build 2 — "and here's the whole argument"

- **The two panes side by side** → one is bright, one is nearly empty. Same signal, same instant
- **The horizontal banding in the bright pane** → this is TDD. The tower and the phone share one frequency and take turns. The wide bright bands are the tower transmitting; the narrow gaps are your phone answering. *That asymmetry is why your download is faster than your upload*
- **`scale auto [−103, −69]`** in the header → both panes are on one colour scale, so a colour means the same power in both
- **`Δ RX1−RX2  18.7 dB`** in the band monitor → **the measurement.** Not a picture of a signal — the difference between the reference antenna and the protected one
- **The green band on the PSD** → that's the slice being measured, and it's draggable

### Build 3 — "and it tells you everything about itself"

- **The config header** → sixteen fields, always showing what the radio is *actually* running: `rate 15.36 MS/s · gain 0 dB · fft 1024→1008 · rows 569 · duration 20 ms · analysis calibrated · fps 14`
- **`fft 1024→1008`** → it asked for 1024 and the maths needs 1008, so it snapped and *shows both numbers*. That's the honesty demo, previewed
- **`UNCALIBRATED`** badge → it will not claim calibrated units it hasn't earned
- **The `LOG` panel** → every change lands here, on every connected screen
- **The six tabs** → DISPLAY · PSD · MEASURE · CAPTURE · RECORD · OPS. One glance at the scope, no demo needed
- **`connected` · `ADMIN` · `PRO`** → multi-user, role-gated

Pick four or five callouts per build, not all of them. Build 2 is the one that matters — that's
where the room decides whether the project is interesting.

---

# What you need to make

1. **Retake the screenshot** with the footer fixed. Everything else in it is perfect.
2. **Three build slides** off that screenshot (14–16).
3. **Three small new slides** — the field campaign (8), what IQ is (9), the gap (11). The IQ one
   wants a tiny diagram; the other two can be text on your existing style.
4. **Add the shell device** to slide 13, as you said.
5. **The feature wall** (18) — spec is in `feature-wall.md`.
6. **Delete slide 22** (`git clone`).
7. **Confirm the port assignment** — which antenna is Port 0.

---

# Open questions

1. **Is Port 0 the dish or the omni?** Everything in Build 2 hangs on it.
2. **Do you want the acknowledgements before or after the close?** Right now it's the last slide,
   which means your talk ends on a list of names rather than on your line. I'd put the close last
   and the acknowledgements just before it — or leave them up during Q&A.
3. **How long is the Pi/Pluto input switch, physically?** If it's more than about eight seconds
   of dead projector, the Pluto beat needs a line to talk over while it comes up.
