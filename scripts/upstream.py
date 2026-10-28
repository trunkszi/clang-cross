#!/usr/bin/env python3
"""Resolve one upstream candidate and preflight its patches before accepting it."""

import argparse, json, os, pathlib, re, subprocess, sys, tempfile, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(*args, check=True):
    p = subprocess.run(
        args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    if check and p.returncode:
        raise RuntimeError(p.stdout.strip())
    return p


def git(source, *args, check=True):
    return run("git", "-C", str(source), *args, check=check)


def emit(**values):
    text = "".join(
        f"{k}={str(v).lower() if isinstance(v, bool) else v}\n"
        for k, v in values.items()
    )
    print(text, end="")
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(text)


def summary(path, text):
    if path:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(text + "\n")


def safe_path(name):
    p = pathlib.PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts:
        raise RuntimeError("unsafe patch path: " + name)
    return name


def patch_conflict(summary_path, repo, branch, old, new, patches, detail):
    names = ", ".join(str(p.relative_to(ROOT)) for p in patches)
    diagnostic = f"""## Upstream patch conflict

- Upstream: `{repo}` / `{branch}`
- Accepted commit: `{old}`
- Candidate commit: `{new}`
- Compare: https://github.com/{repo}/compare/{old}...{new}
- Affected patches: `{names}`
- **Published v0.0.1 Release: UNTOUCHED**

```text
{detail.rstrip()}
```

### Manual repair

```bash
./scripts/sync-upstream.sh --check --summary-file patch-review.md
git -C llvm fetch origin {new}
# Rebase the named patches against the candidate, including any moved files.
# Regenerate their context, then verify the complete ordered series again.
./scripts/sync-upstream.sh --apply
```
"""
    summary(summary_path, diagnostic)
    print(
        "::error title=UPSTREAM_PATCH_CONFLICT::" + names + " failed against " + new,
        file=sys.stderr,
    )
    raise RuntimeError("UPSTREAM_PATCH_CONFLICT: " + detail.strip())


def candidate_text(source, revision, name):
    try:
        if not git(
            source, "ls-tree", "--name-only", revision, "--", name
        ).stdout.strip():
            return None
        return git(source, "show", revision + ":" + name).stdout
    except Exception as error:
        raise RuntimeError(
            "DISCOVERY_FAILED: cannot read candidate source " + name + ": " + str(error)
        ) from error


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--summary-file", default=os.getenv("GITHUB_STEP_SUMMARY"))
    a = ap.parse_args()
    repo = os.environ["LLVM_UPSTREAM_REPO"]
    branch = os.environ["LLVM_UPSTREAM_BRANCH"]
    old = os.environ["LLVM_SOURCE_REVISION"]
    discovered_version = None
    if (
        not re.fullmatch(r"[\w.-]+/[\w.-]+", repo)
        or not re.fullmatch(r"[\w./-]+", branch)
        or not re.fullmatch(r"[0-9a-f]{40}", old)
    ):
        raise RuntimeError("invalid accepted source metadata")
    try:
        if os.getenv("UPSTREAM_COMMIT_JSON_FILE"):
            data = json.loads(
                pathlib.Path(os.environ["UPSTREAM_COMMIT_JSON_FILE"]).read_text()
            )
        else:
            request = urllib.request.Request(
                f"https://api.github.com/repos/{repo}/commits/{branch}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "clang-cross-upstream",
                },
            )
            if os.getenv("GH_TOKEN"):
                request.add_header("Authorization", "Bearer " + os.environ["GH_TOKEN"])
            with urllib.request.urlopen(request, timeout=30) as r:
                data = json.load(r)
        new = data["sha"]
        if not isinstance(new, str) or not re.fullmatch(r"[0-9a-f]{40}", new):
            raise ValueError("missing/invalid commit SHA")
    except Exception as e:
        raise RuntimeError("DISCOVERY_FAILED: " + str(e)) from e
    if new == old:
        emit(
            status="no_update",
            should_build=False,
            upstream_updated=False,
            old_sha=old,
            new_sha=new,
        )
        return
    source = ROOT / "llvm"
    upstream = pathlib.Path(os.environ.get("UPSTREAM_LLVM_DIR", source)).resolve()
    if git(source, "status", "--porcelain").stdout.strip():
        raise RuntimeError("dirty llvm source; refusing update")
    if git(source, "rev-parse", "HEAD").stdout.strip() != old:
        raise RuntimeError("llvm HEAD disagrees with accepted revision")
    try:
        if git(upstream, "cat-file", "-e", new + "^{commit}", check=False).returncode:
            if os.getenv("UPSTREAM_LLVM_DIR"):
                raise RuntimeError("candidate unavailable in offline source")
            git(
                upstream,
                "fetch",
                "--depth=1",
                "--filter=blob:none",
                f"https://github.com/{repo}.git",
                new,
            )
    except Exception as e:
        raise RuntimeError("DISCOVERY_FAILED: " + str(e)) from e
    patches = sorted(
        (ROOT / "targets/x86_64-unknown-linux-musl/patches").glob("*.patch")
    )
    with tempfile.TemporaryDirectory(
        prefix="clang-cross-preflight-", dir=os.environ.get("TMPDIR") or "/tmp"
    ) as temporary:
        work = pathlib.Path(temporary)
        files = {}
        for p in patches:
            for name in re.findall(r"^--- a/(\S+)", p.read_text(), re.M):
                files.setdefault(safe_path(name), []).append(p)
        for name, affected in sorted(files.items()):
            contents = candidate_text(upstream, new, name)
            if contents is None:
                patch_conflict(
                    a.summary_file,
                    repo,
                    branch,
                    old,
                    new,
                    affected,
                    "Missing upstream patch target: " + name,
                )
            dest = work / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(contents)
        for name in [
            "cmake/Modules/LLVMVersion.cmake",
            "llvm/cmake/Modules/LLVMVersion.cmake",
            "llvm/CMakeLists.txt",
        ]:
            contents = candidate_text(upstream, new, name)
            if contents is None:
                continue
            # Ignore CMake line and bracket comments before reading active assignments.
            text = re.sub(r"#\[(=*)\[.*?\]\1\]", "", contents, flags=re.S)
            text = re.sub(r"#.*", "", text)
            parts = [
                re.findall(
                    r"^\s*set\s*\(\s*LLVM_VERSION_" + part + r'\s+"?(\d+)"?\s*\)',
                    text,
                    re.I | re.M,
                )
                for part in ["MAJOR", "MINOR", "PATCH"]
            ]
            if all(len(values) == 1 for values in parts):
                discovered_version = ".".join(values[0] for values in parts)
            break
        if discovered_version is None:
            raise RuntimeError("DISCOVERY_FAILED: cannot parse candidate LLVM version")
        version = discovered_version
        for p in patches:
            with p.open() as patch:
                result = subprocess.run(
                    [
                        "patch",
                        "--fuzz=0",
                        "--batch",
                        "--forward",
                        "-d",
                        str(work),
                        "-Np1",
                    ],
                    stdin=patch,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
            if result.returncode:
                patch_conflict(
                    a.summary_file, repo, branch, old, new, [p], result.stdout
                )
    if a.apply:
        versions = ROOT / "targets/versions"
        notes = ROOT / "targets/x86_64-unknown-linux-musl/patches/README.md"
        backups = {p: p.read_bytes() for p in [versions, notes]}
        try:
            if upstream != source:
                try:
                    git(source, "fetch", "--no-tags", str(upstream), new)
                except Exception as error:
                    raise RuntimeError("DISCOVERY_FAILED: " + str(error)) from error
            git(source, "checkout", "--detach", new)
            text = versions.read_text()
            for key, value in [
                ("LLVM_SOURCE_REVISION", new),
                ("LLVM_VERSION", version),
            ]:
                text, count = re.subn(
                    r"^" + key + r"=.*$",
                    key + "=${" + key + ":-" + value + "}",
                    text,
                    flags=re.M,
                )
                if count != 1:
                    raise RuntimeError("missing/duplicate version field " + key)
            versions.write_text(text)
            notes.write_text(
                re.sub(
                    r"Based on LLVM [0-9.]+",
                    "Based on LLVM " + version,
                    notes.read_text().replace(old, new),
                )
            )
        except BaseException:
            for p, value in backups.items():
                p.write_bytes(value)
            git(source, "checkout", "--detach", old)
            raise
    summary(
        a.summary_file,
        f"## Upstream preflight passed\n\n`{old}` → `{new}` (LLVM {version})\n\nhttps://github.com/{repo}/compare/{old}...{new}\n\nPublished v0.0.1 Release: UNTOUCHED\n",
    )
    emit(
        status="updated" if a.apply else "update_available",
        should_build=True,
        upstream_updated=a.apply,
        old_sha=old,
        new_sha=new,
        llvm_version=version,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
