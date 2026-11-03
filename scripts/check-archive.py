#!/usr/bin/env python3
"""Validate complete toolchain archive paths and link chains before extraction."""

import collections, pathlib, sys, tarfile

with tarfile.open(sys.argv[1], "r:xz") as archive:
    members = archive.getmembers()
    roots = set()
    names = set()
    links = {}
    for member in members:
        name = member.name[2:] if member.name.startswith("./") else member.name
        p = pathlib.PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or not p.parts:
            raise SystemExit("unsafe archive path: " + name)
        if str(p) in names:
            raise SystemExit("duplicate archive path: " + name)
        names.add(str(p))
        roots.add(p.parts[0])
        if member.issym() or member.islnk():
            if member.linkname.startswith("/"):
                raise SystemExit("escaping archive link: " + name)
            links[p.parts] = (
                member.issym(),
                pathlib.PurePosixPath(member.linkname).parts,
            )
        elif not (member.isfile() or member.isdir()):
            raise SystemExit("unsupported archive entry: " + name)
    if len(roots) != 1 or next(iter(roots)) not in [
        "x86_64-unknown-linux-musl",
        "x86_64-unknown-linux-gnu",
    ]:
        raise SystemExit("unexpected toolchain archive root")
    root = next(iter(roots))
    # Resolve components in filesystem order: normalizing '..' before following
    # a symlink could hide an escape through a second link.
    for name in names:
        todo = collections.deque(pathlib.PurePosixPath(name).parts)
        resolved = []
        hops = 0
        while todo:
            part = todo.popleft()
            if part == ".":
                continue
            if part == "..":
                if len(resolved) <= 1:
                    raise SystemExit("escaping archive link: " + name)
                resolved.pop()
                continue
            resolved.append(part)
            if tuple(resolved) in links:
                relative, target = links[tuple(resolved)]
                hops += 1
                if hops > 64:
                    raise SystemExit("cyclic archive link: " + name)
                resolved = resolved[:-1] if relative else []
                todo.extendleft(reversed(target))
            if resolved and resolved[0] != root:
                raise SystemExit("escaping archive link: " + name)
    for sub in ["bin", "include", "lib", root + "/sysroot"]:
        path = root + "/" + sub
        if not any(n == path or n.startswith(path + "/") for n in names):
            raise SystemExit("incomplete toolchain layout: " + path)
    print(root)
