"""Dedupe store so a given item only ever alerts once.

Backed by Azure Table Storage (pennies per month). Falls back to an in-memory
set when no connection string is configured, which keeps local runs and tests
free of any Azure dependency.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_PARTITION = "seen"


class SeenStore:
    def __init__(self, connection_string: str | None = None, table_name: str = "seenitems"):
        self._table = None
        self._memory: set[str] = set()
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

    def is_new(self, item_id: str) -> bool:
        if self._table is None:
            return item_id not in self._memory
        try:
            self._table.get_entity(partition_key=_PARTITION, row_key=item_id)
            return False
        except Exception as exc:  # ResourceNotFoundError and transient failures
            if type(exc).__name__ == "ResourceNotFoundError":
                return True
            log.exception("Dedupe lookup failed for %s; treating as seen.", item_id)
            # Fail closed: a storage blip should not spam the phone with repeats.
            return False

    def mark_seen(self, item_id: str, title: str = "") -> None:
        if self._table is None:
            self._memory.add(item_id)
            return
        try:
            self._table.upsert_entity(
                {
                    "PartitionKey": _PARTITION,
                    "RowKey": item_id,
                    "title": title[:512],
                    "seen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            )
        except Exception:
            log.exception("Failed to record %s as seen.", item_id)
