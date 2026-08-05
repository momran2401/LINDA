
# LINDA — Speaker Script (v5)

Built on the approved structure. Deck explains, radio proves, two switches.

Changes from v4: new opening · "why not just a dish" answered · IQ folded into striqt's first
line · field campaign moved *before* the pipeline · the archive contradiction fixed · speed test
gone · TDD corrected and moved live · every line you marked corny is rewritten or cut · the
verification paragraph compressed to two sentences.

**Length: 1,430 spoken words, about 10:00 at 145 wpm, plus roughly 90 seconds of demo pauses.
Call it 11:30.**

---
# BLOCK 1 — DECK
---

`→ slide 2`

Three and a half to three point eight gigahertz. The Department of Energy uses that stretch of
spectrum to watch dams. Reservoir levels, grid monitoring, telemetry off equipment nobody can
drive out to every day.

It has just been sold to phone companies.

---

Spectrum works a lot like land. There is a fixed amount of it, the federal government controls
it, and it carves it into bands, writes the rules for what each band can be used for, and
auctions pieces off.

Which works fine, right up until the piece you're selling sits next
to something that cannot be interrupted.

`→ slide 3`

That's the mid-band. Federal-only for a very long time, point-to-point links carrying data that
cannot drop out — and now the same stretch is going to carriers for 5G.

So somebody has to answer a question: how close can you put a commercial 5G transmission station to one of
those links before you break it?

---

`→ slide 4`

The fix is a guard band. Empty spectrum left between the two so neither is standing on the
other. And somebody has to decide how wide it is.

You can be wrong in both directions. Leave too little, and leakage from the commercial side
starts knocking out federal links. Leave too much, and you've fenced off a public resource
nobody is allowed to use, and left billions of dollars of auction revenue on the table.

NIST doesn't get to pick that number. What NIST does is hand the people who do pick it a
measurement they can trust.

---

`→ slide 5`

So what are we actually measuring?

Traditionally, you measure a band like this with an omnidirectional antenna. `→ point to cart`
One of these. It hears everything, from every direction, at once.

Which is also the problem with it, in the National Telecommunications and Information Administration's own words, "with
an omnidirectional antenna, directional variation gets smoothed out" A 5G beam sweeps past for a
fraction of a second, and the omni averages it in with the entire sky.

But the receiver you're protecting isn't an omni. It's a dish. Highly directional, more than
thirty dBi of gain, aimed at one tower. When that beam sweeps past, the dish is pointed straight
at it.

Same interference. Two very different numbers.

`→ pause`

So why not just use a dish and be done with it? Because a dish only tells you about one
direction, and you don't know in advance which direction is going to matter.

The omni is the reference — it tells you what's out there at all. The dish tells you what the
protected receiver actually experiences. The measurement isn't either of those numbers on its
own. It's the difference between them.

And you can't measure a difference with one antenna.

---

`→ slides 6 and 7`

So that's what this is. An omni on the top, a directional antenna on the side, both coming down
through a bandpass filter and an amplifier into the radio at the bottom. A Deepwave. running striqt. my mentor dan's library!

---

`→ slide 8`

And here's how the data actually gets collected. A one hundred millisecond recording, every five
minutes, running continuously for about a week.

So you wheel the rig onto a roof, you point it, and you leave.

---

`→ slide 9 — striqt`



`→ slide 10`

There's one thing it can't tell you. Whether any of it was worth collecting.

striqt has no idea what the antenna was pointed at. It will process a week of noise exactly as
carefully as it processes a week of data, and the file it hands you looks the same either way.

A cable came loose. You put the amplifier on backwards, which I have definitely never done. Or
the band was quiet and nothing happened.

You find out at your desk. A week later.

`→ pause. Then straight to the LINDA slide. No bridge line.`

---

`→ slide 11`

This is Live IQ Navigation and Display Application. Or, as it's known by its stage name, LINDA.

It's how you watch it happen. Same radio, same library, same measurements — live, in a browser,
from anywhere in the world.

---

`→ slide 13 — screenshot, layout callouts`

Here's what it looks like.

Two spectrograms, one for each antenna. Frequency runs across, time runs down, and colour is
power. Underneath is a power spectral density plot — the same data, but power against frequency
instead of time.

Up here is the frequency it's tuned to, and which band that is. And down the left is the band
monitor, which I'll come back to.

---

`→ slide 14 — screenshot, control-surface callouts`

This row is sixteen fields of exactly what the radio is running right now. Sample rate, gain,
FFT size, frame rate.

Look at this one. `→ fft 1024→1008` I asked for one thousand and twenty four. The maths striqt
runs needs a multiple of twenty eight, so it used one thousand and eight — and it shows you both
numbers, not just the one I asked for.

`→ UNCALIBRATED badge` And that says uncalibrated, because the live view is. It will not print a
unit it hasn't earned.

Six tabs down the side. Display, PSD, measurement, capture, record, and the operations log.

---

`→ slide 15 — URL`

That address is the radio on this cart. Right now.

Pull it up, sign in as **viewer**, and follow along on your own screen.

`→ give them fifteen seconds. Do not fill the silence.`

---
# BLOCK 2 — LIVE
---

`→ switch the projector to the radio`

Alright. Let's look at the real thing.

Everything on your screen is happening now.

It's parked at 3750 megahertz, which is the band this entire talk is about. And it's mostly empty. That's what a protected federal band looks like from a conference room.

So let me move it!

---

Twenty five ninety three. T-Mobile.

`→ retune. Say nothing until it lands on their screens too.`

It retuned. And every screen in this room moved together.

---

And Now I'm going to ask this radio for a sample rate it physically cannot run.

`→ type 20 MS/s and Apply`

It didn't take it. And it didn't ignore me either. It snapped to the nearest rate this radio can actually run, and it said so — right there in the log — along with why

LINDA doesn't just take my word for anything

Every setting is checked thourougly before anything gets applied.

When I put in any config. I ask the hardware directly: what rates do you accept, what range do you
tune, how many antennas do you have.

And for anything only striqt can judge, LINDA builds the
real analysis — the actual striqt measurement, with your settings in it — and runs it on a
buffer of zeros first. If it's going to break, it breaks there and never touches the radio or reach your screen.

And once a setting is applied, it turns around and asks the radio what it actually did, then waits for a
frame captured *after* the change landed and Only then does it tell you it worked.

---

Look at the two panes.

`→ point` This one is the omni. This one is the dish. They are capturing the Same signal, same instant — and one of
them is much brighter and Not just the bursts but The background too.

Those bands running down the bright pane are TDD. The tower and the phone share one frequency
and take turns on it. The wide bright bands are the tower transmitting. The narrow gaps are
handsets answering back — and that asymmetry is why your download is often faster than your upload.

You can compare the two panes by eye because they share one colour scale. One range is computed
across both of them together. so a given colour is calibrated live to guarantee you the same power on the left as it is on the
right.

But you don't have to eyeball it. Down here at the band monitor. It prints the difference.

`→ read the live number`

That's the gap between the reference antenna and the dish. That
difference is the measurement. That's the number the guard band gets
argued over.

---

And each one of you can drag across the green box on Anywhere you'd like.

`→ let them do it`

Every one of you just measured a different slice of that band. And that isn't thirty requests to
my radio — the analysis is running in your browser.

---

One more thing. And this one exists because halfway through the summer my mentor aric told me I was using striqt not like he like he intended

Remember the field recording is a hundred milliseconds. So instead of grabbing a new short
window fifteen times a second, this takes one continuous hundred millisecond capture, runs the
full striqt analysis over all of it in a single pass, and lets you step through it.

`→ scrub`

Which means this isn't just a way of looking at the radio. It's a preview of exactly what the recorder is about to write.

---

So let's write it.

`→ Record tab`

It takes the settings you're already looking at, and allows you to choose the duration and what goes into
the file.

Every screen in the room has a banner now. That's this radio writing a striqt archive — same
format the lab has always used, out of the same library.

And as an added bonus, I can pull it off this radio onto any laptop with one simple command.

`→ stop the recording`

---

All of this installs in one command. the installer works out which radio you plugged in, how many receive
channels it has, what sample rates it'll accept, what frequency range it reaches — and then it even
sets up the drivers, the service, the network, and the whole environment, and hands you a URL you can access immediately.

But wait Gabe asks, Does that mean the same software runs on
different radios?

`→ the Pi`

Yes, Gabe.

That's a Different radio from a Different manufacturer on a Different computer. LINDA found the radio by name, worked
out how many channels it has, and those sample rates came off it a few seconds ago. without any input from me

---

`→ unplug the dish. Say nothing. Let it scroll.`

The dish just went dead.

That's a an amplifier going bad. And I know right now — which means I fix it right now, and start the
recording again.

Not next Tuesday.

---
# BLOCK 3 — DECK
---

`→ feature wall`

There's a lot more in there than I have time for.

`→ next slide`

And this question isn't only about 5G and dams. CBRS sharing a band with naval radar. Six
gigahertz Wi-Fi running over utility microwave links. Anywhere spectrum gets crowded, somebody
has to measure the overlap, and somebody has to make a decision on the number that comes back.

---

`→ close`

The problem was that you recorded for a week, and found out afterwards whether it was worth
anything.

Now you watch it happen live. From a roof, from a browser, from your phone, from anywhere in the world, and on almost any radio you can afford.

With that, ladies and gentlemen — I hope your tax dollars were put to satisfactory work.

Thank you.

---
---

## Timing

| Block | Section | Target |
|---|---|---|
| Deck | Mid-band → guard band → NIST | 1:30 |
| Deck | The antenna problem, and why both | 1:15 |
| Deck | Rig → field campaign | 0:40 |
| Deck | striqt → the gap | 1:05 |
| Deck | LINDA → two screenshot builds → URL | 1:20 + pause |
| Live | Go live, empty band, retune | 0:50 + pause |
| Live | Illegal rate → two ways it knows | 1:05 |
| Live | Two panes, TDD, the difference | 1:00 |
| Live | Everyone drags | 0:30 + pause |
| Live | AHAWI | 0:40 + pause |
| Live | Record | 0:35 + pause |
| Live | Unplug | 0:25 + pause |
| Live | Installer → Pluto | 0:50 + pause |
| Deck | Feature wall → everywhere → close | 1:00 |

Running long? Cut in this order: the "six tabs" line on slide 14 · the shared-colour-scale
paragraph · AHAWI · "Different manufacturer, different computer."

---

## Things in the script that need confirming

**1. Which pane is which.** The script says `this one is the omni, this one is the dish` and
points, deliberately — so it works whichever port is which. But you need to *know* before you
stand up. In your screenshot, RX1 on the left is the bright one, at `Δ RX1−RX2 = +18.7 dB`.
Bright = more gain = the dish. If that's actually the omni, something is wrong with the cabling,
not with the script.

**2. The Pluto's price.** I've written "two hundred dollar radio." Your v4 said two thousand.
A PlutoSDR is roughly $150–230. Say the real number for whatever is actually on that screen —
and note that two hundred against twenty thousand is a *better* line than two thousand was.

**3. The footer.** It's baked into the full-bleed screenshot on slides 19–20 of the current deck
and it will be four inches tall on the projector. Fix the string, retake the shot.

**4. TDD direction.** Wide bright bands = downlink = tower transmitting = your download. That's
why download beats upload. You had it inverted when we talked.

**5. The delta number.** Don't pre-write it. `→ read the live number` is in the script on
purpose — the screenshot said 18.7 dB, the room will say something else, and quoting a number
that doesn't match the screen is the one thing that would undercut the whole section.

---

## What changed, and why

**The opening.** Concrete before abstract. Dams first, then the auction, then the idea that
spectrum is finite and owned. "Much like oil, minerals, or any other natural resource" is a
thesis statement, and thesis statements are the weakest possible first sentence — the room
doesn't know yet why they should care about the category. Naming a dam gets you a stake in nine
words.

**"Why not just a dish" is answered, and it does double duty.** The omni is the reference, the
dish is the device under test, the measurement is the difference. That kills the obvious
objection, justifies the two-antenna rig, and pre-loads the band monitor — so when a number
appears on screen later, the room already knows what a difference is *for*.

**IQ is one sentence inside striqt**, as you asked. Enough that nobody thinks it's an IQ test,
short enough that it isn't a lesson.

**The field campaign moved ahead of the pipeline.** The week has to be on the table before the
gap can hurt. In v4 you described striqt's excellence and then revealed a problem the room had
no stake in.

**The archive contradiction is fixed.** "A beautiful, clean archive" argued against itself. Now
the archive is *technically* perfect and *substantively* empty: striqt has no idea what the
antenna was pointed at, and processes noise as carefully as data. Same fact, no contradiction.

**The bridge into LINDA is silence.** "So I built the thing that tells you now" was doing work
the slide change does better. The section ends on "a week later," you pause, the title comes up.

**The verification paragraph went from four sentences to two.** "Then it sends it, then it turns
around, then it waits" was a list of steps nobody could hold. What survives is the part you said
flies under the radar, and it now gets its own paragraph and its own build: *LINDA builds the
real analysis and runs it on a buffer of zeros first.*

**The speed test is gone and TDD is live.** You cut the speed test because nothing showed. The
TDD banding is visible in the pane whether or not anyone is downloading, because the tower's
sync burst broadcasts constantly — so the explanation now hangs on something that's always
there instead of something that has to work on cue.

**AHAWI has a reason now.** It's not a third view. The field recording *is* 100 ms, so AHAWI is
a preview of the file Record is about to write. That's why it sits directly before Record.

**Cut without replacement:** the 32-element dipole array · "wireless backhaul for places where
running fibre across a mountain isn't realistic" · "striqt turns that into science" · "the
omni's answer is the optimistic one" · "which is the dangerous direction to be wrong in" · "the
dish takes the whole thing" · "the measurement doesn't know the difference" · "and you know the
number on the screen is the number the radio actually produced" · the hotspot line at the Pi.

**Kept exactly as you wrote it:** the tax dollars close.

---

## Pre-flight

**Blocking**

1. **The `interns` role** — `app.js:60` renders `"fuck you 🖕"` and a full-screen image takeover
   for that username. Change it, or make the slide unmissable that the username is `viewer`.
2. **The footer string** — `index.html:478`, on every screen in the room *and* baked into your
   screenshot slides.
3. **`ADMIN_USER`** — no passwords, the username is the credential, and the client retries
   forever as a takeover queue. Set it to something unguessable in `radio.env`.
4. **Sequential broadcast** — `striqt_web_server.py:1848` awaits each client in turn; one bad
   phone stalls the frame loop for everyone. `asyncio.gather(..., return_exceptions=True)`.

**Recommended**

5. Run with `--quantize --fps 5` for the room.
6. AHAWI ships ~5.7 MB per capture to every client. Rehearse it with five phones on the tunnel.
7. `app.js:692` — log the successful verdicts too, not just the failures, so "it said so right
   there in the log" is literally true on their screens.

**Rehearsal**

8. Time it standing, with the demo running.
9. Confirm 20 MS/s snaps to 15.36, and that the log line is legible from the back.
10. Check 2593 at idle — the sync burst should be visible in both panes with nobody using the
    network. If it isn't, the two-pane beat needs traffic and you'll want a phone streaming.
11. Read the live delta out loud a few times so an unexpected number doesn't throw you.
12. Confirm the twenty thousand and two hundred dollar figures.
13. Tell Gabe his line is coming.
14. Have the pre-recorded backup queued full screen.
