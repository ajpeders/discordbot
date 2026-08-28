import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from music.search.youtube_search import YouTubeSearchProvider
from music.search.youtube_data_api import YouTubeDataAPISearchProvider


@pytest.fixture
def ydl_provider():
    return YouTubeSearchProvider()


@pytest.fixture
def api_provider():
    return YouTubeDataAPISearchProvider(api_key="fake-key")


@pytest.mark.asyncio
async def test_ydl_search_returns_track(ydl_provider):
    mock_info = {
        "title": "Bohemian Rhapsody",
        "webpage_url": "https://youtube.com/watch?v=abc",
        "duration": 354,
        "thumbnail": "https://img.example/thumb.jpg",
    }
    with patch.object(YouTubeSearchProvider, "_extract", return_value=mock_info):
        track = await ydl_provider.search("bohemian rhapsody")
    assert track is not None
    assert track.title == "Bohemian Rhapsody"
    assert track.source == "youtube"
    assert track.stream_url is None
    assert track.thumbnail == "https://img.example/thumb.jpg"


@pytest.mark.asyncio
async def test_ydl_search_returns_none_on_no_results(ydl_provider):
    with patch.object(YouTubeSearchProvider, "_extract", return_value=None):
        track = await ydl_provider.search("asjkdhaskjdhaksjdh")
    assert track is None


@pytest.mark.asyncio
async def test_api_search_returns_track(api_provider):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value={
        "items": [{
            "id": {"videoId": "dQw4w9WgXcQ"},
            "snippet": {
                "title": "Never Gonna Give You Up",
                "thumbnails": {"high": {"url": "https://img.example/high.jpg"}},
            },
        }]
    })
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("music.search.youtube_data_api.aiohttp.ClientSession", return_value=mock_session):
        track = await api_provider.search("never gonna give you up")

    assert track is not None
    assert track.title == "Never Gonna Give You Up"
    assert track.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert track.source == "youtube"
    assert track.stream_url is None
    assert track.thumbnail == "https://img.example/high.jpg"


@pytest.mark.asyncio
async def test_api_search_returns_none_on_no_results(api_provider):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value={"items": []})
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("music.search.youtube_data_api.aiohttp.ClientSession", return_value=mock_session):
        track = await api_provider.search("asjkdhaskjdhaksjdh")

    assert track is None
