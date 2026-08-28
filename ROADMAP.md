# discordbot — roadmap

Future features and known follow-ups. Items here are not on a fixed
schedule — they're the picks for "what's next" when the next session starts.

## Music recommendations / personalization

Track what each user (and the server overall) plays, then surface a
**Recommended** / **New for you** panel based on that history.

**Why:** the bot already streams everything; we have the raw signal
(track titles, artists, requesters, timestamps). Right now we throw it
away after `GuildPlayer.history` rotates out.

**Sketch of how it could work:**
1. **Persist play history**: every time `_play_next_locked` actually
   starts a track, write a row to a new JSON/SQLite file under
   `DATA_DIR/history/<guild_id>.jsonl` with
   `{ts, requester, title, url, source, duration}`. Keep it append-only
   (small files, simple recovery).
2. **Per-user listening profiles**: aggregate by `requester` (Discord
   display name today; would become a Discord user id if/when we add
   real OAuth). Surface most-played tracks/artists, recent plays, and
   "haven't heard this in a while" cues.
3. **Recommendation source**:
   - Easy path: feed the top-played track titles to the **Spotify Web
     API's** `recommendations` endpoint (`/v1/recommendations` takes up
     to 5 seed track ids and returns similar tracks). Credentials already
     in place via `SPOTIFY_CLIENT_ID`/`SECRET`.
   - Alt path: YouTube's "related videos" via yt-dlp.
   - Hybrid: build seeds from history → ask Spotify → resolve each via
     existing YouTube search path → ready to queue.
4. **UI**: a new sidebar entry **Discover** with two sections:
   - *Recommended for you* — based on the current Discord user's plays.
   - *New for the server* — based on aggregated server plays minus what
     anyone here has already played recently.
   - Each result clickable to queue.
5. **Privacy / scope**: per-guild only by default; never leave the
   guild's `DATA_DIR`. No telemetry off-host.

**Open design choices**:
- User identity: stays display-name today (good enough for a single
  server); becomes Discord OAuth uid if/when we go that direction.
- Cold start (new user with no history): fall back to server-wide
  popular tracks.
- Storage cap: rotate per-guild jsonl after N rows or M months to keep
  files small.

---

## Homelab assistant — generalizing the interface split

The goal: every domain the bot can act on is reachable from every interface,
rather than being locked inside whichever cog happens to implement it.

**Done**

- `MusicEngine` — music (see below).
- `GamesService` — game-server control. Palworld status, start, roster and join
  details are now on the web dashboard as well as Discord.
- `LlmService` — LLM router client. No Discord-locked domains remain; every cog
  is now a thin adapter over a service.

**Next**

- **Expose the LLM over HTTP.** The service is ready, but there are no
  `/api/llm/*` routes and no web UI for it. Deliberately deferred: the agentic
  direction needs the service callable *in-process* (as tools), which it now
  is, and a chat UI is a separate build. Worth doing if you want to talk to the
  router from the dashboard.
- ~~**Per-user identity (Discord OAuth).**~~ **Done.** The dashboard offers
  "Continue with Discord"; sessions carry the Discord user id, display name,
  avatar, and an `admin` flag, and web-queued tracks are attributed to the
  person who queued them. Follow-ons:
  - **Gate destructive actions on `admin`.** The claim exists but nothing uses
    it yet. `POST /api/games/start` is the obvious first candidate — the
    Discord side has a per-user allowlist that the web side still lacks.
  - **Record the Discord user id in play history**, not just the display name,
    so attribution survives a rename. Needed before recommendations.
  - **Retire `WEB_PASSWORD`** once OAuth is confirmed working in deployment.
- **A service registry.** Three services attached individually to the bot is
  still fine; a registry earns its place when the LLM needs to enumerate what
  it can act on.

---

## Engine / interface split

**Done.** `MusicEngine` (`music/engine.py`) owns playback state, the stores,
search, and all query resolution. `web/server.py` contains no `get_cog` call and
imports nothing from `cogs/`; the two adapters are siblings. Engine behaviour is
covered directly by `tests/music/test_engine.py`, which drives the engine with
no Discord runtime.

Possible follow-ons, none of them blocking:

- **An ops CLI.** Worth noting the original framing here was wrong: a separate
  process cannot share the running bot's in-memory engine, so a CLI would be a
  *client of the HTTP API*, not a third adapter over the engine. Still useful
  for `now-playing` / `skip` / `queue` from a terminal — just not architectural
  proof. The engine tests already serve that purpose.
- **Per-interface identity.** The engine records `requester` as a display name
  ("web" for API calls). Real identity needs Discord OAuth (below), after which
  the engine could attribute plays per user regardless of interface.

---

## Other open candidates

These came up in earlier "what's next" rounds but haven't been built.

- **Admin page stream stuttering.** Investigate playback/stream stability
  on the admin page. Capture whether the stutter is browser-only, HLS/audio
  pipeline related, network buffering, ffmpeg CPU pressure, or caused by
  UI polling/render work.
- **Discord OAuth login** to replace the single shared password. Gives
  per-user identity, which would unlock the recommendation feature
  above.
- **WebSocket push** for now-playing updates instead of the current 3s
  polling.
- **Real audio visualizer.** Current visualizer is decorative; a real
  one needs to stream the bot's audio into the browser (HLS or
  WebSocket). Significant work.

---

## Frontend audit / QoL backlog

Audit date: 2026-06-22. Current frontend is React + Vite + Tailwind CSS
with pages for Dashboard, Library, Playlist detail, Local files, History,
and shared password auth.

### P0 / quick wins

- **Fix stale roadmap/test drift.** History and scrub/seek are now built.
  Keep tests and docs aligned whenever frontend capabilities move from
  "future" to "shipped".
- **Mobile pass for dense rows.** Queue, playlist tracks, local files, and
  history rows still rely on horizontal space for badges/buttons. Verify on
  a phone viewport and stack or collapse metadata/actions where needed.

### Recently shipped

- **Actionable empty states.** Empty queue, empty playlist, and no local
  files now name the next action and link to it. (History already did.)
- **In-app delete confirmation.** Playlist delete is a two-step in-app
  confirm (autofocused *Confirm delete* + *Cancel*, Escape cancels) rather
  than native `window.confirm()`.
- **Search errors are visible.** `SearchBox` now renders three distinct states
  — results, "no results for X", and an inline error carrying the API's own
  message — instead of silently closing the dropdown on failure. Also guards
  against a slow earlier request overwriting a newer one's results.
- **Project documentation.** README, ARCHITECTURE, and HOWTO now exist
  alongside ROADMAP.
- **Lint in CI.** ruff (Python) and eslint (frontend) both run in CI; the
  `npm run lint` script had been broken since it was added.
- **Prevent duplicate seek commits.** Dashboard seek now uses an in-flight
  guard so `pointerup`, `keyup`, and `blur` cannot post duplicate seeks for
  the same interaction.
- **Show seek-in-progress state.** While `/seek` is pending, the scrubber is
  disabled and shows a subtle `Seeking...` state.

### P1 / user workflow improvements

- **Add toast/status system.** Each page owns its own `msg`/`error` state.
  A shared toast/status component would make success and failure feedback
  consistent across queue, upload, import, seek, and playlist operations.
- **Add optimistic queue updates.** Queue moves/removals currently wait for
  a poll/refresh. Update the local queue immediately and roll back on
  error, matching the scrubber's optimistic behavior.
- **Drag-and-drop queue reordering.** Up/down buttons work, but long queues
  are tedious. Add keyboard-safe drag handles or a reorder mode for the
  Dashboard queue and Playlist detail tracks.
- **Save preferred voice channel per guild.** The selected channel resets
  to the first available channel. Persist the last used channel in
  `localStorage` keyed by guild id.
- **Batch local-file actions.** Local files can only be queued one at a
  time. Add multi-select, "queue folder", and "queue selected" actions.
- **Local file search/filter.** The Local page builds a tree but has no
  filter. Add client-side filtering by filename/path, with folder matches
  auto-expanded.
- **History filters.** Add text/source/requester filters, pagination, and
  "queue selected from history" for rebuilding previous sessions.
- **Playlist import preview.** Before importing or replacing a playlist,
  show the detected playlist title/count when available and clearly show
  whether the operation will append or replace.
- **Playlist rename/duplicate.** Users can create/delete playlists, but
  cannot rename or clone one from the web UI.
- **Add "save current queue as playlist".** Useful bridge between Dashboard
  and Library: take current + up-next queue and save it as a named playlist.

### P2 / architecture and polish

- **Extract shared UI primitives.** Button/input/card/badge class strings
  are copied across pages. Introduce small local components or constants so
  states and spacing stay consistent.
- **Centralize async command handling.** Pages repeat `busy`, `msg`,
  `error`, `try/catch/finally` patterns. A small `useCommand` hook could
  standardize loading, success, error, and rollback behavior.
- **Use richer iconography.** The UI currently uses text symbols and emoji.
  Add an icon library such as `lucide-react` for predictable sizing,
  accessibility labels, and visual consistency.
- **WebSocket/SSE state updates.** Replace 3s Dashboard polling and 5s
  guild status polling with push updates for now-playing, queue, voice
  channel, and history changes.
- **Route-level loading and error boundaries.** Add page skeletons and
  error boundaries so a failed API call or thrown render error does not
  collapse the whole app experience.
- **Keyboard shortcut layer.** Add optional shortcuts for play/pause,
  skip, focus search, seek +/- 5s, and queue navigation, with shortcuts
  disabled while typing.
- **Accessibility audit.** Run axe/playwright checks and fix focus order,
  status announcements, slider semantics, listbox active descendant, and
  reduced-motion behavior.
- **Visual regression screenshots.** Add Playwright screenshots for desktop
  and mobile for Dashboard, Library, Playlist detail, Local, History, and
  Login to catch layout regressions before deploy.
- **Bundle/deploy cache busting check.** Confirm Traefik/nginx/browser cache
  behavior after deploys so users reliably receive the newest frontend
  assets without hard-refresh confusion.
## Make this usable by others (added 2026-08-27)

- [ ] Universalize the README / docs / code for outside users: document setup
  from scratch on generic infrastructure, replace homelab-specific assumptions
  (private hostnames, LAN addresses, personal paths and defaults) with
  env-driven configuration plus examples, and keep the public GitHub mirror
  directly runnable.
