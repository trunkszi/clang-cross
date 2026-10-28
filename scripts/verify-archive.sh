#!/usr/bin/env bash
set -euo pipefail
[[ $# == 2 ]] || { echo "Usage: $0 <target> <archive>" >&2; exit 64; }
target=$1
archive=$(realpath "$2")
case "$target" in x86_64-unknown-linux-gnu|x86_64-unknown-linux-musl) ;; *) exit 64 ;; esac
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
source "$script_dir/make"
source "$PROJECT_ROOT/targets/$target/config"
verify_file_checksum "$archive"
archive_root=$(python3 "$script_dir/check-archive.py" "$archive")
[[ "$archive_root" == "$target" ]] || { echo 'cached target mismatch' >&2; exit 1; }
verify_stage=$(mktemp -d "${TMPDIR:-/tmp}/clang-cross-cached.XXXXXX")
cleanup() {
    chmod -R u+w "$verify_stage"
    rm -rf -- "$verify_stage"
}
trap cleanup EXIT
tar -xJf "$archive" --no-same-owner -C "$verify_stage"
echo "Reusing packaged toolchain; running current verification without compilation"
bash "$script_dir/verify-toolchain.sh" "$target" "$verify_stage/$target" "$LLVM_VERSION" \
    "${EXPECTED_MUSL_VERSION:--}" "${EXPECTED_MUSL_LOADER:--}"
