#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
source "${root}/targets/versions"
[[ $(git -C "${root}/llvm" rev-parse HEAD) == "$LLVM_SOURCE_REVISION" ]]
[[ -z $(git -C "${root}/llvm" status --porcelain) ]]
temp_base=$(cd "${TMPDIR:-/tmp}" && pwd -P)
case "$temp_base" in "$root"|"$root"/*) echo 'Test TMPDIR must be outside the repository' >&2; exit 64 ;; esac
work=$(mktemp -d "${temp_base}/clang-cross-patches.XXXXXX")
trap 'rm -rf -- "$work"' EXIT
python3 - "$root" <<'PY'
import hashlib, pathlib, re, sys
root = pathlib.Path(sys.argv[1])
patches = root / "targets/x86_64-unknown-linux-musl/patches"
text = (patches / "README.md").read_text()
section = re.search(r"^## Local SHA-512.*?\n```\n(.*?)```", text, re.M | re.S)
if section is None:
    raise SystemExit("Local SHA-512 provenance section missing")
records = dict((name, digest) for digest, name in
               re.findall(r"^([0-9a-f]{128})  (\S+\.patch)$", section[1], re.M))
files = sorted(patches.glob("*.patch"))
if set(records) != {p.name for p in files}:
    raise SystemExit("Local SHA-512 patch set mismatch")
for patch in files:
    if hashlib.sha512(patch.read_bytes()).hexdigest() != records[patch.name]:
        raise SystemExit("Local SHA-512 mismatch: " + patch.name)
print("local patch SHA-512 provenance: PASS")
PY
# Apply in order in an isolated copy so dependent patches are checked together.
cp -a "${root}/llvm/compiler-rt" "$work/"
for p in "${root}"/targets/x86_64-unknown-linux-musl/patches/*.patch; do
    patch --fuzz=0 --batch --forward -d "$work" -Np1 --dry-run < "$p"
    patch --fuzz=0 --batch --forward -d "$work" -Np1 < "$p"
done
echo 'configuration and patch preflight: PASS'
