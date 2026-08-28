from music.track import Track


def test_track_creation():
    t = Track(
        title="Bohemian Rhapsody",
        url="https://youtube.com/watch?v=abc",
        source="youtube",
        duration=354,
        requester="Alice",
    )
    assert t.title == "Bohemian Rhapsody"
    assert t.source == "youtube"
    assert t.stream_url is None


def test_track_with_stream_url():
    t = Track(
        title="local.mp3",
        url="/music/local.mp3",
        source="local",
        duration=200,
        requester="Bob",
        stream_url="/music/local.mp3",
    )
    assert t.stream_url == "/music/local.mp3"


def test_track_needs_resolution():
    t = Track(title="x", url="x", source="youtube", duration=None, requester="u")
    assert t.needs_resolution is True
    t2 = Track(title="x", url="x", source="local", duration=None, requester="u", stream_url="/f.mp3")
    assert t2.needs_resolution is False
