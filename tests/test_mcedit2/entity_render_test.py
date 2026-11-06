"""
    entity_render_test

    Entities and block entities in worlds from Minecraft 1.11 and later have IDs like "minecraft:cow",
    while the renderers knew only the old IDs like "Cow", so no mob, chest or command block was drawn.
"""
import uuid

import numpy
import pytest

from mcedit2.rendering.chunkmeshes import entitymesh
from mcedit2.rendering.chunkmeshes.entity import models
from mceditlib import nbt
from mceditlib.anvil.entities import PCEntityRef, PCTileEntityRef
from mceditlib.geometry import Vector


@pytest.mark.parametrize("entityID, expected", [
    ("minecraft:cow", "Cow"),
    ("minecraft:armor_stand", "ArmorStand"),
    ("minecraft:experience_orb", "XPOrb"),
    ("minecraft:item_frame", "ItemFrame"),
    ("minecraft:glow_item_frame", "ItemFrame"),
    ("minecraft:zombie_pigman", "PigZombie"),
    ("minecraft:command_block", "Control"),
    ("minecraft:chest", "Chest"),
    ("Cow", "Cow"),                          # worlds from 1.10 and before
    ("create:seat", "create:seat"),          # mods keep theirs
])
def testLegacyID(entityID, expected):
    assert models.legacyID(entityID) == expected


def entityTag(entityID, pos, yaw=0.0):
    tag = nbt.TAG_Compound()
    tag["id"] = nbt.TAG_String(entityID)
    tag["Pos"] = nbt.TAG_List([nbt.TAG_Double(v) for v in pos])
    tag["Motion"] = nbt.TAG_List([nbt.TAG_Double(0) for v in pos])
    tag["Rotation"] = nbt.TAG_List([nbt.TAG_Float(yaw), nbt.TAG_Float(0)])
    tag["UUID"] = nbt.TAG_Int_Array(numpy.frombuffer(uuid.uuid4().bytes, dtype=">i4").astype(numpy.int32))
    return tag


def blockEntityTag(entityID, pos, **extra):
    tag = nbt.TAG_Compound()
    tag["id"] = nbt.TAG_String(entityID)
    for name, value in zip("xyz", pos):
        tag[name] = nbt.TAG_Int(value)
    for name, value in extra.items():
        tag[name] = value
    return tag


class FakeBlock:
    def __init__(self, internalName, blockState):
        self.internalName = internalName
        self.blockState = blockState


class FakeDimension:
    def __init__(self, blocks):
        self.blocks = blocks

    def getBlock(self, x, y, z):
        return self.blocks.get((x, y, z), FakeBlock("minecraft:air", ""))


class FakeChunk:
    cx = cz = 0
    chunkPosition = (0, 0)

    def __init__(self, entities=(), blockEntities=(), blocks=None):
        self.Entities = [PCEntityRef(tag, self) for tag in entities]
        self.TileEntities = [PCTileEntityRef(tag, self) for tag in blockEntities]
        self.dimension = FakeDimension(blocks or {})


class FakeUpdateTask:
    class worldScene:
        bounds = None

    def __init__(self):
        self.texturesAsked = []

    def getModelTexture(self, path):
        self.texturesAsked.append(path)
        return None


class FakeChunkUpdate:
    def __init__(self):
        self.updateTask = FakeUpdateTask()


def render(meshClass, chunk):
    mesh = meshClass(FakeChunkUpdate())
    for _ in mesh.makeChunkVertices(chunk, None):
        pass
    return mesh


MOBS = [
    entityTag("minecraft:cow", (1.5, 64, 1.5)),
    entityTag("minecraft:sheep", (3.5, 64, 1.5)),
    entityTag("minecraft:creeper", (5.5, 64, 1.5)),
    entityTag("minecraft:villager", (7.5, 64, 1.5)),
    entityTag("minecraft:chicken", (1.5, 64, 5.5)),
    entityTag("minecraft:zombified_piglin", (3.5, 64, 5.5)),
    entityTag("create:seat", (5.5, 64, 5.5)),
    entityTag("alexsmobs:grizzly_bear", (7.5, 64, 5.5)),
    entityTag("minecraft:item", (9.5, 64, 9.5)),
    entityTag("minecraft:painting", (11.5, 64, 9.5)),
]


def testModernMobsGetModels():
    mesh = render(entitymesh.MonsterModelRenderer, FakeChunk(MOBS))
    # cow, sheep and its wool, creeper, villager
    assert mesh.sceneNode is not None
    assert mesh.sceneNode.childCount() == 5
    asked = mesh.chunkUpdate.updateTask.texturesAsked
    assert "assets/minecraft/textures/entity/cow/cow.png" in asked
    assert "assets/minecraft/textures/entity/villager/villager.png" in asked


def testEntitiesWithoutModelsGetBoxes():
    mesh = render(entitymesh.EntityBoxRenderer, FakeChunk(MOBS))
    assert mesh.sceneNode is not None
    boxes = list(mesh.sceneNode.children)[1].vertexArrays[0]
    # chicken, zombified piglin, and the two from mods; not the mobs with models, items or paintings
    assert len(boxes.vertex) == 4 * 6
    colors = {tuple(c) for c in boxes.rgba.reshape(-1, 4)}
    assert colors == {entitymesh.EntityBoxRenderer.vanillaColor, entitymesh.EntityBoxRenderer.moddedColor}


def testModEntitiesAreBoxedInTheirOwnColour():
    chunk = FakeChunk([entityTag("create:seat", (1.5, 64, 1.5))])
    mesh = render(entitymesh.EntityBoxRenderer, chunk)
    boxes = list(mesh.sceneNode.children)[1].vertexArrays[0]
    assert {tuple(c) for c in boxes.rgba.reshape(-1, 4)} == {entitymesh.EntityBoxRenderer.moddedColor}


def testModernItemsAndPaintingsAreShown():
    mesh = render(entitymesh.ItemRenderer, FakeChunk(MOBS))
    assert len(mesh.sceneNode.vertexArrays[0].vertex) == 2 * 6


def testModernCommandBlocksAreColoured():
    chunk = FakeChunk(blockEntities=[
        blockEntityTag("minecraft:command_block", (1, 64, 1), Command=nbt.TAG_String("/say hello")),
        blockEntityTag("minecraft:command_block", (2, 64, 1), Command=nbt.TAG_String("kill")),
        blockEntityTag("minecraft:command_block", (3, 64, 1)),
        blockEntityTag("minecraft:chest", (4, 64, 1)),
    ])
    mesh = render(entitymesh.CommandBlockColorsMesh, chunk)
    colors = [tuple(c) for c in mesh.sceneNode.vertexArrays[0].rgba.reshape(3, -1, 4)[:, 0]]
    assert colors[0] == entitymesh.commandColor("say") + (0x44,)
    assert colors[1] == entitymesh.commandColor("kill") + (0x44,)
    assert colors[2] == (0xff, 0xff, 0x33, 0x44)


def testModernChestsAreDrawnOnePerHalf():
    blocks = {
        (1, 64, 1): FakeBlock("minecraft:chest", "[facing=north,type=left,waterlogged=false]"),
        (2, 64, 1): FakeBlock("minecraft:chest", "[facing=north,type=right,waterlogged=false]"),
        (5, 64, 1): FakeBlock("minecraft:chest", "[facing=east,type=single,waterlogged=true]"),
    }
    chunk = FakeChunk(blockEntities=[blockEntityTag("minecraft:chest", pos) for pos in blocks], blocks=blocks)
    mesh = render(entitymesh.TileEntityModelRenderer, chunk)
    assert mesh.sceneNode.childCount() == 3
    assert set(mesh.chunkUpdate.updateTask.texturesAsked) == {"assets/minecraft/textures/entity/chest/normal.png"}


def testOldChestsStillMakeDoubleChests():
    blocks = {
        (1, 64, 1): FakeBlock("minecraft:chest", "[facing=north]"),
        (2, 64, 1): FakeBlock("minecraft:chest", "[facing=north]"),
    }
    chunk = FakeChunk(blockEntities=[blockEntityTag("Chest", pos) for pos in blocks], blocks=blocks)
    mesh = render(entitymesh.TileEntityModelRenderer, chunk)
    assert mesh.sceneNode.childCount() == 1
    assert mesh.chunkUpdate.updateTask.texturesAsked == ["assets/minecraft/textures/entity/chest/normal_double.png"]


# --- SlashBlade (拔刀剑) ---

def bladeItem(itemID="slashblade:slashblade", translationKey="item.slashblade.yamato", broken=False):
    state = nbt.TAG_Compound()
    state["translationKey"] = nbt.TAG_String(translationKey)
    state["isBroken"] = nbt.TAG_Byte(1 if broken else 0)
    state["ModelName"] = nbt.TAG_String("slashblade:model/named/yamato.obj")
    components = nbt.TAG_Compound()
    components["slashblade:blade_state"] = state
    item = nbt.TAG_Compound()
    item["id"] = nbt.TAG_String(itemID)
    item["count"] = nbt.TAG_Int(1)
    item["components"] = components
    return item


def holding(tag, item):
    tag["Item"] = item
    return tag


def testFindingBlades():
    from mcedit2.util import slashblade
    assert slashblade.isBlade(bladeItem())
    # Blades from add-on mods have IDs of their own but the same data
    assert slashblade.isBlade(bladeItem(itemID="someaddon:great_blade"))
    plain = nbt.TAG_Compound()
    plain["id"] = nbt.TAG_String("slashblade:slashblade_wood")
    assert slashblade.isBlade(plain)
    stone = nbt.TAG_Compound()
    stone["id"] = nbt.TAG_String("minecraft:stone")
    assert not slashblade.isBlade(stone)
    stand = nbt.TAG_Compound()
    stand["id"] = nbt.TAG_String("slashblade:bladestand_1")
    assert not slashblade.isBlade(stand)
    assert slashblade.bladeName(bladeItem()) == "yamato"
    assert slashblade.isBroken(bladeItem(broken=True))


BLADES = [
    holding(entityTag("slashblade:blade_item_entity", (1.5, 64, 1.5)), bladeItem()),
    holding(entityTag("minecraft:item", (3.5, 64, 1.5)), bladeItem(broken=True)),
    holding(entityTag("slashblade:blade_stand_entity", (5.5, 64, 1.5)), bladeItem(itemID="someaddon:great_blade")),
    entityTag("slashblade:blade_stand_entity", (7.5, 64, 1.5)),
    entityTag("slashblade:judgement_cut", (9.5, 64, 1.5)),
]


def testBladesAreDrawnAsSwords():
    mesh = render(entitymesh.SlashBladeRenderer, FakeChunk(BLADES))
    solid, outlines = [node.vertexArrays[0] for node in mesh.sceneNode.children]
    # three swords of three boxes, and two stand bases
    assert len(solid.vertex) == (3 * 3 + 2) * 6
    assert len(outlines.vertex) == 3 * 6
    colors = {tuple(c) for c in solid.rgba.reshape(-1, 4)}
    assert entitymesh.SlashBladeRenderer.brokenBladeColor in colors
    assert entitymesh.SlashBladeRenderer.standColor in colors


def testBladesAreNotAlsoBoxesOrItems():
    boxes = render(entitymesh.EntityBoxRenderer, FakeChunk(BLADES))
    # only the judgement cut effect
    assert len(list(boxes.sceneNode.children)[1].vertexArrays[0].vertex) == 1 * 6
    items = render(entitymesh.ItemRenderer, FakeChunk(BLADES))
    assert len(items.sceneNode.vertexArrays[0].vertex) == 0


def testEntityListNamesTheBlade():
    from mcedit2.editortools.select_entity import SelectEntityTool

    class FakeTool:
        tr = staticmethod(lambda text: text)
        entityLabel = SelectEntityTool.entityLabel

    chunk = FakeChunk(BLADES)
    labels = [FakeTool().entityLabel(ref) for ref in chunk.Entities]
    assert labels == [
        "slashblade:blade_item_entity (blade: yamato)",
        "minecraft:item (blade: yamato, broken)",
        "slashblade:blade_stand_entity (blade: yamato)",
        "slashblade:blade_stand_entity",
        "slashblade:judgement_cut",
    ]


def testContainersWithBladesAreMarked():
    chest = blockEntityTag("minecraft:chest", (1, 64, 1))
    chest["Items"] = nbt.TAG_List([bladeItem()])
    barrel = blockEntityTag("minecraft:barrel", (3, 64, 1))
    barrel["Items"] = nbt.TAG_List([nbt.TAG_Compound()])
    zombie = entityTag("minecraft:zombie", (5.5, 64, 1.5))
    zombie["HandItems"] = nbt.TAG_List([bladeItem(broken=True), nbt.TAG_Compound()])
    mesh = render(entitymesh.SlashBladeRenderer, FakeChunk([zombie], [chest, barrel]))
    solid, outlines = [node.vertexArrays[0] for node in mesh.sceneNode.children]
    # a sword on the chest and one at the zombie; nothing for the barrel
    assert len(solid.vertex) == 2 * 3 * 6
    assert len(outlines.vertex) == 2 * 6
