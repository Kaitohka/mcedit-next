"""
    custom_blocks_test

    Models for blocks that block packs and user definitions describe, using a small pack with its own
    assets. The tests that load models need the Minecraft 1.21.1 client jar in the version cache.
"""
import importlib.util
import json
import struct
import zlib
from pathlib import Path

import pytest

from mcedit2.dialogs.configure_blocks import BlockDefinition, modelDisplayName, modelPathFromName
from mcedit2.rendering.biome_colors import modernBiomeColors
from mcedit2.rendering.blockmodels import BlockModels
from mcedit2.rendering.textureatlas import TextureAtlas
from mcedit2.resourceloader import ResourceLoader
from mcedit2.util import minecraftinstall
from mceditlib.blocktypes import pack_library
from mceditlib.blocktypes.block_pack import BlockPack
from mceditlib.worldeditor import WorldEditor

ROOT = Path(__file__).resolve().parents[2]
JAR = ROOT / "MCEdit 2 Files" / "versionCache" / "1.21.1.jar"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / "test_mceditlib" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


libraryTests = _load("pack_library_test")
worlds = libraryTests.worlds

needsJar = pytest.mark.skipif(not JAR.is_file(), reason="The Minecraft 1.21.1 client jar is not in the version cache")


def png(red, green, blue, size=16):
    """ A square of one color. """
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    row = b"\0" + bytes([red, green, blue, 255]) * size
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b""))


def writeAsset(pack, path, data):
    file = pack / path
    file.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        file.write_bytes(data)
    else:
        file.write_text(json.dumps(data), encoding="utf-8")


def writeDemoPack(folder):
    """
    A pack with three blocks: `machine` has its own model and texture, `bare` has no assets but says
    which texture it looks like, and `oil` is a fluid.
    """
    bare = libraryTests.machine("demomod:bare")
    bare["common"]["hasBlockstateFile"] = False
    bare["common"]["particleTexture"] = "minecraft:block/gold_block"
    oil = libraryTests.machine("demomod:oil")
    oil["common"].update(renderType=1, materialLiquid=True, particleTexture="demomod:block/machine", opaqueCube=False)
    oil["properties"] = {"level": ["0", "1"]}
    oil["defaultState"] = "level=0"
    oil["states"] = [{"state": "level=0", "resourceVariant": "level=0"}, {"state": "level=1", "resourceVariant": "level=1"}]

    libraryTests.writePack(folder, [libraryTests.machine(), bare, oil])
    writeAsset(folder, "assets/demomod/blockstates/machine.json", {
        "variants": {"mode=off": {"model": "demomod:block/machine"}, "mode=on": {"model": "demomod:block/machine", "y": 90}}})
    writeAsset(folder, "assets/demomod/models/block/machine.json", {
        "parent": "minecraft:block/cube_all", "textures": {"all": "demomod:block/machine"}})
    writeAsset(folder, "assets/demomod/textures/block/machine.png", png(255, 120, 0))
    return folder


# --- Blocks whose model files don't tell the whole story ---

def writeAwkwardPack(folder):
    """
    Blocks found in real mods: `made_in_code` names a model with no file, as a model made in code does. `filler` has an
    empty model and no texture. `partly` has one model made in code for one state, and a real one for another.
    `own_box` has a small box of its own and a cube for a parent, as AE2's charger does. `composite` is made of two
    models by NeoForge's composite loader, plus a third that is hidden. `obj` uses a loader that MCEdit doesn't have.
    """
    def block(name, particle, **common):
        result = libraryTests.machine("demomod:" + name)
        result["common"].update(particleTexture=particle, **common)
        return result

    blocks = [block("made_in_code", "minecraft:block/gold_block"),
              block("made_in_code_no_texture", "minecraft:missingno"),
              block("filler", "minecraft:missingno", modelSource="fallback"),
              block("partly", "minecraft:block/gold_block"),
              block("own_box", "minecraft:block/gold_block"),
              block("composite", "minecraft:block/gold_block"),
              block("obj", "minecraft:block/gold_block"),
              block("obj_parent_textures", "minecraft:block/gold_block")]
    libraryTests.writePack(folder, blocks)
    for name in "made_in_code", "made_in_code_no_texture":
        writeAsset(folder, "assets/demomod/blockstates/%s.json" % name, {"variants": {"": {"model": "demomod:block/from_code"}}})
    writeAsset(folder, "assets/demomod/blockstates/filler.json", {"variants": {"": {"model": "demomod:block/empty"}}})
    writeAsset(folder, "assets/demomod/models/block/empty.json", {"textures": {}})
    writeAsset(folder, "assets/demomod/blockstates/partly.json", {"variants": {
        "mode=off": {"model": "minecraft:block/stone"}, "mode=on": {"model": "demomod:block/from_code"}}})

    box = {"from": [4, 0, 4], "to": [12, 8, 12], "faces": {face: {"texture": "#0"} for face in
                                                            ("down", "up", "north", "south", "west", "east")}}
    for name in "own_box", "composite", "obj":
        writeAsset(folder, "assets/demomod/blockstates/%s.json" % name, {"variants": {"": {"model": "demomod:block/" + name}}})
    writeAsset(folder, "assets/demomod/models/block/own_box.json", {
        "parent": "minecraft:block/cube_all", "textures": {"0": "minecraft:block/stone"}, "elements": [box]})
    writeAsset(folder, "assets/demomod/models/block/composite.json", {
        "parent": "minecraft:block/block", "loader": "neoforge:composite",
        "textures": {"particle": "minecraft:block/gold_block"},
        "children": {"base": {"textures": {"0": "minecraft:block/stone"}, "elements": [box]},
                     "top": {"textures": {"0": "minecraft:block/dirt"}, "elements": [dict(box, **{"from": [6, 8, 6], "to": [10, 12, 10]})]},
                     "hidden": {"textures": {"0": "minecraft:block/dirt"}, "elements": [box]}},
        "visibility": {"hidden": False}})
    writeAsset(folder, "assets/demomod/models/block/obj.json", {
        "parent": "minecraft:block/block", "loader": "neoforge:obj", "model": "demomod:models/block/obj.obj"})
    # An OBJ model whose material names a texture that only the model's parent gives, as Create's wheels do.
    writeAsset(folder, "assets/demomod/blockstates/obj_parent_textures.json",
               {"variants": {"": {"model": "demomod:block/wheel/block"}}})
    writeAsset(folder, "assets/demomod/models/block/wheel/block.json", {
        "parent": "demomod:block/wheel/textures", "loader": "neoforge:obj", "flip_v": True,
        "model": "demomod:models/block/wheel/wheel.obj"})
    writeAsset(folder, "assets/demomod/models/block/wheel/textures.json", {
        "parent": "minecraft:block/block", "textures": {"plates": "minecraft:block/dirt"}})
    writeAsset(folder, "assets/demomod/models/block/wheel/wheel.obj",
               b"mtllib wheel.mtl\nv 0 0 0\nv 1 0 0\nv 1 0 1\nv 0 0 1\nvt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\n"
               b"usemtl plates\nf 1/1 2/2 3/3 4/4\n")
    writeAsset(folder, "assets/demomod/models/block/wheel/wheel.mtl", b"newmtl plates\nmap_Kd #plates\n")
    return folder


@pytest.fixture
def awkward(tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    worlds.writeLevelDat(world)
    pack = BlockPack(str(writeAwkwardPack(tmp_path / "pack")))
    library = tmp_path / "blockpacks"
    library.mkdir()
    writeAwkwardPack(library / "pack")
    pack_library.setPackFolders([str(library)])
    editor = WorldEditor(str(world), readonly=True)
    pack_library.setPackFolders([])
    loader = ResourceLoader(str(JAR))
    loader.addBlockPack(pack)
    loader.addZipFile(str(JAR))
    models = BlockModels(editor.blocktypes, loader)
    TextureAtlas(editor, loader, models, overrideMaxSize=8192)
    return editor.blocktypes, models


@needsJar
@pytest.mark.parametrize("name, hasModel, quads", [
    ("made_in_code[mode=off]", True, 6),
    ("filler[mode=off]", True, 0),
    ("partly[mode=off]", True, 6),
    ("partly[mode=on]", True, 6),
    ("own_box[mode=off]", True, 6),  # only its own box, not its parent's cube as well
    ("composite[mode=off]", True, 12),  # both shown parts
    ("obj[mode=off]", True, 6),  # a cube with its particle texture
])
def testBlocksWithAwkwardModels(awkward, name, hasModel, quads):
    blocktypes, models = awkward
    block = blocktypes["demomod:" + name]
    assert models.hasModel(block.ID, block.meta) is hasModel
    assert models.quadCount(block.ID, block.meta) == quads


@needsJar
def testModelMadeInCodeWithNoTextureIsAQuestionMark(awkward):
    blocktypes, models = awkward
    block = blocktypes["demomod:made_in_code_no_texture[mode=off]"]
    assert not models.hasModel(block.ID, block.meta)


@needsJar
def testCompositePartsUseTheirOwnTextures(awkward):
    blocktypes, models = awkward
    assert models.firstTextures["demomod:composite[mode=off]"] == "assets/minecraft/textures/block/stone.png"
    assert "MCEDIT_UNKNOWN" not in models.firstTextures["demomod:own_box[mode=off]"]


@needsJar
def testObjModelTakesTexturesFromItsParents(awkward):
    blocktypes, models = awkward
    block = blocktypes["demomod:obj_parent_textures[mode=off]"]
    assert models.quadCount(block.ID, block.meta) == 1
    assert models.firstTextures[block.nameAndState] == "assets/minecraft/textures/block/dirt.png"


# --- Definitions ---

def testShortestDefinitionByHand():
    definition = BlockDefinition(defJson={"internalName": "somemod:copper_machine"})
    assert definition.internalName == "somemod:copper_machine"
    assert definition.modelPath is None
    assert definition.opacity is None and definition.brightness is None


def testDefinitionFromThePlan():
    definition = BlockDefinition(defJson={"internalName": "somemod:copper_machine",
                                          "textures": {"all": "somemod:block/copper_machine"},
                                          "model": "minecraft:block/cube_all"})
    assert definition.modelPath == "assets/minecraft/models/block/cube_all.json"
    assert definition.modelTextures == {"all": "somemod:block/copper_machine"}


def testTexturesWithNoModelMeanACube():
    definition = BlockDefinition(defJson={"internalName": "a:b", "textures": {"all": "a:block/b"}})
    assert definition.modelPath == "assets/minecraft/models/block/cube_all.json"


def testOldStyleDefinitionsStillLoad():
    definition = BlockDefinition(defJson={"internalName": "a:b", "meta": 3, "opacity": 7, "brightness": 2,
                                          "modelPath": "assets/minecraft/models/block/stone.json",
                                          "modelTextures": {"#all": "assets/minecraft/textures/block/dirt.png"}})
    assert definition.meta == 3 and definition.opacity == 7 and definition.brightness == 2
    assert definition.modelPath == "assets/minecraft/models/block/stone.json"
    assert definition.modelTextures == {"#all": "assets/minecraft/textures/block/dirt.png"}


def testDefinitionSurvivesSaving():
    definition = BlockDefinition(defJson={"internalName": "a:b", "blockState": "[x=1]", "model": "a:block/b"})
    again = BlockDefinition(defJson=json.loads(json.dumps(definition.exportAsJson())))
    assert again.blockState == "[x=1]"
    assert again.modelPath == "assets/a/models/block/b.json"


@pytest.mark.parametrize("name, path", [("minecraft:block/cube", "assets/minecraft/models/block/cube.json"),
                                        ("block/cube", "assets/minecraft/models/block/cube.json"),
                                        ("a:block/b", "assets/a/models/block/b.json"),
                                        ("assets/a/models/block/b.json", "assets/a/models/block/b.json")])
def testModelPaths(name, path):
    assert modelPathFromName(name) == path


@pytest.mark.parametrize("path, name", [("assets/minecraft/models/block/cube_all.json", "cube_all"),
                                        ("assets/create/models/block/gearbox.json", "create:block/gearbox")])
def testModelNamesInTheList(path, name):
    assert modelDisplayName(path) == name


# --- The search path ---

@pytest.fixture
def library(tmp_path):
    folder = tmp_path / "blockpacks"
    folder.mkdir()
    pack_library.setPackFolders([str(folder)])
    yield folder
    pack_library.setPackFolders([])


@pytest.fixture
def jar(tmp_path):
    return str(libraryTests.zipFolder(_folderWith(tmp_path / "game", "assets/minecraft/x.txt", b"game"), tmp_path / "game.jar"))


def _folderWith(folder, path, data):
    writeAsset(folder, path, data)
    writeAsset(folder, "version.json", {"id": "1.21.1"})
    return folder


def testBlockPackAssetsComeBeforeTheGame(library, tmp_path, jar, monkeypatch):
    pack = writeDemoPack(library / "demo")
    writeAsset(pack, "assets/minecraft/x.txt", b"pack")
    world = libraryTests.moddedWorld(tmp_path, worlds.state("demomod:machine", mode="on"), demomod="1.0")

    monkeypatch.setattr(minecraftinstall, "GetInstalls", lambda: FakeInstalls(jar))
    loader = minecraftinstall.getModernResourceLoader(str(world), "1.21.1")
    assert loader.openStream("assets/minecraft/x.txt").read() == b"pack"
    assert loader.openStream("assets/demomod/textures/block/machine.png").read().startswith(b"\x89PNG")


def testModsFolderComesBeforeTheGame(library, tmp_path, jar, monkeypatch):
    game = tmp_path / "game folder"
    world = game / "saves" / "World"
    world.mkdir(parents=True)
    worlds.writeLevelDat(world)
    (game / "mods").mkdir()
    libraryTests.zipFolder(_folderWith(tmp_path / "mod", "assets/minecraft/x.txt", b"mod"), game / "mods" / "mod.jar")

    monkeypatch.setattr(minecraftinstall, "GetInstalls", lambda: FakeInstalls(jar))
    loader = minecraftinstall.getModernResourceLoader(str(world), "1.21.1")
    assert loader.openStream("assets/minecraft/x.txt").read() == b"mod"


def testOnlyChosenPacksJoinTheSearchPath(library, tmp_path, jar, monkeypatch):
    writeDemoPack(library / "demo")
    world = libraryTests.moddedWorld(tmp_path, othermod="1.0")
    monkeypatch.setattr(minecraftinstall, "GetInstalls", lambda: FakeInstalls(jar))
    loader = minecraftinstall.getModernResourceLoader(str(world), "1.21.1")
    assert [z.filename for z in loader.zipFiles] == [jar]


class FakeInstalls:
    def __init__(self, jar):
        self.jar = jar

    def findAssetsJar(self, version):
        return self.jar

    def getCurrentInstall(self):
        return None


def testPackBiomesAreDefinedFirst(tmp_path, jar):
    folder = tmp_path / "pack"
    libraryTests.writePack(folder)
    (folder / "biomes.json").write_text('{"biomes": {"demomod:glade": {"temperature": 0.5, "downfall": 0.9}}}')
    loader = ResourceLoader(jar)
    loader.addBlockPack(BlockPack(str(folder)))
    assert dict(loader.biomeDefinitions())["demomod:glade"]["downfall"] == 0.9


# --- Models ---

@pytest.fixture
def scene(library, tmp_path):
    """ A world with a block pack in use, and a function that loads and cooks its models. """
    writeDemoPack(library / "demo")
    world = libraryTests.moddedWorld(tmp_path, worlds.state("demomod:machine", mode="on"), demomod="1.0")
    editor = WorldEditor(str(world), readonly=True)
    loader = ResourceLoader(str(JAR))
    loader.addBlockPack(editor.adapter.packSelection.packs[0])
    loader.addZipFile(str(JAR))

    def cook():
        models = BlockModels(editor.blocktypes, loader)
        atlas = TextureAtlas(editor, loader, models, overrideMaxSize=8192)
        return models, atlas

    return editor, loader, cook


def modelOf(models, block):
    return models.hasModel(block.ID, block.meta), models.quadCount(block.ID, block.meta)


@needsJar
def testPackBlockUsesThePacksModelAndTexture(scene):
    editor, _, cook = scene
    models, atlas = cook()
    assert modelOf(models, editor.blocktypes["demomod:machine[mode=on]"]) == (True, 6)
    assert "assets/demomod/textures/block/machine.png" in atlas.texCoordsByName


@needsJar
def testPackBlockWithNoAssetsUsesItsTexture(scene):
    editor, _, cook = scene
    models, atlas = cook()
    assert modelOf(models, editor.blocktypes["demomod:bare[mode=off]"]) == (True, 6)
    assert "assets/minecraft/textures/block/gold_block.png" in atlas.texCoordsByName


@needsJar
def testModFluidIsDrawnAsABox(scene):
    editor, _, cook = scene
    models, _ = cook()
    oil = editor.blocktypes["demomod:oil[level=0]"]
    assert modelOf(models, oil) == (True, 6)
    assert editor.blocktypes.renderType[oil.ID] == 3


@needsJar
def testGameBlocksStillHaveTheirModels(scene):
    editor, _, cook = scene
    models, _ = cook()
    assert modelOf(models, editor.blocktypes["minecraft:stone"]) == (True, 6)


@needsJar
def testUnknownBlockGetsTheUnknownModel(scene):
    editor, _, cook = scene
    models, _ = cook()
    ID, meta = editor.blocktypes.idAndMeta("nobody:knows")
    assert not models.hasModel(ID, meta)


@needsJar
def testLateStateOfAKnownBlockGetsItsModelAtOnce(scene):
    editor, _, cook = scene
    models, _ = cook()
    ID, meta = editor.blocktypes.idAndMeta("minecraft:redstone_lamp", [("lit", "maybe")])
    assert models.hasModel(ID, meta)
    assert models.quadCount(ID, meta) == 6


@needsJar
def testDefinitionGivesAnUnknownBlockAModelAndTexture(scene):
    editor, _, cook = scene
    definition = BlockDefinition(defJson={"internalName": "somemod:copper_machine",
                                          "model": "minecraft:block/cube_all",
                                          "textures": {"all": "minecraft:block/copper_block"}})
    editor.blocktypes.applyBlockDefinitions([definition])
    models, atlas = cook()
    block = editor.blocktypes["somemod:copper_machine"]
    assert modelOf(models, block) == (True, 6)
    assert "assets/minecraft/textures/block/copper_block.png" in atlas.texCoordsByName
    assert not block.unknown


@needsJar
def testStateSeenAfterADefinitionUsesItsModel(scene):
    editor, _, cook = scene
    editor.blocktypes.applyBlockDefinitions([BlockDefinition(defJson={
        "internalName": "somemod:thing", "model": "minecraft:block/cube_all", "textures": {"all": "minecraft:block/dirt"}})])
    models, _ = cook()
    ID, meta = editor.blocktypes.idAndMeta("somemod:thing", [("powered", "true")])
    assert models.hasModel(ID, meta)
    assert models.quadCount(ID, meta) == 6


@needsJar
def testDefinitionWithTexturesInTheOldForm(scene):
    editor, _, cook = scene
    editor.blocktypes.applyBlockDefinitions([BlockDefinition(defJson={
        "internalName": "somemod:thing", "modelPath": "assets/minecraft/models/block/cube_all.json",
        "modelTextures": {"#all": "assets/minecraft/textures/block/dirt.png"}})])
    models, atlas = cook()
    assert modelOf(models, editor.blocktypes["somemod:thing"]) == (True, 6)
    assert "assets/minecraft/textures/block/dirt.png" in atlas.texCoordsByName


@needsJar
def testDefinitionCanRestyleAGameBlock(scene):
    editor, _, cook = scene
    models, _ = cook()
    before = models.quadCount(editor.blocktypes["minecraft:stone"].ID, 0)
    editor.blocktypes.applyBlockDefinitions([BlockDefinition(defJson={
        "internalName": "minecraft:stone", "model": "minecraft:block/carpet", "textures": {"wool": "minecraft:block/dirt"}})])
    models, atlas = cook()
    assert before == 6
    assert "assets/minecraft/textures/block/dirt.png" in atlas.texCoordsByName
    assert models.hasModel(editor.blocktypes["minecraft:stone"].ID, 0)


@needsJar
def testBiomeFromAPackGetsItsColors(scene):
    editor, loader, _ = scene
    loader.extraBiomes["demomod:glade"] = {"temperature": 0.5, "downfall": 0.5, "effects": {"water_color": 0x123456}}
    colors = modernBiomeColors(loader, editor.adapter)
    assert colors.water[editor.adapter.biomeID("demomod:glade")].tolist() == [0x56, 0x34, 0x12, 0xff]
