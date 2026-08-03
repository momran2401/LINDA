from kit import *

P = deck()

def note(slide, text):
    slide.notes_slide.notes_text_frame.text = text

# ============================== 1 · TITLE ==============================
s = blank(P)
rect(s, 0, 0, W, Inches(0.10), fill=ACCENT)
linda_mark(s, ML, Inches(1.42), scale=2.55)
_, tf = tb(s, ML, Inches(3.28), Inches(7.4), Inches(0.9))
para(tf, "Live IQ Navigation and Display Application", 16, INK, bold=True, first=True)
para(tf, "Real-time spectrum measurement you can watch, on any radio.", 13.5, DIM, space_before=5)
hairline(s, ML, Inches(4.42), Inches(3.2))
_, tf = tb(s, ML, Inches(4.60), Inches(7.6), Inches(0.7))
para(tf, "Mustafa Omran   ·   NIST SURF 2026", 11, INK, bold=True, first=True)
para(tf, "Communications Technology Laboratory   ·   Mentors: Dan Kuester, Aric Sanders", 9.5, FAINT, space_before=3)
nist_mark(s)
note(s, "Up before you speak. Don't read it. First words are the spectrum-as-a-resource line.")

# ============================== 2 · THE RESOURCE ==============================
s = blank(P); nist_mark(s); footmark(s)
head(s, "THE RESOURCE", "Spectrum is finite, and it is being sold.")
_, tf = tb(s, ML, Inches(1.55), Inches(3.55), Inches(2.6))
para(tf, "3.5 to 3.8 GHz has been federal-only for a very long time.", 13, INK, bold=True, first=True, line=1.25)
para(tf, "Department of Energy point-to-point microwave links carry dam telemetry, "
         "reservoir levels and grid monitoring across terrain where fibre isn't realistic.", 11.5, DIM, space_before=8, line=1.35)
para(tf, "That same band is now being auctioned to carriers for 5G.", 11.5, INK, bold=True, space_before=8, line=1.35)

# spectrum bar diagram
bx, by, bw, bh = Inches(4.55), Inches(1.95), Inches(4.83), Inches(0.62)
rect(s, bx, by, bw, bh, fill=WELL, line=HAIR)
fed = int(bw*0.56)
rect(s, bx, by, fed, bh, fill=ASOFT, line=ACCENT, lw=1.25)
rect(s, bx+fed, by, bw-fed, bh, fill=RSOFT, line=RED, lw=1.25)
_, tf = tb(s, bx, by+Inches(0.14), Emu(fed), Inches(0.34), align=PP_ALIGN.CENTER)
para(tf, "FEDERAL INCUMBENT", 9, ACCENT, bold=True, first=True, align=PP_ALIGN.CENTER, tracking=60)
_, tf = tb(s, bx+fed, by+Inches(0.14), Emu(bw-fed), Inches(0.34), align=PP_ALIGN.CENTER)
para(tf, "AUCTIONED · 5G", 9, RED, bold=True, first=True, align=PP_ALIGN.CENTER, tracking=60)
hairline(s, bx, by+bh+Inches(0.13), bw, color=HAIR)
for frac, txt in ((0.0,"3.5 GHz"), (0.56,"3.7"), (1.0,"3.8 GHz")):
    ox = bx + int(bw*frac) - Inches(0.42)
    _, tf = tb(s, ox, by+bh+Inches(0.22), Inches(0.84), Inches(0.2), align=PP_ALIGN.CENTER)
    para(tf, txt, 8.5, FAINT, bold=True, first=True, align=PP_ALIGN.CENTER)
_, tf = tb(s, bx, by-Inches(0.42), bw, Inches(0.3))
para(tf, "Neighbours, with nothing between them.", 10.5, DIM, bold=True, first=True)

hairline(s, ML, Inches(4.30), CW)
_, tf = tb(s, ML, Inches(4.46), CW, Inches(0.4))
para(tf, "Selling a band works perfectly, as long as what you're selling isn't being used.", 12.5, INK, bold=True, first=True)
note(s, "Beat: oil/minerals analogy, then the band, then the auction. Don't read the diagram.")

# ============================== 3 · THE NUMBER ==============================
s = blank(P); nist_mark(s); footmark(s)
head(s, "THE NUMBER", "Too narrow breaks the link.\nToo wide burns billions.", size=26)
_, tf = tb(s, ML, Inches(1.72), Inches(7.6), Inches(0.35))
para(tf, "A guard band is an empty strip of spectrum left between two competing users. "
         "You can be wrong in both directions.", 12, DIM, first=True)

top = Inches(2.35); cardw = Inches(4.42); gap = Inches(0.32); ch_ = Inches(1.62)
for i,(clr,soft,ttl,body) in enumerate([
    (RED, RSOFT, "TOO LITTLE",
     "Leakage from the commercial side starts knocking out federal links. Dam telemetry, reservoir levels, grid monitoring."),
    (ACCENT, ASOFT, "TOO MUCH",
     "A public resource is fenced off that nobody is allowed to use, and billions of dollars of auction revenue are left on the table."),
]):
    x = ML + i*(cardw+gap)
    rect(s, x, top, cardw, ch_, fill=soft, line=None)
    rect(s, x, top, Inches(0.045), ch_, fill=clr)
    _, tf = tb(s, x+Inches(0.30), top+Inches(0.24), cardw-Inches(0.58), Inches(1.2))
    para(tf, ttl, 10, clr, bold=True, first=True, tracking=90)
    para(tf, body, 12, INK, space_before=7, line=1.30)

hairline(s, ML, Inches(4.32), CW)
_, tf = tb(s, ML, Inches(4.48), CW, Inches(0.4))
para(tf, "NIST doesn't pick that number. NIST hands the people who do a measurement they can trust.", 12.5, INK, bold=True, first=True)
note(s, "Land 'wrong in both directions'. The NIST line is the turn into the science.")

# ============================== 4 · THE PROBLEM ==============================
s = blank(P); nist_mark(s); footmark(s)
head(s, "THE PROBLEM", "The honest answer depends on which\nantenna you measure with.", size=25)
top = Inches(2.02); cardw = Inches(4.42); gap = Inches(0.32); ch_ = Inches(1.98)
for i,(clr,ttl,sub,body) in enumerate([
    (DIM, "OMNIDIRECTIONAL", "What people normally measure with",
     "Hears everything, from every direction, at once. A beam sweeping past is averaged in with the whole sky, so it barely registers."),
    (ACCENT, "HIGH-GAIN DISH", "What you are actually protecting",
     "Pointed at one thing, with more than 30 dBi of gain in that direction. The same beam arrives at full strength."),
]):
    x = ML + i*(cardw+gap)
    rect(s, x, top, cardw, ch_, fill=(WELL if i==0 else ASOFT), line=None)
    _, tf = tb(s, x+Inches(0.30), top+Inches(0.24), cardw-Inches(0.58), Inches(1.5))
    para(tf, ttl, 10, clr, bold=True, first=True, tracking=90)
    para(tf, sub, 10, FAINT, bold=True, space_before=3)
    para(tf, body, 12, INK, space_before=9, line=1.30)

_, tf = tb(s, ML, Inches(4.14), CW, Inches(0.3))
para(tf, "Same interference. Two completely different answers.", 12.5, DIM, bold=True, first=True)
rect(s, ML, Inches(4.52), CW, Inches(0.52), fill=RSOFT)
rect(s, ML, Inches(4.52), Inches(0.045), Inches(0.52), fill=RED)
_, tf = tb(s, ML+Inches(0.30), Inches(4.52), CW-Inches(0.6), Inches(0.52), anchor=MSO_ANCHOR.MIDDLE)
para(tf, "The omni's answer is the optimistic one. Which is the dangerous direction to be wrong in.", 12.5, INK, bold=True, first=True)
note(s, "Pause before the red line. This is the thesis of the whole research programme.")

# ============================== 5 · THE RIG ==============================
s = blank(P); nist_mark(s); footmark(s)
head(s, "THE RIG", "So the lab records both, at the same time,\non one radio.", size=25)

cy = Inches(2.35); bw_ = Inches(1.42); bh_ = Inches(0.66); sp = Inches(0.30)
chain_x = ML + Inches(2.55)
def blk(x, y, w, h, t1, t2=None, accent=False):
    rect(s, x, y, w, h, fill=(ASOFT if accent else WELL), line=(ACCENT if accent else HAIR), lw=1.1)
    _, tf = tb(s, x+Inches(0.08), y, w-Inches(0.16), h, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, t1, 10.5, (ACCENT if accent else INK), bold=True, first=True, align=PP_ALIGN.CENTER)
    if t2: para(tf, t2, 8.5, FAINT, space_before=2, align=PP_ALIGN.CENTER)
def arrow(x, y, w):
    rect(s, x, y+Inches(0.32), w, Pt(1.1), fill=HAIR)

# two antennas feeding in
blk(ML, Inches(1.86), Inches(2.20), Inches(0.60), "OMNI", "reference")
blk(ML, Inches(2.70), Inches(2.20), Inches(0.60), "DISH", "32-element array in the field", accent=True)
arrow(ML+Inches(2.20), Inches(1.86), Inches(0.35))
arrow(ML+Inches(2.20), Inches(2.70), Inches(0.35))
x = chain_x
for t1,t2,acc in [("BANDPASS","filter",False), ("LNA","amplifier",False),
                  ("DEEPWAVE","AIR8201B · 2 ch",True)]:
    blk(x, cy, bw_, bh_, t1, t2, accent=acc); x += bw_
    if t1 != "DEEPWAVE": arrow(x, cy, sp); x += sp
arrow(x, cy, sp); x += sp
blk(x, cy, Inches(1.55), bh_, "striqt → LINDA", "measure · watch", accent=True)

rect(s, ML, Inches(3.86), CW, Inches(0.62), fill=WELL)
_, tf = tb(s, ML+Inches(0.28), Inches(3.86), CW-Inches(0.56), Inches(0.62), anchor=MSO_ANCHOR.MIDDLE)
para(tf, "Two antennas, one radio, one clock. The whole point is that the two channels are "
         "measured at the same instant, so they can be compared.", 11.5, INK, first=True, line=1.25)
_, tf = tb(s, ML, Inches(4.66), CW, Inches(0.3))
para(tf, "The field rig uses a 32-element dipole array. A real dish at these frequencies is too big to move between rooftops.", 10, FAINT, first=True)
note(s, "Gesture at the cart, not the slide. The dish joke lands here.")

# ============================== 6 · striqt ==============================
s = blank(P); nist_mark(s); footmark(s)
head(s, "THE INSTRUMENT   ·   striqt, by Dan Kuester",
     "striqt turns raw IQ into calibrated,\nreal-dBm measurement.", size=25)

py_ = Inches(2.22); pw = Inches(1.66); psp = Inches(0.20); pbh = Inches(0.98)
steps = [("IQ", "complex baseband,\nup to 125 MS/s"),
         ("RESAMPLE", "exact, in the\nfrequency domain"),
         ("CALIBRATE", "Y-factor against a\nknown noise source"),
         ("ANALYSE", "13 measurements,\n5 of them 5G"),
         ("ARCHIVE", "labelled zarr,\nunits on every axis")]
x = ML
for i,(t1,t2) in enumerate(steps):
    acc = i in (1,2)
    rect(s, x, py_, pw, pbh, fill=(ASOFT if acc else WELL), line=(ACCENT if acc else HAIR), lw=1.1)
    _, tf = tb(s, x+Inches(0.10), py_+Inches(0.17), pw-Inches(0.20), Inches(0.72), align=PP_ALIGN.CENTER)
    para(tf, t1, 10, (ACCENT if acc else INK), bold=True, first=True, align=PP_ALIGN.CENTER, tracking=70)
    para(tf, t2, 8.5, DIM, space_before=4, align=PP_ALIGN.CENTER, line=1.22)
    x += pw
    if i < len(steps)-1:
        rect(s, x+Inches(0.055), py_+Inches(0.46), psp-Inches(0.11), Pt(1.1), fill=HAIR); x += psp

_, tf = tb(s, ML, Inches(3.52), Inches(4.42), Inches(0.9))
para(tf, "It refuses rather than approximate.", 12, INK, bold=True, first=True)
para(tf, "striqt searches for FFT sizes that make the rate ratio an exact rational number. "
         "If it can't find one, it raises instead of getting close.", 11, DIM, space_before=5, line=1.28)
_, tf = tb(s, ML+Inches(4.74), Inches(3.52), Inches(4.02), Inches(0.9))
para(tf, "Y-factor is what makes it metrology.", 12, INK, bold=True, first=True)
para(tf, "Measured against a noise source of known temperature, so you know how much of what "
         "you're seeing the radio added itself.", 11, DIM, space_before=5, line=1.28)
hairline(s, ML, Inches(4.68), CW)
_, tf = tb(s, ML, Inches(4.82), CW, Inches(0.3))
para(tf, "One command runs it:  sensor-sweep spec.yaml  →  a calibrated archive.", 10.5, FAINT, bold=True, first=True)
note(s, "Credit Dan by name and look at him. Don't read the pipeline boxes; narrate two of five.")

# ============================== 7 · THE GAP ==============================
s = blank(P); nist_mark(s); footmark(s)
rect(s, ML, Inches(1.16), Inches(0.055), Inches(1.70), fill=ACCENT)
_, tf = tb(s, ML+Inches(0.40), Inches(1.16), Inches(8.2), Inches(1.8))
para(tf, "A clean recording doesn't mean\nyou recorded what you meant to.", 32, INK, bold=True, first=True, line=1.10)
_, tf = tb(s, ML+Inches(0.40), Inches(3.16), Inches(7.9), Inches(1.2))
para(tf, "100 ms every 5 minutes, continuously, for about a week. You wheel the rig onto a roof, "
         "you point it, and you walk away.", 13, DIM, first=True, line=1.35)
para(tf, "A loose cable, a dead front end, an amplifier on backwards, or a band that simply had "
         "nothing in it — all four come back as a beautiful, clean archive. You find out at your "
         "desk, a week later.", 13, DIM, space_before=9, line=1.35)
note(s, "Slowest slide in the deck. Let the headline sit before you speak.")

# ============================== 8 · LINDA ==============================
s = blank(P); nist_mark(s)
rect(s, 0, 0, W, Inches(0.10), fill=ACCENT)
linda_mark(s, ML, Inches(1.62), scale=2.40)
_, tf = tb(s, ML, Inches(3.36), Inches(8.2), Inches(1.1))
para(tf, "striqt does the measuring. LINDA is how you watch it happen.", 17, INK, bold=True, first=True)
para(tf, "Every sample the radio is hearing, right now — in a browser, on a kiosk, or over a shell. "
         "Reachable over a hotspot, an ethernet cable, or the internet.", 12.5, DIM, space_before=8, line=1.32)
note(s, "Say the acronym once, then move. The cart has been live this whole time — remind them.")

# ============================== 9 · DEMO (near-empty) ==============================
s = blank(P)
footmark(s)
label(s, ML, Inches(2.66), Inches(4.0), "LIVE", size=11, color=FAINT, tracking=200)
note(s, "Holder slide. Deliberately near-empty so nothing competes with the screen. "
        "If you switch HDMI source you may never see this — that's fine.")

# ============================== 10-12 · AHAWI BUILD ==============================
def ahawi(step):
    s = blank(P); footmark(s)
    words = [("Apparently", "A"), ("How", "H"), ("Aric", "A"), ("Wanted", "W"), ("It", "I")]
    if step < 3:
        _, tf = tb(s, ML, Inches(2.16), CW, Inches(1.2))
        p = tf.paragraphs[0]; p.line_spacing = 1.10
        for i,(word, first_letter) in enumerate(words):
            if step == 2:
                r = p.add_run(); r.text = first_letter
                r.font.size = Pt(40); r.font.bold = True; r.font.name = FONT
                r.font.color.rgb = ACCENT
                r2 = p.add_run(); r2.text = word[1:] + ("  " if i < 4 else "")
                r2.font.size = Pt(40); r2.font.bold = True; r2.font.name = FONT
                r2.font.color.rgb = RGBColor(0xC3,0xCB,0xD8)
            else:
                r = p.add_run(); r.text = word + ("  " if i < 4 else "")
                r.font.size = Pt(40); r.font.bold = True; r.font.name = FONT
                r.font.color.rgb = INK
        label(s, ML, Inches(1.70), Inches(6.0), "THE THIRD MODE", size=9.5, color=ACCENT)
    else:
        _, tf = tb(s, ML, Inches(1.52), CW, Inches(1.3))
        para(tf, "AHAWI", 82, INK, bold=True, first=True, tracking=900)
        hairline(s, ML, Inches(3.06), Inches(5.4))
        _, tf = tb(s, ML, Inches(3.32), Inches(8.4), Inches(1.4))
        para(tf, "One coherent 100 ms capture. One striqt pass. Replayed 20 ms at a time.", 16, INK, bold=True, first=True)
        para(tf, "100 ms is the recording length in the real field campaign. The 5G sync burst repeats "
                 "every 20 ms, so each slice holds exactly one of them — and consecutive slices really "
                 "are consecutive in time.", 12, DIM, space_before=9, line=1.32)
    nist_mark(s)
    return s

note(ahawi(1), "Click 1. Say it straight, no wink yet.")
note(ahawi(2), "Click 2. Let them find it themselves. Do not explain the joke.")
note(ahawi(3), "Click 3, then cut back to the live view and scrub.")

# ============================== 13 · PORTABILITY ==============================
s = blank(P); nist_mark(s); footmark(s)
head(s, "PORTABILITY", "Same software. Any radio.")
_, tf = tb(s, ML, Inches(1.50), Inches(7.6), Inches(0.35))
para(tf, "Two commands to install. Plug a radio in and it works out what you gave it.", 12.5, DIM, first=True)

top = Inches(2.02); cardw = Inches(4.42); gap = Inches(0.32); ch_ = Inches(1.28)
for i,(clr, soft, price, what, sub) in enumerate([
    (ACCENT, ASOFT, "$20,000", "Deepwave AIR8201B", "Two receive channels. 125 MHz converter clock."),
    (DIM, WELL, "$2,000", "Commodity SDR", "One channel, over USB, on a Raspberry Pi."),
]):
    x = ML + i*(cardw+gap)
    rect(s, x, top, cardw, ch_, fill=soft, line=None)
    _, tf = tb(s, x+Inches(0.30), top+Inches(0.22), cardw-Inches(0.58), Inches(1.0))
    para(tf, price, 26, clr, bold=True, first=True)
    para(tf, what, 11.5, INK, bold=True, space_before=4)
    para(tf, sub, 10.5, DIM, space_before=3, line=1.25)

rect(s, ML, Inches(3.56), CW, Inches(0.50), fill=RGBColor(0xF7,0xF9,0xFC), line=HAIR, lw=1.0)
_, tf = tb(s, ML+Inches(0.26), Inches(3.56), CW-Inches(0.52), Inches(0.50), anchor=MSO_ANCHOR.MIDDLE)
para(tf, "git clone   ·   bash install_linda.sh", 12, INK, bold=True, first=True, font="Courier New")
hairline(s, ML, Inches(4.34), CW)
_, tf = tb(s, ML, Inches(4.50), CW, Inches(0.5))
para(tf, "Channel count and the list of sample rates the radio will actually accept come off "
         "the radio itself at startup. Nobody types them in.", 12, INK, bold=True, first=True, line=1.28)
note(s, "This is the Gabe beat. Point at the second screen, not the slide.")

# ============================== 14 · CLOSE ==============================
s = blank(P); nist_mark(s)
rect(s, 0, 0, W, Inches(0.10), fill=ACCENT)
rect(s, ML, Inches(0.92), Inches(0.055), Inches(1.52), fill=ACCENT)
_, tf = tb(s, ML+Inches(0.40), Inches(0.92), Inches(8.1), Inches(1.6))
para(tf, "The next person who puts one of these on a roof\nwill know, in the first ten seconds,\nwhether the week is going to be worth anything.",
     23, INK, bold=True, first=True, line=1.18)

hairline(s, ML, Inches(2.86), CW)
_, tf = tb(s, ML, Inches(3.06), Inches(2.90), Inches(1.2))
para(tf, "And not only 5G and dams.", 12, INK, bold=True, first=True)
para(tf, "Is this new signal going to step on that critical one? "
         "The question is everywhere right now.", 10.5, DIM, space_before=5, line=1.28)
for i,(t, sub) in enumerate([("CBRS", "and naval radar"),
                             ("6 GHz Wi-Fi", "over utility microwave"),
                             ("Radar altimeters", "and 5G in C-band")]):
    x = ML + Inches(3.20) + i*Inches(1.92)
    rect(s, x, Inches(3.06), Inches(1.80), Inches(0.86), fill=WELL)
    _, tf = tb(s, x+Inches(0.16), Inches(3.06), Inches(1.50), Inches(0.86), anchor=MSO_ANCHOR.MIDDLE)
    para(tf, t, 11.5, ACCENT, bold=True, first=True)
    para(tf, sub, 9, DIM, space_before=3, line=1.22)

hairline(s, ML, Inches(4.36), CW)
_, tf = tb(s, ML, Inches(4.52), CW, Inches(0.5))
para(tf, "striqt: Dan Kuester   ·   Measurement framing: Aric Sanders   ·   NIST Communications Technology Laboratory",
     9.5, FAINT, bold=True, first=True)
linda_mark(s, W - MR - Inches(1.15), Inches(4.44), scale=0.36, bar_color=FAINT, text_color=FAINT)
note(s, "End here. Do not add a 'Questions?' slide. Stop talking after 'worth anything'.")

P.save("/sessions/elegant-quirky-archimedes/mnt/outputs/LINDA_SURF_2026_deck.pptx")
print("slides:", len(P.slides.__iter__.__self__._sldIdLst))
