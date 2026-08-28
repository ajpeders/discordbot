import re
from enum import Enum, auto

class SourceType(Enum):
    YOUTUBE = auto()
    YOUTUBE_PLAYLIST = auto()
    LOCAL = auto()
    DIRECT_URL = auto()
    SPOTIFY = auto()
    SPOTIFY_PLAYLIST = auto()
    APPLE_MUSIC = auto()
    APPLE_MUSIC_PLAYLIST = auto()
    SOUNDCLOUD = auto()
    SOUNDCLOUD_PLAYLIST = auto()
    SEARCH = auto()

_AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".ogg", ".aac", ".m4a"}
_YOUTUBE_RE = re.compile(r"(youtube\.com/|youtu\.be/)")
_YOUTUBE_LIST_RE = re.compile(r"[?&]list=")
_SPOTIFY_RE = re.compile(r"open\.spotify\.com/(track|album|playlist)/")
_SPOTIFY_COLLECTION_RE = re.compile(r"open\.spotify\.com/(album|playlist)/")
_APPLE_MUSIC_RE = re.compile(r"music\.apple\.com/")
_APPLE_MUSIC_SONG_RE = re.compile(r"music\.apple\.com/[^/]+/song/")
_APPLE_MUSIC_COLLECTION_RE = re.compile(r"music\.apple\.com/[^/]+/(album|playlist)/")
_SOUNDCLOUD_RE = re.compile(r"soundcloud\.com/")
_SOUNDCLOUD_SET_RE = re.compile(r"soundcloud\.com/[^/]+/sets/")
_URL_RE = re.compile(r"^https?://")

def detect_source(query: str) -> SourceType:
    q = query.strip()
    if _YOUTUBE_RE.search(q):
        if _YOUTUBE_LIST_RE.search(q):
            return SourceType.YOUTUBE_PLAYLIST
        return SourceType.YOUTUBE
    if _SPOTIFY_RE.search(q):
        if _SPOTIFY_COLLECTION_RE.search(q):
            return SourceType.SPOTIFY_PLAYLIST
        return SourceType.SPOTIFY
    if _APPLE_MUSIC_RE.search(q):
        if _APPLE_MUSIC_SONG_RE.search(q):
            return SourceType.APPLE_MUSIC
        if "?i=" in q or "&i=" in q:
            return SourceType.APPLE_MUSIC
        if _APPLE_MUSIC_COLLECTION_RE.search(q):
            return SourceType.APPLE_MUSIC_PLAYLIST
        return SourceType.APPLE_MUSIC
    if _SOUNDCLOUD_RE.search(q):
        if _SOUNDCLOUD_SET_RE.search(q):
            return SourceType.SOUNDCLOUD_PLAYLIST
        return SourceType.SOUNDCLOUD
    # An http(s) link is always a remote stream, even when it ends in an audio
    # extension (e.g. a direct download URL). Only treat extension matches as
    # local files when the query is not a URL.
    if _URL_RE.match(q):
        return SourceType.DIRECT_URL
    lower = q.lower()
    for ext in _AUDIO_EXTENSIONS:
        if lower.endswith(ext):
            return SourceType.LOCAL
    return SourceType.SEARCH
