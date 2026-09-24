"""FFmpeg audio-filter presets + custom EQ. Edit PRESETS to add your own 'signals'."""

# key: (label, ffmpeg -af chain). asetrate is preceded by aresample=48000 so pitch math is exact.
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


def label(g):
    name = PRESETS.get(g["fx"], PRESETS["off"])[0]
    eq = g["eq"]
    extra = f" · eq {eq['bass']:+d}/{eq['treble']:+d}" if eq["bass"] or eq["treble"] else ""
    return name + extra


def build(g):
    parts = []
    base = PRESETS.get(g["fx"], PRESETS["off"])[1]
    if base:
        parts.append(base)
    if g["eq"]["bass"]:
        parts.append(f"bass=g={g['eq']['bass']}")
    if g["eq"]["treble"]:
        parts.append(f"treble=g={g['eq']['treble']}")
    if parts:
        parts.append("alimiter=limit=0.9")  # keep boosted chains from clipping
    return ",".join(parts)
