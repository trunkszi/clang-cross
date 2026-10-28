#!/usr/bin/env python3
"""Candidate provenance, exact asset validation, and ordered mutable-release promotion."""

import argparse, hashlib, json, os, pathlib, re, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGETS = ["x86_64-unknown-linux-gnu", "x86_64-unknown-linux-musl"]
HOSTS = ["amazonlinux2", "alinux3", "centos7", "ubuntu22"]
FILES = ["targets/versions", "targets/x86_64-unknown-linux-musl/patches/README.md"]


def sha(p):
    h = hashlib.sha256()
    with pathlib.Path(p).open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def command(*args, input=None, env=None, check=True):
    p = subprocess.run(
        args,
        text=True,
        input=input,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and p.returncode:
        raise RuntimeError(" ".join(args[:3]) + ": " + p.stdout + p.stderr)
    return p


def gh(*args, missing=False):
    p = command("gh", "api", *args, check=False)
    if p.returncode:
        if missing and re.search(r"\bHTTP 404\b", p.stderr):
            return None
        raise RuntimeError("GitHub API failed: " + p.stderr)
    return json.loads(p.stdout)


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def load(p):
    return json.loads(pathlib.Path(p).read_text())


def save(p, value):
    p = pathlib.Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, indent=2) + "\n")


def revision(directory):
    text = (pathlib.Path(directory) / "targets/versions").read_text()
    m = re.search(
        r"^LLVM_SOURCE_REVISION=\$\{LLVM_SOURCE_REVISION:-([0-9a-f]{40})\}$", text, re.M
    )
    require(m, "invalid candidate revision")
    return m[1]


def source_sha():
    value = os.getenv("ARTIFACT_SHA") or os.environ["GITHUB_SHA"]
    require(re.fullmatch("[0-9a-f]{40}", value), "invalid artifact SHA")
    return value


def candidate(directory):
    root = pathlib.Path(directory)
    for name in FILES:
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, dest)
    save(
        root / "source.json",
        {
            "schema": "clang-cross-source/1",
            "base_sha": source_sha(),
            "llvm_sha": revision(root),
            "upstream_updated": os.getenv("UPSTREAM_UPDATED", "false") == "true",
            "files": {n: sha(root / n) for n in FILES},
        },
    )


def resolve(run_id):
    repository = os.environ["GITHUB_REPOSITORY"]
    if run_id:
        require(re.fullmatch("[0-9]+", run_id), "invalid source_run_id")
        r = gh(f"repos/{repository}/actions/runs/{run_id}")
        require(
            r["status"] == "completed" and r["conclusion"] == "success",
            "source run did not complete successfully",
        )
        require(
            r["head_repository"]["full_name"] == repository
            and r["head_branch"] == "main",
            "foreign source run",
        )
        require(
            r["path"] == ".github/workflows/release.yaml"
            and r["event"] in ["schedule", "workflow_dispatch"],
            "unapproved source workflow",
        )
        commit = r["head_sha"]
    else:
        commit = os.environ["GITHUB_SHA"]
    require(re.fullmatch("[0-9a-f]{40}", commit), "malformed source run SHA")
    output = (
        "ARTIFACT_SHA="
        + commit
        + "\nACCEPTED_VERSIONS_SHA256="
        + sha(ROOT / "targets/versions")
        + "\n"
    )
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write(output)
    print(output, end="")


def receipt(directory, target, host):
    require(target in TARGETS and host in HOSTS, "invalid matrix cell")
    name = target + "-" + host + ".tar.xz"
    root = pathlib.Path(directory)
    save(
        root / (name + ".provenance.json"),
        {
            "schema": "clang-cross-cell/1",
            "base_sha": source_sha(),
            "llvm_sha": revision(ROOT),
            "versions_sha256": sha(ROOT / "targets/versions"),
            "target": target,
            "host": host,
            "archive_sha256": sha(root / name),
        },
    )


def verify(directory, candidate_dir, notes):
    root = pathlib.Path(directory)
    croot = pathlib.Path(candidate_dir)
    c = load(croot / "source.json")
    require(
        c["schema"] == "clang-cross-source/1" and c["base_sha"] == source_sha(),
        "candidate source identity mismatch",
    )
    require(set(c["files"]) == set(FILES), "unexpected candidate files")
    require(isinstance(c["upstream_updated"], bool), "invalid upstream update flag")
    for name in FILES:
        require(
            sha(croot / name) == c["files"][name], "candidate metadata changed: " + name
        )
    require(revision(croot) == c["llvm_sha"], "candidate LLVM revision mismatch")
    if os.getenv("SOURCE_RUN_ID"):
        require(
            os.getenv("ACCEPTED_VERSIONS_SHA256", sha(ROOT / "targets/versions"))
            == sha(croot / "targets/versions"),
            "historical run differs from accepted source lock",
        )
    expected = set()
    rows = []
    for target in TARGETS:
        for host in HOSTS:
            name = target + "-" + host + ".tar.xz"
            expected.update([name, name + ".sha256", name + ".provenance.json"])
            archive = root / name
            digest = (root / (name + ".sha256")).read_text().strip()
            require(
                re.fullmatch("[0-9a-fA-F]{64}", digest), "malformed checksum: " + name
            )
            command(
                "sha256sum", "--check", "-", input=digest + "  " + str(archive) + "\n"
            )
            r = load(root / (name + ".provenance.json"))
            require(
                r
                == {
                    "schema": "clang-cross-cell/1",
                    "base_sha": source_sha(),
                    "llvm_sha": c["llvm_sha"],
                    "versions_sha256": c["files"]["targets/versions"],
                    "target": target,
                    "host": host,
                    "archive_sha256": digest.lower(),
                },
                "foreign matrix artifact: " + name,
            )
            rows.append("| " + name + " | " + digest.lower() + " |")
    require(
        {p.name for p in root.iterdir()} == expected,
        "missing or unexpected release files",
    )
    pathlib.Path(notes).write_text(
        "# Clang/LLVM cross toolchains\n\nSource: `"
        + source_sha()
        + "`\nLLVM: `"
        + c["llvm_sha"]
        + "`\n\n| Filename | SHA256 |\n| --- | --- |\n"
        + "\n".join(rows)
        + "\n"
    )
    print("verified all 8 archives, 8 sidecars, and 8 matching provenance receipts")


def find_release(repository, tag):
    published = gh(f"repos/{repository}/releases/tags/{tag}", missing=True)
    if published is not None:
        return published
    # The published-tag endpoint omits pending drafts; paginate authenticated
    # releases so a failed first upload can resume its existing draft.
    pages = gh("--paginate", "--slurp", f"repos/{repository}/releases?per_page=100")
    require(
        isinstance(pages, list) and all(isinstance(page, list) for page in pages),
        "invalid release listing",
    )
    drafts = [
        r
        for page in pages
        for r in page
        if r.get("draft") is True and r.get("tag_name") == tag
    ]
    require(len(drafts) <= 1, "multiple pending drafts for release tag")
    return drafts[0] if drafts else None


def publish(directory, candidate_dir, notes, tag):
    repository = os.environ["GITHUB_REPOSITORY"]
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", tag), "invalid release tag")
    verify(directory, candidate_dir, notes)
    c = load(pathlib.Path(candidate_dir) / "source.json")
    target = source_sha()
    planned = None
    main = gh(f"repos/{repository}/git/ref/heads/main")["object"]["sha"]
    require(
        main == os.environ["GITHUB_SHA"],
        "main changed during build; refusing stale promotion",
    )
    if c["upstream_updated"]:
        # Build the candidate tree from its exact source run; the commit has no parents.
        command("git", "read-tree", source_sha())
        for name in FILES:
            shutil.copyfile(pathlib.Path(candidate_dir) / name, ROOT / name)
        command("git", "add", "--", *FILES)
        command(
            "git",
            "update-index",
            "--add",
            "--cacheinfo",
            "160000," + c["llvm_sha"] + ",llvm",
        )
        tree = command("git", "write-tree").stdout.strip()
        if os.getenv("UPSTREAM_UPDATED") == "true" and not os.getenv("SOURCE_RUN_ID"):
            identity = dict(
                os.environ,
                GIT_AUTHOR_NAME="Nathan Shea",
                GIT_AUTHOR_EMAIL="itzrustz@gmail.com",
                GIT_COMMITTER_NAME="Nathan Shea",
                GIT_COMMITTER_EMAIL="itzrustz@gmail.com",
            )
            target = command(
                "git", "commit-tree", tree, input="first commit\n", env=identity
            ).stdout.strip()
            planned = main
        else:
            existing = command("git", "show", "-s", "--format=%T", main).stdout.strip()
            require(
                tree == existing, "historical candidate is not the accepted source tree"
            )
            target = main
    initial_ref = gh(f"repos/{repository}/git/ref/tags/{tag}", missing=True)
    release = find_release(repository, tag)
    if release:
        require(
            not release.get("immutable", False), "immutable release cannot be replaced"
        )
        if release.get("draft"):
            require(
                release.get("target_commitish") == main,
                "pending draft targets a different source; inspect before retry",
            )
    else:
        command(
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            repository,
            "--draft",
            "--target",
            main,
            "--title",
            "Release " + tag,
            "--notes-file",
            notes,
        )
    files = [
        str(pathlib.Path(directory) / (t + "-" + h + ".tar.xz" + suffix))
        for t in TARGETS
        for h in HOSTS
        for suffix in ["", ".sha256"]
    ]
    # GitHub clobbers assets individually. Failure may leave mixed assets; refs stay put.
    command("gh", "release", "upload", tag, *files, "--repo", repository, "--clobber")
    command("gh", "release", "edit", tag, "--repo", repository, "--notes-file", notes)
    ref = gh(f"repos/{repository}/git/ref/tags/{tag}", missing=True)
    require(
        ref == initial_ref,
        "release tag changed during asset upload; refusing promotion",
    )
    require(
        gh(f"repos/{repository}/git/ref/heads/main")["object"]["sha"] == main,
        "main changed during asset upload",
    )
    if planned:
        command(
            "git",
            "push",
            "origin",
            target + ":refs/heads/main",
            "--force-with-lease=refs/heads/main:" + planned,
        )
    if ref:
        gh(
            "--method",
            "PATCH",
            f"repos/{repository}/git/refs/tags/{tag}",
            "-f",
            "sha=" + target,
            "-F",
            "force=true",
        )
    else:
        gh(
            "--method",
            "POST",
            f"repos/{repository}/git/refs",
            "-f",
            "ref=refs/tags/" + tag,
            "-f",
            "sha=" + target,
        )
    command(
        "gh",
        "release",
        "edit",
        tag,
        "--repo",
        repository,
        "--target",
        target,
        "--draft=false",
        "--latest",
    )
    print("Published " + tag + " at " + target)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="operation", required=True)
    for name in ["candidate", "receipt", "verify", "publish"]:
        p = sub.add_parser(name)
        p.add_argument("--directory", required=True)
        if name in ["verify", "publish"]:
            p.add_argument("--candidate", default="candidate")
            p.add_argument("--notes", default="RELEASE.md")
        if name == "publish":
            p.add_argument("--tag", required=True)
        if name == "receipt":
            p.add_argument("--target", required=True)
            p.add_argument("--host", required=True)
    p = sub.add_parser("resolve")
    p.add_argument("--run-id", default="")
    a = ap.parse_args()
    if a.operation == "candidate":
        candidate(a.directory)
    elif a.operation == "resolve":
        resolve(a.run_id)
    elif a.operation == "receipt":
        receipt(a.directory, a.target, a.host)
    elif a.operation == "verify":
        verify(a.directory, a.candidate, a.notes)
    elif a.operation == "publish":
        publish(a.directory, a.candidate, a.notes, a.tag)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("::error title=RELEASE_FAILED::" + str(e), file=sys.stderr)
        sys.exit(1)
