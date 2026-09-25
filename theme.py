"""Eluga Studio — themes, Music Card layouts, and all bot copy live here.
Add a theme: one line in THEMES. Add a bar style: one line in BARS.
"""
import discord

THEMES = {
    "pinewood": {"color": 0x1F3D2B, "emoji": "🌲", "tag": "pinewood", "line": "the trees are listening"},
    "static":   {"color": 0x4A4D4C, "emoji": "📻", "tag": "static", "line": "signal unstable. do not adjust."},
    "ember":    {"color": 0x7A1616, "emoji": "🕯️", "tag": "ember", "line": "someone is humming on the frequency"},
    "midnight": {"color": 0x0A0F1E, "emoji": "🌑", "tag": "midnight", "line": "it is later than you think"},
    "aurora":   {"color": 0x1A5C6B, "emoji": "🌌", "tag": "aurora", "line": "something drifts overhead"},
    "sakura":   {"color": 0xB5567A, "emoji": "🌸", "tag": "sakura", "line": "petals fall, the signal stays"},
    "cyber":    {"color": 0x00C2A8, "emoji": "⚡", "tag": "cyber", "line": "connection: stable? unclear."},
    "winter":   {"color": 0x2E4057, "emoji": "❄️", "tag": "winter", "line": "the frequency is cold tonight"},
}

# progress-bar renderers — add a key here and it shows up in /tune card barstyle automatically.
# Each is (filled, marker, empty) tiled across the bar width.
_BAR_GLYPHS = {
    "blocks":   ("▰", "◉", "▱"),
    "dots":     ("●", "○", "·"),
    "wave":     ("▬", "◆", "─"),
    "arrows":   ("=", ">", " "),
    "hearts":   ("♥", "❤", "♡"),
    "stars":    ("★", "✦", "☆"),
    "notes":    ("♪", "♫", "·"),
    "squares":  ("■", "◆", "□"),
    "circles":  ("⬤", "◉", "○"),
    "diamonds": ("◆", "✦", "◇"),
    "pipes":    ("│", "┃", "┆"),
    "shade":    ("█", "▓", "░"),
    "braille":  ("⣿", "⣤", "⠂"),
    "petals":   ("✿", "❀", "·"),
    "ocean":    ("≈", "≋", "·"),
    "carets":   ("^", "⌃", "‾"),
    "ticks":    ("✓", "✔", "·"),
    "plus":     ("+", "⊕", "·"),
    "hash":     ("#", "▣", "-"),
    "bullets":  ("•", "●", "∘"),
    "lines":    ("―", "▮", "·"),
    "gems":     ("♦", "✦", "◇"),
    "moons":    ("●", "☾", "○"),
    "suns":     ("☀", "✹", "·"),
    "petals2":  ("❁", "✾", "·"),
}
BARS = {name: (lambda n, w, f=f, m=m, e=e: f * n + m + e * (w - 1 - n)) for name, (f, m, e) in _BAR_GLYPHS.items()}

# card layouts — which fields show on the Now Playing card, and in what shape
LAYOUTS = {
    "classic":   {"desc": "artist + bar + fields grid", "fields": ("signal", "vol", "loop", "next")},
    "compact":   {"desc": "one-line status, no field grid", "fields": ()},
    "cinematic": {"desc": "big title, fewer fields, mood line up top", "fields": ("signal", "next")},
}

CARD_DEFAULTS = {"layout": "classic", "bar": "blocks", "bar_width": 16, "show_footer_line": True}

# animated Now Playing backgrounds — a rolling window over a glyph string, cycled ~every 5s by
# the player's ticker. Symbols only (no pictograph emoji mixed into the pattern), tiled to fill
# the full card width. Kept to low-FPS text swaps (not real GIFs) to stay under Discord's edit
# rate limits. "aurora" is rendered in green only, via a Discord ```ansi code block.
BG_WIDTH = 28
BG_THEMES = {
    "none": None,
    "aurora": "▁▂▃▅▇▇▅▃▂▁▂▃▅▇▅▃",
    "ocean": "≈≋≈~≈≋≈~≈≋≈~≈≋≈~",
    "rain": "┆┊¦│┆┊¦│┆┊¦│┆┊¦│",
    "galaxy": "·⋆∘⁘·⋆∘⁘·⋆∘⁘·⋆∘⁘",
    "sakura": "⋆✧∘·⋆✧∘·⋆✧∘·⋆✧∘·",
    "fire": "░▒▓█▓▒░▒▓█▓▒░▒▓█",
    "ice": "·❆✶∘·❆✶∘·❆✶∘·❆✶∘",
    "night_city": "▁▂▃▅▇▅▃▂▁▂▃▅▇▅▃▂",
}


def bg_frame(g, i, width: int = BG_WIDTH):
    chars = BG_THEMES.get(g.get("bg", "none"))
    if not chars:
        return None
    n = len(chars)
    offset = i % n
    tiled = chars * (width // n + 2)
    frame = tiled[offset:offset + width]
    if g.get("bg") == "aurora":  # green-only, real color via Discord's ansi code-block support
        return f"```ansi\n\u001b[32m{frame}\u001b[0m\n```"
    return f"`{frame}`"

MSG = {
    "novoice": "🌲 ไม่มีใครอยู่ในป่านี้… เข้าห้องเสียงก่อน แล้วเรียกใหม่",
    "busy": "📻 ฉันกำลังส่งสัญญาณอยู่อีกห้อง ไปฟังที่นั่น หรือรอให้จบก่อน",
    "wrongch": "▒ เธอไม่ได้อยู่บนความถี่เดียวกับฉัน (เข้าห้องเสียงเดียวกับบอทก่อน)",
    "yt": "🚫 ที่นี่ไม่รับสัญญาณจาก YouTube — ใช้ไฟล์ในคลัง หรือลิงก์สตรีมตรงแทน",
    "badurl": "🚫 ลิงก์นี้ใช้ไม่ได้ (รับเฉพาะ http/https สาธารณะ ไม่รับที่ชี้เข้าเครือข่ายภายใน)",
    "notfound": "░ ไม่พบเพลงนี้ในคลัง… พิมพ์ชื่อบางส่วน หรือเลือกจากรายการที่เด้งขึ้นมา",
    "emptylib": "░ คลังเพลงว่างเปล่า — ใส่ไฟล์ลงโฟลเดอร์ music/ แล้วใช้ /tune rescan",
    "fail": "▒ เชื่อมต่อห้องเสียงไม่ได้ ลองใหม่อีกครั้ง",
    "tuning": "📻 กำลังจูนสัญญาณ…",
    "queued": "▸ เข้าคิว",
    "nothing": "░ ไม่มีอะไรกำลังเล่นอยู่",
    "noperm": "🔒 ต้องมีสิทธิ์ Manage Server",
}


def say(key):
    return MSG[key]


def _t(g):
    return THEMES.get(g["theme"], THEMES["pinewood"])


def _card(g):
    c = dict(CARD_DEFAULTS)
    c.update(g.get("card") or {})
    return c


def color(g):
    return discord.Color(g["custom_color"] if g.get("custom_color") is not None else _t(g)["color"])


def fmt(sec):
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def bar(pos, dur, g):
    c = _card(g)
    w = c["bar_width"]
    if not dur:
        return "▒░▒░ LIVE ░▒░▒░▒░▒"
    n = min(w - 1, max(0, int(w * pos / dur)))
    draw = BARS.get(c["bar"], BARS["blocks"])
    return draw(n, w)


def _esc(s):
    return discord.utils.escape_markdown(str(s))


def now_playing(g, title, artist, pos, dur, paused, qlen, by, fx_label, bg_i=0):
    t, c = _t(g), _card(g)
    layout = c["layout"]
    state = "▮▮ paused" if paused else "▶ on air"
    time_str = f"{fmt(pos)} / {fmt(dur) if dur else 'live'}"
    bgf = bg_frame(g, bg_i)
    bg_line = f"{bgf}\n" if bgf else ""

    if layout == "compact":
        desc = f"{bg_line}**{_esc(artist)}** · `{time_str}` · {state}\n`{bar(pos, dur, g)}`"
        e = discord.Embed(color=color(g), title=f"{t['emoji']} {_esc(title)}"[:256], description=desc)
    elif layout == "cinematic":
        desc = (f"{bg_line}*{t['line']}*\n\n## {_esc(title)}\n**{_esc(artist)}**\n"
                f"`{bar(pos, dur, g)}`\n`{time_str}` · {state}")
        e = discord.Embed(color=color(g), description=desc)
    else:  # classic
        e = discord.Embed(
            color=color(g),
            title=f"▓▒░ {_esc(title)}"[:256],
            description=f"{bg_line}**{_esc(artist)}**\n`{bar(pos, dur, g)}`\n`{time_str}` · {state}",
        )

    field_map = {
        "signal": ("signal", fx_label),
        "vol": ("vol", f"{g['volume']}%"),
        "loop": ("loop", g["loop"]),
        "next": ("next", f"{qlen} in queue"),
    }
    for key in LAYOUTS.get(layout, LAYOUTS["classic"])["fields"]:
        name, value = field_map[key]
        e.add_field(name=name, value=value, inline=True)

    if c["show_footer_line"]:
        e.set_footer(text=f"{t['emoji']} {t['tag']} · {t['line']} · req. {by}")
    else:
        e.set_footer(text=f"req. {by}")
    return e


def idle_embed(g):
    t = _t(g)
    e = discord.Embed(color=color(g), title="░▒▓ signal lost", description="ไม่มีอะไรออกอากาศแล้ว… ตอนนี้")
    e.set_footer(text=f"{t['emoji']} {t['tag']} · {t['line']}")
    return e


def queue_embed(g, now, items):
    t = _t(g)
    lines = [f"`{i + 1:02d}` {_esc(a)} — {_esc(b)}" for i, (a, b) in enumerate(items[:10])]
    more = f"\n… +{len(items) - 10} more" if len(items) > 10 else ""
    e = discord.Embed(color=color(g), title="▓▒░ queue", description=(f"**now:** {_esc(now)}\n\n" if now else "") + ("\n".join(lines) or "empty") + more)
    e.set_footer(text=f"{t['emoji']} {t['tag']} · {t['line']}")
    return e


def profile_embed(g, guild_name):
    t, c = _t(g), _card(g)
    rows = [
        ("Theme", f"{t['emoji']} {t['tag']}"),
        ("Card layout", f"{c['layout']} — {LAYOUTS.get(c['layout'], {}).get('desc', '')}"),
        ("Bar style", c["bar"]),
        ("Now Playing bg", g.get("bg", "none")),
        ("FX", g["fx"]),
        ("EQ", f"bass {g['eq']['bass']:+d} / vocal {g['eq'].get('vocal', 0):+d} / treble {g['eq']['treble']:+d}"),
        ("Pro EQ bands set", str(sum(1 for v in (g.get('eq_pro') or {}).values() if v))),
        ("Preamp", f"{g.get('preamp', 0):+d} dB"),
        ("Loop", g["loop"]),
        ("Volume", f"{g['volume']}%"),
        ("Stations saved", str(len(g.get("stations", {})))),
    ]
    desc = "\n".join(f"**{k}** — {v}" for k, v in rows)
    e = discord.Embed(color=color(g), title=f"{t['emoji']} ELUGA PROFILE", description=desc)
    e.set_footer(text=guild_name)
    return e
