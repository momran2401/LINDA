# LINDA — Speaker Script (v3)

Your edits are in. The DAN/ARIC section stays out. The closing intern line is cut. Gabe is
written as a real exchange. The legal-values demo is now a live beat inside the third explanation,
which is where you asked for it.

**Length: 2,347 spoken words, about 16 minutes at 145 wpm, plus the demo pauses.** Cutting the
ARIC section bought back about a minute; the legal-values demo and the two lines I had to restore
put roughly half of it back.

Notes on every change are at the bottom, including two places where I had to add a line back for
the sense to hold.

---

Much like oil, minerals, or any other natural resource, spectrum is finite. In the U.S. the
federal government is the one who controls it. It carves it into bands, writes the rules for what
each band can be used for, and sells chunks of it off to telecom companies.

Which works perfectly, as long as what you're selling isn't being used.

---

The mid-band, roughly 3.5 to 3.8 gigahertz, has been federal-only for a very long time. Agencies
like the Department of Energy run point-to-point microwave links up there: a dish on a tower
talking to another dish on another tower. Dam telemetry, reservoir levels, grid monitoring. It's
wireless backhaul for places where running fibre across a mountain isn't realistic, and it's
carrying the kind of data that cannot drop out.

That same mid-band is now being auctioned off to carriers for 5G. Which raises the question my
lab has to answer: how close can you put a loud commercial base station next to one of those
links before you break the whole system?

---

The way you answer that is with a guard band. An empty strip of spectrum you leave between two
competing chunks so they aren't stepping on each other.

And you can be wrong in both directions. Leave too little, and leakage from the commercial side
starts knocking out federal links. Leave too much, and you've fenced off a public resource nobody
is allowed to use, and left billions of dollars of auction revenue on the table.

NIST doesn't get to pick that number. What NIST does is hand the people who do pick it a
measurement they can actually trust.

---

So you put an antenna on a roof and you measure for a while. And what people normally use is an
omnidirectional antenna, `→ point to cart` much like this one here. An antenna that hears
everything, from every direction, all at once.

But the receiver you're actually trying to protect isn't an omni. It's a dish, pointed at one
specific thing, with more than thirty dBi of gain in that one direction. And those two do not
hear the same world. When a 5G beam sweeps across that dish, the omni averages that moment in
with everything else in the sky, so it barely registers it. The dish takes the whole thing.

Same interference. Two completely different answers. And the omni's answer is the optimistic one.

`→ pause`

Which is the dangerous direction to be wrong in.

So the lab doesn't pick one. It records both, at the same time, on one radio, and compares them.
In a setup resembling the one here.

---

And that's what this is. An omni antenna on the top, a directional antenna on the side, both
coming down through a bandpass filter and an amplifier into the radio at the bottom, a Deepwave.
And everything I'm about to show you is running on this setup right now.

Now, the real rig uses a 32-element dipole array instead of a dish, because an actual dish at
these frequencies is too big and too heavy to move between rooftops. I only had to wheel mine
across a building. So I got to bring the dish.

---

So you've got two antennas and a radio. What turns that into a measurement is a library called
striqt. Dan's library.

`→ look at Dan. Let the beat sit. Don't oversell it.`

What comes off the converter is complex baseband IQ, up to a hundred and twenty five million
samples a second of it. And at that point, it's voltage with a timestamp on it.

The first job is getting it onto a grid you can measure against.

5G is built on exact numbers: the subcarrier spacing, the symbol boundaries, the slot timing. And
the radio's master clock doesn't divide cleanly into any of them. So striqt resamples, in the
frequency domain, and it does it exactly. It searches for FFT sizes that make the ratio a whole
rational number, and if it can't find one, it refuses rather than approximate.

Then the calibration. Y-factor: the receiver gets measured against a noise source of known
temperature, so you know how much of what you're seeing the radio added itself. That's the step
that turns relative numbers into real dBm.

And then the analyses run. Spectrogram, power spectral density, channel power over time, and the
5G ones, which don't just find the sync burst. They tell you which cell it came from, and which
beam. It all comes out labelled, with real units on every axis, and gets archived to disk.

---

And that archive is good. It's calibrated, it's labelled, it's reproducible, and it's what the
paper actually gets written from.

There's just one thing it can't tell you. Whether any of it was worth collecting.

Because the way this data gets gathered is a one hundred millisecond recording, taken every five
minutes, running continuously for about a week. So the workflow is that you wheel a rig onto a
roof, you point it, and you walk away for a week.

And striqt will hand you a beautiful, clean archive whether that dish was pointed at the sky or
straight at a wall. A cable came loose. The front end died. You put the amplifier on backwards,
which I have definitely never done. Or the band was just quiet, because nothing happened to be
transmitting. All of it comes back looking like perfectly good data, and you don't find out until
you're back at your desk, a week later.

So I built the thing that tells you now.

---

This is Live IQ Navigation and Display Application. Or, as it's known by its stage name, LINDA.

striqt does the measuring. LINDA is how you watch it happen. Everything the radio is hearing,
right now, in a browser, on a kiosk, or over a shell. Reachable over a hotspot, an ethernet
cable, or the internet from anywhere in the world.

And it's been running on this cart since before I started talking.

---

Ladies and gentlemen. A spectrogram.

Time runs across, frequency runs down, and colour is power. Two panes, because two antennas. Left
is the omni, right is the dish, both live on the Deepwave right at this instant. And underneath
is a power spectral density plot. Same data, but power against frequency instead of time. That's
the view you'd actually take a number off.

Right now it's parked somewhere fairly quiet. Let me tune it to 2593 megahertz, T-Mobile's
mid band.

`→ retune, then stop talking until it settles`

Three things just happened.

The first one, watch what it didn't do. It didn't tear. And it didn't show you a single frame of
the old band with the new label on it. It went blank for a moment instead, on purpose, and then
it came back.

That pause is a design decision. A ring buffer is why it's that short.

A ring buffer is a fixed block of memory, allocated once, with a pointer that writes into it in a
circle. When it hits the end it wraps back to the start and overwrites the oldest samples. It
never grows and it never allocates. One thread in LINDA does nothing but pull samples off the
radio and drop them in. That thread never runs an FFT, never touches the network, and never waits
on any of you. Everything else in the system reads out of the circle behind it.

Which matters, because the radio's own buffer is small and it is filling continuously. Stop
draining it for a moment and it overflows, and you never find out, because a gap in a waterfall
looks exactly like a quiet band.

The second one is my favourite. I didn't write these controls. striqt publishes a
machine-readable description of its own capture settings: which fields exist, what type each one
takes, what range it's allowed, what the default is. LINDA asks for it on startup and renders this
form out of it. Change a setting in striqt and this form changes with it.

The third one is the one I'd defend hardest. LINDA doesn't take my word for anything.

Watch. I'm going to ask this radio for a sample rate it cannot run.

`→ type an off-grid rate, e.g. 20 MS/s, and Apply`

It didn't take it, and it didn't quietly ignore me either. It snapped to the nearest rate this
radio will actually run, and then it told me it had done that, and why.

That's the same thing it did when I typed the frequency. It checked the number was legal for this
radio, then sent it, then turned around and asked the radio what it actually did, because a radio
will happily accept a setting and quietly do something else. And then it waited for a frame
captured after the change landed, so it can't show you old data with a new label on it.

Only then does it say the change worked. And if the radio had come back with something different
from what I asked for, it would say so, right here, in red, to every single person watching this,
on every screen it's being watched on.

---

Now let me give it something to look at. Let's run a speed test on my phone.

`→ start something continuous, then stop talking until the blocks arrive`

There. Those blocks are my phone and the tower going back and forth.

That on-off pattern is TDD, time division duplex. Instead of using two frequencies, the tower and
the phone share one and take turns on it. The bright parts are the tower transmitting. The gaps
are the tower listening, which is my phone talking back.

Now look at both panes at once, because this is the whole reason the rig has two antennas. Same
signal, same instant, and the right pane is brighter. Not just the blocks, the background too.
Everything the dish hears sits higher than everything the omni hears.

That difference is the antenna gain, and you can only see it because both panes share one colour
scale. One range, computed across both of them together. So a given colour is the same power on
the left as it is on the right, and the two panes can be compared by eye.

Colour tells you which antenna is hearing more. For how much more, I need a number.

`→ drag out a slice of the band on the PSD. Rehearse with a real number in your mouth.`

It averages the power inside that slice, separately for each antenna. Omni here, dish here, and
they disagree by about eight decibels about how loud the same signal is.

That disagreement is the measurement. It's what the guard band number gets computed from.

What you're watching right now is the steady difference between the two antennas. The interference
that actually worries people is a beam that sweeps across the dish for a fraction of a second, and
the difference spikes far past this. Those are the events the guard band exists to survive, and
they are not something I can make happen in a conference room.

---

Now look at where those blocks start and stop as new rows come in.

This is a waterfall. New rows land at the top and push everything else down. But each refresh
grabs a window of samples from wherever the buffer happens to be at that moment, and that moment
has nothing to do with the tower's timing. So the block edges land somewhere different every
time. You are never actually looking at two rows that were next to each other in time.

There's a third mode for that, and it exists because my mentors told me I wasn't using the library
the way it's meant to be used. It's called Apparently How Aric Wanted It. AHAWI, for short.

Instead of grabbing a new short window fifteen times a second, it takes one continuous hundred
millisecond snapshot, runs the full striqt analysis over that entire snapshot in a single pass,
and then plays it back to me twenty milliseconds at a time, slower than real time so you can
actually see it. Then it takes the next snapshot and does it again.

The hundred milliseconds is the recording length in the real field campaign. And the 5G sync
burst repeats every twenty milliseconds, so each twenty millisecond slice holds exactly one of
them.

`→ scrub through the slices`

And now the edges line up, because consecutive slices really are consecutive in time. I'm not
grabbing a new random window anymore, I'm sliding along one continuous piece of it.

---

`→ unplug the dish, then stop talking and let it scroll`

The dish just went dead. The omni didn't move.

That's a cable coming loose. And I can see it from here, right now, which means I can fix it and
start the recording again. Nobody has to find out next Tuesday that the week is gone.

`→ plug it back in`

And back.

---

All of this installs in two commands. `git clone`, then `bash install_linda.sh`. You plug a radio
in, and it works out how many receive channels it has, what sample rates it'll accept, and what
frequency range it can reach.

`→ Gabe`

Which is when Gabe asked me the good question. Does that mean the same software runs on a
different radio?

`→ plug in the HDMI`

Yes, Gabe.

It detected the radio by name. It worked out how many channels it has. And the sample rates in
that list came off that radio a few seconds ago. Nobody typed them in.

That's a twenty thousand dollar instrument on the cart, and a two thousand dollar radio on the
screen. The measurement doesn't know the difference.

---

So, back to the guard band.

The measurement that decides how close 5G can safely sit to the radio watching a dam now has
real-time eyes on it. You can watch it while it's happening, you can tell whether the thing you're
recording is the thing you meant to record, and you can run it on almost any radio you can afford.

And this isn't only about 5G and dams.

That same question, is this new signal going to step on that critical one, is everywhere right
now. CBRS and naval radar. Six gigahertz Wi-Fi over utility microwave links. Anywhere spectrum
gets crowded, somebody has to measure the overlap, and somebody has to stake a decision on the
number that comes back.

The next person who puts one of these on a roof will know, in the first ten seconds, whether the
week is going to be worth anything.

---

## Notes on this version

### The two lines I had to put back

**"The omni didn't move."** You cut it from the unplug beat. Without it there is no comparison,
and the whole point of that moment is that one pane died and the other didn't. It's four words.
If you want it gone, the beat needs a different contrast, and I don't have a better one.

**"Now look at both panes at once..."** You cut this paragraph, but "That difference is the antenna
gain" three lines later had nothing left to refer to. I've put back a compressed version, two
sentences instead of four, which also serves as the setup for the colour scale point.

### The legal-values demo

This is now a live action inside the third explanation, not a claim. You ask the radio for a
sample rate it can't run, it snaps to the nearest legal one and tells you why. `SharedConfig.update`
does exactly that, and the ack lands in the OPS log where the room can see it.

I chose sample rate over frequency on purpose. An out-of-range **frequency** gets clamped to the
edge of the envelope, which means the radio actually retunes to 6 GHz and your display goes dead,
and you have to tune back on stage. An off-grid **rate** snaps, tells you, and the picture keeps
running. Nothing to recover from.

Rehearse which value you type. 20 MS/s should snap down to 15.36. Do not type anything that snaps
to 61.44 or above, because crossing 30.72 raises a persistent "unproven rate" banner for every
viewer, which is a great beat but a different one and it eats thirty seconds.

### The colour scale, shorter and more scientific

You asked whether it needed more depth after you cut the paragraph below it. It doesn't. What it
needed was to stop explaining the counterfactual. The version here states the property once, says
what it buys you, and moves on:

> "One range, computed across both of them together. So a given colour is the same power on the
> left as it is on the right, and the two panes can be compared by eye."

That is the whole claim, and it's exactly what the code does in both places.

### The transition you flagged

"Brightness gives you the direction. If you want the number, this is what it's for" is now:

> "Colour tells you which antenna is hearing more. For how much more, I need a number."

Same job, and it names what the drag is for before you do it.

### The beam-sweep caveat

Rewritten to your framing: say what the interference actually is, say those are the events the
guard band exists to survive, then say you can't produce one. That reads as scope, not as an
apology, which is what the old version sounded like.

### "Then actually believe the answer"

Replaced with "somebody has to stake a decision on the number that comes back." You were right
that it isn't about belief. It's about the fact that a real decision gets made on that number,
which is the whole reason accuracy matters, and it lands harder.

### The ending

The cart gesture is gone. It didn't work because you already say "it's been running on this cart
since before I started talking" fifteen minutes earlier, so the callback was a restatement rather
than a payoff. The talk now ends on "worth anything," which is the strongest sentence in the
close.

If you want a physical ending, the version that works is silence: walk to the cart, look at the
screen with the room, and let them see it still scrolling. No line. Riskier, and it needs
rehearsing so the applause cue is clear.

### Smaller fixes

- Dish joke reordered so the punchline is last: "I only had to wheel mine across a building. So I
  got to bring the dish."
- "Crowd, I introduce you to a spectrogram" became "Ladies and gentlemen. A spectrogram." Same
  showmanship, and the full stop gives you the beat.
- "Then A Y-factor calibration" became "Then the calibration. Y-factor:"
- AHAWI expansion now runs long-form first, acronym second, which is how the joke lands.
- The Gabe exchange is written as something that happened, past tense, so it works whether or not
  he reacts. Tell him it's coming.
- The red-mismatch line is now "to every single person watching this, on every screen it's being
  watched on," which is both true (the broadcaster pushes op events to every connected client) and
  a better close for that beat than "to everyone watching."

### Still to settle before you walk in

1. Time it standing up with the demo running.
2. Rehearse the rate-snap. Confirm which value snaps where, and that the ack is visible from the
   back of the room.
3. Rehearse the unplug on the actual cart, in a room this quiet.
4. Confirm the twenty thousand and two thousand figures for the two radios.
5. Demo a retune, not a gain change. Gain is never verified by any adapter in the repo.
6. Have the pre-recorded backup queued full screen.
