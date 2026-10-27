"""Serve only allowlisted extension fixtures on loopback for manual browser QA."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = {
    "/reviews/claim-product": ROOT / "tests/extension_fixture.html",
    "/reviews/navigation-focus": ROOT / "tests/navigation_focus_fixture.html",
    "/fixture-stubs.js": ROOT / "tests/fixture-stubs.js",
    "/page-scroll-fixture": ROOT / "tests/page_scroll_fixture.html",
    "/verification-watch-fixture": ROOT / "tests/verification_watch_fixture.html",
    **{f"/{name}": ROOT / "extension" / name for name in (
        "options.html", "options.js", "sweep-modes.js", "runtime-policy.js",
        "page-controls.js", "page-scroll.js", "live-observer.js", "relay-settings.js",
        "scanner-control.js",
        "facet-controls.js", "content.js", "verification-watch.js",
    )},
}


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        source = ROUTES.get(path)
        if source is None:
            self.send_error(404)
            return
        body = source.read_text(encoding="utf-8")
        if path == "/options.html":
            body = body.replace(
                '<meta charset="utf-8">',
                '<meta charset="utf-8"><script src="/fixture-stubs.js"></script>',
                1,
            )
            body = body.replace(
                "<h1>Reviewer Item Relay</h1>",
                "<h1>Reviewer Item Relay</h1><p><strong>Local test preview:</strong> "
                "permissions, storage and notifier responses are simulated.</p>",
                1,
            )
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/javascript" if path.endswith(".js") else "text/html")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)


if __name__ == "__main__":
    print("http://127.0.0.1:8879/reviews/claim-product", flush=True)
    print("http://127.0.0.1:8879/options.html", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8879), FixtureHandler).serve_forever()
