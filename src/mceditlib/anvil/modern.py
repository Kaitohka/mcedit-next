"""
    modern

    Reads and writes worlds saved by Minecraft 1.18 and later. Chunks are converted to the form the rest of
    MCEdit works with as they are read, and back when they are written, with anything MCEdit doesn't
    know about left as it was. See FormatProfile for which versions can be opened.
"""
import json
import logging
import os
import zlib

import numpy

from mceditlib import nbt, nbtattr
from mceditlib.anvil.adapter import AnvilChunkFormatError, AnvilPlayerRef, AnvilWorldAdapter, AnvilWorldMetadata, \
    SessionLockLost, packNibbleArray, unpackNibbleArray
from mceditlib.anvil.entities import PCEntityRef, PCTileEntityRef
from mceditlib.anvil.biome_types import LEGACY_BIOME_NAMES
from mceditlib.anvil.format_profile import FIRST_ENTITY_FILES_DATA_VERSION, FIRST_LIGHT_ENGINE_DATA_VERSION, \
    FIRST_LOWERCASE_PALETTE_DATA_VERSION, FIRST_PALETTE_DATA_VERSION, FIRST_ROOT_CHUNK_DATA_VERSION, \
    FIRST_UNSPANNED_DATA_VERSION, profileForDataVersion, releaseForDataVersion
from mceditlib.anvil.format_profile import FIRST_FILE_LOCK_DATA_VERSION
from mceditlib.anvil.session_lock import SessionLock
from mceditlib.anvil.worldfolder import AnvilWorldFolder, ENTITY_STORE, POI_STORE, isStoreName, splitStore
from mceditlib.blocktypes import STATE_UNRESOLVED
from mceditlib.blocktypes.modern import AIR_NAMES, ModernBlockTypeSet, paletteEntryState
from mceditlib.blocktypes.pack_library import selectPacks, worldMods
from mceditlib.exceptions import ChunkNotPresent, LevelFormatError
from mceditlib.geometry import Vector
from mceditlib.nbt import NBTFormatError
from mceditlib.revisionhistory import RevisionChanges, RevisionHistory
from mceditlib.selection import BoundingBox

log = logging.getLogger(__name__)

SECTION_SHAPE = (16, 16, 16)
BLOCKS_PER_SECTION = 4096
MIN_BLOCK_BITS = 4
FULL_LIGHT = 15
UNKNOWN_BIOME = 255
SURFACE_HEIGHTMAP = "WORLD_SURFACE"
STATUS_FULL = "minecraft:full"
# 1.15 (19w36a): biomes for each 4x4x4 cell instead of each column.
FIRST_3D_BIOMES_DATA_VERSION = 2203
PLAINS_NUMBER = 1

# What the game called a finished chunk before 1.18.
LEGACY_FINISHED_STATUSES = ("full", "fullchunk", "postprocessed", "minecraft:full")
READ_ONLY_MESSAGE = "This world is opened read only."


def unpackIndices(longs, bitsPerEntry, count, spanning=False):
    """
    Unpack an array of longs holding `count` unsigned integers of `bitsPerEntry` bits each. Each long is
    filled from its lowest bits up.

    :type longs: numpy.ndarray
    :type bitsPerEntry: int
    :type count: int
    :param spanning: Whether an entry can start in one long and end in the next, as before Minecraft 1.16.
        Otherwise the bits left over at the top of each long are unused.
    :rtype: numpy.ndarray
    """
    if spanning:
        return _unpackSpanningIndices(longs, bitsPerEntry, count)
    entriesPerLong = 64 // bitsPerEntry
    longsNeeded = (count + entriesPerLong - 1) // entriesPerLong
    if len(longs) < longsNeeded:
        raise AnvilChunkFormatError("Expected %d longs for %d entries of %d bits, found %d"
                                    % (longsNeeded, count, bitsPerEntry, len(longs)))

    values = numpy.asarray(longs[:longsNeeded]).astype("u8")
    shifts = numpy.arange(entriesPerLong, dtype="u8") * numpy.uint64(bitsPerEntry)
    mask = numpy.uint64((1 << bitsPerEntry) - 1)
    return ((values[:, numpy.newaxis] >> shifts) & mask).ravel()[:count].astype("intp")


def _unpackSpanningIndices(longs, bitsPerEntry, count):
    longsNeeded = (count * bitsPerEntry + 63) // 64
    if len(longs) < longsNeeded:
        raise AnvilChunkFormatError("Expected %d longs for %d entries of %d bits, found %d"
                                    % (longsNeeded, count, bitsPerEntry, len(longs)))
    values = numpy.asarray(longs[:longsNeeded]).astype("u8")
    starts = numpy.arange(count, dtype="u8") * numpy.uint64(bitsPerEntry)
    first = (starts >> numpy.uint64(6)).astype("intp")
    offsets = starts & numpy.uint64(63)
    mask = numpy.uint64((1 << bitsPerEntry) - 1)

    result = values[first] >> offsets
    # The entries that run on into the next long take their top bits from it.
    crossing = offsets + numpy.uint64(bitsPerEntry) > numpy.uint64(64)
    if crossing.any():
        upper = values[first[crossing] + 1] << (numpy.uint64(64) - offsets[crossing])
        result[crossing] |= upper
    return (result & mask).astype("intp")


def packIndices(indices, bitsPerEntry):
    """
    The reverse of unpackIndices: pack small integers into longs, filling each from its lowest bits up, with
    no entry crossing from one long to the next.

    :type indices: numpy.ndarray
    :rtype: numpy.ndarray
    """
    entriesPerLong = 64 // bitsPerEntry
    longCount = (len(indices) + entriesPerLong - 1) // entriesPerLong
    positions = numpy.arange(len(indices))
    values = numpy.asarray(indices).astype("u8") << ((positions % entriesPerLong).astype("u8") * numpy.uint64(bitsPerEntry))
    longs = numpy.zeros(longCount, "u8")
    numpy.bitwise_or.at(longs, positions // entriesPerLong, values)
    return longs.astype(">u8")


def packSpanningIndices(indices, bitsPerEntry):
    """
    The reverse of unpacking with spanning=True: pack small integers into one run of bits from the lowest bit of
    the first long up, as the game did before 1.16.

    :rtype: numpy.ndarray
    """
    count = len(indices)
    values = numpy.asarray(indices).astype("u8")
    starts = numpy.arange(count, dtype="u8") * numpy.uint64(bitsPerEntry)
    first = (starts >> numpy.uint64(6)).astype("intp")
    offsets = starts & numpy.uint64(63)
    longs = numpy.zeros((count * bitsPerEntry + 63) // 64, "u8")
    numpy.bitwise_or.at(longs, first, values << offsets)
    crossing = offsets + numpy.uint64(bitsPerEntry) > numpy.uint64(64)
    if crossing.any():
        numpy.bitwise_or.at(longs, first[crossing] + 1, values[crossing] >> (numpy.uint64(64) - offsets[crossing]))
    return longs.astype(">u8")


def bitsForPalette(size):
    """ Return the bits needed to tell apart `size` palette entries. """
    return (size - 1).bit_length()


# What each numbered block of 1.12.2 became in 1.13.2, as made by scripts/versions/make_flattening_table.py.
FLATTENING_TABLE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "blocktypes", "flattening.json")
_flatteningTable = None


def flatteningTable():
    """ The flattening table as {ID << 4 | meta: "name[properties]"}. """
    global _flatteningTable
    if _flatteningTable is None:
        with open(FLATTENING_TABLE_FILE, encoding="utf-8") as f:
            blocks = json.load(f)["blocks"]
        _flatteningTable = {}
        for key, state in blocks.items():
            number, meta = (int(n) for n in key.split(":"))
            _flatteningTable[number << 4 | meta] = state
    return _flatteningTable


# Blocks renamed after 1.13, by their 1.13 names.
RENAMED_SINCE_1_13 = {
    "minecraft:sign": "minecraft:oak_sign",  # 1.14
    "minecraft:wall_sign": "minecraft:oak_wall_sign",  # 1.14
    "minecraft:grass_path": "minecraft:dirt_path",  # 1.17
    "minecraft:grass": "minecraft:short_grass",  # 1.20.3
}


def stateForThisVersion(state, blocktypes):
    """
    Turn a 1.13 block state into the same block's state in the version the block types are for: its newer name if it
    has been renamed, with properties added since taking their default values and any since removed left out.

    :return: (name, [(key, value), ...] or None)
    """
    name, _, rest = state.partition("[")
    properties = dict(pair.split("=", 1) for pair in rest.rstrip("]").split(",")) if rest else {}
    if name not in blocktypes.IDsByName and RENAMED_SINCE_1_13.get(name) in blocktypes.IDsByName:
        name = RENAMED_SINCE_1_13[name]
    default = blocktypes.defaultBlockstates.get(name)
    if default is not None:
        defaults = dict(pair.split("=", 1) for pair in default.strip("[]").split(",") if pair)
        properties = {key: properties.get(key, value) for key, value in defaults.items()}
    return name, sorted(properties.items()) or None


DYE_COLORS = ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray", "light_gray", "cyan",
              "purple", "blue", "brown", "green", "red", "black")
SKULL_KINDS = (("skeleton_skull", "skeleton_wall_skull"), ("wither_skeleton_skull", "wither_skeleton_wall_skull"),
               ("zombie_head", "zombie_wall_head"), ("player_head", "player_wall_head"),
               ("creeper_head", "creeper_wall_head"), ("dragon_head", "dragon_wall_head"))
SKULL_FACINGS = {2: "north", 3: "south", 4: "west", 5: "east"}
# What a flower pot holds, by the item as 1.12.2 saves it and its data value: the 1.13 block of the pot.
POTTED = dict(
    [(("minecraft:sapling", data), "potted_%s_sapling" % kind)
     for data, kind in enumerate(("oak", "spruce", "birch", "jungle", "acacia", "dark_oak"))]
    + [(("minecraft:red_flower", data), "potted_" + kind)
       for data, kind in enumerate(("poppy", "blue_orchid", "allium", "azure_bluet", "red_tulip", "orange_tulip",
                                    "white_tulip", "pink_tulip", "oxeye_daisy"))]
    + [(("minecraft:yellow_flower", 0), "potted_dandelion"), (("minecraft:brown_mushroom", 0), "potted_brown_mushroom"),
       (("minecraft:red_mushroom", 0), "potted_red_mushroom"), (("minecraft:cactus", 0), "potted_cactus"),
       (("minecraft:deadbush", 0), "potted_dead_bush"), (("minecraft:tallgrass", 2), "potted_fern")]
)
# 1.7.10 saved the item by number.
POTTED_ITEM_NUMBERS = {6: "minecraft:sapling", 31: "minecraft:tallgrass", 32: "minecraft:deadbush",
                       37: "minecraft:yellow_flower", 38: "minecraft:red_flower", 39: "minecraft:brown_mushroom",
                       40: "minecraft:red_mushroom", 81: "minecraft:cactus"}


def _withProperty(state, key, value):
    name, _, rest = state.partition("[")
    properties = dict(pair.split("=", 1) for pair in rest.rstrip("]").split(",")) if rest else {}
    properties[key] = str(value)
    return "%s[%s]" % (name, ",".join("%s=%s" % pair for pair in sorted(properties.items())))


def _renamed(state, name):
    _, bracket, rest = state.partition("[")
    return name + bracket + rest


def blockEntityState(number, meta, state, entity):
    """
    The 1.13 state of a block of 1.12.2 or earlier that kept part of what it is in its block entity, or None if
    the flattening table's state needs nothing from it.
    """
    def value(key, default=0):
        tag = entity.get(key)
        return tag.value if tag is not None else default

    if number == 26:  # bed: its color
        return _renamed(state, "minecraft:%s_bed" % DYE_COLORS[value("color", 14) & 15])
    if number in (176, 177):  # standing and wall banners: the color of their base, saved as a dye's data value
        kind = "wall_banner" if number == 177 else "banner"
        return _renamed(state, "minecraft:%s_%s" % (DYE_COLORS[15 - (value("Base", 15) & 15)], kind))
    if number == 144:  # head: its kind, and on the floor its rotation
        floor, wall = SKULL_KINDS[value("SkullType") % len(SKULL_KINDS)]
        if meta in SKULL_FACINGS:
            return "minecraft:%s[facing=%s]" % (wall, SKULL_FACINGS[meta])
        return "minecraft:%s[rotation=%d]" % (floor, value("Rot") & 15)
    if number == 140:  # flower pot: what is in it
        item = value("Item", "")
        if isinstance(item, int):
            item = POTTED_ITEM_NUMBERS.get(item, "")
        elif item and ":" not in item:
            item = "minecraft:" + item
        potted = POTTED.get((item, value("Data")))
        return "minecraft:" + potted if potted else "minecraft:flower_pot"
    if number == 25:  # note block: its note
        return _withProperty(state, "note", value("note") % 25)
    return None


# --- Saving a chunk last saved by 1.12.2 or earlier ---

# The block entity of each block that blockEntityState reads one for, as 1.12.2 names it.
NUMBERED_ENTITY_IDS = {25: "minecraft:noteblock", 26: "minecraft:bed", 140: "minecraft:flower_pot",
                       144: "minecraft:skull", 176: "minecraft:banner", 177: "minecraft:banner"}
POTTED_ITEMS = {potted: item for item, potted in POTTED.items()}


def parseState(state):
    """ "name[key=value,...]" as (name, {key: value}). """
    name, _, rest = state.partition("[")
    return name, dict(pair.split("=", 1) for pair in rest.rstrip("]").split(",")) if rest else {}


def numberedEntityState(name, properties):
    """
    For a 1.13 state that 1.12.2 kept partly in a block entity, the reverse of blockEntityState: the state that
    the flattening table gives the block on its own, and what its block entity says, as (name, properties,
    {key: tag}). None for any other state.
    """
    path = name.partition(":")[2]
    for color, dye in enumerate(DYE_COLORS):
        if path == dye + "_bed":
            return "minecraft:red_bed", properties, {"color": nbt.TAG_Int(color)}
        if path in (dye + "_banner", dye + "_wall_banner"):
            return name.replace(dye + "_", "white_", 1), properties, {"Base": nbt.TAG_Int(15 - color)}
    for kind, (floor, wall) in enumerate(SKULL_KINDS):
        if path == floor:
            return "minecraft:skeleton_skull", {"rotation": "0"}, {
                "SkullType": nbt.TAG_Byte(kind), "Rot": nbt.TAG_Byte(int(properties.get("rotation", 0)))}
        if path == wall:
            return "minecraft:skeleton_wall_skull", properties, {"SkullType": nbt.TAG_Byte(kind)}
    if path == "flower_pot" or path in POTTED_ITEMS:
        item, data = POTTED_ITEMS.get(path, ("", 0))
        return "minecraft:flower_pot", {}, {"Item": nbt.TAG_String(item), "Data": nbt.TAG_Int(data)}
    if path == "note_block":
        return name, properties, {"note": nbt.TAG_Byte(int(properties.get("note", 0))),
                                  "powered": nbt.TAG_Byte(properties.get("powered") == "true")}
    return None


class NumberedBlocks(object):
    """
    The flattening table backwards, for the block types of one world: the number and data value 1.12.2 would save
    a block as, so that the game makes it into that block again when it upgrades the chunk.
    """
    def __init__(self, blocktypes):
        self.blocktypes = blocktypes
        self.exact = {}
        self.byName = {}
        for key, state in sorted(flatteningTable().items()):
            name, properties = stateForThisVersion(state, blocktypes)
            properties = dict(properties or ())
            self.exact.setdefault((name, tuple(sorted(properties.items()))), key)
            self.byName.setdefault(name, []).append((properties, key))
        self._keys = {}

    @classmethod
    def forBlockTypes(cls, blocktypes):
        table = getattr(blocktypes, "_numberedBlocks", None)
        if table is None:
            table = blocktypes._numberedBlocks = cls(blocktypes)
        return table

    def key(self, ID, meta):
        """
        For a block of the world, (number << 4 | data value, {key: tag} for its block entity or None), or None if
        1.12.2 has no such block. A state that 1.12.2 worked out from the blocks around it, as a fence's
        connections, gets the nearest the table has, and the game works it out again when it upgrades the chunk.
        """
        if (ID, meta) not in self._keys:
            self._keys[ID, meta] = self._find(ID, meta)
        return self._keys[ID, meta]

    def _find(self, ID, meta):
        state = self.blocktypes.statesByID.get((ID, meta))
        if state is None:
            return None
        name, properties = parseState(state)
        if name in AIR_NAMES:
            return 0, None
        fields = None
        entityState = numberedEntityState(name, properties)
        if entityState is not None:
            name, properties, fields = entityState
        if properties:
            name += "[%s]" % ",".join("%s=%s" % pair for pair in sorted(properties.items()))
        name, properties = stateForThisVersion(name, self.blocktypes)
        properties = dict(properties or ())
        key = self.exact.get((name, tuple(sorted(properties.items()))))
        if key is None:
            candidates = self.byName.get(name)
            if not candidates:
                return None
            matching = lambda other: sum(other.get(k) == v for k, v in properties.items())
            key = max(candidates, key=lambda c: (matching(c[0]), -c[1]))[1]
        return key, fields


def looksNewerThanNumbered(tag):
    """
    If an entity or block entity is in the form of 1.20.5 or later, whose item stacks are saved differently. The
    game would read it as 1.12.2's if it went into a chunk that it upgrades from that version.
    """
    if isinstance(tag, nbt.TAG_Compound):
        if "components" in tag or ("count" in tag and "id" in tag and "Count" not in tag):
            return True
        return any(looksNewerThanNumbered(value) for value in tag.values())
    if isinstance(tag, nbt.TAG_List):
        return any(looksNewerThanNumbered(value) for value in tag)
    return False


def legacySectionTag(tag):
    """
    A section tag of a chunk from 1.13 to 1.17, with its blocks under the names that 1.18 gave them. It is for
    reading only. Its arrays are those of the chunk's tags, not copies, but the tags are new: a tag carries its
    own name, and putting the chunk's own tags in here under other names would rename them in the chunk too.
    """
    section = nbt.TAG_Compound()
    for key, value in tag.items():
        if key not in ("Palette", "BlockStates"):
            section[key] = value.copy()
    if "Palette" in tag:
        states = nbt.TAG_Compound()
        states["palette"] = tag["Palette"].copy()
        if "BlockStates" in tag:
            states["data"] = nbt.TAG_Long_Array(tag["BlockStates"].value)
        section["block_states"] = states
    return section


class ModernSection(object):
    """
    A 16x16x16 piece of a chunk, in the same form as AnvilSection. Arrays are indexed YZX.

    :ivar Y: Section's position. Negative in worlds that extend below zero.
    :ivar Blocks: Virtual block IDs, which ModernBlockTypeSet gives meaning to.
    :ivar Data: Virtual block metas.
    :ivar BlockLight: Light from blocks [0..15]
    :ivar SkyLight: Light from the sky [0..15]
    """

    def __init__(self, Y, Blocks, Data, BlockLight, SkyLight):
        self.Y = Y
        self.Blocks = Blocks
        self.Data = Data
        self.BlockLight = BlockLight
        self.SkyLight = SkyLight


class ModernChunkData(object):
    """
    One chunk, offering the same attributes as AnvilChunkData. rootTag is the chunk as it was saved.
    Sections are decoded when first asked for, as a chunk has more of them than one in an older world
    and a good part of them are often not looked at.
    """

    def __init__(self, adapter, cx, cz, dimName, rootTag, entityRoot=None, entitiesUnreadable=False):
        """
        :type adapter: ModernAnvilAdapter
        :type rootTag: nbt.TAG_Compound
        :param entityRoot: The chunk's entry in the entity region file, if it has one.
        :type entityRoot: nbt.TAG_Compound | None
        :param entitiesUnreadable: If its entity data could not be read, which must then not be overwritten.
        :type entitiesUnreadable: bool
        """
        self.adapter = adapter
        self.cx = cx
        self.cz = cz
        self.dimName = dimName
        self.rootTag = rootTag
        self.dirty = False
        self.minHeight, self.maxHeight = adapter.heightRange(dimName)

        dataVersionTag = rootTag.get("DataVersion")
        self.dataVersion = dataVersionTag.value if dataVersionTag is not None else 0
        if self.dataVersion < FIRST_PALETTE_DATA_VERSION and "Level" not in rootTag:
            raise AnvilChunkFormatError("Chunk %s was last saved by Minecraft 1.12 or earlier, but is not in the form "
                                        "those versions saved." % ((cx, cz),))

        # Up to 1.17 a chunk keeps its tags in a "Level" tag, under other names. Such a chunk is read, but
        # changes to it are not saved: the game upgrades it when it loads it.
        self.isLegacyLayout = "Level" in rootTag
        self._spanning = self.dataVersion < FIRST_UNSPANNED_DATA_VERSION
        if self.isLegacyLayout:
            self._chunkTag = rootTag["Level"]
            sectionTags = [legacySectionTag(tag) for tag in self._chunkTag.get("Sections", ())]
            entitiesKey, blockEntitiesKey, blockTicksKey = "Entities", "TileEntities", "TileTicks"
        else:
            self._chunkTag = rootTag
            sectionTags = rootTag.get("sections", ())
            entitiesKey, blockEntitiesKey, blockTicksKey = "entities", "block_entities", "block_ticks"
        chunkTag = self._chunkTag

        self._sectionTags = {int(tag["Y"].value): tag for tag in sectionTags}
        self._sections = {}
        self._sectionPositions = None
        self._skyLights = {}
        self._biomeIndices = {}
        self._heightMap = None
        self._biomes = None

        self._entityRoot = entityRoot
        self._entitiesUnreadable = entitiesUnreadable
        self._entities = entityRoot["Entities"] if entityRoot is not None else nbt.TAG_List(list_type=nbt.ID_COMPOUND)
        self._savedEntities = self._entityBytes(self._entities)

        # A chunk the game has not finished generating keeps entities in the chunk instead, and so did every
        # chunk before 1.17.
        self._ungeneratedEntities = {id(entity) for entity in chunkTag.get(entitiesKey, ())}
        for entity in chunkTag.get(entitiesKey, ()):
            self._entities.append(entity)

        self._blocksHaveChanged = False
        self._blockEntities = chunkTag[blockEntitiesKey] if blockEntitiesKey in chunkTag else nbt.TAG_List(list_type=nbt.ID_COMPOUND)
        self._blockTicks = chunkTag[blockTicksKey] if blockTicksKey in chunkTag else nbt.TAG_List(list_type=nbt.ID_COMPOUND)

    # --- Saving ---

    def buildNBTTag(self):
        """
        Return the chunk's tag as it is to be saved. Sections with no changes stay exactly as they were
        saved, including their palettes, and so does anything else in the chunk that isn't known about.
        """
        if self.isLegacyLayout:
            return self._buildLegacyTag()
        tag = self.rootTag.copy()
        sectionTags = {int(section["Y"].value): section for section in tag.get("sections", ())}

        changed = []
        for cy, section in self._changedSections().items():
            changed.append(cy)
            sectionTag = sectionTags.get(cy)
            if sectionTag is None:
                sectionTag = sectionTags[cy] = self._newSectionTag(cy, sectionTags)
            sectionTag["block_states"] = self._encodeBlocks(section)

        if changed:
            self._forgetWhatTheGameWillWorkOut(tag, sectionTags)

        if changed:
            # Otherwise the list stays as it was saved, in whatever order that was.
            tag["sections"] = nbt.TAG_List([sectionTags[cy] for cy in sorted(sectionTags)], list_type=nbt.ID_COMPOUND)
        if "block_entities" in tag or len(self._blockEntities):
            tag["block_entities"] = self._remainingBlockEntities(changed)
        if "block_ticks" in tag or len(self._blockTicks):
            tag["block_ticks"] = self._blockTicks.copy()

        ungenerated = [e for e in self._entities if id(e) in self._ungeneratedEntities]
        if "entities" in tag and len(ungenerated) != len(tag["entities"]):
            tag["entities"] = nbt.TAG_List([e.copy() for e in ungenerated], list_type=tag["entities"].list_type)
        return tag

    def _changedSections(self):
        """ The sections whose blocks have changed since they were read, as {cy: section}. """
        changed = {}
        for cy, section in self._sections.items():
            oldTag = self._sectionTags.get(cy)
            if oldTag is not None:
                oldBlocks, oldData = self._decodeBlocks(oldTag)
            else:
                oldBlocks = oldData = numpy.zeros(SECTION_SHAPE, "uint8")
            if (section.Blocks != oldBlocks).any() or (section.Data != oldData).any():
                changed[cy] = section
        return changed

    def _buildLegacyTag(self):
        """
        Save a chunk in the form of 1.13 to 1.17, which it was read in. Its data version stays as it was, so a later
        version of the game upgrades it as it would have before.
        """
        tag = self.rootTag.copy()
        level = tag["Level"]
        sectionTags = {int(section["Y"].value): section for section in level.get("Sections", ())}
        # 1.13 cannot load a section without the light of a dimension that has a sky. A new chunk has no section to
        # tell from, and of the game's own dimensions only the overworld has a sky.
        if sectionTags:
            hasSkyLight = any("SkyLight" in section for section in sectionTags.values())
        else:
            hasSkyLight = self.dimName == ""
        relit = self.dataVersion >= FIRST_LIGHT_ENGINE_DATA_VERSION

        numbered = self.dataVersion < FIRST_PALETTE_DATA_VERSION
        changed = self._changedSections()
        if numbered:
            blockEntities = self._remainingBlockEntities(list(changed))
            self._checkNumberedEntities(blockEntities)
        for cy, section in changed.items():
            sectionTag = sectionTags.get(cy)
            if sectionTag is None:
                sectionTag = sectionTags[cy] = nbt.TAG_Compound()
                sectionTag["Y"] = nbt.TAG_Byte(cy)
                if not relit:
                    # 1.13 does not work light out again, so a new section starts out lit by the sky.
                    sectionTag["BlockLight"] = nbt.TAG_Byte_Array(numpy.zeros(2048, "uint8"))
                    if hasSkyLight:
                        sectionTag["SkyLight"] = nbt.TAG_Byte_Array(numpy.full(2048, 0xff, "uint8"))
            if numbered:
                self._encodeNumberedSection(cy, section, sectionTag, blockEntities)
                continue
            palette, indices = self._palette(section)
            bits = max(MIN_BLOCK_BITS, bitsForPalette(len(palette)))
            sectionTag["Palette"] = palette
            if self._spanning:
                sectionTag["BlockStates"] = nbt.TAG_Long_Array(packSpanningIndices(indices, bits))
            else:
                sectionTag["BlockStates"] = nbt.TAG_Long_Array(packIndices(indices, bits))

        if changed:
            if relit:
                # From 1.14 the game lights a chunk again if it says it isn't lit, and works out missing heightmaps.
                self._forgetWhatTheGameWillWorkOut(level, sectionTags)
            level["Sections"] = nbt.TAG_List([sectionTags[cy] for cy in sorted(sectionTags)], list_type=nbt.ID_COMPOUND)

        if numbered:
            level["TileEntities"] = blockEntities
        elif "TileEntities" in level or len(self._blockEntities):
            level["TileEntities"] = self._remainingBlockEntities(list(changed))
        if "TileTicks" in level or len(self._blockTicks):
            level["TileTicks"] = self._blockTicks.copy()

        if self.dataVersion < FIRST_ENTITY_FILES_DATA_VERSION:
            # Before 1.17 every entity is kept in its chunk.
            entities = self._entities
        else:
            entities = [e for e in self._entities if id(e) in self._ungeneratedEntities]
        if "Entities" in level or len(entities):
            level["Entities"] = nbt.TAG_List([e.copy() for e in entities], list_type=nbt.ID_COMPOUND)
        return tag

    def _checkNumberedEntities(self, blockEntities):
        newer = [tag["id"].value if "id" in tag else "?" for tag in list(blockEntities) + list(self._entities)
                 if looksNewerThanNumbered(tag)]
        if newer:
            raise AnvilChunkFormatError(
                "Chunk %s was last saved by Minecraft 1.12.2 or earlier, and the game would read %s in it as that "
                "version's. Load the chunk in the game first, which upgrades it."
                % ((self.cx, self.cz), ", ".join(sorted(set(newer)))))

    def _encodeNumberedSection(self, cy, section, sectionTag, blockEntities):
        """
        Save a section of a chunk last saved by 1.12.2 or earlier by number, as that version would, for the game to
        upgrade it. Blocks that have not changed keep the numbers they had, so nothing about them is lost, and
        those that have get the numbers that the game makes into them again.
        """
        if "Blocks" in sectionTag:
            numbers = sectionTag["Blocks"].value.reshape(SECTION_SHAPE).astype("u4")
            if "Add" in sectionTag:
                numbers |= unpackNibbleArray(sectionTag["Add"].value.reshape(16, 16, 8)).astype("u4") << 8
            metas = unpackNibbleArray(sectionTag["Data"].value.reshape(16, 16, 8))
            oldBlocks, oldData = self._decodeNumberedBlocks(sectionTag)
        else:
            numbers = numpy.zeros(SECTION_SHAPE, "u4")
            metas = numpy.zeros(SECTION_SHAPE, "uint8")
            oldBlocks = oldData = numpy.zeros(SECTION_SHAPE, "uint8")
        changed = (section.Blocks != oldBlocks) | (section.Data != oldData)
        pairs = section.Blocks.astype("u4") * 16 + section.Data

        table = NumberedBlocks.forBlockTypes(self.adapter.blocktypes)
        missing = set()
        entities = {}
        for pair in numpy.unique(pairs[changed]).tolist():
            found = table.key(pair >> 4, pair & 15)
            if found is None:
                missing.add(self.adapter.blocktypes.statesByID.get((pair >> 4, pair & 15), "block %d" % pair))
                continue
            key, fields = found
            where = changed & (pairs == pair)
            numbers[where] = key >> 4
            metas[where] = key & 15
            if fields is not None:
                for y, z, x in zip(*numpy.nonzero(where)):
                    entities[int(x), int(y), int(z)] = (NUMBERED_ENTITY_IDS[key >> 4], fields)
        if missing:
            raise AnvilChunkFormatError(
                "Chunk %s was last saved by Minecraft 1.12.2 or earlier, which has no way to save %s. Load the chunk "
                "in the game first, which upgrades it." % ((self.cx, self.cz), ", ".join(sorted(missing))))

        sectionTag["Blocks"] = nbt.TAG_Byte_Array((numbers & 0xff).astype("uint8").ravel())
        if (numbers > 0xff).any():
            sectionTag["Add"] = nbt.TAG_Byte_Array(packNibbleArray((numbers >> 8).astype("uint8")).ravel())
        elif "Add" in sectionTag:
            del sectionTag["Add"]
        sectionTag["Data"] = nbt.TAG_Byte_Array(packNibbleArray(metas.astype("uint8")).ravel())

        for (x, y, z), (entityID, fields) in sorted(entities.items()):
            position = (self.cx * 16 + x, cy * 16 + y, self.cz * 16 + z)
            tag = next((t for t in blockEntities if "x" in t and "y" in t and "z" in t
                        and (t["x"].value, t["y"].value, t["z"].value) == position), None)
            if tag is not None and ("id" not in tag or tag["id"].value != entityID):
                blockEntities.remove(tag)
                tag = None
            if tag is None:
                tag = nbt.TAG_Compound()
                tag["id"] = nbt.TAG_String(entityID)
                for key, value in zip("xyz", position):
                    tag[key] = nbt.TAG_Int(value)
                blockEntities.append(tag)
            for key, value in fields.items():
                tag[key] = value.copy()

    @staticmethod
    def _forgetWhatTheGameWillWorkOut(tag, sectionTags):
        # With blocks changed, these are no longer right. The game works them out again when it loads a chunk
        # with no isLightOn, or with no heightmaps. MCEdit can't do either as well as the game can.
        for sectionTag in sectionTags.values():
            for key in "BlockLight", "SkyLight":
                if key in sectionTag:
                    del sectionTag[key]
        tag["isLightOn"] = nbt.TAG_Byte(0)
        if "Heightmaps" in tag:
            del tag["Heightmaps"]

    def _newSectionTag(self, cy, sectionTags):
        sectionTag = nbt.TAG_Compound()
        sectionTag["Y"] = nbt.TAG_Byte(cy)

        # Without biomes it would turn into plains, so it takes those of the nearest section.
        withBiomes = [other for other, otherTag in sectionTags.items() if "biomes" in otherTag]
        if withBiomes:
            nearest = min(withBiomes, key=lambda other: abs(other - cy))
            sectionTag["biomes"] = sectionTags[nearest]["biomes"].copy()
        return sectionTag

    def _palette(self, section):
        """ A palette for a section's blocks, and each block's index into it. """
        packed = section.Blocks.astype("u4") * 16 + section.Data
        pairs, indices = numpy.unique(packed.ravel(), return_inverse=True)
        palette = nbt.TAG_List(list_type=nbt.ID_COMPOUND)
        for pair in pairs:
            palette.append(self._paletteEntry(int(pair) >> 4, int(pair) & 15))
        return palette, indices.ravel()

    def _encodeBlocks(self, section):
        palette, indices = self._palette(section)
        states = nbt.TAG_Compound()
        states["palette"] = palette
        if len(palette) > 1:
            bits = max(MIN_BLOCK_BITS, bitsForPalette(len(palette)))
            states["data"] = nbt.TAG_Long_Array(packIndices(indices, bits))
        return states

    def _paletteEntry(self, ID, meta):
        blocktypes = self.adapter.blocktypes
        nameAndState = blocktypes.statesByID.get((ID, meta))
        if nameAndState is None:
            # Code written for older worlds can set a meta that means nothing here.
            nameAndState = blocktypes.statesByID.get((ID, 0))
        if nameAndState is None:
            raise AnvilChunkFormatError("Block ID %d in chunk %s has no name to save it by." % (ID, (self.cx, self.cz)))

        # The keys are those of the version that last saved this chunk, which a later version upgrades from.
        if self.dataVersion >= FIRST_LOWERCASE_PALETTE_DATA_VERSION:
            nameKey, propertiesKey = "id", "properties"
        else:
            nameKey, propertiesKey = "Name", "Properties"
        name, _, state = nameAndState.partition("[")
        entry = nbt.TAG_Compound()
        entry[nameKey] = nbt.TAG_String(name)
        if state:
            properties = nbt.TAG_Compound()
            for pair in state.rstrip("]").split(","):
                key, _, value = pair.partition("=")
                properties[key] = nbt.TAG_String(value)
            entry[propertiesKey] = properties
        return entry

    def _remainingBlockEntities(self, changedSections):
        """ A block entity is left out if the block it was for has been replaced by one that has none. """
        blocktypes = self.adapter.blocktypes
        remaining = nbt.TAG_List(list_type=self._blockEntities.list_type)
        for tag in self._blockEntities:
            cy = tag["y"].value >> 4 if "y" in tag else None
            if cy in changedSections:
                section = self._sections[cy]
                y, z, x = tag["y"].value & 15, tag["z"].value & 15, tag["x"].value & 15
                nameAndState = blocktypes.statesByID.get((int(section.Blocks[y, z, x]), int(section.Data[y, z, x])))
                attributes = blocktypes.blockJsons.get(nameAndState, {})
                if not attributes.get("hasEntity", True):
                    continue
            remaining.append(tag.copy())
        return remaining

    def buildEntityTag(self):
        """
        Return what to save in the entity region file, or None if nothing about it has changed.
        """
        if self.dataVersion < FIRST_ENTITY_FILES_DATA_VERSION:
            return None  # kept in the chunk
        entities = nbt.TAG_List([e.copy() for e in self._entities if id(e) not in self._ungeneratedEntities],
                                list_type=nbt.ID_COMPOUND)
        if self._entitiesUnreadable or self._entityBytes(entities) == self._savedEntities:
            return None

        if self._entityRoot is not None:
            tag = self._entityRoot.copy()
        else:
            tag = nbt.TAG_Compound()
            tag["DataVersion"] = nbt.TAG_Int(self.rootTag["DataVersion"].value)
            tag["Position"] = nbt.TAG_Int_Array(numpy.array([self.cx, self.cz], "i4").astype(">u4"))
        tag["Entities"] = entities
        return tag

    @staticmethod
    def _entityBytes(entities):
        tag = nbt.TAG_Compound()
        tag["Entities"] = entities
        return tag.save(compressed=False)

    def entitiesWereSaved(self, entityTag):
        self._savedEntities = self._entityBytes(entityTag["Entities"])

    # --- Sections ---

    def _isInHeightRange(self, cy):
        return self.minHeight <= cy << 4 < self.maxHeight

    def _hasContent(self, tag):
        if "Blocks" in tag:
            return bool(tag["Blocks"].value.any())
        states = tag.get("block_states")
        if states is not None:
            if any(paletteEntryState(entry)[0] not in AIR_NAMES for entry in states.get("palette", ())):
                return True

        # An empty section is only worth keeping if it is not the same as no section at all.
        blockLight = tag.get("BlockLight")
        if blockLight is not None and blockLight.value.any():
            return True
        skyLight = tag.get("SkyLight")
        return skyLight is not None and (skyLight.value != 0xff).any()

    def sectionPositions(self):
        if self._sectionPositions is None:
            self._sectionPositions = [cy for cy, tag in self._sectionTags.items()
                                      if self._isInHeightRange(cy) and self._hasContent(tag)]
        return self._sectionPositions

    def getSection(self, cy, create=False):
        """
        :param cy: Section number (y coordinate >> 4)
        :param create: If there's nothing there, whether to add a new section there. It can't be outside the world.
        :return: The requested section, or None if the chunk has nothing there.
        :rtype: ModernSection
        """
        if create and self.adapter.readonly:
            raise IOError(READ_ONLY_MESSAGE)
        if not self._isInHeightRange(cy):
            return None

        section = self._sections.get(cy)
        if section is None and (create or cy in self.sectionPositions()):
            try:
                section = self._sections[cy] = self._decodeSection(cy)
            except (KeyError, IndexError, ValueError, TypeError) as e:
                raise AnvilChunkFormatError("Error in section %d of chunk %s: %r"
                                            % (cy, (self.cx, self.cz), e)) from e
            if cy not in self.sectionPositions():
                self._sectionPositions.append(cy)
        return section

    def _decodeSection(self, cy):
        tag = self._sectionTags.get(cy)
        if tag is None:
            return ModernSection(cy, numpy.zeros(SECTION_SHAPE, "uint16"), numpy.zeros(SECTION_SHAPE, "uint8"),
                                 numpy.zeros(SECTION_SHAPE, "uint8"), self._skyLight(cy))

        Blocks, Data = self._decodeBlocks(tag, noteUnresolved=(self.dimName, self.cx, cy, self.cz))
        return ModernSection(cy, Blocks, Data, self._blockLight(tag), self._skyLight(cy))

    def _decodeBlocks(self, tag, noteUnresolved=None):
        if "Blocks" in tag:
            return self._decodeNumberedBlocks(tag)
        Blocks = numpy.zeros(SECTION_SHAPE, "uint16")
        Data = numpy.zeros(SECTION_SHAPE, "uint8")

        states = tag.get("block_states")
        if states is not None and "palette" in states:
            palette = states["palette"]
            blocktypes = self.adapter.blocktypes
            IDs, metas = blocktypes.paletteTables(palette)
            if "data" in states:
                bits = max(MIN_BLOCK_BITS, bitsForPalette(len(palette)))
                indices = unpackIndices(states["data"].value, bits, BLOCKS_PER_SECTION, self._spanning)
                Blocks = IDs[indices].reshape(SECTION_SHAPE)
                Data = metas[indices].reshape(SECTION_SHAPE)
            else:
                indices = None
                Blocks[:] = IDs[0]
                Data[:] = metas[0]

            if noteUnresolved and (blocktypes.stateFlags[IDs.astype("intp") * 16 + metas] & STATE_UNRESOLVED).any():
                if indices is None:
                    counts = [BLOCKS_PER_SECTION]
                else:
                    counts = numpy.bincount(indices, minlength=len(palette))
                blocktypes.noteUnresolvedBlocks(noteUnresolved, IDs, metas, counts)

        return Blocks, Data

    def _decodeNumberedBlocks(self, tag):
        """
        A section of a chunk last saved by 1.12.2 or earlier, which keeps blocks by number. Each is read as the
        block 1.13.2 made it into when upgrading, from FLATTENING_TABLE_FILE. This is only a guide to what the game
        will make of the chunk, as it can look at block entities and neighbors that this does not.
        """
        numbers = tag["Blocks"].value.reshape(SECTION_SHAPE).astype("u4")
        if "Add" in tag:
            numbers |= unpackNibbleArray(tag["Add"].value.reshape(16, 16, 8)).astype("u4") << 8
        data = unpackNibbleArray(tag["Data"].value.reshape(16, 16, 8))
        keys = numbers << 4 | data
        unique, inverse = numpy.unique(keys.ravel(), return_inverse=True)

        table = flatteningTable()
        blocktypes = self.adapter.blocktypes
        IDs = numpy.zeros(len(unique), "uint16")
        metas = numpy.zeros(len(unique), "uint8")
        states = {}
        for index, key in enumerate(unique.tolist()):
            number, meta = key >> 4, key & 15
            state = table.get(key) or table.get(number << 4)
            if number == 0:
                state = "minecraft:air"
            elif state is None:
                state = "mcedit:numbered_block_%d" % number
            states[key] = state
            name, properties = stateForThisVersion(state, blocktypes)
            IDs[index], metas[index] = blocktypes.idAndMeta(name, properties)
        Blocks = IDs[inverse].reshape(SECTION_SHAPE)
        Data = metas[inverse].reshape(SECTION_SHAPE)

        # Some blocks kept part of what they are in their block entity, which the table could not know about.
        cy = int(tag["Y"].value)
        for entity in self._blockEntities:
            try:
                x, y, z = entity["x"].value & 15, entity["y"].value, entity["z"].value & 15
            except KeyError:
                continue
            if y >> 4 != cy:
                continue
            y &= 15
            key = int(keys[y, z, x])
            state = blockEntityState(key >> 4, key & 15, states[key], entity)
            if state is not None:
                name, properties = stateForThisVersion(state, blocktypes)
                Blocks[y, z, x], Data[y, z, x] = blocktypes.idAndMeta(name, properties)
        return Blocks, Data

    @staticmethod
    def _blockLight(tag):
        light = tag.get("BlockLight")
        if light is None:
            return numpy.zeros(SECTION_SHAPE, "uint8")
        return unpackNibbleArray(light.value.reshape(16, 16, 8))

    def _skyLight(self, cy):
        """
        The game does not save a section's sky light when it can be told from the section above: every
        layer is then the same as the lowest layer of that section. Above the top section, the sky is
        fully lit. Dimensions with no sky save none at all, so they come out fully lit and easy to see.
        """
        light = self._skyLights.get(cy)
        if light is None:
            tag = self._sectionTags.get(cy)
            if tag is not None and "SkyLight" in tag:
                light = unpackNibbleArray(tag["SkyLight"].value.reshape(16, 16, 8))
            elif tag is not None:
                light = numpy.tile(self._skyLight(cy + 1)[0], (16, 1, 1))
            else:
                light = numpy.full(SECTION_SHAPE, FULL_LIGHT, "uint8")
            self._skyLights[cy] = light
        return light

    @property
    def bounds(self):
        return BoundingBox((self.cx << 4, self.minHeight, self.cz << 4), (16, self.maxHeight - self.minHeight, 16))

    @property
    def blocktypes(self):
        return self.adapter.blocktypes

    # --- Entities ---

    @property
    def Entities(self):
        return self._entities

    @property
    def TileEntities(self):
        return self._blockEntities

    @property
    def TileTicks(self):
        return self._blockTicks

    # --- Whole-chunk values ---

    @property
    def HeightMap(self):
        """ For each column, indexed ZX, one more than the Y of its highest block. """
        self._blocksHaveChanged |= self.dirty
        if self._blocksHaveChanged:
            return self._heightsFromBlocks()

        if self._heightMap is None:
            heightMaps = self._chunkTag.get("Heightmaps")
            if heightMaps is not None and SURFACE_HEIGHTMAP in heightMaps:
                # The game saves heights counted from the bottom of the world.
                bits = (self.maxHeight - self.minHeight).bit_length()
                heights = unpackIndices(heightMaps[SURFACE_HEIGHTMAP].value, bits, 256, self._spanning).astype("int32")
                self._heightMap = heights.reshape(16, 16) + self.minHeight
            else:
                self._heightMap = self._heightsFromBlocks()
        return self._heightMap

    def _heightsFromBlocks(self):
        heights = numpy.full((16, 16), self.minHeight, "int32")
        unfound = numpy.ones((16, 16), "bool")
        for cy in sorted(self.sectionPositions(), reverse=True):
            filled = self.getSection(cy).Blocks != 0
            columns = filled.any(axis=0) & unfound
            if columns.any():
                # The index of the first filled block when looking down from the top of the section
                top = 15 - numpy.argmax(filled[::-1], axis=0)
                heights[columns] = (cy << 4) + top[columns] + 1
                unfound &= ~columns
                if not unfound.any():
                    break
        return heights

    def _biomeTableAt(self, cy):
        """
        Return the IDs for the biome cells of a section as an array indexed YZX, or None if it has none.
        """
        if cy not in self._biomeIndices:
            cells = self.adapter.profile.biomeCellsPerSide
            table = None
            tag = self._sectionTags.get(cy)
            biomes = tag.get("biomes") if tag is not None else None
            if biomes is not None and "palette" in biomes:
                palette = biomes["palette"]
                IDs = numpy.array([self.adapter.biomeID(entry.value) for entry in palette], "uint8")
                if "data" in biomes:
                    indices = unpackIndices(biomes["data"].value, bitsForPalette(len(palette)), cells ** 3)
                    table = IDs[indices].reshape(cells, cells, cells)
                else:
                    table = numpy.full((cells, cells, cells), IDs[0], "uint8")
            self._biomeIndices[cy] = table
        return self._biomeIndices[cy]

    @property
    def Biomes(self):
        """
        One biome per column, indexed ZX, for the parts of MCEdit that color blocks with a flat map. It
        is the biome at the column's surface. The chunk's own biomes vary with height, and are not altered.
        """
        if self._biomes is None and self.isLegacyLayout:
            self._biomes = self._legacyBiomes()
        if self._biomes is None:
            cells = self.adapter.profile.biomeCellsPerSide
            blocksPerCell = 16 // cells
            heights = self.HeightMap
            biomes = numpy.full((16, 16), UNKNOWN_BIOME, "uint8")
            for cellZ in range(cells):
                for cellX in range(cells):
                    z = cellZ * blocksPerCell
                    x = cellX * blocksPerCell
                    y = int(heights[z, x]) - 1
                    y = max(self.minHeight, min(y, self.maxHeight - 1))
                    table = self._biomeTableAt(y >> 4)
                    if table is not None:
                        biomes[z:z + blocksPerCell, x:x + blocksPerCell] = table[(y & 15) // blocksPerCell, cellZ, cellX]
            self._biomes = biomes
        return self._biomes

    def _legacyBiomes(self):
        """
        Up to 1.17 a chunk saves biomes by number, as one per column up to 1.14, and after that one for each
        4x4x4 cell from the bottom of the world up.
        """
        biomes = numpy.full((16, 16), UNKNOWN_BIOME, "uint8")
        tag = self._chunkTag.get("Biomes")
        if tag is None or not len(tag.value):
            return biomes
        numbers = numpy.asarray(tag.value).astype("int64")
        if tag.tagID == nbt.ID_BYTE_ARRAY:
            numbers &= 0xff  # 1.12.2 and earlier save one unsigned byte for each column
        if len(numbers) == 256:
            columns = numbers.reshape(16, 16)
        else:
            layers = numbers.reshape(-1, 4, 4)
            heights = self.HeightMap
            columns = numpy.zeros((16, 16), "int64")
            for cellZ in range(4):
                for cellX in range(4):
                    y = int(heights[cellZ * 4, cellX * 4]) - 1
                    layer = max(0, min((y - self.minHeight) >> 2, len(layers) - 1))
                    columns[cellZ * 4:cellZ * 4 + 4, cellX * 4:cellX * 4 + 4] = layers[layer, cellZ, cellX]

        for number in numpy.unique(columns):
            name = LEGACY_BIOME_NAMES.get(int(number), "unknown:%d" % number)
            biomes[columns == number] = self.adapter.biomeID(name)
        return biomes

    @property
    def TerrainPopulated(self):
        if self.isLegacyLayout:
            status = self._chunkTag.get("Status")
            return status is not None and status.value in LEGACY_FINISHED_STATUSES
        status = self.rootTag.get(self.adapter.profile.statusKey)
        return status is not None and status.value == STATUS_FULL

    @TerrainPopulated.setter
    def TerrainPopulated(self, val):
        raise IOError(READ_ONLY_MESSAGE)


# Since 26.1 the world generation settings are in a file of their own instead of level.dat.
WORLD_GEN_SETTINGS_FILE = "data/minecraft/world_gen_settings.dat"


DIFFICULTIES = ("peaceful", "easy", "normal", "hard")


class ModernWorldMetadata(AnvilWorldMetadata):
    """
    Since 26.1 some of what level.dat held is kept differently: the spawn point in "spawn", and the difficulty in
    "difficulty_settings", by name. These attributes read either.
    """
    # The world generation settings, from level.dat or, since 26.1, WORLD_GEN_SETTINGS_FILE.
    worldGenSettings = None

    def _value(self, key, default):
        tag = self.rootTag.get(key)
        return tag.value if tag is not None else default

    @property
    def Spawn(self):
        spawn = self.rootTag.get("spawn")
        if spawn is not None and "pos" in spawn:
            return Vector(*(int(v) for v in spawn["pos"].value))
        return Vector(*(self._value(key, 0) for key in ("SpawnX", "SpawnY", "SpawnZ")))

    @Spawn.setter
    def Spawn(self, pos):
        spawn = self.rootTag.get("spawn")
        if spawn is not None and "pos" in spawn:
            spawn["pos"] = nbt.TAG_Int_Array(numpy.array([int(v) for v in pos], ">i4"))
        else:
            for key, value in zip(("SpawnX", "SpawnY", "SpawnZ"), pos):
                self.rootTag[key] = nbt.TAG_Int(int(value))
        self.dirty = True

    def _difficultySetting(self, key, oldKey, default):
        settings = self.rootTag.get("difficulty_settings")
        if settings is not None and key in settings:
            return settings[key].value
        return self._value(oldKey, default)

    @property
    def Difficulty(self):
        difficulty = self._difficultySetting("difficulty", "Difficulty", 0)
        if isinstance(difficulty, str):
            return DIFFICULTIES.index(difficulty) if difficulty in DIFFICULTIES else 0
        return difficulty

    def _setDifficultySetting(self, key, oldKey, tag, newTag):
        settings = self.rootTag.get("difficulty_settings")
        if settings is not None:
            settings[key] = newTag
        else:
            self.rootTag[oldKey] = tag
        self.dirty = True

    @Difficulty.setter
    def Difficulty(self, value):
        self._setDifficultySetting("difficulty", "Difficulty", nbt.TAG_Byte(int(value)),
                                   nbt.TAG_String(DIFFICULTIES[int(value)]))

    @property
    def DifficultyLocked(self):
        return bool(self._difficultySetting("locked", "DifficultyLocked", False))

    @DifficultyLocked.setter
    def DifficultyLocked(self, value):
        self._setDifficultySetting("locked", "DifficultyLocked", nbt.TAG_Byte(bool(value)), nbt.TAG_Byte(bool(value)))

    @property
    def hardcore(self):
        return bool(self._difficultySetting("hardcore", "hardcore", False))

    @hardcore.setter
    def hardcore(self, value):
        self._setDifficultySetting("hardcore", "hardcore", nbt.TAG_Byte(bool(value)), nbt.TAG_Byte(bool(value)))

    @property
    def generatorName(self):
        if "generatorName" in self.rootTag:
            return self.rootTag["generatorName"].value
        # Since 1.16 each dimension has its own generator. The overworld's type is the nearest thing.
        try:
            generator = self.worldGenSettings["dimensions"]["minecraft:overworld"]["generator"]
            return generator["type"].value.partition(":")[2] or generator["type"].value
        except (KeyError, TypeError):
            return "default"

    @generatorName.setter
    def generatorName(self, value):
        # Only worlds from before 1.16 keep this. Later ones describe each dimension's generator, which this
        # does not change.
        if "generatorName" in self.rootTag:
            self.rootTag["generatorName"] = nbt.TAG_String(value)
            self.dirty = True

    @property
    def generatorOptions(self):
        return self._value("generatorOptions", "")

    @generatorOptions.setter
    def generatorOptions(self, value):
        if "generatorOptions" in self.rootTag:
            self.rootTag["generatorOptions"] = nbt.TAG_String(value)
            self.dirty = True

    @property
    def DayTime(self):
        # Since 26.1 the time of day is kept by the world's clocks, in data/. The age of the world is near enough.
        return self._value("DayTime", self._value("Time", 0))

    @property
    def RandomSeed(self):
        settings = self.worldGenSettings
        if settings is not None and "seed" in settings:
            return settings["seed"].value
        # Before 1.16 the seed was kept with everything else.
        seed = self.rootTag.get("RandomSeed")
        return seed.value if seed is not None else 0


class ModernPlayerRef(AnvilPlayerRef):
    Health = nbtattr.NBTAttr('Health', nbt.TAG_Float, 20.0)

    @property
    def dimName(self):
        return self.adapter.profile.dimensionFolder(self.rootTag["Dimension"].value)

    @dimName.setter
    def dimName(self, name):
        self.rootTag["Dimension"].value = self.adapter.profile.dimensionKey(name)
        self.dirty = True


def worldDataVersion(filename):
    """
    Return the data version of the game that last saved the given world, or None if it can't be told.

    :param filename: A world folder, or a level.dat or level.dat_old file in one.
    """
    if os.path.isdir(filename):
        folder = filename
    else:
        folder = os.path.dirname(filename)

    for name in "level.dat", "level.dat_old":
        try:
            return nbt.load(os.path.join(folder, name))["Data"]["DataVersion"].value
        except (KeyError, EnvironmentError, EOFError, zlib.error, NBTFormatError):
            continue
    return None


def _whenWritable(name):
    """ Make a method that does what the base class does unless this world is read-only. """
    method = getattr(AnvilWorldAdapter, name)

    def checked(self, *args, **kwargs):
        if self.readonly:
            raise IOError(READ_ONLY_MESSAGE)
        return method(self, *args, **kwargs)

    checked.__name__ = name
    checked.__doc__ = method.__doc__
    return checked


class ModernAnvilAdapter(AnvilWorldAdapter):
    """
    Opens worlds from the versions in format_profile.PROFILES. When read-only, nothing in the world folder is
    created or changed. Otherwise changes go in a folder beside the world until they are saved, as for any
    other adapter, and the world is locked against the game for as long as it is open.
    """

    usesNumericBlockIDs = False

    # MCEdit's lighting code assumes older worlds. The game works out the lighting of a chunk that says it needs it.
    hasLights = False

    # Dimensions have their own heights, given by heightRange. These are for code with no dimension.
    minHeight = -64
    maxHeight = 320

    def __init__(self, filename=None, create=False, readonly=False, resume=None):
        if create:
            raise IOError("MCEdit cannot create worlds in this format.")

        if os.path.basename(filename) in ("level.dat", "level.dat_old"):
            filename = os.path.dirname(filename)
        if not os.path.isdir(filename):
            raise IOError("File is not a Minecraft world")

        dataVersion = worldDataVersion(filename)
        self.profile = profileForDataVersion(dataVersion) if dataVersion is not None else None
        if self.profile is None:
            raise LevelFormatError("This world was saved by a version of Minecraft (data version %s) that MCEdit "
                                   "cannot open. It can open worlds from the releases of Minecraft from 1.7.10 to "
                                   "26.3, but not from snapshots." % dataVersion)
        if not self.profile.hasBlockPack:
            raise LevelFormatError("MCEdit does not have the list of blocks for Minecraft %s yet, so it cannot "
                                   "open this world." % self.profile.name)
        # Saving is allowed only in formats that have been checked in the game.
        readonly = readonly or not self.profile.writable
        if self.profile.hasDimensionsFolder:
            self.playerDataFolder = "players/data"

        self.lockTime = 0
        self.EntityRef = PCEntityRef
        self.TileEntityRef = PCTileEntityRef
        self.filename = filename
        self.readonly = readonly
        self.sessionLock = None
        if readonly:
            self.revisionHistory = AnvilWorldFolder(filename, readonly=True)
            self.selectedRevision = self.revisionHistory
        else:
            # Before anything else is written, as it's how to find out that the game has the world open.
            self.sessionLock = SessionLock(filename)
            if self._usesTimestampLock:
                self.lockTime = self.sessionLock.writeTimestamp()
            try:
                self.revisionHistory = RevisionHistory(filename, resume, externalChunks=True)
            except Exception:
                # Whoever catches this may well try to open the world again.
                self.sessionLock.release()
                raise
            self.selectedRevision = self.revisionHistory.getHead()
        self._heightRanges = {}
        self._biomeIDs = {}
        self.biomeNames = {}

        self.loadMetadata()
        self.loadBlockMapping()

    def __repr__(self):
        return "ModernAnvilAdapter(%r)" % self.filename

    @classmethod
    def canOpenFile(cls, filename):
        if not super(ModernAnvilAdapter, cls).canOpenFile(filename):
            return False
        # Every world from 1.13 on is this adapter's to open, or to refuse with the reason why.
        dataVersion = worldDataVersion(filename)
        return dataVersion is not None and dataVersion >= FIRST_PALETTE_DATA_VERSION

    def loadMetadata(self):
        for name in "level.dat", "level.dat_old":
            try:
                self.metadata = ModernWorldMetadata(nbt.load(buf=self.selectedRevision.readFile(name)))
                self.metadata.worldGenSettings = self._loadWorldGenSettings()
                return
            except (EnvironmentError, EOFError, zlib.error, NBTFormatError) as e:
                log.info("Could not read %s: %r", name, e)
        raise LevelFormatError("Neither level.dat nor level.dat_old could be read.")

    def _loadWorldGenSettings(self):
        settings = self.metadata.rootTag.get("WorldGenSettings")
        if settings is not None:
            return settings
        try:
            return nbt.load(buf=self.selectedRevision.readFile(WORLD_GEN_SETTINGS_FILE))["data"]
        except (KeyError, EnvironmentError, EOFError, zlib.error, NBTFormatError):
            return None

    def loadBlockMapping(self):
        self.packSelection = selectPacks(worldMods(self.metadata.metadataTag), self.profile)
        for problem in self.packSelection.problems:
            log.warning("Block pack: %s", problem)
        self.blocktypes = ModernBlockTypeSet(self.profile, self.packSelection.packs)

    # --- Heights ---

    def heightRange(self, dimName):
        """
        Return the Y of the lowest block in the given dimension, and one above the Y of its highest.

        :rtype: (int, int)
        """
        heights = self._heightRanges.get(dimName)
        if heights is None:
            heights = self._heightRanges[dimName] = self._findHeightRange(dimName)
        return heights

    def _findHeightRange(self, dimName):
        settings = self.metadata.worldGenSettings
        try:
            typeTag = settings["dimensions"][self.profile.dimensionKey(dimName)]["type"]
            if typeTag.tagID == nbt.ID_STRING:
                heights = self.profile.dimensionTypeHeights(typeTag.value)
                if heights is not None:
                    return heights
            elif "min_y" in typeTag and "height" in typeTag:
                # A dimension type a data pack wrote out in full, as it could from 1.16.2.
                return typeTag["min_y"].value, typeTag["min_y"].value + typeTag["height"].value
        except (KeyError, TypeError):
            log.info("No dimension type found in the world generation settings for dimension %r", dimName)

        # Added by a mod or data pack, so look at what a chunk there has.
        for cx, cz in sorted(self.chunkPositions(dimName)):
            try:
                tag = nbt.load(buf=self.selectedRevision.readChunkBytes(cx, cz, dimName))
                if "Level" in tag:
                    break  # from 1.17 or earlier, when the world was the same height everywhere
                top = max(tag["Y"].value for tag in tag["sections"] if "block_states" in tag)
                return tag["yPos"].value << 4, (int(top) + 1) << 4
            except (KeyError, ValueError, EnvironmentError, LevelFormatError) as e:
                log.info("Could not find the height of dimension %r from chunk %s: %r", dimName, (cx, cz), e)
                break
        return self.profile.defaultHeights

    # --- Chunks ---

    def readChunk(self, cx, cz, dimName):
        try:
            data = self.selectedRevision.readChunkBytes(cx, cz, dimName)
            entityRoot, entitiesUnreadable = self._readEntities(cx, cz, dimName)
            return ModernChunkData(self, cx, cz, dimName, nbt.load(buf=data), entityRoot, entitiesUnreadable)
        except ChunkNotPresent:
            raise
        except (KeyError, IndexError, ValueError, TypeError, zlib.error, NBTFormatError) as e:
            raise AnvilChunkFormatError("Error loading chunk %s: %r" % ((cx, cz), e)) from e

    def _readEntities(self, cx, cz, dimName):
        # Failing to read a chunk's entities should not cost the chance to look at its blocks.
        store = dimName + ENTITY_STORE
        try:
            if not self.selectedRevision.containsChunk(cx, cz, store):
                return None, False
            root = nbt.load(buf=self.selectedRevision.readChunkBytes(cx, cz, store))
            root["Entities"]  # noqa: to find out now if it isn't there
            return root, False
        except (KeyError, EnvironmentError, LevelFormatError, zlib.error, NBTFormatError) as e:
            log.warning("Could not read the entities of chunk %s: %r", (cx, cz), e)
            return None, True

    def writeChunk(self, chunk):
        if self.readonly:
            raise IOError(READ_ONLY_MESSAGE)
        revision = self.selectedRevision
        revision.writeChunkBytes(chunk.cx, chunk.cz, chunk.dimName, chunk.buildNBTTag().save(compressed=False))
        entityTag = chunk.buildEntityTag()
        if entityTag is not None:
            revision.writeChunkBytes(chunk.cx, chunk.cz, chunk.dimName + ENTITY_STORE, entityTag.save(compressed=False))
            chunk.entitiesWereSaved(entityTag)

    def createChunk(self, cx, cz, dimName):
        if self.readonly:
            raise IOError(READ_ONLY_MESSAGE)
        if self.selectedRevision.containsChunk(cx, cz, dimName):
            raise ValueError("Chunk %s already exists in dim %r" % ((cx, cz), dimName))

        dataVersion = self.metadata.rootTag["DataVersion"].value
        if dataVersion < FIRST_ROOT_CHUNK_DATA_VERSION:
            tag = self._newLegacyChunkTag(cx, cz, dataVersion)
            self.selectedRevision.writeChunkBytes(cx, cz, dimName, tag.save(compressed=False))
            return ModernChunkData(self, cx, cz, dimName, tag)

        # The game fills in anything else that is missing from a chunk like this when it loads it.
        tag = nbt.TAG_Compound()
        tag["DataVersion"] = nbt.TAG_Int(dataVersion)
        tag["xPos"] = nbt.TAG_Int(cx)
        tag["zPos"] = nbt.TAG_Int(cz)
        tag["yPos"] = nbt.TAG_Int(self.heightRange(dimName)[0] >> 4)
        tag[self.profile.statusKey] = nbt.TAG_String(STATUS_FULL)
        tag["sections"] = nbt.TAG_List(list_type=nbt.ID_COMPOUND)
        tag["block_entities"] = nbt.TAG_List(list_type=nbt.ID_COMPOUND)
        tag["LastUpdate"] = nbt.TAG_Long(0)
        tag["InhabitedTime"] = nbt.TAG_Long(0)
        tag["isLightOn"] = nbt.TAG_Byte(0)
        self.selectedRevision.writeChunkBytes(cx, cz, dimName, tag.save(compressed=False))
        return ModernChunkData(self, cx, cz, dimName, tag)

    @staticmethod
    def _newLegacyChunkTag(cx, cz, dataVersion):
        """ An empty, finished chunk as 1.13 to 1.17 saved it, with plains for its biome. """
        level = nbt.TAG_Compound()
        level["xPos"] = nbt.TAG_Int(cx)
        level["zPos"] = nbt.TAG_Int(cz)
        level["Status"] = nbt.TAG_String("postprocessed" if dataVersion < FIRST_LIGHT_ENGINE_DATA_VERSION else "full")
        level["LastUpdate"] = nbt.TAG_Long(0)
        level["InhabitedTime"] = nbt.TAG_Long(0)
        level["Sections"] = nbt.TAG_List(list_type=nbt.ID_COMPOUND)
        level["TileEntities"] = nbt.TAG_List(list_type=nbt.ID_COMPOUND)
        level["Entities"] = nbt.TAG_List(list_type=nbt.ID_COMPOUND)
        biomeCount = 256 if dataVersion < FIRST_3D_BIOMES_DATA_VERSION else 1024
        level["Biomes"] = nbt.TAG_Int_Array(numpy.full(biomeCount, PLAINS_NUMBER, ">u4"))
        if dataVersion >= FIRST_LIGHT_ENGINE_DATA_VERSION:
            level["isLightOn"] = nbt.TAG_Byte(0)
        tag = nbt.TAG_Compound()
        tag["DataVersion"] = nbt.TAG_Int(dataVersion)
        tag["Level"] = level
        return tag

    def deleteChunk(self, cx, cz, dimName):
        if self.readonly:
            raise IOError(READ_ONLY_MESSAGE)
        for store in dimName, dimName + ENTITY_STORE, dimName + POI_STORE:
            if self.selectedRevision.containsChunk(cx, cz, store):
                self.selectedRevision.deleteChunk(cx, cz, store)

    def listDimensions(self):
        return (dimName for dimName in self.selectedRevision.listDimensions() if not isStoreName(dimName))

    # --- Undo revisions ---

    @staticmethod
    def _changesByDimension(changes):
        """ What's changed in a chunk's other stores has changed the chunk, as far as anything else knows. """
        if changes is None:
            return None
        result = RevisionChanges()
        result.files = changes.files
        for dimName, chunks in changes.chunks.items():
            result.chunks[splitStore(dimName)[0]].update(chunks)
        return result

    def selectRevision(self, index):
        return self._changesByDimension(super(ModernAnvilAdapter, self).selectRevision(index))

    def getRevisionChanges(self, oldRevision, newRevision):
        return self._changesByDimension(super(ModernAnvilAdapter, self).getRevisionChanges(oldRevision, newRevision))

    # --- Session lock ---

    @property
    def _usesTimestampLock(self):
        return self.profile.firstDataVersion < FIRST_FILE_LOCK_DATA_VERSION

    def acquireSessionLock(self):
        if self.readonly:
            raise IOError(READ_ONLY_MESSAGE)
        if self.sessionLock is None or not self.sessionLock.held:
            self.sessionLock = SessionLock(self.filename)
        if self._usesTimestampLock:
            self.lockTime = self.sessionLock.writeTimestamp()

    def checkSessionLock(self):
        if self.readonly or self.sessionLock is None or not self.sessionLock.held:
            raise SessionLockLost("The lock on this world is not held.")
        if self._usesTimestampLock and self.sessionLock.readTimestamp() != self.lockTime:
            raise SessionLockLost("Minecraft has opened this world since MCEdit did.")

    def stealSessionLock(self):
        self.acquireSessionLock()

    def close(self):
        super(ModernAnvilAdapter, self).close()
        if self.sessionLock is not None:
            self.sessionLock.release()

    # --- Biomes ---

    def biomeID(self, name):
        """
        Return a number for the given biome that can be stored in a chunk's flat biome map. These are
        given out in the order the biomes are seen, and are good only until the world is closed.
        """
        biomeID = self._biomeIDs.get(name)
        if biomeID is None:
            biomeID = min(len(self._biomeIDs), UNKNOWN_BIOME)
            self._biomeIDs[name] = biomeID
            self.biomeNames[biomeID] = name
        return biomeID

    # --- Players ---

    def getPlayer(self, playerUUID=""):
        return ModernPlayerRef(self, playerUUID)

    # --- What read-only stops ---

    savePlayerTag = _whenWritable("savePlayerTag")
    saveMapTag = _whenWritable("saveMapTag")
    deleteMap = _whenWritable("deleteMap")
    createRevision = _whenWritable("createRevision")
    closeRevision = _whenWritable("closeRevision")
    setRevisionInfo = _whenWritable("setRevisionInfo")
    saveChanges = _whenWritable("saveChanges")
    saveChangesIter = _whenWritable("saveChangesIter")

    def createPlayer(self, playerUUID=""):
        raise IOError("MCEdit cannot create players in this format.")

    def createMap(self):
        raise IOError("MCEdit cannot create maps in this format.")

    def syncToDisk(self):
        if not self.readonly:
            super(ModernAnvilAdapter, self).syncToDisk()

    def requireRevisions(self):
        if not self.readonly:
            super(ModernAnvilAdapter, self).requireRevisions()
