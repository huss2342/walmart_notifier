"""Only REST is faked: watches, matching, leases and restarts use SQLite."""

import pytest
from discord_bridge.api import DiscordError
from discord_bridge.request_delivery import RequestWorker
from discord_bridge.request_store import RequestStore

from test_discord_requests import GUILD, USER, observe, product

PRO = "555555555555555555"
DM = "666666666666666666"
MESSAGE = "777777777777777777"
STATE = {"guild_id": GUILD, "roles": {"pro": PRO}}


class Provider:
    def __init__(self):
        self.calls = []
        self.roles = [PRO]
        self.member_error = None
        self.send_error = None
        self.on_member = None
        self.dm_type = 1

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "GET" and path == f"/guilds/{GUILD}/members/{USER}":
            if self.on_member:
                self.on_member()
            if self.member_error:
                raise self.member_error
            return {"user": {"id": USER}, "roles": self.roles}
        if method == "POST" and path == "/users/@me/channels":
            assert payload == {"recipient_id": USER}
            return {"id": DM, "type": self.dm_type, "recipients": [{"id": USER}]}
        if method == "POST" and path == f"/channels/{DM}/messages":
            if self.send_error:
                raise self.send_error
            return {"id": MESSAGE}
        raise AssertionError((method, path))

    @property
    def messages(self):
        return [payload for method, path, payload in self.calls if path.endswith("/messages")]


def setup(tmp_path, **kwargs):
    store = RequestStore(tmp_path / "requests.sqlite3", guild_id=GUILD)
    store.set_dm_status(USER, "ready")
    request = store.add(USER, "air fryer", now=10)
    observe(store, [product()], now=11)
    provider = Provider()
    return store, request, provider, RequestWorker(provider, STATE, store, enabled=True, **kwargs)


def test_product_goes_only_to_dm_after_current_pro_verification(tmp_path):
    store, _, provider, worker = setup(tmp_path)
    result = worker.tick(now=12)
    assert result["sent"] == 1
    assert len(provider.messages) == 1
    payload = provider.messages[0]
    assert payload["allowed_mentions"] == {"parse": []}
    assert "air fryer" in str(payload)
    assert payload["enforce_nonce"] is True
    assert provider.calls[-2][1] == f"/guilds/{GUILD}/members/{USER}"
    RequestWorker(provider, STATE, RequestStore(store.path), enabled=True).tick(now=13)
    assert len(provider.messages) == 1


@pytest.mark.parametrize(
    "roles,missing", [([], False), (["888888888888888888"], False), ([PRO], True)]
)
def test_removed_member_or_nonpro_receives_no_dm(tmp_path, roles, missing):
    store, _, provider, worker = setup(tmp_path)
    provider.roles = roles
    if missing:
        provider.member_error = DiscordError(status=404, provider_code=10007)
    worker.tick(now=12)
    assert provider.messages == []
    assert store.dm_status(USER) == "needs_test"


def test_membership_failure_defers_without_product_data(tmp_path):
    store, _, provider, worker = setup(tmp_path)
    provider.member_error = DiscordError(status=503)
    assert worker.tick(now=12)["retried"] == 1
    assert provider.messages == []
    provider.member_error = None
    assert worker.tick(now=60)["sent"] == 1


@pytest.mark.parametrize("status", [401, 403])
def test_unverifiable_membership_is_deferred_until_access_recovers(tmp_path, status):
    store, _, provider, worker = setup(tmp_path)
    provider.member_error = DiscordError(status=status)
    result = worker.tick(now=12)
    assert result["retried"] == 1
    assert provider.messages == []
    assert store.dm_status(USER) == "ready"
    provider.member_error = None
    assert worker.tick(now=60)["sent"] == 1


@pytest.mark.parametrize("provider_code", [None, 10004])
def test_unknown_guild_or_unclassified_member_404_preserves_requests(tmp_path, provider_code):
    store, _, provider, worker = setup(tmp_path)
    provider.member_error = DiscordError(status=404, provider_code=provider_code)
    result = worker.tick(now=12)
    assert result["retried"] == 1
    assert provider.messages == []
    assert store.dm_status(USER) == "ready"
    assert len(store.list(USER)) == 1
    provider.member_error = None
    assert worker.tick(now=60)["sent"] == 1


def test_final_member_404_without_unknown_member_code_defers_safely(tmp_path):
    store, _, provider, worker = setup(tmp_path)
    checks = 0

    def fail_final_member_read():
        nonlocal checks
        checks += 1
        if checks == 2:
            provider.member_error = DiscordError(status=404, provider_code=10004)

    provider.on_member = fail_final_member_read
    result = worker.tick(now=12)
    assert result["retried"] == 1
    assert provider.messages == []
    assert store.dm_status(USER) == "ready"
    provider.member_error = None
    provider.on_member = None
    assert worker.tick(now=60)["sent"] == 1


def test_role_removed_while_opening_dm_cannot_receive_product(tmp_path):
    store, _, provider, worker = setup(tmp_path)
    calls = 0

    def role_change():
        nonlocal calls
        calls += 1
        if calls == 2:
            provider.roles = []

    provider.on_member = role_change
    worker.tick(now=12)
    assert provider.messages == []
    assert store.dm_status(USER) == "needs_test"


def test_unavailable_during_membership_check_prevents_delivery(tmp_path):
    store, _, provider, worker = setup(tmp_path)
    provider.on_member = lambda: observe(store, [product(available=False)], now=12)
    worker.tick(now=12)
    assert provider.messages == []


def test_blocked_dm_has_no_public_fallback(tmp_path):
    store, _, provider, worker = setup(tmp_path)
    provider.send_error = DiscordError(status=403, provider_code=50007)
    assert worker.tick(now=12)["blocked"] == 1
    assert store.dm_status(USER) == "blocked"
    assert all(
        path
        in {f"/guilds/{GUILD}/members/{USER}", "/users/@me/channels", f"/channels/{DM}/messages"}
        for _, path, _ in provider.calls
    )
    worker.tick(now=80)
    assert len(provider.messages) == 1


@pytest.mark.parametrize(
    "error", [DiscordError(ambiguous=True), DiscordError(status=503), DiscordError()]
)
def test_ambiguous_send_requires_review_and_never_blindly_resends(tmp_path, error):
    store, _, provider, worker = setup(tmp_path)
    provider.send_error = error
    assert worker.tick(now=12)["review"] == 1
    provider.send_error = None
    restarted = RequestWorker(provider, STATE, RequestStore(store.path), enabled=True)
    restarted.tick(now=100)
    assert len(provider.messages) == 1
    assert store.stats()["review"] == 1


def test_rate_limit_retries_with_same_nonce_without_marking_dm_blocked(tmp_path):
    store, _, provider, worker = setup(tmp_path)
    provider.send_error = DiscordError(status=429, retry_after=7)
    assert worker.tick(now=12)["throttled"] == 1
    worker.tick(now=18)
    assert len(provider.messages) == 1
    provider.send_error = None
    assert worker.tick(now=20)["sent"] == 1
    assert provider.messages[0]["nonce"] == provider.messages[1]["nonce"]
    assert store.dm_status(USER) == "ready"


def test_delete_while_verifying_prevents_send(tmp_path):
    store, request, provider, worker = setup(tmp_path)
    provider.on_member = lambda: store.delete(USER, request["id"])
    worker.tick(now=12)
    assert provider.messages == []


def test_disabled_and_failed_audit_never_send(tmp_path):
    store, _, provider, _ = setup(tmp_path)
    RequestWorker(provider, STATE, store).tick(now=12)
    assert provider.calls == []
    RequestWorker(provider, STATE, store, enabled=True, auditor=lambda *_: {"ok": False}).tick(
        now=12
    )
    assert provider.messages == []


def test_one_tick_has_bounded_fanout(tmp_path):
    store, _, provider, worker = setup(tmp_path)
    observe(store, [product("air fryer", "987654321")], now=11)
    worker.tick(now=12)
    assert len(provider.messages) == 1
    assert store.stats()["pending"] == 1


def test_health_exposes_counts_without_member_queries(tmp_path):
    _, _, _, worker = setup(tmp_path)
    worker.tick(now=12)
    health = worker.health()
    assert health["enabled"] is True
    assert health["queue"]["sent"] == 1
    assert USER not in str(health)
    assert "air fryer" not in str(health)


@pytest.mark.parametrize("channel_type", [None, 0, 3])
def test_malformed_dm_open_response_cannot_send_to_other_channel_types(tmp_path, channel_type):
    _, _, provider, worker = setup(tmp_path)
    provider.dm_type = channel_type
    worker.tick(now=12)
    assert provider.messages == []


def test_payload_uses_associations_committed_at_submission(tmp_path, monkeypatch):
    store, first, provider, worker = setup(tmp_path)
    store.add(USER, "Ninja", now=10)
    observe(store, [product()], now=11)
    original = store.begin_send

    def delete_before_commit(job, **kwargs):
        store.delete(USER, first["id"])
        return original(job, **kwargs)

    monkeypatch.setattr(store, "begin_send", delete_before_commit)
    assert worker.tick(now=12)["sent"] == 1
    description = provider.messages[0]["embeds"][0]["description"]
    assert "Matched requests: Ninja\n" in description


def test_payload_uses_latest_product_snapshot_at_submission(tmp_path, monkeypatch):
    store, _, provider, worker = setup(tmp_path)
    original = store.begin_send

    def update_before_commit(job, **kwargs):
        observe(store, [product("New air fryer model", value_cents=1999)], now=12)
        return original(job, **kwargs)

    monkeypatch.setattr(store, "begin_send", update_before_commit)
    assert worker.tick(now=12)["sent"] == 1
    title = provider.messages[0]["embeds"][0]["title"]
    assert title == "$19.99 | New air fryer model"


def test_render_failure_before_actual_post_is_safe_to_retry(tmp_path, monkeypatch):
    store, _, provider, worker = setup(tmp_path)

    def broken_render(_):
        raise ValueError("cannot render")

    monkeypatch.setattr("discord_bridge.request_delivery.render_request_message", broken_render)
    result = worker.tick(now=12)
    assert result["retried"] == 1
    assert result["review"] == 0
    assert provider.messages == []
    assert store.stats()["pending"] == 1
