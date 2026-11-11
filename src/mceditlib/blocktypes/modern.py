"""
    modern

    Block types for worlds from Minecraft 1.13 and later, which have no numeric block IDs.

    The rest of MCEdit works with (ID, meta) pairs, so each "name[state]" gets a virtual pair that is
    valid only while this set exists. Virtual IDs must never be saved: the world keeps its own names.

    Properties such as opacity are stored per ID with no meta axis, so one ID only holds states that
    agree on them. A block with more than 16 such states uses several IDs.
"""
import logging
import os
import re
from collections import OrderedDict

import numpy

from mceditlib import nbt
from mceditlib.blocktypes import BlockTypeSet, id_limit, itemtypes, VERSION_1_8, STATE_REDSTONE_WIRE, \
    STATE_UNRESOLVED, STATE_WATERLOGGED
from mceditlib.blocktypes.block_pack import BlockPack
from mceditlib.blocktypes.fallback_shapes import fallbackModel
from mceditlib.exceptions import LevelFormatError

log = logging.getLogger(__name__)

PACK_FOLDER = os.path.join(os.path.dirname(__file__), "packs")

STATES_PER_ID = 16

# The tables with one entry per ID. States with different values cannot share an ID.
PER_ID_ATTRIBUTES = ("opaqueCube", "normalCube", "opacity", "brightness", "useNeighborBrightness",
                     "materialLiquid", "renderType")

# All empty, and much of MCEdit treats ID 0 as nothing there. They differ only by meta.
AIR_NAMES = ("minecraft:air", "minecraft:cave_air", "minecraft:void_air")

RENDER_LIQUID = 1
RENDER_MODEL = 3

# Blocks whose color the game takes from the biome, and how. Block packs can't say, because the block
# is exported with no world around it. Other tinted blocks use the single color given in their pack.
BIOME_TINTS = {
    "grass": ("grass_block", "short_grass", "tall_grass", "fern", "large_fern", "potted_fern", "sugar_cane",
              "pink_petals"),
    "foliage": ("oak_leaves", "jungle_leaves", "acacia_leaves", "dark_oak_leaves", "vine"),
    "foliagePine": ("spruce_leaves",),
    "foliageBirch": ("birch_leaves",),
}
BIOME_TINT_BY_NAME = {"minecraft:" + name: tint for tint, names in BIOME_TINTS.items() for name in names}

# Tinted the same everywhere, by a color that the game does not give when there is no world around.
FIXED_TINTS = {
    "minecraft:mangrove_leaves": 0x92c648,
    "minecraft:lily_pad": 0x208030,
    "minecraft:water_cauldron": 0x3f76e4,
}

# Always have water around them, so they have no waterlogged property.
ALWAYS_IN_WATER = ("minecraft:kelp", "minecraft:kelp_plant", "minecraft:seagrass", "minecraft:tall_seagrass",
                   "minecraft:bubble_column")

# The game does not draw the faces between two of these when they are the same block.
SELF_CULLING = re.compile(r"(glass|glass_pane|iron_bars|:ice|:frosted_ice|:slime_block|:honey_block)$")

_packs = {}


def _loadBuiltInPack(name):
    pack = _packs.get(name)
    if pack is None:
        pack = _packs[name] = BlockPack(os.path.join(PACK_FOLDER, name))
    return pack


def joinNameAndProperties(name, properties):
    """
    Return the "name[a=1,b=2]" string for a block with the given (key, value) properties. The world
    keeps properties in no particular order, and block packs have them sorted.
    """
    if not properties:
        return name
    return "%s[%s]" % (name, ",".join("%s=%s" % pair for pair in sorted(properties)))


class ModernBlockTypeSet(BlockTypeSet):
    """
    :ivar itemTypes: Item types are not worked out for these worlds yet. Each item holds its own name.
    """

    hasHiddenStates = False  # since Minecraft 1.13, all states are saved
    modelNamesAreFull = True
    textureFolder = "block"

    def __init__(self, profile, packs=()):
        """
        :param profile: The format of the world.
        :param packs: Block packs to use, the most important first. All come before the game's own table.
        """
        super(ModernBlockTypeSet, self).__init__()
        self.profile = profile
        self.name = profile.name
        self.itemStackVersion = VERSION_1_8
        self.itemTypes = itemtypes.PCItemTypeSet()

        self._nextID = 1  # 0 is air
        self._unknownSlots = {}  # internal name -> (ID, next free meta)
        self._selfCullGroups = {}  # internal name -> number
        self._nameAndStatesByBlock = {}  # internal name -> list of "name[state]"
        self._originalJsons = {}  # "name[state]" -> its json from before block definitions were applied
        self._unresolvedSectionsSeen = set()
        self._unresolvedBlockCounts = {}  # internal name -> number of blocks seen
        self.blockEntityIDs = {}  # internal name -> ID of the block entity it needs
        self.modelForLateState = None  # called with (ID, meta, ID to copy from, meta to copy from)

        for pack in packs:
            self.addBlockPack(pack)
        self.addBlockPack(_loadBuiltInPack(profile.blockPack))

    # --- Assigning virtual IDs ---

    def _allocateID(self):
        ID = self._nextID
        if ID >= id_limit:
            raise LevelFormatError("This world uses more block states than MCEdit has room for.")
        self._nextID += 1
        return ID

    def _assign(self, ID, meta, attributes):
        name = attributes["internalName"]
        self.IDsByState[name + attributes["blockState"]] = ID, meta
        self.statesByID[ID, meta] = name + attributes["blockState"]
        self.namesByID.setdefault(ID, name)
        self.IDsByName.setdefault(name, ID)
        self._nameAndStatesByBlock.setdefault(name, []).append(name + attributes["blockState"])
        self._setRenderFlags(ID, meta, name, attributes["blockState"], attributes.get("cullsSameBlock"))
        self.addJsonBlock(attributes)

    def _setRenderFlags(self, ID, meta, name, blockState, cullsSameBlock=None):
        flags = 0
        if "waterlogged=true" in blockState or name in ALWAYS_IN_WATER:
            flags |= STATE_WATERLOGGED
        if name == "minecraft:redstone_wire":
            flags |= STATE_REDSTONE_WIRE
        self.stateFlags[ID * 16 + meta] = flags

        # Glass and the like hide their faces against the same block. A block pack says so for blocks from mods.
        if cullsSameBlock or SELF_CULLING.search(name):
            self.selfCullGroup[ID] = self._selfCullGroups.setdefault(name, len(self._selfCullGroups) + 1)

    @staticmethod
    def _addRenderHints(attributes):
        """ Work out from a state's attributes in its pack the ones that the renderer goes by. """
        name = attributes["internalName"]
        tint = BIOME_TINT_BY_NAME.get(name)
        if tint is not None:
            attributes["biomeTintType"] = tint
        if name in FIXED_TINTS:
            attributes["color"] = FIXED_TINTS[name]
        elif "tintColor" in attributes:
            attributes["color"] = attributes["tintColor"] & 0xffffff

        model = fallbackModel(attributes)
        if model is not None and attributes.get("modelSource") == "json" and not name.startswith("minecraft:"):
            # A mod's block entity whose renderer's faces could not be had, but whose model files can be drawn, as
            # HBM's doors' models from their own loaders: those are nearer its look than a box.
            attributes["renderType"] = RENDER_MODEL
        elif model is not None:
            attributes["fallbackModel"] = model
            attributes["renderType"] = RENDER_MODEL

    def redstonePower(self, ID, meta):
        """ Return the power of a redstone wire state. """
        return int(self.statesByID[ID, meta].partition("power=")[2].partition(",")[0].rstrip("]"))

    def addBlockPack(self, pack):
        """
        Give every state in the pack a virtual ID, and add it to this set. A block that was already
        added stays as it is, so a block comes whole from the first pack to have it.
        """
        for entity in pack.blockEntities:
            for name in entity["blocks"]:
                self.blockEntityIDs.setdefault(name, entity["id"])

        statesByBlock = OrderedDict()
        for attributes in pack.states():
            if attributes["internalName"] in self._nameAndStatesByBlock:
                continue
            self._addRenderHints(attributes)
            statesByBlock.setdefault(attributes["internalName"], []).append(attributes)

        for meta, name in enumerate(AIR_NAMES):
            for attributes in statesByBlock.pop(name, ()):
                self._assign(0, meta, attributes)

        for name, states in statesByBlock.items():
            if self._isFluid(states):
                self._assignFluid(states)
            else:
                self._assignInGroups(states)

    @staticmethod
    def _isFluid(states):
        return states[0].get("renderType") == RENDER_LIQUID and all("level=" in s["blockState"] for s in states)

    def _assignFluid(self, states):
        # The renderer takes the meta of a fluid to be its level, so one ID holds all of the levels.
        ID = self._allocateID()
        for attributes in states:
            level = int(attributes["blockState"].partition("level=")[2].partition(",")[0].rstrip("]"))
            self._assign(ID, level, attributes)

    def _assignInGroups(self, states):
        groups = OrderedDict()
        for attributes in states:
            key = tuple(attributes.get(a) for a in PER_ID_ATTRIBUTES)
            groups.setdefault(key, []).append(attributes)

        for group in groups.values():
            # The default state goes first, because ID alone means meta 0.
            group.sort(key=lambda attributes: not attributes.get("defaultState"))
            for start in range(0, len(group), STATES_PER_ID):
                ID = self._allocateID()
                for meta, attributes in enumerate(group[start:start + STATES_PER_ID]):
                    self._assign(ID, meta, attributes)

    def _addUnknownState(self, name, blockState):
        """
        Add a state this set was not told about. If its block is known, as when a mod has added a new
        property, it looks and behaves like the block's default state. Otherwise, like the unknown
        blocks of older worlds, it is drawn as an opaque question mark box. Either way it keeps its
        name and state, so nothing about it is lost.
        """
        ID, meta = self._unknownSlots.get(name, (None, STATES_PER_ID))
        if meta >= STATES_PER_ID:
            ID, meta = self._allocateID(), 0
        self._unknownSlots[name] = ID, meta + 1

        nameAndState = name + blockState
        baseName = name + self.defaultBlockstates.get(name, "") if name in self.defaultBlockstates else None
        base = self.blockJsons.get(baseName)
        if base is not None:
            attributes = dict(base, blockState=blockState, defaultState=False)
            original = self._originalJsons.get(baseName)
            if original is not None:
                self._originalJsons[nameAndState] = dict(original, blockState=blockState, defaultState=False)
        else:
            attributes = {
                "internalName": name,
                "blockState": blockState,
                "displayName": name.partition(":")[2] or name,
                "unknown": True,
            }

        self._assign(ID, meta, attributes)
        self.stateFlags[ID * 16 + meta] |= STATE_UNRESOLVED
        if name not in self.defaultBlockstates:
            self.defaultBlockstates[name] = blockState

        if base is not None and self.modelForLateState is not None:
            self.modelForLateState(ID, meta, *self.IDsByState[baseName])
        log.info("Added block state %s as %s:%s, which %s", nameAndState, ID, meta,
                 "looks like %s" % baseName if base is not None else "is not known")
        return ID, meta

    # --- User definitions ---

    def applyBlockDefinitions(self, definitions):
        """
        Apply the block definitions a user has made, replacing any applied before. They come before
        anything else that describes a block. A definition without a state covers all states of a
        block, and can also say how it treats light. One with a state only says how that state looks,
        because how a block treats light is stored by ID and states can share an ID.

        A definition can name a block the world has not been seen to have yet, or that doesn't exist
        at all. It is simply there if a chunk asks for it.

        :param definitions: Objects with attributes as in mcedit2.dialogs.configure_blocks.BlockDefinition
        """
        self._removeBlockDefinitions()
        for definition in definitions:
            name = definition.internalName
            if ":" not in name:
                name = self.namePrefix + name

            state = getattr(definition, "blockState", "") or ""
            if state:
                if not state.startswith("["):
                    state = "[%s]" % state
                self.idAndMeta(name, sorted(tuple(pair.split("=", 1)) for pair in state[1:-1].split(",") if "=" in pair))
                keys = [name + state]
            else:
                if name not in self._nameAndStatesByBlock:
                    self.idAndMeta(name)
                keys = self._nameAndStatesByBlock[name]

            for key in keys:
                self._applyDefinitionTo(key, definition, wholeBlock=not state)

    def _removeBlockDefinitions(self):
        for key, original in self._originalJsons.items():
            self.blockJsons[key] = dict(original)
            self._setPerIDAttributes(self.IDsByState[key], original)
        self._originalJsons = {}

    def _setPerIDAttributes(self, pair, attributes):
        for name in PER_ID_ATTRIBUTES:
            if name in attributes:
                getattr(self, name)[pair[0]] = attributes[name]

    def _applyDefinitionTo(self, key, definition, wholeBlock):
        attributes = self.blockJsons[key]
        self._originalJsons.setdefault(key, dict(attributes))

        modelPath = getattr(definition, "modelPath", None)
        if modelPath:
            attributes["forcedModel"] = modelPath
            attributes["forcedModelTextures"] = definition.modelTextures
            attributes["forcedModelRotation"] = definition.modelRotations
            attributes["forcedRotationFlags"] = definition.rotationFlags
            attributes["renderType"] = RENDER_MODEL
            attributes["unknown"] = False

        englishName = getattr(definition, "englishName", None)
        if englishName and englishName != definition.internalName:
            attributes["displayName"] = englishName

        if wholeBlock:
            perID = {"renderType": attributes.get("renderType", RENDER_MODEL)}
            opacity = getattr(definition, "opacity", None)
            if opacity is not None:
                attributes["opacity"] = perID["opacity"] = opacity
                attributes["opaqueCube"] = perID["opaqueCube"] = opacity >= 15
            brightness = getattr(definition, "brightness", None)
            if brightness is not None:
                attributes["brightness"] = perID["brightness"] = brightness
            self._setPerIDAttributes(self.IDsByState[key], perID)

    # --- What the world has that nothing describes ---

    def noteUnresolvedBlocks(self, sectionKey, IDs, metas, counts):
        """
        Record how many blocks of each palette entry in a section were not described by anything.

        :param sectionKey: Identifies the section, because it can be read more than once.
        :param IDs: The IDs of the entries in the palette.
        :param metas: The metas of the entries in the palette.
        :param counts: The number of blocks using each entry in the palette.
        """
        if sectionKey in self._unresolvedSectionsSeen:
            return
        self._unresolvedSectionsSeen.add(sectionKey)
        for ID, meta, count in zip(IDs, metas, counts):
            if self.stateFlags[int(ID) * 16 + int(meta)] & STATE_UNRESOLVED:
                name = self.statesByID[int(ID), int(meta)].partition("[")[0]
                self._unresolvedBlockCounts[name] = self._unresolvedBlockCounts.get(name, 0) + int(count)

    def unresolvedBlockReport(self):
        """
        Describe the blocks seen so far that nothing described, most common first. A block that the
        user has since defined is left out.

        :return: Dicts with `internalName`, `blocks` (number of blocks seen), `states` (their names)
            and `knownBlock` (whether the block itself was known, and only these states were not).
        """
        report = []
        for name, count in self._unresolvedBlockCounts.items():
            flagged = [key for key in self._nameAndStatesByBlock[name]
                       if self.stateFlags[self.IDsByState[key][0] * 16 + self.IDsByState[key][1]] & STATE_UNRESOLVED]
            first = self.blockJsons[self._nameAndStatesByBlock[name][0]]
            if first.get("forcedModel"):
                continue
            report.append({"internalName": name, "blocks": count, "states": sorted(key[len(name):] for key in flagged),
                           "knownBlock": not first.get("unknown")})
        report.sort(key=lambda entry: (-entry["blocks"], entry["internalName"]))
        return report

    # --- Reading palettes ---

    def idAndMeta(self, name, properties=None):
        """
        Return the virtual (ID, meta) of a block state, adding it if it was not known before.

        :param name: The block's full name.
        :param properties: The block's properties as (key, value) pairs in any order.
        """
        nameAndState = joinNameAndProperties(name, properties)
        pair = self.IDsByState.get(nameAndState)
        if pair is None:
            pair = self._addUnknownState(name, nameAndState[len(name):])
        return pair

    def paletteTables(self, paletteTag):
        """
        Return arrays that turn indexes into the given palette into virtual IDs and metas.

        :param paletteTag: A list of block states as saved in a chunk section, in any of the forms the game has
            used. See paletteEntryState.
        :return: (IDs, metas)
        :rtype: (numpy.ndarray, numpy.ndarray)
        """
        IDs = numpy.zeros(len(paletteTag), dtype="uint16")
        metas = numpy.zeros(len(paletteTag), dtype="uint8")
        for index, entry in enumerate(paletteTag):
            name, properties = paletteEntryState(entry)
            if properties is None:
                # Since 26.3 the properties of a default state may be left out. Before that, a block with
                # properties always had them saved, so this is a block that has none.
                state = self.defaultBlockstates.get(name, "")
                pair = self.IDsByState.get(name + state)
                if pair is None:
                    pair = self.idAndMeta(name)
                IDs[index], metas[index] = pair
            else:
                IDs[index], metas[index] = self.idAndMeta(name, properties)
        return IDs, metas


def paletteEntryState(entry):
    """
    Return the block name of a palette entry, and its properties as (key, value) pairs, or None if it has
    none saved.

    Up to 26.2 an entry is a compound with "Name" and maybe "Properties". From 26.3 the keys are "id" and
    "properties", and an entry may instead be a string: the name, maybe followed by [key=value,...].
    """
    if entry.tagID == nbt.ID_COMPOUND and len(entry) == 1 and "" in entry:
        # A list can't hold both strings and compounds, so in one that has both the strings are each wrapped in a
        # compound under the empty name.
        entry = entry[""]
    if entry.tagID == nbt.ID_STRING:
        text = entry.value
        name, bracket, rest = text.partition("[")
        if not bracket:
            return name, None
        pairs = [pair.partition("=") for pair in rest.rstrip("]").split(",") if pair]
        return name, [(key, value) for key, _, value in pairs]

    nameTag = entry.get("id")
    if nameTag is None:
        nameTag = entry["Name"]
    propertiesTag = entry.get("properties")
    if propertiesTag is None:
        propertiesTag = entry.get("Properties")
    if propertiesTag is None:
        return nameTag.value, None
    return nameTag.value, [(key, tag.value) for key, tag in propertiesTag.items()]
