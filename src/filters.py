"""Rule engine deciding which items are worth a push notification."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from models import Item

_PRIORITIES = {"low", "normal", "high", "urgent"}


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
        """Build a rule from configuration, rejecting ambiguous values.

        JSON is untyped enough that mistakes such as ``"50"`` for a numeric
        threshold or ``"tv"`` for a keyword list otherwise survive loading and
        fail much later, while an ingest request is being handled.  Validate at
        the configuration boundary so a bad rule can be reported (or safely
        fallen back from) without breaking the relay.
        """
        if not isinstance(data, dict):
            raise TypeError("rule must be an object")

        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        values = {k: v for k, v in data.items() if k in known}

        if "name" in values:
            name = values["name"]
            if not isinstance(name, str) or not name.strip():
                raise ValueError("rule name must be a non-empty string")
            values["name"] = name.strip()

        for field_name in ("keywords", "exclude_keywords", "categories"):
            if field_name not in values:
                continue
            entries = values[field_name]
            if not isinstance(entries, list):
                raise TypeError(f"{field_name} must be a list of strings")
            cleaned: list[str] = []
            for entry in entries:
                if not isinstance(entry, str) or not entry.strip():
                    raise ValueError(f"{field_name} entries must be non-empty strings")
                cleaned.append(entry.strip())
            values[field_name] = cleaned

        for field_name in ("min_value_usd", "max_value_usd"):
            if field_name not in values or values[field_name] is None:
                continue
            amount = values[field_name]
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                raise TypeError(f"{field_name} must be a number or null")
            try:
                amount = float(amount)
            except OverflowError as exc:
                raise ValueError(
                    f"{field_name} must be a finite non-negative number"
                ) from exc
            if not math.isfinite(amount) or amount < 0:
                raise ValueError(f"{field_name} must be a finite non-negative number")
            values[field_name] = amount

        minimum = values.get("min_value_usd")
        maximum = values.get("max_value_usd")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("min_value_usd must not exceed max_value_usd")

        for field_name in ("match_all_keywords", "alert_on_unknown_value"):
            if field_name in values and not isinstance(values[field_name], bool):
                raise TypeError(f"{field_name} must be a boolean")

        if "priority" in values:
            priority = values["priority"]
            if not isinstance(priority, str) or priority.strip().lower() not in _PRIORITIES:
                allowed = ", ".join(sorted(_PRIORITIES))
                raise ValueError(f"priority must be one of: {allowed}")
            values["priority"] = priority.strip().lower()

        return cls(**values)


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


def _known_value(value: object) -> bool:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or value < 0):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


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
        if not _known_value(item.value_usd):
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
