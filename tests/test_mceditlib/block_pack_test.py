"""
    block_pack_test

Uses small packs written here, in the format of exporter/common/.../PackWriter.java.
"""
import gzip
import importlib.util
import json
from pathlib import Path

import pytest

from mceditlib.blocktypes.block_pack import BlockPack, BlockPackError

_spec = importlib.util.spec_from_file_location(
    "validate_block_pack", Path(__file__).resolve().parents[2] / "scripts" / "validate_block_pack.py")
validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validator)


def stateAttributes(**overrides):
    attributes = {
        "opaqueCube": False, "normalCube": False, "collidable": True, "opacity": 0, "brightness": 0,
        "hasEntity": False, "materialLiquid": False, "materialMapColor": 0, "renderLayer": "solid",
        "renderType": 3, "resourcePath": "minecraft:lever", "hasBlockstateFile": True,
    }
    attributes.update(overrides)
    return attributes


def lever():
    return {
        "id": "minecraft:lever",
        "displayName": "Lever",
        "descriptionId": "block.minecraft.lever",
        "defaultState": "powered=false",
        "properties": {"powered": ["false", "true"]},
        "common": stateAttributes(),
        "states": [
            {"state": "powered=false", "resourceVariant": "powered=false", "brightness": 0},
            {"state": "powered=true", "resourceVariant": "powered=true", "brightness": 7},
        ],
    }


def writePack(folder, blocks, schemaVersion=1, **counts):
    folder.mkdir(exist_ok=True)
    manifest = {
        "schemaVersion": schemaVersion, "minecraftVersion": "1.21.1", "dataVersion": 3955, "loader": "neoforge",
        "scope": "all",
        "counts": dict({"blocks": len(blocks), "states": sum(len(b["states"]) for b in blocks),
                        "blockEntities": 0, "problems": 0}, **counts),
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with gzip.open(folder / "blocks.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"blocks": blocks}, f)
    (folder / "block_entities.json").write_text('{"blockEntities": []}', encoding="utf-8")
    (folder / "report.json").write_text('{"problems": []}', encoding="utf-8")
    return folder


def writeAssets(folder, blockstates, models=("minecraft:block/lever",)):
    states = folder / "assets" / "minecraft" / "blockstates"
    states.mkdir(parents=True)
    (states / "lever.json").write_text(json.dumps(blockstates), encoding="utf-8")
    for model in models:
        path = folder / "assets" / "minecraft" / "models" / (model.split(":")[1] + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    return validator.Assets([str(folder)])


LEVER_VARIANTS = {"variants": {"powered=false": {"model": "minecraft:block/lever"},
                               "powered=true": {"model": "minecraft:block/lever"}}}


def testStatesMergeCommonAttributes(tmp_path):
    states = list(BlockPack(str(writePack(tmp_path / "pack", [lever()]))).states())
    assert [s["blockState"] for s in states] == ["[powered=false]", "[powered=true]"]
    assert [s["brightness"] for s in states] == [0, 7]
    assert all(s["internalName"] == "minecraft:lever" and s["renderType"] == 3 for s in states)
    assert [s["defaultState"] for s in states] == [True, False]
    assert all("state" not in s for s in states)


def testStatelessBlockHasEmptyBlockState(tmp_path):
    block = dict(lever(), id="minecraft:stone", defaultState="", properties={},
                 states=[{"state": "", "resourceVariant": ""}])
    state = next(BlockPack(str(writePack(tmp_path / "pack", [block]))).states())
    assert state["blockState"] == ""


def testNewerSchemaIsRefused(tmp_path):
    with pytest.raises(BlockPackError):
        BlockPack(str(writePack(tmp_path / "pack", [lever()], schemaVersion=999)))


def testFolderWithoutManifestIsRefused(tmp_path):
    pack = writePack(tmp_path / "pack", [lever()])
    (pack / "manifest.json").unlink()
    with pytest.raises(BlockPackError):
        BlockPack(str(pack))


def testValidPackPasses(tmp_path):
    pack = BlockPack(str(writePack(tmp_path / "pack", [lever()])))
    errors, _, stats = validator.validate(pack, writeAssets(tmp_path / "assets", LEVER_VARIANTS))
    assert errors == []
    assert stats["resolved"] == 2


@pytest.mark.parametrize("change, expected", [
    (lambda b: b["states"].pop(), "properties allow"),
    (lambda b: b.update(defaultState="powered=maybe"), "default state"),
    (lambda b: b["common"].pop("opacity"), "missing"),
    (lambda b: b["states"].append(dict(b["states"][0])), "duplicate states"),
])
def testStructuralProblemsAreFound(tmp_path, change, expected):
    block = lever()
    change(block)
    errors, _, _ = validator.validate(BlockPack(str(writePack(tmp_path / "pack", [block]))), None)
    assert any(expected in e for e in errors), errors


def testWrongCountsAreFound(tmp_path):
    errors, _, _ = validator.validate(BlockPack(str(writePack(tmp_path / "pack", [lever()], states=99))), None)
    assert any("manifest says" in e for e in errors)


def testMissingVariantIsFound(tmp_path):
    assets = writeAssets(tmp_path / "assets", {"variants": {"powered=false": {"model": "minecraft:block/lever"}}})
    errors, _, _ = validator.validate(BlockPack(str(writePack(tmp_path / "pack", [lever()]))), assets)
    assert any("no variant" in e and "powered=true" in e for e in errors)


def testMissingModelIsFound(tmp_path):
    assets = writeAssets(tmp_path / "assets", LEVER_VARIANTS, models=())
    errors, _, _ = validator.validate(BlockPack(str(writePack(tmp_path / "pack", [lever()]))), assets)
    assert any("was not found" in e for e in errors)


def testBlockstateFileClaimedButMissingIsFound(tmp_path):
    (tmp_path / "assets").mkdir()
    errors, _, _ = validator.validate(BlockPack(str(writePack(tmp_path / "pack", [lever()]))),
                                      validator.Assets([str(tmp_path / "assets")]))
    assert any("claims a blockstate file" in e for e in errors)


@pytest.mark.parametrize("when, state, expected", [
    ({"powered": "true"}, {"powered": "true"}, True),
    ({"powered": "true"}, {"powered": "false"}, False),
    ({"powered": True}, {"powered": "true"}, True),
    ({"facing": "north|south"}, {"facing": "south"}, True),
    ({"facing": "north|south"}, {"facing": "east"}, False),
    ({"OR": [{"a": "1"}, {"b": "1"}]}, {"a": "0", "b": "1"}, True),
    ({"AND": [{"a": "1"}, {"b": "1"}]}, {"a": "0", "b": "1"}, False),
])
def testMultipartConditions(when, state, expected):
    assert validator.whenMatches(when, state) is expected


def testSubsetVariantKeyMatches():
    blockstates = {"variants": {"": {"model": "minecraft:block/a"}}}
    assert validator.modelsForState(blockstates, {"resourceVariant": "facing=north"}) == ["minecraft:block/a"]
    blockstates = {"variants": {"facing=north": [{"model": "minecraft:block/a"}, {"model": "minecraft:block/b"}]}}
    assert validator.modelsForState(blockstates, {"resourceVariant": "facing=north,lit=true"}) == [
        "minecraft:block/a", "minecraft:block/b"]


# --- The built-in table generated from Minecraft 1.21.1 ---

BUILT_IN = str(Path(__file__).resolve().parents[2] / "src" / "mceditlib" / "blocktypes" / "packs" / "minecraft_1_21_1")


@pytest.fixture(scope="module")
def builtIn():
    return BlockPack(BUILT_IN)


@pytest.fixture(scope="module")
def builtInStates(builtIn):
    return {s["internalName"] + s["blockState"]: s for s in builtIn.states()}


def testBuiltInIdentifiesItsGame(builtIn):
    assert builtIn.dataVersion == 3955
    assert builtIn.manifest["minecraftVersion"] == "1.21.1"
    assert builtIn.manifest["scope"] == "vanilla"
    assert builtIn.problems == []


def testBuiltInPassesStructureChecks(builtIn):
    errors, _, _ = validator.validate(builtIn, None)
    assert errors == []


def testBuiltInHasOnlyMinecraftBlocks(builtIn):
    assert all(block["id"].startswith("minecraft:") for block in builtIn.blocks)


@pytest.mark.parametrize("name, attributes", [
    ("minecraft:stone", {"opaqueCube": True, "opacity": 15, "renderType": 3, "defaultState": True}),
    ("minecraft:air", {"opaqueCube": False, "opacity": 0, "renderType": 0}),
    ("minecraft:water[level=0]", {"materialLiquid": True, "renderType": 1, "defaultState": True}),
    ("minecraft:redstone_lamp[lit=true]", {"brightness": 15, "defaultState": False}),
    ("minecraft:redstone_lamp[lit=false]", {"brightness": 0, "defaultState": True}),
    ("minecraft:grass_block[snowy=false]", {"tinted": True, "opaqueCube": True}),
    ("minecraft:chest[facing=north,type=single,waterlogged=false]", {"hasEntity": True, "renderType": 2}),
    ("minecraft:oak_sign[rotation=0,waterlogged=false]", {"hasEntity": True, "renderType": 0}),
])
def testBuiltInBlockFacts(builtInStates, name, attributes):
    state = builtInStates[name]
    assert {k: state[k] for k in attributes} == attributes


def testBuiltInFitsSixteenStatesPerId(builtIn):
    # The virtual ID scheme planned for M4 gives each block one ID per 16 states.
    assert sum((len(b["states"]) + 15) // 16 for b in builtIn.blocks) < 4096
