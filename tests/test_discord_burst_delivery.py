"""Offline behavior of the actual parallel dispatcher; never flood Discord."""

import json
import threading
import time

from discord_bridge.api import DiscordError
from discord_bridge.audit_guard import AuditGuard
from discord_bridge.core import JobStore
from discord_bridge.delivery import deliver_due

from test_discord_burst_store import only_route, product

STATE = {
    "guild_id": "1562530389866717295",
    "channels": {
        "pro-all": "1700000000000000001",
        "type-electronics": "1700000000000000002",
        "price-10-20": "1700000000000000003",
        "starter-items": "1700000000000000004",
        "yesterday-recap": "1700000000000000005",
        "type-pets": "1700000000000000006",
    },
}


class Remote:
    def __init__(self, *, blocked=None, global_limit=False, delay=0, require_overlap=False):
        self.calls, self.blocked, self.global_limit = [], blocked, global_limit
        self.delay = delay
        self.active = self.peak = 0
        self.lock = threading.Lock()
        self.started = 0
        self.overlap = threading.Barrier(2) if require_overlap else None
        self.overlap_routes = []

    def request(self, method, route, payload):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.started += 1
            synchronize = self.overlap is not None and self.started <= 2
            if synchronize:
                self.overlap_routes.append(route)
        try:
            if synchronize:
                # The next SQLite claim may take longer than a simulated HTTP
                # sleep. Hold the first request until another route actually
                # enters HTTP; a serialized dispatcher fails within five seconds.
                self.overlap.wait(timeout=5)
            time.sleep(self.delay)
            if self.global_limit or (self.blocked is not None and self.blocked in route):
                raise DiscordError(status=429, retry_after=30, global_limit=self.global_limit)
            with self.lock:
                self.calls.append((route, payload))
                return {"id": str(1800000000000000000 + len(self.calls))}
        finally:
            with self.lock:
                self.active -= 1


def run(store, remote, now=1000, **options):
    return deliver_due(
        store, remote, STATE, live=True, now=now, auditor=lambda *_: {"ok": True}, **options
    )


def test_definitive_413_adapts_fresh_batch_without_looping_or_losing_items(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(i) for i in range(10)], now=1000)
    only_route(store, "pro-all")

    class PayloadLimited(Remote):
        def __init__(self):
            super().__init__()
            self.rejected = []

        def request(self, method, route, payload):
            if len(payload["embeds"]) > 5:
                self.rejected.append(payload["nonce"])
                raise DiscordError(status=413)
            return super().request(method, route, payload)

    remote = PayloadLimited()
    assert run(store, remote)["sent"] == 10
    assert len(remote.rejected) == 1
    assert len(remote.calls) == 2
    assert {payload["nonce"] for _, payload in remote.calls}.isdisjoint(remote.rejected)


def test_burst_reduces_message_count_and_overlaps_independent_channels(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(i) for i in range(30)], now=1000)
    remote = Remote(require_overlap=True)
    result = run(store, remote)
    assert result["sent"] == 90 and result["messages"] == 9
    assert 2 <= remote.peak <= 4
    assert len(set(remote.overlap_routes)) == 2
    assert store.counts() == {"sent": 90, "pending": 30}
    assert len({payload["nonce"] for _, payload in remote.calls}) == 9
    assert all(len(payload["embeds"]) == 10 for _, payload in remote.calls)


def test_throttled_channel_does_not_stall_healthy_routes_or_burn_failure_budget(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(i) for i in range(10)], now=1000)
    remote = Remote(blocked=STATE["channels"]["pro-all"])
    result = run(store, remote)
    assert result["sent"] == 20 and result["retried"] == 10
    assert result["failed"] == 0
    with store._connect() as db:
        assert db.execute("SELECT SUM(attempts) FROM jobs WHERE route='pro-all'").fetchone()[0] == 0
    assert len(remote.calls) == 2


def test_global_throttle_survives_restart_without_new_send_or_audit(tmp_path):
    path = tmp_path / "q"
    store = JobStore(path)
    store.record([product(i) for i in range(10)], now=1000)
    assert run(store, Remote(global_limit=True))["failed"] == 0
    calls = []
    restarted = JobStore(path)
    result = deliver_due(
        restarted, Remote(), STATE, live=True, now=1020, auditor=lambda *_: calls.append(True)
    )
    assert result["sent"] == 0 and result["wait_seconds"] == 10
    assert calls == []
    assert run(restarted, Remote(), now=1030)["sent"] == 30


def test_due_free_history_waits_until_current_paid_backlog_drains(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(999, available=False)], now=1000)
    store.record([product(i) for i in range(30)], now=87400)
    remote = Remote()
    result = run(store, remote, now=87400, limit=4)
    assert result["sent"] == 40
    assert all(STATE["channels"]["yesterday-recap"] not in route for route, _ in remote.calls)
    assert run(store, remote, now=87400)["sent"] == 51
    history = next(
        payload for route, payload in remote.calls if STATE["channels"]["yesterday-recap"] in route
    )
    assert "Historical" in json.dumps(history)


def test_slow_free_send_cannot_block_the_next_same_channel_pro_envelope(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(999, available=False)], now=1000)
    store.record([product(i) for i in range(20)], now=87400)
    with store._connect() as db:
        db.execute(
            "UPDATE jobs SET state='skipped' WHERE route NOT IN ('pro-all','yesterday-recap')"
        )

    class SlowFree(Remote):
        def __init__(self):
            super().__init__()
            self.pro_requests = 0
            self.second_pro_started = threading.Event()
            self.free_blocked_paid = False

        def request(self, method, route, payload):
            if STATE["channels"]["pro-all"] in route:
                self.pro_requests += 1
                if self.pro_requests == 2:
                    self.second_pro_started.set()
            elif STATE["channels"]["yesterday-recap"] in route:
                # The old dispatcher waits for this Free request before it can
                # start the second ready Pro envelope on the same channel.
                self.free_blocked_paid = not self.second_pro_started.wait(timeout=0.25)
            return super().request(method, route, payload)

    remote = SlowFree()
    assert run(store, remote, now=87400)["sent"] == 21
    assert remote.pro_requests == 2
    assert remote.free_blocked_paid is False


def test_due_free_does_not_cause_audit_spins_while_paid_waits_on_learned_cooldown(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(999, available=False)], now=1000)
    store.record([product(1)], now=87400)
    with store._connect() as db:
        db.execute(
            "UPDATE jobs SET state='skipped' WHERE route NOT IN ('pro-all','yesterday-recap')"
        )
    remote, audits = Remote(), []
    remote.cooldown_delay = lambda _method, path: 17 if STATE["channels"]["pro-all"] in path else 0
    result = deliver_due(
        store,
        remote,
        STATE,
        live=True,
        now=87400,
        auditor=lambda *_: audits.append(True) or {"ok": True},
    )
    assert result["sent"] == 0 and result["wait_seconds"] == 17
    assert audits == remote.calls == []


def test_future_free_and_expired_pro_jobs_do_not_hold_due_free_history(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(999, available=False)], now=1000)
    store.record([product(1)], now=85600)  # Paid expiry is exactly87400.
    store.record([product(2)], now=87300)  # Free release is173700.
    with store._connect() as db:
        db.execute(
            "UPDATE jobs SET state='skipped' WHERE item_id=? "
            "AND route!='yesterday-recap'",
            (product(2)["item_id"],),
        )
    remote = Remote()
    assert run(store, remote, now=87400)["sent"] == 1
    assert all(STATE["channels"]["yesterday-recap"] in route for route, _ in remote.calls)


def test_stop_during_audit_does_not_claim_or_submit_products(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    stopping = threading.Event()

    def audited(*_):
        stopping.set()
        return {"ok": True}

    remote = Remote()
    result = deliver_due(
        store, remote, STATE, live=True, now=1000, auditor=audited, stop_event=stopping
    )
    assert result["sent"] == 0 and remote.calls == []
    assert store.counts() == {"pending": 4}


def test_audit_failure_never_blocks_any_channel(tmp_path):
    # 2026-11-25: a managed Server Booster role failed the audit and the
    # fail-closed gate stopped every alert for ~80 minutes. Findings now warn.
    store = JobStore(tmp_path / "q")
    store.record([product(i) for i in range(10)], now=1000)
    remote, events = Remote(), []
    result = deliver_due(
        store, remote, STATE, live=True, now=1000,
        auditor=lambda *_: {"ok": False, "findings": ["Role 1700000000000000099: privileged."]},
        event_sink=lambda name, **fields: events.append((name, fields)),
    )
    assert result["blocked"] is False and result["sent"] == 30 and remote.calls
    names = [name for name, _ in events]
    assert "privacy_blocked" not in names and names.count("privacy_warning") == 1
    warning = next(fields for name, fields in events if name == "privacy_warning")
    assert warning["reason"] == "privacy_audit" and warning["findings_count"] == 1


def test_audit_429_warns_and_still_sends_without_a_global_defer(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    only_route(store, "pro-all")
    events = []

    def limited(*_):
        raise DiscordError(status=429, retry_after=17)

    result = deliver_due(
        store, Remote(), STATE, live=True, now=1000, auditor=limited,
        event_sink=lambda name, **fields: events.append((name, fields)),
    )
    assert result["sent"] == 1 and result["throttled"] == 0 and not result["blocked"]
    warning = next(fields for name, fields in events if name == "privacy_warning")
    assert warning["reason"] == "audit_error" and warning["status"] == 429
    assert "audit_throttled" not in [name for name, _ in events]
    with store._connect() as db:
        assert db.execute("SELECT SUM(attempts) FROM jobs").fetchone()[0] == 1


def test_single_alert_keeps_original_format_and_has_no_fill_delay(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(1)], now=1000)
    only_route(store, "pro-all")
    remote = Remote()
    assert run(store, remote)["sent"] == 1
    payload = remote.calls[0][1]
    assert "content" not in payload
    assert "components" not in payload
    assert "sampling" in payload["embeds"][0]["url"].lower()


def test_old_quiet_pro_category_cannot_starve_behind_three_fresh_paid_channels(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(998, available=False)], now=1000)
    store.record([product(999, category="pets")], now=87000)
    with store._connect() as db:
        db.execute(
            "UPDATE jobs SET state='skipped' WHERE item_id=? AND route!='type-pets'",
            (product(999)["item_id"],),
        )
    # Pro/all/category/value remain freshly replenished throughout the cycle.
    store.record([product(i) for i in range(30)], now=87400)
    remote = Remote()
    run(store, remote, now=87400, limit=4)
    assert any(STATE["channels"]["type-pets"] in route for route, _ in remote.calls)


def test_privacy_audit_runs_once_per_call_not_before_every_wave(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(i) for i in range(30)], now=1000)
    remote, audits = Remote(), []

    def audited(*_):
        audits.append(len(remote.calls))
        return {"ok": True}

    result = deliver_due(store, remote, STATE, live=True, now=1000, auditor=audited)
    assert result["sent"] == 90 and result["messages"] == 9
    assert audits == [0]


def test_failing_audit_does_not_stop_later_waves(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(i) for i in range(30)], now=1000)
    remote, audits = Remote(), []

    def audited(*_):
        audits.append(True)
        return {"ok": False}

    result = deliver_due(store, remote, STATE, live=True, now=1000, auditor=audited)
    assert result["blocked"] is False
    assert result["sent"] == 90 and len(remote.calls) == 9
    assert len(audits) == 1
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE state='leased'").fetchone()[0] == 0


def test_learned_post_cooldown_skips_idle_privacy_audit_without_claiming(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(i) for i in range(10)], now=1000)
    only_route(store, "pro-all")
    remote, audits = Remote(), []
    remote.cooldown_delay = lambda *_: 17

    def forbidden(*_):
        raise AssertionError("local request-budget/socket hints are not provider cooldowns")

    remote.retry_delay = forbidden
    result = deliver_due(
        store,
        remote,
        STATE,
        live=True,
        now=1000,
        auditor=lambda *_: audits.append(True) or {"ok": True},
    )
    assert result["sent"] == 0 and result["wait_seconds"] == 17
    assert remote.calls == audits == []
    with store._connect() as db:
        assert db.execute("SELECT SUM(attempts) FROM jobs").fetchone()[0] == 0
    assert JobStore(store.path).next_due_in(now=1000) == 17


def test_ready_paid_routes_take_free_slot_while_free_uses_spare_capacity(tmp_path):
    store = JobStore(tmp_path / "q")
    store.record([product(999, available=False)], now=1000)
    store.record([product(i) for i in range(20)], now=87400)
    store.record([product(100, value_usd=999, category="pets")], now=87400)
    state = {
        **STATE,
        "channels": {
            **STATE["channels"],
            "type-pets": "1700000000000000006",
            "price-200-plus": "1700000000000000007",
        },
    }
    remote = Remote()
    result = deliver_due(
        store, remote, state, live=True, now=87400, limit=4, auditor=lambda *_: {"ok": True}
    )
    assert result["messages"] == 4
    assert all(STATE["channels"]["yesterday-recap"] not in route for route, _ in remote.calls)
    assert any(state["channels"]["price-200-plus"] in route for route, _ in remote.calls)


def test_shared_guard_caches_pass_and_failure_for_its_ttl(tmp_path):
    store = JobStore(tmp_path / "q")
    audits, verdict, events, clock = [], {"ok": True}, [], [0.0]

    def audited(*_):
        audits.append(True)
        return dict(verdict)

    guard = AuditGuard(
        audited, ttl=300, event_sink=lambda name, **fields: events.append((name, fields)),
        clock=lambda: clock[0],
    )

    def deliver(item, now):
        store.record([product(item)], now=now)
        return deliver_due(store, Remote(), STATE, live=True, now=now, auditor=guard)

    assert deliver(1, 1000)["sent"] and len(audits) == 1
    assert deliver(2, 1001)["sent"] and len(audits) == 1, "a recent pass is reused"
    clock[0] = 301  # TTL elapsed
    verdict.update(ok=False, findings=["Role 1700000000000000099: privileged."])
    assert deliver(3, 1002)["sent"] and len(audits) == 2, "a failure still sends"
    assert deliver(4, 1003)["sent"] and len(audits) == 2, "a failure is cached too"
    clock[0] = 602
    assert deliver(5, 1004)["sent"] and len(audits) == 3
    names = [name for name, _ in events]
    assert names.count("privacy_warning") == 1, "same findings warn once, not every cycle"
    verdict.update(ok=True, findings=[])
    clock[0] = 903
    assert deliver(6, 1005)["sent"] and len(audits) == 4
    assert [name for name, _ in events].count("privacy_cleared") == 1
    assert guard.status()["ok"] is True and guard.status()["findings"] == []
