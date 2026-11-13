#!/usr/bin/env python3
"""A stand-in for `docker`, for tests only. The registry, cosign, syft, conftest, Prometheus, the router and the scripts in the
tests are REAL; only docker itself is replaced:

  build -> makes an image layer (app.py, a version marker, one python dist-info per pinned requirement)
  push  -> pushes it to the real registry with `crane append`
  run   -> starts service/app.py locally as the container plat-blue or plat-green, with the version and BUG of that image
  rm -f -> stops it (a crash: no goodbye)
What this cannot show: that the real Dockerfile builds, and that the real daemon, network and healthcheck behave."""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

STUB = os.environ.get("STUB_DIR", "")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def path(*parts):
    p = os.path.join(STUB, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def safe(ref):
    return re.sub(r"[^A-Za-z0-9._-]", "_", ref)


def cmd_build(args):
    ref, build_args, context = None, {}, None
    it = iter(args)
    for a in it:
        if a == "-t":
            ref = next(it)
        elif a == "--build-arg":
            k, _, v = next(it).partition("=")
            build_args[k] = v
        elif a == "-f":
            next(it)
        elif a.startswith("-"):
            continue
        else:
            context = a
    reqs = build_args.get("REQS", "requirements.txt")
    work = tempfile.mkdtemp()
    site = os.path.join(work, "usr/local/lib/python3.12/site-packages")
    os.makedirs(os.path.join(work, "app"))
    shutil.copy(os.path.join(context, "app.py"), os.path.join(work, "app", "app.py"))
    with open(os.path.join(work, "app", "BUILD"), "w") as fh:
        fh.write(json.dumps(build_args))
    with open(os.path.join(context, reqs)) as fh:
        for line in fh:
            if "==" in line:
                name, ver = line.strip().split("==")
                d = os.path.join(site, f"{name}-{ver}.dist-info")
                os.makedirs(d)
                with open(os.path.join(d, "METADATA"), "w") as m:
                    m.write(f"Metadata-Version: 2.1\nName: {name}\nVersion: {ver}\n")
    layer = path("images", safe(ref) + ".tar")
    with tarfile.open(layer, "w") as tar:
        for root, dirs, files in sorted(os.walk(work)):
            dirs.sort()
            for f in sorted(files):
                full = os.path.join(root, f)
                info = tar.gettarinfo(full, os.path.relpath(full, work))
                info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
                with open(full, "rb") as fh:
                    tar.addfile(info, fh)
    shutil.rmtree(work)
    with open(path("images", safe(ref) + ".json"), "w") as fh:
        json.dump({"version": build_args.get("APP_VERSION", "dev"), "bug": build_args.get("BUG", "")}, fh)
    print("sha256:" + "0" * 64)
    return 0


def cmd_push(args):
    ref = [a for a in args if not a.startswith("-")][0]
    layer = path("images", safe(ref) + ".tar")
    if not os.path.exists(layer):
        sys.stderr.write(f"An image with the name {ref} does not exist locally\n")
        return 1
    r = subprocess.run(["crane", "append", "-f", layer, "-t", ref], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        return 1
    digest = subprocess.run(["crane", "digest", ref], capture_output=True, text=True).stdout.strip()
    with open(path("images", safe(ref) + ".json")) as fh, open(path("digests", digest.replace(":", "_") + ".json"), "w") as out:
        out.write(fh.read())
    print(digest)
    return 0


def kill(name):
    p = path("run", name + ".pid")
    try:
        with open(p) as fh:
            os.killpg(int(fh.read()), signal.SIGKILL)
        time.sleep(0.2)
    except (OSError, ValueError):
        pass
    try:
        os.remove(p)
    except OSError:
        pass


def cmd_run(args):
    name, rest = None, []
    it = iter(args)
    for a in it:
        if a == "--name":
            name = next(it)
        elif a in ("--network", "--restart"):
            next(it)
        elif a.startswith("-"):
            continue
        else:
            rest.append(a)
    ref = rest[0]
    digest = ref.split("@")[1] if "@" in ref else subprocess.run(["crane", "digest", ref], capture_output=True, text=True).stdout.strip()
    try:
        with open(path("digests", digest.replace(":", "_") + ".json")) as fh:
            meta = json.load(fh)
    except OSError:
        sys.stderr.write(f"Unable to find image '{ref}'\n")
        return 125
    port = os.environ["STUB_PORT_" + name.split("-")[1].upper()]
    kill(name)
    proc = subprocess.Popen([sys.executable, os.path.join(REPO, "service", "app.py")],
                            env=dict(os.environ, PORT=port, APP_VERSION=meta["version"], BUG=meta["bug"]),
                            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(path("run", name + ".pid"), "w") as fh:
        fh.write(str(proc.pid))
    print("c" * 64)
    return 0


def main(argv):
    cmd, rest = (argv[0] if argv else ""), argv[1:]
    if cmd == "build":
        return cmd_build(rest)
    if cmd == "push":
        return cmd_push(rest)
    if cmd == "run":
        return cmd_run(rest)
    if cmd == "rm":
        for name in [a for a in rest if not a.startswith("-")]:
            kill(name)
        return 0
    if cmd == "compose" and "ps" in rest and "-q" in rest:
        print("\n".join(f"c{i}" for i in range(5)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
