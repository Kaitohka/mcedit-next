"""
    revision_model_test

    Runs long random sequences of edits, deletes, undos, redos and saves on a RevisionHistory, alongside a plain
    Python model of what each revision should hold. Every revision has to agree with the model at every step.
    Undo is what saves work when something goes wrong, and these are the kinds of bugs that lose it.
"""
import random
import zlib

import numpy
import pytest

from mceditlib import nbt
from mceditlib.anvil.worldfolder import AnvilWorldFolder, ENTITY_STORE, isStoreName, splitStore
from mceditlib.pc.regionfile import RegionFile
from mceditlib.revisionhistory import RevisionHistory


def chunkBytes(label):
    tag = nbt.TAG_Compound()
    tag["label"] = nbt.TAG_String(label)
    return tag.save(compressed=False)


def labelOf(data):
    return nbt.load(buf=data)["label"].value


class Model(object):
    """
    A history with a world folder beneath it, and what each revision should hold by plain dicts. It is driven
    the way the adapter drives it.
    """

    def __init__(self, tmp_path, seedChunks=8):
        world = tmp_path / "world"
        world.mkdir()
        self.folderPath = str(world)
        seed = AnvilWorldFolder(self.folderPath)
        self.chunks = {}
        for i in range(seedChunks):
            label = "seed %d" % i
            seed.writeChunkBytes(i, 0, "", chunkBytes(label))
            self.chunks[i, 0, ""] = label
        seed.writeFile("level.dat", b"seed level")
        seed.close()
        self.files = {"level.dat": b"seed level"}

        self.history = RevisionHistory(self.folderPath)
        self.states = [(dict(self.chunks), dict(self.files))]  # one for each node in the history
        self.current = 0
        self.savedIndex = 0
        self.counter = 0

    def check(self):
        for index, node in enumerate(self.history.nodes):
            chunks, files = self.states[index]
            for key, label in chunks.items():
                cx, cz, dimName = key
                assert node.containsChunk(cx, cz, dimName), (index, key, "is missing")
                assert labelOf(node.readChunkBytes(cx, cz, dimName)) == label, (index, key)
            for dimName in {key[2] for key in chunks} | {""}:
                expected = {(cx, cz) for cx, cz, d in chunks if d == dimName}
                assert set(node.chunkPositions(dimName)) == expected, (index, dimName)
                assert node.chunkCount(dimName) == len(expected)
            for path, data in files.items():
                assert node.containsFile(path), (index, path)
                assert node.readFile(path) == data, (index, path)
            for path in {"a.dat", "b.dat", "data/c.dat"} - set(files):
                assert not node.containsFile(path), (index, path)
            listed = {name for name in node.listFolder("data")}
            assert listed == {path for path in files if path.startswith("data/")}, index

    def checkWorldFolder(self):
        """ The world folder itself must hold the state that was saved. """
        chunks, files = self.states[self.savedIndex]
        folder = AnvilWorldFolder(self.folderPath, readonly=True)
        found = {}
        for dimName in folder.listDimensions():
            for cx, cz in folder.chunkPositions(dimName):
                found[cx, cz, dimName] = labelOf(folder.readChunkBytes(cx, cz, dimName))
        assert found == chunks
        for path, data in files.items():
            assert folder.readFile(path) == data
        for path in {"a.dat", "b.dat", "data/c.dat"} - set(files):
            assert not folder.containsFile(path)

    # --- The operations ---

    def newRevision(self):
        self.history.createRevision(self.current)
        del self.states[self.current + 1:]
        self.states.append((dict(self.states[self.current][0]), dict(self.states[self.current][1])))
        self.current = len(self.history.nodes) - 1
        self.savedIndex = min(self.savedIndex, self.current)

    def node(self):
        return self.history.nodes[self.current]

    def edit(self, rng):
        chunks, files = self.states[self.current]
        for _ in range(rng.randint(1, 6)):
            self.counter += 1
            action = rng.choice(("write", "write", "write", "delete", "writeFile", "deleteFile"))
            key = rng.randint(0, 11), rng.randint(0, 1), rng.choice(("", "", "DIM1", "" + ENTITY_STORE))
            path = rng.choice(("a.dat", "b.dat", "data/c.dat", "level.dat"))
            if action == "write":
                label = "edit %d" % self.counter
                self.node().writeChunkBytes(key[0], key[1], key[2], chunkBytes(label))
                chunks[key] = label
            elif action == "delete":
                if key in chunks:
                    self.node().deleteChunk(*key)
                    del chunks[key]
            elif action == "writeFile":
                data = b"edit %d" % self.counter
                self.node().writeFile(path, data)
                files[path] = data
            elif action == "deleteFile" and path in files:
                self.node().deleteFile(path)
                del files[path]

    def closeRevision(self):
        self.history.closeRevision()

    def undo(self):
        if self.current > 0:
            self.current -= 1

    def redo(self):
        if self.current < len(self.history.nodes) - 1:
            self.current += 1

    def save(self):
        self.history.writeAllChanges(self.current)
        self.savedIndex = self.current
        self.checkWorldFolder()


@pytest.mark.parametrize("seed", range(6))
def testRandomHistories(tmp_path, seed):
    rng = random.Random(seed)
    model = Model(tmp_path)
    for _ in range(60):
        action = rng.choice(("edit", "edit", "edit", "undo", "undo", "redo", "save"))
        if action == "edit":
            model.newRevision()
            model.edit(rng)
            model.closeRevision()
        elif action == "undo":
            model.undo()
        elif action == "redo":
            model.redo()
        else:
            model.save()
        model.check()
    model.history.close()


def testUndoAfterSavingManyRevisionsFindsTheRightData(tmp_path):
    # Revision 1 touches one chunk and revision 2 another. After saving, going back to the start has to bring
    # back the chunk that only revision 2 touched, which is why the reverse revisions must form a chain.
    model = Model(tmp_path)
    for i in 0, 1:
        model.newRevision()
        model.node().writeChunkBytes(i, 0, "", chunkBytes("changed %d" % i))
        model.states[model.current][0][i, 0, ""] = "changed %d" % i
        model.closeRevision()
    model.save()
    model.check()
    assert labelOf(model.history.nodes[0].readChunkBytes(1, 0, "")) == "seed 1"
    model.history.close()


def testSavingAtAnEarlierRevisionKeepsLaterOnes(tmp_path):
    model = Model(tmp_path)
    for i in range(3):
        model.newRevision()
        model.node().writeChunkBytes(i, 0, "", chunkBytes("changed %d" % i))
        model.states[model.current][0][i, 0, ""] = "changed %d" % i
        model.closeRevision()
    model.current = 1
    model.save()
    model.check()
    model.current = 3
    model.save()
    model.check()
    model.history.close()


def testDeleteAfterWriteInOneRevisionDoesNotBringBackOldData(tmp_path):
    model = Model(tmp_path)
    model.history.createRevision(0)
    node = model.history.nodes[1]
    node.writeChunkBytes(3, 0, "", chunkBytes("new"))
    node.deleteChunk(3, 0, "")
    assert not node.containsChunk(3, 0, "")
    node.writeFile("level.dat", b"new")
    node.deleteFile("level.dat")
    assert not node.containsFile("level.dat")
    model.history.close()


def testWriteAfterDeleteInOneRevisionBringsItBack(tmp_path):
    model = Model(tmp_path)
    model.history.createRevision(0)
    node = model.history.nodes[1]
    node.deleteChunk(3, 0, "")
    node.writeChunkBytes(3, 0, "", chunkBytes("back"))
    assert labelOf(node.readChunkBytes(3, 0, "")) == "back"
    node.deleteFile("level.dat")
    node.writeFile("level.dat", b"back")
    assert node.readFile("level.dat") == b"back"
    model.history.close()


def testAllDeletionsAreRemembered(tmp_path):
    model = Model(tmp_path)
    model.history.createRevision(0)
    node = model.history.nodes[1]
    node.deleteChunk(1, 0, "")
    node.deleteChunk(2, 0, "")
    node.writeFile("a.dat", b"x")
    node.deleteFile("a.dat")
    node.deleteFile("level.dat")
    assert sorted(node.loadDeletedChunks()) == [(1, 0, ""), (2, 0, "")]
    assert sorted(node.loadDeletedFiles()) == ["a.dat", "level.dat"]
    model.history.close()


def testSavedOldRevisionsSurviveClosingTheHistory(tmp_path):
    model = Model(tmp_path)
    model.newRevision()
    model.edit(random.Random(1))
    model.closeRevision()
    model.save()
    model.history.close()
    model.checkWorldFolder()


# --- Stores other than blocks ---

def testStoreNames():
    assert splitStore("") == ("", "region")
    assert splitStore("@entities") == ("", "entities")
    assert splitStore("DIM1@poi") == ("DIM1", "poi")
    assert splitStore("dimensions/a/b@entities") == ("dimensions/a/b", "entities")
    assert isStoreName("DIM-1@entities")
    assert not isStoreName("DIM-1")


def testStoresGoInTheirOwnFolders(tmp_path):
    folder = AnvilWorldFolder(str(tmp_path))
    folder.writeChunkBytes(1, 2, ENTITY_STORE, chunkBytes("entities"))
    folder.writeChunkBytes(1, 2, "DIM1" + ENTITY_STORE, chunkBytes("end entities"))
    assert (tmp_path / "entities" / "r.0.0.mca").exists()
    assert (tmp_path / "DIM1" / "entities" / "r.0.0.mca").exists()
    assert not (tmp_path / "region").exists() or not list((tmp_path / "region").iterdir())


def testStoresAreFoundWhenOpeningAWorld(tmp_path):
    folder = AnvilWorldFolder(str(tmp_path))
    folder.writeChunkBytes(1, 2, ENTITY_STORE, chunkBytes("entities"))
    folder.writeChunkBytes(1, 2, "DIM1" + ENTITY_STORE, chunkBytes("end entities"))
    folder.writeChunkBytes(1, 2, "dimensions/a/b" + ENTITY_STORE, chunkBytes("mod entities"))
    folder.close()
    again = AnvilWorldFolder(str(tmp_path), readonly=True)
    assert labelOf(again.readChunkBytes(1, 2, "dimensions/a/b" + ENTITY_STORE)) == "mod entities"
    assert labelOf(again.readChunkBytes(1, 2, "DIM1" + ENTITY_STORE)) == "end entities"


def testEnumeratingKeepsEmptyRegionFiles(tmp_path):
    (tmp_path / "region").mkdir()
    empty = tmp_path / "region" / "r.0.0.mca"
    empty.write_bytes(bytes(8192))
    assert list(AnvilWorldFolder(str(tmp_path)).chunkPositions("")) == []
    assert empty.exists()


def testDeletingTheLastChunkRemovesTheRegionFile(tmp_path):
    folder = AnvilWorldFolder(str(tmp_path))
    folder.writeChunkBytes(0, 0, "", chunkBytes("x"))
    folder.deleteChunk(0, 0, "")
    assert not (tmp_path / "region" / "r.0.0.mca").exists()
    assert not folder.containsChunk(0, 0, "")
    assert not (tmp_path / "region" / "r.0.0.mca").exists()  # asking must not create it again


# --- Region files ---

@pytest.fixture
def huge():
    # Random bytes don't compress, so this stays over a megabyte
    tag = nbt.TAG_Compound()
    tag["blob"] = nbt.TAG_Byte_Array(numpy.random.RandomState(1).randint(0, 256, 2000000).astype("uint8"))
    tag["xPos"] = nbt.TAG_Int(0)
    tag["zPos"] = nbt.TAG_Int(0)
    return tag.save(compressed=False)


def testHugeChunkGoesInAFileOfItsOwn(tmp_path, huge):
    region = RegionFile(str(tmp_path / "r.0.0.mca"), allowExternal=True)
    region.writeChunkBytes(3, 4, huge)
    assert (tmp_path / "c.3.4.mcc").exists()
    assert region.readChunkBytes(3, 4) == huge
    assert (tmp_path / "r.0.0.mca").stat().st_size < 20000
    assert zlib.decompress((tmp_path / "c.3.4.mcc").read_bytes()) == huge

    # and it still reads after opening the file again
    assert RegionFile(str(tmp_path / "r.0.0.mca"), readonly=True).readChunkBytes(3, 4) == huge


def testExternalChunkLeavesWhenItShrinks(tmp_path, huge):
    region = RegionFile(str(tmp_path / "r.0.0.mca"), allowExternal=True)
    region.writeChunkBytes(0, 0, huge)
    region.writeChunkBytes(0, 0, chunkBytes("small"))
    assert not (tmp_path / "c.0.0.mcc").exists()
    assert labelOf(region.readChunkBytes(0, 0)) == "small"


def testDeletingAnExternalChunkRemovesItsFile(tmp_path, huge):
    region = RegionFile(str(tmp_path / "r.0.0.mca"), allowExternal=True)
    region.writeChunkBytes(0, 0, huge)
    region.writeChunkBytes(1, 0, chunkBytes("other"))
    region.deleteChunk(0, 0)
    assert not (tmp_path / "c.0.0.mcc").exists()
    assert not region.containsChunk(0, 0)


def testExternalChunkFileNameUsesTheWorldPosition(tmp_path, huge):
    region = RegionFile(str(tmp_path / "r.-1.2.mca"), allowExternal=True)
    region.writeChunkBytes(-1, 65, huge)
    assert (tmp_path / "c.-1.65.mcc").exists()


def testHugeChunkStaysAnErrorForOldWorlds(tmp_path, huge):
    region = RegionFile(str(tmp_path / "r.0.0.mca"))
    with pytest.raises(Exception, match="exceeds 1 megabyte"):
        region.writeChunkBytes(0, 0, huge)


def testHugeChunkMovesThroughAHistory(tmp_path, huge):
    world = tmp_path / "world"
    world.mkdir()
    (world / "level.dat").write_bytes(b"x")
    history = RevisionHistory(str(world), externalChunks=True)
    node = history.createRevision(0)
    node.writeChunkBytes(2, 3, "", huge)
    assert node.readChunkBytes(2, 3, "") == huge
    history.writeAllChanges(1)
    assert (world / "region" / "c.2.3.mcc").exists()
    assert history.nodes[1].readChunkBytes(2, 3, "") == huge
    assert not history.nodes[0].containsChunk(2, 3, "")
    history.close()


def regionWithSomething(path):
    region = RegionFile(str(path))
    tag = nbt.TAG_Compound()
    tag["xPos"] = nbt.TAG_Int(0)
    tag["zPos"] = nbt.TAG_Int(0)
    region.writeChunkBytes(0, 0, tag.save(compressed=False))
    return path


def testOpeningARegionFileChangesNothing(tmp_path):
    path = regionWithSomething(tmp_path / "r.0.0.mca")
    with open(path, "ab") as f:
        f.write(b"some bytes past the last sector")
    data = path.read_bytes()
    region = RegionFile(str(path))
    assert region.containsChunk(0, 0)
    assert region.readChunkBytes(0, 0)
    assert path.read_bytes() == data


def testEmptyRegionFileCanBeWrittenTo(tmp_path):
    path = tmp_path / "r.0.0.mca"
    path.write_bytes(b"")
    region = RegionFile(str(path))
    assert not region.containsChunk(0, 0)
    assert path.stat().st_size == 0
    region.writeChunkBytes(0, 0, chunkBytes("x"))
    assert labelOf(region.readChunkBytes(0, 0)) == "x"
    assert path.stat().st_size >= 8192


def testRepairWaitsForAWrite(tmp_path):
    path = regionWithSomething(tmp_path / "r.0.0.mca")
    data = bytearray(path.read_bytes())
    data[4:8] = data[0:4]  # chunk 1,0 now claims the sectors of chunk 0,0
    path.write_bytes(bytes(data))
    before = path.read_bytes()

    region = RegionFile(str(path))
    assert path.read_bytes() == before
    assert region.containsChunk(1, 0)

    region.writeChunkBytes(5, 5, chunkBytes("x"))
    assert region.containsChunk(0, 0)
    assert not region.containsChunk(1, 0)


def testRepairKeepsAChunkItCannotRead(tmp_path):
    path = regionWithSomething(tmp_path / "r.0.0.mca")
    region = RegionFile(str(path))
    region.writeChunkCompressed(2, 0, b"\0\x05hello data", RegionFile.VERSION_CUSTOM)
    data = bytearray(path.read_bytes())
    data[4 * 5:4 * 5 + 4] = (9999 << 8 | 1).to_bytes(4, "big")  # slot 5,0 points past the end of the file
    path.write_bytes(bytes(data))

    region = RegionFile(str(path))
    region.writeChunkBytes(7, 0, chunkBytes("x"))
    assert region.containsChunk(2, 0)  # needs a mod to read, but is no reason to lose it
    assert not region.containsChunk(5, 0)  # really is gone
