import asyncio
from typing import Optional

import yt_dlp

from music.search.base import SearchProvider, SearchResult
from music.track import Track

_YDL_SEARCH_OPTS = {
    "remote_components": ["ejs:github"],
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "extract_flat": "in_playlist",
    "default_search": "ytsearch1",
    "socket_timeout": 5,
    "retries": 0,
    "extractor_retries": 0,
}


class YouTubeSearchProvider(SearchProvider):
    async def search_many(self, query: str, limit: int = 5) -> list[SearchResult]:
        n = max(1, min(limit, 10))
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, self._extract, f"ytsearch{n}:{query}")
        if info is None:
            return []
        entries = info.get("entries") if "entries" in info else [info]
        results: list[SearchResult] = []
        for entry in entries or []:
            if not entry:
                continue
            entry_id = entry.get("id")
            url = entry.get("webpage_url") or (
                f"https://www.youtube.com/watch?v={entry_id}" if entry_id else None
            )
            if not url:
                continue
            results.append(
                SearchResult(
                    title=entry.get("title", ""),
                    url=url,
                    source="youtube",
                    duration=entry.get("duration"),
                    uploader=entry.get("uploader") or entry.get("channel"),
                    thumbnail=entry.get("thumbnail"),
                )
            )
        return results

    async def search(self, query: str) -> Optional[Track]:
        results = await self.search_many(query, limit=1)
        if not results:
            return None
        r = results[0]
        return Track(
            title=r.title,
            url=r.url,
            source=r.source,
            duration=r.duration,
            requester="",
            thumbnail=r.thumbnail,
        )

    @staticmethod
    def _extract(query: str) -> Optional[dict]:
        with yt_dlp.YoutubeDL(_YDL_SEARCH_OPTS) as ydl:
            try:
                return ydl.extract_info(query, download=False)
            except yt_dlp.utils.DownloadError:
                return None
