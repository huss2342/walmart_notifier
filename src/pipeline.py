"""Source -> filter -> dedupe -> notify."""

from __future__ import annotations

import logging
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
    matched: int = 0
    notified: int = 0
    failed: int = 0
    seeded: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"seen": self.seen, "new": self.new, "matched": self.matched,
                "notified": self.notified, "failed": self.failed, "seeded": self.seeded}


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
        summary.seen += 1
        if not store.is_new(item.item_id):
            continue
        summary.new += 1

        if seed_only:
            store.mark_seen(item.item_id, item.title)
            summary.seeded += 1
            continue

        rule = first_match(item, rules)
        if rule is None:
            # Record non-matching items too: if a rule is loosened later we do
            # not want a backlog of old listings to fire all at once.
            store.mark_seen(item.item_id, item.title)
            continue

        summary.matched += 1

        # Claim before notifying: the timer and the ingest endpoint can be in
        # this loop for the same item at the same time, and only one of them
        # should buzz the phone.
        if not store.claim(item.item_id, item.title):
            log.info("Item %s claimed by a concurrent run; skipping.", item.item_id)
            summary.matched -= 1
            continue

        if notifier.send(item, priority=rule.priority):
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
