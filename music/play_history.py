"""Append-only per-guild play history.

Each time `GuildPlayer` actually starts playing a track, the cog records
one JSONL row to `DATA_DIR/history/<guild_id>.jsonl`. Reads return the
newest rows first, suitable for the History UI page and as the data
source for future "recommended" surfaces.
"""
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Optional

from music.track import Track

logger = logging.getLogger(__name__)


@dataclass
class PlayRecord:
    ts: float
    title: str
    url: str
    source: str
    duration: Optional[int]
    requester: str
    thumbnail: Optional[str]


class PlayHistoryStore:
    """JSONL-backed play history. One file per guild, append-only.

    Concurrency: a per-guild `threading.Lock` serializes writes so a
    concurrent record+read can't observe a half-written line."""

    def __init__(self, data_dir: str):
        self._dir = os.path.join(data_dir, "history")
        # Defer makedirs until first write so importing the cog never fails
        # just because DATA_DIR isn't writable (e.g. unit tests).
        self._locks: dict[int, threading.Lock] = {}
        self._meta_lock = threading.Lock()

    def _path(self, guild_id: int) -> str:
        return os.path.join(self._dir, f"{guild_id}.jsonl")

    def _lock_for(self, guild_id: int) -> threading.Lock:
        with self._meta_lock:
            lock = self._locks.get(guild_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[guild_id] = lock
            return lock

    def record(self, guild_id: int, track: Track, *, ts: Optional[float] = None) -> None:
        if ts is None:
            ts = time.time()
        record = PlayRecord(
            ts=ts,
            title=track.title,
            url=track.url,
            source=track.source,
            duration=track.duration,
            requester=track.requester,
            thumbnail=track.thumbnail,
        )
        try:
            with self._lock_for(guild_id):
                os.makedirs(self._dir, exist_ok=True)
                with open(self._path(guild_id), "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(record)) + "\n")
        except OSError as exc:
            logger.warning("play_history_record_failed guild=%s error=%s", guild_id, exc)

    def recent(self, guild_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return up to `limit` records starting at `offset` (0-based from newest)."""
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        path = self._path(guild_id)
        if not os.path.exists(path):
            return []
        try:
            with self._lock_for(guild_id), open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as exc:
            logger.warning("play_history_read_failed guild=%s error=%s", guild_id, exc)
            return []
        results: list[dict] = []
        need = limit + offset
        for raw in reversed(lines):
            line = raw.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(results) >= need:
                break
        return results[offset:offset + limit]
