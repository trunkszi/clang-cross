"""Real temporary Git repositories exercise source synchronization without a network."""

import json, os, pathlib, re, shutil, subprocess, tempfile, unittest

import sys

sys.dont_write_bytecode = True

ROOT = pathlib.Path(__file__).resolve().parents[1]


def call(args, cwd=None, **kw):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, **kw)


class Sync(unittest.TestCase):
    def setUp(self):
        temp_base = pathlib.Path(os.environ.get("TMPDIR") or "/tmp").resolve()
        if temp_base == ROOT or ROOT in temp_base.parents:
            raise RuntimeError("Test TMPDIR must be outside the repository")
        self.temp = tempfile.TemporaryDirectory(
            dir=os.environ.get("TMPDIR") or "/tmp", prefix="clang-cross-sync."
        )
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        shutil.copytree(ROOT / "scripts", self.repo / "scripts")
        (self.repo / "targets/x86_64-unknown-linux-musl/patches").mkdir(parents=True)
        self.source = self.repo / "llvm"
        self.source.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.source / "llvm").mkdir()
        (self.source / "llvm/CMakeLists.txt").write_text(
            "set(LLVM_VERSION_MAJOR 21)\nset(LLVM_VERSION_MINOR 0)\nset(LLVM_VERSION_PATCH 0)\n"
        )
        (self.source / "sample").write_text("before\n")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")
        self.old = self.git("rev-parse", "HEAD").strip()
        (self.source / "llvm/CMakeLists.txt").write_text(
            "set(LLVM_VERSION_MAJOR 22)\nset(LLVM_VERSION_MINOR 1)\nset(LLVM_VERSION_PATCH 3)\n"
        )
        self.git("add", ".")
        self.git("commit", "-qm", "candidate")
        self.new = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "--detach", self.old)
        self.versions = self.repo / "targets/versions"
        accepted = (ROOT / "targets/versions").read_text()
        pin = re.search(r"LLVM_SOURCE_REVISION:-([0-9a-f]{40})", accepted)[1]
        self.versions.write_text(accepted.replace(pin, self.old))
        self.notes = self.repo / "targets/x86_64-unknown-linux-musl/patches/README.md"
        self.notes.write_text("Based on LLVM 21.0.0\n(commit `" + self.old + "`)\n")
        self.patch = self.notes.parent / "0001.patch"
        self.patch.write_text(
            "--- a/sample\n+++ b/sample\n@@ -1 +1 @@\n-before\n+after\n"
        )
        self.payload = self.root / "commit.json"
        self.summary = self.root / "summary.md"

    def git(self, *args):
        p = call(["git", "-C", str(self.source), *args])
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def run_sync(self, sha, mode="--apply", **extra):
        self.payload.write_text(json.dumps({"sha": sha}))
        env = dict(
            os.environ,
            UPSTREAM_COMMIT_JSON_FILE=str(self.payload),
            UPSTREAM_LLVM_DIR=str(self.source),
            **extra,
        )
        return call(
            [
                "bash",
                str(self.repo / "scripts/sync-upstream.sh"),
                mode,
                "--summary-file",
                str(self.summary),
            ],
            env=env,
        )

    def test_unchanged_has_no_mutations(self):
        before = self.versions.read_bytes()
        p = self.run_sync(self.old)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("status=no_update", p.stdout)
        self.assertIn("should_build=false", p.stdout)
        self.assertEqual(before, self.versions.read_bytes())
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.old)

    def test_apply_updates_pin_version_and_provenance_after_preflight(self):
        p = self.run_sync(self.new)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("should_build=true", p.stdout)
        self.assertIn(self.new, self.versions.read_text())
        self.assertIn("22.1.3", self.versions.read_text())
        self.assertIn(self.new, self.notes.read_text())
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.new)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual((self.source / "sample").read_text(), "before\n")

    def test_check_does_not_apply(self):
        p = self.run_sync(self.new, "--check")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.old)
        self.assertIn(self.old, self.versions.read_text())

    def test_conflict_reports_hunks_and_preserves_state(self):
        self.patch.write_text(self.patch.read_text().replace("-before", "-missing"))
        original = self.versions.read_bytes()
        p = self.run_sync(self.new)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("UPSTREAM_PATCH_CONFLICT", p.stdout + p.stderr)
        summary = self.summary.read_text()
        for expected in [
            self.old,
            self.new,
            "0001.patch",
            "FAILED",
            "Published v0.0.1 Release: UNTOUCHED",
            "compare/",
            "Manual repair",
        ]:
            self.assertIn(expected, summary)
        self.assertEqual(original, self.versions.read_bytes())
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.old)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_discovery_failure_is_not_no_update(self):
        p = self.run_sync("not-a-sha")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("DISCOVERY_FAILED", p.stderr + p.stdout)
        self.assertNotIn("status=no_update", p.stdout)

    def test_scheduled_no_update_skips_and_update_selects_full_matrix(self):
        for sha, expected in [(self.old, "false"), (self.new, "true")]:
            self.payload.write_text(json.dumps({"sha": sha}))
            output = self.root / "outputs"
            output.write_text("")
            p = call(
                ["bash", str(self.repo / "scripts/prepare-matrix.sh")],
                env=dict(
                    os.environ,
                    GITHUB_EVENT_NAME="schedule",
                    GITHUB_SHA=self.old,
                    GITHUB_OUTPUT=str(output),
                    UPSTREAM_COMMIT_JSON_FILE=str(self.payload),
                    UPSTREAM_LLVM_DIR=str(self.source),
                ),
            )
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            lines = output.read_text().splitlines()
            self.assertIn("should_build=" + expected, lines)
            matrix = json.loads(next(v[7:] for v in lines if v.startswith("matrix=")))
            self.assertEqual(len(matrix["include"]), 8)

    def test_candidate_fetch_failure_is_discovery_failed(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        real_git = shutil.which("git")
        (bin_dir / "git").write_text(
            "#!/usr/bin/env python3\nimport os,sys\na=sys.argv[1:]\nif 'cat-file' in a:sys.exit(1)\nif 'fetch' in a:print('mock fetch failure',file=sys.stderr);sys.exit(128)\nos.execv("
            + repr(real_git)
            + ", ["
            + repr(real_git)
            + ",*a])\n"
        )
        (bin_dir / "git").chmod(0o755)
        self.payload.write_text(json.dumps({"sha": self.new}))
        env = dict(
            os.environ,
            UPSTREAM_COMMIT_JSON_FILE=str(self.payload),
            PATH=str(bin_dir) + ":" + os.environ["PATH"],
        )
        env.pop("UPSTREAM_LLVM_DIR", None)
        result = call(
            ["bash", str(self.repo / "scripts/sync-upstream.sh"), "--apply"], env=env
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DISCOVERY_FAILED", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.old)

    def test_missing_or_commented_version_rejects_update(self):
        for text in [
            "# version moved\n",
            "# set(LLVM_VERSION_MAJOR 99)\n# set(LLVM_VERSION_MINOR 1)\n# set(LLVM_VERSION_PATCH 2)\n",
        ]:
            self.git("checkout", "-q", "--detach", self.new)
            (self.source / "llvm/CMakeLists.txt").write_text(text)
            self.git("add", ".")
            self.git("commit", "-qm", "invalid version fixture")
            revision = self.git("rev-parse", "HEAD").strip()
            self.git("checkout", "-q", "--detach", self.old)
            before = self.versions.read_bytes()
            result = self.run_sync(revision)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "DISCOVERY_FAILED: cannot parse candidate LLVM version", result.stderr
            )
            self.assertEqual(before, self.versions.read_bytes())
            self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.old)

    def test_candidate_version_ignores_line_and_bracket_comments(self):
        self.git("checkout", "-q", "--detach", self.new)
        path = self.source / "cmake/Modules/LLVMVersion.cmake"
        path.parent.mkdir(parents=True)
        path.write_text(
            "#[=[\nset(LLVM_VERSION_MAJOR 99)\nset(LLVM_VERSION_MINOR 99)\nset(LLVM_VERSION_PATCH 99)\n]=]\n# set(LLVM_VERSION_MAJOR 98)\nset(LLVM_VERSION_MAJOR 23) # active value\nset(LLVM_VERSION_MINOR 2)\nset(LLVM_VERSION_PATCH 7)\n"
        )
        self.git("add", ".")
        self.git("commit", "-qm", "commented version fixture")
        revision = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "--detach", self.old)
        result = self.run_sync(revision)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("llvm_version=23.2.7", result.stdout)

    def test_removed_patch_target_has_complete_conflict_report(self):
        self.git("checkout", "-q", "--detach", self.new)
        self.git("rm", "-q", "sample")
        self.git("commit", "-qm", "moved patch target fixture")
        revision = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "--detach", self.old)
        before = self.versions.read_bytes()
        result = self.run_sync(revision)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("::error title=UPSTREAM_PATCH_CONFLICT::", result.stderr)
        for token in [
            "sample",
            self.patch.name,
            self.old,
            revision,
            "compare/",
            "Published v0.0.1 Release: UNTOUCHED",
            "Manual repair",
            "--apply",
        ]:
            self.assertIn(token, self.summary.read_text())
        self.assertEqual(before, self.versions.read_bytes())
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.old)

    def test_dirty_source_rejected_without_changes(self):
        (self.source / "sample").write_text("dirty\n")
        p = self.run_sync(self.new)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual((self.source / "sample").read_text(), "dirty\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
