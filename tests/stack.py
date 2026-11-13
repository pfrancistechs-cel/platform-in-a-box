"""Test helper: the whole platform as local processes. REAL: registry, router, console, traffic, Prometheus, cosign, syft,
conftest and the scripts. Replaced: docker (tests/stubdocker.py). Skipped when a tool is missing."""
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOLS = ("cosign", "syft", "registry", "crane", "conftest", "prometheus")
AVAILABLE = all(shutil.which(t) for t in TOOLS)
SKIP_REASON = "needs cosign, syft, registry, crane, conftest and prometheus in PATH (the demo instance has only cosign, syft and conftest)"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Platform:
    def __init__(self, rate="20"):
        self.tmp = tempfile.mkdtemp(prefix="plat")
        self.bin = os.path.join(self.tmp, "bin")
        self.results = os.path.join(self.tmp, "results")
        self.state = os.path.join(self.tmp, "state")
        self.keys = os.path.join(self.tmp, "keys")
        for d in (self.bin, self.results, self.state, self.keys, os.path.join(self.tmp, "registry"), os.path.join(self.tmp, "prom")):
            os.makedirs(d)
        self.ports = {k: free_port() for k in ("registry", "router", "console", "prom", "blue", "green")}
        self.rate = rate
        self.procs = []
        with open(os.path.join(self.bin, "docker"), "w") as fh:
            fh.write(f'#!/bin/sh\nexec "{sys.executable}" "{os.path.join(ROOT, "tests", "stubdocker.py")}" "$@"\n')
        os.chmod(os.path.join(self.bin, "docker"), 0o755)
        self.router_url = f"http://127.0.0.1:{self.ports['router']}"
        self.console_url = f"http://127.0.0.1:{self.ports['console']}"
        self.prom_url = f"http://127.0.0.1:{self.ports['prom']}"
        self.env = dict(os.environ, PYTHONUNBUFFERED="1", no_proxy="127.0.0.1,localhost", NO_PROXY="127.0.0.1,localhost",
                        PATH=self.bin + os.pathsep + os.environ["PATH"], STUB_DIR=self.tmp,
                        STUB_PORT_BLUE=str(self.ports["blue"]), STUB_PORT_GREEN=str(self.ports["green"]),
                        REGISTRY=f"localhost:{self.ports['registry']}", ROUTER_URL=self.router_url, CONSOLE_URL=self.console_url,
                        PROM_URL=self.prom_url, RESULTS_DIR=self.results, STATE_DIR=self.state, KEYS_DIR=self.keys)
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            self.env.pop(var, None)

    def spawn(self, cmd, **env):
        self.procs.append(subprocess.Popen(cmd, env={**self.env, **env}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           start_new_session=True))

    def wait(self, url, secs=30):
        end = time.time() + secs
        while True:
            try:
                OPENER.open(url, timeout=2).read()
                return
            except Exception:
                if time.time() > end:
                    raise RuntimeError("not ready: " + url)
                time.sleep(0.2)

    def start(self):
        reg_cfg = os.path.join(self.tmp, "registry.yml")
        with open(reg_cfg, "w") as fh:
            fh.write(f"version: 0.1\nstorage:\n  filesystem:\n    rootdirectory: {self.tmp}/registry\n"
                     f"http:\n  addr: 127.0.0.1:{self.ports['registry']}\n")
        self.spawn(["registry", "serve", reg_cfg])
        with open(os.path.join(self.state, "router.json"), "w") as fh:
            fh.write('{"active": null, "candidate": null, "weight": 0}')
        self.spawn([sys.executable, os.path.join(ROOT, "platform", "router.py")], PORT=str(self.ports["router"]),
                   CONFIG_FILE=os.path.join(self.state, "router.json"), BLUE_URL=f"http://127.0.0.1:{self.ports['blue']}",
                   GREEN_URL=f"http://127.0.0.1:{self.ports['green']}")
        # Prometheus: the project's own rules file, with the scrape target pointed at the local router
        shutil.copy(os.path.join(ROOT, "platform", "rules.yml"), os.path.join(self.tmp, "rules.yml"))
        with open(os.path.join(self.tmp, "prometheus.yml"), "w") as fh:
            fh.write("global:\n  scrape_interval: 5s\n  evaluation_interval: 5s\nrule_files:\n  - rules.yml\n"
                     f"scrape_configs:\n  - job_name: router\n    static_configs:\n      - targets: ['127.0.0.1:{self.ports['router']}']\n")
        self.spawn(["prometheus", f"--config.file={self.tmp}/prometheus.yml", f"--storage.tsdb.path={self.tmp}/prom",
                    f"--web.listen-address=127.0.0.1:{self.ports['prom']}", "--storage.tsdb.retention.time=1h"])
        self.spawn([sys.executable, os.path.join(ROOT, "platform", "console.py")], PORT=str(self.ports["console"]),
                   ROUTER_URL=self.router_url, PROM_URL=self.prom_url, REGISTRY_URL=f"http://127.0.0.1:{self.ports['registry']}",
                   REPO="platform/shop", RESULTS_DIR=self.results, STATE_DIR=self.state)
        self.wait(self.router_url + "/healthz")
        self.wait(self.console_url + "/healthz")
        self.wait(self.prom_url + "/-/ready", 40)
        self.wait(f"http://127.0.0.1:{self.ports['registry']}/v2/")
        self.spawn([sys.executable, os.path.join(ROOT, "platform", "traffic.py")], TARGET=self.router_url + "/", RATE=self.rate)
        return self

    def script(self, *args, timeout=600, **env):
        r = subprocess.run(["bash", os.path.join(ROOT, "scripts", args[0]), *args[1:]], env={**self.env, **env},
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr

    def get(self, url):
        return OPENER.open(url, timeout=5).read().decode()

    def stop(self):
        subprocess.run(["docker", "rm", "-f", "plat-blue", "plat-green"], env=self.env, capture_output=True)
        for proc in self.procs:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(0.3)
        shutil.rmtree(self.tmp, ignore_errors=True)
