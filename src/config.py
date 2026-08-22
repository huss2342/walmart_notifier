"""Configuration loading for rules and runtime options."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from filters import Rule

log = logging.getLogger(__name__)

DEFAULT_RULES = [Rule(name="high-value", min_value_usd=100.0, priority="high")]


def load_rules() -> list[Rule]:
    """Rules come from RULES_JSON (inline) or RULES_PATH (file), in that order.

    Inline JSON in an app setting is the easiest thing to edit from the Azure
    portal without redeploying, so it wins over the bundled file.
    """
    raw = os.environ.get("RULES_JSON", "").strip()
    if raw:
        try:
            return _parse(json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            log.exception("RULES_JSON is not valid; falling back.")

    path = Path(os.environ.get("RULES_PATH", Path(__file__).parent / "rules.json"))
    if path.is_file():
        try:
            return _parse(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            log.exception("Could not read rules from %s; falling back.", path)

    log.warning("No rules configured; defaulting to items valued over $100.")
    return list(DEFAULT_RULES)


def _parse(data: object) -> list[Rule]:
    if isinstance(data, dict):
        data = data.get("rules", [])
    if not isinstance(data, list) or not data:
        raise ValueError("rules must be a non-empty list")
    rules = [Rule.from_dict(d) for d in data if isinstance(d, dict)]
    if not rules:
        raise ValueError("no usable rules found")
    return rules
