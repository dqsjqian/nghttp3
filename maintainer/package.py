#!/usr/bin/env python3
"""Create a reproducible CMake source archive, including pinned submodules."""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def collect(root: Path, revision: str, prefix: str, entries: dict,
            revisions: dict) -> None:
    revisions[prefix or "."] = revision
    archive = git(root, "archive", "--format=tar", revision)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source:
            if not (member.isfile() or member.issym()):
                continue
            data = source.extractfile(member).read() if member.isfile() else None
            info = copy.copy(member)
            info.name = prefix + member.name
            entries[info.name] = (info, data)
    for entry in git(root, "ls-tree", "-rz", revision).split(b"\0"):
        if not entry:
            continue
        metadata, path_bytes = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        if kind == "commit":
            path = path_bytes.decode("utf-8")
            child = root / path
            if not (child / ".git").exists():
                raise ValueError(f"Initialize the submodule first: {prefix}{path}")
            collect(child, oid, prefix + path + "/", entries, revisions)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("build/dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = git(root, "show", "HEAD:configure.ac").decode("utf-8")
    match = re.search(r"AC_INIT\(\[(nghttp[23])\], \[([\w.+-]+)\]", config)
    if not match:
        raise ValueError("Cannot identify the committed source version")
    name, version = match.groups()
    revision = git(root, "rev-parse", "HEAD").decode().strip()
    epoch = int(git(root, "show", "-s", "--format=%ct", revision))
    entries, revisions = {}, {}
    collect(root, revision, "", entries, revisions)
    manifest = (json.dumps({"name": name, "version": version,
                            "revisions": revisions}, indent=2, sort_keys=True) + "\n").encode()
    info = tarfile.TarInfo("MIRA-SOURCE.json")
    info.size, info.mode = len(manifest), 0o644
    entries[info.name] = (info, manifest)
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / f"{name}-{version}.tar.xz"
    with tarfile.open(target, "w:xz", format=tarfile.PAX_FORMAT) as output:
        for path, (member, data) in sorted(entries.items()):
            member.name = f"{name}-{version}/{path}"
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            member.mtime = epoch
            member.pax_headers = {}
            output.addfile(member, io.BytesIO(data) if data is not None else None)
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    line = f"{checksum}  {target.name}\n"
    target.with_suffix(target.suffix + ".sha256").write_text(line, encoding="ascii")
    print(line, end="")


if __name__ == "__main__":
    main()
