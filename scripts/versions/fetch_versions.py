#!/usr/bin/env python3
"""Download the server and client of every Minecraft release from 1.7.10 to 26.3, and the Java runtimes they need.

Usage: python scripts/versions/fetch_versions.py [--first 1.7.10] [--last 26.3] [--only ID ...] [--no-java]

Everything goes in worlds_local/, which git ignores. The jars belong to Mojang: they are for testing on this
computer only, and must not be committed or passed on.

  worlds_local/versions/releases.json      the releases, oldest first, with their Java version
  worlds_local/versions/<id>/server.jar
  worlds_local/versions/<id>/client.jar
  worlds_local/java/<major>/               a Java runtime from Eclipse Temurin

Files already there with the right SHA-1 are not downloaded again.
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOCAL = ROOT / "worlds_local"
VERSIONS = LOCAL / "versions"
JAVA = LOCAL / "java"

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
TEMURIN_URL = "https://api.adoptium.net/v3/binary/latest/{major}/ga/windows/x64/jre/hotspot/normal/eclipse"

# Temurin has no Java 16 any more. The game versions that ask for it run on 17.
JAVA_SUBSTITUTES = {16: 17}


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "mcedit-version-fetch"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(url, path, expectedSha1):
    if path.exists() and sha1(path) == expectedSha1:
        return False
    data = fetch(url)
    if hashlib.sha1(data).hexdigest() != expectedSha1:
        raise IOError("%s did not match its SHA-1" % url)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_bytes(data)
    os.replace(temp, path)
    return True


def releasesBetween(manifest, first, last):
    """ The releases from first to last, oldest first. """
    releases = [v for v in manifest["versions"] if v["type"] == "release"]
    releases.sort(key=lambda v: v["releaseTime"])
    ids = [v["id"] for v in releases]
    for name in first, last:
        if name not in ids:
            raise SystemExit("%s is not a release in Mojang's version list" % name)
    return releases[ids.index(first):ids.index(last) + 1]


def javaFor(major):
    return JAVA_SUBSTITUTES.get(major, major)


def javaExecutable(major):
    """ The java.exe of the downloaded runtime, or None. """
    for candidate in (JAVA / str(major)).glob("*/bin/java.exe"):
        return candidate
    return None


def fetchJava(major):
    if javaExecutable(major):
        return False
    print("Java %d: downloading from Eclipse Temurin" % major, flush=True)
    data = fetch(TEMURIN_URL.format(major=major))
    target = JAVA / str(major)
    if target.exists():
        shutil.rmtree(target)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(target)
    if not javaExecutable(major):
        raise IOError("The Java %d download has no bin/java.exe" % major)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--first", default="1.7.10")
    parser.add_argument("--last", default="26.3")
    parser.add_argument("--only", nargs="*", help="download only these releases")
    parser.add_argument("--no-java", action="store_true", help="do not download Java runtimes")
    args = parser.parse_args()

    manifest = json.loads(fetch(MANIFEST_URL))
    releases = releasesBetween(manifest, args.first, args.last)
    print("%d releases from %s to %s" % (len(releases), args.first, args.last), flush=True)

    listed = []
    for entry in releases:
        info = json.loads(fetch(entry["url"]))
        downloads = info.get("downloads", {})
        major = info.get("javaVersion", {}).get("majorVersion", 8)
        listed.append({
            "id": entry["id"],
            "releaseTime": entry["releaseTime"],
            "javaMajor": major,
            "javaToUse": javaFor(major),
            "hasServer": "server" in downloads,
            "hasClient": "client" in downloads,
        })
        if args.only and entry["id"] not in args.only:
            continue
        folder = VERSIONS / entry["id"]
        for side in "server", "client":
            if side not in downloads:
                print("%s: no %s download" % (entry["id"], side), flush=True)
                continue
            d = downloads[side]
            got = download(d["url"], folder / (side + ".jar"), d["sha1"])
            print("%s: %s %s" % (entry["id"], side, "downloaded" if got else "already there"), flush=True)

    VERSIONS.mkdir(parents=True, exist_ok=True)
    with open(VERSIONS / "releases.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(listed, f, indent=2)
        f.write("\n")

    if not args.no_java:
        for major in sorted({r["javaToUse"] for r in listed}):
            fetchJava(major)
            print("Java %d: %s" % (major, javaExecutable(major)), flush=True)


if __name__ == "__main__":
    sys.exit(main())
