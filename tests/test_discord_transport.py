"""Offline socket-pool and concurrency checks for the Discord HTTP client."""

from __future__ import annotations

import http.client
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from email.message import Message

import pytest
from discord_bridge.api import DiscordClient, DiscordError


class SocketResponse:
    def __init__(self, data=None, *, status=200, body=None, will_close=False, **headers):
        self.status = status
        self.headers = Message()
        for name, value in headers.items():
            self.headers[name.replace("_", "-")] = str(value)
        self.body = io.BytesIO(json.dumps(data or {}).encode() if body is None else body)
        self.will_close = will_close
        self.closed = False

    def read(self, size):
        return self.body.read(size)

    def close(self):
        self.closed = True


class SocketFactory:
    def __init__(self, responses):
        self.responses = list(responses)
        self.connections = []
        self.requests = []
        self.lock = threading.Lock()

    def __call__(self, host, *, timeout):
        assert host == "discord.com" and timeout == 10
        connection = SocketConnection(self)
        with self.lock:
            self.connections.append(connection)
        return connection


class SocketConnection:
    def __init__(self, factory):
        self.factory = factory
        self.closed = False
        self.response = None
        self.sock = None

    def connect(self):
        self.sock = FakeSocket()

    def request(self, method, path, *, body, headers):
        assert self.closed is False
        assert path.startswith("/api/v10/")
        assert headers["Authorization"] == "Bot offline-dummy-token"
        with self.factory.lock:
            self.factory.requests.append((method, path, body))
            self.response = self.factory.responses.pop(0)
        if isinstance(self.response, Exception):
            raise self.response

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True
        if self.sock is not None:
            self.sock.close()
            self.sock = None


class FakeSocket:
    def __init__(self):
        self.timeouts = []
        self.shutdowns = []
        self.closed = False

    def settimeout(self, seconds):
        self.timeouts.append(seconds)

    def shutdown(self, how):
        self.shutdowns.append(how)

    def close(self):
        self.closed = True


def socket_client(monkeypatch, *responses):
    clock = [100.0]
    monkeypatch.setattr("discord_bridge.api.time.monotonic", lambda: clock[0])
    client = DiscordClient("offline-dummy-token")
    factory = SocketFactory(responses)
    client._connection_factory = factory
    return client, factory, clock


def test_successful_requests_reuse_one_tls_connection_and_close_releases_it(monkeypatch):
    first, second = SocketResponse({"id": "one"}), SocketResponse({"id": "two"})
    client, factory, _ = socket_client(monkeypatch, first, second)
    assert client.request("POST", "/channels/111/messages", {"content": "one"}) == {"id": "one"}
    assert client.request("POST", "/channels/222/messages", {"content": "two"}) == {"id": "two"}
    assert len(factory.connections) == 1
    assert first.closed and second.closed
    assert factory.connections[0].closed is False
    client.close()
    client.close()
    assert factory.connections[0].closed is True
    with pytest.raises(DiscordError, match="closed"):
        client.request("GET", "/channels/111")
    with pytest.raises(DiscordError, match="closed"):
        client.retry_delay("GET", "/channels/111")


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 403, 500, 502])
def test_error_or_redirect_is_never_followed_retried_or_reused(monkeypatch, status):
    error = SocketResponse(status=status, Location="https://untrusted.example/steal-token")
    client, factory, _ = socket_client(monkeypatch, error, SocketResponse({"id": "next"}))
    with pytest.raises(DiscordError) as raised:
        client.request("POST", "/channels/111/messages", {"content": "one"})
    assert raised.value.status == status
    assert len(factory.requests) == 1
    assert factory.connections[0].closed is True
    assert error.closed is True
    assert client.request("GET", "/channels/222") == {"id": "next"}
    assert len(factory.connections) == 2


@pytest.mark.parametrize(
    "failure",
    [
        OSError("offline-dummy-token must stay private"),
        http.client.RemoteDisconnected("secret in ambiguous POST failure"),
        http.client.BadStatusLine("secret remote status"),
    ],
)
def test_broken_connection_never_retries_ambiguous_post_or_leaks_remote_text(monkeypatch, failure):
    client, factory, _ = socket_client(monkeypatch, failure)
    with pytest.raises(DiscordError) as raised:
        client.request("POST", "/channels/111/messages", {})
    assert str(raised.value) == "Discord connection or response failed"
    assert len(factory.requests) == len(factory.connections) == 1
    assert factory.connections[0].closed is True


@pytest.mark.parametrize(
    "body", [b"{invalid", b"\xff", b"x" * 1_048_577], ids=["json", "encoding", "oversized"]
)
def test_bad_or_oversized_response_discards_connection(monkeypatch, body):
    response = SocketResponse(body=body)
    client, factory, _ = socket_client(monkeypatch, response)
    with pytest.raises(DiscordError):
        client.request("POST", "/channels/111/messages", {})
    assert factory.connections[0].closed and response.closed
    assert len(factory.requests) == 1


def test_explicit_connection_close_prevents_reuse(monkeypatch):
    client, factory, _ = socket_client(
        monkeypatch, SocketResponse(will_close=True), SocketResponse()
    )
    client.request("GET", "/channels/111")
    assert factory.connections[0].closed
    client.request("GET", "/channels/222")
    assert len(factory.connections) == 2


def test_global_reservation_is_atomic_non_mutating_and_below_discord_ceiling(monkeypatch):
    client, factory, clock = socket_client(monkeypatch, *(SocketResponse() for _ in range(26)))
    for index in range(25):
        assert client.retry_delay("GET", f"/channels/{index}") == 0
        assert client.retry_delay("GET", f"/channels/{index}") == 0
        client.request("GET", f"/channels/{index}")
    assert len(factory.requests) == 25
    assert client.retry_delay("GET", "/guilds/111") == 1.0
    with pytest.raises(DiscordError) as raised:
        client.request("POST", "/channels/999/messages", {})
    assert raised.value.global_limit and raised.value.retry_after == 1.0
    assert len(factory.requests) == 25
    clock[0] += 0.5
    assert client.retry_delay("GET", "/channels/999/messages") == 0.5
    clock[0] += 0.5
    assert client.request("POST", "/channels/999/messages", {}) == {}


class PausedResponse(SocketResponse):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()

    def read(self, size):
        self.entered.set()
        assert self.release.wait(timeout=5), "test did not release fake response"
        return super().read(size)


def test_independent_channels_use_four_connections_without_waiting_on_same_channel(monkeypatch):
    responses = [PausedResponse() for _ in range(4)]
    client, factory, _ = socket_client(monkeypatch, *responses)
    with ThreadPoolExecutor(max_workers=4) as workers:
        pending = []
        try:
            for index, response in enumerate(responses):
                pending.append(
                    workers.submit(client.request, "POST", f"/channels/{index}/messages", {})
                )
                assert response.entered.wait(timeout=5)
            assert len(factory.connections) == 4
            assert client.retry_delay("GET", "/channels/0") > 0
            with pytest.raises(DiscordError) as busy:
                client.request("POST", "/channels/0/messages", {})
            assert busy.value.status == 429 and busy.value.retry_after > 0
            with pytest.raises(DiscordError) as full:
                client.request("POST", "/channels/999/messages", {})
            assert full.value.status == 429 and full.value.retry_after > 0
            assert len(factory.requests) == 4
        finally:
            for response in responses:
                response.release.set()
        assert [future.result(timeout=5) for future in pending] == [{}] * 4
    assert len(client._connections) == 4
    client.close()
    assert all(connection.closed for connection in factory.connections)


def test_same_major_is_serialized_even_when_other_pool_connections_are_free(monkeypatch):
    paused = PausedResponse(X_RateLimit_Bucket="messages", X_RateLimit_Remaining=2)
    client, factory, _ = socket_client(monkeypatch, paused, SocketResponse())
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(client.request, "POST", "/channels/111/messages", {})
        try:
            assert paused.entered.wait(timeout=5)
            with pytest.raises(DiscordError) as busy:
                client.request("GET", "/channels/111/messages/222")
            assert busy.value.retry_after > 0
            assert client.retry_delay("POST", "/channels/333/messages") == 0
            assert client.request("POST", "/channels/333/messages", {}) == {}
        finally:
            paused.release.set()
        assert pending.result(timeout=5) == {}
    assert len(factory.connections) == 2


def test_known_bucket_aliases_without_major_resources_share_inflight_and_reset_gates(monkeypatch):
    paused = PausedResponse(
        X_RateLimit_Bucket="shared", X_RateLimit_Remaining=0, X_RateLimit_Reset_After=7
    )
    client, factory, clock = socket_client(
        monkeypatch,
        SocketResponse(X_RateLimit_Bucket="shared"),
        SocketResponse(X_RateLimit_Bucket="shared"),
        paused,
        SocketResponse(),
    )
    client.request("GET", "/users/@me")
    client.request("GET", "/gateway/bot")
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(client.request, "GET", "/users/@me")
        try:
            assert paused.entered.wait(timeout=5)
            assert client.retry_delay("GET", "/gateway/bot") == 7
            with pytest.raises(DiscordError):
                client.request("GET", "/gateway/bot")
            assert len(factory.requests) == 3
        finally:
            paused.release.set()
        pending.result(timeout=5)
    clock[0] += 7
    assert client.request("GET", "/gateway/bot") == {}


def test_global_429_from_one_worker_gates_other_channels_without_retrying(monkeypatch):
    paused = PausedResponse()
    client, factory, clock = socket_client(
        monkeypatch,
        paused,
        SocketResponse({"global": True, "retry_after": 3.25}, status=429),
        SocketResponse(),
    )
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(client.request, "POST", "/channels/111/messages", {})
        try:
            assert paused.entered.wait(timeout=5)
            with pytest.raises(DiscordError) as limited:
                client.request("POST", "/channels/222/messages", {})
            assert limited.value.global_limit and limited.value.retry_after == 3.25
            assert client.retry_delay("POST", "/channels/333/messages") == 3.25
            with pytest.raises(DiscordError) as gated:
                client.request("POST", "/channels/333/messages", {})
            assert gated.value.global_limit and gated.value.retry_after == 3.25
            assert len(factory.requests) == 2
        finally:
            paused.release.set()
        pending.result(timeout=5)
    clock[0] += 3.25
    assert client.request("POST", "/channels/333/messages", {}) == {}


def test_429_body_in_flight_temporarily_gates_all_reservations(monkeypatch):
    paused = PausedResponse(status=429, data={"global": True, "retry_after": 2.5})
    client, factory, _ = socket_client(monkeypatch, paused)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(client.request, "POST", "/channels/111/messages", {})
        try:
            assert paused.entered.wait(timeout=5)
            assert client.retry_delay("POST", "/channels/222/messages") > 0
            with pytest.raises(DiscordError) as waiting:
                client.request("POST", "/channels/222/messages", {})
            assert waiting.value.global_limit
            assert len(factory.requests) == 1
        finally:
            paused.release.set()
        with pytest.raises(DiscordError) as limited:
            pending.result(timeout=5)
        assert limited.value.global_limit and limited.value.retry_after == 2.5
    assert client.retry_delay("POST", "/channels/222/messages") == 2.5


def test_disconnected_429_body_still_honors_global_headers(monkeypatch):
    class InterruptedErrorBody(SocketResponse):
        def read(self, _size):
            raise http.client.IncompleteRead(b"secret")

    response = InterruptedErrorBody(status=429, X_RateLimit_Global="true", Retry_After="8.25")
    client, factory, _ = socket_client(monkeypatch, response)
    with pytest.raises(DiscordError) as limited:
        client.request("POST", "/channels/111/messages", {})
    assert limited.value.status == 429 and limited.value.retry_after == 8.25
    assert limited.value.global_limit
    assert client.retry_delay("POST", "/channels/222/messages") == 8.25
    assert factory.connections[0].closed


def test_close_during_request_closes_active_connection_when_it_finishes(monkeypatch):
    paused = PausedResponse()
    client, factory, _ = socket_client(monkeypatch, paused)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(client.request, "POST", "/channels/111/messages", {})
        try:
            assert paused.entered.wait(timeout=5)
            client.close()
            assert client._closed is True
        finally:
            paused.release.set()
        assert pending.result(timeout=5) == {}
    assert factory.connections[0].closed
    assert client._connection_count == 0


class ManualTimer:
    def __init__(self, seconds, callback):
        assert seconds == 10
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self, *, already_queued=False):
        if not self.cancelled or already_queued:
            self.callback()


class TimerFactory:
    def __init__(self):
        self.timers = []

    def __call__(self, seconds, callback):
        timer = ManualTimer(seconds, callback)
        self.timers.append(timer)
        return timer


def test_slow_dns_or_tls_never_submits_post_after_total_deadline(monkeypatch):
    client, factory, clock = socket_client(monkeypatch, SocketResponse())
    timers = TimerFactory()
    client._timer_factory = timers
    original_factory = client._connection_factory

    def slow_connection(host, *, timeout):
        connection = original_factory(host, timeout=timeout)
        original_connect = connection.connect

        def slow_connect():
            # Models DNS that cannot be interrupted while no socket exists.
            timers.timers[-1].fire()
            original_connect()
            clock[0] += 11

        connection.connect = slow_connect
        return connection

    client._connection_factory = slow_connection
    with pytest.raises(DiscordError, match="timed out") as failure:
        client.request("POST", "/channels/111/messages", {})
    assert failure.value.ambiguous is False
    assert len(factory.requests) == 0
    assert factory.connections[0].closed
    assert timers.timers[0].daemon and timers.timers[0].cancelled


def test_watchdog_closes_socket_during_response_headers_and_marks_write_ambiguous(monkeypatch):
    client, factory, _ = socket_client(monkeypatch, SocketResponse())
    timers = TimerFactory()
    client._timer_factory = timers
    original_factory = client._connection_factory
    active_sockets = []

    def stalled_connection(host, *, timeout):
        connection = original_factory(host, timeout=timeout)

        def slow_headers():
            active_sockets.append(connection.sock)
            timers.timers[-1].fire()
            raise OSError("private header read error")

        connection.getresponse = slow_headers
        return connection

    client._connection_factory = stalled_connection
    with pytest.raises(DiscordError, match="timed out") as failure:
        client.request("POST", "/channels/111/messages", {})
    assert failure.value.ambiguous is True
    assert len(factory.requests) == 1
    assert active_sockets[0].shutdowns and active_sockets[0].closed
    assert factory.connections[0].closed
    assert not client._connections


def test_dribbling_success_body_cannot_outlive_total_deadline_or_reuse_socket(monkeypatch):
    client, factory, clock = socket_client(monkeypatch)
    timers = TimerFactory()
    client._timer_factory = timers

    class DribblingResponse(SocketResponse):
        def read(self, size):
            # Every modeled chunk arrives within an inactivity timeout, but
            # their total duration exceeds the absolute request budget.
            for _ in range(3):
                clock[0] += 4
            timers.timers[-1].fire()
            return super().read(size)

    factory.responses.append(DribblingResponse({"id": "late"}))
    with pytest.raises(DiscordError, match="timed out") as failure:
        client.request("POST", "/channels/111/messages", {})
    assert failure.value.ambiguous
    assert len(factory.requests) == 1
    assert factory.connections[0].closed
    assert not client._connections


def test_queued_watchdog_callback_cannot_close_returned_or_reused_connection(monkeypatch):
    client, factory, _ = socket_client(monkeypatch, SocketResponse(), SocketResponse())
    timers = TimerFactory()
    client._timer_factory = timers
    client.request("POST", "/channels/111/messages", {})
    active = factory.connections[0].sock
    timers.timers[0].fire(already_queued=True)
    assert active.closed is False
    assert client.request("POST", "/channels/222/messages", {}) == {}
    assert len(factory.connections) == 1
    timers.timers[0].fire(already_queued=True)
    timers.timers[1].fire(already_queued=True)
    assert active.closed is False
    assert all(timer.daemon and timer.started and timer.cancelled for timer in timers.timers)


def test_socket_timeout_at_deadline_is_classified_without_waiting_for_watchdog(monkeypatch):
    client, factory, clock = socket_client(monkeypatch)
    timers = TimerFactory()
    client._timer_factory = timers

    class TimedOutResponse(SocketResponse):
        def read(self, _size):
            clock[0] += 10
            raise TimeoutError("private socket details")

    factory.responses.append(TimedOutResponse())
    with pytest.raises(DiscordError, match="timed out") as failure:
        client.request("POST", "/channels/111/messages", {})
    assert failure.value.ambiguous
    assert len(factory.requests) == 1
    assert factory.connections[0].closed
