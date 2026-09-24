"""Modern-creepy look & feel. Change colours, taglines and MSG text freely."""
import discord

THEMES = {
    "pinewood": {"color": 0x1F3D2B, "tag": "🌲 pinewood", "line": "the trees are listening"},
    "static": {"color": 0x4A4D4C, "tag": "📻 static", "line": "signal unstable. do not adjust."},
    "ember": {"color": 0x7A1616, "tag": "🕯️ ember", "line": "someone is humming on the frequency"},
    "midnight": {"color": 0x0A0F1E, "tag": "🌑 midnight", "line": "it is later than you think"},
}

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


def color(g):
    return discord.Color(g["custom_color"] if g.get("custom_color") is not None else _t(g)["color"])


def fmt(sec):
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def bar(pos, dur, w=16):
    if not dur:
        return "▒░▒░ LIVE ░▒░▒░▒░▒"
    n = min(w - 1, int(w * pos / dur))
    return "▰" * n + "◉" + "▱" * (w - 1 - n)


def _esc(s):
    return discord.utils.escape_markdown(str(s))


def now_playing(g, title, artist, pos, dur, paused, qlen, by, fx_label):
    t = _t(g)
    state = "▮▮ paused" if paused else "▶ on air"
    e = discord.Embed(
        color=color(g),
        title=f"▓▒░ {_esc(title)}"[:256],
        description=f"**{_esc(artist)}**\n`{bar(pos, dur)}`\n`{fmt(pos)} / {fmt(dur) if dur else 'live'}` · {state}",
    )
    e.add_field(name="signal", value=fx_label, inline=True)
    e.add_field(name="vol", value=f"{g['volume']}%", inline=True)
    e.add_field(name="loop", value=g["loop"], inline=True)
    e.add_field(name="next", value=f"{qlen} in queue", inline=True)
    e.set_footer(text=f"{t['tag']} · {t['line']} · req. {by}")
    return e


def idle_embed(g):
    t = _t(g)
    e = discord.Embed(color=color(g), title="░▒▓ signal lost", description="ไม่มีอะไรออกอากาศแล้ว… ตอนนี้")
    e.set_footer(text=f"{t['tag']} · {t['line']}")
    return e


def queue_embed(g, now, items):
    t = _t(g)
    lines = [f"`{i + 1:02d}` {_esc(a)} — {_esc(b)}" for i, (a, b) in enumerate(items[:10])]
    more = f"\n… +{len(items) - 10} more" if len(items) > 10 else ""
    e = discord.Embed(color=color(g), title="▓▒░ queue", description=(f"**now:** {_esc(now)}\n\n" if now else "") + ("\n".join(lines) or "empty") + more)
    e.set_footer(text=f"{t['tag']} · {t['line']}")
    return e
