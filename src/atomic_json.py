"""Atomic persistence for already-validated, encoded JSON snapshots.

The owning store keeps its schema, size limits, locks, and error policy. Only
the sibling-file write, sync, replacement, and best-effort cleanup live here.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path


def write_atomic_json(
    path: Path,
    encoded: str | bytes,
    *,
    logger: logging.Logger | None = None,
    cleanup_warning: str = "Temporary JSON snapshot cleanup unavailable",
) -> None:
    """Replace a snapshot after its complete contents have reached disk.

    A unique temporary sibling avoids collisions and keeps replacement on the
    same filesystem. Failures propagate without altering the prior snapshot;
    cleanup errors never replace the original failure or expose payloads.
    """
    payload = encoded.encode("utf-8") if isinstance(encoded, str) else encoded
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", mode="wb", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as error:
                # Concurrent replacements or a short-lived Windows reader may
                # temporarily deny delete sharing. Keep genuine/persistent
                # failures visible and retain a fixed 75 ms total retry budget.
                if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 4:
                    raise
                time.sleep(0.005 * 2**attempt)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                if logger is not None:
                    logger.warning(cleanup_warning)
