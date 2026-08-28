import logging
import socket
from typing import Optional

import aiohttp

from music.search.base import SearchProvider, SearchResult
from music.track import Track

_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
logger = logging.getLogger(__name__)


class YouTubeDataAPISearchProvider(SearchProvider):
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(family=socket.AF_INET)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def search_many(self, query: str, limit: int = 5) -> list[SearchResult]:
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": max(1, min(limit, 10)),
            "key": self._api_key,
        }
        session = await self._get_session()
        async with session.get(_SEARCH_URL, params=params) as resp:
            if not resp.ok:
                body = await resp.text()
                logger.warning(
                    "YouTube Data API error status=%s body=%s", resp.status, body[:300]
                )
                return []
            data = await resp.json()

        results: list[SearchResult] = []
        for item in data.get("items", []):
            video_id = item.get("id", {}).get("videoId")
            if not video_id:
                continue
            snippet = item.get("snippet", {})
            thumbs = snippet.get("thumbnails", {}) or {}
            thumb = None
            for key in ("maxres", "standard", "high", "medium", "default"):
                candidate = thumbs.get(key, {})
                if candidate.get("url"):
                    thumb = candidate["url"]
                    break
            results.append(
                SearchResult(
                    title=snippet.get("title", ""),
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    source="youtube",
                    duration=None,  # Data API search doesn't include duration
                    uploader=snippet.get("channelTitle"),
                    thumbnail=thumb,
                )
            )
        return results

    async def search(self, query: str) -> Optional[Track]:
        results = await self.search_many(query, limit=5)
        if not results:
            logger.warning("YouTube Data API no video results query=%r", query)
            return None
        first = results[0]
        return Track(
            title=first.title,
            url=first.url,
            source=first.source,
            duration=first.duration,
            requester="",
            thumbnail=first.thumbnail,
        )
