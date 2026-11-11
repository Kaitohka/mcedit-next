#!/usr/bin/env python3
"""Save changes to each release's test world with MCEdit, have that release's server load and save it, and check
that the game kept what MCEdit wrote.

Usage: python scripts/versions/check_saving.py --accept-eula [--only ID ...] [--parallel 2]

Run scripts/versions/make_test_worlds.py first. For each release, a copy of its normal test world is edited in
worlds_local/save_check/<release>/:

  - a 16 x 4 x 16 box of mixed blocks, with properties, in the chunk at the spawn point;
  - a few blocks high above the ground, in a section the chunk did not have;
  - a new chunk away from everything, with a few blocks in it (releases with /forceload, 1.13.1 on);
  - every block state the release has, one per block, in chunks of their own.

The server then loads those chunks and changes one marker block in each, so that it saves them again in its own
way. The result is read back and compared with what was written, block by block, and the server's log is searched
for errors. Formats are opened for saving here whether or not the application allows it yet. Starting a server
means agreeing to the Minecraft EULA, so this does nothing without --accept-eula. Results go in
worlds_local/save_check/results.json.
"""
import argparse
import json
import re
import shutil
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_versions import ROOT, VERSIONS, javaExecutable  # noqa: E402
from make_test_worlds import atLeast, forceloadCommands, say, startServer, versionKey  # noqa: E402

sys.path.insert(0, str(ROOT / "src"))
from mceditlib.anvil import format_profile  # noqa: E402
from mceditlib.worldeditor import WorldEditor  # noqa: E402

WORK = ROOT / "worlds_local" / "save_check"

# Blocks for the mixed box, by their names in 1.13 and later, then in 1.12 and earlier.
MODERN_BOX = ["minecraft:stone", "minecraft:oak_planks", "minecraft:glass", "minecraft:white_wool",
              "minecraft:oak_stairs[facing=east,half=bottom,shape=straight,waterlogged=false]",
              "minecraft:oak_log[axis=x]", "minecraft:glowstone", "minecraft:bricks"]
OLD_BOX = ["minecraft:stone[variant=stone]", "minecraft:planks[variant=oak]", "minecraft:glass",
           "minecraft:wool[color=white]", "minecraft:oak_stairs[facing=east,half=bottom,shape=straight]",
           "minecraft:log[axis=x,variant=oak]", "minecraft:glowstone", "minecraft:brick_block"]

ERROR_PATTERN = re.compile(r"Couldn't load chunk|Failed to load|Couldn't read|Exception|Corrupt|Invalid|"
                           r"Unknown block|Unable to|Error loading|Chunk file at .* is in the wrong location|"
                           r"Missing|missing", re.I)
HARMLESS = ("Can't keep up", "Perflib", "Counters may need to be rebuilt", "SocketTimeoutException", "Failed to load user banlist", "Failed to load ip banlist", "Failed to load operators",
            "Failed to load white-list", "Ambiguity between arguments", "FileNotFoundException: banned", "FileNotFoundException: ops",
            "FileNotFoundException: whitelist", "Win32Exception", "Tried to load a block entity for block",
            "Unknown or incomplete command", "Incorrect argument for command", "Unknown game rule",
            "gamerule", "No key", "missing texture", "Missing language", "api.minecraftservices.com",
            "authlib")

MARKER = {"modern": "minecraft:diamond_block", "old": "minecraft:diamond_block"}


def typesByName(blocktypes, names):
    found = []
    for name in names:
        try:
            block = blocktypes[name]
        except KeyError:
            continue
        found.append((block.ID, block.meta, block.nameAndState))
    return found


def everyState(blocktypes, isModern):
    """ (ID, meta, name) of every state the block types know, but air. """
    aliases = getattr(blocktypes, "metaAliases", {})
    states = []
    for (ID, meta), name in sorted(blocktypes.statesByID.items()):
        if ID == 0 or (ID, meta) in aliases:
            continue
        if isModern and name.partition("[")[0] in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air"):
            continue
        if not isModern and name.startswith("minecraft:air"):
            continue
        states.append((ID, meta, name))
    return states


def blockIDsOfRelease(release):
    """
    The block IDs a release from before 1.13 has, from its debug world, or None if it has none. All of them use the
    table of 1.12.2, which has blocks the earlier ones don't.
    """
    debug = VERSIONS / release / "run" / "debug"
    if not (debug / "level.dat").exists():
        return None
    editor = WorldEditor(str(debug), readonly=True)
    IDs = set()
    try:
        dim = editor.getDimension()
        for cx, cz in dim.chunkPositions():
            chunk = dim.getChunk(cx, cz)
            for cy in chunk.sectionPositions():
                IDs.update(int(ID) for ID in numpy.unique(chunk.getSection(cy).Blocks))
    finally:
        editor.close()
    return IDs


class Plan(object):
    """ What was written where, to check afterwards: {(x, y, z): name}. """

    def __init__(self):
        self.blocks = {}
        self.chunks = set()  # chunks the server must load and save
        self.newChunk = None

    def put(self, dim, x, y, z, ID, meta, name, create=False):
        chunk = dim.getChunk(x >> 4, z >> 4)
        section = chunk.getSection(y >> 4, create=True)
        section.Blocks[y & 15, z & 15, x & 15] = ID
        section.Data[y & 15, z & 15, x & 15] = meta
        chunk.dirty = True
        self.blocks[x, y, z] = name
        self.chunks.add((x >> 4, z >> 4))


def edit(world, release):
    editor = WorldEditor(str(world))
    plan = Plan()
    try:
        isModern = hasattr(editor.adapter, "profile")
        blocktypes = editor.blocktypes
        dim = editor.getDimension()
        sx, sy, sz = (int(v) for v in editor.adapter.metadata.Spawn)
        bottom, top = (editor.adapter.heightRange("") if isModern else (0, 256))
        baseX, baseZ = sx >> 4 << 4, sz >> 4 << 4
        editor.beginUndo()

        # The mixed box, in the chunk at the spawn point, which every release keeps loaded.
        box = typesByName(blocktypes, MODERN_BOX if isModern else OLD_BOX)
        y0 = max(bottom + 1, min(sy + 20, top - 40))
        for y in range(y0, y0 + 4):
            for z in range(16):
                for x in range(16):
                    ID, meta, name = box[(x + z + y) % len(box)]
                    plan.put(dim, baseX + x, y, baseZ + z, ID, meta, name)

        # High up, where the chunk has no section yet (before 1.18; later ones save every section).
        for i, (ID, meta, name) in enumerate(box[:4]):
            plan.put(dim, baseX + 2 + i, top - 10, baseZ + 2, ID, meta, name)

        canForceload = atLeast(release, "1.13.1")
        if canForceload:
            # A new chunk, far from the generated ones.
            ncx, ncz = (baseX >> 4) + 40, baseZ >> 4
            if not dim.containsChunk(ncx, ncz):
                dim.createChunk(ncx, ncz)
                plan.newChunk = (ncx, ncz)
                for i, (ID, meta, name) in enumerate(box):
                    plan.put(dim, ncx * 16 + i, 64, ncz * 16 + 3, ID, meta, name)

        # Every state, sixteen layers to a chunk. Releases without /forceload use chunks the spawn area keeps loaded.
        states = everyState(blocktypes, isModern)
        if not isModern:
            known = blockIDsOfRelease(release)
            if known:
                states = [state for state in states if state[0] in known]
        perChunk = 16 * 16 * 16
        firstCx = (baseX >> 4) + (60 if canForceload else 2)
        yStates = max(bottom + 1, min(100, top - 30))
        for index, (ID, meta, name) in enumerate(states):
            chunkIndex, rest = divmod(index, perChunk)
            layer, rest = divmod(rest, 256)
            z, x = divmod(rest, 16)
            cx, cz = firstCx + chunkIndex % 4, (baseZ >> 4) + chunkIndex // 4
            if canForceload and not dim.containsChunk(cx, cz):
                dim.createChunk(cx, cz)
            plan.put(dim, cx * 16 + x, yStates + layer, cz * 16 + z, ID, meta, name)

        editor.commitUndo()
        editor.saveChanges()
        return plan, len(states), (yStates, top)
    finally:
        editor.close()


def runServer(release, info, world, plan, top):
    folder = world.parent
    java = javaExecutable(info["javaToUse"])
    server = startServer(release, java, VERSIONS / release / "server.jar", folder, world.name, "default")
    if server is None:
        raise RuntimeError("the server did not start")
    try:
        for rule in ("gamerule randomTickSpeed 0", "gamerule doFireTick false", "gamerule doMobSpawning false",
                     "gamerule minecraft:random_tick_speed 0", "gamerule minecraft:fire_spread_radius_around_player 0"):
            server.send(rule)
        if atLeast(release, "1.13.1"):
            for cx, cz in sorted(plan.chunks):
                for command in forceloadCommands(release, cx * 16, cz * 16, cx * 16 + 15, cz * 16 + 15):
                    server.send(command)
            time.sleep(10)
        # A marker in each chunk, so that the game saves it again itself. The command is there from 1.7.2.
        if atLeast(release, "1.7.2"):
            marker = MARKER["modern"] if atLeast(release, "1.13") else "minecraft:diamond_block"
            for cx, cz in sorted(plan.chunks):
                server.send("setblock %d %d %d %s" % (cx * 16 + 15, top - 2, cz * 16 + 15, marker))
                time.sleep(0.05)
        server.saveUntilSettled(world, minimum=20, maximum=300)
    finally:
        server.stop()
    return folder / ("%s.log" % world.name)


def verify(world, plan, release):
    editor = WorldEditor(str(world), readonly=True)
    wrong = []
    try:
        dim = editor.getDimension()
        markers = 0
        for (x, y, z), name in plan.blocks.items():
            found = dim.getBlock(x, y, z).nameAndState
            if found != name:
                wrong.append("%s at %s became %s" % (name, (x, y, z), found))
        isModern = hasattr(editor.adapter, "profile")
        top = editor.adapter.heightRange("")[1] if isModern else 256
        for cx, cz in plan.chunks:
            if dim.getBlock(cx * 16 + 15, top - 2, cz * 16 + 15).internalName == "minecraft:diamond_block":
                markers += 1
        relit = None
        if isModern:
            data = dim.getChunk(*next(iter(sorted(plan.chunks)))).chunkData
            tag = data._chunkTag
            relit = tag["isLightOn"].value if "isLightOn" in tag else None
    finally:
        editor.close()
    return wrong, markers, relit


def statesOf(editor, cx, cz):
    """ {(x, y, z): name} of the blocks that are not air in a chunk, or None if it has none. """
    dim = editor.getDimension()
    if not dim.containsChunk(cx, cz):
        return None
    chunk = dim.getChunk(cx, cz)
    found = {}
    names = editor.blocktypes.statesByID
    for cy in chunk.sectionPositions():
        section = chunk.getSection(cy)
        if section is None:
            continue
        pairs = section.Blocks.astype("u4") * 16 + section.Data
        for y, z, x in zip(*numpy.nonzero(section.Blocks)):
            pair = int(pairs[y, z, x])
            found[cx * 16 + int(x), cy * 16 + int(y), cz * 16 + int(z)] = names.get((pair >> 4, pair & 15), str(pair))
    return found


def unintendedChanges(source, world, plan, top):
    """ Blocks of the edited chunks that MCEdit was not told to change, and that are not the same as before. """
    before = WorldEditor(str(source), readonly=True)
    after = WorldEditor(str(world), readonly=True)
    changes = []
    try:
        for cx, cz in sorted(plan.chunks):
            old = statesOf(before, cx, cz)
            if old is None:
                continue  # a new chunk
            new = statesOf(after, cx, cz) or {}
            for position in set(old) | set(new):
                if position in plan.blocks or position == (cx * 16 + 15, top - 2, cz * 16 + 15):
                    continue
                if old.get(position) != new.get(position):
                    changes.append("%s at %s became %s" % (old.get(position), position, new.get(position)))
    finally:
        before.close()
        after.close()
    return changes


def logProblems(log):
    problems = []
    with open(log, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith(">"):
                continue
            if ERROR_PATTERN.search(line) and not any(h in line for h in HARMLESS):
                problems.append(line.strip()[:220])
    return problems


def checkRelease(release, info):
    source = VERSIONS / release / "run" / "normal"
    folder = WORK / release
    world = folder / "world"
    result = {"release": release}
    try:
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        shutil.copytree(source, world)
        for name in ("session.lock",):
            (world / name).unlink(missing_ok=True)
        plan, stateCount, (yStates, top) = edit(world, release)
        result["blocksWritten"] = len(plan.blocks)
        result["states"] = stateCount
        result["chunks"] = len(plan.chunks)
        result["newChunk"] = plan.newChunk is not None
        log = runServer(release, info, world, plan, top)
        result["logProblems"] = logProblems(log)[:10]
        wrong, markers, relit = verify(world, plan, release)
        unintended = unintendedChanges(source, world, plan, top)
        result["unintended"] = len(unintended)
        result["unintendedExamples"] = unintended[:12]
        result["wrong"] = len(wrong)
        result["wrongExamples"] = wrong[:12]
        result["markersSaved"] = markers
        result["isLightOn"] = relit
    except Exception:
        result["error"] = traceback.format_exc(limit=6)
    say(release, "save check:", summary(result))
    return result


def summary(result):
    if "error" in result:
        return "ERROR " + result["error"].strip().splitlines()[-1][:200]
    text = "wrote %d blocks (%d states) in %d chunks, %d changed by the game, %d/%d chunks saved again by it" % (
        result["blocksWritten"], result["states"], result["chunks"], result["wrong"], result["markersSaved"],
        result["chunks"])
    text += ", %d other blocks changed" % result.get("unintended", 0)
    if result.get("unintendedExamples"):
        text += " (e.g. %s)" % result["unintendedExamples"][0]
    if result.get("isLightOn") is not None:
        text += ", isLightOn %s" % result["isLightOn"]
    if result["logProblems"]:
        text += "; log: %d problems, first: %s" % (len(result["logProblems"]), result["logProblems"][0])
    if result["wrongExamples"]:
        text += "; e.g. " + "; ".join(result["wrongExamples"][:3])
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--accept-eula", action="store_true",
                        help="agree to the Minecraft EULA, https://aka.ms/MinecraftEULA, which starting a server needs")
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--parallel", type=int, default=2)
    args = parser.parse_args()
    if not args.accept_eula:
        raise SystemExit("Starting a Minecraft server means agreeing to the Minecraft EULA, "
                         "https://aka.ms/MinecraftEULA. Pass --accept-eula if you agree.")

    # Saving is tried in every format, including those the application does not allow yet.
    format_profile.PROFILES = tuple(p._replace(writable=True) for p in format_profile.PROFILES)

    with open(VERSIONS / "releases.json", encoding="utf-8") as f:
        releases = [r for r in json.load(f) if not args.only or r["id"] in args.only]
    releases.sort(key=lambda r: versionKey(r["id"]))
    WORK.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        results = list(pool.map(lambda r: checkRelease(r["id"], r), releases))

    resultsFile = WORK / "results.json"
    old = json.loads(resultsFile.read_text(encoding="utf-8")) if resultsFile.exists() else {}
    old.update({r["release"]: r for r in results})
    with open(resultsFile, "w", encoding="utf-8", newline="\n") as f:
        json.dump(old, f, indent=2, sort_keys=True)
        f.write("\n")
    for r in results:
        print("%-8s %s" % (r["release"], summary(r)))


if __name__ == "__main__":
    sys.exit(main())
