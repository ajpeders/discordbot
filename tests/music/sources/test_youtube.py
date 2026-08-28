import pytest
from unittest.mock import patch
from music.sources.youtube import resolve_stream_url, resolve_youtube_track
from music.track import Track

@pytest.mark.asyncio
async def test_resolve_youtube_track():
    mock_info = {
        "title": "Never Gonna Give You Up",
        "webpage_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "duration": 212,
        "url": "https://stream.example/audio",
        "thumbnail": "https://img.example/thumb.jpg",
    }
    with patch("music.sources.youtube._extract", return_value=mock_info):
        track = await resolve_youtube_track("https://youtube.com/watch?v=dQw4w9WgXcQ", requester="Alice")
    assert track.title == "Never Gonna Give You Up"
    assert track.source == "youtube"
    assert track.stream_url is None  # resolved lazily at play time
    assert track.thumbnail == "https://img.example/thumb.jpg"

@pytest.mark.asyncio
async def test_resolve_youtube_track_returns_none_on_failure():
    with patch("music.sources.youtube._extract", return_value=None):
        track = await resolve_youtube_track("https://youtube.com/watch?v=invalid", requester="Alice")
    assert track is None


@pytest.mark.asyncio
async def test_resolve_stream_url_returns_stream_and_thumbnail():
    mock_info = {
        "title": "Never Gonna Give You Up",
        "webpage_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "url": "https://stream.example/audio",
        "thumbnail": "https://img.example/thumb.jpg",
    }
    track = Track(
        title="Never Gonna Give You Up",
        url="https://youtube.com/watch?v=dQw4w9WgXcQ",
        source="youtube",
        duration=212,
        requester="Alice",
    )
    with patch("music.sources.youtube._extract", return_value=mock_info):
        stream_url, thumbnail = await resolve_stream_url(track)
    assert stream_url == "https://stream.example/audio"
    assert thumbnail == "https://img.example/thumb.jpg"
