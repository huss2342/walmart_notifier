"""Alert when the browser stops feeding the notifier.

Every failure so far has been on the browser side -- a closed reviewer tab, a
tab navigated elsewhere, a bot check, a sweep that ended on a transient empty
page -- and all of them look identical from here: the ingest endpoint simply
goes quiet. Silence is indistinguishable from "nothing matched your filter",
so it went unnoticed for eighteen hours.

This watches the age of the last successful relay and sends one alert when it
crosses a threshold. One per episode: it re-arms only after a relay arrives,
so a tab left closed overnight does not produce an alert per check.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from models import Item

log = logging.getLogger(__name__)

DEFAULT_STALE_HOURS = 6.0
DEFAULT_CHECK_SECONDS = 300.0


def _hours(value: object, fallback: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


class RelayWatchdog:
    """Polls relay age and alerts once per stale episode."""

    def __init__(
        self,
        runtime_status,
        notifier,
        stale_seconds: float = DEFAULT_STALE_HOURS * 3600,
        check_seconds: float = DEFAULT_CHECK_SECONDS,
        now: Callable[[], float] | None = None,
    ):
        self.runtime_status = runtime_status
        self.notifier = notifier
        self.stale_seconds = stale_seconds
        self.check_seconds = check_seconds
        self._now = now
        self._alerted = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_env(cls, runtime_status, notifier, env: dict[str, str]) -> RelayWatchdog:
        hours = _hours(env.get("STALE_RELAY_HOURS"), DEFAULT_STALE_HOURS)
        return cls(runtime_status, notifier, stale_seconds=hours * 3600)

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="relay-watchdog", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(self.check_seconds):
            try:
                self.check()
            except Exception:
                # A watchdog that dies on an unexpected error is worse than no
                # watchdog, because its silence looks like everything is fine.
                log.exception("Relay watchdog check failed.")

    # --- the check ----------------------------------------------------------

    def relay_age_seconds(self) -> float | None:
        """Age of the last successful relay, or None if there has never been one."""
        snapshot = self.runtime_status.snapshot()
        record = snapshot.get("last_successful_relay")
        if not isinstance(record, dict):
            # Never relayed. Measure from process start instead, so a install
            # that never worked at all is reported too.
            uptime = snapshot.get("uptime_seconds")
            return float(uptime) if isinstance(uptime, (int, float)) else None
        age = record.get("age_seconds")
        return float(age) if isinstance(age, (int, float)) else None

    def check(self) -> bool:
        """Send an alert if newly stale. Returns whether one was sent."""
        age = self.relay_age_seconds()
        if age is None:
            return False

        if age < self.stale_seconds:
            # A relay arrived: re-arm so the next episode alerts again.
            self._alerted = False
            return False
        if self._alerted:
            return False

        self._alerted = True
        return self._alert(age)

    def _alert(self, age_seconds: float) -> bool:
        hours = age_seconds / 3600
        item = Item(
            item_id="relay-stale",
            title=(
                f"Reviewer relay silent for {hours:.1f}h. The notifier is running, "
                "so check Chrome: is the reviewer tab still open on "
                "walmart.com/reviews/claim-product, and is there a bot check to solve?"
            ),
            url="https://www.walmart.com/reviews/claim-product",
            source="diagnostic",
        )
        try:
            delivered = self.notifier.send(item, priority="high")
        except Exception:
            log.exception("Could not send the stale-relay alert.")
            return False
        log.warning("No successful reviewer relay for %.1f hours.", hours)
        return bool(delivered)
