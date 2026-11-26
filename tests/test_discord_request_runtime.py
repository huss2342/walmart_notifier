"""The optional listener and DM sender share collector lifecycle and storage."""

import threading

from discord_bridge.core import JobStore, normalize_item
from discord_bridge.request_runtime import RequestRuntime
from discord_bridge.request_store import RequestStore

from notifiers.discord import DiscordNotifier

STATE = {
    "guild_id": "100000000000000001",
    "bot_user_id": "100000000000000002",
    "roles": {"pro": "100000000000000003"},
    "channels": {"item-requests": "100000000000000004"},
}
USER = "100000000000000005"


class Child:
    def __init__(self, *args, **kwargs):
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def health(self):
        return {"running": self.started and not self.stopped, "connected": self.started}


def test_atomic_collector_intake_matches_a_fresh_reobservation(tmp_path):
    path = tmp_path / "collector.sqlite3"
    jobs = JobStore(path)
    item = normalize_item({"item_id": "ip-1234567", "title": "Air Fryer", "value_usd": 25})
    jobs.record([item], now=100)
    watches = RequestStore(path, guild_id=STATE["guild_id"])
    watches.set_dm_status(USER, "ready")
    watches.add(USER, "air fryer", now=101)
    jobs.record([item], now=102)
    assert watches.claim(now=102 + 60)["user_id"] == USER  # Older match: gathered first.


def test_failed_startup_audit_warns_and_the_feature_still_starts(tmp_path):
    events = []

    def failing(*_):
        raise RuntimeError("audit unavailable")

    for auditor in (lambda *_: {"ok": False}, failing):
        runtime = RequestRuntime(
            object(),
            STATE,
            tmp_path / "q",
            token="offline",
            auditor=auditor,
            worker_factory=Child,
            gateway_factory=Child,
            event_sink=lambda name, **fields: events.append((name, fields)),
        )
        # 2026-11-25: findings and audit errors never disable member requests.
        assert runtime.prepare() is True
        assert runtime.worker.started and runtime.gateway.started
        assert runtime.health()["ready"] is True and runtime.health()["last_error"] is None
        runtime.stop()
    reasons = [fields["reason"] for name, fields in events if name == "privacy_warning"]
    assert reasons == ["privacy_audit", "audit_error"]


def test_feature_children_start_and_stop_together(tmp_path):
    runtime = RequestRuntime(
        object(),
        STATE,
        tmp_path / "q",
        token="offline",
        auditor=lambda *_: {"ok": True},
        worker_factory=Child,
        gateway_factory=Child,
    )
    assert runtime.prepare() is True
    assert runtime.worker.started and runtime.gateway.started
    assert runtime.health()["ready"] is True
    runtime.stop()
    assert runtime.worker.stopped and runtime.gateway.stopped


def test_stopped_feature_cannot_start(tmp_path):
    event = threading.Event()
    event.set()
    runtime = RequestRuntime(
        object(),
        STATE,
        tmp_path / "q",
        token="offline",
        stop_event=event,
        auditor=lambda *_: {"ok": True},
        worker_factory=Child,
        gateway_factory=Child,
    )
    assert runtime.prepare() is False


def test_optional_requests_disabled_in_preview_even_if_selected(tmp_path):
    notifier = DiscordNotifier(JobStore(tmp_path / "q"), requests_enabled=True)
    assert notifier.requests is None
    assert notifier.health()["requests"]["enabled"] is False
