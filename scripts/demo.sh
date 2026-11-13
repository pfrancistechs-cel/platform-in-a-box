#!/usr/bin/env bash
# Scripted platform tour. Usage: ./scripts/demo.sh [ship|upgrade|policy|supply|rollback|outage|gameday]
# Keep the console (http://<EC2-PUBLIC-IP>:8081/) open while it runs.
#   ship      first release: tests, policy, build, SBOM, signature, gate, deploy -> v1 is live
#   upgrade   v2 goes through the same stages and is released as a canary 10% -> 50% -> 100%, with no failed request
#   policy    a sloppy Dockerfile is stopped at stage 2, before anything is built
#   supply    a signed image with a vulnerable library is stopped at the gate (stage 5)
#   rollback  a build with a hidden bug passes every check except the live canary: it rolls back by itself
#   outage    the live container is killed: the SLO alert fires; "heal" brings it back; the alert resolves
set -uo pipefail
. "$(dirname "$0")/lib.sh"

FAILURES=0
assert() { # LABEL COMMAND...
  local label="$1"; shift
  if "$@"; then ok "$label"; else fail "$label"; FAILURES=$((FAILURES + 1)); fi
}
finish() {
  if [ "$FAILURES" -eq 0 ]; then printf '\n\033[1;32mPASS\033[0m  %s\n\n' "$1"; else printf '\n\033[1;31mFAIL\033[0m  %s\n\n' "$1"; exit 1; fi
}
eq() { [ "$1" = "$2" ]; }
ne() { [ "$1" != "$2" ]; }
has() { [[ "$1" == *"$2"* ]]; }                                      # has "text" "needle"
live_slot() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["slot"])' "$STATE_DIR/live.json" 2>/dev/null; }
last_run_is() { [ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[-1]["result"])' "$RESULTS_DIR/runs.json")" = "$1" ]; }

SHIP_OUT=""; SHIP_RC=0
ship() { # ship.sh with its output shown and kept
  SHIP_OUT="$("$ROOT/scripts/ship.sh" "$@" 2>&1)"; SHIP_RC=$?
  printf '%s\n' "$SHIP_OUT" | sed 's/^/    /'
}

need_stack() {
  registry_up && router_up && prom_ready || die "The platform is not running. Run ./scripts/up.sh first."
  mkdir -p "$RESULTS_DIR" "$STATE_DIR"
}
ensure_live() { # something must be live, so "the live service is untouched" means something
  if [ -z "$(live_version)" ]; then say "nothing is live yet: shipping v1 first"; ship v1; [ "$SHIP_RC" -eq 0 ] || die "could not ship v1"; fi
}

scenario_ship() {
  log "SCENARIO ship: the first release through all six stages"
  ship v1
  assert "all six stages passed (exit 0)"            eq "$SHIP_RC" 0
  assert "v1 is live: the router answers with v1"    wait_until 20 live_is v1
  assert "the image in the registry is signed"       verify_sig "$(tag_digest v1)"
  assert "the run is recorded as LIVE"               last_run_is LIVE
  finish ship
}

scenario_upgrade() {
  log "SCENARIO upgrade: v2 as a canary, 10% -> 50% -> 100%"
  ensure_live
  local before; before="$(live_version)"
  say "live before: $before"
  ship v2
  assert "the release succeeded (exit 0)"            eq "$SHIP_RC" 0
  assert "the canary went through 10%, 50% and 100%" has "$SHIP_OUT" "100% to v2"
  assert "v2 is live now"                            wait_until 20 live_is v2
  assert "the old version is no longer the live one" ne "$(live_version)" "$before"
  assert "users saw (almost) no errors during the release" wait_until 40 error_ratio_below 0.02
  finish upgrade
}

scenario_policy() {
  log "SCENARIO policy: a sloppy Dockerfile"
  ensure_live
  local before; before="$(live_version)"
  ship v9-sloppy --bad-dockerfile
  assert "the pipeline stopped (exit 1)"             eq "$SHIP_RC" 1
  assert "it was stopped at stage 2 (policy)"        has "$SHIP_OUT" "stage 2"
  assert "the reason names the rules (DKR-001/002)"  has "$SHIP_OUT" "DKR-00"
  assert "nothing was built: the registry has no v9-sloppy" eq "$(tag_digest v9-sloppy)" ""
  assert "the live version did not change"           eq "$(live_version)" "$before"
  finish policy
}

scenario_supply() {
  log "SCENARIO supply: a signed image with a vulnerable library"
  ensure_live
  local before; before="$(live_version)"
  ship v9-deps --bad-deps
  assert "the pipeline stopped (exit 1)"             eq "$SHIP_RC" 1
  assert "it was stopped at stage 5 (the gate)"      has "$SHIP_OUT" "stage 5"
  assert "the reason is the SBOM (urllib3 1.26.4)"   has "$SHIP_OUT" "urllib3 1.26.4"
  assert "yet its signature is valid (a signature is not a safety check)" verify_sig "$(tag_digest v9-deps)"
  assert "the live version did not change"           eq "$(live_version)" "$before"
  finish supply
}

scenario_rollback() {
  log "SCENARIO rollback: a bug the tests cannot see (40% of requests fail)"
  ensure_live
  local before; before="$(live_version)"
  ship v3 --bug errors
  assert "stages 1-5 passed and the canary rolled back (exit 1)" eq "$SHIP_RC" 1
  assert "the canary was judged at 10% and rolled back" has "$SHIP_OUT" "ROLL BACK"
  assert "the run is recorded as ROLLED BACK"        last_run_is "ROLLED BACK"
  assert "the live version did not change"           eq "$(live_version)" "$before"
  assert "users are fine again (errors below 2%)"    wait_until 40 error_ratio_below 0.02
  finish rollback
}

scenario_outage() {
  log "SCENARIO outage: the live container dies"
  ensure_live
  assert "the SLO alert is quiet before we start"    wait_until 90 alert_is ErrorRatioHigh inactive
  local slot version; slot="$(live_slot)"; version="$(live_version)"
  say "killing plat-$slot (serving $version)"
  $DOCKER rm -f "plat-$slot" >/dev/null 2>&1
  assert "users now get errors"                      wait_until 20 live_is ""
  assert "Prometheus fires ErrorRatioHigh (within 90 s)" wait_until 90 alert_is ErrorRatioHigh firing
  say "healing: the last good version is started again from the registry"
  python3 "$ROOT/scripts/deploy.py" heal | sed 's/^/    /'
  assert "$version is serving again"                 wait_until 30 live_is "$version"
  assert "the alert resolves by itself (within 90 s)" wait_until 90 alert_is ErrorRatioHigh inactive
  finish outage
}

need_stack
case "${1:-gameday}" in
  ship)     scenario_ship ;;
  upgrade)  scenario_upgrade ;;
  policy)   scenario_policy ;;
  supply)   scenario_supply ;;
  rollback) scenario_rollback ;;
  outage)   scenario_outage ;;
  gameday)  scenario_ship; scenario_upgrade; scenario_policy; scenario_supply; scenario_rollback; scenario_outage
            printf '\033[1;32mALL SCENARIOS PASSED\033[0m\n' ;;
  *) echo "Usage: $0 [ship|upgrade|policy|supply|rollback|outage|gameday]" >&2; exit 2 ;;
esac
