"""Serve this checkout's primary frontend on loopback, without stale browser caches."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class FrontendHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        del self.headers["If-Modified-Since"]
        del self.headers["If-None-Match"]
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3100)
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    handler = partial(FrontendHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"Basis frontend: {directory}", flush=True)
    print(f"http://127.0.0.1:{server.server_port}/?mode=live&signin=redirect#dashboard", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
