"""The privacy audit is advisory: cached, rate-limited warnings, never a gate.

2026-11-25: a fail-closed audit, re-run every cycle because failures were never
cached, stopped every channel alert for ~80 minutes over a harmless managed
Server Booster role and starved other workers of the local Discord budget.
"""

import json
import threading

from discord_bridge.api import DiscordError
from discord_bridge.audit_guard import AuditGuard
from discord_bridge.monitoring import DeliveryMonitor

GUILD = "100000000000000001"
FINDING = "Role 300000000000000009: unexpected privileged base permissions can bypass the policy."


def make(auditor, **kwargs):
    clock, events = [0.0], []
    guard = AuditGuard(
        auditor,
        clock=lambda: clock[0],
        wall_clock=lambda: 1_800_000_000 + clock[0],
        event_sink=lambda name, **fields: events.append((name, fields)),
        **kwargs,
    )
    return guard, clock, events


def names(events):
    return [name for name, _ in events]


def test_failures_are_cached_for_the_ttl_so_the_audit_cannot_hammer_discord():
    calls = []
    guard, clock, events = make(
        lambda *_: calls.append(True) or {"ok": False, "findings": [FINDING]}, ttl=300
    )
    for second in range(0, 300, 5):  # One delivery cycle every 5 s.
        clock[0] = second
        assert guard(None, GUILD, {})["findings"] == [FINDING]
    assert len(calls) == 1
    clock[0] = 300
    guard(None, GUILD, {})
    assert len(calls) == 2
    assert names(events) == ["privacy_warning"], "the same findings warn once"


def test_same_findings_rewarn_at_most_hourly_and_a_new_set_warns_at_once():
    verdict = {"ok": False, "findings": [FINDING]}
    guard, clock, events = make(lambda *_: dict(verdict), ttl=300, warn_interval=3600)
    for second in range(0, 3600, 300):
        clock[0] = second
        guard(None, GUILD, {})
    assert names(events) == ["privacy_warning"]
    clock[0] = 3600
    guard(None, GUILD, {})
    assert names(events) == ["privacy_warning"] * 2
    verdict["findings"] = [FINDING, "Server widget is enabled; disable it."]
    clock[0] = 3900
    guard(None, GUILD, {})
    assert names(events) == ["privacy_warning"] * 3
    assert events[-1][1]["findings_count"] == 2 and events[-1][1]["blocked"] is False
    verdict.update(ok=True, findings=[])
    clock[0] = 4200
    assert guard(None, GUILD, {})["ok"] is True
    assert names(events)[-1] == "privacy_cleared"
    assert guard.status()["findings"] == [] and guard.status()["ok"] is True


def test_audit_errors_never_raise_are_cached_and_warn_once():
    calls = []

    def limited(*_):
        calls.append(True)
        raise DiscordError("PRIVATE provider body", status=429, retry_after=2)

    guard, clock, events = make(limited, ttl=300)
    for second in range(0, 300, 5):
        clock[0] = second
        assessment = guard(None, GUILD, {}, caller="channel")
        assert assessment["ok"] is None and assessment["error"] == "DiscordError"
    assert len(calls) == 1
    clock[0] = 300
    guard(None, GUILD, {})
    assert len(calls) == 2
    assert names(events) == ["privacy_warning"]
    fields = events[0][1]
    assert fields["reason"] == "audit_error" and fields["status"] == 429
    assert fields["caller"] == "channel" and "PRIVATE" not in json.dumps(fields)
    status = guard.status()
    assert status["last_error"] == "DiscordError" and status["blocks_delivery"] is False


def test_an_error_keeps_the_last_known_findings_for_the_owner():
    verdict = [{"ok": False, "findings": [FINDING]}]

    def auditor(*_):
        if not verdict:
            raise OSError("network down")
        return verdict[0]

    guard, clock, _ = make(auditor, ttl=300)
    guard(None, GUILD, {})
    verdict.clear()
    clock[0] = 300
    assert guard(None, GUILD, {})["findings"] == [FINDING]
    status = guard.status()
    assert status["findings"] == [FINDING] and status["last_error"] == "OSError"


def test_garbage_results_and_a_failed_verdict_without_findings_still_report():
    guard, _, events = make(lambda *_: None)
    assert guard(None, GUILD, {})["ok"] is None
    assert events[0][1]["reason"] == "audit_error"
    guard, _, events = make(lambda *_: {"ok": False})
    assert guard(None, GUILD, {})["findings"] == ["Privacy audit did not pass."]
    assert events[0][1]["findings"] == ["Privacy audit did not pass."]


def test_shutdown_interrupting_the_audit_is_neither_cached_nor_reported():
    stopping = threading.Event()
    calls = []

    def interrupted(*_):
        calls.append(True)
        stopping.set()
        raise DiscordError("Delivery stopping")

    guard, _, events = make(interrupted)
    assert guard(None, GUILD, {}, stop_event=stopping).get("pending") is True
    assert events == [] and guard.status()["last_error"] is None
    stopping.clear()
    guard(None, GUILD, {}, stop_event=stopping)
    assert len(calls) == 2


def test_a_callers_own_not_now_interrupt_is_neither_cached_nor_reported():
    class Deferred(Exception):
        pass

    busy, calls = [True], []

    def audited(*_):
        calls.append(True)
        if busy[0]:
            raise Deferred  # e.g. welcome yielding the rate budget to paid alerts
        return {"ok": True}

    guard, _, events = make(audited)
    assert guard(None, GUILD, {}, interrupts=(Deferred,)).get("pending") is True
    assert events == [] and guard.status()["last_error"] is None
    busy[0] = False
    assert guard(None, GUILD, {}, interrupts=(Deferred,))["ok"] is True
    assert len(calls) == 2 and events == []


def test_background_mode_never_makes_a_caller_wait_and_runs_one_audit():
    release, started, calls = threading.Event(), threading.Event(), []

    def slow(*_):
        calls.append(True)
        started.set()
        release.wait(5)
        return {"ok": False, "findings": [FINDING]}

    guard, _, events = make(slow, background=True)
    assert guard(None, GUILD, {}).get("pending") is True
    assert started.wait(5)
    for _ in range(20):  # Every worker keeps going while the audit reads.
        assert guard(None, GUILD, {}).get("pending") is True
    release.set()
    guard.join(5)
    assert len(calls) == 1
    assert guard(None, GUILD, {})["findings"] == [FINDING]
    assert names(events) == ["privacy_warning"]


def test_shared_guard_is_reused_and_plain_auditors_are_wrapped():
    guard = AuditGuard(lambda *_: {"ok": True})
    assert AuditGuard.wrap(guard) is guard
    wrapped = AuditGuard.wrap(lambda *_: {"ok": True}, ttl=60)
    assert isinstance(wrapped, AuditGuard) and wrapped.ttl == 60
    assert AuditGuard().status() == {
        "mode": "advisory",
        "blocks_delivery": False,
        "ok": None,
        "findings": [],
        "findings_count": 0,
        "last_checked_at": None,
        "last_error": None,
        "last_error_at": None,
        "audits": 0,
        "ttl_seconds": 300.0,
        "next_check_in_seconds": 0.0,
    }


def test_monitor_persists_the_warning_and_recovery_rows(tmp_path):
    monitor = DeliveryMonitor(tmp_path, entry_point="collector_worker", console=False)
    verdict = {"ok": False, "findings": [FINDING]}
    clock = [0.0]
    guard = AuditGuard(lambda *_: dict(verdict), clock=lambda: clock[0], event_sink=monitor.emit)
    guard(None, GUILD, {}, caller="membership")
    verdict.update(ok=True, findings=[])
    clock[0] = 300
    guard(None, GUILD, {}, caller="welcome")
    rows = [
        json.loads(line)
        for line in (tmp_path / "delivery.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["event"] for row in rows] == ["privacy_warning", "privacy_cleared"]
    warning = rows[0]
    assert warning["findings"] == [FINDING] and warning["findings_count"] == 1
    assert warning["reason"] == "privacy_audit" and warning["blocked"] is False
    assert warning["caller"] == "membership" and warning["phase"] == "audit"
    assert rows[1]["caller"] == "welcome"
