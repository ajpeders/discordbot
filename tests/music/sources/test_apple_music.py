from unittest.mock import patch

from music.sources.apple_music import resolve_apple_music


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def json(self, *args, **kwargs):
        return self._payload


def _fake_get(payload, status=200, captured_params=None):
    """Return a replacement for aiohttp.ClientSession.get that yields a fake response."""

    def _get(self, url, params=None, **kwargs):
        if captured_params is not None:
            captured_params.clear()
            captured_params.update(params or {})
        return _FakeResponse(payload, status=status)

    return _get


async def test_album_url_resolves_to_multiple_track_queries():
    payload = {
        "resultCount": 3,
        "results": [
            {"wrapperType": "collection", "collectionName": "Take Care (Deluxe)"},
            {"wrapperType": "track", "trackName": "Over My Dead Body", "artistName": "Drake"},
            {"wrapperType": "track", "trackName": "Shot for Me", "artistName": "Drake"},
        ],
    }
    with patch("aiohttp.ClientSession.get", _fake_get(payload)):
        queries, err = await resolve_apple_music(
            "https://music.apple.com/us/album/take-care-deluxe-version/1440642493"
        )
    assert err is None
    assert queries == ["Over My Dead Body Drake", "Shot for Me Drake"]


async def test_song_url_resolves_to_single_query():
    payload = {
        "resultCount": 1,
        "results": [
            {"wrapperType": "track", "trackName": "Over My Dead Body", "artistName": "Drake"},
        ],
    }
    captured: dict = {}
    with patch("aiohttp.ClientSession.get", _fake_get(payload, captured_params=captured)):
        queries, err = await resolve_apple_music(
            "https://music.apple.com/us/song/over-my-dead-body/1440642618"
        )
    assert err is None
    assert queries == ["Over My Dead Body Drake"]
    # Song lookup should not request the album entity expansion.
    assert captured.get("id") == "1440642618"
    assert "entity" not in captured


async def test_i_param_overrides_album_path():
    payload = {
        "resultCount": 1,
        "results": [
            {"wrapperType": "track", "trackName": "Over My Dead Body", "artistName": "Drake"},
        ],
    }
    captured: dict = {}
    with patch("aiohttp.ClientSession.get", _fake_get(payload, captured_params=captured)):
        queries, err = await resolve_apple_music(
            "https://music.apple.com/us/album/take-care-deluxe-version/1440642493?i=1440642618"
        )
    assert err is None
    assert queries == ["Over My Dead Body Drake"]
    # The song id from ?i= must win over the album id in the path.
    assert captured.get("id") == "1440642618"
    assert "entity" not in captured


async def test_playlist_url_returns_unsupported_error():
    queries, err = await resolve_apple_music(
        "https://music.apple.com/us/playlist/foo/pl.abc123"
    )
    assert queries == []
    assert err is not None
    assert "playlist" in err.lower()
    assert "album" in err.lower()


async def test_unrecognized_apple_music_url_returns_error():
    queries, err = await resolve_apple_music(
        "https://music.apple.com/us/artist/some-artist/12345"
    )
    assert queries == []
    assert err is not None
    assert "unrecognized" in err.lower()
