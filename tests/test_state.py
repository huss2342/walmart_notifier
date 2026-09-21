"""Dedupe store semantics, exercised against the in-memory fallback."""

import json
from datetime import UTC, datetime, timedelta

import pytest

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


def test_pending_claim_is_new_but_cannot_be_claimed_twice():
    store = make_store()
    assert store.claim("ip-1", "TV") is True
    # It has not been delivered/recorded yet, so a concurrent pipeline must
    # distinguish it from a completed duplicate and return a retry signal.
    assert store.is_new("ip-1") is True
    assert store.claim("ip-1", "TV") is False


def test_commit_records_a_pending_claim():
    store = make_store()
    assert store.claim("ip-1", "TV") is True
    assert store.commit("ip-1") is True
    assert store.is_new("ip-1") is False
    assert store.commit("ip-1") is False


def test_mark_seen_cannot_overwrite_an_in_flight_delivery():
    store = make_store()
    assert store.claim("ip-1", "TV", 500.0) is True
    assert store.is_pending("ip-1") is True
    assert store.mark_seen("ip-1", "TV", 500.0) is False
    assert store.is_new("ip-1") is True


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
    store = SeenStore(path)
    store.claim("ip-1", "TV")
    store.commit("ip-1")
    store.save()
    assert SeenStore(path).is_new("ip-1") is False


def test_pending_claim_does_not_survive_a_restart(tmp_path):
    path = tmp_path / "seen.json"
    SeenStore(path).claim("ip-1", "TV")
    # A process crash during delivery must allow a retry after restart. This
    # can duplicate an alert if delivery beat the crash, but cannot miss one.
    assert SeenStore(path).is_new("ip-1") is True


def test_release_is_persisted(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    store.claim("ip-1", "TV")
    store.release("ip-1")
    assert SeenStore(path).is_new("ip-1") is True


def test_markers_survive_a_restart(tmp_path):
    path = tmp_path / "seen.json"
    writer = SeenStore(path)
    writer.set_marker("page", "42")
    writer.save()
    assert SeenStore(path).get_marker("page") == "42"


def test_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "nested" / "deeper" / "seen.json"
    store = SeenStore(path)
    store.claim("ip-1")
    store.commit("ip-1")
    store.save()
    assert path.is_file()


def test_corrupt_file_starts_empty_instead_of_crashing(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{ this is not json", encoding="utf-8")
    store = SeenStore(path)
    assert len(store) == 0
    # And it recovers: the next write produces a valid file.
    store.claim("ip-1", "TV")
    store.commit("ip-1")
    store.save()
    assert SeenStore(path).is_new("ip-1") is False


def test_non_object_file_starts_empty(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert len(SeenStore(path)) == 0


def test_no_temp_file_is_left_behind(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    store.claim("ip-1")
    store.commit("ip-1")
    store.save()
    assert [p.name for p in tmp_path.iterdir()] == ["seen.json"]


def test_store_is_trimmed_to_the_cap(tmp_path, monkeypatch):
    import state as state_module
    monkeypatch.setattr(state_module, "MAX_ENTRIES", 5)

    store = SeenStore(tmp_path / "seen.json", autosave=False)
    for i in range(12):
        item_id = f"ip-{i:03d}"
        store.claim(item_id, f"item {i}")
        store.commit(item_id)
    store.save()

    reloaded = SeenStore(tmp_path / "seen.json")
    assert len(reloaded) == 5
    # The newest survive; the oldest are dropped.
    assert reloaded.is_new("ip-011") is False
    assert reloaded.is_new("ip-000") is True


def test_trimming_tolerates_parseable_legacy_records(tmp_path, monkeypatch):
    import state as state_module
    monkeypatch.setattr(state_module, "MAX_ENTRIES", 2)

    store = SeenStore(tmp_path / "seen.json", autosave=False)
    now = datetime.now(UTC)
    store._seen = {
        "legacy": "not-an-object",
        "old": {"seen_at": (now - timedelta(days=2)).isoformat(timespec="seconds")},
        "new": {"seen_at": now.isoformat(timespec="seconds")},
    }

    store.save()

    assert len(SeenStore(tmp_path / "seen.json")) == 2


def test_memory_mode_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = SeenStore("")
    store.claim("ip-1", "TV")
    store.commit("ip-1")
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
    assert store._pending["ip-1"]["value_usd"] == 149.5
    store.commit("ip-1")
    assert store._seen["ip-1"]["value_usd"] == 149.5


def test_value_defaults_to_none_when_unknown():
    store = make_store()
    store.mark_seen("ip-1", "Mystery item")
    assert store._seen["ip-1"]["value_usd"] is None


def test_observed_value_stats_are_aggregate_only():
    store = make_store()
    store.mark_seen("ip-1", "Cheap", 2.69)
    store.mark_seen("ip-2", "Expensive", 49.99)
    store.mark_seen("ip-3", "Mystery")

    assert store.observed_value_stats() == {
        "value_known": 2,
        "value_unknown": 1,
        "min_value_usd": 2.69,
        "max_value_usd": 49.99,
    }


def test_observed_value_stats_tolerate_malformed_records():
    store = make_store()
    store._seen = {
        "bad-record": "not an object",
        "bad-value": {"value_usd": "lots"},
        "not-finite": {"value_usd": float("nan")},
        "overflow": {"value_usd": 10**1_000},
        "good": {"value_usd": 12},
    }

    assert store.observed_value_stats() == {
        "value_known": 1,
        "value_unknown": 4,
        "min_value_usd": 12.0,
        "max_value_usd": 12.0,
    }


# --- retention and write batching -------------------------------------------


def _aged(days):
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


def test_records_older_than_the_retention_window_are_dropped(tmp_path, monkeypatch):
    """A count cap alone set the horizon to whatever churn implied.

    At 20,000 entries that had quietly become about three days, so an item
    still listed in the portal could be trimmed and alert again as if new.
    """
    monkeypatch.setenv("RETENTION_DAYS", "30")
    path = tmp_path / "seen.json"
    store = SeenStore(path, autosave=False)
    store._seen = {
        "fresh": {"seen_at": _aged(1), "value_usd": 1.0},
        "recent": {"seen_at": _aged(29), "value_usd": 2.0},
        "stale": {"seen_at": _aged(31), "value_usd": 3.0},
        "ancient": {"seen_at": _aged(400), "value_usd": 4.0},
    }
    store.save()

    reloaded = SeenStore(path)
    assert reloaded.is_new("fresh") is False
    assert reloaded.is_new("recent") is False
    assert reloaded.is_new("stale") is True
    assert reloaded.is_new("ancient") is True


def test_expired_records_are_pruned_on_load(tmp_path, monkeypatch):
    monkeypatch.setenv("RETENTION_DAYS", "10")
    path = tmp_path / "seen.json"
    path.write_text(json.dumps({
        "seen": {"old": {"seen_at": _aged(11)}, "new": {"seen_at": _aged(1)}},
        "markers": {},
    }), encoding="utf-8")

    assert len(SeenStore(path)) == 1


def test_a_record_without_a_timestamp_is_not_immortal(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path, autosave=False)
    store._seen = {"undated": {"title": "no timestamp"}}
    store.save()
    assert len(SeenStore(path)) == 0


@pytest.mark.parametrize("value,expected", [
    ("30", 30), ("1", 1), ("", 60), ("junk", 60), ("0", 60), ("-5", 60),
])
def test_retention_window_comes_from_the_environment(monkeypatch, value, expected):
    import state as state_module
    monkeypatch.setenv("RETENTION_DAYS", value)
    assert state_module.retention_days() == expected


def test_writes_are_batched_rather_than_one_per_item(tmp_path):
    """Saving per item rewrote the entire file for every single record."""
    path = tmp_path / "seen.json"
    store = SeenStore(path, autosave=False)
    saves = []
    original = store.save

    def counting_save():
        saves.append(1)
        original()

    store.save = counting_save
    for index in range(50):
        store.claim(f"ip-{index}")
        store.commit(f"ip-{index}")
    assert saves == []          # nothing written yet

    store.save()
    assert len(saves) == 1      # one write for all fifty
    assert len(SeenStore(path)) == 50


def test_titles_are_truncated_so_they_do_not_dominate_the_file():
    store = make_store()
    store.mark_seen("ip-1", "x" * 500, 9.99)
    assert len(store._seen["ip-1"]["title"]) == 120


def test_close_flushes_and_stops_the_background_writer(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    store.mark_seen("ip-1", "TV", 5.0)
    store.close()

    assert SeenStore(path).is_new("ip-1") is False
    assert store._flusher is None


# --- append-only log ---------------------------------------------------------


def test_new_records_are_appended_not_rewritten(tmp_path):
    """The hot path must cost what is new, not what is stored."""
    path = tmp_path / "seen.json"
    store = SeenStore(path, autosave=False)
    for index in range(500):
        store.mark_seen(f"ip-{index}", "x" * 50, 1.0)
    store.save()                      # snapshot the 500
    snapshot_size = path.stat().st_size

    store.mark_seen("ip-new", "one more", 2.0)
    store.flush()

    # The snapshot is untouched; only the log grew, by roughly one record.
    assert path.stat().st_size == snapshot_size
    assert store.log_path.exists()
    assert store.log_path.stat().st_size < snapshot_size // 100


def test_appended_records_survive_a_restart(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path, autosave=False)
    store.mark_seen("ip-1", "TV", 5.0)
    store.set_marker("page", "42")
    store.flush()

    reloaded = SeenStore(path)
    assert reloaded.is_new("ip-1") is False
    assert reloaded.get_marker("page") == "42"


def test_a_torn_final_line_does_not_lose_earlier_records(tmp_path):
    """Appending without fsync can leave a partial last line after a kill."""
    path = tmp_path / "seen.json"
    store = SeenStore(path, autosave=False)
    store.mark_seen("ip-1", "first", 1.0)
    store.mark_seen("ip-2", "second", 2.0)
    store.flush()
    with store.log_path.open("a", encoding="utf-8") as handle:
        handle.write('{"t":"item","k":"ip-3","v":{"seen')

    reloaded = SeenStore(path)
    assert reloaded.is_new("ip-1") is False
    assert reloaded.is_new("ip-2") is False
    assert reloaded.is_new("ip-3") is True


def test_compaction_folds_the_log_into_the_snapshot(tmp_path, monkeypatch):
    import state as state_module
    monkeypatch.setattr(state_module, "MIN_COMPACT_LINES", 10)

    path = tmp_path / "seen.json"
    store = SeenStore(path, autosave=False)
    for index in range(40):
        store.mark_seen(f"ip-{index}", "x", 1.0)
        store.flush()

    # Compaction ran, so the snapshot holds most records and the log is short
    # again. Appending resumes afterwards, so the log is not expected to be
    # absent -- only bounded.
    snapshot = json.loads(path.read_text(encoding="utf-8"))["seen"]
    assert len(snapshot) >= 10
    assert store._log_lines < 40
    # Nothing is lost across the fold.
    assert len(SeenStore(path)) == 40


def test_a_failed_append_keeps_the_records_for_the_next_flush(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path, autosave=False)
    store.mark_seen("ip-1", "TV", 5.0)

    # Make the log unwritable by putting a directory where the file belongs.
    store.log_path.mkdir(parents=True)
    assert store.flush() == 0

    store.log_path.rmdir()
    # The record was not dropped on the floor; a lost one means a later
    # duplicate alert.
    assert store.flush() == 1
    assert SeenStore(path).is_new("ip-1") is False


def test_compaction_removes_the_log_only_after_the_snapshot_lands(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path, autosave=False)
    store.mark_seen("ip-1", "TV", 5.0)
    store.flush()
    assert store.log_path.exists()

    store.save()
    assert not store.log_path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["seen"]["ip-1"]["title"] == "TV"
