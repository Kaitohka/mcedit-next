"""
    version_write_test

    Saving changes to worlds and chunks from the versions of Minecraft other than 1.21.1: each chunk is written back
    in the form it was read in, keeping its data version. Whether the game then loads them is checked by
    scripts/versions/check_saving.py, against each release's own server.
"""
import importlib.util
import time
from pathlib import Path

import numpy
import pytest

from mceditlib import nbt
from mceditlib.anvil import format_profile
from mceditlib.anvil.adapter import AnvilChunkFormatError, SessionLockLost
from mceditlib.anvil.modern import packSpanningIndices, unpackIndices
from mceditlib.anvil.worldfolder import AnvilWorldFolder
from mceditlib.worldeditor import WorldEditor

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("modern_world_test", HERE / "modern_world_test.py")
worlds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worlds)


@pytest.fixture(autouse=True)
def everyFormatWritable(monkeypatch):
    """ These tests save in formats that are not yet open to saving in the application. """
    monkeypatch.setattr(format_profile, "PROFILES", tuple(p._replace(writable=True) for p in format_profile.PROFILES))


def makeWorld(tmp_path, dataVersion, chunks):
    world = tmp_path / "world"
    world.mkdir()
    worlds.writeLevelDat(world, dataVersion)
    worlds.writeRegion(world / "region" / "r.0.0.mca", {(0, 0): chunk for chunk in chunks})
    return world


def setBlock(editor, x, y, z, name):
    block = editor.blocktypes[name]
    chunk = editor.getDimension().getChunk(x >> 4, z >> 4)
    section = chunk.getSection(y >> 4, create=True)
    section.Blocks[y & 15, z & 15, x & 15] = block.ID
    section.Data[y & 15, z & 15, x & 15] = block.meta
    chunk.dirty = True


def editAndSave(editor, *edits):
    editor.beginUndo()
    for edit in edits:
        edit()
    editor.commitUndo()
    editor.saveChanges()


def blockAt(world, x, y, z):
    editor = WorldEditor(str(world), readonly=True)
    try:
        return editor.getDimension().getBlock(x, y, z).nameAndState
    finally:
        editor.close()


def rawChunk(world, cx=0, cz=0):
    return nbt.load(buf=AnvilWorldFolder(str(world), readonly=True).readChunkBytes(cx, cz, ""))


# --- Packing as before 1.16 ---

@pytest.mark.parametrize("bits", [4, 5, 7, 9, 13])
def testSpanningPackUnpacksTheSame(bits):
    values = numpy.random.RandomState(bits).randint(0, 1 << bits, 4096)
    longs = packSpanningIndices(values, bits)
    assert len(longs) == 4096 * bits // 64
    assert (unpackIndices(longs, bits, 4096, spanning=True) == values).all()


def testSpanningPackMatchesTheGamesLayout():
    longs = packSpanningIndices(numpy.array([1] * 12 + [31, 7]), 5)
    # The 13th entry starts at bit 60 and runs on into the next long.
    assert int(longs[0]) >> 60 == 31 & 0xf
    assert int(longs[1]) & 1 == 1
    assert (int(longs[1]) >> 1) & 31 == 7


# --- Chunks from 1.13 to 1.17 ---

STAIRS = "minecraft:oak_stairs[facing=east,half=bottom,shape=straight,waterlogged=false]"


@pytest.mark.parametrize("dataVersion", [1631, 1976, 2230, 2586, 2730])
def testLegacyChunkIsSavedInItsOwnForm(tmp_path, dataVersion):
    indices = numpy.random.RandomState(1).randint(0, len(worlds.LEGACY_PALETTE), 4096)
    world = makeWorld(tmp_path, dataVersion, [worlds.legacyChunk(0, 0, dataVersion, worlds.LEGACY_PALETTE, indices)])
    before = blockAt(world, 5, 5, 5)
    editor = WorldEditor(str(world))
    editAndSave(editor, lambda: setBlock(editor, 1, 2, 3, STAIRS), lambda: setBlock(editor, 1, 40, 3, "minecraft:stone"))
    editor.close()

    raw = rawChunk(world)
    assert raw["DataVersion"].value == dataVersion
    assert "Level" in raw and "sections" not in raw
    section = next(s for s in raw["Level"]["Sections"] if s["Y"].value == 0)
    bits = max(4, (len(section["Palette"]) - 1).bit_length())
    longs = section["BlockStates"].value
    if dataVersion < 2529:
        assert len(longs) == 4096 * bits // 64
    else:
        assert len(longs) == (4096 + 64 // bits - 1) // (64 // bits)
    assert blockAt(world, 1, 2, 3) == STAIRS
    assert blockAt(world, 1, 40, 3) == "minecraft:stone"
    assert blockAt(world, 5, 5, 5) == before


@pytest.mark.parametrize("dataVersion", [1631, 2586])
def testUnchangedSectionsOfALegacyChunkStayAsTheyWere(tmp_path, dataVersion):
    chunk = worlds.legacyChunk(0, 0, dataVersion, worlds.LEGACY_PALETTE, numpy.zeros(4096, "intp"))
    other = chunk["Level"]["Sections"][0].copy()
    other["Y"] = nbt.TAG_Byte(1)
    chunk["Level"]["Sections"].append(other)
    world = makeWorld(tmp_path, dataVersion, [chunk])
    before = rawChunk(world)["Level"]["Sections"][1].save(compressed=False)
    editor = WorldEditor(str(world))
    editAndSave(editor, lambda: setBlock(editor, 1, 2, 3, "minecraft:stone"))
    editor.close()
    sections = {s["Y"].value: s for s in rawChunk(world)["Level"]["Sections"]}
    assert sorted(sections[0].keys()) == sorted(sections[1].keys())
    assert "Palette" in sections[1] and "BlockStates" in sections[1] and "palette" not in sections[1]
    assert sections[1].save(compressed=False) == before


def testLightIsLeftForTheGameFrom1_14(tmp_path):
    world = makeWorld(tmp_path, 1976, [worlds.legacyChunk(0, 0, 1976, worlds.LEGACY_PALETTE, numpy.zeros(4096, "intp"))])
    editor = WorldEditor(str(world))
    editAndSave(editor, lambda: setBlock(editor, 1, 2, 3, "minecraft:stone"))
    editor.close()
    level = rawChunk(world)["Level"]
    assert level["isLightOn"].value == 0
    assert all("SkyLight" not in s and "BlockLight" not in s for s in level["Sections"])


def testLightIsKeptIn1_13(tmp_path):
    chunk = worlds.legacyChunk(0, 0, 1631, worlds.LEGACY_PALETTE, numpy.zeros(4096, "intp"))
    chunk["Level"]["Sections"][0]["SkyLight"] = nbt.TAG_Byte_Array(numpy.full(2048, 0xff, "uint8"))
    world = makeWorld(tmp_path, 1631, [chunk])
    editor = WorldEditor(str(world))
    editAndSave(editor, lambda: setBlock(editor, 1, 2, 3, "minecraft:stone"),
                lambda: setBlock(editor, 1, 100, 3, "minecraft:stone"))
    editor.close()
    level = rawChunk(world)["Level"]
    assert "isLightOn" not in level
    assert all("SkyLight" in s for s in level["Sections"])


def testEntitiesStayInTheChunkBefore1_17(tmp_path):
    zombie = nbt.TAG_Compound()
    zombie["id"] = nbt.TAG_String("minecraft:zombie")
    world = makeWorld(tmp_path, 2586, [worlds.legacyChunk(0, 0, 2586, worlds.LEGACY_PALETTE, numpy.zeros(4096, "intp"),
                                                          entities=[zombie])])
    editor = WorldEditor(str(world))
    editAndSave(editor, lambda: setBlock(editor, 1, 2, 3, "minecraft:stone"))
    editor.close()
    assert [e["id"].value for e in rawChunk(world)["Level"]["Entities"]] == ["minecraft:zombie"]
    assert not (world / "entities").exists()


@pytest.mark.parametrize("dataVersion, status", [(1631, "postprocessed"), (2586, "full")])
def testNewChunkInAnOlderWorld(tmp_path, dataVersion, status):
    world = makeWorld(tmp_path, dataVersion, [worlds.legacyChunk(0, 0, dataVersion, worlds.LEGACY_PALETTE,
                                                                 numpy.zeros(4096, "intp"))])
    editor = WorldEditor(str(world))
    editor.beginUndo()
    editor.getDimension().createChunk(3, 0)
    setBlock(editor, 3 * 16 + 1, 64, 1, "minecraft:stone")
    editor.commitUndo()
    editor.saveChanges()
    editor.close()
    raw = rawChunk(world, 3, 0)
    assert raw["DataVersion"].value == dataVersion
    assert raw["Level"]["Status"].value == status
    assert len(raw["Level"]["Biomes"].value) == (256 if dataVersion < 2203 else 1024)
    if dataVersion < 1901:
        # 1.13 needs the light of every section of the overworld.
        assert all(len(s["SkyLight"].value) == 2048 for s in raw["Level"]["Sections"])
    assert blockAt(world, 3 * 16 + 1, 64, 1) == "minecraft:stone"


def numberedWorld(tmp_path, blocks=1, data=0, entities=()):
    chunk = worlds.numberedChunk(0, 0, numpy.full(4096, blocks, "uint8"), numpy.full(4096, data, "uint8"))
    for entity in entities:
        chunk["Level"]["TileEntities"].append(entity)
    return makeWorld(tmp_path, 3955, [chunk]), chunk


def numberedSection(world, cy=0):
    section = next(s for s in rawChunk(world)["Level"]["Sections"] if s["Y"].value == cy)
    numbers = section["Blocks"].value.astype("u4")
    if "Add" in section:
        add = section["Add"].value
        numbers |= numpy.stack([add & 15, add >> 4], -1).ravel().astype("u4") << 8
    data = section["Data"].value
    return numbers, numpy.stack([data & 15, data >> 4], -1).ravel()


def index(x, y, z):
    return (y & 15) * 256 + z * 16 + x


def testNumberedChunkIsSavedByNumber(tmp_path):
    world, _ = numberedWorld(tmp_path)
    editor = WorldEditor(str(world))
    stairs = "minecraft:oak_stairs[facing=west,half=top,shape=straight,waterlogged=false]"
    editAndSave(editor, lambda: setBlock(editor, 1, 2, 3, "minecraft:dirt"),
                lambda: setBlock(editor, 2, 2, 3, "minecraft:magenta_wool"),
                lambda: setBlock(editor, 3, 2, 3, stairs),
                lambda: setBlock(editor, 4, 20, 3, "minecraft:gold_block"))
    editor.close()
    raw = rawChunk(world)
    # Still a chunk of 1.12.2, which the game upgrades when it loads it.
    assert raw["DataVersion"].value == 1343 and "Palette" not in raw["Level"]["Sections"][0]
    numbers, data = numberedSection(world)
    assert (numbers[index(1, 2, 3)], data[index(1, 2, 3)]) == (3, 0)
    assert (numbers[index(2, 2, 3)], data[index(2, 2, 3)]) == (35, 2)
    assert (numbers[index(3, 2, 3)], data[index(3, 2, 3)]) == (53, 5)
    assert (numbers[index(0, 0, 0)], data[index(0, 0, 0)]) == (1, 0)
    numbers, data = numberedSection(world, 1)
    assert numbers[index(4, 20, 3)] == 41 and numbers[index(0, 16, 0)] == 0
    assert len(next(s for s in raw["Level"]["Sections"] if s["Y"].value == 1)["SkyLight"].value) == 2048
    assert blockAt(world, 3, 2, 3) == stairs
    assert blockAt(world, 2, 2, 3) == "minecraft:magenta_wool"


def testUnchangedNumberedBlocksKeepTheirNumbers(tmp_path):
    # Block 300 with data value 5, as a mod might have had, which MCEdit reads without its data value.
    world, chunk = numberedWorld(tmp_path, blocks=300 & 0xff, data=5)
    chunk["Level"]["Sections"][0]["Add"] = nbt.TAG_Byte_Array(numpy.full(2048, 0x11, "uint8"))
    worlds.writeRegion(world / "region" / "r.0.0.mca", {(0, 0): chunk})
    editor = WorldEditor(str(world))
    editAndSave(editor, lambda: setBlock(editor, 1, 2, 3, "minecraft:stone"))
    editor.close()
    numbers, data = numberedSection(world)
    assert (numbers[index(1, 2, 3)], data[index(1, 2, 3)]) == (1, 0)
    assert (numbers[index(0, 0, 0)], data[index(0, 0, 0)]) == (300, 5)
    assert (numbers == 300).sum() == 4095


def testNumberedBlocksWithBlockEntitiesGetThem(tmp_path):
    world, _ = numberedWorld(tmp_path)
    editor = WorldEditor(str(world))
    blocks = {
        (1, 2, 1): "minecraft:white_bed[facing=north,occupied=false,part=foot]",
        (2, 2, 1): "minecraft:blue_banner[rotation=4]",
        (3, 2, 1): "minecraft:potted_poppy",
        (4, 2, 1): "minecraft:zombie_head[powered=false,rotation=3]",
        (5, 2, 1): "minecraft:creeper_wall_head[facing=east,powered=false]",
        (6, 2, 1): "minecraft:note_block[instrument=harp,note=7,powered=false]",
    }
    editAndSave(editor, *[lambda p=p, n=n: setBlock(editor, *p, n) for p, n in blocks.items()])
    editor.close()
    entities = {(e["x"].value, e["y"].value, e["z"].value): e for e in rawChunk(world)["Level"]["TileEntities"]}
    assert entities[1, 2, 1]["id"].value == "minecraft:bed" and entities[1, 2, 1]["color"].value == 0
    assert entities[2, 2, 1]["Base"].value == 4
    assert (entities[3, 2, 1]["Item"].value, entities[3, 2, 1]["Data"].value) == ("minecraft:red_flower", 0)
    assert (entities[4, 2, 1]["SkullType"].value, entities[4, 2, 1]["Rot"].value) == (2, 3)
    assert entities[5, 2, 1]["SkullType"].value == 4
    assert entities[6, 2, 1]["note"].value == 7
    for position, name in blocks.items():
        found = blockAt(world, *position)
        assert found.partition("[")[0] == name.partition("[")[0], (position, found)


def testBlocksNewerThanNumberedChunksAreNotSaved(tmp_path):
    world, _ = numberedWorld(tmp_path)
    editor = WorldEditor(str(world))
    editor.beginUndo()
    setBlock(editor, 1, 2, 3, "minecraft:deepslate")
    with pytest.raises(AnvilChunkFormatError, match="minecraft:deepslate"):
        editor.commitUndo()
        editor.saveChanges()
    editor.close()


def testNewerBlockEntitiesAreNotSavedInNumberedChunks(tmp_path):
    chest = nbt.TAG_Compound()
    chest["id"] = nbt.TAG_String("minecraft:chest")
    for key, value in zip("xyz", (5, 2, 5)):
        chest[key] = nbt.TAG_Int(value)
    item = nbt.TAG_Compound()
    item["id"] = nbt.TAG_String("minecraft:diamond")
    item["count"] = nbt.TAG_Int(1)
    chest["Items"] = nbt.TAG_List([item])
    world, _ = numberedWorld(tmp_path, entities=[chest])
    editor = WorldEditor(str(world))
    editor.beginUndo()
    setBlock(editor, 5, 2, 5, "minecraft:chest[facing=north,type=single,waterlogged=false]")
    with pytest.raises(AnvilChunkFormatError, match="minecraft:chest"):
        editor.commitUndo()
        editor.saveChanges()
    editor.close()


# --- Palette keys follow the chunk, not the world ---

def testChunkFrom1_21In26_3WorldKeepsItsKeys(tmp_path):
    chunk = worlds.makeChunk(0, 0, [worlds.mixedSection(0)], dataVersion=3955)
    world = makeWorld(tmp_path, 5023, [chunk])
    editor = WorldEditor(str(world))
    editAndSave(editor, lambda: setBlock(editor, 1, 2, 3, STAIRS))
    editor.close()
    palette = next(s for s in rawChunk(world)["sections"] if s["Y"].value == 0)["block_states"]["palette"]
    assert all("Name" in entry and "id" not in entry for entry in palette)


def testChunkFrom26_3UsesLowercaseKeys(tmp_path):
    chunk = worlds.makeChunk(0, 0, [worlds.mixedSection(0)], dataVersion=5023)
    world = makeWorld(tmp_path, 5023, [chunk])
    editor = WorldEditor(str(world))
    editAndSave(editor, lambda: setBlock(editor, 1, 2, 3, STAIRS))
    editor.close()
    palette = next(s for s in rawChunk(world)["sections"] if s["Y"].value == 0)["block_states"]["palette"]
    assert all("id" in entry and "Name" not in entry for entry in palette)
    assert blockAt(world, 1, 2, 3) == STAIRS


# --- The lock of worlds from before 1.15 ---

def testTimestampLockBefore1_15(tmp_path):
    world = makeWorld(tmp_path, 1976, [worlds.legacyChunk(0, 0, 1976, worlds.LEGACY_PALETTE, numpy.zeros(4096, "intp"))])
    editor = WorldEditor(str(world))
    try:
        editor.adapter.checkSessionLock()
        # The game, opening the world, writes its own time there.
        lock = editor.adapter.sessionLock
        lock._file.seek(0)
        lock._file.write((int(time.time() * 1000) + 5000).to_bytes(8, "big"))
        with pytest.raises(SessionLockLost):
            editor.adapter.checkSessionLock()
    finally:
        editor.close()


def testCheckedFormatsAreTheOnlyOnesWritable(monkeypatch):
    monkeypatch.undo()
    for profile in format_profile.PROFILES:
        assert profile.writable == all(format_profile.releaseForDataVersion(v) in format_profile.WRITABLE_RELEASES
                                       for v in profile.dataVersions)


# --- The metas of 1.12 and earlier ---

def testStatesAreSavedWithTheGamesMetas():
    from mceditlib.blocktypes import PCBlockTypeSet
    types = PCBlockTypeSet()
    # From 1.9 the game reads a chest with meta 0 as air, and saves one facing north with meta 2.
    assert types["minecraft:chest[facing=north]"].meta == 2
    assert types["minecraft:portal[axis=x]"].meta == 1
    assert types.statesByID[54, 0] == "minecraft:chest[facing=north]"
    assert types.metaAliases[54, 0] == 2
