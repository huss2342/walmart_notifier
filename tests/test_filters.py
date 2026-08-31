
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
