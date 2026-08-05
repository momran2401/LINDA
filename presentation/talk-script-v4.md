# LINDA — Speaker Script (v4)

The restructure. striqt is down to one paragraph. LINDA starts at about 3:40 instead of
8:00 and now runs six and a half minutes. The room is on the radio the whole time. Recording
is in. AHAWI is in but short. The unplug moved to the end of the Deepwave section, where it
works as the pivot to the second radio.

**Length: 1,510 spoken words, about 10:30 at 145 wpm, plus roughly 90 seconds of demo pauses.
Call it 12 minutes.**

Pre-flight checklist is at the bottom. Three items on it are blocking — read them before you
put the URL up.

---

(Much like oil, minerals, or any other natural resource, spectrum is finite.)[can we do a better intro] In the U.S. the
federal government is the one who controls it. It carves it into bands, writes the rules for
what each band can be used for, and sells chunks of it off to telecom companies.

---

The mid-band, roughly 3.5 to 3.8 gigahertz, has been federal-only for a very long time.
Agencies like the Department of Energy run point-to-point links in that range:( a dish on
a tower talking to a dish on another tower.) These are used to communicate things like Dam telemetry, reservoir levels, and grid
monitoring. It's wireless backhaul for places where running fibre across a mountain isn't
realistic, but most importantly, its communications that cannot drop out.

That same mid-band is now being auctioned off to carriers for 5G. Which raises the question my
lab has to answer: how close can you put a loud commercial base station next to one of those
links before you break the whole system?

---

The way you answer that is with a guard band. An empty strip of spectrum you leave between two
competing chunks so they aren't stepping on each other.

And you can be wrong in both directions. Leave too little, and leakage from the commercial side
starts knocking out federal links. Leave too much, and you've fenced off a public resource
nobody is allowed to use, and left billions of dollars of auction revenue on the table.

NIST doesn't get to pick that number. What NIST does is hand the people who do pick it a
measurement they can actually trust.

---

So you put an antenna on a roof and you measure for a while. And what people normally use is an
omnidirectional antenna, `→ point to cart` much like this one here. An antenna that hears
everything, from every direction, all at once.

But the receiver you're actually trying to protect isn't an omni. It's a dish, pointed at one
specific thing, with more than thirty dBi of gain in that one direction. When a 5G beam sweeps
across that dish, the omni averages that moment in with everything else in the sky, so it
barely registers it. The dish takes the whole thing.

Same interference. Two completely different answers. (And the omni's answer is the optimistic
one.)[corny sounds like AI]

`→ pause`

Which is the dangerous direction to be wrong in.

So the lab doesn't pick one. It records both, at the same time, on one radio, and compares
them. In a setup resembling the one here.

---

An omni antenna on the top, a directional antenna on the side, both coming down through a
bandpass filter and an amplifier into the radio at the bottom, a Deepwave.

(Now, the real rig uses a 32-element dipole array instead of a dish, because an actual dish at
these frequencies is too big and too heavy to move between rooftops. I only had to wheel mine
across a building. So I got to bring the dish.)[remove??]

---

What turns that into a measurement is a library called
striqt. Dan's library.

`→ look at Dan. Let the beat sit. Don't oversell it.`

What comes off the converter is voltage with a timestamp on it. (striqt turns that into science)[science??].
It resamples onto the exact grid 5G is built on. It calibrates the receiver against a noise
source of known temperature. And then
it runs the analyses — spectrogram, power spectral density, channel power over time, and the 5G
ones, which don't just find the sync burst, they tell you which cell it came from and which
beam. 

It all comes out labelled, with real units on every axis, and gets archived to disk.

There's just one thing it can't tell you. Whether any of it was worth collecting.

---

he way this data gets gathered is a one hundred millisecond recording, taken every
five minutes, running continuously for about a week. So what you essentially do is that you wheel a rig
onto a roof, you point it, and you walk away for a week.

And striqt will hand you a beautiful, clean archive whether that dish was pointed at the sky or
straight at a wall. A cable came loose. You put the amplifier on backwards,
which I have definitely never done. Or the band was just quiet

All of it comes back looking like perfectly good data, and you don't find out until you're back
at your desk, a week later.

(So I built the thing that tells you now.)[corny line]

---

This is Live IQ Navigation and Display Application. Or, as it's known by its stage name, LINDA.

LINDA is how you watch it happen, live, right now 

It is a visualization software, viewable over Browser, kiosk, or shell. accesible on a hotspot, ethernet, or from the internet, from anywhere in the world

---

Ladies and gentlemen. A spectrogram.

Time runs across, frequency runs down, and colour is power. Two panes, because two antennas.
Left is the omni, right is the dish. And underneath is a power spectral density plot — same
data, but power against frequency instead of time.

And it's quiet. Because it's parked at 3750 megahertz, which is the band this entire talk is
about. That's what a federal band looks like from a conference room.

let me give it something to look at.

---

Twenty five ninety three megahertz. T-Mobile's mid band.

`→ retune. Stop talking until it settles on their screens too.`

Every screen in this room just moved together.

And watch what it didn't do. It didn't tear. And it didn't show you a single frame of the old
band with the new label on it. It went blank for a moment instead,and then it came
back.

LINDA doesn't take my word for anything.

Watch — I'm going to ask this radio for a sample rate it cannot run.

`→ type 20 MS/s and Apply`

It didn't take it. And it didn't quietly ignore me either. It snapped to the nearest rate this
radio will actually run, and then it told me it had done that, and why. Same thing if I ask for
a frequency it can't reach or a settings it can't display

How does it know? Two ways. The limits come from the radio itself —
I ask the hardware what rates it accepts, what range it tunes, how many antennas it has. And
for the things only striqt can judge, LINDA builds the real analysis and runs it on a buffer of
zeros first, so a setting that would break striqt never reaches your screen.

Then it sends it. Then it turns around and asks the radio what it actually did, because a radio
will happily accept a setting and quietly do something else. And then it waits for a frame
captured *after* the change landed, so it can't show you old data with a new label on it.

Only then does it say the change worked. `→ point at the log` And it's all in the log — on my
screen, and on every one of yours. If the radio had come back with something different from
what I asked for, every person in this room would see it, in red.

---

Now let me make that band loud. Speed test on my phone.

`→ start it, then stop talking until the blocks arrive`

There. Those blocks are my phone and the tower going back and forth.

That on-off pattern is TDD. Instead of using two frequencies, the tower and the phone share one
and take turns on it. The bright parts are the tower transmitting. The gaps are the tower
listening — that's my phone talking back.

---

Now look at both panes at once, because this is the whole reason the rig has two antennas.

Same signal, same instant, and the right pane is brighter. Not just the blocks — the background
too. Everything the dish hears sits higher than everything the omni hears.

That's the antenna gain, and you can only see it because both panes share one colour scale. One
range, computed across both of them together. So a given colour is the same power on the left as
it is on the right, and the two panes can be compared by eye.

And you don't have to eyeball it. Down here `→ band monitor` it prints the difference. About
eight decibels, right now, between what the omni thinks is happening and what the dish knows is
happening.

That disagreement is the measurement. That's what the guard band number gets computed from.

---

Now drag across the plot on your own screen. Anywhere you like.

`→ let them do it`

Every one of you just measured a different slice of this band. And that's not thirty requests to
my radio — the analysis is running in your browser. One antenna, thirty independent
measurements.

---

One more thing. And this came from my mentor telling me a halfway through the summer that my data display 
was going in a completely different direction that what he intended and wasn't using striqt to its fullest potential. 

Which is why we added a 2nd vieweing mode apparently how araic wanted it. AHAWI
Instead of grabbing a new short window fifteen times a second, it takes one continuous hundred
millisecond snapshot — the same length as a real field recording — runs the full striqt analysis
over the whole thing in a single pass, and then lets you scrub through it.

`→ scrub`

The edges line up now, because consecutive slices really are consecutive in time. And you can
step through it yourself, on your own screen, at your own pace.

---

`→ Record tab`

And when the picture looks just right, you can record. It takes the settings you're already looking at and fills in the relevent fields
as well as letting you choose the duration and what you want in that recording

`→ hit record, let the banner land on their screens`

That's this radio writing a striqt archive, the
same format the lab has always used, from the same library. 

And I pull it off this radio onto
any laptop with one command.

`→ stop the recording`

---

---

All of this installs in two commands. `git clone`, then `bash install_linda.sh`. It works out
which radio you plugged in, how many receive channels it has, what sample rates it'll accept,
what frequency range it can reach — and then it sets up the drivers, the service, the network,
and the whole environment, and hands you a URL.

Which is when Gabe asked me the good question. Does that mean the same software runs on a
different radio?

`→ Gabe. Then the Pi.`

Yes, Gabe.

Different radio, different manufacturer, different computer. It detected the radio by name. It
worked out how many channels it has. And the sample rates in that list came off that radio a
few seconds ago. Nobody typed them in.

So that's a twenty thousand dollar instrument on the cart, and a two thousand dollar radio on
that screen. (The measurement doesn't know the difference.)[corny]

---

`→ unplug the dish. Say nothing. Let it scroll.`

The dish just went dead. 

That's a cable coming loose. And I know right now — which means I fix it right now, and start
the recording again.

Not next Tuesday.

`→ plug it back in`

`→ feature wall`

And there's a lot more in there than I have time for.

That same question — is this new signal going to step on that critical one — is everywhere
right now. CBRS and naval radar. Six gigahertz Wi-Fi over utility microwave links. Anywhere
spectrum gets crowded, somebody has to measure the overlap, and somebody has to stake a
decision on the number that comes back.

---						
So. The problem was that you recorded for a week, and found out afterwards whether it was worth
anything.

Now you watch it happen — from a roof, from a browser, from your phone, from anywhere in the
world, on almost any radio you can afford. (And you know the number on the screen is the number
the radio actually produced, because the software checks, and tells you when it isn't.)[corny]

with that, ladies and gentlemen, i hope your tax dollars were put to satisfactory work.

Thank you.

---
---

## Timing

| Section | Target | Words |
|---|---|---|
| Spectrum → guard band → antennas → rig | 3:00 | 430 |
| striqt and the gap | 0:50 | 215 |
| LINDA: join, spectrogram, empty band | 0:50 | 145 + pause |
| Retune → doesn't tear → illegal rate → the log | 1:40 | 290 + pause |
| Speed test → TDD | 0:35 | 75 + pause |
| Both panes, colour scale, the eight dB | 0:50 | 135 |
| Everyone drags, PSD stats | 0:40 | 90 + pause |
| AHAWI | 0:40 | 95 + pause |
| Record | 0:35 | 70 + pause |
| Unplug | 0:30 | 45 + pause |
| Installer → Pluto → price | 1:15 | 175 + pause |
| Feature wall → everywhere → close | 1:10 | 165 |

If you're running long, cut in this order: the PSD statistics line, then AHAWI, then the
"different manufacturer, different computer" sentence.

---

## The demo order, and why it's this one

The radio boots at 3750, and there is nothing to see at 3750 — which is the point, and the
script now says so out loud: *that's what a federal band looks like from a conference room.*
Everything after that is forced by physics rather than by preference:

1. **You have to tune before you can run the speed test**, because the speed test only shows up
   in T-Mobile's band. So the retune isn't a demo you slot in wherever — it's the thing that
   gives the screen something to be about.
2. **The verification story belongs to the retune**, not to a separate beat. You just changed
   the radio in front of thirty people; that is the natural moment to say "and here's how you
   know it actually did what I asked." Snap the sample rate while you're already there.
3. **The speed test comes next and makes the band loud**, which is what the two-antenna
   comparison needs. At idle you'd be measuring the tower's sync burst, which is real but faint;
   the TDD blocks are what make the brightness difference obvious from the back of the room.
4. **The eight decibels lands on a loud band**, not a quiet one, and it lands after the room has
   already seen both panes with their own eyes.
5. **Then they drag their own**, because by now they know what they're dragging across.
6. **AHAWI last**, because it needs the 5G burst to be worth looking at.

---

## Blocking — do these before you put the URL up

**1. The `interns` role.** `app.js:60` — the deny message for that role is `"fuck you 🖕"`, and
instead of the small popup it triggers a **full-screen image takeover** (`#intern-block` →
`fortheinterns.jpg`). If one person in the audience types `intern`, that is on their screen in
a NIST talk. Either change the copy or be very clear on the slide that the username is
`viewer`. Also check the footer at `index.html:478` — it currently reads "ur tax dollars paid
for labor behind the gorilla," and it is on every screen in the room.

**2. Anyone who types `admin` gets your radio.** There are no passwords; the username is the
credential. A second admin is refused with code 4001, but the client retries every 1.2 seconds
forever — the server comment calls it a takeover queue. Set `ADMIN_USER` in `radio.env` to
something nobody will guess.

**3. The broadcast fan-out is sequential.** `striqt_web_server.py:1848` awaits
`ws.send_bytes(msg)` one client at a time. One phone on bad wifi with a full send buffer stalls
the frame loop for everybody. Wrapping that loop in `asyncio.gather(..., return_exceptions=True)`
is a three-line change and it's the highest-value thing you can do before this talk.

---

## Recommended, not blocking

**Run the server with `--quantize --fps 5`.** Rolling frames are ~98 KB float32 or ~25 KB
quantised. Thirty phones at 15 fps unquantised is roughly 44 MB/s through the tunnel; quantised
at 5 fps it's about 3.7 MB/s. The eight-bit quantisation discloses its own scale in the header
and the PSD is recomputed from the dequantised data, so nothing you can see is lost.

**AHAWI is the bandwidth risk.** A 100 ms capture at 15.36 MS/s is ~5.7 MB per message to every
connected client, and that's independent of FFT size — it scales with capture length × sample
rate. Two ways to de-risk it: shorten the capture for that beat, or drop the sample rate before
you switch modes. Rehearse it with five phones on the tunnel and see what happens.

**Put the operation verdict in the Log.** `app.js:692` currently sets the log level to `null`
for `success` and `verified`, so a *successful* operation is silent — only failures show up.
Change that to log every terminal verdict, and log the `readback` and `data-path` stages too.
Then the Log carries the whole story, the OPS tab becomes a glance rather than a stop, and the
"it's all in the log, on every one of your screens" line is literally true.

---

## Feature wall — suggested contents

No explanations, just boxes. It's up while you talk about CBRS and 6 GHz Wi-Fi.

GPS position in every capture · one-command install · browser, kiosk, or SSH · works with no
internet · runs on its own hotspot · 186 tests · role-based access · verified operations log ·
GPU accelerated · recording catalog · pull recordings with one command · presets for common
measurements · CSV / PNG / JSON export · peak hold and min trace · dark mode · works on a $150
radio

---

## Notes on the changes

**striqt went from 290 words to 215, and all of the mechanism came out.** The exactness of the
resampling and the Y-factor derivation were the expensive part and nothing later in the talk
paid them off. What's left does the one job that section needs to do: make the archive sound
excellent, so that "it will hand you that archive pointed at a wall" lands.

**The self-describing form is gone from the script.** It was the best striqt-adjacent detail,
but it needed 40 seconds of setup and the honest version of the claim is narrower than the old
script implied — the widget types, dropdown options and constraints come from striqt's schema,
but which fields appear is an allow-list in the browser and the per-radio limits come from the
driver. It's a good Q&A answer. It isn't worth 40 seconds of an eleven-minute talk.

**"How does it know" is answered correctly now.** The old script implied striqt supplied the
legal values. It doesn't. The radio supplies its own limits, and striqt's rules get checked by
building the real analysis and running it on zeros. That's a better story and it's the true one,
which matters with the library's authors in the room.

**The access-denied beat is cut.** It came out of the options list rather than out of what we
actually talked through, and it doesn't earn its thirty seconds — it's a gag about permissions
in the middle of an argument about measurement. The thing it was there to prove, that everyone
is watching one instrument rather than thirty private copies, now comes free off the retune:
*every screen in this room just moved together.*

**The rate demo, not the frequency demo.** They make the same point, but an out-of-range
frequency *clamps*, which means the radio genuinely retunes to the edge of its envelope and
your display — and thirty phones — go dead until you tune back. An off-grid rate *snaps*, tells
you, and the picture never stops. So the frequency case is one sentence inside the rate demo.

**The unplug moved to the end of the Deepwave section.** Your idea, and it's right. It closes
that half of the talk on the thesis and it hands you a clean pivot into the second radio.
One thing: plug the dish back in while you're walking over, or the room spends your last three
minutes looking at a dead pane.

**Recording comes before the unplug, deliberately.** Record → "that's the real archive, and I
pull it off with one command" → unplug → "and this is what I'd have lost." The order does the
argument for you.

**The striqt bug fixes are out**, per your call. The claim is that it works on any radio, not
that you fixed somebody's code.

**Nothing about the transmitter.** It's the one major subsystem that's entirely yours and it's
still out, because it doesn't serve the story. It's on the feature wall by omission — if
somebody asks, you have a good answer.

**The close.** No cart gesture, no callback. It states the before, states the after, and lands
on the shortest sentence in the talk. "It just has eyes now" is doing the work that a list of
accomplishments would do worse.

---

## Still to settle before you walk in

1. Time it standing up, with the demo running and the pauses real.
2. Confirm 20 MS/s snaps to 15.36 on this radio, and that the acknowledgement is legible from
   the back of the room. Do not type anything that snaps above 30.72 — that raises the
   unqualified-rate banner on every screen, which is a good beat but a different one.
3. Check what 2593 looks like at idle, before you start the speed test. The tower's sync burst
   broadcasts whether or not anyone is using it, so there should already be something faint on
   both panes — that's what AHAWI locks onto later. If the band is genuinely dead in that room,
   the speed test has to carry the whole section and you'll want a second phone ready to keep
   traffic running while you talk.
4. A speed test finishes in about thirty seconds and you need traffic through four beats. Run
   something continuous instead — a video stream, or just start another one — and know which
   button you're pressing without looking.
3. Rehearse the audience join with at least five phones on the actual tunnel.
4. Confirm the eight decibel figure live. It's the number the whole first half builds to.
5. Confirm the twenty thousand and two thousand dollar figures.
6. Tell Gabe his line is coming.
7. Have the pre-recorded backup queued full screen.
