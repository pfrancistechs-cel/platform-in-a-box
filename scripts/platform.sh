#!/usr/bin/env bash
# Day-2 commands.   ./scripts/platform.sh status | heal | alerts | traffic
#   status   what is live
#   heal     the live container is gone: start the last good version again (what an operator or an auto-healer does)
#   alerts   Prometheus alerts right now
#   traffic  one request through the router, showing which version answered
set -uo pipefail
. "$(dirname "$0")/lib.sh"
case "${1:-status}" in
  status)  python3 "$ROOT/scripts/deploy.py" status; echo "live version answering: $(live_version)" ;;
  heal)    python3 "$ROOT/scripts/deploy.py" heal ;;
  alerts)  echo "ErrorRatioHigh: $(alert_state ErrorRatioHigh)" ;;
  traffic) curl -s -D - -o /dev/null -m 3 "$ROUTER_URL/" | tr -d '\r' | grep -i -E '^(HTTP|x-version|x-slot)' ;;
  *) echo "Usage: $0 status|heal|alerts|traffic" >&2; exit 2 ;;
esac
