"""
    find_slashblades_test

    Finding the blades of the SlashBlade mod in a world: in containers (also inside a shulker box in
    a chest), in mobs' hands, on the ground, and in players' inventories and ender chests.
"""
import importlib.util
from pathlib import Path

import numpy
import pytest

from mcedit2.editorcommands.find_slashblades import BladeResult, scanDimension, scanPlayers
from mcedit2.util import slashblade
from mceditlib import nbt
from mceditlib.worldeditor import WorldEditor

TESTS = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("modern_world_test", TESTS / "test_mceditlib" / "modern_world_test.py")
worlds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worlds)


@pytest.fixture
def world(tmp_path):
    folder = tmp_path / "world"
    folder.mkdir()
    worlds.writeLevelDat(folder)
    return folder


def bladeItem(key, broken=False, slot=None):
    state = nbt.TAG_Compound()
    state["translationKey"] = nbt.TAG_String(key)
    state["isBroken"] = nbt.TAG_Byte(1 if broken else 0)
    components = nbt.TAG_Compound()
    components["slashblade:blade_state"] = state
    item = nbt.TAG_Compound()
    item["id"] = nbt.TAG_String("slashblade:slashblade")
    item["count"] = nbt.TAG_Int(1)
    item["components"] = components
    if slot is not None:
        item["Slot"] = nbt.TAG_Byte(slot)
    return item


def plainItem(itemID, slot):
    item = nbt.TAG_Compound()
    item["id"] = nbt.TAG_String(itemID)
    item["count"] = nbt.TAG_Int(1)
    item["Slot"] = nbt.TAG_Byte(slot)
    return item


def container(blockID, x, y, z, items):
    tag = nbt.TAG_Compound()
    tag["id"] = nbt.TAG_String(blockID)
    for name, value in zip("xyz", (x, y, z)):
        tag[name] = nbt.TAG_Int(value)
    tag["Items"] = nbt.TAG_List(items, list_type=nbt.ID_COMPOUND)
    return tag


def shulkerBoxWith(blade, slot):
    entry = nbt.TAG_Compound()
    entry["slot"] = nbt.TAG_Int(0)
    entry["item"] = blade
    components = nbt.TAG_Compound()
    components["minecraft:container"] = nbt.TAG_List([entry])
    box = plainItem("minecraft:shulker_box", slot)
    box["components"] = components
    return box


def entity(entityID, pos, uuid):
    tag = nbt.TAG_Compound()
    tag["id"] = nbt.TAG_String(entityID)
    tag["Pos"] = nbt.TAG_List([nbt.TAG_Double(v) for v in pos])
    tag["UUID"] = nbt.TAG_Int_Array(numpy.array(uuid, ">u4"))
    return tag


def playerFile(world, uuid):
    root = nbt.TAG_Compound()
    root["Pos"] = nbt.TAG_List([nbt.TAG_Double(v) for v in (8.5, 70.0, 8.5)])
    root["Dimension"] = nbt.TAG_String("minecraft:overworld")
    root["Inventory"] = nbt.TAG_List([plainItem("minecraft:bread", 0), bladeItem("item.slashblade.koseki", broken=True, slot=5)])
    root["EnderItems"] = nbt.TAG_List([bladeItem("item.slashblade.yamato", slot=3)])
    folder = world / "playerdata"
    folder.mkdir()
    (folder / ("%s.dat" % uuid)).write_bytes(root.save())


def makeWorld(world):
    blockEntities = [
        container("minecraft:chest", 1, 64, 1, [plainItem("minecraft:stone", 0), bladeItem("item.slashblade.muramasa", slot=2)]),
        container("minecraft:chest", 3, 64, 1, [shulkerBoxWith(bladeItem("item.slashblade.hidden"), 4)]),
        container("minecraft:barrel", 5, 64, 1, [plainItem("minecraft:dirt", 0)]),
    ]
    section = worlds.makeSection(4, [worlds.state("minecraft:air")])
    worlds.writeRegion(world / "region" / "r.0.0.mca",
                       {(0, 0): worlds.makeChunk(0, 0, [section], blockEntities=blockEntities)})

    zombie = entity("minecraft:zombie", (7.5, 64, 7.5), (1, 2, 3, 4))
    zombie["HandItems"] = nbt.TAG_List([bladeItem("item.slashblade.zombie_blade"), nbt.TAG_Compound()])
    dropped = entity("slashblade:blade_item_entity", (9.5, 64, 9.5), (5, 6, 7, 8))
    dropped["Item"] = bladeItem("item.slashblade.dropped")
    cow = entity("minecraft:cow", (11.5, 64, 11.5), (9, 9, 9, 9))
    worlds.writeRegion(world / "entities" / "r.0.0.mca", {(0, 0): worlds.entityFile([zombie, dropped, cow])})
    playerFile(world, "0a0b0c0d-0000-0000-0000-000000000001")


def testFindsBladesEverywhere(world):
    makeWorld(world)
    editor = WorldEditor(str(world), readonly=True)
    try:
        results = []
        for _ in scanDimension(editor.getDimension(), results):
            pass
        scanPlayers(editor, results)
    finally:
        editor.close()

    found = sorted((r.kind, r.name, slashblade.pathText(r.path), r.broken) for r in results)
    assert found == sorted([
        (BladeResult.Container, "muramasa", "Items[1]", False),
        (BladeResult.Container, "hidden", "Items[0].components.minecraft:container[0].item", False),
        (BladeResult.Entity, "zombie_blade", "HandItems[0]", False),
        (BladeResult.Entity, "dropped", "Item", False),
        (BladeResult.Player, "koseki", "Inventory[1]", True),
        (BladeResult.Player, "yamato", "EnderItems[0]", False),
    ])
    players = [r for r in results if r.kind == BladeResult.Player]
    assert all(r.dimName == "" and tuple(r.position) == (8.5, 70.0, 8.5) for r in players)
    containers = {tuple(r.position) for r in results if r.kind == BladeResult.Container}
    assert containers == {(1, 64, 1), (3, 64, 1)}


def testNothingInAWorldWithoutBlades(world):
    worlds.writeRegion(world / "region" / "r.0.0.mca",
                       {(0, 0): worlds.makeChunk(0, 0, [worlds.makeSection(4, [worlds.state("minecraft:air")])])})
    editor = WorldEditor(str(world), readonly=True)
    try:
        results = []
        for _ in scanDimension(editor.getDimension(), results):
            pass
        scanPlayers(editor, results)
    finally:
        editor.close()
    assert results == []
