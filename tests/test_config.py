import json

import pytest

from config import (
    clear_user_rules,
    load_rules,
    rules_source,
    rules_to_dicts,
    save_user_rules,
    seed_mode,
)


@pytest.fixture(autouse=True)
def clear_env(monkeypatch, tmp_path):
    """Isolate every rules source from the developer's real machine.

    USER_RULES_PATH matters most: without it these tests read whatever the
    running notifier last saved to data/rules.json, so they passed or failed
    depending on what the user had typed into the options page.
    """
    monkeypatch.delenv("RULES_JSON", raising=False)
    monkeypatch.delenv("RULES_PATH", raising=False)
    monkeypatch.setenv("USER_RULES_PATH", str(tmp_path / "user-rules.json"))


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
    """With every file missing, the built-in rule is the last line of defence."""
    import config

    monkeypatch.setenv("RULES_PATH", str(tmp_path / "nope.json"))
    monkeypatch.setenv("USER_RULES_PATH", str(tmp_path / "also-nope.json"))
    monkeypatch.setattr(config, "BUNDLED_RULES_PATH", tmp_path / "gone.json")

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


# --- user rules layer --------------------------------------------------------


@pytest.fixture
def user_rules(monkeypatch, tmp_path):
    """The UI-saved rules file for tests that write to it.

    clear_env already redirects USER_RULES_PATH; this names a distinct file so
    a test can assert on its existence.
    """
    path = tmp_path / "rules.json"
    monkeypatch.setenv("USER_RULES_PATH", str(path))
    return path


def test_user_rules_win_over_the_bundled_file(user_rules):
    save_user_rules({"rules": [{"name": "from-ui", "min_value_usd": 12.5}]})
    rules = load_rules()
    assert [r.name for r in rules] == ["from-ui"]
    assert rules[0].min_value_usd == 12.5


def test_saving_does_not_touch_the_bundled_file(user_rules):
    import config

    before = config.BUNDLED_RULES_PATH.read_text(encoding="utf-8")
    save_user_rules({"rules": [{"name": "from-ui", "min_value_usd": 1}]})
    assert config.BUNDLED_RULES_PATH.read_text(encoding="utf-8") == before


def test_env_still_beats_user_rules(monkeypatch, user_rules):
    save_user_rules({"rules": [{"name": "from-ui"}]})
    monkeypatch.setenv("RULES_JSON", json.dumps([{"name": "from-env"}]))
    assert [r.name for r in load_rules()] == ["from-env"]


def test_clearing_reverts_to_the_bundled_rules(user_rules):
    save_user_rules({"rules": [{"name": "from-ui"}]})
    assert clear_user_rules() is True
    assert [r.name for r in load_rules()] == ["expensive", "watched-keywords"]


def test_clearing_when_nothing_was_saved_is_harmless(user_rules):
    assert clear_user_rules() is False


def test_invalid_rules_are_rejected_before_anything_is_written(user_rules):
    """A bad save must not leave a file that breaks every later relay."""
    for bad in ({"rules": []}, {"rules": "nope"}, [], {"rules": [1, 2]}):
        with pytest.raises(ValueError):
            save_user_rules(bad)
    assert not user_rules.exists()


def test_saved_rules_round_trip_every_field(user_rules):
    original = {
        "name": "everything", "keywords": ["tv"], "exclude_keywords": ["toy"],
        "min_value_usd": 5.0, "max_value_usd": 50.0, "categories": ["clearance"],
        "match_all_keywords": True, "alert_on_unknown_value": False,
        "priority": "high",
    }
    save_user_rules({"rules": [original]})
    assert rules_to_dicts(load_rules())[0] == original


def test_rules_source_reports_the_active_layer(monkeypatch, user_rules):
    assert rules_source() == "bundled"
    save_user_rules({"rules": [{"name": "from-ui"}]})
    assert rules_source() == "user"
    monkeypatch.setenv("RULES_JSON", json.dumps([{"name": "x"}]))
    assert rules_source() == "env"
