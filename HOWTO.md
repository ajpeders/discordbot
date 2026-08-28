# HOWTO

Step-by-step guides for common tasks. For system design see
[ARCHITECTURE.md](ARCHITECTURE.md).

## Run locally

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # then fill in BOT_TOKEN and Spotify credentials
.venv/bin/python bot.py
```

`ffmpeg` must be on PATH. Without it, tracks resolve but never play.

The frontend runs separately:

```bash
cd frontend
npm install
npm run dev               # http://localhost:5173, proxies /api to the bot
```

## Run the tests

```bash
.venv/bin/python -m pytest -q          # backend
cd frontend && npm test                 # frontend
cd frontend && npm run lint             # eslint
cd frontend && npm run build            # tsc -b + bundle
```

Run all four before pushing — CI runs exactly these.

If pytest reports `ModuleNotFoundError: No module named 'discord'`, you are
running system Python instead of the venv. Use `.venv/bin/python -m pytest`.

## Deploy

The deployed stack is two containers behind Traefik: `discordbot` (Python, port
8080, serves `/api`) and `discordbot-web` (nginx serving the built SPA). Both
attach to the external `web` network; `discordbot` additionally attaches to the
internal-only `dashboard-internal` network to reach the power sidecar.

```bash
docker compose up -d --build
docker compose logs -f discordbot
```

A rebuild also refreshes `yt-dlp`, which is unpinned on purpose. **When YouTube
playback breaks, rebuild first** — a stale extractor is the most common cause.

Two host paths are mounted: the music library at `/music` (read) and persistent
state at `/data` (playlists, play history, and the yt-dlp cache).

## Configure

All configuration is environment variables. `config.py` is the single source of
truth for names and defaults.

**Required** — the process exits at boot without these:
`BOT_TOKEN`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`.

**Core** — `GUILD_ID` (sync slash commands to one guild instantly instead of
waiting up to an hour for global propagation), `MUSIC_DIR`, `DATA_DIR`,
`IDLE_TIMEOUT` (seconds before leaving an idle voice channel).

**Web** — `WEB_PORT`, `WEB_PASSWORD` (shared browser password),
`WEB_API_KEY` (header key for non-browser clients).

> With no `WEB_PASSWORD`, no Discord OAuth, and no `WEB_API_KEY`, the API is
> **completely open**. Only do that on a trusted LAN.

## Set up Discord login

Gives the dashboard per-user identity instead of one shared password, so queued
tracks are attributed to the person who queued them.

1. Open <https://discord.com/developers/applications> and select the same
   application the bot's token comes from.
2. Under **OAuth2 → Redirects**, add your callback URL *exactly*:
   `https://<your-domain>/api/auth/discord/callback`. A trailing-slash or
   scheme mismatch makes Discord reject the exchange.
3. Copy the **Client ID** and **Client Secret**.
4. Set `DISCORDBOT_DISCORD_CLIENT_ID`, `DISCORDBOT_DISCORD_CLIENT_SECRET` and
   `DISCORDBOT_DISCORD_REDIRECT_URI` (see `.env.example`), then redeploy.
5. Load the dashboard — the login page now offers **Continue with Discord**.

**Only members of a guild the bot is in can log in.** Completing Discord's
consent screen proves someone owns a Discord account, which everyone on Discord
does; the bot re-checks membership with its own credentials before issuing a
session. Guild owners, anyone with Administrator, and `POWER_ADMINS` are marked
`admin` in their session.

`WEB_PASSWORD` keeps working alongside OAuth on purpose, so a misconfigured
redirect cannot lock you out of your own dashboard. **Once Discord login is
confirmed working, unset `WEB_PASSWORD`** to make identity mandatory.

Rotating `WEB_API_KEY` invalidates every issued session token — that is the
panic button if a token leaks.

**Optional cogs** — unset the lead variable to disable the cog entirely:
`LLM_API_BASE_URL` for `/llm-*`, `POWER_URL` for `/palworld *`.

Note that `.env.example` documents the `DISCORDBOT_*` compose-interpolation
overrides, which are a different layer from the variables the bot itself reads.

## Add a new music source

1. Add a `SourceType` member and its detection regex in
   `music/sources/resolver.py`. Order matters — put specific patterns before
   general ones, and remember the URL check runs before the file-extension check.
2. Create `music/sources/<name>.py` exposing a function that returns `Track`s.
   If the service only gives metadata (as Spotify and Apple Music do), leave
   `stream_url` as `None` and let the YouTube search path resolve it.
3. Wire the new `SourceType` into the dispatch in `cogs/music.py`.
4. Add tests under `tests/music/sources/`. Mock the network — no test should
   make a real request.

## Add a new API endpoint

1. Write an `async def handler(request)` in `web/server.py`.
2. Register it in the route table inside `create_app()`.
3. It is authenticated by default. Only add it to `_AUTH_EXEMPT` if it genuinely
   must be public.
4. Add a client function in `frontend/src/api/bot.ts`.
5. Test the handler in `tests/web/test_server.py`.

## Diagnose playback problems

**"The application did not respond" on `/play`** — check the logs for
`Event loop stall detected`. If present, something is blocking the event loop;
confirm `deno --version` works inside the container, since yt-dlp needs it to
keep signature solving off the GIL. See [CLAUDE.md](CLAUDE.md) for the full
history of this bug.

**A specific track fails** — look for `stream_resolve` and `ffmpeg_spawn` log
lines, which carry `elapsed_ms`. Slow resolution points at YouTube or the
network; slow ffmpeg spawn points at host CPU pressure.

**Nothing plays at all** — verify the bot actually joined voice, then confirm
`ffmpeg` is present in the container.

## Update the documentation

Treat docs as part of the commit. When behavior changes, update the affected
file in the same change:

- New command or setup step → `README.md`
- New module, flow, or design decision → `ARCHITECTURE.md`
- New operational procedure → this file
- Shipped or newly planned work → `ROADMAP.md`
- A bug whose root cause was non-obvious → `CLAUDE.md`
