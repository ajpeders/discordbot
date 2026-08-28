from music.playlist_store import PlaylistEntry, PlaylistStore


def test_sync_sources_are_stored_separately_from_playlists(tmp_path):
    store = PlaylistStore(str(tmp_path))
    store.save(42, "Imported Mix", [PlaylistEntry("Song", "url", "search", "web")])
    store.set_sync_source(42, "Imported Mix", "https://open.spotify.com/playlist/abc", "spotify")

    assert store.list_playlists(42) == ["imported_mix"]

    sources = store.load_sync_sources(42)
    assert sources["imported_mix"].url == "https://open.spotify.com/playlist/abc"
    assert sources["imported_mix"].source == "spotify"

    store.mark_sync_success(42, "Imported Mix")
    sources = store.load_sync_sources(42)
    assert sources["imported_mix"].last_synced_at is not None
    assert sources["imported_mix"].last_error is None


def test_deleting_playlist_clears_sync_source(tmp_path):
    store = PlaylistStore(str(tmp_path))
    store.save(42, "Imported Mix", [PlaylistEntry("Song", "url", "search", "web")])
    store.set_sync_source(42, "Imported Mix", "https://music.apple.com/us/playlist/x", "apple_music")

    assert store.delete_playlist(42, "Imported Mix") is True
    assert store.load_sync_sources(42) == {}
