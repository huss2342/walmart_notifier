import pytest

from filters import Rule
from models import Item
from pipeline import process
from state import SeenStore


class FakeNotifier:
    def __init__(self, succeed=True):
        self.succeed = succeed
        self.sent: list[tuple[str, str]] = []

    def send(self, item, priority="normal"):
        self.sent.append((item.item_id, priority))
        return self.succeed


@pytest.fixture
def store():
    # No connection string -> in-memory dedupe, no Azure dependency.
    return SeenStore(connection_string="")


@pytest.fixture
def rules():
    return [Rule(name="big", min_value_usd=100, priority="high")]


def test_matching_item_notifies_once(store, rules):
    notifier = FakeNotifier()
    items = [Item(title="TV", value_usd=500.0, url="https://www.walmart.com/ip/TV/123456789")]

    first = process(items, rules, store, notifier)
    assert first.notified == 1

    second = process(items, rules, store, notifier)
    assert second.new == 0 and second.notified == 0
    assert len(notifier.sent) == 1


def test_rule_priority_is_passed_through(store, rules):
    notifier = FakeNotifier()
    process([Item(title="TV", value_usd=500.0)], rules, store, notifier)
    assert notifier.sent[0][1] == "high"


def test_non_matching_item_is_marked_seen_but_not_sent(store):
    notifier = FakeNotifier()
    rules = [Rule(min_value_usd=100, alert_on_unknown_value=False)]
    items = [Item(title="Towel", value_usd=9.0)]

    summary = process(items, rules, store, notifier)
    assert summary.new == 1 and summary.matched == 0 and summary.notified == 0
    assert notifier.sent == []

    # Marked seen so that loosening the rule later does not replay old listings.
    assert process(items, rules, store, notifier).new == 0


def test_failed_delivery_is_retried_next_run(store, rules):
    failing = FakeNotifier(succeed=False)
    items = [Item(title="TV", value_usd=500.0)]

    summary = process(items, rules, store, failing)
    assert summary.matched == 1 and summary.notified == 0

    working = FakeNotifier()
    retry = process(items, rules, store, working)
    assert retry.notified == 1


def test_summary_counts_across_mixed_batch(store, rules):
    notifier = FakeNotifier()
    items = [
        Item(title="TV", value_usd=500.0),
        Item(title="Towel", value_usd=9.0),
        Item(title="Laptop", value_usd=899.0),
    ]
    summary = process(items, rules, store, notifier)
    assert summary.as_dict() == {"seen": 3, "new": 3, "matched": 2, "notified": 2}


def test_empty_batch_is_a_no_op(store, rules):
    assert process([], rules, store, FakeNotifier()).as_dict() == {
        "seen": 0, "new": 0, "matched": 0, "notified": 0
    }
