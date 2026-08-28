"""Spotify Web API client (client-credentials flow).

Spotify's pages are DRM-blocked by yt-dlp, so we use the official Web API to
turn Spotify track/album/playlist URLs into YouTube search queries that the
player can then resolve via yt-dlp."""
import asyncio
import logging
import re
import time
from typing import Optional

import aiohttp

import config

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_API_BASE = "https://api.spotify.com/v1"
_PLAYLIST_LIMIT = 100
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)

_SPOTIFY_URL_RE = re.compile(
    r"open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)"
)

_token_lock = asyncio.Lock()
_token: Optional[str] = None
_token_expires_at: float = 0.0


class SpotifyError(Exception):
    pass


def _credentials_set() -> bool:
    return bool(config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET)


async def _get_token(session: aiohttp.ClientSession) -> str:
    global _token, _token_expires_at
    async with _token_lock:
        now = time.monotonic()
        if _token and now < _token_expires_at - 30:
            return _token
        if not _credentials_set():
            raise SpotifyError("Spotify credentials not configured.")
        auth = aiohttp.BasicAuth(config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET)
        async with session.post(
            _TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=auth,
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise SpotifyError(f"token request status={resp.status} body={body[:200]}")
            data = await resp.json()
        _token = data["access_token"]
        _token_expires_at = now + int(data.get("expires_in", 3600))
        return _token


async def _get_json(session: aiohttp.ClientSession, url: str, params: Optional[dict] = None) -> dict:
    token = await _get_token(session)
    headers = {"Authorization": f"Bearer {token}"}
    async with session.get(url, params=params, headers=headers) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise SpotifyError(f"GET {url} status={resp.status} body={body[:200]}")
        return await resp.json()


def _track_query(track: dict) -> Optional[str]:
    name = track.get("name") or ""
    artists = track.get("artists") or []
    artist_names = ", ".join(
        a.get("name", "") for a in artists if isinstance(a, dict) and a.get("name")
    )
    if not name:
        return None
    return f"{name} {artist_names}".strip() if artist_names else name


async def resolve_spotify(url: str) -> tuple[list[str], Optional[str]]:
    """Return (queries, error_message) for a Spotify URL.
    Resolves track / album / playlist URLs to YouTube search strings."""
    queries, _title, err = await resolve_spotify_with_title(url)
    return queries, err


async def resolve_spotify_with_title(
    url: str,
) -> tuple[list[str], Optional[str], Optional[str]]:
    """Like resolve_spotify, but also returns a derived title (track name for
    tracks, album/playlist name for collections). Title is ``None`` on error."""
    if not _credentials_set():
        return (
            [],
            None,
            "Spotify support requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to be set.",
        )
    m = _SPOTIFY_URL_RE.search(url)
    if not m:
        return [], None, "Unrecognized Spotify URL."
    kind, sid = m.groups()
    try:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            if kind == "track":
                data = await _get_json(session, f"{_API_BASE}/tracks/{sid}")
                q = _track_query(data)
                title = data.get("name") if isinstance(data, dict) else None
                return (
                    ([q] if q else []),
                    (title or None) if q else None,
                    None if q else "Couldn't read that Spotify track.",
                )
            if kind == "album":
                album = await _get_json(session, f"{_API_BASE}/albums/{sid}")
                items = (album.get("tracks") or {}).get("items") or []
                queries = [q for q in (_track_query(t) for t in items) if q]
                title = album.get("name") if isinstance(album, dict) else None
                return queries[:_PLAYLIST_LIMIT], (title or None), None
            if kind == "playlist":
                meta = await _get_json(
                    session,
                    f"{_API_BASE}/playlists/{sid}",
                    params={"fields": "name"},
                )
                data = await _get_json(
                    session,
                    f"{_API_BASE}/playlists/{sid}/tracks",
                    params={
                        "limit": 100,
                        "fields": "items(track(name,artists(name)))",
                    },
                )
                items = data.get("items") or []
                queries: list[str] = []
                for item in items:
                    track = item.get("track") if isinstance(item, dict) else None
                    if not isinstance(track, dict):
                        continue
                    q = _track_query(track)
                    if q:
                        queries.append(q)
                title = meta.get("name") if isinstance(meta, dict) else None
                return queries[:_PLAYLIST_LIMIT], (title or None), None
    except SpotifyError as exc:
        logger.warning("spotify_api_failed url=%r error=%s", url, exc)
        return [], None, "Spotify API request failed."
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("spotify_http_failed url=%r error=%s", url, exc)
        return [], None, "Couldn't reach Spotify."
    return [], None, "Unrecognized Spotify URL."
