"""Tests for the incremental IMAP reader.

The polling loop runs every two minutes, so the difference between "fetch what
is new" and "fetch the last 25 messages again" is roughly a gigabyte a day of
Gmail bandwidth. These tests pin the parts that make it incremental.
"""

from datetime import UTC, datetime
from email.message import EmailMessage

import pytest

from sources.imap_source import ImapSource, _imap_date
from state import SeenStore


class FakeConn:
    """Just enough IMAP to exercise search, fetch and marker handling."""

    def __init__(self, per_sender: dict[str, list[int]], bodies: dict[int, bytes] | None = None):
        self.per_sender = per_sender
        self.bodies = bodies or {}
        self.searches: list[tuple] = []
        self.fetched: list[int] = []

    def uid(self, command, *args):
        if command == "SEARCH":
            # args: (None, "FROM", '"sender"', *window)
            sender = args[2].strip('"')
            self.searches.append(args[3:])
            uids = self.per_sender.get(sender, [])
            return "OK", [b" ".join(str(u).encode() for u in uids)] if uids else [b""]
        if command == "FETCH":
            uid = int(args[0])
            self.fetched.append(uid)
            body = self.bodies.get(uid)
            if body is None:
                return "NO", [None]
            return "OK", [(b"1 (BODY[] {1})", body)]
        raise AssertionError(f"unexpected IMAP command {command}")


def make_source(store=None, **kw):
    return ImapSource(
        host="imap.example.com", user="you@example.com", password="pw",
        store=store, **kw
    )


def message_with(url: str, price: str) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = "New items to review"
    msg["From"] = "noreply@walmart.com"
    msg.set_content(f"{url} {price}")
    return msg.as_bytes()


# --- search ------------------------------------------------------------------


def test_search_sorts_numerically_across_senders():
    """Concatenating per-sender results and slicing the tail is not 'newest'.

    Without a numeric sort the tail is whichever sender was searched last, so a
    mailbox with 500 Walmart mails and 3 Bazaarvoice ones would keep re-reading
    the same three old messages and miss the recent ones.
    """
    conn = FakeConn({"walmart.com": [10, 11, 12, 500], "bazaarvoice.com": [3, 4]})
    source = make_source(lookback=3)

    assert source._search(conn, last_uid=0) == [11, 12, 500]


def test_search_ignores_uids_at_or_below_the_marker():
    # `UID n:*` still returns the newest message when n is past the end of the
    # mailbox, so the server's answer always needs filtering.
    conn = FakeConn({"walmart.com": [7], "bazaarvoice.com": []})
    source = make_source()

    assert source._search(conn, last_uid=7) == []
    assert source._search(conn, last_uid=6) == [7]


def test_first_run_uses_a_dated_backfill_window():
    conn = FakeConn({"walmart.com": [1]})
    make_source(backfill_days=2)._search(conn, last_uid=0)
    assert conn.searches[0][0] == "SINCE"


def test_later_runs_ask_only_for_newer_uids():
    conn = FakeConn({"walmart.com": [1]})
    make_source()._search(conn, last_uid=42)
    assert conn.searches[0] == ("UID", "43:*")


def test_imap_date_is_locale_independent():
    assert _imap_date(datetime(2026, 3, 7, tzinfo=UTC)) == "07-Mar-2026"


# --- marker ------------------------------------------------------------------


@pytest.fixture
def store():
    return SeenStore(connection_string="")


def test_marker_round_trips(store):
    source = make_source(store=store)
    source.pending_marker = "99:42"
    source.commit_marker()

    assert make_source(store=store)._read_marker() == (99, 42)


def test_marker_is_not_written_until_committed(store):
    source = make_source(store=store)
    source.pending_marker = "99:42"
    assert make_source(store=store)._read_marker() == (0, 0)


def test_garbage_marker_falls_back_to_a_full_window(store):
    store.set_marker(make_source(store=store).marker_key, "not-a-marker")
    assert make_source(store=store)._read_marker() == (0, 0)


def test_uidvalidity_change_restarts_from_the_backfill_window(store):
    """UIDs are only comparable within one UIDVALIDITY generation."""
    source = make_source(store=store)
    source.pending_marker = "100:50"
    source.commit_marker()

    conn = FakeConn({"walmart.com": [1, 2]}, bodies={})
    list(make_source(store=store)._messages_to_items(conn, uidvalidity=999))

    # A UID window would have asked for 51:*; a reset asks by date instead.
    assert conn.searches[0][0] == "SINCE"


# --- end to end --------------------------------------------------------------


def test_only_new_messages_are_fetched_and_the_marker_advances(store):
    url = "https://www.walmart.com/ip/Test-Item/123456789"
    conn = FakeConn(
        {"walmart.com": [5, 6, 7]},
        bodies={6: message_with(url, "$199.99"), 7: message_with(url, "$199.99")},
    )
    source = make_source(store=store)
    source.pending_marker = "12:5"
    source.commit_marker()

    items = list(make_source(store=store)._messages_to_items(conn, uidvalidity=12))

    assert conn.fetched == [6, 7]  # 5 is already behind the marker
    assert items and items[0].item_id == "ip-123456789"


def test_marker_holds_when_nothing_new_arrives(store):
    conn = FakeConn({"walmart.com": []})
    source = make_source(store=store)

    assert list(source._messages_to_items(conn, uidvalidity=12)) == []
    assert source.pending_marker is None


def test_unfetchable_message_does_not_stall_the_marker(store):
    conn = FakeConn({"walmart.com": [8, 9]}, bodies={})  # every FETCH returns NO
    source = make_source(store=store)

    assert list(source._messages_to_items(conn, uidvalidity=12)) == []
    # Otherwise a single permanently-broken message blocks the mailbox forever.
    assert source.pending_marker == "12:9"
