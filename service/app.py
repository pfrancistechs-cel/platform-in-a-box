"""The service the platform ships (a tiny 'shop' API). The release variants differ only in build arguments:
  APP_VERSION  what it reports (v1, v2, ...)
  BUG          "" (healthy), "errors" (40% of / requests fail: a bug the unit tests cannot see), "slow" (adds 0.8 s)"""
import json
import os
import random
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata

VERSION = os.environ.get("APP_VERSION", "dev")
BUG = os.environ.get("BUG", "")
STARTED = time.time()


def dependency_version():
    try:
        return metadata.version("urllib3")
    except metadata.PackageNotFoundError:
        return "not installed"


def handle(path):
    """Returns (status, body dict). Kept free of HTTP so the tests can call it directly."""
    if path == "/healthz":
        return 200, {"status": "ok", "version": VERSION}
    if path == "/info":
        return 200, {"version": VERSION, "urllib3": dependency_version(), "host": socket.gethostname(),
                     "uptime_seconds": round(time.time() - STARTED, 1)}
    if path.split("?")[0] == "/":
        if BUG == "slow":
            time.sleep(0.8)
        if BUG == "errors" and random.random() < 0.4:
            return 500, {"error": "internal error", "version": VERSION}
        return 200, {"app": "shop", "version": VERSION, "items": ["tea", "bread", "rice"]}
    return 404, {"error": "not found"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        status, body = handle(self.path)
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Version", VERSION)
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8000"))), Handler).serve_forever()
