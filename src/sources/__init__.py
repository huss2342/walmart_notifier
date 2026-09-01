"""Item sources.

The only source is the browser extension, which reads the reviewer page the
account holder already has open in their own signed-in session and POSTs what
it finds to the local server. See docs/architecture.md for why there is
deliberately no source that logs into walmart.com with stored credentials.
"""

from .base import ItemSource
from .webhook_source import parse_ingest_payload

__all__ = ["ItemSource", "parse_ingest_payload"]
