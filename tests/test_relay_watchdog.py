"""The watchdog that reports browser-side silence.

Eighteen hours of no relays looked exactly like "nothing matched your filter".
These pin the behaviour that makes the two distinguishable.
"""

import pytest

from relay_watchdog import DEFAULT_STALE_HOURS, RelayWatchdog


class FakeStatus:
    def __init__(self, age_seconds=None, uptime_seconds=0, relayed=True):
        self.age_seconds = age_seconds
        self.uptime_seconds = uptime_seconds
        self.relayed = relayed

    def snapshot(self):
        record = {"at": "2026-09-20T00:00:00+00:00", "age_seconds": self.age_seconds}
        return {
            "uptime_seconds": self.uptime_seconds,
            "last_successful_relay": record if self.relayed else None,
        }


class FakeNotifier:
    def __init__(self, delivered=True, raises=False):
        self.delivered = delivered
        self.raises = raises
        self.sent = []

    def send(self, item, priority="normal"):
        if self.raises:
            raise RuntimeError("provider exploded")
        self.sent.append((item.item_id, item.title, priority))
        return self.delivered


def watchdog(status, notifier, hours=6.0):
    return RelayWatchdog(status, notifier, stale_seconds=hours * 3600)


def test_a_fresh_relay_sends_nothing():
    notifier = FakeNotifier()
    assert watchdog(FakeStatus(age_seconds=60), notifier).check() is False
    assert notifier.sent == []


def test_a_stale_relay_alerts_once_per_episode():
    notifier = FakeNotifier()
    status = FakeStatus(age_seconds=7 * 3600)
    dog = watchdog(status, notifier)

    assert dog.check() is True
    # Repeated checks during the same outage must not alert again; a tab left
    # closed overnight would otherwise alert every five minutes.
    assert dog.check() is False
    assert dog.check() is False
    assert len(notifier.sent) == 1

    item_id, title, priority = notifier.sent[0]
    assert item_id == "relay-stale"
    assert "7.0h" in title
    assert "reviewer tab" in title
    assert priority == "high"


def test_a_relay_rearms_the_alert():
    notifier = FakeNotifier()
    status = FakeStatus(age_seconds=7 * 3600)
    dog = watchdog(status, notifier)
    assert dog.check() is True

    status.age_seconds = 30          # the browser came back
    assert dog.check() is False
    status.age_seconds = 9 * 3600    # and went away again
    assert dog.check() is True
    assert len(notifier.sent) == 2


def test_an_install_that_never_relayed_is_measured_from_startup():
    """Otherwise a setup that never worked at all stays silent forever."""
    notifier = FakeNotifier()
    status = FakeStatus(relayed=False, uptime_seconds=8 * 3600)
    assert watchdog(status, notifier).check() is True

    quiet = FakeNotifier()
    young = FakeStatus(relayed=False, uptime_seconds=60)
    assert watchdog(young, quiet).check() is False


def test_a_provider_failure_does_not_kill_the_watchdog():
    dog = watchdog(FakeStatus(age_seconds=7 * 3600), FakeNotifier(raises=True))
    assert dog.check() is False
    # The episode is still marked, so it will not retry in a tight loop.
    assert dog.check() is False


def test_undeliverable_alert_is_reported_as_not_sent():
    dog = watchdog(FakeStatus(age_seconds=7 * 3600), FakeNotifier(delivered=False))
    assert dog.check() is False


@pytest.mark.parametrize("value,expected_hours", [
    ("2", 2.0), ("0.5", 0.5), ("", DEFAULT_STALE_HOURS), ("junk", DEFAULT_STALE_HOURS),
    ("0", DEFAULT_STALE_HOURS), ("-3", DEFAULT_STALE_HOURS), (None, DEFAULT_STALE_HOURS),
])
def test_threshold_comes_from_the_environment_with_a_sane_fallback(value, expected_hours):
    env = {} if value is None else {"STALE_RELAY_HOURS": value}
    dog = RelayWatchdog.from_env(FakeStatus(age_seconds=0), FakeNotifier(), env)
    assert dog.stale_seconds == pytest.approx(expected_hours * 3600)


def test_missing_age_is_not_treated_as_stale():
    notifier = FakeNotifier()
    assert watchdog(FakeStatus(age_seconds=None), notifier).check() is False
    assert notifier.sent == []
