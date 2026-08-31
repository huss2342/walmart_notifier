"""Rule engine deciding which items are worth a push notification."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from models import Item


@dataclass(slots=True)
class Rule:
    """One named alert rule. An item matches when *every* configured clause passes."""

    name: str = "default"
    keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    min_value_usd: float | None = None
    max_value_usd: float | None = None
    categories: list[str] = field(default_factory=list)
    match_all_keywords: bool = False
    # When an item's value cannot be parsed, a min_value_usd rule has nothing to
    # compare against. Default to alerting: a missed $200 item costs more than a
    # spurious buzz, and unknown values are common in email digests.
    alert_on_unknown_value: bool = True
    priority: str = "normal"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rule:
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def _haystack(item: Item) -> str:
    return " ".join(filter(None, [item.title, item.category, item.url])).lower()


def _contains(haystack: str, needle: str) -> bool:
    """Whole-word-ish containment so that 'tv' does not match 'tvs' inside 'shirts'."""
    needle = needle.strip().lower()
    if not needle:
        return False
    if not needle.isalnum():  # phrases / hyphenated terms: plain substring
        return needle in haystack
    return re.search(rf"\b{re.escape(needle)}\b", haystack) is not None


def matches(item: Item, rule: Rule) -> bool:
    hay = _haystack(item)

    for bad in rule.exclude_keywords:
        if _contains(hay, bad):
            return False

    if rule.keywords:
        hits = [k for k in rule.keywords if _contains(hay, k)]
        if rule.match_all_keywords:
            if len(hits) != len(rule.keywords):
                return False
        elif not hits:
            return False

    if rule.categories:
        cat = (item.category or "").lower()
        if not any(c.strip().lower() in cat for c in rule.categories if c.strip()):
            return False

    if rule.min_value_usd is not None or rule.max_value_usd is not None:
        if item.value_usd is None:
            return rule.alert_on_unknown_value
        if rule.min_value_usd is not None and item.value_usd < rule.min_value_usd:
            return False
        if rule.max_value_usd is not None and item.value_usd > rule.max_value_usd:
            return False

    return True


def first_match(item: Item, rules: Iterable[Rule]) -> Rule | None:
    """Return the first rule the item satisfies, or None.

    Rules are evaluated in configuration order, so put the ones whose `priority`
    you care about most at the top of the list.
    """
    for rule in rules:
        if matches(item, rule):
            return rule
    return None
