"""Dedupe store so a given item only ever alerts once.

A JSON file on disk. The whole point of this project is that it runs on a
machine that is already on all the time, so there is nothing to gain from a
hosted database -- and a file you can open in a text editor is much easier to
inspect and reset than a cloud table.

Writes go through a temp file and an atomic replace, so killing the process
mid-write leaves the previous good file rather than a truncated one.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).parent.parent / "data" / "seen.json"

# Keep the file from growing without bound. 971 items are visible in the
# portal today across 25 pages; this leaves generous headroom for churn.
MAX_ENTRIES = 20_000


def default_path() -> Path:
    return Path(os.environ.get("STATE_PATH", DEFAULT_PATH)).expanduser()


class SeenStore:
    def __init__(self, path: Path | str | None = None, autosave: bool = True):
        # `path=""` (or ":memory:") keeps everything in RAM, which is what the
        # tests use.
        self.path: Path | None
        if path == "" or path == ":memory:":
            self.path = None
        else:
            self.path = Path(path) if path is not None else default_path()

        self.autosave = autosave
        self._lock = threading.RLock()
        self._seen: dict[str, dict] = {}
        self._markers: dict[str, str] = {}
        self._load()

    # --- persistence --------------------------------------------------------

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read %s; starting with an empty store.", self.path)
            return
        if not isinstance(data, dict):
            log.error("%s is not a JSON object; starting with an empty store.", self.path)
            return
        seen = data.get("seen")
        markers = data.get("markers")
        self._seen = seen if isinstance(seen, dict) else {}
        self._markers = markers if isinstance(markers, dict) else {}
        log.info("Loaded %d seen items from %s", len(self._seen), self.path)

    def save(self) -> None:
        if self.path is None:
            return
        with self._lock:
            self._trim()
            payload = json.dumps(
                {"seen": self._seen, "markers": self._markers},
                indent=1, sort_keys=True,
            )
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Atomic replace: a crash mid-write must not destroy the file
                # that stops the next run re-alerting on everything.
                tmp = self.path.with_suffix(self.path.suffix + ".tmp")
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(self.path)
            except OSError:
                log.exception("Could not write %s; dedupe will not survive a restart.", self.path)

    def _trim(self) -> None:
        if len(self._seen) <= MAX_ENTRIES:
            return
        # Oldest first by seen_at; entries without one are treated as oldest.
        ordered = sorted(self._seen.items(), key=lambda kv: kv[1].get("seen_at", ""))
        for item_id, _ in ordered[: len(self._seen) - MAX_ENTRIES]:
            del self._seen[item_id]

    def _touch(self) -> None:
        if self.autosave:
            self.save()

    # --- dedupe -------------------------------------------------------------

    def is_new(self, item_id: str) -> bool:
        with self._lock:
            return item_id not in self._seen

    def claim(self, item_id: str, title: str = "", value: float | None = None) -> bool:
        """Atomically take ownership of an item. True only for the first caller.

        Two browser tabs can relay the same item at the same moment, and the
        server handles each request on its own thread, so the check and the
        write have to happen under one lock.
        """
        with self._lock:
            if item_id in self._seen:
                return False
            self._record(item_id, title, value)
            self._touch()
            return True

    def release(self, item_id: str) -> None:
        """Undo a claim so a later run retries. Used when delivery fails."""
        with self._lock:
            if self._seen.pop(item_id, None) is not None:
                self._touch()

    def mark_seen(self, item_id: str, title: str = "", value: float | None = None) -> None:
        with self._lock:
            self._record(item_id, title, value)
            self._touch()

    def _record(self, item_id: str, title: str, value: float | None = None) -> None:
        # The value is recorded purely so "has a $50 item ever actually been
        # relayed?" is answerable. Without it, an item that never arrives and
        # an item that arrived and was filtered look identical after the fact.
        self._seen[item_id] = {
            "title": title[:300],
            "value_usd": value,
            "seen_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    # --- markers ------------------------------------------------------------

    def get_marker(self, key: str) -> str | None:
        with self._lock:
            return self._markers.get(key)

    def set_marker(self, key: str, value: str) -> None:
        with self._lock:
            self._markers[key] = value
            self._touch()

    # --- introspection ------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)
