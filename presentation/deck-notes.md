# Deck notes — LINDA_SURF_2026_deck.pptx

14 slides, 16:9, rebuilt against `talk-script-v3.md`. Speaker notes are on every slide.

The old deck was a Google Slides export: bullet walls, stock photos, and a slide that described
the demo in text while the demo was on screen. This one carries the beats your script doesn't
carry out loud, and gets out of the way during the live section.

## Slide map

| # | Slide | Script beat |
|---|---|---|
| 1 | Title | Up before you speak. Don't read it. |
| 2 | Spectrum is finite, and it is being sold | Opening, the band, the auction |
| 3 | Too narrow breaks the link. Too wide burns billions | Guard band |
| 4 | The honest answer depends on which antenna | Omni vs dish |
| 5 | So the lab records both, at the same time, on one radio | The rig |
| 6 | striqt turns raw IQ into calibrated, real-dBm measurement | Dan's library |
| 7 | A clean recording doesn't mean you recorded what you meant to | The gap |
| 8 | LINDA | The reveal |
| 9 | **Near-empty holder** | The whole live demo |
| 10 | Apparently How Aric Wanted It | AHAWI, click 1 |
| 11 | Same, initials in blue | AHAWI, click 2 |
| 12 | AHAWI | AHAWI, click 3, then cut back to live |
| 13 | Same software. Any radio | Install + second radio (the Gabe beat) |
| 14 | Close | Callback and reach |

Fourteen slides across sixteen minutes, and roughly eight of those minutes are slide 9, so the
talking sections average well under a minute a slide. That's the right ratio.

## The AHAWI build

Three slides, click through them. No animation to break on someone else's laptop, and the timing
stays in your hands.

- **10** — the phrase, plain. Say it straight. No wink yet.
- **11** — same phrase, A H A W I in blue, the rest greyed back. Say nothing. Let them find it.
- **12** — AHAWI, large, with what it actually does underneath.

This sits inside the demo, so it's a fast cut out and back. Three clicks, maybe fifteen seconds.
If it feels like it breaks the flow in rehearsal, skip straight to 12 or drop all three and say
the line over the live view.

## Swapping in the real NIST logo

The mark on every slide is a typographic placeholder, positioned and sized so the official file
drops straight in:

- **Box:** 1.30 in wide × 0.42 in tall, top-right, right edge at 0.62 in from the slide edge, top
  at 0.36 in.
- Delete the two placeholder text boxes ("NIST" and "SURF 2026"), insert the official logo, and
  snap it to that box.
- Do it once on slide 1, then copy-paste in place (Cmd+Shift+V) across the rest, or move the mark
  into the slide master.

Get the file from the NIST brand/identity page or from your CTL group. It's a controlled
government trademark, so use the official artwork rather than anything reconstructed.

## The LINDA logo

Drawn natively from `live/web/linda-logo.svg`, so it's vector and scales cleanly: the five-bar PSD
envelope, the shared frequency axis, and the wordmark, with the same bar heights and opacities as
the app. It appears large on slides 1 and 8, and small in the bottom-left corner elsewhere.

## Type and colour

Arial throughout, deliberately. It's the one sans that renders identically on any machine you
might end up presenting from. If you're confident about the laptop and you have IBM Plex Sans
installed, swapping the theme font matches the deck to the LINDA UI exactly.

Colours come from LINDA's own light theme in `live/web/style.css`, so the deck and the app are the
same product: `#2563EB` accent, `#1A2029` ink, `#566072` secondary, `#CF3A3A` for the failure
side of the guard band.

## Rebuilding

`deck_build/` has the generator. `python3 slides.py` from inside that folder regenerates the
`.pptx`. Edit `slides.py` for content, `kit.py` for the design system. Worth keeping if you expect
to give this talk more than once.

## One thing to check

Slide 13 says $20,000 and $2,000. Confirm both figures before you present, since they're now on
screen and not just spoken.
