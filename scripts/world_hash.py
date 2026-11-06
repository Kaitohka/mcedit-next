#!/usr/bin/env python3
"""Snapshot and verify the SHA-256 of every file under a folder.

Proves that opening a world read-only left it untouched, including that no files were
added or removed. Uses only the standard library and never writes inside the folder.

    python scripts/world_hash.py snapshot <folder> <manifest.json>
    python scripts/world_hash.py verify <folder> <manifest.json>
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path


def hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_folder(folder):
    return {
        path.relative_to(folder).as_posix(): hash_file(path)
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def diff(expected, actual):
    """Return the problems found, as (kind, path) pairs."""
    problems = [("removed", p) for p in expected if p not in actual]
    problems += [("added", p) for p in actual if p not in expected]
    problems += [("changed", p) for p in expected if p in actual and expected[p] != actual[p]]
    return sorted(problems, key=lambda problem: problem[1])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["snapshot", "verify"])
    parser.add_argument("folder", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()

    if not args.folder.is_dir():
        sys.exit(f"Not a folder: {args.folder}")
    if args.manifest.resolve().is_relative_to(args.folder.resolve()):
        sys.exit("The manifest must be stored outside the folder it describes.")

    actual = hash_folder(args.folder)
    if args.command == "snapshot":
        args.manifest.write_text(json.dumps(actual, indent=1) + "\n")
        print(f"Recorded {len(actual)} files in {args.manifest}")
        return 0

    problems = diff(json.loads(args.manifest.read_text()), actual)
    for kind, path in problems:
        print(f"{kind}: {path}")
    if problems:
        print(f"FAILED: {len(problems)} difference(s)")
        return 1
    print(f"OK: {len(actual)} files unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
