import os
import pytest
from music.sources.local import resolve_local_track

@pytest.fixture
def music_dir(tmp_path):
    """Create a temp music directory with a test file."""
    song = tmp_path / "song.mp3"
    song.write_bytes(b"\x00" * 100)
    return str(tmp_path)

def test_resolve_local_track(music_dir):
    track = resolve_local_track("song.mp3", requester="Alice", music_dir=music_dir)
    assert track is not None
    assert track.title == "song.mp3"
    assert track.source == "local"
    assert track.stream_url == os.path.join(music_dir, "song.mp3")
    assert track.duration is None

def test_resolve_local_track_not_found(music_dir):
    track = resolve_local_track("nonexistent.mp3", requester="Alice", music_dir=music_dir)
    assert track is None

def test_resolve_local_track_path_traversal(music_dir):
    track = resolve_local_track("../../../etc/passwd", requester="Alice", music_dir=music_dir)
    assert track is None

def test_resolve_local_track_no_music_dir():
    track = resolve_local_track("song.mp3", requester="Alice", music_dir=None)
    assert track is None
