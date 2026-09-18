"""Source -> filter -> dedupe -> notify."""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass

from filters import Rule, first_match
from models import Item
from notifiers.base import Notifier
from state import SeenStore

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RunSummary:
    seen: int = 0
    new: int = 0
    duplicates: int = 0
    filtered: int = 0
    matched: int = 0
    notified: int = 0
    failed: int = 0
    pending: int = 0
    seeded: int = 0
    value_known: int = 0
    value_unknown: int = 0
    min_value_usd: float | None = None
    max_value_usd: float | None = None

    def observe(self, item: Item) -> None:
        """Record extraction diagnostics before filtering or deduplication."""
        self.seen += 1
        value = item.value_usd
        if (value is None or isinstance(value, bool)
                or not isinstance(value, (int, float))):
            self.value_unknown += 1
            return
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            self.value_unknown += 1
            return
        if not math.isfinite(value):
            self.value_unknown += 1
            return
        self.value_known += 1
        if self.min_value_usd is None or value < self.min_value_usd:
            self.min_value_usd = value
        if self.max_value_usd is None or value > self.max_value_usd:
            self.max_value_usd = value

    def as_dict(self) -> dict[str, int | float | None]:
        return {
            "seen": self.seen,
            "new": self.new,
            "duplicates": self.duplicates,
            "filtered": self.filtered,
            "matched": self.matched,
            "notified": self.notified,
            "failed": self.failed,
            "pending": self.pending,
            "seeded": self.seeded,
            "value_known": self.value_known,
            "value_unknown": self.value_unknown,
            "min_value_usd": self.min_value_usd,
            "max_value_usd": self.max_value_usd,
        }


def process(
    items: Iterable[Item],
    rules: list[Rule],
    store: SeenStore,
    notifier: Notifier,
    seed_only: bool = False,
) -> RunSummary:
    """Run items through the rules and alert on the ones that match.

    `seed_only` records everything as seen and sends nothing. Run it once
    against a fresh dedupe table: without it the first run treats every listing
    already on the page (or in the last N emails) as brand new and fires them
    all at once.
    """
    summary = RunSummary()

    for item in items:
        summary.observe(item)
        if store.is_pending(item.item_id):
            summary.new += 1
            summary.pending += 1
            continue
        if not store.is_new(item.item_id):
            summary.duplicates += 1
            continue
        summary.new += 1

        if seed_only:
            if store.mark_seen(item.item_id, item.title, item.value_usd):
                summary.seeded += 1
            else:
                summary.pending += 1
            continue

        rule = first_match(item, rules)
        if rule is None:
            summary.filtered += 1
            # Record non-matching items too: if a rule is loosened later we do
            # not want a backlog of old listings to fire all at once.
            if not store.mark_seen(item.item_id, item.title, item.value_usd):
                summary.filtered -= 1
                summary.pending += 1
            continue

        summary.matched += 1

        # Claim before notifying: the timer and the ingest endpoint can be in
        # this loop for the same item at the same time, and only one of them
        # should buzz the phone.
        if not store.claim(item.item_id, item.title, item.value_usd):
            # This is not a completed duplicate. Another request is still
            # delivering it, so the caller must retry the page until that
            # request either commits success or releases the claim.
            log.info("Item %s is pending in a concurrent run; retrying later.", item.item_id)
            summary.matched -= 1
            summary.pending += 1
            continue

        try:
            delivered = notifier.send(item, priority=rule.priority)
        except Exception:
            # An unexpected notifier error must not leave the persisted claim
            # looking like a successful alert. The HTTP handler reports the
            # exception while a later relay remains free to retry this item.
            store.release(item.item_id)
            raise

        if delivered:
            store.commit(item.item_id)
            summary.notified += 1
            log.info("Alerted on %s (rule=%s, value=%s)",
                     item.title, rule.name, item.value_usd)
        else:
            # Release the claim so the next run retries rather than silently
            # dropping an item the user actually wanted.
            store.release(item.item_id)
            summary.failed += 1
            log.error("Delivery failed for %s; will retry next run.", item.item_id)

    return summary
