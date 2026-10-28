"""Exercise the real matrix, asset admission, publisher and notification commands offline."""

import hashlib, json, os, pathlib, shutil, subprocess, tempfile, unittest

import sys

sys.dont_write_bytecode = True

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGETS = ["x86_64-unknown-linux-gnu", "x86_64-unknown-linux-musl"]
HOSTS = ["amazonlinux2", "alinux3", "centos7", "ubuntu22"]


def run(args, **kw):
    return subprocess.run(args, text=True, capture_output=True, **kw)


class Release(unittest.TestCase):
    def setUp(self):
        temp_base = pathlib.Path(os.environ.get("TMPDIR") or "/tmp").resolve()
        if temp_base == ROOT or ROOT in temp_base.parents:
            raise RuntimeError("Test TMPDIR must be outside the repository")
        self.tmp = tempfile.TemporaryDirectory(
            dir=os.environ.get("TMPDIR") or "/tmp", prefix="clang-cross-release."
        )
        self.addCleanup(self.tmp.cleanup)
        self.work = pathlib.Path(self.tmp.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.candidate = self.work / "candidate"
        self.assets = self.work / "release"
        self.assets.mkdir()
        self.notes = self.work / "RELEASE.md"
        self.state = self.work / "github.json"
        self.commit = "1" * 40
        self.old_tag = "2" * 40
        self.env = dict(
            os.environ,
            PATH=str(self.bin) + ":" + os.environ["PATH"],
            GITHUB_SHA=self.commit,
            ARTIFACT_SHA=self.commit,
            GITHUB_REPOSITORY="fixture/clang-cross",
            GITHUB_ENV=str(self.work / "env"),
            GITHUB_STEP_SUMMARY=str(self.work / "summary"),
            GH_STATE=str(self.state),
            UPSTREAM_UPDATED="false",
            SOURCE_RUN_ID="",
        )
        self.invoke("candidate", "--directory", str(self.candidate), success=True)
        candidate = json.loads((self.candidate / "source.json").read_text())
        for t in TARGETS:
            for h in HOSTS:
                name = t + "-" + h + ".tar.xz"
                p = self.assets / name
                p.write_text(name)
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
                pathlib.Path(str(p) + ".sha256").write_text(digest + "\n")
                pathlib.Path(str(p) + ".provenance.json").write_text(
                    json.dumps(
                        dict(
                            schema="clang-cross-cell/1",
                            base_sha=self.commit,
                            llvm_sha=candidate["llvm_sha"],
                            versions_sha256=candidate["files"]["targets/versions"],
                            target=t,
                            host=h,
                            archive_sha256=digest,
                        )
                    )
                )
        self.state.write_text(
            json.dumps(
                dict(
                    main=self.commit,
                    tag=self.old_tag,
                    release=True,
                    assets={},
                    calls=[],
                )
            )
        )
        fake = """#!/usr/bin/env python3
import json,os,pathlib,sys
p=pathlib.Path(os.environ['GH_STATE']);s=json.loads(p.read_text());a=sys.argv[1:];s['calls'].append(a)
def done(code=0,value=None):
 p.write_text(json.dumps(s))
 if value is not None:print(json.dumps(value))
 sys.exit(code)
if a[0]=='api':
 path=next(v for v in a[1:] if v.startswith('repos/'))
 if path.endswith('/git/ref/heads/main'):done(value={'object':{'sha':s['main']}})
 if '/actions/runs/' in path:
  done(value={'status':'completed','conclusion':os.getenv('RUN_CONCLUSION','success'),'head_repository':{'full_name':'fixture/clang-cross'},'head_branch':'main','path':'.github/workflows/release.yaml','event':'workflow_dispatch','head_sha':s['main']})
 if path.endswith('/releases?per_page=100'):
  done(value=[[{'id':7,'tag_name':'v0.0.1','draft':True,'name':'Release v0.0.1','target_commitish':s['main']}] if s.get('draft') else []])
 if '/releases/tags/' in path:
  if os.getenv('API_FAILURE'):print('HTTP '+os.environ['API_FAILURE'],file=sys.stderr);done(1)
  if s['release']:done(value={'immutable':False})
  print('HTTP 404',file=sys.stderr);done(1)
 if '/git/ref/tags/' in path:
  if s['tag']:done(value={'object':{'sha':s['tag']}})
  print('HTTP 404',file=sys.stderr);done(1)
 if '/git/refs' in path:
  s['tag']=next(x[4:] for x in a if x.startswith('sha='));done(value={'object':{'sha':s['tag']}})
if a[:2]==['release','create']:
 if s.get('draft'):print('HTTP 422 duplicate draft',file=sys.stderr);done(1)
 s['draft']=True;done()
if a[:2]==['release','upload']:
 for name in a[3:]:
  if name=='--repo':break
  f=pathlib.Path(name);s['assets'][f.name]=f.read_text()
  if os.getenv('UPLOAD_FAILURE'):done(9)
 done()
if a[:2]==['release','edit']:
 if os.getenv('EDIT_FAILURE'):done(8)
 if '--draft=false' in a:s['draft']=False;s['release']=True
 done()
done(99)
"""
        (self.bin / "gh").write_text(fake)
        (self.bin / "gh").chmod(0o755)

    def invoke(self, *args, success=False, **env):
        p = run(
            ["python3", str(ROOT / "scripts/release.py"), *args],
            cwd=ROOT,
            env=dict(self.env, **env),
        )
        if success:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def publish(self, **env):
        return self.invoke(
            "publish",
            "--directory",
            str(self.assets),
            "--candidate",
            str(self.candidate),
            "--notes",
            str(self.notes),
            "--tag",
            "v0.0.1",
            **env,
        )

    def test_schedule_selects_both_targets_on_all_four_hosts(self):
        p = run(["python3", str(ROOT / "scripts/matrix.py"), "schedule", "", ""])
        self.assertEqual(p.returncode, 0, p.stderr)
        m = json.loads(p.stdout)["include"]
        self.assertEqual(len(m), 8)
        self.assertEqual(
            {(r["target"], r["container"]["label"]) for r in m},
            {(t, h) for t in TARGETS for h in HOSTS},
        )
        for targets, hosts in [
            ("bad", "all"),
            (",".join(TARGETS), "bad"),
            (TARGETS[0] + "," + TARGETS[0], "all"),
        ]:
            self.assertNotEqual(
                run(
                    [
                        "python3",
                        str(ROOT / "scripts/matrix.py"),
                        "workflow_dispatch",
                        targets,
                        hosts,
                    ]
                ).returncode,
                0,
            )

    def test_publish_uploads_all_assets_before_metadata_and_tag(self):
        p = self.publish()
        self.assertEqual(p.returncode, 0, p.stderr)
        s = json.loads(self.state.read_text())
        self.assertEqual(s["tag"], self.commit)
        self.assertEqual(len(s["assets"]), 16)
        commands = s["calls"]
        upload = next(
            i for i, x in enumerate(commands) if x[:2] == ["release", "upload"]
        )
        edit = next(i for i, x in enumerate(commands) if x[:2] == ["release", "edit"])
        tag = next(
            i for i, x in enumerate(commands) if "--method" in x and "PATCH" in x
        )
        self.assertLess(upload, edit)
        self.assertLess(edit, tag)

    def test_partial_upload_failure_does_not_move_tag_or_edit_metadata(self):
        p = self.publish(UPLOAD_FAILURE="true")
        self.assertNotEqual(p.returncode, 0)
        s = json.loads(self.state.read_text())
        self.assertEqual(s["tag"], self.old_tag)
        self.assertEqual(s["main"], self.commit)
        self.assertEqual(len(s["assets"]), 1)
        self.assertFalse(any(x[:2] == ["release", "edit"] for x in s["calls"]))

    def test_metadata_failure_does_not_move_tag(self):
        p = self.publish(EDIT_FAILURE="true")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(json.loads(self.state.read_text())["tag"], self.old_tag)

    def test_checksum_missing_pair_foreign_receipt_and_extra_file_rejected_before_api(
        self,
    ):
        archive = self.assets / (TARGETS[0] + "-" + HOSTS[0] + ".tar.xz")
        original = archive.read_bytes()
        archive.write_text("corrupt")
        self.assertNotEqual(self.publish().returncode, 0)
        archive.write_bytes(original)
        side = pathlib.Path(str(archive) + ".sha256")
        saved = side.read_bytes()
        side.unlink()
        self.assertNotEqual(self.publish().returncode, 0)
        side.write_bytes(saved)
        receipt = pathlib.Path(str(archive) + ".provenance.json")
        saved = receipt.read_bytes()
        r = json.loads(saved)
        r["base_sha"] = "3" * 40
        receipt.write_text(json.dumps(r))
        self.assertNotEqual(self.publish().returncode, 0)
        receipt.write_bytes(saved)
        (self.assets / "unexpected").touch()
        self.assertNotEqual(self.publish().returncode, 0)
        self.assertEqual(json.loads(self.state.read_text())["calls"], [])

    def test_api_outage_is_not_treated_as_new_release(self):
        for code in ["403", "429", "500"]:
            self.assertNotEqual(self.publish(API_FAILURE=code).returncode, 0)
            self.assertFalse(
                any(
                    x[:2] == ["release", "create"]
                    for x in json.loads(self.state.read_text())["calls"]
                )
            )

    def test_new_release_drafts_then_uploads_before_creating_tag(self):
        state = json.loads(self.state.read_text())
        state.update(release=False, tag=None)
        self.state.write_text(json.dumps(state))
        p = self.publish()
        self.assertEqual(p.returncode, 0, p.stderr)
        s = json.loads(self.state.read_text())
        self.assertEqual(s["tag"], self.commit)
        self.assertEqual(len(s["assets"]), 16)
        self.assertIn(
            "--draft", next(x for x in s["calls"] if x[:2] == ["release", "create"])
        )

    def test_resolve_requires_successful_trusted_source_run(self):
        self.invoke("resolve", "--run-id", "123", success=True)
        self.assertIn(self.commit, (self.work / "env").read_text())
        p = self.invoke("resolve", "--run-id", "123", RUN_CONCLUSION="failure")
        self.assertNotEqual(p.returncode, 0)

    def test_notification_panel_does_not_claim_unconfigured_email_delivery(self):
        env = dict(
            self.env,
            SMTP_HOST="",
            PREPARE_RESULT="failure",
            BUILD_RESULT="skipped",
            RELEASE_RESULT="skipped",
        )
        p = run(["python3", str(ROOT / "scripts/notify-failure.py")], env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        text = (self.work / "summary").read_text()
        self.assertIn("NOT SENT", text)
        self.assertIn("Discovery / patches", text)
        self.assertIn("::error", p.stdout)

    def test_failed_first_publication_reuses_pending_draft(self):
        state = json.loads(self.state.read_text())
        state.update(release=False, tag=None)
        self.state.write_text(json.dumps(state))
        failed = self.publish(UPLOAD_FAILURE="true")
        self.assertNotEqual(failed.returncode, 0)
        state = json.loads(self.state.read_text())
        self.assertTrue(state["draft"])
        self.assertFalse(state["release"])
        self.assertIsNone(state["tag"])
        retry = self.publish()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        state = json.loads(self.state.read_text())
        self.assertFalse(state["draft"])
        self.assertTrue(state["release"])
        self.assertEqual(state["tag"], self.commit)
        self.assertEqual(sum(c[:2] == ["release", "create"] for c in state["calls"]), 1)

    def test_updated_source_produces_single_root_with_exact_identity_after_upload(self):
        # All Git writes stay in this disposable fixture, never the reviewed checkout.
        fixture = self.work / "source"
        (fixture / "scripts").mkdir(parents=True)
        shutil.copyfile(ROOT / "scripts/release.py", fixture / "scripts/release.py")
        for name in [
            "targets/versions",
            "targets/x86_64-unknown-linux-musl/patches/README.md",
        ]:
            p = fixture / name
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, p)

        def git(*args):
            p = run(["/usr/bin/git", "-C", str(fixture), *args])
            self.assertEqual(p.returncode, 0, p.stderr)
            return p.stdout.strip()

        git("init", "-q")
        git("config", "user.name", "Initial fixture")
        git("config", "user.email", "fixture@example.invalid")
        git("add", ".")
        git("commit", "-qm", "fixture base")
        base = git("rev-parse", "HEAD")
        c = json.loads((self.candidate / "source.json").read_text())
        old = c["llvm_sha"]
        new = "a" * 40
        c.update(base_sha=base, llvm_sha=new, upstream_updated=True)
        for name in c["files"]:
            p = self.candidate / name
            p.write_text(p.read_text().replace(old, new))
            c["files"][name] = hashlib.sha256(p.read_bytes()).hexdigest()
        (self.candidate / "source.json").write_text(json.dumps(c))
        for receipt in self.assets.glob("*.provenance.json"):
            r = json.loads(receipt.read_text())
            r.update(
                base_sha=base,
                llvm_sha=new,
                versions_sha256=c["files"]["targets/versions"],
            )
            receipt.write_text(json.dumps(r))
        state = json.loads(self.state.read_text())
        state.update(main=base)
        self.state.write_text(json.dumps(state))
        fakegit = self.bin / "git"
        fakegit.write_text("""#!/usr/bin/env python3
import json,os,pathlib,sys
if sys.argv[1]=='push':
 p=pathlib.Path(os.environ['GH_STATE']);s=json.loads(p.read_text());assert len(s['assets'])==16
 s['main']=sys.argv[3].split(':')[0];s['calls'].append(['git',*sys.argv[1:]]);p.write_text(json.dumps(s));sys.exit()
os.execv('/usr/bin/git',['git',*sys.argv[1:]])
""")
        fakegit.chmod(0o755)
        env = dict(
            self.env, GITHUB_SHA=base, ARTIFACT_SHA=base, UPSTREAM_UPDATED="true"
        )
        failed = run(
            [
                "python3",
                str(fixture / "scripts/release.py"),
                "publish",
                "--directory",
                str(self.assets),
                "--candidate",
                str(self.candidate),
                "--notes",
                str(self.notes),
                "--tag",
                "v0.0.1",
            ],
            cwd=fixture,
            env=dict(env, UPLOAD_FAILURE="true"),
        )
        self.assertNotEqual(failed.returncode, 0)
        unchanged = json.loads(self.state.read_text())
        self.assertEqual(unchanged["main"], base)
        self.assertEqual(unchanged["tag"], self.old_tag)
        p = run(
            [
                "python3",
                str(fixture / "scripts/release.py"),
                "publish",
                "--directory",
                str(self.assets),
                "--candidate",
                str(self.candidate),
                "--notes",
                str(self.notes),
                "--tag",
                "v0.0.1",
            ],
            cwd=fixture,
            env=env,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        state = json.loads(self.state.read_text())
        new_sha = state["main"]
        self.assertEqual(new_sha, state["tag"])
        self.assertEqual(git("rev-list", "--count", new_sha), "1")
        self.assertEqual(
            git("show", "-s", "--format=%an <%ae>|%cn <%ce>|%B", new_sha),
            "Nathan Shea <itzrustz@gmail.com>|Nathan Shea <itzrustz@gmail.com>|first commit",
        )
        calls = state["calls"]
        push = next(i for i, c in enumerate(calls) if c[:2] == ["git", "push"])
        upload = next(i for i, c in enumerate(calls) if c[:2] == ["release", "upload"])
        self.assertGreater(push, upload)

    def test_workflow_has_explicit_schedule_and_failure_routing(self):
        text = (ROOT / ".github/workflows/release.yaml").read_text()
        self.assertIn("cron: '40 3 * * *'", text)
        for name in ["prepare-matrix", "build", "release"]:
            self.assertIn("needs." + name + ".result == 'failure'", text)
        self.assertIn("Resolve artifact source", text)
        self.assertIn("scripts/prepare-matrix.sh", text)
        self.assertIn("scripts/release.py publish", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
