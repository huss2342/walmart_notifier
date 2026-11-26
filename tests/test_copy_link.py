import asyncio
from types import SimpleNamespace

from discord_bridge.copy_link import CUSTOM_ID, CopyLinkHandler, claim_links, reply_text


CLAIM = "https://www.walmart.com/ip/1234567?filters=sampling"


def test_links_come_from_walmart_link_buttons_in_order():
    rows = [{"type": 1, "components": [
        {"type": 2, "style": 5, "label": "1 · Claim item", "url": CLAIM},
        {"type": 2, "style": 5, "label": "Other", "url": "https://example.com/x"},
        {"type": 2, "style": 2, "label": "Copy link", "custom_id": CUSTOM_ID}]},
        SimpleNamespace(children=[SimpleNamespace(url=CLAIM + "&b")])]
    assert claim_links(rows) == [CLAIM, CLAIM + "&b"]
    assert reply_text([CLAIM]) == CLAIM
    assert reply_text([CLAIM, CLAIM + "&b"]) == f"1. {CLAIM}\n2. {CLAIM}&b"
    assert reply_text([]) == "This alert has no claim link."
    assert len(reply_text([CLAIM] * 200)) <= 2000


def test_button_replies_only_to_the_member_without_previews():
    sent = {}

    class Response:
        async def send_message(self, text, **kwargs):
            sent.update(text=text, **kwargs)

    interaction = SimpleNamespace(type=SimpleNamespace(value=3), data={"custom_id": CUSTOM_ID},
                                  message=SimpleNamespace(components=[{"components": [{"url": CLAIM}]}]),
                                  response=Response())
    handler = CopyLinkHandler()
    assert handler.accepts(interaction)
    assert not handler.accepts(SimpleNamespace(type=SimpleNamespace(value=3), data={"custom_id": "other"}))
    asyncio.run(handler.handle(interaction))
    assert sent == {"text": CLAIM, "ephemeral": True, "suppress_embeds": True}

