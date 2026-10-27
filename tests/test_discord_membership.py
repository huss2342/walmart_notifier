"""Role-based celebrations; offline provider, real durable outbox and restart."""

import json
import threading

import pytest
from discord_bridge.api import DiscordError
from discord_bridge.membership import MembershipWorker

from test_discord_welcome import BOT, STATE, USER, Client, posts

STARTER = "1700000000000000010"
PRO = "1700000000000000011"
OTHER = "1700000000000000012"
MEMBER_STATE = {**STATE, "roles": {"starter": STARTER, "pro": PRO}}


def member(user=USER, *roles, bot=False):
    return {"user": {"id": user, "bot": bot}, "roles": list(roles)}


class MembersClient(Client):
    def __init__(self):
        super().__init__()
        self.members = []
        self.member_error = None

    def guild_members(self, guild, *, limit=1000, after=None):
        self.calls.append(("members", guild, after))
        if self.member_error:
            raise self.member_error
        return sorted(
            [m for m in self.members if after is None or int(m["user"]["id"]) > int(after)],
            key=lambda m: int(m["user"]["id"]),
        )[:limit]


def make(tmp_path, *, client=None, clock=None, paid=None, auditor=None):
    client, clock = client or MembersClient(), clock or [1800000000.0]
    worker = MembershipWorker(
        client,
        MEMBER_STATE,
        tmp_path / "memberships.sqlite3",
        enabled=True,
        clock=lambda: clock[0],
        paid_due=paid or (lambda: False),
        auditor=auditor or (lambda *_: {"ok": True}),
        stop_event=threading.Event(),
    )
    return worker, client, clock


def advance(worker, clock):
    clock[0] += 30
    return worker.tick()


def test_new_paid_role_gets_colored_congrats_without_public_member_count(tmp_path):
    worker, client, clock = make(tmp_path)
    client.members = [member(), member(OTHER, STARTER, PRO), member(BOT, PRO, bot=True)]
    assert worker.tick()["baseline"]
    assert posts(client) == []
    client.members[0] = member(USER, STARTER)
    assert advance(worker, clock)["sent"] == 1
    payload = posts(client)[0][2]
    assert payload["content"] == f"🎉 Congratulations <@{USER}>!"
    embed = payload["embeds"][0]
    assert "Starter" in embed["title"] and embed["color"] == 0x1ABC9C
    assert "fields" not in embed
    assert "footer" not in embed
    assert "payment" not in json.dumps(payload).lower()
    assert payload["allowed_mentions"] == {
        "parse": [],
        "users": [USER],
        "roles": [],
        "replied_user": False,
    }
    assert payload["enforce_nonce"]
    assert USER not in json.dumps(worker.health())
    assert worker.health()["paid_members"] == 2
    assert advance(worker, clock)["sent"] == 0


def test_upgrade_celebrates_once_but_renewal_downgrade_and_restart_do_not(tmp_path):
    worker, client, clock = make(tmp_path)
    client.members = [member(USER, STARTER)]
    worker.tick()
    client.members = [member(USER, STARTER, PRO)]
    assert advance(worker, clock)["sent"] == 1
    embed = posts(client)[0][2]["embeds"][0]
    assert "Pro" in embed["title"] and "upgrade" in embed["title"].lower()
    assert embed["color"] == 0xF1C40F
    assert "fields" not in embed
    assert "footer" not in embed
    restarted, _, _ = make(tmp_path, client=client, clock=clock)
    assert restarted.tick()["sent"] == 0
    client.members = [member(USER, STARTER)]
    assert advance(restarted, clock)["sent"] == 0
    # Role churn/rejoining cannot ping the same member repeatedly within 24h.
    client.members = []
    advance(restarted, clock)
    client.members = [member(USER, STARTER)]
    assert advance(restarted, clock)["sent"] == 0
    assert len(posts(client)) == 1


def test_new_direct_pro_member_and_paid_removal_update_the_count(tmp_path):
    worker, client, clock = make(tmp_path)
    worker.tick()
    client.members = [member(USER, PRO)]
    assert advance(worker, clock)["sent"] == 1
    embed = posts(client)[0][2]["embeds"][0]
    assert "Pro" in embed["title"]
    assert "fields" not in embed
    assert "footer" not in embed
    client.members = []
    assert advance(worker, clock)["sent"] == 0
    assert worker.health()["paid_members"] == 0


def test_complete_paginated_baseline_is_required_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("discord_bridge.membership._PAGE_SIZE", 2)
    worker, client, clock = make(tmp_path)
    client.members = [member(USER, STARTER), member(OTHER, PRO)]
    worker.tick()
    assert worker.health()["paid_members"] is None
    restarted, _, _ = make(tmp_path, client=client, clock=clock)
    assert restarted.tick()["baseline"]
    assert restarted.health()["paid_members"] == 2 and posts(client) == []
    client.members = [member(USER, STARTER), member(OTHER, PRO), member(BOT, PRO, bot=True)]
    advance(restarted, clock)
    advance(restarted, clock)
    assert posts(client) == []


def test_partial_role_scan_failure_keeps_the_last_complete_count(tmp_path, monkeypatch):
    monkeypatch.setattr("discord_bridge.membership._PAGE_SIZE", 2)
    worker, client, clock = make(tmp_path)
    worker.tick()
    client.members = [member(USER, STARTER), member(OTHER, PRO)]
    advance(worker, clock)
    assert worker.health()["paid_members"] == 0 and posts(client) == []
    client.member_error = DiscordError(status=429, retry_after=60)
    assert advance(worker, clock)["throttled"]
    assert worker.health()["paid_members"] == 0 and posts(client) == []
    client.member_error = None
    clock[0] += 61
    assert worker.tick()["sent"] == 2
    assert worker.health()["paid_members"] == 2


def test_bounded_outbox_drains_without_losing_other_new_members(tmp_path, monkeypatch):
    monkeypatch.setattr("discord_bridge.membership._MAX_PENDING", 1)
    worker, client, clock = make(tmp_path)
    worker.tick()
    client.members = [member(USER, STARTER), member(OTHER, PRO)]
    assert advance(worker, clock)["sent"] == 1
    restarted, _, _ = make(tmp_path, client=client, clock=clock)
    assert restarted.tick()["sent"] == 1
    assert len(posts(client)) == 2
    assert worker.health()["paid_members"] == 2
    assert all("fields" not in p[2]["embeds"][0] for p in posts(client))
    assert all("footer" not in p[2]["embeds"][0] for p in posts(client))


def test_failed_or_deferred_scan_never_invents_new_subscribers(tmp_path):
    paid = [False]
    worker, client, clock = make(tmp_path, paid=lambda: paid[0])
    worker.tick()
    client.members = [member(USER, PRO)]
    client.member_error = DiscordError(status=403)
    assert advance(worker, clock)["sent"] == 0
    assert worker.health()["paid_members"] == 0
    assert worker.health()["last_error"] == "member_access"
    client.member_error = None
    paid[0] = True
    before = len(client.calls)
    assert advance(worker, clock)["deferred"] and len(client.calls) == before
    paid[0] = False
    assert advance(worker, clock)["sent"] == 1


def test_privacy_audit_failure_blocks_scanning_and_sending(tmp_path):
    worker, client, _ = make(tmp_path, auditor=lambda *_: {"ok": False})
    assert worker.tick()["blocked"] and client.calls == []


@pytest.mark.parametrize("acknowledged", [False, True])
def test_ambiguous_send_reconciles_without_duplicate_ping(tmp_path, acknowledged):
    worker, client, clock = make(tmp_path)
    worker.tick()
    client.members = [member(USER, PRO)]
    client.error = DiscordError(ambiguous=True)
    advance(worker, clock)
    payload = posts(client)[0][2]
    if acknowledged:
        client.dest = [{"id": "1900000000000000002", "author": {"id": BOT}, **payload}]
    restarted, _, _ = make(tmp_path, client=client, clock=clock)
    result = restarted.tick()
    assert result["reconciled" if acknowledged else "review"] == 1
    assert len(posts(client)) == 1


def test_definitive_rate_limit_retries_the_same_frozen_card(tmp_path):
    worker, client, clock = make(tmp_path)
    worker.tick()
    client.members = [member(USER, PRO)]
    client.error = DiscordError(status=429, retry_after=60)
    assert advance(worker, clock)["throttled"]
    client.members.append(member(OTHER, PRO))
    clock[0] += 61
    worker.tick()
    assert posts(client)[0][2] == posts(client)[1][2]
    assert (
        len([c for c in posts(client) if c[2]["content"] == f"🎉 Congratulations <@{USER}>!"]) == 2
    )


def test_role_rebinding_is_rejected_and_terminal_details_are_pruned(tmp_path):
    worker, client, clock = make(tmp_path)
    worker.tick()
    client.members = [member(USER, STARTER)]
    advance(worker, clock)
    changed = {**MEMBER_STATE, "roles": {"starter": PRO, "pro": STARTER}}
    with pytest.raises(ValueError, match="role configuration"):
        MembershipWorker(client, changed, worker.path, enabled=True)
    clock[0] += 8 * 86400
    worker.tick()
    with worker._db() as db:
        assert db.execute("SELECT COUNT(*) FROM celebrations").fetchone()[0] == 0


def test_notifier_starts_stops_and_reports_the_membership_worker(tmp_path):
    from discord_bridge.core import JobStore

    from notifiers.discord import DiscordNotifier

    client = MembersClient()
    client.closed = False
    client.close = lambda: setattr(client, "closed", True)
    notifier = DiscordNotifier(
        JobStore(tmp_path / "queue.sqlite3"),
        client=client,
        state=MEMBER_STATE,
        live=True,
        auditor=lambda *_: {"ok": True},
        membership_enabled=True,
    )
    assert notifier.membership.client is notifier.client
    assert notifier.membership.stop_event is notifier._stop
    assert notifier.health()["membership"]["enabled"]
    notifier.start()
    notifier.stop()
    assert not notifier.membership._thread.is_alive()
    assert client.closed


def test_preview_mode_does_not_start_membership_announcements(tmp_path):
    from discord_bridge.core import JobStore

    from notifiers.discord import DiscordNotifier

    notifier = DiscordNotifier(JobStore(tmp_path / "queue.sqlite3"), membership_enabled=True)
    assert notifier.membership is None
    assert notifier.health()["membership"]["enabled"] is False
