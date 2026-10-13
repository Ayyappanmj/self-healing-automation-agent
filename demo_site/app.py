"""Local demo site: serves UI versions of the same login page."""
from __future__ import annotations

import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSIONS_DIR = Path(__file__).parent / "versions"
ROUTES = {
    "/": "index.html",
    "/v1": "v1.html",
    "/v2": "v2.html",
    "/v3": "v3.html",
    "/v4": "v4.html",
}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(VERSIONS_DIR), **kwargs)

    def do_GET(self):
        path = self.path.split("?")[0]
        path = path.rstrip("/") or "/"
        if path in ROUTES:
            self.path = "/" + ROUTES[path]
        super().do_GET()

    def log_message(self, *args):  # keep test/UI output clean
        pass


def start_server(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Start in a background thread. port=0 picks a free port."""
    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":  # pragma: no cover
    srv = start_server(port=8000)
    print("Demo site running at http://127.0.0.1:8000  (Ctrl+C to stop)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()
