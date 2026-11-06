"""
    modern_write_test

    Editing and saving worlds in the format of Minecraft 1.21.1. The world is written by the test or copied from one
    the game made, and what comes back is read again from the files.
"""
import importlib.util
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy
import pytest

from mceditlib import nbt
from mceditlib.anvil.modern import bitsForPalette, packIndices, unpackIndices
from mceditlib.anvil.session_lock import SessionLock, WorldInUse
from mceditlib.anvil.worldfolder import AnvilWorldFolder, ENTITY_STORE, POI_STORE
from mceditlib.exceptions import ChunkNotPresent
from mceditlib.selection import BoundingBox
from mceditlib.worldeditor import WorldEditor

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("modern_world_test", HERE / "modern_world_test.py")
worlds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worlds)

REAL_WORLD = worlds.REAL_WORLD
needsRealWorld = worlds.needsRealWorld


def stone():
    return worlds.state("minecraft:stone")


def lampSection(y, **kwargs):
    return worlds.mixedSection(y, **kwargs)


@pytest.fixture
def world(tmp_path):
    folder = tmp_path / "world"
    folder.mkdir()
    worlds.writeLevelDat(folder)
    attachments = nbt.TAG_Compound()
    attachments["data"] = nbt.TAG_Int(5)
    heights = worlds.heightmapTag(numpy.full(256, 64 + 16))
    chunk = worlds.makeChunk(0, 0, [lampSection(-4, skyLight=0x33, blockLight=0x11), lampSection(0, skyLight=0xff)],
                             blockEntities=[worlds.blockEntity(1, 2, 3)], Heightmaps=heights,
                             isLightOn=nbt.TAG_Byte(1), InhabitedTime=nbt.TAG_Long(77),
                             **{"neoforge:attachments": attachments})
    other = worlds.makeChunk(5, 0, [lampSection(0)])
    worlds.writeRegion(folder / "region" / "r.0.0.mca", {(0, 0): chunk, (5, 0): other})
    worlds.writeRegion(folder / "entities" / "r.0.0.mca", {(0, 0): worlds.entityFile([worlds.armorStand()])})
    worlds.writeRegion(folder / "poi" / "r.0.0.mca", {(0, 0): worlds.simpleChunk(0, 0)})
    return folder


@pytest.fixture
def editor(world):
    worldEditor = WorldEditor(str(world))
    worldEditor.requireRevisions()
    yield worldEditor
    worldEditor.close()


def reopen(world):
    """ Read what is now in the files. """
    return WorldEditor(str(world), readonly=True)


def rawChunk(world, cx=0, cz=0, dimName=""):
    return nbt.load(buf=AnvilWorldFolder(str(world), readonly=True).readChunkBytes(cx, cz, dimName))


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


# --- Packed arrays ---

@pytest.mark.parametrize("bits", [1, 2, 4, 5, 7, 9, 12, 16])
def testPackedIndicesUnpackTheSame(bits):
    values = numpy.random.RandomState(bits).randint(0, 1 << bits, 4096)
    assert (unpackIndices(packIndices(values, bits), bits, 4096) == values).all()


def testPackedIndicesMatchTheGamesLayout():
    # 5 bits: 12 to a long, from the lowest bits up, with the top 4 bits left over.
    longs = packIndices(numpy.array([1, 2, 3] + [0] * 9 + [31]), 5)
    assert longs.dtype == numpy.dtype(">u8")
    assert longs.tolist() == [1 | 2 << 5 | 3 << 10, 31]


def testPackedIndicesCanFillAWholeLong():
    assert packIndices(numpy.full(16, 15), 4).tolist() == [0xffffffffffffffff]


# --- Blocks ---

def testBlockChangeIsSavedAndReadBack(world, editor):
    editAndSave(editor, lambda: setBlock(editor, 5, 6, 7, "minecraft:gold_block"))
    again = reopen(world)
    assert again.getDimension().getBlock(5, 6, 7).internalName == "minecraft:gold_block"
    assert again.getDimension().getBlock(5, 6, 8).internalName == "minecraft:stone"
    assert again.getDimension().getBlock(1, 2, 3).internalName == "minecraft:redstone_lamp"


def testBlockWithStateIsSavedWithItsProperties(world, editor):
    stairs = "minecraft:oak_stairs[facing=east,half=top,shape=outer_left,waterlogged=true]"
    editAndSave(editor, lambda: setBlock(editor, 5, 6, 7, stairs))
    assert reopen(world).getDimension().getBlock(5, 6, 7).nameAndState == stairs

    palette = rawChunk(world)["sections"][1]["block_states"]["palette"]
    entry = [e for e in palette if e["Name"].value == "minecraft:oak_stairs"][0]
    assert entry["Properties"]["facing"].value == "east"
    assert entry["Properties"]["waterlogged"].value == "true"
    assert isinstance(entry["Properties"]["waterlogged"], nbt.TAG_String)
    assert "Properties" not in [e for e in palette if e["Name"].value == "minecraft:stone"][0]


def testBlocksBelowZero(world, editor):
    editAndSave(editor, lambda: setBlock(editor, 5, -60, 7, "minecraft:gold_block"))
    assert reopen(world).getDimension().getBlock(5, -60, 7).internalName == "minecraft:gold_block"
    assert rawChunk(world)["sections"][0]["Y"].value == -4


def testNewSectionIsAddedInOrder(world, editor):
    editAndSave(editor, lambda: setBlock(editor, 5, 40, 7, "minecraft:gold_block"))
    assert [s["Y"].value for s in rawChunk(world)["sections"]] == [-4, 0, 2]
    assert reopen(world).getDimension().getBlock(5, 40, 7).internalName == "minecraft:gold_block"


def testNewSectionTakesItsBiomesFromTheNearest(world, editor):
    editAndSave(editor, lambda: setBlock(editor, 5, 40, 7, "minecraft:gold_block"))
    assert rawChunk(world)["sections"][2]["biomes"]["palette"][0].value == "minecraft:plains"


def testAllOneBlockHasNoData(world, editor):
    def fill():
        section = editor.getDimension().getChunk(0, 0).getSection(0)
        section.Blocks[:] = editor.blocktypes["minecraft:gold_block"].ID
        section.Data[:] = 0
        editor.getDimension().getChunk(0, 0).dirty = True

    editAndSave(editor, fill)
    states = rawChunk(world)["sections"][1]["block_states"]
    assert len(states["palette"]) == 1
    assert "data" not in states


def testManyBlockTypesUseMoreBits(world, editor):
    wires = ["minecraft:redstone_wire[east=none,north=none,power=%d,south=none,west=none]" % p for p in range(16)]
    wires += ["minecraft:oak_planks", "minecraft:gold_block", "minecraft:dirt", "minecraft:sand"]

    def fill():
        for i, name in enumerate(wires):
            setBlock(editor, i % 16, 16 + i // 16, 0, name)
        section = editor.getDimension().getChunk(0, 0).getSection(1)
        assert section is not None

    editAndSave(editor, fill)
    again = reopen(world).getDimension()
    for i, name in enumerate(wires):
        assert again.getBlock(i % 16, 16 + i // 16, 0).nameAndState == name

    states = rawChunk(world)["sections"][2]["block_states"]
    bits = max(4, bitsForPalette(len(states["palette"])))
    assert len(states["data"].value) == (4096 + 64 // bits - 1) // (64 // bits)


def testUnknownBlocksKeepTheirNamesWhenASectionIsSaved(world, editor, tmp_path):
    section = worlds.makeSection(3, [worlds.state("somemod:gadget", mode="on"), stone()], numpy.arange(4096) % 2)
    worlds.writeRegion(tmp_path / "other" / "region" / "r.0.0.mca", {(0, 0): worlds.makeChunk(0, 0, [section])})
    other = tmp_path / "other"
    worlds.writeLevelDat(other)
    otherEditor = WorldEditor(str(other))
    editAndSave(otherEditor, lambda: setBlock(otherEditor, 5, 48, 7, "minecraft:gold_block"))
    otherEditor.close()

    palette = rawChunk(other)["sections"][0]["block_states"]["palette"]
    entry = [e for e in palette if e["Name"].value == "somemod:gadget"][0]
    assert entry["Properties"]["mode"].value == "on"
    assert reopen(other).getDimension().getBlock(0, 48, 0).nameAndState == "somemod:gadget[mode=on]"


def testKnownBlockWithUnknownStateKeepsThatState(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    worlds.writeLevelDat(other)
    section = worlds.makeSection(3, [worlds.state("minecraft:redstone_lamp", lit="maybe"), stone()], numpy.arange(4096) % 2)
    worlds.writeRegion(other / "region" / "r.0.0.mca", {(0, 0): worlds.makeChunk(0, 0, [section])})
    otherEditor = WorldEditor(str(other))
    editAndSave(otherEditor, lambda: setBlock(otherEditor, 5, 48, 7, "minecraft:gold_block"))
    otherEditor.close()
    assert reopen(other).getDimension().getBlock(0, 48, 0).nameAndState == "minecraft:redstone_lamp[lit=maybe]"


def testMetaThatMeansNothingBecomesTheBlocksFirstState(world, editor):
    def edit():
        chunk = editor.getDimension().getChunk(0, 0)
        section = chunk.getSection(0)
        section.Blocks[0, 0, 0] = editor.blocktypes["minecraft:gold_block"].ID
        section.Data[0, 0, 0] = 9
        chunk.dirty = True

    editAndSave(editor, edit)
    assert reopen(world).getDimension().getBlock(0, 0, 0).internalName == "minecraft:gold_block"


def testChunkWithNoChangesEncodesTheSame(world, editor):
    raw = AnvilWorldFolder(str(world), readonly=True).readChunkBytes(0, 0, "")
    chunk = editor.adapter.readChunk(0, 0, "")
    chunk.getSection(0)
    assert chunk.buildNBTTag().save(compressed=False) == nbt.load(buf=raw).save(compressed=False)


def testUntouchedSectionsStayAsTheyWere(world, editor):
    before = rawChunk(world)["sections"][0]["block_states"]
    editAndSave(editor, lambda: setBlock(editor, 5, 6, 7, "minecraft:gold_block"))
    after = rawChunk(world)["sections"][0]["block_states"]
    assert (before["data"].value == after["data"].value).all()
    assert [e["Name"].value for e in before["palette"]] == [e["Name"].value for e in after["palette"]]


def testChunksNotEditedStayExactlyAsTheyWere(world, editor):
    folder = AnvilWorldFolder(str(world), readonly=True)
    before = folder.getRegionFile(0, 0, "").readChunkCompressed(5, 0)
    editAndSave(editor, lambda: setBlock(editor, 5, 6, 7, "minecraft:gold_block"))
    after = AnvilWorldFolder(str(world), readonly=True).getRegionFile(0, 0, "").readChunkCompressed(5, 0)
    assert bytes(before[0]) == bytes(after[0])


def testReadingAChunkDoesNotMakeItSave(world, editor):
    before = (world / "region" / "r.0.0.mca").read_bytes()
    editor.beginUndo()
    chunk = editor.getDimension().getChunk(0, 0)
    chunk.getSection(0)
    list(chunk.Entities)
    list(chunk.TileEntities)
    chunk.HeightMap
    editor.commitUndo()
    editor.saveChanges()
    assert (world / "region" / "r.0.0.mca").read_bytes() == before


def testSectionEditedBackIsNotChangedForLight(world, editor):
    # All that matters for keeping light data is whether blocks changed in the end.
    def edit():
        chunk = editor.getDimension().getChunk(0, 0)
        section = chunk.getSection(0)
        old = section.Blocks[5, 5, 5]
        section.Blocks[5, 5, 5] = 1
        section.Blocks[5, 5, 5] = old
        chunk.dirty = True

    editAndSave(editor, edit)
    assert rawChunk(world)["isLightOn"].value == 1
    assert "Heightmaps" in rawChunk(world)


# --- What goes with blocks ---

def testChangedChunkHasLightAndHeightsLeftToTheGame(world, editor):
    editAndSave(editor, lambda: setBlock(editor, 5, 6, 7, "minecraft:gold_block"))
    tag = rawChunk(world)
    assert tag["isLightOn"].value == 0
    assert "Heightmaps" not in tag
    assert all("SkyLight" not in s and "BlockLight" not in s for s in tag["sections"])


def testRootTagsAreKept(world, editor):
    editAndSave(editor, lambda: setBlock(editor, 5, 6, 7, "minecraft:gold_block"))
    tag = rawChunk(world)
    assert tag["neoforge:attachments"]["data"].value == 5
    assert tag["InhabitedTime"].value == 77
    assert tag["DataVersion"].value == worlds.DATA_VERSION
    assert tag["Status"].value == "minecraft:full"
    assert tag["yPos"].value == -4
    assert tag["sections"][0]["biomes"]["palette"][0].value == "minecraft:plains"


def testHeightMapFollowsEditsInMemory(world, editor):
    editor.beginUndo()
    setBlock(editor, 5, 200, 7, "minecraft:gold_block")
    assert editor.getDimension().getChunk(0, 0).HeightMap[7, 5] == 201
    editor.commitUndo()


def testHeightMapIsWorkedOutWhenTheChunkHasNone(world, editor):
    editAndSave(editor, lambda: setBlock(editor, 5, 200, 7, "minecraft:gold_block"))
    heights = reopen(world).getDimension().getChunk(0, 0).HeightMap
    assert heights[7, 5] == 201
    assert heights[0, 0] == 16  # the top of section 0, the highest filled one here


def testBlockEntityGoesWhenItsBlockDoes(world, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    worlds.writeLevelDat(other)
    chest = worlds.state("minecraft:chest", facing="north", type="single", waterlogged="false")
    indices = numpy.zeros(4096, "intp")
    indices[0] = 1
    indices[1] = 1
    section = worlds.makeSection(0, [stone(), chest], indices)
    worlds.writeRegion(other / "region" / "r.0.0.mca", {(0, 0): worlds.makeChunk(
        0, 0, [section], blockEntities=[worlds.blockEntity(0, 0, 0), worlds.blockEntity(1, 0, 0)])})

    otherEditor = WorldEditor(str(other))
    editAndSave(otherEditor, lambda: setBlock(otherEditor, 0, 0, 0, "minecraft:gold_block"))
    otherEditor.close()
    remaining = rawChunk(other)["block_entities"]
    assert [(e["x"].value, e["y"].value) for e in remaining] == [(1, 0)]


def testBlockEntityStaysWhenOnlyAnotherSectionChanges(world, editor):
    editAndSave(editor, lambda: setBlock(editor, 5, -60, 7, "minecraft:gold_block"))
    assert len(rawChunk(world)["block_entities"]) == 1


def testBlockEntityOfAnUnknownBlockIsKept(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    worlds.writeLevelDat(other)
    indices = numpy.zeros(4096, "intp")
    section = worlds.makeSection(0, [worlds.state("somemod:machine"), stone()], indices)
    worlds.writeRegion(other / "region" / "r.0.0.mca", {(0, 0): worlds.makeChunk(0, 0, [section], blockEntities=[worlds.blockEntity(0, 0, 0)])})
    otherEditor = WorldEditor(str(other))
    editAndSave(otherEditor, lambda: setBlock(otherEditor, 9, 9, 9, "minecraft:gold_block"))
    otherEditor.close()
    assert len(rawChunk(other)["block_entities"]) == 1


# --- Entities ---

def testEntityChangeGoesToTheEntityFile(world, editor):
    def edit():
        chunk = editor.getDimension().getChunk(0, 0)
        ref = next(iter(chunk.Entities))
        ref.rootTag["CustomName"] = nbt.TAG_String("Moved")
        chunk.dirty = True

    editAndSave(editor, edit)
    root = rawChunk(world, dimName=ENTITY_STORE)
    assert root["Entities"][0]["CustomName"].value == "Moved"
    assert (root["Position"].value == [0, 0]).all()
    assert "Entities" not in rawChunk(world)
    assert next(iter(reopen(world).getDimension().getChunk(0, 0).Entities)).rootTag["CustomName"].value == "Moved"


def testChunkEditLeavesItsEntityFileAlone(world, editor):
    before = (world / "entities" / "r.0.0.mca").read_bytes()
    editAndSave(editor, lambda: setBlock(editor, 5, 6, 7, "minecraft:gold_block"))
    assert (world / "entities" / "r.0.0.mca").read_bytes() == before


def testEntityInAChunkThatHadNoneMakesAnEntry(world, editor):
    stand = worlds.armorStand()

    def edit():
        chunk = editor.getDimension().getChunk(5, 0)
        chunk.chunkData.Entities.append(stand)
        chunk.dirty = True

    editAndSave(editor, edit)
    root = rawChunk(world, 5, 0, ENTITY_STORE)
    assert root["DataVersion"].value == worlds.DATA_VERSION
    assert (root["Position"].value == [5, 0]).all()
    assert len(root["Entities"]) == 1


def testEntityPositionInNegativeChunks(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    worlds.writeLevelDat(other)
    worlds.writeRegion(other / "region" / "r.-1.-1.mca", {(-3, -2): worlds.makeChunk(-3, -2, [worlds.mixedSection(0)])})
    otherEditor = WorldEditor(str(other))

    def edit():
        chunk = otherEditor.getDimension().getChunk(-3, -2)
        chunk.chunkData.Entities.append(worlds.armorStand())
        chunk.dirty = True

    editAndSave(otherEditor, edit)
    otherEditor.close()
    position = rawChunk(other, -3, -2, ENTITY_STORE)["Position"]
    assert position.value.astype("i4").tolist() == [-3, -2]
    assert position.tagID == nbt.ID_INT_ARRAY


def testRemovingAllEntitiesSaves(world, editor):
    def edit():
        chunk = editor.getDimension().getChunk(0, 0)
        chunk.removeEntities(list(chunk.Entities))

    editAndSave(editor, edit)
    assert len(rawChunk(world, dimName=ENTITY_STORE)["Entities"]) == 0
    assert list(reopen(world).getDimension().getChunk(0, 0).Entities) == []


def testDamagedEntityFileIsNotOverwritten(world, tmp_path):
    worlds.writeRegion(world / "entities" / "r.0.0.mca", {(0, 0): (2, b"garbage")})
    before = (world / "entities" / "r.0.0.mca").read_bytes()
    editor = WorldEditor(str(world))
    editAndSave(editor, lambda: setBlock(editor, 5, 6, 7, "minecraft:gold_block"))
    editor.close()
    assert (world / "entities" / "r.0.0.mca").read_bytes() == before


def testEntitiesOfAnUngeneratedChunkStayInTheChunk(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    worlds.writeLevelDat(other)
    chunk = worlds.makeChunk(0, 0, [worlds.mixedSection(0)], entities=nbt.TAG_List([worlds.armorStand()]))
    worlds.writeRegion(other / "region" / "r.0.0.mca", {(0, 0): chunk})
    otherEditor = WorldEditor(str(other))
    assert len(list(otherEditor.getDimension().getChunk(0, 0).Entities)) == 1
    editAndSave(otherEditor, lambda: setBlock(otherEditor, 5, 6, 7, "minecraft:gold_block"))
    otherEditor.close()
    assert len(rawChunk(other)["entities"]) == 1
    assert not AnvilWorldFolder(str(other), readonly=True).containsChunk(0, 0, ENTITY_STORE)


def testUUIDStaysAnIntArray(world, editor):
    import uuid

    def edit():
        chunk = editor.getDimension().getChunk(0, 0)
        ref = next(iter(chunk.Entities))
        ref.UUID = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
        chunk.dirty = True

    editAndSave(editor, edit)
    tag = rawChunk(world, dimName=ENTITY_STORE)["Entities"][0]
    assert "UUIDMost" not in tag
    assert tag["UUID"].value.tolist() == [0x00112233, 0x44556677, 0x8899aabb, 0xccddeeff]


# --- Creating and deleting chunks ---

def testDeletingAChunkDeletesAllOfIt(world, editor):
    editAndSave(editor, lambda: editor.getDimension().deleteChunk(0, 0))
    folder = AnvilWorldFolder(str(world), readonly=True)
    assert not folder.containsChunk(0, 0, "")
    assert not folder.containsChunk(0, 0, ENTITY_STORE)
    assert not folder.containsChunk(0, 0, POI_STORE)
    assert folder.containsChunk(5, 0, "")
    assert set(reopen(world).getDimension().chunkPositions()) == {(5, 0)}


def testNewChunkIsMadeWithWhatTheGameNeeds(world, editor):
    def edit():
        editor.getDimension().createChunk(9, 9)
        setBlock(editor, 9 * 16, -30, 9 * 16, "minecraft:gold_block")

    editAndSave(editor, edit)
    tag = rawChunk(world, 9, 9)
    assert tag["DataVersion"].value == worlds.DATA_VERSION
    assert tag["xPos"].value == 9 and tag["zPos"].value == 9 and tag["yPos"].value == -4
    assert tag["Status"].value == "minecraft:full"
    assert reopen(world).getDimension().getBlock(9 * 16, -30, 9 * 16).internalName == "minecraft:gold_block"


def testNewChunkCannotReplaceAnother(world, editor):
    editor.beginUndo()
    with pytest.raises(ValueError):
        editor.adapter.createChunk(0, 0, "")
    editor.commitUndo()


def testCannotAddASectionOutsideTheWorld(world, editor):
    editor.beginUndo()
    chunk = editor.getDimension().getChunk(0, 0)
    assert chunk.getSection(20, create=True) is None
    assert chunk.getSection(-5, create=True) is None
    editor.commitUndo()


# --- Undo ---

def testUndoAndRedoBeforeSaving(world, editor):
    dim = editor.getDimension()
    editor.beginUndo()
    setBlock(editor, 5, 6, 7, "minecraft:gold_block")
    editor.commitUndo()
    assert dim.getBlock(5, 6, 7).internalName == "minecraft:gold_block"
    editor.undo()
    assert dim.getBlock(5, 6, 7).internalName == "minecraft:stone"
    editor.redo()
    assert dim.getBlock(5, 6, 7).internalName == "minecraft:gold_block"


def testNothingReachesTheWorldBeforeSaving(world, editor):
    before = worlds.snapshot(world / "region")
    editor.beginUndo()
    setBlock(editor, 5, 6, 7, "minecraft:gold_block")
    editor.commitUndo()
    assert worlds.snapshot(world / "region") == before


def testUndoAfterSavingKeepsWorking(world, editor):
    dim = editor.getDimension()
    for name in "minecraft:gold_block", "minecraft:diamond_block":
        editor.beginUndo()
        setBlock(editor, 5, 6, 7, name)
        editor.commitUndo()
    editor.saveChanges()
    assert reopen(world).getDimension().getBlock(5, 6, 7).internalName == "minecraft:diamond_block"

    editor.undo()
    assert dim.getBlock(5, 6, 7).internalName == "minecraft:gold_block"
    editor.undo()
    assert dim.getBlock(5, 6, 7).internalName == "minecraft:stone"

    # Saving here puts the world back as it was
    editor.saveChanges()
    assert reopen(world).getDimension().getBlock(5, 6, 7).internalName == "minecraft:stone"


def testUndoBringsBackEntitiesAndDeletedChunks(world, editor):
    dim = editor.getDimension()
    editor.beginUndo()
    dim.deleteChunk(0, 0)
    editor.commitUndo()
    assert not dim.containsChunk(0, 0)
    editor.saveChanges()
    assert not (world / "entities" / "r.0.0.mca").exists()

    editor.undo()
    assert dim.containsChunk(0, 0)
    assert [r.id for r in dim.getChunk(0, 0).Entities] == ["minecraft:armor_stand"]
    editor.saveChanges()
    assert [r.id for r in reopen(world).getDimension().getChunk(0, 0).Entities] == ["minecraft:armor_stand"]


def testUndoOfAnEntityChangeReachesTheChunk(world, editor):
    dim = editor.getDimension()
    editor.beginUndo()
    chunk = dim.getChunk(0, 0)
    chunk.removeEntities(list(chunk.Entities))
    editor.commitUndo()
    changes = editor.adapter.getRevisionChanges(0, 1)
    assert (0, 0) in changes.chunks[""]
    assert not [d for d in changes.chunks if "@" in d]
    editor.undo()
    assert [r.id for r in dim.getChunk(0, 0).Entities] == ["minecraft:armor_stand"]


def testRoundsOfSavingAreConsistent(world, editor):
    for i in range(4):
        name = ("minecraft:gold_block", "minecraft:diamond_block", "minecraft:dirt", "minecraft:sand")[i]
        editAndSave(editor, lambda n=name, x=i: setBlock(editor, x, 6, 7, n))
    again = reopen(world).getDimension()
    assert [again.getBlock(i, 6, 7).internalName for i in range(4)] == [
        "minecraft:gold_block", "minecraft:diamond_block", "minecraft:dirt", "minecraft:sand"]


def testUndoFolderGoesWhenTheWorldCloses(world):
    editor = WorldEditor(str(world))
    undoFolder = world.parent / ("##%s.UNDO##" % world.name)
    assert undoFolder.exists()
    editor.close()
    assert not undoFolder.exists()


# --- Other things to save ---

def testLevelDatIsOnlyWrittenIfChanged(world, editor):
    before = (world / "level.dat").read_bytes()
    editAndSave(editor, lambda: setBlock(editor, 5, 6, 7, "minecraft:gold_block"))
    assert (world / "level.dat").read_bytes() == before


def testLevelDatChangeIsSaved(world, editor):
    def edit():
        editor.getWorldMetadata().LevelName = "Renamed"

    editAndSave(editor, edit)
    assert nbt.load(str(world / "level.dat"))["Data"]["LevelName"].value == "Renamed"


# --- The session lock ---

def holdLockInAnotherProcess(world):
    code = textwrap.dedent("""
        import sys
        sys.path.insert(0, %r)
        from mceditlib.anvil.session_lock import SessionLock
        lock = SessionLock(sys.argv[1])
        print("locked", flush=True)
        sys.stdin.readline()
    """ % str(Path(__file__).resolve().parents[2] / "src"))
    process = subprocess.Popen([sys.executable, "-c", code, str(world)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert process.stdout.readline().strip() == "locked"
    return process


def testWorldInUseIsRefusedBeforeAnythingIsWritten(world):
    process = holdLockInAnotherProcess(world)
    try:
        before = worlds.snapshot(world / "region")
        with pytest.raises(WorldInUse):
            WorldEditor(str(world))
        assert worlds.snapshot(world / "region") == before
        assert not (world.parent / ("##%s.UNDO##" % world.name)).exists()
    finally:
        process.communicate("\n")


def testWorldInUseCanStillBeOpenedReadOnly(world):
    process = holdLockInAnotherProcess(world)
    try:
        assert reopen(world).getDimension().getBlock(1, 2, 3).internalName == "minecraft:redstone_lamp"
    finally:
        process.communicate("\n")


def testLockIsHeldWhileOpenAndReleasedOnClose(world):
    editor = WorldEditor(str(world))
    with pytest.raises(WorldInUse):
        SessionLock(str(world))
    editor.close()
    SessionLock(str(world)).release()


def testNewLockFileIsLikeTheGames(world):
    assert not (world / "session.lock").exists()
    SessionLock(str(world)).release()
    assert (world / "session.lock").read_bytes() == "☃".encode("utf-8")


def testExistingLockFileIsLeftAlone(world):
    (world / "session.lock").write_bytes(b"from the game")
    SessionLock(str(world)).release()
    assert (world / "session.lock").read_bytes() == b"from the game"


def testReadOnlyTakesNoLock(world):
    reopen(world)
    assert not (world / "session.lock").exists()


# --- A world from the game ---

@pytest.fixture
def realCopy(tmp_path):
    folder = tmp_path / "world"
    shutil.copytree(REAL_WORLD, folder)
    return folder


@needsRealWorld
def testEditingAWorldFromTheGame(realCopy):
    editor = WorldEditor(str(realCopy))
    regionBefore = (realCopy / "region" / "r.-1.-1.mca").read_bytes()
    netherBefore = (realCopy / "DIM-1" / "region" / "r.0.0.mca").read_bytes()

    def edit():
        dim = editor.getDimension()
        setBlock(editor, 3, 200, 3, "minecraft:gold_block")
        setBlock(editor, 3, -60, 3, "minecraft:gold_block")
        setBlock(editor, 3, 319, 3, "minecraft:gold_block")
        assert dim.getBlock(0, 200, 0).internalName == "minecraft:chest"

    editAndSave(editor, edit)
    editor.close()

    again = reopen(realCopy).getDimension()
    for y in 200, -60, 319:
        assert again.getBlock(3, y, 3).internalName == "minecraft:gold_block"
    assert again.getBlock(0, 200, 0).internalName == "minecraft:chest"
    assert [ref.id for ref in again.getChunk(0, 0).TileEntities][0] == "minecraft:chest"
    assert again.getChunk(0, 0).TileEntities[0].rootTag["Items"][0]["count"].value == 5

    assert (realCopy / "region" / "r.-1.-1.mca").read_bytes() == regionBefore
    assert (realCopy / "DIM-1" / "region" / "r.0.0.mca").read_bytes() == netherBefore


@needsRealWorld
def testEveryChunkFromTheGameEncodesTheSameWithNoChanges(realCopy):
    # This is what says that nothing in a chunk is lost by reading it and saving it, whatever it holds.
    editor = reopen(realCopy)
    adapter = editor.adapter
    count = 0
    for dimName in list(editor.listDimensions()):
        for cx, cz in sorted(adapter.selectedRevision.chunkPositions(dimName)):
            raw = adapter.selectedRevision.readChunkBytes(cx, cz, dimName)
            chunk = adapter.readChunk(cx, cz, dimName)
            for cy in chunk.sectionPositions():
                chunk.getSection(cy)
            assert chunk.buildNBTTag().save(compressed=False) == nbt.load(buf=raw).save(compressed=False), (dimName, cx, cz)
            count += 1
    assert count > 400


@needsRealWorld
def testFillWorksOnAWorldFromTheGame(realCopy):
    editor = WorldEditor(str(realCopy))
    dim = editor.getDimension()
    box = BoundingBox((-20, -64, -20), (40, 384, 40))

    def edit():
        dim.fillBlocks(box, editor.blocktypes["minecraft:stone"], blocksToReplace=[editor.blocktypes["minecraft:air"]],
                       updateLights=False)

    editAndSave(editor, edit)
    editor.close()
    again = reopen(realCopy).getDimension()
    assert again.getBlock(5, 300, 5).internalName == "minecraft:stone"
    assert again.getBlock(0, 200, 0).internalName == "minecraft:chest"


def testMissingChunkIsStillMissing(world, editor):
    with pytest.raises(ChunkNotPresent):
        editor.getDimension().getChunk(20, 20)
