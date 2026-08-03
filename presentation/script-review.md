# LINDA talk: fact-check and delivery review

I read the whole codebase (`live/core/`, the web frontend, the installer), the vendored striqt
tree, and `Project_Reasoning.pdf`, then went through your script line by line. Everything below
is either quoted from the source or from your own reasoning doc.

Two headlines before the detail:

1. **The script is roughly 19 minutes of talking.** 2,714 spoken words at a realistic 145 wpm,
   before the five places you deliberately stop and let the demo run. Your own
   `talk-structure.md` budgets 12 to 13 minutes. You are about 60% over.
2. **Six claims are wrong or will get corrected in the room**, and two of them are wrong in
   front of the two people most likely to notice. Dan and Aric are expected to be there.

The good news is that most of the script is accurate, the arc is genuinely good, and almost
every fix makes a sentence shorter.

---

## Part 1: Things that are wrong. Change these.

### 1.1 "A hundred and twenty five million samples a second" (said twice)

This is the one I would fix first. 125 MHz is the AIR-T's **converter clock**, not the sample
rate your viewer is running at.

`core/constants.py`:
```
MASTER_CLOCK_RATE   = 125e6   # AIR-T reference clock. NOT a universal default
DEFAULT_SAMPLE_RATE = 15.36e6
QUALIFIED_MAX_RATE_HZ = 30.72e6
```

The cart is running **15.36 MS/s**, eight times lower. Worse, your second use of the number is
the load-bearing sentence in the ring buffer story: *"the radio's own buffer is small, and it's
filling at a hundred and twenty five million samples a second."* It is not. And your own repo
says 122.88 MS/s has never been sustained and would silently drop samples if you tried.

Your reasoning doc does say "DeepWave does this with a sampling rate of 125 MS/s," which is
where you got it, and that is true of the ADC. But you are pointing at a live screen while you
say it, and the number on that screen will be 15.36.

Two fixes, both shorter than what you have:

> "What comes off the converter is complex baseband IQ at up to 125 million samples a second.
> We're running a 15 megahertz slice of that today."

and later:

> "The radio's own buffer is small and it's filling continuously. If the thread draining it ever
> stops to do something slow, that buffer overflows and you lose data."

You lose nothing by dropping the number the second time. The point is "it never stops," not the
rate.

Bonus: the honest ring depth is a **better** number than the one you are implying. 4M samples per
channel at 15.36 MS/s is **273 milliseconds** of history sitting in memory. At the rate you
currently imply it would be 34 ms. Say 273 ms if you want a number there.

### 1.2 "The picture didn't tear, didn't freeze, and didn't lose anything"

It freezes, on purpose, and the audience will be looking straight at it.

`Acquirer.rearm` disables the stream, arms the new capture, re-enables, and then:

```python
with self._lock:
    self._clear_ring_locked()
OPERATIONS.stage(op_id, "applied", "arm_spec completed, ring cleared of old-tuning IQ")
```

and `Computer.run` refuses to publish anything until the ring refills, with this comment:

> *"Skip frames straddling a retune. Either would publish zero-padded dark rows or mislabel old-band
> energy with the new header."*

So samples across the boundary are deliberately thrown away and the display holds the last frame
until fresh IQ arrives. This is the correct behaviour and it is a better story than the one you
are telling. You built a thing that would rather show you nothing than lie to you. That is the
whole thesis of your talk. Do not claim seamlessness and then have the room watch it pause.

> "Watch what it does not do. It didn't tear, and it didn't show you one frame of the old band
> under the new label. It went blank for a moment instead, on purpose, and then came back. That
> pause is a design decision, and the ring buffer is why it's that short."

### 1.3 "I never touch the code"

The schema is real. `striqt_web_server.py:capture_editor_schema` genuinely imports
`json_schema` from `striqt.analysis.specs.helpers` and serves striqt's own JSON Schema at
`/schema`. The form really does read types, enums, minimums, maximums and defaults out of it.

But which fields appear is a hardcoded list in `app.js`:

```javascript
const captureFields = ["center_frequency", "sample_rate", "gain",
                       "analysis_bandwidth", "lo_shift", "host_resample",
                       "backend_sample_rate"];
```

If Dan adds a field tomorrow, it is silently dropped until someone adds its name to that array.
Dan will know this. Say the true version, which is still impressive:

> "I didn't write these controls. striqt publishes a machine-readable description of its own
> capture settings, and LINDA renders the form from that: the types, the allowed values, the
> defaults, all straight out of striqt's schema. Change a setting's type or its range in striqt
> and this form changes with it."

Drop "I never touch the code."

### 1.4 "The limits in that interface came off that radio, not a config file"

For the second radio (Pluto, generic Soapy) this is completely true, all three limits are
queried. For the **AIR8201B on the cart**, it is one out of three:

```python
"air8201b": {
    "envelope": {"freq_min": 300e6, "freq_max": 6e9,
                 "gain_min": -60.0, "gain_max": 10.0,
                 "rate_min": 1e6,   "rate_max": 125e6},
    "query_envelope": ("rate",),
}
```

Frequency range and gain range are static constants. Deliberately, and for a good reason your
own comment gives: the gain window is striqt's calibrated-gain convention, not the driver's raw
range, and the driver rejects -60 outright.

Since you say this line while pointing at the **second** radio, you are probably fine. Just make
sure the line lands on the cheap radio and not the AIR-T. Safest phrasing either way:

> "The sample rates in that list came off that radio a few seconds ago. Nobody typed them in."

### 1.5 "It integrates the power inside that box"

Two problems. First, `updateBandMonitor` averages, it does not integrate:

```javascript
const linBand = sumInBand / (nBins * depth);
band[ch] = 10 * Math.log10(Math.max(linBand, 1e-20));
```

A wider selection does not give a bigger number. (It does average in the linear domain before
converting to dB, which is the right thing to do, and worth one clause if you want it.)

Second, it is not a box. `setupBandDrag` binds to the **PSD plot**, not the waterfall, and tracks
only a low and a high frequency. There is no time boundary to drag. Calling it a box while
dragging a vertical band on a different plot invites exactly one confused question.

The good news: the Δ RX1−RX2 readout in dB is real and is displayed, exactly as you describe.

> "If you want the number, I drag out a slice of the band down here. It averages the power inside
> that slice, separately for each antenna. Omni here, dish here, and the difference between them,
> about N decibels."

### 1.6 "It's the LO, dealt with in the same pass"

Careful here, for two reasons.

First, LINDA's own default is `lo_shift: "none"` (`core/config.py:137`). So the thing you are
describing is switched off in the demo you are standing in front of.

Second, in the striqt tree I can read, `corrections._resample()` never passes the `shift`
argument that would do the digital re-centring, and `USE_OARESAMPLE = False` disables the other
path that would. The radio genuinely is detuned by `lo_offset`. The shift-back is where it gets
murky. The DC spur is handled in practice by `lo_bandstop`, which does not filter at all, it
masks those bins to NaN (`null_lo`: *"sets samples within the specified bandwidth on a frequency
axis to nan in-place"*).

Caveat on my caveat: your own `CLAUDE.md` warns that the vendored tree is a later snapshot than
the v0.7.0 pinned on the radio, so I cannot settle what the installed build does. **Ask Dan
before the talk.** Until then, say the part that is unambiguously true:

> "The same design step that picks the resample ratio also picks a local oscillator offset, so
> the radio can be tuned deliberately off centre and the receiver's own LO leak lands outside the
> band you're measuring instead of straight through the middle of it."

That keeps the idea and drops the mechanism claim.

---

## Part 2: Things that are true but will get you a follow-up question

### 2.1 The ring buffer paragraph oversells three details

Preallocated once: true. One dedicated daemon thread: true. Does no DSP: true. Those are the
parts that matter and they hold up.

But:

- **"it never waits on anything"** is not right. `_ring_write` and `get_latest` take the *same*
  `threading.Lock`, and `get_latest` does the whole copy inside it. In AHAWI that is a 25 MB
  memcpy the writer waits behind.
- **"it doesn't talk to anyone"** is not right either. That thread owns the entire hardware
  lifecycle: open, rearm, recover, driver readback, `pause_and_release` for recording and TX.
- **"the browser reads the newest slice out of the ring"** is not right. The browser reads a
  separate published-frame slot under `_pub_lock`. Only the Computer touches the ring.

None of that weakens your point. Rewrite tighter and it is bulletproof:

> "A ring buffer is a fixed block of memory, allocated once, with a pointer writing into it in a
> circle. When it hits the end it wraps and overwrites the oldest samples. It never grows and it
> never allocates. One thread in LINDA does nothing but pull samples off the radio and drop them
> in. No FFTs, no network, no waiting on the browser. Everything else reads out of it."

Shorter, and every clause survives inspection.

### 2.2 "Everything after it is dBm"

The Y-factor description is accurate. `sensor/lib/calibration.py` does the real thing:
`noise_figure = enr_dB - 10*log10(Y - 1)`, hot and cold measurement, ENR from a known diode,
an operator prompt to flip it.

Two nuances. It is an **offline campaign** that produces a netCDF correction table, and at
capture time striqt does a table lookup, not a live Y-factor. And the `dBm` label is hardcoded on
the measurement whether or not a calibration file was loaded. With no cal file, striqt's own docs
say IQ is normalized to ADC full scale and the axis still says dBm.

If your cart is running uncalibrated today (worth checking), do not say "everything after it is
dBm" while pointing at the screen. Say:

> "Then the calibration goes on. Y-factor: the receiver gets measured against a noise source of
> known temperature, so you know how much of what you're seeing the radio added itself. That's
> what turns relative numbers into real dBm."

Same beat, no claim about what is on screen right now.

### 2.3 The dish-unplug demo

There is no per-channel health signal anywhere in the codebase. `DATA_STALE_SEC` is whole-stream
and silent. Unplugging the antenna does not stop samples arriving, the port keeps streaming
noise. What the audience sees is the waterfall going dark and the Δ readout dropping, which is
exactly the point, but "I know about it about a second after it happened" is you reading a
picture, not the software telling you.

That is fine. Just do not imply an alarm fired.

> "The dish just went dead. The omni didn't move. That's a cable coming loose, and I can see it
> from here, right now."

Also worth knowing: the array is fixed and this is a conference room. Rehearse the unplug on the
actual cart, because if the room is quiet enough that both panes look like noise anyway, the
moment does not land.

### 2.4 AHAWI replay speed

Everything you say is true: 100 ms default capture, single striqt pass, 20 ms user-set segments,
15 fps in the rolling modes. Two things you did not say that someone may notice:

- Playback dwells **200 ms per segment** (`ahawiDwell = 200`), so it is 10x slow motion.
- New captures are paced one second apart (`AHAWI_REFRESH_S = 1.0`), so it is one 100 ms snapshot
  per second, not back to back.

Neither hurts you. The slow motion is arguably the feature. One clause covers it: *"played back
slower than real time so you can actually see it."*

### 2.5 "In red, to everyone watching"

Verified true. `_broadcaster` pushes every op event to every connected client, and
`.op-state.op-bad` is `#ff6060`. Nice.

One caveat if you demo a **gain** change instead of a frequency change: `gain_readback_comparable
= False` on every adapter in the repo, so gain is never verified, it reports "readback
unsupported." The comment explains why (striqt calibrated gain versus raw driver gain disagree by
construction). Demo a retune, not a gain change, and the four-step story is exactly right.

### 2.6 "Three interfaces: a browser, a kiosk, and a shell"

True, with a footnote: the kiosk *is* the web server plus a fullscreen browser, so it is more of a
deployment mode than a third interface. The genuinely distinct third one is the curses terminal
over SSH plus `radioctl`. Nobody will challenge this. Mentioning it costs you four seconds you do
not have.

"The internet from anywhere in the world" is also true, via `run_web.sh --tunnel` and cloudflared,
but that is a separate manual path and is not part of the one-command install. Also, auth is
username-only with no password. I would not dwell on the public URL in a room at a federal
facility.

---

## Part 3: Things that are right, and better than you are saying

Short list, because you should know which sentences you can lean on hard.

- **Shared colour scale.** Verified in both places: the client pools samples across channels in
  `updateAutoLevels` before taking percentiles, and the server quantizes on a range computed
  across all blocks in `serialize_frame`. The code comment even names the bug it fixed. This is
  your strongest correctness claim and you deliver it well.
- **The four-step verified operation.** All four steps are real, and step four is the one nobody
  else does: `complete_verification(gen)` only fires when a frame computed *after* the retune
  actually publishes. Keep every word of that paragraph.
- **Y-factor, 13 measurements, five of them 5G-specific.** All real. The 5G side is stronger than
  "correlators that pull out sync bursts": it resolves cell ID and beam index. If you have three
  seconds spare, "it doesn't just find the sync burst, it tells you which cell and which beam"
  is a better sentence and gives Dan more credit.
- **Frequency-domain resampling.** True and exact. `design_cola_resampler` searches for integer
  FFT sizes making the ratio exactly rational and raises rather than approximate. Your "exactly"
  is earned.
- **FIR lowpass.** True and separate: `firls`, 4001 taps, 250 kHz transition.
- **32-element array, 100 ms every 5 minutes for a week, >30 dBi, Boulder, 3.7 to 3.8 GHz.** All
  straight out of `Project_Reasoning.pdf`. Solid.
- **Zarr archive, xarray, real units on the axes.** True.
- **striqt does not transmit.** True.
- **Hotspot and shared-ethernet modes.** Both really provisioned by the installer.

Two small wording notes: it is **dBi**, not dB, for antenna gain, and your doc says the band has
been federal since well before 5G but the specific auction framing is C-band and adjacent. "3.5
to 3.8" matches your reasoning doc's "3.55 GHz to 3.8 GHz," so you are fine, but if anyone asks,
the DOE SCADA and microwave incumbency people usually cite is Western Area Power Administration
and Bonneville Power Administration.

---

## Part 4: The speech itself

The bones are good. There is one question, it gets asked in the first 30 seconds, and the last
line answers it. That is more than most technical talks manage. What follows is where it loses
the room.

### 4.1 The length problem is a structure problem

Nineteen minutes of script into a twelve minute slot is not fixed by talking faster. Something has
to leave. Here is where the time actually is:

| Section | Words | Approx |
|---|---|---|
| Spectrum, guard band, antennas | 640 | 4:25 |
| The cart, striqt, the DSP pipeline | 640 | 4:25 |
| LINDA intro, retune, three explanations | 620 | 4:15 |
| Speed test, shared scale, band monitor, caveat | 470 | 3:15 |
| AHAWI, unplug, ARIC mode, second radio, close | 480 | 3:20 |

The striqt DSP pipeline is the densest passage in the talk, and it arrives before the audience has
seen a single thing on a screen. That is the part I would look at first, and not necessarily by
cutting it. Some of it lands better attached to something visible. The resampling idea, for
instance, is easier to feel when there is a spectrogram in front of people and you can point at
the bins. Moving one or two of those explanations into the demo costs no content and buys back
the attention you are currently spending in the dark.

Beyond that, the arithmetic is stubborn. Talking faster does not close a seven minute gap, and
the demo pauses only make it wider. Three honest levers:

1. **Confirm the actual slot.** Twelve to thirteen minutes is what your structure doc assumed. If
   the real slot is twenty, most of this section evaporates and you should ignore it.
2. **Replace narration with screen time.** The only way to lose words without losing content is to
   let the software say something instead of you. There are a few specific opportunities for this
   in Part 6, and they are the cheapest minutes in the script.
3. **Pick your two densest paragraphs and halve them.** Not delete. Halve. Most of the technical
   passages here are about 40% longer than the idea inside them, which is what section 4.2 is
   really about.

### 4.2 Your best writing is at the beginnings and the worst is in the middles

The openings of your sections are consistently strong:

- "Which works perfectly as long as what you're selling isn't being used."
- "And striqt will hand you a beautiful, clean archive whether that dish was pointed at the sky or
  straight at a wall."
- "The dish just went dead. The omni didn't move."
- "It's been running this whole time."

That last one is a genuinely great closing line and you should not touch it.

The middles sag because you switch registers. You write like a person for a paragraph, then a
manual for a paragraph, then a person again. The ring buffer passage is the clearest case: "A
ring buffer is a fixed block of memory, allocated once, with a pointer that writes into it in a
circle" is textbook voice. It is correct, and it is a wall. Compare to how you handle the same
kind of idea 20 lines later: "a radio will happily accept a setting and quietly do something
else." That is the same technical content delivered as a character trait. Do that more.

Rule of thumb for the rewrite: every technical paragraph should have one sentence in it that
could not have come out of a datasheet.

### 4.3 The transitions are mostly missing

You have five section breaks marked with `---` and most of them are hard cuts. In a document that
is fine because the reader sees the whitespace. Out loud, a hard cut sounds like you lost your
place.

The ones that need a bridge:

**Into the cart.** You end on "in a setup resembling the one here" and then start "And that's what
this is." That works. Keep it.

**Into striqt.** "So you've got two antennas and a radio. What turns that into a measurement is a
library called striqt" is your best transition in the script. Keep it exactly.

**Into LINDA.** This is the weakest one and it is the most important one, because it is the hinge
of the whole talk. You end the striqt section on the amplifier joke and then start cold: "This is
Live IQ Navigation and Display Application." The joke deflates the tension you just spent a
paragraph building. Land the problem, *then* name the thing:

> "...you don't find out until you're back at your desk a week later. So I built the thing that
> tells you now."

Then the name. And move the amplifier joke earlier, into the "cable came loose" list, where it is
an example rather than a punchline.

**Into AHAWI.** "Now look at where those blocks start and stop as new rows come in" is good,
because it makes them look before you explain. More of that.

**Into ARIC mode.** "Everything I've just done assumes you already know what a local oscillator
is" is a good line but it is doing a section transition and a joke setup at once and neither
lands cleanly. Split them, and let the screen do the middle bit:

> "Everything I've just shown you assumes you already know what a local oscillator is, and why
> you'd want to move one. Most people don't. Most people shouldn't have to."
>
> `→ switch modes. Say nothing. Let them look at it.`
>
> "Same radio. Same server. Same everything underneath. You just tap the band you want."
>
> `→ tap one. Let it land.`
>
> "The two modes are called DAN mode and ARIC mode. I'll let you work out which is which."

Three beats instead of one paragraph. The transition line closes the previous section, the silence
is where the audience actually notices the interface changed, and the joke arrives last with
nothing competing against it. Right now you talk continuously through the switch, so the room is
listening when it should be looking.

### 4.4 Small line-level notes

- **"which is our friend Dan's over here!"** The exclamation reads as nervous energy. If Dan is
  in the room, look at him and say "Dan's library" and let the beat sit. Warmer with less effort.
- **"which I have definitely NOT done before"** Good joke, wrong place (see 4.3). It is also one of
  the very few self-deprecating beats in 19 minutes, which is a shame because it is the most human
  line in the script.
- **"I'll let you work out which is which"** Excellent. Do not explain it. Do not even smile,
  just move on.
- **"Or as it's known by its stage name, LINDA"** Charming, keep it.
- **"about N decibels"** Rehearse this with a real number in your mouth. Reading a live value off
  a screen mid-sentence is harder than it sounds, and you say it twice in a row. Consider saying
  it once: *"they disagree by about eight decibels about how loud the same signal is."*
- **"the omni's answer is the optimistic one, which is the dangerous direction to be wrong in"**
  This is the thesis of the entire research program in one clause. Slow down on it. Consider
  pausing after it.
- **"Nobody has to find out next Tuesday that the week is gone"** Strong. Keep.
- **"complex baseband IQ... it's voltage with a timestamp on it"** Lovely. Keep.

---

## Part 5: Pre-flight checklist

Things to settle before you walk in, in priority order.

1. **Ask Dan** whether the installed v0.7.0 applies the digital LO shift-back. Until then, use the
   safe phrasing in 1.6.
2. **Check whether the cart is running with a calibration file loaded.** If not, soften the dBm
   line (2.2).
3. **Time yourself out loud, standing up, with the demo running.** Not reading at a desk. The
   number you get will be worse than 19 minutes, not better.
4. **Rehearse the retune and watch what the screen does.** Then rewrite 1.2 to describe what you
   actually saw.
5. **Rehearse the unplug on the actual cart in a quiet room.** Confirm the moment is visible.
6. **Confirm the second radio's actual price** so the number you say is the right one. Purely a
   fact check, no comment on the line itself.
7. **Confirm every ARIC chip you plan to tap is above 300 MHz.** On an AIR8201B,
   `gateStationChips` greys out FM 98, aircraft 127, 2 m 146, marine 162, NOAA 162.475 and
   VHF-Hi 195. Tapping a dead chip on stage is a bad ten seconds.
8. **Demo a retune, not a gain change**, so the four-step verification story stays true (2.5).
9. **Have the pre-recorded backup queued full-screen**, as your structure doc says.

---

## Part 6: Ideas, offered not prescribed

You asked for this bit, so treat all of it as optional. Everything here is grounded in something
that already works in the repo, so none of it is new engineering. They are ordered by how much I
think each is worth against how much it costs you.

The thread running through them: your thesis is that you built a piece of software on top of
striqt. The strongest way to make that land is to let the software do things on stage rather than
have you describe things it does. That also happens to be where your missing minutes are.

### 6.1 Stop narrating the verification. Let the op log run.

This is the one I would do first.

`OPERATIONS` stages every radio-affecting change through `requested → validated → applying →
applied → readback → data-path → verdict`, and `_broadcaster` pushes each stage to every client as
it happens. The OPS rail tab renders them live.

Right now you spend about 120 words describing that sequence while the audience looks at a static
waterfall. Instead: put the OPS tab on screen next to the waterfall, hit Apply, and shut up. Seven
lines type themselves out, one at a time, ending in a green verdict. Then one sentence over the
top of it:

> "It asked. The radio answered. It checked the answer. Then it waited for a frame that was
> actually captured after the change landed. Only then does it say it worked."

Twenty-eight words instead of 120, and the room watched it happen rather than taking your word for
it. Which, given the talk is about not taking things on faith, is a better argument than the
paragraph was.

### 6.2 Break it on purpose

Your only failure demo right now is the unplug, and the unplug depends on RF conditions in a room
you have not stood in yet. There is a failure you can trigger deterministically, indoors, with no
antenna involved at all: ask the radio for something it will not give you.

Two versions, both already built:

- **Out of envelope.** Type a frequency outside the radio's range. `SharedConfig.update` clamps it
  and tells you it clamped it, with the reason attached. The op comes back saying what you asked
  for and what you got.
- **Above the qualified rate.** Select a sample rate above `QUALIFIED_MAX_RATE_HZ` (30.72 MS/s).
  This one is better, because it is not a refusal. The radio accepts it, reads it back perfectly,
  and LINDA still raises a persistent banner to every connected viewer saying this rate has never
  been sustained and samples may be dropping silently. Per `constants.py`, in its own words: *"a
  gappy waterfall looks like a quiet band."*

That second one is the single best demonstration of your thesis in the entire system. The radio
said yes. The readback was clean. The software warned you anyway. Nothing else in the talk makes
that point as sharply, and unlike the unplug it cannot fail on stage.

If you keep only one idea from this section, keep this one.

### 6.3 Hand the room the radio

The installer provisions a hotspot mode with a printed SSID, password and URL, every client gets
the same broadcast, and the mobile layout is a real thing you deliberately built (phone-specific
pixel heights, both spectrograms side by side, tap a pane header for focus mode).

So: put the SSID and URL on a slide early, and by the time you get to the retune, some of the room
is watching on their phones. Then you tune, and forty screens move at once.

Two honest risks. Auth is username-only with no password, and this is a federal facility, so check
with whoever owns the room first. And the read-only role is genuinely locked down (`SAFE_SELECTOR`
is a whitelist, not a hidden button), which is worth one clause if anyone asks whether the audience
can break your demo. That clause is also a nice credit to yourself.

Medium risk, high payoff. If the room says no, it costs you nothing to have asked.

### 6.4 Open in ARIC, close in DAN

At the moment ARIC mode arrives at the very end, gets about sixty words, and reads as an
afterthought. It is actually one of the two best pieces of evidence for what you built, and it is
in the wrong place.

Consider inverting it. Open the demo in ARIC mode: no jargon, no controls, tap a band, something
happens. Let the room understand the picture with nothing explained to them. Then:

> "That's the version for people who just want to see. Here's the version for Dan."

`→ switch to DAN`

Now every control you were going to explain appears at once, and it appears as a reveal rather
than as a wall. And the schema-generated form has somewhere natural to live, because "striqt
describes its own settings, so I could render the same settings two completely different ways for
two completely different people" is one idea rather than two.

This is a structural change, so only worth it if you are rewriting anyway. But it is the version
of the talk that most clearly says "I built a layer on top of striqt," which is the thing you
actually want said.

### 6.5 Press Record

The whole talk is about a week-long field campaign, and you never show that LINDA can start one.
The Record tab runs the real sweep, in-process, on the live source, with GPS position embedded in
each capture. Three seconds of it closes the loop between the live view and the archive the paper
gets written from.

One caveat to check first: recording takes the stream through `pause_and_release`, so the live view
freezes while the sweep runs. For a three second demo that is probably fine, and the UI discloses
it honestly, which is on-message. But rehearse it, and do not press it if it does not come back
cleanly.

### 6.6 Two small pieces of stagecraft

**The cold open.** Your structure doc already says do not boot on stage. Go one step further and
open on ten seconds of nothing: both panes running, no slide, no words. Let people work out that
the thing on screen is moving before you tell them anything about it. Then start on oil and
minerals.

**Do not reveal the second radio.** Have it running on the second screen from the beginning, off to
the side, unmentioned. Someone in the room will notice it and wonder. When you finally point at it
and say "that's been running the same software this whole time," the reveal is theirs rather than
yours. Same content, better beat, and it rhymes with your closing line.

---

## Summary table

| # | Claim | Verdict |
|---|---|---|
| 1 | 125 MS/s off the receiver, ring filling at 125 MS/s | **Wrong.** 125 MHz is the converter clock; the viewer runs 15.36 MS/s |
| 2 | Retune didn't freeze or lose anything | **Wrong.** Stream is rearmed, ring deliberately cleared, frames withheld |
| 3 | New striqt field appears with no code change | **Wrong.** Hardcoded `captureFields` allowlist in app.js |
| 4 | Interface limits came off the radio, not a config file | **Wrong for the AIR-T** (rate only). True for the Pluto |
| 5 | Band monitor integrates power in a box | **Wrong twice.** It averages, over a frequency band on the PSD |
| 6 | LO dealt with in the same pass, leak outside the span | **Unproven, and LINDA defaults to `lo_shift: none`** |
| 7 | Ring buffer never waits, talks to no one, browser reads it | **Overstated.** Shared lock, owns the hardware lifecycle, browser reads a separate slot |
| 8 | Everything after calibration is dBm | **Conditional.** dBm label is static; uncalibrated data still says dBm |
| 9 | Dish unplug known within a second | **Visual only.** No per-channel health signal exists |
| 10 | AHAWI: 100 ms, single pass, 20 ms segments, 15 fps | **True.** Replay is 10x slow motion, captures paced 1 s apart |
| 11 | Mismatch shown in red to everyone | **True.** Except gain, which is never verified on any adapter |
| 12 | Shared colour scale across both panes | **True**, client and server both |
| 13 | Four-step verified operation | **True**, all four steps |
| 14 | Y-factor, 13 measurements, 5G correlators, zarr, no TX | **True** |
| 15 | Exact frequency-domain resampling, 4001-tap FIR lowpass | **True** |
| 16 | 32-element array, 100 ms / 5 min / one week, >30 dBi | **True**, per Project_Reasoning.pdf |
| 17 | Hotspot, ethernet, internet | **True.** Tunnel is a separate manual path, not part of the install |

---

Nothing here is unfixable, and most of the fixes make the script shorter, which you need anyway.

The talk's real strength is that it has somewhere to arrive. striqt is a serious instrument that
sees everything and shows you none of it live, and you built the layer that closes that gap. Every
suggestion above is pointed at making that clearer, not at making it more modest. The accuracy
fixes matter for the same reason: the argument you are making is that this software tells you the
truth about what the radio is doing, and that argument is worth more if every sentence around it
is also true.
