"""Back-again alerts: $80+ offers that vanish from every list and later return.

Nov 16-19: SIIL ostomy belts left the lists every evening and came back at
18:00 CST; a batch of instrument cases came back after 5+ days. None was
announced: the backend took each return for an ordinary duplicate.
"""

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from discord_bridge.core import JobStore
from discord_bridge.messages import render_batch

import server
from back_again import (
    AWAY_SECONDS,
    COOLDOWN_SECONDS,
    OUTAGE_SECONDS,
    PENDING_SECONDS,
    BackAgain,
)
from models import Item
from notifiers.discord import DiscordNotifier
from operational_logging import OperationalLog
from state import SeenStore

BELT = "ip-20812616510"


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    # The queue stamps observations with time.time(); both share this clock.
    fake = Clock()
    monkeypatch.setattr(time, "time", fake)
    return fake


def offer(item_id=BELT, value=90.0, **raw):
    number = item_id.removeprefix("ip-")
    return Item(title="Ostomy belt bundle", item_id=item_id, value_usd=value,
                url=f"https://www.walmart.com/ip/{number}", source="extension",
                raw={"available": True, **raw})


def notifier_for(tmp_path, clock):
    store = JobStore(tmp_path / "collector.sqlite3")
    back = BackAgain(tmp_path / "back-again.json", store.path, clock=clock)
    return DiscordNotifier(store, back_again=back), store


def send_all(store, now):
    while (job := store.claim(now=now)) is not None:
        store.finish(job, "sent", message_id="123456789012345678")


def pending_jobs(store):
    with store._connect() as db:
        return [dict(row) for row in db.execute(
            "SELECT jobs.*,items.payload,items.first_seen,items.last_seen FROM jobs "
            "JOIN items USING(item_id) WHERE state='pending' AND route!='yesterday-recap'")]


def last_seen(store, item_id=BELT):
    with store._connect() as db:
        return db.execute("SELECT last_seen FROM items WHERE item_id=?", (item_id,)).fetchone()[0]


def keep_lists_covered(notifier, clock, until, step=240):
    """Other $80+ offers keep arriving, as the price_high/min_price=79 lists do."""
    while clock.now + step < until:
        clock.now += step
        notifier.ingest([offer("ip-99999999", 150.0)])
        send_all(notifier.store, clock.now)
    clock.now = until


def test_an_offer_back_after_an_hour_unseen_is_held_then_announced_once(tmp_path, clock):
    notifier, store = notifier_for(tmp_path, clock)
    first = notifier.ingest([offer()])
    assert first.new == 1 and first.confirming == 0
    send_all(store, clock.now)
    seen_at = clock.now
    keep_lists_covered(notifier, clock, seen_at + AWAY_SECONDS + 60)
    send_all(store, clock.now)

    held = notifier.ingest([offer(), offer("ip-99999999", 150.0)])
    assert held.seen == 2, "every observation is acknowledged"
    assert held.confirming == 1, "the extension re-reads the page about 10 s later"
    assert [stage for stage, *_ in held.back_again] == ["held"]
    assert pending_jobs(store) == []
    assert last_seen(store) == seen_at, "held, not recorded: the queue's clock stays put"

    clock.now += 5
    assert notifier.ingest([offer()]).confirming == 1, "too soon to confirm"
    clock.now += 5
    confirmed = notifier.ingest([offer()])
    assert confirmed.confirming == 0
    assert [(stage, item_id) for stage, item_id, _ in confirmed.back_again] == [("confirmed", BELT)]
    jobs = pending_jobs(store)
    assert jobs and all(job["item_id"] == BELT for job in jobs)
    job = store.claim(now=clock.now)
    assert job["item_id"] == BELT and job["due"] == clock.now
    assert "Back in stock" in json.dumps(render_batch([job]))
    store.finish(job, "sent", message_id="123456789012345679")
    send_all(store, clock.now)

    # Seen again and again afterwards: nothing more.
    for _ in range(3):
        clock.now += 30
        assert notifier.ingest([offer()]).confirming == 0
    assert pending_jobs(store) == []


def test_a_return_within_the_cooldown_is_not_announced_again(tmp_path, clock):
    notifier, store = notifier_for(tmp_path, clock)
    notifier.ingest([offer()])
    send_all(store, clock.now)
    for _ in range(2):
        keep_lists_covered(notifier, clock, clock.now + AWAY_SECONDS + 60)
        notifier.ingest([offer()])
        clock.now += 10
        notifier.ingest([offer()])
        send_all(store, clock.now)
    # The second return came 1 h after the first alert: inside the 6 h cooldown.
    assert notifier.back_again.cooldown[BELT] == pytest.approx(clock.now - AWAY_SECONDS - 70, abs=1)
    keep_lists_covered(notifier, clock, clock.now + COOLDOWN_SECONDS)
    assert notifier.ingest([offer()]).confirming == 1, "after 6 h a return is news again"


def test_no_second_sighting_drops_the_hold_and_a_later_sighting_starts_over(tmp_path, clock):
    notifier, store = notifier_for(tmp_path, clock)
    notifier.ingest([offer()])
    send_all(store, clock.now)
    keep_lists_covered(notifier, clock, clock.now + AWAY_SECONDS + 60)
    assert notifier.ingest([offer()]).confirming == 1
    keep_lists_covered(notifier, clock, clock.now + PENDING_SECONDS + 1)
    later = notifier.ingest([offer()])
    assert [stage for stage, *_ in later.back_again] == ["expired", "held"]
    assert pending_jobs(store) == []


def test_an_extension_restock_is_never_held_and_starts_the_cooldown(tmp_path, clock):
    notifier, store = notifier_for(tmp_path, clock)
    notifier.ingest([offer()])
    send_all(store, clock.now)
    keep_lists_covered(notifier, clock, clock.now + 2 * AWAY_SECONDS)
    flagged = notifier.ingest([offer(restocked=True)])
    assert flagged.confirming == 0 and flagged.back_again == []
    assert pending_jobs(store), "announced by the extension's own restock path"
    assert BELT in notifier.back_again.cooldown
    send_all(store, clock.now)
    # Gone again for over an hour: same day, so the comeback rule stays quiet.
    keep_lists_covered(notifier, clock, clock.now + AWAY_SECONDS + 60)
    assert notifier.ingest([offer()]).confirming == 0


def test_an_outage_of_the_lists_is_not_absence(tmp_path, clock):
    notifier, store = notifier_for(tmp_path, clock)
    notifier.ingest([offer()])
    send_all(store, clock.now)
    # Scanner (or backend) down for two hours: nothing $80+ arrives at all.
    clock.now += 2 * AWAY_SECONDS
    back = notifier.ingest([offer(), offer("ip-99999999", 150.0)])
    assert back.confirming == 0
    assert [stage for stage, *_ in back.back_again] == ["outage"]
    # Covered absence still counts: 65 min observed, then a 2 h outage, then back.
    keep_lists_covered(notifier, clock, clock.now + AWAY_SECONDS + 300)
    clock.now += 2 * AWAY_SECONDS
    assert notifier.ingest([offer()]).confirming == 1


def test_cheap_new_and_seed_observations_are_never_held(tmp_path, clock):
    notifier, store = notifier_for(tmp_path, clock)
    notifier.ingest([offer("ip-11111111", 79.99)])
    send_all(store, clock.now)
    keep_lists_covered(notifier, clock, clock.now + 2 * AWAY_SECONDS)
    assert notifier.ingest([offer("ip-11111111", 79.99)]).confirming == 0
    assert notifier.ingest([offer("ip-22222222", 300.0)]).confirming == 0, "new: the new path"
    notifier.ingest([offer()], seed_only=True)
    keep_lists_covered(notifier, clock, clock.now + 2 * AWAY_SECONDS)
    assert notifier.ingest([offer()], seed_only=True).confirming == 0


def test_pending_holds_cooldowns_and_coverage_survive_a_restart(tmp_path, clock):
    notifier, store = notifier_for(tmp_path, clock)
    notifier.ingest([offer()])
    send_all(store, clock.now)
    keep_lists_covered(notifier, clock, clock.now + AWAY_SECONDS + 60)
    assert notifier.ingest([offer()]).confirming == 1
    notifier.stop()
    clock.now += 12  # Restarted in between.
    restarted, _ = notifier_for(tmp_path, clock)
    assert [stage for stage, *_ in restarted.ingest([offer()]).back_again] == ["confirmed"]
    assert pending_jobs(store)
    again, _ = notifier_for(tmp_path, clock)
    assert BELT in again.back_again.cooldown
    assert again.back_again.heartbeat == clock.now


def test_a_batch_of_comebacks_is_routed_together_like_new_items(tmp_path, clock):
    notifier, store = notifier_for(tmp_path, clock)
    cases = [offer(f"ip-{14800000000 + n}", 145.0) for n in range(30)]
    notifier.ingest(cases)
    send_all(store, clock.now)
    keep_lists_covered(notifier, clock, clock.now + 3 * AWAY_SECONDS, step=OUTAGE_SECONDS - 30)
    assert notifier.ingest(cases).confirming == 30
    clock.now += 11
    routed = []
    notifier.monitor.emit = lambda event, **fields: routed.append((event, fields))
    assert notifier.ingest(cases).confirming == 0
    restocked = [fields for event, fields in routed
                 if event == "observation_routed" and fields.get("transition") == "restocked"]
    assert restocked and all(len(fields["item_ids"]) == 30 for fields in restocked), \
        "one routing group per route, bundled by the normal delivery"
    assert len(pending_jobs(store)) == 30 * len(restocked)


def test_a_missing_queue_database_never_fails_intake(tmp_path, clock):
    back = BackAgain(tmp_path / "back-again.json", tmp_path / "absent.sqlite3", clock=clock)
    rows = [{"item_id": BELT, "available": True, "value_cents": 9000}]
    assert back.plan(rows, set())[:2] == (set(), set())
    assert back.last_error == "OperationalError"
    assert not (tmp_path / "absent.sqlite3").exists()


def test_a_corrupt_state_file_starts_empty(tmp_path, clock):
    (tmp_path / "back-again.json").write_text("{not json")
    back = BackAgain(tmp_path / "back-again.json", tmp_path / "q.sqlite3", clock=clock)
    assert back.pending == {} and back.last_error == "JSONDecodeError"


# --- HTTP: receipts, the reply, and shutdown --------------------------------


@pytest.fixture
def http(tmp_path, monkeypatch, clock):
    monkeypatch.delenv("SEED_MODE", raising=False)
    notifier, store = notifier_for(tmp_path, clock)
    seen = SeenStore(tmp_path / "seen.json")
    monkeypatch.setattr(server.Handler, "notifier", notifier, raising=False)
    monkeypatch.setattr(server.Handler, "store", seen, raising=False)
    monkeypatch.setattr(server.Handler, "token", "", raising=False)
    monkeypatch.setattr(server.Handler, "runtime_status", server.RuntimeStatus(seen), raising=False)
    operations = OperationalLog(tmp_path / "ops")
    monkeypatch.setattr(server.Handler, "operations", operations, raising=False)
    monkeypatch.setattr(server.Handler, "intake_gate", server.IntakeGate(), raising=False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", notifier, store, tmp_path / "ops"
    finally:
        httpd.shutdown()
        httpd.server_close()
        seen.close()


def post(base, entries):
    request = urllib.request.Request(
        base + "/ingest", json.dumps({"items": entries}).encode(),
        {"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def wire(item_id=BELT, value=90.0):
    number = item_id.removeprefix("ip-")
    return {"item_id": item_id, "title": "Ostomy belt bundle", "value_usd": value,
            "url": f"https://www.walmart.com/ip/{number}", "available": True}


def test_http_reply_counts_held_offers_and_receipts_name_each_stage(http, clock):
    base, notifier, store, ops = http
    post(base, [wire()])
    send_all(store, clock.now)
    keep_lists_covered(notifier, clock, clock.now + AWAY_SECONDS + 60)
    reply = post(base, [wire(), wire("ip-99999999", 150.0)])
    assert reply["seen"] == 2 and reply["confirming"] == 1
    clock.now += 10
    assert post(base, [wire()])["confirming"] == 0
    server.Handler.operations.flush()
    rows = [json.loads(line) for line in (ops / "operations.jsonl").read_text().splitlines()]
    stages = [(row["stage"], row["item_id"], row["severity"]) for row in rows
              if row["event"] == "intake_back_again"]
    assert stages == [("held", BELT, "info"), ("confirmed", BELT, "info")]
    results = [row for row in rows if row["event"] == "intake_result"]
    assert [row.get("confirming") for row in results][-2:] == [1, 0]


def test_an_unpublishable_observation_is_logged_as_a_warning(http):
    base, *_, ops = http
    post(base, [wire(), {**wire("ip-1234568"), "url": "https://evil.example/ip/1234568"}])
    server.Handler.operations.flush()
    rows = [json.loads(line) for line in (ops / "operations.jsonl").read_text().splitlines()]
    dropped = [row for row in rows if row["event"] == "intake_invalid_item"]
    assert [row["severity"] for row in dropped] == ["warning"]


def test_a_stopping_backend_refuses_new_uploads_whole_and_finishes_the_ones_inside(http):
    base, notifier, store, _ = http
    gate = server.Handler.intake_gate
    assert gate.enter()  # An upload already inside.
    closed = []
    stopper = threading.Thread(target=lambda: closed.append(gate.close(timeout=5)))
    stopper.start()
    time.sleep(0.05)
    with pytest.raises(urllib.error.HTTPError) as refused:
        post(base, [wire("ip-55555555", 200.0)])
    assert refused.value.code == 503, "retryable: the extension keeps it queued"
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0, "nothing half-processed"
    assert closed == [], "shutdown waits for the upload inside"
    gate.leave()
    stopper.join(timeout=5)
    assert closed == [True]


CHEAP = "ip-30000000001"


def test_an_under_80_offer_back_after_a_day_is_announced_on_first_sighting(tmp_path, clock):
    from back_again import LOW_AWAY_SECONDS

    notifier, store = notifier_for(tmp_path, clock)
    notifier.ingest([offer(CHEAP, 25.0)])
    send_all(store, clock.now)
    keep_lists_covered(notifier, clock, clock.now + LOW_AWAY_SECONDS - 3600, step=500)
    early = notifier.ingest([offer(CHEAP, 25.0)])
    assert early.confirming == 0 and early.back_again == [], "23 h away: just a duplicate"
    send_all(store, clock.now)
    keep_lists_covered(notifier, clock, clock.now + LOW_AWAY_SECONDS + 1800, step=500)
    send_all(store, clock.now)
    back = notifier.ingest([offer(CHEAP, 25.0)])
    assert back.confirming == 0, "no hold under $80: department rounds are hours apart"
    assert [(stage, item_id) for stage, item_id, _ in back.back_again] == [("confirmed", CHEAP)]
    job = next(j for j in pending_jobs(store) if j["item_id"] == CHEAP)
    assert job["due"] == clock.now
    rendered = json.dumps(render_batch([store.claim(now=clock.now)]))
    assert "Back in stock" in rendered and "First listed" in rendered
    assert "Last observed" not in rendered


def test_under_80_comebacks_are_capped_per_hour(tmp_path, clock):
    from back_again import LOW_AWAY_SECONDS, LOW_MAX_PER_HOUR

    notifier, store = notifier_for(tmp_path, clock)
    cheap = [offer(f"ip-3000000{n:04d}", 20.0) for n in range(LOW_MAX_PER_HOUR + 5)]
    notifier.ingest(cheap)
    send_all(store, clock.now)
    keep_lists_covered(notifier, clock, clock.now + LOW_AWAY_SECONDS + 60, step=500)
    send_all(store, clock.now)
    back = notifier.ingest(cheap)
    assert sum(stage == "confirmed" for stage, *_ in back.back_again) == LOW_MAX_PER_HOUR


def test_new_alerts_show_first_detected_only():
    from discord_bridge.messages import render_message

    item = {"value_cents": 2500, "title": "Lamp", "category": "home", "item_id": "ip-1",
            "product_url": "https://www.walmart.com/ip/1",
            "review_url": "https://www.walmart.com/reviews/1"}
    text = json.dumps(render_message(item, first_seen=1_800_000_000, last_seen=1_800_000_900,
                                     route="pro-all"))
    assert "First detected" in text and "Last observed" not in text
