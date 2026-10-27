"""Compressed operational history with an age target and a firm space backstop."""

from __future__ import annotations

import gzip
import logging
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
RETENTION_DAYS = 60


class RollingLog:
    """Threads and processes serialize append/rotation for each log path.

    Rotate daily or at max_bytes, then gzip complete segments. Reserve room for
    a full active segment when pruning so appends cannot exceed total_bytes.
    Old numbered segments remain readable and subject to the same cleanup.
    """

    def __init__(
        self, path, *, max_bytes=5 * 1024 * 1024, total_bytes=32 * 1024 * 1024,
        retention_days=RETENTION_DAYS, backups=256, clock=None,
    ):
        if max_bytes < 1 or total_bytes < max_bytes or retention_days < 1 or backups < 1:
            raise ValueError("invalid log retention bounds")
        self.path = Path(path)
        self.max_bytes, self.total_bytes = max_bytes, total_bytes
        self.retention_days, self.backups = retention_days, backups
        self.clock = clock or time.time
        self._next_cleanup = 0
        self._archive_pattern = re.compile(
            re.escape(self.path.name) + r"\.(?:[1-9][0-9]*|\d{8}T\d{12}-[a-f0-9]{8}\.gz)"
        )
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.path.resolve()), threading.RLock())

    @contextmanager
    def _process_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name("." + self.path.name + ".lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "r+b", buffering=0) as handle:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Closing the handle also releases the OS lock. Contention raises
            # OSError instead of blocking delivery while another process rotates.
            yield

    def _rotate(self, modified):
        stamp = datetime.fromtimestamp(modified, UTC).strftime("%Y%m%dT%H%M%S%f")
        archive = self.path.with_name(f"{self.path.name}.{stamp}-{uuid.uuid4().hex[:8]}.gz")
        temporary = self.path.with_name(self.path.name + ".rotate.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with (
                os.fdopen(descriptor, "wb") as output,
                self.path.open("rb") as source,
                gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as zipped,
            ):
                shutil.copyfileobj(source, zipped)
            os.utime(temporary, (modified, modified))
            temporary.replace(archive)
            # The original is removed only after the compressed copy is complete.
            try:
                self.path.unlink()
            except OSError:
                # A locked original must not create another duplicate archive
                # on every failed attempt and slowly defeat the disk budget.
                archive.unlink(missing_ok=True)
                raise
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)

    def _prune(self, now):
        cutoff = now - self.retention_days * 86400
        archives = []
        for path in self.path.parent.iterdir():
            if self._archive_pattern.fullmatch(path.name) and path.is_file():
                stat = path.stat()
                archives.append((stat.st_mtime, path.name, path, stat.st_size))
        archives.sort()
        total = sum(entry[3] for entry in archives) + self.max_bytes
        remaining = len(archives)
        for modified, _, path, size in archives:
            if modified > cutoff and total <= self.total_bytes and remaining <= self.backups:
                break
            path.unlink()
            total -= size
            remaining -= 1
        self._next_cleanup = now + 3600

    def append(self, encoded: bytes):
        if len(encoded) > self.max_bytes:
            raise ValueError("log record exceeds segment size")
        if not encoded:
            return
        with self._lock, self._process_lock():
            now = self.clock()
            rotated = False
            if self.path.exists():
                stat = self.path.stat()
                if stat.st_size and (
                    stat.st_size + len(encoded) > self.max_bytes
                    or int(stat.st_mtime // 86400) != int(now // 86400)
                ):
                    self._rotate(stat.st_mtime)
                    rotated = True
            if rotated or now >= self._next_cleanup:
                self._prune(now)
            descriptor = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0),
                0o600,
            )
            try:
                remaining = memoryview(encoded)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OSError("log write did not advance")
                    remaining = remaining[written:]
            finally:
                os.close(descriptor)
            os.utime(self.path, (now, now))


class RollingLogHandler(logging.Handler):
    """Use the same retention policy for ordinary Python operational logs."""

    def __init__(self, path, **options):
        super().__init__()
        self.writer = RollingLog(path, **options)

    def emit(self, record):
        try:
            encoded = (self.format(record) + "\n").encode("utf-8", errors="backslashreplace")
            self.writer.append(encoded)
        except Exception:
            self.handleError(record)
