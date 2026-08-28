from dataclasses import dataclass
from typing import Optional


@dataclass
class Track:
    title: str
    url: str
    source: str
    duration: Optional[int]
    requester: str
    stream_url: Optional[str] = None
    thumbnail: Optional[str] = None

    @property
    def needs_resolution(self) -> bool:
        return self.stream_url is None
