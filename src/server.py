"""Local ingest server.

Replaces what used to be an Azure Function. The reviewer portal can only be
read from a signed-in browser, so something on this machine has to be awake to
receive what the extension reads anyway -- and once that is true, a cloud
function is a bill and a deployment step buying nothing.

Binds to loopback only. Nothing here is reachable from the network.

    python src/server.py
"""

from __future__ import annotations

import argparse
import contextlib
import hmac
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import load_rules, seed_mode  # noqa: E402
from notifiers import build_notifier  # noqa: E402
from pipeline import process  # noqa: E402
from sources.webhook_source import parse_ingest_payload  # noqa: E402
from state import SeenStore, default_path  # noqa: E402

log = logging.getLogger("notifier")

MAX_INGEST_BYTES = 4_000_000
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


class Handler(BaseHTTPRequestHandler):
    server_version = "ReviewerNotifier/2.0"

    # Injected by serve().
    store: SeenStore
    token: str
    # Built once. Rebuilding per request re-ran the env parsing, and on a
    # half-configured install logged the same failure on every single relay.
    notifier: object

    # --- helpers ------------------------------------------------------------

    def _cors(self) -> None:
        # The extension's service worker has host permission for this origin, so
        # its fetch is exempt from CORS. Answering anyway costs nothing and
        # removes a whole class of silent, hard-to-read failures.
        origin = self.headers.get("Origin", "")
        if origin.startswith("chrome-extension://") or origin.startswith("moz-extension://"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Ingest-Token")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def _reply(self, code: int, payload: dict | str) -> None:
        body = (json.dumps(payload) if isinstance(payload, dict) else payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type",
                         "application/json" if isinstance(payload, dict) else "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not self.token:
            return True
        return hmac.compare_digest(self.headers.get("X-Ingest-Token", ""), self.token)

    def log_message(self, fmt: str, *args) -> None:
        # Route through logging instead of stderr, and drop the noisy default
        # per-request line -- the handlers below log what actually matters.
        log.debug(fmt, *args)

    # --- routes -------------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/", "/health"):
            self._reply(200, {
                "status": "ok",
                "rules": [r.name for r in load_rules()],
                "notifier": type(self.notifier).__name__,
                "notifier_configured": type(self.notifier).__name__ != "NullNotifier",
                "seed_mode": seed_mode(),
                "seen_items": len(self.store),
                "state_file": str(self.store.path) if self.store.path else "(memory)",
            })
            return
        self._reply(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if path != "/ingest":
            self._reply(404, "not found")
            return
        if not self._authorized():
            self._reply(403, "forbidden")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reply(400, "bad content-length")
            return
        if length > MAX_INGEST_BYTES:
            self._reply(413, "payload too large")
            return

        body = self.rfile.read(length) if length else b""
        try:
            items = parse_ingest_payload(body, source="extension")
        except Exception:
            log.exception("Could not parse ingest payload.")
            self._reply(400, "bad payload")
            return

        # Rules are re-read per request on purpose: editing src/rules.json
        # takes effect on the next relay, with no restart.
        summary = process(
            items, load_rules(), self.store, self.notifier, seed_only=seed_mode()
        )
        if summary.notified or summary.failed:
            log.info("Ingest: %s", json.dumps(summary.as_dict()))
        else:
            log.debug("Ingest: %s", json.dumps(summary.as_dict()))
        self._reply(200, summary.as_dict())


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          state_path: str | None = None) -> None:
    # Unbuffered-ish stdout so the banner and log lines appear immediately when
    # this is piped to a file or run as a scheduled task.
    with contextlib.suppress(AttributeError, OSError):
        sys.stdout.reconfigure(line_buffering=True)

    Handler.store = SeenStore(state_path)
    Handler.token = os.environ.get("INGEST_TOKEN", "")
    Handler.notifier = build_notifier()

    notifier = Handler.notifier
    rules = load_rules()

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True

    print(f"Reviewer notifier listening on http://{host}:{port}")
    print(f"  ingest endpoint : http://{host}:{port}/ingest")
    print(f"  state file      : {Handler.store.path or '(memory)'}  ({len(Handler.store)} seen)")
    print(f"  notifier        : {type(notifier).__name__}")
    print(f"  rules           : {', '.join(r.name for r in rules)}")
    print(f"  token required  : {'yes' if Handler.token else 'no'}")
    if seed_mode():
        print("  SEED_MODE       : ON - recording items as seen, sending nothing")
    if type(notifier).__name__ == "NullNotifier":
        print("\n  WARNING: no push channel configured - nothing will reach your phone.")
        print("  Set NTFY_TOPIC in notifier.env. Generate one with:")
        print('    python -c "import secrets; print(secrets.token_hex(16))"')
    print("\nLeave this running. Ctrl-C to stop.")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        Handler.store.save()
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local reviewer-item notifier.")
    parser.add_argument("--host", default=os.environ.get("BIND_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--state", default=os.environ.get("STATE_PATH"),
                        help=f"dedupe file (default: {default_path()})")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    serve(args.host, args.port, args.state)


if __name__ == "__main__":
    main()
