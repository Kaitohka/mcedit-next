"""
    format_profile

    What differs between the versions of the world format that MCEdit can read. Only the versions
    listed in PROFILES can be opened. A world saved by any other version is refused, not guessed at.
"""
from typing import NamedTuple


class FormatProfile(NamedTuple):
    name: str
    firstDataVersion: int
    lastDataVersion: int
    blockPack: str  # folder name under mceditlib/blocktypes/packs
    assetsVersion: str  # the version of the game to get block models and textures from
    paletteNameKey: str = "Name"
    palettePropertiesKey: str = "Properties"
    statusKey: str = "Status"
    biomeCellsPerSide: int = 4

    def dimensionKey(self, dimName):
        """ Return the name the game uses for the dimension kept in the given folder. """
        if dimName in DIMENSION_KEYS:
            return DIMENSION_KEYS[dimName]
        if dimName.startswith("dimensions/"):
            _, namespace, path = dimName.split("/", 2)
            return "%s:%s" % (namespace, path)
        return None

    def dimensionFolder(self, dimensionKey):
        """ Return the folder of the dimension the game calls by the given name. """
        for dimName, key in DIMENSION_KEYS.items():
            if key == dimensionKey:
                return dimName
        namespace, _, path = dimensionKey.partition(":")
        return "dimensions/%s/%s" % (namespace, path)


DIMENSION_KEYS = {
    "": "minecraft:overworld",
    "DIM-1": "minecraft:the_nether",
    "DIM1": "minecraft:the_end",
}

# The heights of the game's own dimension types, as (lowest block, one above the highest block).
VANILLA_DIMENSION_TYPE_HEIGHTS = {
    "minecraft:overworld": (-64, 320),
    "minecraft:overworld_caves": (-64, 320),
    "minecraft:the_nether": (0, 256),
    "minecraft:the_end": (0, 256),
}

MINECRAFT_1_21 = FormatProfile(name="1.21-1.21.1", firstDataVersion=3953, lastDataVersion=3955,
                               blockPack="minecraft_1_21_1", assetsVersion="1.21.1")

PROFILES = (MINECRAFT_1_21,)

# Chunks can be older than the world they are in, since the game upgrades a chunk only when it loads
# it. Chunks from this version on keep their tags in the root tag and their blocks in palettes.
FIRST_PALETTE_CHUNK_DATA_VERSION = 2844


def profileForDataVersion(dataVersion):
    """ Return the profile for the given version, or None if worlds it saves cannot be read. """
    for profile in PROFILES:
        if profile.firstDataVersion <= dataVersion <= profile.lastDataVersion:
            return profile
    return None
