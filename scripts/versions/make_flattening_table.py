#!/usr/bin/env python3
"""Have Minecraft 1.13.2 upgrade a 1.12.2 world, and record what each numbered block became.

Usage: python scripts/versions/make_flattening_table.py --accept-eula

Run scripts/versions/make_test_worlds.py first, for the 1.12.2 debug world, which has every block state of 1.12.2.
A copy of it is opened by the 1.13.2 server, which turns each chunk it loads into the named blocks of 1.13. The
two worlds are then compared block by block, and each (ID, meta) of 1.12.2 is written with the 1.13 block state it
became to src/mceditlib/blocktypes/flattening.json.

MCEdit uses the table to read chunks that a world from 1.13 or later still holds in the older form, because the
game has not loaded them since the world was last played in 1.12.2 or earlier. The debug world has no block
entities, so blocks whose 1.13 state came from their block entity, such as the color of a bed or what is in a
flower pot, come out as they do with none.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_versions import ROOT, VERSIONS, javaExecutable  # noqa: E402
from make_test_worlds import Server, forceloadCommands  # noqa: E402

sys.path.insert(0, str(ROOT / "src"))
from mceditlib import nbt  # noqa: E402
from mceditlib.pc.regionfile import RegionFile  # noqa: E402
from mceditlib.worldeditor import WorldEditor  # noqa: E402

SOURCE_RELEASE = "1.12.2"
UPGRADE_RELEASE = "1.13.2"
WORK = ROOT / "worlds_local" / "flattening"
TABLE = ROOT / "src" / "mceditlib" / "blocktypes" / "flattening.json"


def upgrade():
    source = VERSIONS / SOURCE_RELEASE / "run" / "debug"
    if not (source / "level.dat").exists():
        raise SystemExit("Make the %s debug world first, with make_test_worlds.py" % SOURCE_RELEASE)
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    shutil.copytree(source, WORK / "upgraded")
    # The game makes the chunks of a debug world afresh instead of loading them, so the copy is made a flat world,
    # whose saved chunks are loaded and upgraded.
    levelPath = WORK / "upgraded" / "level.dat"
    level = nbt.load(str(levelPath))
    level["Data"]["generatorName"] = nbt.TAG_String("default")
    levelPath.write_bytes(level.save())
    # A debug world's chunks are saved as never lit, and the game would make such a chunk again instead of
    # upgrading it.
    for path in (WORK / "upgraded" / "region").glob("r.*.mca"):
        region = RegionFile(str(path))
        for cx, cz in list(region.chunkPositions()):
            chunk = nbt.load(buf=region.readChunkBytes(cx, cz))
            chunk["Level"]["LightPopulated"] = nbt.TAG_Byte(1)
            chunk["Level"]["TerrainPopulated"] = nbt.TAG_Byte(1)
            region.writeChunkBytes(cx, cz, chunk.save(compressed=False))
        del region

    with open(VERSIONS / "releases.json", encoding="utf-8") as f:
        info = next(r for r in json.load(f) if r["id"] == UPGRADE_RELEASE)
    server = Server(UPGRADE_RELEASE, javaExecutable(info["javaToUse"]), VERSIONS / UPGRADE_RELEASE / "server.jar",
                    WORK, "upgraded", "default")
    try:
        if server.waitFor(r"Done \(", 600) is None:
            raise SystemExit("The %s server did not start; see %s" % (UPGRADE_RELEASE, WORK / "upgraded.log"))
        # Load every chunk the old world has, so that each is upgraded and saved.
        for command in forceloadCommands(UPGRADE_RELEASE, -400, -400, 400, 400):
            server.send(command)
            time.sleep(0.3)
        server.saveUntilSettled(WORK / "upgraded", minimum=30, maximum=600)
    finally:
        server.stop()
    return source, WORK / "upgraded"


def compare(oldWorld, newWorld):
    old = WorldEditor(str(oldWorld), readonly=True)
    new = WorldEditor(str(newWorld), readonly=True)
    counts = {}  # "ID:meta" -> {state: blocks}
    try:
        oldDim, newDim = old.getDimension(), new.getDimension()
        for cx, cz in oldDim.chunkPositions():
            oldChunk = oldDim.getChunk(cx, cz)
            try:
                newChunk = newDim.getChunk(cx, cz)
            except Exception as e:
                print("chunk %s was not upgraded: %r" % ((cx, cz), e))
                continue
            for cy in oldChunk.sectionPositions():
                oldSection = oldChunk.getSection(cy)
                newSection = newChunk.getSection(cy)
                if oldSection is None or newSection is None:
                    continue
                pairs = oldSection.Blocks.astype("u4") * 16 + oldSection.Data
                newPairs = newSection.Blocks.astype("u4") * 16 + newSection.Data
                for pair in numpy.unique(pairs):
                    pair = int(pair)
                    if pair == 0:
                        continue
                    found, numbers = numpy.unique(newPairs[pairs == pair], return_counts=True)
                    key = "%d:%d" % (pair >> 4, pair & 15)
                    for newPair, number in zip(found, numbers):
                        state = new.blocktypes.statesByID[int(newPair) >> 4, int(newPair) & 15]
                        stateCounts = counts.setdefault(key, {})
                        stateCounts[state] = stateCounts.get(state, 0) + int(number)
    finally:
        old.close()
        new.close()
    # Blocks whose states in 1.12 depended on their neighbors, such as fences and stairs, share a meta, and 1.13 works
    # their states out from the neighbors again. Each takes the state it became most often.
    table = {key: max(states.items(), key=lambda item: (item[1], item[0]))[0] for key, states in counts.items()}
    conflicts = sum(1 for states in counts.values() if len(states) > 1)
    return table, conflicts


# Blocks that kept part of their state in a block entity, which the debug world doesn't have. What is in a flower
# pot, and the kind of a head, came from the block entity; these are what they are when it says nothing.
WITHOUT_BLOCK_ENTITY = dict(
    [("140:%d" % meta, "minecraft:flower_pot") for meta in range(16)]
    + [("144:0", "minecraft:skeleton_skull[rotation=0]"), ("144:1", "minecraft:skeleton_skull[rotation=0]")]
    + [("144:%d" % meta, "minecraft:skeleton_wall_skull[facing=%s]" % facing)
       for meta, facing in ((2, "north"), (3, "south"), (4, "west"), (5, "east"))]
)


def withAllProperties(state, report):
    """ The game leaves out of some upgraded states the properties that have their default values. """
    name, _, rest = state.partition("[")
    properties = dict(pair.split("=", 1) for pair in rest.rstrip("]").split(",") if pair)
    block = report.get(name)
    if block is None:
        return state
    default = next((s.get("properties", {}) for s in block["states"] if s.get("default")), {})
    merged = dict(default, **properties)
    if not merged:
        return name
    return "%s[%s]" % (name, ",".join("%s=%s" % pair for pair in sorted(merged.items())))


def finish(table):
    with open(VERSIONS / UPGRADE_RELEASE / "reports" / "blocks.json", encoding="utf-8") as f:
        report = json.load(f)
    table = dict(table)
    for key, state in WITHOUT_BLOCK_ENTITY.items():
        if key in table:
            table[key] = state
    return {key: withAllProperties(state, report) for key, state in table.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--accept-eula", action="store_true",
                        help="agree to the Minecraft EULA, https://aka.ms/MinecraftEULA, which starting a server needs")
    args = parser.parse_args()
    if not args.accept_eula:
        raise SystemExit("Starting a Minecraft server means agreeing to the Minecraft EULA, "
                         "https://aka.ms/MinecraftEULA. Pass --accept-eula if you agree.")
    oldWorld, newWorld = upgrade()
    table, conflicts = compare(oldWorld, newWorld)
    table = finish(table)
    ordered = dict(sorted(table.items(), key=lambda item: tuple(int(n) for n in item[0].split(":"))))
    with open(TABLE, "w", encoding="utf-8", newline="\n") as f:
        json.dump({
            "about": "What each block of Minecraft %s, as \"ID:meta\", became when Minecraft %s upgraded it. Made by "
                     "scripts/versions/make_flattening_table.py." % (SOURCE_RELEASE, UPGRADE_RELEASE),
            "blocks": ordered,
        }, f, indent=1)
        f.write("\n")
    print("%d block states, %d of which became more than one state" % (len(ordered), conflicts))


if __name__ == "__main__":
    sys.exit(main())
