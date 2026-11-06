"""
    modern

    Reads and writes worlds saved by Minecraft 1.18 and later. Chunks are converted to the form the rest of
    MCEdit works with as they are read, and back when they are written, with anything MCEdit doesn't
    know about left as it was. See FormatProfile for which versions can be opened.
"""
import logging
import os
import zlib

import numpy

from mceditlib import nbt, nbtattr
from mceditlib.anvil.adapter import AnvilChunkFormatError, AnvilPlayerRef, AnvilWorldAdapter, AnvilWorldMetadata, \
    SessionLockLost, unpackNibbleArray
from mceditlib.anvil.entities import PCEntityRef, PCTileEntityRef
from mceditlib.anvil.format_profile import FIRST_PALETTE_CHUNK_DATA_VERSION, VANILLA_DIMENSION_TYPE_HEIGHTS, \
    profileForDataVersion
from mceditlib.anvil.session_lock import SessionLock
from mceditlib.anvil.worldfolder import AnvilWorldFolder, ENTITY_STORE, POI_STORE, isStoreName, splitStore
from mceditlib.blocktypes import STATE_UNRESOLVED
from mceditlib.blocktypes.modern import AIR_NAMES, ModernBlockTypeSet
from mceditlib.blocktypes.pack_library import selectPacks, worldMods
from mceditlib.exceptions import ChunkNotPresent, LevelFormatError
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
READ_ONLY_MESSAGE = "This world is opened read only."


def unpackIndices(longs, bitsPerEntry, count):
    """
    Unpack an array of longs holding `count` unsigned integers of `bitsPerEntry` bits each. Entries do
    not cross from one long to the next, and each long is filled from its lowest bits up.

    :type longs: numpy.ndarray
    :type bitsPerEntry: int
    :type count: int
    :rtype: numpy.ndarray
    """
    entriesPerLong = 64 // bitsPerEntry
    longsNeeded = (count + entriesPerLong - 1) // entriesPerLong
    if len(longs) < longsNeeded:
        raise AnvilChunkFormatError("Expected %d longs for %d entries of %d bits, found %d"
                                    % (longsNeeded, count, bitsPerEntry, len(longs)))

    values = numpy.asarray(longs[:longsNeeded]).astype("u8")
    shifts = numpy.arange(entriesPerLong, dtype="u8") * numpy.uint64(bitsPerEntry)
    mask = numpy.uint64((1 << bitsPerEntry) - 1)
    return ((values[:, numpy.newaxis] >> shifts) & mask).ravel()[:count].astype("intp")


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


def bitsForPalette(size):
    """ Return the bits needed to tell apart `size` palette entries. """
    return (size - 1).bit_length()


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
        if "Level" in rootTag or dataVersionTag is None or dataVersionTag.value < FIRST_PALETTE_CHUNK_DATA_VERSION:
            raise AnvilChunkFormatError("Chunk %s was last saved by a version of Minecraft older than 1.18. "
                                        "It will be upgraded when the game next loads it." % ((cx, cz),))

        self._sectionTags = {int(tag["Y"].value): tag for tag in rootTag.get("sections", ())}
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

        # A chunk the game has not finished generating keeps entities in the chunk instead.
        self._ungeneratedEntities = {id(entity) for entity in rootTag.get("entities", ())}
        for entity in rootTag.get("entities", ()):
            self._entities.append(entity)

        self._blocksHaveChanged = False
        self._blockEntities = rootTag["block_entities"] if "block_entities" in rootTag else nbt.TAG_List(list_type=nbt.ID_COMPOUND)
        self._blockTicks = rootTag["block_ticks"] if "block_ticks" in rootTag else nbt.TAG_List(list_type=nbt.ID_COMPOUND)

    # --- Saving ---

    def buildNBTTag(self):
        """
        Return the chunk's tag as it is to be saved. Sections with no changes stay exactly as they were
        saved, including their palettes, and so does anything else in the chunk that isn't known about.
        """
        tag = self.rootTag.copy()
        sectionTags = {int(section["Y"].value): section for section in tag.get("sections", ())}

        changed = []
        for cy, section in self._sections.items():
            oldTag = self._sectionTags.get(cy)
            if oldTag is not None:
                oldBlocks, oldData = self._decodeBlocks(oldTag)
            else:
                oldBlocks = oldData = numpy.zeros(SECTION_SHAPE, "uint8")
            if (section.Blocks != oldBlocks).any() or (section.Data != oldData).any():
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

    def _encodeBlocks(self, section):
        blocktypes = self.adapter.blocktypes
        packed = section.Blocks.astype("u4") * 16 + section.Data
        pairs, indices = numpy.unique(packed.ravel(), return_inverse=True)

        palette = nbt.TAG_List(list_type=nbt.ID_COMPOUND)
        for pair in pairs:
            palette.append(self._paletteEntry(int(pair) >> 4, int(pair) & 15))

        states = nbt.TAG_Compound()
        states["palette"] = palette
        if len(palette) > 1:
            bits = max(MIN_BLOCK_BITS, bitsForPalette(len(palette)))
            states["data"] = nbt.TAG_Long_Array(packIndices(indices.ravel(), bits))
        return states

    def _paletteEntry(self, ID, meta):
        blocktypes = self.adapter.blocktypes
        nameAndState = blocktypes.statesByID.get((ID, meta))
        if nameAndState is None:
            # Code written for older worlds can set a meta that means nothing here.
            nameAndState = blocktypes.statesByID.get((ID, 0))
        if nameAndState is None:
            raise AnvilChunkFormatError("Block ID %d in chunk %s has no name to save it by." % (ID, (self.cx, self.cz)))

        profile = self.adapter.profile
        name, _, state = nameAndState.partition("[")
        entry = nbt.TAG_Compound()
        entry[profile.paletteNameKey] = nbt.TAG_String(name)
        if state:
            properties = nbt.TAG_Compound()
            for pair in state.rstrip("]").split(","):
                key, _, value = pair.partition("=")
                properties[key] = nbt.TAG_String(value)
            entry[profile.palettePropertiesKey] = properties
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
        states = tag.get("block_states")
        if states is not None:
            nameKey = self.adapter.profile.paletteNameKey
            if any(entry[nameKey].value not in AIR_NAMES for entry in states.get("palette", ())):
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
        Blocks = numpy.zeros(SECTION_SHAPE, "uint16")
        Data = numpy.zeros(SECTION_SHAPE, "uint8")

        states = tag.get("block_states")
        if states is not None and "palette" in states:
            palette = states["palette"]
            blocktypes = self.adapter.blocktypes
            IDs, metas = blocktypes.paletteTables(palette)
            if "data" in states:
                bits = max(MIN_BLOCK_BITS, bitsForPalette(len(palette)))
                indices = unpackIndices(states["data"].value, bits, BLOCKS_PER_SECTION)
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
            heightMaps = self.rootTag.get("Heightmaps")
            if heightMaps is not None and SURFACE_HEIGHTMAP in heightMaps:
                # The game saves heights counted from the bottom of the world.
                bits = (self.maxHeight - self.minHeight).bit_length()
                heights = unpackIndices(heightMaps[SURFACE_HEIGHTMAP].value, bits, 256).astype("int32")
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

    @property
    def TerrainPopulated(self):
        status = self.rootTag.get(self.adapter.profile.statusKey)
        return status is not None and status.value == STATUS_FULL

    @TerrainPopulated.setter
    def TerrainPopulated(self, val):
        raise IOError(READ_ONLY_MESSAGE)


class ModernWorldMetadata(AnvilWorldMetadata):
    @property
    def RandomSeed(self):
        settings = self.rootTag.get("WorldGenSettings")
        return settings["seed"].value if settings is not None and "seed" in settings else 0


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

        self.profile = profileForDataVersion(worldDataVersion(filename))
        if self.profile is None:
            raise LevelFormatError("MCEdit cannot read this world's format.")

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
        dataVersion = worldDataVersion(filename)
        return dataVersion is not None and profileForDataVersion(dataVersion) is not None

    def loadMetadata(self):
        for name in "level.dat", "level.dat_old":
            try:
                self.metadata = ModernWorldMetadata(nbt.load(buf=self.selectedRevision.readFile(name)))
                return
            except (EnvironmentError, EOFError, zlib.error, NBTFormatError) as e:
                log.info("Could not read %s: %r", name, e)
        raise LevelFormatError("Neither level.dat nor level.dat_old could be read.")

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
        settings = self.metadata.rootTag.get("WorldGenSettings")
        try:
            typeTag = settings["dimensions"][self.profile.dimensionKey(dimName)]["type"]
            heights = VANILLA_DIMENSION_TYPE_HEIGHTS.get(typeTag.value)
            if heights is not None:
                return heights
        except (KeyError, TypeError):
            log.info("No dimension type found in level.dat for dimension %r", dimName)

        # Added by a mod or data pack, so look at what a chunk there has.
        for cx, cz in sorted(self.chunkPositions(dimName)):
            try:
                tag = nbt.load(buf=self.selectedRevision.readChunkBytes(cx, cz, dimName))
                top = max(tag["Y"].value for tag in tag["sections"] if "block_states" in tag)
                return tag["yPos"].value << 4, (int(top) + 1) << 4
            except (KeyError, ValueError, EnvironmentError, LevelFormatError) as e:
                log.info("Could not find the height of dimension %r from chunk %s: %r", dimName, (cx, cz), e)
                break
        return self.minHeight, self.maxHeight

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

        # The game fills in anything else that is missing from a chunk like this when it loads it.
        tag = nbt.TAG_Compound()
        tag["DataVersion"] = nbt.TAG_Int(self.metadata.rootTag["DataVersion"].value)
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

    def acquireSessionLock(self):
        if self.readonly:
            raise IOError(READ_ONLY_MESSAGE)
        if self.sessionLock is None or not self.sessionLock.held:
            self.sessionLock = SessionLock(self.filename)

    def checkSessionLock(self):
        if self.readonly or self.sessionLock is None or not self.sessionLock.held:
            raise SessionLockLost("The lock on this world is not held.")

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
