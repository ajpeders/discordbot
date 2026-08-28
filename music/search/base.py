from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from music.track import Track


@dataclass
class SearchResult:
    title: str
    url: str
    source: str
    duration: Optional[int] = None
    uploader: Optional[str] = None
    thumbnail: Optional[str] = None


class SearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str) -> Optional[Track]:
        """Given a text query, return the best matching track."""
        ...

    @abstractmethod
    async def search_many(self, query: str, limit: int = 5) -> list[SearchResult]:
        """Return up to `limit` search candidates without committing to one."""
        ...
