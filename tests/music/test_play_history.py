

from music.play_history import PlayHistoryStore
from music.track import Track


def _track(title="A"):
    return Track(
        title=title,
        url=f"https://x/{title}",
        source="youtube",
        duration=180,
        requester="alex",
        thumbnail="https://thumb",
    )


def test_record_and_read_newest_first(tmp_path):
    store = PlayHistoryStore(str(tmp_path))
    store.record(42, _track("A"), ts=1.0)
    store.record(42, _track("B"), ts=2.0)
    store.record(42, _track("C"), ts=3.0)
    rows = store.recent(42)
    assert [r["title"] for r in rows] == ["C", "B", "A"]
    assert rows[0]["ts"] == 3.0
    assert rows[0]["thumbnail"] == "https://thumb"


def test_limit_and_offset(tmp_path):
    store = PlayHistoryStore(str(tmp_path))
    for i, title in enumerate(["A", "B", "C", "D", "E"]):
        store.record(7, _track(title), ts=float(i))
    page1 = store.recent(7, limit=2)
    page2 = store.recent(7, limit=2, offset=2)
    assert [r["title"] for r in page1] == ["E", "D"]
    assert [r["title"] for r in page2] == ["C", "B"]


def test_guilds_are_isolated(tmp_path):
    store = PlayHistoryStore(str(tmp_path))
    store.record(1, _track("one"))
    store.record(2, _track("two"))
    assert [r["title"] for r in store.recent(1)] == ["one"]
    assert [r["title"] for r in store.recent(2)] == ["two"]


def test_missing_guild_returns_empty(tmp_path):
    store = PlayHistoryStore(str(tmp_path))
    assert store.recent(999) == []


def test_corrupted_line_is_skipped(tmp_path):
    store = PlayHistoryStore(str(tmp_path))
    store.record(5, _track("A"), ts=1.0)
    # Append a garbage line manually.
    with open(store._path(5), "a", encoding="utf-8") as f:
        f.write("not-valid-json\n")
    store.record(5, _track("B"), ts=2.0)
    rows = store.recent(5)
    assert [r["title"] for r in rows] == ["B", "A"]
