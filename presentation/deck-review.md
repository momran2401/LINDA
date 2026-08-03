# Slide review — "Development of Visual" draft

This is a real step up. The typography is clean, the isometric line-art illustration style is
consistent and it looks like a deck a lab would put its name on. Keep the aesthetic. Everything
below is about what the slides are *doing*, not how they look.

---

## The one problem that matters

**Twenty-three slides, and about eight minutes of talking to spend on them.**

Your script is 16 minutes. Roughly eight of those are the live demo, where the deck should be
invisible. That leaves 23 slides sharing eight minutes: **21 seconds each**. You will either
race, or you will abandon the deck halfway and it will sit on the wrong slide for five minutes.

And the reason it's 23 slides is the second problem, which causes the first:

**Almost every slide is written to be read, not spoken over.** Count the full sentences. Slide 3
has three paragraphs that are, near enough, three paragraphs from your script. Slide 13's body
text is your ring buffer explanation, transcribed. When a slide has a paragraph on it, the room
reads the paragraph. They cannot read and listen at once, so for eight seconds they stop hearing
you, and then they look up in the middle of your next sentence.

You do not need slides that say what you're saying. You need slides that show what you *can't*
say: adjacency, magnitude, before-and-after, and the shape of a signal.

Target: **13 to 14 slides.** Everything below gets you there.

---

## Cut these six

**Slide 12, The Spectrogram View.** You are diagramming a thing the audience is about to see live,
about ten seconds later, at full size, in colour, with real data in it. The drawing loses that
comparison badly, and the legend text is unreadable past the third row. Delete the slide and say
"time runs across, frequency runs down, colour is power" over the real one.

**Slide 15, The Speed Test Demo.** Same problem. It describes TDD blocks in text while TDD blocks
are on screen. Keep one thing from it: nothing. Say it live.

**Slide 19, Catching Problems in Real Time.** Three paragraphs explaining what a loose cable looks
like, immediately before you unplug a cable and show them. This is the strongest 20 seconds in
your talk and a slide of text in front of it defuses the surprise.

**Slides 18 and 21, the Real-World Validation and Beyond 5G chapter dividers.** You have four
chapter cards (7, 10, 18, 21). At 21 seconds a slide, a slide that carries no information is a
luxury. Keep **two**: "The Hardware" and "The Software: LINDA." Those two are real act breaks in
the script. The other two aren't.

**Slide 6, The Dual-Antenna Approach.** It's slide 8 (The Measurement Rig) with less detail. Merge:
take slide 6's headline, slide 8's illustration, and one line of body.

That's six slides gone and you've lost nothing you actually say out loud.

---

## The visual that's missing, and it's the important one

**Slide 3 has no picture of the conflict.** The slide is titled "The Mid-Band Conflict," and the
illustration is a tower with a dish on it. A tower does not show a conflict. Adjacency does.

What that slide needs is a horizontal frequency bar:

```
   3.5 GHz ─────────────────────────────────────── 3.8 GHz
   ┌──────────────────────────────┬────────────────────┐
   │   FEDERAL INCUMBENT          │   AUCTIONED · 5G   │
   │   DOE microwave links        │   new base stations│
   └──────────────────────────────┴────────────────────┘
                                  ↑
                       nothing in between
```

One bar, two blocks, touching. The whole talk hangs on those two blocks being neighbours, and
right now no slide shows it. Put it on slide 3, full width, and delete the three text blocks
underneath it — the bar plus your voice says all three of them.

Then **reuse the same bar on slide 4**, with a gap opened between the blocks and the gap labelled.
That's what a guard band *is*, and reusing the identical graphic means the audience recognises it
instantly instead of parsing a new diagram.

Which brings me to:

**Slide 4's chevron is the wrong shape.** A 1 → 2 → 3 chevron means *sequence*. Too Little, The
Guard Band, and Too Much are not steps you go through, they're three positions on one dial with
the right answer in the middle. Use the band diagram with the gap drawn three ways: too narrow
(leakage arrow crossing into federal), right, too wide (a hatched "unusable, unsold" block). Same
graphic, three states, no new vocabulary.

---

## Visuals that don't match what you say

**Slide 5, The Antenna Problem.** The split comparison illustration is the best drawing in the
deck and I'd make it bigger and delete all three text blocks around it. But it shows *radiation
patterns*, and your argument is about *time*: the omni averages the spike away, the dish takes it.

Add a small inset, bottom of the slide, two stacked traces on the same vertical scale:

- **Omni:** a mostly flat line with a barely visible bump
- **Dish:** the same flat line with one tall spike at the same instant

Label them "same moment, same interference." That inset is the entire research programme in one
picture, and it's the thing your current illustration can't say. Two line traces, no axes needed.

**Slide 16, Reading the Power Difference.** The illustration is a 3D spectral surface. What you
actually do on stage is drag a region on a flat 2D PSD and read two numbers and a delta. The
picture and the action don't match, and someone will look for the 3D plot in the live UI and not
find it.

Replace with a simple 2D PSD sketch: frequency across, power up, two curves (omni lower, dish
higher), a shaded vertical band across both, and three readouts to the side — `RX1`, `RX2`,
`Δ 8.0 dB`. That's literally what the band monitor shows, so the slide previews the live thing
instead of contradicting it.

**Slide 17, AHAWI.** Two problems.

The illustration is a bar chart. Bar charts do not communicate "the edges swim" versus "the edges
line up." What does: two small waterfall strips side by side, left one with block edges staggered
row to row, right one with the edges stacked in a perfect vertical column. That is the whole
point, it's drawable in two minutes, and it's instantly legible from the back of the room.

Second, and bigger: **the headline throws away the joke.** "AHAWI: Aligned Waterfall Mode" is the
sanitised version. You asked for the letter reveal and it isn't in the deck. It should be three
slides, clicked through:

1. `Apparently  How  Aric  Wanted  It` — full width, plain, all one colour.
2. Same line, with **A H A W I** in accent colour and the rest of each word greyed back.
3. `AHAWI` alone, large, with one line under it: *One coherent 100 ms capture. One striqt pass.
   Replayed 20 ms at a time.*

Three clicks, about fifteen seconds, and you say nothing on slide 2 of the three. Let them find
it. Aric is in the room and this is the moment that lands with him.

**Slide 8, The Measurement Rig.** The cart illustration is genuinely good and specific — the
Jackery, the 5-port supply, the labelled Deepwave. But the actual cart is three feet away from
you. Pointing at a drawing of an object the audience can see is a strange move.

Keep the slide, change what it's about. Show the **signal chain** instead:
`OMNI ──┐` and `DISH ──┘` → `BANDPASS` → `LNA` → `DEEPWAVE (2 ch)` → `striqt → LINDA`. That's the
part you can't see by looking at the cart, so the slide adds something the room doesn't already
have. Move the cart drawing to a corner at quarter size if you want to keep it.

---

## Content problems

**Slide 9 undersells striqt badly, and it never names it.**

The slide is called "From Voltage to Measurement" and shows three circles: Raw IQ → Y-Factor →
Calibrated. Dan is in the room. His library is the instrument your whole project sits on, and it
appears on this slide as an unnamed middle circle with a thermometer icon.

Rebuild it as a five-step pipeline, and put his name in the kicker:

> **THE INSTRUMENT · striqt, by Dan Kuester**
> **striqt turns raw IQ into calibrated, real-dBm measurement.**
>
> `IQ` → `RESAMPLE` → `CALIBRATE` → `ANALYSE` → `ARCHIVE`
> (complex baseband) (exact, in the frequency domain) (Y-factor vs a known noise source)
> (13 measurements, 5 of them 5G) (labelled zarr, units on every axis)

Then two short lines under it: *"It refuses rather than approximate"* and *"Y-factor is what makes
it metrology."* Those are the two things that make striqt an instrument rather than a plotting
library, and they're the most generous accurate credit you can give.

**Slide 14, decision #1 contradicts what the room will see.** It says "no partial frames, no visual
artifacts." You and I already fixed this in the script: the display *does* go blank on a retune,
deliberately, and they'll watch it happen. The card should say the pause is intentional and that
nothing is ever shown under the wrong label. "No Tearing" as a title is fine; drop "no visual
artifacts."

**Slide 14, decision #2 says "no manual configuration files to maintain or sync."** That's a
cousin of the "I never touch the code" line we cut, and Dan will know the field list in `app.js`
is hardcoded. Say what's true and still impressive: *striqt publishes a machine-readable
description of its own settings — types, ranges, defaults — and LINDA renders the form from it.*

**Slide 23 ends on metrics.** 10s / 8 dB / $2K is a good format, but "$2K Minimum Hardware" is the
weakest of the three and it isn't an impact, it's a spec. And your script ends on *"whether the
week is going to be worth anything"* — that's the last thing the room should be looking at, not a
number panel.

Either move the metrics to slide 20 (Radio Agnostic, where $2K actually belongs) and make 23 a
single closing statement, or keep two metrics and put the closing line in 32pt underneath.

---

## What's missing entirely

**A near-empty demo holder slide.** Right now, when you cut to the live view, whatever slide is up
stays up. Add one deliberately blank slide (small LINDA mark bottom-left, nothing else) that you
advance to as you start the demo. If your screen switching fails, a blank slide is a much better
thing to be sitting on than a paragraph.

**The LINDA logo.** It's in the repo at `live/web/linda-logo.svg` and the only place it appears in
this deck is inside a laptop mockup on slide 10. It should be large on the LINDA chapter slide and
small in the corner of every content slide. It's your project's mark; use it.

**Aric's credit.** Dan gets a mention via the Y-factor content; Aric gets nothing until the AHAWI
joke, which is affectionate but isn't a credit. One line on the title or closing slide:
*Mentors: Dan Kuester (striqt), Aric Sanders (measurement framing).*

**The verified-operation beat as a picture.** Decision #3 on slide 14 is your strongest engineering
claim and it's a paragraph. It's four steps and it wants to be four boxes:
`CHECK → SEND → READ BACK → WAIT FOR A FRESH FRAME`, with a red `MISMATCH` badge hanging off the
end. Ten words instead of forty, and it previews the thing you're about to demonstrate live with
the sample-rate snap.

---

## Suggested final order

| # | Slide | Change |
|---|---|---|
| 1 | Title | Keep. Add the LINDA mark and Aric's credit line. |
| 2 | Spectrum as a Finite Resource | Keep headline, cut both text cards to one line. |
| 3 | The Mid-Band Conflict | **Add the band adjacency bar.** Delete the three text blocks. |
| 4 | The Guard Band | Same bar with the gap, three states. Kill the chevron. |
| 5 | The Antenna Problem | Illustration bigger, text gone, **add the spike inset.** |
| 6 | ~~Dual-Antenna Approach~~ | Merged into 7. |
| 7 | The Hardware (chapter) | Keep. |
| 8 | The Measurement Rig | Rebuild as the **signal chain**. Cart drawing small. |
| 9 | striqt | **Name it. Credit Dan.** Five-step pipeline. |
| 10 | The Software: LINDA (chapter) | Keep. **LINDA logo large.** |
| 11 | What LINDA is | Keep three cards, cut each to one sentence. |
| 12 | ~~The Spectrogram View~~ | Cut. |
| 13 | **Demo holder (blank)** | New. |
| 14 | Ring Buffer | Keep. Consider a gap-vs-quiet-band inset. |
| 15 | Three Design Decisions | Fix #1 and #2. Make #3 four boxes. |
| 16 | ~~Speed Test Demo~~ | Cut. |
| 17 | Reading the Power Difference | Replace the 3D surface with a **2D PSD + drag band + Δ**. |
| 18–20 | **AHAWI, three-slide reveal** | New. Replaces the current slide 17. |
| 21 | ~~Real-World Validation (chapter)~~ | Cut. |
| 22 | ~~Catching Problems in Real Time~~ | Cut. |
| 23 | Radio Agnostic | Keep. Move the $2K metric here. |
| 24 | Spectrum Coexistence Everywhere | Keep as is. It's good. |
| 25 | Close | End on the line, not on metrics. |

Fourteen slides. About 35 seconds each across the non-demo minutes, which is a pace you can
actually hold.

---

## What to keep doing

The isometric line-art style is working. It's specific, it's consistent, and it reads as
engineering rather than clip art. The oil pump on slide 2 is a good literal joke. The exploded SDR
on slide 20 is exactly right for "radio agnostic." The cart drawing is the most detailed thing in
the deck and it shows you actually looked at the hardware.

The problem was never the art. It's that on about half the slides the art is decoration sitting
next to a paragraph, when it could be carrying the argument and letting you delete the paragraph.

---

## Offer

Four of the fixes above are diagrams I can build for you as drop-in graphics, matched to your
palette and line weights: the **band adjacency bar** (reused for the guard band), the **omni/dish
spike inset**, the **2D PSD with the drag band and delta**, and the **swimming-vs-aligned waterfall
strips**. I can also build the **AHAWI three-slide reveal** as a standalone `.pptx` you paste in.

Say the word and I'll do them. The band bar is the one I'd do first, because slide 3 currently
asks the audience to imagine adjacency and nothing on screen helps them.
