Much like oil, gold, or mustafas, Spectrum is finite. and In the U.S. the federal government is the one who controls it. It carves it
into bands, writes the rules for what each band can be used for, and sells chunks of it.

Which works fine, right up until the piece you're selling sits next
to something that cannot be interrupted.

---

The mid-band, roughly 3.5 to 3.8 gigahertz, has been federal-only for a very long time. Agencies
like the Department of Energy run point-to-point links in that range — a dish on a tower talking
to a dish on another tower. These are used to communicate things like dam telemetry, reservoir
levels, and grid monitoring. Communications that cannot drop out.

That same mid-band is now being auctioned off to carriers for 5G. Which raises the question my
lab has to answer: how close can you put a loud commercial 5G transmission station next to one of those
DOE links before you break the whole system?

---

The answer you allocate an empty strip of spectrum between two competing chunks so they aren't stepping on each other. And somebody has to decide how wide it is.

You can be wrong in both directions. If you leave too little, leakage from the commercial side starts knocking out federal links. Leave too much, and you've fenced off a public resource
nobody is allowed to use, and left billions of dollars of auction revenue on the table.

NIST doesn't get to pick that number. What NIST does is hand the people who do pick it a
measurement they can actually trust.

---

So what are we actually measuring?

Traditionally, you measure a band like this with an omnidirectional antenna, `→ point to cart`
much like this one here. An antenna that hears everything, from every direction, at once.

But the receiver you're protecting isn't an omni. It's a dish. A Highly
directional antenna, so When a 5G beam sweeps across
that dish, the omni barely
registers it because it averages that moment in with everything else in the sky. The dish, however, gets it very clearly

those are two  completely different answers for the Same interference

`→ pause`

So the lab doesn't pick one. It records both, at the same time and compares them. using the omni as a reference and the dish to tell you what the protected reciver actually experiences.

All in a setup resembling the one here.

---

So that's what this is. An omni on the top, a directional antenna on the side, both coming down
through a bandpass filter and an amplifier into the radio at the bottom. A Deepwave. running striqt. my mentor dan's library!

What comes off the radio is IQ. In-phase and quadrature — two numbers per sample that between
them record how strong the wave is and where it is in its cycle. 

Striqt then resamples that IQ onto the exact grid 5G is built on. It calibrates the receiver, runs the analyses — including the 5G ones, which find the sync bursts and tell you which cell they came from — and finishes by archiving all of it, in a nicely labelled format containing all that data

---

the data actually gets collected as one hundred millisecond recordings, every five
minutes, running continuously for about a week.

So you essentiallyt wheel the rig onto a roof, you point it, and you leave.

---

the problem is, striqt has no idea what the antenna was pointed at. It will process a week of noise exactly as
carefully as a week of data

If a cable came loose. You put the amplifier on backwards, which I have definitely never done. Or the band was just quiet.

And you don't find out until you're back at your desk, a week later.

`→ pause. Then straight to the LINDA slide. No line.`

---

This is the Live IQ Navigation and Display Application. Or, as it's known by its stage name, LINDA.

LINDA is a visualization software that uses the same radio, same library, same measurements — live, in a browser or a shell accesible from anywhere in the world.

---

`→ slide 20 — whole LINDA`

Here's what it looks like.

---

`→ slide 21 — the config row`

This row is sixteen fields of exactly what the radio is running right now. Sample rate, gain,
FFT size, frame rate.

And over here is the frequency it's tuned to — with the band it lands in, named for you.

---

`→ slide 22 — the spectrograms`

Two spectrograms, one for each antenna. Frequency runs across, time runs down, and colour is
power.

And those on-off blocks, quickly — that's TDD. The tower and the phone share one frequency and
take turns on it. Bright is the tower transmitting, the gaps are handsets answering back. Which
is why your download is faster than your upload.

---

`→ slide 23 — the tabs`

Six tabs down the side. Display, PSD, measurement, capture, record, and the operations log.
Everything the software does is behind one of those.

---

`→ slide 24 — the PSD`

Underneath, the power spectral density. Same data, but power against frequency instead of time.
That's the view you'd actually take a number off.

---

`→ slide 25 — the band monitor`

And the band monitor. Drag a slice on that plot and it prints the power inside it — for each
antenna, and the difference between them.

Hold onto that one.

---

`→ slide 26 — the log`

And the log. Every change I make to this radio lands here. On my screen, and on every screen
it's being watched on.

---

`→ URL slide`

Let's see what it looks like live, 

The address on this screen is LINDA connected to the radio on this cart. sign in as **viewer**, and follow along on
your own screen.

`→ give them fifteen seconds. Do not fill the silence.`

---
### ▸ LIVE — switch the projector to the radio
---

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

LINDA doesn't take my word for anything. Every setting gets checked twice before it reaches the
radio — and proved twice after.

Before: once against the hardware, where I ask the radio directly what rates it accepts and what
range it tunes. And once against striqt — for anything only striqt can judge, LINDA builds the
real analysis, with your settings in it, and runs it on a buffer of zeros. If it's going to
break, it breaks there. The radio never sees it.

After: it asks the radio what it actually did. Then it waits for a frame captured *after* the
change landed. Only then does it tell you it worked.

---

Look at the two panes.

`→ point` This one is the omni. This one is the dish. Same signal, same instant — and one of
them is much brighter. Not just the bursts. The background too.

And there's that TDD pattern, live.

They share one colour scale, computed across both of them together. So a given colour is the
same power on the left as it is on the right.

And the band monitor.

`→ read the live number`

That's the gap between the reference antenna and the dish. That
difference is the measurement. That's the number the guard band gets
argued over.

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

All of this installs in one command. The installer works out which radio you plugged in and every
setting it'll accept, sets up the drivers, the service and the network, and hands you a URL you
can access immediately.

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

`→ feature wall`

There's a lot more in there than I have time for.

And That same question — is this new signal going to step on that other one — is everywhere right
now. CBRS and naval radar. Six gigahertz Wi-Fi and utility microwave links. Anywhere spectrum
gets crowded, somebody has to measure the overlap, and somebody has to make a decision on the
number that comes back.

---

The problem was that you recorded for a week, and found out afterwards whether it was worth
anything.

Now you watch it happen live. From a roof, from a browser, from your phone, from anywhere in the world, and on almost any radio you can afford.

With that, ladies and gentlemen — I hope your tax dollars were put to satisfactory work.

Thank you.
