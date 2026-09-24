"""Online song library (MongoDB) — same idea as Anyaluga: name -> direct audio link (+ Discord message ref)."""
import os
import re
import time

try:
    from pymongo import AsyncMongoClient
except ImportError:  # pymongo missing → vault disabled
    AsyncMongoClient = None


class Vault:
    def __init__(self):
        uri = os.getenv("MONGODB_URI")
        self.enabled = bool(uri and AsyncMongoClient)
        if self.enabled:
            self.client = AsyncMongoClient(uri)
            db = self.client[os.getenv("MONGODB_DB", "eluga_radio")]
            self.songs = db["songs"]
            self.settings = db["guilds"]

    async def init(self):
        if self.enabled:
            await self.songs.create_index([("guild_id", 1), ("name_lower", 1)], unique=True)

    async def add(self, gid, name, url, by, msg_ref=None, duration=None):
        await self.songs.update_one(
            {"guild_id": gid, "name_lower": name.lower()},
            {"$set": {"name": name, "url": url, "by": by, "duration": duration,
                      "msg_ref": list(msg_ref) if msg_ref else None, "created": time.time()}},
            upsert=True,
        )

    async def get(self, gid, q):
        ql = q.lower()
        d = await self.songs.find_one({"guild_id": gid, "name_lower": ql})
        if d:
            return d
        cur = self.songs.find({"guild_id": gid, "name_lower": {"$regex": re.escape(ql)}}).limit(20)
        hits = [x async for x in cur]
        return min(hits, key=lambda x: len(x["name"])) if hits else None

    async def remove(self, gid, name):
        r = await self.songs.delete_one({"guild_id": gid, "name_lower": name.lower()})
        return r.deleted_count > 0

    async def names(self, gid, prefix="", limit=25):
        q = {"guild_id": gid}
        if prefix:
            q["name_lower"] = {"$regex": re.escape(prefix.lower())}
        cur = self.songs.find(q, {"name": 1}).sort("name_lower", 1).limit(limit)
        return [d["name"] async for d in cur]

    async def count(self, gid):
        return await self.songs.count_documents({"guild_id": gid})
