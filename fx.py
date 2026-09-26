"""Eluga audio engine — 3 tiers of depth, all compiled into one ffmpeg -af chain.

Basic    — bass / vocal / treble tone knobs + presets (fx.PRESETS)
Pro      — 11-band graphic EQ (fx.BANDS) + preamp
Advanced — compressor, reverb amount, stereo width, loudness normalize

Everything is additive: presets + basic EQ + pro EQ + advanced FX + preamp +
volume boost, then a final limiter so nothing clips no matter how much is stacked on.
"""

PRESETS = {
    "off": ("clean", ""),
    "bass": ("bass boost", "bass=g=9:f=100:w=0.8"),
    "nightcore": ("nightcore", "aresample=48000,asetrate=58560,aresample=48000"),
    "slowed": ("slowed + reverb", "aresample=48000,asetrate=41280,aresample=48000,aecho=0.8:0.85:70|140:0.35|0.25"),
    "am_radio": ("AM radio", "highpass=f=350,lowpass=f=3200,acompressor=threshold=0.1:ratio=4,volume=1.4"),
    "haunted": ("haunted hall", "aecho=0.8:0.9:900|1700:0.35|0.22,lowpass=f=6000,tremolo=f=0.5:d=0.2"),
    "8d": ("8D drift", "apulsator=hz=0.09"),
    "night": ("night drive", "bass=g=4,treble=g=-3,aecho=0.7:0.7:40:0.25"),
}

# Pro tier: 11-band graphic EQ. key is what's stored in g["eq_pro"]; label is shown in commands.
BANDS = [
    ("32", "32 Hz"), ("60", "60 Hz"), ("120", "120 Hz"), ("144", "144 Hz"), ("256", "256 Hz"),
    ("512", "512 Hz"), ("1000", "1 kHz"), ("2000", "2 kHz"), ("4000", "4 kHz"), ("8000", "8 kHz"), ("16000", "16 kHz"),
]

EQ_DEFAULTS = {"bass": 0, "vocal": 0, "treble": 0}          # Basic tier
ADV_DEFAULTS = {
    "compressor": False, "reverb": 0, "width": 0, "normalize": False,
    "highpass": 0,     # Hz, 0 = off
    "lowpass": 0,      # Hz, 0 = off
    "shelf_ends": False,  # make the 32Hz/16kHz Pro bands real shelves instead of bells
    "deesser": 0,      # 0-100 — dynamic de-ess intensity (real frequency-dependent compression, not a static cut)
    "loud_target": 0,  # 0 = off, else -N LUFS via EBU R128 loudnorm
}  # Advanced tier
VOLUME_MAX = 300  # % — anything over 100 is a real gain boost, not just attenuation

# tier ranges, shared by the slash commands and the interactive /tune eq panel
BASIC_MAX = 15     # dB either side on bass/vocal/treble — ~30 levels at 1dB steps
BASIC_STEP = 1
PRO_MAX = 20       # dB either side, per band
PRO_STEP = 2
PREAMP_MAX = 20
PREAMP_STEP = 2
ADV_REVERB_MAX = 100
ADV_WIDTH_MAX = 200
ADV_STEP = 10

# --- deep-EQ additions: filter shapes, true parametric bands, dynamic de-ess, loudness match ---
HP_MIN, HP_MAX, HP_STEP = 20, 500, 10        # highpass cutoff, Hz — rumble/room-noise removal
LP_MIN, LP_MAX, LP_STEP = 2000, 20000, 500   # lowpass cutoff, Hz
DEESSER_MAX, DEESSER_STEP = 100, 10          # dynamic, frequency-dependent — only reacts when the sibilant band gets loud
LOUD_TARGETS = [0, 14, 16, 19, 23]           # 0 = off, else target -N LUFS (EBU R128); 14 = streaming-loud … 23 = broadcast-quiet
CUSTOM_BAND_MAX = 4                          # extra fully-parametric bands, on top of the fixed 11
CUSTOM_FREQ_MIN, CUSTOM_FREQ_MAX = 20, 20000
CUSTOM_GAIN_MAX = 20
CUSTOM_Q_MIN, CUSTOM_Q_MAX = 0.1, 3.0        # bandwidth in octaves — low Q = wide/gentle, high Q = narrow/surgical (notch territory)

BAND_SHORT = {"32": "32", "60": "60", "120": "120", "144": "144", "256": "256",
              "512": "512", "1000": "1k", "2000": "2k", "4000": "4k", "8000": "8k", "16000": "16k"}


def label(g):
    name = PRESETS.get(g["fx"], PRESETS["off"])[0]
    bits = []
    eq = g.get("eq") or EQ_DEFAULTS
    if eq.get("bass") or eq.get("vocal") or eq.get("treble"):
        bits.append(f"eq {eq.get('bass', 0):+d}/{eq.get('vocal', 0):+d}/{eq.get('treble', 0):+d}")
    pro = g.get("eq_pro") or {}
    if any(pro.values()):
        bits.append(f"{sum(1 for v in pro.values() if v)}-band pro eq")
    custom = g.get("eq_custom") or []
    if custom:
        bits.append(f"{len(custom)} custom band" + ("s" if len(custom) != 1 else ""))
    if g.get("preamp"):
        bits.append(f"preamp {g['preamp']:+d}dB")
    adv = g.get("adv") or ADV_DEFAULTS
    adv_on = [k for k in ("compressor", "normalize") if adv.get(k)] + (["reverb"] if adv.get("reverb") else []) + (["width"] if adv.get("width") else [])
    if adv_on:
        bits.append("adv:" + "+".join(adv_on))
    if adv.get("highpass"):
        bits.append(f"HP {adv['highpass']}Hz")
    if adv.get("lowpass"):
        bits.append(f"LP {adv['lowpass']}Hz")
    if adv.get("shelf_ends"):
        bits.append("shelf ends")
    if adv.get("deesser"):
        bits.append(f"de-ess {adv['deesser']}%")
    if adv.get("loud_target"):
        bits.append(f"loud -{adv['loud_target']}LUFS")
    if g.get("volume", 100) > 100:
        bits.append(f"boost {g['volume']}%")
    return name + (" · " + " · ".join(bits) if bits else "")


def build(g):
    """Return the full ffmpeg -af filter string for this guild's settings."""
    parts = []

    base = PRESETS.get(g["fx"], PRESETS["off"])[1]
    if base:
        parts.append(base)

    eq = g.get("eq") or EQ_DEFAULTS
    if eq.get("bass"):
        parts.append(f"bass=g={eq['bass']}")
    if eq.get("vocal"):  # a broad mid-band bump/cut around the vocal presence range
        parts.append(f"equalizer=f=2500:width_type=o:width=1.5:g={eq['vocal']}")
    if eq.get("treble"):
        parts.append(f"treble=g={eq['treble']}")

    adv = g.get("adv") or ADV_DEFAULTS

    if adv.get("highpass"):  # real high-pass, not just a bell — cuts rumble/room noise below the cutoff
        parts.append(f"highpass=f={adv['highpass']}")

    pro = g.get("eq_pro") or {}
    shelf_ends = adv.get("shelf_ends")
    for band, gain in pro.items():
        if not gain:
            continue
        if shelf_ends and band == "32":       # boundary bands as real shelves — smoother than a bell at the edge
            parts.append(f"bass=g={gain}:f=32:w=0.6")
        elif shelf_ends and band == "16000":
            parts.append(f"treble=g={gain}:f=16000:w=0.6")
        else:
            parts.append(f"equalizer=f={band}:width_type=o:width=0.9:g={gain}")

    for band in (g.get("eq_custom") or [])[:CUSTOM_BAND_MAX]:  # true parametric — any freq, any Q, boost or cut
        if band.get("gain"):
            parts.append(f"equalizer=f={band['freq']}:width_type=o:width={band.get('q', 1.0)}:g={band['gain']}")

    if adv.get("lowpass"):
        parts.append(f"lowpass=f={adv['lowpass']}")

    if g.get("preamp"):
        parts.append(f"volume={g['preamp']}dB")

    if adv.get("deesser"):  # dynamic — only clamps down when the sibilant band actually gets loud
        i = max(1, min(100, adv["deesser"])) / 100
        parts.append(f"deesser=i={i:.2f}:m={i:.2f}")

    if adv.get("normalize"):
        parts.append("dynaudnorm=f=200:g=15")
    if adv.get("loud_target"):  # EBU R128 target loudness — keeps tracks matched instead of jumping in volume
        parts.append(f"loudnorm=I=-{adv['loud_target']}:TP=-1.5:LRA=11")
    if adv.get("compressor"):
        parts.append("acompressor=threshold=0.1:ratio=3:attack=20:release=250")
    if adv.get("reverb"):  # 0-100 -> decay/delay mix
        amt = adv["reverb"] / 100
        parts.append(f"aecho=0.8:{0.4 + amt * 0.5:.2f}:{int(30 + amt * 900)}:{0.15 + amt * 0.35:.2f}")
    if adv.get("width"):  # 0-200 -> ffmpeg extrastereo m (0 = unchanged, 1 = double width)
        parts.append(f"extrastereo=m={adv['width'] / 100:.2f}")

    vol = g.get("volume", 60)
    if vol > 100:  # boosted gain — needs to happen before the limiter, python-side volume stays at 1.0
        parts.append(f"volume={vol / 100:.2f}")

    # always on now, not just a warning — a real lookahead-ish limiter so nothing can clip no matter how hard it's boosted
    parts.append("alimiter=limit=0.95:attack=7:release=100")
    return ",".join(parts)


def player_volume(g):
    """What discord.PCMVolumeTransformer.volume should be — capped at 1.0 since >100% is done in ffmpeg (see build())."""
    return min(g.get("volume", 60), 100) / 100


def freq_chart(pro: dict) -> str:
    """Small ascii frequency-response readout — kept for text/export contexts (e.g. /tune eq_show).
    The interactive panel uses per-band embed fields instead, since this wraps badly on mobile."""
    rows = (20, 10, 0, -10, -20)
    lines = []
    for r in rows:
        prefix = f"{r:+3d}dB" if r else " 0dB"
        cells = []
        for key, _ in BANDS:
            gain = pro.get(key, 0) or 0
            nearest = min(rows, key=lambda x: abs(x - gain))
            cells.append("●" if nearest == r else ("─" if r == 0 else " "))
        lines.append(f"{prefix} " + " ".join(cells))
    axis = "      " + " ".join(BAND_SHORT[k].rjust(3) for k, _ in BANDS)
    return "\n".join(lines) + "\n" + axis


def slider(value: int, maximum: int, width: int = 9, centered: bool = True) -> str:
    """A little mixing-console fader — used by the interactive EQ panel instead of raw numbers."""
    if not centered:
        pos = 0 if maximum <= 0 else round(max(0, min(value, maximum)) / maximum * (width - 1))
        return "▰" * pos + "●" + "▱" * (width - 1 - pos)
    pos = round((max(-maximum, min(value, maximum)) + maximum) / (2 * maximum) * (width - 1))
    mid = width // 2
    chars = ["┈"] * width
    chars[mid] = "┆"
    chars[pos] = "●"
    return "".join(chars)


def _bell_db(f, fc, gain, oct_width):
    """Gaussian-in-log-frequency approximation of a peaking/bell filter — for the graph only, not audio."""
    import math
    if f <= 0 or fc <= 0 or not gain:
        return 0.0
    x = math.log2(f / fc) / max(oct_width, 0.05)
    return gain * math.exp(-(x * x))


def _shelf_db(f, fc, gain, low):
    """Smooth-step approximation of a low/high shelf — for the graph only."""
    import math
    if f <= 0 or fc <= 0 or not gain:
        return 0.0
    x = math.log2(f / fc)
    s = 1 / (1 + math.exp(-x * 4))  # ~0 well below fc, ~1 well above fc
    return gain * (1 - s) if low else gain * s


def resp_db(g, f):
    """Approximate total response in dB at frequency f, combining every active stage.
    Good enough to *look at* on the graph — not a substitute for measuring the real ffmpeg output."""
    total = 0.0
    eq = g.get("eq") or EQ_DEFAULTS
    adv = g.get("adv") or ADV_DEFAULTS
    if eq.get("bass"):
        total += _shelf_db(f, 100, eq["bass"], low=True)
    if eq.get("treble"):
        total += _shelf_db(f, 8000, eq["treble"], low=False)
    if eq.get("vocal"):
        total += _bell_db(f, 2500, eq["vocal"], 1.5)

    pro = g.get("eq_pro") or {}
    shelf_ends = adv.get("shelf_ends")
    for band, gain in pro.items():
        if not gain:
            continue
        fc = float(band)
        if shelf_ends and band == "32":
            total += _shelf_db(f, 32, gain, low=True)
        elif shelf_ends and band == "16000":
            total += _shelf_db(f, 16000, gain, low=False)
        else:
            total += _bell_db(f, fc, gain, 0.9)

    for cb in (g.get("eq_custom") or [])[:CUSTOM_BAND_MAX]:
        total += _bell_db(f, cb.get("freq", 1000), cb.get("gain", 0), cb.get("q", 1.0))

    if adv.get("highpass") and f < adv["highpass"]:
        import math
        total += max(-24.0, -12.0 * math.log2(adv["highpass"] / max(f, 1)))
    if adv.get("lowpass") and f > adv["lowpass"]:
        import math
        total += max(-24.0, -12.0 * math.log2(f / adv["lowpass"]))

    return max(-24.0, min(24.0, total))
