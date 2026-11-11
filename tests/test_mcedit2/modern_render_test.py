"""
    modern_render_test

    Loads the block models and textures of Minecraft 1.21.1 for every block state. Those tests need the
    game's client jar, and are skipped if it has not been downloaded to the version cache.
"""
import importlib.util
import json
import zipfile
from pathlib import Path

import numpy
import pytest

from mcedit2.rendering.biome_colors import legacyBiomeColors, modernBiomeColors
from mcedit2.rendering.blockmodels import BlockModels, findMatchingVariant, matchWhen, splitResourceName
from mcedit2.rendering.textureatlas import TextureAtlas
from mcedit2.resourceloader import ResourceLoader
from mcedit2.util import minecraftinstall
from mceditlib.anvil.format_profile import MINECRAFT_1_21
from mceditlib.blocktypes import STATE_REDSTONE_WIRE, STATE_WATERLOGGED, PCBlockTypeSet
from mceditlib.blocktypes.fallback_shapes import fallbackModel
from mceditlib.blocktypes.modern import ModernBlockTypeSet
from mceditlib.worldeditor import WorldEditor

ROOT = Path(__file__).resolve().parents[2]
JAR = ROOT / "MCEdit 2 Files" / "versionCache" / "1.21.1.jar"

_spec = importlib.util.spec_from_file_location("modern_world_test", ROOT / "tests" / "test_mceditlib" / "modern_world_test.py")
worlds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worlds)

needsJar = pytest.mark.skipif(not JAR.is_file(), reason="The Minecraft 1.21.1 client jar is not in the version cache")


def bgra(color):
    return [color & 0xff, (color >> 8) & 0xff, (color >> 16) & 0xff, 0xff]


# --- Finding models ---

@pytest.mark.parametrize("name, expected", [
    ("minecraft:block/stone", ("minecraft", "block/stone")),
    ("block/cube_all", ("minecraft", "block/cube_all")),
    ("somemod:block/thing", ("somemod", "block/thing")),
    ("stone", ("minecraft", "stone")),
])
def testResourceNames(name, expected):
    assert splitResourceName(name) == expected


VARIANTS = {
    "facing=north,half=top": "top north",
    "facing=north,half=bottom": "bottom north",
    "facing=south": "any south",
}


@pytest.mark.parametrize("variant, expected", [
    ("facing=north,half=top,waterlogged=true", "top north"),
    ("facing=north,half=bottom,waterlogged=false", "bottom north"),
    ("facing=south,half=top", "any south"),
    ("facing=east,half=top", None),
])
def testVariantKeysCanNameSomeOfTheProperties(variant, expected):
    assert findMatchingVariant(VARIANTS, variant) == expected


def testEmptyVariantKeyMatchesAnyState():
    assert findMatchingVariant({"": "everything"}, "axis=x,waterlogged=true") == "everything"


@pytest.mark.parametrize("when, state, expected", [
    ({"north": "true"}, {"north": "true"}, True),
    ({"north": "true"}, {"north": "false"}, False),
    ({"north": "low|tall"}, {"north": "tall"}, True),
    ({"OR": [{"a": "1"}, {"b": "1"}]}, {"a": "0", "b": "1"}, True),
    ({"AND": [{"a": "1"}, {"b": "1"}]}, {"a": "0", "b": "1"}, False),
])
def testWhenClauses(when, state, expected):
    assert bool(matchWhen(when, state)) is expected


# --- Blocks the game draws with code of its own ---

def attributes(name, blockState="", renderType=2, hasEntity=True, particle="minecraft:block/oak_planks"):
    return {"internalName": name, "blockState": blockState, "renderType": renderType, "hasEntity": hasEntity,
            "particleTexture": particle}


def testChestHasNoFallbackBox():
    # It is drawn from its block entity, and a box the same size would hide it.
    assert fallbackModel(attributes("minecraft:chest", "[facing=north,type=single,waterlogged=false]")) is None


def testFallbackForTrappedChest():
    model = fallbackModel(attributes("minecraft:trapped_chest", "[facing=north,type=single,waterlogged=false]"))
    element = model["model"]["elements"][0]
    assert element["from"] == [1, 0, 1] and element["to"] == [15, 14, 15]
    assert len(element["faces"]) == 6
    assert model["model"]["textures"]["all"] == "minecraft:block/oak_planks"


@pytest.mark.parametrize("facing, rotation", [("north", 0), ("east", 90), ("south", 180), ("west", 270)])
def testFallbackTurnsWithFacing(facing, rotation):
    assert fallbackModel(attributes("minecraft:oak_wall_sign", "[facing=%s,waterlogged=false]" % facing))["y"] == rotation


@pytest.mark.parametrize("name, texture", [("minecraft:red_bed", "minecraft:block/red_wool"),
                                           ("minecraft:light_blue_banner", "minecraft:block/light_blue_wool"),
                                           ("minecraft:white_wall_banner", "minecraft:block/white_wool")])
def testBedsAndBannersUseTheirColor(name, texture):
    assert fallbackModel(attributes(name))["model"]["textures"]["all"] == texture


@pytest.mark.parametrize("attrs", [
    attributes("minecraft:stone", renderType=3, hasEntity=False),
    attributes("minecraft:barrier", renderType=0, hasEntity=False),
    attributes("minecraft:furnace", renderType=3),
    attributes("minecraft:moving_piston", renderType=0),
])
def testNoFallbackNeeded(attrs):
    assert fallbackModel(attrs) is None


def testBlockWithNoTextureGetsTheUnknownTexture():
    model = fallbackModel(attributes("somemod:machine", particle="minecraft:missingno"))
    assert model["model"]["textures"]["all"] == "MCEDIT_UNKNOWN"


# --- Hints for the renderer ---

@pytest.fixture(scope="module")
def blocktypes():
    return ModernBlockTypeSet(MINECRAFT_1_21)


@pytest.mark.parametrize("name, tint", [("minecraft:grass_block[snowy=false]", "grass"),
                                        ("minecraft:sugar_cane[age=0]", "grass"),
                                        ("minecraft:oak_leaves[distance=7,persistent=false,waterlogged=false]", "foliage"),
                                        ("minecraft:spruce_leaves[distance=7,persistent=false,waterlogged=false]", "foliagePine"),
                                        ("minecraft:birch_leaves[distance=7,persistent=false,waterlogged=false]", "foliageBirch"),
                                        ("minecraft:stone", None)])
def testBiomeTints(blocktypes, name, tint):
    assert blocktypes[name].biomeTintType == tint


def testStemsKeepTheirColorByAge(blocktypes):
    assert blocktypes["minecraft:melon_stem[age=0]"].color == 0x00ff00
    assert blocktypes["minecraft:melon_stem[age=7]"].color != 0x00ff00


def testFixedTints(blocktypes):
    assert blocktypes["minecraft:lily_pad"].color == 0x208030
    assert blocktypes["minecraft:mangrove_leaves[distance=7,persistent=false,waterlogged=false]"].color == 0x92c648


def flagsOf(blocktypes, name):
    block = blocktypes[name]
    return blocktypes.stateFlags[block.ID * 16 + block.meta]


def testWaterloggedFlag(blocktypes):
    stairs = "minecraft:oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=%s]"
    assert flagsOf(blocktypes, stairs % "true") & STATE_WATERLOGGED
    assert not flagsOf(blocktypes, stairs % "false") & STATE_WATERLOGGED
    assert flagsOf(blocktypes, "minecraft:kelp[age=0]") & STATE_WATERLOGGED
    assert flagsOf(blocktypes, "minecraft:seagrass") & STATE_WATERLOGGED
    assert not flagsOf(blocktypes, "minecraft:stone")


def testRedstoneWireFlagAndPower(blocktypes):
    # A block with this many states uses many IDs, and they all need to be known as redstone wire.
    for power in (0, 7, 15):
        name = "minecraft:redstone_wire[east=none,north=none,power=%d,south=none,west=none]" % power
        block = blocktypes[name]
        assert flagsOf(blocktypes, name) & STATE_REDSTONE_WIRE
        assert blocktypes.redstonePower(block.ID, block.meta) == power
    assert blocktypes.redstonePower(blocktypes["minecraft:redstone_wire[east=none,north=none,power=7,south=side,west=up]"].ID,
                                    blocktypes["minecraft:redstone_wire[east=none,north=none,power=7,south=side,west=up]"].meta) == 7


def testSelfCullingGroups(blocktypes):
    glass = blocktypes.selfCullGroup[blocktypes["minecraft:glass"].ID]
    red = blocktypes.selfCullGroup[blocktypes["minecraft:red_stained_glass"].ID]
    assert glass and red and glass != red
    assert not blocktypes.selfCullGroup[blocktypes["minecraft:stone"].ID]
    pane = "minecraft:glass_pane[east=%s,north=false,south=false,waterlogged=false,west=false]"
    assert blocktypes.selfCullGroup[blocktypes[pane % "true"].ID] == blocktypes.selfCullGroup[blocktypes[pane % "false"].ID]


def testOldBlockTypesAreUnchanged():
    old = PCBlockTypeSet()
    assert old.hasHiddenStates and not old.modelNamesAreFull
    assert old.textureFolder == "blocks"
    assert not old.stateFlags.any()
    assert not old.selfCullGroup.any()


# --- Which game's assets ---

def writeJar(path, version):
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr("version.json", json.dumps({"id": version}))
    return path


def testJarGameVersion(tmp_path):
    assert minecraftinstall.jarGameVersion(writeJar(tmp_path / "a.jar", "1.21.1")) == "1.21.1"
    (tmp_path / "bad.jar").write_bytes(b"not a jar")
    assert minecraftinstall.jarGameVersion(tmp_path / "bad.jar") is None
    assert minecraftinstall.jarGameVersion(tmp_path / "missing.jar") is None


def testJarBesideWorldInAVersionFolder(tmp_path):
    game = tmp_path / "versions" / "My Pack"
    world = game / "saves" / "World"
    world.mkdir(parents=True)
    assert minecraftinstall.findJarBesideWorld(str(world), "1.21.1") is None
    jar = writeJar(game / "My Pack.jar", "1.21.1")
    assert minecraftinstall.findJarBesideWorld(str(world), "1.21.1") == str(jar)
    assert minecraftinstall.findJarBesideWorld(str(world), "1.20.1") is None


def testAssetsVersionForWorld(tmp_path):
    modern = tmp_path / "modern"
    modern.mkdir()
    worlds.writeLevelDat(modern)
    assert minecraftinstall.assetsVersionForWorld(str(modern)) == "1.21.1"
    assert minecraftinstall.assetsVersionForWorld(str(modern / "level.dat")) == "1.21.1"

    schematic = modern / "thing.schematic"
    schematic.write_bytes(b"x")
    assert minecraftinstall.assetsVersionForWorld(str(schematic)) is None

    old = tmp_path / "old"
    old.mkdir()
    assert minecraftinstall.assetsVersionForWorld(str(old)) is None


def testAssetsOfTheWorldsOwnReleaseComeFirst(tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    worlds.writeLevelDat(world, 3837)  # 1.20.5, which has the same blocks as 1.20.6 to 1.21.1
    assert minecraftinstall.assetsVersionsForWorld(str(world)) == ["1.20.5", "1.21.1"]


def testOnly112AssetsServeOldWorlds(monkeypatch, tmp_path):
    # 1.13 renamed nearly every block, model and texture, so a newer game is no use for an older world.
    group = minecraftinstall.MCInstallGroup.__new__(minecraftinstall.MCInstallGroup)
    jar = writeJar(tmp_path / "1.21.1.jar", "1.21.1")

    class Install:
        versions = ["1.21.1"]

        def getVersionJarPath(self, version):
            return str(jar)

    monkeypatch.setattr(minecraftinstall, "_versionCacheDir", str(tmp_path / "none"))
    monkeypatch.setattr(minecraftinstall.MCInstallGroup, "installs", [Install()], raising=False)
    monkeypatch.setattr(minecraftinstall.MCInstallGroup, "mmcInstalls", [], raising=False)
    assert not group.findVersionWithAssets()
    assert group.findAssetsJar("1.21.1") == str(jar)
    assert group.findAssetsJar("1.20.1") is None


# --- Biome colors ---

class FakeLoader:
    def __init__(self, real, definitions):
        self.real = real
        self.definitions = definitions

    def openStream(self, path):
        return self.real.openStream(path)

    def biomeDefinitions(self):
        return iter(self.definitions.items())


@pytest.fixture(scope="module")
def loader():
    resourceLoader = ResourceLoader(str(JAR))
    resourceLoader.addZipFile(str(JAR))
    return resourceLoader


@pytest.fixture
def adapter(tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    worlds.writeLevelDat(world)
    return WorldEditor(str(world), readonly=True).adapter


@needsJar
def testBiomeDefinitionsAreFound(loader):
    names = dict(loader.biomeDefinitions())
    assert len(names) >= 60
    assert names["minecraft:desert"]["temperature"] == 2.0


@needsJar
def testBiomeColorsFromDefinitions(loader, adapter):
    colors = modernBiomeColors(loader, adapter)
    plains = adapter.biomeID("minecraft:plains")
    assert colors.water[plains].tolist() == bgra(0x3f76e4)
    assert colors.water[adapter.biomeID("minecraft:swamp")].tolist() == bgra(0x617b64)
    assert colors.grass[plains].tolist() == bgra(0x91bd59)
    assert colors.foliage[plains].tolist() == bgra(0x77ab2f)
    assert colors.grass[adapter.biomeID("minecraft:badlands")].tolist() == bgra(0x90814d)
    assert colors.grass[adapter.biomeID("minecraft:swamp")].tolist() == bgra(0x6a7039)
    assert colors.grass[adapter.biomeID("minecraft:dark_forest")].tolist() == bgra(0x507a32)


@needsJar
def testBiomeWithNoDefinitionGetsDefaultColors(loader, adapter):
    colors = modernBiomeColors(FakeLoader(loader, {}), adapter)
    unknown = adapter.biomeID("somemod:strange")
    assert colors.water[unknown].tolist() == bgra(0x3f76e4)
    assert colors.grass[unknown].tolist() == bgra(0x91bd59)


@needsJar
def testBiomeIDsAlreadyGivenOutAreKept(loader, adapter):
    first = adapter.biomeID("minecraft:desert")
    modernBiomeColors(loader, adapter)
    assert adapter.biomeID("minecraft:desert") == first


@needsJar
def testOldWorldsHaveUntintedWater(loader):
    colors = legacyBiomeColors(loader)
    assert (colors.water == 255).all()
    assert colors.grass.shape == (256, 4)


# --- Every block state ---

@pytest.fixture(scope="module")
def world(tmp_path_factory):
    folder = tmp_path_factory.mktemp("render") / "world"
    folder.mkdir()
    worlds.writeLevelDat(folder)
    return WorldEditor(str(folder), readonly=True)


@pytest.fixture(scope="module")
def models(world, loader):
    blockModels = BlockModels(world.blocktypes, loader)
    TextureAtlas(world, loader, blockModels, overrideMaxSize=8192)  # this cooks the models
    return blockModels


@pytest.fixture(scope="module")
def atlas(world, loader):
    blockModels = BlockModels(world.blocktypes, loader)
    return TextureAtlas(world, loader, blockModels, overrideMaxSize=8192)


@needsJar
def testEveryStateHasAModel(world, models):
    missing = []
    for (ID, meta), nameAndState in world.blocktypes.statesByID.items():
        if world.blocktypes[ID, meta].renderType == 3 and not models.hasModel(ID, meta):
            missing.append(nameAndState)
    assert missing == []


@needsJar
def testEveryTextureWasFound(atlas):
    missing = [name for name in atlas.blockModels.getTextureNames() if name not in atlas.texCoordsByName]
    assert missing == []


@needsJar
def testAtlasHasWaterAndLava(atlas):
    assert "assets/minecraft/textures/block/water_still.png" in atlas.texCoordsByName
    assert "assets/minecraft/textures/block/lava_still.png" in atlas.texCoordsByName


@needsJar
@pytest.mark.parametrize("name, quads", [
    ("minecraft:stone", 6),
    ("minecraft:trapped_chest[facing=north,type=single,waterlogged=false]", 6),
    ("minecraft:oak_sign[rotation=0,waterlogged=false]", 6),
    ("minecraft:red_bed[facing=north,occupied=false,part=foot]", 6),
    ("minecraft:black_shulker_box[facing=up]", 6),
    ("minecraft:heavy_core[waterlogged=false]", 6),
])
def testModelQuadCounts(world, models, name, quads):
    block = world.blocktypes[name]
    assert models.hasModel(block.ID, block.meta)
    assert models.quadCount(block.ID, block.meta) == quads


@needsJar
def testMultipartStateCombinesParts(world, models):
    post = world.blocktypes["minecraft:oak_fence[east=false,north=false,south=false,waterlogged=false,west=false]"]
    joined = world.blocktypes["minecraft:oak_fence[east=true,north=true,south=false,waterlogged=false,west=false]"]
    assert models.quadCount(joined.ID, joined.meta) > models.quadCount(post.ID, post.meta) > 0


@needsJar
def testStatesThatDifferOnlyByWaterloggedShareQuads(world, models):
    dry = world.blocktypes["minecraft:oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]"]
    wet = world.blocktypes["minecraft:oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=true]"]
    assert models.quadCount(dry.ID, dry.meta) == models.quadCount(wet.ID, wet.meta) > 0


@needsJar
def testStateAddedAfterCookingUsesTheUnknownModel(world, models):
    ID, meta = world.blocktypes.idAndMeta("somemod:gadget", [("n", "1")])
    assert not models.hasModel(ID, meta)
    assert models.quadCount(ID, meta) == models.quadCount(0, 15)


# --- Chest textures from 1.15 on ---

def testChestTextureConversionMovesEachFace():
    from mcedit2.rendering.chunkmeshes.entity.chest import chestTextureInOldLayout
    image = numpy.zeros((64, 64, 4), "uint8")
    image[0:14, 14:28] = 1  # the lid's top, in the new layout
    image[0:14, 28:42] = 2  # the lid's bottom
    image[14:19, 14:28, 0] = numpy.arange(5)[:, None]  # the lid's front, darker at the top
    old = chestTextureInOldLayout(image)
    assert (old[0:14, 14:28] == 2).all() and (old[0:14, 28:42] == 1).all()
    # The new front is the old back, turned half round.
    assert old[14:19, 42:56, 0].tolist() == [[4 - row] * 14 for row in range(5)]


def testChestTextureConversionUndoesItself():
    from mcedit2.rendering.chunkmeshes.entity.chest import chestTextureInOldLayout
    image = numpy.random.RandomState(3).randint(0, 255, (64, 64, 4)).astype("uint8")
    assert (chestTextureInOldLayout(chestTextureInOldLayout(image)) == image).all()


def testDoubleChestHalvesHaveTheirOwnTextures():
    from mcedit2.rendering.chunkmeshes.entity.chest import CHEST_TEXTURES, HALF_CHEST_BOXES, chestTextureInOldLayout
    folder = "assets/minecraft/textures/entity/chest/"
    assert CHEST_TEXTURES[folder + "normal_left.png"] == HALF_CHEST_BOXES
    assert CHEST_TEXTURES[folder + "trapped_right.png"] == HALF_CHEST_BOXES
    image = numpy.zeros((64, 64, 4), "uint8")
    image[0:14, 14:29] = 1  # the lid's top, a block and a half wide
    old = chestTextureInOldLayout(image, HALF_CHEST_BOXES)
    assert (old[0:14, 29:44] == 1).all() and (old[0:14, 14:29] == 0).all()


@pytest.mark.parametrize("side, west, east, knob", [("left", 0, 15, (0, 1)), ("right", 1, 16, (15, 16))])
def testDoubleChestHalvesMeetInTheMiddle(side, west, east, knob):
    # Facing south, the left half reaches over the west side of its block, where the right half is.
    from mcedit2.rendering.chunkmeshes.entity import models
    model = models.cookedTileEntityModels["MCEDIT_Chest_" + side]
    assert model.modelTexture.endswith("normal_%s.png" % side)
    xs = numpy.array(model.vertices)[:, 0]
    assert (xs.min(), xs.max()) == (west, east)
    knobModel = models.tileEntityModels["MCEDIT_Chest_" + side].chestKnob
    assert knobModel.cx == knob[0]


def testModBlockEntityWithModelFilesIsDrawnFromThem():
    # Its renderer's faces could not be had, but the pack says its model files can be drawn, as HBM's doors' can.
    door = attributes("hbm_m:sliding_blast_door", "[facing=north]")
    door["modelSource"] = "json"
    ModernBlockTypeSet._addRenderHints(door)
    assert "fallbackModel" not in door and door["renderType"] == 3
    box = attributes("somemod:machine", "[facing=north]")
    box["modelSource"] = "fallback"
    ModernBlockTypeSet._addRenderHints(box)
    assert "fallbackModel" in box
