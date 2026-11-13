#!/usr/bin/env bash
# The golden path: what happens when a developer ships a version. Six stages; the first one that says no stops the release,
# and the live service is not touched.
#   ./scripts/ship.sh VERSION [--bug errors|slow] [--bad-deps] [--bad-dockerfile]
#     --bug errors|slow  ship a build with a bug that the unit tests cannot see (the canary will)
#     --bad-deps         ship a build with a vulnerable library (the SBOM gate will refuse it)
#     --bad-dockerfile   ship with a sloppy Dockerfile (the policy check will refuse it)
# Exit: 0 live, 1 blocked or rolled back.
set -uo pipefail
. "$(dirname "$0")/lib.sh"

VERSION="${1:-}"; shift || true
[ -n "$VERSION" ] || die "Usage: ship.sh VERSION [--bug errors|slow] [--bad-deps] [--bad-dockerfile]"
BUG=""; REQS="requirements.txt"; DOCKERFILE="Dockerfile"
while [ $# -gt 0 ]; do
  case "$1" in
    --bug)            BUG="${2:?--bug needs errors or slow}"; shift ;;
    --bad-deps)       REQS="requirements-bad.txt" ;;
    --bad-dockerfile) DOCKERFILE="Dockerfile.bad" ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done
DIGEST=""
mkdir -p "$RESULTS_DIR" "$STATE_DIR"

blocked() { # STAGE_NUMBER NAME REASON
  printf '  \033[1;31mBLOCKED\033[0m at stage %s (%s): %s\n' "$1" "$2" "$3"
  printf '  the live service was not touched\n'
  record_run "$VERSION" BLOCKED "$1 $2" "$3" "$DIGEST"
  exit 1
}
stage() { printf '\033[1;34m[%s/6]\033[0m %s\n' "$1" "$2"; }

log "Shipping $VERSION"

stage 1 "unit tests"
test_out="$(cd "$ROOT/service" && python3 -m unittest discover -s tests 2>&1)" || blocked 1 "unit tests" "$(tail -n 3 <<<"$test_out" | tr '\n' ' ')"
ok "$(grep -E '^Ran ' <<<"$test_out") ... OK"

stage 2 "policy check on the Dockerfile (conftest)"
pol_out="$(conftest test --parser dockerfile --policy "$DOCKER_POLICY_DIR" --namespace dockerfile --no-color "$ROOT/service/$DOCKERFILE" 2>&1)" || {
  reason="$(grep -E 'FAIL' <<<"$pol_out" | sed -E 's/^ *FAIL - [^ ]+ - (dockerfile - )?//' | tr '\n' ';' | sed 's/;$//')"
  [ -n "$reason" ] || reason="$pol_out"
  blocked 2 "policy" "$reason"
}
ok "Dockerfile follows the platform's rules (pinned base image, non-root user, no secrets)"

stage 3 "build and push the image"
key_gen release || die "could not make the signing key (is cosign installed? ./scripts/setup.sh)"
$DOCKER build -q -t "$REGISTRY/$REPO:$VERSION" --build-arg "APP_VERSION=$VERSION" --build-arg "BUG=$BUG" --build-arg "REQS=$REQS" \
  -f "$ROOT/service/$DOCKERFILE" "$ROOT/service" >/dev/null 2>&1 || blocked 3 "build" "docker build failed (run it by hand to see why: sudo docker build -f service/$DOCKERFILE service)"
$DOCKER push -q "$REGISTRY/$REPO:$VERSION" >/dev/null 2>&1 || blocked 3 "build" "docker push to $REGISTRY failed"
DIGEST="$(tag_digest "$VERSION")"
[ -n "$DIGEST" ] || blocked 3 "build" "the registry does not know $VERSION after the push"
ok "$REGISTRY/$REPO:$VERSION is $(short "$DIGEST")"

stage 4 "SBOM (syft), signature and attestation (cosign)"
SBOM="$(sbom_make "$DIGEST")" || blocked 4 "sbom" "syft could not read the image"
sign_image "$DIGEST" || blocked 4 "sign" "cosign could not sign the image"
attest_sbom "$DIGEST" "$SBOM" || blocked 4 "sign" "cosign could not attest the SBOM"
ok "SBOM made, image signed, SBOM attached to the image digest"

stage 5 "release gate: verify the signature, the attestation and the SBOM policy"
verify_sig "$DIGEST" || blocked 5 "gate" "the signature does not verify with the release key"
ATT="$RESULTS_DIR/attestation.tmp"
verify_att "$DIGEST" "$ATT" || blocked 5 "gate" "no valid SBOM attestation"
sbom_rc=0
sbom_out="$(python3 "$ROOT/scripts/sbom_check.py" --attestation "$ATT" --policy "$SBOM_POLICY")" || sbom_rc=$?
rm -f "$ATT"
[ "$sbom_rc" -eq 0 ] || blocked 5 "gate" "SBOM: $(tr '\n' ';' <<<"$sbom_out" | sed 's/;$//')"
ok "signature verified, SBOM attested and clean"

stage 6 "release: canary with automatic rollback"
python3 "$ROOT/scripts/deploy.py" deploy "$(ref_of "$DIGEST")" "$VERSION" "$DIGEST" | sed 's/^/    /'
rc=${PIPESTATUS[0]}
case "$rc" in
  0) printf '  \033[1;32mLIVE\033[0m %s\n' "$VERSION"; record_run "$VERSION" LIVE "6 release" "all six stages passed" "$DIGEST"; exit 0 ;;
  1) printf '  \033[1;33mROLLED BACK\033[0m %s (the canary looked bad)\n' "$VERSION"
     record_run "$VERSION" "ROLLED BACK" "6 release" "the canary failed its checks; the previous version is still live" "$DIGEST"; exit 1 ;;
  *) blocked 6 "release" "the new version did not start" ;;
esac
