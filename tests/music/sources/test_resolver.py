from music.sources.resolver import detect_source, SourceType

def test_detect_youtube_url():
    assert detect_source("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == SourceType.YOUTUBE
    assert detect_source("https://youtu.be/dQw4w9WgXcQ") == SourceType.YOUTUBE

def test_detect_local_file():
    assert detect_source("song.mp3") == SourceType.LOCAL
    assert detect_source("album/track.flac") == SourceType.LOCAL
    assert detect_source("my song.wav") == SourceType.LOCAL

def test_detect_plain_text_search():
    assert detect_source("bohemian rhapsody") == SourceType.SEARCH
    assert detect_source("queen we will rock you") == SourceType.SEARCH


def test_detect_youtube_playlist():
    assert detect_source("https://www.youtube.com/playlist?list=PLabc") == SourceType.YOUTUBE_PLAYLIST
    assert detect_source("https://www.youtube.com/watch?v=xyz&list=PLabc") == SourceType.YOUTUBE_PLAYLIST


def test_detect_spotify():
    assert detect_source("https://open.spotify.com/track/abc") == SourceType.SPOTIFY
    assert detect_source("https://open.spotify.com/album/abc") == SourceType.SPOTIFY_PLAYLIST
    assert detect_source("https://open.spotify.com/playlist/abc") == SourceType.SPOTIFY_PLAYLIST


def test_detect_apple_music():
    assert detect_source("https://music.apple.com/us/song/foo/123") == SourceType.APPLE_MUSIC
    assert detect_source("https://music.apple.com/us/album/foo/123?i=456") == SourceType.APPLE_MUSIC
    assert detect_source("https://music.apple.com/us/album/foo/123") == SourceType.APPLE_MUSIC_PLAYLIST
    assert detect_source("https://music.apple.com/us/playlist/foo/pl.abc") == SourceType.APPLE_MUSIC_PLAYLIST


def test_detect_direct_download_url():
    # http(s) links are remote streams even when they end in an audio extension.
    assert detect_source("https://cdn.example.com/track.mp3") == SourceType.DIRECT_URL
    assert detect_source("https://example.com/audio.flac") == SourceType.DIRECT_URL
    assert detect_source("http://example.com/stream") == SourceType.DIRECT_URL
    # bare filenames remain local
    assert detect_source("track.mp3") == SourceType.LOCAL


def test_detect_soundcloud():
    assert detect_source("https://soundcloud.com/artist/track") == SourceType.SOUNDCLOUD
    assert detect_source("https://soundcloud.com/artist/track?si=abc") == SourceType.SOUNDCLOUD
    assert detect_source("https://soundcloud.com/artist/sets/album-name") == SourceType.SOUNDCLOUD_PLAYLIST
