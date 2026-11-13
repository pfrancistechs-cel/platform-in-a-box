# STEPS: Platform in a Box (tests, policy, build, sign, gate, canary, SLO alert)

## Prerequisites (set these up BEFORE step 1)

| Need | Details |
|---|---|
| **AWS account** | Any account that can launch EC2. No IAM role, no other AWS service. One region for everything. |
| **Instance type** | `m7i-flex.large` (2 vCPU, 8 GiB). The whole platform needs about 1 GiB. |
| **OS image (AMI)** | Ubuntu Server 24.04 LTS (64-bit x86). 22.04 also works. |
| **Disk** | 20 GiB gp3. |
| **Key pair** | An RSA `.pem` key pair created in EC2 (step 0a). |
| **Security group** | Created in step 0b. **Inbound: exactly three rules, all with source My IP: SSH TCP 22; Custom TCP 8080 (the live service); Custom TCP 8081 (the console).** **Outbound: leave the default (All traffic, 0.0.0.0/0)**, because the instance pulls images from Docker Hub and the setup script downloads cosign, syft and conftest from GitHub. The registry (5000) and Prometheus (9090) are bound to localhost and need no rule. |
| **Code on the instance** | Option A: private GitHub repo plus a fine-grained token (Contents: Read) as the `git clone` password. Option B: copy the zip with `scp` (step 2). |
| **Accounts / tokens** | None. |
| **On your laptop** | A terminal with `ssh`/`scp` and a web browser. **No SSH tunnel is needed:** open `http://<PUBLIC-IP>:8081/` directly. |

**Time:** about 15 minutes of setup the first time (installs, image pulls, one full run of every scenario in preflight), about 8 minutes for the tour. **Cost:** about $0.10/hour at your quoted rate, plus pennies for the disk. Do step 11 when finished.

---

## Step 0: Create the key pair, security group and instance (AWS console)

**0a. Key pair.** EC2 > Network & Security > Key pairs > **Create key pair**. Name `plat-lab-key`, type **RSA**, format **.pem**. The file downloads once; keep it safe. Lock down its permissions:

```bash
# Linux / macOS
chmod 400 ~/Downloads/plat-lab-key.pem
```
```powershell
# Windows PowerShell (OpenSSH)
icacls "$HOME\Downloads\plat-lab-key.pem" /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

**0b. Security group.** EC2 > Network & Security > Security Groups > **Create security group**.
- Name: `plat-lab-sg`, Description: `SSH and demo pages from my IP`, VPC: the default VPC.
- **Inbound rules > Add rule** (three times; Type `SSH` first, then `Custom TCP` twice; Protocol `TCP`; **Source `My IP`** every time):
  1. Port range `22` (SSH)
  2. Port range `8080` (the live service)
  3. Port range `8081` (the platform console)
- **Outbound rules:** leave the default rule (`All traffic`, `0.0.0.0/0`).
- Click **Create security group**.

> Showing someone on another network? "My IP" blocks them. Share your screen, or add their public IP followed by `/32` to the 8080 and 8081 rules. Never use `0.0.0.0/0`.

**0c. Launch the instance.** EC2 > Instances > **Launch instances**.
- Name: `plat-lab-box`
- OS Image: **Ubuntu Server 24.04 LTS**, **64-bit (x86)**
- Instance type: `m7i-flex.large`
- Key pair: `plat-lab-key`
- Network settings > **Edit**: Auto-assign public IP **Enable**; **Select existing security group** > `plat-lab-sg`
- Storage: **20 GiB**, `gp3`
- Advanced details > IAM instance profile: **No IAM role**
- **Launch instance**, wait for `Running` and `2/2 passed`, copy the **Public IPv4 address** (`<PUBLIC-IP>` below).

> If your network changes, SSH and the pages time out: edit **all three** inbound rules and set Source to My IP again. Stop/start also changes the public IP.

## Step 1: Connect (from your laptop)

```bash
ssh -i ~/Downloads/plat-lab-key.pem ubuntu@<PUBLIC-IP>
```
Type `yes` at the fingerprint prompt. Everything below runs **on the instance**.

## Step 2: Update packages and get the code

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git unzip
```

**Option A (private GitHub repo):**
```bash
git clone https://github.com/<you>/platform-in-a-box.git
cd platform-in-a-box
```
Username = your GitHub username, password = the fine-grained token.

**Option B (no GitHub). From your laptop, in the folder holding the zip:**
```bash
scp -i ~/Downloads/plat-lab-key.pem platform-in-a-box.zip ubuntu@<PUBLIC-IP>:~
```
Then on the instance:
```bash
unzip platform-in-a-box.zip
cd platform-in-a-box
```

## Step 3: Make the scripts executable (do not skip)

Zipped or uploaded files often lose the executable flag, which gives `Permission denied`.

```bash
chmod +x scripts/*.sh scripts/*.py
ls -l scripts/        # every .sh and .py line should start with -rwxr-xr-x
```

## Step 4: Install Docker, cosign, syft and conftest

```bash
./scripts/setup.sh
```
Runs `apt update`/`upgrade`, installs base tools, Docker Engine and Compose v2 from Docker's repo (fallback: Ubuntu's `docker.io`), then downloads **cosign 2.4.1**, **syft 1.14.0** and **conftest 0.56.0** from GitHub into `/usr/local/bin`. Takes 3-5 minutes and ends with `Setup complete.` If it says to log out and in for the docker group, either do that or keep going (the scripts use `sudo` by themselves).

## Step 5: Preflight (run well before the demo, and do not skip it)

```bash
./scripts/preflight.sh
```
Checks Docker, the three tools, disk (6 GB), memory (3 GB), ports 5000, 8080, 8081 and 9090, and that Docker Hub is reachable; pulls `python:3.12-slim`, `registry:2` and `prom/prometheus:v2.54.1`; **starts the platform**, runs `smoke.sh`, then runs **every demo scenario once** (`demo.sh gameday`, about 8 minutes) and the unit tests. Every line says `ok` and the last line is `Preflight passed. The whole platform works on this instance.` The platform is left running.

## Step 6: Start the stack

```bash
./scripts/up.sh
```
Starts the registry, router, Prometheus, traffic generator and console (the first start builds one small image), creates the signing key, and prints:

```
Platform console : http://<PUBLIC-IP>:8081/
The live service : http://<PUBLIC-IP>:8080/    <- answers 503 until the first release goes live
```

## Step 7: Health check

```bash
./scripts/smoke.sh
```
Every line says `ok` and it ends with **`All 13 checks passed.`**

## Step 8: Open the console (on your laptop)

`http://<PUBLIC-IP>:8081/`: **Live service** (version, requests per second, error rate, p95), **SLO** (99% target, availability, active alerts), **Pipeline runs** (every release attempt and its result: LIVE, BLOCKED, ROLLED BACK) and **Registry** (each image tag with signed and SBOM flags). **Keep this tab open.**

## Step 9: Run the tour (on the instance)

```bash
./scripts/demo.sh gameday          # all six scenarios: about 8 minutes
```
Or one at a time:
```bash
./scripts/demo.sh ship             # first release: six stages, v1 goes live
./scripts/demo.sh upgrade          # v2 as a canary 10% -> 50% -> 100%, no failed request
./scripts/demo.sh policy           # sloppy Dockerfile stopped at stage 2
./scripts/demo.sh supply           # vulnerable library stopped at the gate (stage 5)
./scripts/demo.sh rollback         # hidden bug: the canary rolls itself back
./scripts/demo.sh outage           # kill the live container: alert fires, heal, alert resolves
```
Each ends with a green `PASS`. By hand:
```bash
./scripts/ship.sh v3                       # a normal release
./scripts/ship.sh v4 --bug errors          # a build that fails 40% of requests
./scripts/platform.sh status               # what is live
./scripts/platform.sh alerts               # active Prometheus alerts
```

## Step 10: Unit tests (optional)

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```
Ends with `OK`. On the instance the service, router, release-judge and repo tests run; the tests that need `registry`, `crane` and `prometheus` binaries are skipped (this guide does not install them).

## Step 11: Clean up, then terminate the instance

```bash
./scripts/destroy.sh
```
Removes all containers (including the live app), the registry volume (every image and signature), built images, keys and results. In the AWS console: EC2 > Instances > `plat-lab-box` > **Instance state > Terminate instance**. Optionally delete `plat-lab-sg` and `plat-lab-key`.

---

## What you should see

| Step | Result |
|---|---|
| 4 | Docker, cosign, syft and conftest versions, then `Setup complete.` |
| 5 | All `ok`, then `Preflight passed.` |
| 6 | The console and service URLs with your public IP |
| 7 | `All 13 checks passed.` |
| 8 | Live service: nothing deployed yet; the SLO and run panels fill as you ship |
| 9 | `ship`: all six stages, `PASS ship`. `upgrade`: "100% to v2", `PASS`. `policy`: DKR-001/002 listed, `PASS`. `supply`: urllib3 1.26.4 denied, `PASS`. `rollback`: "rolled back", `PASS`. `outage`: alert firing, healed, resolved, `PASS`. Then `ALL SCENARIOS PASSED` |
| 10 | `OK` |

## Risky dependencies and fallbacks

| Dependency | Risk | Fallback |
|---|---|---|
| Docker Hub (`python:3.12-slim`, `registry:2`, `prom/prometheus`) | Rate limit on anonymous pulls | `sudo docker login` with a free account, then re-run preflight; images stay cached |
| GitHub releases (cosign, syft, conftest) | Blocked or slow | Outbound rule must be All traffic; re-run `./scripts/setup.sh` |
| `pip install` during the image build (PyPI) | Blocked or slow | Outbound rule must be All traffic; re-run `./scripts/ship.sh v1` |
| Local signing key, no transparency log | Not production practice | Use KMS or keyless signing in production (stated in the demo) |
| Do not run `destroy.sh` before the demo | It deletes the registry and the keys | Re-run `./scripts/up.sh` afterwards |
| MinIO, LocalStack, S3 | **Not used** | none needed |

## If something goes wrong

| Symptom | Fix |
|---|---|
| SSH: `Connection timed out` | Your IP changed. Edit `plat-lab-sg`, inbound rule 22, Source My IP. Check the instance is Running with a public IP. |
| Console or service never loads | (1) Use `http://` and ports 8081 (console) and 8080 (service). (2) The rules need Source My IP and your current IP. (3) Check the instance's current public IP. (4) On the instance `curl -i http://localhost:8081/healthz` should return `200`; if so, it is the security group. |
| Testing the public IP from the instance hangs | Expected. Use `localhost` on the instance. |
| The service on 8080 answers 503 | Nothing is deployed yet (or the release was blocked). Run `./scripts/ship.sh v1`. |
| SSH: `Permission denied (publickey)` | User `ubuntu`, the right `.pem`, `chmod 400`. |
| `./scripts/x.sh: Permission denied` | `chmod +x scripts/*.sh scripts/*.py` (step 3), or `bash scripts/x.sh`. |
| `bad interpreter: /usr/bin/env bash^M` | `sed -i 's/\r$//' scripts/*.sh scripts/*.py` |
| `cosign`, `syft` or `conftest: command not found` | Run `./scripts/setup.sh` (step 4). |
| Preflight: `Port NNNN is free` fails | `sudo ss -ltnp \| grep ':NNNN '`; stop that process, or `./scripts/destroy.sh` if it is an old run. |
| Preflight: a pull fails | Docker Hub rate limit: `sudo docker login`, re-run. |
| `up.sh`: a part does not answer | The message names the command: `sudo docker logs --tail 30 plat-registry` (or `plat-router`, `plat-prometheus`). |
| `smoke.sh`: `the router exports metrics and Prometheus scrapes them` fails | Prometheus needs about 15 s after start. Wait and re-run smoke; check `sudo docker logs plat-prometheus`. |
| `ship.sh`: stage 3 fails | Usually `pip install` could not reach PyPI (outbound rule) or Docker Hub rate-limited the base image. Read the red lines and re-run. |
| `ship.sh`: stage 4 or 5 fails with a key error | The key is missing: run `./scripts/up.sh` (it creates `keys/release.key`). |
| `demo.sh rollback`: v3 went live | The instance was slow to send enough traffic. Run `./scripts/platform.sh traffic` to check the generator (20 req/s), then retry. |
| `demo.sh outage`: the alert does not fire | Prometheus evaluates every 5 s and the alert needs 10 s of errors; wait 30 s and check `./scripts/platform.sh alerts`. |
| `demo.sh`: `The platform is not running` | `./scripts/up.sh` |
| Disk full | `sudo docker system prune -af` (removes cached images; the next start pulls them again) |
