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
ADV_DEFAULTS = {"compressor": False, "reverb": 0, "width": 0, "normalize": False}  # Advanced tier
VOLUME_MAX = 300  # % — anything over 100 is a real gain boost, not just attenuation


def label(g):
    name = PRESETS.get(g["fx"], PRESETS["off"])[0]
    bits = []
    eq = g.get("eq") or EQ_DEFAULTS
    if eq.get("bass") or eq.get("vocal") or eq.get("treble"):
        bits.append(f"eq {eq.get('bass', 0):+d}/{eq.get('vocal', 0):+d}/{eq.get('treble', 0):+d}")
    pro = g.get("eq_pro") or {}
    if any(pro.values()):
        bits.append(f"{sum(1 for v in pro.values() if v)}-band pro eq")
    if g.get("preamp"):
        bits.append(f"preamp {g['preamp']:+d}dB")
    adv = g.get("adv") or ADV_DEFAULTS
    adv_on = [k for k in ("compressor", "normalize") if adv.get(k)] + (["reverb"] if adv.get("reverb") else []) + (["width"] if adv.get("width") else [])
    if adv_on:
        bits.append("adv:" + "+".join(adv_on))
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

    pro = g.get("eq_pro") or {}
    for band, gain in pro.items():
        if gain:
            parts.append(f"equalizer=f={band}:width_type=o:width=0.9:g={gain}")

    if g.get("preamp"):
        parts.append(f"volume={g['preamp']}dB")

    adv = g.get("adv") or ADV_DEFAULTS
    if adv.get("normalize"):
        parts.append("dynaudnorm=f=200:g=15")
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

    if parts:  # limiter last, always — the safety net for every stacked effect above
        parts.append("alimiter=limit=0.9")
    return ",".join(parts)


def player_volume(g):
    """What discord.PCMVolumeTransformer.volume should be — capped at 1.0 since >100% is done in ffmpeg (see build())."""
    return min(g.get("volume", 60), 100) / 100
