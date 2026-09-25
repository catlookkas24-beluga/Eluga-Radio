"""Per-guild settings. JSON file by default; MongoDB when a collection is given (survives redeploys)."""
import asyncio
import json
import logging
import os
import pathlib

log = logging.getLogger("eluga")

DEFAULTS = {
    "theme": "pinewood",
    "custom_color": None,
    "fx": "off",
    "eq": {"bass": 0, "vocal": 0, "treble": 0},
    "eq_pro": {},
    "preamp": 0,
    "adv": {"compressor": False, "reverb": 0, "width": 0, "normalize": False},
    "bg": "none",
    "volume": 60,
    "loop": "off",
    "stations": {},
}
_NESTED = ("eq", "adv")  # dicts that need missing sub-keys backfilled too, not just the top-level key


class Store:
    def __init__(self, path=None, col=None):
        self.col = col
        self.data = {}
        self._tasks = set()
        if col is None:
            # DATA_DIR should point at a persistent disk in production (e.g. /data on Render)
            self.path = pathlib.Path(path or os.path.join(os.getenv("DATA_DIR", "data"), "guilds.json"))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                self.data = json.loads(self.path.read_text("utf-8"))

    async def load(self):
        if self.col is not None:
            async for d in self.col.find({}):
                self.data[d["_id"]] = d["settings"]

    def get(self, guild_id):
        g = self.data.setdefault(str(guild_id), {})
        for k, v in DEFAULTS.items():
            g.setdefault(k, json.loads(json.dumps(v)))
        for k in _NESTED:  # backfill sub-keys added by later versions into an already-saved dict
            for sk, sv in DEFAULTS[k].items():
                g[k].setdefault(sk, sv)
        return g

    def save(self):
        if self.col is None:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(self.path)
            return
        try:
            t = asyncio.get_running_loop().create_task(self._flush())
        except RuntimeError:
            return
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _flush(self):
        try:
            for gid, g in list(self.data.items()):
                await self.col.replace_one({"_id": gid}, {"_id": gid, "settings": g}, upsert=True)
        except Exception:
            log.exception("settings save failed")
