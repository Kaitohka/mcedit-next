"""
    raycast_negative_y_test

    Picking a block with the mouse has to work below Y=0 in worlds that go down that far.
"""
import importlib.util
from pathlib import Path

import pytest

from mcedit2.util.raycast import rayCast
from mceditlib.geometry import Ray, Vector
from mceditlib.worldeditor import WorldEditor

_spec = importlib.util.spec_from_file_location(
    "modern_world_test", Path(__file__).resolve().parents[1] / "test_mceditlib" / "modern_world_test.py")
worlds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worlds)


@pytest.fixture
def dimension(tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    worlds.writeLevelDat(world)
    # A floor one section thick from Y=-64 to Y=-49, with nothing at all above it.
    section = worlds.makeSection(-4, [worlds.state("minecraft:stone")])
    worlds.writeRegion(world / "region" / "r.0.0.mca", {(0, 0): worlds.makeChunk(0, 0, [section])})
    return WorldEditor(str(world), readonly=True).getDimension()


def testRayFromAboveFindsTheTopOfANegativeFloor(dimension):
    position, face = rayCast(Ray(Vector(8.5, -20.5, 8.5), Vector(0, -1, 0)), dimension, maxDistance=100)
    assert tuple(position) == (8, -49, 8)


def testRayFromBelowZeroFindsTheFloor(dimension):
    position, face = rayCast(Ray(Vector(8.5, -40.5, 8.5), Vector(0, -1, 0)), dimension, maxDistance=100)
    assert tuple(position) == (8, -49, 8)
