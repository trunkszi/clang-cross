# clang-cross

`clang-cross` provides prebuilt, self-contained x86-64 Linux C/C++ cross-compilation
toolchains targeting both GNU/glibc (`x86_64-unknown-linux-gnu`) and musl
(`x86_64-unknown-linux-musl`), built from Bloomberg's experimental P2996 C++26
reflection branch ([`bloomberg/clang-p2996`](https://github.com/bloomberg/clang-p2996),
branch `p2996`, Clang/LLVM 21.0.0). Each archive combines target-prefixed Clang
drivers, LLD, LLVM binary utilities, a target sysroot with GCC runtime support,
LLVM C++ and sanitizer runtimes (`compiler-rt`, `libc++`, `libc++abi`,
`libunwind`), and bundled environment helpers (`setvars.sh`,
`bin/clang-cross-run`, and `toolchain.cmake`).

Each archive is indexed by two orthogonal compatibility boundaries: the
**compiler host profile** (`centos7`, `amazonlinux2`, `alinux3`, or `ubuntu22`)
and the **target C library** (`glibc` or `musl`). Choose the host label matching
or older than the Linux machine running the compiler; a musl **target**
determines the target sysroot and runtime libraries linked into produced
binaries, and does not make the host compiler executable static or compatible
with every Linux system.

## Contents

- [Installation](#installation)
- [Usage](#usage)
- [Environment and runtime helpers](#environment-and-runtime-helpers)
- [Supported targets and host profiles](#supported-targets-and-host-profiles)
- [Release pipeline](#release-pipeline)
- [Building locally](#building-locally)
- [Repository layout](#repository-layout)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## Installation

Select a target triple (`x86_64-unknown-linux-gnu` or
`x86_64-unknown-linux-musl`) and host profile (`centos7`, `amazonlinux2`,
`alinux3`, or `ubuntu22`), verify the single-digest `.sha256` sidecar before
extracting, and source `setvars.sh`:

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

INSTALL="$HOME/.local/share/clang-cross/$TAG/$HOST"
mkdir -p "$INSTALL"
tar -xJf "$ARCHIVE" -C "$INSTALL"
export TOOLCHAIN="$INSTALL/$TARGET"
source "$TOOLCHAIN/setvars.sh"
"$TARGET-clang" --version
```

Keep the extracted `<target>/` directory intact. `v0.0.1` is mutable: retain the
archive SHA-256 and source revision (`llvm_sha` in `<target>-<host>.tar.xz.provenance.json`)
when recording a reproducible dependency.

### Release assets per cell

| Asset | Format | Purpose |
| --- | --- | --- |
| `<target>-<host>.tar.xz` | XZ-compressed tar archive | Relocatable `<target>/` toolchain root |
| `<target>-<host>.tar.xz.sha256` | Single 64-hex digest + newline (`65` bytes) | Checksum verified before extraction and release publication |
| `<target>-<host>.tar.xz.provenance.json` | JSON provenance receipt (`clang-cross-cell/1`) | Binds `base_sha`, `llvm_sha`, `versions_sha256`, `target`, `host`, and `archive_sha256` |

### Extracted `<target>/` layout

| Path inside `<target>/` | Description |
| --- | --- |
| `bin/<target>-clang`, `bin/<target>-clang++` | Target-prefixed Clang/Clang++ wrappers supplying `--target` and `--sysroot` relative to their own directory |
| `bin/<target>-ar`, `bin/<target>-nm`, `bin/<target>-ranlib`, `bin/<target>-strip`, `bin/<target>-objcopy`, `bin/<target>-objdump` | Target-prefixed LLVM binary utilities (`llvm-ar`, `llvm-nm`, `llvm-ranlib`, `llvm-strip`, `llvm-objcopy`, `llvm-objdump`) |
| `bin/clang-cross-run` | Target binary runner executing via the bundled sysroot dynamic loader |
| `setvars.sh` | Sourceable `bash`/`zsh` environment activation and deactivation script |
| `toolchain.cmake` | Relocatable CMake cross-compilation toolchain file |
| `bin/clang`, `bin/clang++`, `bin/ld.lld`, `bin/llvm-readelf` | Underlying Clang/LLVM 21.0.0 compiler, LLD linker, and ELF inspection binaries |
| `lib/clang/21/` | Clang resource headers and target `compiler-rt` builtins/sanitizer libraries |
| `<target>/sysroot/` | Target libc headers/libraries plus bundled `libc++`, `libc++abi`, `libunwind`, `libstdc++`, and `libgcc_s` |

## Usage

### Compiling C, C++, and C++26 P2996 reflection programs

The wrappers supply `--target` and `--sysroot` relative to their own directory.
GNU uses the same commands with `TARGET=x86_64-unknown-linux-gnu`:

```bash
"$TARGET-clang" hello.c -o hello
"$TARGET-clang++" -std=c++23 hello.cpp -o hello-cpp
"$TARGET-clang++" -std=c++23 -stdlib=libc++ hello.cpp -o hello-libcxx
"$TARGET-clang" -static hello.c -o hello-static
```

Minimal C++26 P2996 static reflection example:

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
clang-cross-run ./reflection
```

### Running dynamically linked target binaries

Execute dynamically linked target binaries against the bundled sysroot and
compiler runtime libraries using `clang-cross-run` (or `"$TOOLCHAIN/bin/clang-cross-run"`
when `setvars.sh` has not been sourced):

```bash
clang-cross-run ./hello-libcxx

# Equivalent explicit loader invocation:
"$CLANG_CROSS_LOADER" --library-path "$CLANG_CROSS_LIBRARY_PATH" ./hello-libcxx
```

### Building CMake projects

Pass the relocatable toolchain file exported by `setvars.sh`:

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_TOOLCHAIN_FILE="$CLANG_CROSS_CMAKE_TOOLCHAIN_FILE"
cmake --build build -j
```

### Building Rust projects

Sourcing `setvars.sh` automatically exports `CARGO_TARGET_<TRIPLE>_LINKER`,
`CARGO_TARGET_<TRIPLE>_RUNNER` (`clang-cross-run`), and `cc-rs` variables:

```bash
rustup target add x86_64-unknown-linux-gnu x86_64-unknown-linux-musl
cargo build --target x86_64-unknown-linux-musl
```

If configuring `.cargo/config.toml` manually without `setvars.sh` (Rust's
standard library is separate):

```toml
[target.x86_64-unknown-linux-gnu]
linker = "x86_64-unknown-linux-gnu-clang"
[target.x86_64-unknown-linux-musl]
linker = "x86_64-unknown-linux-musl-clang"
```

Use `cargo build --target x86_64-unknown-linux-musl`. Rust may supply its own
musl CRT objects; selecting this linker alone does not replace that policy.

## Environment and runtime helpers

### `setvars.sh` options and lifecycle commands

| Option / Command | Behavior |
| --- | --- |
| `source "$TOOLCHAIN/setvars.sh"` | Validates internal toolchain layout and runtime closure (`realpath -e`), prepends `$TOOLCHAIN/bin` to `PATH`, and exports compiler, CMake, and Cargo variables |
| `--force` | Re-evaluates and overwrites an already active `setvars.sh` session for the same `CLANG_CROSS_ROOT` |
| `--lld` (default) | Sets `CLANG_CROSS_LDFLAGS=-fuse-ld=lld` |
| `--no-lld` | Sets `CLANG_CROSS_LDFLAGS=""` |
| `--no-cc-env` | Leaves ambient `CC`, `CXX`, `CPP`, `AR`, `NM`, `RANLIB`, `STRIP`, `OBJCOPY`, `OBJDUMP`, `READELF`, `LD`, and `CLANG_CROSS_LDFLAGS` untouched |
| `--quiet` | Suppresses the activation summary line on `stdout` |
| `clang_cross_deactivate` | Restores `PATH` and unsets all environment variables exported by the active `setvars.sh` session |

### Exported environment variables

| Variable | Value |
| --- | --- |
| `CLANG_CROSS_ROOT` | Canonical path to the extracted `<target>` directory |
| `CLANG_CROSS_TARGET` | Active target triple (`x86_64-unknown-linux-gnu` or `x86_64-unknown-linux-musl`) |
| `CLANG_CROSS_LIBC_FLAVOR` | `glibc` or `musl` |
| `CLANG_CROSS_CLANG_VERSION` | Bundled Clang/LLVM version (`21.0.0`) |
| `CLANG_CROSS_SYSROOT` | Canonical path to the target sysroot (`$CLANG_CROSS_ROOT/$CLANG_CROSS_TARGET/sysroot`) |
| `CLANG_CROSS_LOADER` | Bundled target dynamic loader (`ld-linux-x86-64.so.2` or `ld-musl-x86_64.so.1`) |
| `CLANG_CROSS_LIBRARY_PATH` | Colon-delimited runtime search path across bundled target sysroot and runtime library directories |
| `CLANG_CROSS_CMAKE_TOOLCHAIN_FILE` | `$CLANG_CROSS_ROOT/toolchain.cmake` |
| `CLANG_CROSS_LDFLAGS` | `-fuse-ld=lld` (or empty when `--no-lld` is passed; omitted with `--no-cc-env`) |
| `CC`, `CXX`, `CPP`, `AR`, `NM`, `RANLIB`, `STRIP`, `OBJCOPY`, `OBJDUMP`, `READELF`, `LD` | Target-prefixed Clang/LLVM tools (`<target>-clang`, `<target>-clang++`, `llvm-readelf`, `ld.lld`; omitted with `--no-cc-env`) |
| `CARGO_TARGET_<TRIPLE>_LINKER` | `$CLANG_CROSS_ROOT/bin/$CLANG_CROSS_TARGET-clang` |
| `CARGO_TARGET_<TRIPLE>_RUNNER` | `$CLANG_CROSS_ROOT/bin/clang-cross-run` |
| `CC_<triple>`, `CXX_<triple>`, `AR_<triple>`, `RANLIB_<triple>` | Absolute paths to compiler and archive tools for `cc-rs` build scripts |

## Supported targets and host profiles

### 8-cell build matrix

| Target | Host profile | Container image | Base sysroot / libc | Clang / LLVM / LLD | Runtimes |
| --- | --- | --- | --- | --- | --- |
| `x86_64-unknown-linux-gnu` | `centos7` | `centos:7.9.2009` | `gnu-cross` `v0.0.1` (`centos7` glibc sysroot) | `21.0.0` (`p2996`) | `compiler-rt`, `libc++`, `libc++abi`, `libunwind` |
| `x86_64-unknown-linux-gnu` | `amazonlinux2` | `public.ecr.aws/amazonlinux/amazonlinux:2` | `gnu-cross` `v0.0.1` (`amazonlinux2` glibc sysroot) | `21.0.0` (`p2996`) | `compiler-rt`, `libc++`, `libc++abi`, `libunwind` |
| `x86_64-unknown-linux-gnu` | `alinux3` | `alibaba-cloud-linux-3-registry.cn-hangzhou.cr.aliyuncs.com/alinux3/alinux3:latest` | `gnu-cross` `v0.0.1` (`alinux3` glibc sysroot) | `21.0.0` (`p2996`) | `compiler-rt`, `libc++`, `libc++abi`, `libunwind` |
| `x86_64-unknown-linux-gnu` | `ubuntu22` | `ubuntu:22.04` | `gnu-cross` `v0.0.1` (`ubuntu22` glibc sysroot) | `21.0.0` (`p2996`) | `compiler-rt`, `libc++`, `libc++abi`, `libunwind` |
| `x86_64-unknown-linux-musl` | `centos7` | `centos:7.9.2009` | `musl-cross` `v1.2.6-r1` (musl `1.2.6`, base GCC `16.1.0`) | `21.0.0` (`p2996`) | `compiler-rt`, `libc++`, `libc++abi`, `libunwind` |
| `x86_64-unknown-linux-musl` | `amazonlinux2` | `public.ecr.aws/amazonlinux/amazonlinux:2` | `musl-cross` `v1.2.6-r1` (musl `1.2.6`, base GCC `16.1.0`) | `21.0.0` (`p2996`) | `compiler-rt`, `libc++`, `libc++abi`, `libunwind` |
| `x86_64-unknown-linux-musl` | `alinux3` | `alibaba-cloud-linux-3-registry.cn-hangzhou.cr.aliyuncs.com/alinux3/alinux3:latest` | `musl-cross` `v1.2.6-r1` (musl `1.2.6`, base GCC `16.1.0`) | `21.0.0` (`p2996`) | `compiler-rt`, `libc++`, `libc++abi`, `libunwind` |
| `x86_64-unknown-linux-musl` | `ubuntu22` | `ubuntu:22.04` | `musl-cross` `v1.2.6-r1` (musl `1.2.6`, base GCC `16.1.0`) | `21.0.0` (`p2996`) | `compiler-rt`, `libc++`, `libc++abi`, `libunwind` |

### Component summary

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

## Release pipeline

The daily `03:40 UTC` workflow (`.github/workflows/release.yaml`) and manual
`workflow_dispatch` runs execute a 3-stage pipeline:

1. **Discover and preflight (`prepare-matrix`)**: Runs all local contract suites
   (`bash tests/run.sh`), discovers the latest `bloomberg/clang-p2996` `p2996`
   commit when scheduled or when `check_upstream=true`, validates musl
   `compiler-rt` patches with `--fuzz=0`, and emits the immutable
   `candidate-source` artifact. If no upstream update is found on schedule, the
   build is skipped.
2. **Build, cache, and verify (`build`)**: Pulls the host container image,
   restores any matching `toolchain-v1-*` or `llvm-v1-*` cache (or recovers host
   LLVM from `llvm_seed_run_id`), builds and verifies the target toolchain, and
   uploads `<target>-<host>.tar.xz`, `.sha256`, and `.provenance.json`. CI saves
   host LLVM and the packaged toolchain before runtime verification so changes to
   `scripts/verify-toolchain.sh` rerun verification without recompiling LLVM.
3. **Publish and promote (`release`)**: Verifies all 8 cell archives, sidecars,
   and provenance receipts against `candidate-source`. Asset replacement is per
   file, while tag (`v0.0.1`) and `main` promotion (single `first commit` root by
   Nathan Shea) wait until all uploads and release notes succeed.

Trigger a manual matrix build and release:

```bash
gh workflow run release.yaml \
  -f targets=x86_64-unknown-linux-gnu,x86_64-unknown-linux-musl \
  -f containers=all \
  -f create_release=true \
  -f release_tag=v0.0.1 \
  -f check_upstream=false
```

### `workflow_dispatch` inputs

| Input | Default | Description |
| --- | --- | --- |
| `targets` | `x86_64-unknown-linux-gnu,x86_64-unknown-linux-musl` | Target triples (comma-separated); full matrix required for publication |
| `containers` | `all` | Host labels (`amazonlinux2,alinux3,centos7,ubuntu22` or `all`) |
| `create_release` | `false` | Publish the complete verified 8-cell matrix |
| `release_tag` | `v0.0.1` | Mutable release tag |
| `source_run_id` | `''` | Reuse verified artifacts from a completed `release.yaml` workflow run |
| `check_upstream` | `false` | Discover and preflight the latest `bloomberg/clang-p2996` `p2996` commit |
| `llvm_seed_run_id` | `''` | Recover host LLVM without compilation from a completed run's verified musl artifact with matching recipe and container digest |

## Building locally

```bash
git clone --recursive https://github.com/trunkszi/clang-cross.git
cd clang-cross

# Run all offline contract suites:
bash tests/run.sh

# Build and verify inside a host container:
./scripts/build-with-docker.sh x86_64-unknown-linux-musl ubuntu:22.04 ubuntu22

# Alternatively, on a configured disposable host (installs packages unless skipped):
./scripts/make x86_64-unknown-linux-gnu --skip-deps

# Check upstream bloomberg/clang-p2996 commits and patch preflight:
./scripts/sync-upstream.sh --check --summary-file upstream-summary.md
```

Override `GITHUB_ORG=trunkszi MUSL_CROSS_VERSION=v0.0.1` to change per-libc
base releases (`gnu-cross` and `musl-cross` remain the defaults;
`GITHUB_ORG=trunkszi BASE_TOOLCHAIN_REPO=gcc-cross GCC_CROSS_VERSION=v0.0.1` is
supported for unified `trunkszi/gcc-cross` release archives once published);
also set `EXPECTED_BASE_GCC_VERSION` to match the selected musl base archive.

### Build environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `GITHUB_ORG` | `Central-Limit-Trading` | GitHub organization hosting base GCC/sysroot archives |
| `BASE_TOOLCHAIN_REPO` | `''` (uses `gnu-cross` / `musl-cross`) | Optional unified base repository name (e.g. `gcc-cross`) |
| `GNU_CROSS_VERSION` | `v0.0.1` | Release tag for `gnu-cross` base archives |
| `MUSL_CROSS_VERSION` | `v1.2.6-r1` | Release tag for `musl-cross` base archives |
| `GCC_CROSS_VERSION` | `v0.0.1` | Release tag when `BASE_TOOLCHAIN_REPO=gcc-cross` is set |
| `EXPECTED_BASE_GCC_VERSION` | `16.1.0` | Expected GCC directory version inside the musl base archive |
| `BUILD_JOBS` | `$(nproc)` (fallback `2`) | Parallel Ninja/CMake build jobs |
| `XZ_OPT` | `-T0` | `xz` compression options for host LLVM and target toolchain archives |
| `REUSE_PACKAGED_TOOLCHAIN` | `false` | When `true`, skips compilation and re-verifies an existing `<target>.tar.xz` |

## Repository layout

| Path | Purpose |
| --- | --- |
| `.github/workflows/release.yaml` | Daily and manual 8-cell build, cache/seed recovery, verification, and release workflow |
| `llvm/` | Pinned `bloomberg/clang-p2996` LLVM submodule |
| `scripts/make` | Isolated-staging host LLVM and target runtime build driver, wrapper installer, and packager |
| `scripts/host-dependencies.sh` | Host OS detection and declarative package installation (`scripts/packages/*.conf`) |
| `scripts/setvars.sh` | Bundled `bash`/`zsh` environment helper installed to `<target>/setvars.sh` |
| `scripts/download-toolchain.sh` | Base GCC/sysroot downloader with SHA-256 and `check-archive.py` verification |
| `scripts/check-archive.py` | Pre-extraction tar path, symlink-hop, and bundled helper layout validator |
| `scripts/seed-llvm.py` | Trusted host-LLVM seed extractor from verified musl artifacts |
| `scripts/verify-toolchain.sh` / `scripts/verify-archive.sh` | Runtime verifier probing C/C++, `libc++`, C++26 P2996 reflection, sanitizers, `setvars.sh`, `clang-cross-run`, and `toolchain.cmake` |
| `scripts/sync-upstream.sh` / `scripts/prepare-matrix.sh` / `scripts/matrix.py` | Upstream `p2996` discovery, `--fuzz=0` patch preflight, and matrix planner |
| `scripts/release.py` / `scripts/notify-failure.py` | Candidate provenance, asset admission, ordered release publication, and failure alerts |
| `targets/` | Locked `versions`, per-target runtime `config`, and musl `compiler-rt` patches with SHA-512 provenance |
| `tests/` | Offline contract suites (`test_config_and_patches.sh`, `test_scripts_contract.sh`, `test_cache_contract.sh`, `test_upstream_sync.sh`, `test_release_workflow.sh`) |

## Troubleshooting

- **`version 'GLIBC_2.XX' not found` when invoking `<target>-clang` on the host**:
  Select a host archive (`centos7`, `amazonlinux2`, `alinux3`, or `ubuntu22`)
  whose host glibc baseline is older than or equal to the machine running the
  compiler.
- **`error while loading shared libraries: libc++.so.1` (or `libunwind.so.1`) when running a target binary**:
  Launch dynamically linked target binaries via `clang-cross-run ./binary` (or
  `"$CLANG_CROSS_LOADER" --library-path "$CLANG_CROSS_LIBRARY_PATH" ./binary`) so
  execution uses the bundled target sysroot loader and LLVM/GCC runtime libraries.
- **`setvars.sh` reports `already active` when changing `--no-lld` or `--no-cc-env`**:
  Pass `--force` (`source "$TOOLCHAIN/setvars.sh" --force --no-lld`) or run
  `clang_cross_deactivate` before sourcing again.
- **`incomplete toolchain layout` from `scripts/check-archive.py`**:
  Packaged `clang-cross` target archives must contain `<target>/setvars.sh`,
  `<target>/bin/clang-cross-run`, and `<target>/toolchain.cmake` in addition to
  `bin`, `include`, `lib`, and `<target>/sysroot`.

## License

There is no single top-level repository license declaration. LLVM's terms are
in [`llvm/llvm/LICENSE.TXT`](llvm/llvm/LICENSE.TXT); bundled GCC, libc, and other
upstream components retain their own licenses and notices.
