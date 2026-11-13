import os
import re
import stat
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = ["scripts/" + f for f in os.listdir(os.path.join(ROOT, "scripts")) if f.endswith(".sh")]
EXEC = SCRIPTS + ["scripts/deploy.py", "scripts/sbom_check.py"]


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class Repo(unittest.TestCase):
    def test_scripts_parse_and_are_executable(self):
        for rel in EXEC:
            path = os.path.join(ROOT, rel)
            self.assertTrue(os.stat(path).st_mode & stat.S_IXUSR, rel + " is not executable")
            if rel.endswith(".sh"):
                self.assertEqual(subprocess.run(["bash", "-n", path]).returncode, 0, rel)

    def test_python_files_compile(self):
        for rel in ("scripts/deploy.py", "scripts/sbom_check.py", "platform/router.py", "platform/console.py",
                    "platform/traffic.py", "service/app.py"):
            compile(read(rel), rel, "exec")

    def test_required_files(self):
        for rel in ("docker-compose.yml", ".env.example", ".gitignore", ".gitattributes", "platform/rules.yml",
                    "platform/prometheus.yml", "platform/policy/sbom.json", "platform/policy/dockerfile/rules.rego",
                    "service/Dockerfile", "service/Dockerfile.bad", "README.md", "STEPS.md", "DEMO.md", "DIAGRAM_PROMPT.md"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), rel)

    def test_wait_until_is_never_given_a_command_substitution(self):
        for rel in SCRIPTS:
            for n, line in enumerate(read(rel).splitlines(), 1):
                if re.search(r"wait_until\s+\d+\s.*\$\(", line) and not line.lstrip().startswith("#"):
                    self.fail(f"{rel}:{n} passes $(...) to wait_until: {line.strip()}")

    def test_no_pipe_into_grep_q(self):
        for rel in SCRIPTS:
            self.assertNotRegex(read(rel), r"\|\s*grep -q", rel)

    def test_no_curl_f_on_docker_hub(self):
        for rel in SCRIPTS:
            self.assertNotRegex(read(rel), r"curl[^\n]*-[a-zA-Z]*f[^\n]*registry-1\.docker\.io", rel)

    def test_only_the_intended_ports_are_published(self):
        ports = re.findall(r'ports:\s*\["([^"]+)"\]', read("docker-compose.yml"))
        self.assertEqual(sorted(ports), ["127.0.0.1:5000:5000", "127.0.0.1:9090:9090", "8080:8080", "8081:8081"])

    def test_the_demo_images_are_pinned(self):
        env = read(".env.example")
        self.assertIn("prom/prometheus:v2.54.1", env)
        self.assertNotIn(":latest", env)
        self.assertIn("python:latest", read("service/Dockerfile.bad"))      # the one deliberate exception
        self.assertNotIn(":latest", read("service/Dockerfile"))

    def test_expected_container_count_matches_compose(self):
        self.assertEqual(len(re.findall(r"container_name:", read("docker-compose.yml"))), 5)
        self.assertRegex(read("scripts/lib.sh"), r"EXPECTED_CONTAINERS=5")

    def test_every_script_run_by_hand_is_documented(self):
        steps = read("STEPS.md")
        self.assertIn("chmod +x scripts/*.sh scripts/*.py", steps)
        for cmd in ("setup.sh", "preflight.sh", "up.sh", "smoke.sh", "demo.sh gameday", "destroy.sh", "All 13 checks passed."):
            self.assertIn(cmd, steps)
        for port in ("22", "8080", "8081"):
            self.assertIn(port, steps)

    def test_demo_doc_is_brief(self):
        text = read("DEMO.md")
        self.assertLess(len(text.split()), 450)
        self.assertLess(len(text.splitlines()), 40)


if __name__ == "__main__":
    unittest.main()
