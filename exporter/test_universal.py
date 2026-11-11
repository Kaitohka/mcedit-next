#!/usr/bin/env python3
"""Start each supported version of the game, with each loader, with the universal jar in its mods, and check what
it exports.

Usage: python exporter/test_universal.py [--only 1.20.1-fabric ...] [--scope all]

Run build_universal.py first, and scripts/versions/make_reports.py for the vanilla reports to compare with. The game
is installed and started by portablemc, in worlds_local/tools/launcher-venv, offline, with the system property that
makes the exporter export once the title screen is up and then quit. Fabric targets also get Fabric API, from
Modrinth. For each, this reports whether the exporter started, how many blocks and states it exported, how
that compares with the game's own list of blocks, and how many problems it reported. Results also go in
worlds_local/exporter_test/results.json.

Each run opens a game window, and takes a minute or two, longer the first time a version is installed.
"""
import argparse
import glob
import gzip
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import build_universal  # noqa: E402

WORK = ROOT / "worlds_local" / "exporter_test"
LAUNCHER = ROOT / "worlds_local" / "tools" / "launcher-venv" / "Scripts" / "portablemc.exe"
VERSIONS = ROOT / "worlds_local" / "versions"
TIMEOUT = 600


FABRIC_API_CACHE = ROOT / "worlds_local" / "tools" / "fabric-api"


def fabricApiJar(version):
    """
    Fabric API as players get it, for the given version of the game, from Modrinth. The jar of the same name in the
    Gradle cache is only a list of its modules, without their classes.
    """
    import urllib.parse
    import urllib.request
    folder = FABRIC_API_CACHE / version
    found = sorted(folder.glob("*.jar")) if folder.exists() else []
    if found:
        return found[-1]
    query = urllib.parse.urlencode({"game_versions": json.dumps([version]), "loaders": json.dumps(["fabric"])})
    request = urllib.request.Request("https://api.modrinth.com/v2/project/fabric-api/version?" + query,
                                     headers={"User-Agent": "mcedit-exporter-test"})
    versions = json.loads(urllib.request.urlopen(request, timeout=60).read())
    if not versions:
        return None
    file = next(f for f in versions[0]["files"] if f.get("primary")) if any(
        f.get("primary") for f in versions[0]["files"]) else versions[0]["files"][0]
    folder.mkdir(parents=True, exist_ok=True)
    data = urllib.request.urlopen(urllib.request.Request(file["url"], headers={"User-Agent": "mcedit-exporter-test"}),
                                  timeout=300).read()
    (folder / file["filename"]).write_bytes(data)
    return folder / file["filename"]


def vanillaStates(version):
    report = VERSIONS / version / "reports" / "blocks.json"
    if not report.exists():
        return None, None
    blocks = json.loads(report.read_text(encoding="utf-8"))
    return len(blocks), sum(len(b["states"]) for b in blocks.values())


def newestExport(gameDir):
    exports = sorted((gameDir / "mcedit_export").glob("*/manifest.json")) if (gameDir / "mcedit_export").exists() else []
    return exports[-1].parent if exports else None


# Lines after which the game will not export, so that it need not be waited for.
FAILED = ("[MCEdit Exporter] Could not start", "[MCEdit Exporter] This jar has no exporter",
          "[MCEdit Exporter] Fabric API is not installed", "Incompatible mods found", "#@!@# Game crashed!",
          "Missing or unsupported mandatory dependencies")


def waitForGame(process, log):
    """ Until the game quits, as it does once it has exported, or it is clear that it will not export. """
    deadline = time.time() + TIMEOUT
    while process.poll() is None:
        if time.time() > deadline or any(line in log.read_text(encoding="utf-8", errors="replace") for line in FAILED):
            time.sleep(3)  # for the rest of the error to be written
            # The game runs as a child of the launcher, and must go too.
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
            process.wait()
            return
        time.sleep(5)


JAVA17 = ROOT / "worlds_local" / "java" / "17-jdk"


def jvmFor(version):
    """
    Java 17 for the releases that run on it, if the JDK is there. The launcher may otherwise pick a newer Java, which
    the earliest Forge for 1.18 does not start on.
    """
    numbers = [int(n) for n in version.split(".")]
    if not [1, 17] <= numbers < [1, 20, 5]:
        return None
    found = sorted(JAVA17.glob("*/bin/javaw.exe"))
    return found[-1] if found else None


def confirmExperimental(levelDat):
    """
    Say yes to the warning a world with experimental settings shows when it is opened, as a player clicking
    Proceed would, so that a world joined at start opens without waiting for a click.
    """
    sys.path.insert(0, str(ROOT / "src"))
    from mceditlib import nbt
    root = nbt.load(str(levelDat))
    root["Data"]["confirmedExperimentalSettings"] = nbt.TAG_Byte(1)
    root.save(str(levelDat))


def runTarget(version, loader, scope, extraMods=(), inWorld=False, properties=()):
    name = "%s-%s" % (version, loader)
    gameDir = WORK / name
    mods = gameDir / "mods"
    if mods.exists():
        shutil.rmtree(mods)
    mods.mkdir(parents=True)
    if (gameDir / "mcedit_export").exists():
        shutil.rmtree(gameDir / "mcedit_export")
    jar = HERE / "dist" / ("mcedit_exporter-%s-universal.jar" % build_universal.VERSION)
    shutil.copy(jar, mods / jar.name)
    if loader == "fabric":
        api = fabricApiJar(version)
        if api is None:
            return {"target": name, "error": "no Fabric API for %s on Modrinth" % version}
        shutil.copy(api, mods / api.name)
    for extra in extraMods:
        shutil.copy(extra, mods / Path(extra).name)
    if inWorld:
        # A copy of the release's own test world, made by scripts/versions, to export in.
        saves = gameDir / "saves" / "mcedit_export_world"
        if saves.exists():
            shutil.rmtree(saves)
        shutil.copytree(ROOT / "worlds_local" / "versions" / version / "run" / "normal", saves)
        (saves / "session.lock").unlink(missing_ok=True)
        confirmExperimental(saves / "level.dat")
        # A first start shows the accessibility screen, which waits for a click before joining the world.
        options = gameDir / "options.txt"
        lines = options.read_text(encoding="utf-8").splitlines() if options.exists() else []
        lines = [line for line in lines if not line.startswith("onboardAccessibility:")]
        options.write_text("".join(line + "\n" for line in lines + ["onboardAccessibility:false"]), encoding="utf-8")

    log = gameDir / "launch.log"
    started = time.time()
    # Some releases have only beta versions of NeoForge, which the launcher takes only when asked.
    for spec in ("%s:%s" % (loader, version), "%s:%s:unstable" % (loader, version)):
        jvm = jvmFor(version)
        command = [str(LAUNCHER), "--main-dir", str(WORK / "launcher"), "start", "--mc-dir", str(gameDir),
                   "-u", "Tester", "--jvm-arg=-D%s=%s" % ("mcedit.exporter.autoExport", scope)]
        command += ["--jvm", str(jvm)] if jvm else []
        if inWorld:
            command += ["--jvm-arg=-Dmcedit.exporter.inWorld=true", "--join-world", "mcedit_export_world"]
        command += ["--jvm-arg=-D%s" % prop for prop in properties]
        if loader == "forge":
            # Forge for 1.20.3 looks for its libraries here, and the launcher does not say where they are.
            command += ["--jvm-arg=-DlibraryDirectory=%s" % (WORK / "launcher" / "libraries")]
        command += [spec]
        with open(log, "w", encoding="utf-8", errors="replace") as out:
            process = subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT)
            waitForGame(process, log)
        text = log.read_text(encoding="utf-8", errors="replace")
        if "latest stable loader version" not in text:
            break
    seconds = round(time.time() - started)
    unstable = spec.endswith(":unstable")
    result = {"target": name, "seconds": seconds, "unstableLoader": unstable,
              "exporterLog": [line.strip()[-200:] for line in text.splitlines() if "MCEdit Exporter" in line
                              or "mcedit_exporter" in line][:8]}
    export = newestExport(gameDir)
    if export is None:
        crash = [line.strip()[-200:] for line in text.splitlines() if "Exception" in line or "Error" in line][:5]
        result["error"] = "nothing was exported"
        result["logErrors"] = crash
        return result
    manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
    counts = manifest["counts"]
    result.update(blocks=counts["blocks"], states=counts["states"], problems=counts["problems"],
                  loader=manifest.get("loader"), loaderVersion=manifest.get("loaderVersion"),
                  minecraftVersion=manifest.get("minecraftVersion"), export=str(export))
    vanillaBlocks, vanillaStateCount = vanillaStates(version)
    if scope in ("all", "vanilla") and vanillaStateCount:
        with gzip.open(export / "blocks.json.gz") as f:
            exported = json.load(f)["blocks"]
        vanilla = [b for b in exported if b["id"].startswith("minecraft:")]
        result["vanillaBlocks"] = "%d of %d" % (len(vanilla), vanillaBlocks)
        result["vanillaStates"] = "%d of %d" % (sum(len(b["states"]) for b in vanilla), vanillaStateCount)
    report = json.loads((export / "report.json").read_text(encoding="utf-8"))
    result["firstProblems"] = report["problems"][:5]
    return result


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--scope", default="all", choices=["all", "vanilla", "mods"])
    parser.add_argument("--extra-mod", action="append", default=[], metavar="JAR",
                        help="another mod to put in the mods folder, such as one to test the export with")
    parser.add_argument("--jvm-property", action="append", default=[], metavar="NAME=VALUE",
                        help="a Java system property for the game, as mcedit.exporter.gameRenderers=true, which also "
                             "takes what the game's own block entity renderers draw, to test that")
    parser.add_argument("--in-world", action="store_true",
                        help="export in a copy of the release's test world, as some block entity renderers draw "
                             "nothing at the title screen (1.20 on, which can join a world at start)")
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    resultsFile = WORK / "results.json"
    results = json.loads(resultsFile.read_text(encoding="utf-8")) if resultsFile.exists() else {}
    # Every release each build serves, as the build for one release often serves those around it too.
    for version, loader in build_universal.TARGETS:
        for release in build_universal.served(version, loader):
            name = "%s-%s" % (release, loader)
            if args.only and name not in args.only:
                continue
            result = runTarget(release, loader, args.scope, args.extra_mod, args.in_world, args.jvm_property)
            result["build"] = build_universal.targetName(version, loader)
            results[name] = result
            print(json.dumps(result, indent=1, ensure_ascii=False), flush=True)
            with open(resultsFile, "w", encoding="utf-8", newline="\n") as f:
                json.dump(results, f, indent=1, ensure_ascii=False)
                f.write("\n")


if __name__ == "__main__":
    sys.exit(main())
