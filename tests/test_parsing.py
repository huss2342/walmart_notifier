from sources.parsing import extract_items, strip_html
from sources.webhook_source import parse_ingest_payload

EMAIL_HTML = """
<html><body>
  <h1>New items available to claim</h1>
  <table>
    <tr>
      <td><a href="https://www.walmart.com/ip/Ninja-Air-Fryer-XL/456789123">Ninja Air Fryer XL</a></td>
      <td>Retail value $129.99</td>
    </tr>
    <tr>
      <td><a href="https://www.walmart.com/ip/Bath-Towel-Set/111222333">Bath Towel Set</a></td>
      <td>Retail value $24.00</td>
    </tr>
  </table>
</body></html>
"""


class TestStripHtml:
    def test_drops_scripts_and_tags(self):
        text = strip_html("<div>Hello<script>evil()</script><b>World</b></div>")
        assert "evil" not in text
        assert "Hello" in text and "World" in text

    def test_unescapes_entities(self):
        assert "&" in strip_html("<p>Tools &amp; Home</p>")


class TestExtractItems:
    def test_finds_every_item(self):
        items = list(extract_items(EMAIL_HTML, source="email"))
        assert len(items) == 2
        assert {i.item_id for i in items} == {"ip-456789123", "ip-111222333"}

    def test_associates_the_right_price(self):
        by_id = {i.item_id: i for i in extract_items(EMAIL_HTML, source="email")}
        assert by_id["ip-456789123"].value_usd == 129.99
        assert by_id["ip-111222333"].value_usd == 24.00

    def test_title_comes_from_url_slug(self):
        items = list(extract_items(EMAIL_HTML, source="email"))
        assert any("Ninja Air Fryer" in i.title for i in items)

    def test_item_without_price_is_kept(self):
        markup = '<a href="https://www.walmart.com/ip/Mystery-Box/999888777">Mystery Box</a>'
        items = list(extract_items(markup, source="email"))
        assert len(items) == 1
        assert items[0].value_usd is None

    def test_duplicate_links_collapse(self):
        markup = EMAIL_HTML + EMAIL_HTML
        assert len(list(extract_items(markup, source="email"))) == 2

    def test_non_item_links_ignored(self):
        markup = '<a href="https://www.walmart.com/help/article/foo">Help</a>'
        assert list(extract_items(markup, source="email")) == []


class TestIngestPayload:
    def test_structured_items(self):
        items = parse_ingest_payload(
            '{"items":[{"title":"Monitor","value_usd":249.0,'
            '"url":"https://www.walmart.com/ip/Mon/123456789"}]}'
        )
        assert len(items) == 1
        assert items[0].value_usd == 249.0
        assert items[0].item_id == "ip-123456789"

    def test_bare_list(self):
        assert len(parse_ingest_payload('[{"title":"A"},{"title":"B"}]')) == 2

    def test_single_object(self):
        assert len(parse_ingest_payload('{"title":"Solo"}')) == 1

    def test_price_alias_and_string_form(self):
        items = parse_ingest_payload('[{"title":"TV","price":"$1,099.00"}]')
        assert items[0].value_usd == 1099.0

    def test_entries_without_title_or_url_are_dropped(self):
        assert parse_ingest_payload('[{"value_usd": 10}]') == []

    def test_falls_back_to_markup_extraction(self):
        assert len(parse_ingest_payload(EMAIL_HTML)) == 2

    def test_malformed_json_does_not_raise(self):
        assert parse_ingest_payload('{"items": [broken') == []

    def test_empty_body(self):
        assert parse_ingest_payload("") == []
        assert parse_ingest_payload(b"") == []


PRICE_BEFORE_LINK = """
<div class="card">
  <span>Retail value $59.99</span>
  <a href="https://www.walmart.com/ip/Cast-Iron-Skillet/222333444">Cast Iron Skillet</a>
</div>
<div class="card">
  <span>Retail value $12.50</span>
  <a href="https://www.walmart.com/ip/Dish-Towels/555666777">Dish Towels</a>
</div>
"""

REPEATED_LINKS = """
<div>
  <a href="https://www.walmart.com/ip/Ninja-Air-Fryer/456789123"><img alt="Ninja"></a>
  <a href="https://www.walmart.com/ip/Ninja-Air-Fryer/456789123">Ninja Air Fryer</a>
  <span>$129.99</span>
</div>
<div>
  <a href="https://www.walmart.com/ip/Bath-Towel/111222333"><img alt="Towel"></a>
  <a href="https://www.walmart.com/ip/Bath-Towel/111222333">Bath Towel</a>
  <span>$24.00</span>
</div>
"""


class TestPriceAssociation:
    """Regression cover: a naive symmetric window around each link hands item N
    the price of item N-1. Prices must never cross an item boundary."""

    def test_price_after_link_stays_with_its_own_item(self):
        by_id = {i.item_id: i for i in extract_items(EMAIL_HTML, source="email")}
        assert by_id["ip-456789123"].value_usd == 129.99
        assert by_id["ip-111222333"].value_usd == 24.00

    def test_price_before_link_is_found(self):
        by_id = {i.item_id: i for i in extract_items(PRICE_BEFORE_LINK, source="email")}
        assert by_id["ip-222333444"].value_usd == 59.99
        assert by_id["ip-555666777"].value_usd == 12.50

    def test_lookbehind_does_not_steal_previous_item_price(self):
        # The $59.99 card sits immediately above the Dish Towels link; the
        # lookbehind must stop at the preceding item's link.
        by_id = {i.item_id: i for i in extract_items(PRICE_BEFORE_LINK, source="email")}
        assert by_id["ip-555666777"].value_usd != 59.99

    def test_repeated_links_collapse_to_one_item_with_right_price(self):
        items = list(extract_items(REPEATED_LINKS, source="email"))
        assert len(items) == 2
        by_id = {i.item_id: i for i in items}
        assert by_id["ip-456789123"].value_usd == 129.99
        assert by_id["ip-111222333"].value_usd == 24.00

    def test_item_with_no_price_anywhere_stays_none(self):
        markup = '<a href="https://www.walmart.com/ip/Mystery/999888777">Mystery</a>'
        assert list(extract_items(markup, source="email"))[0].value_usd is None

    def test_plain_text_email_segments_correctly(self):
        text = (
            "Air Fryer https://www.walmart.com/ip/Air-Fryer/456789123 value $129.99\n"
            "Towel Set https://www.walmart.com/ip/Towel-Set/111222333 value $24.00\n"
        )
        by_id = {i.item_id: i for i in extract_items(text, source="email", is_html=False)}
        assert by_id["ip-456789123"].value_usd == 129.99
        assert by_id["ip-111222333"].value_usd == 24.00
