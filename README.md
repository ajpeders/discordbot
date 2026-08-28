# discordbot

A self-hosted Discord music bot with a web dashboard. It streams audio into a
voice channel from YouTube, Spotify, Apple Music, SoundCloud, direct URLs, or a
local music library, and exposes the same controls over an HTTP API and a React
SPA.

Beyond music it carries two side cogs: an LLM chat passthrough (`/llm-*`) and
Palworld game-server controls (`/palworld *`). Both disable themselves when
their environment variables are unset.

## Quick start

Requires Python 3.12+, `ffmpeg` on PATH, and Node 22+ for the frontend.

```bash
# 1. Configure — BOT_TOKEN and Spotify credentials are mandatory.
cp .env.example .env && $EDITOR .env

# 2. Backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python bot.py

# 3. Frontend (separate shell)
cd frontend && npm install && npm run dev
```

The bot exits immediately with a clear message if `BOT_TOKEN`,
`SPOTIFY_CLIENT_ID`, or `SPOTIFY_CLIENT_SECRET` are missing.

For the deployed setup (Docker + Traefik), see [HOWTO.md](HOWTO.md).

## Key commands

| Command | What it does |
| --- | --- |
| `.venv/bin/python -m pytest -q` | Backend tests (105) |
| `cd frontend && npm test` | Frontend tests (vitest) |
| `cd frontend && npm run lint` | ESLint over the SPA |
| `cd frontend && npm run build` | Typecheck (`tsc -b`) + production bundle |
| `cd frontend && npm run dev` | Vite dev server |
| `docker compose up -d --build` | Build and run bot + web |

CI (`.forgejo/workflows/ci.yml`) runs all of the above on push to `main` and on
every pull request.

## Discord commands

**Music** — `/play`, `/skip`, `/pause`, `/resume`, `/prev`, `/queue`, `/gtfo`
(stop and leave voice).

**Playlists** — `/playlist` group: create, list, show, add, remove, play,
import, sync, delete.

**LLM** — `/llm-chat`, `/llm-generate`, `/llm-models`, `/llm-health`. Inert
unless `LLM_API_BASE_URL` is set.

**Palworld** — `/palworld` group: `status`, `players`, `connect`, `start`,
`allow`, `deny`, `allowed`. Inert unless `POWER_URL` is set.

## Web dashboard

Pages: Dashboard (now-playing, scrubber, queue), Library (playlists), Playlist
detail, Local files (browse + upload), History, and Games (Palworld status,
start, roster, and join details).

Sign in with **Discord** (per-user identity, so queued tracks are attributed to
whoever queued them) or with a shared password (`WEB_PASSWORD`). Only members of
a guild the bot is in can log in with Discord. Non-browser clients can use
`X-API-Key` against `WEB_API_KEY` instead. With none of the three configured the
API is open, which is only appropriate on a trusted LAN.

See [HOWTO.md](HOWTO.md) for the Discord OAuth setup steps.

## Configuration

All configuration is environment variables, read once in `config.py`. See
[.env.example](.env.example) for the deployment overrides and
[HOWTO.md](HOWTO.md) for what each group does.

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — system design, data flow, key decisions
- [ROADMAP.md](ROADMAP.md) — current status and planned work
- [HOWTO.md](HOWTO.md) — step-by-step guides for common tasks
- [CLAUDE.md](CLAUDE.md) — notes on known bugs and their root causes
