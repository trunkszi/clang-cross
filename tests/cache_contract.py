"""Cached toolchain verification and trusted host-LLVM recovery contracts."""

import hashlib, json, os, pathlib, shutil, tarfile, unittest

import scripts_contract as contracts

ROOT = contracts.ROOT
run = contracts.run
executable = contracts.executable


class Cache(unittest.TestCase):
    setUp = contracts.Scripts.setUp
    toolchain = contracts.Scripts.toolchain

    def cached_archive(self, target):
        root = self.toolchain(target)
        for name in ["include", "lib"]:
            (root / name).mkdir()
        executable(root / "bin/clang", '#!/bin/sh\necho "clang version 21.0.0"\n')
        (root / "bin/clang++").symlink_to("clang")
        for name in ["lib/clang/21/include/header.h", "lib/clang/21/lib/linux/libasan.a", "lib/gcc/target/16/libgcc.a"]:
            file = root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.touch()
        archive = self.work / (target + ".tar.xz")
        with tarfile.open(archive, "w:xz") as output:
            output.add(root, arcname=target)
        pathlib.Path(str(archive) + ".sha256").write_text(
            hashlib.sha256(archive.read_bytes()).hexdigest() + "\n"
        )
        return archive

    def test_seed_excludes_target_libraries(self):
        target = "x86_64-unknown-linux-musl"
        source = self.cached_archive(target)
        seed = self.work / "seed"
        seed.mkdir()
        archive = seed / (target + "-centos7.tar.xz")
        shutil.copyfile(source, archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        pathlib.Path(str(archive) + ".sha256").write_text(digest + "\n")
        receipt = dict(schema="clang-cross-cell/1", base_sha="1" * 40,
                       llvm_sha=run(["bash", "-c", 'source "$1/targets/versions"; echo "$LLVM_SOURCE_REVISION"', "bash", str(ROOT)]).stdout.strip(),
                       versions_sha256=hashlib.sha256((ROOT / "targets/versions").read_bytes()).hexdigest(),
                       target=target, host="centos7", archive_sha256=digest)
        pathlib.Path(str(archive) + ".provenance.json").write_text(json.dumps(receipt))
        executable(self.bin / "gh", '''#!/usr/bin/env python3
import base64,json,os,pathlib,sys
path=sys.argv[-1]
if path=='--help':
 print('--allow-escape-sequences' if os.getenv('NEW_GH') else 'legacy api');sys.exit()
if '/contents/' in path:
 name=path.split('/contents/')[1].split('?')[0]
 text=(pathlib.Path(os.environ['SEED_ROOT'])/name).read_text()
 if os.getenv('BAD_RECIPE') and name=='scripts/make':text=text.replace('LLVM_ENABLE_RTTI=ON','LLVM_ENABLE_RTTI=OFF')
 print(json.dumps({'content':base64.b64encode(text.encode()).decode()}))
elif path.endswith('/logs'):
 if os.getenv('NEW_GH') and '--allow-escape-sequences' not in sys.argv:
  print('the response contains terminal escape sequences',file=sys.stderr);sys.exit(1)
 print('2026-10-27T00:00:00Z Expected Digest: sha256:'+'3'*64)
 print('2026-10-27T00:00:01Z Digest: sha256:'+'2'*64)
elif '/jobs?' in path:print(json.dumps({'jobs':[{'id':7,'name':'build (x86_64-unknown-linux-musl, centos:7.9.2009, centos7)','conclusion':'success'}]}))
else:print(json.dumps({'status':'completed','head_repository':{'full_name':os.getenv('SEED_REPO','fixture/clang-cross')},'head_branch':'main','path':'.github/workflows/release.yaml','event':'workflow_dispatch','head_sha':'1'*40}))
''')
        output = self.work / "host-llvm.tar.xz"
        command = ["python3", str(ROOT / "scripts/seed-llvm.py"), "--run-id", "123", "--host", "centos7",
                   "--directory", str(seed), "--image-digest", "sha256:" + "2" * 64, "--output", str(output)]
        env = dict(self.env, SEED_ROOT=str(ROOT), GITHUB_REPOSITORY="fixture/clang-cross")
        for extra in [dict(BAD_RECIPE="1"), dict(SEED_REPO="foreign/repo")]:
            p = run(command, env=dict(env, **extra))
            self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertFalse(output.exists())
        p = run(command, env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = run(command, env=dict(env, NEW_GH="1"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = run(["python3", str(ROOT / "scripts/seed-llvm.py"), "--run-id", "123",
                 "--host", "centos7", "--image-only"], env=dict(env, NEW_GH="1"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.strip(), "sha256:" + "2" * 64)
        with tarfile.open(output) as recovered:
            names = recovered.getnames()
        self.assertIn("clang-cross/bin/clang", names)
        self.assertIn("clang-cross/bin/clang++", names)
        self.assertIn("clang-cross/lib/clang/21/include/header.h", names)
        self.assertFalse(any("/lib/gcc" in name or "/lib/clang/21/lib" in name or target in name for name in names))
        output.unlink()
        archive.write_bytes(b"corrupt")
        p = run(command, env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(output.exists())

    def test_cached_archive_rechecks_runtime_failures(self):
        target = "x86_64-unknown-linux-gnu"
        archive = self.cached_archive(target)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        for status in [0, 42, 124]:
            with self.subTest(status=status):
                p = run(
                    ["bash", str(ROOT / "scripts/verify-archive.sh"), target, str(archive)],
                    env=dict(self.env, TEST_TARGET=target, GLIBC_STATUS=str(status)),
                )
                self.assertEqual(p.returncode, status, p.stdout + p.stderr)
                self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), digest)
                self.assertFalse(list(self.work.glob("clang-cross-cached.*")))
        archive.write_bytes(b"corrupt")
        p = run(
            ["bash", str(ROOT / "scripts/verify-archive.sh"), target, str(archive)],
            env=dict(self.env, TEST_TARGET=target),
        )
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("checksum mismatch", p.stderr)

    def test_cached_make_skips_all_compilation(self):
        target = "x86_64-unknown-linux-gnu"
        archive = self.cached_archive(target)
        project = self.work / "cached project"
        shutil.copytree(ROOT / "scripts", project / "scripts")
        shutil.copytree(ROOT / "targets", project / "targets")
        for name in [archive, pathlib.Path(str(archive) + ".sha256")]:
            shutil.copyfile(name, project / name.name)
        command = (
            'source "$1/scripts/make"; '
            'detect_system() { echo ubuntu22; }; '
            'verify_llvm_source() { echo admitted-source; }; '
            'build_llvm() { echo unexpected-compilation >&2; return 93; }; '
            'main "$2" --skip-deps'
        )
        for status in [0, 42]:
            with self.subTest(status=status):
                p = run(
                    ["bash", "-euo", "pipefail", "-c", command, "bash", str(project), target],
                    env=dict(self.env, TEST_TARGET=target, GLIBC_STATUS=str(status), REUSE_PACKAGED_TOOLCHAIN="true"),
                )
                self.assertEqual(p.returncode, status, p.stdout + p.stderr)
                self.assertIn("admitted-source", p.stdout)
                self.assertIn("without compilation", p.stdout)
                self.assertNotIn("unexpected-compilation", p.stderr)
        args = ["bash", "-euo", "pipefail", "-c", command, "bash", str(project), target]
        p = run(args, env=dict(self.env, TEST_TARGET=target, REUSE_PACKAGED_TOOLCHAIN="false"))
        self.assertEqual(p.returncode, 93, p.stdout + p.stderr)
        (project / archive.name).unlink()
        p = run(args, env=dict(self.env, TEST_TARGET=target, REUSE_PACKAGED_TOOLCHAIN="true"))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("cache is missing", p.stderr)
        self.assertNotIn("unexpected-compilation", p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
