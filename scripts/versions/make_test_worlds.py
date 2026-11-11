#!/usr/bin/env python3
"""Have each release's own server make test worlds, for checking that MCEdit reads that release's worlds.

Usage: python scripts/versions/make_test_worlds.py --accept-eula [--only ID ...] [--worlds normal debug]
                                                  [--parallel 2] [--redo]

Run scripts/versions/fetch_versions.py first. For each release this makes, in worlds_local/versions/<id>/run/:

  normal/   about 400 chunks of terrain around 0,0 with a chest, a sign and some mobs, plus some of the
            Nether and the End where the release has the commands for it (1.13.1 on)
  debug/    a debug world, with every block state of the release in a grid (1.8 on). From 1.16 to 26.2 the
            game does not save a debug world's chunks, so these have none.

Starting a Minecraft server means agreeing to the Minecraft EULA (https://aka.ms/MinecraftEULA), so this does
nothing without --accept-eula. The worlds are for testing on this computer only.
"""
import argparse
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_versions import ROOT, VERSIONS, javaExecutable  # noqa: E402

sys.path.insert(0, str(ROOT / "src"))
from mceditlib import nbt  # noqa: E402

SEED = "20261108"
SERVER_START_TIMEOUT = 600
LOG_LOCK = threading.Lock()


def say(release, *words):
    with LOG_LOCK:
        print("[%s]" % release, *words, flush=True)


def versionKey(release):
    """ A sortable form of a release name: 1.7.10 -> (1, 7, 10), 26.3 -> (26, 3, 0). """
    parts = [int(p) for p in release.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def atLeast(release, other):
    return versionKey(release) >= versionKey(other)


def freePort():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def chunkSquares(x0, z0, x1, z1):
    """ Split a block rectangle into pieces of at most 16 x 16 chunks, the most one forceload command takes. """
    step = 16 * 16
    for x in range(x0, x1 + 1, step):
        for z in range(z0, z1 + 1, step):
            yield x, z, min(x + step - 1, x1), min(z + step - 1, z1)


def forceloadCommands(release, x0, z0, x1, z1, dimension=None):
    commands = []
    for a, b, c, d in chunkSquares(x0, z0, x1, z1):
        command = "forceload add %d %d %d %d" % (a, b, c, d)
        if dimension:
            command = "execute in %s run %s" % (dimension, command)
        commands.append(command)
    return commands


def contentCommands(release):
    """ Commands that put a chest, a sign and some mobs at about 8, 100, 8. """
    if not atLeast(release, "1.8"):
        # 1.7.10 has /summon but no /setblock or /fill.
        return ["summon %s 8 100 8" % mob for mob in ("Zombie", "Skeleton", "Creeper", "Villager", "Pig")]

    commands = ["fill 0 99 0 16 99 16 minecraft:stone"]
    if atLeast(release, "1.20.5"):
        item = '{Slot:0b,id:"minecraft:diamond",count:1}'
    else:
        item = '{Slot:0b,id:"minecraft:diamond",Count:1b}'

    if atLeast(release, "1.13"):
        commands.append("setblock 8 100 8 minecraft:chest{Items:[%s]}" % item)
        commands.append("setblock 10 100 8 minecraft:%s" % ("oak_sign" if atLeast(release, "1.14") else "sign"))
        commands.append("setblock 12 100 8 minecraft:furnace")
    else:
        commands.append("setblock 8 100 8 minecraft:chest 0 replace {Items:[%s]}" % item)
        commands.append("setblock 10 100 8 minecraft:standing_sign 0 replace")
        commands.append("setblock 12 100 8 minecraft:furnace 0 replace")

    if atLeast(release, "1.11"):
        mobs = ["minecraft:zombie", "minecraft:skeleton", "minecraft:creeper", "minecraft:villager",
                "minecraft:pig", "minecraft:armor_stand"]
    else:
        mobs = ["Zombie", "Skeleton", "Creeper", "Villager", "Pig", "ArmorStand"]
    for i, mob in enumerate(mobs):
        commands.append("summon %s %d 100 12 {NoAI:1b}" % (mob, 2 + 2 * i))
    return commands


def regionBytes(world):
    total = 0
    for folder, dirs, files in os.walk(world):
        total += sum(os.path.getsize(os.path.join(folder, f)) for f in files if f.endswith(".mca"))
    return total


class Server(object):
    def __init__(self, release, java, jar, runDir, levelName, levelType):
        self.release = release
        self.runDir = runDir
        self.levelName = levelName
        self.lines = queue.Queue()
        self.log = open(runDir / ("%s.log" % levelName), "w", encoding="utf-8", newline="\n")

        properties = {
            "level-name": levelName,
            "level-seed": SEED,
            "level-type": levelType,
            "online-mode": "false",
            "server-port": str(freePort()),
            "server-ip": "127.0.0.1",
            "spawn-protection": "0",
            "max-players": "1",
            "snooper-enabled": "false",
            "enable-query": "false",
            "enable-rcon": "false",
            "view-distance": "4",
            "sync-chunk-writes": "true",
            "max-tick-time": "-1",  # generating many chunks at once can take longer than the watchdog allows
        }
        with open(runDir / "server.properties", "w", encoding="utf-8", newline="\n") as f:
            for key, value in properties.items():
                f.write("%s=%s\n" % (key, value))
        # The user agreed to the Minecraft EULA by passing --accept-eula.
        with open(runDir / "eula.txt", "w", encoding="utf-8", newline="\n") as f:
            f.write("eula=true\n")

        command = [str(java), "-Xms512M", "-Xmx2G", "-jar", str(jar), "nogui"]
        self.process = subprocess.Popen(command, cwd=runDir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in self.process.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            self.log.write(line + "\n")
            self.log.flush()
            self.lines.put(line)
        self.lines.put(None)

    def waitFor(self, pattern, timeout):
        """ Wait for an output line matching pattern, and return it, or None if the server stopped first. """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self.lines.get(timeout=1)
            except queue.Empty:
                continue
            if line is None:
                return None
            if re.search(pattern, line):
                return line
        raise TimeoutError("%s: no line matching %r after %d s" % (self.release, pattern, timeout))

    def drain(self):
        """ Return the output lines that have come since the last call. """
        lines = []
        while True:
            try:
                line = self.lines.get_nowait()
            except queue.Empty:
                return lines
            if line is None:
                return lines
            lines.append(line)

    def send(self, command):
        self.log.write("> %s\n" % command)
        self.process.stdin.write((command + "\n").encode("utf-8"))
        self.process.stdin.flush()

    def saveUntilSettled(self, world, minimum, maximum):
        """ Save over and over until the region files stop growing, so that what was asked for is generated. """
        start = time.time()
        last = -1
        steady = 0
        flush = atLeast(self.release, "1.13")
        while time.time() - start < maximum:
            self.send("save-all flush" if flush else "save-all")
            time.sleep(10)
            size = regionBytes(world)
            steady = steady + 1 if size == last else 0
            last = size
            if steady >= 2 and time.time() - start >= minimum:
                break
        return last

    def stop(self):
        try:
            self.send("stop")
        except OSError:
            pass
        try:
            self.process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.log.close()


def worldIsRight(world, kind):
    """ Whether a world was made, and made as the right kind: its level name is the kind. """
    try:
        return nbt.load(str(world / "level.dat"))["Data"]["LevelName"].value == kind
    except Exception:
        return False


def startServer(release, java, jar, runDir, kind, levelType):
    """ Start a server and wait until it is ready. Returns None if it could not start. """
    for attempt in range(3):
        server = Server(release, java, jar, runDir, kind, levelType)
        line = server.waitFor(r"Done \(|FAILED TO BIND", SERVER_START_TIMEOUT)
        if line is not None and "FAILED TO BIND" not in line:
            return server
        server.stop()
        say(release, kind, "could not start (%s), trying again" % (line or "stopped"))
    return None


def makeWorld(release, info, kind, redo):
    java = javaExecutable(info["javaToUse"])
    jar = VERSIONS / release / "server.jar"
    runDir = VERSIONS / release / "run"
    world = runDir / kind
    done = runDir / (kind + ".done")
    if done.exists() and worldIsRight(world, kind) and not redo:
        say(release, kind, "already made")
        return "kept"
    if kind == "debug" and not atLeast(release, "1.8"):
        return "no debug world before 1.8"
    if java is None:
        return "no Java %s" % info["javaToUse"]
    if not jar.exists():
        return "no server.jar"

    runDir.mkdir(parents=True, exist_ok=True)
    if world.exists():
        shutil.rmtree(world)
    done.unlink(missing_ok=True)

    levelType = "debug_all_block_states" if kind == "debug" else "default"
    say(release, kind, "starting server with Java", info["javaToUse"])
    server = startServer(release, java, jar, runDir, kind, levelType)
    if server is None:
        return "server did not start; see %s/%s.log" % (runDir, kind)
    try:

        commands = []
        canForceload = atLeast(release, "1.13.1")
        if kind == "debug":
            if canForceload:
                # The grid runs along +x and +z from the origin, two blocks per state.
                commands += forceloadCommands(release, 0, 0, 400, 400)
        else:
            if canForceload:
                commands += forceloadCommands(release, -160, -160, 159, 159)
                commands += forceloadCommands(release, -48, -48, 47, 47, "minecraft:the_nether")
                commands += forceloadCommands(release, -48, -48, 47, 47, "minecraft:the_end")
            commands += contentCommands(release)

        for command in commands:
            server.send(command)
            time.sleep(0.3)
        time.sleep(2)
        failures = [line for line in server.drain()
                    if re.search(r"Unknown|Incorrect|Invalid|Couldn't|Could not|Expected|not loaded|Failed|Error",
                                 line, re.I) and "Can't keep up" not in line]

        size = server.saveUntilSettled(world, minimum=20 if kind == "debug" else 40, maximum=900)
        result = "%.1f MB of regions" % (size / 1e6)
        if failures:
            result += "; %d command problems, first: %s" % (len(failures), failures[0][-160:])
    finally:
        server.stop()

    if not worldIsRight(world, kind):
        return "no %s world was made; see %s/%s.log" % (kind, runDir, kind)
    done.write_text(result + "\n", encoding="utf-8")
    say(release, kind, "done:", result)
    return result


def makeWorlds(release, info, kinds, redo):
    """ Make one release's worlds one after another, as they share the server's folder and settings. """
    results = {}
    for kind in kinds:
        try:
            results[kind] = makeWorld(release, info, kind, redo)
        except Exception as e:
            results[kind] = "failed: %r" % e
            say(release, kind, "failed:", repr(e))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--accept-eula", action="store_true",
                        help="agree to the Minecraft EULA, https://aka.ms/MinecraftEULA, which starting a server needs")
    parser.add_argument("--only", nargs="*", help="only these releases")
    parser.add_argument("--worlds", nargs="*", default=["normal", "debug"], choices=["normal", "debug"])
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--redo", action="store_true", help="make worlds again even if they were made before")
    args = parser.parse_args()
    if not args.accept_eula:
        raise SystemExit("Starting a Minecraft server means agreeing to the Minecraft EULA, "
                         "https://aka.ms/MinecraftEULA. Pass --accept-eula if you agree.")

    with open(VERSIONS / "releases.json", encoding="utf-8") as f:
        releases = json.load(f)
    if args.only:
        releases = [r for r in releases if r["id"] in args.only]

    results = {}
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(makeWorlds, r["id"], r, args.worlds, args.redo): r["id"] for r in releases}
        for future, release in futures.items():
            for kind, result in future.result().items():
                results["%s %s" % (release, kind)] = result

    summary = VERSIONS / "test_worlds.json"
    old = json.loads(summary.read_text(encoding="utf-8")) if summary.exists() else {}
    old.update(results)
    with open(summary, "w", encoding="utf-8", newline="\n") as f:
        json.dump(old, f, indent=2, sort_keys=True)
        f.write("\n")
    for key in sorted(results, key=lambda k: (versionKey(k.split()[0]), k)):
        print(key, "-", results[key])


if __name__ == "__main__":
    sys.exit(main())
