"""The real scripts, real registry, cosign, syft, conftest, Prometheus, router, console and traffic; docker is the stub in
tests/stubdocker.py. Skipped unless those programs are in PATH."""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import stack  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")


@unittest.skipUnless(stack.AVAILABLE, stack.SKIP_REASON)
class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = stack.Platform().start()
        os.makedirs(cls.p.keys, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.p.stop()

    def scenario(self, name):
        code, out = self.p.script("demo.sh", name)
        out = ANSI.sub("", out)
        self.assertEqual(code, 0, out)
        self.assertIn("PASS  " + name, out)
        return out

    def state(self):
        return json.loads(self.p.get(self.p.console_url + "/api/state"))

    def test_1_ship(self):
        self.scenario("ship")
        live = json.load(open(os.path.join(self.p.state, "live.json")))
        self.assertEqual(live["version"], "v1")

    def test_2_smoke(self):
        code, out = self.p.script("smoke.sh")
        self.assertEqual(code, 0, ANSI.sub("", out))
        self.assertIn("All 13 checks passed.", out)

    def test_3_upgrade(self):
        out = self.scenario("upgrade")
        self.assertIn("50% to v2", out)

    def test_4_policy(self):
        self.scenario("policy")

    def test_5_supply(self):
        self.scenario("supply")

    def test_6_rollback(self):
        self.scenario("rollback")

    def test_7_outage(self):
        self.scenario("outage")

    def test_8_console_shows_the_whole_story(self):
        s = self.state()
        results = [r["result"] for r in s["runs"]]
        for needed in ("LIVE", "BLOCKED", "ROLLED BACK"):
            self.assertIn(needed, results)
        self.assertEqual(s["live"]["version"], "v2")
        tags = {i["tag"]: i for i in s["images"]}
        self.assertTrue(tags["v2"]["signed"] and tags["v2"]["sbom"])
        self.assertNotIn("v9-sloppy", tags)                 # blocked before the build
        self.assertIsNotNone(s["alerts"])                   # Prometheus reachable
        self.assertIsNotNone(s["slo"]["availability_5m"])

    def test_9_a_slow_build_passes_the_gate_and_is_judged_by_the_canary(self):
        code, out = self.p.script("ship.sh", "v7", "--bug", "slow")
        out = ANSI.sub("", out)
        self.assertIn("release gate", out)
        self.assertIn("p95", out) if code == 1 else self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
