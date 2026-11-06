"""
Regression tests for behaviour that changes silently between Python 2 and 3, or between
NumPy 1 and 2, plus a full round trip of the 1.8 world used as the porting baseline.
"""
import gc
import pickle

import numpy
import pytest

from mceditlib import nbt
from mceditlib.blocktypes import PCBlockTypeSet
from mceditlib.anvil.adapter import AnvilWorldAdapter
from mceditlib.exceptions import LevelFormatError, RegionFormatError
from mceditlib.geometry import Vector
from mceditlib.multi_block import decodeChunkPos, chunkPosArray
from mceditlib.pc.regionfile import RegionFile
from mceditlib.revisionhistory import RevisionHistory, UndoFolderExists
from mceditlib.selection import BoundingBox
from tests.conftest import copy_temp_level

BASELINE_WORLD = "AnvilWorld_1.8"


def canonical(tag):
    """Reduce a tag to plain values, so compound key order does not affect comparison."""
    if tag.isCompound():
        return {key: canonical(value) for key, value in tag.items()}
    if tag.isList():
        return [canonical(value) for value in tag]
    if isinstance(tag, (nbt.TAG_Byte_Array, nbt.TAG_Int_Array, nbt.TAG_Long_Array)):
        return type(tag).__name__, tag.value.tobytes()
    return type(tag).__name__, tag.value


# --- NBT ---

@pytest.mark.parametrize("text", [
    "plain ascii",
    "中文告示牌",
    "emoji \U0001F600 above the BMP",
    "embedded\x00nul",
])
def testStringRoundTrip(text):
    root = nbt.TAG_Compound()
    root["text"] = nbt.TAG_String(text)
    root[text.replace("\x00", "")] = nbt.TAG_Byte(1)
    loaded = nbt.load(buf=root.save(compressed=False))
    assert loaded["text"].value == text


def testReadsModifiedUtf8AsWrittenByJava():
    # U+1F600 as a surrogate pair of three-byte sequences, then NUL as C0 80.
    payload = b"\xed\xa0\xbd\xed\xb8\x80\xc0\x80"
    name = b"s"
    data = (b"\x0a\x00\x00"
            + b"\x08" + len(name).to_bytes(2, "big") + name
            + len(payload).to_bytes(2, "big") + payload
            + b"\x00")
    assert nbt.load(buf=data)["s"].value == "\U0001F600\x00"


def testWritesModifiedUtf8():
    root = nbt.TAG_Compound()
    root["s"] = nbt.TAG_String("\U0001F600\x00")
    raw = root.save(compressed=False)
    assert b"\xed\xa0\xbd\xed\xb8\x80\xc0\x80" in raw
    assert b"\xf0\x9f" not in raw  # standard four-byte UTF-8 is not valid here


def testStringTooLongIsRejected():
    root = nbt.TAG_Compound()
    root["s"] = nbt.TAG_String("x" * 65536)
    with pytest.raises(ValueError):
        root.save(compressed=False)


def testLoadedArraysAreWritable():
    root = nbt.TAG_Compound()
    root["a"] = nbt.TAG_Byte_Array(numpy.zeros(4, "u1"))
    loaded = nbt.load(buf=root.save(compressed=False))
    loaded["a"].value[0] = 7  # raises if the array wraps the read-only input buffer
    assert loaded["a"].value[0] == 7


def testNativeOrderArrayIsSavedBigEndian():
    root = nbt.TAG_Compound()
    root["a"] = nbt.TAG_Int_Array(numpy.array([1], dtype="<u4"))
    assert b"\x00\x00\x00\x01" * 2 in root.save(compressed=False)  # the length, then the value


@pytest.mark.parametrize("tagClass, dtype", [
    (nbt.TAG_Byte_Array, "u1"),
    (nbt.TAG_Int_Array, ">u4"),
    (nbt.TAG_Long_Array, ">u8"),
])
def testArraysOfDifferentLengthCompareUnequal(tagClass, dtype):
    a = tagClass(numpy.zeros(2, dtype))
    b = tagClass(numpy.zeros(3, dtype))
    assert not a == b
    assert a != b
    assert a == a.copy()


def testCompoundReprDoesNotRecurse():
    root = nbt.TAG_Compound()
    root["a"] = nbt.TAG_Int(1)
    assert "'a'" in repr(root)


def testTagsPickle():
    root = nbt.TAG_Compound()
    root["a"] = nbt.TAG_Int(1)
    assert pickle.loads(pickle.dumps(root))["a"].value == 1


# --- Block types ---

def testBlockTypeTruthAndAttributes():
    blocktypes = PCBlockTypeSet()
    assert not blocktypes["minecraft:air"]
    assert blocktypes["minecraft:stone"]
    # An unknown attribute must look like a missing attribute, or hasattr() raises KeyError.
    assert not hasattr(blocktypes["minecraft:stone"], "notAnAttribute")


def testBlockTypesSort():
    blocktypes = PCBlockTypeSet()
    assert len(blocktypes) > 0
    listed = list(blocktypes)
    assert listed == sorted(listed)


# --- Geometry and selection ---

def testVectorDividesTruly():
    assert Vector(1, 2, 3) / 2 == Vector(0.5, 1, 1.5)


def testVectorAcceptsNumpyScalars():
    assert Vector(1, 2, 3) * numpy.int64(2) == Vector(2, 4, 6)
    assert Vector(2, 4, 6) / numpy.int32(2) == Vector(1, 2, 3)


def testBoundingBoxEqualityAndHash():
    a = BoundingBox((1, 2, 3), (4, 5, 6))
    b = BoundingBox((1, 2, 3), (4, 5, 6))
    c = BoundingBox((0, 2, 3), (4, 5, 6))
    assert a == b
    assert a != c
    assert a != None  # noqa: E711  (exercises __eq__ against a different type)
    assert {a, b, c} == {a, c}


def testChunkPosEncoding():
    # The encoder takes block coordinates and packs their chunk coordinates.
    x = numpy.array([-1, 0, 80], dtype="i4")
    z = numpy.array([48, -112, 0], dtype="i4")
    decoded = decodeChunkPos(chunkPosArray(x, z))
    assert decoded[..., 0].tolist() == (x >> 4).tolist()
    assert decoded[..., 1].tolist() == (z >> 4).tolist()


# --- Region files ---

def testOversizedChunkIsRefused(tmpdir):
    region = RegionFile(str(tmpdir.join("r.0.0.mca")))
    with pytest.raises(RegionFormatError):
        region.writeChunkCompressed(0, 0, b"\x00" * (256 * 4096), RegionFile.VERSION_DEFLATE)
    assert not region.containsChunk(0, 0)


def testRegionRoundTrip(tmpdir):
    path = str(tmpdir.join("r.0.0.mca"))
    region = RegionFile(path)
    region.writeChunkBytes(3, 4, b"chunk data")
    assert RegionFile(path).readChunkBytes(3, 4) == b"chunk data"


# --- The baseline world ---

@pytest.fixture
def baseline(tmpdir):
    return copy_temp_level(tmpdir, BASELINE_WORLD)


def testBaselineWorldInfo(baseline):
    assert baseline.adapter.metadata.LevelName == "New World1.8-2"
    assert "" in list(baseline.listDimensions())


def testEveryChunkSurvivesDecodeAndEncode(baseline):
    """Exercises nibble unpacking and packing, the most NumPy-sensitive code in the adapter."""
    dim = baseline.getDimension()
    adapter = baseline.adapter
    region = adapter.selectedRevision
    positions = list(dim.chunkPositions())
    assert positions

    for cx, cz in positions:
        original = canonical(nbt.load(buf=region.readChunkBytes(cx, cz, "")))
        rebuilt = canonical(adapter.readChunk(cx, cz, "").buildNBTTag())

        sectionsBefore = sorted(original["Level"].pop("Sections"), key=lambda s: s["Y"][1])
        sectionsAfter = sorted(rebuilt["Level"].pop("Sections"), key=lambda s: s["Y"][1])
        # Fully empty sections are dropped on save, by design. Nothing else may be.
        kept = {after["Y"][1] for after in sectionsAfter}
        for section in sectionsBefore:
            if section["Y"][1] not in kept:
                assert not any(section["Blocks"][1]), (cx, cz, section["Y"])
        assert sectionsAfter == [s for s in sectionsBefore if s["Y"][1] in kept], (cx, cz)

        # The adapter adds an empty Biomes array when one is missing, and drops empty TileTicks.
        for level in original["Level"], rebuilt["Level"]:
            level.pop("TileTicks", None)
            level.pop("Biomes", None)
        assert original == rebuilt, (cx, cz)


def testEditAndSavePersistsOnlyThatEdit(baseline):
    dim = baseline.getDimension()
    positions = sorted(dim.chunkPositions())
    (cx, cz), other = positions[0], positions[-1]
    x, z = (cx << 4) + 3, (cz << 4) + 5
    y = max(cy for cy in dim.getChunk(cx, cz).sectionPositions()) << 4

    region = baseline.adapter.selectedRevision
    otherBefore = canonical(nbt.load(buf=region.readChunkBytes(other[0], other[1], "")))
    filename = baseline.filename

    baseline.requireRevisions()
    baseline.beginUndo()
    dim.setBlock(x, y, z, "minecraft:gold_block")
    baseline.commitUndo()
    baseline.saveChanges()
    baseline.close()

    from mceditlib.worldeditor import WorldEditor
    reopened = WorldEditor(filename)
    dim = reopened.getDimension()
    assert dim.getBlock(x, y, z).internalName == "minecraft:gold_block"
    assert dim.getBlock(x + 1, y, z).internalName != "minecraft:gold_block"

    region = reopened.adapter.selectedRevision
    assert canonical(nbt.load(buf=region.readChunkBytes(other[0], other[1], ""))) == otherBefore
    reopened.close()


def testUndoRestoresBlock(baseline):
    dim = baseline.getDimension()
    cx, cz = sorted(dim.chunkPositions())[0]
    x, z = (cx << 4) + 3, (cz << 4) + 5
    y = max(cy for cy in dim.getChunk(cx, cz).sectionPositions()) << 4
    before = dim.getBlock(x, y, z).internalName

    baseline.requireRevisions()
    baseline.beginUndo()
    dim.setBlock(x, y, z, "minecraft:gold_block")
    baseline.commitUndo()
    assert dim.getBlock(x, y, z).internalName == "minecraft:gold_block"

    baseline.undo()
    assert dim.getBlock(x, y, z).internalName == before

    baseline.redo()
    assert dim.getBlock(x, y, z).internalName == "minecraft:gold_block"
    baseline.close()


def testFailedOpenKeepsExistingUndoFolder(tmpdir):
    world = tmpdir.join("World")
    world.mkdir()
    undoFolder = tmpdir.join("##World.UNDO##")
    undoFolder.mkdir()
    undoFolder.join("work").write("another session's history")

    with pytest.raises(UndoFolderExists):
        RevisionHistory(world.strpath)
    gc.collect()  # runs __del__ on the partly constructed object

    assert undoFolder.join("work").read() == "another session's history"


def testRefusesNewerWorldWithoutTouchingIt(tmpdir):
    world = tmpdir.mkdir("World")
    root = nbt.TAG_Compound()
    root["Data"] = nbt.TAG_Compound()
    root["Data"]["DataVersion"] = nbt.TAG_Int(3955)
    root["Data"]["version"] = nbt.TAG_Int(19133)
    root.save(world.join("level.dat").strpath)
    world.mkdir("region")
    before = sorted(p.basename for p in tmpdir.visit())

    with pytest.raises(LevelFormatError):
        AnvilWorldAdapter(world.strpath)
    gc.collect()

    assert sorted(p.basename for p in tmpdir.visit()) == before


def testCloseTwiceIsHarmless(baseline):
    baseline.close()
    baseline.adapter.revisionHistory.close()
