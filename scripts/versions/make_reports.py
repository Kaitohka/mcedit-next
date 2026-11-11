#!/usr/bin/env python3
"""Run the data generator of each release from 1.13 on, to list its blocks, block states and registries.

Usage: python scripts/versions/make_reports.py [--only ID ...] [--redo]

Run scripts/versions/fetch_versions.py first. The data generator does not start a server, so this needs no
EULA. For each release it writes worlds_local/versions/<id>/reports/, where blocks.json lists every block
and its states, and registries.json the game's registries.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_versions import VERSIONS, javaExecutable  # noqa: E402
from make_test_worlds import atLeast  # noqa: E402

FIRST_WITH_REPORTS = "1.13"
FIRST_BUNDLED_SERVER = "1.18"  # from here the server jar holds the game in a nested jar


def makeReports(release, javaMajor, redo):
    folder = VERSIONS / release
    out = folder / "reports"
    if (out / "blocks.json").exists() and not redo:
        return "already made"
    java = javaExecutable(javaMajor)
    if java is None:
        return "no Java %s" % javaMajor
    jar = folder / "server.jar"
    if not jar.exists():
        return "no server.jar"

    work = folder / "datagen"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    if atLeast(release, FIRST_BUNDLED_SERVER):
        command = [str(java), "-DbundlerMainClass=net.minecraft.data.Main", "-jar", str(jar)]
    else:
        command = [str(java), "-cp", str(jar), "net.minecraft.data.Main"]
    command += ["--reports", "--output", str(work / "generated")]
    with open(folder / "datagen.log", "w", encoding="utf-8", newline="\n") as log:
        result = subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT, timeout=900,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    generated = work / "generated" / "reports"
    if result.returncode != 0 or not (generated / "blocks.json").exists():
        return "data generator failed (exit %d); see %s" % (result.returncode, folder / "datagen.log")

    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(generated, out)
    shutil.rmtree(work)
    with open(out / "blocks.json", encoding="utf-8") as f:
        blocks = json.load(f)
    return "%d blocks, %d states" % (len(blocks), sum(len(b["states"]) for b in blocks.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--redo", action="store_true")
    args = parser.parse_args()

    with open(VERSIONS / "releases.json", encoding="utf-8") as f:
        releases = json.load(f)
    for info in releases:
        release = info["id"]
        if not atLeast(release, FIRST_WITH_REPORTS) or (args.only and release not in args.only):
            continue
        try:
            result = makeReports(release, info["javaToUse"], args.redo)
        except Exception as e:
            result = "failed: %r" % e
        print("%s: %s" % (release, result), flush=True)


if __name__ == "__main__":
    sys.exit(main())
