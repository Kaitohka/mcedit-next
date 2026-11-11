#cython: boundscheck=False, language_level=3
"""
    blockmodels
"""
from collections import defaultdict
import json
import logging
import math
import itertools

import numpy as np
cimport numpy as cnp
from mcedit2.util import resources

cnp.import_array()

from mcedit2.resourceloader import ResourceNotFound
from mcedit2.rendering import forge_blockstates, objmodels, connected, colladamodels

from mceditlib.blocktypes import BlockType
from mceditlib.cachefunc import lru_cache
from mceditlib.geometry import Vector
from mceditlib.selection import FloatBox
from mceditlib.blocktypes import splitInternalName

from libc.stdlib cimport malloc, free
from libc.string cimport memset

log = logging.getLogger(__name__)

# Loaders that make a model of other models, each written out in full in the model's file: NeoForge's, and Forge's
# from 1.19.
COMPOSITE_LOADERS = (u"neoforge:composite", u"forge:composite")
# Loaders that read a Wavefront .obj file: NeoForge's, and Forge's from 1.13.
OBJ_LOADERS = (u"neoforge:obj", u"forge:obj")
# Loaders whose models are written like the game's and only add to them: Fusion's, whose connecting textures are
# drawn from the textures' .mcmeta files and the model's "connections".
PLAIN_LOADERS = (u"fusion:model",)
# The export mod's models of faces the game drew, for blocks a mod draws in code.
QUADS_LOADER = u"mcedit:quads"

cdef enum:
    MAX_TEXTURE_RECURSIONS = 200

cdef struct ModelQuad:
    float[32] xyzuvstc
    char[4] cullface  # isCulled, dx, dy, dz
    char[4] quadface  # face, dx, dy, dz
    char biomeTintType

cdef struct ModelQuadList:
    int count
    ModelQuad *quads

cdef class ModelQuadListObj(object):
    pass  # defined in blockmodels.pxd


cdef class FaceInfo(object):
    cdef:
        float x1, y1, z1, x2, y2, z2
        short face, cullface
        unicode texture
        short u1, v1, u2, v2
        char shade
        float ox, oy, oz
        cnp.ndarray elementMatrix, variantMatrix
        int textureRotation
        int variantXrot, variantYrot
        object tintcolor
        char biomeTintType
        object points  # for a face of an OBJ model, its corners: (x, y, z, u, v) * 4, u and v from 0 to 1
        public object connection  # how a connected texture of the face connects, where its model says

    def __init__(self, x1, y1, z1, x2, y2, z2, face,
                 texture, u1, v1, u2, v2, cullface,
                 shade, ox, oy, oz, elementMatrix, textureRotation,
                 variantXrot, variantYrot, variantMatrix, tintcolor, biomeTintType, points=None):
        self.points = points
        self.connection = None
        self.x1, self.y1, self.z1 = x1, y1, z1
        self.x2, self.y2, self.z2 = x2, y2, z2
        self.face = face
        self.texture = texture
        self.u1, self.v1, self.u2, self.v2 = u1, v1, u2, v2
        self.cullface = cullface
        self.shade = shade
        self.ox = ox
        self.oy = oy
        self.oz = oz
        self.elementMatrix = elementMatrix
        self.textureRotation = textureRotation
        self.variantXrot = variantXrot
        self.variantYrot = variantYrot
        self.variantMatrix = variantMatrix
        self.tintcolor = tintcolor
        self.biomeTintType = biomeTintType

UNKNOWN_MODEL = {
    u"parent": u"block/cube_all",
    u"textures": {
        u"all": u"MCEDIT_UNKNOWN"
    }
}
"""
Some blocks have blockStates not represented in the world file.
These will have additional blockStates with corresponding resourceVariants that do not
have a unique ID/meta combination.

To make these additional block states renderable, we will need to have a function
`getActualBlockState` to get the state, and another function to get the resourceVariant
for that state. We will also need to load the model specified by the resourceVariant
into BlockModels.

Since BlockModels only stores models in an ID/meta lookup table, these "extra" models
will need to be stored differently. Storing all of the models in a dict keyed to the
blockstate would be expensive, as we'd need up to 4k dict lookups per section.

One possible solution is to assign an internal numeric ID to each
internalName+blockState combination, and use a table to map ID/meta to our own internal
ID, and use a dict to map the internalName+blockState to our internal ID. This skips
the dict lookup for states that actually are represented as ID/meta, so only states
that are returned by `getActualBlockState` will be looked up textually.


"""
def splitResourceName(name):
    """
    Return the namespace and path of a name like "minecraft:block/stone". Names with no namespace are
    in the game's own.
    """
    namespace, separator, path = name.rpartition(":")
    return (namespace, path) if separator else ("minecraft", name)


def _pngSize(resourceLoader, path):
    """ The width and height of a PNG file, from its header. """
    try:
        header = resourceLoader.openStream(path).read(24)
    except (ResourceNotFound, IOError):
        return None
    if len(header) < 24 or header[12:16] != b"IHDR":
        return None
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def _atlasRect(part, texCoordsByName):
    """ A part of a texture as (left, top, width, height) on the atlas, the top being the higher v. """
    coords = texCoordsByName.get(part.texture)
    if coords is None:
        return None
    l, t, w, h = coords
    frame = w if h > w else h  # an animated texture shows its frame at the bottom of the strip, as faces do
    return (l + part.x * w, t + frame - part.y * frame, part.w * w, part.h * frame)


def _connectedRects(found, dict texCoordsByName, base=None):
    """
    For each mask, or each tile of a texture chosen by position, (kind, the 16 numbers of its rects, how many), or
    None if a texture is not on the atlas: kind 1 is one rect for the whole face, 0 one for each quarter, 2 up to four
    drawn one over another. A part that draws nothing, as an overlay's "<skip>", has a rect of no size.

    :param base: the rect of the face's own texture, (left, top, width, height), for parts that leave it as it was
    """
    cache = found.__dict__.setdefault("_rects", {})
    key = (id(texCoordsByName), base if found.usesDefault else None)
    cached = cache.get(key)
    if cached is not None and cached[0] is texCoordsByName:
        return cached[1]

    def rectOf(part):
        if part.texture == connected.SKIP:
            return (0, 0, 0, 0)
        if part.texture == connected.DEFAULT:
            if base is None:
                return None
            l, top, w, h = base
            return (l + part.x * w, top - part.y * h, part.w * w, part.h * h)
        return _atlasRect(part, texCoordsByName)

    result = []
    for entry in found.parts:
        if isinstance(entry, connected.Layers):
            rects = [rectOf(part) for part in entry[:4]]
            kind, count = 2, len(rects)
            rects += [(0, 0, 0, 0)] * (4 - count)
        elif isinstance(entry, list):
            rects = [rectOf(part) for part in entry]
            kind, count = 0, 4
        else:
            rects = [rectOf(entry)] + [(0, 0, 0, 0)] * 3
            kind, count = 1, 1
        if any(r is None for r in rects):
            log.warning("A texture of %r is not on the atlas", found)
            return None
        result.append((kind, [value for rect in rects for value in rect], count))
    obscured = rectOf(found.obscured) if found.obscured is not None else None
    if found.obscured is not None and obscured is None:
        log.warning("A texture of %r is not on the atlas", found)
        return None
    result = (result, obscured)
    cache[key] = texCoordsByName, result
    return result


def _axis(x, y, z):
    """ The block offset nearest a direction. """
    m = max(abs(x), abs(y), abs(z))
    if m == 0:
        return None
    if abs(x) == m:
        return (1 if x > 0 else -1, 0, 0)
    if abs(y) == m:
        return (0, 1 if y > 0 else -1, 0)
    return (0, 0, 1 if z > 0 else -1)


def _axisIndex(offset):
    """ Which axis a block offset is along: 0, 1 or 2, or -1 for none. """
    for k in range(3):
        if offset[k]:
            return k
    return -1


cdef _textureAxes(float *q, float l, float t, float w, float h):
    """
    The block offsets toward the right of a face's texture and toward its top, worked out from where its texture
    coordinates are on its corners. None if they don't span the face.
    """
    top = t + h
    p = [(q[i * 8], q[i * 8 + 1], q[i * 8 + 2], (q[i * 8 + 3] - l) / w, (top - q[i * 8 + 4]) / h) for i in range(4)]
    e1 = [p[1][k] - p[0][k] for k in range(5)]
    e2 = [p[3][k] - p[0][k] for k in range(5)]
    det = e1[3] * e2[4] - e2[3] * e1[4]
    if abs(det) < 1e-9:
        e2 = [p[2][k] - p[0][k] for k in range(5)]
        det = e1[3] * e2[4] - e2[3] * e1[4]
        if abs(det) < 1e-9:
            return None
    # d(position)/du and d(position)/dv, v counting down from the top of the texture
    du = [(e1[k] * e2[4] - e2[k] * e1[4]) / det for k in range(3)]
    dv = [(e2[k] * e1[3] - e1[k] * e2[3]) / det for k in range(3)]
    right = _axis(*du)
    up = _axis(-dv[0], -dv[1], -dv[2])
    if right is None or up is None:
        return None
    return right, up


_variantKeys = {}


def parseVariantKey(key):
    parsed = _variantKeys.get(key)
    if parsed is None:
        parsed = _variantKeys[key] = [tuple(pair.split('=', 1)) for pair in key.split(',') if '=' in pair]
    return parsed


def findMatchingVariant(variants, resourceVariant):
    """
    Return the entry for a block state from the `variants` of a blockstates file. Keys need not name
    all of a block's properties, so a state matches a key if it has the values the key gives. The empty
    key matches every state.
    """
    state = dict(parseVariantKey(resourceVariant))
    for key, entry in variants.items():
        for name, value in parseVariantKey(key):
            if state.get(name) != value:
                break
        else:
            return entry
    return None


def matchWhen(when, variantMap):
    ok = True
    for key, val in when.items():
        if key == 'OR':
            ok = False
            for when2 in val:
                ok |= matchWhen(when2, variantMap)
        elif key == 'AND':
            for when2 in val:
                ok &= matchWhen(when2, variantMap)
        else:
            varVal = variantMap.get(key)

            if varVal is None:
                ok = False
            matchVals = str(val).lower().split("|")

            if str(varVal).lower() not in matchVals:
                ok = False

    return ok


# Attributes that BlockType works out rather than reading from the state's entry.
_WORKED_OUT_ATTRIBUTES = frozenset(["nameAndState", "json", "stateDict"])


cdef class _StateAttributes(object):
    """
    Reads a block's attributes from its entry in its block type set, found once. Reading them from the BlockType
    finds the entry again each time, which adds up over every attribute of every state of every block.
    """
    cdef object block
    cdef object info
    cdef dict defaults

    def __init__(self, block, statesByID, blockJsons, dict defaults):
        self.block = block
        self.defaults = defaults
        self.info = None
        nameAndState = statesByID.get((block.ID, block.meta))
        if nameAndState is None:
            nameAndState = statesByID.get((block.ID, 0))
        if nameAndState is not None:
            self.info = blockJsons.get(nameAndState)

    def __getattr__(self, name):
        block = self.block
        own = block.__dict__
        if name in own:
            return own[name]
        if self.info is None or name in _WORKED_OUT_ATTRIBUTES or name not in self.defaults:
            return getattr(block, name)
        value = self.info.get(name)
        if value is None:
            return self.defaults[name]
        return value


cdef class BlockModels(object):
    def _getBlockModel(self, modelName):
        if modelName == u"block/MCEDIT_UNKNOWN" or modelName == u"MCEDIT_UNKNOWN":
            return UNKNOWN_MODEL

        return self._getBlockModelByPath("assets/%s/models/%s.json" % splitResourceName(modelName))

    def _getBlockModelByPath(self, modelPath):
        model = self.modelBlockJsons.get(modelPath)
        if model is None:
            model = json.load(self.resourceLoader.openStream(modelPath))
            self.modelBlockJsons[modelPath] = model
        return model

    def _getBlockState(self, stateName, fallback=False):
        if stateName == u"MCEDIT_UNKNOWN":
            return {
                u"variants": {
                    u"MCEDIT_UNKNOWN": {
                        u"model": u"MCEDIT_UNKNOWN",
                    }
                }
            }
        state = self.modelStateJsons.get((stateName, fallback))
        if state is None:
            path = "assets/%s/blockstates/%s.json" % splitResourceName(stateName)
            state = json.load(self.resourceLoader.openStream(path, fallback))
            self.modelStateJsons[stateName, fallback] = state
        return state

    def __init__(self, blocktypes, resourceLoader):
        """

        :param blocktypes:
        :type blocktypes: mceditlib.blocktypes.BlockTypeSet
        :param resourceLoader:
        :type resourceLoader: ResourceLoader
        :return:
        :rtype: BlockModels
        """
        self.resourceLoader = resourceLoader
        self.blocktypes = blocktypes
        self.connectedTextures = None
        self.connectedByBlock = None

        self.modelBlockJsons = {}
        self.modelStateJsons = {}
        self.quadsByResourcePathVariant = {}
        self.blockStatesByResourcePathVariant = defaultdict(list)
        self.cookedModelsByBlockState = {}

        self._texturePaths = set()
        self.firstTextures = {}  # first texture found for each block - used for icons (xxx)
        self.cookedModels = {}  # nameAndState -> list[(xyzuvstc, cullface)]

        memset(self.cookedModelsByID, 0, sizeof(self.cookedModelsByID))
        self.cooked = False

        missingnoProxy = BlockType(-1, -1, blocktypes)
        missingnoProxy.internalName = missingnoProxy.nameAndState = u"MCEDIT_UNKNOWN"
        missingnoProxy.blockState = ""

        missingnoProxy.renderType = 3
        missingnoProxy.resourcePath = u"MCEDIT_UNKNOWN"
        missingnoProxy.resourceVariant = u"MCEDIT_UNKNOWN"
        missingnoProxy.color = 0xFFFFFF
        log.info("Loading block models...")

        cdef dict modelDict, textures, textureVars
        cdef list elements
        cdef int i
        cdef short variantXrot, variantYrot
        cdef char biomeTintType

        if hasattr(blocktypes, "modelForLateState"):
            blocktypes.modelForLateState = self.copyModel

        statesByID = getattr(blocktypes, "statesByID", None)
        blockJsons = getattr(blocktypes, "blockJsons", None)
        defaults = getattr(blocktypes, "defaults", None)
        quickAttributes = statesByID is not None and blockJsons is not None and isinstance(defaults, dict)

        for i, block in enumerate(list(blocktypes) + [missingnoProxy]):
            if i % 1000 == 0:
                log.info("Loading block models %s/%s", i, len(blocktypes))
            if quickAttributes:
                block = _StateAttributes(block, statesByID, blockJsons, defaults)

            if block.renderType != 3:  # only rendertype 3 uses block models
                continue

            internalName = block.internalName
            blockState = block.blockState
            resourcePath = block.resourcePath
            modelDict = None
            parts = None
            fallback = block.fallbackModel

            if block.forcedModel is not None:  # user-configured block
                modelDict = self._getBlockModelByPath(block.forcedModel)
                if modelDict is None:
                    log.debug("No model found for %s", internalName)
                    continue

                # jam the custom models into quadsByResourcePathVariant and blockStatesByResourcePathVariant
                resourcePath = "MCEDIT_CUSTOM_" + internalName
                resourceVariant = "MCEDIT_CUSTOM_" + blockState
                self.blockStatesByResourcePathVariant[resourcePath, resourceVariant].append((internalName, blockState))
                variantXrot = variantYrot = 0
                if block.forcedModelRotation:
                    variantXrot = block.forcedModelRotation[0]
                    variantYrot = block.forcedModelRotation[1]

                parts = [(modelDict, variantXrot, variantYrot)]

            elif fallback is not None:
                # Blocks with the same box and texture can share their quads.
                resourcePath = u"MCEDIT_FALLBACK"
                resourceVariant = repr(fallback)
                self.blockStatesByResourcePathVariant[resourcePath, resourceVariant].append((internalName, blockState))
                if (resourcePath, resourceVariant) not in self.quadsByResourcePathVariant:
                    parts = [(fallback["model"], fallback["x"], fallback["y"])]

            elif resourcePath is not None and blocktypes.modelNamesAreFull:
                # Many states use the same models, such as those that differ only by being waterlogged.
                # They share their quads, which also takes far less time and memory.
                entries = None
                if block.modelSource not in ("fallback", "generated"):
                    entries = self._modelEntries(resourcePath, block.resourceVariant)
                generated = block.generatedModel
                if generated:
                    entries = (entries or []) + [{"model": generated}]

                if entries is not None:
                    resourceVariant = tuple((e["model"], e.get("x", 0), e.get("y", 0)) for e in entries)
                    if not entries:
                        # A multipart block with no part for this state, like a wall with no sides and no post.
                        self.quadsByResourcePathVariant[resourcePath, resourceVariant] = []
                    elif (resourcePath, resourceVariant) not in self.quadsByResourcePathVariant:
                        parts = self._loadModelEntries(entries)
                        if parts:
                            self.loadModelParts(block, resourcePath, resourceVariant, parts)
                    if (resourcePath, resourceVariant) in self.quadsByResourcePathVariant:
                        self.blockStatesByResourcePathVariant[resourcePath, resourceVariant].append((internalName, blockState))
                        continue
                    # Its models could not be read. Mods can make models in code, which leaves no file.

                # A block with no usable model, or whose pack has no assets. If it has a texture to go by,
                # that is better than an unknown block.
                texture = block.particleTexture
                if not texture or texture.endswith(":missingno"):
                    if block.modelSource == "fallback" and block.hasBlockstateFile:
                        # It has model files with nothing in them, and no texture: there is nothing to see,
                        # as with a block that only exists to take up space for another.
                        resourceVariant = u"MCEDIT_NOTHING"
                        self.quadsByResourcePathVariant[u"MCEDIT_FALLBACK", resourceVariant] = []
                        self.blockStatesByResourcePathVariant[u"MCEDIT_FALLBACK", resourceVariant].append((internalName, blockState))
                    continue

                resourcePath = u"MCEDIT_FALLBACK"
                resourceVariant = texture
                self.blockStatesByResourcePathVariant[resourcePath, resourceVariant].append((internalName, blockState))
                if (resourcePath, resourceVariant) not in self.quadsByResourcePathVariant:
                    parts = [({"parent": "block/cube_all", "textures": {"all": texture}}, 0, 0)]
                    self.loadModelParts(block, resourcePath, resourceVariant, parts)
                continue

            elif resourcePath is not None and block.generatedModel:
                # Faces the game drew, which the export mod wrote into a model, with the block's own if it has one.
                entries = []
                if block.modelSource not in ("fallback", "generated"):
                    entries = self._modelEntries(resourcePath, block.resourceVariant) or []
                entries = entries + [{"model": block.generatedModel}]
                resourceVariant = tuple((e["model"], e.get("x", 0), e.get("y", 0)) for e in entries)
                self.blockStatesByResourcePathVariant[resourcePath, resourceVariant].append((internalName, blockState))
                if (resourcePath, resourceVariant) not in self.quadsByResourcePathVariant:
                    parts = self._loadModelEntries(entries)

            elif resourcePath is not None:
                resourceVariant = block.resourceVariant
                self.blockStatesByResourcePathVariant[resourcePath, resourceVariant].append((internalName, blockState))
                parts = self.loadResourceVariant(resourcePath, resourceVariant)

            if parts is None or len(parts) == 0:
                continue

            self.loadModelParts(block, resourcePath, resourceVariant, parts)

        if not blocktypes.hasHiddenStates:
            return

        hiddenModels = json.loads(open(resources.resourcePath("mcedit2/rendering/hiddenstates_1_12.json"), "rb").read())
        log.info("Loading %s hidden blockState models...", len(hiddenModels))
        hiddensLoaded = 0
        for i, hidden in enumerate(hiddenModels):
            if i % 500 == 0:
                log.debug("Loading hidden blockState models %s/%s", i, len(hiddenModels))

            nameAndState = hidden['blockState']
            resourcePath = hidden['resourcePath']
            resourceVariant = hidden['resourceVariant']
            if nameAndState in self.blockStatesByResourcePathVariant[resourcePath, resourceVariant]:
                log.debug("Model for state %s previously loaded", nameAndState)
                continue

            internalName, blockState = splitInternalName(nameAndState)
            block = blocktypes.get(internalName, None)
            if block is None:
                log.debug("No block found for block %s", internalName)
                continue

            self.blockStatesByResourcePathVariant[resourcePath, resourceVariant].append((internalName, blockState))

            if (resourcePath, resourceVariant) in self.quadsByResourcePathVariant:
                log.debug("Model for variant %s#%s previously loaded", resourcePath, resourceVariant)
                continue

            parts = self.loadResourceVariant(resourcePath, resourceVariant)
            if parts is None:
                log.debug("No blockstates file found for %s: %s#%s", nameAndState, resourcePath, resourceVariant)
                continue

            self.loadModelParts(block, resourcePath, resourceVariant, parts)
            hiddensLoaded += 1

        log.info("Found %s additional models for hidden states", hiddensLoaded)

    def loadModelParts(self, block, resourcePath, resourceVariant, parts):
        quads = []
        anyLoaded = False
        for modelDict, variantXrot, variantYrot in parts:
            res = self._loadModel(block, resourcePath, resourceVariant, modelDict, variantXrot, variantYrot)
            if res is not None:  # None when a parent model is missing or broken
                anyLoaded = True
                quads.extend(res)

        # A model can properly have nothing in it, but with no model at all the block is an unknown block.
        if anyLoaded:
            self.quadsByResourcePathVariant[resourcePath, resourceVariant] = quads

    def _loadModel(self, block, resourcePath, resourceVariant, modelDict, variantXrot, variantYrot):

        # model will either have an 'elements' key or a 'parent' key (maybe both).
        # 'parent' will be the name of a model
        # following 'parent' keys will eventually lead to a model with 'elements'
        #
        # 'elements' is a list of dicts each describing a box that makes up the model.
        # each box dict has 'from' and 'to' keys, which are lists of 3 float coordinates.
        #
        # the 'crossed squares' model demonstrates most of the keys found in a box element
        #
        # {   "from": [ 0.8, 0, 8 ],
        #     "to": [ 15.2, 16, 8 ],
        #     "rotation": { "origin": [ 8, 8, 8 ], "axis": "y", "angle": 45, "rescale": true },
        #     "shade": false,
        #     "faces": {
        #         "north": { "uv": [ 0, 0, 16, 16 ], "texture": "#cross" },
        #         "south": { "uv": [ 0, 0, 16, 16 ], "texture": "#cross" }
        #     }
        # }
        #
        # model may also have a 'textures' dict which assigns a texture file to a texture variable,
        # or a texture variable to another texture variable.
        #
        # the result of loading a model should be a list of quads, each with four vertexes, four pairs of
        # texture coordinates, four RGBA values for shading, plus a Face telling which adjacent block when
        # present causes that quad to be culled.

        # grab textures and elements from this model, then get parent and merge its textures and elements
        # continue until no parent is found
        textureVars = {}
        allElements = []
        hasElements = False
        compositeChildren = None  # for a model made of other models, by NeoForge's composite loader
        objVisibility = {}  # for an OBJ model, its parts to hide, given by it or by a model with it for a parent
        objTransform = None
        fusionConnection = None  # how the connected textures of a Fusion connecting model connect

        for i in range(MAX_TEXTURE_RECURSIONS):
            visibility = modelDict.get("visibility")
            if isinstance(visibility, dict):
                for k, v in visibility.items():
                    objVisibility.setdefault(k, bool(v))
            if objTransform is None and modelDict.get("transform") is not None:
                objTransform = modelDict["transform"]
            if fusionConnection is None:
                fusionConnection = connected.modelConnections(modelDict)
            textures = modelDict.get("textures")
            if textures is not None:
                # Resolve texture variables in parent to values in child
                for k, v in textures.items():
                    if k in textureVars:
                        # Child always overrides parent
                        continue
                    if isinstance(v, dict):
                        # Since 26.1 a texture can be an object, with the texture itself in "sprite".
                        v = v.get("sprite", "")
                    if v.startswith("#"):
                        v = textureVars.get(v[1:], v)
                    textureVars[k] = v
                    
            # As in the game, the nearest model with elements gives all of them, and a parent's are not added:
            # a model can have a cube for a parent only to get its textures and display settings.
            if not hasElements:
                elements = modelDict.get("elements")
                loader = modelDict.get("loader")
                if loader in PLAIN_LOADERS:
                    loader = None
                if elements is not None:
                    allElements.extend(elements)
                    hasElements = True
                elif loader in COMPOSITE_LOADERS:
                    visibility = modelDict.get("visibility") or {}
                    compositeChildren = [child for name, child in (modelDict.get("children") or {}).items()
                                         if visibility.get(name, True)]
                    hasElements = True
                elif loader == QUADS_LOADER:
                    return self._loadQuadsModel(block, modelDict, variantXrot, variantYrot)
                elif loader in OBJ_LOADERS and isinstance(modelDict.get("model"), str):
                    for k, v in (modelDict.get("textures") or {}).items():
                        textureVars.setdefault(k, v.get("sprite", "") if isinstance(v, dict) else v)
                    self._parentTextures(modelDict, textureVars)
                    return self._loadObjModel(block, modelDict, textureVars, objVisibility, objTransform,
                                              variantXrot, variantYrot)
                elif loader is not None:
                    # A loader of a mod's own. Some name a model file, as HBM's do: an .obj file, or a Collada
                    # .dae file, drawn as the loader would. Others make the model in code, leaving nothing here.
                    if isinstance(modelDict.get("model"), str):
                        for k, v in (modelDict.get("textures") or {}).items():
                            textureVars.setdefault(k, v.get("sprite", "") if isinstance(v, dict) else v)
                        self._parentTextures(modelDict, textureVars)
                        quads = self._loadModelFile(block, modelDict, textureVars, objVisibility, objTransform,
                                                    variantXrot, variantYrot)
                        if quads is not None:
                            return quads
                    log.debug("Model loader %s of %s is not supported", loader, block.nameAndState)
                    return None

            parentName = modelDict.get("parent")
            if parentName is None or parentName.startswith("builtin/") or parentName.startswith("minecraft:builtin/"):
                break  # the builtin models are for items, and are made by the game, not read from a file
            try:
                modelDict = self._getBlockModel(parentName)
            except Exception as e:
                log.exception("Error parsing json for block/%s: %s", parentName, e)
                return
        else:
            log.error("Parent loop detected in block model for %s" % block.nameAndState)
            return

        if block.forcedModelTextures:  # user-configured model textures
            for var, tex in block.forcedModelTextures.items():
                textureVars[var.lstrip("#")] = tex

        if compositeChildren is not None:
            # Each child is a model of its own. Its textures come first, then those of the models around it.
            quads = []
            anyLoaded = False
            for child in compositeChildren:
                childDict = dict(child)
                childTextures = dict(textureVars)
                childTextures.update(child.get("textures") or {})
                childDict["textures"] = childTextures
                res = self._loadModel(block, resourcePath, resourceVariant, childDict, variantXrot, variantYrot)
                if res is not None:
                    anyLoaded = True
                    quads.extend(res)
            return quads if anyLoaded else None

        if block.biomeTintType == "grass":
            biomeTintType = BIOME_GRASS
        elif block.biomeTintType == "foliage":
            biomeTintType = BIOME_FOLIAGE
        elif block.biomeTintType == "foliagePine":
            biomeTintType = BIOME_FOLIAGE_PINE
        elif block.biomeTintType == "foliageBirch":
            biomeTintType = BIOME_FOLIAGE_BIRCH
        else:
            biomeTintType = BIOME_NONE

        # each element describes a box with up to six faces, each with a texture. convert the box into
        # quads.
        allQuads = []

        try:

            if block.internalName == "minecraft:redstone_wire":
                blockColor = (0xff, 0xff, 0xff)
                biomeTintType = BIOME_REDSTONE
            else:
                blockColor = block.color
                r = (blockColor >> 16) & 0xff
                g = (blockColor >> 8) & 0xff
                b = blockColor & 0xff
                blockColor = r, g, b

            variantMatrix = variantRotation(variantXrot, variantYrot)
            for element in allElements:
                quads = self.buildBoxQuads(element, block.nameAndState, textureVars,
                                           variantXrot, variantYrot,
                                           variantMatrix, blockColor, biomeTintType)
                if quads:
                    if fusionConnection is not None:
                        for quad in quads:
                            quad.connection = fusionConnection
                    allQuads.extend(quads)


        except Exception as e:
            log.exception("Failed to parse variant of block %s\nelements:\n%s\ntextures:\n%s",
                      block.nameAndState, allElements, textureVars)

        return allQuads

    def _parentTextures(self, dict modelDict, dict textureVars):
        """
        Add the textures the model's parents give to textureVars, where the model and those before it gave none
        of that name. A model read from a file, as an OBJ model, has its file named by the model nearest the
        block, but its materials can name textures that a parent gives, as those of Create's wheels do.
        """
        for i in range(MAX_TEXTURE_RECURSIONS):
            parentName = modelDict.get("parent")
            if parentName is None or parentName.startswith("builtin/") or parentName.startswith("minecraft:builtin/"):
                return
            try:
                modelDict = self._getBlockModel(parentName)
            except Exception as e:
                log.info("Could not read the parent model %s for its textures: %r", parentName, e)
                return
            for k, v in (modelDict.get("textures") or {}).items():
                if k in textureVars:
                    continue
                if isinstance(v, dict):
                    v = v.get("sprite", "")
                if v.startswith("#"):
                    v = textureVars.get(v[1:], v)
                textureVars[k] = v

    def _blockTint(self, block):
        """ The color of a block's tinted faces as (r, g, b), and the kind of biome color it takes. """
        biomeTintType = {"grass": BIOME_GRASS, "foliage": BIOME_FOLIAGE, "foliagePine": BIOME_FOLIAGE_PINE,
                         "foliageBirch": BIOME_FOLIAGE_BIRCH}.get(block.biomeTintType, BIOME_NONE)
        color = block.color
        return ((color >> 16) & 0xff, (color >> 8) & 0xff, color & 0xff), biomeTintType

    def _loadModelFile(self, block, dict modelDict, dict textureVars, dict visibility, transform,
                       short variantXrot, short variantYrot):
        """
        The faces of the model file a mod's own loader names, by itself or with ".obj" or ".dae" after it, or None.
        Such a model is made about the middle of its block, as these loaders place it, unless it says otherwise.
        """
        name = modelDict["model"]
        names = [name] if name.endswith(".obj") or name.endswith(".dae") else [name + ".obj", name + ".dae"]
        if transform is None:
            transform = {"translation": [0.5, 0, 0.5]}
        for candidate in names:
            for path in objmodels.modelPaths(candidate):
                try:
                    data = self.resourceLoader.openStream(path).read()
                except ResourceNotFound:
                    continue
                if candidate.endswith(".obj"):
                    found = dict(modelDict)
                    found["model"] = candidate
                    found.setdefault("flip_v", True)
                    return self._loadObjModel(block, found, textureVars, visibility, transform, variantXrot,
                                              variantYrot)
                try:
                    textureKey = "default" if "default" in textureVars else next(
                        (k for k in textureVars if k != "particle"), "particle")
                    faces = colladamodels.colladaFaces(data, "#" + textureKey,
                                                       bool(modelDict.get("flip_v", True)),
                                                       objmodels.rootTransform(transform))
                except Exception as e:
                    log.warning("Could not read Collada model %s of %s: %r", candidate, block.nameAndState, e)
                    return None
                return self._modelFileQuads(block, faces, textureVars, variantXrot, variantYrot)
        return None

    def _modelFileQuads(self, block, faces, dict textureVars, short variantXrot, short variantYrot):
        variantMatrix = variantRotation(variantXrot, variantYrot)
        quads = []
        for face in faces:
            texture = face.texture
            for i in range(MAX_TEXTURE_RECURSIONS):
                if texture is not None and texture.startswith("#"):
                    texture = textureVars.get(texture[1:])
                else:
                    break
            quads.append(self._pointsFace(block, texture, face.corners, face.side, face.cullface, face.shade,
                                          face.tintindex, face.color[:3], variantXrot, variantYrot, variantMatrix))
        return quads

    def _loadObjModel(self, block, dict modelDict, dict textureVars, dict visibility, transform,
                      short variantXrot, short variantYrot):
        """ The faces of an OBJ model, or None if its file can't be read. """
        def readText(path):
            return self.resourceLoader.openStream(path).read().decode("utf-8", "replace")

        modelName = modelDict["model"]
        for path in objmodels.modelPaths(modelName):
            try:
                text = readText(path)
                break
            except ResourceNotFound:
                continue
        else:
            log.info("Could not get OBJ model %s of %s", modelName, block.nameAndState)
            return None

        try:
            faces = objmodels.objFaces(text, bool(modelDict.get("flip_v", False)), readText, path,
                                       modelDict.get("mtl_override"), visibility,
                                       objmodels.rootTransform(transform),
                                       bool(modelDict.get("automatic_culling", True)),
                                       bool(modelDict.get("shade_quads", True)),
                                       bool(modelDict.get("emissive_ambient", True)))
        except (ValueError, IndexError, TypeError, KeyError) as e:
            log.warning("Could not read OBJ model %s of %s: %r", modelName, block.nameAndState, e)
            return None

        variantMatrix = variantRotation(variantXrot, variantYrot)
        quads = []
        for face in faces:
            texture = face.texture
            for i in range(MAX_TEXTURE_RECURSIONS):
                if texture is not None and texture.startswith("#"):
                    texture = textureVars.get(texture[1:])
                else:
                    break
            quads.append(self._pointsFace(block, texture, face.corners, face.side, face.cullface, face.shade,
                                          face.tintindex, face.color[:3], variantXrot, variantYrot, variantMatrix))
        return quads

    def _pointsFace(self, block, texture, corners, side, cullface, shade, tintindex, color,
                    short variantXrot, short variantYrot, variantMatrix):
        """
        A face with corners of its own, as (x, y, z, u, v) in blocks and from 0 to 1 across its texture. The
        texture is a texture name, a file as "namespace:textures/entity/thing.png", or None for the missing texture.
        """
        if texture is None or texture.endswith(":missingno"):
            texture = u"MCEDIT_UNKNOWN"  # the game's missing texture
        if texture != u"MCEDIT_UNKNOWN":
            if texture.endswith(".png"):
                texture = "assets/%s/%s" % splitResourceName(texture)
            else:
                texture = "assets/%s/textures/%s.png" % splitResourceName(texture)
        self.firstTextures.setdefault(block.nameAndState, texture)
        self._texturePaths.add(texture)

        # The face's own color, and the block's where the face takes it.
        blockColor, biomeTint = self._blockTint(block)
        r, g, b = (int(round(c * 255)) for c in color)
        biomeTintType = BIOME_NONE
        if tintindex >= 0:
            if biomeTint != BIOME_NONE:
                biomeTintType = biomeTint
            r, g, b = r * blockColor[0] // 255, g * blockColor[1] // 255, b * blockColor[2] // 255
        tintcolor = (r, g, b) if (r, g, b) != (255, 255, 255) or biomeTintType != BIOME_NONE else None
        return FaceInfo(0, 0, 0, 1, 1, 1, facesByCardinal[side], texture, 0, 0, 16, 16,
                        facesByCardinal[cullface] if cullface else -1, shade, 0, 0, 0, None, 0,
                        variantXrot, variantYrot, variantMatrix, tintcolor, biomeTintType, points=corners)

    def _loadQuadsModel(self, block, dict modelDict, short variantXrot, short variantYrot):
        """ The faces of a model the export mod wrote from what the game drew. """
        textures = modelDict.get("textures") or {}
        variantMatrix = variantRotation(variantXrot, variantYrot)
        quads = []
        try:
            for quad in modelDict.get("quads") or []:
                texture = quad.get("texture")
                if texture is not None and texture.startswith("#"):
                    texture = textures.get(texture[1:])
                corners = [tuple(float(c) for c in vertex[:5]) for vertex in quad["vertices"][:4]]
                if len(corners) != 4:
                    continue
                color = (1.0, 1.0, 1.0)
                if quad.get("color"):
                    argb = int(quad["color"], 16)
                    color = (((argb >> 16) & 0xff) / 255., ((argb >> 8) & 0xff) / 255., (argb & 0xff) / 255.)
                quads.append(self._pointsFace(block, texture, corners, quad.get("side", "up"), quad.get("cullface"),
                                              quad.get("shade", True), quad.get("tintindex", -1), color,
                                              variantXrot, variantYrot, variantMatrix))
        except (KeyError, ValueError, TypeError) as e:
            log.warning("Could not read the generated model of %s: %r", block.nameAndState, e)
            return None
        return quads

    def _makeConnectedTables(self, list specs, list faces, dict texCoordsByName):
        """ The C tables the chunk renderer reads connected textures from. """
        cdef int i, mask, k
        cdef unsigned char *table
        if self.ctmSpecs != NULL:
            for i in range(self.ctmSpecCount):
                free(self.ctmSpecs[i].connectIDs)
                free(self.ctmSpecs[i].matchIDs)
        free(self.ctmSpecs)
        free(self.ctmQuads)
        self.ctmSpecs = NULL
        self.ctmQuads = NULL
        self.ctmSpecCount = len(specs)
        self.ctmQuadCount = len(faces)
        if specs:
            self.ctmSpecs = <CTMSpec *>malloc(len(specs) * sizeof(CTMSpec))
            memset(self.ctmSpecs, 0, len(specs) * sizeof(CTMSpec))
            for i, (found, base) in enumerate(specs):
                self.ctmSpecs[i].connect = found.connect
                self.ctmSpecs[i].frontCheck = found.frontCheck
                self.ctmSpecs[i].method = found.method
                self.ctmSpecs[i].symmetry = found.symmetry
                self.ctmSpecs[i].count = min(len(found.parts), 256)
                self.ctmSpecs[i].width = max(found.width, 1)
                self.ctmSpecs[i].height = max(found.height, 1)
                self.ctmSpecs[i].loops = found.loops
                self.ctmSpecs[i].weightSum = sum(found.weights[:256]) if found.weights else 0
                if found.weights:
                    for k, weight in enumerate(found.weights[:256]):
                        self.ctmSpecs[i].weights[k] = weight
                entries, obscured = _connectedRects(found, texCoordsByName, base)
                for mask, (whole, rects, count) in enumerate(entries[:256]):
                    self.ctmSpecs[i].whole[mask] = whole
                    self.ctmSpecs[i].layers[mask] = count
                    for k in range(16):
                        self.ctmSpecs[i].rects[mask][k] = rects[k]
                self.ctmSpecs[i].overlay = found.overlay
                self.ctmSpecs[i].rule = found.rule
                self.ctmSpecs[i].tint = found.tint
                if obscured is not None:
                    self.ctmSpecs[i].hasObscured = 1
                    for k in range(4):
                        self.ctmSpecs[i].obscured[k] = obscured[k]
                self.ctmSpecs[i].source = found.source
                if found.seed is not None:
                    self.ctmSpecs[i].hasSeed = 1
                    self.ctmSpecs[i].seed = found.seed
                if found.method == connected.METHOD_OVERLAY:
                    for which, names, textures in ((0, found.connectBlocks, found.connectTiles),
                                                   (1, found.matchBlocks, found.matchTiles)):
                        table = <unsigned char *>malloc(65536)
                        memset(table, 1 if names is None and textures is None else 0, 65536)
                        paths = {connected.texturePath(t) for t in textures or ()}
                        for block in self.blocktypes:
                            if (names and block.internalName in names) or (
                                    paths and paths & self._texturesByID.get(block.ID, set())):
                                table[block.ID] = 1
                        if which == 0:
                            self.ctmSpecs[i].connectIDs = table
                        else:
                            self.ctmSpecs[i].matchIDs = table
        if faces:
            self.ctmQuads = <CTMQuad *>malloc(len(faces) * sizeof(CTMQuad))
            for i, (spec, right, up, normal, base) in enumerate(faces):
                self.ctmQuads[i].spec = spec
                for k in range(3):
                    self.ctmQuads[i].right[k] = right[k]
                    self.ctmQuads[i].up[k] = up[k]
                    self.ctmQuads[i].normal[k] = normal[k]
                self.ctmQuads[i].bl, self.ctmQuads[i].btop, self.ctmQuads[i].bw, self.ctmQuads[i].bh = base

        # Which block each ID is of, for textures that connect to any state of the same block.
        groups = np.zeros(65536, dtype=np.uint16)
        names = {}
        for block in self.blocktypes:
            groups[block.ID] = names.setdefault(block.internalName, len(names) + 1)
        self.ctmBlockGroups = groups
        if specs:
            log.info("%d faces have %d connected textures", len(faces), len(specs))

    def loadResourceVariant(self, resourcePath, resourceVariant):
        # variants is a dict with each key a resourceVariant value (from the block's ModelResourceLocation)
        # the value for this key is either a dict describing which model to use
        # ... or a list of such models to be selected from randomly
        #
        # each model dict must have a 'model' key whose value is the name of a file under assets/minecraft/models
        # model dict may also have optional keys 'x', 'y', 'z' with a value in degrees, to rotate the model
        # around that axis
        # another optional key is 'uvlock', which needs investigating
        # variant dict for 'rail':

        # "variants": {
        #     "shape=north_south": { "model": "normal_rail_flat" },
        #     "shape=east_west": { "model": "normal_rail_flat", "y": 90 },
        #     "shape=ascending_east": { "model": "normal_rail_raised_ne", "y": 90 },
        #     "shape=ascending_west": { "model": "normal_rail_raised_sw", "y": 90 },
        #     "shape=ascending_north": { "model": "normal_rail_raised_ne" },
        #     "shape=ascending_south": { "model": "normal_rail_raised_sw" },
        #     "shape=south_east": { "model": "normal_rail_curved" },
        #     "shape=south_west": { "model": "normal_rail_curved", "y": 90 },
        #     "shape=north_west": { "model": "normal_rail_curved", "y": 180 },
        #     "shape=north_east": { "model": "normal_rail_curved", "y": 270 }
        # }
        if (resourcePath, resourceVariant) in self.quadsByResourcePathVariant:
            log.debug("Model %s#%s already loaded", resourcePath, resourceVariant)
            return None

        log.debug("Loading %s#%s", resourcePath, resourceVariant)
        entries = self._modelEntries(resourcePath, resourceVariant)
        if entries is None:
            return None
        return self._loadModelEntries(entries)

    def _modelEntries(self, resourcePath, resourceVariant):
        """
        Return the entries of a blockstates file that apply to the given variant. Each entry has the
        name of a model, and may have rotations. Returns None if there are none to find.
        """
        try:
            statesJson = self._getBlockState(resourcePath)
        except ResourceNotFound as e:
            log.debug("Could not get blockstates resource for %s#%s, skipping... (%r)", resourcePath, resourceVariant, e)
            return None

        if forge_blockstates.isForgeBlockstate(statesJson):
            entries = forge_blockstates.modelEntries(statesJson, resourceVariant)
            if not entries:
                log.warning("No model for %s#%s in its Forge blockstate file", resourcePath, resourceVariant)
                return None
            return entries

        variants = statesJson.get('variants')
        if variants is not None:
            variantDict = variants.get(resourceVariant)
            if variantDict is None and self.blocktypes.modelNamesAreFull:
                variantDict = findMatchingVariant(variants, resourceVariant)
            if variantDict is None:
                log.warning("Could not get variant key for %s#%s, skipping...", resourcePath, resourceVariant)
                return None

            if isinstance(variantDict, list):
                if len(variantDict):
                    variantDict = variantDict[0]  # do the random pick thing later, if at all
                else:
                    log.warning("Variant key for %s#%s contains no variants, skipping...", resourcePath, resourceVariant)
                    return None

            if variantDict.get('model') is None:
                log.warning("No 'model' key found for variant %s in 'variants' key of states file %s",
                            resourceVariant, resourcePath)
                return None
            return [variantDict]

        multipart = statesJson.get('multipart')
        if multipart is None:
            return None

        # Multipart looks like this:
        # [
        # {   "when": {"north": false, "east": false, "south": false, "west": false, "up": false},
        #     "apply": [
        #         { "model": "fire_floor0" },
        #         { "model": "fire_floor1" }
        #     ]
        # },
        # {   "when": {"OR": [{"north": true}, {"north": false, "east": false, "south": false, "west": false, "up": false}]},
        #     "apply": [
        #         { "model": "fire_side0" },
        #         { "model": "fire_side1" },
        #         { "model": "fire_side_alt0" },
        #         { "model": "fire_side_alt1" }
        #     ]
        # }
        # ]
        variantMap = dict(pair.split('=') for pair in resourceVariant.split(',') if '=' in pair)
        entries = []
        for part in multipart:
            when = part.get('when')
            if when is None or matchWhen(when, variantMap):
                apply = part.get('apply')
                if apply is not None:
                    if not isinstance(apply, list):
                        apply = [apply]
                    if self.blocktypes.modelNamesAreFull:
                        # Those in a list are alternatives, of which the game picks one at random.
                        apply = apply[:1]
                    entries.extend(entry for entry in apply if entry.get('model') is not None)
        return entries

    def _loadModelEntries(self, entries):
        parts = []
        for entry in entries:
            modelName = entry['model']
            if modelName.endswith(".obj"):
                # Forge 1.12's OBJ models, such as "somemod:thing.obj", with their options in "custom".
                custom = entry.get("custom") if isinstance(entry.get("custom"), dict) else {}
                modelDict = {"loader": OBJ_LOADERS[1], "model": modelName,
                             "flip_v": bool(custom.get("flip-v", False)), "textures": entry.get("textures") or {}}
                parts.append((modelDict, entry.get('x', 0), entry.get('y', 0)))
                continue
            if "." in modelName.rpartition("/")[2]:
                # Forge's B3D models, such as "somemod:thing.b3d", which code makes into a model.
                log.debug("Model %s is not a block model file", modelName)
                continue
            if not self.blocktypes.modelNamesAreFull:
                # Before 1.13 a blockstate file names a model in models/block/, as "stone" or "somemod:thing".
                namespace, colon, path = modelName.rpartition(":")
                modelName = namespace + colon + "block/" + path
            try:
                modelDict = self._getBlockModel(modelName)
            except ResourceNotFound as e:
                log.info("Could not get model resource %s, skipping... (%r)", modelName, e)
                continue
            except ValueError as e:
                log.warning("Error parsing json for %s: %s", modelName, e)
                continue
            if entry.get('textures'):
                # A Forge blockstate file can give a model other textures.
                modelDict = {"parent": modelName, "textures": entry['textures']}
            parts.append((modelDict, entry.get('x', 0), entry.get('y', 0)))
        return parts

    def buildBoxQuads(self, dict element, unicode nameAndState, dict textureVars,
                       short variantXrot, short variantYrot,
                       cnp.ndarray variantMatrix, tuple blockColor, char biomeTintType):
        quads = []
        shade = element.get("shade", True)

        cdef float ox, oy, oz
        ox, oy, oz, elementMatrix = elementRotation(element.get("rotation"))
        cdef float x1, y1, z1, x2, y2, z2
        x1, y1, z1 = element["from"]
        x2, y2, z2 = element["to"]

        x1 /= 16.
        y1 /= 16.
        z1 /= 16.
        x2 /= 16.
        y2 /= 16.
        z2 /= 16.

        cdef short u1, v1, u2, v2
        cdef unicode texture, lasttexvar
        cdef short i
        cdef dict info

        for face, info in element["faces"].items():
            assert info is not None

            face = facesByCardinal[face]
            texture = info["texture"]
            cullface = info.get("cullface")
            cullface = facesByCardinal.get(cullface, -1)
            uv = info.get("uv")
            if uv is not None:
                u1, v1, u2, v2 = [int(c) for c in uv]  # a few models have fractions of a pixel
            else:
                if face == FaceDown or face == FaceUp:
                    u1, v1, u2, v2 = <short>(x1 * 16), <short>(z1 * 16), <short>(x2 * 16), <short>(z2 * 16)
                elif face == FaceNorth or face == FaceSouth:
                    u1, v1, u2, v2 = <short>(x1 * 16), <short>(y1 * 16), <short>(x2 * 16), <short>(y2 * 16)
                elif face == FaceEast or face == FaceWest:
                    u1, v1, u2, v2 = <short>(z1 * 16), <short>(y1 * 16), <short>(z2 * 16), <short>(y2 * 16)
                    

            lasttexvar = texture

            tintindex = info.get("tintindex")
            if tintindex is not None:
                tintcolor = blockColor
            else:
                tintcolor = None

            # resolve texture variables
            for i in range(MAX_TEXTURE_RECURSIONS):
                if texture is None:
                    log.error("Texture variable %s is not assigned." % lasttexvar)
                    return None
                elif texture[0] == u"#":
                    lasttexvar = texture
                    texture = textureVars.get(texture[1:], u"MCEDIT_UNKNOWN")
                elif texture in textureVars:
                    # A variable name without its "#" works too, and heavy_core does this.
                    lasttexvar = texture
                    texture = textureVars[texture]
                else:
                    break
            else:
                log.error("Texture variable loop detected!")
                return None

            if texture != u"MCEDIT_UNKNOWN" and not texture.endswith(".png"):
                texture = "assets/%s/textures/%s.png" % splitResourceName(texture)
            self.firstTextures.setdefault(nameAndState, texture)
            self._texturePaths.add(texture)

            quads.append(FaceInfo(x1, y1, z1, x2, y2, z2, face,
                    texture, u1, v1, u2, v2, cullface,
                    shade, ox, oy, oz, elementMatrix, info.get("rotation", 0),
                    variantXrot, variantYrot, variantMatrix, tintcolor, biomeTintType))

        return quads

    def _findConnectedTextures(self):
        """
        The connected textures of the textures in use: from their .mcmeta files, as the CTM mod and Fusion give
        them, and from OptiFine's properties files. Their tiles are added to the textures to load.
        """
        if self.connectedTextures is not None:
            return
        self.connectedTextures = {}
        self.connectedByBlock = {}
        for path in list(self._texturePaths):
            if not path.endswith(".png"):
                continue
            try:
                text = self.resourceLoader.openStream(path + ".mcmeta").read().decode("utf-8", "replace")
            except (ResourceNotFound, IOError):
                continue
            if '"ctm"' not in text and '"fusion"' not in text:
                continue
            try:
                found = connected.mcmetaTexture(path, text, _pngSize(self.resourceLoader, path),
                                                lambda columns, rows, path=path: self._filledCells(path, columns, rows))
            except Exception as e:
                log.warning("Could not read the connected texture of %s: %r", path, e)
                continue
            if found is not None:
                self.connectedTextures[path] = found
        try:
            optifine = list(self.resourceLoader.optifineConnectedPaths())
        except AttributeError:
            optifine = []
        for path in optifine:
            try:
                text = self.resourceLoader.openStream(path).read().decode("utf-8", "replace")
                rule = connected.optifineTexture(path, text)
            except Exception as e:
                log.warning("Could not read %s: %r", path, e)
                continue
            if rule is None:
                continue
            textures, blocks, found = rule
            for name in textures or []:
                self.connectedTextures.setdefault(connected.texturePath(name), found)
            for name in blocks or []:
                self.connectedByBlock.setdefault(name, found)
        for found in list(self.connectedTextures.values()) + list(self.connectedByBlock.values()):
            self._texturePaths.update(found.textures)
        if self.connectedTextures or self.connectedByBlock:
            log.info("Found %d connected textures, and %d blocks with them", len(self.connectedTextures),
                     len(self.connectedByBlock))

    def _filledCells(self, path, columns, rows):
        """ The cells of a texture, in a grid, that are not wholly clear, as Fusion's random textures leave out. """
        try:
            from PySide6.QtGui import QImage
            data = self.resourceLoader.openStream(path).read()
            image = QImage.fromData(data)
        except Exception:
            return list(range(columns * rows))
        if image.isNull():
            return list(range(columns * rows))
        image = image.convertToFormat(QImage.Format_ARGB32)
        width, height = image.width() // columns, image.height() // rows
        cells = []
        for index in range(columns * rows):
            x0, y0 = (index % columns) * width, (index // columns) * height
            if any((image.pixel(x, y) >> 24) & 0xff for y in range(y0, y0 + height) for x in range(x0, x0 + width)):
                cells.append(index)
        return cells

    def getTextureNames(self):
        self._findConnectedTextures()
        folder = self.blocktypes.textureFolder
        return itertools.chain(iter(self._texturePaths),
                               ['assets/minecraft/textures/%s/water_still.png' % folder,
                                'assets/minecraft/textures/%s/lava_still.png' % folder])

    def cookQuads(self, textureAtlas):
        cdef int ID, meta
        cdef int l, t, w, h, ph
        cdef short u1, u2, v1, v2, tr, tg, tb
        cdef int uw, vh
        cdef short quadface, cullface
        cdef unsigned char shade
        cdef ModelQuadList modelQuads
        cdef ModelQuadList unknownBlockModel
        cdef ModelQuadListObj modelQuadsObj = None
        cdef list allQuads
        cdef size_t i, j

        cdef dict texCoordsByName = textureAtlas.texCoordsByName
        cdef FaceInfo faceInfo
        cdef short * vec
        cdef unsigned char * rgba

        if self.cooked:
            return

        log.info("Cooking quads for %d models...", len(self.quadsByResourcePathVariant))
        cookedModels = {}
        UNKNOWN_BLOCK = u'MCEDIT_UNKNOWN'
        self._findConnectedTextures()
        ctmSpecIndex = {}  # (ConnectedTexture, base rect) -> index, or -1 if its textures are not on the atlas
        ctmSpecList = []
        ctmQuadList = []  # (spec, right, up, normal, (bl, btop, bw, bh))
        self._texturesByID = {}
        # Only OptiFine's overlays go by the textures of the blocks beside a face.
        needTextures = any(found.method == connected.METHOD_OVERLAY and (found.connectTiles or found.matchTiles)
                           for found in list(self.connectedTextures.values()) + list(self.connectedByBlock.values()))

        for (path, variant), allQuads in self.quadsByResourcePathVariant.items():

            modelQuads.count = len(allQuads)
            # A model with nothing in it still has a list, to tell it from a block with no model.
            modelQuads.quads = <ModelQuad *>malloc(max(modelQuads.count, 1) * sizeof(ModelQuad))

            for i, faceInfo in enumerate(allQuads):
                if faceInfo.texture not in texCoordsByName:
                    log.warning("Texture %s was not loaded, using 'UNKNOWN'", faceInfo.texture)
                    l, t, w, h = texCoordsByName[UNKNOWN_BLOCK]
                else:
                    l, t, w, h = texCoordsByName[faceInfo.texture]
                u1 = faceInfo.u1
                u2 = faceInfo.u2
                v1 = faceInfo.v1
                v2 = faceInfo.v2

                # u1..u2 is on the scale 0..16, convert it to l..l+w
                # v1..v2 is also 0..16, but should be flipped to t+h..t
                # u /= 16.
                # u *= w
                # u += l
                # v /= 16.
                # v = -v
                # v *= h
                # v += h
                # v += t

                # TODO: read texture .mcmeta files
                # use top square of texture for now.
                # Faces with corners of their own may have a texture that is not square, as an entity's, which is
                # all one picture; an animated texture is a strip of square frames.
                ph = w if h > w else h
                h = w

                u1 = <short>(l + (u1 * w) / 16.)
                u2 = <short>(l + (u2 * w) / 16.)

                v1 = <short>(t + h + (v1 * -h) / 16.)
                v2 = <short>(t + h + (v2 * -h) / 16.)

                quadface = faceInfo.face
                cullface = faceInfo.cullface
                if faceInfo.points is not None:
                    # A face of an OBJ model, with corners of its own and texture coordinates from 0 to 1.
                    for j, (px, py, pz, pu, pv) in enumerate(faceInfo.points):
                        modelQuads.quads[i].xyzuvstc[j * 8 + 0] = px
                        modelQuads.quads[i].xyzuvstc[j * 8 + 1] = py
                        modelQuads.quads[i].xyzuvstc[j * 8 + 2] = pz
                        modelQuads.quads[i].xyzuvstc[j * 8 + 3] = l + pu * w
                        # The image is stored bottom row first, so its top is at t + ph.
                        modelQuads.quads[i].xyzuvstc[j * 8 + 4] = t + ph - pv * ph
                        modelQuads.quads[i].xyzuvstc[j * 8 + 5] = 0.5
                        modelQuads.quads[i].xyzuvstc[j * 8 + 6] = 0.5
                        modelQuads.quads[i].xyzuvstc[j * 8 + 7] = 0.0
                else:
                    getBlockFaceVertices(modelQuads.quads[i].xyzuvstc,
                                         faceInfo.x1, faceInfo.y1, faceInfo.z1,
                                         faceInfo.x2, faceInfo.y2, faceInfo.z2,
                                         quadface, u1, v1, u2, v2, faceInfo.textureRotation)

                quadface = rotateFaceByVariant(quadface,
                                               faceInfo.variantXrot,
                                               faceInfo.variantYrot)
                if cullface != -1:
                    cullface = rotateFaceByVariant(cullface,
                                                   faceInfo.variantXrot,
                                                   faceInfo.variantYrot)

                applyRotations(faceInfo.ox, faceInfo.oy, faceInfo.oz,
                               faceInfo.elementMatrix, faceInfo.variantMatrix,
                               modelQuads.quads[i].xyzuvstc)

                rgba = <unsigned char *>modelQuads.quads[i].xyzuvstc
                if faceInfo.shade:
                    shade = faceShades[quadface]
                    for j in range(4):
                        rgba[28 + 32*j + 0] = shade
                        rgba[28 + 32*j + 1] = shade
                        rgba[28 + 32*j + 2] = shade
                        rgba[28 + 32*j + 3] = 0xff

                else:
                    for j in range(4):
                        rgba[28 + 32*j + 0] = 0xff
                        rgba[28 + 32*j + 1] = 0xff
                        rgba[28 + 32*j + 2] = 0xff
                        rgba[28 + 32*j + 3] = 0xff

                modelQuads.quads[i].biomeTintType = 0
                if faceInfo.tintcolor is not None:
                    #log.info("Applying tint color for face %d of %s, biomeTint=%d", i, nameAndState, faceInfo.biomeTintType)
                    if faceInfo.biomeTintType is BIOME_NONE:
                        tr, tg, tb = faceInfo.tintcolor
                        for j in range(4):
                            rgba[28 + 32*j + 0] = (tr * rgba[28 + 32*j + 0]) >> 8
                            rgba[28 + 32*j + 1] = (tg * rgba[28 + 32*j + 1]) >> 8
                            rgba[28 + 32*j + 2] = (tb * rgba[28 + 32*j + 2]) >> 8
                    else:
                        modelQuads.quads[i].biomeTintType = faceInfo.biomeTintType

                #cookedQuads.append((xyzuvstc, cullface, face))
                if cullface != -1:
                    modelQuads.quads[i].cullface[0] = 1
                    vec = _faceVector(cullface)
                    modelQuads.quads[i].cullface[1] = vec[0]
                    modelQuads.quads[i].cullface[2] = vec[1]
                    modelQuads.quads[i].cullface[3] = vec[2]
                else:
                    modelQuads.quads[i].cullface[0] = 0
                    modelQuads.quads[i].cullface[1] = 0
                    modelQuads.quads[i].cullface[2] = 0
                    modelQuads.quads[i].cullface[3] = 0


                vec = _faceVector(quadface)
                modelQuads.quads[i].quadface[0] = int(quadface)
                modelQuads.quads[i].quadface[1] = vec[0]
                modelQuads.quads[i].quadface[2] = vec[1]
                modelQuads.quads[i].quadface[3] = vec[2]

                if needTextures and faceInfo.texture:
                    for internalName, blockState in self.blockStatesByResourcePathVariant[path, variant]:
                        try:
                            self._texturesByID.setdefault(
                                self.blocktypes.IDsByState[internalName + blockState][0], set()).add(faceInfo.texture)
                        except (KeyError, AttributeError, TypeError):
                            pass

                # A connected texture, from the texture or from the block.
                modelQuads.quads[i].ctm = 0
                found = None
                if self.connectedTextures or self.connectedByBlock:
                    found = self.connectedTextures.get(faceInfo.texture)
                    if found is not None and faceInfo.connection is not None:
                        found = found.withConnect(faceInfo.connection)
                    if found is None and self.connectedByBlock:
                        for internalName, blockState in self.blockStatesByResourcePathVariant[path, variant]:
                            found = self.connectedByBlock.get(internalName)
                            if found is not None:
                                break
                if found is not None and (found.faces is None or _FACE_NAMES[quadface] in found.faces):
                    faceHeight = ph if faceInfo.points is not None else w
                    base = (l, t + faceHeight, w, faceHeight)
                    specKey = (found, base if found.usesDefault else None)
                    spec = ctmSpecIndex.get(specKey)
                    if spec is None:
                        spec = len(ctmSpecList) if _connectedRects(found, texCoordsByName, base) is not None else -1
                        ctmSpecIndex[specKey] = spec
                        if spec >= 0:
                            ctmSpecList.append((found, base))
                    axes = _textureAxes(modelQuads.quads[i].xyzuvstc, l, t, w, faceHeight)
                    # Only for a face whose texture's right and up lie in its plane, across each other, as on a
                    # side of a block; the neighbours of any other are not where the texture would look for them.
                    if axes is not None and len({_axisIndex(axes[0]), _axisIndex(axes[1]),
                                                 _axisIndex((vec[0], vec[1], vec[2]))}) != 3:
                        axes = None
                    if axes is None and found.method != connected.METHOD_NEIGHBOURS:
                        axes = ((0, 0, 0), (0, 0, 0))  # chosen by position, which needs no neighbours
                    if spec >= 0 and axes is not None:
                        ctmQuadList.append((spec, axes[0], axes[1], (vec[0], vec[1], vec[2]), base))
                        modelQuads.quads[i].ctm = len(ctmQuadList)

            for internalName, blockState in self.blockStatesByResourcePathVariant[path, variant]:
                if internalName != UNKNOWN_BLOCK:
                    modelQuadsObj = ModelQuadListObj()
                    modelQuadsObj.quadList = modelQuads

                    stateProps = sorted(blockState[1:-1].split(","))
                    self.cookedModelsByBlockState[internalName, tuple(stateProps)] = modelQuadsObj

                    try:
                        ID, meta = self.blocktypes.IDsByState[internalName + blockState]
                    except KeyError:
                        continue
                    
                # cookedModels[nameAndState] = cookedQuads
                if path == UNKNOWN_BLOCK:
                    unknownBlockModel = modelQuads
                    self.unknownModelQuads = modelQuads.quads
                else:
                    self.cookedModelsByID[ID][meta] = modelQuads

        self._makeConnectedTables(ctmSpecList, ctmQuadList, texCoordsByName)

        # Metas that older worlds' tables leave out look like the listed meta the game reads them as.
        for (ID, meta), alias in getattr(self.blocktypes, "metaAliases", {}).items():
            if self.cookedModelsByID[ID][meta].quads == NULL:
                self.cookedModelsByID[ID][meta] = self.cookedModelsByID[ID][alias]

        for ID in range(65536):
            for meta in range(16):
                if self.cookedModelsByID[ID][meta].quads == NULL:
                    self.cookedModelsByID[ID][meta] = unknownBlockModel

        self.cookedModels = cookedModels
        self.cooked = True

        # import pprint; pprint.pprint((self.cookedModelsByBlockState)); raise SystemExit
        self.cookFluidQuads()

    def copyModel(self, ID, meta, sourceID, sourceMeta):
        """ Give a block state that has appeared since cooking the model of another. """
        if self.cooked:
            self.cookedModelsByID[ID][meta] = self.cookedModelsByID[sourceID][sourceMeta]

    def hasModel(self, ID, meta):
        """ Return whether a block state has a model of its own, and is not drawn as an unknown block. """
        return self.cookedModelsByID[ID][meta].quads != self.unknownModelQuads

    def quadCount(self, ID, meta):
        return self.cookedModelsByID[ID][meta].count

    def cookFluidQuads(self):
        cdef ModelQuadList * modelQuads
        cdef float[:] quadVerts, modelQuadVerts
        cdef short * fv
        cdef short dx, dy, dz
        cdef cnp.ndarray varray = np.empty(shape=(4, 8), dtype='f4')

        for filled in range(9):
            box = FloatBox((0, 0, 0), (1, ((8 - filled) / 9.0) if filled < 8 else 1.0, 1))

            modelQuads = &self.fluidQuads[filled]
            modelQuads.count = 6
            modelQuads.quads = <ModelQuad *>malloc(6 * sizeof(ModelQuad))

            for face in range(6):
                modelQuads.quads[face].cullface[0] = 0
                modelQuads.quads[face].ctm = 0

                fv = _faceVector(face)
                dx = fv[0]
                dy = fv[1]
                dz = fv[2]

                modelQuads.quads[face].quadface[0] = <int>face
                modelQuads.quads[face].quadface[1] = dx
                modelQuads.quads[face].quadface[2] = dy
                modelQuads.quads[face].quadface[3] = dz

                getBlockFaceVertices(<float *>varray.data,
                                     box.minx, box.miny, box.minz,
                                     box.maxx, box.maxy, box.maxz,
                                     face, 0, 0, 16, 16, 0)

                varray.view('uint8')[:, 28:] = faceShades[face]

                quadVerts = varray.ravel()
                modelQuadVerts = modelQuads.quads[face].xyzuvstc
                modelQuadVerts[:] = quadVerts[:]

    cdef cookedModelsForState(self, tuple nameAndState):
        quads = self.cookedModelsByBlockState.get(nameAndState)
        if quads is not None:
            return quads

        return _nullQuads
        #name, state = nameAndState


_nullQuads = ModelQuadListObj()

cdef short FaceEast   = 0
cdef short FaceWest   = 1
cdef short FaceUp     = 2
cdef short FaceDown   = 3
cdef short FaceSouth  = 4
cdef short FaceNorth  = 5

cdef short FaceXIncreasing = FaceEast
cdef short FaceXDecreasing = FaceWest
cdef short FaceYIncreasing = FaceUp
cdef short FaceYDecreasing = FaceDown
cdef short FaceZIncreasing = FaceSouth
cdef short FaceZDecreasing = FaceNorth
MaxDirections = 6

cdef short[18] faceDirections  # cython doesn't work right if the following initializer if this is a 'char'

faceDirections[:] = [
    1,  0,  0,
    -1, 0,  0,
    0,  1,  0,
    0, -1,  0,
    0,  0,  1,
    0,  0, -1,
]

cdef short * _faceVector(char face):
    return faceDirections + face * 3

faceRotations = (
    (
        FaceNorth,
        FaceDown,
        FaceSouth,
        FaceUp,
    ),
    (
        FaceNorth,
        FaceEast,
        FaceSouth,
        FaceWest,
    ),
    # (
    #     FaceXIncreasing,
    #     FaceYIncreasing,
    #     FaceXDecreasing,
    #     FaceYDecreasing,
    # ),

)

facesByCardinal = dict(
    east=FaceEast,
    west=FaceWest,
    up=FaceUp,
    down=FaceDown,
    south=FaceSouth,
    north=FaceNorth,
)
_FACE_NAMES = {face: name for name, face in facesByCardinal.items()}



cdef short[6] faceShades
faceShades[:] = [
    0xCC,
    0xCC,
    0xFF,
    0x77,
    0x99,
    0x99,
]

cdef short rotateFaceByVariant(short face, short variantXrot, short variantYrot):
    if variantXrot:
        face = rotateFace(face, 0, variantXrot)
    if variantYrot:
        face = rotateFace(face, 1, variantYrot)
    return face


cdef short rotateFace(short face, short axis, int degrees):
    rots = faceRotations[axis]
    try:
        idx = rots.index(face)
    except ValueError:
        return face

    while degrees > 0:
        idx += 1
        degrees -= 90
    idx %= 4
    return rots[idx]

cdef void applyRotations(float ox, float oy, float oz,
                         cnp.ndarray[ndim=2, dtype=double] elementMatrix,
                         cnp.ndarray[ndim=2, dtype=double] variantMatrix,
                         float * xyzuvstc):
    cdef int i
    cdef float x, y, z, nx, ny, nz
    if elementMatrix is not None:
        for i in range(4):
            x = xyzuvstc[i*8 + 0] - ox
            y = xyzuvstc[i*8 + 1] - oy
            z = xyzuvstc[i*8 + 2] - oz
            nx = x * elementMatrix[0, 0] + y * elementMatrix[1, 0] + z * elementMatrix[2, 0]
            ny = x * elementMatrix[0, 1] + y * elementMatrix[1, 1] + z * elementMatrix[2, 1]
            nz = x * elementMatrix[0, 2] + y * elementMatrix[1, 2] + z * elementMatrix[2, 2]
            xyzuvstc[i*8 + 0] = nx + ox
            xyzuvstc[i*8 + 1] = ny + oy
            xyzuvstc[i*8 + 2] = nz + oz

    if variantMatrix is not None:
        for i in range(4):
            x = xyzuvstc[i*8 + 0] - 0.5
            y = xyzuvstc[i*8 + 1] - 0.5
            z = xyzuvstc[i*8 + 2] - 0.5
            nx = x * variantMatrix[0, 0] + y * variantMatrix[1, 0] + z * variantMatrix[2, 0]
            ny = x * variantMatrix[0, 1] + y * variantMatrix[1, 1] + z * variantMatrix[2, 1]
            nz = x * variantMatrix[0, 2] + y * variantMatrix[1, 2] + z * variantMatrix[2, 2]
            xyzuvstc[i*8 + 0] = nx + 0.5
            xyzuvstc[i*8 + 1] = ny + 0.5
            xyzuvstc[i*8 + 2] = nz + 0.5

cdef elementRotation(dict rotation):
    if rotation is None:
        return 0, 0, 0, None

    origin = rotation["origin"]
    rescale = rotation.get("rescale", False)
    if "axis" in rotation:
        matrix = npRotate(rotation["axis"], -rotation["angle"], rescale)
    else:
        # Angles about all three axes, as 26.2's hanging signs have, which the game turns about X, then Y, then Z. The matrices here
        # are applied to row vectors, so they are the game's transposed, and in the opposite order.
        matrix = (npRotate("x", -rotation.get("x", 0)) * npRotate("y", -rotation.get("y", 0))
                  * npRotate("z", -rotation.get("z", 0)))
    ox, oy, oz = origin
    return ox / 16., oy / 16., oz / 16., matrix[:3, :3]

# There are only a few of these, and making a numpy matrix takes far longer than looking one up.
_variantRotations = {}

cdef variantRotation(variantXrot, variantYrot):
    if variantXrot or variantYrot:
        key = (variantXrot, variantYrot)
        rotation = _variantRotations.get(key)
        if rotation is None:
            matrix = np.matrix(np.identity(4))
            if variantXrot:
                matrix *= npRotate("x", variantXrot)
            if variantYrot:
                matrix *= npRotate("y", variantYrot)
            rotation = _variantRotations[key] = matrix[:3, :3]
        return rotation

@lru_cache()
def npRotate(axis, angle, rescale=False):
    # ( xx(1-c)+c	xy(1-c)-zs  xz(1-c)+ys	 0  )
    # | yx(1-c)+zs	yy(1-c)+c   yz(1-c)-xs	 0  |
    # | xz(1-c)-ys	yz(1-c)+xs  zz(1-c)+c	 0  |
    # (	 0	        0		    0	         1  )
    # axis:
    # "x": (1, 0, 0)
    # "y": (0, 1, 0)
    # "z": (0, 0, 1)
    x = y = z = 0
    if axis == "x":
        x = 1
    elif axis == "y":
        y = 1
    elif axis == "z":
        z = 1
    else:
        raise ValueError("Unknown axis: %r" % axis)

    s = math.sin(math.radians(angle))
    c = math.cos(math.radians(angle))
    rotate = np.matrix([[x*x*(1-c)+c,    x*y*(1-c)-z*s,  x*z*(1-c)+y*s,  0],
                           [y*x*(1-c)+z*s,  y*y*(1-c)+c,    y*z*(1-c)-x*s,  0],
                           [x*z*(1-c)-y*s,  y*z*(1-c)+x*s,  z*z*(1-c)+c,    0],
                           [0,              0,              0,              1]])
    # xxx rescale
    return rotate



cdef void getBlockFaceVertices(float[] xyzuvstc,
                               float x1, float y1, float z1,
                               float x2, float y2, float z2,
                               short face,
                               short u1, short v1, short u2, short v2, int textureRotation):
    cdef int roll = 0

    cdef float[8] tc
    if textureRotation:
        roll = textureRotation // 90
        roll %= 4
        roll *= 2
        roll = 8 - roll

    tc[(0+roll)%8] = u1
    tc[(1+roll)%8] = v1
    tc[(2+roll)%8] = u1
    tc[(3+roll)%8] = v2
    tc[(4+roll)%8] = u2
    tc[(5+roll)%8] = v2
    tc[(6+roll)%8] = u2
    tc[(7+roll)%8] = v1

    if face == FaceXDecreasing:
        xyzuvstc[:] = [x1, y2, z1, tc[0], tc[1], 0.5, 0.5, 0.0,
                       x1, y1, z1, tc[2], tc[3], 0.5, 0.5, 0.0,
                       x1, y1, z2, tc[4], tc[5], 0.5, 0.5, 0.0,
                       x1, y2, z2, tc[6], tc[7], 0.5, 0.5, 0.0,
        ]

    elif face == FaceXIncreasing:
        xyzuvstc[:] = [x2, y2, z2, tc[0], tc[1], 0.5, 0.5, 0.0,
                       x2, y1, z2, tc[2], tc[3], 0.5, 0.5, 0.0,
                       x2, y1, z1, tc[4], tc[5], 0.5, 0.5, 0.0,
                       x2, y2, z1, tc[6], tc[7], 0.5, 0.5, 0.0,
        ]

    elif face == FaceYDecreasing:
        xyzuvstc[:] = [x1, y1, z2, tc[0], tc[1], 0.5, 0.5, 0.0,
                       x1, y1, z1, tc[2], tc[3], 0.5, 0.5, 0.0,
                       x2, y1, z1, tc[4], tc[5], 0.5, 0.5, 0.0,
                       x2, y1, z2, tc[6], tc[7], 0.5, 0.5, 0.0,
        ]

    elif face == FaceYIncreasing:
        xyzuvstc[:] = [x1, y2, z1, tc[0], tc[1], 0.5, 0.5, 0.0,
                       x1, y2, z2, tc[2], tc[3], 0.5, 0.5, 0.0,
                       x2, y2, z2, tc[4], tc[5], 0.5, 0.5, 0.0,
                       x2, y2, z1, tc[6], tc[7], 0.5, 0.5, 0.0,
        ]

    elif face == FaceZDecreasing:
        xyzuvstc[:] = [x2, y2, z1, tc[0], tc[1], 0.5, 0.5, 0.0,
                       x2, y1, z1, tc[2], tc[3], 0.5, 0.5, 0.0,
                       x1, y1, z1, tc[4], tc[5], 0.5, 0.5, 0.0,
                       x1, y2, z1, tc[6], tc[7], 0.5, 0.5, 0.0,
        ]

    elif face == FaceZIncreasing:
        xyzuvstc[:] = [x1, y2, z2, tc[0], tc[1], 0.5, 0.5, 0.0,
                       x1, y1, z2, tc[2], tc[3], 0.5, 0.5, 0.0,
                       x2, y1, z2, tc[4], tc[5], 0.5, 0.5, 0.0,
                       x2, y2, z2, tc[6], tc[7], 0.5, 0.5, 0.0,
        ]
    else:
        raise ValueError("Unknown face %s" % face)

