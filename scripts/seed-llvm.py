#!/usr/bin/env python3
"""Recover only host LLVM from a verified, matching toolchain artifact."""

import argparse, base64, copy, os, pathlib, re, tarfile

import release


def seed_image(run_id, host):
    repository = os.environ["GITHUB_REPOSITORY"]
    jobs = release.gh(f"repos/{repository}/actions/runs/{run_id}/jobs?per_page=100")["jobs"]
    marker = {"amazonlinux2": "amazonlinux", "alinux3": "alibaba-cloud", "centos7": "centos:", "ubuntu22": "ubuntu:"}[host]
    matching = [j for j in jobs if j["name"].startswith("build (x86_64-unknown-linux-musl,")
                and marker in j["name"] and j["conclusion"] == "success"]
    release.require(len(matching) == 1, "LLVM seed job was not successful")
    command = ["gh", "api"]
    # Newer gh versions reject raw Actions logs containing terminal escapes.
    if "--allow-escape-sequences" in release.command("gh", "api", "--help").stdout:
        command.append("--allow-escape-sequences")
    log = release.command(*command, f"repos/{repository}/actions/jobs/{matching[0]['id']}/logs").stdout
    digests = re.findall(r"(?m)^(?:\S+ )?Digest: (sha256:[0-9a-f]{64})\r?$", log)
    release.require(len(digests) == 1, "LLVM seed host image cannot be verified")
    return digests[0]


def llvm_recipe(text):
    start = text.index("\nbuild_llvm() {")
    end = text.index("\n}\n", start) + 2
    targets = re.search(r"^LLVM_TARGETS=.*$", text, re.M)
    release.require(targets, "missing LLVM backend selection")
    return targets[0] + text[start:end]


def host_member(name, target):
    parts = pathlib.PurePosixPath(name).parts
    release.require(parts and parts[0] == target and ".." not in parts, "invalid seed path")
    if len(parts) == 1:
        return True
    if parts[1] not in ["bin", "lib", "lib64", "share", "libexec"]:
        return False
    if parts[1] == "bin" and len(parts) > 2 and parts[2].startswith(tuple(release.TARGETS)):
        return False
    if parts[1:3] == ("lib", "gcc"):
        return False
    if len(parts) > 4 and parts[1:3] == ("lib", "clang") and parts[4] == "lib":
        return False
    return True


def repack(archive, output, target):
    release.command("python3", str(release.ROOT / "scripts/check-archive.py"), str(archive))
    with tarfile.open(archive, "r:xz") as source, tarfile.open(output, "w:xz") as destination:
        members = source.getmembers()
        included = {m.name for m in members if host_member(m.name, target)}
        for name in ["bin/clang", "bin/clang++", "bin/ld.lld"]:
            release.require(target + "/" + name in included, "incomplete host LLVM: " + name)
        for member in members:
            if member.name not in included:
                continue
            cloned = copy.copy(member)
            cloned.name = "clang-cross" + member.name[len(target):]
            if member.islnk():
                release.require(member.linkname in included, "seed hardlink crosses into target files")
                cloned.linkname = "clang-cross" + member.linkname[len(target):]
            destination.addfile(cloned, source.extractfile(member) if member.isfile() else None)
    pathlib.Path(str(output) + ".sha256").write_text(release.sha(output) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--host", choices=release.HOSTS, required=True)
    parser.add_argument("--image-only", action="store_true")
    parser.add_argument("--image-digest")
    parser.add_argument("--directory", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    release.require(re.fullmatch("[0-9]+", args.run_id), "invalid LLVM seed run")
    repository = os.environ["GITHUB_REPOSITORY"]
    run = release.gh(f"repos/{repository}/actions/runs/{args.run_id}")
    release.require(
        run["status"] == "completed" and run["head_repository"]["full_name"] == repository
        and run["head_branch"] == "main" and run["path"] == ".github/workflows/release.yaml"
        and run["event"] in ["schedule", "workflow_dispatch"], "untrusted LLVM seed run",
    )
    if args.image_only:
        print(seed_image(args.run_id, args.host))
        return
    release.require(args.image_digest and args.directory and args.output, "incomplete LLVM seed arguments")
    target = "x86_64-unknown-linux-musl"
    archive = args.directory / (target + "-" + args.host + ".tar.xz")
    receipt = release.load(str(archive) + ".provenance.json")
    digest = pathlib.Path(str(archive) + ".sha256").read_text().strip()
    release.require(receipt == {
        "schema": "clang-cross-cell/1", "base_sha": run["head_sha"],
        "llvm_sha": release.revision(release.ROOT),
        "versions_sha256": release.sha(release.ROOT / "targets/versions"),
        "target": target, "host": args.host, "archive_sha256": digest,
    } and re.fullmatch("[0-9a-f]{64}", digest) and release.sha(archive) == digest,
        "LLVM seed provenance or checksum mismatch")
    for name in ["scripts/dependencies.sh", "scripts/packages/" + args.host + ".conf", "scripts/make"]:
        data = release.gh(f"repos/{repository}/contents/{name}?ref={run['head_sha']}")
        previous = base64.b64decode(data["content"]).decode()
        current = (release.ROOT / name).read_text()
        release.require(
            llvm_recipe(previous) == llvm_recipe(current) if name == "scripts/make" else previous == current,
            "LLVM seed build recipe changed: " + name,
        )
    release.require(args.image_digest == seed_image(args.run_id, args.host),
                    "LLVM seed host image changed or cannot be verified")
    repack(archive, args.output, target)
    release.save(str(args.output) + ".seed.json", {
        "schema": "clang-cross-llvm-seed/1", "run_id": args.run_id,
        "source_receipt": receipt, "image_digest": args.image_digest,
        "archive_sha256": release.sha(args.output),
    })
    print("Recovered matching host LLVM without compilation: " + str(args.output))


if __name__ == "__main__":
    main()
