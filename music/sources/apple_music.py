"""Apple Music via the public iTunes Lookup API (no auth).

Apple Music pages are DRM-blocked by yt-dlp, so we use Apple's free iTunes
Lookup endpoint to turn album/song URLs into YouTube search queries that the
player can then resolve via yt-dlp. User-curated playlists (`pl.XXX` ids) are
not addressable through the free API, so we refuse them with a helpful error."""
import asyncio
import logging
import re
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_LOOKUP_URL = "https://itunes.apple.com/lookup"
_PLAYLIST_LIMIT = 100
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)

_SONG_URL_RE = re.compile(r"music\.apple\.com/[^/]+/song/[^/]+/(\d+)")
_ALBUM_URL_RE = re.compile(r"music\.apple\.com/[^/]+/album/[^/]+/(\d+)")
_PLAYLIST_URL_RE = re.compile(r"music\.apple\.com/[^/]+/playlist/")
_I_PARAM_RE = re.compile(r"[?&]i=(\d+)")

_PLAYLIST_ERROR = (
    "Apple Music user playlists aren't supported by the free iTunes API. "
    "Use an album URL instead."
)


def _track_query(track: dict) -> Optional[str]:
    name = track.get("trackName") or ""
    artist = track.get("artistName") or ""
    if not name:
        return None
    return f"{name} {artist}".strip() if artist else name


async def _lookup(session: aiohttp.ClientSession, params: dict) -> Optional[dict]:
    async with session.get(_LOOKUP_URL, params=params) as resp:
        if resp.status != 200:
            return None
        # iTunes returns Content-Type: text/javascript; pass content_type=None
        # so aiohttp doesn't raise ContentTypeError.
        return await resp.json(content_type=None)


async def resolve_apple_music(url: str) -> tuple[list[str], Optional[str]]:
    """Return (queries, error_message) for an Apple Music URL."""
    queries, _title, err = await resolve_apple_music_with_title(url)
    return queries, err


async def resolve_apple_music_with_title(
    url: str,
) -> tuple[list[str], Optional[str], Optional[str]]:
    """Like resolve_apple_music, but also returns a derived title.

    - For single songs (or albums deep-linked via ``?i=``): the track name.
    - For albums: the ``collectionName`` from the album wrapper.

    A `?i=<songId>` query param takes precedence over the album in the path
    (Apple Music uses this to deep-link a single track inside an album page).
    """
    if _PLAYLIST_URL_RE.search(url):
        return [], None, _PLAYLIST_ERROR

    i_match = _I_PARAM_RE.search(url)
    if i_match:
        sid = i_match.group(1)
        kind = "song"
    else:
        song_match = _SONG_URL_RE.search(url)
        album_match = _ALBUM_URL_RE.search(url)
        if song_match:
            sid = song_match.group(1)
            kind = "song"
        elif album_match:
            sid = album_match.group(1)
            kind = "album"
        else:
            return [], None, "Unrecognized Apple Music URL."

    params: dict[str, str] = {"id": sid}
    if kind == "album":
        params["entity"] = "song"

    try:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            data = await _lookup(session, params)
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("apple_music_http_failed url=%r error=%s", url, exc)
        return [], None, "Couldn't reach Apple Music."

    if not data:
        return [], None, "Couldn't reach Apple Music."

    results = data.get("results") or []
    tracks = [r for r in results if isinstance(r, dict) and r.get("wrapperType") == "track"]

    if kind == "song":
        if not tracks:
            return [], None, "Couldn't read that Apple Music song."
        q = _track_query(tracks[0])
        title = tracks[0].get("trackName")
        return (
            ([q] if q else []),
            (title or None) if q else None,
            None if q else "Couldn't read that Apple Music song.",
        )

    # Album: collectionName lives on the album-wrapper result (entity != track).
    title: Optional[str] = None
    for r in results:
        if isinstance(r, dict) and r.get("wrapperType") == "collection":
            title = r.get("collectionName") or None
            break
    if title is None and tracks:
        # Fallback — every track also carries the collection it belongs to.
        title = tracks[0].get("collectionName") or None
    queries = [q for q in (_track_query(t) for t in tracks) if q]
    return queries[:_PLAYLIST_LIMIT], title, None
