"""Repeatable OFFLINE burst model; never a Discord rate-limit or SLA claim.

Run from the repository root:
    .venv/Scripts/python.exe tests/benchmark_discord_burst.py --items 750

SQLite, batching, dispatch selection, deadlines, retries and nonce recovery use
the real JobStore and delivery.deliver_due. HTTP is a deterministic virtual-time
model, including five sequential audit reads per audit. A completed worker's
100 ms response advances its own clock; the next main-thread scheduling wave
advances to the latest completion, modeling overlapping independent channels.
Disk/CPU time is reported separately and is NOT modeled as production capacity.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import sqlite3
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discord_bridge.api import DiscordError  # noqa: E402
from discord_bridge.core import (  # noqa: E402
    CATEGORIES,
    FREE_DELAY_SECONDS,
    STARTER_DELAY_SECONDS,
    JobStore,
    normalize_item,
    render_message,
)
from discord_bridge.layout import CHANNELS, FREE_ROUTE_KEY  # noqa: E402

delivery = importlib.import_module("discord_bridge.delivery")
BASE = 1_800_000_000.0


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    index = max(0, math.ceil(len(values) * fraction) - 1)
    return round(values[index], 3)


class ModelClock:
    def __init__(self):
        self.now = BASE
        self.pending = []
        self.local = threading.local()
        self.lock = threading.Lock()
        self.observe = lambda _now: None
        self.main = threading.get_ident()

    def time(self):
        return (
            self.now
            if threading.get_ident() == self.main
            else max(self.now, getattr(self.local, "now", self.now))
        )

    def advance_to(self, target):
        self.now = max(self.now, target)
        self.observe(self.now)

    def complete(self, finish):
        if threading.get_ident() == self.main:
            self.advance_to(finish)
        else:
            self.local.now = finish
            with self.lock:
                self.pending.append(finish)

    def flush(self):
        with self.lock:
            pending, self.pending = self.pending, []
        if pending:
            self.advance_to(max(pending))


class RestartAfterAccept(Exception):
    """Simulated crash after a remote accept, before SQLite acknowledgement."""


class ModelJobStore(JobStore):
    """Same production SQL; disposable benchmark files skip fsync cost."""

    @contextmanager
    def _connect(self):
        with super()._connect() as db:
            db.execute("PRAGMA synchronous=OFF")
            yield db


class ClockedStore:
    """Real JobStore, with virtual HTTP completions settled between waves."""

    def __init__(self, scenario):
        self.scenario = scenario

    def __getattr__(self, name):
        return getattr(self.scenario.store, name)

    def has_due(self, *, now):
        self.scenario.clock.flush()
        return self.scenario.store.has_due(now=self.scenario.clock.now)

    def next_due_in(self, *, now, audience="any"):
        self.scenario.clock.flush()
        return self.scenario.store.next_due_in(now=self.scenario.clock.now, audience=audience)

    def claim_batch(self, **kwargs):
        # Compare the same production scheduler at two envelope bounds; only
        # the previous five-item limit differs in this controlled pair.
        kwargs["size"] = min(kwargs.get("size", 5), self.scenario.paid_batch_size)
        return self.scenario.store.claim_batch(**kwargs)

    def finish_batch(self, batch, state, **kwargs):
        scenario = self.scenario
        if (
            state == "sent"
            and scenario.faults
            and not scenario.restart
            and scenario.clock.time() - BASE >= 75
        ):
            scenario.restart = {
                "at_seconds": round(scenario.clock.time() - BASE, 3),
                "envelope_items": len(batch["jobs"]),
                "nonce": batch["payload"]["nonce"],
            }
            raise RestartAfterAccept
        return scenario.store.finish_batch(batch, state, **kwargs)


class ModelProvider:
    """Assumed learned gates, not hard-coded limits in the production client."""

    def __init__(self, scenario):
        self.scenario = scenario
        self.lock = threading.Lock()
        self.global_times = deque()
        self.channel_times = defaultdict(deque)
        self.global_until = 0.0
        self.route_until = defaultdict(float)
        self.counts = Counter()
        self.messages = []
        self.pairs = {}
        self.nonces = {}
        self.duplicate_pairs = 0
        self.fault_route = False
        self.fault_global = False

    def cooldown_delay(self, method, path):
        route = self.scenario.reverse_channels.get(path.split("/")[2])
        now = self.scenario.clock.now
        with self.lock:
            recent_route = [at for at in self.channel_times[route] if at > now - 5 + 1e-7]
            deadline = max(self.global_until, self.route_until[route])
            if method == "POST" and len(recent_route) >= 5:
                deadline = max(deadline, recent_route[0] + 5)
            return max(0, deadline - now)

    def request(self, method, path, payload=None):
        clock = self.scenario.clock
        start = clock.now
        latency = 0.1
        route = self.scenario.reverse_channels.get(path.split("/")[2]) if method == "POST" else None
        error = None
        result = {}
        finish = start + latency
        with self.lock:
            self.counts["calls"] += 1
            while self.global_times and self.global_times[0] <= start - 1 + 1e-7:
                self.global_times.popleft()
            route_times = self.channel_times[route]
            while route_times and route_times[0] <= start - 5 + 1e-7:
                route_times.popleft()
            global_gate = self.global_until
            if len(self.global_times) >= 25:
                global_gate = max(global_gate, self.global_times[0] + 1)
            route_gate = self.route_until[route]
            if route and len(route_times) >= 5:
                route_gate = max(route_gate, route_times[0] + 5)
            blocked = max(global_gate, route_gate)
            if blocked > start + 1e-7:
                self.counts["local_gate_deferrals"] += 1
                finish = start
                error = DiscordError(
                    "Modeled learned gate",
                    status=429,
                    retry_after=blocked - start,
                    global_limit=global_gate > start + 1e-7,
                )
            else:
                self.global_times.append(start)
                self.counts["network_requests"] += 1
                self.counts["audit_get_requests" if method == "GET" else "post_requests"] += 1
                elapsed = start - BASE
                if self.scenario.faults and route and not self.fault_global and elapsed >= 45:
                    self.fault_global = True
                    self.global_until = start + 2.5
                    self.counts["injected_global_429"] += 1
                    error = DiscordError(
                        "Injected global 429", status=429, retry_after=2.4, global_limit=True
                    )
                elif (
                    self.scenario.faults
                    and route == "pro-all"
                    and not self.fault_route
                    and elapsed >= 20
                ):
                    self.fault_route = True
                    self.route_until[route] = start + 1.25
                    self.counts["injected_route_429"] += 1
                    error = DiscordError("Injected route 429", status=429, retry_after=1.15)
                elif method == "POST":
                    route_times.append(start)
                    nonce = payload.get("nonce")
                    if nonce in self.nonces:
                        self.counts["duplicate_nonce_suppressed"] += 1
                        result = {"id": self.nonces[nonce]}
                    else:
                        message_id = str(900000000000000000 + len(self.messages))
                        if nonce:
                            self.nonces[nonce] = message_id
                        items = [embed["footer"]["text"] for embed in payload["embeds"]]
                        self.messages.append({"route": route, "items": items, "at": finish})
                        for item_id in items:
                            pair = route, item_id
                            self.duplicate_pairs += int(pair in self.pairs)
                            self.pairs.setdefault(pair, finish)
                        result = {"id": message_id}
        clock.complete(finish)
        if error:
            raise error
        return result


class Scenario:
    def __init__(self, name, path, *, items, faults=False, paid_batch_size=10):
        self.name, self.path, self.faults = name, path, faults
        self.paid_batch_size = paid_batch_size
        self.clock = ModelClock()
        # Keep the disposable WAL open between production JobStore sessions;
        # repeated Windows WAL teardown is irrelevant to virtual HTTP timing.
        self.anchor = sqlite3.connect(path)
        self.anchor.execute("PRAGMA journal_mode=WAL")
        self.store = ModelJobStore(path)
        self.view = ClockedStore(self)
        self.state = {
            "guild_id": "700000000000000001",
            "channels": {
                channel.key: str(800000000000000000 + index)
                for index, channel in enumerate(CHANNELS)
            },
        }
        self.reverse_channels = {value: key for key, value in self.state["channels"].items()}
        self.provider = ModelProvider(self)
        self.audit_calls = 0
        self.audit_seconds = 0.0
        self.restart = None
        self.restarts_completed = 0
        self.initial_ids = set()
        self.fresh_ids = set()
        self.first_seen = {}
        self.arrivals = []
        self.next_arrival = 0
        self.iterations = 0
        self.record([self.item(index) for index in range(items)], now=BASE)
        self.initial_ids = set(self.first_seen)
        # Already stale paid jobs are deliberate expiry sentinels, not work to revive.
        self.stale = [self.item(900000 + index) for index in range(5)]
        self.record(self.stale, now=BASE - 3600)
        for index in range(10):
            self.arrivals.append(
                (BASE + 10 + index * 5, [self.item(100000 + index * 5 + n) for n in range(5)])
            )
        self.clock.observe = self.inject_arrivals

    @staticmethod
    def item(index):
        return normalize_item(
            {
                "item_id": f"ip-{600000000000 + index}",
                "title": f"Offline modeled product {index}",
                "value_cents": [599, 1499, 2999, 5999, 14999, 29999][index % 6],
                "category": CATEGORIES[index % len(CATEGORIES)],
            }
        )

    def record(self, items, *, now):
        for offset in range(0, len(items), 500):
            self.store.record(items[offset : offset + 500], now=now)
        for item in items:
            self.first_seen.setdefault(item["item_id"], now)

    def query(self, sql, args=()):
        db = sqlite3.connect(self.path)
        try:
            return db.execute(sql, args).fetchall()
        finally:
            db.close()

    def inject_arrivals(self, now):
        while self.next_arrival < len(self.arrivals) and self.arrivals[self.next_arrival][0] <= now:
            due, items = self.arrivals[self.next_arrival]
            pending = self.query(
                "SELECT COUNT(*) FROM jobs WHERE route!=? AND state IN ('pending','leased') "
                "AND due<=? AND expires>?",
                (FREE_ROUTE_KEY, due, due),
            )[0][0]
            self.record(items, now=due)
            self.fresh_ids.update(item["item_id"] for item in items)
            self.arrivals[self.next_arrival] = due, items, pending
            self.next_arrival += 1

    def auditor(self, client, _guild_id, _state):
        self.audit_calls += 1
        start = self.clock.now
        try:
            # The current bound-membership snapshot performs these five reads.
            for suffix in ("", "/roles", "/channels", "/members/1", "/members/2"):
                client.request("GET", "/guilds/700000000000000001" + suffix)
            return {"ok": True}
        finally:
            self.audit_seconds += self.clock.now - start

    def baseline_once(self):
        if not self.store.has_due(now=self.clock.now):
            return
        try:
            self.auditor(self.provider, None, None)
        except DiscordError:
            return
        for _ in range(20):
            job = self.store.claim(now=self.clock.now)
            if job is None:
                break
            payload = render_message(
                job["item"],
                first_seen=job["first_seen"],
                last_seen=job["last_seen"],
                route=job["route"],
            )
            payload.update(nonce=job["id"], enforce_nonce=True)
            try:
                response = self.provider.request(
                    "POST", f"/channels/{self.state['channels'][job['route']]}/messages", payload
                )
            except DiscordError as error:
                self.store.retry(
                    job,
                    now=self.clock.now,
                    delay=error.retry_after or 5,
                    reason="rate_limited",
                    count_attempt=False,
                )
                break
            else:
                self.store.finish(job, "sent", message_id=response["id"])

    def pending_paid(self):
        return self.query(
            "SELECT COUNT(*) FROM jobs WHERE route!=? AND state IN ('pending','leased')",
            (FREE_ROUTE_KEY,),
        )[0][0]

    def advance_wait(self, seconds):
        target = self.clock.now + max(0.01, seconds)
        if self.next_arrival < len(self.arrivals):
            target = min(target, self.arrivals[self.next_arrival][0])
        self.clock.advance_to(target)

    def dispatch(self, *, baseline=False):
        self.iterations += 1
        if baseline:
            self.baseline_once()
            wait = 2
        else:
            try:
                result = delivery.deliver_due(
                    self.view, self.provider, self.state, live=True, limit=20, auditor=self.auditor
                )
                wait = min(5, max(0.01, result["wait_seconds"]))
            except RestartAfterAccept:
                self.clock.flush()
                self.store = ModelJobStore(self.path)
                self.restarts_completed += 1
                wait = 1
        self.clock.flush()
        self.advance_wait(wait)

    def run(self, *, baseline=False):
        started = time.perf_counter()
        original_time = delivery.time
        delivery.time = SimpleNamespace(time=self.clock.time, monotonic=self.clock.time)
        try:
            while self.next_arrival < len(self.arrivals) or self.pending_paid():
                self.dispatch(baseline=baseline)
                if self.clock.now - BASE > 2500 or self.iterations > 20000:
                    raise RuntimeError("benchmark model failed to drain")
            paid_end = self.clock.now
            paid_counts = self.store.counts()
            paid_provider = dict(self.provider.counts)
            paid_messages = len(self.provider.messages)
            paid_audit_calls, paid_audit_seconds = self.audit_calls, self.audit_seconds
            # Refresh stale observations without changing their immutable job clocks.
            self.record(self.stale, now=paid_end)
            assert (
                self.query(
                    "SELECT COUNT(*) FROM jobs WHERE item_id>=? AND item_id<? AND route!=? "
                    "AND state IN ('pending','leased')",
                    ("ip-600000900000", "ip-600000900005", FREE_ROUTE_KEY),
                )[0][0]
                == 0
            )
            self.clock.advance_to(BASE + FREE_DELAY_SECONDS)
            while self.query(
                "SELECT COUNT(*) FROM jobs WHERE route=? AND state IN ('pending','leased')",
                (FREE_ROUTE_KEY,),
            )[0][0]:
                self.dispatch(baseline=baseline)
            return self.report(
                paid_end,
                paid_counts,
                paid_provider,
                paid_messages,
                paid_audit_calls,
                paid_audit_seconds,
                time.perf_counter() - started,
            )
        finally:
            delivery.time = original_time
            self.anchor.close()

    def report(
        self, paid_end, paid_counts, paid_provider, paid_messages, paid_audits, audit_seconds, wall
    ):
        def pro(route):
            return route not in {FREE_ROUTE_KEY, "starter-items"}

        initial = [
            at - BASE
            for (route, item), at in self.provider.pairs.items()
            if pro(route) and item in self.initial_ids
        ]
        fresh = [
            at - self.first_seen[item]
            for (route, item), at in self.provider.pairs.items()
            if pro(route) and item in self.fresh_ids
        ]
        values = {
            item: json.loads(payload)["value_cents"]
            for item, payload in self.query("SELECT item_id,payload FROM items")
        }
        expensive = [
            at - self.first_seen[item]
            for (route, item), at in self.provider.pairs.items()
            if pro(route) and item in self.fresh_ids and (values[item] or 0) >= 20_000
        ]
        early = late = 0
        due_by_pair = {
            (route, item): (due, expires)
            for route, item, due, expires in self.query(
                "SELECT route,item_id,due,expires FROM jobs"
            )
        }
        for pair, at in self.provider.pairs.items():
            due, expires = due_by_pair[pair]
            early += at + 1e-6 < due
            late += at >= expires
        assert early == late == self.provider.duplicate_pairs == 0
        if self.faults:
            assert self.restarts_completed == 1
            assert self.provider.counts["duplicate_nonce_suppressed"] == 1
            assert self.provider.counts["injected_route_429"] == 1
            assert self.provider.counts["injected_global_429"] == 1
        return {
            "name": self.name,
            "initial_items": len(self.initial_ids),
            "fresh_arrival_items": len(self.fresh_ids),
            "arrival_batches_with_paid_backlog": sum(entry[2] > 0 for entry in self.arrivals),
            "initial_pro_all_destinations_drained_seconds": round(max(initial), 3),
            "all_paid_terminal_seconds_including_starter_300s": round(paid_end - BASE, 3),
            "fresh_pro_destination_delay_seconds": {
                "samples": len(fresh),
                "p50": percentile(fresh, 0.5),
                "p95": percentile(fresh, 0.95),
                "worst": round(max(fresh), 3),
            },
            "fresh_high_value_pro_destination_delay_seconds": {
                "value_cents_at_least": 20_000,
                "samples": len(expensive),
                "p50": percentile(expensive, 0.5),
                "p95": percentile(expensive, 0.95),
                "worst": round(max(expensive), 3) if expensive else None,
            },
            "paid_phase": {
                "states": paid_counts,
                "unique_message_posts": paid_messages,
                "destination_items_per_message": round(
                    paid_counts.get("sent", 0) / paid_messages, 3
                ),
                "audit_calls": paid_audits,
                "modeled_audit_seconds": round(audit_seconds, 3),
                "provider": paid_provider,
            },
            "complete_with_free_history": {
                "states": self.store.counts(),
                "unique_message_posts": len(self.provider.messages),
                "provider": dict(self.provider.counts),
            },
            "invariants": {
                "sent_before_tier_due": early,
                "sent_at_or_after_expiry": late,
                "duplicate_destination_items": self.provider.duplicate_pairs,
                "expired_paid_sentinels_revived": 0,
                "starter_delay_seconds": STARTER_DELAY_SECONDS,
                "free_delay_seconds": FREE_DELAY_SECONDS,
            },
            "restart_after_remote_accept": self.restart,
            "restarts_completed": self.restarts_completed,
            "real_wall_seconds": round(wall, 3),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, default=750)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/discord_bridge/burst-benchmark.json"
    )
    args = parser.parse_args()
    if not 500 <= args.items <= 1000:
        parser.error("--items must be between 500 and 1000")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    scenarios = []
    with tempfile.TemporaryDirectory(
        prefix="burst-benchmark-", dir=args.output.parent
    ) as directory:
        for name, baseline, faults, size in (
            ("old_sequential_replica", True, False, 5),
            ("five_item_paid_envelopes_same_scheduler", False, False, 5),
            ("new_actual_dispatcher", False, False, 10),
            ("new_actual_dispatcher_with_faults", False, True, 10),
        ):
            scenario = Scenario(
                name,
                Path(directory) / f"{name}.sqlite3",
                items=args.items,
                faults=faults,
                paid_batch_size=size,
            )
            scenarios.append(scenario.run(baseline=baseline))
    baseline, previous, current, _faulted = scenarios
    output = {
        "kind": "offline_virtual_time_model_not_a_production_sla",
        "assumptions": {
            "network_access": False,
            "modeled_channel_posts": 5,
            "modeled_channel_window_seconds": 5,
            "modeled_global_requests_per_second_including_audit": 25,
            "modeled_response_latency_seconds": 0.1,
            "modeled_privacy_audit_sequential_gets": 5,
            "old_replica": "serial one-item posts; audit per20; stop pass on retry; 2s polling",
            "new": "actual deliver_due limit20, concurrency4, up to10 paid items or5 free items",
            "controlled_pair": "same dispatcher/gates/arrivals, only paid envelope bound5 vs10",
            "fresh_arrivals": "5 items every5s at10..55s; report each overlap with paid backlog",
            "faults": "one route429 >=20s, global429 >=45s, accept-before-ack crash >=75s",
            "restart": "SQLite reopened, remote nonce cache retained, 60s lease recovery",
            "tier_clocks": "JobStore: Pro0s, Starter300s, Free86400s; paid expiry1800s",
            "scope": "All model gates and latency are assumptions, not Discord limits or timings.",
            "not_modeled": "TLS/DNS, CPU/disk/network variability, mobile push, real audit policy",
            "storage": "Real JobStore SQL; fsync disabled only in disposable benchmark DB files",
            "audit": "Mock policy only; five GETs per audit count against time and global budget.",
        },
        "comparison": {
            "initial_pro_drain_speedup": round(
                baseline["initial_pro_all_destinations_drained_seconds"]
                / current["initial_pro_all_destinations_drained_seconds"],
                3,
            ),
            "paid_unique_message_reduction_percent": round(
                100
                * (
                    1
                    - current["paid_phase"]["unique_message_posts"]
                    / baseline["paid_phase"]["unique_message_posts"]
                ),
                3,
            ),
        },
        "paid_envelope_comparison": {
            "initial_pro_drain_speedup": round(
                previous["initial_pro_all_destinations_drained_seconds"]
                / current["initial_pro_all_destinations_drained_seconds"],
                3,
            ),
            "paid_unique_message_reduction_percent": round(
                100
                * (
                    1
                    - current["paid_phase"]["unique_message_posts"]
                    / previous["paid_phase"]["unique_message_posts"]
                ),
                3,
            ),
            "audit_get_request_reduction_percent": round(
                100
                * (
                    1
                    - current["paid_phase"]["provider"]["audit_get_requests"]
                    / previous["paid_phase"]["provider"]["audit_get_requests"]
                ),
                3,
            ),
        },
        "scenarios": scenarios,
        "real_wall_seconds": round(time.perf_counter() - started, 3),
    }
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "comparison": output["comparison"],
                "paid_envelope_comparison": output["paid_envelope_comparison"],
                "scenarios": [
                    {
                        key: scenario[key]
                        for key in (
                            "name",
                            "initial_pro_all_destinations_drained_seconds",
                            "fresh_pro_destination_delay_seconds",
                            "fresh_high_value_pro_destination_delay_seconds",
                            "real_wall_seconds",
                        )
                    }
                    for scenario in scenarios
                ],
                "real_wall_seconds": output["real_wall_seconds"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
