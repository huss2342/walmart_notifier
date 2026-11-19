"""Bridge -> Reviewer Alerts Elite auto-claim signal: new/restocked in-stock items only."""

import io
import json
import threading
import time

from discord_bridge.core import JobStore
from discord_bridge.elite_signal import EliteSignal
from discord_bridge.service import make_server

from test_discord_bridge_service import request

KEY = "e" * 40


class Opener:
    def __init__(self, status=202):
        self.calls, self.status, self.done = [], status, threading.Event()

    def __call__(self, req, timeout):
        self.calls.append((req.full_url, dict(req.header_items()), json.loads(req.data)))
        self.done.set()
        items = json.loads(req.data)["items"]
        body = json.dumps({"accepted": [i["itemId"] for i in items], "rejected": []})

        class Response(io.BytesIO):
            status = self.status

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        return Response(body.encode())


def serve(tmp_path, signal, events=None):
    store = JobStore(tmp_path / "s.sqlite3")
    sink = (lambda e, **f: events.append((e, f))) if events is not None else None
    server = make_server(store, key="k" * 40, port=0, elite_signal=signal, event_sink=sink)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def post(server, items):
    headers = {"Authorization": "Bearer " + "k" * 40, "Content-Type": "application/json"}
    return request(server, "POST", "/v1/observations", {"items": items}, headers)


def test_new_in_stock_item_is_signalled_once_with_review_url(tmp_path):
    opener, events = Opener(), []
    signal = EliteSignal("http://host.docker.internal:8899/v2/signals/items", KEY,
                         lambda e, **f: events.append((e, f)), opener=opener)
    server = serve(tmp_path, signal)
    try:
        item = {"item_id": "ip-20449164214", "title": "Gaming computer", "value_usd": 450.0,
                "category": "electronics"}
        assert post(server, [item])[0] == 202
        assert opener.done.wait(3)
        url, headers, body = opener.calls[0]
        assert url.endswith("/v2/signals/items") and headers["Authorization"] == "Bearer " + KEY
        sent = body["items"][0]
        assert sent["itemId"] == "20449164214" and sent["valueCents"] == 45000
        assert sent["category"] == "electronics" and sent["transition"] == "queued"
        assert "/ip/" in sent["url"] and "Sampling" in sent["url"] and "from=" in sent["url"]
        # Seen again: duplicate, no new signal.
        opener.done.clear()
        assert post(server, [item])[0] == 202
        assert not opener.done.wait(0.5)
        deadline = time.time() + 2
        while time.time() < deadline and not any(e == "elite_signal" for e, _ in events):
            time.sleep(0.05)
        assert (events[0][0], ) == ("elite_signal", ) and events[0][1]["outcome"] == "accepted"
    finally:
        server.shutdown()
        server.server_close()


def test_out_of_stock_and_unpriced_items_are_not_signalled(tmp_path):
    opener = Opener()
    server = serve(tmp_path, EliteSignal("http://x/v2/signals/items", KEY, opener=opener))
    try:
        assert post(server, [{"item_id": "ip-20449164215", "title": "Gone", "value_usd": 450.0,
                              "available": False},
                             {"item_id": "ip-20449164216", "title": "No price"}])[0] == 202
        assert not opener.done.wait(0.5)
    finally:
        server.shutdown()
        server.server_close()


def test_network_failure_is_logged_and_retried(tmp_path):
    events = []
    attempts = []

    def failing(req, timeout):
        attempts.append(1)
        raise OSError("connection refused")
    signal = EliteSignal("http://x/v2/signals/items", KEY, lambda e, **f: events.append(f),
                         opener=failing)
    item = {"item_id": "ip-123456789", "title": "T", "value_cents": 100, "available": True,
            "category": "other", "review_url": "https://www.walmart.com/ip/123456789?x"}
    assert signal.offer([item], {"ip-123456789": "restocked"}) == 1
    deadline = time.time() + 5
    while time.time() < deadline and len(events) < 3:
        time.sleep(0.05)
    assert len(attempts) == 3
    assert all(e["outcome"] == "unknown" and e["error_type"] == "OSError" for e in events)


def test_from_env_disabled_without_settings(monkeypatch):
    monkeypatch.delenv("ELITE_SIGNAL_URL", raising=False)
    assert EliteSignal.from_env() is None


def test_in_process_collector_signals_new_items(tmp_path):
    """Production runs the bridge inside src/server.py: its ingest path must signal too."""
    from models import Item
    from notifiers.discord import DiscordNotifier

    opener = Opener()
    signal = EliteSignal("http://x/v2/signals/items", KEY, opener=opener)
    notifier = DiscordNotifier(JobStore(tmp_path / "q.sqlite3"), elite_signal=signal)
    item = Item(title="Gaming computer", item_id="ip-20449164217", value_usd=450.0,
                url="https://www.walmart.com/ip/20449164217", category="electronics")
    notifier.ingest([item])
    assert opener.done.wait(3)
    sent = opener.calls[0][2]["items"][0]
    assert sent["itemId"] == "20449164217" and sent["valueCents"] == 45000
    assert sent["transition"] == "queued" and "/ip/" in sent["url"]
    opener.done.clear()
    notifier.ingest([item])  # duplicate: no second signal
    assert not opener.done.wait(0.5)


def test_seed_ingest_never_signals(tmp_path):
    from models import Item
    from notifiers.discord import DiscordNotifier

    opener = Opener()
    notifier = DiscordNotifier(JobStore(tmp_path / "q.sqlite3"),
                               elite_signal=EliteSignal("http://x/v2/signals/items", KEY, opener=opener))
    notifier.ingest([Item(title="Old", item_id="ip-20449164218", value_usd=450.0,
                          url="https://www.walmart.com/ip/20449164218")], seed_only=True)
    assert not opener.done.wait(0.5)


WATCH = {"watches": [{"minValueCents": 20000, "keywords": ["gaming", "laptop"], "keywordMode": "all",
                      "excludeKeywords": ["refurbished"], "categoryIds": ["furniture"]}],
         "searches": ["gaming laptop"], "categoryMap": {"furniture": "furniture"}}


def candidate(item_id, title, cents, category="electronics"):
    return {"item_id": item_id, "title": title, "value_cents": cents, "available": True,
            "category": category, "review_url": f"https://www.walmart.com/ip/{item_id[3:]}?x"}


def test_only_items_an_elite_request_wants_are_forwarded():
    opener = Opener()
    signal = EliteSignal("http://x/v2/signals/items", KEY, opener=opener)
    signal.set_watch(WATCH)
    items = [candidate("ip-100000001", "Gaming Laptop 16GB", 45000),
             candidate("ip-100000002", "Gaming laptop refurbished", 45000),
             candidate("ip-100000003", "Gaming laptop", 1000),
             candidate("ip-100000004", "Office chair", 30000, "furniture"),
             candidate("ip-100000005", "Desk lamp", 30000)]
    routed = {item["item_id"]: "queued" for item in items}
    assert signal.offer(items, routed) == 2
    assert opener.done.wait(3)
    assert [i["itemId"] for i in opener.calls[0][2]["items"]] == ["100000001", "100000004"]
    assert signal.search_queries() == ["gaming laptop"]


def test_stale_or_missing_watch_forwards_everything():
    signal = EliteSignal("http://x/v2/signals/items", KEY, opener=Opener())
    lamp = candidate("ip-100000005", "Desk lamp", 30000)
    assert signal.wanted(lamp) and signal.search_queries() == []
    signal.set_watch(WATCH, now=time.time() - 3600)
    assert signal.wanted(lamp) and signal.search_queries() == []
    signal.set_watch(WATCH)
    assert not signal.wanted(lamp)


def test_watch_loop_loads_watch_from_elite():
    seen = []

    def opener(req, timeout):
        seen.append((req.full_url, req.get_method(), req.get_header("Authorization")))

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        return Response(json.dumps(WATCH).encode())
    events = []
    signal = EliteSignal("http://x/v2/signals/items", KEY, lambda e, **f: events.append(f),
                         opener=opener, watch=True)
    deadline = time.time() + 3
    while time.time() < deadline and not signal.search_queries():
        time.sleep(0.05)
    assert signal.search_queries() == ["gaming laptop"]
    assert seen[0] == ("http://x/v2/signals/watch", "GET", "Bearer " + KEY)
    assert events[0]["outcome"] == "watch_loaded" and events[0]["searches"] == 1
