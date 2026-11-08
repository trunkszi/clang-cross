#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$PROJECT_ROOT"
EVENT=${GITHUB_EVENT_NAME:-workflow_dispatch}
TARGETS_INPUT=${TARGETS_INPUT:-x86_64-unknown-linux-gnu,x86_64-unknown-linux-musl}
CONTAINERS_INPUT=${CONTAINERS_INPUT:-all}
SOURCE_RUN_ID=${SOURCE_RUN_ID:-}
CHECK_UPSTREAM=${CHECK_UPSTREAM:-false}
[[ "$EVENT" == schedule || "$EVENT" == workflow_dispatch ]] || exit 64
[[ -z "$SOURCE_RUN_ID" || "$SOURCE_RUN_ID" =~ ^[0-9]+$ ]] || exit 64
[[ "$CHECK_UPSTREAM" == true || "$CHECK_UPSTREAM" == false ]] || exit 64
# Validate manual selections before discovery or accepting a candidate.
MATRIX=$(python3 scripts/matrix.py "$EVENT" "$TARGETS_INPUT" "$CONTAINERS_INPUT")
UPSTREAM_UPDATED=false
SHOULD_BUILD=true
if [[ -n "$SOURCE_RUN_ID" ]]; then
    [[ "$CHECK_UPSTREAM" == false ]] || { echo 'source_run_id cannot discover new upstream' >&2; exit 64; }
    SHOULD_BUILD=false
elif [[ "$EVENT" == schedule || "$CHECK_UPSTREAM" == true ]]; then
    result=$(./scripts/sync-upstream.sh --apply)
    printf '%s\n' "$result"
    if grep -qx 'upstream_updated=true' <<< "$result"; then
        UPSTREAM_UPDATED=true
        MATRIX=$(python3 scripts/matrix.py schedule '' '')
    elif [[ "$EVENT" == schedule ]]; then SHOULD_BUILD=false; fi
fi
FULL_MATRIX=$(MATRIX="$MATRIX" python3 -c 'import json,os;print(str(len(json.loads(os.environ["MATRIX"])["include"])==8).lower())')
{
    printf 'matrix=%s\nshould_build=%s\nupstream_updated=%s\nfull_matrix=%s\n' "$MATRIX" "$SHOULD_BUILD" "$UPSTREAM_UPDATED" "$FULL_MATRIX"
} | tee -a "${GITHUB_OUTPUT:-/dev/null}"
if [[ -z "$SOURCE_RUN_ID" ]]; then
    export UPSTREAM_UPDATED
    python3 scripts/release.py candidate --directory candidate
fi
