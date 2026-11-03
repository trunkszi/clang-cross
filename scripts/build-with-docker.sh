#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 3 ]] || { echo "Usage: $0 <target> <image> <os-label>" >&2; exit 64; }
TARGET=$1
CONTAINER_IMAGE=$2
OS_LABEL=$3
PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
source "${PROJECT_ROOT}/targets/versions"
case "$TARGET" in x86_64-unknown-linux-gnu|x86_64-unknown-linux-musl) ;; *) echo 'invalid target' >&2; exit 64 ;; esac
case "$OS_LABEL" in amazonlinux2|alinux3|centos7|ubuntu22) ;; *) echo 'invalid OS label' >&2; exit 64 ;; esac
[[ -n "$CONTAINER_IMAGE" && "$CONTAINER_IMAGE" != -* && "$CONTAINER_IMAGE" != *[[:space:]]* ]] || exit 64
CONTAINER_NAME="clang-cross-${TARGET}-${OS_LABEL}-$$"
cleanup() {
    local rc=$?
    trap - EXIT
    if ! docker rm -f "$CONTAINER_NAME"; then echo "container cleanup failed: $CONTAINER_NAME" >&2; [[ $rc != 0 ]] || rc=1; fi
    exit "$rc"
}
docker run -d --security-opt seccomp=unconfined --name "$CONTAINER_NAME" \
    -v "$PROJECT_ROOT:/workspace/clang-cross" -w /workspace/clang-cross \
    "$CONTAINER_IMAGE" tail -f /dev/null
trap cleanup EXIT
if [[ "$OS_LABEL" == alinux3 || "$CONTAINER_IMAGE" == *alinux3* ]]; then
    docker exec "$CONTAINER_NAME" bash -c '
        set -euo pipefail
        mkdir -p /etc/yum.repos.d/backup
        shopt -s nullglob
        for repo in /etc/yum.repos.d/*.repo; do mv "$repo" /etc/yum.repos.d/backup/; done
        for component in os updates plus powertools module; do
            cat >> /etc/yum.repos.d/alinux3.repo <<REPO
[alinux3-${component}]
name=Alibaba Cloud Linux 3 - ${component}
baseurl=https://mirrors.aliyun.com/alinux/\$releasever/${component}/\$basearch/
enabled=1
gpgcheck=1
gpgkey=https://mirrors.aliyun.com/alinux/3/RPM-GPG-KEY-ALINUX-3

REPO
        done
        if command -v dnf >/dev/null; then dnf clean all; else yum clean all; fi
    '
fi
docker exec -e BUILD_TARGET="$TARGET" -e BUILD_OS_LABEL="$OS_LABEL" \
    -e GITHUB_ORG="$GITHUB_ORG" -e GNU_CROSS_VERSION="$GNU_CROSS_VERSION" \
    -e MUSL_CROSS_VERSION="$MUSL_CROSS_VERSION" -e LLVM_VERSION="$LLVM_VERSION" \
    -e LLVM_SOURCE_REVISION="$LLVM_SOURCE_REVISION" \
    -e REUSE_PACKAGED_TOOLCHAIN="${REUSE_PACKAGED_TOOLCHAIN:-false}" \
    -e EXPECTED_BASE_GCC_VERSION="${EXPECTED_BASE_GCC_VERSION:-}" \
    "$CONTAINER_NAME" bash -c '
        set -euo pipefail
        cd /workspace/clang-cross
        ./scripts/make "$BUILD_TARGET"
        source_archive="${BUILD_TARGET}.tar.xz"
        artifact="${BUILD_TARGET}-${BUILD_OS_LABEL}.tar.xz"
        test -f "$source_archive"
        expected_hash=$(cat "${source_archive}.sha256")
        [[ "$expected_hash" =~ ^[[:xdigit:]]{64}$ ]]
        printf "%s  %s\n" "$expected_hash" "$source_archive" | sha256sum --check -
        cp "$source_archive" "$artifact"
        cp "${source_archive}.sha256" "${artifact}.sha256"
        printf "%s  %s\n" "$expected_hash" "$artifact" | sha256sum --check -
    '
printf 'Output: %s-%s.tar.xz and .sha256\n' "$TARGET" "$OS_LABEL"
