"""
    legacy_packs_test

    Block packs for worlds from 1.12.2 and earlier, which keep blocks by number: the numbers come from what Forge
    wrote in level.dat, and the states and the rest from a pack the exporter made in the same game.
"""
import gzip
import json

import pytest

from mceditlib import nbt
from mceditlib.blocktypes import PCBlockTypeSet, legacy_packs, pack_library
from mceditlib.blocktypes.block_pack import BlockPack

LAMP = {
    "id": "testmod:lamp",
    "displayName": "Test Lamp",
    "descriptionId": "tile.testmod.lamp",
    "defaultState": "facing=north,lit=false",
    "properties": {"facing": ["north", "south"], "lit": ["false", "true"]},
    "common": {"opaqueCube": True, "opacity": 15, "renderType": 3, "resourcePath": "testmod:lamp",
               "hasBlockstateFile": True, "modelSource": "json"},
    "states": [
        {"state": "facing=north,lit=false", "meta": 0, "brightness": 0, "resourceVariant": "facing=north,lit=false"},
        {"state": "facing=north,lit=true", "meta": 1, "brightness": 15, "resourceVariant": "facing=north,lit=true"},
        {"state": "facing=south,lit=false", "meta": 2, "brightness": 0, "resourceVariant": "facing=south,lit=false"},
        {"state": "facing=south,lit=true", "meta": 3, "brightness": 15, "resourceVariant": "facing=south,lit=true"},
    ],
    "legacy": {"numericId": 512, "metaStates": {"0": "facing=north,lit=false", "1": "facing=north,lit=true",
                                                "2": "facing=south,lit=false", "3": "facing=south,lit=true"}},
}


def writePack(folder, blocks, dataVersion=1343, minecraftVersion="1.12.2"):
    folder.mkdir(parents=True)
    with gzip.open(folder / "blocks.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"blocks": blocks}, f)
    (folder / "block_entities.json").write_text(json.dumps({"blockEntities": []}), encoding="utf-8")
    (folder / "report.json").write_text(json.dumps({"problems": []}), encoding="utf-8")
    manifest = {"schemaVersion": 1, "minecraftVersion": minecraftVersion, "loader": "forge",
                "mods": [{"id": "testmod", "version": "1.0"}]}
    if dataVersion:
        manifest["dataVersion"] = dataVersion
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return BlockPack(str(folder))


def levelTag(blocks, dataVersion=1343, itemData=False):
    root = nbt.TAG_Compound()
    data = root["Data"] = nbt.TAG_Compound()
    if dataVersion:
        data["DataVersion"] = nbt.TAG_Int(dataVersion)
    fml = root["FML"] = nbt.TAG_Compound()
    entries = nbt.TAG_List(list_type=nbt.ID_COMPOUND)
    for name, ID in blocks.items():
        entry = nbt.TAG_Compound()
        entry["K"] = nbt.TAG_String(("\x01" + name) if itemData else name)
        entry["V"] = nbt.TAG_Int(ID)
        entries.append(entry)
    if itemData:
        fml["ItemData"] = entries
    else:
        registries = fml["Registries"] = nbt.TAG_Compound()
        registries["minecraft:blocks"] = nbt.TAG_Compound()
        registries["minecraft:blocks"]["ids"] = entries
    mods = fml["ModList"] = nbt.TAG_List(list_type=nbt.ID_COMPOUND)
    for modID in ("minecraft", "FML", "testmod"):
        mod = nbt.TAG_Compound()
        mod["ModId"] = nbt.TAG_String(modID)
        mod["ModVersion"] = nbt.TAG_String("1.0")
        mods.append(mod)
    return root


@pytest.mark.parametrize("itemData", [False, True])
def testForgeNumbersAreRead(itemData):
    tag = levelTag({"testmod:lamp": 300, "minecraft:stone": 1}, itemData=itemData)
    assert legacy_packs.fmlBlockIDs(tag) == {"testmod:lamp": 300, "minecraft:stone": 1}
    assert legacy_packs.fmlMods(tag) == {"minecraft": "1.0", "FML": "1.0", "testmod": "1.0"}


def testWorldNumbersAndDataValuesGetThePacksStates(tmp_path):
    pack = writePack(tmp_path / "pack", [LAMP])
    types = PCBlockTypeSet()
    # The world's number for the block, not the one in the game that exported it.
    assert legacy_packs.applyLegacyPacks(types, [pack], {"testmod:lamp": 300}) == 1
    assert types.statesByID[300, 1] == "testmod:lamp[facing=north,lit=true]"
    assert types.statesByID[300, 3] == "testmod:lamp[facing=south,lit=true]"
    lit = types[300, 1]
    assert lit.internalName == "testmod:lamp"
    assert lit.displayName == "Test Lamp"
    assert lit.resourcePath == "testmod:lamp"
    assert types["testmod:lamp[facing=south,lit=false]"].ID == 300
    assert types["testmod:lamp[facing=south,lit=false]"].meta == 2
    assert types.brightness[300] in (0, 15)


def testThePacksBlockTakesTheNumberFromTheGamesOwn(tmp_path):
    pack = writePack(tmp_path / "pack", [LAMP])
    types = PCBlockTypeSet()
    assert types.statesByID[1, 0].startswith("minecraft:stone")
    legacy_packs.applyLegacyPacks(types, [pack], {"testmod:lamp": 1})
    assert types.statesByID[1, 0] == "testmod:lamp[facing=north,lit=false]"
    assert not any(state.startswith("minecraft:stone") for (ID, meta), state in types.statesByID.items() if ID == 1)


def testDataValuesThePackDoesNotListAreNotGuessed(tmp_path):
    pack = writePack(tmp_path / "pack", [LAMP])
    types = PCBlockTypeSet()
    legacy_packs.applyLegacyPacks(types, [pack], {"testmod:lamp": 300})
    assert (300, 7) not in types.statesByID


def testDataValuesReadAsAnotherStateLookLikeIt(tmp_path):
    lamp = json.loads(json.dumps(LAMP))
    lamp["legacy"]["metaStates"]["5"] = "facing=north,lit=true"
    pack = writePack(tmp_path / "pack", [lamp])
    types = PCBlockTypeSet()
    legacy_packs.applyLegacyPacks(types, [pack], {"testmod:lamp": 300})
    assert types.statesByID[300, 5] == "testmod:lamp[facing=north,lit=true]"
    assert types.metaAliases[300, 5] == 1
    assert (300, 1) not in types.metaAliases
    assert types["testmod:lamp[facing=north,lit=true]"].meta == 1


def testBlocksNotInTheWorldAndTheGamesOwnAreLeftAlone(tmp_path):
    stone = dict(LAMP, id="minecraft:stone")
    pack = writePack(tmp_path / "pack", [LAMP, stone])
    types = PCBlockTypeSet()
    before = types.statesByID[1, 0]
    assert legacy_packs.applyLegacyPacks(types, [pack], {"minecraft:stone": 1}) == 0
    assert types.statesByID[1, 0] == before


def testPacksAreChosenByTheWorldsVersion(tmp_path, monkeypatch):
    writePack(tmp_path / "packs" / "new", [LAMP], dataVersion=1343)
    writePack(tmp_path / "packs" / "old", [LAMP], dataVersion=0, minecraftVersion="1.7.10")
    monkeypatch.setattr(pack_library, "_packFolders", [str(tmp_path / "packs")])
    newWorld = legacy_packs.selectLegacyPacks(levelTag({"testmod:lamp": 300}, dataVersion=1343))
    oldWorld = legacy_packs.selectLegacyPacks(levelTag({"testmod:lamp": 300}, dataVersion=0, itemData=True))
    assert [p.manifest["minecraftVersion"] for p in newWorld.packs] == ["1.12.2"]
    assert [p.manifest["minecraftVersion"] for p in oldWorld.packs] == ["1.7.10"]


def testWorldsFrom1_7And1_8AreToldApartWithoutDataVersions(tmp_path, monkeypatch):
    # Data versions began with 1.9. Forge for 1.7.10 keeps its numbers in ItemData, and from 1.8 in Registries.
    writePack(tmp_path / "packs" / "a", [LAMP], dataVersion=0, minecraftVersion="1.7.10")
    writePack(tmp_path / "packs" / "b", [LAMP], dataVersion=0, minecraftVersion="1.8.9")
    writePack(tmp_path / "packs" / "c", [LAMP], dataVersion=184, minecraftVersion="1.9.4")
    monkeypatch.setattr(pack_library, "_packFolders", [str(tmp_path / "packs")])
    world1_7 = legacy_packs.selectLegacyPacks(levelTag({"testmod:lamp": 300}, dataVersion=0, itemData=True))
    world1_8 = legacy_packs.selectLegacyPacks(levelTag({"testmod:lamp": 300}, dataVersion=0))
    world1_9 = legacy_packs.selectLegacyPacks(levelTag({"testmod:lamp": 300}, dataVersion=184))
    assert [p.manifest["minecraftVersion"] for p in world1_7.packs] == ["1.7.10"]
    assert [p.manifest["minecraftVersion"] for p in world1_8.packs] == ["1.8.9"]
    assert [p.manifest["minecraftVersion"] for p in world1_9.packs] == ["1.9.4"]


def testABlockWithAModelFromTheGameIsDrawnAsAModel(tmp_path):
    # A block entity's renderer drew some states, but not the last; the one render type of the number is a model's.
    chest = json.loads(json.dumps(LAMP))
    chest["id"] = "testmod:chest"
    chest["common"]["renderType"] = 2
    for state in chest["states"][:3]:
        state["renderType"] = 3
        state["generatedModel"] = "testmod:mcedit_generated/chest/%d" % state["meta"]
    pack = writePack(tmp_path / "pack", [chest])
    types = PCBlockTypeSet()
    assert legacy_packs.applyLegacyPacks(types, [pack], {"testmod:chest": 300}) == 1
    assert types.renderType[300] == 3
    assert types[300, 3].json.get("generatedModel") is None
