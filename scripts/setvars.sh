#!/usr/bin/env bash

_clang_cross_path_without() {
    local remaining=$1 removed=$2 component result='' separator=''
    while :; do
        component=${remaining%%:*}
        if [ "$component" != "$removed" ]; then
            result=${result}${separator}${component}
            separator=:
        fi
        case $remaining in *:*) remaining=${remaining#*:} ;; *) break ;; esac
    done
    printf '%s' "$result"
}

clang_cross_deactivate() {
    if [ -n "${_CLANG_CROSS_ACTIVE_BIN:-}" ]; then
        PATH=$(_clang_cross_path_without "${PATH-}" "$_CLANG_CROSS_ACTIVE_BIN")
        export PATH
    fi
    if [ "${_CLANG_CROSS_CC_ENV:-0}" = 1 ]; then
        unset CC CXX CPP AR NM RANLIB STRIP OBJCOPY OBJDUMP READELF LD
    fi
    unset CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUNNER
    unset CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_RUNNER
    unset CC_x86_64_unknown_linux_gnu CXX_x86_64_unknown_linux_gnu AR_x86_64_unknown_linux_gnu RANLIB_x86_64_unknown_linux_gnu
    unset CC_x86_64_unknown_linux_musl CXX_x86_64_unknown_linux_musl AR_x86_64_unknown_linux_musl RANLIB_x86_64_unknown_linux_musl
    unset CLANG_CROSS_ROOT CLANG_CROSS_TARGET CLANG_CROSS_SYSROOT CLANG_CROSS_CLANG_VERSION CLANG_CROSS_LIBC_FLAVOR
    unset CLANG_CROSS_SETVARS_COMPLETED CLANG_CROSS_LDFLAGS CLANG_CROSS_LOADER CLANG_CROSS_LIBRARY_PATH CLANG_CROSS_CMAKE_TOOLCHAIN_FILE
    unset _CLANG_CROSS_ACTIVE_BIN _CLANG_CROSS_CC_ENV
    return 0
}

_clang_cross_activate() {
    local source_file=$1 force=0 lld=1 cc_env=1 quiet=0
    local root compiler cxx target='' sysroot version candidate resolved loader='' loader_name
    local lib_path='' runtime cargo_key cc_key flavor
    shift
    for candidate in "$@"; do
        case $candidate in
            --force) force=1 ;; --lld) lld=1 ;; --no-lld) lld=0 ;;
            --no-cc-env) cc_env=0 ;; --quiet) quiet=1 ;;
            *) printf 'Unknown setvars option: %s\n' "$candidate" >&2; return 64 ;;
        esac
    done
    source_file=$(realpath -e -- "$source_file") || return 1
    root=$(cd -- "$(dirname -- "$source_file")" && pwd -P) || return 1
    case $root in *:*|*'
'*) echo 'Invalid toolchain root' >&2; return 1 ;; esac
    if [ "${CLANG_CROSS_SETVARS_COMPLETED:-0}" = 1 ] && [ "${CLANG_CROSS_ROOT:-}" = "$root" ] && [ "$force" = 0 ]; then
        [ "$quiet" = 1 ] || printf 'clang-cross environment already initialized: %s\n' "$root"
        return 0
    fi
    for candidate in x86_64-unknown-linux-gnu x86_64-unknown-linux-musl; do
        if [ -x "$root/bin/$candidate-clang" ]; then
            [ -z "$target" ] || { echo 'Ambiguous toolchain target' >&2; return 1; }
            target=$candidate
        fi
    done
    [ -n "$target" ] || { echo 'Toolchain compiler not found' >&2; return 1; }
    compiler=$(realpath -e -- "$root/bin/$target-clang") || return 1
    case $compiler in "$root"/*) ;; *) echo 'Compiler escapes toolchain root' >&2; return 1 ;; esac
    cxx=$(realpath -e -- "$root/bin/$target-clang++") || return 1
    case $cxx in "$root"/*) ;; *) echo 'C++ compiler escapes toolchain root' >&2; return 1 ;; esac
    [ -x "$cxx" ] || return 1
    sysroot="$root/$target/sysroot"
    if [ ! -e "$sysroot" ] && [ ! -L "$sysroot" ]; then
        sysroot=$("$compiler" --print-sysroot) || return $?
    fi
    sysroot=$(realpath -e -- "$sysroot") || return 1
    case $sysroot in "$root"/*) ;; *) echo 'Sysroot escapes toolchain root' >&2; return 1 ;; esac
    candidate=$("$compiler" --version) || return $?
    version=$(printf '%s\n' "$candidate" | sed -n 's/.*clang version \([0-9][0-9.]*\).*/\1/p' | head -n 1)
    [ -n "$version" ] || { echo 'Clang version missing' >&2; return 1; }
    case $target in
        *-gnu) flavor=glibc; loader_name=ld-linux-x86-64.so.2; cargo_key=X86_64_UNKNOWN_LINUX_GNU ;;
        *-musl) flavor=musl; loader_name=ld-musl-x86_64.so.1; cargo_key=X86_64_UNKNOWN_LINUX_MUSL ;;
    esac
    for candidate in "$sysroot/lib64" "$sysroot/lib" "$sysroot/usr/lib64" "$sysroot/usr/lib"; do
        if [ -e "$candidate" ] || [ -L "$candidate" ]; then
            resolved=$(realpath -e -- "$candidate") || return 1
            case $resolved in *:*|*$'\n'*) echo 'Invalid runtime path' >&2; return 1 ;; "$root"/*) ;; *) echo 'Library directory escapes toolchain root' >&2; return 1 ;; esac
            lib_path=${lib_path:+$lib_path:}${resolved}
            if [ -z "$loader" ] && [ -e "$candidate/$loader_name" ]; then
                loader=$(realpath -e -- "$candidate/$loader_name") || return 1
            fi
        fi
    done
    case $loader in "$root"/*) ;; *) echo 'Loader missing or escapes toolchain root' >&2; return 1 ;; esac
    [ -x "$loader" ] || return 1
    for runtime in libstdc++.so.6 libgcc_s.so.1 libc++.so libc++abi.so libunwind.so; do
        case $runtime in
            libstdc++.so.6|libgcc_s.so.1) candidate=$("$compiler" -print-file-name="$runtime") || return $? ;;
            *) candidate=$("$cxx" -stdlib=libc++ -print-file-name="$runtime") || return $? ;;
        esac
        resolved=$(realpath -e -- "$candidate") || return 1
        case $resolved in *:*|*$'\n'*) echo 'Invalid runtime path' >&2; return 1 ;; "$root"/*) ;; *) echo 'Runtime escapes toolchain root' >&2; return 1 ;; esac
        [ -f "$resolved" ] || return 1
        candidate=$(dirname -- "$resolved")
        case :$lib_path: in *:"$candidate":*) ;; *) lib_path=${lib_path:+$lib_path:}${candidate} ;; esac
    done
    # Validate a replacement completely before removing the active environment.
    clang_cross_deactivate
    PATH=$(_clang_cross_path_without "${PATH-}" "$root/bin")
    export PATH="$root/bin:$PATH"
    export _CLANG_CROSS_ACTIVE_BIN=$root/bin _CLANG_CROSS_CC_ENV=$cc_env
    export CLANG_CROSS_ROOT=$root CLANG_CROSS_TARGET=$target CLANG_CROSS_SYSROOT=$sysroot
    export CLANG_CROSS_CLANG_VERSION=$version CLANG_CROSS_LIBC_FLAVOR=$flavor CLANG_CROSS_SETVARS_COMPLETED=1
    if [ "$cc_env" = 1 ]; then
        export CLANG_CROSS_LDFLAGS=''
        [ "$lld" = 0 ] || CLANG_CROSS_LDFLAGS=-fuse-ld=lld
        export CC=$target-clang CXX=$target-clang++ CPP=$target-cpp AR=$target-ar NM=$target-nm RANLIB=$target-ranlib
        export STRIP=$target-strip OBJCOPY=$target-objcopy OBJDUMP=$target-objdump READELF=llvm-readelf
        export LD=ld.lld
    fi
    cc_key=${target//-/_}
    export "CARGO_TARGET_${cargo_key}_LINKER=$root/bin/$target-clang"
    export "CARGO_TARGET_${cargo_key}_RUNNER=$root/bin/clang-cross-run"
    export "CC_${cc_key}=$root/bin/$target-clang" "CXX_${cc_key}=$root/bin/$target-clang++"
    export "AR_${cc_key}=$root/bin/$target-ar" "RANLIB_${cc_key}=$root/bin/$target-ranlib"
    export CLANG_CROSS_LOADER=$loader CLANG_CROSS_LIBRARY_PATH=$lib_path
    export CLANG_CROSS_CMAKE_TOOLCHAIN_FILE=$root/toolchain.cmake
    [ "$quiet" = 1 ] || printf 'clang-cross %s (Clang %s) initialized\n' "$target" "$version"
    return 0
}

if [ -n "${ZSH_VERSION:-}" ]; then
    case ${ZSH_EVAL_CONTEXT:-} in
        *:file*) ;;
        *) printf 'ERROR: This script must be sourced\nUsage: source %s\n' "$0" >&2; exit 2 ;;
    esac
    # zsh exposes the sourced filename through its prompt expansion syntax.
    # shellcheck disable=SC2296
    if _clang_cross_activate "${(%):-%x}" "$@"; then return 0; else return $?; fi
else
    if [ "${BASH_SOURCE[0]}" = "$0" ]; then
        printf 'ERROR: This script must be sourced\nUsage: source %s\n' "$0" >&2
        exit 2
    fi
    if _clang_cross_activate "${BASH_SOURCE[0]}" "$@"; then return 0; else return $?; fi
fi
