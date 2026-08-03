# LINDA — Speaker Script (v2)

Everything here is what you say out loud. The few `→` lines are things you physically do.

Changes from v1: every factual correction from `script-review.md` is applied. The LO paragraph,
the FIR paragraph and the beam-sweep caveat paragraph are cut. The three-explanations block after
the retune is kept as you wanted it. The price line and the 2593 MHz move are untouched.

**Length: 2,424 spoken words, about 16.5 minutes at 145 wpm, plus five demo pauses.** That is the
honest number, not an optimistic one. v1 was 2,714. The cuts you approved plus a tightening pass
bought back about two and a half minutes, and that is all they can buy. Getting to 12–13 means
taking out a whole beat, and the only beat big enough is one of the three explanations after the
retune. See the note at the end.

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
these frequencies is too big and too heavy to move between rooftops. I got to bring the dish,
because I only had to wheel it across a building.

---

So you've got two antennas and a radio. What turns that into a measurement is a library called
striqt. Dan's library.

`→ look at Dan. Let the beat sit. Don't oversell it.`

What comes off the converter is complex baseband IQ, up to a hundred and twenty five million
samples a second of it. And at that point, it's voltage with a timestamp on it.

The first job is getting it onto a grid you can measure against. 5G is built on exact numbers:
the subcarrier spacing, the symbol boundaries, the slot timing. And the radio's master clock
doesn't divide cleanly into any of them. So striqt resamples, in the frequency domain, and it
does it exactly. Not close. It searches for FFT sizes that make the ratio a whole rational
number, and if it can't find one, it refuses rather than approximate.

Then it stops being numbers and starts being physics. A Y-factor calibration: the receiver gets
measured against a noise source of known temperature, so you know how much of what you're seeing
the radio added itself. That's the step that turns relative numbers into real dBm.

And then the analyses run. Spectrogram, power spectral density, channel power over time, and the
5G ones, which don't just find the sync burst. They tell you which cell it came from, and which
beam. It all comes out labelled, with real units on every axis, and gets archived to disk.

---

And that archive is good. It's calibrated, it's labelled, it's reproducible, and it's what the
paper actually gets written from.

There's just one thing it can't tell you. Whether any of it was worth collecting.

Because the way this data gets gathered is a one hundred millisecond recording, taken every five
minutes, running continuously for about a week. So the workflow is that you wheel a rig onto a
roof, you point it, and you walk away.

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

This is a spectrogram. Time runs across, frequency runs down, and colour is power. Two panes,
because two antennas. Left is the omni, right is the dish, both live on the Deepwave right at
this instant. And underneath is a power spectral density plot. Same data, but power against
frequency instead of time. That's the view you'd actually take a number off.

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
radio and drop them in. No FFTs, no network, no waiting on the browser. Everything else reads out
of it.

That separation is what keeps this alive. The radio's own buffer is small and it's filling
continuously. If the thread draining it ever stops to do something slow, that buffer overflows,
and you lose data silently, because a gap in a waterfall looks exactly like a quiet band.

The second one is my favourite, and it looks like nothing. I didn't write these controls. striqt
publishes a machine-readable description of its own capture settings: which fields exist, what
type each one takes, what range it's allowed, what the default is. LINDA asks for it on startup
and renders this form out of it. Change a setting in striqt and this form changes with it.

The third one is over here. When I typed that frequency in, LINDA didn't take my word for it. It
checked the number was legal for this radio, then sent it, then turned around and asked the radio
what it actually did, because a radio will happily accept a setting and quietly do something
else. And then it waited for a frame captured after the change landed, so it can't show you old
data with a new label on it.

Only then does it say the change worked. And if the radio came back with something different from
what I asked for, it says so, right here, in red, to everyone watching.

---

Now let me give it something to look at. Let's run a speed test on my phone.

`→ start something continuous, then stop talking until the blocks arrive`

There. Those blocks are my phone and the tower going back and forth.

That on-off pattern is TDD, time division duplex. Instead of using two frequencies, the tower and
the phone share one and take turns on it. The bright parts are the tower transmitting. The gaps
are the tower listening, which is my phone talking back, and phones run at much lower power, so
there's a lot less to see.

Now look at both panes at once, because this is the whole reason the rig has two antennas on it.
The signal is in both of them. The omni is not missing it, and that was never the worry. But the
right pane is brighter, and it isn't just the blocks. The background is lifted too. Everything
the dish hears is sitting higher than everything the omni hears.

That difference is the antenna gain. And the only reason you can see it at all is that both panes
are drawn on one shared colour scale. A single range, computed across both of them together, so a
given colour is the same power on the left as it is on the right.

A display would normally give each pane its own range, to make each picture look good. Do that,
and the loudest part of each pane comes out the same colour, and you have no way of telling which
antenna was hearing more.

Brightness gives you the direction. If you want the number, this is what it's for.

`→ drag out a slice of the band on the PSD. Rehearse with a real number in your mouth.`

It averages the power inside that slice, separately for each antenna. Omni here, dish here, and
they disagree by about eight decibels about how loud the same signal is.

That disagreement is the measurement. It's what the guard band number gets computed from. What
you're watching right now is the steady difference between the two antennas. What actually
worries people is rarer, and I can't make one happen on demand in a conference room.

Which is exactly why the campaign runs for a week, and not for a minute.

---

Now look at where those blocks start and stop as new rows come in.

This is a waterfall. New rows land at the top and push everything else down. But each refresh
grabs a window of samples from wherever the buffer happens to be at that moment, and that moment
has nothing to do with the tower's timing. So the block edges land somewhere different every
time. You are never actually looking at two rows that were next to each other in time.

There's a third mode for that, and it exists because my mentors told me I wasn't using the
library the way it's meant to be used. It's called AHAWI. Which stands for Apparently How Aric
Wanted It.

Instead of grabbing a new short window fifteen times a second, it takes one continuous hundred
millisecond snapshot, runs the full striqt analysis over that entire snapshot in a single pass,
and then plays it back to me twenty milliseconds at a time, slower than real time so you can
actually see it. Then it takes the next snapshot and does it again.

The hundred milliseconds isn't a number I picked. That's the recording length in the real field
campaign. And the 5G sync burst repeats every twenty milliseconds, so each twenty millisecond
slice holds exactly one of them.

`→ scrub through the slices`

And now the edges line up, because consecutive slices really are consecutive in time. I'm not
grabbing a new random window anymore, I'm sliding along one continuous piece of it.

---

Now, the reason all of this matters.

`→ unplug the dish, then stop talking and let it scroll`

The dish just went dead. The omni didn't move.

That's a cable coming loose. And I can see it from here, right now, which means I can fix it and
start the recording again. Nobody has to find out next Tuesday that the week is gone.

`→ plug it back in`

And back.

---

Everything I've just done assumes you already know what a spectrogram is, what a sample rate
does, and why you'd ever want to change one.

Most people don't. And most people shouldn't have to.

`→ switch modes`

So there's a second mode. Same radio, same server, same everything underneath. You just tap the
band you want to look at.

The two modes are called DAN mode and ARIC mode.

`→ pause. Don't smile.`

I'll let you work out which is which.

---

And all of this installs with one command. You point it at a radio, and it works out what you
plugged in: how many receive channels it has, what sample rates it'll actually accept, what
frequency range it can reach.

Which means, same software, different radio.

`→ plug in the HDMI`

You can see in the corner there, it detected the radio by name, it worked out how many channels
it has, and the sample rates in that list came off that radio a few seconds ago. Nobody typed
them in.

This one cost a couple of thousand dollars. The one on the cart cost about twenty. The
measurement doesn't know the difference.

---

So, back to the guard band.

The measurement that decides how close 5G can safely sit to the radio watching a dam now has eyes
on it. You can watch it while it's happening, you can tell whether the thing you're recording is
the thing you meant to record, and you can run it on almost any radio you can afford.

And this isn't only about 5G and dams. The same question, is this new signal going to step on
that critical one, is everywhere right now. CBRS and naval radar. Six gigahertz Wi-Fi over
utility microwave links. Radar altimeters on aircraft. Anywhere spectrum gets crowded, somebody
has to measure the overlap and then actually believe the answer.

The next person who puts one of these on a roof will know, in the first ten seconds, whether the
week is going to be worth anything.

`→ point at the cart`

It's been running this whole time.

---

## Notes on this version

**What changed, and why**

- "125 million samples a second" is now "off the converter, up to 125 million," said once. The
  second use, in the ring buffer paragraph, is gone. The cart runs 15.36 MS/s and the screen will
  say so.
- The retune beat is inverted. Instead of claiming it didn't freeze, you now point at the fact
  that it went blank on purpose. `rearm()` clears the ring and `Computer.run` withholds frames
  until it refills, so the room was going to see the pause either way. Now the pause is the point.
- "I never touch the code" is gone. Everything else about the schema form is true and stays.
- The interface-limits line is narrowed to sample rates, which is the one the AIR8201B actually
  queries.
- The band monitor now "averages the power inside that slice" on the PSD, not "integrates inside
  that box" on the waterfall.
- The LO paragraph and the FIR paragraph are cut, as you asked. The resample paragraph absorbs the
  interesting half: striqt refuses to approximate. That is a better line than either of the two
  that left.
- The beam-sweep caveat paragraph is cut. Two clauses survive inside the band-monitor beat, so you
  are not claiming the eight decibels is anything other than the steady gain difference. If you
  want it fully gone, delete "What you're watching right now is the steady difference between the
  two antennas. What actually worries people is rarer, and I can't make one happen on demand in a
  conference room." The following line still works on its own.
- The amplifier joke moved into the list of things that come back looking like clean data, where
  it is an example instead of a punchline that deflates the section.
- The striqt section now ends on "So I built the thing that tells you now," so LINDA arrives as an
  answer rather than a name.
- The ARIC transition no longer references a local oscillator, since that paragraph is gone. It
  now calls back to spectrograms and sample rates, which you did say.
- The mode-name joke is split off from the transition by the mode switch itself.
- Antenna gain is dBi. The 5G measurements resolve cell and beam, which is a stronger and more
  generous credit to Dan than "correlators."
- "About eight decibels" is written as one number, said once. Replace it with the real one and
  rehearse it out loud. Reading a live value mid-sentence is harder than it looks.

**Where the remaining minutes are**

You kept the three explanations after the retune, which is your call and I understand the reason.
It is also 470 words, a little over three minutes, with the screen static. That is the single
largest block in the talk. If you overrun on the day, that is where it will happen.

If you ever want it back: the ring buffer explanation is the one an audience is least likely to
miss. The pause on retune already proves the point without a memory-layout lecture, and you could
land it in two sentences instead of eight.

**Still to settle before you walk in**

1. Time it standing up with the demo running. Not at a desk.
2. Rehearse the retune and confirm the blank-and-return is as short as this script claims.
3. Rehearse the unplug on the actual cart, in a room this quiet.
4. Check whether the cart is running with a calibration file loaded. If not, the dBm line is
   about striqt's method, not about the number on screen, and the script is already worded that
   way.
5. Confirm every ARIC chip you plan to tap is above 300 MHz. On an AIR8201B everything below that
   is greyed out.
6. Demo a retune, not a gain change. Gain is never verified by any adapter in the repo, so the
   four-step story only holds for frequency.
7. Have the pre-recorded backup queued full screen.
