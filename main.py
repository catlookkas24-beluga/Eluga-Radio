import logging
import os
import re
import subprocess

import discord
from aiohttp import web
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("eluga")
WANT = (9, 0, 2)


def check_ffmpeg(exe: str) -> str:
    try:
        line = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
    except (OSError, IndexError, subprocess.SubprocessError):
        raise SystemExit(f"FFmpeg not found at '{exe}'. Install FFmpeg 9.0.2 and set FFMPEG_PATH in .env")
    m = re.search(r"version n?(\d+)\.(\d+)(?:\.(\d+))?", line)
    if m and tuple(int(x or 0) for x in m.groups()) < WANT:
        log.warning("FFmpeg is older than %s: %s", ".".join(map(str, WANT)), line)
    else:
        log.info(line)
    return line


class Eluga(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned,
                         intents=discord.Intents(guilds=True, voice_states=True),
                         activity=discord.Activity(type=discord.ActivityType.listening, name="the static 📻🌲"))
        self.ffmpeg_line = check_ffmpeg(os.getenv("FFMPEG_PATH", "ffmpeg"))

    async def setup_hook(self):
        await self.load_extension("radio")
        # Render "web service" mode: answer HTTP on $PORT so the platform (and an uptime pinger) sees a live site
        port = os.getenv("PORT")
        if port:
            app = web.Application()
            app.router.add_get("/", lambda r: web.Response(text="📻🌲 on air"))
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", int(port)).start()
            log.info("keep-alive http on :%s", port)
        dev = os.getenv("DEV_GUILD_ID")
        if dev:
            g = discord.Object(int(dev))
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=g)
        else:
            await self.tree.sync()

    async def on_ready(self):
        log.info("on air as %s", self.user)


if __name__ == "__main__":
    Eluga().run(os.environ["DISCORD_TOKEN"], log_handler=None)
