"""Strict, editable scan settings with bounded reads and atomic saves.

No file is seeded automatically: a missing file lets an existing extension keep
its saved Chrome settings. Reads consult disk every time so a manual JSON edit
applies without restarting the notifier. Invalid edits are reported to callers,
which can retain their last usable configuration.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from copy import deepcopy
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "relay-settings.json"
MAX_FILE_BYTES = 16 * 1024
INVALID_SETTINGS = "Invalid relay settings"
_KEYS = frozenset({"schemaVersion", "refreshMinutes", "pageDelaySeconds", "sweepMode"})
_MODES = frozenset({"none", "sweep", "high-value"})


def validate_settings(value: object) -> dict:
    """Return an independent schema-v1 object or a fixed, payload-free error."""
    if type(value) is not dict or value.keys() != _KEYS:
        raise ValueError(INVALID_SETTINGS)
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise ValueError(INVALID_SETTINGS)
    for field, minimum, maximum in (("refreshMinutes", 0, 120), ("pageDelaySeconds", 5, 60)):
        number = value[field]
        if type(number) is not int or not minimum <= number <= maximum:
            raise ValueError(INVALID_SETTINGS)
    mode = value["sweepMode"]
    if type(mode) is not str or len(mode) > 10 or mode not in _MODES:
        raise ValueError(INVALID_SETTINGS)
    return {
        "schemaVersion": 1,
        "refreshMinutes": value["refreshMinutes"],
        "pageDelaySeconds": value["pageDelaySeconds"],
        "sweepMode": mode,
    }


def _unique_object(pairs: list[tuple]) -> dict:
    value = {}
    for key, field in pairs:
        if key in value:
            raise ValueError(INVALID_SETTINGS)
        value[key] = field
    return value


class RelaySettingsStore:
    """Thread-safe settings store; empty paths explicitly select memory only."""

    def __init__(self, path: Path | str | None = None):
        if path in ("", ":memory:"):
            self.path = None
        else:
            self.path = Path(path) if path is not None else DEFAULT_PATH
        self._lock = threading.RLock()
        self._settings: dict | None = None

    def read(self) -> dict | None:
        """Read current JSON; missing is absent, invalid raises, I/O propagates."""
        with self._lock:
            if self.path is None:
                return deepcopy(self._settings)
            try:
                with self.path.open("rb") as handle:
                    encoded = handle.read(MAX_FILE_BYTES + 1)
            except FileNotFoundError:
                return None
            if len(encoded) > MAX_FILE_BYTES:
                raise ValueError(INVALID_SETTINGS)
            try:
                parsed = json.loads(encoded.decode("utf-8-sig"), object_pairs_hook=_unique_object)
                clean = validate_settings(parsed)
            except (ValueError, UnicodeError, RecursionError):
                # JSON decoder messages can contain user input; expose none of
                # the original message, payload or exception chain.
                raise ValueError(INVALID_SETTINGS) from None
            self._settings = clean
            return deepcopy(clean)

    def save(self, value: object) -> dict:
        """Validate, persist atomically, then commit and return an isolated copy."""
        clean = validate_settings(value)
        encoded = (json.dumps(clean, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise ValueError(INVALID_SETTINGS)
        with self._lock:
            if self.path is not None:
                self._write(encoded)
            self._settings = clean
            return deepcopy(clean)

    def _write(self, encoded: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp",
                mode="wb", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    log.warning("Relay settings temporary file cleanup unavailable")
