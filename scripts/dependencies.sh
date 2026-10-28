#!/usr/bin/env bash
# Host setup is explicit; package recipes themselves perform no discovery or I/O.
retry() {
    local attempt rc=1
    for attempt in 1 2 3; do
        if "$@"; then return 0; else rc=$?; fi
        echo "Attempt ${attempt}/3 failed (${rc}): $*" >&2
        if [[ $attempt != 3 ]]; then sleep "${RETRY_DELAY:-5}"; fi
    done
    return "$rc"
}
ensure_cmake() {
    local version=0.0.0
    if command -v cmake >/dev/null; then
        version=$(cmake --version | awk 'NR==1 {print $3}')
    fi
    if [[ $(printf '%s\n' 3.20.0 "$version" | sort -V | head -n 1) != 3.20.0 ]]; then
        retry ${SUDO_CMD} python3 -m pip install cmake
        export PATH="/usr/local/bin:$PATH"
    fi
}
centos_mirrors() {
    ${SUDO_CMD} sed -i -e 's|^mirrorlist=|#mirrorlist=|g' \
        -e 's|#baseurl=http://mirror.centos.org|baseurl=https://mirrors.aliyun.com|g' /etc/yum.repos.d/CentOS-*.repo
    ${SUDO_CMD} rm -f /etc/yum.repos.d/CentOS-SCLo-sclo.repo /etc/yum.repos.d/CentOS-SCLo-rh.repo
    retry ${SUDO_CMD} yum install -y --disablerepo='centos-sclo-*' epel-release centos-release-scl centos-release-scl-rh
    local component repo_file
    for component in sclo rh; do
        repo_file=scl
        if [[ "$component" == rh ]]; then repo_file=scl-rh; fi
        ${SUDO_CMD} tee "/etc/yum.repos.d/CentOS-SCLo-${repo_file}.repo" >/dev/null <<REPO
[centos-sclo-${component}]
name=CentOS 7 SCLo ${component}
baseurl=https://mirrors.aliyun.com/centos-vault/7.9.2009/sclo/\$basearch/${component}/
gpgcheck=1
enabled=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-SIG-SCLo
REPO
    done
    # EPEL 7 is archived; avoid dead metalinks and mirrors requiring newer TLS.
    ${SUDO_CMD} tee /etc/yum.repos.d/epel.repo >/dev/null <<'REPO'
[epel]
name=EPEL 7 archive
baseurl=https://mirrors.aliyun.com/epel-archive/7/$basearch/
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-EPEL-7
REPO
    retry ${SUDO_CMD} yum clean all
    retry ${SUDO_CMD} yum makecache
}
install_python_build_tools() {
    # These wheels support Python 3.6/3.7 and old glibc without compiling build tools.
    retry ${SUDO_CMD} python3 -m pip install --upgrade --only-binary=:all: pip==21.3.1
    retry ${SUDO_CMD} python3 -m pip install --only-binary=:all: cmake==3.27.9 ninja==1.11.1.1
    export PATH="/usr/local/bin:$PATH"
    cmake --version
    ninja --version
}
amazonlinux_compiler() {
    local tool
    for tool in gcc g++ c++ cpp ar ranlib nm objcopy objdump strip; do
        ${SUDO_CMD} ln -sf "/usr/bin/gcc10-${tool}" "/usr/bin/${tool}"
    done
    ${SUDO_CMD} ln -sf /usr/bin/gcc10-gcc /usr/bin/cc
    install_python_build_tools
}
centos_compiler() {
    local tool
    for tool in gcc g++ c++ cpp; do
        ${SUDO_CMD} ln -sf "/opt/rh/devtoolset-11/root/usr/bin/${tool}" "/usr/local/bin/${tool}"
    done
    install_python_build_tools
}
alinux_python() {
    if [[ ! -e /usr/bin/python3 && -x /usr/bin/python3.11 ]]; then
        ${SUDO_CMD} ln -s /usr/bin/python3.11 /usr/bin/python3
    fi
}
install_dependencies() {
    local config="${SCRIPT_DIR}/packages/${HOST_SYSTEM}.conf" setup
    [[ -f "$config" ]] || { echo "Unsupported host: $HOST_SYSTEM" >&2; return 1; }
    # shellcheck source=/dev/null
    source "$config"
    if [[ $PKG_MANAGER == rpm-auto ]]; then
        if command -v dnf >/dev/null; then PKG_MANAGER=dnf; else PKG_MANAGER=yum; fi
    fi
    for setup in ${MIRROR_SETUP_COMMANDS[@]+"${MIRROR_SETUP_COMMANDS[@]}"}; do "$setup"; done
    case "$PKG_MANAGER" in
        yum|dnf) retry ${SUDO_CMD} "$PKG_MANAGER" install -y "${PACKAGES[@]}" ;;
        apt-get) retry ${SUDO_CMD} apt-get update; retry ${SUDO_CMD} apt-get install -y --no-install-recommends "${PACKAGES[@]}" ;;
        pacman) retry ${SUDO_CMD} pacman -Syu --noconfirm --needed "${PACKAGES[@]}" ;;
        apk) retry ${SUDO_CMD} apk update; retry ${SUDO_CMD} apk add "${PACKAGES[@]}" ;;
        *) echo "Unsupported package manager: $PKG_MANAGER" >&2; return 1 ;;
    esac
    for setup in ${EXTRA_SETUP_COMMANDS[@]+"${EXTRA_SETUP_COMMANDS[@]}"}; do "$setup"; done
}
