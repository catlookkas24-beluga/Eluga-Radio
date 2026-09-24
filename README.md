# Eluga Radio 📻🌲

Discord music bot — no YouTube. Plays your own/licensed files, direct audio URLs, Icecast/Shoutcast/HLS streams and uploaded audio through FFmpeg.

## Setup
1. Python 3.11+ → `pip install -r requirements.txt`
2. Install **FFmpeg 9.0.2** (or newer) and point `FFMPEG_PATH` at it. On start the bot logs the version and warns if it is older than 9.0.2. `/tune about` shows it too.
3. Copy `.env.example` → `.env`, fill `DISCORD_TOKEN`. Put audio in `music/`.
4. `python main.py`
5. Invite (scopes `bot applications.commands`, permissions 3196928 = Connect, Speak, Send Messages, Embed Links, Attach Files). No privileged intents needed.

## Commands
**Vault (like Anyaluga):** `/addsongfromvideo` `/addsong` `/removesong` `/songlist` — songs live in MongoDB (`MONGODB_URI`), nothing to store on disk.
`/play` `/playfile` `/radio` (endless shuffle of music/) `/station add|remove|play` `/pause` `/skip` `/stop` `/shuffle` `/loop` `/volume` `/queue` `/nowplaying`
Panel buttons: ⏮ ⏯ ⏭ 🔁 ⏹ + signal-filter dropdown.
Customize (Manage Server): `/tune theme|color|fx|eq|rescan|about`

## Make it yours
- `theme.py` — palettes, taglines, all Thai bot messages (`MSG`)
- `fx.py` — add ffmpeg `-af` chains to `PRESETS`; they appear in `/tune fx` and the panel dropdown automatically

## Notes
- YouTube links are blocked on purpose; only public http/https URLs are accepted (private/internal IPs rejected, ffmpeg protocols whitelisted).
- Keep discord.py updated — Discord changes its voice protocol from time to time.
- Only stream music you own or are licensed to use (royalty-free / CC / your own tracks).
