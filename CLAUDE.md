# discordbot — Notes for Claude

## Known bugs

### /play interaction expired before I could acknowledge it

**Symptom:** Discord shows "The application did not respond", followed by the bot sending a channel message: `` `/play` interaction expired before I could acknowledge it. Please retry. ``

**Symptom (cont.):** Logs show `Event loop stall detected: X.XXXs` (monitor in `bot.py:_event_loop_monitor`, logs stalls >1s) in 2–6s bursts around `/play`.

**Root cause (confirmed 2026-06-21):** NOT FFmpeg — `discord.FFmpegOpusAudio(...)` and yt-dlp `extract_info` were already offloaded to `run_in_executor`, and `ffmpeg_spawn` measures 0–34ms. The real culprit was YouTube **`nsig`/signature deciphering**: yt-dlp ran it as pure-Python in a worker thread, but that work is CPU-bound and **holds the GIL**, starving the asyncio event loop for seconds even though it's "in a thread". With no external JS runtime present, every signature solve blocked the loop → fresh `/play` interactions went stale (>2800ms) before `_defer_quick` could `defer()`.

**Fixed 2026-06-21:**
1. `Dockerfile` installs **deno**; yt-dlp auto-detects it and runs signature solving in a deno **subprocess** (off the GIL).
2. yt-dlp ≥ 2026.06 needs the EJS challenge-solver script ("remote component"), which is skipped by default → "Signature solving failed". Enabled via `"remote_components": ["ejs:github"]` in the `_YDL_*` opts dicts (`music/sources/youtube.py`, `music/search/youtube_search.py`). yt-dlp downloads + caches the solver from the official `yt-dlp/ejs` github release and runs it in deno's sandbox.
3. `XDG_CACHE_HOME=/data/.cache` (compose) persists that solver + sigfuncs cache across restarts so it isn't re-fetched from github each boot.

`requirements.txt` leaves `yt-dlp` unpinned deliberately (must stay current vs YouTube breakage) — a rebuild also refreshes it.
