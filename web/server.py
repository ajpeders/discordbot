"""In-process HTTP API for the Discord bot.

Runs inside the bot's asyncio event loop (started from bot.setup_hook), so it
can read and control the live GuildPlayer objects and the shared PlaylistStore
directly. The React SPA (separate nginx container) is the only client; Traefik
routes /api/* here and everything else to the SPA.
"""
import asyncio
import logging
import os
import urllib.parse
from typing import Optional

import aiohttp
import discord
from aiohttp import web

import config
from web import auth
from music.track import Track
from music.playlist_store import PlaylistEntry, _safe
from music.sources.resolver import detect_source, SourceType

logger = logging.getLogger(__name__)

BOT_KEY = web.AppKey("bot", object)


# --- accessors -------------------------------------------------------------

def _engine(bot):
    """The music engine. Both this API and the Discord cogs are adapters over it."""
    return bot.engine


def _games(bot):
    """Game-server control, shared with the Discord cog."""
    return bot.games


def _track_dict(t: Track) -> dict:
    return {
        "title": t.title,
        "url": t.url,
        "source": t.source,
        "duration": t.duration,
        "requester": t.requester,
        "thumbnail": t.thumbnail,
    }


def _entry_dict(e: PlaylistEntry) -> dict:
    return {"title": e.title, "url": e.url, "source": e.source, "added_by": e.added_by}


def _auto_sync_source(url: str) -> Optional[str]:
    source_type = detect_source(url)
    if source_type == SourceType.SPOTIFY_PLAYLIST:
        return "spotify"
    if source_type == SourceType.APPLE_MUSIC_PLAYLIST:
        return "apple_music"
    return None


def _pick_text_channel(guild: discord.Guild) -> Optional[discord.abc.Messageable]:
    me = guild.me
    if guild.system_channel and guild.system_channel.permissions_for(me).send_messages:
        return guild.system_channel
    for ch in guild.text_channels:
        if ch.permissions_for(me).send_messages:
            return ch
    return None


# --- error helpers ---------------------------------------------------------

def _json_error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _require_guild(bot, request) -> discord.Guild:
    gid = int(request.match_info["gid"])
    guild = bot.get_guild(gid)
    if guild is None:
        raise web.HTTPNotFound(reason="Bot is not in that guild.")
    return guild


# --- middleware ------------------------------------------------------------

_AUTH_EXEMPT = {
    "/api/health",
    "/api/login",
    "/api/auth/config",
    "/api/auth/discord/login",
    "/api/auth/discord/callback",
}

# The authenticated caller's claims, or {} for API-key and open access.
USER_KEY = web.RequestKey("auth_user", dict)


def _user(request) -> dict:
    return request.get(USER_KEY) or {}


def _requester(request, fallback: str = "web") -> str:
    """Display name to attribute a queued track or playlist entry to.

    Falls back to "web" for API-key callers and password logins, which carry no
    identity.
    """
    return _user(request).get("name") or fallback


@web.middleware
async def _auth_middleware(request, handler):
    request[USER_KEY] = {}
    if request.path in _AUTH_EXEMPT:
        return await handler(request)
    # Non-browser clients: X-API-Key against WEB_API_KEY.
    if config.WEB_API_KEY and request.headers.get("X-API-Key") == config.WEB_API_KEY:
        return await handler(request)
    # Browser: Bearer token issued by /api/login or the Discord callback.
    if auth.auth_enabled():
        authz = request.headers.get("Authorization", "")
        token = authz[7:] if authz.startswith("Bearer ") else ""
        claims = auth.verify_token(token)
        if not claims:
            return _json_error(401, "Authentication required.")
        request[USER_KEY] = claims
        return await handler(request)
    # No password set: if an API key is required but absent, reject; else open (LAN).
    if config.WEB_API_KEY:
        return _json_error(401, "Invalid or missing API key.")
    return await handler(request)


@web.middleware
async def _error_middleware(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:
        logger.exception("web_handler_error path=%s: %s", request.path, exc)
        return _json_error(500, "Internal error handling request.")


# --- handlers --------------------------------------------------------------

async def health(request):
    return web.json_response({"ok": True})


async def login(request):
    if not auth.auth_enabled():
        # No password configured — issue nothing; the API is open.
        return _json_error(400, "Login is not enabled on this server.")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not auth.check_password((body.get("password") or "")):
        return _json_error(401, "Incorrect password.")
    return web.json_response({"token": auth.make_token()})


async def auth_config(request):
    """What the login page should offer. Public on purpose — it leaks only
    which login methods exist, which the login page has to show anyway."""
    return web.json_response({
        "password": auth.password_enabled(),
        "discord": auth.oauth_enabled(),
    })


async def auth_me(request):
    return web.json_response({"user": _user(request) or None})


async def discord_login(request):
    if not auth.oauth_enabled():
        return _json_error(503, "Discord login isn't configured on this bot.")
    raise web.HTTPFound(auth.authorize_url(auth.make_state()))


async def _resolve_member(bot, user_id: int):
    """Find the caller in a guild the bot serves.

    This is the authorization step, and it is the whole point: OAuth only
    proves someone owns a Discord account. Anyone on Discord can complete the
    consent screen, so without this every stranger would get a dashboard.

    Uses fetch_member (an API call) rather than get_member, because the bot
    runs without the privileged members intent and its member cache is
    therefore incomplete — get_member would deny real members at random.
    """
    if config.GUILD_ID:
        guild = bot.get_guild(int(config.GUILD_ID))
        guilds = [guild] if guild else []
    else:
        guilds = list(bot.guilds)

    for guild in guilds:
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            continue
        except discord.HTTPException as exc:
            logger.warning("member lookup failed guild=%s: %s", guild.id, exc)
            continue
        if member is not None:
            return guild, member
    return None, None


async def discord_callback(request):
    if not auth.oauth_enabled():
        return _json_error(503, "Discord login isn't configured on this bot.")

    if request.query.get("error"):
        # User clicked Cancel on the consent screen.
        raise web.HTTPFound("/login?error=denied")

    code = request.query.get("code") or ""
    state = request.query.get("state") or ""
    if not code or not auth.verify_state(state):
        # A missing or unsigned state means this callback did not start here.
        raise web.HTTPFound("/login?error=state")

    data = {
        "client_id": config.DISCORD_CLIENT_ID,
        "client_secret": config.DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.DISCORD_REDIRECT_URI,
    }
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(auth.TOKEN_URL, data=data) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("oauth token exchange failed %s: %s", resp.status, body[:300])
                    raise web.HTTPFound("/login?error=exchange")
                token_payload = await resp.json()

            access_token = token_payload.get("access_token")
            if not access_token:
                raise web.HTTPFound("/login?error=exchange")

            async with session.get(
                auth.USER_URL, headers={"Authorization": f"Bearer {access_token}"}
            ) as resp:
                if resp.status != 200:
                    logger.warning("oauth identify failed %s", resp.status)
                    raise web.HTTPFound("/login?error=identify")
                profile = await resp.json()
    except aiohttp.ClientError as exc:
        logger.warning("oauth network error: %s", exc)
        raise web.HTTPFound("/login?error=network") from None

    user_id = str(profile.get("id") or "")
    if not user_id:
        raise web.HTTPFound("/login?error=identify")

    guild, member = await _resolve_member(request.app[BOT_KEY], int(user_id))
    if member is None:
        logger.info("oauth denied: %s is not in a guild this bot serves", user_id)
        raise web.HTTPFound("/login?error=not_a_member")

    perms = getattr(member, "guild_permissions", None)
    is_admin = bool(
        (guild is not None and getattr(guild, "owner_id", None) == int(user_id))
        or (perms is not None and perms.administrator)
        or user_id in config.POWER_ADMINS
    )

    token = auth.make_token(claims={
        "sub": user_id,
        "name": getattr(member, "display_name", None)
        or profile.get("global_name")
        or profile.get("username")
        or "Discord user",
        "avatar": auth.avatar_url(user_id, profile.get("avatar")),
        "admin": is_admin,
    })
    logger.info("oauth login: %s (admin=%s)", user_id, is_admin)
    # Fragment, not query: the token never reaches the server access log or a
    # Referer header this way. The SPA reads it and clears the hash.
    raise web.HTTPFound(f"/login#token={urllib.parse.quote(token)}")


async def status(request):
    bot = request.app[BOT_KEY]
    players = _engine(bot).players
    guilds = []
    for g in bot.guilds:
        player = players.get(g.id)
        vc = player.voice_client if player else None
        guilds.append({
            "id": str(g.id),
            "name": g.name,
            "connected": bool(vc and vc.is_connected()),
            "voice_channel": vc.channel.name if (vc and vc.is_connected()) else None,
            "now_playing": player.current.title if (player and player.current) else None,
            "queue_length": len(player.queue) if player else 0,
            "paused": bool(vc and vc.is_paused()),
        })
    return web.json_response({
        "bot": str(bot.user) if bot.user else None,
        "guilds": guilds,
    })


async def search(request):
    bot = request.app[BOT_KEY]
    q = (request.query.get("q") or "").strip()
    if not q:
        raise web.HTTPBadRequest(reason="q required.")
    try:
        limit = int(request.query.get("limit", "5"))
    except ValueError:
        limit = 5
    limit = max(1, min(limit, 10))
    engine = _engine(bot)
    if engine.search_provider is None:
        return web.json_response({"results": []})
    results = await engine.search_provider.search_many(q, limit=limit)
    return web.json_response({
        "results": [
            {
                "title": r.title,
                "url": r.url,
                "source": r.source,
                "duration": r.duration,
                "uploader": r.uploader,
                "thumbnail": r.thumbnail,
            }
            for r in results
        ]
    })


async def list_voice_channels(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    channels = [
        {"id": str(ch.id), "name": ch.name, "members": len([m for m in ch.members if not m.bot])}
        for ch in guild.voice_channels
    ]
    return web.json_response({"channels": channels})


async def now_playing(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    player = _engine(bot).get_player(guild.id)
    if not player:
        return web.json_response({
            "connected": False,
            "current": None,
            "queue": [],
            "paused": False,
            "elapsed": None,
            "duration": None,
        })
    vc = player.voice_client
    return web.json_response({
        "connected": bool(vc and vc.is_connected()),
        "channel": vc.channel.name if (vc and vc.is_connected()) else None,
        "paused": bool(vc and vc.is_paused()),
        "current": _track_dict(player.current) if player.current else None,
        "queue": [_track_dict(t) for t in player.queue],
        "elapsed": player.elapsed_seconds(),
        "duration": player.current.duration if player.current else None,
    })


async def history(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    engine = _engine(bot)
    if engine.history_store is None:
        return web.json_response({"entries": []})
    try:
        limit = int(request.query.get("limit", "50"))
    except ValueError:
        limit = 50
    try:
        offset = int(request.query.get("offset", "0"))
    except ValueError:
        offset = 0
    entries = engine.history_store.recent(guild.id, limit=limit, offset=offset)
    return web.json_response({"entries": entries})



async def _connect_to_channel(bot, guild, channel_id: int):
    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.VoiceChannel) or channel.guild.id != guild.id:
        raise web.HTTPBadRequest(reason="Invalid voice channel for this guild.")
    player = _engine(bot).get_or_create_player(guild.id, _pick_text_channel(guild))
    await player.connect(channel)
    return player


async def playback_control(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    action = request.match_info["action"]
    player = _engine(bot).get_player(guild.id)
    if not player:
        return _json_error(409, "Nothing is playing in that guild.")
    if action == "pause":
        ok = player.pause()
        return web.json_response({"ok": ok})
    if action == "resume":
        ok = player.resume()
        return web.json_response({"ok": ok})
    if action == "skip":
        player.skip()
        return web.json_response({"ok": True})
    if action == "shuffle":
        count = await player.shuffle_queue()
        return web.json_response({"ok": True, "count": count})
    if action == "prev":
        moved = await player.prev()
        return web.json_response({"ok": moved})
    if action == "stop":
        player.stop()
        await player.disconnect()
        _engine(bot).players.pop(guild.id, None)
        return web.json_response({"ok": True})
    if action == "leave":
        # Disconnect but keep the queue (and the current track at front).
        await player.leave_voice()
        return web.json_response({"ok": True})
    raise web.HTTPNotFound(reason="Unknown action.")


async def seek_playback(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    body = await request.json() if request.can_read_body else {}
    try:
        seconds = float(body.get("seconds"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="seconds (number) required.") from None
    player = _engine(bot).get_player(guild.id)
    if not player:
        return _json_error(409, "Nothing is playing in that guild.")
    ok = await player.seek(seconds)
    if not ok:
        return _json_error(409, "Can't seek — no track, not connected, or stream not resolved.")
    return web.json_response({"ok": True, "elapsed": player.elapsed_seconds()})


async def connect(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    body = await request.json()
    channel_id = body.get("channel_id")
    if not channel_id:
        raise web.HTTPBadRequest(reason="channel_id required.")
    await _connect_to_channel(bot, guild, int(channel_id))
    return web.json_response({"ok": True})


async def queue_track(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    body = await request.json()
    query = (body.get("query") or "").strip()
    channel_id = body.get("channel_id")
    if not query:
        raise web.HTTPBadRequest(reason="query required.")
    engine = _engine(bot)
    if channel_id:
        player = await _connect_to_channel(bot, guild, int(channel_id))
    else:
        # Queue even if not connected — playback starts when the bot joins.
        player = engine.get_or_create_player(guild.id, _pick_text_channel(guild))
    tracks, err = await engine.resolve_tracks(query, _requester(request))
    if not tracks:
        return _json_error(422, err or "Couldn't resolve that query.")
    await player.add_many_and_play(tracks)
    connected = bool(player.voice_client and player.voice_client.is_connected())
    return web.json_response({
        "queued": len(tracks),
        "connected": connected,
        "tracks": [_track_dict(t) for t in tracks],
    })


async def remove_queue_track(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    try:
        index = int(request.match_info["index"])
    except ValueError:
        raise web.HTTPBadRequest(reason="Invalid index.") from None
    player = _engine(bot).get_player(guild.id)
    if not player:
        return _json_error(409, "Nothing is queued in that guild.")
    removed = await player.remove_from_queue(index)
    if removed is None:
        raise web.HTTPNotFound(reason="No track at that position.")
    return web.json_response({"removed": _track_dict(removed)})


async def move_queue_track(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    body = await request.json() if request.can_read_body else {}
    try:
        src = int(body.get("from"))
        dst = int(body.get("to"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="'from' and 'to' are required integers.") from None
    player = _engine(bot).get_player(guild.id)
    if not player:
        return _json_error(409, "Nothing is queued in that guild.")
    if not await player.move_in_queue(src, dst):
        raise web.HTTPBadRequest(reason="Indices out of range.")
    return web.json_response({"ok": True})


# --- playlist handlers -----------------------------------------------------

async def list_playlists(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    store = _engine(bot).playlist_store
    names = store.list_playlists(guild.id)
    out = [{"name": n, "count": len(store.load(guild.id, n))} for n in sorted(names)]
    return web.json_response({"playlists": out})


async def get_playlist(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    name = request.match_info["name"]
    store = _engine(bot).playlist_store
    entries = store.load(guild.id, name)
    return web.json_response({"name": name, "entries": [_entry_dict(e) for e in entries]})


async def add_to_playlist(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    name = request.match_info["name"]
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        raise web.HTTPBadRequest(reason="query required.")
    entry = await _engine(bot).resolve_playlist_entry(query, _requester(request))
    if not entry:
        return _json_error(422, "Couldn't resolve that track.")
    position = _engine(bot).playlist_store.add(guild.id, name, entry)
    return web.json_response({"position": position, "entry": _entry_dict(entry)})


async def create_playlist(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    body = await request.json() if request.can_read_body else {}
    raw_name = (body.get("name") or "").strip()
    if not raw_name:
        raise web.HTTPBadRequest(reason="name required.")
    store = _engine(bot).playlist_store
    existing_slugs = {_safe(n) for n in store.list_playlists(guild.id)}
    final_name = _safe(raw_name)
    if final_name in existing_slugs:
        return _json_error(409, f"A playlist named {raw_name!r} already exists.")
    store.save(guild.id, raw_name, [])
    return web.json_response({"name": final_name, "count": 0})


async def delete_playlist(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    name = request.match_info["name"]
    ok = _engine(bot).playlist_store.delete_playlist(guild.id, name)
    if not ok:
        raise web.HTTPNotFound(reason="No such playlist.")
    return web.json_response({"ok": True})


async def remove_track(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    name = request.match_info["name"]
    index = int(request.match_info["index"])
    removed = _engine(bot).playlist_store.remove(guild.id, name, index)
    if removed is None:
        raise web.HTTPNotFound(reason="No track at that position.")
    return web.json_response({"removed": _entry_dict(removed)})


async def reorder_playlist(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    name = request.match_info["name"]
    body = await request.json()
    raw = body.get("entries")
    if not isinstance(raw, list):
        raise web.HTTPBadRequest(reason="entries (list) required.")
    entries = [
        PlaylistEntry(
            title=e["title"], url=e["url"], source=e["source"], added_by=e.get("added_by", "web")
        )
        for e in raw
    ]
    _engine(bot).playlist_store.save(guild.id, name, entries)
    return web.json_response({"ok": True, "count": len(entries)})


async def _url_to_entries(bot, url: str, added_by: str) -> tuple[list[PlaylistEntry], Optional[str]]:
    entries, _title, err = await _url_to_entries_with_title(bot, url, added_by)
    return entries, err


async def _url_to_entries_with_title(
    bot, url: str, added_by: str
) -> tuple[list[PlaylistEntry], Optional[str], Optional[str]]:
    """Like ``_url_to_entries`` but also returns a derived playlist title.

    Title is the playlist/album/collection name when the source exposes one,
    the track name for single-track imports, or ``None`` when nothing useful
    can be derived (e.g. generic single-URL imports)."""
    source_type = detect_source(url)
    if source_type in (SourceType.SPOTIFY, SourceType.SPOTIFY_PLAYLIST):
        from music.sources.spotify import resolve_spotify_with_title
        queries, title, err = await resolve_spotify_with_title(url)
        if not queries:
            return [], None, err or "Couldn't read that Spotify URL."
        return (
            [PlaylistEntry(title=q, url=q, source="search", added_by=added_by) for q in queries],
            title,
            None,
        )
    if source_type in (SourceType.YOUTUBE_PLAYLIST, SourceType.SOUNDCLOUD_PLAYLIST):
        from music.sources.youtube import resolve_youtube_playlist_with_title
        tracks, title = await resolve_youtube_playlist_with_title(url, added_by)
        if not tracks:
            return [], None, "Couldn't load any tracks from that playlist."
        return (
            [PlaylistEntry(title=t.title, url=t.url, source="youtube", added_by=added_by) for t in tracks],
            title,
            None,
        )
    if source_type in (SourceType.APPLE_MUSIC, SourceType.APPLE_MUSIC_PLAYLIST):
        from music.sources.apple_music import resolve_apple_music_with_title
        queries, title, err = await resolve_apple_music_with_title(url)
        if not queries:
            return [], None, err or "Couldn't load that Apple Music URL."
        return (
            [PlaylistEntry(title=q, url=q, source="search", added_by=added_by) for q in queries],
            title,
            None,
        )
    entry = await _engine(bot).resolve_playlist_entry(url, added_by)
    if not entry:
        return [], None, "Couldn't resolve that URL."
    return [entry], entry.title, None


async def sync_playlist(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    name = request.match_info["name"]
    body = await request.json()
    url = (body.get("url") or "").strip()
    replace = bool(body.get("replace", False))
    if not url:
        raise web.HTTPBadRequest(reason="url required.")
    entries, err = await _url_to_entries(bot, url, _requester(request))
    if not entries:
        return _json_error(422, err or "Nothing to import.")
    store = _engine(bot).playlist_store
    if replace:
        store.save(guild.id, name, entries)
    else:
        existing = store.load(guild.id, name)
        store.save(guild.id, name, existing + entries)
    auto_source = _auto_sync_source(url)
    if replace and auto_source:
        store.set_sync_source(guild.id, name, url, auto_source)
    return web.json_response({"imported": len(entries), "total": len(store.load(guild.id, name))})


def _unique_playlist_name(existing_slugs: set[str], base: str) -> str:
    """Return a slugged playlist name that isn't already in ``existing_slugs``.

    ``existing_slugs`` is the set of slugs already on disk (case-insensitive).
    Tries ``base``, then ``base (2)``, ``base (3)``, ... until it finds a free
    slot. Returns the human-readable name (caller should ``_safe`` it again
    when persisting — passing ``"foo (2)"`` through ``_safe`` is stable)."""
    base_slug = _safe(base)
    if base_slug not in existing_slugs:
        return base
    n = 2
    while True:
        candidate = f"{base} ({n})"
        if _safe(candidate) not in existing_slugs:
            return candidate
        n += 1


async def import_playlist(request):
    """Create a NEW playlist from a URL.

    Body: ``{url, name?}``. If ``name`` is omitted, derives one from the
    source's playlist/album title, falling back to ``"import"`` with a numeric
    suffix. Refuses (409) if the resulting slug collides with an existing
    playlist file."""
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    body = await request.json()
    url = (body.get("url") or "").strip()
    raw_name = (body.get("name") or "").strip()
    if not url:
        raise web.HTTPBadRequest(reason="url required.")
    entries, title, err = await _url_to_entries_with_title(bot, url, _requester(request))
    if not entries:
        return _json_error(422, err or "Nothing to import.")
    store = _engine(bot).playlist_store
    existing_slugs = {_safe(n) for n in store.list_playlists(guild.id)}
    if raw_name:
        if _safe(raw_name) in existing_slugs:
            return _json_error(409, f"A playlist named {raw_name!r} already exists.")
        final_name = raw_name
    else:
        base = title or "import"
        final_name = _unique_playlist_name(existing_slugs, base)
    store.save(guild.id, final_name, entries)
    auto_source = _auto_sync_source(url)
    if auto_source:
        store.set_sync_source(guild.id, final_name, url, auto_source)
    return web.json_response({"name": _safe(final_name), "imported": len(entries)})


async def play_playlist(request):
    bot = request.app[BOT_KEY]
    guild = _require_guild(bot, request)
    name = request.match_info["name"]
    body = await request.json() if request.can_read_body else {}
    channel_id = body.get("channel_id")
    engine = _engine(bot)
    store = engine.playlist_store
    entries = store.load(guild.id, name)
    if not entries:
        return _json_error(422, "Playlist is empty.")
    if channel_id:
        player = await _connect_to_channel(bot, guild, int(channel_id))
    else:
        # Queue even if not connected — playback starts when the bot joins.
        player = engine.get_or_create_player(guild.id, _pick_text_channel(guild))
    tracks = [
        Track(title=e.title, url=e.url, source=e.source, duration=None, requester=e.added_by)
        for e in entries
    ]
    await player.add_many_and_play(tracks)
    connected = bool(player.voice_client and player.voice_client.is_connected())
    return web.json_response({"queued": len(tracks), "connected": connected})


# --- local file handlers ---------------------------------------------------

_AUDIO_EXTS = {".mp3", ".flac", ".wav", ".ogg", ".aac", ".m4a", ".opus", ".wma"}
_MAX_FILES_LISTED = 500
_UPLOAD_MAX_BYTES = 100 * 1024 * 1024  # 100 MB per upload field


def _is_audio_filename(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in _AUDIO_EXTS


def _scan_music_dir(music_dir: str) -> tuple[list[dict], bool]:
    """Walk the music library and return (entries, truncated).

    Synchronous and potentially slow — a large library means thousands of
    stat() calls — so callers must run this off the event loop.
    """
    if not os.path.isdir(music_dir):
        return [], False
    base = os.path.normpath(music_dir)
    collected: list[dict] = []
    for dirpath, _dirnames, filenames in os.walk(base, followlinks=False):
        for fname in filenames:
            if not _is_audio_filename(fname):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            try:
                size: Optional[int] = os.path.getsize(full)
            except OSError:
                size = None
            collected.append({"path": rel, "name": fname, "size": size})
    collected.sort(key=lambda e: e["path"].lower())
    truncated = len(collected) > _MAX_FILES_LISTED
    if truncated:
        collected = collected[:_MAX_FILES_LISTED]
    return collected, truncated


async def list_files(request):
    music_dir = config.MUSIC_DIR
    if not music_dir:
        return web.json_response({"files": []})
    # os.walk over a large library blocks for seconds. This process also runs
    # the Discord voice loop, so it must never happen inline.
    loop = asyncio.get_running_loop()
    collected, truncated = await loop.run_in_executor(None, _scan_music_dir, music_dir)
    out: dict = {"files": collected}
    if truncated:
        out["truncated"] = True
    return web.json_response(out)


def _pick_unique_basename(directory: str, basename: str) -> str:
    """Return a basename that doesn't collide with an existing file in *directory*.

    Suffixes " (2)", " (3)", ... before the extension until a free name is found.
    """
    candidate = basename
    if not os.path.exists(os.path.join(directory, candidate)):
        return candidate
    stem, ext = os.path.splitext(basename)
    n = 2
    while True:
        candidate = f"{stem} ({n}){ext}"
        if not os.path.exists(os.path.join(directory, candidate)):
            return candidate
        n += 1


async def upload_file(request):
    music_dir = config.MUSIC_DIR
    if not music_dir or not os.path.isdir(music_dir) or not os.access(music_dir, os.W_OK):
        return _json_error(503, "Music directory is not configured or not writable.")
    base = os.path.normpath(music_dir)

    try:
        reader = await request.multipart()
    except Exception:
        raise web.HTTPBadRequest(reason="Expected multipart upload.") from None

    saved: list[str] = []
    while True:
        field = await reader.next()
        if field is None:
            break
        if field.name != "file":
            # Ignore other fields.
            continue
        raw_name = field.filename or ""
        basename = os.path.basename(raw_name).strip()
        if not basename:
            return _json_error(400, "Missing filename.")
        if not _is_audio_filename(basename):
            return _json_error(400, f"Unsupported file extension: {basename!r}.")
        final_name = _pick_unique_basename(base, basename)
        dest = os.path.join(base, final_name)
        total = 0
        try:
            with open(dest, "wb") as fh:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _UPLOAD_MAX_BYTES:
                        fh.close()
                        try:
                            os.remove(dest)
                        except OSError:
                            pass
                        return _json_error(413, "Upload exceeds 100 MB limit.")
                    fh.write(chunk)
        except OSError as exc:
            try:
                os.remove(dest)
            except OSError:
                pass
            logger.exception("upload_failed name=%s: %s", final_name, exc)
            return _json_error(500, "Failed to write uploaded file.")
        saved.append(final_name)

    if not saved:
        return _json_error(400, "No 'file' field in upload.")
    return web.json_response({"saved": saved})


# --- games -----------------------------------------------------------------
#
# These sit behind the same auth as everything else. Note the Discord side also
# keeps a per-user allowlist, because dashboard-power has no authentication of
# its own and any guild member can invoke a slash command. The web API has no
# equivalent gate to add: holding the dashboard password is already enough to
# queue tracks and upload files, so the meaningful boundary is the password
# itself. The surface stays start-only, one fixed container.

async def games_status(request):
    games = _games(request.app[BOT_KEY])
    if not games.enabled:
        return web.json_response({"enabled": False})
    running, err = await games.is_running()
    return web.json_response({
        "enabled": True,
        "running": running,
        "starting": games.starting,
        "players_configured": games.players_configured,
        "error": err,
    })


async def games_start(request):
    games = _games(request.app[BOT_KEY])
    if not games.enabled:
        return _json_error(503, "Server control isn't configured on this bot.")
    state, err = await games.ensure_started()
    if state == "error":
        return _json_error(502, f"Couldn't reach the server controls: {err}.")
    if state == "busy":
        return _json_error(409, "Palworld is already starting.")
    # "starting" returns immediately; the client polls /api/games/status. The
    # boot can take minutes when a game update downloads, which is far longer
    # than a request should be held open for.
    return web.json_response({"state": state})


async def games_players(request):
    games = _games(request.app[BOT_KEY])
    if not games.enabled or not games.players_configured:
        return _json_error(503, "The player list isn't configured on this bot.")
    running, err = await games.is_running()
    if not err and not running:
        return web.json_response({"running": False, "players": []})
    people, err = await games.players()
    if err:
        return _json_error(502, f"Couldn't read the player list — {err}.")
    return web.json_response({"running": True, "players": people})


async def games_connect(request):
    games = _games(request.app[BOT_KEY])
    if not games.enabled:
        return _json_error(503, "Server control isn't configured on this bot.")
    return web.json_response({
        "address": await games.connect_address(),
        "password": config.PALWORLD_SERVER_PASSWORD or None,
        "server_name": config.PALWORLD_SERVER_NAME or None,
    })


# --- app factory -----------------------------------------------------------

def create_app(bot) -> web.Application:
    app = web.Application(middlewares=[_error_middleware, _auth_middleware])
    app[BOT_KEY] = bot
    app.add_routes([
        web.get("/api/health", health),
        web.post("/api/login", login),
        web.get("/api/auth/config", auth_config),
        web.get("/api/auth/me", auth_me),
        web.get("/api/auth/discord/login", discord_login),
        web.get("/api/auth/discord/callback", discord_callback),
        web.get("/api/status", status),
        web.get("/api/search", search),
        web.get("/api/files", list_files),
        web.get("/api/games/status", games_status),
        web.post("/api/games/start", games_start),
        web.get("/api/games/players", games_players),
        web.get("/api/games/connect", games_connect),
        web.post("/api/upload", upload_file),
        web.get("/api/guilds/{gid}/voice-channels", list_voice_channels),
        web.get("/api/guilds/{gid}/now-playing", now_playing),
        web.get("/api/guilds/{gid}/history", history),
        web.post("/api/guilds/{gid}/playback/{action}", playback_control),
        web.post("/api/guilds/{gid}/seek", seek_playback),
        web.post("/api/guilds/{gid}/connect", connect),
        web.post("/api/guilds/{gid}/queue", queue_track),
        web.post("/api/guilds/{gid}/queue/move", move_queue_track),
        web.delete("/api/guilds/{gid}/queue/{index}", remove_queue_track),
        web.get("/api/guilds/{gid}/playlists", list_playlists),
        web.post("/api/guilds/{gid}/playlists", create_playlist),
        web.post("/api/guilds/{gid}/playlists/import", import_playlist),
        web.get("/api/guilds/{gid}/playlists/{name}", get_playlist),
        web.post("/api/guilds/{gid}/playlists/{name}", add_to_playlist),
        web.delete("/api/guilds/{gid}/playlists/{name}", delete_playlist),
        web.delete("/api/guilds/{gid}/playlists/{name}/tracks/{index}", remove_track),
        web.put("/api/guilds/{gid}/playlists/{name}", reorder_playlist),
        web.post("/api/guilds/{gid}/playlists/{name}/sync", sync_playlist),
        web.post("/api/guilds/{gid}/playlists/{name}/play", play_playlist),
    ])
    return app


async def start_web_server(bot) -> web.AppRunner:
    app = create_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.WEB_PORT)
    await site.start()
    logger.info("Web API listening on 0.0.0.0:%d", config.WEB_PORT)
    return runner
