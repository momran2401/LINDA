# Feature wall — everything the script doesn't say

The script names about fifteen things. This is the rest of what's in the repo, grouped, with
nothing repeated from the talk. ★ marks my picks for the slide.

**What the script already covers, so it must NOT go on the wall:** two-antenna capture · shared
colour scale · band monitor delta · PSD statistics view · verified retune · illegal-value
snapping · the log · AHAWI · recording · pulling recordings · auto-detection · radio agnostic ·
hotspot · multi-viewer.

---

## Measurement

- ★ **Four analysis backends** — calibrated spectrogram, raw-FFT quicklook, PSD statistics, 5G SSB spectrogram
- ★ **Channel power, live** — native RMS and peak detectors with crest factor
- ★ **Occupancy** — histogram plus a stated fraction-above-threshold, not a vibe
- ★ **5G cell detection** — PSS/SSS correlation, peak-to-median gate, tracks persistence across captures
- ★ **Measurement presets** — five expert configurations, one click each
- **Peak markers, peak hold, min trace** — hold the worst case while you watch
- **Crosshair readout** — exact frequency and power under the cursor
- **RX1−RX2 difference trace** — the two antennas subtracted, plotted live
- **FFT size 256 to 4096** — snapped to the grid striqt's calibrated path actually runs
- **LO null** — removes the radio's own DC spur, sized in Hz, not in bins
- **Absolute RF or baseband offsets** — axis labelled either way, never ambiguous
- **Y-span control** — auto, or pinned at 10/20/40/60 dB
- **Burst alignment** — folds residual power at the burst period so a carrier can't bury it
- **Power-strip navigation** — click anywhere in a capture's power history to jump there
- ★ **GPU accelerated** — cupy on the radio, automatic CPU fallback, disclosed either way

## Verification and honesty

- ★ **Verified operations log** — every change numbered, staged, and given a verdict
- **Six verdicts** — success, verified, unverified, mismatch, failed, superseded
- **Readback judged against intent** — knows striqt offsets the LO deliberately, so it can't false-alarm
- **Gain reported, not judged** — because judging it would alarm on every healthy change
- ★ **Unqualified-rate warning** — above 30.72 MS/s it says nobody has proven the pipeline keeps up
- **Backend fallback disclosure** — ask for SSB at an impossible rate and it tells you what it ran instead
- **Honest units** — the live view says "dB relative"; only recordings claim calibrated power
- **Won't invent a cell ID** — reports the candidate, refuses the number it can't prove
- **Coherence flag** — a capture marks itself as possibly seamed rather than pretending
- **Auto-revert** — a setting that breaks analysis reverts to last-good instead of freezing the view
- **Verified restart** — Reset Radio confirms a new boot ID, not just a "sure, restarting"
- **Disclosed quantisation** — frames ship 8-bit with the scale in the header

## Access and deployment

- ★ **One-command install** — 22 stages: drivers, udev, service, environment, network, secrets
- ★ **Browser, kiosk, or SSH** — three frontends over one shared core
- ★ **Works with no internet** — plot library vendored and checksum-verified
- ★ **Role-based access** — three roles, one of which can touch the radio
- **Signed sessions** — HMAC cookies, 24-hour expiry, role sealed inside the signature
- **One driver at a time** — single admin slot, everyone else queues
- **Terminal viewer** — full curses waterfall over SSH, no display required
- **Command-line control** — status, watch, logs, set, gps, self-test
- **Reversible self-test** — changes seven settings, verifies each, restores everything
- **Hardware qualification tool** — proves a radio end to end before you trust it
- **Service logs in the browser** — journal streamed to the ops view
- **Idempotent installer** — re-run it after swapping radios, offline, and it just works
- **mDNS** — reach it by name, no DNS server

## Data

- ★ **GPS in every capture** — position, fix quality, error bars; NaN when unknown, never 0.0/0.0
- ★ **Real striqt archives** — zarr, byte-compatible with the lab's existing tools
- **CRC-verified writes** — atomic rename, so a partial file can never look finished
- **Recording catalog** — browse and inspect what's on the radio from the browser
- **Two ways to pull data** — rsync over SSH, or a stdlib-only HTTP client
- **Choose what gets archived** — spectrogram, PSD, channel power, raw IQ, any combination
- **Raw YAML override** — drop to a full striqt sweep spec when you need to
- ★ **Export** — CSV, PNG, and measurement metadata as JSON

## Interface

- ★ **Phone-native layout** — a separate layout, not a shrunk desktop
- **Focus mode** — tap any graph, it takes the screen
- **Two skill modes** — full control, or a simplified tuner
- **Station presets** — FM, GPS L1, ADS-B, CBRS, ISM; greyed out if your radio can't reach them
- **Automatic band naming** — it tells you you're in n41, or CBRS, or GNSS L1
- **Dark and light themes** — data surfaces stay dark on purpose
- **Per-viewer pause** — freeze your own screen without touching the instrument
- **Live health** — ring fill, frame age, uptime, boot ID
- **Applied-config readout** — sixteen fields, always showing what is actually running
- **Self-describing capture form** — widgets built from striqt's own schema
- **Settings as files** — load and save striqt sweep JSON

## Engineering

- ★ **186 automated tests** — fourteen of sixteen modules need no radio at all
- **Tested to the same standard as hardware** — the fake-radio test demands the same three proofs
- ★ **Demo mode** — a full synthetic radio, so the whole thing runs with nothing plugged in
- **Dedicated capture thread** — one thread only drains the radio, so a slow analysis can't drop samples
- **One shared core** — nineteen modules, zero duplicated logic across three frontends
- **Transmit** — a full transmit path with a legal gate and an audit trail *(left off the wall by choice)*

---

# FINAL PLACEMENT — 23 slots, reading order

Mined from every file in the repo. Positions match the layout you generated.

### Row 1 — numbers (top row, most eyes, best five)

| Slot | Numeral | Caption |
|---|---|---|
| 1 | **186** | automated tests |
| 2 | **1** | command installs everything |
| 3 | **100 ms** | coherent capture |
| 4 | **0** | internet required |
| 6 *(far right)* | **36** | validated controls |

### Row 2 — numbers

| Slot | Numeral | Caption |
|---|---|---|
| 1 | **4096** | point FFT |
| 2 | **4** | analysis backends |
| *(centre)* | | **monitor image** |
| 5 | **7** | radio families supported |
| 6 | **3** | frontends, one core |

### Row 3

| Slot | Tile |
|---|---|
| 1 | Verified operations log |
| 2 | GPS in every capture |
| 5 | Auto-detects your radio |
| 6 | **39** · one-tap station presets *(number)* |

### Row 4

| Slot | Tile |
|---|---|
| 1 | Records striqt archives |
| 2 | GPU accelerated |
| 5 | Phone-native layout |
| 6 | Broadcasts its own Wi-Fi |

### Row 5

| Slot | Tile |
|---|---|
| 1 | Runs with no radio |
| 2 | Automatic band naming |
| 3–4 *(wide)* | **Live 5G cell detection** |
| 5 | Terminal viewer over SSH |
| 6 | Burst-aligned replay |

### The bench — swap in if something doesn't fit

**No price tags and no radio specs anywhere on this slide.** Nothing that describes a particular
piece of hardware — only what the software does. Bench, all software-side:

Numbers: **3** proofs per setting · **21** tunable analysis parameters · **5** deployment modes ·
**5** one-click presets · **6** verdicts per change · **4** export formats · **10** GPS fields per
capture · **20** RF bands auto-named · **64** replay segments · **0** JavaScript dependencies ·
**23** HTTP routes · **7** CLI commands · **1** admin, unlimited viewers · **4×** smaller frames

Words: Reversible self-test · CRC-verified archives · One shared colour scale · Draggable band
monitor · Hardware qualification tool · Live journal streaming · Kiosk fullscreen mode ·
Schema-driven settings · Occupancy metrics · Peak hold and min trace · Focus any graph ·
Zero JS dependencies

---

# Raw material (full catalogue below)

No images. No hero. No wordmark. Just features, packed as tight as they'll go.

With the pictures gone, **the numerals are the hero.** Ten big numbers scattered through
thirty small word tiles is what makes it read as a wall instead of a bulleted list — that
size contrast is doing the job Apple's product photo was doing.

### Cut for redundancy

These were on the list and are now off it, because the script says them out loud. Putting them
on the wall makes the wall look thin rather than deep:

~~100 ms coherent capture~~ (you describe AHAWI) · ~~1 command to pull your data~~ (you say it
at the Record beat) · ~~22 setup steps~~ (you list what the installer does) · ~~$150 radio~~
(you do the price comparison) · ~~2 antennas, one radio~~ (the whole first half) · ~~Real striqt
archives~~ (you say it while recording) · ~~Its own Wi-Fi hotspot~~ (you say it at the Pi) ·
~~Auto-detects your radio~~ (you list what it works out) · ~~Verified operations log~~ (you
point at the log) · ~~Two-antenna comparison~~ · ~~RX1−RX2 difference~~ (the band monitor beat)

> Note: your script says "two thousand dollar radio" for the Pluto. A Pluto is closer to $150–230.
> Worth checking which radio you're actually putting on that screen before you quote a number.

### The ten numbers

| Number | Caption |
|---|---|
| **186** | automated tests |
| **4096** | point FFT |
| **7** | radio families detected |
| **5** | deployment modes |
| **5** | measurement presets |
| **4** | analysis backends |
| **3** | access roles |
| **30** | MS/s qualified on hardware |
| **6** | operation verdicts |
| **0** | internet required |

Spares: **19**-module shared core · **8**-bit frames, disclosed scale · **24**-hour sessions ·
**15** frames per second

### The thirty word tiles

**Measurement**
Live 5G cell detection · Channel power, live · Occupancy metrics · Peak hold and min trace ·
Crosshair readout · Burst alignment · GPU accelerated

**Trust**
Honest units · Auto-revert on failure · Verified restart · Unqualified-rate warnings ·
Reversible self-test · Hardware qualification tool

**Data**
GPS in every capture · CRC-verified writes · Recording catalog · CSV · PNG · JSON export ·
Choose what gets archived

**Reach**
Phone-native layout · Works offline · Terminal viewer over SSH · Kiosk mode ·
Command-line control · Signed sessions · Live health readout

**Interface**
Runs with no radio · Self-describing capture form · Focus mode · Dark and light themes ·
Station presets

Forty tiles, zero overlap with anything you say. If it's too dense on screen, cut from the
bottom of each group — they're ordered strongest first.

---

# Design prompt

Attach two things with this: the Apple M1 spec-wall slide, and three or four slides from your
own deck. The prompt takes composition from the first and everything visual from the second.

> Design a single 16:9 presentation slide: a dense wall of software features for a technical
> conference talk. It is scenery the audience absorbs peripherally while I talk over it, so it
> should read as calm and confident at a glance, not as a document.
>
> **Look — take this entirely from my deck, which I've attached.** Sample the colours directly
> off those slides: the background, the text, the secondary text, the accent. My deck is dark —
> a near-black background with off-white text and muted grey captions. Tiles are rounded
> rectangles filled a few percent lighter than the background so they read as a subtle lift
> rather than as boxes, with no borders and no drop shadows. The finished slide must look like
> it has always been part of that deck.
>
> **Colour rule, and I need this followed exactly: do not introduce a single colour that is not
> already visible in my attached slides.** In particular, do not use the bright saturated blue
> that design tools default to — no `#2563EB`, no `#3B82F6`, no "primary blue," nothing in that
> family, anywhere, at any opacity. Do not produce a light or white slide. **If you are ever
> unsure what accent to use, use none at all: set every numeral in the same off-white as the
> body text.** A fully monochrome version of this slide is a correct and preferred outcome. An
> accent is optional; the wrong accent is a failure.
>
> **Composition — take this from the Apple reference.** An irregular masonry grid of rounded
> tiles at *deliberately unequal sizes*, packed edge to edge with tight, consistent gutters and
> no visible column rhythm. The unevenness is the entire effect; a regular grid kills it.
> **Ignore the reference's colours completely** — I want its density and its layout logic, not
> its palette, and unlike the reference my slide has no photographs, no product images, no
> logos and no wordmark anywhere.
>
> **The numbers are the hero.** Ten tiles carry a very large numeral with a small grey caption
> beneath it. Those numerals are four to six times the size of any other text on the slide and
> they are what the eye lands on first. Make those ten tiles larger than the rest and scatter
> them across the layout rather than grouping them, so they set a rhythm across the whole
> surface. Set them all in off-white, or — only if the deck clearly supports it — in a colour
> sampled from the deck itself. Never in a colour you have chosen.
>
> **Everything else is a word tile:** two to four words, no number, no caption, no description,
> set small enough that the tiles clearly recede behind the numerals. Sentence case. These pack
> into the gaps around the number tiles at varying widths — some one unit wide, some two.
>
> **The ten number tiles** (numeral, then caption):
> 186 automated tests · 4096 point FFT · 7 radio families detected · 5 deployment modes ·
> 5 measurement presets · 4 analysis backends · 3 access roles · 30 MS/s qualified on hardware ·
> 6 operation verdicts · 0 internet required
>
> **The thirty word tiles:**
> Live 5G cell detection · Channel power, live · Occupancy metrics · Peak hold and min trace ·
> Crosshair readout · Burst alignment · GPU accelerated · Honest units · Auto-revert on failure ·
> Verified restart · Unqualified-rate warnings · Reversible self-test · Hardware qualification
> tool · GPS in every capture · CRC-verified writes · Recording catalog · CSV · PNG · JSON
> export · Choose what gets archived · Phone-native layout · Works offline · Terminal viewer
> over SSH · Kiosk mode · Command-line control · Signed sessions · Live health readout · Runs
> with no radio · Self-describing capture form · Focus mode · Dark and light themes ·
> Station presets
>
> **Constraints:** every tile from both lists must appear — forty tiles, none dropped, none
> merged, none invented. No title, no headings, no category labels, no icons, no images, no
> sentences, no explanatory text under any tile. Fill the frame edge to edge with a small
> uniform outer margin. Captions must stay legible from twenty feet.

## If it comes back wrong

The three failure modes, and the line to add:

- **It made a light slide.** → "The background must be near-black, matching the attached deck. Not white."
- **It used blue anyway.** → "Remove every blue element. Render the entire slide in greyscale plus off-white text only. Do not add any accent colour." Monochrome is the safe harbour — take it rather than negotiating.
- **The numerals aren't dominant.** → "The ten numerals must be at least four times the height of the word-tile text, and their tiles must be visibly larger."
- **It made a tidy uniform grid.** → "Tile sizes must vary noticeably — mix one-unit and two-unit widths, and vary heights. No two adjacent rows should share the same column boundaries."

## If you're building it by hand instead

Place the ten number tiles first, spread across the frame with no two touching, then flow the
word tiles into the gaps. On a 10 × 5.625 in slide: number tiles roughly 1.7 × 1.2 in, word
tiles roughly 1.15 × 0.7 in, gutters a consistent 0.08 in, outer margin 0.14 in. That comes to
about forty tiles with room to breathe. If you end up with clean full-width rows, break three
tiles onto different spans and it'll come back.

## Two notes on using it

**Where it goes.** Up during the "this is everywhere" beat, right before the close. You're
talking about CBRS and 6 GHz Wi-Fi while it sits there. Say one line and one line only — *"and
there's a lot more in there than I have time for"* — then let it be wallpaper. If you start
reading tiles aloud you've turned scenery into a list and lost ninety seconds.

**Forty tiles is about the ceiling, and it's a real one.** At 16:9 that puts word tiles around
10 pt. Legible from twenty feet, but only just — which is fine here, because nobody is meant to
read the whole thing. The number tiles carry it from the back of the room and the word tiles
create the impression of depth behind them. If you push past forty-five the numerals lose their
size advantage and it stops reading as a wall and starts reading as a spreadsheet.
