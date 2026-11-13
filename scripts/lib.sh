#!/usr/bin/env bash
# Shared helpers. Sourced by the other scripts, not run directly.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

# Use plain docker if the current user may talk to the daemon, otherwise sudo.
if docker info >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo docker"; fi
DC="$DOCKER compose"

REGISTRY="${REGISTRY:-localhost:5000}"            # docker treats localhost registries as safe over plain http
REPO="${REPO:-platform/shop}"
ROUTER_URL="${ROUTER_URL:-http://127.0.0.1:8080}"  # the public entry point, as seen from the instance
CONSOLE_URL="${CONSOLE_URL:-http://127.0.0.1:8081}"
PROM_URL="${PROM_URL:-http://127.0.0.1:9090}"
RESULTS_DIR="${RESULTS_DIR:-$ROOT/results}"
STATE_DIR="${STATE_DIR:-$ROOT/state}"
KEYS_DIR="${KEYS_DIR:-$ROOT/keys}"
SBOM_POLICY="${SBOM_POLICY:-$ROOT/platform/policy/sbom.json}"
DOCKER_POLICY_DIR="${DOCKER_POLICY_DIR:-$ROOT/platform/policy}"
EXPECTED_CONTAINERS=5                             # registry, router, prometheus, traffic, console
export REGISTRY REPO ROUTER_URL CONSOLE_URL PROM_URL RESULTS_DIR STATE_DIR KEYS_DIR DOCKER
export COSIGN_PASSWORD="${COSIGN_PASSWORD-}"      # the demo key has an empty password; never do that for real keys
export SYFT_REGISTRY_INSECURE_USE_HTTP=true       # our registry speaks http on localhost

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '  \033[1;32mok\033[0m   %s\n' "$*"; }
fail() { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
say()  { printf '    %s\n' "$*"; }

ensure_env() { # create .env on first run
  [ -f "$ROOT/.env" ] || cp "$ROOT/.env.example" "$ROOT/.env"
  set -a; . "$ROOT/.env"; set +a
}
ensure_env

public_ip() { # EC2 metadata (IMDSv2), then a public what-is-my-ip fallback
  local tok ip=""
  tok="$(curl -fsS -m 2 -X PUT http://169.254.169.254/latest/api/token \
         -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)"
  if [ -n "$tok" ]; then
    ip="$(curl -fsS -m 2 -H "X-aws-ec2-metadata-token: $tok" \
          http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  fi
  if [ -z "$ip" ]; then
    ip="$(curl -fsS -m 4 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
  fi
  echo "${ip:-<EC2-PUBLIC-IP>}"
}

http_code() { curl -s -o /dev/null -m 5 -w '%{http_code}' "$@" 2>/dev/null || true; }   # 000 = no connection

wait_http() { # LABEL URL [SECONDS]  (any 2xx)
  local label="$1" url="$2" secs="${3:-60}" i
  for ((i = 0; i < secs; i++)); do
    if curl -fsS -o /dev/null --max-time 2 "$url" 2>/dev/null; then ok "$label is up"; return 0; fi
    sleep 1
  done
  fail "$label did not answer at $url within ${secs}s"
  return 1
}

wait_until() { # SECONDS COMMAND...   (retries every second; the command is re-run each time)
  local secs="$1" i; shift
  for ((i = 0; i < secs; i++)); do
    "$@" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

# ---------------------------------------------------------------- conditions for wait_until (re-evaluated on every try)
registry_up() { [ "$(http_code "http://$REGISTRY/v2/")" = "200" ]; }
router_up()   { [ "$(http_code "$ROUTER_URL/healthz")" = "200" ]; }
console_up()  { [ "$(http_code "$CONSOLE_URL/healthz")" = "200" ]; }
prom_ready()  { [ "$(http_code "$PROM_URL/-/ready")" = "200" ]; }
live_version() { curl -s -m 3 -D - -o /dev/null "$ROUTER_URL/" | tr -d '\r' | awk 'tolower($1) == "x-version:" {print $2}'; }
live_is()     { [ "$(live_version)" = "$1" ]; }                          # VERSION
alert_state() { # NAME -> firing | pending | inactive
  curl -s -m 4 "$PROM_URL/api/v1/alerts" | python3 -c '
import json, sys
name = sys.argv[1]
try:
    alerts = json.load(sys.stdin)["data"]["alerts"]
except Exception:
    alerts = []
print(next((a["state"] for a in alerts if a["labels"].get("alertname") == name), "inactive"))' "$1"
}
alert_is()    { [ "$(alert_state "$1")" = "$2" ]; }                      # NAME STATE
error_ratio_below() { # LIMIT: the router has been healthy lately (uses the router's own 30 s numbers)
  local out; out="$(curl -s -m 4 "$ROUTER_URL/_router/state?since=$(($(date +%s) - 15))")"
  python3 -c '
import json, sys
s = json.loads(sys.argv[1])["stats"]
req = sum(v["requests"] for v in s.values()); err = sum(v["errors"] for v in s.values())
sys.exit(0 if req > 5 and err / req < float(sys.argv[2]) else 1)' "$out" "$1"
}

# ---------------------------------------------------------------- registry
MANIFEST_ACCEPT='application/vnd.oci.image.manifest.v1+json,application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.v2+json,application/vnd.docker.distribution.manifest.list.v2+json'
tag_digest() { # TAG -> sha256:... (empty when the tag does not exist)
  curl -s -I -m 10 -H "Accept: $MANIFEST_ACCEPT" "http://$REGISTRY/v2/$REPO/manifests/$1" |
    tr -d '\r' | awk 'tolower($1) == "docker-content-digest:" {print $2}'
}
ref_of() { echo "$REGISTRY/$REPO@$1"; }
short() { echo "${1#sha256:}" | cut -c1-12; }

# ---------------------------------------------------------------- signing keys
key_gen() { # NAME   (keys/NAME.key and keys/NAME.pub; the .key stays private)
  mkdir -p "$KEYS_DIR"
  [ -f "$KEYS_DIR/$1.key" ] && return 0
  local tmp; tmp="$(mktemp -d)"
  ( cd "$tmp" && cosign generate-key-pair >/dev/null 2>&1 ) || { rm -rf "$tmp"; return 1; }
  mv "$tmp/cosign.key" "$KEYS_DIR/$1.key"; mv "$tmp/cosign.pub" "$KEYS_DIR/$1.pub"
  rm -rf "$tmp"; chmod 600 "$KEYS_DIR/$1.key"
}
sign_image()  { cosign sign --key "$KEYS_DIR/release.key" --tlog-upload=false --allow-insecure-registry -y "$(ref_of "$1")" >/dev/null 2>&1; }   # DIGEST
attest_sbom() { cosign attest --key "$KEYS_DIR/release.key" --tlog-upload=false --allow-insecure-registry -y --type spdxjson --predicate "$2" "$(ref_of "$1")" >/dev/null 2>&1; }   # DIGEST SBOMFILE
verify_sig()  { cosign verify --key "$KEYS_DIR/release.pub" --insecure-ignore-tlog=true --allow-insecure-registry "$(ref_of "$1")" >/dev/null 2>&1; }
verify_att()  { cosign verify-attestation --key "$KEYS_DIR/release.pub" --type spdxjson --insecure-ignore-tlog=true --allow-insecure-registry "$(ref_of "$1")" >"$2" 2>/dev/null; }   # DIGEST OUTFILE
sbom_make() { # DIGEST -> prints the path of the SPDX json SBOM made from the image in the registry
  local out="$RESULTS_DIR/sbom-$(short "$1").spdx.json"
  mkdir -p "$RESULTS_DIR"
  syft "registry:$(ref_of "$1")" -q -o "spdx-json=$out" >/dev/null 2>&1 || return 1
  echo "$out"
}

# ---------------------------------------------------------------- run history (the console's pipeline table)
record_run() { # VERSION RESULT STAGE REASON [DIGEST]
  python3 - "$RESULTS_DIR" "$@" <<'PY'
import json, os, sys, time
results, version, result, stage, reason, *rest = sys.argv[1:]
path = os.path.join(results, "runs.json")
try:
    with open(path, encoding="utf-8") as fh:
        items = json.load(fh)
except (OSError, ValueError):
    items = []
items.append({"time": time.strftime("%H:%M:%S"), "version": version, "result": result, "stage": stage, "reason": reason,
              "digest": rest[0] if rest else ""})
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(items[-40:], fh)
os.replace(tmp, path)
PY
}
