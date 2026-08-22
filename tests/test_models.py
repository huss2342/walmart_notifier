from models import Item, parse_price


class TestParsePrice:
    def test_plain_amount(self):
        assert parse_price("$129.99") == 129.99

    def test_thousands_separator(self):
        assert parse_price("$1,299.00") == 1299.0

    def test_embedded_in_sentence(self):
        assert parse_price("Retail value $89 - claim yours now") == 89.0

    def test_no_amount(self):
        assert parse_price("Free item") is None
        assert parse_price("") is None
        assert parse_price(None) is None


class TestFingerprint:
    def test_prefers_walmart_item_id(self):
        a = Item(title="Blender", url="https://www.walmart.com/ip/Some-Blender/123456789")
        b = Item(title="Different Name", url="https://www.walmart.com/ip/Other/123456789")
        assert a.item_id == b.item_id == "ip-123456789"

    def test_bare_ip_url_without_slug(self):
        assert Item(title="X", url="https://www.walmart.com/ip/987654321").item_id == "ip-987654321"

    def test_falls_back_to_title_hash(self):
        a = Item(title="Cast Iron Skillet")
        b = Item(title="  cast   iron   skillet ")
        assert a.item_id == b.item_id
        assert a.item_id.startswith("t-")

    def test_distinct_titles_differ(self):
        assert Item(title="Skillet").item_id != Item(title="Kettle").item_id

    def test_explicit_id_is_respected(self):
        assert Item(title="X", item_id="custom-1").item_id == "custom-1"


class TestRoundTrip:
    def test_to_from_dict(self):
        original = Item(title="Monitor", value_usd=249.0, category="Electronics")
        assert Item.from_dict(original.to_dict()).item_id == original.item_id

    def test_string_price_is_coerced(self):
        assert Item.from_dict({"title": "TV", "value_usd": "$499.99"}).value_usd == 499.99

    def test_unknown_keys_land_in_raw(self):
        parsed = Item.from_dict({"title": "TV", "campaign": "spring"})
        assert parsed.raw["campaign"] == "spring"
