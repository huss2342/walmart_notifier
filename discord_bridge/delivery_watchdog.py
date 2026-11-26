"""Owner alarm when due channel alerts stop going out; revives the sender thread.

On 2026-11-25 channel delivery stopped for ~80 minutes and nothing said so.
This watchdog runs on its own thread, reads two cheap indexed aggregates and,
when due channel jobs have waited at least ``stall_seconds`` while no channel
message went out for as long, emits ``delivery_stalled`` (repeated every
``repeat_seconds``) and asks ``notify`` once per episode to tell the owner
(the private collector-health channel; never a member DM or mention). It emits
``delivery_recovered`` when sending resumes or no due work is left.

It never sends, claims, defers or expires alerts itself, and every callback
failure is contained: the watchdog is monitoring, never part of delivery.
"""

from __future__ import annotations

import math
import threading
import time

from .monitoring import emit_event, error_fields

STALL_SECONDS = 300
CHECK_SECONDS = 30
REPEAT_SECONDS = 900


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class DeliveryWatchdog:
    def __init__(
        self,
        store,
        *,
        event_sink=None,
        stall_seconds=STALL_SECONDS,
        interval=CHECK_SECONDS,
        repeat_seconds=REPEAT_SECONDS,
        clock=None,
        notify=None,
        revive=None,
        heartbeat=None,
        stop_event=None,
    ):
        if not (
            _finite(stall_seconds) and stall_seconds > 0
            and _finite(interval) and interval > 0
            and _finite(repeat_seconds) and repeat_seconds > 0
        ):
            raise ValueError("invalid watchdog bounds")
        self.store, self.event_sink = store, event_sink
        self.stall_seconds, self.interval = float(stall_seconds), float(interval)
        self.repeat_seconds = float(repeat_seconds)
        self.clock = clock or time.time
        self.notify, self.revive, self.heartbeat = notify, revive, heartbeat
        self._stop = stop_event if stop_event is not None else threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._stalled_since = None
        self._last_alarm_at = None
        self._notified = False
        self._status = {
            "enabled": True,
            "stall_seconds": self.stall_seconds,
            "check_interval_seconds": self.interval,
            "stalled": False,
            "stalled_since": None,
            "stalled_seconds": 0,
            "alarms": 0,
            "owner_notified": False,
            "last_check_at": None,
            "last_sent_at": None,
            "last_sent_age_seconds": None,
            "due_items": 0,
            "oldest_due_age_seconds": 0,
            "delivery_restarts": 0,
            "last_error": None,
        }

    def _emit(self, event, **fields):
        emit_event(self.event_sink, event, component="channel", **fields)

    def check(self, now=None):
        """One evaluation; returns the status snapshot. Never raises."""
        now = self.clock() if now is None else now
        try:
            snapshot = self.store.stall_snapshot(now=now)
            due_items = int(snapshot.get("due_items") or 0)
            oldest_due = snapshot.get("oldest_due_at")
            last_sent = snapshot.get("last_sent_at")
        except Exception as exc:
            with self._lock:
                self._status.update(last_check_at=now, last_error=type(exc).__name__)
            self._emit("worker_error", phase="watchdog", **error_fields(exc))
            return self.status()
        due_age = max(0.0, now - oldest_due) if due_items and _finite(oldest_due) else 0.0
        sent_age = max(0.0, now - last_sent) if _finite(last_sent) else None
        stalled = (
            due_items > 0
            and due_age >= self.stall_seconds
            and (sent_age is None or sent_age >= self.stall_seconds)
        )
        alarm = notify = recovered = False
        with self._lock:
            if stalled:
                if self._stalled_since is None:
                    # The stall began when the oldest due job became overdue
                    # or the last send, whichever is later.
                    started = now - min(due_age, sent_age if sent_age is not None else due_age)
                    self._stalled_since = started
                    self._notified = False
                    alarm = True
                elif (
                    self._last_alarm_at is None
                    or now - self._last_alarm_at >= self.repeat_seconds
                ):
                    alarm = True
                if alarm:
                    self._last_alarm_at = now
                    self._status["alarms"] += 1
                notify = not self._notified
            elif self._stalled_since is not None:
                recovered = True
                stalled_for = max(0.0, now - self._stalled_since)
                self._stalled_since = None
                self._last_alarm_at = None
                self._notified = False
            stalled_seconds = max(0.0, now - self._stalled_since) if self._stalled_since else 0
            self._status.update(
                stalled=stalled,
                stalled_since=self._stalled_since,
                stalled_seconds=round(stalled_seconds, 3),
                last_check_at=now,
                last_sent_at=last_sent if _finite(last_sent) else None,
                last_sent_age_seconds=round(sent_age, 3) if sent_age is not None else None,
                due_items=due_items,
                oldest_due_age_seconds=round(due_age, 3),
                last_error=None,
            )
        fields = {
            "due_items": due_items,
            "oldest_due_age_seconds": round(due_age, 3),
            **({"last_sent_age_seconds": round(sent_age, 3)} if sent_age is not None else {}),
        }
        if alarm:
            self._emit("delivery_stalled", stalled_seconds=round(stalled_seconds, 3), **fields)
        if recovered:
            self._emit(
                "delivery_recovered",
                stalled_seconds=round(stalled_for, 3),
                resumed=sent_age is not None and sent_age < self.stall_seconds,
                **fields,
            )
        if notify and self.notify is not None:
            delivered = False
            try:
                delivered = bool(self.notify(due_items=due_items, oldest_due_age_seconds=due_age,
                                             last_sent_age_seconds=sent_age))
            except Exception as exc:
                self._emit("worker_error", phase="watchdog", **error_fields(exc))
            with self._lock:
                # A failed notice is retried on the next check of this episode.
                if delivered and self._stalled_since is not None:
                    self._notified = True
                self._status["owner_notified"] = self._notified
        elif recovered:
            with self._lock:
                self._status["owner_notified"] = False
        return self.status()

    def tick(self):
        """One watchdog pass: revive the sender, heartbeat, then check."""
        if self.revive is not None:
            try:
                if self.revive():
                    with self._lock:
                        self._status["delivery_restarts"] += 1
            except Exception as exc:
                self._emit("worker_error", phase="watchdog", **error_fields(exc))
        if self.heartbeat is not None:
            try:
                self.heartbeat()
            except Exception as exc:
                self._emit("worker_error", phase="queue_summary", **error_fields(exc))
        return self.check()

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return

        def run():
            while not self._stop.wait(self.interval):
                try:
                    self.tick()
                except Exception as exc:  # never let monitoring die quietly
                    self._emit("worker_error", phase="watchdog", **error_fields(exc))

        # Not named discord-delivery*: that prefix marks priority channel sends.
        self._thread = threading.Thread(target=run, name="delivery-watchdog", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())

    def status(self):
        with self._lock:
            return {**self._status, "running": self.is_alive()}
