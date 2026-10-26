#!/usr/bin/env python3
"""Check real source archives against committed blobs and Git configuration."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PackageTest(unittest.TestCase):
    def test_archive_is_independent_of_git_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            archives = []
            # Override user configuration without changing any repository config.
            for index, values in enumerate((("false", "lf", "0000"),
                                            ("true", "crlf", "0077"))):
                env = os.environ.copy()
                count = int(env.get("GIT_CONFIG_COUNT", "0"))
                keys = ("core.autocrlf", "core.eol", "tar.umask")
                for offset, (key, value) in enumerate(zip(keys, values)):
                    env[f"GIT_CONFIG_KEY_{count + offset}"] = key
                    env[f"GIT_CONFIG_VALUE_{count + offset}"] = value
                env["GIT_CONFIG_COUNT"] = str(count + len(keys))
                output = Path(temporary) / str(index)
                subprocess.run([sys.executable, str(ROOT / "maintainer/package.py"),
                                "--output", str(output)], env=env, check=True,
                               stdout=subprocess.PIPE)
                candidates = list(output.glob("*.tar.xz"))
                self.assertEqual(len(candidates), 1)
                archive = candidates[0]
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                checksum = archive.with_suffix(archive.suffix + ".sha256")
                self.assertEqual(checksum.read_text().split()[0], digest)
                archives.append((archive, digest))
            self.assertEqual(archives[0][1], archives[1][1])

            with tarfile.open(archives[0][0]) as archive:
                members = archive.getmembers()
                top = members[0].name.split("/", 1)[0] + "/"
                manifest_member = archive.getmember(top + "MIRA-SOURCE.json")
                self.assertEqual(manifest_member.mode, 0o644)
                manifest = json.loads(archive.extractfile(manifest_member).read())
                head = subprocess.check_output(
                    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
                self.assertEqual(manifest["revisions"]["."], head)
                modes = set()
                # Check all modes and key source/build/license blobs in every
                # module. Other files can have explicit committed attributes
                # (mruby's Windows .bat script deliberately uses eol=crlf).
                for prefix, revision in manifest["revisions"].items():
                    prefix = "" if prefix == "." else prefix
                    tree = subprocess.check_output(
                        ["git", "-C", str(ROOT / prefix), "ls-tree", "-rz", revision])
                    for record in tree.split(b"\0"):
                        if not record:
                            continue
                        metadata, path = record.split(b"\t", 1)
                        mode, kind, oid = metadata.decode("ascii").split()
                        if kind != "blob":
                            continue
                        name = prefix + path.decode("utf-8")
                        with self.subTest(path=name):
                            member = archive.getmember(top + name)
                            if mode == "120000":
                                self.assertTrue(member.issym())
                                data = member.linkname.encode("utf-8")
                            else:
                                self.assertTrue(member.isfile())
                                expected_mode = int(mode, 8) & 0o777
                                self.assertEqual(member.mode, expected_mode)
                                modes.add(member.mode)
                                data = archive.extractfile(member).read()
                            filename = Path(name).name
                            if (filename.startswith(("COPYING", "LICENSE")) or
                                    filename in ("CMakeLists.txt", "configure.ac", "package.py") or
                                    Path(name).suffix in (".c", ".h")):
                                blob = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
                                algorithm = "sha1" if len(oid) == 40 else "sha256"
                                self.assertEqual(hashlib.new(algorithm, blob).hexdigest(), oid)
                self.assertEqual(modes, {0o644, 0o755})


if __name__ == "__main__":
    unittest.main()
