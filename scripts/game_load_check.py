#!/usr/bin/env python3
"""Check that Minecraft accepts a world that MCEdit has saved.

Tests can only say that MCEdit reads back what it wrote. This has the game itself read it.

    python scripts/game_load_check.py edit <source world> <folder for the edited copy> <request file>
    (from exporter/mc1211-neoforge) ./gradlew runClient -Pmcedit.checkWorld=<name of the copy>
    python scripts/game_load_check.py compare <report file>

The copy goes in the game's saves folder, and the request file beside it as run/mcedit_check_request.json. Then read
the game's log for errors from ChunkSerializer, and see what it saved with `after`:

    python scripts/game_load_check.py after <the copy>

The source is the test world described in exporter/README.md. Only ever use a copy of a world you can lose.
"""
import json
import logging
import re
import shutil
import sys
from pathlib import Path

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from mceditlib import nbt  # noqa: E402
from mceditlib.anvil.worldfolder import AnvilWorldFolder  # noqa: E402
from mceditlib.selection import BoundingBox  # noqa: E402
from mceditlib.worldeditor import WorldEditor  # noqa: E402

OVERWORLD, NETHER, END = "minecraft:overworld", "minecraft:the_nether", "minecraft:the_end"
GOLD = "minecraft:gold_block"
STAIRS = "minecraft:oak_stairs[facing=east,half=top,shape=straight,waterlogged=true]"
LEFT_ALONE = ("air", "water", "lava", "moving_piston", "fire", "sand", "gravel")  # these change, or fall, by themselves


def put(dimension, position, name):
    x, y, z = position
    block = dimension.blocktypes[name]
    chunk = dimension.getChunk(x >> 4, z >> 4)
    section = chunk.getSection(y >> 4, create=True)
    section.Blocks[y & 15, z & 15, x & 15] = block.ID
    section.Data[y & 15, z & 15, x & 15] = block.meta
    chunk.dirty = True


def edit(source, destination, requestFile):
    destination = Path(destination)
    shutil.rmtree(destination, ignore_errors=True)
    shutil.copytree(source, destination)
    (destination / "session.lock").unlink(missing_ok=True)

    editor = WorldEditor(str(destination))
    editor.requireRevisions()
    world = editor.getDimension()
    blocktypes = editor.blocktypes
    places = []

    def placed(dimension, dimensionKey, position, name):
        put(dimension, position, name)
        places.append((dimensionKey, position))

    editor.beginUndo()
    placed(world, OVERWORLD, (20, 215, 20), GOLD)
    placed(world, OVERWORLD, (21, 215, 20), "minecraft:glowstone")
    placed(world, OVERWORLD, (22, 215, 20), STAIRS)
    placed(world, OVERWORLD, (20, -62, 20), "minecraft:diamond_block")
    placed(world, OVERWORLD, (20, 318, 20), "minecraft:emerald_block")
    placed(world, OVERWORLD, (1, 200, 0), "minecraft:stone")  # there was a sign here, so its block entity must go
    places.append((OVERWORLD, (0, 200, 0)))  # the chest beside it must stay

    world.fillBlocks(BoundingBox((-24, 220, -24), (48, 32, 48)), blocktypes["minecraft:glass"], updateLights=False)
    places.extend([(OVERWORLD, (0, 230, 0)), (OVERWORLD, (-24, 251, -24)), (OVERWORLD, (23, 220, 23))])

    states = [s for s in blocktypes.statesByID.values()
              if s.startswith("minecraft:") and not any(word in s for word in LEFT_ALONE)]
    for i, name in enumerate(states[:600]):
        put(world, (32 + i % 16, 224 + (i // 16) % 16, 32 + i // 256), name)

    chunk = world.getChunk(0, 0)
    stand = next(ref for ref in chunk.Entities if ref.id == "minecraft:armor_stand")
    stand.rootTag["CustomName"] = nbt.TAG_String('"Edited by MCEdit"')
    stand.dirty = True
    copy = stand.rootTag.copy()
    copy["CustomName"] = nbt.TAG_String('"Copied by MCEdit"')
    copy["UUID"] = nbt.TAG_Int_Array(numpy.array([11, 22, 33, 44], ">u4"))
    copy["Pos"] = nbt.TAG_List([nbt.TAG_Double(40.5), nbt.TAG_Double(250.0), nbt.TAG_Double(40.5)])
    target = world.getChunk(2, 2)
    target.chunkData.Entities.append(copy)
    target.dirty = True

    placed(editor.getDimension("DIM-1"), NETHER, (30, 90, 30), GOLD)
    placed(editor.getDimension("DIM1"), END, (30, 90, 30), GOLD)

    world.deleteChunk(-5, -5)
    world.createChunk(40, 40)
    placed(world, OVERWORLD, (40 * 16 + 1, 100, 40 * 16 + 1), GOLD)

    editor.getWorldMetadata().LevelName = "Edited by MCEdit"
    editor.commitUndo()
    editor.saveChanges()
    editor.close()

    checker = WorldEditor(str(destination), readonly=True)
    folders = {OVERWORLD: "", NETHER: "DIM-1", END: "DIM1"}
    request = {
        "blocks": [dict(dimension=key, x=p[0], y=p[1], z=p[2],
                        expected=checker.getDimension(folders[key]).getBlock(*p).nameAndState) for key, p in places],
        "entityChunks": [dict(dimension=OVERWORLD, x=0, z=0), dict(dimension=OVERWORLD, x=2, z=2)],
    }
    Path(requestFile).write_text(json.dumps(request, indent=1), encoding="utf-8")
    print("Edited a copy at %s, with %d places to check." % (destination, len(places)))


def stateParts(state):
    return state.split("[")[0], sorted(state[state.index("[") + 1:-1].split(",")) if "[" in state else []


def compare(reportFile):
    report = json.loads(Path(reportFile).read_text(encoding="utf-8"))
    print("Level name:", report["levelName"])
    different = 0
    for found in report["blocks"]:
        match = re.search(r"\{(.+?)\}(\[.*\])?", found["state"])
        name = (match.group(1) + (match.group(2) or "")) if match else found["state"]
        same = stateParts(name) == stateParts(found["expected"])
        different += not same
        print("%s %5d %4d %5d  %s  sky %d, block %d, surface %d %s" % (
            "same     " if same else "DIFFERENT", found["x"], found["y"], found["z"], name, found["skyLight"],
            found["blockLight"], found["surface"], found.get("blockEntity", "")))
        if not same:
            print("         expected", found["expected"])
    for entity in report["entities"]:
        if entity["name"]:
            print("entity", entity["id"], repr(entity["name"]), entity["x"], entity["y"], entity["z"])
    print("%d different, of %d" % (different, len(report["blocks"])))
    return 1 if different else 0


def after(world):
    folder = AnvilWorldFolder(world, readonly=True)
    for cx, cz, label in (1, 1, "edited"), (40, 40, "created"), (-5, -5, "deleted, then made again by the game"):
        tag = nbt.load(buf=folder.readChunkBytes(cx, cz, ""))
        print("%4d %4d  %-38s isLightOn %s, heightmaps %s" % (
            cx, cz, label, tag["isLightOn"].value if "isLightOn" in tag else None,
            sorted(tag["Heightmaps"].keys()) if "Heightmaps" in tag else None))


def main():
    logging.disable(logging.INFO)
    command, *args = sys.argv[1:]
    if command == "edit":
        edit(*args)
    elif command == "compare":
        return compare(*args)
    elif command == "after":
        after(*args)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
