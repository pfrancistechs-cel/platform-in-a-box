"""The service's own unit tests: stage 1 of the pipeline runs these. Note what they cannot see: a BUG baked in at build time."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402


class ServiceTests(unittest.TestCase):
    def setUp(self):
        app.BUG = ""

    def test_healthz(self):
        status, body = app.handle("/healthz")
        self.assertEqual((status, body["status"]), (200, "ok"))

    def test_index_lists_items(self):
        status, body = app.handle("/")
        self.assertEqual(status, 200)
        self.assertEqual(body["items"], ["tea", "bread", "rice"])

    def test_unknown_path_is_404(self):
        self.assertEqual(app.handle("/nope")[0], 404)

    def test_info_has_version(self):
        self.assertIn("version", app.handle("/info")[1])

    def test_query_string_is_ignored(self):
        self.assertEqual(app.handle("/?x=1")[0], 200)


if __name__ == "__main__":
    unittest.main()
