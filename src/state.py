"""Dedupe store so a given item only ever alerts once.

Backed by Azure Table Storage (pennies per month). Falls back to an in-memory
set when no connection string is configured, which keeps local runs and tests
free of any Azure dependency.

Also stores small opaque markers (the IMAP UID high-water mark) so the poller
does not re-download the same mail on every run.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

log = logging.getLogger(__name__)

_PARTITION = "seen"
_MARKERS = "markers"


def _is(exc: BaseException, name: str) -> bool:
    """Match an azure-core error by name without importing azure at module level."""
    return type(exc).__name__ == name


class SeenStore:
    def __init__(self, connection_string: str | None = None, table_name: str = "seenitems"):
        self._table = None
        self._memory: set[str] = set()
        self._marker_memory: dict[str, str] = {}
        conn = connection_string or os.environ.get("AzureWebJobsStorage")
        if not conn:
            log.warning("No storage connection string; dedupe is in-memory only.")
            return
        try:
            from azure.data.tables import TableServiceClient

            service = TableServiceClient.from_connection_string(conn)
            service.create_table_if_not_exists(table_name)
            self._table = service.get_table_client(table_name)
        except Exception:
            log.exception("Table Storage unavailable; falling back to in-memory dedupe.")

    # --- dedupe -------------------------------------------------------------

    def is_new(self, item_id: str) -> bool:
        """Cheap pre-filter. `claim` is the authoritative check."""
        if self._table is None:
            return item_id not in self._memory
        try:
            self._table.get_entity(partition_key=_PARTITION, row_key=item_id)
            return False
        except Exception as exc:  # ResourceNotFoundError and transient failures
            if _is(exc, "ResourceNotFoundError"):
                return True
            log.exception("Dedupe lookup failed for %s; treating as seen.", item_id)
            # Fail closed: a storage blip should not spam the phone with repeats.
            return False

    def claim(self, item_id: str, title: str = "") -> bool:
        """Atomically take ownership of an item. True only for the first caller.

        The timer and the ingest endpoint can run concurrently, so a
        read-then-write dedupe would let the same item alert twice. Table
        Storage rejects a create for an existing row key, which makes the claim
        a single atomic operation instead.
        """
        if self._table is None:
            if item_id in self._memory:
                return False
            self._memory.add(item_id)
            return True
        try:
            self._table.create_entity(
                {
                    "PartitionKey": _PARTITION,
                    "RowKey": item_id,
                    "title": title[:512],
                    "seen_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
            )
            return True
        except Exception as exc:
            if _is(exc, "ResourceExistsError"):
                return False  # someone else got there first
            log.exception("Could not claim %s; treating as already claimed.", item_id)
            return False

    def release(self, item_id: str) -> None:
        """Undo a claim so the next run retries.

        Used when delivery fails: an item the user wanted should not be lost
        just because ntfy was briefly unreachable.
        """
        if self._table is None:
            self._memory.discard(item_id)
            return
        try:
            self._table.delete_entity(partition_key=_PARTITION, row_key=item_id)
        except Exception as exc:
            if not _is(exc, "ResourceNotFoundError"):
                log.exception("Could not release claim on %s.", item_id)

    def mark_seen(self, item_id: str, title: str = "") -> None:
        """Record an item as seen regardless of who saw it first."""
        if self._table is None:
            self._memory.add(item_id)
            return
        try:
            self._table.upsert_entity(
                {
                    "PartitionKey": _PARTITION,
                    "RowKey": item_id,
                    "title": title[:512],
                    "seen_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
            )
        except Exception:
            log.exception("Failed to record %s as seen.", item_id)

    # --- markers ------------------------------------------------------------

    def get_marker(self, key: str) -> str | None:
        if self._table is None:
            return self._marker_memory.get(key)
        try:
            entity = self._table.get_entity(partition_key=_MARKERS, row_key=key)
            value = entity.get("value")
            return str(value) if value is not None else None
        except Exception as exc:
            if not _is(exc, "ResourceNotFoundError"):
                log.exception("Could not read marker %s.", key)
            return None

    def set_marker(self, key: str, value: str) -> None:
        if self._table is None:
            self._marker_memory[key] = value
            return
        try:
            self._table.upsert_entity(
                {"PartitionKey": _MARKERS, "RowKey": key, "value": value}
            )
        except Exception:
            log.exception("Could not write marker %s.", key)
