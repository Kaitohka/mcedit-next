"""
    pack_library_test

    Finding and choosing block packs, and the order in which block descriptions apply.
"""
import importlib.util
import shutil
import zipfile
from pathlib import Path

import numpy
import pytest

from mceditlib import nbt
from mceditlib.anvil.format_profile import MINECRAFT_1_21
from mceditlib.blocktypes import STATE_UNRESOLVED, pack_library
from mceditlib.blocktypes.block_pack import BlockPack, BlockPackError
from mceditlib.blocktypes.fallback_shapes import fallbackModel
from mceditlib.blocktypes.modern import ModernBlockTypeSet
from mceditlib.worldeditor import WorldEditor

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


packs = _load("block_pack_test")
worlds = _load("modern_world_test")


def machine(name="demomod:machine", **common):
    block = dict(packs.lever(), id=name, displayName="Machine", defaultState="mode=off",
                 descriptionId="block.demomod.machine",
                 properties={"mode": ["off", "on"]},
                 states=[{"state": "mode=off", "resourceVariant": "mode=off", "brightness": 0},
                         {"state": "mode=on", "resourceVariant": "mode=on", "brightness": 9}])
    block["common"] = packs.stateAttributes(resourcePath=name, opaqueCube=True, opacity=15,
                                            particleTexture="demomod:block/machine", **common)
    return block


def writePack(folder, blocks=None, dataVersion=3955, mods=None):
    packs.writePack(folder, blocks if blocks is not None else [machine()])
    manifest = __import__("json").loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["dataVersion"] = dataVersion
    if mods is not None:
        manifest["mods"] = mods
    (folder / "manifest.json").write_text(__import__("json").dumps(manifest), encoding="utf-8")
    return folder


@pytest.fixture
def library(tmp_path):
    folder = tmp_path / "blockpacks"
    folder.mkdir()
    pack_library.setPackFolders([str(folder)])
    yield folder
    pack_library.setPackFolders([])


class Definition(object):
    """ Has the attributes of mcedit2's BlockDefinition, which this library does not import. """

    def __init__(self, internalName, blockState="", modelPath=None, modelTextures=None, opacity=None, brightness=None,
                 englishName=None):
        self.internalName = internalName
        self.blockState = blockState
        self.modelPath = modelPath
        self.modelTextures = modelTextures or {}
        self.modelRotations = [0, 0]
        self.rotationFlags = []
        self.opacity = opacity
        self.brightness = brightness
        self.englishName = englishName or internalName


# --- Reading packs ---

def testPackFromAFolder(tmp_path):
    pack = BlockPack(str(writePack(tmp_path / "pack")))
    assert [b["id"] for b in pack.blocks] == ["demomod:machine"]
    assert pack.mods == {"demomod": None}


def zipFolder(folder, path, prefix=""):
    with zipfile.ZipFile(path, "w") as z:
        for file in folder.rglob("*"):
            if file.is_file():
                z.write(file, prefix + file.relative_to(folder).as_posix())
    return path


@pytest.mark.parametrize("prefix", ["", "my pack/"])
def testPackFromAZip(tmp_path, prefix):
    folder = writePack(tmp_path / "pack")
    (folder / "assets" / "demomod" / "blockstates").mkdir(parents=True)
    (folder / "assets" / "demomod" / "blockstates" / "machine.json").write_text("{}")
    pack = BlockPack(str(zipFolder(folder, tmp_path / "pack.zip", prefix)))
    assert pack.mods == {"demomod": None}
    assert "assets/demomod/blockstates/machine.json" in pack.files.namelist()
    assert pack.files.read("assets/demomod/blockstates/machine.json") == b"{}"
    assert pack.files.open("assets/demomod/blockstates/machine.json").read() == b"{}"


def testFolderFilesActLikeAZip(tmp_path):
    folder = writePack(tmp_path / "pack")
    (folder / "assets").mkdir()
    (folder / "assets" / "a.txt").write_text("hello")
    files = BlockPack(str(folder)).files
    assert "assets/a.txt" in files.namelist()
    assert files.open("assets/a.txt").read() == b"hello"
    with pytest.raises(KeyError):
        files.open("assets/none.txt")


def testFolderFilesKeepTheirListOfFiles(tmp_path):
    files = BlockPack(str(writePack(tmp_path / "pack"))).files
    assert files.read("manifest.json")
    with pytest.raises(KeyError):
        files.read("no_such_file.json")
    assert "manifest.json" in files.namelist()


def testZipThatIsNotAPackIsRefused(tmp_path):
    path = tmp_path / "other.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("readme.txt", "hello")
    with pytest.raises(BlockPackError):
        BlockPack(str(path))


def testManifestModsComeBeforeNamespaces(tmp_path):
    pack = BlockPack(str(writePack(tmp_path / "pack", mods=[{"id": "demomod", "version": "1.2"},
                                                            {"id": "minecraft", "version": "1.21.1"}])))
    assert pack.mods == {"demomod": "1.2"}


def testPackWithOnlyTheGamesBlocksHasNoMods(tmp_path):
    pack = BlockPack(str(writePack(tmp_path / "pack", [packs.lever()])))
    assert pack.mods == {}


def testBiomesAreOptional(tmp_path):
    folder = writePack(tmp_path / "pack")
    assert BlockPack(str(folder)).biomes == {}
    (folder / "biomes.json").write_text('{"biomes": {"demomod:glade": {"temperature": 0.5}}}')
    assert BlockPack(str(folder)).biomes == {"demomod:glade": {"temperature": 0.5}}


# --- Choosing packs ---

def levelTagWithMods(**mods):
    tag = nbt.TAG_Compound()
    tag["fml"] = nbt.TAG_Compound()
    entries = []
    for name, version in mods.items():
        entry = nbt.TAG_Compound()
        entry["ModId"] = nbt.TAG_String(name)
        entry["ModVersion"] = nbt.TAG_String(version)
        entries.append(entry)
    tag["fml"]["LoadingModList"] = nbt.TAG_List(entries)
    return tag


def testWorldModsAreRead():
    assert pack_library.worldMods(levelTagWithMods(demomod="1.0", neoforge="21.1")) == {"demomod": "1.0", "neoforge": "21.1"}


def testWorldThatDoesNotSayHasUnknownMods():
    assert pack_library.worldMods(nbt.TAG_Compound()) is None


def names(selection):
    return [Path(pack.path).name for pack in selection.packs]


def testPackForAModInTheWorldIsChosen(library):
    writePack(library / "demo")
    selection = pack_library.selectPacks({"demomod": "1.0"}, MINECRAFT_1_21)
    assert names(selection) == ["demo"]
    assert selection.missingMods == []


def testPackForOtherModsIsNotChosen(library):
    writePack(library / "demo")
    selection = pack_library.selectPacks({"othermod": "1.0"}, MINECRAFT_1_21)
    assert names(selection) == []
    assert selection.missingMods == ["othermod"]


def testPackThatCoversMoreModsComesFirst(library):
    writePack(library / "aaa", [machine("mod1:a")])
    writePack(library / "bbb", [machine("mod1:a"), machine("mod2:b")])
    selection = pack_library.selectPacks({"mod1": "1", "mod2": "1", "mod3": "1"}, MINECRAFT_1_21)
    assert names(selection)[0] == "bbb"


def testPackThatAddsNothingIsLeftOut(library):
    writePack(library / "aaa", [machine("mod1:a")])
    writePack(library / "bbb", [machine("mod1:a"), machine("mod2:b")])
    assert names(pack_library.selectPacks({"mod1": "1", "mod2": "1"}, MINECRAFT_1_21)) == ["bbb"]


def testPacksThatEachAddSomethingAreBothUsed(library):
    writePack(library / "aaa", [machine("mod1:a")])
    writePack(library / "bbb", [machine("mod2:a"), machine("mod3:b")])
    assert names(pack_library.selectPacks({"mod1": "1", "mod2": "1", "mod3": "1"}, MINECRAFT_1_21)) == ["bbb", "aaa"]


def testNewestExportWins(library):
    for name, time in ("older", "2026-01-01T00:00:00Z"), ("newer", "2026-06-01T00:00:00Z"), ("oldest", "2025-01-01T00:00:00Z"):
        folder = writePack(library / name, [machine("mod1:a")])
        manifest = __import__("json").loads((folder / "manifest.json").read_text(encoding="utf-8"))
        manifest["generatedAt"] = time
        (folder / "manifest.json").write_text(__import__("json").dumps(manifest), encoding="utf-8")
    assert names(pack_library.selectPacks({"mod1": "1"}, MINECRAFT_1_21)) == ["newer"]
    assert names(pack_library.selectPacks(None, MINECRAFT_1_21)) == ["newer"]


def testExportProblemsAreNoted(library):
    folder = writePack(library / "demo")
    (folder / "report.json").write_text('{"problems": ["one", "two"]}')
    assert pack_library.selectPacks({"demomod": "1"}, MINECRAFT_1_21).reports == [("demo", 2)]


def testGameAndLoaderAreNotMissingMods(library):
    selection = pack_library.selectPacks({"minecraft": "1.21.1", "neoforge": "21.1"}, MINECRAFT_1_21)
    assert selection.missingMods == []


def testPackIsNotUsedForAnotherGameVersion(library):
    writePack(library / "old", dataVersion=3700)
    selection = pack_library.selectPacks({"demomod": "1.0"}, MINECRAFT_1_21)
    assert names(selection) == []
    assert "old" in selection.problems[0]
    assert selection.missingMods == ["demomod"]


def testVersionDifferenceIsNotedButThePackIsUsed(library):
    writePack(library / "demo", mods=[{"id": "demomod", "version": "1.0"}])
    selection = pack_library.selectPacks({"demomod": "2.0"}, MINECRAFT_1_21)
    assert names(selection) == ["demo"]
    assert selection.versionMismatches == [("demomod", "2.0", "1.0")]


def testWhenTheWorldDoesNotSayAnyPackMayBeOfUse(library):
    writePack(library / "demo")
    assert names(pack_library.selectPacks(None, MINECRAFT_1_21)) == ["demo"]


def testBrokenPackIsReportedAndSkipped(library):
    (library / "broken").mkdir()
    writePack(library / "demo")
    selection = pack_library.selectPacks({"demomod": "1"}, MINECRAFT_1_21)
    assert names(selection) == ["demo"]
    assert "broken" in selection.problems[0]


def testZipInTheLibraryIsFound(library, tmp_path):
    zipFolder(writePack(tmp_path / "pack"), library / "demo.zip")
    assert names(pack_library.selectPacks({"demomod": "1"}, MINECRAFT_1_21)) == ["demo.zip"]


def testOtherFilesInTheLibraryAreIgnored(library):
    (library / "notes.txt").write_text("hello")
    assert pack_library.selectPacks(None, MINECRAFT_1_21).problems == []


def testPackChangedOnDiskIsReadAgain(library):
    folder = writePack(library / "demo", [machine("mod1:a")])
    assert names(pack_library.selectPacks({"mod1": "1"}, MINECRAFT_1_21)) == ["demo"]
    shutil.rmtree(folder)
    assert names(pack_library.selectPacks({"mod1": "1"}, MINECRAFT_1_21)) == []


# --- Order of descriptions ---

@pytest.fixture
def demoPack(tmp_path):
    return BlockPack(str(writePack(tmp_path / "pack")))


def testPackBlockUsesItsAttributes(demoPack):
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21, [demoPack])
    on = blocktypes["demomod:machine[mode=on]"]
    off = blocktypes["demomod:machine[mode=off]"]
    assert on.displayName == "Machine"
    assert not on.unknown
    assert blocktypes.brightness[on.ID] == 9
    assert blocktypes.brightness[off.ID] == 0
    assert on.resourcePath == "demomod:machine"


def testFirstPackToHaveABlockWins(tmp_path):
    first = BlockPack(str(writePack(tmp_path / "first", [machine(particleTexture_="x")])))
    other = machine()
    other["displayName"] = "Other Machine"
    second = BlockPack(str(writePack(tmp_path / "second", [other])))
    assert ModernBlockTypeSet(MINECRAFT_1_21, [first, second])["demomod:machine[mode=on]"].displayName == "Machine"
    assert ModernBlockTypeSet(MINECRAFT_1_21, [second, first])["demomod:machine[mode=on]"].displayName == "Other Machine"


def testPackCanReplaceAGameBlock(tmp_path):
    stone = machine("minecraft:stone")
    stone["displayName"] = "Modded Stone"
    stone["properties"] = {}
    stone["defaultState"] = ""
    stone["states"] = [{"state": "", "resourceVariant": ""}]
    pack = BlockPack(str(writePack(tmp_path / "pack", [stone])))
    assert ModernBlockTypeSet(MINECRAFT_1_21, [pack])["minecraft:stone"].displayName == "Modded Stone"


def testGameBlocksStayWithAPack(demoPack):
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21, [demoPack])
    assert blocktypes["minecraft:stone"].displayName == "Stone"
    assert not blocktypes["minecraft:air"]


def testStatesOfAPackBlockMustMatch(demoPack):
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21, [demoPack])
    assert not blocktypes.stateFlags[blocktypes["demomod:machine[mode=on]"].ID * 16] & STATE_UNRESOLVED


def testUnknownStateOfAKnownBlockLooksLikeItsDefault(demoPack):
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21, [demoPack])
    ID, meta = blocktypes.idAndMeta("demomod:machine", [("mode", "turbo")])
    block = blocktypes[ID, meta]
    assert block.blockState == "[mode=turbo]"
    assert block.internalName == "demomod:machine"
    assert block.resourcePath == "demomod:machine"
    assert not block.unknown
    assert block.displayName == "Machine"
    assert blocktypes.stateFlags[ID * 16 + meta] & STATE_UNRESOLVED
    assert blocktypes.opaqueCube[ID] == 1


def testUnknownStateAsksForTheDefaultsModel(demoPack):
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21, [demoPack])
    calls = []
    blocktypes.modelForLateState = lambda *args: calls.append(args)
    ID, meta = blocktypes.idAndMeta("demomod:machine", [("mode", "turbo")])
    assert calls == [(ID, meta) + tuple(blocktypes.IDsByState["demomod:machine[mode=off]"])]


def testWhollyUnknownBlockIsAQuestionMark():
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21)
    calls = []
    blocktypes.modelForLateState = lambda *args: calls.append(args)
    ID, meta = blocktypes.idAndMeta("somemod:gadget", [("n", "1")])
    block = blocktypes[ID, meta]
    assert block.unknown
    assert block.nameAndState == "somemod:gadget[n=1]"
    assert calls == []
    # and so is a further state of that block
    ID, meta = blocktypes.idAndMeta("somemod:gadget", [("n", "2")])
    assert blocktypes[ID, meta].unknown
    assert blocktypes[ID, meta].blockState == "[n=2]"


# --- User definitions ---

CUBE = "assets/minecraft/models/block/cube_all.json"


def testDefinitionComesFirst(demoPack):
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21, [demoPack])
    blocktypes.applyBlockDefinitions([Definition("demomod:machine", modelPath=CUBE, modelTextures={"all": "x:block/y"},
                                                 englishName="My Machine")])
    block = blocktypes["demomod:machine[mode=on]"]
    assert block.forcedModel == CUBE
    assert block.forcedModelTextures == {"all": "x:block/y"}
    assert block.displayName == "My Machine"
    # The state of a block keeps its name, however it looks.
    assert block.nameAndState == "demomod:machine[mode=on]"


def testDefinitionCanNameABlockNotSeenYet():
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21)
    blocktypes.applyBlockDefinitions([Definition("somemod:thing", modelPath=CUBE)])
    block = blocktypes["somemod:thing"]
    assert block.forcedModel == CUBE
    assert not block.unknown


def testStatesSeenAfterADefinitionUseIt():
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21)
    blocktypes.applyBlockDefinitions([Definition("somemod:thing", modelPath=CUBE)])
    ID, meta = blocktypes.idAndMeta("somemod:thing", [("n", "1")])
    assert blocktypes[ID, meta].forcedModel == CUBE
    assert blocktypes[ID, meta].blockState == "[n=1]"
    assert not blocktypes[ID, meta].unknown


def testDefinitionOfOneState(demoPack):
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21, [demoPack])
    blocktypes.applyBlockDefinitions([Definition("demomod:machine", "[mode=on]", modelPath=CUBE)])
    assert blocktypes["demomod:machine[mode=on]"].forcedModel == CUBE
    assert blocktypes["demomod:machine[mode=off]"].forcedModel is None


def testDefinitionWithoutBracketsAroundItsState(demoPack):
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21, [demoPack])
    blocktypes.applyBlockDefinitions([Definition("demomod:machine", "mode=on", modelPath=CUBE)])
    assert blocktypes["demomod:machine[mode=on]"].forcedModel == CUBE


def testDefinitionWithoutANamespaceMeansTheGame():
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21)
    blocktypes.applyBlockDefinitions([Definition("stone", modelPath=CUBE)])
    assert blocktypes["minecraft:stone"].forcedModel == CUBE


def testOpacityAndBrightnessTakeEffect():
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21)
    blocktypes.applyBlockDefinitions([Definition("somemod:lamp", modelPath=CUBE, opacity=0, brightness=12)])
    ID = blocktypes["somemod:lamp"].ID
    assert blocktypes.opacity[ID] == 0
    assert blocktypes.brightness[ID] == 12
    assert blocktypes.opaqueCube[ID] == 0


def testDefinitionWithoutLightValuesLeavesThemAlone():
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21)
    glowstone = blocktypes["minecraft:glowstone"].ID
    blocktypes.applyBlockDefinitions([Definition("minecraft:glowstone", modelPath=CUBE)])
    assert blocktypes.brightness[glowstone] == 15


def testRemovingADefinitionRestoresTheBlock(demoPack):
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21, [demoPack])
    ID = blocktypes["demomod:machine[mode=on]"].ID
    blocktypes.applyBlockDefinitions([Definition("demomod:machine", modelPath=CUBE, opacity=3, brightness=1, englishName="X")])
    assert blocktypes.opacity[ID] == 3
    late, lateMeta = blocktypes.idAndMeta("demomod:machine", [("mode", "turbo")])
    assert blocktypes[late, lateMeta].forcedModel == CUBE

    blocktypes.applyBlockDefinitions([])
    block = blocktypes["demomod:machine[mode=on]"]
    assert block.forcedModel is None
    assert block.displayName == "Machine"
    assert blocktypes.opacity[ID] == 15
    assert blocktypes.brightness[ID] == 9
    assert blocktypes[late, lateMeta].forcedModel is None


def testDefinedBlockGoesBackToUnknownWhenRemoved():
    blocktypes = ModernBlockTypeSet(MINECRAFT_1_21)
    blocktypes.applyBlockDefinitions([Definition("somemod:thing", modelPath=CUBE)])
    assert not blocktypes["somemod:thing"].unknown
    blocktypes.applyBlockDefinitions([])
    assert blocktypes["somemod:thing"].unknown


# --- Worlds with mod blocks ---

def moddedWorld(tmp_path, *paletteEntries, **fml):
    world = tmp_path / "world"
    world.mkdir()
    worlds.writeLevelDat(world)
    if fml:
        tag = nbt.load(str(world / "level.dat"))
        tag["fml"] = levelTagWithMods(**fml)["fml"]
        (world / "level.dat").write_bytes(tag.save())
    palette = [worlds.state("minecraft:stone")] + list(paletteEntries)
    indices = numpy.arange(4096) % len(palette)
    section = worlds.makeSection(0, palette, indices)
    worlds.writeRegion(world / "region" / "r.0.0.mca", {(0, 0): worlds.makeChunk(0, 0, [section])})
    return world


def testWorldUsesThePackForItsMod(library, tmp_path):
    writePack(library / "demo")
    world = moddedWorld(tmp_path, worlds.state("demomod:machine", mode="on"), demomod="1.0")
    editor = WorldEditor(str(world), readonly=True)
    block = editor.getDimension().getBlock(1, 0, 0)
    assert block.nameAndState == "demomod:machine[mode=on]"
    assert block.displayName == "Machine"
    assert not block.unknown
    assert names(editor.adapter.packSelection) == ["demo"]
    assert editor.blocktypes.unresolvedBlockReport() == []


def testWorldThatIsOnlyVanillaIgnoresPacks(library, tmp_path):
    writePack(library / "demo")
    world = moddedWorld(tmp_path, minecraft="1.21.1", neoforge="21.1")
    editor = WorldEditor(str(world), readonly=True)
    assert names(editor.adapter.packSelection) == []
    assert "demomod:machine" not in editor.blocktypes.IDsByName


def testReportCountsUnknownBlocks(library, tmp_path):
    world = moddedWorld(tmp_path, worlds.state("somemod:gadget", n="1"), worlds.state("somemod:gadget", n="2"),
                        worlds.state("othermod:thing"))
    editor = WorldEditor(str(world), readonly=True)
    chunk = editor.getDimension().getChunk(0, 0)
    chunk.getSection(0)
    report = editor.blocktypes.unresolvedBlockReport()
    assert [(entry["internalName"], entry["blocks"]) for entry in report] == [("somemod:gadget", 2048), ("othermod:thing", 1024)]
    assert report[0]["states"] == ["[n=1]", "[n=2]"]
    assert not report[0]["knownBlock"]


def testReportCountsASectionOnce(library, tmp_path):
    world = moddedWorld(tmp_path, worlds.state("somemod:gadget"))
    editor = WorldEditor(str(world), readonly=True)
    chunk = editor.getDimension().getChunk(0, 0)
    chunk.getSection(0)
    chunk.chunkData._sections.clear()
    chunk.getSection(0)
    assert editor.blocktypes.unresolvedBlockReport()[0]["blocks"] == 2048


def testReportNotesKnownBlocksWithUnknownStates(library, tmp_path):
    world = moddedWorld(tmp_path, worlds.state("minecraft:redstone_lamp", lit="maybe"))
    editor = WorldEditor(str(world), readonly=True)
    editor.getDimension().getChunk(0, 0).getSection(0)
    entry = editor.blocktypes.unresolvedBlockReport()[0]
    assert entry["internalName"] == "minecraft:redstone_lamp"
    assert entry["knownBlock"]
    assert entry["states"] == ["[lit=maybe]"]


def testDefinedBlockLeavesTheReport(library, tmp_path):
    world = moddedWorld(tmp_path, worlds.state("somemod:gadget"))
    editor = WorldEditor(str(world), readonly=True)
    editor.getDimension().getChunk(0, 0).getSection(0)
    editor.blocktypes.applyBlockDefinitions([Definition("somemod:gadget", modelPath=CUBE)])
    assert editor.blocktypes.unresolvedBlockReport() == []


def testSingleEntryPaletteIsCounted(library, tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    worlds.writeLevelDat(world)
    section = worlds.makeSection(0, [worlds.state("somemod:gadget")])
    worlds.writeRegion(world / "region" / "r.0.0.mca", {(0, 0): worlds.makeChunk(0, 0, [section])})
    editor = WorldEditor(str(world), readonly=True)
    editor.getDimension().getChunk(0, 0).getSection(0)
    assert editor.blocktypes.unresolvedBlockReport()[0]["blocks"] == 4096


# --- Fluids from mods ---

def testModFluidGetsABox():
    model = fallbackModel({"internalName": "demomod:oil", "blockState": "[level=0]", "renderType": 1,
                           "particleTexture": "demomod:block/oil"})
    assert model["model"]["textures"]["all"] == "demomod:block/oil"
    assert model["model"]["elements"][0]["to"] == [16, 14, 16]


def testGameFluidsNeedNoBox():
    assert fallbackModel({"internalName": "minecraft:water", "blockState": "[level=0]", "renderType": 1}) is None
