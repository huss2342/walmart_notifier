"""Nothing outside alert logic may stop, starve or silently drop channel alerts.

2026-11-25: an unrelated privacy-audit finding stopped every channel alert for
~80 minutes and $80+ jobs expired as "stale". These tests pin the guarantees
added afterwards: channel sends own the Discord budget, outages never drop
$80+ alerts, unrelated components fail alone, and a stall raises an alarm.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from discord_bridge import core
from discord_bridge.api import DeliveryOutcome, DiscordError, RateLimitSource
from discord_bridge.core import JobStore
from discord_bridge.delivery import deliver_due
from discord_bridge.delivery_watchdog import DeliveryWatchdog
from discord_bridge.monitoring import DeliveryMonitor
from discord_bridge.policy import (
    HIGH_VALUE_JOB_LIFETIME_SECONDS,
    JOB_LIFETIME_SECONDS,
    RESTOCK_COOLDOWN_SECONDS,
)
from tools import log_audit

from test_discord_burst_delivery import STATE
from test_discord_burst_store import only_route, product
from test_discord_transport import PausedResponse, SocketResponse, socket_client


def in_thread(name, call, *args, **kwargs):
    """Run one client call on a named thread (the client classifies callers by name)."""
    outcome = {}

    def target():
        try:
            outcome["value"] = call(*args, **kwargs)
        except BaseException as exc:  # re-raised on the test thread
            outcome["error"] = exc

    thread = threading.Thread(target=target, name=name)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def channel(call, *args, **kwargs):
    return in_thread("discord-route_0", call, *args, **kwargs)


def background(call, *args, **kwargs):
    return in_thread("pro-access", call, *args, **kwargs)


# --- Discord budget: channel sends first -----------------------------------


def test_background_callers_get_a_capped_share_and_channel_sends_keep_the_rest(monkeypatch):
    client, factory, _ = socket_client(monkeypatch, *(SocketResponse() for _ in range(26)))
    for index in range(15):
        background(client.request, "PUT", f"/guilds/111/members/{index}/roles/222")
    with pytest.raises(DiscordError) as refused:
        background(client.request, "PUT", "/guilds/111/members/99/roles/222")
    assert refused.value.status == 429
    assert refused.value.rate_limit_source is RateLimitSource.LOCAL
    assert refused.value.outcome is DeliveryOutcome.NOT_SUBMITTED
    assert refused.value.global_limit is False
    assert len(factory.requests) == 15
    for index in range(10):
        assert channel(client.request, "POST", f"/channels/{index}/messages", {}) == {}
    assert len(factory.requests) == 25
    assert client.priority_status()["background_deferrals"] == 1


def test_due_channel_alerts_shrink_the_background_share(monkeypatch):
    client, factory, clock = socket_client(monkeypatch, *(SocketResponse() for _ in range(30)))
    client.prioritize(10)
    for index in range(5):
        background(client.request, "GET", f"/guilds/{index}/members")
    with pytest.raises(DiscordError):
        background(client.request, "GET", "/guilds/9/members")
    for index in range(20):
        assert channel(client.request, "POST", f"/channels/{index}/messages", {}) == {}
    assert client.priority_status()["channel_demand"] is True
    clock[0] += 11  # demand lapses once delivery stops claiming it
    assert client.priority_status()["channel_demand"] is False
    for index in range(5):
        background(client.request, "GET", f"/guilds/{10 + index}/members")
    assert len(factory.requests) == 30


def test_priority_hint_rejects_invalid_or_unbounded_values(monkeypatch):
    client, _, clock = socket_client(monkeypatch)
    for bad in (float("nan"), float("inf"), -1, 0, "10", None):
        client.prioritize(bad)
    assert client.priority_status()["channel_demand"] is False
    client.prioritize(10_000)
    clock[0] += 61
    assert client.priority_status()["channel_demand"] is False


def test_discord_global_429_seen_by_background_pauses_only_background(monkeypatch):
    client, factory, clock = socket_client(
        monkeypatch,
        SocketResponse({"global": True, "retry_after": 3.25}, status=429),
        SocketResponse({"id": "1800000000000000001"}),
        SocketResponse(),
    )
    with pytest.raises(DiscordError) as limited:
        background(client.request, "GET", "/guilds/111/members")
    assert limited.value.rate_limit_source is RateLimitSource.SERVER
    assert limited.value.global_limit
    with pytest.raises(DiscordError) as paused:
        background(client.request, "GET", "/guilds/222/members")
    assert paused.value.rate_limit_source is RateLimitSource.LOCAL
    assert paused.value.retry_after == 3.25
    # The channel send is not deferred by background work.
    assert channel(client.cooldown_delay, "POST", "/channels/333/messages") == 0
    assert channel(client.retry_delay, "POST", "/channels/333/messages") == 0
    assert channel(client.request, "POST", "/channels/333/messages", {}) == {
        "id": "1800000000000000001"
    }
    assert client.priority_status()["background_paused_seconds"] == 3.25
    clock[0] += 3.25
    assert background(client.request, "GET", "/guilds/222/members") == {}
    assert len(factory.requests) == 3


def test_discord_global_429_on_a_channel_send_is_still_respected_by_everyone(monkeypatch):
    client, factory, clock = socket_client(
        monkeypatch,
        SocketResponse({"global": True, "retry_after": 2.0}, status=429),
        SocketResponse(),
    )
    with pytest.raises(DiscordError):
        channel(client.request, "POST", "/channels/111/messages", {})
    for caller in (channel, background):
        with pytest.raises(DiscordError) as gated:
            caller(client.request, "POST", "/channels/222/messages", {})
        assert gated.value.rate_limit_source is RateLimitSource.LOCAL
        assert gated.value.global_limit
    assert channel(client.cooldown_delay, "POST", "/channels/222/messages") == 2.0
    clock[0] += 2
    assert channel(client.request, "POST", "/channels/222/messages", {}) == {}
    assert len(factory.requests) == 2


def test_background_holds_at_most_two_sockets_and_never_the_channel_ones(monkeypatch):
    paused = [PausedResponse(), PausedResponse()]
    client, factory, _ = socket_client(
        monkeypatch, *paused, *(SocketResponse() for _ in range(4))
    )
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="discord-trials") as workers:
        pending = [
            workers.submit(client.request, "GET", f"/guilds/{index}/members")
            for index in range(2)
        ]
        try:
            for response in paused:
                assert response.entered.wait(timeout=5)
            with pytest.raises(DiscordError) as busy:
                background(client.request, "GET", "/guilds/9/members")
            assert busy.value.status == 429 and busy.value.global_limit is False
            # All four channel sends still go out while background holds two.
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="discord-route") as sends:
                results = list(sends.map(
                    lambda index: client.request("POST", f"/channels/{index}/messages", {}),
                    range(4),
                ))
            assert results == [{}] * 4
        finally:
            for response in paused:
                response.release.set()
        assert [future.result(timeout=5) for future in pending] == [{}, {}]
    assert len(factory.requests) == 6
    assert client.priority_status()["background_inflight"] == 0


def test_background_pending_429_body_never_holds_a_channel_send(monkeypatch):
    paused = PausedResponse(status=429, data={"global": True, "retry_after": 2.5})
    client, factory, _ = socket_client(monkeypatch, paused, SocketResponse())
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="free-role") as workers:
        pending = workers.submit(client.request, "GET", "/guilds/111/members")
        try:
            assert paused.entered.wait(timeout=5)
            with pytest.raises(DiscordError):
                background(client.request, "GET", "/guilds/222/members")
            assert channel(client.request, "POST", "/channels/333/messages", {}) == {}
        finally:
            paused.release.set()
        with pytest.raises(DiscordError):
            pending.result(timeout=5)
    assert len(factory.requests) == 2
    assert channel(client.cooldown_delay, "POST", "/channels/333/messages") == 0


# --- Delivery: local refusals are not Discord's limits ------------------------


class Refusing:
    """One refusal, then success; records priority hints."""

    def __init__(self, error):
        self.error, self.calls, self.hints = error, [], []

    def prioritize(self, seconds):
        self.hints.append(seconds)

    def request(self, method, route, payload):
        self.calls.append(route)
        if len(self.calls) == 1:
            raise self.error
        return {"id": str(1800000000000000000 + len(self.calls))}


def global_gate(store):
    with store._connect() as db:
        return JobStore._global_until(db)


def test_local_global_refusal_never_persists_a_gate_that_pauses_every_route(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    only_route(store, "pro-all")
    remote = Refusing(DiscordError(
        status=429, retry_after=0.5, global_limit=True,
        outcome=DeliveryOutcome.NOT_SUBMITTED, rate_limit_source=RateLimitSource.LOCAL,
    ))
    result = deliver_due(store, remote, STATE, live=True, now=1000,
                         auditor=lambda *_: {"ok": True}, limit=1)
    assert result["throttled"] == 1
    assert global_gate(store) == 0
    assert store.delivery_metrics(now=1000)["active_route_cooldowns"] == 0
    assert remote.hints and remote.hints[0] > 0
    sent = deliver_due(store, remote, STATE, live=True, now=1001,
                       auditor=lambda *_: {"ok": True})
    assert sent["sent"] == 1


def test_discord_global_429_on_a_channel_send_still_defers_every_route(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    only_route(store, "pro-all")
    remote = Refusing(DiscordError(
        status=429, retry_after=30, global_limit=True, rate_limit_source=RateLimitSource.SERVER,
    ))
    deliver_due(store, remote, STATE, live=True, now=1000, auditor=lambda *_: {"ok": True},
                limit=1)
    assert global_gate(store) == 1030


class Down:
    def __init__(self):
        self.calls = 0

    def request(self, method, route, payload):
        self.calls += 1
        raise DiscordError("Discord returned HTTP 503", status=503)


class Accepting:
    def __init__(self):
        self.calls = []

    def request(self, method, route, payload=None):
        self.calls.append((method, route, payload))
        return {"id": str(1800000000000000000 + len(self.calls))}


def job_rows(store):
    with store._connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY route")]


@pytest.mark.parametrize(("value_usd", "kept"), [(120.0, True), (19.99, False)])
def test_an_outage_never_exhausts_an_80_plus_alert_before_it_expires(tmp_path, value_usd, kept):
    store = JobStore(tmp_path / "q")
    store.record([product(1, value_usd=value_usd)], now=1000)
    only_route(store, "pro-all")
    with store._connect() as db:
        db.execute("UPDATE jobs SET attempts=10 WHERE route='pro-all'")
    deliver_due(store, Down(), STATE, live=True, now=1000, auditor=lambda *_: {"ok": True},
                limit=1)
    (job,) = [row for row in job_rows(store) if row["route"] == "pro-all"]
    if kept:
        assert job["state"] == "pending" and job["reason"] == "remote_retry"
        assert job["next_attempt"] == 1000 + 256  # 2**8 backoff, never above 300 s
    else:
        assert job["state"] == "failed" and job["reason"] == "http_503"  # retry limit hit


def test_definitive_rejections_still_fail_80_plus_alerts_at_once(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1, value_usd=120.0)], now=1000)
    only_route(store, "pro-all")

    class Forbidden:
        def request(self, *_):
            raise DiscordError("Discord returned HTTP 403", status=403)

    deliver_due(store, Forbidden(), STATE, live=True, now=1000,
                auditor=lambda *_: {"ok": True}, limit=1)
    (job,) = [row for row in job_rows(store) if row["route"] == "pro-all"]
    assert job["state"] == "failed" and job["reason"] == "http_403"


# --- Expiry: $80+ alerts survive an outage ----------------------------------


def expiries(store):
    return {row["route"]: row["expires"] for row in job_rows(store)}


def test_fresh_80_plus_jobs_live_three_hours_and_cheaper_ones_keep_thirty_minutes(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1, value_usd=120.0), product(2, value_usd=19.99)], now=1000)
    with store._connect() as db:
        rows = db.execute("SELECT item_id,route,due,expires FROM jobs").fetchall()
    for row in rows:
        if row["route"] == "yesterday-recap":
            continue
        lifetime = (
            HIGH_VALUE_JOB_LIFETIME_SECONDS
            if row["item_id"] == "ip-10000001" else JOB_LIFETIME_SECONDS
        )
        assert row["expires"] == 1000 + lifetime, dict(row)
    assert {row["route"] for row in rows if row["item_id"] == "ip-10000001"} >= {
        "pro-all", "price-80-200"
    }


def test_80_plus_alert_queued_before_an_80_minute_outage_is_sent_late_not_dropped(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1, value_usd=95.0), product(2, value_usd=19.99)], now=1000)
    only_route(store, "pro-all")
    outage_end = 1000 + 80 * 60
    result = deliver_due(store, Accepting(), STATE, live=True, now=outage_end,
                         auditor=lambda *_: {"ok": True})
    assert result["sent"] == 1
    states = {
        (row["item_id"], row["state"], row["reason"])
        for row in job_rows(store) if row["route"] == "pro-all"
    }
    assert states == {("ip-10000001", "sent", None), ("ip-10000002", "expired", "stale")}
    assert store.delivery_metrics(now=outage_end)["reasons"]["expired"]["stale"] == 1


def test_an_80_plus_job_still_expires_after_three_hours(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1, value_usd=95.0)], now=1000)
    only_route(store, "pro-all")
    assert not store.has_due(now=1000 + HIGH_VALUE_JOB_LIFETIME_SECONDS)
    assert {row["state"] for row in job_rows(store) if row["route"] == "pro-all"} == {"expired"}


def test_an_old_item_gaining_a_route_is_not_revived_by_the_longer_lifetime(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1, value_usd=70.0)], now=1000)
    store.record([product(1, value_usd=90.0)], now=1000 + 3600)
    assert expiries(store)["price-80-200"] == 1000 + JOB_LIFETIME_SECONDS
    assert not store.has_due(now=1000 + 3600)


def test_an_80_plus_restock_rearms_with_the_longer_lifetime(tmp_path):
    store = JobStore(tmp_path / "q")
    item = product(1, value_usd=120.0)
    store.record([item], now=1000)
    with store._connect() as db:
        db.execute("UPDATE jobs SET state='sent',sent_at=1001 WHERE route!='yesterday-recap'")
    later = 1000 + RESTOCK_COOLDOWN_SECONDS + 1
    store.record([item], now=later, restocked={item["item_id"]})
    rearmed = [row for row in job_rows(store) if row["route"] == "pro-all"][0]
    assert rearmed["state"] == "pending" and rearmed["due"] == later
    assert rearmed["expires"] == later + HIGH_VALUE_JOB_LIFETIME_SECONDS


def test_startup_extends_80_plus_jobs_queued_before_the_upgrade(tmp_path):
    store = JobStore(tmp_path / "q")
    now = time.time()
    store.record([product(1, value_usd=120.0), product(2, value_usd=19.99)], now=now - 60)
    with store._connect() as db:
        db.execute(
            "UPDATE jobs SET expires=due+? WHERE route!='yesterday-recap'",
            (JOB_LIFETIME_SECONDS,),
        )
        db.execute(
            "INSERT INTO items VALUES ('ip-10000003',?,?,?)",
            (json.dumps(product(3, value_usd=150.0)), now - 7200, now - 7200),
        )
        db.execute(
            "INSERT INTO jobs (id,item_id,route,due,expires,next_attempt,state,reason) "
            "VALUES ('dead00000000000000000000a','ip-10000003','pro-all',?,?,?, "
            "'expired','stale')",
            (now - 7200, now - 7200 + JOB_LIFETIME_SECONDS, now - 7200),
        )
    reopened = JobStore(tmp_path / "q")
    by_item = {}
    for row in job_rows(reopened):
        if row["route"] == "pro-all":
            by_item[row["item_id"]] = row
    high, low, dead = by_item["ip-10000001"], by_item["ip-10000002"], by_item["ip-10000003"]
    assert high["expires"] == pytest.approx(high["due"] + HIGH_VALUE_JOB_LIFETIME_SECONDS)
    assert low["expires"] == pytest.approx(low["due"] + JOB_LIFETIME_SECONDS)
    assert dead["state"] == "expired"  # nothing already dropped is revived


# --- Intake: unrelated parts cannot refuse a batch ----------------------------


def test_personal_request_matching_failure_still_queues_channel_jobs(tmp_path, monkeypatch):
    store = JobStore(tmp_path / "q")
    events = []

    def broken(db, *_args, **_kwargs):
        db.execute("CREATE TABLE IF NOT EXISTS request_side_effect (x)")
        raise RuntimeError("request matcher bug")

    monkeypatch.setattr(core, "match_requests", broken)
    result = store.record(
        [product(1, value_usd=120.0)], now=1000,
        event_sink=lambda event, **fields: events.append((event, fields)),
    )
    assert result["queued"] == 1
    assert {row["route"] for row in job_rows(store)} >= {"pro-all", "price-80-200"}
    with store._connect() as db:
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='request_side_effect'"
        ).fetchone() is None  # only the request part rolled back
    failed = [fields for event, fields in events if event == "component_failed"]
    assert failed == [{"component": "personal", "phase": "intake", "error_type": "RuntimeError"}]


# --- Watchdog -----------------------------------------------------------------


class Snapshots:
    def __init__(self):
        self.value = {"due_items": 0, "oldest_due_at": None, "last_sent_at": None}
        self.fail = False

    def stall_snapshot(self, *, now):
        if self.fail:
            raise OSError("disk")
        return dict(self.value)


def watchdog(store, **options):
    events, notices = [], []
    results = [options.pop("delivered", True)]

    def notify(**fields):
        notices.append(fields)
        return results.pop(0) if results else True

    dog = DeliveryWatchdog(
        store,
        event_sink=lambda event, **fields: events.append((event, fields)),
        notify=notify,
        **options,
    )
    return dog, events, notices


def test_watchdog_alarms_once_repeats_slowly_notifies_owner_once_and_recovers():
    store = Snapshots()
    dog, events, notices = watchdog(store)
    store.value = {"due_items": 3, "oldest_due_at": 1000, "last_sent_at": 900}
    assert dog.check(now=1200)["stalled"] is False  # oldest due only 200 s old
    status = dog.check(now=1400)
    assert status["stalled"] and status["due_items"] == 3 and status["alarms"] == 1
    assert status["owner_notified"] is True
    assert [event for event, _ in events] == ["delivery_stalled"]
    assert events[0][1]["oldest_due_age_seconds"] == 400
    assert events[0][1]["last_sent_age_seconds"] == 500
    dog.check(now=1500)
    dog.check(now=1400 + 899)
    assert len(events) == 1 and len(notices) == 1
    dog.check(now=1400 + 900)
    assert [event for event, _ in events] == ["delivery_stalled"] * 2
    assert len(notices) == 1  # one owner notice per episode
    store.value = {"due_items": 1, "oldest_due_at": 2200, "last_sent_at": 2300}
    status = dog.check(now=2310)
    assert status["stalled"] is False and status["owner_notified"] is False
    assert events[-1][0] == "delivery_recovered" and events[-1][1]["resumed"] is True


def test_watchdog_retries_a_failed_owner_notice_and_never_raises():
    store = Snapshots()
    dog, events, notices = watchdog(store, delivered=False)
    store.value = {"due_items": 1, "oldest_due_at": 0, "last_sent_at": None}
    assert dog.check(now=1000)["owner_notified"] is False
    assert dog.check(now=1030)["owner_notified"] is True  # second notice delivered
    assert len(notices) == 2
    store.fail = True
    status = dog.check(now=1060)
    assert status["last_error"] == "OSError"
    assert events[-1][0] == "worker_error"


def test_watchdog_ignores_quiet_queues_and_recent_sends():
    store = Snapshots()
    dog, events, _ = watchdog(store)
    store.value = {"due_items": 0, "oldest_due_at": None, "last_sent_at": 0}
    assert dog.check(now=10_000)["stalled"] is False
    store.value = {"due_items": 5, "oldest_due_at": 0, "last_sent_at": 9_990}
    assert dog.check(now=10_000)["stalled"] is False  # sending, just behind
    assert events == []


def test_watchdog_tick_revives_and_heartbeats_without_letting_failures_escape():
    store = Snapshots()
    calls = []

    def revive():
        calls.append("revive")
        return True

    def heartbeat():
        calls.append("heartbeat")
        raise RuntimeError("log disk")

    dog = DeliveryWatchdog(store, revive=revive, heartbeat=heartbeat)
    status = dog.tick()
    assert calls == ["revive", "heartbeat"] and status["delivery_restarts"] == 1


def test_watchdog_reads_real_queue_aggregates(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1, value_usd=120.0)], now=1000)
    snapshot = store.stall_snapshot(now=1100)
    assert snapshot["due_items"] >= 1 and snapshot["oldest_due_at"] == 1000
    assert snapshot["last_sent_at"] is None


def test_watchdog_bounds_are_validated():
    for bad in ({"stall_seconds": 0}, {"interval": float("nan")}, {"repeat_seconds": -1}):
        with pytest.raises(ValueError):
            DeliveryWatchdog(Snapshots(), **bad)


# --- Monitoring allowlists ----------------------------------------------------


def test_new_events_and_fields_are_logged_and_read_back(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    assert monitor.emit("delivery_stalled", component="channel", due_items=4,
                        stalled_seconds=310.5, oldest_due_age_seconds=320.0,
                        last_sent_age_seconds=330.0)
    assert monitor.emit("delivery_recovered", component="channel", resumed=True)
    assert monitor.emit("component_failed", component="elite-signal", phase="intake",
                        error_type="RuntimeError")
    assert monitor.emit("worker_lifecycle", component="channel", transition="restarted")
    rows = [json.loads(line) for line in monitor.path.read_text().splitlines()]
    assert rows[0]["due_items"] == 4 and rows[0]["stalled_seconds"] == 310.5
    assert rows[0]["last_sent_age_seconds"] == 330.0
    assert rows[1]["resumed"] is True
    assert (rows[2]["component"], rows[2]["phase"]) == ("elite-signal", "intake")
    assert rows[3]["transition"] == "restarted"
    assert {"delivery_stalled", "delivery_recovered", "component_failed"} <= log_audit.EVENTS
    assert {"stalled_seconds", "due_items"} <= log_audit.NUMBERS
    assert "restarted" in log_audit.LABELS["transition"]


# --- Collector notifier: optional components fail alone -----------------------


def mapping():
    return {
        "guild_id": "1562530389866717295",
        "channels": {**STATE["channels"], "collector-health": "1700000000000000099"},
        "roles": {},
    }


def notifier(tmp_path, **options):
    from notifiers.discord import DiscordNotifier

    return DiscordNotifier(
        JobStore(tmp_path / "discord.sqlite3"),
        client=Accepting(),
        state=mapping(),
        live=True,
        auditor=lambda *_: {"ok": True},
        **options,
    )


def walmart_item(number=1, value=120.0):
    from models import Item

    return Item(
        title=f"Standing desk {number}",
        item_id=f"ip-{20000000 + number}",
        value_usd=value,
        url=f"https://www.walmart.com/ip/{20000000 + number}",
        category="furniture",
        source="extension",
    )


def test_an_optional_worker_that_cannot_be_built_never_disables_channel_alerts(
    tmp_path, monkeypatch
):
    import discord_bridge.welcome

    class Broken:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("welcome database unreadable")

    monkeypatch.setattr(discord_bridge.welcome, "WelcomeWorker", Broken)
    bot = notifier(tmp_path, welcome_enabled=True)
    assert bot.welcome is None and bot.component_errors == {"welcome": "RuntimeError"}
    assert bot.ingest([walmart_item()]).queued == 1
    assert bot.deliver_once()["sent"] >= 1
    health = bot.health()
    assert health["components"]["failed"] == {"welcome": "RuntimeError"}
    assert health["welcome"] == {"enabled": False, "configured": False}
    assert health["watchdog"]["enabled"] is True


def test_an_optional_worker_that_fails_to_start_leaves_delivery_running(tmp_path, monkeypatch):
    import discord_bridge.membership

    class NoStart:
        _thread = None

        def __init__(self, *_args, **_kwargs):
            self.referrals_enabled = False

        def start(self):
            raise RuntimeError("member list unavailable")

        def stop(self):
            raise RuntimeError("already gone")

        def health(self):
            raise RuntimeError("broken health")

    monkeypatch.setattr(discord_bridge.membership, "MembershipWorker", NoStart)
    bot = notifier(tmp_path, membership_enabled=True)
    bot.ingest([walmart_item()])
    bot.start()
    try:
        deadline = time.monotonic() + 5
        while not bot.client.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert bot.client.calls, "delivery did not run"
        assert bot._thread.is_alive()
        assert bot.component_errors["membership"] == "RuntimeError"
        assert bot.health()["membership"]["health_error"] == "RuntimeError"
    finally:
        bot.stop()


def test_an_auto_claim_hand_off_failure_never_refuses_an_intake(tmp_path):
    class BrokenElite:
        def capture(self, _sink):
            raise RuntimeError("elite queue gone")

        def offer(self, *_args):
            raise AssertionError("never reached without a capture")

    bot = notifier(tmp_path, elite_signal=BrokenElite())
    summary = bot.ingest([walmart_item()])
    assert summary.queued == 1
    assert bot.component_errors == {"elite-signal": "RuntimeError"}


def test_post_commit_bookkeeping_failures_never_fail_an_accepted_intake(tmp_path):
    class Flaky:
        lock = threading.Lock()

        def plan(self, public, flagged):
            raise RuntimeError("back-again file corrupt")

        def settle(self, *_args):
            raise AssertionError("settle is skipped once plan failed")

    bot = notifier(tmp_path, back_again=Flaky())

    class BrokenRequests:
        def wake(self):
            raise RuntimeError("request worker gone")

    bot.requests = BrokenRequests()
    summary = bot.ingest([walmart_item()])
    assert summary.queued == 1
    assert set(bot.component_errors) == {"back-again", "request-runtime"}


def test_watchdog_revives_a_dead_delivery_thread_but_not_after_stop(tmp_path):
    bot = notifier(tmp_path)
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    bot._thread = dead
    assert bot._revive_delivery() is True
    try:
        assert bot._thread.is_alive() and bot._thread.name == "discord-delivery"
        assert bot._revive_delivery() is False
    finally:
        bot.stop()
    assert bot._revive_delivery() is False


def test_stall_notice_goes_only_to_the_private_owner_channel_without_mentions(tmp_path):
    bot = notifier(tmp_path)
    assert bot._notify_stall(due_items=4, oldest_due_age_seconds=600,
                             last_sent_age_seconds=900) is True
    ((method, route, payload),) = bot.client.calls
    assert (method, route) == ("POST", "/channels/1700000000000000099/messages")
    assert payload["allowed_mentions"] == {"parse": []}
    assert "4 due" in payload["content"] and "15 min ago" in payload["content"]


def test_channel_sends_keep_their_sockets_while_other_callers_hold_theirs(monkeypatch):
    paused = [PausedResponse() for _ in range(4)]
    client, factory, _ = socket_client(monkeypatch, *paused, SocketResponse())
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="interaction") as workers:
        pending = [
            workers.submit(client.request, "GET", f"/channels/{index}") for index in range(4)
        ]
        try:
            for response in paused:
                assert response.entered.wait(timeout=5)
            with pytest.raises(DiscordError):  # the standard share is full
                client.request("GET", "/channels/9")
            assert channel(client.request, "POST", "/channels/8/messages", {}) == {}
        finally:
            for response in paused:
                response.release.set()
        assert [future.result(timeout=5) for future in pending] == [{}] * 4
    assert len(factory.requests) == 5
