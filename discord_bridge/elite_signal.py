"""Forward requested new/restocked in-stock items to the owner's Reviewer Alerts Elite service.

Optional and off unless ELITE_SIGNAL_URL and ELITE_SIGNAL_KEY are set. Delivery runs on
its own thread so Discord intake/delivery latency is unaffected; every attempt is logged
through the bridge monitor as an `elite_signal` event.

The Elite service publishes what its members want (`/v2/signals/watch`): only items that
match one of those watches are forwarded, and the watches' searches join the scanner's
request lane so Elite requests get dedicated Walmart searches like Pro requests. Until the
first watch loads (or if it goes stale) every candidate is forwarded; Elite matches again.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
import unicodedata

SIGNAL_TRANSITIONS = {"queued", "rearmed", "restocked"}
SIGNAL_ROUTE = "pro-all"  # Every available item has this route; FREE-only items are out of stock.
MAX_QUEUE = 1000
TIMEOUT_SECONDS = 3
ATTEMPTS = 3
WATCH_REFRESH_SECONDS = 15
WATCH_STALE_SECONDS = 120


def _normalized(value: str) -> str:
    # Same as reviewer_alerts_elite/elite_service/rules.py so both sides agree on a match.
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))


def _contains(title: str, phrase: str) -> bool:
    return f" {_normalized(phrase)} " in f" {_normalized(title)} "


def watch_matches(watch: dict, item: dict, category_map: dict) -> bool:
    """Mirror of Elite's rules.matches for one published watch."""
    value, title = item.get("value_cents"), item.get("title") or ""
    if type(value) is not int or value < watch["minValueCents"] or not title.strip():
        return False
    if any(_contains(title, word) for word in watch["excludeKeywords"]):
        return False
    words = watch["keywords"]
    hits = [_contains(title, word) for word in words]
    word_match = bool(words) and (all(hits) if watch["keywordMode"] == "all" else any(hits))
    return category_map.get(item.get("category", "other"), "other") in watch["categoryIds"] or word_match


class EliteSignal:
    def __init__(self, url: str, key: str, event_sink=None, *, opener=None, watch=False):
        if not url.startswith(("http://", "https://")):
            raise ValueError("ELITE_SIGNAL_URL must be an http(s) URL")
        if not 32 <= len(key) <= 256 or any(c.isspace() for c in key):
            raise ValueError("ELITE_SIGNAL_KEY must be 32-256 non-space characters")
        self.url, self.key, self.event_sink = url, key, event_sink
        self.opener = opener or urllib.request.urlopen
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

    def wanted(self, item: dict) -> bool:
        """True if an Elite request could match; with no current watch, forward everything."""
        watch = self._fresh_watch()
        if watch is None:
            return True
        return any(watch_matches(w, item, watch["categoryMap"]) for w in watch["watches"])

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
            if item.get("value_cents") is None or not self.wanted(item):
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
        try:
            self.queue.put_nowait((time.time(), payload))
        except queue.Full:
            self._emit(outcome="rejected", error_type="QueueFull", items=len(payload),
                       item_ids=["ip-" + p["itemId"] for p in payload])
            return 0
        return len(payload)

    def _post(self, payload):
        body = json.dumps({"items": payload}).encode()
        request = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        with self.opener(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, json.loads(response.read() or b"{}")

    def _run(self):
        while True:
            queued_at, payload = self.queue.get()
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
