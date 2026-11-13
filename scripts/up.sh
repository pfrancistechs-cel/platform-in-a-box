#!/usr/bin/env bash
# Starts the platform (registry, router, Prometheus, traffic, console), makes the signing key, and waits until everything answers.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
mkdir -p "$RESULTS_DIR" "$STATE_DIR"; chmod 755 "$RESULTS_DIR" "$STATE_DIR"
for tool in cosign syft conftest; do command -v "$tool" >/dev/null || die "$tool is missing. Run ./scripts/setup.sh first."; done
[ -f "$STATE_DIR/router.json" ] || echo '{"active": null, "candidate": null, "weight": 0}' > "$STATE_DIR/router.json"
chmod 644 "$STATE_DIR/router.json"

log "Starting the platform (first start pulls two images and builds one small one, 1-3 minutes)"
$DC up -d --build

log "Waiting for every part to answer"
wait_until 90 registry_up && ok "the registry answers on $REGISTRY" || die "Run: $DOCKER logs --tail 30 plat-registry"
wait_until 60 router_up   && ok "the router answers on $ROUTER_URL" || die "Run: $DOCKER logs --tail 30 plat-router"
wait_until 90 prom_ready  && ok "Prometheus is ready" || die "Run: $DOCKER logs --tail 30 plat-prometheus"
wait_http "the console" "$CONSOLE_URL/healthz" 60 || die "Run: $DOCKER logs --tail 30 plat-console"
key_gen release && ok "the release signing key exists (keys/release.key, keys/release.pub)"

IP="$(public_ip)"
cat <<EOF

Ready. Open this in your browser (from the laptop allowed by the security group):
  Platform console : http://${IP}:8081/    <- keep it open
  The live service : http://${IP}:8080/    <- answers 503 until the first release goes live

Next: ./scripts/smoke.sh   then   ./scripts/demo.sh gameday
By hand: ./scripts/ship.sh v1
EOF
