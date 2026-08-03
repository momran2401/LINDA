# Slide review, second pass

Much better. The things that were wrong are mostly right now, and the deck is 21 slides instead
of 23. What's left is four duplicates and cuts, two things that will embarrass you on a projector,
and the same three diagrams I flagged last time that still aren't in there.

---

## Fix these before anything else

### 1. There's an AI artifact on slide 9

The footnote ends:

> "...the foundation of any trustworthy measurement. **[cite: 9]**"

That's a generation leftover and it will be projected at eight feet wide. Whatever else you do,
delete it.

### 2. Slide 21 ends mid-sentence

> "You know you are recording what you meant to record. Hardware faults surface immediately**,**"

Trailing comma, nothing after it. Finish it or cut the clause.

### 3. You have two striqt slides

**Slide 9** ("Raw voltage is calibrated into SI power measurements," three steps) and **slide 10**
("striqt · by Dan Kuester," five steps) are the same slide twice, back to back.

Delete slide 9. Slide 10 is the one you want: it names striqt, credits Dan in the header, has the
five-step pipeline, and carries both pull-quotes. It's the best-executed slide in the deck.

### 4. You have two Radio Agnostic slides

**Slides 18 and 19** are near-identical. Keep 19 (the body copy is better: "the measurement quality
scales with the hardware, the workflow does not change"). Delete 18.

---

## Cuts

**Slide 17, the Real-World Validation chapter card.** It introduces nothing now. The slide it used
to lead into ("Catching Problems in Real Time") is gone, correctly, so this divider now announces a
chapter and then hands off to Radio Agnostic. Cut it.

**Slide 6, The Dual-Antenna Approach.** Its three cards say what slide 8's signal chain shows, one
slide later. Take the headline if you like it, drop the slide.

Four cuts takes you to 17. Add the demo holder and the missing AHAWI step and you're at 19, which
is about 25 seconds a slide across your non-demo minutes. If you want to get to a comfortable
pace, the two easy merges are **2 + 3** (spectrum is finite / the mid-band conflict are one idea)
and **11 + 12** (the LINDA chapter card and the What-LINDA-Is slide).

---

## Slide 8: it's a grid, not a chain

You built the six boxes I asked for, but they're laid out two columns by three rows, so the eye
reads:

```
OMNI      DISH
BANDPASS  LNA
DEEPWAVE  striqt → LINDA
```

That's a matrix. A signal chain has to read in one direction or it isn't a chain. Two ways to fix:

**Horizontal, with the antennas merging** (what I'd do):

```
OMNI ─┐
      ├─→ BANDPASS ─→ LNA ─→ DEEPWAVE ─→ striqt → LINDA
DISH ─┘
```

**Or vertical**, one column of five with the two antennas side by side at the top and a downward
arrow between each stage.

Either way, add the arrows. Right now nothing on the slide indicates flow, and the whole point of
the slide is that signal goes through those stages in that order.

Keep the caption. *"The part you can't see by looking at the cart"* is a good line and you turned
a note into a joke, which is the right instinct.

**One correction on that slide:** the Deepwave box says `2-channel SDR · 125 Msps`. 125 is the
converter clock, and we already took that out of your script for exactly this reason. Make it
`2-channel SDR · 15.36 MS/s today` or just drop the rate. Same on slide 10, step 01: change
`Complex baseband · 125 Msps` to `up to 125 MS/s`, which is the true version.

---

## The AHAWI reveal needs to be built, not just typed

Slides 15 and 16 have the right words in the wrong form. Both are default text boxes, around 14
point, floating in the dead centre of an otherwise empty slide. From the back of the room that's
a whisper on a blank wall, and the joke needs to be legible instantly or it doesn't land.

Three slides, all left-aligned on the same 0.54 in margin as every other slide:

**Slide A**
```
THE THIRD MODE                                        ← kicker, 10pt, accent

Apparently  How  Aric  Wanted  It                     ← 40pt bold, one colour
```

**Slide B** — identical layout, identical position, only the colour changes:
```
Apparently  How  Aric  Wanted  It
▲          ▲    ▲     ▲       ▲
A H A W I in accent blue; every other letter greyed back to ~#C3CBD8
```

**Slide C**
```
AHAWI                                                 ← 80pt bold, letter-spaced
─────────────────────────────
One coherent 100 ms capture. One striqt pass.
Replayed 20 ms at a time.                             ← 16pt

100 ms is the recording length in the real field campaign. The 5G sync
burst repeats every 20 ms, so each slice holds exactly one of them.      ← 12pt, dim
```

The reason B has to be pixel-identical to A is that the only thing that should change between
clicks is the colour. If the text moves, the eye tracks the movement instead of the letters. Say
nothing on B.

You currently have A and C. B is the one doing the work.

---

## Still missing, third time asking

**The band adjacency bar on slide 3.** Slide 3 is still titled "The Mid-Band Conflict" and still
shows a tower with a dish. A tower is not a conflict. Two blocks touching is a conflict.

This is the single highest-value thing left in the deck, because the entire talk rests on federal
and commercial being *neighbours*, and no slide has ever shown that. One bar, two blocks, no gap,
labelled 3.5 and 3.8. Then the same bar again on slide 4 with a gap opened in the middle, so the
audience recognises the graphic instead of parsing a new one.

**The spike inset on slide 5.** Your illustration shows radiation patterns. Your argument is about
time: the omni averages the spike away, the dish takes it. Two small traces on a shared vertical
scale, flat-with-a-bump over flat-with-a-spike, labelled "same moment, same interference."

**A near-empty demo holder slide.** One blank slide you advance to as the demo starts. If your
display switching hiccups, a blank slide is a much better thing to be sitting on than a paragraph
about TDD.

**The LINDA logo.** `live/web/linda-logo.svg`. It appears exactly once, inside a laptop mockup on
slide 12. It should be large on that chapter card and small in the corner of every content slide.

One note on what you cut: "Reading the Power Difference" is gone, which is defensible since you
show it live. But **8 dB now appears on the Impact slide with nothing anywhere that showed where
it came from.** Either that's fine because the demo carries it, or the Impact slide should say
"omni vs dish, measured live" under the number. Your call.

---

## What you fixed, and got right

Worth naming, because these were the substantive ones:

- **Slide 10** is now the best slide in the deck. striqt named, Dan credited in the header, five
  honest steps, and the two pull-quotes are exactly the right two.
- **Design decision #1** now says the display goes blank deliberately and nothing is shown under
  the wrong label. That matches what the room will actually watch happen.
- **Design decision #2** is now the true version of the schema claim.
- **Design decision #3** is CHECK → SEND → READ BACK → WAIT with MISMATCH in red. That's the
  strongest engineering claim in your talk and it finally looks like a process instead of a
  paragraph. One nit: it's a stacked list, so card 3 runs taller than cards 1 and 2 and the row
  looks uneven. Four small boxes in a 2×2 inside the card would even it out.
- The four slides that described the demo in text are gone. That was the right call and it's most
  of what was wrong last time.

---

## Offer, restated smaller

Three flat-vector graphics, matched to the card style you're already using on slides 8 and 10 (not
the isometric illustrations, which I can't match):

1. The band adjacency bar, in both states, drop-in PNG or a two-slide `.pptx`
2. The omni/dish spike inset
3. The AHAWI three-slide reveal, properly typeset

Say which ones and they'll be in the folder. The band bar is the one I'd take.
