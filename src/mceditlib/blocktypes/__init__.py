"""
    Block definitions for all known level formats
    Reader for block definitions stored in .yaml files (based on Minecraft X-Ray)
"""
from logging import getLogger
import traceback
from collections import defaultdict, namedtuple
from collections.abc import MutableSet
import bisect

import numpy
from mceditlib.blocktypes import itemtypes

from mceditlib.blocktypes.json_resources import openResource, getJsonFile


log = getLogger(__name__)

MCYDecreasing = 0
MCYIncreasing = 1
MCZDecreasing = 2 #south
MCZIncreasing = 3 #north
MCXDecreasing = 4 #west
MCXIncreasing = 5 #east



def parseBlockstate(state):
    assert state[0] == '[' and state[-1] == ']'
    state = state[1:-1]
    return dict(pair.split('=') for pair in state.split(','))


def joinBlockstate(stateDict):
    return '[' + ','.join(k + '=' + v for k, v in stateDict.items()) + ']'


def splitInternalName(nameAndState):
    if '[' in nameAndState:
        idx = nameAndState.index('[')
        internalName, blockState = nameAndState[:idx], nameAndState[idx:]
    else:
        internalName = nameAndState
        blockState = ""
    return internalName, blockState


class BlockType(namedtuple("_BlockType", "ID meta blocktypeSet")):
    """
    Value object representing an (id, data, blocktypeSet) tuple.
    Accessing its attributes will return the corresponding elements
    of its parent set's json arrays.

    A blocktype set is initialized with one BlockType for each known
    ID/meta combination. BlockTypes are also created as needed when the
    set is asked for an unknown combination.

    """

    def __repr__(self):
        try:
            names = " (%s (%s%s)" % (self.displayName, self.internalName, self.blockState)
        except:
            names = ""
        return "<BlockType (%d:%d)%s>" % (self.ID, self.meta, names)

    def __str__(self):
        return "%s (%s%s) [%s:%s]" % (self.displayName, self.internalName, self.blockState, self.ID, self.meta)

    def __getattr__(self, attr):

        if attr == "stateDict":
            if attr in self.__dict__:
                return self.__dict__[attr]
            blockState = self.blockState
            if not len(blockState):
                self.__dict__[attr] = {}
            else:
                self.__dict__[attr] = parseBlockstate(blockState)

            return self.__dict__[attr]

        try:
            return self.blocktypeSet.getBlockTypeAttr(self, attr)
        except KeyError:
            # hasattr() only swallows AttributeError on Python 3
            raise AttributeError(attr) from None
    
    def __bool__(self):
        return int(self.ID) != 0  # self.ID is a numpy.int

# Flags in BlockTypeSet.stateFlags
STATE_WATERLOGGED = 1  # has water in the same space, besides whatever it draws for itself
STATE_REDSTONE_WIRE = 2
STATE_UNRESOLVED = 4  # the world has this state but nothing described it, so its looks are a guess

# Virtual IDs for modern worlds can go this high, since section.Blocks has 16 bits per block.
id_limit = 65536


class SortedSet(MutableSet):

    def __iter__(self):
        return iter(self._sorted)

    def discard(self, value):
        self._set.discard(value)
        self._sorted.remove(value)

    def add(self, value):
        if value in self._set:
            return
        self._set.add(value)
        bisect.insort(self._sorted, value)

    def __contains__(self, x):
        return x in self._set

    def __len__(self):
        return len(self._set)

    def __init__(self):
        self._sorted = []
        self._set = set()


class BlockTypeSet(object):
    defaultColor = (0xc9, 0x77, 0xf0, 0xff)
    hasHiddenStates = True  # blocks have states that are worked out by the game and not saved
    modelNamesAreFull = False  # blockstate files give models as "minecraft:block/stone", not "stone"
    textureFolder = "blocks"  # in the textures folder of the game's assets

    def __init__(self, defaultName="Unused Block", idMapping=None):
        object.__init__(self)
        self.allBlocks = SortedSet()

        self.blockJsons = {}
        self.metaAliases = {}  # (id, meta) -> the listed meta whose state it has, for metas the tables leave out
        self.IDsByState = {}  # "internalName[blockstates]" -> (id, meta)
        self.statesByID = {}  # (id, meta) -> "internalName[blockstates]"

        self.IDsByName = {}  # internalName -> id
        self.namesByID = {}  # id -> internalName

        self.defaultBlockstates = {}  # internalName -> "[blockstates]"

        self.defaults = {
            'displayName': defaultName,
            'opacity': 15,
            'brightness': 0,
            'internalName': 'UNKNOWN_NAME',
            'blockState': '',
            'unlocalizedName': 'name.unknown',
            'opaqueCube': True,
            'resourcePath': None,  # Name of states file in assets/minecraft/blockstates/
            'resourceVariant': None,  # Name of variant from above file to use for this block type
            'forcedModel': None,  # Name of model file in assets/minecraft/models/block, forced by user configured blocks
            'forcedModelTextures': None,  # Mapping of texture variables to full texture paths, forced by user configured blocks
            'forcedModelRotation': None,  # Mapping of 'x', 'y', to 0, 90, 180 etc to rotate model. for non-forced blocks this is specified in the states file.
            'forcedRotationFlags': None,  # One or more of 'north', 'south' etc to use when rotating this block
            'renderType': 3,  # Model block - defaults to question mark box
            'unknown': False,  # False for blocks loaded from builtin .json, True for FML IDs, False for blocks configured in editor
            'color': 0xffffff,
            'biomeTintType': None,  # "grass", "leaves", or None
            'useNeighborBrightness': False,
            'normalCube': True,
            'materialLiquid': False,
            'aka': '',
            'fallbackModel': None,  # block model for a block whose own appearance comes from code in the game
            'particleTexture': None,  # texture to use if the block turns out to have no usable model
            'modelSource': None,  # "fallback" if a block pack says that its model files can't be used
            'hasBlockstateFile': False,  # whether a block pack found a blockstates file for this block
            # a model of the faces the game drew, written by the export mod: all there is to draw where modelSource is
            # "generated", and drawn with the block's own models otherwise
            'generatedModel': None,
        }

        self.aka = defaultdict(lambda: "")

        self.useNeighborBrightness = numpy.zeros(id_limit, dtype='uint8')
        self.useNeighborBrightness[:] = self.defaults['useNeighborBrightness']

        self.brightness = numpy.zeros(id_limit, dtype='uint8')
        self.brightness[:] = self.defaults['brightness']

        self.opacity = numpy.zeros(id_limit, dtype='uint8')
        self.opacity[:] = self.defaults['opacity']

        self.renderColor = numpy.zeros((id_limit, 16, 3), dtype='uint8')
        self.renderColor[:] = 0xFF

        self.mapColor = numpy.zeros((id_limit, 16, 3), dtype='uint8')
        self.mapColor[:] = 0xFF

        self.opaqueCube = numpy.ones((id_limit, ), dtype='uint8')
        self.opaqueCube[0] = 0

        self.normalCube = numpy.ones((id_limit, ), dtype='uint8')
        self.normalCube[0] = 0

        self.materialLiquid = numpy.zeros((id_limit, ), dtype='uint8')

        self.renderType = numpy.zeros((id_limit, ), dtype='uint8')
        self.renderType[:] = self.defaults['renderType']

        # For the renderer, indexed by ID * 16 + meta
        self.stateFlags = numpy.zeros(id_limit * 16, dtype='uint8')

        # For the renderer: if nonzero, a face toward a block with the same number is not drawn.
        self.selfCullGroup = numpy.zeros(id_limit, dtype='uint16')

        self.name = "Unnamed Set"
        self.namePrefix = "minecraft:"

    def getBlockTypeAttr(self, block, attr):
        """
        Return an attribute from the given block's json dict.

        :param block:
        :type block:
        :param attr:
        :type attr:
        :return:
        :rtype:
        """

        nameAndState = self.statesByID.get((block.ID, block.meta))
        if nameAndState is None:
            nameAndState = self.statesByID.get((block.ID, 0))
        if nameAndState is None:
            if attr == "nameAndState":
                return "UNKNOWN_NAME[id=%d,meta=%d]"
            return self.defaults[attr]

        if attr == "nameAndState":
            return nameAndState

        try:
            blockJson = self.blockJsons[nameAndState]
        except KeyError:
            return self.defaults[attr]

        if attr == "json":
            return blockJson

        retval = blockJson.get(attr)
        if retval is None:
            retval = self.defaults[attr]

        return retval

    def __repr__(self):
        return "<BlockTypeSet ({0})>".format(self.name)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __len__(self):
        return len(self.allBlocks)

    def __iter__(self):
        return iter(self.allBlocks)

    def __getitem__(self, nameAndState):
        """

        Possible uses:
            # block ID
            level.blocktypes[120]

            # block ID, meta
            level.blocktypes[120, 0]

            # returns Air
            level.blocktypes["air"]

            # returns default state of Powered Rail
            level.blocktypes["golden_rail"]

            # returns Powered Rail with given state
            level.blocktypes["golden_rail[powered=false,shape=north_south]"]

        :type nameAndState: str
        """

        if isinstance(nameAndState, tuple):
            internalName, blockState = nameAndState
            if isinstance(internalName, str):
                if ":" not in internalName:
                    internalName = self.namePrefix + internalName
                try:
                    ID, meta = self.IDsByState[internalName + blockState]
                except KeyError:
                    raise KeyError("No block found for name and state: %s%s" % (internalName, blockState))
            else:  # (ID, meta)
                ID, meta = nameAndState

        elif isinstance(nameAndState, str):
            if ":" not in nameAndState:
                nameAndState = self.namePrefix + nameAndState
            if nameAndState not in self.IDsByState:
                if nameAndState in self.defaultBlockstates:
                    nameAndState += self.defaultBlockstates[nameAndState]
            try:
                ID, meta = self.IDsByState[nameAndState]
            except KeyError:
                raise KeyError("No block found for name and state: %s" % (nameAndState,))
        else:  # block ID
            ID = nameAndState
            meta = 0

        return BlockType(ID, meta, self)

    def discardIDs(self, blockIDs):
        blockIDs = set(blockIDs)
        blocktypes = [b for b in self.allBlocks if b.ID in blockIDs]
        for b in blocktypes:
            self.allBlocks.discard(b)

    def discardInternalNameMetas(self, nameMetas):
        nameMetas = set(nameMetas)

        blocktypes = [bt for bt in self.allBlocks
                      if (bt.internalName, bt.meta) in nameMetas]

        for b in blocktypes:
            self.allBlocks.discard(b)

    def addBlocktypes(self, blocktypes):
        for b in blocktypes:
            self.allBlocks.add(b)

    def blocksMatching(self, name):
        name = name.lower()
        return [v for v in self.allBlocks if name in v.displayName.lower() or name in v.aka.lower()]

    def addIDMappingFromJSON(self, entries):
        for ID, meta, nameAndState in entries:
            internalName, blockState = splitInternalName(nameAndState)
            self.IDsByState[nameAndState] = ID, meta
            self.IDsByName[internalName] = ID
        self.statesByID = {v: k for (k, v) in self.IDsByState.items()}
        for (ID, meta), alias in self.metaAliases.items():
            if (ID, alias) in self.statesByID and (ID, meta) not in self.statesByID:
                self.statesByID[ID, meta] = self.statesByID[ID, alias]
        self.namesByID = {v: k for (k, v) in self.IDsByName.items()}

    def addBlockIDsFromSchematicTag(self, mapping):
        for ID, tag in mapping.items():
            try:
                ID = int(ID)
            except ValueError:
                log.error("Can't parse %s as an integer.")
                continue

            name = tag.value
            if name not in self.IDsByName:
                self.IDsByName[name] = ID
                self.namesByID[ID] = name

    def addItemIDsFromSchematicTag(self, mapping):
        pass

    def addBlocksFromJSON(self, blockJson):
        for block in blockJson:
            try:
                self.addJsonBlock(block)
            except (KeyError, ValueError) as e:
                log.warning(u"Exception while parsing block: %r", e)
                traceback.print_exc()
                log.warning(u"Block dict: \n%s", block)
                raise

        for block in self.allBlocks:
            if block.internalName not in self.defaultBlockstates:
                self.defaultBlockstates[block.internalName] = block.blockState

    def addJsonBlock(self, blockJson):
        """
        Adds a block to this blockset using the keys and values in `block`.

        Required keys:
            internalName - String used by Minecraft to identify this block

        :type blockJson: dict
        :return:
        :rtype:
        """

        ID = blockJson.get("id")
        internalName = blockJson.get("internalName")
        if ID is None and internalName is None:
            raise ValueError("Block definition must have either `id` or `internalName` attribute.")


        #log.debug("Adding block: \n%s", json.dumps(blockJson, indent=2))
        blockState = blockJson.get("blockState", internalName)
        _, blockState = splitInternalName(blockState)

        # XXX raw jsons are created with 'blockState': <nameAndState>
        blockJson['blockState'] = blockState
        nameAndState = internalName + blockState

        IDmeta = self.IDsByState.get(nameAndState)
        if IDmeta is None:
            log.info("No ID mapping for %s, skipping...", internalName)
            return
        ID, meta = IDmeta
        self.allBlocks.add(BlockType(ID, meta, self))

        oldJson = self.blockJsons.get(nameAndState)
        if oldJson is None:
            oldJson = self.blockJsons[nameAndState] = blockJson
        else:
            oldJson.update(blockJson)

        blockJson = oldJson
        del oldJson

        if blockJson.get("defaultState"):
            self.defaultBlockstates[internalName] = blockState

        for key in [
            'opaqueCube',
            'brightness',
            'opacity',
            'useNeighborBrightness',
            'normalCube',
            'materialLiquid',
            'renderType',
        ]:  # does not have data axis
            if key in blockJson:
                array = getattr(self, key)
                if key == 'renderType':
                    # One render type for all sixteen data values: a block some of whose states have a model made
                    # from what the game draws keeps that, as the states without one are drawn as unknown blocks
                    # either way, where otherwise the last state read would decide for them all.
                    modelIDs = self.__dict__.setdefault('_generatedModelIDs', set())
                    if blockJson.get('generatedModel'):
                        modelIDs.add(ID)
                    elif ID in modelIDs:
                        continue
                array[ID] = blockJson[key]

        color = blockJson.get('renderColor')
        if color is not None:
            self.renderColor[ID, meta or slice(None)] = ((color >> 16) & 0xff,
                                                              (color >> 8) & 0xff,
                                                              color & 0xff)

        color = blockJson.get('materialMapColor')
        if color is not None:
            self.mapColor[ID, meta or slice(None)] = ((color >> 16) & 0xff,
                                                           (color >> 8) & 0xff,
                                                           color & 0xff)


# States that idmapping_raw_1_12.json lists at a meta the game reads them from but never saves them with, as
# (ID, that meta): the meta the game uses. From 1.9 the game reads a block with such a meta as air, so these must be
# saved with the game's own. Found from the 1.12.2 debug world, where the game places every state with its own meta.
GAME_METAS_1_12 = {
    (54, 0): 2, (61, 0): 2, (62, 0): 2, (65, 0): 2, (68, 0): 2, (90, 0): 1, (130, 0): 2, (146, 0): 2, (177, 0): 2,
    (132, 0): 2, (132, 1): 3, (132, 4): 6, (132, 5): 7, (132, 8): 10, (132, 9): 11, (132, 12): 14,
}


class PCBlockTypeSet(BlockTypeSet):
    def __init__(self, itemStackVersion=None):
        super(PCBlockTypeSet, self).__init__()
        self.itemStackVersion = itemStackVersion or VERSION_1_7
        self.name = "Alpha"
        self.addIDMappingFromJSON(getJsonFile("idmapping_raw_1_12.json"))
        self.addIDMappingFromJSON(getJsonFile("idmapping.json"))
        self._useTheGamesMetas()
        self.addBlocksFromJSON(getJsonFile("minecraft_raw_1_12.json"))
        self.addBlocksFromJSON(getJsonFile("minecraft.json"))
        self.addMetaAliases()

        self.itemTypes = itemtypes.PCItemTypeSet()

    def _useTheGamesMetas(self):
        """ Give the states in GAME_METAS_1_12 the game's metas. The old ones still read as the same states. """
        for (ID, listed), meta in GAME_METAS_1_12.items():
            state = self.statesByID.get((ID, listed))
            if state is not None and self.IDsByState.get(state) == (ID, listed) and (ID, meta) not in self.statesByID:
                self.IDsByState[state] = ID, meta
        self.statesByID = {v: k for (k, v) in self.IDsByState.items()}

    def addMetaAliases(self):
        """
        The tables list one meta for each block state, but the game reads other metas as some of the same states:
        a chest with meta 2 faces north just as one with meta 0 does, and the top of any double plant is meta 8
        with other bits that mean nothing. Each meta not listed is given the state of the listed meta whose bits
        it has the most of, and so the larger of two with as many. They are kept in metaAliases as
        (ID, meta) -> listed meta, and statesByID gives them that meta's state.
        """
        listed = defaultdict(set)
        for ID, meta in self.statesByID:
            listed[ID].add(meta)
        for ID, metas in listed.items():
            for meta in range(16):
                if meta in metas:
                    continue
                if (ID, meta) in GAME_METAS_1_12 and GAME_METAS_1_12[ID, meta] in metas:
                    alias = GAME_METAS_1_12[ID, meta]
                else:
                    candidates = [m for m in metas if m & meta == m]
                    if not candidates:
                        continue
                    alias = max(candidates, key=lambda m: (bin(m).count("1"), m))
                self.metaAliases[ID, meta] = alias
                self.statesByID[ID, meta] = self.statesByID[ID, alias]
                self.renderColor[ID, meta] = self.renderColor[ID, alias]
                self.mapColor[ID, meta] = self.mapColor[ID, alias]
                self.stateFlags[ID * 16 + meta] = self.stateFlags[ID * 16 + alias]


VERSION_1_7 = 17
VERSION_1_8 = 18


def _modernConverter(destTypes, sourceTypes):
    """ Virtual IDs mean nothing to another set of block types, so each state is matched by its name. """
    known = {}

    def _convert(IDs, metas):
        pairs, inverse = numpy.unique(numpy.asarray(IDs).astype("u4") * 16 + metas, return_inverse=True)
        newIDs = numpy.zeros(len(pairs), "uint16")
        newMetas = numpy.zeros(len(pairs), "uint8")
        for index, pair in enumerate(pairs.tolist()):
            target = known.get(pair)
            if target is None:
                nameAndState = sourceTypes.statesByID.get((pair >> 4, pair & 15))
                if nameAndState is None:
                    target = (pair >> 4, pair & 15) if destTypes.statesByID.get((pair >> 4, pair & 15)) else (0, 0)
                else:
                    name, _, state = nameAndState.partition("[")
                    properties = [tuple(p.split("=", 1)) for p in state.rstrip("]").split(",")] if state else None
                    target = destTypes.idAndMeta(name, properties)
                known[pair] = target
            newIDs[index], newMetas[index] = target
        shape = numpy.shape(IDs)
        return newIDs[inverse].reshape(shape), newMetas[inverse].reshape(shape)

    return _convert


def blocktypeConverter(destTypes, sourceTypes, replaceUnknownWith=None):
    """

    :param destTypes:
    :type destTypes: BlockTypeSet
    :param sourceTypes:
    :type sourceTypes: BlockTypeSet
    :return:
    :rtype:
    """
    if destTypes is sourceTypes:
        return lambda ID, meta: (ID, meta)

    destIsModern = hasattr(destTypes, "idAndMeta")
    if destIsModern and hasattr(sourceTypes, "idAndMeta"):
        return _modernConverter(destTypes, sourceTypes)
    if destIsModern or hasattr(sourceTypes, "idAndMeta"):
        raise ValueError("Blocks can't be copied between worlds from before and after Minecraft 1.13.")

    idTable = numpy.arange(0, id_limit, dtype=numpy.uint16)
    for name, ID in sourceTypes.IDsByName.items():
        if name in destTypes.IDsByName:
            try:
                idTable[ID] = destTypes.IDsByName[name]
            except IndexError:
                log.error("Can't insert %s->%s into %s (%s???) ??", ID, destTypes.IDsByName[name], idTable.shape, type(ID))
                raise
        elif replaceUnknownWith is not None:
            idTable[ID] = replaceUnknownWith

    def _convert(ID, meta):
        return idTable[ID], meta

    return _convert

