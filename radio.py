"""Eluga Radio — music cog. Sources: local library, direct/Icecast streams, Discord attachments. No YouTube."""
from __future__ import annotations

import asyncio
import base64
import difflib
import ipaddress
import json
import logging
import os
import random
import re
import shutil
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands

import fx
import theme
from store import Store
from vault import Vault

try:
    from mutagen import File as MutagenFile
except ImportError:  # optional: only used for tags + duration
    MutagenFile = None

log = logging.getLogger("eluga")
EXT = {".mp3", ".flac", ".ogg", ".opus", ".wav", ".m4a", ".aac", ".wma"}
BLOCKED = ("youtube.com", "youtu.be", "youtube-nocookie.com")
# http-only options; whitelist stops ffmpeg from opening file:/concat:/etc. from user links
NET_OPTS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -protocol_whitelist http,https,tcp,tls,crypto"


@dataclass
class Track:
    title: str
    artist: str
    source: str
    local: bool
    duration: int | None
    by: str
    msg_ref: tuple | None = None   # (channel_id, message_id) that holds the vault file
    refreshed: float = 0.0


def probe_duration(path: str):
    if MutagenFile:
        try:
            return int(MutagenFile(path).info.length)
        except Exception:
            pass
    return None


def make_track(path: Path, by: str) -> Track:
    title, artist, dur = path.stem, "unknown", None
    if MutagenFile:
        try:
            m = MutagenFile(path, easy=True)
            if m is not None:
                dur = int(m.info.length)
                title = (m.get("title") or [title])[0]
                artist = (m.get("artist") or [artist])[0]
        except Exception:
            pass
    return Track(title, artist, str(path), True, dur, by)


class Player:
    def __init__(self, cog: "Radio", guild: discord.Guild):
        self.cog, self.guild = cog, guild
        self.queue: deque[Track] = deque()
        self.history: list[Track] = []
        self.current: Track | None = None
        self.channel = None
        self.panel: discord.Message | None = None
        self.radio = False
        self.src = None
        self._gen = 0
        self._t0 = 0.0
        self._offset = 0.0
        self._paused_at = None
        self._idle_task = None
        self._ticker_task = None
        self._bg_i = 0

    @property
    def g(self):
        return self.cog.store.get(self.guild.id)

    @property
    def vc(self):
        return self.guild.voice_client

    def pos(self):
        if not self.current:
            return 0.0
        p = (self._paused_at or time.monotonic()) - self._t0 + self._offset
        d = self.current.duration
        return min(p, d) if d else p

    # ---- playback -------------------------------------------------------
    async def start(self, seek: float = 0.0):
        vc, t = self.vc, self.current
        if not vc or not t:
            return
        await self.cog.refresh(t)
        self._gen += 1
        gen = self._gen
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        if t.local:
            before = f"-ss {seek:.2f}" if seek > 0.5 else None
        elif t.duration:  # known length (vault songs) → seekable over http
            before = NET_OPTS + (f" -ss {seek:.2f}" if seek > 0.5 else "")
        else:
            before, seek = NET_OPTS, 0.0
        af = fx.build(self.g)
        opts = "-vn" + (f' -af "{af}"' if af else "")
        raw = discord.FFmpegPCMAudio(t.source, executable=self.cog.ffmpeg, before_options=before, options=opts)
        self.src = discord.PCMVolumeTransformer(raw, fx.player_volume(self.g))
        self._offset, self._t0, self._paused_at = seek, time.monotonic(), None
        vc.play(self.src, after=lambda e, gen=gen: asyncio.run_coroutine_threadsafe(self._after(e, gen), self.cog.bot.loop))
        self._cancel_idle()
        self._ensure_ticker()

    def _ensure_ticker(self):
        if self._ticker_task is None or self._ticker_task.done():
            self._ticker_task = asyncio.create_task(self._tick())

    async def _tick(self):
        """Low-FPS refresh: real-time position + one animated-background frame, every 5s.
        Deliberately slow — Discord rate-limits message edits, this stays comfortably under that."""
        try:
            while self.current:
                await asyncio.sleep(5)
                if not self.current:
                    break
                self._bg_i += 1
                await self.refresh_panel()
        except asyncio.CancelledError:
            pass

    async def _after(self, err, gen):
        if gen != self._gen:
            return  # stale callback from a track we replaced/skipped
        if err:
            log.warning("player error: %s", err)
        try:
            await self.advance()
        except Exception:
            log.exception("advance failed")

    async def advance(self, skip: bool = False):
        g, cur = self.g, self.current
        if cur and g["loop"] == "track" and not skip:
            await self.start()
            return
        if cur:
            self.history = (self.history + [cur])[-25:]
            if g["loop"] == "queue":
                self.queue.append(cur)
        if not self.queue and self.radio:
            await self.refill()
        if not self.queue:
            self.current = None
            await self.refresh_panel()
            self._idle_task = asyncio.create_task(self._idle())
            return
        self.current = self.queue.popleft()
        await self.start()
        await self.send_panel()

    async def skip(self):
        self._gen += 1
        if self.vc:
            self.vc.stop()
        await self.advance(skip=True)

    async def previous(self):
        if self.current and self.pos() > 5:
            await self.start()
            return
        if not self.history:
            return
        if self.current:
            self.queue.appendleft(self.current)
        self.current = self.history.pop()
        await self.start()
        await self.send_panel()

    def toggle_pause(self):
        vc = self.vc
        if not vc:
            return
        if vc.is_paused():
            vc.resume()
            self._t0 += time.monotonic() - self._paused_at
            self._paused_at = None
        elif vc.is_playing():
            vc.pause()
            self._paused_at = time.monotonic()

    async def restart_in_place(self):
        if self.current and self.vc and (self.vc.is_playing() or self.vc.is_paused()):
            await self.start(self.pos() if (self.current.local or self.current.duration) else 0.0)
            await self.refresh_panel()

    async def refill(self):
        lib = self.cog.library
        if not lib:
            return
        recent = {t.source for t in self.history[-10:]}
        pool = [k for k in lib if str(lib[k]) not in recent] or list(lib)
        picks = random.sample(pool, min(8, len(pool)))
        loop = asyncio.get_running_loop()
        tracks = await loop.run_in_executor(None, lambda: [make_track(lib[k], "radio") for k in picks])
        self.queue.extend(tracks)

    def shuffle(self):
        items = list(self.queue)
        random.shuffle(items)
        self.queue = deque(items)

    # ---- panel ----------------------------------------------------------
    def embed(self):
        t = self.current
        if not t:
            return theme.idle_embed(self.g)
        return theme.now_playing(self.g, t.title, t.artist, self.pos(), t.duration,
                                 self._paused_at is not None, len(self.queue), t.by, fx.label(self.g), self._bg_i)

    async def send_panel(self):
        if self.panel:
            try:
                await self.panel.delete()
            except discord.HTTPException:
                pass
            self.panel = None
        if self.channel:
            try:
                self.panel = await self.channel.send(embed=self.embed(), view=Panel(self))
            except discord.HTTPException:
                pass

    async def refresh_panel(self):
        if self.panel:
            try:
                await self.panel.edit(embed=self.embed(), view=Panel(self) if self.current else None)
            except discord.HTTPException:
                self.panel = None

    # ---- lifecycle ------------------------------------------------------
    def _cancel_idle(self):
        if self._idle_task:
            self._idle_task.cancel()
            self._idle_task = None

    async def _idle(self):
        await asyncio.sleep(180)
        if not self.current:
            await self.destroy()

    async def destroy(self):
        self._gen += 1
        self._cancel_idle()
        if self._ticker_task:
            self._ticker_task.cancel()
            self._ticker_task = None
        self.queue.clear()
        self.current = None
        self.radio = False
        self.cog.players.pop(self.guild.id, None)
        if self.panel:
            try:
                await self.panel.delete()
            except discord.HTTPException:
                pass
        if self.vc:
            await self.vc.disconnect()


class Panel(discord.ui.View):
    def __init__(self, p: Player):
        super().__init__(timeout=None)
        self.p = p
        self.fx_select.options = [
            discord.SelectOption(label=v[0], value=k, default=(k == p.g["fx"])) for k, v in fx.PRESETS.items()
        ]

    async def interaction_check(self, i: discord.Interaction):
        vc = self.p.guild.voice_client
        if vc and i.user.voice and i.user.voice.channel == vc.channel:
            return True
        await i.response.send_message(theme.say("wrongch"), ephemeral=True)
        return False

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.secondary)
    async def prev(self, i, b):
        await i.response.defer()
        await self.p.previous()

    @discord.ui.button(emoji="⏯", style=discord.ButtonStyle.primary)
    async def pause(self, i, b):
        self.p.toggle_pause()
        await i.response.edit_message(embed=self.p.embed(), view=Panel(self.p))

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.secondary)
    async def skip(self, i, b):
        await i.response.defer()
        await self.p.skip()

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary)
    async def loop(self, i, b):
        g = self.p.g
        g["loop"] = {"off": "track", "track": "queue", "queue": "off"}[g["loop"]]
        self.p.cog.store.save()
        await i.response.edit_message(embed=self.p.embed(), view=Panel(self.p))

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger)
    async def stop(self, i, b):
        await i.response.defer()
        await self.p.destroy()

    @discord.ui.select(placeholder="☾ signal filter", options=[discord.SelectOption(label="clean", value="off")])
    async def fx_select(self, i, s: discord.ui.Select):
        self.p.g["fx"] = s.values[0]
        self.p.cog.store.save()
        await i.response.defer()
        await self.p.restart_in_place()

    @discord.ui.button(emoji="🎚", label="EQ", style=discord.ButtonStyle.secondary, row=2)
    async def eq(self, i, b):
        view = EQPanel(self.p.cog, self.p.guild.id)
        await i.response.send_message(embed=view.embed(), view=view, ephemeral=True)


class SavePresetModal(discord.ui.Modal, title="บันทึกพรีเซ็ต EQ"):
    name = discord.ui.TextInput(label="ชื่อพรีเซ็ต", max_length=32, placeholder="เช่น bright, punchy bass")

    def __init__(self, panel: "EQPanel"):
        super().__init__()
        self.panel = panel

    async def on_submit(self, i: discord.Interaction):
        g = self.panel.g
        g.setdefault("eq_pro_presets", {})[self.name.value[:32]] = {
            "bands": dict(g.get("eq_pro") or {}), "preamp": g.get("preamp", 0),
        }
        self.panel.cog.store.save()
        self.panel._build()
        await i.response.edit_message(embed=self.panel.embed(), view=self.panel)


class EQPanel(discord.ui.View):
    """One interactive message flipping between the Basic / Pro / Advanced EQ tabs —
    dropdown to switch mode, +/- buttons to adjust, Reset All always available."""

    def __init__(self, cog: "Radio", guild_id: int, mode: str = "basic", band: str | None = None):
        super().__init__(timeout=300)
        self.cog, self.guild_id, self.mode = cog, guild_id, mode
        self.band = band or fx.BANDS[0][0]
        self._build()

    @property
    def g(self):
        return self.cog.store.get(self.guild_id)

    def _build(self):
        self.clear_items()
        mode_sel = discord.ui.Select(
            placeholder="เลือกโหมด EQ...", row=0,
            options=[
                discord.SelectOption(label="Basic", emoji="📻", value="basic", default=self.mode == "basic"),
                discord.SelectOption(label="Pro", emoji="⚡", value="pro", default=self.mode == "pro"),
                discord.SelectOption(label="Advanced", emoji="🎙️", value="advanced", default=self.mode == "advanced"),
            ],
        )

        async def on_mode(i: discord.Interaction):
            self.mode = i.data["values"][0]
            self._build()
            await i.response.edit_message(embed=self.embed(), view=self)

        mode_sel.callback = on_mode
        self.add_item(mode_sel)

        if self.mode == "basic":
            self._build_basic()
        elif self.mode == "pro":
            self._build_pro()
        else:
            self._build_advanced()

        reset = discord.ui.Button(label="↺ Reset All", style=discord.ButtonStyle.danger, row=4)
        reset.callback = self._reset_all
        self.add_item(reset)

    # ---- Basic ------------------------------------------------------------
    def _build_basic(self):
        for row, (key, label) in enumerate((("bass", "เบส"), ("vocal", "เสียงร้อง"), ("treble", "แหลม")), start=1):
            minus = discord.ui.Button(label=f"{label} −", style=discord.ButtonStyle.secondary, row=row)
            plus = discord.ui.Button(label=f"{label} +", style=discord.ButtonStyle.secondary, row=row)
            minus.callback = self._basic_step(key, -1)
            plus.callback = self._basic_step(key, 1)
            self.add_item(minus)
            self.add_item(plus)

    def _basic_step(self, key, direction):
        async def cb(i: discord.Interaction):
            g = self.g
            g["eq"][key] = max(-fx.BASIC_MAX, min(fx.BASIC_MAX, g["eq"][key] + direction * fx.BASIC_STEP))
            self.cog.store.save()
            await self._apply(i)
        return cb

    # ---- Pro ---------------------------------------------------------------
    def _build_pro(self):
        band_sel = discord.ui.Select(
            placeholder=f"แบนด์: {fx.BAND_SHORT[self.band]}", row=1,
            options=[discord.SelectOption(label=fx.BAND_SHORT[k], value=k, default=k == self.band) for k, _ in fx.BANDS],
        )

        async def on_band(i: discord.Interaction):
            self.band = i.data["values"][0]
            self._build()
            await i.response.edit_message(embed=self.embed(), view=self)

        band_sel.callback = on_band
        self.add_item(band_sel)

        band_minus = discord.ui.Button(label="แบนด์ −", style=discord.ButtonStyle.secondary, row=2)
        band_plus = discord.ui.Button(label="แบนด์ +", style=discord.ButtonStyle.secondary, row=2)
        band_minus.callback = self._pro_step(-1)
        band_plus.callback = self._pro_step(1)
        self.add_item(band_minus)
        self.add_item(band_plus)

        pre_minus = discord.ui.Button(label="Preamp −", style=discord.ButtonStyle.primary, row=3)
        pre_plus = discord.ui.Button(label="Preamp +", style=discord.ButtonStyle.primary, row=3)
        pre_minus.callback = self._preamp_step(-1)
        pre_plus.callback = self._preamp_step(1)
        self.add_item(pre_minus)
        self.add_item(pre_plus)

        save = discord.ui.Button(label="💾 Save Preset", style=discord.ButtonStyle.success, row=3)
        save.callback = self._save_preset
        self.add_item(save)

        presets = self.g.get("eq_pro_presets") or {}
        if presets:
            load = discord.ui.Select(
                placeholder="โหลดพรีเซ็ตที่บันทึกไว้...", row=4,
                options=[discord.SelectOption(label=n, value=n) for n in list(presets)[:25]],
            )

            async def on_load(i: discord.Interaction):
                p = presets.get(i.data["values"][0])
                if p:
                    g = self.g
                    g["eq_pro"] = dict(p.get("bands") or {})
                    g["preamp"] = p.get("preamp", 0)
                    self.cog.store.save()
                await self._apply(i)

            load.callback = on_load
            self.add_item(load)

    def _pro_step(self, direction):
        async def cb(i: discord.Interaction):
            g = self.g
            cur = (g.get("eq_pro") or {}).get(self.band, 0)
            g.setdefault("eq_pro", {})[self.band] = max(-fx.PRO_MAX, min(fx.PRO_MAX, cur + direction * fx.PRO_STEP))
            self.cog.store.save()
            await self._apply(i)
        return cb

    def _preamp_step(self, direction):
        async def cb(i: discord.Interaction):
            g = self.g
            g["preamp"] = max(-fx.PREAMP_MAX, min(fx.PREAMP_MAX, g.get("preamp", 0) + direction * fx.PREAMP_STEP))
            self.cog.store.save()
            await self._apply(i)
        return cb

    async def _save_preset(self, i: discord.Interaction):
        await i.response.send_modal(SavePresetModal(self))

    # ---- Advanced ----------------------------------------------------------
    def _build_advanced(self):
        adv = self.g["adv"]
        comp = discord.ui.Button(label=f"Compressor: {'on' if adv['compressor'] else 'off'}",
                                  style=discord.ButtonStyle.secondary, row=1)
        comp.callback = self._toggle("compressor")
        self.add_item(comp)
        norm = discord.ui.Button(label=f"Normalize: {'on' if adv['normalize'] else 'off'}",
                                  style=discord.ButtonStyle.secondary, row=1)
        norm.callback = self._toggle("normalize")
        self.add_item(norm)

        for row, (key, lbl, mx) in enumerate((("reverb", "Reverb", fx.ADV_REVERB_MAX), ("width", "Width", fx.ADV_WIDTH_MAX)), start=2):
            minus = discord.ui.Button(label=f"{lbl} −", style=discord.ButtonStyle.secondary, row=row)
            plus = discord.ui.Button(label=f"{lbl} +", style=discord.ButtonStyle.secondary, row=row)
            minus.callback = self._adv_step(key, -1, mx)
            plus.callback = self._adv_step(key, 1, mx)
            self.add_item(minus)
            self.add_item(plus)

    def _toggle(self, key):
        async def cb(i: discord.Interaction):
            g = self.g
            g["adv"][key] = not g["adv"][key]
            self.cog.store.save()
            self._build()
            await self._apply(i)
        return cb

    def _adv_step(self, key, direction, maximum):
        async def cb(i: discord.Interaction):
            g = self.g
            g["adv"][key] = max(0, min(maximum, g["adv"][key] + direction * fx.ADV_STEP))
            self.cog.store.save()
            await self._apply(i)
        return cb

    # ---- shared --------------------------------------------------------
    async def _reset_all(self, i: discord.Interaction):
        g = self.g
        g["eq"] = dict(fx.EQ_DEFAULTS)
        g["eq_pro"] = {}
        g["preamp"] = 0
        g["adv"] = dict(fx.ADV_DEFAULTS)
        self.cog.store.save()
        self._build()
        await self._apply(i)

    async def _apply(self, i: discord.Interaction):
        p = self.cog.players.get(self.guild_id)
        if p:
            await p.restart_in_place()
        await i.response.edit_message(embed=self.embed(), view=self)

    def embed(self):
        g = self.g
        if self.mode == "basic":
            e = g["eq"]
            title = "📻 Basic EQ"
            desc = (f"เบส `{e['bass']:+d} dB` · เสียงร้อง `{e['vocal']:+d} dB` · แหลม `{e['treble']:+d} dB`\n"
                    f"ปรับได้ ±{fx.BASIC_MAX} dB ทีละ {fx.BASIC_STEP} dB ต่อการกด")
        elif self.mode == "pro":
            pro = g.get("eq_pro") or {}
            title = "⚡ Pro EQ — 11-band"
            desc = (f"Preamp `{g.get('preamp', 0):+d} dB` · แบนด์ที่เลือก `{fx.BAND_SHORT[self.band]}` "
                    f"(`{pro.get(self.band, 0):+d} dB`)\n```\n{fx.freq_chart(pro)}\n```")
        else:
            a = g["adv"]
            title = "🎙️ Advanced EQ"
            desc = ("⚠️ สำหรับผู้ใช้ที่ต้องการปรับแต่งละเอียด — บางค่าอาจทำให้เสียงแตกหรือดังผิดปกติ ปรับอย่างระมัดระวัง 🔊\n\n"
                    f"Compressor `{a['compressor']}` · Normalize `{a['normalize']}`\n"
                    f"Reverb `{a['reverb']}` · Stereo Width `{a['width']}`")
        emb = discord.Embed(color=theme.color(g), title=title, description=desc)
        emb.set_footer(text=fx.label(g))
        return emb


class Radio(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ffmpeg = os.getenv("FFMPEG_PATH", "ffmpeg")
        self.root = Path(os.getenv("MUSIC_DIR", "music")).resolve()
        self.vault = Vault()
        self.store = Store(col=self.vault.settings if self.vault.enabled else None)
        self.library: dict[str, Path] = {}
        self.players: dict[int, Player] = {}

    async def cog_load(self):
        await self.vault.init()
        await self.store.load()
        await self.rescan()

    async def interaction_check(self, i: discord.Interaction):
        if i.guild is None:
            await i.response.send_message("🌲 ใช้ในเซิร์ฟเวอร์เท่านั้น", ephemeral=True)
            return False
        return True

    # ---- helpers --------------------------------------------------------
    async def rescan(self) -> int:
        def scan():
            out = {}
            if self.root.is_dir():
                for p in self.root.rglob("*"):
                    if p.suffix.lower() in EXT and p.is_file() and p.resolve().is_relative_to(self.root):
                        rel = p.relative_to(self.root).as_posix()
                        if len(rel) <= 100:  # autocomplete value limit
                            out[rel] = p
            return out
        self.library = await asyncio.get_running_loop().run_in_executor(None, scan)
        return len(self.library)

    def player(self, guild) -> Player:
        if guild.id not in self.players:
            self.players[guild.id] = Player(self, guild)
        return self.players[guild.id]

    def find_local(self, q: str):
        if q in self.library:
            return self.library[q]
        ql = q.lower()
        hits = [k for k in self.library if ql in k.lower()]
        if hits:
            return self.library[min(hits, key=len)]
        m = difflib.get_close_matches(q, list(self.library), n=1, cutoff=0.5)
        return self.library[m[0]] if m else None

    @staticmethod
    async def public_host(host: str) -> bool:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None)
        except OSError:
            return False
        for *_, sa in infos:
            ip = ipaddress.ip_address(sa[0].split("%")[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True

    async def resolve(self, q: str, by: str, name: str | None = None, gid: int | None = None):
        """Return a Track, or a themed error string."""
        q = q.strip()
        if q.lower().startswith(("http://", "https://")):
            u = urlparse(q)
            host = (u.hostname or "").lower()
            if host.endswith(BLOCKED):
                return theme.say("yt")
            if not host or not await self.public_host(host):
                return theme.say("badurl")
            return Track(name or Path(u.path).name or host, "stream", q, False, None, by)
        if gid and self.vault.enabled:
            s = await self.vault.get(gid, q)
            if s:
                ref = tuple(s["msg_ref"]) if s.get("msg_ref") else None
                return Track(s["name"], "vault", s["url"], False, s.get("duration"), by, ref)
        path = self.find_local(q)
        if not path:
            return theme.say("notfound")
        return await asyncio.get_running_loop().run_in_executor(None, make_track, path, by)

    async def refresh(self, t: Track):
        """Discord CDN links expire — fetch a fresh one from the original message (like Anyaluga)."""
        if not t.msg_ref or time.monotonic() - t.refreshed < 1800:
            return
        t.refreshed = time.monotonic()
        try:
            ch = self.bot.get_channel(t.msg_ref[0]) or await self.bot.fetch_channel(t.msg_ref[0])
            m = await ch.fetch_message(t.msg_ref[1])
            if m.attachments:
                t.source = m.attachments[0].url
        except discord.HTTPException as e:
            log.warning("could not refresh %r: %s", t.title, e)

    async def join(self, i: discord.Interaction):
        v = i.user.voice
        if not v or not v.channel:
            await i.followup.send(theme.say("novoice"), ephemeral=True)
            return None
        vc = i.guild.voice_client
        try:
            if not vc:
                await v.channel.connect(self_deaf=True)
            elif vc.channel != v.channel:
                if vc.is_playing() or vc.is_paused():
                    await i.followup.send(theme.say("busy"), ephemeral=True)
                    return None
                await vc.move_to(v.channel)
        except (discord.ClientException, asyncio.TimeoutError) as e:
            log.warning("voice connect failed: %s", e)
            await i.followup.send(theme.say("fail"), ephemeral=True)
            return None
        p = self.player(i.guild)
        p.channel = i.channel
        return p

    async def enqueue(self, i, p: Player, track: Track):
        p.radio = False if p.current is None else p.radio
        p.queue.append(track)
        if p.current is None:
            await i.followup.send(theme.say("tuning"))
            await p.advance()
        else:
            await i.followup.send(f"{theme.say('queued')} #{len(p.queue)} · `{track.title}`")

    def _need_admin(self, i):
        return i.user.guild_permissions.manage_guild

    # ---- play commands --------------------------------------------------
    @app_commands.command(name="play", description="เล่นเพลงจากคลัง หรือลิงก์สตรีมตรง (ไม่รองรับ YouTube)")
    async def play(self, i: discord.Interaction, query: str):
        await i.response.defer()
        t = await self.resolve(query, i.user.display_name, gid=i.guild.id)
        if isinstance(t, str):
            await i.followup.send(t, ephemeral=True)
            return
        p = await self.join(i)
        if p:
            await self.enqueue(i, p, t)

    @play.autocomplete("query")
    async def play_ac(self, i, cur: str):
        names = await self.vault.names(i.guild.id, cur) if self.vault.enabled else []
        names += [k for k in self.library if cur.lower() in k.lower()]
        return [app_commands.Choice(name=n[-100:], value=n) for n in dict.fromkeys(names)][:25]

    @app_commands.command(name="playfile", description="เล่นไฟล์เสียงที่แนบมา")
    async def playfile(self, i: discord.Interaction, file: discord.Attachment):
        await i.response.defer()
        if not (file.content_type or "").startswith("audio/") or file.size > 50 * 1024 * 1024:
            await i.followup.send(theme.say("notfound"), ephemeral=True)
            return
        p = await self.join(i)
        if p:
            await self.enqueue(i, p, Track(Path(file.filename).stem, "upload", file.url, False, None, i.user.display_name))

    @app_commands.command(name="radio", description="เปิดโหมดวิทยุ: สุ่มเพลงจากคลังไม่จบ")
    async def radio(self, i: discord.Interaction):
        await i.response.defer()
        if not self.library:
            await i.followup.send(theme.say("emptylib"), ephemeral=True)
            return
        p = await self.join(i)
        if not p:
            return
        p.radio = True
        await i.followup.send(theme.say("tuning"))
        if p.current is None:
            await p.advance()

    # ---- transport ------------------------------------------------------
    @app_commands.command(name="pause", description="หยุด/เล่นต่อ")
    async def pause(self, i: discord.Interaction):
        p = self.players.get(i.guild.id)
        if not p or not p.current:
            return await i.response.send_message(theme.say("nothing"), ephemeral=True)
        p.toggle_pause()
        await i.response.send_message("▮▮" if p._paused_at else "▶")
        await p.refresh_panel()

    @app_commands.command(name="skip", description="ข้ามเพลง")
    async def skip(self, i: discord.Interaction):
        p = self.players.get(i.guild.id)
        if not p or not p.current:
            return await i.response.send_message(theme.say("nothing"), ephemeral=True)
        await i.response.send_message("⏭")
        await p.skip()

    @app_commands.command(name="stop", description="หยุดและออกจากห้อง")
    async def stop(self, i: discord.Interaction):
        p = self.players.get(i.guild.id)
        await i.response.send_message("░▒▓ signal lost")
        if p:
            await p.destroy()
        elif i.guild.voice_client:
            await i.guild.voice_client.disconnect()

    @app_commands.command(name="shuffle", description="สับคิว")
    async def shuffle(self, i: discord.Interaction):
        p = self.players.get(i.guild.id)
        if p:
            p.shuffle()
        await i.response.send_message("🔀")

    @app_commands.command(name="loop", description="วนเพลง")
    @app_commands.choices(mode=[app_commands.Choice(name=m, value=m) for m in ("off", "track", "queue")])
    async def loop(self, i: discord.Interaction, mode: app_commands.Choice[str]):
        self.store.get(i.guild.id)["loop"] = mode.value
        self.store.save()
        await i.response.send_message(f"🔁 {mode.value}")

    @app_commands.command(name="volume", description="ระดับเสียง 0-300 (เกิน 100 = บูสต์เสียงจริง มีลิมิตเตอร์กันแตกให้)")
    async def volume(self, i: discord.Interaction, level: app_commands.Range[int, 0, fx.VOLUME_MAX]):
        g = self.store.get(i.guild.id)
        old = g["volume"]
        g["volume"] = level
        self.store.save()
        p = self.players.get(i.guild.id)
        warn = " ⚠ เกิน 100% เสียงอาจเพี้ยนได้ถ้าเพลงดังอยู่แล้ว" if level > 100 else ""
        await i.response.send_message(f"🔉 {level}%{warn}")
        if p and p.src:
            if level <= 100 and old <= 100:
                p.src.volume = level / 100  # both sides of the boost line stay instant, no restart
            else:
                await p.restart_in_place()

    @app_commands.command(name="queue", description="ดูคิว")
    async def queue(self, i: discord.Interaction):
        p = self.players.get(i.guild.id)
        items = [(t.artist, t.title) for t in p.queue] if p else []
        now = p.current.title if p and p.current else None
        await i.response.send_message(embed=theme.queue_embed(self.store.get(i.guild.id), now, items))

    @app_commands.command(name="nowplaying", description="ส่งแผงควบคุมใหม่")
    async def nowplaying(self, i: discord.Interaction):
        p = self.players.get(i.guild.id)
        if not p or not p.current:
            return await i.response.send_message(theme.say("nothing"), ephemeral=True)
        p.channel = i.channel
        await i.response.send_message("📻", ephemeral=True)
        await p.send_panel()

    # ---- vault: online song library (MongoDB) ---------------------------
    async def _vault_ok(self, i: discord.Interaction) -> bool:
        if not self.vault.enabled:
            await i.response.send_message("░ ยังไม่ได้ตั้ง MONGODB_URI — คลังเพลงออนไลน์ใช้ไม่ได้", ephemeral=True)
            return False
        if not self._need_admin(i):
            await i.response.send_message(theme.say("noperm"), ephemeral=True)
            return False
        return True

    @app_commands.command(name="addsong", description="เพิ่มเพลงเข้าคลังด้วยลิงก์ไฟล์เสียงตรง (ต้องมีสิทธิ์ Manage Server)")
    async def addsong(self, i: discord.Interaction, name: str, url: str):
        if not await self._vault_ok(i):
            return
        await i.response.defer(ephemeral=True)
        if not url.lower().startswith(("http://", "https://")):
            return await i.followup.send(theme.say("badurl"))
        t = await self.resolve(url, i.user.display_name)
        if isinstance(t, str):
            return await i.followup.send(t)
        await self.vault.add(i.guild.id, name[:100], url, i.user.id)
        host = (urlparse(url).hostname or "").lower()
        note = "\n⚠ ลิงก์ Discord หมดอายุเอง — ใช้ /addsongfromvideo แทนจะไม่หมดอายุ" if host.endswith(("discordapp.com", "discordapp.net", "discord.com")) else ""
        await i.followup.send(f"📻 saved `{name[:100]}` → `/play {name[:100]}`{note}")

    @app_commands.command(name="addsongfromvideo", description="แนบวิดีโอ/เสียง บอทตัดเสียงเก็บเข้าคลัง (ลิงก์ไม่หมดอายุ)")
    async def addsongfromvideo(self, i: discord.Interaction, name: str, file: discord.Attachment):
        if not await self._vault_ok(i):
            return
        if not (file.content_type or "").startswith(("video/", "audio/")):
            return await i.response.send_message(theme.say("notfound"), ephemeral=True)
        await i.response.defer()
        tmp = tempfile.mkdtemp(prefix="eluga_")
        try:
            src = os.path.join(tmp, "in" + (os.path.splitext(file.filename)[1] or ".bin"))
            out = os.path.join(tmp, "audio.m4a")
            await file.save(src)
            proc = await asyncio.create_subprocess_exec(
                self.ffmpeg, "-y", "-i", src, "-vn", "-c:a", "aac", "-b:a", "128k", out,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                _, err = await asyncio.wait_for(proc.communicate(), 180)
            except asyncio.TimeoutError:
                proc.kill()
                return await i.followup.send(theme.say("fail"))
            if proc.returncode != 0 or not os.path.isfile(out):
                log.error("extract failed: %s", err.decode(errors="ignore")[-500:])
                return await i.followup.send(theme.say("fail"))
            dur = await asyncio.get_running_loop().run_in_executor(None, probe_duration, out)
            msg = await i.channel.send(
                content=f"📦 vault: **{discord.utils.escape_markdown(name[:100])}** — ห้ามลบข้อความนี้ ไม่งั้นเพลงจะหาย",
                file=discord.File(out, filename=f"eluga_{int(time.time())}.m4a"))
            await self.vault.add(i.guild.id, name[:100], msg.attachments[0].url, i.user.id, (msg.channel.id, msg.id), dur)
            await i.followup.send(f"📻 saved `{name[:100]}` → `/play {name[:100]}`")
        except discord.HTTPException as e:
            log.error("vault upload failed: %s", e)
            await i.followup.send(theme.say("fail"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @app_commands.command(name="removesong", description="ลบเพลงออกจากคลัง (ต้องมีสิทธิ์ Manage Server)")
    async def removesong(self, i: discord.Interaction, name: str):
        if not await self._vault_ok(i):
            return
        ok = await self.vault.remove(i.guild.id, name)
        await i.response.send_message("🗑" if ok else theme.say("notfound"), ephemeral=True)

    @removesong.autocomplete("name")
    async def vault_ac(self, i, cur: str):
        names = await self.vault.names(i.guild.id, cur) if self.vault.enabled else []
        return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names]

    @app_commands.command(name="songlist", description="ดูเพลงในคลัง")
    async def songlist(self, i: discord.Interaction):
        if not self.vault.enabled:
            return await i.response.send_message("░ ยังไม่ได้ตั้ง MONGODB_URI", ephemeral=True)
        names = await self.vault.names(i.guild.id, "", 30)
        total = await self.vault.count(i.guild.id)
        lines = [f"`{n + 1:02d}` {discord.utils.escape_markdown(x)}" for n, x in enumerate(names)]
        more = f"\n… +{total - len(names)} more" if total > len(names) else ""
        e = discord.Embed(color=theme.color(self.store.get(i.guild.id)), title="▓▒░ vault",
                          description=("\n".join(lines) or "empty") + more)
        await i.response.send_message(embed=e)

    # ---- stations -------------------------------------------------------
    station = app_commands.Group(name="station", description="สถานีวิทยุสตรีมที่บันทึกไว้")

    @station.command(name="add", description="บันทึกสถานี (ต้องมีสิทธิ์ Manage Server)")
    async def st_add(self, i: discord.Interaction, name: str, url: str):
        if not self._need_admin(i):
            return await i.response.send_message(theme.say("noperm"), ephemeral=True)
        await i.response.defer(ephemeral=True)
        t = await self.resolve(url, i.user.display_name, name)
        if isinstance(t, str):
            return await i.followup.send(t)
        self.store.get(i.guild.id)["stations"][name[:80].replace(".", "·").replace("$", "＄")] = url
        self.store.save()
        await i.followup.send(f"📻 saved `{name}`")

    @station.command(name="remove", description="ลบสถานี (ต้องมีสิทธิ์ Manage Server)")
    async def st_remove(self, i: discord.Interaction, name: str):
        if not self._need_admin(i):
            return await i.response.send_message(theme.say("noperm"), ephemeral=True)
        self.store.get(i.guild.id)["stations"].pop(name, None)
        self.store.save()
        await i.response.send_message("🗑", ephemeral=True)

    @station.command(name="play", description="เล่นสถานีที่บันทึกไว้")
    async def st_play(self, i: discord.Interaction, name: str):
        await i.response.defer()
        url = self.store.get(i.guild.id)["stations"].get(name)
        if not url:
            return await i.followup.send(theme.say("notfound"), ephemeral=True)
        p = await self.join(i)
        if not p:
            return
        t = await self.resolve(url, i.user.display_name, name)
        if isinstance(t, str):
            return await i.followup.send(t, ephemeral=True)
        await self.enqueue(i, p, t)

    @st_play.autocomplete("name")
    @st_remove.autocomplete("name")
    async def st_ac(self, i, cur: str):
        names = [n for n in self.store.get(i.guild.id)["stations"] if cur.lower() in n.lower()][:25]
        return [app_commands.Choice(name=n, value=n) for n in names]

    # ---- customization (Manage Server) ---------------------------------
    tune = app_commands.Group(
        name="tune", description="ปรับแต่งบอท",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @tune.command(name="theme", description="เลือกธีม")
    @app_commands.choices(name=[app_commands.Choice(name=k, value=k) for k in theme.THEMES])
    async def t_theme(self, i: discord.Interaction, name: app_commands.Choice[str]):
        g = self.store.get(i.guild.id)
        g["theme"], g["custom_color"] = name.value, None
        self.store.save()
        p = self.players.get(i.guild.id)
        t = theme.THEMES[name.value]
        await i.response.send_message(f"{t['emoji']} {t['tag']} — {t['line']}", ephemeral=True)
        if p:
            await p.refresh_panel()

    @tune.command(name="color", description="สี embed เอง เช่น #7a1616 (พิมพ์ reset เพื่อกลับธีม)")
    async def t_color(self, i: discord.Interaction, hex: str):
        g = self.store.get(i.guild.id)
        if hex.lower() == "reset":
            g["custom_color"] = None
        else:
            try:
                g["custom_color"] = int(hex.lstrip("#"), 16) & 0xFFFFFF
            except ValueError:
                return await i.response.send_message("ใช้รูปแบบ #rrggbb", ephemeral=True)
        self.store.save()
        await i.response.send_message("🎨 ok", ephemeral=True)

    @tune.command(name="fx", description="เลือกฟิลเตอร์เสียง")
    @app_commands.choices(preset=[app_commands.Choice(name=v[0], value=k) for k, v in fx.PRESETS.items()])
    async def t_fx(self, i: discord.Interaction, preset: app_commands.Choice[str]):
        self.store.get(i.guild.id)["fx"] = preset.value
        self.store.save()
        await i.response.send_message(f"☾ {preset.name}", ephemeral=True)
        p = self.players.get(i.guild.id)
        if p:
            await p.restart_in_place()

    @tune.command(name="eq_basic", description=f"ปรับเสียงง่าย ๆ: เบส/เสียงร้อง/แหลม (±{fx.BASIC_MAX} dB)")
    async def t_eq_basic(self, i: discord.Interaction,
                          bass: app_commands.Range[int, -fx.BASIC_MAX, fx.BASIC_MAX] | None = None,
                          vocal: app_commands.Range[int, -fx.BASIC_MAX, fx.BASIC_MAX] | None = None,
                          treble: app_commands.Range[int, -fx.BASIC_MAX, fx.BASIC_MAX] | None = None):
        g = self.store.get(i.guild.id)
        if bass is not None:
            g["eq"]["bass"] = bass
        if vocal is not None:
            g["eq"]["vocal"] = vocal
        if treble is not None:
            g["eq"]["treble"] = treble
        self.store.save()
        e = g["eq"]
        await i.response.send_message(f"🎚 bass {e['bass']:+d} / vocal {e['vocal']:+d} / treble {e['treble']:+d}", ephemeral=True)
        p = self.players.get(i.guild.id)
        if p:
            await p.restart_in_place()

    @tune.command(name="eq_pro", description=f"EQ 11 แบนด์ละเอียด (Pro, ±{fx.PRO_MAX} dB)")
    @app_commands.choices(band=[app_commands.Choice(name=lbl, value=k) for k, lbl in fx.BANDS])
    async def t_eq_pro(self, i: discord.Interaction, band: app_commands.Choice[str], gain: app_commands.Range[int, -fx.PRO_MAX, fx.PRO_MAX]):
        g = self.store.get(i.guild.id)
        g["eq_pro"][band.value] = gain
        self.store.save()
        await i.response.send_message(f"🎛️ {band.name} → {gain:+d} dB", ephemeral=True)
        p = self.players.get(i.guild.id)
        if p:
            await p.restart_in_place()

    @tune.command(name="preamp", description="เกนโดยรวมก่อนเข้า EQ (-20 ถึง +20 dB)")
    async def t_preamp(self, i: discord.Interaction, gain: app_commands.Range[int, -20, 20]):
        g = self.store.get(i.guild.id)
        g["preamp"] = gain
        self.store.save()
        await i.response.send_message(f"🎚️ preamp {gain:+d} dB", ephemeral=True)
        p = self.players.get(i.guild.id)
        if p:
            await p.restart_in_place()

    @tune.command(name="eq_advanced", description="⚠ Advanced: compressor/reverb/stereo width/normalize — อาจทำให้เสียงเพี้ยนได้")
    async def t_eq_advanced(self, i: discord.Interaction,
                             compressor: bool | None = None,
                             reverb: app_commands.Range[int, 0, 100] | None = None,
                             width: app_commands.Range[int, 0, 200] | None = None,
                             normalize: bool | None = None):
        g = self.store.get(i.guild.id)
        if compressor is not None:
            g["adv"]["compressor"] = compressor
        if reverb is not None:
            g["adv"]["reverb"] = reverb
        if width is not None:
            g["adv"]["width"] = width
        if normalize is not None:
            g["adv"]["normalize"] = normalize
        self.store.save()
        a = g["adv"]
        await i.response.send_message(
            "🧪 **Advanced mode** — สำหรับผู้ใช้ที่เข้าใจการประมวลผลเสียง การตั้งค่าบางอย่างอาจทำให้เสียงผิดเพี้ยนหรือ clip ได้\n"
            f"compressor `{a['compressor']}` · reverb `{a['reverb']}` · width `{a['width']}` · normalize `{a['normalize']}`",
            ephemeral=True,
        )
        p = self.players.get(i.guild.id)
        if p:
            await p.restart_in_place()

    @tune.command(name="eq", description="เปิดแผงปรับ EQ แบบอินเทอร์แอคทีฟ (Basic/Pro/Advanced)")
    async def t_eq(self, i: discord.Interaction):
        view = EQPanel(self, i.guild.id)
        await i.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    @tune.command(name="eq_show", description="ดูค่าปรับเสียงทั้งหมดตอนนี้")
    async def t_eq_show(self, i: discord.Interaction):
        g = self.store.get(i.guild.id)
        e = g["eq"]
        bands = ", ".join(f"{lbl} {g['eq_pro'][k]:+d}" for k, lbl in fx.BANDS if g["eq_pro"].get(k)) or "—"
        a = g["adv"]
        desc = (f"**Basic** — bass {e['bass']:+d} · vocal {e['vocal']:+d} · treble {e['treble']:+d}\n"
                f"**Pro** — preamp {g['preamp']:+d} dB · bands: {bands}\n"
                f"**Advanced** — compressor {a['compressor']} · reverb {a['reverb']} · width {a['width']} · normalize {a['normalize']}\n"
                f"**Volume** — {g['volume']}%")
        await i.response.send_message(embed=discord.Embed(color=theme.color(g), title="🎧 EQ / audio settings", description=desc), ephemeral=True)

    @tune.command(name="eq_reset", description="รีเซ็ตการปรับเสียงทั้งหมด")
    async def t_eq_reset(self, i: discord.Interaction):
        g = self.store.get(i.guild.id)
        g["eq"] = dict(fx.EQ_DEFAULTS)
        g["eq_pro"] = {}
        g["preamp"] = 0
        g["adv"] = dict(fx.ADV_DEFAULTS)
        self.store.save()
        await i.response.send_message("↺ eq reset", ephemeral=True)
        p = self.players.get(i.guild.id)
        if p:
            await p.restart_in_place()

    @tune.command(name="eq_export", description="ส่งออกค่าปรับเสียงเป็นโค้ด แชร์ให้เซิร์ฟเวอร์อื่นได้")
    async def t_eq_export(self, i: discord.Interaction):
        g = self.store.get(i.guild.id)
        payload = {"eq": g["eq"], "eq_pro": g["eq_pro"], "preamp": g["preamp"], "adv": g["adv"]}
        code = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
        await i.response.send_message(f"💾 โค้ดพรีเซ็ตของคุณ (ใช้กับ `/tune eq_import`):\n```\n{code}\n```", ephemeral=True)

    @tune.command(name="eq_import", description="นำเข้าโค้ดพรีเซ็ตเสียงจาก /tune eq_export")
    async def t_eq_import(self, i: discord.Interaction, code: str):
        try:
            payload = json.loads(base64.urlsafe_b64decode(code.encode()).decode())
            g = self.store.get(i.guild.id)
            g["eq"] = {**fx.EQ_DEFAULTS, **{k: v for k, v in payload.get("eq", {}).items() if k in fx.EQ_DEFAULTS}}
            g["eq_pro"] = {k: v for k, v in payload.get("eq_pro", {}).items() if k in dict(fx.BANDS)}
            g["preamp"] = max(-20, min(20, int(payload.get("preamp", 0))))
            adv = payload.get("adv", {})
            g["adv"] = {**fx.ADV_DEFAULTS, "compressor": bool(adv.get("compressor")), "reverb": max(0, min(100, int(adv.get("reverb", 0)))),
                       "width": max(0, min(200, int(adv.get("width", 0)))), "normalize": bool(adv.get("normalize"))}
        except Exception:
            return await i.response.send_message("▒ โค้ดนี้ใช้ไม่ได้", ephemeral=True)
        self.store.save()
        await i.response.send_message("✅ นำเข้าพรีเซ็ตแล้ว", ephemeral=True)
        p = self.players.get(i.guild.id)
        if p:
            await p.restart_in_place()

    @tune.command(name="bg", description="พื้นหลังเคลื่อนไหวบนการ์ด Now Playing (อัปเดตทุก ~5 วิ)")
    @app_commands.choices(name=[app_commands.Choice(name=k, value=k) for k in theme.BG_THEMES])
    async def t_bg(self, i: discord.Interaction, name: app_commands.Choice[str]):
        g = self.store.get(i.guild.id)
        g["bg"] = name.value
        self.store.save()
        await i.response.send_message(f"🖼️ now playing bg → **{name.value}**", ephemeral=True)
        p = self.players.get(i.guild.id)
        if p:
            await p.refresh_panel()

    @tune.command(name="rescan", description="สแกนโฟลเดอร์เพลงใหม่")
    async def t_rescan(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await i.followup.send(f"📻 {await self.rescan()} tracks")

    @tune.command(name="about", description="ข้อมูลระบบ / เวอร์ชัน FFmpeg")
    async def t_about(self, i: discord.Interaction):
        line = getattr(self.bot, "ffmpeg_line", "unknown")
        await i.response.send_message(f"```\n{line}\nlibrary: {len(self.library)} tracks\n```", ephemeral=True)

    @tune.command(name="profile", description="สรุปการตั้งค่าทั้งหมดของเซิร์ฟเวอร์นี้")
    async def t_profile(self, i: discord.Interaction):
        await i.response.send_message(embed=theme.profile_embed(self.store.get(i.guild.id), i.guild.name))

    # ---- card designer ----------------------------------------------------
    card = app_commands.Group(
        name="card", parent=tune, description="ออกแบบหน้าตา Now Playing card",
    )

    async def _card_preview(self, i: discord.Interaction, note: str):
        g = self.store.get(i.guild.id)
        self.store.save()
        embed = theme.now_playing(g, "ตัวอย่างเพลง (Preview)", "Eluga Radio", 62, 214, False, 3, i.user.display_name, fx.label(g))
        await i.response.send_message(f"{note}\n▾ ตัวอย่าง:", embed=embed, ephemeral=True)
        p = self.players.get(i.guild.id)
        if p:
            await p.refresh_panel()

    @card.command(name="layout", description="เลือกโครงหน้าตาการ์ดเพลง")
    @app_commands.choices(name=[app_commands.Choice(name=f"{k} — {v['desc']}", value=k) for k, v in theme.LAYOUTS.items()])
    async def c_layout(self, i: discord.Interaction, name: app_commands.Choice[str]):
        g = self.store.get(i.guild.id)
        g.setdefault("card", dict(theme.CARD_DEFAULTS))["layout"] = name.value
        await self._card_preview(i, f"🖼️ layout → **{name.value}**")

    @card.command(name="barstyle", description="เลือกสไตล์แถบเวลาเพลง")
    @app_commands.choices(name=[app_commands.Choice(name=k, value=k) for k in theme.BARS])
    async def c_bar(self, i: discord.Interaction, name: app_commands.Choice[str]):
        g = self.store.get(i.guild.id)
        g.setdefault("card", dict(theme.CARD_DEFAULTS))["bar"] = name.value
        await self._card_preview(i, f"▬ progress bar → **{name.value}**")

    @card.command(name="footer", description="แสดงบรรทัดธีมท้ายการ์ดหรือไม่")
    async def c_footer(self, i: discord.Interaction, show: bool):
        g = self.store.get(i.guild.id)
        g.setdefault("card", dict(theme.CARD_DEFAULTS))["show_footer_line"] = show
        await self._card_preview(i, f"📝 footer line → **{'on' if show else 'off'}**")

    @card.command(name="reset", description="รีเซ็ตการ์ดกลับค่าเริ่มต้น (classic)")
    async def c_reset(self, i: discord.Interaction):
        g = self.store.get(i.guild.id)
        g["card"] = dict(theme.CARD_DEFAULTS)
        await self._card_preview(i, "↺ reset to classic")

    # ---- housekeeping ---------------------------------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.id == self.bot.user.id and after.channel is None:
            p = self.players.pop(member.guild.id, None)
            if p:
                p._gen += 1
                p._cancel_idle()
            return
        vc = member.guild.voice_client
        p = self.players.get(member.guild.id)
        if not vc or not p or member.bot or before.channel != vc.channel or after.channel == vc.channel:
            return
        if not any(not m.bot for m in vc.channel.members):
            await asyncio.sleep(60)
            vc = member.guild.voice_client
            if vc and not any(not m.bot for m in vc.channel.members):
                await p.destroy()


async def setup(bot: commands.Bot):
    await bot.add_cog(Radio(bot))
