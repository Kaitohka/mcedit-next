"""
    ${NAME}
"""

import logging
import sys
import collections
import itertools

import numpy

from mcedit2.rendering.layers import Layer
from mcedit2.rendering import chunkupdate
from mcedit2.rendering.players import PlayersNode
from mcedit2.rendering.scenegraph import scenenode
from mcedit2.rendering import renderstates
from mcedit2.rendering.chunkmeshes.entity import chest
from mcedit2.rendering.chunknode import ChunkNode, ChunkGroupNode
from mcedit2.rendering.chunkupdate import ChunkRenderInfo
from mcedit2.rendering.depths import DepthOffsets
from mcedit2.rendering.geometrycache import GeometryCache
from mcedit2.rendering.scenegraph.depth_test import DepthOffset
from mcedit2.rendering.scenegraph.scenenode import Node
from mcedit2.rendering.scenegraph.texture_atlas import TextureAtlasState
from mcedit2.util.glutils import Texture
from mcedit2.util.load_png import loadPNGData

log = logging.getLogger(__name__)



def layerProperty(layer, default=True):
    attr = sys.intern(str("_draw" + layer))

    def _get(self):
        return getattr(self, attr, default)

    def _set(self, val):
        if val != _get(self):
            setattr(self, attr, val)
            self.toggleLayer(val, layer)

    return property(_get, _set)


class SceneUpdateTask(object):
    showRedraw = True
    showHiddenOres = False
    showChunkRedraw = True

    spaceHeight = 64
    targetFPS = 30

    def __init__(self, worldScene, textureAtlas):
        """

        Parameters
        ----------

        worldScene : WorldScene
        textureAtlas : TextureAtlas
        """
        self.worldScene = worldScene

        self.render = True
        self.rotation = 0

        self.alpha = 255

        self.textureAtlas = textureAtlas

        self.mapTextures = {}
        self.modelTextures = {}
        self._chestsComeInHalves = None

        self.renderType = self.worldScene.dimension.blocktypes.renderType

    overheadMode = False

    maxWorkFactor = 64
    minWorkFactor = 1
    workFactor = 2

    def wantsChunk(self, cPos):
        if self.worldScene.bounds is not None:
            if not self.worldScene.bounds.containsChunk(*cPos):
                return False

        chunkInfo = self.worldScene.chunkRenderInfo.get(cPos)
        if chunkInfo is None:
            return True

        return bool(chunkInfo.layersToRender)

    def workOnChunk(self, chunk, visibleSections=None):
        work = 0
        cPos = chunk.chunkPosition

        log.debug("Working on chunk %s sections %s", cPos, visibleSections)
        chunkInfo = self.worldScene.getChunkRenderInfo(cPos)

        chunkInfo.visibleSections = visibleSections  # currently unused

        try:
            chunkUpdate = chunkupdate.ChunkUpdate(self, chunkInfo, chunk)
            for _ in chunkUpdate:
                work += 1
                if (work % SceneUpdateTask.workFactor) == 0:
                    yield

            meshesByRS = collections.defaultdict(list)
            for mesh in chunkUpdate.blockMeshes:
                meshesByRS[mesh.renderstate].append(mesh)

            # Create one ChunkNode for each renderstate group, if needed
            for renderstate in renderstates.allRenderstates:
                groupNode = self.worldScene.getRenderstateGroup(renderstate)
                if groupNode.containsChunkNode(cPos):
                    chunkNode = groupNode.getChunkNode(cPos)
                else:
                    chunkNode = ChunkNode(cPos)
                    groupNode.addChunkNode(chunkNode)

                meshes = meshesByRS[renderstate]
                if len(meshes):
                    meshes = sorted(meshes, key=lambda m: m.layer)
                    log.debug("Updating chunk node for renderstate %s, mesh count %d", renderstate, len(meshes))
                    for layer, layerMeshes in itertools.groupby(meshes, lambda m: m.layer):
                        if layer not in self.worldScene.visibleLayers:
                            continue
                        layerMeshes = list(layerMeshes)

                        # Check if the mesh was re-rendered and remove the old mesh
                        meshTypes = set(type(m) for m in layerMeshes)
                        for arrayNode in list(chunkNode.children):
                            if arrayNode.meshType in meshTypes:
                                chunkNode.removeChild(arrayNode)

                        # Add the scene nodes created by each mesh builder
                        for mesh in layerMeshes:
                            if mesh.sceneNode:
                                mesh.sceneNode.layerName = layer
                                mesh.sceneNode.meshType = type(mesh)
                                chunkNode.addChild(mesh.sceneNode)

                        chunkInfo.renderedLayers.add(layer)

                if chunkNode.childCount() == 0:
                    groupNode.discardChunkNode(*cPos)

        except Exception as e:
            log.exception("Rendering chunk %s failed: %r", cPos, e)

    def chunkNotPresent(self, position):
        # Assume chunk was deleted by the user
        (cx, cz) = position
        for renderstate in renderstates.allRenderstates:
            groupNode = self.worldScene.getRenderstateGroup(renderstate)
            groupNode.discardChunkNode(cx, cz)

    def getMapTexture(self, mapID):

        if mapID in self.mapTextures:
            return self.mapTextures[mapID]
        try:
            mapData = self.worldScene.dimension.worldEditor.getMap(mapID)
        except Exception as e:
            log.exception("Map %s could not be loaded (while loading GL texture)", mapID)
        else:
            colors = mapData.getColorsAsRGBA()

            mapTex = Texture(image=colors.ravel(),
                             width=colors.shape[1],
                             height=colors.shape[0])
            self.mapTextures[mapID] = mapTex
            return mapTex

    def _hasResource(self, path):
        try:
            self.textureAtlas.resourceLoader.openStream(path).close()
            return True
        except Exception:
            return False

    def chestsComeInHalves(self):
        """ True from 1.15, where each half of a double chest has its own texture, in a new layout. """
        if self._chestsComeInHalves is None:
            self._chestsComeInHalves = self._hasResource(chest.NEW_LAYOUT_MARK)
        return self._chestsComeInHalves

    def getModelTexture(self, texturePath):
        if texturePath in self.modelTextures:
            return self.modelTextures[texturePath]

        data = None
        for path in [texturePath] + ENTITY_TEXTURES_SINCE_MOVED.get(texturePath, []):
            try:
                data = self.textureAtlas.resourceLoader.openStream(path).read()
                break
            except Exception:
                continue
        try:
            if data is None:
                raise IOError("Not found in the game's assets")
            w, h, rgba = loadPNGData(data)
            if texturePath in chest.CHEST_TEXTURES and self.chestsComeInHalves():
                # loadPNGData gives the bottom row first.
                shape = numpy.asarray(rgba).shape
                image = numpy.asarray(rgba).reshape(h, w, 4)
                boxes = chest.CHEST_TEXTURES[texturePath]
                rgba = chest.chestTextureInOldLayout(image[::-1], boxes)[::-1].reshape(shape)
        except Exception as e:
            log.exception("Model texture %s could not be loaded", texturePath)
            self.modelTextures[texturePath] = None  # so as not to try again for every chunk
        else:
            modelTex = Texture(image=rgba[::-1], width=w, height=h)
            self.modelTextures[texturePath] = modelTex
            return modelTex


# Where other versions of the game keep textures that models name by older paths, to try in turn. 1.21.5 gave some
# animals a texture for each climate, and 26.1 renamed them again. Villagers lost these professions in 1.14.
ENTITY_TEXTURES_SINCE_MOVED = {
    "assets/minecraft/textures/entity/%s" % old: ["assets/minecraft/textures/entity/%s" % new for new in moved]
    for old, moved in dict({
        "pig/pig.png": ["pig/temperate_pig.png", "pig/pig_temperate.png"],
        "cow/cow.png": ["cow/temperate_cow.png", "cow/cow_temperate.png"],
        "chicken.png": ["chicken/temperate_chicken.png", "chicken/chicken_temperate.png"],
        "sheep/sheep_fur.png": ["sheep/sheep_wool.png"],
        "armorstand/wood.png": ["armorstand/armorstand.png"],
        "zombie_pigman.png": ["piglin/zombified_piglin.png"],
        "shulker/endergolem.png": ["shulker/shulker_purple.png", "shulker/shulker.png"],
        "shulker/shulker.png": ["shulker/shulker_purple.png"],
    }, **{"villager/%s.png" % profession: ["villager/villager.png"]
          for profession in ("farmer", "librarian", "priest", "smith", "butcher")}).items()
}


class WorldScene(scenenode.Node):
    def __init__(self, dimension, textureAtlas=None, geometryCache=None, bounds=None):
        super(WorldScene, self).__init__()

        self.dimension = dimension
        self.textureAtlas = textureAtlas
        self.depthOffset = DepthOffset(DepthOffsets.Renderer)
        self.addState(self.depthOffset)

        self.textureAtlasState = TextureAtlasState(textureAtlas)
        self.addState(self.textureAtlasState)

        self.renderstateNodes = {}
        for rsClass in renderstates.allRenderstates:
            groupNode = ChunkGroupNode()
            groupNode.name = rsClass.__name__
            groupNode.addState(rsClass())
            self.addChild(groupNode)
            self.renderstateNodes[rsClass] = groupNode

        self.chunkRenderInfo = {}
        self.visibleLayers = set(Layer.DefaultVisibleLayers)

        self.updateTask = SceneUpdateTask(self, textureAtlas)

        if geometryCache is None:
            geometryCache = GeometryCache()
        self.geometryCache = geometryCache

        self.showRedraw = False

        self.minlod = 0
        self.bounds = bounds

        self.playersNode = PlayersNode(dimension)
        self.addChild(self.playersNode)

    def setTextureAtlas(self, textureAtlas):
        if textureAtlas is not self.textureAtlas:
            self.textureAtlas = textureAtlas
            self.textureAtlasState.textureAtlas = textureAtlas
            self.updateTask.textureAtlas = textureAtlas
            # Textures from other resources, which may be another version's.
            self.updateTask.modelTextures = {}
            self.updateTask._chestsComeInHalves = None
            self.discardAllChunks()

    def chunkPositions(self):
        return iter(self.chunkRenderInfo.keys())

    def getRenderstateGroup(self, rsClass):
        groupNode = self.renderstateNodes.get(rsClass)

        return groupNode

    def discardChunk(self, cx, cz):
        """
        Discard the chunk at the given position from the scene
        """
        for groupNode in self.renderstateNodes.values():
            groupNode.discardChunkNode(cx, cz)
        self.chunkRenderInfo.pop((cx, cz), None)

    def discardChunks(self, chunks):
        for cx, cz in chunks:
            self.discardChunk(cx, cz)

    def discardAllChunks(self):
        for groupNode in self.renderstateNodes.values():
            groupNode.clear()
        self.chunkRenderInfo.clear()

    def invalidateChunk(self, cx, cz, invalidLayers=None):
        """
        Mark the chunk for regenerating vertex data
        """
        if invalidLayers is None:
            invalidLayers = Layer.AllLayers

        node = self.chunkRenderInfo.get((cx, cz))
        if node:
            node.invalidLayers.update(invalidLayers)

    _fastLeaves = False

    @property
    def fastLeaves(self):
        return self._fastLeaves

    @fastLeaves.setter
    def fastLeaves(self, val):
        if self._fastLeaves != bool(val):
            self.discardAllChunks()

        self._fastLeaves = bool(val)

    _roughGraphics = False

    @property
    def roughGraphics(self):
        return self._roughGraphics

    @roughGraphics.setter
    def roughGraphics(self, val):
        if self._roughGraphics != bool(val):
            self.discardAllChunks()

        self._roughGraphics = bool(val)

    _showHiddenOres = False

    @property
    def showHiddenOres(self):
        return self._showHiddenOres

    @showHiddenOres.setter
    def showHiddenOres(self, val):
        if self._showHiddenOres != bool(val):
            self.discardAllChunks()

        self._showHiddenOres = bool(val)

    def wantsChunk(self, cPos):
        return self.updateTask.wantsChunk(cPos)

    def workOnChunk(self, chunk, visibleSections=None):
        return self.updateTask.workOnChunk(chunk, visibleSections)

    def chunkNotPresent(self, cPos):
        self.updateTask.chunkNotPresent(cPos)

    def getChunkRenderInfo(self, cPos):
        chunkInfo = self.chunkRenderInfo.get(cPos)
        if chunkInfo is None:
            #log.info("Creating ChunkRenderInfo %s in %s", cPos, self.worldScene)
            chunkInfo = ChunkRenderInfo(self, cPos)
            self.chunkRenderInfo[cPos] = chunkInfo

        return chunkInfo

    def setLayerVisible(self, layerName, visible):
        if visible:
            self.visibleLayers.add(layerName)
        else:
            self.visibleLayers.discard(layerName)

        for groupNode in self.renderstateNodes.values():
            groupNode.setLayerVisible(layerName, visible)

    def setVisibleLayers(self, layerNames):
        self.visibleLayers = set(layerNames)
