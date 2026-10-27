"""Offline rate-limit and request-safety tests for the actual REST client."""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message

import pytest
from discord_bridge.api import DiscordClient, DiscordError, _NoRedirect, encode_payload


def headers(**values):
    result = Message()
    for key, value in values.items():
        result[key.replace("_", "-")] = str(value)
    return result


class Response:
    def __init__(self, data=None, **response_headers):
        self.headers = headers(**response_headers)
        self.body = io.BytesIO(json.dumps({} if data is None else data).encode())

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size):
        return self.body.read(size)


class Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def http_error(status, data=None, **response_headers):
    return urllib.error.HTTPError(
        "https://discord.com/api/v10/test",
        status,
        "remote message must stay private",
        headers(**response_headers),
        io.BytesIO(json.dumps(data or {}).encode()),
    )


def client_with_clock(monkeypatch, *responses):
    clock = [100.0]
    monkeypatch.setattr("discord_bridge.api.time.monotonic", lambda: clock[0])
    client = DiscordClient("offline-dummy-token")
    client._opener = Opener(responses)
    return client, clock


@pytest.mark.parametrize(
    "code,expected",
    [(40005, 40005), (True, None), ("private-token", None), (-1, None), (1e30, None)],
)
def test_413_exposes_only_bounded_numeric_metadata(monkeypatch, code, expected):
    failure = http_error(413, {"code": code, "message": "private-token https://private.example"})
    client, _ = client_with_clock(monkeypatch, failure)
    payload = {"content": "offline test"}
    with pytest.raises(DiscordError) as raised:
        client.request("POST", "/channels/111/messages", payload)
    assert raised.value.status == 413 and raised.value.ambiguous is False
    assert raised.value.provider_code == expected
    assert raised.value.request_bytes == len(encode_payload(payload))
    assert raised.value.response_bytes > 0
    assert "private" not in str(raised.value)
    assert client._opener.requests[0].data == encode_payload(payload)


def test_cooldown_hint_reads_observed_route_headers_without_reserving_requests(monkeypatch):
    client, clock = client_with_clock(
        monkeypatch, Response(X_RateLimit_Remaining=0, X_RateLimit_Reset_After=12)
    )
    client.request("POST", "/channels/111/messages", {})
    assert client.cooldown_delay("POST", "/channels/111/messages") == 12
    assert client.cooldown_delay("POST", "/channels/111/messages") == 12
    assert client.cooldown_delay("POST", "/channels/222/messages") == 0
    clock[0] += 7
    assert client.cooldown_delay("POST", "/channels/111/messages") == 5
    assert len(client._opener.requests) == 1


def test_cooldown_hint_excludes_local_request_budget_without_bypassing_it(monkeypatch):
    client, _ = client_with_clock(monkeypatch, *(Response() for _ in range(25)))
    for index in range(25):
        client.request("GET", f"/channels/{index}")
    assert client.retry_delay("POST", "/channels/999/messages") == 1
    assert client.cooldown_delay("POST", "/channels/999/messages") == 0
    with pytest.raises(DiscordError) as raised:
        client.request("POST", "/channels/999/messages", {})
    assert raised.value.global_limit is True
    assert len(client._opener.requests) == 25


def test_cooldown_hint_retains_observed_global_retry_after(monkeypatch):
    client, clock = client_with_clock(
        monkeypatch, http_error(429, {"retry_after": 7.5, "global": True})
    )
    with pytest.raises(DiscordError):
        client.request("GET", "/guilds/111")
    assert client.cooldown_delay("POST", "/channels/999/messages") == 7.5
    clock[0] += 7.5
    assert client.cooldown_delay("POST", "/channels/999/messages") == 0
    assert len(client._opener.requests) == 1


def test_cooldown_hint_validates_routes_and_refuses_closed_clients(monkeypatch):
    client, _ = client_with_clock(monkeypatch)
    with pytest.raises(ValueError):
        client.cooldown_delay("POST", "https://evil.example/collect")
    client.close()
    with pytest.raises(DiscordError, match="closed"):
        client.cooldown_delay("POST", "/channels/999/messages")
    assert client._opener.requests == []


def test_channel_messages_uses_validated_query_and_shared_route_limits(monkeypatch):
    channel, after = "1700000000000000001", "1700000000000000002"
    client, _ = client_with_clock(
        monkeypatch,
        Response(data=[{"id": after}], X_RateLimit_Remaining=0, X_RateLimit_Reset_After=12),
    )
    assert client.channel_messages(channel, limit=10, after=after) == [{"id": after}]
    assert client._opener.requests[0].full_url.endswith(
        f"/channels/{channel}/messages?limit=10&after={after}"
    )
    with pytest.raises(DiscordError) as blocked:
        client.channel_messages(channel, limit=1)
    assert blocked.value.status == 429


def test_guild_members_validates_pagination_and_shares_route_limits(monkeypatch):
    guild, user = "1700000000000000001", "1700000000000000002"
    member = {"user": {"id": user}, "roles": []}
    client, _ = client_with_clock(
        monkeypatch,
        Response(data=[member], X_RateLimit_Remaining=0, X_RateLimit_Reset_After=12),
    )
    assert client.guild_members(guild, limit=1000, after=guild) == [member]
    assert client._opener.requests[0].full_url.endswith(
        f"/guilds/{guild}/members?limit=1000&after={guild}"
    )
    with pytest.raises(DiscordError) as blocked:
        client.guild_members(guild)
    assert blocked.value.status == 429
    for kwargs in ({"after": "1&limit=9999"}, {"limit": True}, {"limit": 1001}):
        with pytest.raises(ValueError):
            client.guild_members(guild, **kwargs)


@pytest.mark.parametrize(
    "rows",
    [None, {}, [None], [{"user": {"id": "bad"}, "roles": []}]],
)
def test_guild_members_rejects_malformed_responses(monkeypatch, rows):
    client, _ = client_with_clock(monkeypatch, Response(data=rows))
    with pytest.raises(DiscordError):
        client.guild_members("1700000000000000001")


def test_channel_messages_validates_before_and_excludes_mixed_cursors(monkeypatch):
    channel, before = "1700000000000000001", "1700000000000000002"
    client, _ = client_with_clock(monkeypatch, Response(data=[]))
    assert client.channel_messages(channel, before=before) == []
    assert client._opener.requests[0].full_url.endswith(f"?limit=100&before={before}")
    with pytest.raises(ValueError):
        client.channel_messages(channel, before=before, after=before)
    with pytest.raises(ValueError):
        client.channel_messages(channel, before=before + "&limit=9999")


@pytest.mark.parametrize(
    "channel,limit,after",
    [
        ("https://evil.invalid", 10, None),
        ("1700000000000000001?x=1", 10, None),
        ("1700000000000000001", 0, None),
        ("1700000000000000001", 101, None),
        ("1700000000000000001", True, None),
        ("1700000000000000001", 10, "1700000000000000002&token=secret"),
    ],
)
def test_channel_messages_rejects_query_injection(monkeypatch, channel, limit, after):
    client, _ = client_with_clock(monkeypatch)
    with pytest.raises(ValueError):
        client.channel_messages(channel, limit=limit, after=after)
    assert client._opener.requests == []


@pytest.mark.parametrize("data", [{}, [{"id": "bad"}], [None]])
def test_channel_messages_rejects_malformed_provider_response(monkeypatch, data):
    client, _ = client_with_clock(monkeypatch, Response(data=data))
    with pytest.raises(DiscordError):
        client.channel_messages("1700000000000000001")


def test_exhausted_guild_get_does_not_block_role_or_channel_endpoints(monkeypatch):
    client, _ = client_with_clock(
        monkeypatch,
        Response(X_RateLimit_Remaining=0, X_RateLimit_Reset_After=30),
        Response(data=[]),
        Response(data=[]),
    )
    client.request("GET", "/guilds/111")
    assert client.request("GET", "/guilds/111/roles") == []
    assert client.request("GET", "/guilds/111/channels") == []
    assert len(client._opener.requests) == 3


def test_unknown_routes_separate_http_methods(monkeypatch):
    client, _ = client_with_clock(
        monkeypatch,
        Response(X_RateLimit_Remaining=0, X_RateLimit_Reset_After=30),
        Response(data={"id": "112"}),
    )
    client.request("GET", "/guilds/111/channels")
    assert client.request("POST", "/guilds/111/channels", {"name": "private"}) == {"id": "112"}
    assert len(client._opener.requests) == 2


def test_same_known_bucket_is_shared_by_endpoints_for_same_major_resource(monkeypatch):
    client, clock = client_with_clock(
        monkeypatch,
        Response(X_RateLimit_Bucket="shared", X_RateLimit_Remaining=1),
        Response(X_RateLimit_Bucket="shared", X_RateLimit_Remaining=0, X_RateLimit_Reset_After=10),
        Response(data={"id": "after-reset"}),
    )
    client.request("GET", "/guilds/111/roles")
    client.request("POST", "/guilds/111/channels", {"name": "private"})
    with pytest.raises(DiscordError) as raised:
        client.request("GET", "/guilds/111/roles")
    assert raised.value.status == 429 and raised.value.retry_after == 10
    assert len(client._opener.requests) == 2
    clock[0] += 10
    assert client.request("GET", "/guilds/111/roles") == {"id": "after-reset"}


@pytest.mark.parametrize("resource", ["guilds", "channels", "webhooks"])
def test_same_server_bucket_id_is_partitioned_by_major_resource(monkeypatch, resource):
    suffix = "/offline-webhook-token" if resource == "webhooks" else ""
    client, _ = client_with_clock(
        monkeypatch,
        Response(X_RateLimit_Bucket="shared", X_RateLimit_Remaining=0, X_RateLimit_Reset_After=10),
        Response(data={"id": "other-major"}, X_RateLimit_Bucket="shared"),
    )
    client.request("GET", f"/{resource}/111{suffix}")
    assert client.request("GET", f"/{resource}/222{suffix}") == {"id": "other-major"}
    assert len(client._opener.requests) == 2


def test_nonmajor_resource_ids_share_normalized_route_before_bucket_discovery(monkeypatch):
    client, _ = client_with_clock(
        monkeypatch, Response(X_RateLimit_Remaining=0, X_RateLimit_Reset_After=10)
    )
    client.request("GET", "/guilds/111/members/222")
    with pytest.raises(DiscordError) as raised:
        client.request("GET", "/guilds/111/members/333")
    assert raised.value.status == 429
    assert len(client._opener.requests) == 1


def test_remote_429_learns_bucket_and_honors_fractional_retry_after_without_retrying(monkeypatch):
    client, clock = client_with_clock(
        monkeypatch,
        Response(X_RateLimit_Bucket="shared"),
        http_error(429, {"retry_after": 0.25, "global": False}, X_RateLimit_Bucket="shared"),
        Response(data={"id": "after-reset"}),
    )
    client.request("GET", "/guilds/111/roles")
    with pytest.raises(DiscordError) as raised:
        client.request("POST", "/guilds/111/channels", {"name": "private"})
    assert raised.value.retry_after == 0.25 and raised.value.global_limit is False
    assert len(client._opener.requests) == 2
    with pytest.raises(DiscordError) as local:
        client.request("GET", "/guilds/111/roles")
    assert local.value.retry_after == 0.25
    assert len(client._opener.requests) == 2
    clock[0] += 0.25
    assert client.request("GET", "/guilds/111/roles") == {"id": "after-reset"}


@pytest.mark.parametrize("body_global,header_global", [(True, False), (False, True)])
def test_global_429_blocks_all_major_resources_and_reports_global_gate(
    monkeypatch, body_global, header_global
):
    extra_headers = {"X_RateLimit_Global": "true"} if header_global else {}
    client, clock = client_with_clock(
        monkeypatch,
        http_error(429, {"retry_after": 12.5, "global": body_global}, **extra_headers),
        Response(),
    )
    with pytest.raises(DiscordError) as raised:
        client.request("GET", "/guilds/111")
    assert raised.value.global_limit is True
    with pytest.raises(DiscordError) as local:
        client.request("GET", "/channels/222")
    assert local.value.global_limit is True and local.value.retry_after == 12.5
    assert len(client._opener.requests) == 1
    clock[0] += 12.5
    assert client.request("GET", "/channels/222") == {}


def test_retry_after_header_is_used_and_long_delays_are_not_shortened(monkeypatch):
    client, _ = client_with_clock(monkeypatch, http_error(429, Retry_After=7200))
    with pytest.raises(DiscordError) as raised:
        client.request("GET", "/guilds/111")
    assert raised.value.retry_after == 7200
    with pytest.raises(DiscordError) as local:
        client.request("GET", "/guilds/111")
    assert local.value.retry_after == 7200
    assert len(client._opener.requests) == 1


def test_bucket_identity_is_learned_from_non429_http_errors_without_retrying(monkeypatch):
    client, _ = client_with_clock(
        monkeypatch,
        http_error(403, X_RateLimit_Bucket="shared"),
        Response(X_RateLimit_Bucket="shared", X_RateLimit_Remaining=0, X_RateLimit_Reset_After=8),
    )
    with pytest.raises(DiscordError) as forbidden:
        client.request("GET", "/guilds/111/roles")
    assert forbidden.value.status == 403
    assert len(client._opener.requests) == 1
    client.request("GET", "/guilds/111/channels")
    with pytest.raises(DiscordError) as local:
        client.request("GET", "/guilds/111/roles")
    assert local.value.status == 429 and local.value.retry_after == 8
    assert len(client._opener.requests) == 2


def test_webhook_tokens_partition_a_shared_bucket_with_same_webhook_id(monkeypatch):
    client, _ = client_with_clock(
        monkeypatch,
        Response(X_RateLimit_Bucket="shared", X_RateLimit_Remaining=0, X_RateLimit_Reset_After=8),
        Response(data={"id": "different-token"}),
    )
    client.request("POST", "/webhooks/111/offline-token-one", {})
    assert client.request("POST", "/webhooks/111/offline-token-two", {}) == {
        "id": "different-token"
    }
    assert len(client._opener.requests) == 2


@pytest.mark.parametrize(
    "failure",
    [urllib.error.URLError("offline-dummy-token must not be printed"), http_error(502)],
)
def test_ambiguous_writes_are_never_retried_and_errors_are_redacted(monkeypatch, failure):
    client, _ = client_with_clock(monkeypatch, failure)
    with pytest.raises(DiscordError) as raised:
        client.request("POST", "/guilds/111/channels", {"name": "private"})
    assert raised.value.retry_after is None
    assert len(client._opener.requests) == 1
    assert "offline-dummy-token" not in str(raised.value)
    assert "remote message" not in str(raised.value)


def test_redirect_handler_refuses_redirects_and_origin_remains_fixed(monkeypatch):
    assert (
        _NoRedirect().redirect_request(None, None, 302, "secret", {}, "https://evil.test") is None
    )
    client, _ = client_with_clock(monkeypatch, Response())
    client.request("GET", "/guilds/111")
    assert client._opener.requests[0].full_url == "https://discord.com/api/v10/guilds/111"
