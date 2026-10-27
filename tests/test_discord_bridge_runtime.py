"""The sidecar's privacy, time and restart boundaries."""

import json

import pytest
from discord_bridge.core import JobStore, normalize_item, render_message, route_keys


def item(value=29.99, **extra):
    return {"item_id": "ip-20449164214", "title": "Audio card", "value_usd": value, **extra}


def test_sampling_context_is_allowlisted_and_collector_quota_is_dropped():
    clean = normalize_item(
        item(
            url="https://www.walmart.com/ip/Audio/20449164214?secret=private",
            claims_remaining=2,
            query="Secret personal search",
            category="new arrival",
        )
    )
    assert clean["product_url"] == "https://www.walmart.com/ip/Audio/20449164214"
    assert "sampling" in clean["review_url"].lower()
    assert "secret" not in json.dumps(clean).lower()
    assert "claims_remaining" not in clean
    assert clean["category"] == "other"


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/ip/20449164214",
        "http://www.walmart.com/ip/20449164214",
        "https://www.walmart.com@evil.test/ip/20449164214",
        "https://www.walmart.com/ip/99999999999",
    ],
)
def test_untrusted_or_mismatched_product_link_is_rejected(url):
    with pytest.raises(ValueError):
        normalize_item(item(url=url))


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "29.99"])
def test_invalid_values_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_item(item(value))


def test_price_boundary_and_unknown_do_not_unlock_starter():
    assert "starter-items" in route_keys(normalize_item(item(39.99)))
    for value in (40, 200, 0, None):
        assert "starter-items" not in route_keys(normalize_item(item(value)))
    assert "price-200-plus" in route_keys(normalize_item(item(200)))
    assert "type-electronics" in route_keys(normalize_item(item(category="electronics")))


def test_restart_duplicate_ingest_and_five_minute_delay(tmp_path):
    path = tmp_path / "queue.sqlite3"
    store = JobStore(path)
    assert store.record([normalize_item(item())], now=1000)["new"] == 1
    assert store.record([normalize_item(item())], now=1200)["duplicates"] == 1
    # Reopening is an actual process-state loss, not an in-memory timer.
    restarted = JobStore(path)
    live = restarted.claim(now=1299)
    assert live["route"] != "starter-items"
    while live:
        restarted.finish(live, "sent", message_id="123456789012345678")
        live = restarted.claim(now=1299)
    assert restarted.claim(now=1299) is None
    delayed = restarted.claim(now=1300)
    assert delayed["route"] == "starter-items"
    restarted.finish(delayed, "sent", message_id="123456789012345679")
    assert restarted.record([normalize_item(item())], now=1400)["new"] == 0
    assert restarted.claim(now=1400) is None


def test_failed_destination_does_not_repeat_other_destinations(tmp_path):
    store = JobStore(tmp_path / "queue.sqlite3")
    store.record([normalize_item(item())], now=1000)
    first = store.claim(now=1000)
    store.retry(first, now=1000, delay=20, reason="rate_limited")
    second = store.claim(now=1000)
    assert second["id"] != first["id"]
    store.finish(second, "sent", message_id="123456789012345678")
    third = store.claim(now=1000)
    store.finish(third, "sent", message_id="123456789012345679")
    assert store.claim(now=1019) is None
    assert store.claim(now=1020)["id"] == first["id"]


def test_lease_ownership_and_expired_jobs(tmp_path):
    store = JobStore(tmp_path / "queue.sqlite3")
    store.record([normalize_item(item())], now=1000)
    first = store.claim(now=1000)
    replacement = store.claim(now=1100)
    assert replacement["id"] == first["id"]
    assert not store.finish(first, "sent", message_id="123456789012345678")
    assert store.finish(replacement, "sent", message_id="123456789012345678")
    assert store.claim(now=3000) is None
    assert store.counts()["expired"] == 3


def test_batch_validation_does_not_partially_commit(tmp_path):
    store = JobStore(tmp_path / "queue.sqlite3")
    with pytest.raises(ValueError):
        store.record([normalize_item(item()), {"oops": "bad"}], now=1000)
    assert store.counts().get("pending", 0) == 0


def test_message_is_safe_without_claim_count_or_pings():
    clean = normalize_item(item(title="@everyone **Audio card**", claims_remaining=3))
    message = render_message(clean, first_seen=1000, last_seen=1000, route="starter-items")
    text = json.dumps(message)
    assert message["allowed_mentions"] == {"parse": []}
    assert "claims_remaining" not in text
    assert "Claims remaining" not in text
    assert "5-minute delay" in text
    assert [button["label"] for button in message["components"][0]["components"]] == [
        "Open review item",
        "View product",
    ]


@pytest.mark.parametrize("folder", ["asr", "seo"])
def test_public_product_image_becomes_small_thumbnail_without_tracking(folder):
    image = f"https://i5.walmartimages.com/{folder}/product-123.jpeg"
    clean = normalize_item(item(image_url=image + "?width=300&secret=private#private"))
    assert clean["image_url"] == image
    assert normalize_item(clean)["image_url"] == image
    message = render_message(clean, first_seen=1000, last_seen=1000, route="pro-all")
    assert message["embeds"][0]["thumbnail"] == {"url": image}
    assert "image" not in message["embeds"][0]  # Use the compact thumbnail slot.
    assert "private" not in json.dumps(message)


@pytest.mark.parametrize(
    "image",
    [
        None,
        12,
        "",
        "https://tracker.example/photo.jpg",
        "https://i5.walmartimages.com.evil.example/asr/photo.jpg",
        "https://i5.walmartimages.com@evil.example/asr/photo.jpg",
        "https://owner:private@i5.walmartimages.com/asr/photo.jpg",
        "https://i5.walmartimages.com:8443/asr/photo.jpg",
        "http://i5.walmartimages.com/asr/photo.jpg",
        "data:image/png;base64,private",
        "https://i5.walmartimages.com/dfw/tracker.gif",
        "https://i5.walmartimages.com/asr/photo.svg",
        "https://i5.walmartimages.com/asr/../dfw/photo.jpg",
        "https://i5.walmartimages.com/asr/%2e%2e%2fphoto.jpg",
        "https://i5.walmartimages.com/asr/photo.jpg\n",
        "https://[broken/asr/photo.jpg",
        "https://i5.walmartimages.com/asr/" + "x" * 2048 + ".jpg",
    ],
)
def test_missing_or_untrusted_optional_image_does_not_block_alert(image):
    clean = normalize_item(item(image_url=image))
    assert clean.get("image_url", "") == ""
    message = render_message(clean, first_seen=1000, last_seen=1000, route="pro-all")
    assert "thumbnail" not in message["embeds"][0]
    assert len(message["components"][0]["components"]) == 2


def test_legacy_queued_item_without_image_still_renders():
    clean = normalize_item(item())
    clean.pop("image_url", None)
    assert (
        "thumbnail"
        not in render_message(clean, first_seen=1000, last_seen=1000, route="pro-all")["embeds"][0]
    )


def test_later_enrichment_creates_missing_routes_without_resetting_clock(tmp_path):
    store = JobStore(tmp_path / "enrich.sqlite3")
    store.record([normalize_item(item(None, available=False))], now=1000)
    assert store.counts() == {"pending": 1}  # Historical free observation only.
    store.record([normalize_item(item(None))], now=1100)
    assert store.counts() == {"pending": 3}
    store.record([normalize_item(item(29.99, category="electronics"))], now=1200)
    routes = {}
    while job := store.claim(now=1299):
        routes[job["route"]] = job
        store.finish(job, "sent", message_id="123456789012345678")
    assert "type-electronics" in routes and "price-20-40" in routes
    assert "starter-items" not in routes
    delayed = store.claim(now=1300)
    assert delayed["route"] == "starter-items"
    assert delayed["first_seen"] == 1000


def test_long_product_slug_falls_back_to_short_safe_discord_links():
    clean = normalize_item(item(url="https://www.walmart.com/ip/" + "a" * 600 + "/20449164214"))
    message = render_message(clean, first_seen=1000, last_seen=1000, route="pro-all")
    assert all(len(button["url"]) <= 512 for button in message["components"][0]["components"])
    assert clean["product_url"] == "https://www.walmart.com/ip/20449164214"
