"""Operational history ages out gradually and stays within its disk budget."""

import gzip
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from discord_bridge.rolling_logs import RollingLog, RollingLogHandler

DAY = 86400
NOW = 1_792_700_000.0


def contents(path):
    if path.suffix == ".gz":
        return gzip.decompress(path.read_bytes())
    return path.read_bytes()


def log_files(directory):
    return [path for path in directory.iterdir() if not path.name.startswith(".")]


def test_history_survives_restart_until_sixty_days_and_unrelated_files_are_safe(tmp_path):
    now = [NOW]
    path = tmp_path / "events.log"
    unrelated = tmp_path / "notes.gz"
    unrelated.write_bytes(b"keep")
    writer = RollingLog(path, clock=lambda: now[0])
    writer.append(b"first day\n")
    now[0] += DAY
    writer.append(b"second day\n")
    archive = next(tmp_path.glob("events.log.*.gz"))
    assert contents(archive) == b"first day\n"
    now[0] = NOW + 59 * DAY
    RollingLog(path, clock=lambda: now[0]).append(b"day 59\n")
    assert archive.exists()
    now[0] = NOW + 61 * DAY
    RollingLog(path, clock=lambda: now[0]).append(b"day 61\n")
    assert not archive.exists()
    assert path.read_bytes() == b"day 61\n"
    assert unrelated.read_bytes() == b"keep"


def test_budget_trims_oldest_archives_before_newer_history(tmp_path):
    now = [NOW]
    writer = RollingLog(
        tmp_path / "events.log", max_bytes=512, total_bytes=800, clock=lambda: now[0]
    )
    payloads = [os.urandom(400) for _ in range(4)]
    for payload in payloads:
        writer.append(payload)
        now[0] += 1
    paths = log_files(tmp_path)
    assert sum(path.stat().st_size for path in paths) <= 800
    assert writer.path.read_bytes() == payloads[-1]
    assert all(payloads[0] != contents(path) for path in paths)


def test_busy_day_rotates_without_losing_complete_lines(tmp_path):
    writer = RollingLog(tmp_path / "events.log", max_bytes=512, total_bytes=16000)
    expected = [f"{index:03d} {'x' * 90}\n".encode() for index in range(20)]
    for line in expected:
        writer.append(line)
    paths = log_files(tmp_path)
    actual = [line for path in paths for line in contents(path).splitlines(keepends=True)]
    assert sorted(actual) == sorted(expected)
    assert all(path.stat().st_size <= 512 for path in paths)


def test_old_numbered_logs_obey_age_and_space_limits_without_touching_state(tmp_path):
    path = tmp_path / "events.log"
    old = tmp_path / "events.log.1"
    old.write_bytes(b"old history\n")
    os.utime(old, (NOW - 61 * DAY, NOW - 61 * DAY))
    recent = tmp_path / "events.log.2"
    recent.write_bytes(b"recent history\n")
    os.utime(recent, (NOW - DAY, NOW - DAY))
    state = tmp_path / "events.log.sqlite3"
    state.write_bytes(b"durable state")
    RollingLog(path, clock=lambda: NOW).append(b"current\n")
    assert not old.exists()
    assert recent.read_bytes() == b"recent history\n"
    assert state.read_bytes() == b"durable state"


def test_failed_compression_keeps_original_log_and_removes_temporary_file(tmp_path, monkeypatch):
    writer = RollingLog(tmp_path / "events.log", max_bytes=512)
    writer.append(b"x" * 500)

    def unavailable(*args, **kwargs):
        raise OSError("unavailable")

    monkeypatch.setattr(gzip, "GzipFile", unavailable)
    with pytest.raises(OSError):
        writer.append(b"new record\n" * 2)
    assert writer.path.read_bytes() == b"x" * 500
    assert log_files(tmp_path) == [writer.path]


def test_locked_original_does_not_accumulate_duplicate_archives(tmp_path, monkeypatch):
    writer = RollingLog(tmp_path / "events.log", max_bytes=512)
    writer.append(b"x" * 500)
    unlink = Path.unlink

    def locked(path, *args, **kwargs):
        if path == writer.path:
            raise PermissionError("original is locked")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    for _ in range(3):
        with pytest.raises(PermissionError):
            writer.append(b"new record\n" * 2)
    assert writer.path.read_bytes() == b"x" * 500
    assert log_files(tmp_path) == [writer.path]


def test_multiple_writers_in_one_process_keep_records_intact(tmp_path):
    path = tmp_path / "events.log"
    writers = [RollingLog(path, max_bytes=512, total_bytes=16000) for _ in range(2)]
    lines = [f"{index:03d} {'x' * 90}\n".encode() for index in range(60)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda entry: writers[entry[0] % 2].append(entry[1]), enumerate(lines)))
    actual = [line for file in log_files(tmp_path) for line in contents(file).splitlines(True)]
    assert sorted(actual) == sorted(lines)


def test_other_process_cannot_rotate_or_append_while_writer_holds_lock(tmp_path):
    writer = RollingLog(tmp_path / "events.log")
    writer.append(b"original\n")
    program = """
import sys
from discord_bridge.rolling_logs import RollingLog
try:
    RollingLog(sys.argv[1]).append(b'child\\n')
except OSError:
    sys.exit(7)
"""
    with writer._process_lock():
        result = subprocess.run(
            [sys.executable, "-c", program, str(writer.path)],
            capture_output=True, timeout=10,
        )
    assert result.returncode == 7, result.stderr.decode()
    assert writer.path.read_bytes() == b"original\n"
    writer.append(b"parent\n")
    assert writer.path.read_bytes() == b"original\nparent\n"


def test_logging_handler_writes_formatted_records_and_survives_disk_failure(tmp_path):
    handler = RollingLogHandler(tmp_path / "service.log")
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    record = logging.LogRecord("test", logging.INFO, "", 0, "healthy", (), None)
    handler.emit(record)
    assert handler.writer.path.read_text() == "INFO healthy\n"
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")
    broken = RollingLogHandler(blocker / "service.log")
    errors = []
    broken.handleError = lambda record: errors.append(record)
    broken.emit(record)
    assert errors == [record]


def test_server_archives_backend_logs_without_duplicating_delivery_events(tmp_path, monkeypatch):
    import server

    monkeypatch.setattr(sys, "argv", ["server.py", "--state", str(tmp_path / "seen.json")])

    def serve(*args):
        logging.getLogger("notifier").warning("backend healthy")
        logging.getLogger("discord_bridge.monitoring").warning("delivery event")

    monkeypatch.setattr(server, "serve", serve)
    before = list(logging.getLogger().handlers)
    server.main()
    output = (tmp_path / "notifier.log").read_text()
    assert "backend healthy" in output
    assert "delivery event" not in output
    assert logging.getLogger().handlers == before
