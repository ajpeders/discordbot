from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_SAFE_NAME_RE = re.compile(r"[^\w\-]")


def _safe(name: str) -> str:
    result = _SAFE_NAME_RE.sub("_", name.strip().lower())[:64]
    return result or "default"


@dataclass
class PlaylistEntry:
    title: str
    url: str
    source: str
    added_by: str


@dataclass
class PlaylistSyncSource:
    url: str
    source: str
    last_synced_at: float | None = None
    last_error: str | None = None


class PlaylistStore:
    def __init__(self, data_dir: str):
        self._dir = os.path.join(data_dir, "playlists")
        # Defer makedirs until first write, matching PlayHistoryStore: simply
        # constructing the store must not fail because DATA_DIR isn't writable.
        # _guild_dir() creates the tree (including this parent) on write.

    def _guild_dir(self, guild_id: int) -> str:
        guild_dir = os.path.join(self._dir, str(guild_id))
        os.makedirs(guild_dir, exist_ok=True)
        return guild_dir

    def _path(self, guild_id: int, name: str) -> str:
        return os.path.join(self._guild_dir(guild_id), f"{_safe(name)}.json")

    def _sync_sources_path(self, guild_id: int) -> str:
        return os.path.join(self._guild_dir(guild_id), "_sync_sources.json")

    def list_playlists(self, guild_id: int) -> list[str]:
        guild_dir = os.path.join(self._dir, str(guild_id))
        if not os.path.isdir(guild_dir):
            return []
        return [f[:-5] for f in os.listdir(guild_dir) if f.endswith(".json") and not f.startswith("_")]

    def load(self, guild_id: int, name: str) -> list[PlaylistEntry]:
        path = self._path(guild_id, name)
        if not os.path.exists(path):
            return []
        try:
            with open(path) as f:
                return [PlaylistEntry(**e) for e in json.load(f)]
        except Exception as exc:
            logger.warning("playlist_load_failed guild=%s name=%s error=%s", guild_id, name, exc)
            return []

    def save(self, guild_id: int, name: str, entries: list[PlaylistEntry]) -> None:
        path = self._path(guild_id, name)
        try:
            with open(path, "w") as f:
                json.dump([asdict(e) for e in entries], f, indent=2)
        except Exception as exc:
            logger.warning("playlist_save_failed guild=%s name=%s error=%s", guild_id, name, exc)

    def load_sync_sources(self, guild_id: int) -> dict[str, PlaylistSyncSource]:
        path = self._sync_sources_path(guild_id)
        if not os.path.exists(path):
            return {}
        try:
            with open(path) as f:
                raw = json.load(f)
            return {name: PlaylistSyncSource(**value) for name, value in raw.items()}
        except Exception as exc:
            logger.warning("playlist_sync_sources_load_failed guild=%s error=%s", guild_id, exc)
            return {}

    def save_sync_sources(self, guild_id: int, sources: dict[str, PlaylistSyncSource]) -> None:
        path = self._sync_sources_path(guild_id)
        try:
            with open(path, "w") as f:
                json.dump({name: asdict(source) for name, source in sources.items()}, f, indent=2)
        except Exception as exc:
            logger.warning("playlist_sync_sources_save_failed guild=%s error=%s", guild_id, exc)

    def set_sync_source(self, guild_id: int, name: str, url: str, source: str) -> None:
        sources = self.load_sync_sources(guild_id)
        sources[_safe(name)] = PlaylistSyncSource(url=url, source=source)
        self.save_sync_sources(guild_id, sources)

    def mark_sync_success(self, guild_id: int, name: str) -> None:
        sources = self.load_sync_sources(guild_id)
        source = sources.get(_safe(name))
        if source is None:
            return
        source.last_synced_at = time.time()
        source.last_error = None
        self.save_sync_sources(guild_id, sources)

    def mark_sync_error(self, guild_id: int, name: str, error: str) -> None:
        sources = self.load_sync_sources(guild_id)
        source = sources.get(_safe(name))
        if source is None:
            return
        source.last_error = error
        self.save_sync_sources(guild_id, sources)

    def clear_sync_source(self, guild_id: int, name: str) -> None:
        sources = self.load_sync_sources(guild_id)
        if sources.pop(_safe(name), None) is not None:
            self.save_sync_sources(guild_id, sources)

    def add(self, guild_id: int, name: str, entry: PlaylistEntry) -> int:
        entries = self.load(guild_id, name)
        entries.append(entry)
        self.save(guild_id, name, entries)
        return len(entries)

    def remove(self, guild_id: int, name: str, index: int) -> Optional[PlaylistEntry]:
        entries = self.load(guild_id, name)
        if index < 1 or index > len(entries):
            return None
        removed = entries.pop(index - 1)
        self.save(guild_id, name, entries)
        return removed

    def delete_playlist(self, guild_id: int, name: str) -> bool:
        path = self._path(guild_id, name)
        if not os.path.exists(path):
            return False
        os.remove(path)
        self.clear_sync_source(guild_id, name)
        return True

    def clear(self, guild_id: int, name: str) -> int:
        entries = self.load(guild_id, name)
        self.save(guild_id, name, [])
        return len(entries)
