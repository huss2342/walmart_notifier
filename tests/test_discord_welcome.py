"""Offline join-event welcomes: exact mentions, durable at-most-once delivery."""

import json
import threading

import pytest
from discord_bridge.api import DiscordError
from discord_bridge.welcome import WelcomeWorker

GUILD = "1700000000000000001"
SOURCE = "1700000000000000002"
DEST = "1700000000000000003"
BOT = "1700000000000000004"
USER = "1700000000000000005"
EVENT = "1800000000000000001"
STATE = {
    "guild_id": GUILD,
    "bot_user_id": BOT,
    "channels": {
        "audit": SOURCE,
        "greeting": DEST,
        "welcome": "1700000000000000006",
        "how-it-works": "1700000000000000007",
    },
}


def event_id(number):
    return str(int(EVENT) + number * (1 << 22))


def join(event=EVENT, user=USER, **author):
    return {"id": event, "type": 7, "author": {"id": user, **author}}


class Client:
    def __init__(self):
        self.calls = []
        self.source = []
        self.dest = []
        self.error = None
        self.guild = {"system_channel_id": SOURCE, "system_channel_flags": 8}

    def channel_messages(self, channel, *, limit=100, after=None, before=None):
        self.calls.append(("history", channel, after, before))
        values = self.source if channel == SOURCE else self.dest
        return sorted(
            [
                v
                for v in values
                if (after is None or int(v["id"]) > int(after))
                and (before is None or int(v["id"]) < int(before))
            ],
            key=lambda x: int(x["id"]),
            reverse=True,
        )[:limit]

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "GET":
            return self.guild
        if self.error:
            error, self.error = self.error, None
            raise error
        row = {"id": "1900000000000000001", "author": {"id": BOT}, **payload}
        self.dest.append(row)
        return row


def make(tmp_path, *, client=None, clock=None, paid=None, auditor=None, enabled=True):
    client = client or Client()
    clock = clock or [((int(EVENT) >> 22) + 1420070400000 + 1) / 1000]
    worker = WelcomeWorker(
        client,
        STATE,
        tmp_path / "welcomes.sqlite3",
        enabled=enabled,
        clock=lambda: clock[0],
        paid_due=paid or (lambda: False),
        auditor=auditor or (lambda *_: {"ok": True}),
        stop_event=threading.Event(),
    )
    return worker, client, clock


def advance(worker, clock):
    clock[0] += 30
    return worker.tick()


def posts(client):
    return [call for call in client.calls if call[0] == "POST"]


def test_baseline_skips_history_and_new_join_gets_only_its_own_mention(tmp_path):
    worker, client, clock = make(tmp_path)
    client.source = [join()]
    assert worker.tick()["baseline"]
    assert posts(client) == []
    client.source.append(join(event_id(2)))
    assert advance(worker, clock)["sent"] == 1
    payload = posts(client)[0][2]
    assert payload["allowed_mentions"] == {
        "parse": [],
        "users": [USER],
        "roles": [],
        "replied_user": False,
    }
    assert payload["content"].count(f"<@{USER}>") == 1
    assert f"<#{STATE['channels']['welcome']}>" in payload["content"]
    assert f"<#{STATE['channels']['how-it-works']}>" in payload["content"]
    assert "@everyone" not in json.dumps(payload)
    assert payload["enforce_nonce"] is True and len(payload["nonce"]) <= 25
    assert "embeds" not in payload and "components" not in payload
    assert advance(worker, clock)["sent"] == 0
    assert len(posts(client)) == 1
    assert USER not in json.dumps(worker.health())


def test_restart_and_rejoin_cooldown_do_not_reping(tmp_path):
    worker, client, clock = make(tmp_path)
    client.source = [{"id": EVENT, "type": 0}]
    worker.tick()
    client.source.append(join(event_id(2)))
    advance(worker, clock)
    restarted, _, _ = make(tmp_path, client=client, clock=clock)
    client.source.append(join(event_id(3)))
    restarted.tick()
    assert len(posts(client)) == 1
    clock[0] += 86401
    client.source.append(join(event_id(4)))
    assert restarted.tick()["sent"] == 1
    assert len(posts(client)) == 2


@pytest.mark.parametrize("extra", [{"bot": True}, {"id": BOT}, {"id": "@everyone"}])
def test_bots_self_and_malformed_ids_never_greet(tmp_path, extra):
    worker, client, clock = make(tmp_path)
    client.source = [{"id": EVENT, "type": 0}]
    worker.tick()
    row = join(event_id(2))
    row["author"].update(extra)
    client.source.append(row)
    advance(worker, clock)
    assert posts(client) == []


def test_paids_audit_failure_and_wrong_system_channel_fail_closed(tmp_path):
    busy = [True]
    worker, client, clock = make(tmp_path, paid=lambda: busy[0])
    assert worker.tick()["deferred"]
    assert client.calls == []
    busy[0] = False
    worker.auditor = lambda *_: {"ok": False}
    assert advance(worker, clock)["blocked"]
    assert client.calls == []
    worker.auditor = lambda *_: {"ok": True}
    client.guild["system_channel_id"] = DEST
    assert advance(worker, clock)["blocked"]
    assert worker.health()["last_error"] == "system_channel"
    assert posts(client) == []


def test_join_suppression_flag_blocks_and_disabled_never_reads(tmp_path):
    worker, client, clock = make(tmp_path)
    client.guild["system_channel_flags"] = 1
    assert worker.tick()["blocked"]
    disabled, other, _ = make(tmp_path / "disabled", enabled=False)
    assert disabled.tick()["sent"] == 0 and other.calls == []
    assert disabled.health()["enabled"] is False


def test_explicit_429_retries_with_same_nonce_after_cooldown(tmp_path):
    worker, client, clock = make(tmp_path)
    client.source = [{"id": EVENT, "type": 0}]
    worker.tick()
    client.source.append(join(event_id(2)))
    client.error = DiscordError(status=429, retry_after=120)
    assert advance(worker, clock)["throttled"]
    nonce = posts(client)[0][2]["nonce"]
    assert advance(worker, clock)["sent"] == 0 and len(posts(client)) == 1
    clock[0] += 120
    assert worker.tick()["sent"] == 1
    assert posts(client)[1][2]["nonce"] == nonce


def test_ambiguous_send_reconciles_but_never_blindly_repings(tmp_path):
    worker, client, clock = make(tmp_path)
    client.source = [{"id": EVENT, "type": 0}]
    worker.tick()
    client.source.append(join(event_id(2)))
    client.error = DiscordError(ambiguous=True)
    advance(worker, clock)
    payload = posts(client)[0][2]
    client.dest = [{"id": "1900000000000000001", "author": {"id": BOT}, **payload}]
    restarted, _, _ = make(tmp_path, client=client, clock=clock)
    assert restarted.tick()["reconciled"] == 1
    assert len(posts(client)) == 1


def test_unconfirmed_ambiguity_goes_to_manual_review_without_retry(tmp_path):
    worker, client, clock = make(tmp_path)
    client.source = [{"id": EVENT, "type": 0}]
    worker.tick()
    client.source.append(join(event_id(2)))
    client.error = DiscordError(ambiguous=True)
    advance(worker, clock)
    clock[0] += 600
    assert worker.tick()["review"] == 1
    assert worker.health()["manual_review"] == 1
    advance(worker, clock)
    assert len(posts(client)) == 1


def test_stop_or_paid_work_arriving_mid_cycle_prevents_greeting(tmp_path):
    worker, client, clock = make(tmp_path)
    client.source = [{"id": EVENT, "type": 0}]
    worker.tick()
    client.source.append(join(event_id(2)))
    original = client.channel_messages

    def become_busy(*args, **kwargs):
        result = original(*args, **kwargs)
        worker.stop_event.set()
        return result

    client.channel_messages = become_busy
    advance(worker, clock)
    assert posts(client) == []


def test_missing_config_or_changed_destination_cannot_replay_ledger(tmp_path):
    worker, client, _ = make(tmp_path)
    client.source = [{"id": EVENT, "type": 0}]
    worker.tick()
    with pytest.raises(ValueError):
        WelcomeWorker(client, {**STATE, "channels": {}}, tmp_path / "q", enabled=True)
    changed = {**STATE, "channels": {**STATE["channels"], "greeting": "1700000000000000009"}}
    with pytest.raises(ValueError):
        WelcomeWorker(client, changed, tmp_path / "welcomes.sqlite3", enabled=True)


def test_post_activation_joins_survive_initial_paid_deferral_and_restart(tmp_path):
    busy = [True]
    worker, client, clock = make(tmp_path, paid=lambda: busy[0])
    client.source = [join()]
    assert worker.tick()["deferred"] is True
    client.source.append(join(event_id(2)))
    clock[0] += 30
    busy[0] = False
    restarted, _, _ = make(tmp_path, client=client, clock=clock, paid=lambda: busy[0])
    assert restarted.tick()["sent"] == 1
    assert len(posts(client)) == 1


def test_newest_first_pagination_recovers_every_event_across_restart(tmp_path):
    worker, client, clock = make(tmp_path)
    worker.tick()
    with worker._db() as db:
        floor = db.execute("SELECT value FROM settings WHERE key='cursor'").fetchone()[0]
    client.source = [join(event_id(number), str(int(USER) + number)) for number in range(2, 207)]
    advance(worker, clock)
    with worker._db() as db:
        assert db.execute("SELECT COUNT(*) FROM welcomes").fetchone()[0] == 100
        assert db.execute("SELECT value FROM settings WHERE key='cursor'").fetchone()[0] == floor
        assert (
            db.execute("SELECT value FROM settings WHERE key='scan_before'").fetchone() is not None
        )
    restarted, _, _ = make(tmp_path, client=client, clock=clock)
    restarted.tick()
    advance(restarted, clock)
    with restarted._db() as db:
        assert db.execute("SELECT COUNT(*) FROM welcomes").fetchone()[0] == 205
        assert db.execute("SELECT value FROM settings WHERE key='cursor'").fetchone()[
            0
        ] == event_id(206)
        assert db.execute("SELECT value FROM settings WHERE key='scan_before'").fetchone() is None
    assert len({call[2]["nonce"] for call in posts(client)}) == len(posts(client))


def test_full_outbox_still_drains_without_advancing_over_unqueued_events(tmp_path, monkeypatch):
    monkeypatch.setattr("discord_bridge.welcome._MAX_PENDING", 100)
    worker, client, clock = make(tmp_path)
    worker.tick()
    client.source = [join(event_id(n), str(int(USER) + n)) for n in range(2, 102)]
    advance(worker, clock)
    advance(worker, clock)  # Close the first scan's gap.
    client.source.extend(join(event_id(n), str(int(USER) + n)) for n in range(102, 202))
    result = advance(worker, clock)
    assert result["deferred"] is True and result["sent"] == 5
    assert worker.health()["pending"] == 85
    with worker._db() as db:
        assert db.execute("SELECT COUNT(*) FROM welcomes").fetchone()[0] == 100
        assert db.execute("SELECT value FROM settings WHERE key='cursor'").fetchone()[
            0
        ] == event_id(101)
    for _ in range(20):
        advance(worker, clock)
    with worker._db() as db:
        assert db.execute("SELECT COUNT(*) FROM welcomes").fetchone()[0] == 200


def test_recent_bot_message_with_wrong_nonce_does_not_confirm_ambiguous_send(tmp_path):
    worker, client, clock = make(tmp_path)
    client.source = [{"id": EVENT, "type": 0}]
    worker.tick()
    client.source.append(join(event_id(2)))
    client.error = DiscordError(status=500)
    advance(worker, clock)
    client.dest = [{"id": "1900000000000000001", "author": {"id": BOT}, "nonce": "other"}]
    assert advance(worker, clock)["review"] == 1
    assert len(posts(client)) == 1


def test_non_join_events_advance_cursor_without_storing_members(tmp_path):
    worker, client, clock = make(tmp_path)
    client.source = [{"id": EVENT, "type": 0}]
    worker.tick()
    client.source.append({"id": event_id(2), "type": 8, "author": {"id": USER}})
    advance(worker, clock)
    with worker._db() as db:
        assert db.execute("SELECT COUNT(*) FROM welcomes").fetchone()[0] == 0
        assert db.execute("SELECT value FROM settings WHERE key='cursor'").fetchone()[
            0
        ] == event_id(2)
    assert posts(client) == []


def test_stale_pending_welcomes_expire_and_terminal_personal_data_is_pruned(tmp_path):
    worker, client, clock = make(tmp_path)
    client.source = [{"id": EVENT, "type": 0}]
    worker.tick()
    client.source.append(join(event_id(2)))
    client.error = DiscordError(status=429, retry_after=86410)
    advance(worker, clock)
    clock[0] += 86420
    worker.tick()
    assert len(posts(client)) == 1
    assert worker.health()["skipped"] == 1
    clock[0] += 7 * 86400
    worker.tick()
    with worker._db() as db:
        assert db.execute("SELECT COUNT(*) FROM welcomes").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM cooldowns").fetchone()[0] == 0


def test_notifier_shares_transport_and_pauses_welcomes_for_paid_items(tmp_path):
    from discord_bridge.core import JobStore

    from models import Item
    from notifiers.discord import DiscordNotifier

    client = Client()
    notifier = DiscordNotifier(
        JobStore(tmp_path / "collector.sqlite3"),
        client=client,
        state=STATE,
        live=True,
        auditor=lambda *_: {"ok": True},
        welcome_enabled=True,
    )
    assert notifier.welcome.client is notifier.client
    assert notifier.welcome.stop_event is notifier._stop
    notifier.ingest(
        [
            Item.from_dict(
                {
                    "item_id": "ip-1234567",
                    "title": "Table",
                    "value_usd": 100,
                    "url": "https://www.walmart.com/ip/1234567",
                }
            )
        ]
    )
    assert notifier.welcome.tick()["deferred"] is True and client.calls == []
    health = notifier.health()
    assert health["welcome"]["enabled"] is True
    assert "user_id" not in json.dumps(health["welcome"])


def test_notifier_stops_welcome_before_closing_the_shared_client(tmp_path):
    from discord_bridge.core import JobStore

    from notifiers.discord import DiscordNotifier

    client = Client()
    closed = []
    client.close = lambda: closed.append(True)
    notifier = DiscordNotifier(
        JobStore(tmp_path / "collector.sqlite3"),
        client=client,
        state=STATE,
        live=True,
        auditor=lambda *_: {"ok": True},
        welcome_enabled=True,
    )
    notifier.start()
    notifier.stop()
    assert not notifier._thread.is_alive()
    assert not notifier.welcome._thread.is_alive()
    assert closed == [True]
