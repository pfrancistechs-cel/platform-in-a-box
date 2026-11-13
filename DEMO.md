# Demo: Platform in a Box (about 8 minutes)

Open `http://<EC2-PUBLIC-IP>:8081/` (the console) and keep it visible: live version, SLO, pipeline runs, registry.

**1. The idea (30 s).** "One golden path: **tests, policy, build, SBOM and signature, gate, canary**. Every unsafe change is stopped by a machine, at the earliest stage that can see the problem."

**2. Ship v1 and v2 (2 min).**
```bash
./scripts/demo.sh ship
./scripts/demo.sh upgrade
```
v1 goes live. v2 is a canary: 10%, 50%, 100%, with no failed request.

**3. Stopped before the build (1 min).**
```bash
./scripts/demo.sh policy
```
A sloppy Dockerfile (`latest`, root) is refused at stage 2. Nothing was built.

**4. Stopped at the gate (1 min).**
```bash
./scripts/demo.sh supply
```
Signed, but urllib3 1.26.4 inside: the SBOM policy says no. The old version keeps serving.

**5. Rolled back by itself (1.5 min).**
```bash
./scripts/demo.sh rollback
```
A build with a hidden bug passes every check. The 10% canary shows errors and is rolled back.

**6. Outage (1.5 min).**
```bash
./scripts/demo.sh outage
```
Kill the live container: the **ErrorRatioHigh** alert fires, `platform.sh heal` restarts it, the alert resolves.

**7. Close (30 s).** "Earlier checks are cheaper. The live canary catches what tests cannot. Local keys here; use KMS or keyless in production." All at once: `./scripts/demo.sh gameday`. Then `./scripts/destroy.sh` and terminate the instance.
