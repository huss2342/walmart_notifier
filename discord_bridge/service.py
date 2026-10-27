"""Loopback-only ingest; live delivery is a separate, explicitly enabled action."""

from __future__ import annotations

import hmac
import json
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import JobStore, validate_batch
from .delivery import deliver_due as deliver_due

MAX_BODY_BYTES = 524288


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def make_server(
    store: JobStore, *, key: str, port=8788, live=False, on_queued=None, on_accepted=None
) -> ThreadingHTTPServer:
    if not isinstance(key, str) or not 32 <= len(key) <= 256 or any(c.isspace() for c in key):
        raise ValueError(
            "DISCORD_BRIDGE_INGEST_KEY must be a random secret of at least 32 characters"
        )

    class Handler(BaseHTTPRequestHandler):
        server_version = "ReviewerBridge"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, _format, *_args):
            pass

        def reply(self, status, data):
            encoded = json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
            )
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(encoded)

        def authorized(self):
            if self.headers.get("Origin") is not None:
                self.reply(403, {"error": "browser origins are not accepted"})
                return False
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + key).encode("utf-8")):
                self.reply(401, {"error": "authentication required"})
                return False
            return True

        def do_GET(self):  # noqa: N802
            if not self.authorized():
                return
            if self.path != "/health":
                self.reply(404, {"error": "not found"})
                return
            self.reply(200, {"ok": True, "live_sending": live, "jobs": store.counts()})

        def do_POST(self):  # noqa: N802
            if not self.authorized():
                return
            if self.path != "/v1/observations":
                self.reply(404, {"error": "not found"})
                return
            if self.headers.get_content_type() != "application/json":
                self.reply(415, {"error": "application/json required"})
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                    raise ValueError
                length = int(lengths[0])
                if not 0 < length <= MAX_BODY_BYTES:
                    self.reply(413, {"error": "body exceeds limit"})
                    return
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError
                items = validate_batch(json.loads(body))
            except (ValueError, TypeError, UnicodeError, OSError):
                self.reply(400, {"error": "invalid observation batch"})
                return
            try:
                result = store.record(items)
            except Exception:
                self.reply(503, {"error": "durable intake unavailable; retry"})
                return
            if on_queued is not None:
                on_queued()
            if on_accepted is not None:
                # A monitoring failure cannot undo the commit or ask the
                # extension to retry an observation already accepted.
                with suppress(Exception):
                    on_accepted(result)
            self.reply(202, {**result, "status": "durably_accepted"})

        def do_OPTIONS(self):  # noqa: N802
            self.reply(403, {"error": "browser origins are not accepted"})

    return _Server(("127.0.0.1", port), Handler)
