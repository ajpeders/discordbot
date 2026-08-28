import asyncio
import logging
from typing import Optional
import yt_dlp
from music.track import Track

logger = logging.getLogger(__name__)

_YDL_OPTS = {
    "remote_components": ["ejs:github"],
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "socket_timeout": 5,
    "retries": 0,
    "extractor_retries": 0,
}

_YDL_PLAYLIST_OPTS = {
    "remote_components": ["ejs:github"],
    "extract_flat": True,
    "quiet": True,
    "no_warnings": True,
    "socket_timeout": 15,
    "noplaylist": False,
    "playlistend": 100,
}

# Max number of tracks pulled from a single playlist URL.
PLAYLIST_LIMIT = 100


def _best_thumbnail(info: dict) -> Optional[str]:
    """Pick a thumbnail URL out of a yt-dlp info dict."""
    if not isinstance(info, dict):
        return None
    thumbs = info.get("thumbnails")
    if isinstance(thumbs, list) and thumbs:
        # yt-dlp orders thumbnails small→large; the last is usually the best.
        for t in reversed(thumbs):
            if isinstance(t, dict) and t.get("url"):
                return t["url"]
    t = info.get("thumbnail")
    return t if isinstance(t, str) else None


async def resolve_youtube_track(url: str, requester: str) -> Optional[Track]:
    """Extract metadata from a YouTube URL. Stream URL resolved lazily at play time."""
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, _extract, url)
    if info is None:
        return None
    return Track(
        title=info.get("title", url),
        url=info.get("webpage_url", url),
        source="youtube",
        duration=info.get("duration"),
        requester=requester,
        thumbnail=_best_thumbnail(info),
    )

async def resolve_stream_url(track: Track) -> tuple[str, Optional[str]]:
    """Resolve a playable stream URL for a YouTube-backed track. Returns (stream_url, thumbnail)."""
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, _extract, track.url)
    if info is None:
        raise RuntimeError(f"Failed to resolve stream URL for: {track.title}")
    return info["url"], _best_thumbnail(info)


async def resolve_youtube_playlist(url: str, requester: str) -> list[Track]:
    """Extract tracks from a YouTube playlist URL. Stream URLs are resolved lazily."""
    tracks, _title = await resolve_youtube_playlist_with_title(url, requester)
    return tracks


async def resolve_youtube_playlist_with_title(
    url: str, requester: str
) -> tuple[list[Track], Optional[str]]:
    """Like resolve_youtube_playlist, but also returns the playlist title (if any)."""
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, _extract_playlist, url)
    if info is None:
        return [], None
    entries = info.get("entries") or []
    tracks: list[Track] = []
    for entry in entries:
        if not entry:
            continue
        entry_id = entry.get("id")
        entry_url = entry.get("url") or entry.get("webpage_url")
        if entry_url and entry_url.startswith("http"):
            track_url = entry_url
        elif entry_id:
            track_url = f"https://www.youtube.com/watch?v={entry_id}"
        else:
            continue
        tracks.append(Track(
            title=entry.get("title") or track_url,
            url=track_url,
            source="youtube",
            duration=entry.get("duration"),
            requester=requester,
            thumbnail=_best_thumbnail(entry),
        ))
    title = info.get("title") if isinstance(info, dict) else None
    return tracks[:PLAYLIST_LIMIT], (title or None)


def _extract(url: str) -> Optional[dict]:
    with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if info and "entries" in info:
                entries = list(info["entries"])
                return entries[0] if entries else None
            return info
        except yt_dlp.utils.DownloadError:
            return None


def _extract_playlist(url: str) -> Optional[dict]:
    with yt_dlp.YoutubeDL(_YDL_PLAYLIST_OPTS) as ydl:
        try:
            return ydl.extract_info(url, download=False)
        except Exception as exc:
            logger.warning("youtube_playlist_extract_failed url=%r error=%s", url, exc)
            return None
