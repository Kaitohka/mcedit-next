"""
    exporter_pipeline_test

    Reads a world and a block pack that the game itself wrote, with the exporter and its test mod. This is
    the whole way from a mod in the game to a block in MCEdit. To make them, see exporter/README.md.
"""
import shutil
from pathlib import Path

import pytest

from mcedit2.rendering.blockmodels import BlockModels
from mcedit2.rendering.textureatlas import TextureAtlas
from mcedit2.resourceloader import ResourceLoader
from mceditlib.blocktypes import STATE_UNRESOLVED, pack_library
from mceditlib.blocktypes.block_pack import BlockPack
from mceditlib.worldeditor import WorldEditor

ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT / "worlds_local" / "mcedit_modded_121"
PACK = ROOT / "worlds_local" / "testmod_pack"
JAR = ROOT / "MCEdit 2 Files" / "versionCache" / "1.21.1.jar"

pytestmark = pytest.mark.skipif(not (WORLD.is_dir() and PACK.is_dir() and JAR.is_file()),
                                reason="Needs a world and a block pack from the game, and its client jar")

TEST_MOD = "mcedit_testmod:"
POSITIONS_BY_REGISTRY_ORDER = 2  # the generator places one block every two along X


@pytest.fixture(scope="module")
def pack():
    return BlockPack(str(PACK))


@pytest.fixture(scope="module")
def editor(tmp_path_factory):
    folder = tmp_path_factory.mktemp("pipeline")
    library = folder / "blockpacks"
    library.mkdir()
    shutil.copytree(PACK, library / "testmod_pack")
    shutil.copytree(WORLD, folder / "world")
    pack_library.setPackFolders([str(library)])
    worldEditor = WorldEditor(str(folder / "world"), readonly=True)
    yield worldEditor
    worldEditor.close()
    pack_library.setPackFolders([])


@pytest.fixture(scope="module")
def cooked(editor):
    loader = ResourceLoader(str(JAR))
    for selected in editor.adapter.packSelection.packs:
        loader.addBlockPack(selected)
    loader.addZipFile(str(JAR))
    models = BlockModels(editor.blocktypes, loader)
    atlas = TextureAtlas(editor, loader, models, overrideMaxSize=8192)
    return models, atlas


def attributesOf(pack, name):
    return {state["internalName"] + state["blockState"]: state for state in pack.states()}[name]


# --- What the exporter wrote ---

def testPackDescribesTheGameThatMadeIt(pack):
    assert pack.manifest["dataVersion"] == 3955
    assert pack.manifest["loader"] == "neoforge"
    assert pack.mods["mcedit_testmod"] == "1.0"
    assert pack.manifest["counts"]["blocks"] == 8


def testOnlyBlocksFromModsAreInThePack(pack):
    assert all(block["id"].startswith(TEST_MOD) for block in pack.blocks)


@pytest.mark.parametrize("name, source", [
    ("plain", "json"), ("oriented[facing=north]", "json"), ("fragile", "json"), ("obj_model", "json"),
    # NeoForge's composite loader makes a model of other models, which MCEdit draws.
    ("loader_model", "json"),
    # Drawn by code, so the export writes the faces the game drew into a model of their own.
    ("code_model", "generated"), ("render_only", "generated"),
    ("no_assets", "fallback"),
])
def testBlocksAreSortedByHowTheyCanBeDrawn(pack, name, source):
    assert attributesOf(pack, TEST_MOD + name)["modelSource"] == source


def testOnlyFilesInUseAreCopied(pack):
    names = pack.files.namelist()
    assert "assets/mcedit_testmod/textures/block/plain.png" in names
    assert "assets/mcedit_testmod/textures/block/plain.png.mcmeta" in names
    assert "assets/mcedit_testmod/models/block/oriented.json" in names
    assert "assets/mcedit_testmod/textures/block/unused.png" not in names


def testFilesFromTheGameAreNotCopied(pack):
    assert not [name for name in pack.files.namelist() if name.startswith("assets/minecraft/")]


def testFacesTheGameDrewAreWrittenOut(pack):
    names = pack.files.namelist()
    codeModel = attributesOf(pack, TEST_MOD + "code_model")["generatedModel"]
    assert codeModel.startswith("mcedit_testmod:mcedit_generated/code_model/")
    assert "assets/mcedit_testmod/models/%s.json" % codeModel.partition(":")[2] in names
    # The renderer draws with a texture of its own, as entity models do, and with one from the block atlas.
    assert "assets/mcedit_testmod/textures/entity/render_only_box.png" in names
    assert attributesOf(pack, TEST_MOD + "render_only")["renderType"] == 3
    assert "generatedModel" not in attributesOf(pack, TEST_MOD + "plain")


def testParticleTextureOfABlockWithNoDrawnModelIsCopied(pack):
    assert attributesOf(pack, TEST_MOD + "render_only")["particleTexture"] == "mcedit_testmod:block/render_only"
    assert "assets/mcedit_testmod/textures/block/render_only.png" in pack.files.namelist()


def testFailuresAreReportedAndDoNotStopTheExport(pack):
    assert any("fragile: could not read opacity" in problem for problem in pack.problems)
    assert any("fragile_entity: could not read its default NBT" in problem for problem in pack.problems)
    fragile = attributesOf(pack, TEST_MOD + "fragile")
    assert fragile["opacity"] == 15
    assert fragile["collidable"] is True


def testBlockEntities(pack):
    entities = {entity["id"]: entity for entity in pack.blockEntities}
    renderOnly = entities[TEST_MOD + "render_only"]
    assert renderOnly["blocks"] == [TEST_MOD + "render_only"]
    assert renderOnly["hasRenderer"] is True
    assert "charge:7" in renderOnly["defaultNbt"]
    assert "defaultNbt" not in entities[TEST_MOD + "fragile_entity"]


# --- What MCEdit does with it ---

def testWorldChoosesThePack(editor):
    selection = editor.adapter.packSelection
    assert [Path(selected.path).name for selected in selection.packs] == ["testmod_pack"]
    assert selection.missingMods == []
    assert selection.reports == [("testmod_pack", 4)]


def testWorldListsItsMods(editor):
    assert "mcedit_testmod" in pack_library.worldMods(editor.adapter.metadata.metadataTag)


@pytest.mark.parametrize("name", ["plain", "oriented", "render_only", "loader_model", "obj_model", "code_model",
                                  "fragile", "no_assets"])
def testEveryBlockInTheWorldIsDescribed(editor, name):
    found = set()
    dim = editor.getDimension()
    for x in range(0, 16, POSITIONS_BY_REGISTRY_ORDER):
        block = dim.getBlock(x, 210, 0)
        found.add(block.internalName)
        assert not block.unknown
        assert not editor.blocktypes.stateFlags[block.ID * 16 + block.meta] & STATE_UNRESOLVED
    assert TEST_MOD + name in found


def testNothingInTheWorldIsUnresolved(editor):
    chunk = editor.getDimension().getChunk(0, 0)
    for cy in chunk.sectionPositions():
        chunk.getSection(cy)
    assert editor.blocktypes.unresolvedBlockReport() == []


def testBlockWithAModelFileIsDrawnFromIt(editor, cooked):
    models, atlas = cooked
    block = editor.blocktypes[TEST_MOD + "plain"]
    assert models.hasModel(block.ID, block.meta)
    assert models.quadCount(block.ID, block.meta) == 6
    assert "assets/mcedit_testmod/textures/block/plain.png" in atlas.texCoordsByName


def testEveryStateOfATurningBlockHasAModel(editor, cooked):
    models, _ = cooked
    for facing in "north", "south", "east", "west":
        block = editor.blocktypes["%soriented[facing=%s]" % (TEST_MOD, facing)]
        assert models.hasModel(block.ID, block.meta)
        assert models.quadCount(block.ID, block.meta) == 6


def testRendererOnlyBlockIsDrawnAsItsRendererDrawsIt(editor, cooked):
    models, atlas = cooked
    block = editor.blocktypes[TEST_MOD + "render_only"]
    assert models.hasModel(block.ID, block.meta)
    assert models.quadCount(block.ID, block.meta) == 2  # the top of a box, and a plane on the north side
    assert "assets/mcedit_testmod/textures/entity/render_only_box.png" in atlas.texCoordsByName
    assert "assets/mcedit_testmod/textures/block/render_only.png" in atlas.texCoordsByName
    assert editor.blocktypes.renderType[block.ID] == 3


def testCodeModelBlockIsDrawnFromTheFacesTheGameMade(editor, cooked):
    models, atlas = cooked
    block = editor.blocktypes[TEST_MOD + "code_model"]
    assert models.hasModel(block.ID, block.meta)
    assert models.quadCount(block.ID, block.meta) == 2  # a slope, and the bottom under it
    assert "assets/mcedit_testmod/textures/block/code_model.png" in atlas.texCoordsByName


def testCustomLoaderBlockIsACubeWithItsTexture(editor, cooked):
    models, atlas = cooked
    block = editor.blocktypes[TEST_MOD + "loader_model"]
    assert models.hasModel(block.ID, block.meta)
    assert models.quadCount(block.ID, block.meta) == 6
    assert "assets/mcedit_testmod/textures/block/loader_model.png" in atlas.texCoordsByName


def testOBJModelBlockIsDrawnFromItsFaces(editor, cooked, pack):
    models, atlas = cooked
    block = editor.blocktypes[TEST_MOD + "obj_model"]
    assert models.hasModel(block.ID, block.meta)
    assert models.quadCount(block.ID, block.meta) == 5  # a pyramid: its base, and four triangles
    assert "assets/mcedit_testmod/textures/block/obj_model.png" in atlas.texCoordsByName
    names = pack.files.namelist()
    assert "assets/mcedit_testmod/models/block/obj_model.obj" in names
    assert "assets/mcedit_testmod/models/block/obj_model.mtl" in names


def testBlockWithNoAssetsIsAQuestionMarkButKeepsItsDetails(editor, cooked):
    models, _ = cooked
    block = editor.blocktypes[TEST_MOD + "no_assets"]
    assert not models.hasModel(block.ID, block.meta)
    assert block.displayName
    assert not block.unknown
