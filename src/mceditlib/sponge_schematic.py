"""
    sponge_schematic

    The .schem file format, as described by the Sponge Schematic Specification. A .schematic file stores numeric
    block IDs, which worlds from Minecraft 1.13 and later don't have, while this stores block names and states.
    Saves version 3, and reads versions 1, 2 and 3.
"""
import logging
import os
import zlib

import numpy

from mceditlib import nbt
from mceditlib.anvil.format_profile import MINECRAFT_1_21, profileForDataVersion
from mceditlib.blocktypes.modern import ModernBlockTypeSet
from mceditlib.blocktypes.pack_library import selectPacks
from mceditlib.exceptions import LevelFormatError
from mceditlib.nbt import NBTFormatError
from mceditlib.schematic import SchematicFileAdapter

log = logging.getLogger(__name__)

CURRENT_VERSION = 3
SCHEMATIC_TAG = "Schematic"
UNKNOWN_DATA_VERSION = 0
VARINT_CONTINUES = 0x80
VARINT_VALUE = 0x7f


def encodeVarints(values):
    """
    :type values: numpy.ndarray
    :rtype: numpy.ndarray
    """
    if len(values) == 0 or values.max() <= VARINT_VALUE:
        return values.astype("uint8")

    output = bytearray()
    for value in values.tolist():
        while value > VARINT_VALUE:
            output.append(value & VARINT_VALUE | VARINT_CONTINUES)
            value >>= 7
        output.append(value)
    return numpy.frombuffer(bytes(output), "uint8")


def decodeVarints(data, count):
    """
    :type data: numpy.ndarray
    :param count: How many values there should be
    :type count: int
    :rtype: numpy.ndarray
    """
    data = numpy.asarray(data).astype("uint8")
    if len(data) == count and data.max(initial=0) <= VARINT_VALUE:
        return data.astype("intp")

    values = numpy.zeros(count, "intp")
    index = value = shift = 0
    for byte in data.tolist():
        value |= (byte & VARINT_VALUE) << shift
        if byte & VARINT_CONTINUES:
            shift += 7
        else:
            if index >= count:
                raise LevelFormatError("A .schem file has more blocks than its size says it should.")
            values[index] = value
            index += 1
            value = shift = 0
    if index != count:
        raise LevelFormatError("A .schem file has %d blocks, but its size says it should have %d." % (index, count))
    return values


def splitPaletteName(nameAndState):
    """ Return a block name and its properties as (key, value) pairs, from a name like "a:b[c=d,e=f]". """
    name, _, state = nameAndState.partition("[")
    if not state:
        return name, None
    return name, [tuple(pair.split("=", 1)) for pair in state.rstrip("]").split(",") if "=" in pair]


def _schematicTag(rootTag):
    """ Versions 1 and 2 have it all in the root tag, which version 3 has a tag inside of. """
    if SCHEMATIC_TAG in rootTag:
        return rootTag[SCHEMATIC_TAG]
    return rootTag


class SpongeSchematicAdapter(SchematicFileAdapter):
    """
    Holds a .schem file in the same form as other schematics in memory, so that it can be pasted and so on
    the same way. Its block types belong to it, and blocks are matched by name when they're copied to a world.
    """

    def __init__(self, filename, readonly=False, resume=False, **kwargs):
        try:
            rootTag = nbt.load(filename)
        except (EnvironmentError, EOFError, zlib.error, NBTFormatError) as e:
            raise LevelFormatError("Could not read %s: %r" % (filename, e)) from e

        tag = _schematicTag(rootTag)
        width, height, length = (self._unsigned(tag[name].value) for name in ("Width", "Height", "Length"))
        version = tag["Version"].value
        if version > CURRENT_VERSION:
            raise LevelFormatError("%s is a newer version of the .schem format than MCEdit can read." % filename)

        dataVersion = tag["DataVersion"].value if "DataVersion" in tag else UNKNOWN_DATA_VERSION
        profile = profileForDataVersion(dataVersion)
        if profile is None:
            # Block names rarely change much between versions. Those that have will show up as unknown blocks.
            log.warning("%s has data version %s, and is being read as Minecraft %s.", filename, dataVersion,
                        MINECRAFT_1_21.name)
            profile = MINECRAFT_1_21

        blocktypes = ModernBlockTypeSet(profile, selectPacks(None, profile).packs)
        super(SpongeSchematicAdapter, self).__init__(shape=(width, height, length), blocktypes=blocktypes)
        self.filename = filename
        self.readonly = readonly

        blocks = tag["Blocks"] if "Blocks" in tag else tag  # in version 3 they have a tag of their own
        paletteTag = blocks["Palette"]
        dataTag = blocks["Data"] if version >= 3 else tag["BlockData"]
        indices = decodeVarints(dataTag.value, width * height * length)

        size = 1 + max((entry.value for _, entry in paletteTag.items()), default=0)
        IDs = numpy.zeros(size, "uint16")
        metas = numpy.zeros(size, "uint8")
        for nameAndState, entry in paletteTag.items():
            name, properties = splitPaletteName(nameAndState)
            IDs[entry.value], metas[entry.value] = blocktypes.idAndMeta(name, properties)

        # These arrays are rounded up to whole chunks.
        self._Blocks[:height, :length, :width] = IDs[indices].reshape(height, length, width)
        self.rootTag["Data"].value[:height, :length, :width] = metas[indices].reshape(height, length, width)

        self._loadBlockEntities(blocks, tag, version)
        self._loadEntities(tag, version)

    @staticmethod
    def _unsigned(value):
        return value & 0xffff  # the format says these are unsigned, and NBT only has signed shorts

    def _loadBlockEntities(self, blocks, tag, version):
        key = "TileEntities" if version == 1 else "BlockEntities"
        for entry in blocks.get(key, tag.get(key, ())):
            flat = self._flatten(entry, version)
            position = entry["Pos"].value
            flat["x"], flat["y"], flat["z"] = (nbt.TAG_Int(int(p)) for p in position)
            flat["id"] = nbt.TAG_String(entry["Id"].value)
            cx, _, cz = self.TileEntityRef(flat).Position.chunkPos()
            self.tileEntitiesByChunk[cx, cz].append(flat)

    def _loadEntities(self, tag, version):
        for entry in tag.get("Entities", ()):
            flat = self._flatten(entry, version)
            flat["Pos"] = entry["Pos"].copy()
            flat["id"] = nbt.TAG_String(entry["Id"].value)
            cx, _, cz = self.EntityRef(flat).Position.chunkPos()
            self.entitiesByChunk[cx, cz].append(flat)

    @staticmethod
    def _flatten(entry, version):
        """ Return an entity's own tags, which are in `Data` in version 3 and beside the rest before that. """
        flat = nbt.TAG_Compound()
        source = entry["Data"] if version >= 3 and "Data" in entry else entry
        for key, value in source.items():
            if source is not entry or key not in ("Pos", "Id"):
                flat[key] = value.copy()
        return flat

    def saveChangesIter(self):
        saveSpongeSchematic(self, self.filename)
        yield 1, 1, "Saved"

    @classmethod
    def canOpenFile(cls, filename):
        if not os.path.isfile(filename) or not filename.lower().endswith(".schem"):
            return False
        try:
            tag = _schematicTag(nbt.load(filename))
        except (EnvironmentError, EOFError, zlib.error, NBTFormatError):
            return False
        return "Version" in tag and "Width" in tag


def saveSpongeSchematic(adapter, filename):
    """
    Save a schematic in memory, whose block types are of the modern kind, as a .schem file.

    :type adapter: mceditlib.schematic.SchematicFileAdapter
    :type filename: str
    """
    blocktypes = adapter.blocktypes
    height, length, width = adapter.Height, adapter.Length, adapter.Width

    # These arrays are rounded up to whole chunks.
    blockIDs = adapter._Blocks[:height, :length, :width]
    packed = blockIDs.astype("u4") * 16 + adapter.rootTag["Data"].value[:height, :length, :width]
    pairs, indices = numpy.unique(packed.ravel(), return_inverse=True)

    palette = nbt.TAG_Compound()
    for index, pair in enumerate(pairs):
        ID, meta = int(pair) >> 4, int(pair) & 15
        nameAndState = blocktypes.statesByID.get((ID, meta)) or blocktypes.statesByID.get((ID, 0))
        if nameAndState is None:
            raise LevelFormatError("Block ID %d has no name to save it by." % ID)
        palette[nameAndState] = nbt.TAG_Int(index)

    blocks = nbt.TAG_Compound()
    blocks["Palette"] = palette
    blocks["Data"] = nbt.TAG_Byte_Array(encodeVarints(indices.ravel()))
    blocks["BlockEntities"] = nbt.TAG_List(list_type=nbt.ID_COMPOUND)
    for source in [tag for tags in adapter.tileEntitiesByChunk.values() for tag in tags]:
        entry = nbt.TAG_Compound()
        entry["Pos"] = nbt.TAG_Int_Array(numpy.array([source["x"].value, source["y"].value, source["z"].value], "i4").astype(">u4"))
        entry["Id"] = nbt.TAG_String(source["id"].value)
        data = nbt.TAG_Compound()
        for key, value in source.items():
            if key not in ("x", "y", "z", "id"):
                data[key] = value.copy()
        entry["Data"] = data
        blocks["BlockEntities"].append(entry)

    tag = nbt.TAG_Compound()
    tag["Version"] = nbt.TAG_Int(CURRENT_VERSION)
    tag["DataVersion"] = nbt.TAG_Int(blocktypes.profile.lastDataVersion)
    tag["Width"] = nbt.TAG_Short(width)
    tag["Height"] = nbt.TAG_Short(height)
    tag["Length"] = nbt.TAG_Short(length)
    tag["Offset"] = nbt.TAG_Int_Array(numpy.zeros(3, ">u4"))
    tag["Blocks"] = blocks

    tag["Entities"] = nbt.TAG_List(list_type=nbt.ID_COMPOUND)
    for source in [tag for tags in adapter.entitiesByChunk.values() for tag in tags]:
        entry = nbt.TAG_Compound()
        entry["Pos"] = source["Pos"].copy()
        entry["Id"] = nbt.TAG_String(source["id"].value)
        data = nbt.TAG_Compound()
        for key, value in source.items():
            if key not in ("Pos", "id"):
                data[key] = value.copy()
        entry["Data"] = data
        tag["Entities"].append(entry)

    root = nbt.TAG_Compound()
    root[SCHEMATIC_TAG] = tag
    root.save(filename)
