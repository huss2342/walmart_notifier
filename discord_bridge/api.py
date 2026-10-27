"""Small Discord REST client: fixed HTTPS origin, no redirects, redacted errors."""

from __future__ import annotations

import http.client
import json
import math
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from contextlib import suppress


class DiscordError(Exception):
    def __init__(
        self,
        message: str = "Discord request failed",
        *,
        status: int | None = None,
        retry_after: float | None = None,
        global_limit: bool = False,
        ambiguous: bool = False,
        provider_code: int | None = None,
        request_bytes: int | None = None,
        response_bytes: int | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.global_limit = global_limit
        self.ambiguous = ambiguous
        self.provider_code = provider_code
        self.request_bytes = request_bytes
        self.response_bytes = response_bytes


def encode_payload(payload: dict) -> bytes:
    """The one wire encoding, also used by the envelope byte-budget guard."""
    return json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _request, _file, _code, _message, _headers, _url):
        return None


def _delay(value, fallback=5.0):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return result if math.isfinite(result) and result >= 0 else fallback


def _route_identity(method: str, path: str) -> tuple[tuple[str, str], tuple[str, ...]]:
    """Separate endpoint shape from Discord's independently limited resources.

    Discord bucket headers exclude the major resource. Until a bucket is
    discovered, method plus normalized endpoint keeps unrelated calls apart.
    Nonmajor snowflakes (for example member/message IDs) share their endpoint.
    """
    parts = path.lstrip("/").split("/")
    major = ()
    if len(parts) > 1 and parts[0] in {"guilds", "channels", "webhooks"}:
        major = (parts[0], parts[1])
        parts[1] = "{major_id}"
        if parts[0] == "webhooks" and len(parts) > 2:
            major += (parts[2],)
            parts[2] = "{webhook_token}"
    normalized = "/" + "/".join(
        "{id}" if part.isascii() and part.isdecimal() else part for part in parts
    )
    return (method, normalized), major


class _RequestDeadline:
    """Bound socket work by elapsed time, including a dribbling HTTP peer.

    DNS cannot safely be interrupted by another Python thread. The caller
    explicitly connects and checks again before submitting any request bytes.
    Cancellation and pool return synchronize with the watchdog's socket close.
    """

    def __init__(self, connection, timeout, timer_factory):
        self.connection = connection
        self.until = time.monotonic() + timeout
        self._lock = threading.Lock()
        self.expired = False
        self._finished = False
        self._timer = timer_factory(timeout, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def _expire(self):
        with self._lock:
            if self._finished:
                return
            self.expired = True
            active = getattr(self.connection, "sock", None)
            if active is not None:
                # Do not set connection.sock=None here: http.client could
                # otherwise reconnect implicitly during a late request().
                with suppress(OSError):
                    active.shutdown(socket.SHUT_RDWR)
                with suppress(OSError):
                    active.close()

    def check(self):
        with self._lock:
            remaining = self.until - time.monotonic()
            if self.expired or remaining <= 0:
                self.expired = True
                raise TimeoutError
            active = getattr(self.connection, "sock", None)
            if active is not None:
                active.settimeout(remaining)

    def finish(self):
        with self._lock:
            self._finished = True
            self._timer.cancel()
            return self.expired or time.monotonic() >= self.until


class DiscordClient:
    # A sliding reservation window leaves headroom below Discord's global
    # limit. Route-specific limits are learned from Discord, never guessed.
    _GLOBAL_REQUESTS = 25
    _GLOBAL_WINDOW = 1.0
    _MAX_CONNECTIONS = 4
    _BUSY_DELAY = 0.05
    _MAX_RESPONSE = 1_048_576

    def __init__(self, token: str):
        if not isinstance(token, str) or not token.strip() or any(c.isspace() for c in token):
            raise ValueError("DISCORD_BOT_TOKEN is missing or invalid")
        self._token = token
        # Kept as an explicit offline testing seam. Production uses the fixed
        # HTTPS origin below, with no redirect handling or implicit retries.
        self._opener = None
        self._connection_factory = http.client.HTTPSConnection
        self._timer_factory = threading.Timer
        self._lock = threading.Lock()
        self._connections: list[http.client.HTTPSConnection] = []
        self._connection_count = 0
        self._closed = False
        self._inflight: set[tuple[tuple[str, str], tuple[str, ...]]] = set()
        self._pending_limits: set[tuple[tuple[str, str], tuple[str, ...]]] = set()
        self._started: deque[float] = deque()
        self._blocked: dict[tuple, float] = {}
        self._route_buckets: dict[tuple[str, str], str] = {}
        self._global_until = 0.0

    def _limit_key(self, route: tuple[str, str], major: tuple[str, ...]) -> tuple:
        bucket = self._route_buckets.get(route)
        return ("bucket", bucket, major) if bucket else ("route", *route, major)

    def _learn_bucket(self, route: tuple[str, str], major: tuple[str, ...], headers) -> tuple:
        """Update shared rate state while holding ``_lock``."""
        previous = self._limit_key(route, major)
        bucket = headers.get("X-RateLimit-Bucket")
        if isinstance(bucket, str) and bucket.strip():
            self._route_buckets[route] = bucket
        key = self._limit_key(route, major)
        # Keep any previously observed wait if Discord now identifies or
        # changes the bucket. The fallback gate is also checked per resource.
        if key != previous:
            self._blocked[key] = max(self._blocked.get(key, 0), self._blocked.get(previous, 0))
        return key

    def _block_route(self, route, major, key, deadline):
        # Retain the endpoint fallback if a later response changes its bucket
        # identifier. A bucket change must not shorten an observed wait.
        for gate in (key, ("route", *route, major)):
            self._blocked[gate] = max(self._blocked.get(gate, 0), deadline)

    @staticmethod
    def _validate_route(method: str, path: str):
        if (
            method not in {"GET", "POST", "PATCH", "PUT", "DELETE"}
            or not re.fullmatch(r"/[A-Za-z0-9_/@.\-]+", path)
            or ".." in path
            or "//" in path
        ):
            raise ValueError("invalid Discord API route")

    def _cooldown_until(self, route, major):
        """Only deadlines learned from Discord response headers or 429s."""
        key = self._limit_key(route, major)
        return max(
            self._blocked.get(key, 0),
            self._blocked.get(("route", *route, major), 0),
            self._global_until,
        )

    def _wait_until(self, route, major, now):
        """Read gates under the lock without changing state."""
        key = self._limit_key(route, major)
        blocked = self._cooldown_until(route, major)
        global_limit = self._global_until > now
        recent = [started for started in self._started if started > now - self._GLOBAL_WINDOW]
        if len(recent) >= self._GLOBAL_REQUESTS:
            blocked = max(blocked, recent[-self._GLOBAL_REQUESTS] + self._GLOBAL_WINDOW)
            global_limit = True
        if self._pending_limits:
            # A 429 can declare its global scope in the JSON body. Stop new
            # reservations until that bounded response has been interpreted.
            blocked = max(blocked, now + self._BUSY_DELAY)
            global_limit = True
        # Serializing a major resource prevents two responses for the same
        # channel from racing rate headers. Other channels remain independent.
        for active_route, active_major in self._inflight:
            same_resource = (
                major == active_major
                if major
                else (not active_major and key == self._limit_key(active_route, active_major))
            )
            if same_resource:
                blocked = max(blocked, now + self._BUSY_DELAY)
                break
        if not self._connections and self._connection_count >= self._MAX_CONNECTIONS:
            blocked = max(blocked, now + self._BUSY_DELAY)
        return max(0.0, blocked - now), global_limit

    def retry_delay(self, method: str, path: str) -> float:
        """Return a current scheduling hint, without sending or reserving work.

        Requests still recheck atomically: a different worker may reserve the
        remaining capacity after this snapshot. In-flight routes return a
        brief deferral instead of making another thread wait on their socket.
        """
        self._validate_route(method, path)
        route, major = _route_identity(method, path)
        with self._lock:
            if self._closed:
                raise DiscordError("Discord client is closed")
            return self._wait_until(route, major, time.monotonic())[0]

    def cooldown_delay(self, method: str, path: str) -> float:
        """Observed provider cooldown only, without a request or reservation.

        Excludes local socket contention and conservative request budgeting;
        the actual request still enforces those gates atomically.
        """
        self._validate_route(method, path)
        route, major = _route_identity(method, path)
        with self._lock:
            if self._closed:
                raise DiscordError("Discord client is closed")
            return max(0.0, self._cooldown_until(route, major) - time.monotonic())

    def close(self):
        """Close idle connections; active requests close theirs on completion."""
        with self._lock:
            self._closed = True
            connections, self._connections = self._connections, []
            self._connection_count -= len(connections)
        for connection in connections:
            connection.close()

    def request(self, method: str, path: str, payload: dict | None = None):
        return self._request(method, path, payload)

    def guild_members(self, guild_id: str, *, limit=1000, after: str | None = None):
        """Read one validated member page; requires Server Members Intent."""
        if (
            not isinstance(guild_id, str)
            or not re.fullmatch(r"[0-9]{17,20}", guild_id)
            or type(limit) is not int
            or not 1 <= limit <= 1000
            or (
                after is not None
                and (not isinstance(after, str) or not re.fullmatch(r"[0-9]{17,20}", after))
            )
        ):
            raise ValueError("invalid Discord member pagination parameters")
        query = f"?limit={limit}" + (f"&after={after}" if after is not None else "")
        rows = self._request("GET", f"/guilds/{guild_id}/members", query=query)
        if not isinstance(rows, list) or len(rows) > limit:
            raise DiscordError("Invalid Discord member response")
        seen = set()
        for row in rows:
            user = row.get("user") if isinstance(row, dict) else None
            user_id = user.get("id") if isinstance(user, dict) else None
            if (
                not isinstance(user_id, str)
                or not re.fullmatch(r"[0-9]{17,20}", user_id)
                or user_id in seen
                or (after is not None and int(user_id) <= int(after))
                or not isinstance(row.get("roles"), list)
                or not all(
                    isinstance(role, str) and re.fullmatch(r"[0-9]{17,20}", role)
                    for role in row["roles"]
                )
                or type(user.get("bot", False)) is not bool
            ):
                raise DiscordError("Invalid Discord member response")
            seen.add(user_id)
        return rows

    def channel_messages(
        self,
        channel_id: str,
        *,
        limit=100,
        after: str | None = None,
        before: str | None = None,
    ):
        """Read bounded history without opening arbitrary query/URL support.

        Query values are validated here; rate accounting uses the same bare
        endpoint and channel major resource as ordinary message operations.
        """
        if (
            not isinstance(channel_id, str)
            or not re.fullmatch(r"[0-9]{17,20}", channel_id)
            or type(limit) is not int
            or not 1 <= limit <= 100
            or (
                after is not None
                and (not isinstance(after, str) or not re.fullmatch(r"[0-9]{17,20}", after))
            )
            or (
                before is not None
                and (not isinstance(before, str) or not re.fullmatch(r"[0-9]{17,20}", before))
            )
            or (after is not None and before is not None)
        ):
            raise ValueError("invalid Discord message history parameters")
        query = f"?limit={limit}" + (f"&after={after}" if after is not None else "")
        if before is not None:
            query += f"&before={before}"
        rows = self._request("GET", f"/channels/{channel_id}/messages", query=query)
        if not isinstance(rows, list) or len(rows) > limit:
            raise DiscordError("Discord message history response is invalid")
        seen = set()
        for row in rows:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("id"), str)
                or not re.fullmatch(r"[0-9]{17,20}", row["id"])
                or row["id"] in seen
                or row.get("channel_id", channel_id) != channel_id
            ):
                raise DiscordError("Discord message history response is invalid")
            seen.add(row["id"])
        return rows

    def _request(self, method: str, path: str, payload: dict | None = None, *, query=""):
        self._validate_route(method, path)
        route, major = _route_identity(method, path)
        data = encode_payload(payload) if payload is not None else None
        headers = {
            "Authorization": "Bot " + self._token,
            "Content-Type": "application/json",
            "User-Agent": "ReviewerAlerts/1.0 (https://discord.com/developers/docs)",
        }
        with self._lock:
            if self._closed:
                raise DiscordError("Discord client is closed")
            now = time.monotonic()
            wait, global_limit = self._wait_until(route, major, now)
            if wait:
                raise DiscordError(
                    "Discord rate limit", status=429, retry_after=wait, global_limit=global_limit
                )
            while self._started and self._started[0] <= now - self._GLOBAL_WINDOW:
                self._started.popleft()
            self._started.append(now)
            self._inflight.add((route, major))
            connection = self._connections.pop() if self._connections else None
            if connection is None:
                self._connection_count += 1
        response = None
        deadline = None
        submitted = False
        reusable = False
        try:
            if self._opener is not None:
                deadline = _RequestDeadline(None, 10, self._timer_factory)
                deadline.check()
                request = urllib.request.Request(
                    "https://discord.com/api/v10" + path + query,
                    data=data,
                    method=method,
                    headers=headers,
                )
                try:
                    submitted = True
                    response = self._opener.open(request, timeout=10)
                    status = getattr(response, "status", 200)
                except urllib.error.HTTPError as exc:
                    response = exc
                    status = exc.code
            else:
                if connection is None:
                    connection = self._connection_factory("discord.com", timeout=10)
                deadline = _RequestDeadline(connection, 10, self._timer_factory)
                deadline.check()
                if connection.sock is None:
                    connection.connect()
                # A late DNS/TLS result must never submit a timed-out POST.
                deadline.check()
                submitted = True
                connection.request(method, "/api/v10" + path + query, body=data, headers=headers)
                deadline.check()
                response = connection.getresponse()
                status = response.status
            response_headers = response.headers or {}
            with self._lock:
                key = self._learn_bucket(route, major, response_headers)
                if status == 429:
                    self._pending_limits.add((route, major))
                if (
                    status != 429
                    and str(response_headers.get("X-RateLimit-Remaining", "")).strip() == "0"
                ):
                    self._block_route(
                        route,
                        major,
                        key,
                        time.monotonic() + _delay(response_headers.get("X-RateLimit-Reset-After")),
                    )
            body_limit = self._MAX_RESPONSE if 200 <= status < 300 else 65536
            try:
                deadline.check()
                body = response.read(body_limit + 1)
            except (OSError, http.client.HTTPException):
                if status != 429:
                    raise
                # Status and rate headers remain useful even if the server
                # drops its error body. A bounded fallback still gates retries.
                body = b""
            if 200 <= status < 300:
                if len(body) > body_limit:
                    raise DiscordError("Discord response exceeded size limit")
                result = json.loads(body) if body else None
                deadline.check()
                reusable = not getattr(response, "will_close", False)
                return result
            retry_after = None
            global_limit = False
            try:
                error = json.loads(body) if len(body) <= body_limit else {}
            except ValueError:
                error = {}
            if not isinstance(error, dict):
                error = {}
            provider_code = error.get("code")
            if type(provider_code) is not int or not 0 <= provider_code <= 1_000_000_000:
                provider_code = None
            if status == 429:
                retry_after = _delay(
                    error.get("retry_after"),
                    fallback=_delay(response_headers.get("Retry-After")),
                )
                global_limit = (
                    error.get("global") is True
                    or str(response_headers.get("X-RateLimit-Global", "")).lower() == "true"
                    or str(response_headers.get("X-RateLimit-Scope", "")).lower() == "global"
                )
                with self._lock:
                    # Recompute after another channel may have learned an alias.
                    key = self._limit_key(route, major)
                    cooldown_until = time.monotonic() + retry_after
                    if global_limit:
                        self._global_until = max(self._global_until, cooldown_until)
                    else:
                        self._block_route(route, major, key, cooldown_until)
            raise DiscordError(
                f"Discord returned HTTP {status}",
                status=status,
                retry_after=retry_after,
                global_limit=global_limit,
                provider_code=provider_code,
                request_bytes=len(data) if data is not None else 0,
                response_bytes=len(body),
            )
        except (OSError, urllib.error.URLError, ValueError, http.client.HTTPException):
            timed_out = deadline is not None and (
                deadline.expired or time.monotonic() >= deadline.until
            )
            raise DiscordError(
                "Discord request timed out"
                if timed_out
                else "Discord connection or response failed",
                ambiguous=submitted and method != "GET",
            ) from None
        finally:
            if response is not None and hasattr(response, "close"):
                try:
                    response.close()
                except OSError:
                    reusable = False
            if deadline is not None and deadline.finish():
                reusable = False
            with self._lock:
                self._inflight.discard((route, major))
                self._pending_limits.discard((route, major))
                if connection is not None and reusable and not self._closed:
                    self._connections.append(connection)
                    connection = None
                else:
                    self._connection_count -= 1
            if connection is not None:
                connection.close()
