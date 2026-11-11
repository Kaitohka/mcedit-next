"""
    ${NAME}
"""

import logging

from OpenGL import GL
import numpy

from mcedit2.rendering import renderstates
from mcedit2.rendering.command_visuals import CommandVisuals
from mcedit2.rendering.scenegraph import scenenode
from mcedit2.rendering.blockmeshes import standardCubeTemplates
from mcedit2.rendering.blockmeshes import ChunkMeshBase
from mcedit2.rendering.chunkmeshes.entity import models
from mcedit2.rendering.layers import Layer
from mcedit2.rendering.scenegraph.bind_texture import BindTexture
from mcedit2.rendering.scenegraph.matrix import Translate, Rotate, Scale
from mcedit2.rendering.scenegraph.misc import PolygonMode, LineWidth
from mcedit2.rendering.scenegraph.depth_test import DepthFunc
from mcedit2.rendering.scenegraph.scenenode import Node
from mcedit2.rendering.scenegraph.vertex_array import VertexNode
from mcedit2.rendering.slices import _XYZ
from mcedit2.rendering.vertexarraybuffer import QuadVertexArrayBuffer
from mcedit2.util import slashblade
from mcedit2.util.commandblock import ParseCommand
from mceditlib.anvil.entities import PCPaintingEntityRefBase

log = logging.getLogger(__name__)


# TODO: allow these damned things to return multiple scenenodes on different layers,
# TODO: so we don't have to iterate TileEntities over and over

class EntityMeshBase(ChunkMeshBase):
    renderstate = renderstates.RenderstateEntity
    detailLevels = (0, 1, 2)

    def _computeVertices(self, positions, colors, offset=False, chunkPosition=(0, 0)):
        colors = numpy.asarray(colors, dtype=numpy.uint8)
        if len(colors.shape) > 1:
            colors = colors[:, None, None, :]
        else:
            colors = colors[None, None, None, :]

        cx, cz = chunkPosition
        x = cx << 4
        z = cz << 4

        bounds = self.chunkUpdate.updateTask.worldScene.bounds
        if bounds:
            positions = [p for p in positions if p in bounds]

        vertexBuffer = QuadVertexArrayBuffer(len(positions) * 6, lights=False, textures=False)
        vertexBuffer.buffer = vertexBuffer.buffer.reshape((len(positions), 6) + vertexBuffer.buffer.shape[-2:])
        if len(positions):
            positions = numpy.array(positions, dtype=float)
            positions[:, (0, 2)] -= (x, z)
            if offset:
                positions -= (0.5, 0.0, 0.5)

            vertexBuffer.rgba[:] = colors
            vertexBuffer.vertex[:] = positions[:, numpy.newaxis, numpy.newaxis, :]
            vertexBuffer.vertex[:] += standardCubeTemplates[_XYZ]

        vertexBuffer.buffer = vertexBuffer.buffer.reshape((len(positions) * 6, ) + vertexBuffer.buffer.shape[-2:])
        return vertexBuffer


def colorHash(text):
    """
    Stolen from https://github.com/zenozeng/color-hash
    """
    SaturationArray = [0.66, 0.83, .95]
    LightnessArray = [0.5, 0.66, 0.75]

    h = hash(text)
    Hue = h % 359  # (Note that 359 is a prime)
    Saturation = SaturationArray[h // 360 % len(SaturationArray)]
    Lightness = LightnessArray[h // 360 // len(SaturationArray) % len(LightnessArray)]
    return Hue, Saturation, Lightness


def HSL2RGB(H, S, L):
    H /= 360

    q = L * (1 + S) if L < 0.5 else L + S - L * S
    p = 2 * L - q

    def something(color):
        if color < 0:
            color += 1
        
        if color > 1:
            color -= 1
        
        if color < 1/6:
            color = p + (q - p) * 6 * color
        elif color < 0.5:
            color = q
        elif color < 2/3:
            color = p + (q - p) * 6 * (2/3 - color)
        else:
            color = p
        
        return int(color * 255)

    return [something(a) for a in (H + 1/3, H, H - 1/3)]


def computeCommandColor(cmdName):
    return tuple(HSL2RGB(*colorHash(cmdName)))


allCommands = [
    "achievement",
    "blockdata",
    "clear",
    "clone",
    "defaultgamemod",
    "difficulty",
    "effect",
    "enchant",
    "entitydata",
    "execute",
    "fill",
    "gamemode",
    "gamerule",
    "give",
    "help",
    "kill",
    "list",
    "me",
    "particle",
    "playsound",
    "replaceitem",
    "say",
    "scoreboard",
    "seed",
    "setblock",
    "setworldspawn",
    "spawnpoint",
    "spreadplayers",
    "stats",
    "summon",
    "tell",
    "tellraw",
    "testfor",
    "testforblock",
    "testforblocks",
    "time",
    "title",
    "toggledownfall",
    "tp",
    "trigger",
    "weather",
    "worldborder",
    "xp",
]

if __name__ == '__main__':
    _colors = {c: computeCommandColor(c) for c in allCommands}
    from pprint import pprint
    pprint(_colors)

# Guard against randomized hash functions
# Ensure the same colors are used across platforms and executions
_commandColors = {
    'achievement': (38, 248, 6),
    'blockdata': (6, 18, 248),
    'clear': (240, 96, 197),
    'clone': (138, 244, 203),
    'defaultgamemod': (6, 248, 30),
    'difficulty': (219, 233, 21),
    'effect': (115, 248, 6),
    'enchant': (95, 21, 233),
    'entitydata': (250, 85, 96),
    'execute': (225, 111, 189),
    'fill': (233, 191, 149),
    'gamemode': (130, 148, 251),
    'gamerule': (244, 189, 138),
    'give': (244, 138, 180),
    'help': (250, 168, 85),
    'kill': (111, 225, 185),
    'list': (6, 248, 127),
    'me': (176, 233, 21),
    'particle': (43, 211, 60),
    'playsound': (251, 156, 130),
    'replaceitem': (21, 233, 134),
    'say': (240, 96, 223),
    'scoreboard': (240, 96, 96),
    'seed': (43, 211, 127),
    'setblock': (233, 116, 21),
    'setworldspawn': (85, 160, 250),
    'spawnpoint': (149, 233, 206),
    'spreadplayers': (138, 141, 244),
    'stats': (233, 64, 21),
    'summon': (233, 222, 149),
    'tell': (224, 248, 6),
    'tellraw': (149, 233, 201),
    'testfor': (211, 43, 65),
    'testforblock': (206, 233, 149),
    'testforblocks': (96, 240, 199),
    'time': (247, 130, 251),
    'title': (91, 248, 6),
    'toggledownfall': (250, 85, 245),
    'tp': (251, 201, 130),
    'trigger': (143, 85, 250),
    'weather': (21, 233, 81),
    'worldborder': (21, 233, 102),
    'xp': (98, 96, 240)
}


def commandColor(cmd):
    cmd = cmd.lower()
    color = _commandColors.get(cmd)
    if color is None:
        color = computeCommandColor(cmd)
    return color


def commandBlockColor(ref, defaultColor):
    # A 1.13+ command block has the plain block entity ref, without the Command attribute.
    tag = ref.rootTag.get("Command")
    cmdText = tag.value if tag is not None else ""
    words = cmdText.lstrip("/").split(None, 1)
    if not words:
        return defaultColor
    return commandColor(words[0]) + (0x44,)


class TileEntityLocationMesh(EntityMeshBase):
    layer = Layer.TileEntityLocations

    def makeChunkVertices(self, chunk, limitBox):
        tilePositions = []
        defaultColor = (0xff, 0xff, 0x33, 0x44)
        for i, ref in enumerate(chunk.TileEntities):
            if i % 10 == 0:
                yield

            if limitBox and ref.Position not in limitBox:
                continue
            if models.legacyID(ref.id) == "Control":
                continue
            tilePositions.append(ref.Position)

        if not len(tilePositions):
            return

        tiles = self._computeVertices(tilePositions, defaultColor, chunkPosition=chunk.chunkPosition)

        vertexNode = VertexNode([tiles])
        polygonMode = PolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE)
        vertexNode.addState(polygonMode)
        lineWidth = LineWidth(2.0)
        vertexNode.addState(lineWidth)
        depthFunc = DepthFunc(GL.GL_ALWAYS)
        vertexNode.addState(depthFunc)

        self.sceneNode = Node("tileEntityLocations")
        self.sceneNode.addChild(vertexNode)

        vertexNode = VertexNode([tiles])
        self.sceneNode.addChild(vertexNode)


class CommandBlockLocationMesh(EntityMeshBase):
    layer = Layer.CommandBlockLocations

    def makeChunkVertices(self, chunk, limitBox):
        tilePositions = []
        tileColors = []
        defaultColor = (0xff, 0x33, 0x33, 0x44)
        for i, ref in enumerate(chunk.TileEntities):
            if i % 10 == 0:
                yield

            if limitBox and ref.Position not in limitBox:
                continue
            if models.legacyID(ref.id) == "Control":
                tilePositions.append(ref.Position)
                tileColors.append(commandBlockColor(ref, defaultColor))
            else:
                continue

        if not len(tileColors):
            return

        tiles = self._computeVertices(tilePositions, tileColors, chunkPosition=chunk.chunkPosition)

        vertexNode = VertexNode([tiles])
        vertexNode.addState(PolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE))
        vertexNode.addState(LineWidth(2.0))
        vertexNode.addState(DepthFunc(GL.GL_ALWAYS))

        self.sceneNode = Node("commandBlockLocations")
        self.sceneNode.addChild(vertexNode)


class CommandBlockColorsMesh(EntityMeshBase):
    layer = Layer.CommandBlockColors

    def makeChunkVertices(self, chunk, limitBox):
        tilePositions = []
        tileColors = []
        defaultColor = (0xff, 0xff, 0x33, 0x44)
        for i, ref in enumerate(chunk.TileEntities):
            if i % 10 == 0:
                yield

            if limitBox and ref.Position not in limitBox:
                continue
            if models.legacyID(ref.id) == "Control":
                tileColors.append(commandBlockColor(ref, defaultColor))
            else:
                continue
            tilePositions.append(ref.Position)

        tiles = self._computeVertices(tilePositions, tileColors, chunkPosition=chunk.chunkPosition)

        vertexNode = VertexNode([tiles])
        self.sceneNode = vertexNode


class ItemFrameMesh(EntityMeshBase):
    layer = Layer.ItemFrames

    def makeChunkVertices(self, chunk, limitBox):
        mapTiles = []
        for i, ref in enumerate(chunk.Entities):
            if ref.id != "ItemFrame":
                continue

            if i % 10 == 0:
                yield

            if limitBox and ref.Position not in limitBox:
                continue

            try:
                item = ref.Item
                if item.itemType.internalName != "minecraft:filled_map":
                    continue
            except KeyError:
                log.exception("Error while getting ItemFrame item ID in frame at %s", ref.TilePos)
                continue

            mapID = item.Damage

            mapTex = self.chunkUpdate.updateTask.getMapTexture(mapID)
            # xxx if mapTex is None: mapTex = missingNoTex

            # xxxx assumes 1.8 TilePos - fix this in ref??
            mapTiles.append((mapTex, ref.TilePos, ref.Facing))

        if not len(mapTiles):
            return

        nodes = []

        for mapTex, (x, y, z), facing in mapTiles:
            vertexBuffer = QuadVertexArrayBuffer(1, lights=False, textures=True)

            # chunk is already translated - why?
            x -= chunk.cx << 4
            z -= chunk.cz << 4

            vertexBuffer.vertex[:] = x, y, z
            corners = self.faceCorners[facing]
            vertexBuffer.vertex[:] += corners
            texCorners = [(1, 1), (1, 0), (0, 0), (0, 1)]
            vertexBuffer.texcoord[:] += texCorners

            vertexNode = VertexNode([vertexBuffer])
            if mapTex is not None:
                bindTexture = BindTexture(mapTex)
                vertexNode.addState(bindTexture)
            nodes.append(vertexNode)

        self.sceneNode = scenenode.Node("itemFrames")
        for node in nodes:
            self.sceneNode.addChild(node)

    faceCorners = {  # xxx polygon offset?
        PCPaintingEntityRefBase.SouthFacing: ((1, 0, 0.01), (1, 1, 0.01), (0, 1, 0.01), (0, 0, 0.01)),
        PCPaintingEntityRefBase.WestFacing: ((0.99, 0, 1), (0.99, 1, 1), (0.99, 1, 0), (0.99, 0, 0)),
        PCPaintingEntityRefBase.NorthFacing: ((0, 0, 0.99), (0, 1, 0.99), (1, 1, 0.99), (1, 0, 0.99)),
        PCPaintingEntityRefBase.EastFacing: ((0.01, 0, 0), (0.01, 1, 0), (0.01, 1, 1), (0.01, 0, 1)),
    }


def entityModelNode(ref, model, modelTex=None, chunk=None, flip=False):
    modelVerts = numpy.array(model.vertices)
    modelVerts = modelVerts.reshape(modelVerts.shape[0]//4, 4, modelVerts.shape[1])
    # scale down
    modelVerts[..., :3] *= 1/16.
    modelVerts[..., 1] = -modelVerts[..., 1] + 1.5 + 1/64.
    modelVerts[..., 0] = -modelVerts[..., 0]

    vertexBuffer = QuadVertexArrayBuffer(len(modelVerts), lights=False, textures=modelTex is not None)
    vertexBuffer.vertex[:] = modelVerts[..., :3]
    if modelTex is not None:
        vertexBuffer.texcoord[:] = modelVerts[..., 3:5]

    node = VertexNode([vertexBuffer])

    pos = ref.Position
    if chunk is not None:
        pos = pos - (chunk.cx << 4, 0, chunk.cz << 4)

    translate = Translate(pos)
    node.addState(translate)

    rotate = Rotate(ref.Rotation[0], (0., 1., 0.))
    node.addState(rotate)

    if modelTex is not None:
        bindTexture = BindTexture(modelTex, (1./model.texWidth, 1./model.texHeight * (-1 if flip else 1), 1))
        node.addState(bindTexture)
    return node


class MonsterModelRenderer(ChunkMeshBase):
    def makeChunkVertices(self, chunk, limitBox):
        sceneNode = scenenode.Node("monsters")
        for i, ref in enumerate(chunk.Entities):
            ID = models.legacyID(ref.id)
            if ID not in models.cookedModels:
                continue

            model = models.cookedModels[ID]
            texturePath = models.getModelTexture(ref)
            if texturePath is None:
                modelTex = None
            else:
                modelTex = self.chunkUpdate.updateTask.getModelTexture(texturePath)

            node = entityModelNode(ref, model, modelTex, chunk)
            sceneNode.addChild(node)
            if ID == "Sheep":
                woolID = "MCEDIT_SheepWool"
                model = models.cookedModels[woolID]
                texturePath = models.getTexture(woolID)
                modelTex = self.chunkUpdate.updateTask.getModelTexture(texturePath)

                node = entityModelNode(ref, model, modelTex, chunk)
                sceneNode.addChild(node)

            yield

        if not sceneNode.childCount():
            return

        self.sceneNode = sceneNode


def chestEntityModelNode(ref, model, modelTex, chunk, facing, largeX, largeZ):
    modelVerts = numpy.array(model.vertices)
    modelVerts = modelVerts.reshape(modelVerts.shape[0]//4, 4, modelVerts.shape[1])
    # scale down
    modelVerts[..., :3] *= 1/16.
    # modelVerts[..., 1] = -modelVerts[..., 1]
    # modelVerts[..., 0] = -modelVerts[..., 0]

    vertexBuffer = QuadVertexArrayBuffer(len(modelVerts), lights=False, textures=True)
    vertexBuffer.vertex[:] = modelVerts[..., :3]
    vertexBuffer.texcoord[:] = modelVerts[..., 3:5]

    node = VertexNode([vertexBuffer])
    rotations = {
        "north": 180,
        "east": 270,
        "south": 0,
        "west": 90
    }
    dx = dz = 0
    if largeX and facing == "north":
        dx = 1.
    if largeZ and facing == "east":
        dz = -1.
    x, y, z = (ref.Position - (chunk.cx << 4, 0, chunk.cz << 4))

    # A node's states apply to its vertices last first: the model is turned about its middle to face the way the
    # block does, turned the right way up, and then put where the block is.
    node.addState(Translate((x, y + 1., z + 1.)))
    node.addState(Scale((1., -1., -1.)))
    node.addState(Translate((0.5 + dx, 0.5, 0.5 + dz)))
    node.addState(Rotate(rotations[facing], (0., 1., 0.)))
    node.addState(Translate((-0.5, -0.5, -0.5)))

    bindTexture = BindTexture(modelTex, (1./model.texWidth, 1./model.texHeight, 1))
    node.addState(bindTexture)
    return node


class TileEntityModelRenderer(ChunkMeshBase):
    layer = Layer.TileEntities

    def makeChunkVertices(self, chunk, limitBox):
        sceneNode = scenenode.Node("tileEntityModels")
        chests = {}
        for i, ref in enumerate(chunk.TileEntities):
            ID = models.legacyID(ref.id)
            if ID != "Chest":
                continue
            block = chunk.dimension.getBlock(*ref.Position)
            if block.internalName != "minecraft:chest":
                continue
            properties = dict(p.split("=", 1) for p in block.blockState[1:-1].split(",") if "=" in p)
            if properties.get("facing") in ("north", "east", "south", "west"):
                chests[ref.Position] = ref, properties, {}

        for (x, y, z), (ref, properties, adj) in chests.items():
            # Before 1.13 a double chest is two chests side by side, and the one to the west or north draws both.
            if "type" in properties:
                continue
            for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                if (x+dx, y, z+dz) in chests:
                    adj[dx, dz] = ref

        updateTask = self.chunkUpdate.updateTask
        for ref, properties, adj in chests.values():
            facing = properties["facing"]
            if "type" in properties:
                # Since 1.13 each half of a double chest says which it is.
                half = properties["type"]
                if half == "single":
                    modelID = "Chest"
                elif updateTask.chestsComeInHalves():
                    modelID = "MCEDIT_Chest_" + half
                elif half == "right":
                    # In 1.13 and 1.14 the right half draws both, reaching over the left one's block.
                    modelID = "MCEDIT_LargeChest"
                else:
                    continue
                largeX = largeZ = False
            else:
                if (-1, 0) in adj or (0, -1) in adj:
                    continue
                if (1, 0) not in adj and (0, 1) not in adj:
                    modelID = "Chest"
                else:
                    modelID = "MCEDIT_LargeChest"
                largeX, largeZ = (1, 0) in adj, (0, 1) in adj

            model = models.cookedTileEntityModels[modelID]
            texturePath = model.modelTexture
            if texturePath is None:
                modelTex = None
            else:
                modelTex = updateTask.getModelTexture(texturePath)

            node = chestEntityModelNode(ref, model, modelTex, chunk, facing, largeX, largeZ)
            sceneNode.addChild(node)

            yield

        self.sceneNode = sceneNode


class MonsterLocationRenderer(EntityMeshBase):
    layer = Layer.MonsterLocations
    notMonsters = {"Item", "XPOrb", "Painting", "ItemFrame"}

    def makeChunkVertices(self, chunk, limitBox):
        monsterPositions = []
        for i, entityRef in enumerate(chunk.Entities):
            if i % 10 == 0:
                yield
            ID = models.legacyID(entityRef.id)

            if ID in self.notMonsters:
                continue
            pos = entityRef.Position
            if limitBox and pos not in limitBox:
                continue
            monsterPositions.append(pos)

        if not len(monsterPositions):
            return

        monsters = self._computeVertices(monsterPositions,
                                         (0xff, 0x22, 0x22, 0x44),
                                         offset=True,
                                         chunkPosition=chunk.chunkPosition)
        yield

        vertexNode = VertexNode(monsters)
        vertexNode.addState(PolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE))
        vertexNode.addState(LineWidth(2.0))
        vertexNode.addState(DepthFunc(GL.GL_ALWAYS))

        self.sceneNode = Node("monsterLocations")
        self.sceneNode.addChild(vertexNode)

        vertexNode = VertexNode(monsters)
        self.sceneNode.addChild(vertexNode)



class ItemRenderer(EntityMeshBase):
    layer = Layer.Items

    def makeChunkVertices(self, chunk, limitBox):
        entityPositions = []
        entityColors = []
        colorMap = {
            "Item": (0x22, 0xff, 0x22, 0x5f),
            "XPOrb": (0x88, 0xff, 0x88, 0x5f),
            "Painting": (134, 96, 67, 0x5f),
        }
        for i, entityRef in enumerate(chunk.Entities):
            if i % 10 == 0:
                yield
            color = colorMap.get(models.legacyID(entityRef.id))
            if color is None:
                continue
            if slashblade.heldBlade(entityRef.rootTag) is not None:
                continue
            pos = entityRef.Position
            if limitBox and pos not in limitBox:
                continue

            entityPositions.append(pos)
            entityColors.append(color)

        items = self._computeVertices(entityPositions,
                                      entityColors,
                                      offset=True, chunkPosition=chunk.chunkPosition)
        yield
        self.sceneNode = VertexNode(items)


class EntityBoxRenderer(EntityMeshBase):
    """
    A box where each entity is that has no model: most mobs, and every entity from a mod. Mod
    entities are a different colour from the game's own.
    """
    layer = Layer.Blocks
    vanillaColor = (0xff, 0x88, 0x00, 0xaa)
    moddedColor = (0xbb, 0x22, 0xff, 0xaa)
    # Drawn by ItemRenderer and ItemFrameMesh instead
    notShown = {"Item", "XPOrb", "Painting", "ItemFrame"}

    def makeChunkVertices(self, chunk, limitBox):
        positions = []
        colors = []
        for i, entityRef in enumerate(chunk.Entities):
            if i % 10 == 0:
                yield
            ID = models.legacyID(entityRef.id)
            if ID in models.cookedModels or ID in self.notShown:
                continue
            if slashblade.heldBlade(entityRef.rootTag) is not None or slashblade.isBladeStand(entityRef.rootTag):
                continue
            pos = entityRef.Position
            if limitBox and pos not in limitBox:
                continue
            positions.append(pos)
            colors.append(self.moddedColor if models.isModded(entityRef.id) else self.vanillaColor)

        if not len(positions):
            return

        boxes = self._computeVertices(positions, colors, offset=True, chunkPosition=chunk.chunkPosition)
        yield

        outline = VertexNode(boxes)
        outline.addState(PolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE))
        outline.addState(LineWidth(2.0))

        self.sceneNode = Node("entityBoxes")
        self.sceneNode.addChild(outline)
        self.sceneNode.addChild(VertexNode(boxes))


def boxVertices(boxes, chunkPosition):
    """
    Vertices for boxes of any size, given as (origin, size, rgba) with world coordinates.
    """
    cx, cz = chunkPosition
    vertexBuffer = QuadVertexArrayBuffer(len(boxes) * 6, lights=False, textures=False)
    if not len(boxes):
        return vertexBuffer
    origins = numpy.array([b[0] for b in boxes], dtype=float)
    origins[:, (0, 2)] -= (cx << 4, cz << 4)
    sizes = numpy.array([b[1] for b in boxes], dtype=float)
    colors = numpy.array([b[2] for b in boxes], dtype=numpy.uint8)

    shape = vertexBuffer.buffer.shape
    vertexBuffer.buffer = vertexBuffer.buffer.reshape((len(boxes), 6) + shape[-2:])
    template = numpy.asarray(standardCubeTemplates[_XYZ], dtype=float)
    vertexBuffer.vertex[:] = origins[:, None, None, :] + template[None] * sizes[:, None, None, :]
    vertexBuffer.rgba[:] = colors[:, None, None, :]
    vertexBuffer.buffer = vertexBuffer.buffer.reshape(shape)
    return vertexBuffer


class SlashBladeRenderer(EntityMeshBase):
    """
    The blades of the SlashBlade mod, wherever an entity or a container holds one: lying on the
    ground, on a blade stand, as an ordinary item, in a mob's hand, or in a chest. Each is drawn as a
    red sword, with an outline that shows through blocks so a buried blade can be found. Blade
    stands are drawn as a base.
    """
    layer = Layer.Blocks
    bladeColor = (0xe0, 0x10, 0x30, 0xff)
    brokenBladeColor = (0x70, 0x10, 0x20, 0xff)
    guardColor = (0xd8, 0xb0, 0x30, 0xff)
    gripColor = (0x40, 0x22, 0x10, 0xff)
    standColor = (0x5a, 0x3c, 0x28, 0xff)
    outlineColor = (0xff, 0x30, 0x50, 0xff)

    def swordBoxes(self, x, y, z, broken):
        bladeColor = self.brokenBladeColor if broken else self.bladeColor
        return [
            ((x - 0.05, y, z - 0.05), (0.1, 0.35, 0.1), self.gripColor),
            ((x - 0.25, y + 0.35, z - 0.07), (0.5, 0.07, 0.14), self.guardColor),
            ((x - 0.03, y + 0.42, z - 0.09), (0.06, 1.2, 0.18), bladeColor),
        ]

    def makeChunkVertices(self, chunk, limitBox):
        solid = []
        outlines = []
        bounds = self.chunkUpdate.updateTask.worldScene.bounds

        def wanted(pos):
            return not (limitBox and pos not in limitBox) and not (bounds and pos not in bounds)

        for i, entityRef in enumerate(chunk.Entities):
            if i % 10 == 0:
                yield
            tag = entityRef.rootTag
            stand = slashblade.isBladeStand(tag)
            # A blade on the ground or a stand, or one that a mob holds or a minecart carries
            blades = [blade for path, blade in slashblade.findBlades(tag)]
            if not blades and not stand:
                continue
            pos = entityRef.Position
            if not wanted(pos):
                continue
            x, y, z = pos
            if stand:
                solid.append(((x - 0.4, y, z - 0.2), (0.8, 0.12, 0.4), self.standColor))
                y += 0.12
            if blades:
                solid.extend(self.swordBoxes(x, y, z, any(slashblade.isBroken(b) for b in blades)))
                outlines.append(((x - 0.5, y, z - 0.5), (1.0, 1.7, 1.0), self.outlineColor))

        # Chests, barrels, shulker boxes and mods' containers with a blade inside: the block is
        # outlined and a sword stands on it.
        for i, ref in enumerate(chunk.TileEntities):
            if i % 10 == 0:
                yield
            blades = [blade for path, blade in slashblade.findBlades(ref.rootTag)]
            if not blades:
                continue
            x, y, z = ref.Position
            if not wanted(ref.Position):
                continue
            solid.extend(self.swordBoxes(x + 0.5, y + 1.02, z + 0.5, any(slashblade.isBroken(b) for b in blades)))
            outlines.append(((x - 0.02, y - 0.02, z - 0.02), (1.04, 1.04, 1.04), self.outlineColor))

        if not solid:
            return
        yield

        self.sceneNode = Node("slashBlades")
        self.sceneNode.addChild(VertexNode(boxVertices(solid, chunk.chunkPosition)))
        if outlines:
            outlineNode = VertexNode(boxVertices(outlines, chunk.chunkPosition))
            outlineNode.addState(PolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE))
            outlineNode.addState(LineWidth(2.0))
            outlineNode.addState(DepthFunc(GL.GL_ALWAYS))
            self.sceneNode.addChild(outlineNode)
