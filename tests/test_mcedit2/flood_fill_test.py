"""
    flood_fill_test

    Flood fill has to stop at the world's height and at chunks that don't exist. Filling air used
    to spread through the sky and into missing chunks without end, and the app stopped responding.
"""
import importlib.util
from pathlib import Path

import pytest

from mcedit2.editortools import flood_fill
from mceditlib import faces
from mceditlib.worldeditor import WorldEditor

_spec = importlib.util.spec_from_file_location(
    "modern_world_test", Path(__file__).resolve().parents[1] / "test_mceditlib" / "modern_world_test.py")
worlds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worlds)


class FakeSession:
    def __init__(self, dimension):
        self.currentDimension = dimension


@pytest.fixture
def dimension(tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    worlds.writeLevelDat(world)
    # One chunk, with a stone floor from Y=-64 to Y=-49 and nothing above it.
    section = worlds.makeSection(-4, [worlds.state("minecraft:stone")])
    worlds.writeRegion(world / "region" / "r.0.0.mca", {(0, 0): worlds.makeChunk(0, 0, [section])})
    editor = WorldEditor(str(world))
    yield editor.getDimension()
    editor.close()


def fill(dimension, point, blockName, monkeypatch):
    reports = []

    def runTask(text, task, cancel=None):
        for progress in task:
            reports.append(progress)

    monkeypatch.setattr(flood_fill, "showProgress", runTask)
    block = dimension.blocktypes[blockName]
    floodDirs = {face: True for face, offsets in faces.faceDirections}
    command = flood_fill.FloodFillCommand(FakeSession(dimension), point, block, False, floodDirs)
    command.perform()
    return block, reports


def testFillingAirStopsAtTheWorldsEdges(dimension, monkeypatch):
    bounds = dimension.bounds
    assert (bounds.miny, bounds.maxy) == (-64, 320)

    block, reports = fill(dimension, (3, -48, 5), "minecraft:glass", monkeypatch)

    chunk = dimension.getChunk(0, 0)
    filled = 0
    for cy in range(-3, 20):
        section = chunk.getSection(cy)
        assert section is not None, "section %d was not filled" % cy
        assert (section.Blocks == block.ID).all(), "section %d is not all glass" % cy
        filled += section.Blocks.size
    assert filled == 16 * 16 * (320 - -48)
    assert (chunk.getSection(-4).Blocks == dimension.blocktypes["minecraft:stone"].ID).all()
    # Nothing was made outside the world.
    assert sorted(dimension.chunkPositions()) == [(0, 0)]
    assert chunk.getSection(20) is None


def testFillingTheSameBlockDoesNothing(dimension, monkeypatch):
    block, reports = fill(dimension, (0, -60, 0), "minecraft:stone", monkeypatch)
    assert reports == []
    assert dimension.getChunk(0, 0).getSection(0) is None
