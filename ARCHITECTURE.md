# Architecture

## Overview

One Python process runs everything. `bot.py` starts a `discord.py` client, and
its `setup_hook` loads the cogs and then starts an `aiohttp` web server **on the
same event loop**. There is no separate API process, no message broker, and no
database — the bot object itself is the shared state that both Discord commands
and HTTP requests mutate.

```
                             ┌── MusicEngine ──▶ GuildPlayer ──▶ FFmpeg ──▶ voice
Discord  ──▶ cogs/*.py ──────┤        ├──▶ music/sources/*  (resolve queries)
                             │        └──▶ music/*_store    (JSON on disk)
Browser  ──▶ web/server.py ──┤
   ▲                         └── GamesService ─▶ dashboard-power ──▶ palworld
   └── frontend/ (React SPA)
```

The domain objects — `MusicEngine` (`music/engine.py`) and `GamesService`
(`services/games.py`) — are the centre of the system. Discord and HTTP are both
*adapters* over them: neither owns the other's state, and adding a third
interface means writing a third adapter, not modifying the first two.

Both are constructed in `MusicBot.__init__`, above either interface, and handed
to whoever needs them (`bot.engine`, `bot.games`).

The consequence worth internalizing: **anything that blocks the event loop
breaks both Discord and the web UI at once.** That is the root of the biggest
bug this project has had (see "Event loop discipline" below).

## Layers

### `bot.py` — process entry
Builds the client, loads four cogs (`music`, `llm`, `playlist`, `games`), starts
the web server, and runs `_event_loop_monitor` — a 1-second heartbeat that logs
a warning whenever loop drift exceeds 1s. That monitor exists specifically to
make stalls visible, and it is how the deno bug below was diagnosed.

Slash commands sync to `GUILD_ID` when set (instant) and globally otherwise
(up to an hour to propagate).

### `config.py` — configuration
Every environment variable is read once at import into a module-level constant.
`BOT_TOKEN` and the Spotify credentials are required; missing ones call
`sys.exit()` at import time. That is deliberate for a container entrypoint —
fail loudly at boot rather than at first `/play` — but it means importing
`config` in a test requires pre-seeded env (`tests/conftest.py` does this).

### `music/engine.py` — the engine

`MusicEngine` owns everything that is not specific to one interface: the
per-guild `GuildPlayer` registry, the playlist and play-history stores, the
search provider, and all query resolution (`resolve_track`, `resolve_tracks`,
`resolve_playlist_entry`). It is constructed in `MusicBot.__init__`, above
either interface, and handed to both.

It can be constructed and driven with no Discord runtime at all — that is what
`tests/music/test_engine.py` does, and it is the practical check that nothing
interface-shaped has leaked back in.

Interfaces subscribe to playback events with `add_state_listener`. That is how
the Discord cog updates the bot's presence without the engine knowing what a
"presence" is. A listener that raises is logged and skipped, so one broken
interface cannot stall playback or starve the others.

Everything interface-shaped stays out: replying to an interaction, formatting an
embed, writing an HTTP response. The rule of thumb is that if it would be
meaningless without Discord, it belongs in a cog.

### `services/` — non-music domains

**`games.py`** — `GamesService`: the dashboard-power client, Palworld's REST
queries, the access allowlist, and the in-flight start guard. `GamesCog` keeps
only what needs a Discord identity (permission checks) and message formatting.

The start guard belongs here rather than on an interface: a start takes ~90s of
polling, and putting the flag on the cog only serialized Discord callers. On the
service, a start from any interface blocks the others for the whole boot window.

`ensure_started()` exists for callers that cannot sit on a 90s poll — it posts
the start, hands the guard to a background watcher, and returns immediately so
the HTTP client can poll `/api/games/status`. The Discord command drives the
steps itself so it can narrate progress inline.

**`llm.py`** — `LlmService`: the LLM router client, SSL policy, and response
normalising. The router fronts several backends that disagree about response
shape (`message.content` vs `choices[0].message.content`, `backends` vs
`nodes`), so reconciling them is service work rather than something each
interface re-guesses.

Truncation deliberately stays in the cog: 1800 characters is Discord's message
limit, a property of the transport rather than of the model's answer. Another
interface may have room for the whole thing.

### `music/` — playback core

**`track.py`** — `Track` is a plain dataclass. Its one piece of logic is
`needs_resolution`, which is just `stream_url is None`. That property drives all
lazy resolution.

**`player.py`** — `GuildPlayer`, one instance per guild, owns the queue
(`deque[Track]`), history (`deque`, `maxlen=50`), the voice client, and playback
progress. All queue mutation runs under a single `asyncio.Lock`; the internal
`_play_next_locked` is named for the invariant that the caller already holds it.

**`sources/resolver.py`** — `detect_source()` maps a query string to a
`SourceType` using ordered regexes. Order matters: an `http(s)` URL is a remote
stream even when it ends in `.mp3`, so the extension check for local files runs
only after the URL check fails.

**`sources/{youtube,spotify,apple_music,local}.py`** — each turns a URL into
`Track`s. Spotify and Apple Music are *metadata-only*: they yield title/artist
strings, which are then searched on YouTube for an actual stream. Only YouTube
and local files produce a playable URL directly.

**`play_history.py`** — append-only JSONL, one file per guild under
`DATA_DIR/history/<guild_id>.jsonl`. Append-only keeps writes atomic-ish and
recovery trivial (a torn tail line is one lost row). A per-guild
`threading.Lock` serializes writes.

**`playlist_store.py`** — JSON files per guild. Playlist names are slugged
through `_safe()` before touching the filesystem.

### `web/` — HTTP API

**`server.py`** — aiohttp routes under `/api`, two middlewares (error → JSON,
then auth). Handlers call `bot.engine`, the same object the Discord cogs use, so
the web UI and Discord commands cannot drift out of sync. It contains no
`get_cog` call and imports nothing from `cogs/` — the HTTP adapter and the
Discord adapter are siblings, not host and guest.

**`auth.py`** — stateless. Both login routes end at the same HMAC-SHA256 bearer
token; the signing secret is `WEB_API_KEY` if set, else the password. No session
store and no external dependency, which also means rotating `WEB_API_KEY`
invalidates every issued token.

*Discord OAuth* carries identity: the token's claims are the Discord user id,
display name, avatar, and an `admin` flag. *Password login* proves only
knowledge of a shared secret, so its tokens carry no claims and actions fall
back to being attributed to `"web"`.

Three decisions worth keeping:

- **Membership is the real gate.** OAuth authenticates; it does not authorize.
  Anyone on Discord can complete the consent screen, so the callback re-checks
  with the bot's own credentials that the user is in a guild it serves. It uses
  `fetch_member` rather than `get_member` because the bot runs without the
  privileged members intent, so its member cache is incomplete and `get_member`
  would deny real members at random.
- **Only the `identify` scope.** Membership is checked bot-side, so logging in
  never hands us the list of every server the user is in.
- **The token comes back in a URL fragment**, not a query parameter, so it does
  not land in access logs or `Referer` headers. The SPA reads it and clears the
  hash immediately.

CSRF state is signed rather than stored, keeping the callback stateless: a
state the server did not sign cannot be forged without the secret, and it
expires after ten minutes.

### `frontend/` — React + Vite + Tailwind SPA
Served by nginx in its own container. Same-origin with the API behind Traefik,
so it uses relative `/api/*` paths and needs no build-time API URL.

## Key flows

### Playing a track
1. `/play` (or `POST /api/guilds/{gid}/queue`) → `detect_source()`.
2. The matching source module produces one or more `Track`s. For search queries
   and Spotify/Apple metadata, `stream_url` is left `None`.
3. `GuildPlayer.add_and_play()` appends under the lock. If nothing is playing it
   calls `_play_next_locked()`; otherwise it fires a background `_prefetch()`.
4. `_play_next_locked()` resolves `stream_url` if needed (bounded by
   `STREAM_RESOLVE_TIMEOUT`), spawns `FFmpegOpusAudio` in an executor, and calls
   `voice_client.play()` with an `after` callback.
5. On success it records history and prefetches the next unresolved track, so
   resolution latency overlaps with playback instead of stacking up.

A failed resolution logs, messages the channel, and `continue`s to the next
track rather than aborting the queue.

### Seeking
FFmpeg cannot seek an already-running source, so `seek()` tears the source down
and respawns it with `-ss <seconds>`. Stopping playback fires the `after`
callback, which would normally advance the queue — so a `_seeking` flag tells
`_on_track_end` to return early. `_play_started_at` is then backdated by the
seek offset to keep `elapsed_seconds()` honest.

### Authentication
`_auth_middleware` short-circuits `/api/health` and `/api/login`, then tries
`X-API-Key`, then a bearer token. With neither `WEB_PASSWORD` nor `WEB_API_KEY`
configured the API is fully open — acceptable on a LAN, not on the internet.

## Key decisions

**Discord is an interface, not the system.** Originally `MusicCog` owned the
player registry and the stores, so the web API had to reach playback state
through `bot.get_cog("MusicCog")` and call its private `_get_player` and
`_resolve_tracks`. Discord was the root of the process and the dashboard was a
guest in its house; `PlaylistCog` was even handed two of `MusicCog`'s private
methods at wiring time, and silently degraded to an empty registry if the cog
was missing. Extracting `MusicEngine` inverted that. The practical payoff: a
playlist started from the web now records play history and emits state events
identically to one started from `/play`, because there is one construction path
instead of two that had drifted.

**Event loop discipline.** YouTube's `nsig` signature deciphering is CPU-bound
pure Python. Running it in a thread does *not* help, because it holds the GIL and
starves the loop for seconds — long enough for a fresh `/play` interaction to
expire before it could be acknowledged. The fix was to give yt-dlp an external JS
runtime: the Dockerfile installs **deno**, so signature solving runs in a
subprocess, off the GIL. `XDG_CACHE_HOME=/data/.cache` persists the downloaded
EJS solver across restarts. Full details in [CLAUDE.md](CLAUDE.md).

The general rule: any CPU-bound or blocking work must go to a subprocess, not a
thread. I/O-bound work (`FFmpegOpusAudio` construction, `yt-dlp` metadata) is
fine in `run_in_executor`.

**`yt-dlp` is intentionally unpinned** in `requirements.txt`. YouTube breaks
extractors regularly; a stale pin is a guaranteed outage. A rebuild refreshes it.

**The bot never gets `docker.sock`.** It executes YouTube and LLM input, so
handing it the socket would hand it the host. `cogs/games.py` instead calls an
allowlist-gated `dashboard-power` sidecar over an internal-only Docker network.
Palworld's roster is read from Palworld's own REST API directly, so the power
sidecar stays a start/stop-only surface.

**Lazy stream resolution.** Resolving every track at queue time would make
importing a 200-track playlist take minutes and burn rate limit on tracks nobody
reaches. Resolving exactly one track ahead keeps import instant and playback gap-
free. `add_many_and_play` deliberately prefetches only one track so a bulk import
doesn't fan out into hundreds of parallel yt-dlp jobs.

**No database.** Play history is JSONL and playlists are JSON, both under
`DATA_DIR`. At this scale (one homelab guild) a database would add an operational
dependency and a migration story for no benefit, and flat files are trivially
backed up and inspected.

## Testing

`pytest` with `asyncio_mode = "auto"`, so async tests need no decorator.
`tests/conftest.py` does one job: seed the required env vars before anything
imports `config`, which would otherwise `sys.exit()`. Individual tests supply
their own fake voice clients and interactions. The frontend uses vitest +
Testing Library with jsdom.
