#!/usr/bin/env python3
"""The release controller: starts a new version beside the live one, moves traffic to it step by step, watches the router's
numbers, and rolls back by itself when the new version misbehaves.

  deploy.py deploy REF VERSION DIGEST   first deploy: straight to 100%.  Otherwise canary 10% -> 50% -> 100%.
  deploy.py heal                        the live container is gone: start the last good version again
  deploy.py status                      print the live state

Exit codes: 0 released (or healed), 1 rolled back (the live version is unchanged), 2 could not start / usage.
Settings (environment): DOCKER, ROUTER_URL, STATE_DIR, NETWORK, STEPS, MIN_SAMPLES, MAX_ERROR, MAX_P95, STEP_TIMEOUT."""
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

DOCKER = shlex.split(os.environ.get("DOCKER", "docker"))
ROUTER = os.environ.get("ROUTER_URL", "http://127.0.0.1:8080")
STATE_DIR = os.environ.get("STATE_DIR", "state")
NETWORK = os.environ.get("NETWORK", "platform-net")
STEPS = [int(s) for s in os.environ.get("STEPS", "10,50,100").split(",")]
MIN_SAMPLES = int(os.environ.get("MIN_SAMPLES", "30"))     # requests the candidate must serve before a step is judged
MAX_ERROR = float(os.environ.get("MAX_ERROR", "0.10"))     # more than 10% failed: roll back
MAX_P95 = float(os.environ.get("MAX_P95", "0.5"))          # p95 over 0.5 s: roll back
STEP_TIMEOUT = float(os.environ.get("STEP_TIMEOUT", "90"))
START_TIMEOUT = float(os.environ.get("START_TIMEOUT", "90"))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def out(text):
    print(text, flush=True)


def write_json(name, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def read_json(name):
    try:
        with open(os.path.join(STATE_DIR, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def router(path):
    with OPENER.open(ROUTER + path, timeout=5) as resp:
        return json.loads(resp.read())


def docker(*args):
    return subprocess.run(DOCKER + list(args), capture_output=True, text=True)


def other(slot):
    return "green" if slot == "blue" else "blue"


def start(slot, ref):
    docker("rm", "-f", "plat-" + slot)
    r = docker("run", "-d", "--name", "plat-" + slot, "--network", NETWORK, "--restart", "unless-stopped", ref)
    if r.returncode != 0:
        out(f"could not start the container: {r.stderr.strip()[:200]}")
        return False
    end = time.time() + START_TIMEOUT
    while time.time() < end:
        try:
            if router(f"/_router/probe?slot={slot}").get("healthy"):
                return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(1)
    out(f"plat-{slot} did not become healthy within {START_TIMEOUT:.0f} s")
    return False


def judge(slot, since):
    """Waits for enough samples, then decides. Returns (ok, text)."""
    end = time.time() + STEP_TIMEOUT
    while True:
        s = router(f"/_router/state?since={since}")["stats"][slot]
        errors, total = s["errors"], s["requests"]
        rate = errors / total if total else 0.0
        text = f"{total} requests, {errors} errors ({rate * 100:.0f}%), p95 {s['p95'] * 1000:.0f} ms"
        if total >= 8 and errors >= 5 and rate > MAX_ERROR:
            return False, text + f": error rate over {MAX_ERROR * 100:.0f}%"      # no need to wait for more
        if total >= MIN_SAMPLES:
            if rate > MAX_ERROR:
                return False, text + f": error rate over {MAX_ERROR * 100:.0f}%"
            if s["p95"] > MAX_P95:
                return False, text + f": p95 over {MAX_P95 * 1000:.0f} ms"
            return True, text
        if time.time() > end:
            return False, text + f": too little traffic to judge (needed {MIN_SAMPLES})"
        time.sleep(1)


def deploy(ref, version, digest):
    live = read_json("live.json")
    if not live:
        slot = "blue"
        out(f"first deploy: starting {version} in slot {slot}")
        if not start(slot, ref):
            docker("rm", "-f", "plat-" + slot)
            return 2
        write_json("router.json", {"active": slot, "candidate": None, "weight": 0})
        write_json("live.json", {"slot": slot, "version": version, "digest": digest, "ref": ref, "previous": None})
        time.sleep(0.6)                                    # the router re-reads its file every 0.3 s: wait until it has
        out(f"{version} is live (100% of traffic)")
        return 0
    old, new = live["slot"], other(live["slot"])
    out(f"canary: {live['version']} is live in {old}; starting {version} in {new}")
    if not start(new, ref):
        docker("rm", "-f", "plat-" + new)
        write_json("router.json", {"active": old, "candidate": None, "weight": 0})
        return 2
    for weight in STEPS:
        write_json("router.json", {"active": old, "candidate": new, "weight": weight})
        since = time.time()
        time.sleep(0.6)                                    # the router re-reads its file every 0.3 s
        ok, text = judge(new, since)
        out(f"  {weight:>3}% to {version}: {text}" + (" -> ok" if ok else " -> ROLL BACK"))
        if not ok:
            write_json("router.json", {"active": old, "candidate": None, "weight": 0})
            time.sleep(0.6)
            docker("rm", "-f", "plat-" + new)
            out(f"rolled back: {live['version']} is still live")
            return 1
    write_json("router.json", {"active": new, "candidate": None, "weight": 0})
    write_json("live.json", {"slot": new, "version": version, "digest": digest, "ref": ref,
                             "previous": {"version": live["version"], "digest": live["digest"], "ref": live.get("ref")}})
    time.sleep(1)                                          # the router now sends everything to the new slot
    docker("rm", "-f", "plat-" + old)
    out(f"{version} is live (100% of traffic); {live['version']} was stopped")
    return 0


def heal():
    live = read_json("live.json")
    if not live:
        out("nothing to heal: no release has gone live yet")
        return 2
    try:
        if router(f"/_router/probe?slot={live['slot']}").get("healthy"):
            out(f"{live['version']} in {live['slot']} is healthy: nothing to do")
            return 0
    except (urllib.error.URLError, OSError, ValueError):
        pass
    out(f"{live['version']} in {live['slot']} is down: starting it again from the registry")
    if not start(live["slot"], live["ref"]):
        return 2
    out(f"{live['version']} is serving again")
    return 0


def main(argv):
    if argv[:1] == ["deploy"] and len(argv) == 4:
        return deploy(argv[1], argv[2], argv[3])
    if argv[:1] == ["heal"]:
        return heal()
    if argv[:1] == ["status"]:
        out(json.dumps(read_json("live.json")))
        return 0
    out(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
