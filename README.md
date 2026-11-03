# clang-cross

## Overview

Prebuilt x86-64 Linux cross toolchains for glibc and musl, using Clang/LLVM 21
from Bloomberg's experimental P2996 C++26 reflection branch. Each archive
combines target-prefixed drivers, LLD, LLVM tools, a sysroot, and C++ runtimes.
Choose the host label for the machine running the compiler; a musl **target**
does not make the host compiler static or compatible with every Linux system.

Contents: [Targets](#targets--component-matrix) · [Install](#install) ·
[Usage](#usage) · [Build](#build--automated-release) ·
[Layout](#repository-layout) · [License](#license)

## Targets & Component Matrix

| Item | Target / version / source |
| --- | --- |
| glibc target | `x86_64-unknown-linux-gnu`; sysroot and GCC libraries from `Central-Limit-Trading/gnu-cross` `v0.0.1` |
| musl target | `x86_64-unknown-linux-musl`; `Central-Limit-Trading/musl-cross` `v1.2.6-r1`, musl 1.2.6, expected base GCC 16.1.0 |
| Host labels, both targets | `amazonlinux2`, `alinux3`, `centos7`, `ubuntu22` |
| Clang / LLVM / LLD | 21.0.0, [`bloomberg/clang-p2996`](https://github.com/bloomberg/clang-p2996), branch `p2996` |
| compiler-rt / libc++ / libc++abi / libunwind | Built from the same LLVM revision; musl applies local compiler-rt patches |
| Version authority | [`targets/versions`](targets/versions), plus per-target [`config`](targets/) for runtime expectations |

The GNU base archive determines its glibc/GCC versions; a host suffix alone
is not a target-kernel compatibility guarantee. Enabled LLVM code-generation
backends do not imply additional packaged sysroots. MSan, DFSan, and MemProf
require an instrumented dependency closure; runtime presence alone is insufficient.

## Install

Example: the Ubuntu 22.04 host build targeting musl. Sidecars contain one bare digest.

```bash
TAG=v0.0.1
TARGET=x86_64-unknown-linux-musl
HOST=ubuntu22
ARCHIVE="$TARGET-$HOST.tar.xz"
BASE="https://github.com/trunkszi/clang-cross/releases/download/$TAG"
curl -fL --retry 3 -o "$ARCHIVE" "$BASE/$ARCHIVE"
curl -fL --retry 3 -o "$ARCHIVE.sha256" "$BASE/$ARCHIVE.sha256"
DIGEST=$(cat "$ARCHIVE.sha256")
[[ "$DIGEST" =~ ^[[:xdigit:]]{64}$ ]]
printf '%s  %s\n' "$DIGEST" "$ARCHIVE" | sha256sum --check -
INSTALL="$HOME/.local/opt/clang-cross/$TAG/$HOST"
mkdir -p "$INSTALL"
tar -xJf "$ARCHIVE" -C "$INSTALL"
export TOOLCHAIN="$INSTALL/$TARGET"
export PATH="$TOOLCHAIN/bin:$PATH"
"$TARGET-clang" --version
```

Keep the extracted directory intact. `v0.0.1` is mutable: retain the archive
SHA-256 and source revision when recording a reproducible dependency.

## Usage

The wrappers supply `--target` and a sysroot relative to their own directory.
GNU uses the same commands with `TARGET=x86_64-unknown-linux-gnu`.

```bash
"$TARGET-clang" hello.c -o hello
"$TARGET-clang++" -std=c++23 hello.cpp -o hello-cpp
"$TARGET-clang++" -std=c++23 -stdlib=libc++ hello.cpp -o hello-libcxx
"$TARGET-clang" -static hello.c -o hello-static
```

Minimal P2996 example:

```cpp
// reflection.cpp
int main() {
    constexpr auto reflected = ^^int;
    using Value = [:reflected:];
    Value answer = 42;
    return answer != 42;
}
```

```bash
"$TARGET-clang++" -std=c++26 -freflection reflection.cpp -o reflection
./reflection
rustup target add x86_64-unknown-linux-gnu x86_64-unknown-linux-musl
```

Rust `.cargo/config.toml` selects a target wrapper; Rust's standard library is separate:

```toml
[target.x86_64-unknown-linux-gnu]
linker = "x86_64-unknown-linux-gnu-clang"
[target.x86_64-unknown-linux-musl]
linker = "x86_64-unknown-linux-musl-clang"
```

Use `cargo build --target x86_64-unknown-linux-musl`. Rust may supply its own
musl CRT objects; selecting this linker alone does not replace that policy.

## Build & Automated Release

```bash
git clone --recursive https://github.com/trunkszi/clang-cross.git
cd clang-cross
./scripts/build-with-docker.sh x86_64-unknown-linux-musl ubuntu:22.04 ubuntu22
# Alternatively, on a configured host (installs packages unless skipped):
./scripts/make x86_64-unknown-linux-gnu --skip-deps
bash tests/run.sh
./scripts/sync-upstream.sh --check --summary-file upstream-summary.md
```

The daily 03:40 UTC workflow discovers the latest `bloomberg/clang-p2996`
`p2996` commit, validates patches with zero fuzz, and builds both targets on
all four host environments. If no source update is found, the scheduled build is skipped.
Successful updates replace `v0.0.1` only after all eight archives pass checks;
patch conflicts block publication and produce an Actions diagnostic summary.
Optional SMTP secrets enable failure email; without them no email is sent.
Manual runs can rebuild locked sources or reuse a successful, verified source run.
The LLVM cache key includes the source SHA and host environment.
CI saves host LLVM and the packaged toolchain before runtime verification,
including when verification fails. With a matching cache, changes to
`scripts/verify-toolchain.sh` rerun verification without rebuilding LLVM or runtimes.
Cache keys bind the container image digest, source lock, and build recipes.
Manual runs can set `llvm_seed_run_id` to recover host LLVM from a completed
run's successful musl artifact for the same host. This also accepts a failed
matrix run, but requires matching provenance, build recipes, and image digest;
GNU target runtimes still build when no complete toolchain cache is available.
Asset replacement is per file, not atomic: an upload failure can require repair,
while tag and source-ref promotion wait until uploads and release notes succeed.
Override `GITHUB_ORG=trunkszi MUSL_CROSS_VERSION=v0.0.1` to change base releases;
also set `EXPECTED_BASE_GCC_VERSION` to match the selected musl base archive.

## Repository Layout

- `llvm/`: pinned Bloomberg LLVM submodule.
- `scripts/`: source synchronization, dependencies, build, verification, and release.
- `targets/`: accepted versions, target runtime settings, and musl patches.
- `tests/`: offline configuration, failure-injection, and release contract suites.
- `.github/workflows/`: daily and manual build/publication workflow.

## License

There is no single top-level repository license declaration. LLVM's terms are
in [`llvm/llvm/LICENSE.TXT`](llvm/llvm/LICENSE.TXT); bundled GCC, libc, and other
upstream components retain their own licenses and notices.
