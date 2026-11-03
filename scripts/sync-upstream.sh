#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
set -a
source "${PROJECT_ROOT}/targets/versions"
set +a
exec python3 "${PROJECT_ROOT}/scripts/upstream.py" "$@"
