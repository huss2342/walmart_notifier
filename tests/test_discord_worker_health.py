"""Channel-worker health reflects actual polling, separate from configured sending."""

import json
from types import SimpleNamespace

from discord_bridge.core import JobStore

from notifiers import discord


def run_cycles(notifier, monkeypatch, outcomes, *, on_sent=None):
    """Drive the actual worker loop without sleeping or contacting Discord."""
    clock = SimpleNamespace(wall=100.0, elapsed=10.0)
    monkeypatch.setattr(
        discord,
        "time",
        SimpleNamespace(time=lambda: clock.wall, monotonic=lambda: clock.elapsed),
    )
    snapshots = []
    calls = iter(outcomes)

    def deliver():
        outcome = next(calls)
        if callable(outcome):
            outcome = outcome(clock)
        clock.wall += 0.5
        clock.elapsed += 0.5
        if isinstance(outcome, Exception):
            raise outcome
        return {"sent": outcome, "blocked": False, "wait_seconds": 5}

    class Thread:
        def __init__(self, *, target, **_kwargs):
            self.target = target
            self.running = False

        def start(self):
            self.running = True
            try:
                self.target()
            finally:
                self.running = False

        def is_alive(self):
            return self.running

        def join(self, timeout=None):
            assert not self.running

    class Wake:
        def clear(self):
            pass

        def wait(self, _seconds):
            snapshots.append(notifier.health()["worker"])
            clock.wall += 0.5
            clock.elapsed += 0.5
            if len(snapshots) == len(outcomes):
                notifier._stop.set()

        def set(self):
            pass

    monkeypatch.setattr(discord.threading, "Thread", Thread)
    monkeypatch.setattr(notifier, "deliver_once", deliver)
    notifier._wake = Wake()
    notifier.start(on_sent=on_sent)
    return snapshots


def test_disabled_worker_is_not_reported_as_running_or_polled(tmp_path):
    notifier = discord.DiscordNotifier(JobStore(tmp_path / "q"))
    worker = notifier.health()["worker"]
    assert worker["enabled"] is worker["running"] is False
    assert worker["last_poll_at"] is worker["last_poll_status"] is None
    assert worker["last_success"] is worker["last_failure_at"] is None
    assert worker["last_error"] is worker["recovered_at"] is None
    assert worker["in_progress"] is False
    assert worker["in_progress_seconds"] == 0


def test_live_idle_worker_has_recent_polls_without_claiming_a_success(tmp_path, monkeypatch):
    notifier = discord.DiscordNotifier(JobStore(tmp_path / "q"), live=True)
    snapshots = run_cycles(notifier, monkeypatch, [0, 0])
    for index, worker in enumerate(snapshots):
        assert worker["enabled"] is worker["running"] is True
        assert worker["last_poll_at"] == 100.5 + index
        assert worker["last_poll_status"] == "ok"
        assert worker["last_success"] is None
        assert worker["last_error"] is None
        assert worker["in_progress"] is False
    notifier.stop()
    assert notifier.health()["worker"]["running"] is False
    assert notifier.health()["worker"]["last_poll_at"] == 101.5


def test_worker_retains_confirmed_send_then_reports_failure_and_idle_recovery(
    tmp_path, monkeypatch
):
    notifier = discord.DiscordNotifier(JobStore(tmp_path / "q"), live=True)
    snapshots = run_cycles(notifier, monkeypatch, [1, RuntimeError("PRIVATE secret"), 0])
    sent, failed, recovered = snapshots
    assert sent["last_success"] == 100.5
    assert failed["last_success"] == 100.5
    assert failed["last_poll_at"] == failed["last_failure_at"] == 101.5
    assert failed["last_poll_status"] == "error"
    assert failed["last_error"] == "RuntimeError"
    assert failed["in_progress"] is False
    assert recovered["last_poll_status"] == "ok"
    assert recovered["last_error"] is None
    assert recovered["last_success"] == 100.5  # Idle recovery is not a delivery.
    assert recovered["last_failure_at"] == 101.5
    assert recovered["recovered_at"] == recovered["last_poll_at"] == 102.5
    assert "PRIVATE" not in json.dumps(snapshots)


def test_in_progress_poll_exposes_elapsed_work_without_inventing_a_stale_cutoff(
    tmp_path, monkeypatch
):
    notifier = discord.DiscordNotifier(JobStore(tmp_path / "q"), live=True)
    observed = []

    def slow_cycle(clock):
        clock.wall += 240
        clock.elapsed += 240
        assert notifier._lock.acquire(blocking=False)
        notifier._lock.release()  # No worker lock may surround delivery I/O.
        observed.append(notifier.health()["worker"])
        return 0

    run_cycles(notifier, monkeypatch, [slow_cycle])
    worker = observed[0]
    assert worker["running"] is worker["in_progress"] is True
    assert worker["poll_started_at"] == 100
    assert worker["in_progress_seconds"] == 240
    assert worker["last_poll_at"] is worker["last_poll_status"] is None
    assert worker["last_success"] is None


def test_callback_error_does_not_erase_the_confirmed_send(tmp_path, monkeypatch):
    notifier = discord.DiscordNotifier(JobStore(tmp_path / "q"), live=True)

    def broken_callback():
        raise ValueError("PRIVATE callback")

    (worker,) = run_cycles(notifier, monkeypatch, [2], on_sent=broken_callback)
    assert worker["last_success"] == worker["last_failure_at"] == 100.5
    assert worker["last_poll_status"] == "error"
    assert worker["last_error"] == "ValueError"
    assert "PRIVATE" not in json.dumps(worker)
