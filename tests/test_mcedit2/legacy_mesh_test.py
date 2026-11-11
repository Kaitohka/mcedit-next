"""
    legacy_mesh_test

    The chunk mesh of a world from 1.8 to 1.12, whose fences, panes and walls get their actual state from the blocks
    beside them as the mesh is made. A typed variable of the mesh builder once took the place of a name in the code
    that reads those states, and every section with such a block failed to draw.
"""
from pathlib import Path

import numpy
import pytest

from mcedit2.rendering.blockmodels import BlockModels
from mcedit2.rendering.modelmesh import BlockModelMesh
from mcedit2.rendering.textureatlas import TextureAtlas
from mcedit2.resourceloader import ResourceLoader
from mceditlib.blocktypes import PCBlockTypeSet

ROOT = Path(__file__).resolve().parents[2]
JAR = ROOT / "MCEdit 2 Files" / "versionCache" / "1.12.jar"

needsJar = pytest.mark.skipif(not JAR.is_file(), reason="The Minecraft 1.12 client jar is not in the version cache")


class _Holder(object):
    pass


@pytest.fixture(scope="module")
def legacy():
    blocktypes = PCBlockTypeSet()
    resources = ResourceLoader(str(JAR))
    resources.addZipFile(str(JAR))
    world = _Holder()
    world.blocktypes = blocktypes
    models = BlockModels(blocktypes, resources)
    atlas = TextureAtlas(world, resources, models, overrideMaxSize=16384)
    return blocktypes, atlas


def mesh(blocktypes, atlas, placed):
    """ The quads of the mesh of a section with some blocks in it, at (x, y, z) inside it. """
    blocks = numpy.zeros((18, 18, 18), numpy.uint16)
    data = numpy.zeros((18, 18, 18), numpy.uint8)
    for (x, y, z), name in placed.items():
        block = blocktypes[name]
        blocks[y + 1, z + 1, x + 1] = block.ID
        data[y + 1, z + 1, x + 1] = block.meta
    chunk = _Holder()
    chunk.chunkPosition = (0, 0)
    update = _Holder()
    update.textureAtlas = atlas
    update.updateTask = update
    update.chunk = chunk
    section = _Holder()
    section.cy = 0
    section.areaBlocks = blocks
    section.areaData = data
    section.areaLights = lambda kind: numpy.full((18, 18, 18), 15, numpy.uint8)
    section.areaBiomes = numpy.zeros((18, 18), numpy.uint8)
    section.blocktypes = blocktypes
    section.fancyGraphics = True
    section.renderType = blocktypes.renderType
    section.chunkUpdate = update
    built = BlockModelMesh(section)
    built.createVertexArrays()
    if built.sceneNode is None:
        return numpy.zeros((0, 4, 8), numpy.float32)
    return numpy.concatenate([array.buffer.reshape(-1, 4, 8) for array in built.sceneNode.vertexArrays])


@needsJar
def testFencesGetTheirStateFromTheirNeighbours(legacy):
    blocktypes, atlas = legacy
    assert blocktypes.hasHiddenStates
    alone = mesh(blocktypes, atlas, {(5, 5, 5): "minecraft:fence"})
    joined = mesh(blocktypes, atlas, {(5, 5, 5): "minecraft:fence", (6, 5, 5): "minecraft:fence",
                                      (4, 5, 5): "minecraft:stone"})
    assert len(alone) > 0
    # Joined to the fence and the stone beside it, the fence has its arms as well as its post.
    assert len(joined) > len(alone) + 6


@needsJar
def testDoorsAndStainedPanesReadTheirStates(legacy):
    blocktypes, atlas = legacy
    door = mesh(blocktypes, atlas, {(5, 5, 5): "minecraft:wooden_door", (5, 6, 5): "minecraft:wooden_door",
                                    (8, 5, 5): "minecraft:stained_glass_pane",
                                    (9, 5, 5): "minecraft:stained_glass_pane"})
    assert len(door) > 0


# --- Connected textures drawn by the mesh builder ---

def withPack(tmp_path, files):
    """ The 1.12 game's blocks drawn with a pack of some more files in front of its own, as (blocktypes, atlas). """
    import json
    import zipfile
    pack = tmp_path / "pack.zip"
    with zipfile.ZipFile(pack, "w") as z:
        for name, content in files.items():
            z.writestr(name, content if isinstance(content, str) else json.dumps(content))
    blocktypes = PCBlockTypeSet()
    resources = ResourceLoader(str(JAR))
    resources.addZipFile(str(pack))
    resources.addZipFile(str(JAR))
    world = _Holder()
    world.blocktypes = blocktypes
    models = BlockModels(blocktypes, resources)
    atlas = TextureAtlas(world, resources, models, overrideMaxSize=16384)
    return blocktypes, atlas


def rectOf(atlas, name):
    return atlas.texCoordsByName["assets/minecraft/textures/blocks/%s.png" % name]


def inRect(quad, rect, fraction=None):
    """ Whether a quad's texture coordinates are all in a texture's rect on the atlas. """
    l, t, w, h = rect
    us, vs = quad[:, 3], quad[:, 4]
    return us.min() >= l - 1e-3 and us.max() <= l + w + 1e-3 and vs.min() >= t - 1e-3 and vs.max() <= t + h + 1e-3


def sideFaces(quads, x, z, normalAxis, value):
    """ The quads of a block's face at x or z = value, by the axis it faces along. """
    axis = {"x": 0, "z": 2}[normalAxis]
    return [q for q in quads if numpy.allclose(q[:, axis], value, atol=0.01)
            and (q[:, 0].min() >= x - 0.01 and q[:, 0].max() <= x + 1.01) and (q[:, 2].min() >= z - 0.01
                                                                             and q[:, 2].max() <= z + 1.01)]


PILLAR = {"assets/minecraft/textures/blocks/stone.png.mcmeta":
          {"ctm": {"ctm_version": 1, "type": "pillar", "textures": ["minecraft:blocks/cobblestone"]}}}


@needsJar
def testPillarsShowTheirEndsAndTheMiddleOfTheirSides(tmp_path):
    blocktypes, atlas = withPack(tmp_path, PILLAR)
    cobble, stone = rectOf(atlas, "cobblestone"), rectOf(atlas, "stone")
    alone = mesh(blocktypes, atlas, {(5, 5, 5): "minecraft:stone"})
    column = mesh(blocktypes, atlas, {(5, 4, 5): "minecraft:stone", (5, 5, 5): "minecraft:stone",
                                      (5, 6, 5): "minecraft:stone"})
    # Alone, its sides show the top left of the side texture and its top the end.
    side = sideFaces(alone, 5, 5, "z", 5)[0]
    assert inRect(side, cobble) and side[:, 3].max() <= cobble[0] + cobble[2] / 2 + 1e-3
    tops = [q for q in alone if numpy.allclose(q[:, 1], 5 + 1, atol=0.01)]
    assert tops and inRect(tops[0], stone)
    # In a column, the middle one's sides show the bottom left, where the texture runs on above and below.
    middle = [q for q in sideFaces(column, 5, 5, "z", 5) if q[:, 1].min() >= 4.99 and q[:, 1].max() <= 6.01][0]
    assert inRect(middle, cobble)
    lo, hi = sorted((middle[:, 4].min(), middle[:, 4].max()))
    assert not numpy.allclose((lo, hi), sorted((side[:, 4].min(), side[:, 4].max())))


@needsJar
def testEldritchMovesTheMiddleOfEachFace(tmp_path):
    blocktypes, atlas = withPack(tmp_path, {"assets/minecraft/textures/blocks/stone.png.mcmeta":
                                            {"ctm": {"ctm_version": 1, "type": "eldritch"}}})
    quads = mesh(blocktypes, atlas, {(5, 5, 5): "minecraft:stone"})
    assert len(quads) == 24  # four quarters for each side
    l, t, w, h = rectOf(atlas, "stone")
    middles = [corner for q in quads for corner in q if abs(corner[0] - 5.5) + abs(corner[1] - 5.5)
               + abs(corner[2] - 5.5) < 0.6 and not numpy.allclose(corner[3:5], (l + w / 2, t + h / 2), atol=1e-4)]
    assert middles  # some face's middle is not in the middle of its texture


@needsJar
def testFusionOverlaysDrawLayersOrNothing(tmp_path):
    blocktypes, atlas = withPack(tmp_path, {"assets/minecraft/textures/blocks/stone.png.mcmeta":
                                            {"fusion": {"type": "connecting", "layout": "overlay"}}})
    assert len(mesh(blocktypes, atlas, {(5, 5, 5): "minecraft:stone"})) == 0
    pair = mesh(blocktypes, atlas, {(5, 5, 5): "minecraft:stone", (6, 5, 5): "minecraft:stone"})
    assert len(pair) > 0


@needsJar
def testEdgesSplitFacesIntoQuarters(tmp_path):
    blocktypes, atlas = withPack(tmp_path, {"assets/minecraft/textures/blocks/stone.png.mcmeta":
                                            {"ctm": {"ctm_version": 1, "type": "edges",
                                                     "textures": ["minecraft:blocks/cobblestone",
                                                                  "minecraft:blocks/gold_block"]}}})
    alone = mesh(blocktypes, atlas, {(5, 5, 5): "minecraft:stone"})
    assert len(alone) == 24
    pair = mesh(blocktypes, atlas, {(5, 5, 5): "minecraft:stone", (6, 5, 5): "minecraft:stone"})
    cobble = rectOf(atlas, "cobblestone")
    assert any(inRect(q, cobble) for q in pair)  # the joined sides show the connected sheet


WOOL = ["wool_colored_%s" % c for c in ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray",
                                        "silver", "cyan", "purple", "blue", "brown", "green", "red", "black")]


@needsJar
def testOptiFineOverlaysAreDrawnOverTheFace(tmp_path):
    tiles = " ".join("minecraft:blocks/" + name for name in WOOL + ["gold_block"])
    blocktypes, atlas = withPack(tmp_path, {"assets/minecraft/optifine/ctm/overlay/dirt.properties":
                                            "method=overlay\ntiles=%s\nmatchBlocks=dirt\nconnectBlocks=grass\n" % tiles})
    alone = mesh(blocktypes, atlas, {(5, 5, 5): "minecraft:dirt"})
    assert len(alone) == 6
    besideGrass = mesh(blocktypes, atlas, {(5, 5, 5): "minecraft:dirt", (6, 5, 5): "minecraft:grass"})
    dirtFaces = [q for q in besideGrass if q[:, 0].max() <= 6.01 and q[:, 0].min() >= 4.99 and numpy.ptp(q[:, 0]) > 0.5
                 or (q[:, 0].max() <= 6.01 and numpy.allclose(q[:, 0], 5, atol=0.01))]
    woolRects = [rectOf(atlas, name) for name in WOOL + ["gold_block"]]
    overlays = [q for q in dirtFaces if any(inRect(q, rect) for rect in woolRects)]
    assert overlays  # the grass's edge is drawn over the dirt's sides beside it
    lifted = overlays[0]
    assert not numpy.allclose(lifted[:, 2], 5) or not numpy.allclose(lifted[:, 1], 5)
