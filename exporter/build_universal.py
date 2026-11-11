#!/usr/bin/env python3
"""Build the exporter for every version of the game and loader it supports, and put them all in one jar.

Usage: python exporter/build_universal.py [--no-build] [--only TARGET ...]

Each target in targets/ is its own Gradle build, which compiles the code for its version of the game with its
loader's glue, and maps the game's names to those the loader uses at run time. This builds each of them, takes
their classes, compiles the classes every target shares and the loaders' entry classes for Java 8, and writes
dist/mcedit_exporter-<version>-universal.jar, with the metadata of every loader.

--no-build uses the targets' jars as they are.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The exporter's version, as boot/src/.../Boot.java gives it.
VERSION = re.search(r'VERSION = "([^"]+)"',
                    (HERE / "boot" / "src" / "net" / "mcedit" / "exporter" / "boot" / "Boot.java").read_text()).group(1)

# (Minecraft version, loader), each a folder in targets/ named "<version>-<loader>".
TARGETS = [
    ("1.7.10", "forge"),
    ("1.8.9", "forge"),
    ("1.9.4", "forge"),
    ("1.10.2", "forge"),
    ("1.11.2", "forge"),
    ("1.12.2", "forge"),
    ("1.13.2", "forge"),
    ("1.14.4", "forge"),
    ("1.14.4", "fabric"),
    ("1.15.2", "forge"),
    ("1.15.2", "fabric"),
    ("1.16.5", "forge"),
    ("1.16.5", "fabric"),
    ("1.17.1", "forge"),
    ("1.17.1", "fabric"),
    ("1.18.2", "forge"),
    ("1.18.2", "fabric"),
    ("1.19.2", "forge"),
    ("1.19.2", "fabric"),
    ("1.19.3", "forge"),
    ("1.19.3", "fabric"),
    ("1.19.4", "forge"),
    ("1.19.4", "fabric"),
    ("1.20.1", "forge"),
    ("1.20.1", "fabric"),
    ("1.20.4", "forge"),
    ("1.20.4", "neoforge"),
    ("1.20.4", "fabric"),
    ("1.20.6", "forge"),
    ("1.20.6", "neoforge"),
    ("1.20.6", "fabric"),
    ("1.21.1", "forge"),
    ("1.21.1", "neoforge"),
    ("1.21.1", "fabric"),
]
# From 1.21.2, a build for each loader for each release where the game's code that the exporter uses changed, and
# ALSO_SERVES says which releases in between each of them serves.
for _version in ("1.21.3", "1.21.4", "1.21.5", "1.21.8", "1.21.10", "1.21.11", "26.1.2", "26.2", "26.3"):
    TARGETS += [(_version, "forge"), (_version, "neoforge"), (_version, "fabric")]

# Other releases each build serves, where the game's code it uses is the same as in its own, as found by
# test_universal.py. Boot finds the build for a release from targets.properties, which this writes from here.
ALSO_SERVES = {
    "1.8.9-forge": ["1.8", "1.8.8"],
    "1.9.4-forge": ["1.9"],
    "1.10.2-forge": ["1.10"],
    "1.11.2-forge": ["1.11"],
    "1.12.2-forge": ["1.12", "1.12.1"],
    "1.16.5-forge": ["1.16.1", "1.16.2", "1.16.3", "1.16.4"],
    "1.16.5-fabric": ["1.16", "1.16.1", "1.16.2", "1.16.3", "1.16.4"],
    "1.18.2-forge": ["1.18", "1.18.1"],
    "1.18.2-fabric": ["1.18", "1.18.1"],
    "1.19.2-forge": ["1.19", "1.19.1"],
    "1.19.2-fabric": ["1.19", "1.19.1"],
    "1.20.1-forge": ["1.20"],
    "1.20.1-fabric": ["1.20"],
    "1.14.4-fabric": ["1.14", "1.14.1", "1.14.2", "1.14.3"],
    "1.15.2-forge": ["1.15", "1.15.1"],
    "1.15.2-fabric": ["1.15", "1.15.1"],
    "1.17.1-fabric": ["1.17"],
    "1.20.4-forge": ["1.20.2", "1.20.3"],
    "1.20.4-neoforge": ["1.20.2", "1.20.3"],
    "1.20.4-fabric": ["1.20.2", "1.20.3"],
    "1.20.6-neoforge": ["1.20.5"],
    "1.20.6-fabric": ["1.20.5"],
    "1.21.1-forge": ["1.21"],
    "1.21.1-neoforge": ["1.21"],
    "1.21.1-fabric": ["1.21"],
}
for _loader in ("forge", "neoforge", "fabric"):
    for _version, _others in (("1.21.3", ["1.21.2"]), ("1.21.8", ["1.21.6", "1.21.7"]), ("1.21.10", ["1.21.9"]),
                              ("26.1.2", ["26.1", "26.1.1"])):
        # Forge has no build for 1.21.2.
        ALSO_SERVES["%s-%s" % (_version, _loader)] = [v for v in _others if not (_loader == "forge" and v == "1.21.2")]

GRADLE_CACHE = Path.home() / ".gradle" / "caches" / "modules-2" / "files-2.1"


def run(command, **kwargs):
    print("+", " ".join(str(c) for c in command), flush=True)
    subprocess.run([str(c) for c in command], check=True, **kwargs)


def targetName(version, loader):
    return "%s-%s" % (version, loader)


def targetPackage(version, loader):
    return "net/mcedit/exporter/v%s_%s/" % (version.replace(".", "_"), loader)


def buildTarget(version, loader):
    """ With the target's own Gradle wrapper if it has one, as ForgeGradle needs an older Gradle, else the shared one. """
    folder = HERE / "targets" / targetName(version, loader)
    script = "gradlew.bat" if os.name == "nt" else "gradlew"
    ownWrapper = (folder / script).exists()
    gradlew = folder / script if ownWrapper else HERE / script
    # ForgeGradle keeps the first project it sees in a static field, so a Gradle daemon can't be shared by builds.
    run([gradlew, "-p", folder, "build", "--console=plain"] + (["--no-daemon"] if ownWrapper else []), cwd=folder)


def targetJar(version, loader):
    name = targetName(version, loader)
    return HERE / "targets" / name / "build" / "libs" / ("mcedit_exporter-%s.jar" % name)


def oldestGson():
    """ The oldest Gson in the Gradle cache, so that the shared code uses nothing newer than the oldest game has. """
    jars = [Path(p) for p in glob.glob(str(GRADLE_CACHE / "com.google.code.gson" / "gson" / "*" / "*" / "gson-*.jar"))
            if "sources" not in p]
    if not jars:
        raise SystemExit("No Gson jar in the Gradle cache: build a target first.")

    def key(path):
        return tuple(int(n) for n in re.findall(r"\d+", path.parent.parent.name))
    return min(jars, key=key)


def javac():
    home = os.environ.get("JAVA_HOME")
    if home:
        candidate = Path(home) / "bin" / ("javac.exe" if os.name == "nt" else "javac")
        if candidate.exists():
            return candidate
    found = shutil.which("javac")
    if not found:
        raise SystemExit("javac was not found. Set JAVA_HOME to a JDK.")
    return found


def compileShared(out):
    """ The shared code and the entry classes, for Java 8. The stubs stand in for the loaders' classes. """
    sources = [str(p) for p in (HERE / "common" / "src" / "main" / "java").rglob("*.java")]
    sources += [str(p) for p in (HERE / "boot" / "src").rglob("*.java")]
    stubs = [str(p) for p in (HERE / "boot" / "stubs").rglob("*.java")]
    stubOut = out.parent / "stubs"
    stubOut.mkdir(parents=True, exist_ok=True)
    gson = oldestGson()
    print("Compiling the shared code against", gson.name, flush=True)
    run([javac(), "--release", "8", "-encoding", "UTF-8", "-nowarn", "-d", stubOut] + stubs)
    run([javac(), "--release", "8", "-encoding", "UTF-8", "-Xlint:-options", "-d", out,
         "-cp", os.pathsep.join([str(stubOut), str(gson)])] + sources)


def served(version, loader):
    """ The releases a build serves: its own, and any in ALSO_SERVES. """
    return [version] + ALSO_SERVES.get(targetName(version, loader), [])


def versionRanges(loader):
    versions = {v for version, l in TARGETS if l == loader for v in served(version, loader)}
    return sorted(versions, key=lambda v: [int(n) for n in v.split(".")])


def targetsTable():
    """ boot/targets.properties: the package of the build for each loader and release. """
    lines = ["# Which build of the exporter serves each loader and release. Written by build_universal.py."]
    for version, loader in TARGETS:
        package = targetPackage(version, loader).rstrip("/").rpartition("/")[2]
        for release in served(version, loader):
            lines.append("%s.%s=%s" % (loader, release, package))
    return "\n".join(lines) + "\n"


def metadata():
    """ The loaders' metadata files, with the versions each loader has a build for. """
    forge = versionRanges("forge")
    neoforge = versionRanges("neoforge")
    fabric = versionRanges("fabric")
    files = {}
    for path in (HERE / "boot" / "resources").rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        text = text.replace("@VERSION@", VERSION)
        # Forge and NeoForge take Maven version ranges, and a list of them is any of them.
        # mods.toml is read by Forge from 1.13, and by NeoForge until 1.20.4.
        modern = [v for v in forge if [int(n) for n in v.split(".")] >= [1, 13]]
        modern += [v for v in neoforge if [int(n) for n in v.split(".")] < [1, 20, 5] and v not in modern]
        text = text.replace("@FORGE_VERSIONS@", ",".join("[%s]" % v for v in modern) or "[0]")
        text = text.replace("@NEOFORGE_VERSIONS@", ",".join("[%s]" % v for v in neoforge) or "[0]")
        text = text.replace("@FABRIC_VERSIONS@", json.dumps(fabric or ["0"]))
        legacy = [v for v in forge if [int(n) for n in v.split(".")] < [1, 13]]
        text = text.replace("@LEGACY_VERSIONS@", ", ".join(legacy))
        files[path.relative_to(HERE / "boot" / "resources").as_posix()] = text
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--only", nargs="*", help="build only these targets, such as 1.20.1-forge")
    args = parser.parse_args()

    if not args.no_build:
        for version, loader in TARGETS:
            if not args.only or targetName(version, loader) in args.only:
                buildTarget(version, loader)

    out = HERE / "dist" / ("mcedit_exporter-%s-universal.jar" % VERSION)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        shared = Path(temp) / "classes"
        shared.mkdir()
        compileShared(shared)

        entries = {}
        for path in shared.rglob("*.class"):
            entries[path.relative_to(shared).as_posix()] = path.read_bytes()

        for version, loader in TARGETS:
            jar = targetJar(version, loader)
            if not jar.exists():
                raise SystemExit("%s has not been built" % jar)
            package = targetPackage(version, loader)
            taken = 0
            with zipfile.ZipFile(jar) as z:
                for name in z.namelist():
                    if name.startswith(package) and name.endswith(".class"):
                        entries[name] = z.read(name)
                        taken += 1
            if not taken:
                raise SystemExit("%s has no classes in %s" % (jar, package))
            print("%s: %d classes" % (targetName(version, loader), taken), flush=True)

        manifest = ("Manifest-Version: 1.0\r\nImplementation-Title: MCEdit Exporter\r\n"
                    "Implementation-Version: %s\r\n\r\n" % VERSION)
        # Written beside it and then put in its place, so that nothing copies it half written.
        partial = out.with_name(out.name + ".partial")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("META-INF/MANIFEST.MF", manifest)
            for name, text in sorted(metadata().items()):
                z.writestr(name, text)
            z.writestr("net/mcedit/exporter/boot/targets.properties", targetsTable())
            for name, data in sorted(entries.items()):
                z.writestr(name, data)
        os.replace(partial, out)
    print("Wrote", out, "(%d KB)" % (out.stat().st_size // 1024))


if __name__ == "__main__":
    sys.exit(main())
