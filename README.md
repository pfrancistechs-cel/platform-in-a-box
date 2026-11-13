# Platform in a Box (Project 20 of 20, capstone)

A golden-path delivery platform on **one EC2 instance**: every change passes tests, Dockerfile policy, a build, an SBOM and a signature, a release gate, and a canary deploy that rolls itself back. Prometheus watches the SLO. A console shows it all. The demo breaks things on purpose.

```
ship.sh:  1 tests -> 2 policy (conftest) -> 3 build+push -> 4 SBOM+sign (syft, cosign) -> 5 gate -> 6 canary (10/50/100)
live:     traffic (20 req/s) -> router :8080 -> plat-blue | plat-green      Prometheus: error ratio > 5% for 10 s = alert
console:  :8081
```

| Scenario | What it proves |
|---|---|
| `ship`, `upgrade` | The happy path, and a canary with no failed request |
| `policy` | A sloppy Dockerfile (`latest`, root) is stopped before anything is built |
| `supply` | A signed image with urllib3 1.26.4 is stopped by the SBOM policy at the gate |
| `rollback` | A hidden bug passes every check; the live canary catches it and rolls back |
| `outage` | Kill the live container: SLO alert fires, `platform.sh heal`, alert resolves |

Containers: registry, router, Prometheus, traffic, console (compose) plus `plat-blue`/`plat-green` started by the pipeline by digest. Security group: 22, 8080, 8081 from My IP; 5000 and 9090 localhost only.

## Quick start
See **STEPS.md** (EC2 from scratch, copy-paste) and **DEMO.md** (8-minute script). `DIAGRAM_PROMPT.md` makes the architecture picture.

```bash
chmod +x scripts/*.sh scripts/*.py
./scripts/setup.sh && ./scripts/preflight.sh
./scripts/up.sh && ./scripts/smoke.sh && ./scripts/demo.sh gameday
./scripts/destroy.sh
```

## Honest limits
- Signing uses a local key, no transparency log, no keyless identity. Production: KMS or keyless.
- No Alertmanager: the alert is visible in Prometheus and the console, nothing is paged.
- Canary judged on error rate and p95 from the router, not on business metrics.
- `heal` is run by hand; a real platform would have an orchestrator do it.
- Tests: real registry, cosign, syft, conftest, Prometheus, router, console, traffic and scripts run against a stub `docker`. Real Docker builds, compose start-up and container networking are verified only by `preflight.sh` on the instance.
