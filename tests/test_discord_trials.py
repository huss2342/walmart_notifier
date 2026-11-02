"""Durable Discord-only trials, using SQLite and a stateful provider fake."""

import copy
from datetime import UTC, datetime

import pytest
from discord_bridge.api import DiscordError
from discord_bridge.trials import TrialService

GUILD = "1562530389866717295"
BOT = "1562530389866717296"
OWNER = "1562530389866717297"
PRO = "1562530389866717298"
TRIAL = "1562530389866717299"
USER = "1562530389866717300"
OTHER = "1562530389866717301"
CHANNEL = "1562530389866717302"


def member(user=USER, *, joined=100, roles=(), bot=False):
    return {
        "user": {"id": user, "bot": bot},
        "roles": list(roles),
        "joined_at": datetime.fromtimestamp(joined, UTC).isoformat(),
    }


class Discord:
    def __init__(self):
        self.members = {USER: member()}
        self.writes = []
        self.fail = False
        self.member_failure = None
        self.roles = [
            {"id": PRO, "name": "Pro", "permissions": "0", "managed": False, "mentionable": False},
            {
                "id": TRIAL,
                "name": "Pro Trial",
                "permissions": "0",
                "managed": False,
                "mentionable": False,
            },
        ]

    def request(self, method, path, payload=None):
        if method == "GET" and path == f"/guilds/{GUILD}/roles":
            return copy.deepcopy(self.roles)
        assert path.startswith(f"/guilds/{GUILD}/members/")
        user = path.split("/")[4]
        if self.member_failure:
            raise self.member_failure
        if user not in self.members:
            raise DiscordError(status=404, provider_code=10007)
        if method == "GET":
            return copy.deepcopy(self.members[user])
        assert path == f"/guilds/{GUILD}/members/{user}/roles/{TRIAL}"
        self.writes.append((method, user))
        if self.fail:
            raise DiscordError(status=503)
        if method == "PUT":
            if TRIAL not in self.members[user]["roles"]:
                self.members[user]["roles"].append(TRIAL)
        elif method == "DELETE":
            self.members[user]["roles"] = [r for r in self.members[user]["roles"] if r != TRIAL]
        else:
            raise AssertionError(method)
        return None

    def guild_members(self, guild, *, limit=1000, after=None):
        assert guild == GUILD
        return [
            copy.deepcopy(self.members[user])
            for user in sorted(self.members, key=int)
            if after is None or int(user) > int(after)
        ][:limit]


class Requests:
    def __init__(self):
        self.users = {USER, OTHER}
        self.revoked = []

    def owners(self, *, limit=50, after=None):
        return [
            user for user in sorted(self.users, key=int) if after is None or int(user) > int(after)
        ][:limit]

    def revoke_user(self, user):
        self.users.discard(user)
        self.revoked.append(user)


def setup(tmp_path, *, requests=None):
    discord = Discord()
    now = [1000.0]
    state = {
        "guild_id": GUILD,
        "bot_user_id": BOT,
        "roles": {"pro": PRO, "trial": TRIAL},
        "channels": {"trial-management": CHANNEL},
    }
    service = TrialService(
        discord, state, tmp_path / "trials.sqlite3", request_store=requests, clock=lambda: now[0]
    )
    return service, discord, now, state


def grant(service, user=USER, *, seconds=100):
    return service.grant(user, seconds=seconds, actor_id=OWNER, interaction_id="grant:" + user)


def test_grant_is_durable_and_expiration_removes_only_trial_role(tmp_path):
    service, discord, now, state = setup(tmp_path)
    discord.members[USER]["roles"] = [PRO]
    result = grant(service)
    assert result["expires_at"] == 1100
    assert result["synced"] is True
    assert set(discord.members[USER]["roles"]) == {PRO, TRIAL}
    restarted = TrialService(discord, state, service.store.path, clock=lambda: now[0])
    now[0] = 1100
    restarted.step()
    assert discord.members[USER]["roles"] == [PRO]
    assert not restarted.store.active(USER)
    assert all(method in {"PUT", "DELETE"} for method, _ in discord.writes)


def test_extension_replay_does_not_add_time_twice_and_mismatch_is_rejected(tmp_path):
    service, _, now, _ = setup(tmp_path)
    grant(service)
    now[0] += 20
    result = service.extend(USER, seconds=50, actor_id=OWNER, interaction_id="extension:one")
    assert result["expires_at"] == 1150
    replay = service.extend(USER, seconds=50, actor_id=OWNER, interaction_id="extension:one")
    assert replay["expires_at"] == 1150
    with pytest.raises(ValueError):
        service.extend(USER, seconds=70, actor_id=OWNER, interaction_id="extension:one")


def test_failed_role_assignment_remains_pending_and_recovers(tmp_path):
    service, discord, _, _ = setup(tmp_path)
    discord.fail = True
    assert grant(service)["synced"] is False
    assert service.store.active(USER)
    discord.fail = False
    service.step()
    assert TRIAL in discord.members[USER]["roles"]
    assert service.list()[0]["synced"] is True


def test_invalid_member_response_never_creates_grant(tmp_path):
    service, discord, _, _ = setup(tmp_path)
    discord.members[USER]["user"]["id"] = OTHER
    with pytest.raises(DiscordError):
        grant(service)
    assert service.list() == []


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), True, 366 * 86400 + 1])
def test_duration_validation(tmp_path, seconds):
    service, discord, _, _ = setup(tmp_path)
    with pytest.raises(ValueError):
        grant(service, seconds=seconds)
    assert not discord.writes


def test_until_keeps_an_existing_promise_and_revoke_clears_requests(tmp_path):
    requests = Requests()
    service, discord, _, _ = setup(tmp_path, requests=requests)
    result = service.grant(USER, until=1200, actor_id=OWNER, interaction_id="import:one")
    assert result["expires_at"] == 1200
    service.revoke(USER, actor_id=OWNER, interaction_id="revoke:one")
    assert TRIAL not in discord.members[USER]["roles"]
    assert USER in requests.revoked


def test_expiry_deletes_requests_but_retains_those_of_a_paid_member(tmp_path):
    requests = Requests()
    service, discord, now, _ = setup(tmp_path, requests=requests)
    discord.members[OTHER] = member(OTHER, roles=[PRO])
    grant(service)
    grant(service, OTHER)
    now[0] = 1100
    service.step()
    assert USER not in requests.users
    assert OTHER in requests.users


def test_paid_access_loss_is_cleaned_up_and_provider_failure_is_not_loss(tmp_path):
    requests = Requests()
    service, discord, _, _ = setup(tmp_path, requests=requests)
    discord.members[USER]["roles"] = [PRO]
    discord.members[OTHER] = member(OTHER, roles=[PRO])
    service.step()
    assert not requests.revoked
    discord.member_failure = DiscordError(status=503)
    service.step()
    assert not requests.revoked
    discord.member_failure = None
    discord.members[USER]["roles"] = []
    service.step()
    assert requests.revoked == [USER]


def test_welcome_off_does_not_stop_expiry_or_cleanup(tmp_path):
    service, discord, now, _ = setup(tmp_path)
    grant(service)
    assert service.settings()["enabled"] is False
    now[0] = 1100
    service.step()
    assert TRIAL not in discord.members[USER]["roles"]


def test_welcome_only_new_joins_once_without_retroactive_or_rejoin_extension(tmp_path):
    service, discord, now, _ = setup(tmp_path)
    service.configure_welcome(True, hours=8, actor_id=OWNER, interaction_id="welcome:on")
    service.step()
    assert not service.store.active(USER)
    now[0] = 1001
    discord.members[OTHER] = member(OTHER, joined=1001)
    service.step()
    assert service.store.active(OTHER)
    expiry = service.list()[0]["expires_at"]
    assert expiry == 1001 + 8 * 3600
    now[0] += 100
    discord.members[OTHER]["joined_at"] = datetime.fromtimestamp(now[0], UTC).isoformat()
    service.step()
    assert service.list()[0]["expires_at"] == expiry
    service.configure_welcome(False, actor_id=OWNER, interaction_id="welcome:off")
    service.configure_welcome(True, hours=4, actor_id=OWNER, interaction_id="welcome:again")
    service.step()
    assert service.list()[0]["expires_at"] == expiry


def test_members_joining_while_disabled_do_not_get_a_retroactive_trial(tmp_path):
    service, discord, now, _ = setup(tmp_path)
    discord.members[OTHER] = member(OTHER, joined=500)
    service.configure_welcome(True, hours=24, actor_id=OWNER, interaction_id="welcome:on")
    service.step()
    assert not service.store.active(OTHER)
    now[0] += 100
    discord.members[OTHER]["joined_at"] = datetime.fromtimestamp(now[0], UTC).isoformat()
    service.step()
    assert not service.store.active(OTHER)


def test_malformed_member_page_does_not_partially_grant_welcome_trials(tmp_path):
    service, discord, now, _ = setup(tmp_path)
    service.configure_welcome(True, actor_id=OWNER, interaction_id="welcome:on")
    now[0] += 1
    discord.members[USER]["joined_at"] = datetime.fromtimestamp(now[0], UTC).isoformat()
    discord.members[OTHER] = member(OTHER, joined=now[0])
    discord.members[OTHER]["joined_at"] = "timezone-missing"
    service.step()
    assert not service.store.active(USER)
    assert not discord.writes


def test_database_is_bound_to_exact_server_and_trial_role(tmp_path):
    service, discord, _, state = setup(tmp_path)
    state["roles"]["trial"] = OTHER
    with pytest.raises(ValueError, match="binding"):
        TrialService(discord, state, service.store.path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("permissions", "8"),
        ("permissions", False),
        ("managed", True),
        ("mentionable", True),
        ("name", "Something else"),
    ],
)
def test_unsafe_role_cannot_receive_a_grant(tmp_path, field, value):
    service, discord, _, _ = setup(tmp_path)
    discord.roles[1][field] = value
    with pytest.raises(ValueError):
        grant(service)
    assert service.list() == []
    assert not discord.writes


def test_role_drift_blocks_welcome_and_pending_activation_but_not_expiry(tmp_path):
    service, discord, now, _ = setup(tmp_path)
    grant(service)
    service.configure_welcome(True, actor_id=OWNER, interaction_id="welcome:on")
    now[0] += 1
    discord.members[OTHER] = member(OTHER, joined=now[0])
    discord.roles[1]["permissions"] = "8"
    service.step()
    assert not service.store.active(OTHER)
    now[0] = 1100
    service.step()
    assert TRIAL not in discord.members[USER]["roles"]
    assert service.health()["last_error"]


def test_orphan_trial_role_is_removed_without_touching_paid_access(tmp_path):
    service, discord, _, _ = setup(tmp_path)
    discord.members[USER]["roles"] = [TRIAL, PRO]
    service.step()
    assert discord.members[USER]["roles"] == [PRO]
    assert not service.store.active(USER)


def test_role_reassigned_after_confirmed_expiry_is_removed_again(tmp_path):
    service, discord, now, _ = setup(tmp_path)
    grant(service)
    now[0] = 1100
    service.step()
    discord.members[USER]["roles"] = [TRIAL]
    service.step()
    assert TRIAL not in discord.members[USER]["roles"]


def test_rejoin_during_active_trial_restores_role_without_extending_it(tmp_path):
    service, discord, now, _ = setup(tmp_path)
    grant(service)
    discord.members[USER]["roles"] = []
    now[0] += 1
    discord.members[USER]["joined_at"] = datetime.fromtimestamp(now[0], UTC).isoformat()
    service.step()
    service.step()
    assert TRIAL in discord.members[USER]["roles"]
    assert service.list()[0]["expires_at"] == 1100


def test_expired_welcome_trial_does_not_repeat_after_rejoin_or_restart(tmp_path):
    service, discord, now, state = setup(tmp_path)
    service.configure_welcome(True, hours=4, actor_id=OWNER, interaction_id="welcome:on")
    now[0] += 1
    discord.members[OTHER] = member(OTHER, joined=now[0])
    service.step()
    now[0] += 4 * 3600
    service.step()
    restarted = TrialService(discord, state, service.store.path, clock=lambda: now[0])
    discord.members[OTHER]["joined_at"] = datetime.fromtimestamp(now[0], UTC).isoformat()
    restarted.step()
    assert not restarted.store.active(OTHER)
    assert TRIAL not in discord.members[OTHER]["roles"]


def test_revoke_records_departed_member_and_deletes_requests(tmp_path):
    requests = Requests()
    service, discord, _, _ = setup(tmp_path, requests=requests)
    grant(service)
    del discord.members[USER]
    result = service.revoke(USER, actor_id=OWNER, interaction_id="revoke:departed")
    assert result["revoked"] and result["synced"]
    assert USER not in requests.users


def test_malformed_membership_does_not_delete_saved_requests(tmp_path):
    requests = Requests()
    service, discord, _, _ = setup(tmp_path, requests=requests)
    discord.members[USER]["roles"] = "bad"
    service.step()
    assert USER in requests.users


@pytest.mark.parametrize("provider_code", [None, 10004])
def test_404_does_not_prove_member_absence_without_unknown_member_code(tmp_path, provider_code):
    requests = Requests()
    service, discord, now, _ = setup(tmp_path, requests=requests)
    grant(service)
    now[0] = 1100
    discord.member_failure = DiscordError(status=404, provider_code=provider_code)
    service.step()
    assert USER in requests.users
    assert TRIAL in discord.members[USER]["roles"]
    assert not service.list()[0]["synced"]


def test_trial_expiring_during_role_safety_call_cannot_be_activated(tmp_path):
    service, discord, now, _ = setup(tmp_path)
    grant(service)
    discord.members[USER]["roles"] = []
    service.store.set_sync(USER, False)
    original = discord.request

    def delayed(method, path, payload=None):
        if path == f"/guilds/{GUILD}/roles":
            now[0] = 1200
        return original(method, path, payload)

    discord.request = delayed
    previous = len(discord.writes)
    service.step()
    assert TRIAL not in discord.members[USER]["roles"]
    assert discord.writes[previous:] == []


def test_trial_expiring_during_member_read_cannot_be_activated(tmp_path):
    service, discord, now, _ = setup(tmp_path)
    grant(service)
    discord.members[USER]["roles"] = []
    service.store.set_sync(USER, False)
    original = discord.request

    def delayed(method, path, payload=None):
        if method == "GET" and path == f"/guilds/{GUILD}/members/{USER}":
            now[0] = 1200
        return original(method, path, payload)

    discord.request = delayed
    previous = len(discord.writes)
    service.step()
    assert TRIAL not in discord.members[USER]["roles"]
    assert discord.writes[previous:] == []


def test_put_accepted_after_expiry_is_removed_in_the_same_cycle(tmp_path):
    service, discord, now, _ = setup(tmp_path)
    grant(service)
    discord.members[USER]["roles"] = []
    service.store.set_sync(USER, False)
    original = discord.request

    def delayed(method, path, payload=None):
        result = original(method, path, payload)
        if method == "PUT":
            now[0] = 1200
        return result

    discord.request = delayed
    previous = len(discord.writes)
    service.step()
    assert TRIAL not in discord.members[USER]["roles"]
    assert discord.writes[previous:] == [("PUT", USER), ("DELETE", USER)]
