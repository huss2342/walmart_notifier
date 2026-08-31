"""Dedupe store semantics, exercised against the in-memory fallback."""

from state import SeenStore


def make_store():
    # No connection string -> in-memory dedupe, no Azure dependency.
    return SeenStore(connection_string="")


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
