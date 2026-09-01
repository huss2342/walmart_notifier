"""Dedupe store semantics, exercised against the in-memory fallback."""

from state import SeenStore


def make_store():
    # Empty path -> in-memory only, nothing written to disk.
    return SeenStore("")


def test_claim_succeeds_once():
    store = make_store()
    assert store.claim("ip-1", "TV") is True
    assert store.claim("ip-1", "TV") is False


def test_release_lets_a_later_run_reclaim():
    store = make_store()
    store.claim("ip-1")
    store.release("ip-1")
    assert store.claim("ip-1") is True


def test_release_of_an_unclaimed_item_is_harmless():
    make_store().release("never-seen")


def test_mark_seen_blocks_is_new():
    store = make_store()
    assert store.is_new("ip-1") is True
    store.mark_seen("ip-1", "TV")
    assert store.is_new("ip-1") is False


def test_claim_also_blocks_is_new():
    store = make_store()
    store.claim("ip-1", "TV")
    assert store.is_new("ip-1") is False


def test_markers_round_trip_and_default_to_none():
    store = make_store()
    assert store.get_marker("imap-you") is None
    store.set_marker("imap-you", "12:42")
    assert store.get_marker("imap-you") == "12:42"
    store.set_marker("imap-you", "12:99")
    assert store.get_marker("imap-you") == "12:99"


def test_markers_and_items_do_not_collide():
    store = make_store()
    store.set_marker("ip-1", "12:42")
    assert store.is_new("ip-1") is True


# --- file persistence --------------------------------------------------------


def test_state_survives_a_restart(tmp_path):
    path = tmp_path / "seen.json"
    SeenStore(path).claim("ip-1", "TV")
    assert SeenStore(path).is_new("ip-1") is False


def test_release_is_persisted(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    store.claim("ip-1", "TV")
    store.release("ip-1")
    assert SeenStore(path).is_new("ip-1") is True


def test_markers_survive_a_restart(tmp_path):
    path = tmp_path / "seen.json"
    SeenStore(path).set_marker("page", "42")
    assert SeenStore(path).get_marker("page") == "42"


def test_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "nested" / "deeper" / "seen.json"
    SeenStore(path).claim("ip-1")
    assert path.is_file()


def test_corrupt_file_starts_empty_instead_of_crashing(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{ this is not json", encoding="utf-8")
    store = SeenStore(path)
    assert len(store) == 0
    # And it recovers: the next write produces a valid file.
    store.claim("ip-1", "TV")
    assert SeenStore(path).is_new("ip-1") is False


def test_non_object_file_starts_empty(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert len(SeenStore(path)) == 0


def test_no_temp_file_is_left_behind(tmp_path):
    path = tmp_path / "seen.json"
    SeenStore(path).claim("ip-1")
    assert [p.name for p in tmp_path.iterdir()] == ["seen.json"]


def test_store_is_trimmed_to_the_cap(tmp_path, monkeypatch):
    import state as state_module
    monkeypatch.setattr(state_module, "MAX_ENTRIES", 5)

    store = SeenStore(tmp_path / "seen.json", autosave=False)
    for i in range(12):
        store.claim(f"ip-{i:03d}", f"item {i}")
    store.save()

    reloaded = SeenStore(tmp_path / "seen.json")
    assert len(reloaded) == 5
    # The newest survive; the oldest are dropped.
    assert reloaded.is_new("ip-011") is False
    assert reloaded.is_new("ip-000") is True


def test_memory_mode_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = SeenStore("")
    store.claim("ip-1", "TV")
    assert store.path is None
    assert list(tmp_path.iterdir()) == []


def test_value_is_recorded_alongside_the_item():
    """Without it, "never arrived" and "arrived but filtered" look identical."""
    store = make_store()
    store.mark_seen("ip-1", "PVC reamer", 79.99)
    assert store._seen["ip-1"]["value_usd"] == 79.99


def test_claim_records_the_value_too():
    store = make_store()
    store.claim("ip-1", "ADT doorbell", 149.5)
    assert store._seen["ip-1"]["value_usd"] == 149.5


def test_value_defaults_to_none_when_unknown():
    store = make_store()
    store.mark_seen("ip-1", "Mystery item")
    assert store._seen["ip-1"]["value_usd"] is None
