#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 <archive-url> <destination-directory>" >&2
    exit 64
}

[[ $# -eq 2 ]] || usage

archive_url=$1
destination=$2
download_tmp=$(mktemp -d "${TMPDIR:-/tmp}/clang-cross-download.XXXXXX")
extract_stage=""
cleanup() { rm -rf -- "${download_tmp}"; if [[ -n "$extract_stage" ]]; then rm -rf -- "$extract_stage"; fi; }
trap cleanup EXIT

archive="${download_tmp}/toolchain.tar.xz"
sidecar="${archive}.sha256"

curl --fail --silent --show-error --location --retry 3 --retry-delay 5 \
    --connect-timeout 30 --max-time 600 --output "${archive}" "${archive_url}"
curl --fail --silent --show-error --location --retry 3 --retry-delay 5 \
    --connect-timeout 30 --max-time 600 --output "${sidecar}" "${archive_url}.sha256"

expected_hash=$(cat "${sidecar}")
if ! [[ "${expected_hash}" =~ ^[[:xdigit:]]{64}$ ]]; then
    echo "Error: invalid SHA-256 sidecar for ${archive_url}" >&2
    exit 1
fi

actual_hash=$(sha256sum "${archive}" | awk '{print $1}')
if [[ "${actual_hash,,}" != "${expected_hash,,}" ]]; then
    echo "Error: SHA-256 mismatch for ${archive_url}" >&2
    echo "Expected: ${expected_hash}" >&2
    echo "Actual:   ${actual_hash}" >&2
    exit 1
fi
echo "Checksum verification passed: ${archive_url}"

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
root=$(python3 "${script_dir}/check-archive.py" "$archive")
mkdir -p "$destination"
[[ ! -e "$destination/$root" && ! -L "$destination/$root" ]] || { echo "destination already exists: $root" >&2; exit 1; }
extract_stage=$(mktemp -d "$destination/.extract.XXXXXX")
tar -xJf "$archive" --no-same-owner -C "$extract_stage"
for directory in bin include lib "$root/sysroot"; do
    [[ -d "$extract_stage/$root/$directory" ]] || { echo "incomplete extracted toolchain: $directory" >&2; exit 1; }
done
mv -- "$extract_stage/$root" "$destination/$root"
echo "Successfully downloaded and extracted toolchain"
