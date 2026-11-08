#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
temp_base=$(cd "${TMPDIR:-/tmp}" && pwd -P)
case "$temp_base" in "$root"|"$root"/*) echo 'Test TMPDIR must be outside the repository' >&2; exit 64 ;; esac
test_tmp=$(mktemp -d "${temp_base}/clang-cross-tests.XXXXXX")
trap 'rm -rf -- "${test_tmp:?}"' EXIT
export TMPDIR="$test_tmp"
export PYTHONDONTWRITEBYTECODE=1
for test in test_config_and_patches test_scripts_contract test_cache_contract test_upstream_sync test_release_workflow; do
    bash "${root}/tests/${test}.sh"
done
echo 'All local contract suites: PASS'
