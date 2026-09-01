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
# rules.json or from the extension's options page.
DEFAULT_RULES = [Rule(name="worth-claiming", min_value_usd=20.0, priority="normal")]

BUNDLED_RULES_PATH = Path(__file__).parent / "rules.json"
USER_RULES_PATH = Path(__file__).parent.parent / "data" / "rules.json"

_TRUTHY = {"1", "true", "yes", "on"}


def seed_mode() -> bool:
    """Whether to record items as seen without alerting on them.

    An empty dedupe file means every listing already on the page looks brand
    new, and the portal shows around 30 per page. Start the server once with
    SEED_MODE=true, let the extension relay a page, then restart without it.
    """
    return os.environ.get("SEED_MODE", "").strip().lower() in _TRUTHY


def user_rules_path() -> Path:
    """Where rules saved from the options page live.

    Deliberately not src/rules.json: edits made in the UI should never clobber
    the defaults that ship with the repo, and deleting this one file is an
    obvious way back to them.
    """
    return Path(os.environ.get("USER_RULES_PATH", USER_RULES_PATH)).expanduser()


def rules_source() -> str:
    """Which layer load_rules() will actually use. For the options page."""
    if os.environ.get("RULES_JSON", "").strip():
        return "env"
    if os.environ.get("RULES_PATH", "").strip():
        return "env-path"
    if user_rules_path().is_file():
        return "user"
    if BUNDLED_RULES_PATH.is_file():
        return "bundled"
    return "default"


def load_rules() -> list[Rule]:
    """Highest-priority readable source wins.

    RULES_JSON -> RULES_PATH -> data/rules.json -> src/rules.json -> built-in.

    The env vars win so a rule can be overridden for one run without touching
    any file. data/rules.json sits above the bundled file so the options page
    can save without overwriting what the repo ships.
    """
    raw = os.environ.get("RULES_JSON", "").strip()
    if raw:
        try:
            return _parse(json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            log.exception("RULES_JSON is not valid; falling back.")

    candidates = []
    override = os.environ.get("RULES_PATH", "").strip()
    if override:
        candidates.append(Path(override))
    candidates.append(user_rules_path())
    candidates.append(BUNDLED_RULES_PATH)

    for path in candidates:
        if not path.is_file():
            continue
        try:
            return _parse(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            log.exception("Could not read rules from %s; falling back.", path)

    log.warning("No rules configured; defaulting to items valued over $20.")
    return list(DEFAULT_RULES)


def save_user_rules(data: object) -> list[Rule]:
    """Validate and persist rules from the options page.

    Parsed before writing so a malformed payload is rejected outright rather
    than leaving a file that makes the server fall back on every relay.
    """
    rules = _parse(data)
    payload = {"rules": [_rule_to_dict(r) for r in rules]}
    path = user_rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    log.info("Saved %d rule(s) to %s", len(rules), path)
    return rules


def clear_user_rules() -> bool:
    """Drop the UI-saved rules, reverting to the bundled defaults."""
    path = user_rules_path()
    if not path.is_file():
        return False
    path.unlink()
    log.info("Removed %s; reverted to bundled rules.", path)
    return True


def rules_to_dicts(rules: list[Rule]) -> list[dict]:
    return [_rule_to_dict(r) for r in rules]


def _rule_to_dict(rule: Rule) -> dict:
    return {
        "name": rule.name,
        "keywords": list(rule.keywords),
        "exclude_keywords": list(rule.exclude_keywords),
        "min_value_usd": rule.min_value_usd,
        "max_value_usd": rule.max_value_usd,
        "categories": list(rule.categories),
        "match_all_keywords": rule.match_all_keywords,
        "alert_on_unknown_value": rule.alert_on_unknown_value,
        "priority": rule.priority,
    }


def _parse(data: object) -> list[Rule]:
    if isinstance(data, dict):
        data = data.get("rules", [])
    if not isinstance(data, list) or not data:
        raise ValueError("rules must be a non-empty list")
    rules = [Rule.from_dict(d) for d in data if isinstance(d, dict)]
    if not rules:
        raise ValueError("no usable rules found")
    return rules
