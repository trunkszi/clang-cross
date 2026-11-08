"""Shell admission, verified extraction, and runtime probes through command boundaries."""

import hashlib, io, json, os, pathlib, shutil, subprocess, tarfile, tempfile, unittest

import sys

sys.dont_write_bytecode = True

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, **kwargs)


def executable(p, text):
    p.write_text(text)
    p.chmod(0o755)


class Scripts(unittest.TestCase):
    def setUp(self):
        temp_base = pathlib.Path(os.environ.get("TMPDIR") or "/tmp").resolve()
        if temp_base == ROOT or ROOT in temp_base.parents:
            raise RuntimeError("Test TMPDIR must be outside the repository")
        self.tmp = tempfile.TemporaryDirectory(
            dir=os.environ.get("TMPDIR") or "/tmp", prefix="clang-cross-scripts."
        )
        self.addCleanup(self.tmp.cleanup)
        self.work = pathlib.Path(self.tmp.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.log = self.work / "calls"
        self.env = dict(
            os.environ,
            TMPDIR=str(self.work),
            PYTHONDONTWRITEBYTECODE="1",
            PATH=str(self.bin) + ":" + os.environ["PATH"],
            CALLS=str(self.log),
        )

    def test_shell_syntax_and_declarative_packages(self):
        paths = [ROOT / "scripts/make", *ROOT.glob("scripts/**/*.sh"),
                 *ROOT.glob("scripts/packages/*.conf"), *ROOT.glob("tests/*.sh")]
        for p in paths:
            self.assertEqual(run(["bash", "-n", str(p)]).returncode, 0, str(p))
        tool_list = next(line for line in (ROOT / 'scripts/make').read_text().splitlines() if '-DLLVM_TOOLCHAIN_TOOLS=' in line)
        self.assertIn('llvm-readelf', tool_list.split('=')[1].strip(' \"').split(';'))
        lint = run(['shellcheck', '-x', '-S', 'style', *map(str, paths)])
        self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        self.assertEqual(run(['grep', '-rnF', '-I', '--exclude-dir=__pycache__', '/opt/' + 'clang-cross', 'scripts/', 'targets/', 'tests/'], cwd=ROOT).returncode, 1)
        for command in [
            "id",
            "dnf",
            "yum",
            "apt-get",
            "pacman",
            "apk",
            "curl",
            "cmake",
            "python3",
        ]:
            executable(
                self.bin / command, '#!/bin/sh\necho "$0 $*" >> "$CALLS"\nexit 99\n'
            )
        confs = list(ROOT.glob("scripts/packages/*.conf"))
        self.assertEqual(len(confs), 8)
        for p in confs:
            q = run(
                [
                    "bash",
                    "-euo",
                    "pipefail",
                    "-c",
                    'source "$1"; declare -p MIRROR_SETUP_COMMANDS EXTRA_SETUP_COMMANDS PACKAGES >/dev/null',
                    "bash",
                    str(p),
                ],
                env=self.env,
            )
            self.assertEqual(q.returncode, 0, q.stderr)
        self.assertFalse(
            self.log.exists(), "sourcing a package recipe executed external discovery"
        )

    def test_invalid_make_arguments_fail_before_any_installer(self):
        for args in [
            [],
            ["../../bad"],
            ["llvm", "--other"],
            ["llvm", "--skip-deps", "extra"],
        ]:
            p = run([str(ROOT / "scripts/make"), *args], env=self.env)
            self.assertEqual(p.returncode, 64, p.stderr)

    def test_docker_base_gcc_override_has_no_implicit_default(self):
        executable(
            self.bin / "docker",
            "#!/usr/bin/env python3\nimport json,os,sys\n"
            "with open(os.environ['CALLS'],'a') as out:out.write(json.dumps(sys.argv[1:])+'\\n')\n",
        )
        env = dict(self.env, BASE_TOOLCHAIN_REPO="gcc-cross", GCC_CROSS_VERSION="v9.8.7")
        env.pop("EXPECTED_BASE_GCC_VERSION", None)
        for override in [None, "16.3.0"]:
            with self.subTest(override=override):
                self.log.unlink(missing_ok=True)
                p = run(
                    [str(ROOT / "scripts/build-with-docker.sh"),
                     "x86_64-unknown-linux-gnu", "ubuntu:22.04", "ubuntu22"],
                    env=env if override is None else dict(env, EXPECTED_BASE_GCC_VERSION=override),
                )
                self.assertEqual(p.returncode, 0, p.stderr)
                calls = [json.loads(line) for line in self.log.read_text().splitlines()]
                build = next(call for call in calls if call[:2] == ["exec", "-e"])
                values = [value for value in build if value.startswith("EXPECTED_BASE_GCC_VERSION=")]
                self.assertEqual(values, ["EXPECTED_BASE_GCC_VERSION=" + (override or "")])
                self.assertIn("BASE_TOOLCHAIN_REPO=gcc-cross", build)
                self.assertIn("GCC_CROSS_VERSION=v9.8.7", build)

    def test_gnu_base_without_musl_metadata_skips_musl_validation(self):
        p = run(
            ["bash", "-euo", "pipefail", "-c",
             'unset EXPECTED_MUSL_VERSION EXPECTED_MUSL_LOADER; '
             'EXPECTED_BASE_GCC_VERSION=""; source "$1/scripts/make"; '
             'source "$1/targets/x86_64-unknown-linux-gnu/config"; '
             '[[ -z "${EXPECTED_BASE_GCC_VERSION}${EXPECTED_MUSL_VERSION:-}${EXPECTED_MUSL_LOADER:-}" ]]; '
             'verify_base_toolchain x86_64-unknown-linux-gnu "$2"',
             "bash", str(ROOT), str(self.work / "absent-toolchain")],
            env=self.env,
        )
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_musl_base_retains_default_and_strict_version_validation(self):
        target = "x86_64-unknown-linux-musl"
        toolchain = self.work / "base toolchain"
        (toolchain / "bin").mkdir(parents=True)
        loader = toolchain / target / "sysroot/lib/ld-musl-x86_64.so.1"
        loader.parent.mkdir(parents=True)
        executable(toolchain / "bin" / (target + "-gcc"),
                   '#!/bin/sh\nprintf "%s\\n" "$TEST_GCC_VERSION"\n')
        executable(loader, '#!/bin/sh\nprintf "Version %s\\n" "$TEST_MUSL_VERSION"\nexit 1\n')
        command = (
            'unset EXPECTED_MUSL_VERSION EXPECTED_MUSL_LOADER; '
            'EXPECTED_BASE_GCC_VERSION=""; source "$1/scripts/make"; '
            'source "$1/targets/x86_64-unknown-linux-musl/config"; '
            '[[ "$EXPECTED_BASE_GCC_VERSION" == 16.1.0 && "$EXPECTED_MUSL_VERSION" == 1.2.6 && '
            '"$EXPECTED_MUSL_LOADER" == ld-musl-x86_64.so.1 ]]; '
            'verify_base_toolchain x86_64-unknown-linux-musl "$2"'
        )
        for gcc, musl, error in [
            ("16.1.0", "1.2.6", None),
            ("16.0.0", "1.2.6", "base GCC is 16.0.0, expected 16.1.0"),
            ("16.1.0", "1.2.5", "musl loader is not 1.2.6"),
        ]:
            with self.subTest(gcc=gcc, musl=musl):
                p = run(["bash", "-euo", "pipefail", "-c", command,
                         "bash", str(ROOT), str(toolchain)],
                        env=dict(self.env, TEST_GCC_VERSION=gcc, TEST_MUSL_VERSION=musl))
                if error is None:
                    self.assertEqual(p.returncode, 0, p.stderr)
                else:
                    self.assertNotEqual(p.returncode, 0)
                    self.assertIn(error, p.stderr)
        loader.unlink()
        p = run(["bash", "-euo", "pipefail", "-c", command,
                 "bash", str(ROOT), str(toolchain)],
                env=dict(self.env, TEST_GCC_VERSION="16.1.0", TEST_MUSL_VERSION="1.2.6"))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("musl loader not found", p.stderr)

    def test_cache_binds_sha_and_host_and_source_must_be_clean(self):
        text = (ROOT / "scripts/make").read_text()
        self.assertIn(
            "clang-cross-${LLVM_VERSION}-${LLVM_SHORT_SHA}-${HOST_SYSTEM}.tar.xz", text
        )
        self.assertIn('LLVM_SHORT_SHA="${LLVM_SOURCE_REVISION:0:12}"', text)
        # Sourcing exports the exact source-admission function without starting a build.
        q = run(
            [
                "bash",
                "-c",
                'source "$1/scripts/make"; verify_llvm_source',
                "bash",
                str(ROOT),
            ],
            env=self.env,
        )
        self.assertEqual(q.returncode, 0, q.stderr)
        env = dict(self.env, LLVM_SOURCE_REVISION="0" * 40)
        q = run(
            [
                "bash",
                "-c",
                'source "$1/scripts/make"; verify_llvm_source',
                "bash",
                str(ROOT),
            ],
            env=env,
        )
        self.assertNotEqual(q.returncode, 0)

    def test_rpm_installs_without_update_query_and_retries_failures(self):
        for manager in ["yum", "dnf"]:
            executable(
                self.bin / manager,
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n'
                'if [ "$1" = install ]; then exit "$INSTALL_STATUS"; fi\nexit 124\n',
            )
        command = (
            'source "$1/scripts/host-dependencies.sh"; '
            'SCRIPT_DIR="$1/scripts"; SUDO_CMD=""; '
            'centos_mirrors() { :; }; amazonlinux_compiler() { :; }; '
            'centos_compiler() { :; }; alinux_python() { :; }; '
            'install_dependencies'
        )
        for host in ["alinux3", "amazonlinux2", "centos7"]:
            for status, count in [(0, 1), (100, 3), (42, 3), (124, 3)]:
                with self.subTest(host=host, status=status):
                    self.log.unlink(missing_ok=True)
                    p = run(
                        ["bash", "-euo", "pipefail", "-c", command, "bash", str(ROOT)],
                        env=dict(self.env, HOST_SYSTEM=host, INSTALL_STATUS=str(status), RETRY_DELAY="0"),
                    )
                    self.assertEqual(p.returncode, status, p.stderr)
                    calls = self.log.read_text().splitlines()
                    self.assertEqual(len(calls), count)
                    self.assertTrue(all(call.startswith("install -y ") for call in calls), calls)

    def test_dependency_installation_precedes_flock(self):
        project = self.work / "lock-project"
        shutil.copytree(ROOT / "scripts", project / "scripts")
        shutil.copytree(ROOT / "targets", project / "targets")
        marker = self.work / "dependencies-installed"
        executable(
            self.bin / "apt-get",
            '#!/bin/bash\necho "deps $*" >> "$CALLS"\nif [[ $1 == install ]]; then touch "$INSTALLED"; fi\n',
        )
        executable(
            self.bin / "flock",
            '#!/bin/bash\n[[ -f "$INSTALLED" ]] || { echo "flock unavailable before util-linux install" >&2; exit 127; }\necho lock >> "$CALLS"\n',
        )
        executable(
            self.bin / "git", '#!/bin/bash\necho source-check >> "$CALLS"\nexit 42\n'
        )
        executable(self.bin / "id", '#!/bin/sh\nprintf "%s\\n" "$TEST_UID"\n')
        executable(self.bin / "sudo", '#!/bin/sh\nexec "$@"\n')
        (project / "llvm").mkdir()
        for uid in ["0", "1001"]:
            with self.subTest(uid=uid):
                marker.unlink(missing_ok=True)
                self.log.unlink(missing_ok=True)
                result = run(
                    [str(project / "scripts/make"), "llvm"],
                    env=dict(
                        self.env,
                        BUILD_OS_LABEL="ubuntu22",
                        INSTALLED=str(marker),
                        TEST_UID=uid,
                    ),
                )
                self.assertEqual(result.returncode, 42, result.stderr)
                calls = self.log.read_text()
                self.assertLess(calls.index("deps install"), calls.index("lock"))
                self.assertLess(calls.index("lock"), calls.index("source-check"))

    def test_centos_repo_setup_replaces_native_rpm_file_names(self):
        repos = self.work / "repos"
        repos.mkdir()
        (repos / "CentOS-Base.repo").write_text(
            "mirrorlist=http://mirrorlist.centos.org\n#baseurl=http://mirror.centos.org/base\n"
        )
        for name in ["CentOS-SCLo-sclo.repo", "CentOS-SCLo-rh.repo"]:
            (repos / name).write_text("mirrorlist=http://mirrorlist.centos.org\n")
        executable(
            self.bin / "sudo-fixture",
            "#!/usr/bin/env python3\nimport glob,os,sys\na=[]\nfor v in sys.argv[1:]:\n if '/etc/yum.repos.d/' in v:\n  v=v.replace('/etc/yum.repos.d',os.environ['TEST_REPOS'])\n  a.extend(glob.glob(v) if '*' in v else [v])\n else:a.append(v)\nos.execvp(a[0],a)\n",
        )
        executable(
            self.bin / "yum",
            "#!/usr/bin/env python3\nimport os,pathlib,sys\np=pathlib.Path(os.environ['TEST_REPOS'])\nif 'install' in sys.argv:\n for n in ['CentOS-SCLo-scl.repo','CentOS-SCLo-scl-rh.repo']:(p/n).write_text('mirrorlist=http://mirrorlist.centos.org\\n')\nif 'makecache' in sys.argv:\n bad=[str(f) for f in p.glob('CentOS-SCLo-*.repo') if 'mirrorlist=' in f.read_text()]\n if bad:print('Cannot find a valid baseurl: '+str(bad),file=sys.stderr);sys.exit(1)\n",
        )
        result = run(
            [
                "bash",
                "-euo",
                "pipefail",
                "-c",
                'source "$1/scripts/host-dependencies.sh"; SUDO_CMD=sudo-fixture; centos_mirrors',
                "bash",
                str(ROOT),
            ],
            env=dict(self.env, TEST_REPOS=str(repos), RETRY_DELAY="0"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            {p.name for p in repos.glob("CentOS-SCLo-*.repo")},
            {"CentOS-SCLo-scl.repo", "CentOS-SCLo-scl-rh.repo"},
        )
        for name, component in [
            ("CentOS-SCLo-scl.repo", "sclo"),
            ("CentOS-SCLo-scl-rh.repo", "rh"),
        ]:
            text = (repos / name).read_text()
            self.assertIn(
                "https://mirrors.aliyun.com/centos-vault/7.9.2009/sclo/$basearch/"
                + component
                + "/",
                text,
            )

    def test_legacy_host_build_tools_use_only_binary_wheels(self):
        executable(
            self.bin / "python3", '#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\n'
        )
        result = run(
            [
                "bash",
                "-euo",
                "pipefail",
                "-c",
                'source "$1/scripts/host-dependencies.sh"; SUDO_CMD=""; install_python_build_tools',
                "bash",
                str(ROOT),
            ],
            env=self.env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text().splitlines()
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIn("--only-binary=:all:", call)
        self.assertIn("pip==21.3.1", calls[0])
        self.assertIn("cmake==3.27.9", calls[1])
        self.assertIn("ninja==1.11.1.1", calls[1])

    def test_source_verification_works_without_git_dash_c(self):
        executable(
            self.bin / "git",
            '#!/usr/bin/env python3\nimport os,sys\na=sys.argv[1:]\nif "-C" in a:print("Unknown option: -C",file=sys.stderr);sys.exit(129)\nassert os.getcwd()==os.environ["EXPECTED_LLVM_DIR"]\nif "rev-parse" in a:print(os.environ["EXPECTED_REVISION"])\n',
        )
        revision = run(
            [
                "bash",
                "-c",
                'source "$1/targets/versions"; printf "%s" "$LLVM_SOURCE_REVISION"',
                "bash",
                str(ROOT),
            ],
            env=self.env,
        ).stdout
        result = run(
            [
                "bash",
                "-euo",
                "pipefail",
                "-c",
                'source "$1/scripts/make"; verify_llvm_source',
                "bash",
                str(ROOT),
            ],
            env=dict(
                self.env,
                EXPECTED_LLVM_DIR=str(ROOT / "llvm"),
                EXPECTED_REVISION=revision,
            ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_runtime_build_reverts_partial_patches(self):
        fixture = self.work / 'project'
        source = fixture / 'llvm'
        source.mkdir(parents=True)
        for name, body in [('sample', 'before\n'), ('runtimes/CMakeLists.txt', 'fixture'), ('compiler-rt/marker', 'fixture')]:
            path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(body)
        def git(*args):
            result = run(['git', '-C', str(source), *args])
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        for args in [('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.invalid'), ('add', '.'), ('commit', '-qm', 'fixture')]:
            git(*args)
        revision = git('rev-parse', 'HEAD')
        patches = fixture / 'targets/test/patches'; patches.mkdir(parents=True)
        (patches / '0001.patch').write_text('--- a/sample\n+++ b/sample\n@@ -1 +1 @@\n-before\n+after\n')
        prefix = self.work / 'runtime-stage'; (prefix / 'bin').mkdir(parents=True)
        executable(prefix / 'bin/clang', '#!/bin/sh\necho "$TEST_RESOURCE_DIR"\n')
        command = 'source "$1/scripts/make"; PROJECT_ROOT="$2"; LLVM_SOURCE_REVISION="$3"; cmake() { return 83; }; build_compiler_rt test "$4"'
        for partial_patch in (False, True):
            if partial_patch:
                (patches / '0002.patch').write_text('--- a/sample\n+++ b/sample\n@@ -1 +1 @@\n-missing\n+wrong\n')
            result = run(['bash', '-euo', 'pipefail', '-c', command, 'bash', str(ROOT), str(fixture), revision, str(prefix)], env=dict(self.env, TEST_RESOURCE_DIR=str(fixture / 'resource')))
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(git('status', '--porcelain'), '')
            self.assertEqual((source / 'sample').read_text(), 'before\n')
            self.assertFalse((source / 'sample.rej').exists())

    def test_generated_wrappers_survive_spaces_and_forward_arguments(self):
        root = self.work / "installed toolchain"
        (root / "bin").mkdir(parents=True)
        executable(
            root / "bin/clang",
            "#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n",
        )
        p = run(
            [
                "bash",
                "-euo",
                "pipefail",
                "-c",
                'source "$1/scripts/make"; CLANG_ARGS=""; write_target_wrappers "$2" x86_64-unknown-linux-musl',
                "bash",
                str(ROOT),
                str(root),
            ],
            env=self.env,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        p = run(
            [
                str(root / "bin/x86_64-unknown-linux-musl-clang"),
                "source file.c",
                "-o",
                "output file",
            ],
            env=self.env,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        args = json.loads(p.stdout)
        self.assertEqual(
            args,
            [
                "--target=x86_64-unknown-linux-musl",
                "--sysroot=" + str(root / "bin/../x86_64-unknown-linux-musl/sysroot"),
                "source file.c",
                "-o",
                "output file",
            ],
        )

    def test_environment_activation_runner_and_cmake(self):
        roots = {libc: self.toolchain('x86_64-unknown-linux-' + libc) for libc in ('gnu', 'musl')}
        probe = self.work / 'probe'
        executable(probe, '#!/bin/sh\n[ -z "${LD_PRELOAD+x}${LD_AUDIT+x}" ] || exit 89\nprintf "ENV PASS:%s\\n" "$1"\n')
        code = '''before=$-; shell_options=$(set +o); original=$PATH
source "$TOOL/setvars.sh" --quiet || exit
[ "$before" = "$-" ] && [ "$shell_options" = "$(set +o)" ] || exit 81
[ "$CLANG_CROSS_ROOT" = "$TOOL" ] && [ "$CLANG_CROSS_TARGET" = "$TEST_TARGET" ] || exit 82
[ "$CLANG_CROSS_CLANG_VERSION" = 21.0.0 ] && [ "$CC" = "$TEST_TARGET-clang" ] && [ "$CXX" = "$TEST_TARGET-clang++" ] || exit 83
[ "$CLANG_CROSS_LDFLAGS" = -fuse-ld=lld ] && [ "$LD" = ld.lld ] && [ "$READELF" = llvm-readelf ] || exit 84
first=$PATH; source "$TOOL/setvars.sh" --quiet || exit
[ "$first" = "$PATH" ] || exit 85
source "$TOOL/setvars.sh" --force --no-lld --quiet || exit
[ -z "$CLANG_CROSS_LDFLAGS" ] && [ "$first" = "$PATH" ] || exit 86
source "$OTHER/setvars.sh" --quiet || exit
case :$PATH: in *:"$TOOL/bin":*) exit 87 ;; esac
clang_cross_deactivate
[ "$PATH" = "$original" ] && [ -z "${CLANG_CROSS_ROOT+x}" ] || exit 88
CC=host; source "$TOOL/setvars.sh" --quiet --no-cc-env || exit
[ "$CC" = host ] || exit 89
clang_cross_deactivate
[ "$CC" = host ] || exit 90
if source "$TOOL/setvars.sh" --wrong; then exit 91; else rc=$?; fi
[ "$rc" = 64 ] && [ "$PATH" = "$original" ]
'''
        for libc, root in roots.items():
            for shell in tuple(shutil.which(s) for s in ('bash', 'zsh') if shutil.which(s)):
                env = dict(self.env, TOOL=str(root), OTHER=str(roots['musl' if libc == 'gnu' else 'gnu']), TEST_TARGET='x86_64-unknown-linux-' + libc)
                p = run([shell, '-c', code], env=env)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                p = run([str(root / 'bin/clang-cross-run'), str(probe), 'argument with spaces'], env=dict(env, LD_PRELOAD='', LD_AUDIT=''))
                self.assertEqual(p.returncode, 0, p.stderr); self.assertEqual(p.stdout, 'ENV PASS:argument with spaces\n')
            exported = run(['bash', '-c', 'source "$1/setvars.sh" --quiet; env', 'bash', str(root)], env=dict(self.env, TEST_TARGET='x86_64-unknown-linux-' + libc))
            self.assertEqual(exported.returncode, 0, exported.stderr)
            values = dict(line.split('=', 1) for line in exported.stdout.splitlines() if '=' in line)
            cargo = 'CARGO_TARGET_X86_64_UNKNOWN_LINUX_' + libc.upper()
            self.assertEqual(values[cargo + '_LINKER'], str(root / 'bin' / ('x86_64-unknown-linux-' + libc + '-clang')))
            self.assertEqual(values[cargo + '_RUNNER'], str(root / 'bin/clang-cross-run'))
            check = self.work / 'check.cmake'
            check.write_text('include("' + str(root / 'toolchain.cmake') + '")\nif(NOT CMAKE_SYSROOT STREQUAL "' + str(root / ('x86_64-unknown-linux-' + libc) / 'sysroot') + '")\nmessage(FATAL_ERROR "bad sysroot")\nendif()\n')
            p = run(['cmake', '-P', str(check)], env=self.env); self.assertEqual(p.returncode, 0, p.stderr)
            p = run(['shellcheck', '-x', '-S', 'style', str(root / 'bin/clang-cross-run'), str(root / 'setvars.sh')]); self.assertEqual(p.returncode, 0, p.stdout)

    def test_environment_rejects_escapes_without_changing_caller(self):
        outside = self.work / 'outside'; outside.mkdir(); (outside / 'library.so').touch()
        active = self.work / 'active'; self.toolchain('x86_64-unknown-linux-gnu').rename(active)
        self.env['ACTIVE'] = str(active)
        code = 'leak=${LEAK_PATH-}; unset LEAK_PATH; source "$ACTIVE/setvars.sh" --quiet || exit 97; export LEAK_PATH="$leak"; original=$PATH; if source "$TOOL/setvars.sh" --quiet; then exit 99; fi; [ "$PATH" = "$original" ] && [ "$CLANG_CROSS_ROOT" = "$ACTIVE" ] && [[ ":$PATH:" == *":$ACTIVE/bin:"* ]]'
        for libc in ('gnu', 'musl'):
            target = 'x86_64-unknown-linux-' + libc; root = self.toolchain(target)
            for name in ('libgcc_s.so.1', 'libstdc++.so.6', 'libc++.so', 'libc++abi.so', 'libunwind.so'):
                p = run(['bash', '-c', code], env=dict(self.env, TOOL=str(root), TEST_TARGET=target, LEAK_RUNTIME=name, LEAK_PATH=str(outside / 'library.so')))
                self.assertEqual(p.returncode, 0, p.stderr); self.assertIn('escapes', p.stderr)
            for relative in ('bin/' + target + '-clang', 'bin/' + target + '-clang++', target + '/sysroot'):
                path = root / relative; saved = self.work / 'saved'; path.rename(saved); path.symlink_to(outside)
                p = run(['bash', '-c', code], env=dict(self.env, TOOL=str(root), TEST_TARGET=target))
                self.assertEqual(p.returncode, 0, p.stderr); self.assertIn('escapes', p.stderr); path.unlink(); saved.rename(path)
            for relative in ('setvars.sh', 'bin/clang-cross-run', 'toolchain.cmake'):
                path = root / relative; saved = self.work / 'saved'; path.rename(saved); path.symlink_to(outside / 'library.so')
                p = run([str(ROOT / 'scripts/verify-toolchain.sh'), target, str(root), '21.0.0', '1.2.6' if libc == 'musl' else '-', 'ld-musl-x86_64.so.1' if libc == 'musl' else '-'], env=dict(self.env, TEST_TARGET=target))
                self.assertNotEqual(p.returncode, 0); self.assertIn('host runtime leakage', p.stderr)
                path.unlink(); saved.rename(path)
            if libc == 'gnu':
                gconv = root / target / 'sysroot/usr/lib/gconv'; gconv.parent.mkdir(parents=True); gconv.symlink_to(outside)
                p = run([str(root / 'bin/clang-cross-run'), '/bin/true'], env=dict(self.env, TEST_TARGET=target))
                self.assertNotEqual(p.returncode, 0); self.assertIn('gconv escapes', p.stderr)

    def test_isolated_staging_jobs_archives_and_base_repository(self):
        code = '''source "$1/scripts/make"
PROJECT_ROOT=$2; HOST_SYSTEM=ubuntu22; CT_URL=https://example.invalid; LLVM_ARCHIVE=host.tar.xz
verify_llvm_source() { :; }
cmake() {
 for flag in "$@"; do case $flag in -DCMAKE_INSTALL_PREFIX=*) prefix=${flag#*=} ;; esac; done
 mkdir -p "$prefix/bin" "$prefix/lib"
 printf '#!/bin/sh\\necho clang version %s\\n' "$LLVM_VERSION" > "$prefix/bin/clang"
 chmod +x "$prefix/bin/clang"
}
ninja() { printf 'ninja=%s\\n' "$*" >> "$CALLS"; }
strip() { :; }
download_toolchain() {
 printf 'url=%s\\n' "$1" >> "$CALLS"
 mkdir -p "$PROJECT_ROOT/$2/include" "$PROJECT_ROOT/$2/lib/gcc" "$PROJECT_ROOT/$2/$2/sysroot/lib"
}
verify_base_toolchain() { :; }
build_compiler_rt() { printf '%s' "$2" > "$RUNTIME_PREFIX"; }
build_llvm
build_target "$3"
'''
        cases = [('gnu', '', 0), ('musl', '', 0), ('gnu', 'gcc-cross', 42), ('musl', 'gcc-cross', 0)]
        for libc, repo, status in cases:
            with self.subTest(libc=libc, repo=repo):
                project = self.work / (libc + (repo or 'default')); (project / 'scripts').mkdir(parents=True)
                shutil.copytree(ROOT / 'targets', project / 'targets')
                stage = project / 'stage'; stage.mkdir()
                executable(project / 'scripts/verify-toolchain.sh', '#!/bin/bash\ntest -x "$2/setvars.sh" && test -x "$2/bin/clang-cross-run" && test -f "$2/toolchain.cmake" || exit 91\nexit "$FAIL_VERIFY"\n')
                target = 'x86_64-unknown-linux-' + libc
                self.log.unlink(missing_ok=True)
                env = dict(self.env, BUILD_OS_LABEL='ubuntu22', BASE_TOOLCHAIN_REPO=repo, BUILD_JOBS='3', XZ_OPT='-T1', CLANG_CROSS_STAGE_DIR=str(stage), RUNTIME_PREFIX=str(project / 'runtime-prefix'), FAIL_VERIFY=str(status))
                p = run(['bash', '-euo', 'pipefail', '-c', code, 'bash', str(ROOT), str(project), target], env=env)
                self.assertEqual(p.returncode, status, p.stdout + p.stderr)
                self.assertEqual(list(stage.iterdir()), []); self.assertFalse((project / target).exists())
                self.assertIn(str(stage) + '/clang-cross-target.', (project / 'runtime-prefix').read_text())
                calls = self.log.read_text(); self.assertIn('ninja=-j 3 install', calls)
                version = 'v0.0.1' if repo or libc == 'gnu' else 'v1.2.6-r1'
                self.assertIn('/' + (repo or libc + '-cross') + '/releases/download/' + version + '/' + target + '-ubuntu22.tar.xz', calls)
                with tarfile.open(project / (target + '.tar.xz')) as archive:
                    self.assertIn(target + '/setvars.sh', archive.getnames())
                    self.assertIn(target + '/bin/clang-cross-run', archive.getnames())

    def archive(self, unsafe=False):
        p = self.work / "input.tar.xz"
        target = "x86_64-unknown-linux-musl"
        with tarfile.open(p, "w:xz") as t:
            for name in [
                "bin/gcc",
                "include/header",
                "lib/gcc/runtime",
                target + "/sysroot/lib/libc.so",
            ]:
                m = tarfile.TarInfo(target + "/" + name)
                m.size = 2
                t.addfile(m, io.BytesIO(b"ok"))
            if unsafe:
                m = tarfile.TarInfo(target + "/escape")
                m.type = tarfile.SYMTYPE
                m.linkname = "/etc"
                t.addfile(m)
        pathlib.Path(str(p) + ".sha256").write_text(
            hashlib.sha256(p.read_bytes()).hexdigest() + "\n"
        )
        return p

    def test_download_digest_layout_existing_destination_and_link_isolation(self):
        archive = self.archive()
        dest = self.work / "output"
        cmd = [str(ROOT / "scripts/download-toolchain.sh"), archive.as_uri(), str(dest)]
        p = run(cmd, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((dest / "x86_64-unknown-linux-musl/bin/gcc").read_text(), "ok")
        self.assertNotEqual(run(cmd, env=self.env).returncode, 0)
        shutil.rmtree(dest)
        pathlib.Path(str(archive) + ".sha256").write_text("bad\n")
        self.assertNotEqual(run(cmd, env=self.env).returncode, 0)
        self.assertFalse(dest.exists())
        archive = self.archive(unsafe=True)
        p = run(cmd, env=self.env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("escaping archive link", p.stderr)
        self.assertFalse(dest.exists())

    def test_setvars_execution_guard(self):
        result = run(['bash', str(ROOT / 'scripts/setvars.sh')], env=self.env)
        self.assertEqual(result.returncode, 2)
        self.assertIn('This script must be sourced', result.stderr)

    def toolchain(self, target):
        root = self.work / ("toolchain with spaces " + target)
        (root / "bin").mkdir(parents=True)
        result = run(['bash', '-euo', 'pipefail', '-c', 'source "$1/scripts/make"; CLANG_ARGS=""; write_target_wrappers "$2" "$3"', 'bash', str(ROOT), str(root), target], env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        executable(root / 'bin/llvm-readelf', '#!/bin/sh\nexit 0\n')
        lib = root / target / "sysroot/lib"
        lib.mkdir(parents=True)
        for name in [
            "libgcc_s.so.1",
            "libstdc++.so.6",
            "libc.so.6",
            "libc++.so",
            "libc++abi.so",
            "libunwind.so",
            "libc++.a",
            "libc++abi.a",
            "libunwind.a",
        ]:
            (lib / name).touch()
        loader = lib / (
            "ld-musl-x86_64.so.1" if target.endswith("musl") else "ld-linux-x86-64.so.2"
        )
        executable(
            loader,
            '#!/bin/bash\nif [[ $# == 0 ]]; then echo "musl Version 1.2.6"; exit 1; fi\n'
            'if [[ $1 == --version ]]; then echo "--version: cannot open shared object file" >&2; exit 127; fi\n'
            'if [[ $1 == */libc.so.6 ]]; then echo "GNU C Library stable release version 2.17"; exit "${GLIBC_STATUS:-0}"; fi\n'
            'shift 2\nexec "$@"\n',
        )
        compiler = """#!/usr/bin/env python3
import os,pathlib,sys
args=sys.argv[1:]
with open(os.environ['CALLS'],'a') as f:f.write('compile '+repr(args)+'\\n')
root=pathlib.Path(__file__).resolve().parents[1]
if '--version' in args:print('clang version 21.0.0');sys.exit()
query=next((a for a in args if a.startswith('-print-file-name=')),None)
if query:
 name=query.split('=')[1]
 leak=os.environ.get('LEAK_PATH') if os.environ.get('LEAK_RUNTIME') in [None,name] else None
 print(leak or str(root/pathlib.Path(__file__).name.split('-clang')[0]/'sysroot/lib'/name));sys.exit()
if '-static' in args and '-stdlib=libc++' in args and '-pthread' not in args:
 print('ld.lld: error: duplicate symbol: __lll_lock_wait_private',file=sys.stderr);sys.exit(1)
out=pathlib.Path(args[args.index('-o')+1]);name=out.name
messages={'hello-c':'C PASS','hello-dynamic':'C PASS','hello-cxx':'C++ PASS','hello-cxx-dynamic':'C++ PASS','hello-libcxx-dynamic':'LIBCXX PASS','hello-libcxx-static':'LIBCXX PASS','iconv':'iconv PASS','reflection':'REFLECTION PASS','asan':'AddressSanitizer','ubsan':'runtime error: signed integer overflow','lsan':'LeakSanitizer','tsan':'ThreadSanitizer: data race'}
out.write_text('#!/bin/sh\\necho "'+messages[name]+'"\\nexit '+('1' if name in ['asan','ubsan','lsan','tsan'] else '0')+'\\n');out.chmod(0o755)
"""
        executable(root / "bin" / f"{target}-clang", compiler)
        executable(root / "bin" / f"{target}-clang++", compiler)
        executable(root / "bin/ld.lld", '#!/bin/sh\necho "LLD 21.0.0"\n')
        executable(self.bin / "setarch", '#!/bin/bash\nshift 2\nexec "$@"\n')
        executable(
            self.bin / "timeout",
            '#!/bin/bash\necho "timeout $*" >> "$CALLS"\n[[ $1 == 30 ]] || exit 97\nshift\nif [[ ${PROBE_TIMEOUT:-} == asan && ${*: -1} == */asan ]]; then echo AddressSanitizer;exit 124;fi\nexec "$@"\n',
        )
        return root

    def test_gnu_loader_probe_executes_target_libc_and_propagates_failure(self):
        target = "x86_64-unknown-linux-gnu"
        root = self.toolchain(target)
        cmd = [str(ROOT / "scripts/verify-toolchain.sh"), target, str(root), "21.0.0"]
        for status in [0, 42, 124, 127]:
            with self.subTest(status=status):
                self.log.unlink(missing_ok=True)
                p = run(cmd, env=dict(self.env, TEST_TARGET=target, GLIBC_STATUS=str(status)))
                self.assertEqual(p.returncode, status, p.stdout + p.stderr)
                calls = self.log.read_text()
                loader = root / target / "sysroot/lib/ld-linux-x86-64.so.2"
                libc = root / target / "sysroot/lib/libc.so.6"
                self.assertIn(f"timeout 30 {loader} {libc}\n", calls)
                if status:
                    self.assertNotIn("-print-file-name", calls)

    def test_gnu_and_musl_probe_all_required_features(self):
        for libc in ["gnu", "musl"]:
            self.log.unlink(missing_ok=True)
            target = "x86_64-unknown-linux-" + libc
            root = self.toolchain(target)
            env = dict(self.env, TEST_TARGET=target)
            cmd = [
                str(ROOT / "scripts/verify-toolchain.sh"),
                target,
                str(root),
                "21.0.0",
            ] + (["1.2.6", "ld-musl-x86_64.so.1"] if libc == "musl" else [])
            p = run(cmd, env=env)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            calls = self.log.read_text()
            for flag in [
                "-static",
                "-stdlib=libc++",
                "-fsanitize=address",
                "-fsanitize=undefined",
                "-fsanitize=leak",
                "-fsanitize=thread",
                "-freflection",
                "-std=c++26",
            ]:
                self.assertIn(flag, calls)
            self.assertGreaterEqual(calls.count("timeout 30 "), 12)

    def test_external_search_directory_and_selected_cpp_runtime_rejected(self):
        target = "x86_64-unknown-linux-gnu"
        root = self.toolchain(target)
        outside = self.work / "external"
        outside.mkdir()
        library = root / target / "sysroot/usr/lib"
        library.parent.mkdir()
        library.symlink_to(outside, target_is_directory=True)
        cmd = [str(ROOT / "scripts/verify-toolchain.sh"), target, str(root), "21.0.0"]
        p = run(cmd, env=dict(self.env, TEST_TARGET=target))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("host runtime leakage", p.stderr)
        library.unlink()
        library.mkdir()
        (outside / "libunwind.so").touch()
        p = run(
            cmd,
            env=dict(
                self.env,
                TEST_TARGET=target,
                LEAK_RUNTIME="libunwind.so",
                LEAK_PATH=str(outside / "libunwind.so"),
            ),
        )
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("host runtime leakage", p.stderr)

    def test_version_prefix_is_not_a_complete_version(self):
        target = "x86_64-unknown-linux-gnu"
        root = self.toolchain(target)
        for name in [target + "-clang", target + "-clang++"]:
            p = root / "bin" / name
            p.write_text(
                p.read_text().replace("clang version 21.0.0", "clang version 21.0.01")
            )
        cmd = [str(ROOT / "scripts/verify-toolchain.sh"), target, str(root), "21.0.0"]
        result = run(cmd, env=dict(self.env, TEST_TARGET=target))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Clang version mismatch", result.stderr)
        result = run(
            [
                "bash",
                "-c",
                'source "$1/scripts/make"; verify_clang_version "$2"',
                "bash",
                str(ROOT),
                str(root / "bin" / (target + "-clang")),
            ],
            env=dict(self.env, TEST_TARGET=target),
        )
        self.assertNotEqual(result.returncode, 0)

    def test_musl_and_lld_version_tokens_are_exact(self):
        target = "x86_64-unknown-linux-musl"
        root = self.toolchain(target)
        loader = root / target / "sysroot/lib/ld-musl-x86_64.so.1"
        original = loader.read_text()
        loader.write_text(original.replace("Version 1.2.6", "Version 1.2.60"))
        cmd = [
            str(ROOT / "scripts/verify-toolchain.sh"),
            target,
            str(root),
            "21.0.0",
            "1.2.6",
            "ld-musl-x86_64.so.1",
        ]
        result = run(cmd, env=dict(self.env, TEST_TARGET=target))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("musl version mismatch", result.stderr)
        loader.write_text(original)
        lld = root / "bin/ld.lld"
        lld.write_text(lld.read_text().replace("LLD 21.0.0", "LLD 21.0.01"))
        result = run(cmd, env=dict(self.env, TEST_TARGET=target))
        self.assertNotEqual(result.returncode, 0)

    def test_outside_runtime_and_probe_timeout_fail_closed(self):
        target = "x86_64-unknown-linux-gnu"
        root = self.toolchain(target)
        outside = self.work / "host.so"
        outside.touch()
        cmd = [
            str(ROOT / "scripts/verify-toolchain.sh"),
            target,
            str(root),
            "21.0.0",
            "-",
            "-",
        ]
        p = run(cmd, env=dict(self.env, TEST_TARGET=target, LEAK_PATH=str(outside)))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("host runtime leakage", p.stderr)
        p = run(cmd, env=dict(self.env, TEST_TARGET=target, PROBE_TIMEOUT="asan"))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("timed out", p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
