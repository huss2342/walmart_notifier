import json

import pytest

from config import load_rules, seed_mode


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    monkeypatch.delenv("RULES_JSON", raising=False)
    monkeypatch.delenv("RULES_PATH", raising=False)


def test_inline_json_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("RULES_JSON", json.dumps({"rules": [{"name": "inline",
                                                            "min_value_usd": 50}]}))
    rules = load_rules()
    assert [r.name for r in rules] == ["inline"]


def test_bare_list_is_accepted(monkeypatch):
    monkeypatch.setenv("RULES_JSON", json.dumps([{"name": "flat"}]))
    assert load_rules()[0].name == "flat"


def test_reads_from_file(monkeypatch, tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"rules": [{"name": "fromfile", "keywords": ["tv"]}]}))
    monkeypatch.setenv("RULES_PATH", str(path))
    assert load_rules()[0].name == "fromfile"


def test_unknown_keys_are_ignored(monkeypatch):
    monkeypatch.setenv("RULES_JSON", json.dumps([{"name": "x", "bogus_field": 1}]))
    assert load_rules()[0].name == "x"


def test_bad_json_falls_back_to_bundled_defaults(monkeypatch):
    monkeypatch.setenv("RULES_JSON", "{not json")
    # Falls through to the bundled src/rules.json rather than the hardcoded
    # single-rule default.
    names = [r.name for r in load_rules()]
    assert names == ["expensive", "watched-keywords"]


def test_missing_everything_uses_hardcoded_default(monkeypatch, tmp_path):
    monkeypatch.setenv("RULES_PATH", str(tmp_path / "nope.json"))
    rules = load_rules()
    assert len(rules) == 1 and rules[0].min_value_usd == 20.0


@pytest.mark.parametrize("value,expected", [
    ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("", False), ("maybe", False),
])
def test_seed_mode_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("SEED_MODE", value)
    assert seed_mode() is expected


def test_seed_mode_defaults_off(monkeypatch):
    monkeypatch.delenv("SEED_MODE", raising=False)
    assert seed_mode() is False
