"""
    modern_world_test

    Most of these tests write small worlds in the format of Minecraft 1.21.1. The rest read a world made
    by the game itself, and are skipped if it is not there. To make it, see the README in exporter/.
"""
import gzip
import hashlib
import os
import shutil
import struct
import zlib
from pathlib import Path

import numpy
import pytest

import lz4.block

from mceditlib import nbt
from mceditlib.anvil.adapter import AnvilChunkFormatError, AnvilWorldAdapter
from mceditlib.anvil.format_profile import MINECRAFT_1_21, profileForDataVersion
from mceditlib.anvil.modern import ModernAnvilAdapter, bitsForPalette, unpackIndices
from mceditlib.anvil.worldfolder import AnvilWorldFolder
from mceditlib.blocktypes import id_limit
from mceditlib.blocktypes.modern import ModernBlockTypeSet, STATES_PER_ID, joinNameAndProperties
from mceditlib.exceptions import LevelFormatError, RegionFormatError
from mceditlib.findadapter import findAdapter
from mceditlib.pc.regionfile import RegionFile
from mceditlib.worldeditor import WorldEditor

REAL_WORLD = Path(__file__).resolve().parents[2] / "worlds_local" / "mcedit_test_121"
DATA_VERSION = 3955


# --- Writing test worlds ---

def packIndices(values, bits):
    entriesPerLong = 64 // bits
    longs = []
    for start in range(0, len(values), entriesPerLong):
        value = 0
        for i, index in enumerate(values[start:start + entriesPerLong]):
            value |= int(index) << (i * bits)
        longs.append(value)
    return numpy.array(longs, dtype=">u8")


def state(name, **properties):
    tag = nbt.TAG_Compound()
    tag["Name"] = nbt.TAG_String(name)
    if properties:
        tag["Properties"] = nbt.TAG_Compound()
        for key, value in properties.items():
            tag["Properties"][key] = nbt.TAG_String(value)
    return tag


def makeSection(y, palette, indices=None, skyLight=None, blockLight=None, biomes=("minecraft:plains",),
                biomeIndices=None):
    tag = nbt.TAG_Compound()
    tag["Y"] = nbt.TAG_Byte(y)
    states = nbt.TAG_Compound()
    states["palette"] = nbt.TAG_List(list(palette))
    if indices is not None:
        bits = max(4, bitsForPalette(len(palette)))
        states["data"] = nbt.TAG_Long_Array(packIndices(indices, bits))
    tag["block_states"] = states

    biomeTag = nbt.TAG_Compound()
    biomeTag["palette"] = nbt.TAG_List([nbt.TAG_String(b) for b in biomes])
    if biomeIndices is not None:
        biomeTag["data"] = nbt.TAG_Long_Array(packIndices(biomeIndices, bitsForPalette(len(biomes))))
    tag["biomes"] = biomeTag

    if skyLight is not None:
        tag["SkyLight"] = nbt.TAG_Byte_Array(numpy.full(2048, skyLight, "uint8"))
    if blockLight is not None:
        tag["BlockLight"] = nbt.TAG_Byte_Array(numpy.full(2048, blockLight, "uint8"))
    return tag


def makeChunk(cx, cz, sections, yPos=-4, dataVersion=DATA_VERSION, blockEntities=(), **extra):
    tag = nbt.TAG_Compound()
    tag["DataVersion"] = nbt.TAG_Int(dataVersion)
    tag["xPos"] = nbt.TAG_Int(cx)
    tag["zPos"] = nbt.TAG_Int(cz)
    tag["yPos"] = nbt.TAG_Int(yPos)
    tag["Status"] = nbt.TAG_String("minecraft:full")
    tag["sections"] = nbt.TAG_List(list(sections))
    tag["block_entities"] = nbt.TAG_List(list(blockEntities))
    for key, value in extra.items():
        tag[key] = value
    return tag


def lz4Stream(data):
    compressed = lz4.block.compress(data, store_size=False)
    token = bytes([0x20 | 6])
    return (b"LZ4Block" + token + struct.pack("<III", len(compressed), len(data), 0) + compressed +
            b"LZ4Block" + bytes([0x10 | 6]) + struct.pack("<III", 0, 0, 0))


def compressAs(fmt, data):
    if fmt == 1:
        return gzip.compress(data)
    if fmt == 2:
        return zlib.compress(data)
    if fmt == 3:
        return data
    if fmt == 4:
        return lz4Stream(data)
    if fmt == 127:
        name = b"example:scheme"
        return struct.pack(">H", len(name)) + name + data
    raise ValueError(fmt)


def writeRegion(path, chunks, fmt=2):
    """ Write a region file. `chunks` maps (cx, cz) to NBT tags, or to raw (format, bytes) entries. """
    path.parent.mkdir(parents=True, exist_ok=True)
    offsets = [0] * 1024
    body = b""
    for (cx, cz), chunk in chunks.items():
        if isinstance(chunk, tuple):
            chunkFormat, payload = chunk
        else:
            chunkFormat, payload = fmt, compressAs(fmt, chunk.save(compressed=False))
        entry = struct.pack(">IB", len(payload) + 1, chunkFormat) + payload
        sectors = (len(entry) + 4095) // 4096
        offsets[(cx & 31) + (cz & 31) * 32] = (2 + len(body) // 4096) << 8 | sectors
        body += entry + bytes(sectors * 4096 - len(entry))
    path.write_bytes(struct.pack(">1024I", *offsets) + bytes(4096) + body)


def writeLevelDat(world, dataVersion=DATA_VERSION, dimensions=None):
    root = nbt.TAG_Compound()
    data = root["Data"] = nbt.TAG_Compound()
    data["DataVersion"] = nbt.TAG_Int(dataVersion)
    data["version"] = nbt.TAG_Int(19133)
    data["LevelName"] = nbt.TAG_String("Test World")
    settings = data["WorldGenSettings"] = nbt.TAG_Compound()
    settings["seed"] = nbt.TAG_Long(1234)
    types = settings["dimensions"] = nbt.TAG_Compound()
    for key, typeName in (dimensions or {"minecraft:overworld": "minecraft:overworld"}).items():
        types[key] = nbt.TAG_Compound()
        types[key]["type"] = nbt.TAG_String(typeName)
    (world / "level.dat").write_bytes(root.save())


@pytest.fixture
def world(tmp_path):
    folder = tmp_path / "world"
    folder.mkdir()
    writeLevelDat(folder)
    return folder


def snapshot(folder):
    found = {}
    for root, _, files in os.walk(folder):
        found[root] = None
        for name in files:
            path = os.path.join(root, name)
            with open(path, "rb") as f:
                found[path] = hashlib.sha256(f.read()).hexdigest(), os.path.getmtime(path)
    return found


# --- Packed arrays ---

@pytest.mark.parametrize("bits", [1, 4, 5, 7, 9, 13, 16])
def testUnpackIndicesMatchesPackedValues(bits):
    values = numpy.random.RandomState(bits).randint(0, 1 << bits, 4096)
    longs = packIndices(values, bits)
    assert (unpackIndices(longs, bits, 4096) == values).all()


def testEntriesDoNotCrossLongs():
    # 7 entries of 9 bits fit in a long with one bit left, as in a heightmap.
    longs = numpy.array([0b1_000000111_000000110_000000101_000000100_000000011_000000010_000000001, 8], dtype=">u8")
    assert unpackIndices(longs, 9, 8).tolist() == [1, 2, 3, 4, 5, 6, 7, 8]


def testValuesWithTheTopBitSetUnpack():
    longs = numpy.array([0xffffffffffffffff], dtype=">u8")
    assert unpackIndices(longs, 4, 16).tolist() == [15] * 16


def testTooFewLongsIsAnError():
    with pytest.raises(AnvilChunkFormatError):
        unpackIndices(numpy.zeros(3, ">u8"), 4, 4096)


@pytest.mark.parametrize("size, bits", [(1, 0), (2, 1), (3, 2), (4, 2), (5, 3), (16, 4), (17, 5), (1024, 10)])
def testBitsForPalette(size, bits):
    assert bitsForPalette(size) == bits


# --- Format profiles ---

@pytest.mark.parametrize("dataVersion, expected", [(3953, MINECRAFT_1_21), (3955, MINECRAFT_1_21), (3952, None),
                                                   (3956, None), (4080, None), (1343, None)])
def testProfiles(dataVersion, expected):
    assert profileForDataVersion(dataVersion) == expected


@pytest.mark.parametrize("dimName, key", [("", "minecraft:overworld"), ("DIM-1", "minecraft:the_nether"),
                                          ("DIM1", "minecraft:the_end"), ("dimensions/ae2/spatial", "ae2:spatial"),
                                          ("dimensions/a/b/c", "a:b/c")])
def testDimensionNames(dimName, key):
    assert MINECRAFT_1_21.dimensionKey(dimName) == key
    assert MINECRAFT_1_21.dimensionFolder(key) == dimName


# --- Virtual IDs ---

@pytest.fixture(scope="module")
def blocktypes():
    return ModernBlockTypeSet(MINECRAFT_1_21)


def testPropertiesAreSortedWhateverOrderTheyAreSavedIn(blocktypes):
    chest = "minecraft:chest"
    pairs = [("waterlogged", "false"), ("type", "single"), ("facing", "north")]
    assert joinNameAndProperties(chest, pairs) == "minecraft:chest[facing=north,type=single,waterlogged=false]"
    assert blocktypes.idAndMeta(chest, pairs) == blocktypes.idAndMeta(chest, list(reversed(pairs)))


def testEveryPackStateHasItsOwnPair(blocktypes):
    pairs = list(blocktypes.statesByID)
    assert len(pairs) == len(set(pairs))
    assert len(blocktypes.statesByID) == len(blocktypes.IDsByState)
    assert all(blocktypes.IDsByState[name] == pair for pair, name in blocktypes.statesByID.items())
    assert all(0 <= meta < STATES_PER_ID and 0 <= ID < id_limit for ID, meta in pairs)


def testAirFamilyIsIDZero(blocktypes):
    assert blocktypes.idAndMeta("minecraft:air") == (0, 0)
    assert blocktypes.idAndMeta("minecraft:cave_air")[0] == 0
    assert blocktypes.idAndMeta("minecraft:void_air")[0] == 0
    assert blocktypes[0, 1].internalName == "minecraft:cave_air"
    assert not blocktypes["minecraft:air"]


def testOneIDHasOneValueOfEachPerIDProperty(blocktypes):
    lit = blocktypes["minecraft:redstone_lamp[lit=true]"]
    unlit = blocktypes["minecraft:redstone_lamp[lit=false]"]
    assert lit.ID != unlit.ID
    assert blocktypes.brightness[lit.ID] == 15
    assert blocktypes.brightness[unlit.ID] == 0
    assert blocktypes.opacity[blocktypes["minecraft:stone"].ID] == 15


def testIDAloneMeansTheDefaultState(blocktypes):
    ID = blocktypes["minecraft:redstone_lamp"].ID
    assert blocktypes[ID].blockState == "[lit=false]"


@pytest.mark.parametrize("name", ["minecraft:water", "minecraft:lava"])
def testFluidMetaIsItsLevel(blocktypes, name):
    IDs = set()
    for level in range(16):
        ID, meta = blocktypes.idAndMeta(name, [("level", str(level))])
        assert meta == level
        IDs.add(ID)
    assert len(IDs) == 1


def testBlockWithManyStatesUsesSeveralIDs(blocktypes):
    IDs = {blocktypes.idAndMeta("minecraft:note_block", [
        ("instrument", "harp"), ("note", str(note)), ("powered", powered)])[0]
        for note in range(25) for powered in ("true", "false")}
    assert len(IDs) >= 3


def testVanillaLeavesPlentyOfRoom(blocktypes):
    assert max(ID for ID, _ in blocktypes.statesByID) < 4096


def testBlockAttributesReachBlockType(blocktypes):
    stone = blocktypes["minecraft:stone"]
    assert stone.displayName == "Stone"
    assert stone.internalName == "minecraft:stone"
    assert stone.blockState == ""
    assert blocktypes.mapColor[stone.ID, stone.meta].tolist() == [0x70, 0x70, 0x70]


def testUnknownStatesAreKeptAndFillAnIDAtATime():
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21)
    pairs = [blocktypes.idAndMeta("somemod:gadget", [("n", str(n))]) for n in range(STATES_PER_ID + 1)]
    assert len(set(pairs)) == STATES_PER_ID + 1
    assert pairs[0][0] == pairs[STATES_PER_ID - 1][0] != pairs[STATES_PER_ID][0]

    gadget = blocktypes[pairs[3]]
    assert gadget.internalName == "somemod:gadget"
    assert gadget.blockState == "[n=3]"
    assert gadget.unknown
    assert blocktypes.idAndMeta("somemod:gadget", [("n", "3")]) == pairs[3]


def testPaletteTables(blocktypes):
    palette = nbt.TAG_List([state("minecraft:air"), state("minecraft:redstone_lamp", lit="true")])
    IDs, metas = blocktypes.paletteTables(palette)
    lamp = blocktypes["minecraft:redstone_lamp[lit=true]"]
    assert IDs.tolist() == [0, lamp.ID]
    assert metas.tolist() == [0, lamp.meta]


# --- Region files ---

def simpleChunk(cx, cz):
    return makeChunk(cx, cz, [makeSection(0, [state("minecraft:stone")])])


@pytest.mark.parametrize("fmt", [1, 2, 3, 4])
def testRegionCompressionTypes(tmp_path, fmt):
    path = tmp_path / "r.0.0.mca"
    writeRegion(path, {(1, 2): simpleChunk(1, 2)}, fmt)
    tag = nbt.load(buf=RegionFile(str(path), readonly=True).readChunkBytes(1, 2))
    assert tag["xPos"].value == 1 and tag["zPos"].value == 2


def testCustomCompressionNamesItsScheme(tmp_path):
    path = tmp_path / "r.0.0.mca"
    writeRegion(path, {(0, 0): simpleChunk(0, 0)}, 127)
    with pytest.raises(RegionFormatError, match="example:scheme"):
        RegionFile(str(path), readonly=True).readChunkBytes(0, 0)


def testUnknownCompressionIsAnError(tmp_path):
    path = tmp_path / "r.0.0.mca"
    writeRegion(path, {(0, 0): (9, b"xx")})
    with pytest.raises(RegionFormatError):
        RegionFile(str(path), readonly=True).readChunkBytes(0, 0)


@pytest.mark.parametrize("rx, rz, cx, cz, name", [(0, 0, 3, 4, "c.3.4.mcc"), (-1, 2, 3, 4, "c.-29.68.mcc")])
def testExternalChunks(tmp_path, rx, rz, cx, cz, name):
    path = tmp_path / ("r.%d.%d.mca" % (rx, rz))
    writeRegion(path, {(cx, cz): (2 | 0x80, b"")})
    (tmp_path / name).write_bytes(zlib.compress(simpleChunk(7, 8).save(compressed=False)))
    tag = nbt.load(buf=RegionFile(str(path), readonly=True).readChunkBytes(cx, cz))
    assert tag["xPos"].value == 7


def testMissingExternalChunkIsAnError(tmp_path):
    path = tmp_path / "r.0.0.mca"
    writeRegion(path, {(0, 0): (2 | 0x80, b"")})
    with pytest.raises(RegionFormatError, match="c.0.0.mcc"):
        RegionFile(str(path), readonly=True).readChunkBytes(0, 0)


def damagedRegion(path):
    """ Chunks 0,0 and 1,0 say they overlap, which makes a region file need repair. """
    writeRegion(path, {(0, 0): simpleChunk(0, 0), (1, 0): simpleChunk(1, 0)})
    data = bytearray(path.read_bytes())
    data[4:8] = data[0:4]
    path.write_bytes(bytes(data))


def testReadOnlyRegionIsNotRepaired(tmp_path):
    path = tmp_path / "r.0.0.mca"
    damagedRegion(path)
    before = path.read_bytes()
    region = RegionFile(str(path), readonly=True)
    assert region.chunkCount == 2
    assert path.read_bytes() == before


def testRepairKeepsModernChunks(tmp_path):
    # The first version of repair looked for a Level tag, and deleted any chunk without one.
    path = tmp_path / "r.0.0.mca"
    writeRegion(path, {(0, 0): simpleChunk(0, 0), (1, 0): simpleChunk(1, 0), (5, 0): simpleChunk(5, 0)})
    data = bytearray(path.read_bytes())
    data[4:8] = data[0:4]  # 1,0 now points at the sectors of 0,0, where it finds a chunk for another slot
    path.write_bytes(bytes(data))
    region = RegionFile(str(path))
    assert region.containsChunk(0, 0)
    assert region.containsChunk(5, 0)


@pytest.mark.parametrize("size", [0, 100, 5000])
def testTruncatedReadOnlyRegionIsEmpty(tmp_path, size):
    path = tmp_path / "r.0.0.mca"
    path.write_bytes(bytes(size))
    region = RegionFile(str(path), readonly=True)
    assert region.chunkCount == 0
    assert not region.containsChunk(0, 0)
    assert path.stat().st_size == size


def testReadOnlyRegionRefusesWrites(tmp_path):
    path = tmp_path / "r.0.0.mca"
    writeRegion(path, {(0, 0): simpleChunk(0, 0)})
    region = RegionFile(str(path), readonly=True)
    with pytest.raises(IOError):
        region.writeChunkBytes(0, 0, b"data")
    with pytest.raises(IOError):
        region.deleteChunk(0, 0)


# --- World folders ---

def testReadOnlyFolderCreatesNothing(world):
    before = snapshot(world)
    folder = AnvilWorldFolder(str(world), readonly=True)
    assert list(folder.chunkPositions("")) == []
    assert list(folder.listFolder("playerdata")) == []
    assert folder.getRegionFile(0, 0, "") is None
    assert not folder.containsChunk(0, 0, "")
    assert snapshot(world) == before


def testReadOnlyFolderKeepsEmptyRegionFiles(world):
    (world / "region").mkdir()
    empty = world / "region" / "r.0.0.mca"
    empty.write_bytes(bytes(8192))
    folder = AnvilWorldFolder(str(world), readonly=True)
    assert list(folder.chunkPositions("")) == []
    assert empty.exists()


def testReadOnlyFolderRefusesWrites(world):
    folder = AnvilWorldFolder(str(world), readonly=True)
    for call in (lambda: folder.writeFile("x", b""), lambda: folder.deleteFile("level.dat"),
                 lambda: folder.writeChunkBytes(0, 0, "", b""), lambda: folder.deleteChunk(0, 0, "")):
        with pytest.raises(IOError):
            call()


def testFolderFindsDimensionsAtAnyDepth(world):
    for dimension in "DIM-1", "DIM1", "dimensions/ae2/spatial", "dimensions/mod/deep/er":
        (world / dimension / "region").mkdir(parents=True)
    folder = AnvilWorldFolder(str(world), readonly=True)
    assert set(folder.listDimensions()) == {"", "DIM-1", "DIM1", "dimensions/ae2/spatial", "dimensions/mod/deep/er"}


def testMccFilesAreNotRegions(world):
    (world / "region").mkdir()
    (world / "region" / "c.1.2.mcc").write_bytes(b"x")
    writeRegion(world / "region" / "r.0.0.mca", {(0, 0): simpleChunk(0, 0)})
    assert list(AnvilWorldFolder(str(world), readonly=True).chunkPositions("")) == [(0, 0)]


# --- Choosing an adapter ---

def testModernWorldGoesToTheModernAdapter(world):
    assert isinstance(findAdapter(str(world), readonly=True), ModernAnvilAdapter)
    assert isinstance(findAdapter(str(world / "level.dat"), readonly=True), ModernAnvilAdapter)


@pytest.mark.parametrize("dataVersion", [1451, 3700, 3956, 4671])
def testUnsupportedVersionsAreRefusedWithoutTouchingTheWorld(tmp_path, dataVersion):
    world = tmp_path / "world"
    world.mkdir()
    writeLevelDat(world, dataVersion)
    before = snapshot(world)
    with pytest.raises(LevelFormatError, match=str(dataVersion)):
        findAdapter(str(world))
    with pytest.raises(LevelFormatError):
        findAdapter(str(world), readonly=True)
    assert snapshot(world) == before


def testDamagedLevelDatFallsBackToBackup(world):
    shutil.copy(world / "level.dat", world / "level.dat_old")
    (world / "level.dat").write_bytes(b"not a level")
    assert isinstance(findAdapter(str(world), readonly=True), ModernAnvilAdapter)


def testUnsupportedVersionIsFoundInBackupToo(tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    writeLevelDat(world, 4671)
    shutil.copy(world / "level.dat", world / "level.dat_old")
    (world / "level.dat").write_bytes(b"not a level")
    with pytest.raises(LevelFormatError):
        findAdapter(str(world), readonly=True)


def testOldWorldsStillGoToTheOldAdapter(tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    root = nbt.TAG_Compound()
    root["Data"] = nbt.TAG_Compound()
    root["Data"]["version"] = nbt.TAG_Int(19133)
    root["Data"]["LevelName"] = nbt.TAG_String("Old")
    (world / "level.dat").write_bytes(root.save())
    adapter = findAdapter(str(world), readonly=True)
    assert type(adapter) is AnvilWorldAdapter


def testWorldInfo(world):
    info = findAdapter(str(world), getInfo=True)
    assert "Test World" in info.displayName


# --- Reading chunks ---

def mixedSection(y, **kwargs):
    """ Stone everywhere, but with a lit lamp at 1,2,3 (x,y,z) of the section. """
    palette = [state("minecraft:stone"), state("minecraft:redstone_lamp", lit="true")]
    indices = numpy.zeros(4096, "intp")
    indices[2 * 256 + 3 * 16 + 1] = 1
    return makeSection(y, palette, indices, **kwargs)


def openWorld(world, chunks, dimName=""):
    regions = {}
    for chunk in chunks:
        cx, cz = chunk["xPos"].value, chunk["zPos"].value
        regions.setdefault((cx >> 5, cz >> 5), {})[cx, cz] = chunk
    for (rx, rz), found in regions.items():
        folder = world / dimName / "region" if dimName else world / "region"
        writeRegion(folder / ("r.%d.%d.mca" % (rx, rz)), found)
    return WorldEditor(str(world), readonly=True)


def testBlocksAreIndexedYZX(world):
    editor = openWorld(world, [makeChunk(0, 0, [mixedSection(0)])])
    section = editor.getDimension().getChunk(0, 0).getSection(0)
    lamp = editor.blocktypes["minecraft:redstone_lamp[lit=true]"]
    stone = editor.blocktypes["minecraft:stone"]
    assert (section.Blocks[2, 3, 1], section.Data[2, 3, 1]) == (lamp.ID, lamp.meta)
    assert (section.Blocks == stone.ID).sum() == 4095
    assert section.Blocks.shape == section.Data.shape == section.SkyLight.shape == (16, 16, 16)


def testNegativeSectionsAndCoordinates(world):
    editor = openWorld(world, [makeChunk(-1, -1, [mixedSection(-4)])])
    dim = editor.getDimension()
    x, y, z = -16 + 1, -64 + 2, -16 + 3
    assert dim.getBlock(x, y, z).internalName == "minecraft:redstone_lamp"
    assert dim.getBlock(x + 1, y, z).internalName == "minecraft:stone"
    assert editor.containsPoint(x, y, z, "")
    assert not editor.containsPoint(x, -65, z, "")
    assert not editor.containsPoint(x, 320, z, "")


def testSingleEntryPaletteHasNoData(world):
    editor = openWorld(world, [makeChunk(0, 0, [makeSection(0, [state("minecraft:dirt")])])])
    section = editor.getDimension().getChunk(0, 0).getSection(0)
    assert (section.Blocks == editor.blocktypes["minecraft:dirt"].ID).all()


def testPaletteLargerThanSixteenUsesMoreBits(world):
    palette = [state("minecraft:redstone_wire", power=str(p), east="none", west="none", north="none", south="none")
               for p in range(16)] + [state("minecraft:stone")]
    indices = numpy.arange(4096) % 17
    editor = openWorld(world, [makeChunk(0, 0, [makeSection(0, palette, indices)])])
    section = editor.getDimension().getChunk(0, 0).getSection(0)
    assert editor.blocktypes[int(section.Blocks[0, 0, 16 - 16 + 0]), 0].internalName == "minecraft:redstone_wire"
    assert (section.Blocks.ravel()[16::17] == editor.blocktypes["minecraft:stone"].ID).all()


def testAirSectionsAreNotPresent(world):
    air = makeSection(1, [state("minecraft:air")], skyLight=0xff)
    caveAir = makeSection(2, [state("minecraft:cave_air")], skyLight=0xff)
    editor = openWorld(world, [makeChunk(0, 0, [mixedSection(0), air, caveAir])])
    chunk = editor.getDimension().getChunk(0, 0)
    assert chunk.sectionPositions() == [0]
    assert chunk.getSection(1) is None


def testDarkAirSectionIsKeptForItsLight(world):
    dark = makeSection(0, [state("minecraft:cave_air")], skyLight=0)
    editor = openWorld(world, [makeChunk(0, 0, [dark])])
    section = editor.getDimension().getChunk(0, 0).getSection(0)
    assert (section.Blocks == 0).all()
    assert (section.SkyLight == 0).all()


def testSectionsOutsideTheWorldAreIgnored(world):
    # The game saves light-only sections just above and below the world.
    above = nbt.TAG_Compound()
    above["Y"] = nbt.TAG_Byte(20)
    above["SkyLight"] = nbt.TAG_Byte_Array(numpy.zeros(2048, "uint8"))
    below = nbt.TAG_Compound()
    below["Y"] = nbt.TAG_Byte(-5)
    below["BlockLight"] = nbt.TAG_Byte_Array(numpy.full(2048, 0x11, "uint8"))
    editor = openWorld(world, [makeChunk(0, 0, [below, mixedSection(0), above])])
    chunk = editor.getDimension().getChunk(0, 0)
    assert chunk.sectionPositions() == [0]
    assert chunk.getSection(20) is None
    assert chunk.getSection(-5) is None


def testSavedLightIsUnpacked(world):
    editor = openWorld(world, [makeChunk(0, 0, [mixedSection(0, skyLight=0xa5, blockLight=0x3c)])])
    section = editor.getDimension().getChunk(0, 0).getSection(0)
    assert section.SkyLight[0, 0, 0] == 0x5 and section.SkyLight[0, 0, 1] == 0xa
    assert section.BlockLight[0, 0, 0] == 0xc and section.BlockLight[0, 0, 1] == 0x3


def testSkyLightIsFullAboveTheTopSection(world):
    editor = openWorld(world, [makeChunk(0, 0, [mixedSection(0)])])
    section = editor.getDimension().getChunk(0, 0).getSection(0)
    assert (section.SkyLight == 15).all()
    assert (section.BlockLight == 0).all()


def testMissingSkyLightRepeatsBottomLayerAbove(world):
    upper = mixedSection(1)
    light = numpy.zeros(2048, "uint8")
    light[0:128] = 0x77  # the bottom layer has light 7, everything above it none
    upper["SkyLight"] = nbt.TAG_Byte_Array(light)
    editor = openWorld(world, [makeChunk(0, 0, [mixedSection(-1), mixedSection(0), upper])])
    chunk = editor.getDimension().getChunk(0, 0)
    assert (chunk.getSection(1).SkyLight[1:] == 0).all()
    assert (chunk.getSection(0).SkyLight == 7).all()
    assert (chunk.getSection(-1).SkyLight == 7).all()


def testChunkBoundsReachIntoNegativeY(world):
    editor = openWorld(world, [makeChunk(1, 2, [mixedSection(0)])])
    bounds = editor.getDimension().getChunk(1, 2).bounds
    assert tuple(bounds.origin) == (16, -64, 32)
    assert tuple(bounds.size) == (16, 384, 16)


def testDimensionBoundsUseItsHeight(tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    writeLevelDat(world, dimensions={"minecraft:overworld": "minecraft:overworld",
                                     "minecraft:the_nether": "minecraft:the_nether"})
    writeRegion(world / "region" / "r.0.0.mca", {(0, 0): simpleChunk(0, 0), (3, 1): simpleChunk(3, 1)})
    writeRegion(world / "DIM-1" / "region" / "r.0.0.mca", {(0, 0): makeChunk(0, 0, [mixedSection(0)], yPos=0)})
    editor = WorldEditor(str(world), readonly=True)
    overworld = editor.getDimension().bounds
    assert (overworld.miny, overworld.maxy) == (-64, 320)
    assert (overworld.minx, overworld.maxx, overworld.minz, overworld.maxz) == (0, 64, 0, 32)
    nether = editor.getDimension("DIM-1").bounds
    assert (nether.miny, nether.maxy) == (0, 256)


def testNetherHasNoSectionBelowZero(tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    writeLevelDat(world, dimensions={"minecraft:the_nether": "minecraft:the_nether"})
    writeRegion(world / "DIM-1" / "region" / "r.0.0.mca",
                {(0, 0): makeChunk(0, 0, [mixedSection(-1), mixedSection(0)], yPos=0)})
    chunk = WorldEditor(str(world), readonly=True).getDimension("DIM-1").getChunk(0, 0)
    assert chunk.sectionPositions() == [0]


def testCustomDimensionHeightComesFromItsChunks(tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    writeLevelDat(world, dimensions={"minecraft:overworld": "minecraft:overworld",
                                     "mymod:tall": "mymod:tall_type"})
    dimName = "dimensions/mymod/tall"
    sections = [mixedSection(-8), mixedSection(31)]
    writeRegion(world / dimName / "region" / "r.0.0.mca", {(0, 0): makeChunk(0, 0, sections, yPos=-8)})
    editor = WorldEditor(str(world), readonly=True)
    assert editor.adapter.heightRange(dimName) == (-128, 512)
    assert editor.getDimension(dimName).getChunk(0, 0).getSection(-8) is not None


def testUnknownDimensionTypeWithNoChunksUsesDefaultHeight(world):
    assert WorldEditor(str(world), readonly=True).adapter.heightRange("dimensions/none/here") == (-64, 320)


def testOlderChunkInNewWorldIsAnErrorNotADeletion(world):
    old = nbt.TAG_Compound()
    old["DataVersion"] = nbt.TAG_Int(1343)
    old["Level"] = nbt.TAG_Compound()
    writeRegion(world / "region" / "r.0.0.mca", {(0, 0): old})
    editor = WorldEditor(str(world), readonly=True)
    before = snapshot(world)
    with pytest.raises(AnvilChunkFormatError, match="older"):
        editor.getDimension().getChunk(0, 0)
    assert snapshot(world) == before


def testChunkFrom118IsStillRead(world):
    editor = openWorld(world, [makeChunk(0, 0, [mixedSection(0)], dataVersion=2975)])
    assert editor.getDimension().getChunk(0, 0).getSection(0) is not None


def testBadPaletteIndexIsAFormatError(world):
    palette = [state("minecraft:stone"), state("minecraft:dirt")]
    indices = numpy.full(4096, 9)
    editor = openWorld(world, [makeChunk(0, 0, [makeSection(0, palette, indices)])])
    chunk = editor.getDimension().getChunk(0, 0)
    with pytest.raises(AnvilChunkFormatError):
        chunk.getSection(0)


def testUnknownBlockInWorldIsKept(world):
    section = makeSection(0, [state("somemod:gadget", mode="on"), state("minecraft:stone")], numpy.arange(4096) % 2)
    editor = openWorld(world, [makeChunk(0, 0, [section])])
    gadget = editor.getDimension().getBlock(0, 0, 0)
    assert gadget.internalName == "somemod:gadget"
    assert gadget.blockState == "[mode=on]"
    assert gadget.unknown


def testRootTagsAreKeptAsSaved(world):
    attachments = nbt.TAG_Compound()
    attachments["data"] = nbt.TAG_Int(5)
    editor = openWorld(world, [makeChunk(0, 0, [mixedSection(0)], **{"neoforge:attachments": attachments})])
    chunk = editor.getDimension().getChunk(0, 0)
    chunk.getSection(0)
    assert chunk.rootTag["neoforge:attachments"]["data"].value == 5
    assert len(chunk.rootTag["sections"]) == 1
    assert "palette" in chunk.rootTag["sections"][0]["block_states"]


# --- Heightmaps and biomes ---

def heightmapTag(values, height=384):
    tag = nbt.TAG_Compound()
    tag["WORLD_SURFACE"] = nbt.TAG_Long_Array(packIndices(values, height.bit_length()))
    return tag


def testHeightMapIsCountedFromTheBottomOfTheWorld(world):
    values = numpy.arange(256) + 10
    chunk = makeChunk(0, 0, [mixedSection(0)], Heightmaps=heightmapTag(values))
    heightMap = openWorld(world, [chunk]).getDimension().getChunk(0, 0).HeightMap
    assert heightMap.shape == (16, 16)
    assert heightMap[0, 0] == 10 - 64
    assert heightMap[1, 0] == 26 - 64  # indexed ZX
    assert heightMap[0, 1] == 11 - 64
    assert heightMap[15, 15] == 265 - 64


def testHeightMapBitsFollowTheDimensionHeight(tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    writeLevelDat(world, dimensions={"minecraft:the_nether": "minecraft:the_nether"})
    chunk = makeChunk(0, 0, [mixedSection(0)], yPos=0, Heightmaps=heightmapTag(numpy.full(256, 200), 256))
    writeRegion(world / "DIM-1" / "region" / "r.0.0.mca", {(0, 0): chunk})
    assert (WorldEditor(str(world), readonly=True).getDimension("DIM-1").getChunk(0, 0).HeightMap == 200).all()


def testMissingHeightMapIsWorkedOutFromTheBlocks(world):
    heightMap = openWorld(world, [makeChunk(0, 0, [mixedSection(0)])]).getDimension().getChunk(0, 0).HeightMap
    assert (heightMap == 16).all()


def testHeightMapOfAnEmptyChunkIsTheBottomOfTheWorld(world):
    heightMap = openWorld(world, [makeChunk(0, 0, [])]).getDimension().getChunk(0, 0).HeightMap
    assert (heightMap == -64).all()


def testBiomesAreTakenAtTheSurface(world):
    biomes = ("minecraft:plains", "minecraft:desert", "minecraft:lush_caves")
    # The lowest layer of cells is lush caves and the rest are plains, but for cell x=3, z=0 of the top layer.
    indices = numpy.zeros(64, "intp")
    indices[:16] = 2
    indices[3 * 16 + 3] = 1
    section = makeSection(0, [state("minecraft:stone")], biomes=biomes, biomeIndices=indices)
    heights = numpy.full(256, 64 + 16)  # surface at Y=16, so the block below it is in the top cell of section 0
    chunk = makeChunk(0, 0, [section], Heightmaps=heightmapTag(heights))
    editor = openWorld(world, [chunk])
    Biomes = editor.getDimension().getChunk(0, 0).Biomes
    assert Biomes.shape == (16, 16)
    names = editor.adapter.biomeNames
    assert names[Biomes[0, 0]] == "minecraft:plains"
    assert names[Biomes[4, 15]] == "minecraft:plains"
    assert names[Biomes[0, 11]] == "minecraft:plains"
    assert names[Biomes[0, 12]] == "minecraft:desert"
    assert names[Biomes[3, 15]] == "minecraft:desert"


def testBiomesWithOneEntryHaveNoData(world):
    section = makeSection(0, [state("minecraft:stone")], biomes=("minecraft:desert",))
    chunk = makeChunk(0, 0, [section], Heightmaps=heightmapTag(numpy.full(256, 64 + 5)))
    editor = openWorld(world, [chunk])
    Biomes = editor.getDimension().getChunk(0, 0).Biomes
    assert {editor.adapter.biomeNames[b] for b in Biomes.ravel()} == {"minecraft:desert"}


def testColumnWithNoBiomeDataIsUnknown(world):
    chunk = makeChunk(0, 0, [], Heightmaps=heightmapTag(numpy.full(256, 64 + 5)))
    assert (openWorld(world, [chunk]).getDimension().getChunk(0, 0).Biomes == 255).all()


# --- Entities ---

def blockEntity(x, y, z):
    tag = nbt.TAG_Compound()
    tag["id"] = nbt.TAG_String("minecraft:chest")
    tag["x"], tag["y"], tag["z"] = nbt.TAG_Int(x), nbt.TAG_Int(y), nbt.TAG_Int(z)
    return tag


def entityFile(entities):
    root = nbt.TAG_Compound()
    root["DataVersion"] = nbt.TAG_Int(DATA_VERSION)
    root["Position"] = nbt.TAG_Int_Array(numpy.array([0, 0], ">u4"))
    root["Entities"] = nbt.TAG_List(entities)
    return root


def armorStand(uuid=(1, 2, 3, 4)):
    tag = nbt.TAG_Compound()
    tag["id"] = nbt.TAG_String("minecraft:armor_stand")
    tag["Pos"] = nbt.TAG_List([nbt.TAG_Double(1.5), nbt.TAG_Double(-30.0), nbt.TAG_Double(2.5)])
    tag["UUID"] = nbt.TAG_Int_Array(numpy.array(uuid, ">u4"))
    return tag


def testBlockEntitiesAreFound(world):
    editor = openWorld(world, [makeChunk(0, 0, [mixedSection(0)], blockEntities=[blockEntity(1, -30, 2)])])
    refs = list(editor.getDimension().getChunk(0, 0).TileEntities)
    assert len(refs) == 1
    assert refs[0].id == "minecraft:chest"
    assert tuple(refs[0].Position) == (1, -30, 2)


def testEntitiesComeFromTheirOwnFile(world):
    writeRegion(world / "entities" / "r.0.0.mca", {(0, 0): entityFile([armorStand()])})
    editor = openWorld(world, [makeChunk(0, 0, [mixedSection(0)])])
    refs = list(editor.getDimension().getChunk(0, 0).Entities)
    assert [r.id for r in refs] == ["minecraft:armor_stand"]
    assert tuple(refs[0].Position) == (1.5, -30.0, 2.5)


def testUUIDsAreReadFromIntArrays(world):
    uuid = (0x00112233, 0x44556677, 0x8899aabb, 0xccddeeff)
    writeRegion(world / "entities" / "r.0.0.mca", {(0, 0): entityFile([armorStand(uuid)])})
    editor = openWorld(world, [makeChunk(0, 0, [mixedSection(0)])])
    ref = next(iter(editor.getDimension().getChunk(0, 0).Entities))
    assert str(ref.UUID) == "00112233-4455-6677-8899-aabbccddeeff"


def testChunkWithNoEntityFileHasNoEntities(world):
    editor = openWorld(world, [makeChunk(0, 0, [mixedSection(0)])])
    assert list(editor.getDimension().getChunk(0, 0).Entities) == []


def testDamagedEntityFileDoesNotLoseTheBlocks(world):
    writeRegion(world / "entities" / "r.0.0.mca", {(0, 0): (2, b"garbage")})
    editor = openWorld(world, [makeChunk(0, 0, [mixedSection(0)])])
    chunk = editor.getDimension().getChunk(0, 0)
    assert list(chunk.Entities) == []
    assert chunk.getSection(0) is not None


def testEntityFileInADimension(tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    writeLevelDat(world, dimensions={"minecraft:the_nether": "minecraft:the_nether"})
    writeRegion(world / "DIM-1" / "region" / "r.0.0.mca", {(0, 0): makeChunk(0, 0, [mixedSection(0)], yPos=0)})
    writeRegion(world / "DIM-1" / "entities" / "r.0.0.mca", {(0, 0): entityFile([armorStand()])})
    chunk = WorldEditor(str(world), readonly=True).getDimension("DIM-1").getChunk(0, 0)
    assert len(list(chunk.Entities)) == 1


# --- Staying read-only ---

@pytest.mark.parametrize("call", [
    lambda e: e.adapter.createChunk(5, 5, ""),
    lambda e: e.syncToDisk() or e.adapter.selectedRevision.writeFile("x", b""),
    lambda e: e.adapter.deleteChunk(0, 0, ""),
    lambda e: e.adapter.writeChunk(e.getDimension().getChunk(0, 0).chunkData),
    lambda e: e.beginUndo(),
    lambda e: e.saveChanges(),
    lambda e: e.adapter.createPlayer("x"),
    lambda e: e.adapter.createMap(),
    lambda e: e.getDimension().getChunk(0, 0).getSection(5, create=True),
    lambda e: setattr(e.getDimension().getChunk(0, 0), "TerrainPopulated", False),
])
def testWritingIsRefused(world, call):
    editor = openWorld(world, [makeChunk(0, 0, [mixedSection(0)])])
    before = snapshot(world)
    with pytest.raises(IOError):
        call(editor)
    assert snapshot(world) == before


def testOpeningAndReadingChangesNothing(world):
    writeRegion(world / "entities" / "r.0.0.mca", {(0, 0): entityFile([armorStand()])})
    writeRegion(world / "region" / "r.0.0.mca", {(0, 0): makeChunk(0, 0, [mixedSection(0)])})
    before = snapshot(world)

    editor = WorldEditor(str(world), readonly=True)
    dim = editor.getDimension()
    for chunk in dim.getChunks():
        chunk.getSection(0)
        list(chunk.Entities)
        chunk.HeightMap
        chunk.Biomes
    list(editor.listPlayers())
    list(editor.listMaps())
    dim.bounds
    editor.syncToDisk()
    editor.close()
    assert snapshot(world) == before


# --- A world the game made ---

needsRealWorld = pytest.mark.skipif(not REAL_WORLD.is_dir(), reason="No test world from the game was found")


@pytest.fixture(scope="module")
def realWorld(tmp_path_factory):
    # Reading must not change the world, but a bug should not be able to spoil the only copy.
    folder = tmp_path_factory.mktemp("real") / "world"
    shutil.copytree(REAL_WORLD, folder)
    return folder


@pytest.fixture(scope="module")
def realEditor(realWorld):
    editor = WorldEditor(str(realWorld), readonly=True)
    yield editor
    editor.close()


@needsRealWorld
def testRealWorldHasThreeDimensions(realEditor):
    assert set(realEditor.listDimensions()) == {"", "DIM-1", "DIM1"}


@needsRealWorld
@pytest.mark.parametrize("dimName, low, high", [("", -64, 320), ("DIM-1", 0, 256), ("DIM1", 0, 256)])
def testRealWorldHeights(realEditor, dimName, low, high):
    bounds = realEditor.getDimension(dimName).bounds
    assert (bounds.miny, bounds.maxy) == (low, high)
    assert realEditor.getDimension(dimName).chunkCount() >= 144


@needsRealWorld
@pytest.mark.parametrize("dimName, position, name, blockState", [
    ("", (-1, -60, -1), "minecraft:diamond_block", ""),
    ("", (2, 319, 2), "minecraft:gold_block", ""),
    ("", (0, 200, 0), "minecraft:chest", "[facing=north,type=single,waterlogged=false]"),
    ("", (1, 200, 0), "minecraft:oak_sign", "[rotation=0,waterlogged=false]"),
    ("", (2, 200, 0), "minecraft:oak_stairs", "[facing=north,half=bottom,shape=straight,waterlogged=true]"),
    ("", (3, 200, 0), "minecraft:redstone_lamp", "[lit=true]"),
    ("", (5, 200, 0), "minecraft:water", "[level=0]"),
    ("", (7, 200, 0), "minecraft:water", "[level=3]"),
    ("", (9, 200, 0), "minecraft:lava", "[level=0]"),
    ("", (11, 200, 0), "minecraft:redstone_wire", "[east=none,north=none,power=0,south=none,west=none]"),
    ("DIM-1", (1, 100, 1), "minecraft:lapis_block", ""),
    ("DIM1", (1, 100, 1), "minecraft:emerald_block", ""),
])
def testRealWorldPlacedBlocks(realEditor, dimName, position, name, blockState):
    block = realEditor.getDimension(dimName).getBlock(*position)
    assert block.internalName == name
    assert block.blockState == blockState
    assert not block.unknown


@needsRealWorld
def testRealWorldBedrockFloor(realEditor):
    assert realEditor.getDimension().getBlock(5, -64, 5).internalName == "minecraft:bedrock"


@needsRealWorld
def testRealWorldHasNoUnknownBlocks(realEditor):
    # Every block the game generated is in the table exported from the game.
    for dimName in "", "DIM-1", "DIM1":
        dim = realEditor.getDimension(dimName)
        for chunk in dim.getChunks():
            for cy in chunk.sectionPositions():
                chunk.getSection(cy)
    unknown = [state for (_, state) in sorted((k, v) for k, v in realEditor.blocktypes.statesByID.items())
               if realEditor.blocktypes.blockJsons[state].get("unknown")]
    assert unknown == []


@needsRealWorld
def testRealWorldHeightMapMatchesBlocks(realEditor):
    dim = realEditor.getDimension()
    checked = 0
    for x, z in ((8, 8), (20, -30), (-40, 40), (-70, -70)):
        y = int(realEditor.heightMapAt(x, z, ""))
        assert dim.getBlock(x, y, z).internalName in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air")
        assert dim.getBlock(x, y - 1, z).internalName not in ("minecraft:air", "minecraft:cave_air")
        checked += 1
    assert checked == 4


@needsRealWorld
def testRealWorldChest(realEditor):
    chunk = realEditor.getDimension().getChunk(0, 0)
    chests = [ref for ref in chunk.TileEntities if ref.id == "minecraft:chest"]
    assert tuple(chests[0].Position) == (0, 200, 0)
    items = chests[0].rootTag["Items"]
    assert items[0]["id"].value == "minecraft:diamond"
    assert items[0]["count"].value == 5


@needsRealWorld
def testRealWorldEntities(realEditor):
    stand = [ref for ref in realEditor.getDimension().getChunk(0, 0).Entities if ref.id == "minecraft:armor_stand"]
    assert len(stand) == 1
    assert tuple(stand[0].Position) == (0.5, 202.0, 0.5)
    assert stand[0].UUID is not None
    pigs = [ref for ref in realEditor.getDimension().getChunk(-1, -1).Entities if ref.id == "minecraft:pig"]
    assert len(pigs) == 1


@needsRealWorld
def testRealWorldBiomesAreNamed(realEditor):
    names = realEditor.adapter.biomeNames
    chunk = realEditor.getDimension().getChunk(0, 0)
    assert all(names[b].startswith("minecraft:") for b in chunk.Biomes.ravel())
    netherBiomes = realEditor.getDimension("DIM-1").getChunk(0, 0).Biomes
    assert all(names[b] in ("minecraft:nether_wastes", "minecraft:crimson_forest", "minecraft:warped_forest",
                            "minecraft:soul_sand_valley", "minecraft:basalt_deltas") for b in netherBiomes.ravel())


@needsRealWorld
def testRealWorldPlayer(realEditor):
    players = list(realEditor.listPlayers())
    assert "" in players
    player = realEditor.getPlayer("")
    assert player.dimName == ""
    assert player.UUID is not None
    assert len(player.Position) == 3


@needsRealWorld
def testRealWorldMetadata(realEditor):
    metadata = realEditor.getWorldMetadata()
    assert metadata.RandomSeed == 20261105
    assert metadata.LevelName == "mcedit_test_121"
    assert realEditor.getWorldVersionInfo().name == "1.21.1"
    assert findAdapter(str(REAL_WORLD), getInfo=True)[2] == "Minecraft 1.21.1"


@needsRealWorld
def testRealWorldIsUnchangedByAllOfThis(tmp_path):
    folder = tmp_path / "world"
    shutil.copytree(REAL_WORLD, folder)
    before = snapshot(folder)
    editor = WorldEditor(str(folder), readonly=True)
    for dimName in list(editor.listDimensions()):
        dim = editor.getDimension(dimName)
        for chunk in dim.getChunks():
            for cy in chunk.sectionPositions():
                chunk.getSection(cy)
            list(chunk.Entities)
            list(chunk.TileEntities)
            chunk.HeightMap
            chunk.Biomes
    editor.syncToDisk()
    editor.close()
    assert snapshot(folder) == before
