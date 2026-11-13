#!/usr/bin/env bash
# Health check of the running platform (run ./scripts/up.sh first). Ends with "All 13 checks passed."
set -uo pipefail
. "$(dirname "$0")/lib.sh"

TOTAL=0; BAD=0
check() { # LABEL COMMAND...
  local label="$1"; shift
  TOTAL=$((TOTAL + 1))
  if "$@" >/dev/null 2>&1; then ok "$label"; else fail "$label"; BAD=$((BAD + 1)); fi
}
five_running() { [ "$($DC ps --status running -q | wc -l | tr -d ' ')" = "$EXPECTED_CONTAINERS" ]; }
catalog_ok() { curl -fsS -m 5 "http://$REGISTRY/v2/_catalog" >/dev/null; }
metrics_flow() { # the router exports request counters, and Prometheus has scraped the router
  [[ "$(curl -s -m 5 "$ROUTER_URL/metrics")" == *"plat_requests_total"* ]] || return 1
  [[ "$(curl -s -m 5 "$PROM_URL/api/v1/targets")" == *'"health":"up"'* ]]
}
rules_loaded() { [[ "$(curl -s -m 5 "$PROM_URL/api/v1/rules")" == *ErrorRatioHigh* ]]; }
console_sees_all() { local s; s="$(curl -s -m 8 "$CONSOLE_URL/api/state")"; [[ "$s" == *'"alerts": ['* ]] && [[ "$s" == *'"slo"'* ]]; }
key_pair() { [ -s "$KEYS_DIR/release.key" ] && [ -s "$KEYS_DIR/release.pub" ]; }
policies_valid() { python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["deny"]' "$SBOM_POLICY" && [ -s "$DOCKER_POLICY_DIR/dockerfile/rules.rego" ]; }
policy_rejects_bad_dockerfile() { ! conftest test --parser dockerfile --policy "$DOCKER_POLICY_DIR" --namespace dockerfile --no-color "$ROOT/service/Dockerfile.bad"; }
policy_accepts_good_dockerfile() { conftest test --parser dockerfile --policy "$DOCKER_POLICY_DIR" --namespace dockerfile --no-color "$ROOT/service/Dockerfile"; }

check "Docker daemon reachable"                          $DOCKER info
check "all 5 platform containers are running"            five_running
check "the registry answers on $REGISTRY"                registry_up
check "the registry catalog can be read"                 catalog_ok
check "the router is up"                                 router_up
check "Prometheus is ready"                              prom_ready
check "the router exports metrics and Prometheus scrapes them" wait_until 40 metrics_flow
check "the SLO alert rule is loaded in Prometheus"       rules_loaded
check "the console is up and sees the router, Prometheus and the registry" wait_until 30 console_sees_all
check "the release signing key exists"                   key_pair
check "SBOM and Dockerfile policies are present"         policies_valid
check "the policy refuses the sloppy Dockerfile"         policy_rejects_bad_dockerfile
check "the policy accepts the golden-path Dockerfile"    policy_accepts_good_dockerfile

echo
if [ "$BAD" -eq 0 ]; then echo "All $TOTAL checks passed."; exit 0; fi
echo "$BAD of $TOTAL checks failed." >&2
exit 1
