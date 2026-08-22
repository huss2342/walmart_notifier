"""Source -> filter -> dedupe -> notify."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

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

    def as_dict(self) -> dict[str, int]:
        return {"seen": self.seen, "new": self.new,
                "matched": self.matched, "notified": self.notified}


def process(
    items: Iterable[Item],
    rules: list[Rule],
    store: SeenStore,
    notifier: Notifier,
) -> RunSummary:
    summary = RunSummary()

    for item in items:
        summary.seen += 1
        if not store.is_new(item.item_id):
            continue
        summary.new += 1

        rule = first_match(item, rules)
        if rule is None:
            # Record non-matching items too: if a rule is loosened later we do
            # not want a backlog of old listings to fire all at once.
            store.mark_seen(item.item_id, item.title)
            continue

        summary.matched += 1
        delivered = notifier.send(item, priority=rule.priority)
        if delivered:
            summary.notified += 1
            store.mark_seen(item.item_id, item.title)
            log.info("Alerted on %s (rule=%s, value=%s)",
                     item.title, rule.name, item.value_usd)
        else:
            # Leave it unmarked so the next run retries rather than silently
            # dropping an item the user actually wanted.
            log.error("Delivery failed for %s; will retry next run.", item.item_id)

    return summary
