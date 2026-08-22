"""Item sources.

Each source yields `Item` objects from somewhere the account holder already has
legitimate access to. See docs/architecture.md for why there is deliberately no
source that logs into walmart.com with stored credentials.
"""

from .base import ItemSource
from .imap_source import ImapSource
from .webhook_source import parse_ingest_payload

__all__ = ["ItemSource", "ImapSource", "parse_ingest_payload"]
