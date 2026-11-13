#!/usr/bin/env python3
"""The platform's traffic router and metrics source. Reads /config/router.json (the deploy script is the only writer):
  {"active": "blue", "candidate": "green", "weight": 10}
Each request goes to the candidate with probability weight%, otherwise to the active slot.
Extra paths:
  /_router/state?since=EPOCH   config + per-slot statistics (requests, errors, p95) of requests newer than EPOCH
  /_router/probe?slot=green    200 if that slot's /healthz answers 200, else 502 (the deploy script waits on this)
  /metrics                     Prometheus text format: plat_requests_total{slot,version,code}, plat_request_seconds_*"""
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CONFIG_FILE = os.environ.get("CONFIG_FILE", "/config/router.json")
SLOTS = {"blue": os.environ.get("BLUE_URL", "http://plat-blue:8000"), "green": os.environ.get("GREEN_URL", "http://plat-green:8000")}
TIMEOUT = float(os.environ.get("BACKEND_TIMEOUT", "3"))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.5)

LOCK = threading.Lock()
EVENTS = deque()                                   # (time, slot, ok, seconds, version) for the last 5 minutes
COUNTS = defaultdict(int)                          # (slot, version, code) -> n
SECONDS = defaultdict(lambda: [0.0, 0, [0] * (len(BUCKETS) + 1)])   # slot -> [sum, count, cumulative-free bucket counts]
CONFIG = {"active": None, "candidate": None, "weight": 0}
_read_at = [0.0]


def load_config():
    now = time.time()
    if now - _read_at[0] < 0.3:
        return
    _read_at[0] = now
    try:
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        active, cand = data.get("active"), data.get("candidate")
        weight = max(0, min(100, int(data.get("weight", 0))))
        if (active is None or active in SLOTS) and (cand is None or (cand in SLOTS and cand != active)):
            CONFIG.update(active=active, candidate=cand, weight=weight if cand else 0)
    except (OSError, ValueError, TypeError, AttributeError):
        pass                                       # keep the last good config


def pick():
    load_config()
    if CONFIG["candidate"] and random.random() * 100 < CONFIG["weight"]:
        return CONFIG["candidate"]
    return CONFIG["active"]


def record(slot, status, seconds, version):
    now = time.time()
    with LOCK:
        EVENTS.append((now, slot, status < 500, seconds, version))
        while EVENTS and EVENTS[0][0] < now - 300:
            EVENTS.popleft()
        COUNTS[(slot, version or "unknown", str(status))] += 1
        entry = SECONDS[slot]
        entry[0] += seconds
        entry[1] += 1
        for i, bound in enumerate(BUCKETS):
            if seconds <= bound:
                entry[2][i] += 1
                break
        else:
            entry[2][-1] += 1


def stats(since):
    with LOCK:
        events = [e for e in EVENTS if e[0] >= since]
    out = {}
    for slot in SLOTS:
        mine = sorted(e[3] for e in events if e[1] == slot)
        total = len(mine)
        out[slot] = {"requests": total, "errors": sum(1 for e in events if e[1] == slot and not e[2]),
                     "versions": sorted({e[4] for e in events if e[1] == slot and e[4]}),
                     "p95": round(mine[min(total - 1, int(0.95 * total))], 3) if total else 0.0}
    return out


def metrics_text():
    lines = ["# HELP plat_requests_total Requests through the router.", "# TYPE plat_requests_total counter"]
    with LOCK:
        for (slot, version, code), n in sorted(COUNTS.items()):
            lines.append(f'plat_requests_total{{slot="{slot}",version="{version}",code="{code}"}} {n}')
        lines += ["# HELP plat_request_seconds Backend latency.", "# TYPE plat_request_seconds histogram"]
        for slot, (total, count, buckets) in sorted(SECONDS.items()):
            running = 0
            for i, bound in enumerate(BUCKETS):
                running += buckets[i]
                lines.append(f'plat_request_seconds_bucket{{slot="{slot}",le="{bound}"}} {running}')
            lines.append(f'plat_request_seconds_bucket{{slot="{slot}",le="+Inf"}} {count}')
            lines.append(f'plat_request_seconds_sum{{slot="{slot}"}} {total:.4f}')
            lines.append(f'plat_request_seconds_count{{slot="{slot}"}} {count}')
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, status, body, headers=None, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def forward(self, slot, path):
        started = time.time()
        try:
            with OPENER.open(SLOTS[slot] + path, timeout=TIMEOUT) as resp:
                status, body, version = resp.status, resp.read(), resp.headers.get("X-Version", "")
        except urllib.error.HTTPError as exc:
            status, body, version = exc.code, exc.read(), exc.headers.get("X-Version", "")
        except (urllib.error.URLError, OSError):
            status, body, version = 502, b'{"error": "backend unreachable"}', ""
        record(slot, status, time.time() - started, version)
        self.send(status, body, {"X-Slot": slot, "X-Version": version})

    def probe(self, slot):
        try:
            with OPENER.open(SLOTS[slot] + "/healthz", timeout=1.5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/healthz":
            return self.send(200, b"ok\n", ctype="text/plain")
        if url.path == "/metrics":
            return self.send(200, metrics_text().encode(), ctype="text/plain; version=0.0.4")
        if url.path == "/_router/state":
            load_config()
            return self.send(200, {**CONFIG, "stats": stats(float(query.get("since", ["0"])[0]))})
        if url.path == "/_router/probe":
            slot = query.get("slot", [""])[0]
            if slot not in SLOTS:
                return self.send(400, {"error": "unknown slot"})
            return self.send(200 if self.probe(slot) else 502, {"slot": slot, "healthy": self.probe(slot)})
        load_config()
        slot = pick()
        if slot is None:
            return self.send(503, {"error": "nothing deployed yet"})
        self.forward(slot, url.path + ("?" + url.query if url.query else ""))


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
