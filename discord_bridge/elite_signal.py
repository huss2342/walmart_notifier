"""Forward requested new/restocked in-stock items to the owner's Reviewer Alerts Elite service.

Optional and off unless ELITE_SIGNAL_URL and ELITE_SIGNAL_KEY are set. Delivery runs on
its own thread so Discord intake/delivery latency is unaffected; every attempt is logged
through the bridge monitor as an `elite_signal` event.

Every new/restocked in-stock priced item is forwarded: Elite keeps them all for request
previews ("what would this request have claimed?") and turns only items a member's request
matches into claim opportunities, so matching lives in one place. The Elite watch
(`/v2/signals/watch`) still supplies its requests' searches for the scanner's request lane,
so Elite requests get dedicated Walmart searches like Pro requests.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SIGNAL_TRANSITIONS = {"queued", "rearmed", "restocked"}
SIGNAL_ROUTE = "pro-all"  # Every available item has this route; FREE-only items are out of stock.
MAX_QUEUE = 1000
BATCH_ITEMS = 100  # Elite accepts at most 100 items per POST
TIMEOUT_SECONDS = 3
# The Elite service is remote (~150 ms away): a fresh HTTPS connection costs ~3 round trips before the
# request is even sent. Deliveries reuse one kept-alive connection, pinged while idle so it stays open.
KEEPALIVE_SECONDS = 45
ATTEMPTS = 3
WATCH_REFRESH_SECONDS = 15
WATCH_STALE_SECONDS = 120


class EliteSignal:
    def __init__(self, url: str, key: str, event_sink=None, *, opener=None, watch=False):
        if not url.startswith(("http://", "https://")):
            raise ValueError("ELITE_SIGNAL_URL must be an http(s) URL")
        if not 32 <= len(key) <= 256 or any(c.isspace() for c in key):
            raise ValueError("ELITE_SIGNAL_KEY must be 32-256 non-space characters")
        self.url, self.key, self.event_sink = url, key, event_sink
        self.opener = opener or urllib.request.urlopen
        self.persistent = opener is None
        self._conn: http.client.HTTPConnection | None = None
        self.queue: queue.Queue = queue.Queue(MAX_QUEUE)
        self.watch_url = url.rsplit("/", 1)[0] + "/watch"
        self._watch: dict | None = None
        self._watch_at = 0.0
        self._watch_ok: bool | None = None
        self.thread = threading.Thread(target=self._run, name="elite-signal", daemon=True)
        self.thread.start()
        if watch:
            threading.Thread(target=self._watch_loop, name="elite-watch", daemon=True).start()

    @classmethod
    def from_env(cls, event_sink=None):
        url = os.environ.get("ELITE_SIGNAL_URL", "").strip()
        key = os.environ.get("ELITE_SIGNAL_KEY", "").strip()
        if not url or not key:
            return None
        return cls(url, key, event_sink, watch=True)

    def _emit(self, **fields):
        if self.event_sink is not None:
            with contextlib.suppress(Exception):
                self.event_sink("elite_signal", **fields)

    def capture(self, downstream=None):
        """Return (sink, routed): a per-request monitor sink recording item_id -> transition."""
        routed: dict[str, str] = {}

        def sink(event, **fields):
            if (event == "observation_routed" and fields.get("route") == SIGNAL_ROUTE
                    and fields.get("transition") in SIGNAL_TRANSITIONS):
                for item_id in fields.get("item_ids") or ():
                    routed[item_id] = fields["transition"]
            return downstream(event, **fields) if downstream is not None else True

        return sink, routed

    def set_watch(self, body: dict, now: float | None = None) -> None:
        watches = body["watches"]
        if not isinstance(watches, list) or not isinstance(body.get("searches"), list):
            raise ValueError("invalid watch")
        for watch in watches:
            for key in ("keywords", "excludeKeywords", "categoryIds"):
                if not isinstance(watch[key], list):
                    raise ValueError("invalid watch")
            if type(watch["minValueCents"]) is not int or watch["keywordMode"] not in ("all", "any"):
                raise ValueError("invalid watch")
        self._watch = {"watches": watches, "categoryMap": dict(body.get("categoryMap") or {}),
                       "searches": [q for q in body["searches"] if isinstance(q, str) and q.strip()][:50]}
        self._watch_at = time.time() if now is None else now

    def _fresh_watch(self):
        if self._watch is None or time.time() - self._watch_at > WATCH_STALE_SECONDS:
            return None
        return self._watch

    def search_queries(self) -> list[str]:
        """Elite requests' searches for the scanner's request lane (empty if the watch is stale)."""
        watch = self._fresh_watch()
        return list(watch["searches"]) if watch is not None else []

    def _watch_loop(self):
        while True:
            try:
                request = urllib.request.Request(self.watch_url, method="GET",
                                                 headers={"Authorization": "Bearer " + self.key})
                with self.opener(request, timeout=TIMEOUT_SECONDS) as response:
                    self.set_watch(json.loads(response.read() or b"{}"))
                if self._watch_ok is not True:
                    self._watch_ok = True
                    self._emit(outcome="watch_loaded", watches=len(self._watch["watches"]),
                               searches=len(self._watch["searches"]))
            except Exception as exc:  # Elite down or old version: keep forwarding everything
                if self._watch_ok is not False:
                    self._watch_ok = False
                    self._emit(outcome="watch_unavailable", error_type=type(exc).__name__)
            time.sleep(WATCH_REFRESH_SECONDS)

    def offer(self, items: list[dict], routed: dict[str, str]) -> int:
        payload = []
        for item in items:
            if item.get("item_id") not in routed or not item.get("available"):
                continue
            if item.get("value_cents") is None:
                continue
            payload.append({
                "itemId": item["item_id"].removeprefix("ip-"),
                "title": item["title"][:500],
                "valueCents": item["value_cents"],
                "category": item.get("category", "other"),
                "url": item["review_url"],
                "transition": routed[item["item_id"]],
            })
        if not payload:
            return 0
        sent = 0
        for start in range(0, len(payload), BATCH_ITEMS):
            batch = payload[start:start + BATCH_ITEMS]
            try:
                self.queue.put_nowait((time.time(), batch))
                sent += len(batch)
            except queue.Full:
                self._emit(outcome="rejected", error_type="QueueFull", items=len(batch),
                           item_ids=["ip-" + p["itemId"] for p in batch])
        return sent

    def _request(self, method, path, body=None, headers=None):
        """One request on the kept-alive connection; a stale connection is reopened once, at once."""
        for fresh in (False, True):
            if self._conn is None or fresh:
                self._close()
                parts = urllib.parse.urlsplit(self.url)
                kind = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
                self._conn = kind(parts.netloc, timeout=TIMEOUT_SECONDS)
                fresh = True
            try:
                self._conn.request(method, path, body=body, headers=headers or {})
                response = self._conn.getresponse()
                return response.status, response.reason, response.headers, response.read()
            except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError):
                self._close()
                if fresh:
                    raise
            except Exception:
                self._close()
                raise
        raise ConnectionError("unreachable")

    def _close(self):
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None

    def _post(self, payload):
        body = json.dumps({"items": payload}).encode()
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + self.key}
        if self.persistent:
            status, reason, response_headers, data = self._request(
                "POST", urllib.parse.urlsplit(self.url).path, body, headers)
            if status >= 400:
                raise urllib.error.HTTPError(self.url, status, reason, response_headers, None)
            return status, json.loads(data or b"{}")
        request = urllib.request.Request(self.url, data=body, method="POST", headers=headers)
        with self.opener(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, json.loads(response.read() or b"{}")

    def _keepalive(self):
        with contextlib.suppress(Exception):
            self._request("GET", "/health")

    def _run(self):
        if self.persistent:
            self._keepalive()
        while True:
            try:
                queued_at, payload = self.queue.get(timeout=KEEPALIVE_SECONDS if self.persistent else None)
            except queue.Empty:
                self._keepalive()
                continue
            ids = ["ip-" + p["itemId"] for p in payload]
            for attempt in range(1, ATTEMPTS + 1):
                started = time.time()
                try:
                    status, body = self._post(payload)
                    rejected = body.get("rejected") or []
                    self._emit(outcome="accepted" if not rejected else "rejected", status=status,
                               attempt=attempt, items=len(payload), item_ids=ids,
                               accepted=len(body.get("accepted") or []), rejected=len(rejected),
                               elapsed_seconds=round(time.time() - queued_at, 3))
                    break
                except urllib.error.HTTPError as exc:
                    self._emit(outcome="rejected", status=exc.code, attempt=attempt,
                               items=len(payload), item_ids=ids, error_type="HTTPError",
                               elapsed_seconds=round(time.time() - started, 3))
                    if exc.code < 500:
                        break
                except Exception as exc:  # network down / Elite service not running
                    self._emit(outcome="unknown", attempt=attempt, items=len(payload), item_ids=ids,
                               error_type=type(exc).__name__,
                               elapsed_seconds=round(time.time() - started, 3))
                if attempt < ATTEMPTS:
                    time.sleep(0.5 * attempt)
