#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
exec python3 "${root}/tests/upstream_sync.py"
