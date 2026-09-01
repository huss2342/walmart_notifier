"""Configuration loading for rules and runtime options."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from filters import Rule

log = logging.getLogger(__name__)

# Items in this program run roughly $5-$30 retail, so a $100 floor would be
# silent forever. $20 is a "worth walking over to claim" default; tune it in
# rules.json.
DEFAULT_RULES = [Rule(name="worth-claiming", min_value_usd=20.0, priority="normal")]

_TRUTHY = {"1", "true", "yes", "on"}


def seed_mode() -> bool:
    """Whether to record items as seen without alerting on them.

    An empty dedupe file means every listing already on the page looks brand
    new, and the portal shows around 30 per page. Start the server once with
    SEED_MODE=true, let the extension relay a page, then restart without it.
    """
    return os.environ.get("SEED_MODE", "").strip().lower() in _TRUTHY


def load_rules() -> list[Rule]:
    """Rules come from RULES_JSON (inline) or RULES_PATH (file), in that order.

    The env var wins so a rule can be overridden for one run without editing
    the file the server normally reads.
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

    log.warning("No rules configured; defaulting to items valued over $20.")
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
