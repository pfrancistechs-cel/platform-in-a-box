"""No external tools needed (promtool test is skipped without promtool): the service, the router, the release controller's
decisions, the Prometheus rules."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def get(url):
    try:
        with OPENER.open(url, timeout=5) as resp:
            return resp.status, resp.read().decode(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(), dict(exc.headers)


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Service(unittest.TestCase):
    def setUp(self):
        self.app = load("svc", "service/app.py")

    def test_healthy_build_never_errors(self):
        self.app.BUG = ""
        self.assertTrue(all(self.app.handle("/")[0] == 200 for _ in range(200)))

    def test_errors_build_fails_about_40_percent(self):
        self.app.BUG = "errors"
        failed = sum(self.app.handle("/")[0] == 500 for _ in range(2000))
        self.assertTrue(600 < failed < 1000, failed)

    def test_errors_bug_does_not_touch_healthz(self):
        self.app.BUG = "errors"
        self.assertTrue(all(self.app.handle("/healthz")[0] == 200 for _ in range(100)))   # so the start-up probe cannot see the bug

    def test_slow_build_is_slow(self):
        self.app.BUG = "slow"
        start = time.time()
        self.app.handle("/")
        self.assertGreater(time.time() - start, 0.7)


class FakeBackend(BaseHTTPRequestHandler):
    version = "x"

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b'{"ok": true}'
        self.send_response(200 if self.path != "/boom" else 500)
        self.send_header("X-Version", self.server.version)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Router(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backends = {}
        for slot, version in (("blue", "v1"), ("green", "v2")):
            srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeBackend)
            srv.version = version
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            cls.backends[slot] = srv
        cls.tmp = tempfile.mkdtemp()
        cls.config = os.path.join(cls.tmp, "router.json")
        cls.port = free_port()
        env = dict(os.environ, PORT=str(cls.port), CONFIG_FILE=cls.config, no_proxy="127.0.0.1",
                   BLUE_URL=f"http://127.0.0.1:{cls.backends['blue'].server_port}",
                   GREEN_URL=f"http://127.0.0.1:{cls.backends['green'].server_port}")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "platform", "router.py")], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = f"http://127.0.0.1:{cls.port}"
        for _ in range(50):
            try:
                get(cls.base + "/healthz")
                break
            except Exception:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.kill()
        for srv in cls.backends.values():
            srv.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def set_config(self, data):
        with open(self.config, "w") as fh:
            json.dump(data, fh)
        time.sleep(0.5)

    def versions(self, n=60):
        return [get(self.base + "/")[2].get("X-Version") for _ in range(n)]

    def test_1_nothing_deployed_is_503(self):
        self.set_config({"active": None, "candidate": None, "weight": 0})
        self.assertEqual(get(self.base + "/")[0], 503)

    def test_2_active_slot_gets_everything(self):
        self.set_config({"active": "blue", "candidate": None, "weight": 0})
        self.assertEqual(set(self.versions()), {"v1"})

    def test_3_weight_splits_traffic(self):
        self.set_config({"active": "blue", "candidate": "green", "weight": 50})
        seen = self.versions(200)
        self.assertTrue(60 < seen.count("v2") < 140, seen.count("v2"))
        self.set_config({"active": "blue", "candidate": "green", "weight": 100})
        self.assertEqual(set(self.versions(30)), {"v2"})

    def test_4_stats_since_only_counts_new_requests(self):
        self.set_config({"active": "blue", "candidate": None, "weight": 0})
        now = time.time()
        self.versions(10)
        stats = json.loads(get(f"{self.base}/_router/state?since={now}")[1])["stats"]
        self.assertEqual(stats["blue"]["requests"], 10)
        self.assertEqual(json.loads(get(f"{self.base}/_router/state?since={time.time() + 5}")[1])["stats"]["blue"]["requests"], 0)

    def test_5_errors_are_counted(self):
        self.set_config({"active": "blue", "candidate": None, "weight": 0})
        now = time.time()
        for _ in range(4):
            get(self.base + "/boom")
        self.assertEqual(json.loads(get(f"{self.base}/_router/state?since={now}")[1])["stats"]["blue"]["errors"], 4)

    def test_6_metrics_are_prometheus_text(self):
        self.set_config({"active": "blue", "candidate": None, "weight": 0})
        get(self.base + "/")
        text = get(self.base + "/metrics")[1]
        self.assertIn('plat_requests_total{slot="blue",version="v1",code="200"}', text)
        self.assertIn('plat_requests_total{slot="blue",version="v1",code="500"}', text)
        self.assertIn("plat_request_seconds_bucket", text)

    def test_7_probe(self):
        self.assertEqual(get(self.base + "/_router/probe?slot=green")[0], 200)
        self.assertEqual(get(self.base + "/_router/probe?slot=purple")[0], 400)

    def test_8_a_broken_config_file_keeps_the_last_good_one(self):
        self.set_config({"active": "blue", "candidate": None, "weight": 0})
        with open(self.config, "w") as fh:
            fh.write("{not json")
        time.sleep(0.5)
        self.assertEqual(set(self.versions(10)), {"v1"})

    def test_9_unreachable_backend_is_a_502_and_counted(self):
        self.backends["green"].shutdown()
        self.backends["green"].server_close()
        self.set_config({"active": "green", "candidate": None, "weight": 0})
        self.assertEqual(get(self.base + "/")[0], 502)


class ReleaseJudge(unittest.TestCase):
    def setUp(self):
        self.d = load("deploy", "scripts/deploy.py")
        self.d.MIN_SAMPLES, self.d.STEP_TIMEOUT = 30, 0.05

    def judge(self, **stats):
        self.d.router = lambda path: {"stats": {"green": {"requests": 0, "errors": 0, "p95": 0.0, **stats}}}
        return self.d.judge("green", 0)

    def test_healthy_canary_passes(self):
        self.assertTrue(self.judge(requests=31, errors=0, p95=0.02)[0])

    def test_a_few_errors_are_tolerated(self):
        self.assertTrue(self.judge(requests=40, errors=3)[0])          # 7.5%

    def test_error_rate_over_limit_rolls_back(self):
        ok, text = self.judge(requests=40, errors=6)
        self.assertFalse(ok)
        self.assertIn("error rate", text)

    def test_obviously_bad_canary_is_stopped_before_enough_samples(self):
        self.assertFalse(self.judge(requests=10, errors=6)[0])

    def test_slow_canary_rolls_back(self):
        ok, text = self.judge(requests=35, errors=0, p95=0.9)
        self.assertFalse(ok)
        self.assertIn("p95", text)

    def test_too_little_traffic_is_not_a_pass(self):
        ok, text = self.judge(requests=3, errors=0)
        self.assertFalse(ok)
        self.assertIn("too little traffic", text)


@unittest.skipUnless(shutil.which("promtool"), "promtool not installed")
class Rules(unittest.TestCase):
    def test_rules_file_is_valid(self):
        self.assertEqual(subprocess.run(["promtool", "check", "rules", os.path.join(ROOT, "platform", "rules.yml")],
                                        capture_output=True).returncode, 0)

    def test_alert_fires_on_failing_requests_and_stays_quiet_on_healthy_ones(self):
        test_file = os.path.join(tempfile.mkdtemp(), "t.yml")
        with open(test_file, "w") as fh:
            fh.write(f"""rule_files: ["{ROOT}/platform/rules.yml"]
evaluation_interval: 5s
tests:
  - interval: 5s
    input_series:
      - series: 'plat_requests_total{{slot="blue",version="v1",code="200"}}'
        values: '0+100x20'
      - series: 'plat_requests_total{{slot="blue",version="v1",code="500"}}'
        values: '0+50x20'
    alert_rule_test:
      - eval_time: 90s
        alertname: ErrorRatioHigh
        exp_alerts:
          - exp_labels: {{severity: page}}
            exp_annotations: {{summary: "More than 5% of requests are failing (the SLO promises 99% success)"}}
  - interval: 5s
    input_series:
      - series: 'plat_requests_total{{slot="blue",version="v1",code="200"}}'
        values: '0+100x20'
      - series: 'plat_requests_total{{slot="blue",version="v1",code="500"}}'
        values: '0+1x20'
    alert_rule_test:
      - eval_time: 90s
        alertname: ErrorRatioHigh
        exp_alerts: []
""")
        r = subprocess.run(["promtool", "test", "rules", test_file], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
