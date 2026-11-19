"""Serve the repo for the web explorer, telling browsers to revalidate every file.

Run from the repo root: python scripts/serve_web.py [port]
python -m http.server sends no Cache-Control header, so after an update a browser
can pair a cached queries.js with a new app.js; no-cache rules that out.
"""

import functools
import http.server
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    handler = functools.partial(NoCache, directory=str(REPO))
    print(f"Open http://localhost:{port}/web/")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
