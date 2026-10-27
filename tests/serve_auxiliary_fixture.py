"""Serve the isolated auxiliary-scan browser fixture on loopback only."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ROUTES = {
    "/": ROOT / "tests/auxiliary_fixture.html",
    "/auxiliary_fixture.html": ROOT / "tests/auxiliary_fixture.html",
    "/reviews/claim-product": ROOT / "tests/auxiliary_worker_fixture.html",
    **{f"/{name}": ROOT / "extension" / name for name in (
        "sweep-modes.js", "runtime-policy.js", "page-controls.js", "page-scroll.js",
        "live-observer.js", "facet-controls.js", "content.js", "auxiliary-scans.js",
    )},
}


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        source = ROUTES.get(urlsplit(self.path).path)
        if source is None:
            self.send_error(404)
            return
        body = source.read_bytes()
        self.send_response(200)
        content_type = "text/javascript" if source.suffix == ".js" else "text/html; charset=utf-8"
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; "
            "object-src 'none'; frame-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    print("http://127.0.0.1:8881/auxiliary_fixture.html", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8881), FixtureHandler).serve_forever()
