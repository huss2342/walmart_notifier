"""Serve the verification QA page and its two source files on loopback only."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = {
    "/": ROOT / "tests" / "verification_fixture.html",
    "/engine.js": ROOT / "extension" / "verification-engine.js",
    "/chrome-driver.js": ROOT / "extension" / "verification-driver.js",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        source = ROUTES.get(path)
        if source is None:
            self.send_error(404)
            return
        body = source.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/javascript" if path.endswith(".js") else "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print("http://127.0.0.1:8885/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8885), Handler).serve_forever()
