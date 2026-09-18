
import pytest

from filters import Rule, first_match, matches
from models import Item


def item(title="Widget", value=None, url="", category=""):
    return Item(title=title, value_usd=value, url=url, category=category)


class TestMinValue:
    def test_alerts_at_or_above_threshold(self):
        rule = Rule(min_value_usd=100)
        assert matches(item(value=100.0), rule)
        assert matches(item(value=250.0), rule)

    def test_rejects_below_threshold(self):
        assert not matches(item(value=99.99), Rule(min_value_usd=100))

    def test_unknown_value_alerts_by_default(self):
        # Missing prices are common in email digests; erring toward a buzz beats
        # silently dropping a $200 item.
        assert matches(item(value=None), Rule(min_value_usd=100))

    def test_unknown_value_can_be_suppressed(self):
        rule = Rule(min_value_usd=100, alert_on_unknown_value=False)
        assert not matches(item(value=None), rule)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, -1, 10**400])
    def test_invalid_values_follow_the_unknown_value_setting(self, bad):
        assert matches(item(value=bad), Rule(min_value_usd=100))
        assert not matches(
            item(value=bad),
            Rule(min_value_usd=100, alert_on_unknown_value=False),
        )

    def test_max_value_bound(self):
        rule = Rule(max_value_usd=50)
        assert matches(item(value=20.0), rule)
        assert not matches(item(value=80.0), rule)


class TestKeywords:
    def test_any_keyword_matches_by_default(self):
        rule = Rule(keywords=["laptop", "monitor"])
        assert matches(item(title="Gaming Laptop 15 inch"), rule)
        assert not matches(item(title="Bath Towel Set"), rule)

    def test_match_all_requires_every_keyword(self):
        rule = Rule(keywords=["gaming", "laptop"], match_all_keywords=True)
        assert matches(item(title="Gaming Laptop"), rule)
        assert not matches(item(title="Gaming Mouse"), rule)

    def test_exclusions_win_over_keywords(self):
        rule = Rule(keywords=["laptop"], exclude_keywords=["refurbished"])
        assert not matches(item(title="Refurbished Laptop"), rule)

    def test_keyword_is_word_bounded(self):
        # "tv" must not match inside "Advent" or "shirts".
        assert not matches(item(title="Advent Calendar"), Rule(keywords=["tv"]))
        assert matches(item(title="55 inch TV"), Rule(keywords=["tv"]))

    def test_multiword_phrase_matches_as_substring(self):
        assert matches(item(title="Ninja Air Fryer XL"), Rule(keywords=["air fryer"]))

    def test_keywords_search_url_and_category(self):
        rule = Rule(keywords=["electronics"])
        assert matches(item(title="Thing", category="Electronics"), rule)


class TestCombined:
    def test_all_clauses_must_pass(self):
        rule = Rule(keywords=["laptop"], min_value_usd=200)
        assert matches(item(title="Laptop Pro", value=400.0), rule)
        assert not matches(item(title="Laptop Mini", value=150.0), rule)
        assert not matches(item(title="Blender", value=400.0), rule)


class TestFirstMatch:
    def test_returns_first_rule_in_order(self):
        rules = [
            Rule(name="big", min_value_usd=100, priority="high"),
            Rule(name="any", priority="normal"),
        ]
        assert first_match(item(value=500.0), rules).name == "big"
        assert first_match(item(value=5.0), rules).name == "any"

    def test_returns_none_when_nothing_matches(self):
        assert first_match(item(value=5.0), [Rule(min_value_usd=100,
                                                  alert_on_unknown_value=False)]) is None


class TestRuleConfiguration:
    def test_normalizes_safe_string_fields(self):
        rule = Rule.from_dict({
            "name": "  wanted  ",
            "keywords": [" air fryer "],
            "exclude_keywords": [" toy "],
            "categories": [" clearance "],
            "priority": " HIGH ",
        })

        assert rule.name == "wanted"
        assert rule.keywords == ["air fryer"]
        assert rule.exclude_keywords == ["toy"]
        assert rule.categories == ["clearance"]
        assert rule.priority == "high"

    @pytest.mark.parametrize("field", ["keywords", "exclude_keywords", "categories"])
    @pytest.mark.parametrize("bad", ["tv", [1], ["  "]])
    def test_string_lists_reject_wrong_or_empty_entries(self, field, bad):
        with pytest.raises((TypeError, ValueError), match=field):
            Rule.from_dict({field: bad})

    @pytest.mark.parametrize("field", ["min_value_usd", "max_value_usd"])
    @pytest.mark.parametrize(
        "bad", ["50", True, -0.01, float("inf"), float("nan"), 10**1_000]
    )
    def test_value_bounds_reject_unsafe_numbers(self, field, bad):
        with pytest.raises((TypeError, ValueError), match=field):
            Rule.from_dict({field: bad})

    def test_minimum_cannot_exceed_maximum(self):
        with pytest.raises(ValueError, match="must not exceed"):
            Rule.from_dict({"min_value_usd": 51, "max_value_usd": 50})

    @pytest.mark.parametrize("field", ["match_all_keywords", "alert_on_unknown_value"])
    @pytest.mark.parametrize("bad", [0, 1, "true", None])
    def test_boolean_fields_require_real_booleans(self, field, bad):
        with pytest.raises(TypeError, match=field):
            Rule.from_dict({field: bad})

    @pytest.mark.parametrize("bad", ["critical", "", 4, None])
    def test_priority_must_be_supported(self, bad):
        with pytest.raises(ValueError, match="priority"):
            Rule.from_dict({"priority": bad})

    @pytest.mark.parametrize("bad", ["", "   ", 7, None])
    def test_name_must_be_a_non_empty_string(self, bad):
        with pytest.raises(ValueError, match="name"):
            Rule.from_dict({"name": bad})
