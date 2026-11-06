"""
    modern_edit_tools_test

    What editing needs beyond saving a chunk, for worlds from Minecraft 1.13 and later: turning block
    states, copying between worlds and to files, and the .schem format.
"""
import importlib.util
from pathlib import Path

import numpy
import pytest

from mceditlib import nbt
from mceditlib.anvil.format_profile import MINECRAFT_1_21
from mceditlib.blocktypes import PCBlockTypeSet, blocktypeConverter, id_limit
from mceditlib.blocktypes.modern import ModernBlockTypeSet
from mceditlib.blocktypes.modern_rotation import ModernBlockRotations, rotationsFor, turnState
from mceditlib.blocktypes.pack_library import setPackFolders
from mceditlib.findadapter import findAdapter
from mceditlib.revisionhistory import UndoFolderExists
from mceditlib.schematic import createSchematic
from mceditlib.selection import BoundingBox
from mceditlib.sponge_schematic import SpongeSchematicAdapter, decodeVarints, encodeVarints
from mceditlib.structure import exportStructure
from mceditlib.worldeditor import WorldEditor

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("modern_world_test", HERE / "modern_world_test.py")
worlds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worlds)


@pytest.fixture(scope="module")
def blocktypes():
    return ModernBlockTypeSet(MINECRAFT_1_21)


# --- Turning block states ---

@pytest.mark.parametrize("state, axis, expected", [
    ({"facing": "north"}, "y", {"facing": "west"}),
    ({"facing": "west"}, "y", {"facing": "south"}),
    ({"facing": "up"}, "y", {"facing": "up"}),
    ({"facing": "up"}, "x", {"facing": "south"}),
    ({"axis": "x"}, "y", {"axis": "z"}),
    ({"axis": "y"}, "y", {"axis": "y"}),
    ({"axis": "y"}, "x", {"axis": "z"}),
    ({"north": "true", "east": "false"}, "y", {"west": "true", "north": "false"}),
    ({"east": "low", "west": "tall", "up": "true"}, "y", {"north": "low", "south": "tall", "up": "true"}),
    ({"east": "side", "power": "3"}, "y", {"north": "side", "power": "3"}),
    ({"rotation": "4"}, "y", {"rotation": "0"}),
    ({"rotation": "0"}, "y", {"rotation": "12"}),
    ({"rotation": "4"}, "x", {"rotation": "4"}),
    ({"shape": "north_south"}, "y", {"shape": "east_west"}),
    ({"shape": "ascending_north"}, "y", {"shape": "ascending_west"}),
    ({"shape": "straight", "half": "top"}, "y", {"shape": "straight", "half": "top"}),
    ({"orientation": "north_up"}, "y", {"orientation": "west_up"}),
    ({"waterlogged": "true", "type": "left"}, "y", {"waterlogged": "true", "type": "left"}),
])
def testStatesTurn(state, axis, expected):
    assert turnState(state, axis) == expected


@pytest.mark.parametrize("shape", ["south_east", "south_west", "north_east", "north_west"])
def testCurvedRailShapesAreOnesThatExist(shape):
    turned = turnState({"shape": shape}, "y")["shape"]
    assert turned in ("south_east", "south_west", "north_east", "north_west") and turned != shape


def turned(blocktypes, table, name):
    block = blocktypes[name]
    row = table[block.ID, block.meta]
    return blocktypes[int(row[0]), int(row[1])].nameAndState


@pytest.mark.parametrize("name, expected", [
    ("minecraft:oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]",
     "minecraft:oak_stairs[facing=west,half=bottom,shape=straight,waterlogged=false]"),
    ("minecraft:oak_log[axis=x]", "minecraft:oak_log[axis=z]"),
    ("minecraft:oak_sign[rotation=4,waterlogged=false]", "minecraft:oak_sign[rotation=0,waterlogged=false]"),
    ("minecraft:white_banner[rotation=8]", "minecraft:white_banner[rotation=4]"),
    ("minecraft:rail[shape=north_south,waterlogged=false]", "minecraft:rail[shape=east_west,waterlogged=false]"),
    ("minecraft:cobblestone_wall[east=low,north=none,south=none,up=true,waterlogged=false,west=tall]",
     "minecraft:cobblestone_wall[east=none,north=low,south=tall,up=true,waterlogged=false,west=none]"),
    ("minecraft:redstone_wire[east=side,north=none,power=3,south=none,west=up]",
     "minecraft:redstone_wire[east=none,north=side,power=3,south=up,west=none]"),
    ("minecraft:stone", "minecraft:stone"),
])
def testTableForTurningAroundY(blocktypes, name, expected):
    assert turned(blocktypes, ModernBlockRotations(blocktypes).rotateY90, name) == expected


def testStateWithNowhereToGoStays(blocktypes):
    # Stairs have no facing=up
    name = "minecraft:oak_stairs[facing=south,half=bottom,shape=straight,waterlogged=false]"
    rotations = ModernBlockRotations(blocktypes)
    assert turned(blocktypes, rotations.rotateX90, name) == name


def testHalfTurnIsTwoQuarterTurns(blocktypes):
    name = "minecraft:piston[extended=false,facing=up]"
    rotations = ModernBlockRotations(blocktypes)
    assert turned(blocktypes, rotations.rotateX180, name) == "minecraft:piston[extended=false,facing=down]"


def testAllStatesOfAMultiIDBlockTurn(blocktypes):
    # This block has too many states for one ID.
    table = ModernBlockRotations(blocktypes).rotateY90
    for power in 0, 8, 15:
        name = "minecraft:redstone_wire[east=side,north=none,power=%d,south=none,west=none]" % power
        assert turned(blocktypes, table, name) == "minecraft:redstone_wire[east=none,north=side,power=%d,south=none,west=none]" % power


def testTablesAreOnlyMadeWhenNeededAndKept(blocktypes):
    rotations = rotationsFor(blocktypes)
    assert rotationsFor(blocktypes) is rotations
    assert isinstance(rotations, ModernBlockRotations)
    assert rotations._tables == {} or all(key[0] in "xyz" for key in rotations._tables)
    table = rotations.rotateY90
    assert rotations.rotateY90 is table
    assert table.shape[0] == id_limit


# --- Converting between sets of block types ---

def testSameSetChangesNothing(blocktypes):
    convert = blocktypeConverter(blocktypes, blocktypes)
    IDs, metas = numpy.array([5, 6], "uint16"), numpy.array([1, 2], "uint8")
    assert convert(IDs, metas)[0] is IDs


def testOtherModernSetMatchesByNameAndState(blocktypes):
    other = ModernBlockTypeSet(MINECRAFT_1_21)
    other.idAndMeta("somemod:first")  # so that the IDs aren't the same
    source = ModernBlockTypeSet(MINECRAFT_1_21)
    gadget = source.idAndMeta("somemod:gadget", [("mode", "on")])
    lamp = source["minecraft:redstone_lamp[lit=true]"]

    IDs = numpy.array([[gadget[0], lamp.ID]], "uint16")
    metas = numpy.array([[gadget[1], lamp.meta]], "uint8")
    newIDs, newMetas = blocktypeConverter(other, source)(IDs, metas)
    assert newIDs.shape == (1, 2)
    assert other[int(newIDs[0, 0]), int(newMetas[0, 0])].nameAndState == "somemod:gadget[mode=on]"
    assert other[int(newIDs[0, 1]), int(newMetas[0, 1])].nameAndState == "minecraft:redstone_lamp[lit=true]"


def testOldAndModernDoNotMix(blocktypes):
    with pytest.raises(ValueError):
        blocktypeConverter(blocktypes, PCBlockTypeSet())
    with pytest.raises(ValueError):
        blocktypeConverter(PCBlockTypeSet(), blocktypes)


# --- Copying ---

@pytest.fixture
def world(tmp_path):
    folder = tmp_path / "world"
    folder.mkdir()
    worlds.writeLevelDat(folder)
    chest = worlds.state("minecraft:chest", facing="north", type="single", waterlogged="false")
    stairs = worlds.state("minecraft:oak_stairs", facing="east", half="top", shape="straight", waterlogged="true")
    indices = numpy.zeros(4096, "intp")
    indices[2 * 256 + 3 * 16 + 1] = 1  # chest at x=1, y=2, z=3 of the section
    indices[2 * 256 + 3 * 16 + 2] = 2
    section = worlds.makeSection(-2, [worlds.state("minecraft:stone"), chest, stairs], indices)
    worlds.writeRegion(folder / "region" / "r.0.0.mca", {
        (0, 0): worlds.makeChunk(0, 0, [section], blockEntities=[worlds.blockEntity(1, -30, 3)]),
        (1, 0): worlds.makeChunk(1, 0, [worlds.mixedSection(0)])})
    worlds.writeRegion(folder / "entities" / "r.0.0.mca", {(0, 0): worlds.entityFile([worlds.armorStand()])})
    return folder


@pytest.fixture
def editor(world):
    worldEditor = WorldEditor(str(world))
    yield worldEditor
    worldEditor.close()


CHEST = "minecraft:chest[facing=north,type=single,waterlogged=false]"
STAIRS = "minecraft:oak_stairs[facing=east,half=top,shape=straight,waterlogged=true]"
BOX = BoundingBox((0, -32, 0), (8, 8, 8))  # around the chest, stairs and armor stand


def testExportBelowZero(editor):
    schematic = editor.getDimension().exportSchematic(BOX)
    dim = schematic.getDimension()
    assert dim.getBlock(1, 2, 3).nameAndState == CHEST
    assert dim.getBlock(2, 2, 3).nameAndState == STAIRS
    assert dim.getBlock(0, 0, 0).internalName == "minecraft:stone"
    assert [ref.id for ref in dim.getChunk(0, 0).TileEntities] == ["minecraft:chest"]
    assert [ref.id for ref in dim.getChunk(0, 0).Entities] == ["minecraft:armor_stand"]


def testPasteInTheSameWorldKeepsEveryState(world, editor):
    dim = editor.getDimension()
    schematic = dim.exportSchematic(BOX)
    editor.beginUndo()
    dim.copyBlocks(schematic.getDimension(), schematic.getDimension().bounds, (16, 40, 0), create=True)
    editor.commitUndo()
    editor.saveChanges()

    again = WorldEditor(str(world), readonly=True).getDimension()
    assert again.getBlock(17, 42, 3).nameAndState == CHEST
    assert again.getBlock(18, 42, 3).nameAndState == STAIRS
    tags = again.getChunk(1, 0).TileEntities
    assert [(ref.id, tuple(ref.Position)) for ref in tags] == [("minecraft:chest", (17, 42, 3))]


def testPastedEntityGetsANewUUID(world, editor):
    dim = editor.getDimension()
    schematic = dim.exportSchematic(BOX)
    old = next(iter(dim.getChunk(0, 0).Entities)).UUID
    editor.beginUndo()
    dim.copyBlocks(schematic.getDimension(), schematic.getDimension().bounds, (16, 40, 0), create=True)
    editor.commitUndo()
    pasted = [ref for ref in dim.getChunk(1, 0).Entities]
    assert len(pasted) == 1
    assert pasted[0].UUID != old
    assert "UUID" in pasted[0].rootTag and "UUIDMost" not in pasted[0].rootTag


def testPasteTurned(world, editor):
    dim = editor.getDimension()
    schematic = dim.exportSchematic(BOX).getDimension()
    from mceditlib.transform import DimensionTransform
    bounds = schematic.bounds
    turnedDim = DimensionTransform(schematic, bounds.center, (0, 90, 0))
    editor.beginUndo()
    dim.copyBlocks(turnedDim, turnedDim.bounds, (16, 40, 0), create=True)
    editor.commitUndo()
    names = set()
    for chunk in dim.getChunks([(1, 0)]):
        for cy in chunk.sectionPositions():
            section = chunk.getSection(cy)
            names.update(editor.blocktypes[int(ID), int(meta)].nameAndState
                         for ID, meta in zip(section.Blocks.ravel(), section.Data.ravel()) if ID)
    assert "minecraft:oak_stairs[facing=north,half=top,shape=straight,waterlogged=true]" in names


def testCutBetweenModernWorlds(world, tmp_path):
    otherFolder = tmp_path / "other"
    otherFolder.mkdir()
    worlds.writeLevelDat(otherFolder)
    worlds.writeRegion(otherFolder / "region" / "r.0.0.mca", {(0, 0): worlds.makeChunk(0, 0, [worlds.mixedSection(0)])})

    source = WorldEditor(str(world), readonly=True)
    source.blocktypes.idAndMeta("somemod:first")  # so that the two worlds don't share IDs
    dest = WorldEditor(str(otherFolder))
    dest.beginUndo()
    dest.getDimension().copyBlocks(source.getDimension(), BOX, (0, 0, 0))
    dest.commitUndo()
    assert dest.getDimension().getBlock(1, 2, 3).nameAndState == CHEST
    dest.close()


# --- Structure files ---

def testStructureFileSaysItsVersion(world, editor, tmp_path):
    path = tmp_path / "out.nbt"
    exportStructure(str(path), editor.getDimension(), BoundingBox((0, -30, 3), (3, 1, 1)), "tester")
    tag = nbt.load(str(path))
    assert tag["DataVersion"].value == 3955
    names = {entry["Name"].value for entry in tag["palette"]}
    assert {"minecraft:stone", "minecraft:chest", "minecraft:oak_stairs"} <= names


# --- The .schem format ---

@pytest.mark.parametrize("values", [[0, 1, 127], [128, 255, 300, 0, 16383, 16384, 100000]])
def testVarints(values):
    values = numpy.array(values)
    encoded = encodeVarints(values)
    assert (decodeVarints(encoded, len(values)) == values).all()


def testVarintBytesAreAsTheSpecificationSays():
    assert encodeVarints(numpy.array([300, 1])).tolist() == [0xac, 0x02, 0x01]


@pytest.mark.parametrize("data, count", [([1, 2], 5), ([1, 2, 3], 2)])
def testWrongNumberOfBlocksIsAnError(data, count):
    with pytest.raises(Exception):
        decodeVarints(numpy.array(data + [0x80, 0x01]), count)


@pytest.fixture
def savedSchem(world, editor, tmp_path):
    path = tmp_path / "copy.schem"
    editor.getDimension().exportSchematic(BOX).saveToFile(str(path))
    return path


def testSavedFileFollowsTheSpecification(savedSchem):
    tag = nbt.load(str(savedSchem))["Schematic"]
    assert tag["Version"].value == 3
    assert tag["DataVersion"].value == 3955
    assert (tag["Width"].value, tag["Height"].value, tag["Length"].value) == (8, 8, 8)
    blocks = tag["Blocks"]
    assert CHEST in blocks["Palette"]
    assert blocks["Data"].tagID == nbt.ID_BYTE_ARRAY
    assert len(blocks["Data"].value) == 8 * 8 * 8
    index = blocks["Palette"][CHEST].value
    assert blocks["Data"].value[1 + 3 * 8 + 2 * 8 * 8] == index  # x + z * Width + y * Width * Length

    entity = blocks["BlockEntities"][0]
    assert entity["Id"].value == "minecraft:chest"
    assert entity["Pos"].value.tolist() == [1, 2, 3]
    assert "x" not in entity["Data"] and "id" not in entity["Data"]

    assert tag["Entities"][0]["Id"].value == "minecraft:armor_stand"
    assert [p.value for p in tag["Entities"][0]["Pos"]] == [1.5, 2.0, 2.5]


def testSavedFileIsGzipped(savedSchem):
    assert savedSchem.read_bytes()[:2] == b"\x1f\x8b"


def testSavedFileOpensAgain(savedSchem):
    adapter = findAdapter(str(savedSchem), readonly=True)
    assert isinstance(adapter, SpongeSchematicAdapter)
    dim = WorldEditor(adapter=adapter).getDimension()
    assert dim.getBlock(1, 2, 3).nameAndState == CHEST
    assert dim.getBlock(2, 2, 3).nameAndState == STAIRS
    assert [ref.id for ref in dim.getChunk(0, 0).TileEntities] == ["minecraft:chest"]
    assert tuple(next(iter(dim.getChunk(0, 0).TileEntities)).Position) == (1, 2, 3)
    assert [ref.id for ref in dim.getChunk(0, 0).Entities] == ["minecraft:armor_stand"]


def testWorldEditorOpensAFileByItsName(savedSchem):
    assert WorldEditor(str(savedSchem), readonly=True).getDimension().getBlock(1, 2, 3).nameAndState == CHEST


def testPastingAFileIntoAnotherWorld(savedSchem, tmp_path):
    folder = tmp_path / "other"
    folder.mkdir()
    worlds.writeLevelDat(folder)
    worlds.writeRegion(folder / "region" / "r.0.0.mca", {(0, 0): worlds.makeChunk(0, 0, [worlds.mixedSection(0)])})
    source = WorldEditor(str(savedSchem), readonly=True).getDimension()
    dest = WorldEditor(str(folder))
    dest.beginUndo()
    dest.getDimension().copyBlocks(source, source.bounds, (0, 100, 0), create=True)
    dest.commitUndo()
    dest.saveChanges()
    dest.close()
    assert WorldEditor(str(folder), readonly=True).getDimension().getBlock(1, 102, 3).nameAndState == CHEST


def testManyBlockTypesSurviveAFile(tmp_path, blocktypes):
    # A palette with more than 127 entries has varints longer than a byte.
    schematic = createSchematic((16, 16, 16), blocktypes)
    dim = schematic.getDimension()
    names = [name for name in sorted(blocktypes.IDsByState) if "air" not in name][:400]
    chunk = dim.getChunk(0, 0)
    section = chunk.getSection(0, create=True)
    for i, name in enumerate(names):
        ID, meta = blocktypes.IDsByState[name]
        section.Blocks.ravel()[i] = ID
        section.Data.ravel()[i] = meta
    chunk.dirty = True
    path = tmp_path / "many.schem"
    schematic.saveToFile(str(path))

    assert len(nbt.load(str(path))["Schematic"]["Blocks"]["Palette"]) > 127
    again = WorldEditor(str(path), readonly=True).getDimension().getChunk(0, 0).getSection(0)
    other = WorldEditor(str(path), readonly=True)
    for i, name in enumerate(names[:50]):
        assert other.getDimension().getBlock(i % 16, i // 256, (i // 16) % 16).nameAndState == name
    assert again is not None


def testUnknownBlocksSurviveAFile(tmp_path):
    types = ModernBlockTypeSet(MINECRAFT_1_21)
    ID, meta = types.idAndMeta("somemod:gadget", [("mode", "on")])
    schematic = createSchematic((2, 2, 2), types)
    chunk = schematic.getDimension().getChunk(0, 0)
    section = chunk.getSection(0, create=True)
    section.Blocks[0, 0, 0] = ID
    section.Data[0, 0, 0] = meta
    chunk.dirty = True
    path = tmp_path / "u.schem"
    schematic.saveToFile(str(path))
    assert WorldEditor(str(path), readonly=True).getDimension().getBlock(0, 0, 0).nameAndState == "somemod:gadget[mode=on]"


def writeOlderSchem(path, version):
    tag = nbt.TAG_Compound()
    tag["Version"] = nbt.TAG_Int(version)
    if version >= 2:
        tag["DataVersion"] = nbt.TAG_Int(3955)
    tag["Width"], tag["Height"], tag["Length"] = nbt.TAG_Short(2), nbt.TAG_Short(1), nbt.TAG_Short(1)
    palette = nbt.TAG_Compound()
    palette["minecraft:stone"] = nbt.TAG_Int(0)
    palette["minecraft:redstone_lamp[lit=true]"] = nbt.TAG_Int(1)
    tag["Palette"] = palette
    tag["PaletteMax"] = nbt.TAG_Int(2)
    tag["BlockData"] = nbt.TAG_Byte_Array(numpy.array([0, 1], "uint8"))

    entity = nbt.TAG_Compound()
    entity["Pos"] = nbt.TAG_Int_Array(numpy.array([1, 0, 0], ">u4"))
    entity["Id"] = nbt.TAG_String("minecraft:chest")
    entity["CustomName"] = nbt.TAG_String("Box")
    key = "BlockEntities" if version >= 2 else "TileEntities"
    tag[key] = nbt.TAG_List([entity])

    with open(path, "wb") as f:
        tag.name = "Schematic"
        tag.save(f)


@pytest.mark.parametrize("version", [1, 2])
def testOlderVersionsAreRead(tmp_path, version):
    path = tmp_path / "old.schem"
    writeOlderSchem(path, version)
    dim = WorldEditor(str(path), readonly=True).getDimension()
    assert dim.getBlock(0, 0, 0).internalName == "minecraft:stone"
    assert dim.getBlock(1, 0, 0).nameAndState == "minecraft:redstone_lamp[lit=true]"
    entity = next(iter(dim.getChunk(0, 0).TileEntities))
    assert entity.id == "minecraft:chest"
    assert entity.rootTag["CustomName"].value == "Box"
    assert tuple(entity.Position) == (1, 0, 0)


def testPropertiesInAnyOrderInAFile(tmp_path):
    path = tmp_path / "order.schem"
    writeOlderSchem(path, 2)
    tag = nbt.load(str(path))
    tag["Palette"]["minecraft:oak_stairs[waterlogged=false,shape=straight,half=top,facing=east]"] = nbt.TAG_Int(0)
    del tag["Palette"]["minecraft:stone"]
    with open(path, "wb") as f:
        tag.save(f)
    block = WorldEditor(str(path), readonly=True).getDimension().getBlock(0, 0, 0)
    assert block.nameAndState == "minecraft:oak_stairs[facing=east,half=top,shape=straight,waterlogged=false]"


def testNewerVersionIsRefused(tmp_path):
    path = tmp_path / "new.schem"
    writeOlderSchem(path, 2)
    tag = nbt.load(str(path))
    tag["Version"].value = 99
    with open(path, "wb") as f:
        tag.save(f)
    with pytest.raises(Exception, match="newer version"):
        WorldEditor(str(path), readonly=True)


def testOtherFilesWithThatExtensionAreNotOpened(tmp_path):
    path = tmp_path / "junk.schem"
    path.write_bytes(b"not a schematic")
    assert not SpongeSchematicAdapter.canOpenFile(str(path))


# --- Reopening after a crash ---

def testStaleUndoFolderCanBeDiscardedAfterTheFirstTry(world):
    undo = world.parent / ("##%s.UNDO##" % world.name)
    undo.mkdir()
    with pytest.raises(UndoFolderExists):
        WorldEditor(str(world))
    # The first try must have let go of the world's lock for this one to work.
    editor = WorldEditor(str(world), resume=False)
    editor.close()


# --- Block entities for new blocks ---

def testBlockEntityIsKnownFromTheBlock(blocktypes):
    assert blocktypes.blockEntityIDs["minecraft:chest"] == "minecraft:chest"
    assert blocktypes.blockEntityIDs["minecraft:oak_sign"] == "minecraft:sign"
    assert "minecraft:stone" not in blocktypes.blockEntityIDs


@pytest.fixture(autouse=True)
def noPacks():
    setPackFolders([])
    yield
