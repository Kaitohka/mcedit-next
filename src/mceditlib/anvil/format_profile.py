"""
    format_profile

    What differs between the versions of the world format that MCEdit can read with ModernAnvilAdapter,
    which is every release from 1.13 on. Worlds from 1.12.2 and earlier are read by AnvilWorldAdapter.

    Only the releases listed in RELEASES can be opened. A world saved by any other version, such as a
    snapshot, is refused, not guessed at.

    How a chunk is stored is decided chunk by chunk, from the chunk's own data version, since the game
    upgrades a chunk only when it loads it: a world from 26.3 can still hold chunks last saved by 1.13.
"""
import json
import os
from typing import NamedTuple, Tuple

PACK_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "blocktypes", "packs")

# --- Data versions where the format changed ---

# 1.13 (17w47a): blocks are saved by name in palettes instead of by number.
FIRST_PALETTE_DATA_VERSION = 1451
# 1.16 (20w17a): palette indexes and heightmap values no longer run on from one long into the next.
FIRST_UNSPANNED_DATA_VERSION = 2529
# 1.14 (18w43a): the game lights a chunk again when it loads one whose isLightOn is not set.
FIRST_LIGHT_ENGINE_DATA_VERSION = 1901
# 1.15: the game locks session.lock to keep others out of a world. Before, it wrote the time it opened the world
# in it, and gave up the world if that changed.
FIRST_FILE_LOCK_DATA_VERSION = 2225
# 1.17 (20w45a): entities are kept in their own region files, in entities/.
FIRST_ENTITY_FILES_DATA_VERSION = 2681
# 1.18 (21w43a): a chunk's tags are in the root tag instead of a "Level" tag, and sections hold palettes
# for both blocks and biomes.
FIRST_ROOT_CHUNK_DATA_VERSION = 2844
# The world can be taller than 256 blocks and reach below 0. Worlds from 1.17 are 0 to 256 unless a data
# pack said otherwise.
FIRST_TALL_WORLD_DATA_VERSION = 2844
# 26.1: the three dimensions of the game are in dimensions/minecraft/, and much of level.dat moved to files
# in data/.
FIRST_DIMENSIONS_FOLDER_DATA_VERSION = 4786
# 26.3: palette entries use "id" and "properties", may leave out the properties of a default state, and may
# be strings instead of compounds.
FIRST_LOWERCASE_PALETTE_DATA_VERSION = 5023

# Kept for code written when only 1.18 and later could be read.
FIRST_PALETTE_CHUNK_DATA_VERSION = FIRST_ROOT_CHUNK_DATA_VERSION


class FormatProfile(NamedTuple):
    name: str
    firstDataVersion: int
    lastDataVersion: int
    blockPack: str  # folder name under mceditlib/blocktypes/packs
    assetsVersion: str  # the version of the game to get block models and textures from
    paletteNameKey: str = "Name"  # what new palette entries are written with
    palettePropertiesKey: str = "Properties"
    statusKey: str = "Status"
    biomeCellsPerSide: int = 4
    dataVersions: Tuple[int, ...] = ()  # the releases this profile is for; empty means the whole range
    writable: bool = False  # whether changes can be saved. Each format is checked in the game before it is.

    def acceptsDataVersion(self, dataVersion):
        if self.dataVersions:
            return dataVersion in self.dataVersions
        return self.firstDataVersion <= dataVersion <= self.lastDataVersion

    @property
    def hasBlockPack(self):
        return os.path.isdir(os.path.join(PACK_FOLDER, self.blockPack))

    @property
    def hasDimensionsFolder(self):
        """ If the game's own dimensions are in dimensions/minecraft/ instead of the world folder, DIM-1 and DIM1. """
        return self.firstDataVersion >= FIRST_DIMENSIONS_FOLDER_DATA_VERSION

    @property
    def defaultHeights(self):
        """ The lowest Y of the overworld, and one above its highest, unless the world says otherwise. """
        if self.firstDataVersion >= FIRST_TALL_WORLD_DATA_VERSION:
            return VANILLA_DIMENSION_TYPE_HEIGHTS["minecraft:overworld"]
        return 0, 256

    def dimensionTypeHeights(self, typeName):
        """ The heights of one of the game's own dimension types, or None if the type is not one of those. """
        if typeName not in VANILLA_DIMENSION_TYPE_HEIGHTS:
            return None
        if self.firstDataVersion < FIRST_TALL_WORLD_DATA_VERSION:
            return 0, 256
        return VANILLA_DIMENSION_TYPE_HEIGHTS[typeName]

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
        if not self.hasDimensionsFolder:
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

# The heights of the game's own dimension types from 1.18 on, as (lowest block, one above the highest block).
VANILLA_DIMENSION_TYPE_HEIGHTS = {
    "minecraft:overworld": (-64, 320),
    "minecraft:overworld_caves": (-64, 320),
    "minecraft:the_nether": (0, 256),
    "minecraft:the_end": (0, 256),
}

# Every release from 1.13 on, oldest first, with the data version it saves worlds with. Releases with the
# same blocks share a block pack, named after the first of them.
RELEASES = (
    ("1.13", 1519), ("1.13.1", 1628), ("1.13.2", 1631),
    ("1.14", 1952), ("1.14.1", 1957), ("1.14.2", 1963), ("1.14.3", 1968), ("1.14.4", 1976),
    ("1.15", 2225), ("1.15.1", 2227), ("1.15.2", 2230),
    ("1.16", 2566), ("1.16.1", 2567), ("1.16.2", 2578), ("1.16.3", 2580), ("1.16.4", 2584), ("1.16.5", 2586),
    ("1.17", 2724), ("1.17.1", 2730),
    ("1.18", 2860), ("1.18.1", 2865), ("1.18.2", 2975),
    ("1.19", 3105), ("1.19.1", 3117), ("1.19.2", 3120), ("1.19.3", 3218), ("1.19.4", 3337),
    ("1.20", 3463), ("1.20.1", 3465), ("1.20.2", 3578), ("1.20.3", 3698), ("1.20.4", 3700), ("1.20.5", 3837),
    ("1.20.6", 3839),
    ("1.21", 3953), ("1.21.1", 3955), ("1.21.2", 4080), ("1.21.3", 4082), ("1.21.4", 4189), ("1.21.5", 4325),
    ("1.21.6", 4435), ("1.21.7", 4438), ("1.21.8", 4440), ("1.21.9", 4554), ("1.21.10", 4556),
    ("1.21.11", 4671),
    ("26.1", 4786), ("26.1.1", 4788), ("26.1.2", 4790),
    ("26.2", 4903),
    ("26.3", 5023),
)

# Which block pack each release uses, as written by scripts/versions/make_block_packs.py. Releases with the same
# blocks share a pack, and are next to each other in RELEASES. They become one profile.
RELEASES_FILE = os.path.join(PACK_FOLDER, "releases.json")


def _loadPackNames():
    try:
        with open(RELEASES_FILE, encoding="utf-8") as f:
            return json.load(f)["packs"]
    except (EnvironmentError, ValueError, KeyError):
        return {"1.21": "minecraft_1_21_1", "1.21.1": "minecraft_1_21_1"}


PACK_NAMES = _loadPackNames()

# The formats that changes can be saved in, by release. Each is added once a world saved by MCEdit in that
# format has been checked in the game, as scripts/versions/check_saving.py does: the release's own server loaded the
# chunks MCEdit wrote, saved them again, and kept every block. That has been done for all of them.
WRITABLE_RELEASES = {release for release, _ in RELEASES}


def packName(release):
    return PACK_NAMES.get(release) or "minecraft_" + release.replace(".", "_")


def _makeProfiles():
    groups = []  # [(pack name, writable), [(release, data version), ...]]
    for release, dataVersion in RELEASES:
        # A profile's releases must also save worlds the same way.
        formatChanges = tuple(dataVersion >= change for change in (FIRST_TALL_WORLD_DATA_VERSION,
                                                                   FIRST_DIMENSIONS_FOLDER_DATA_VERSION,
                                                                   FIRST_LOWERCASE_PALETTE_DATA_VERSION))
        key = packName(release), release in WRITABLE_RELEASES, formatChanges
        if groups and groups[-1][0] == key:
            groups[-1][1].append((release, dataVersion))
        else:
            groups.append([key, [(release, dataVersion)]])

    profiles = []
    for (pack, writable, _), members in groups:
        releases = [release for release, _ in members]
        dataVersions = tuple(dataVersion for _, dataVersion in members)
        lowercase = dataVersions[0] >= FIRST_LOWERCASE_PALETTE_DATA_VERSION
        profiles.append(FormatProfile(
            name=releases[0] if len(releases) == 1 else "%s-%s" % (releases[0], releases[-1]),
            firstDataVersion=dataVersions[0],
            lastDataVersion=dataVersions[-1],
            dataVersions=dataVersions,
            blockPack=pack,
            assetsVersion=releases[-1],
            paletteNameKey="id" if lowercase else "Name",
            palettePropertiesKey="properties" if lowercase else "Properties",
            writable=writable,
        ))
    return tuple(profiles)


PROFILES = _makeProfiles()

# The block pack that comes with MCEdit and was made by the export mod. Code and tests that need some
# modern profile use it.
MINECRAFT_1_21 = next(profile for profile in PROFILES if 3955 in profile.dataVersions)


def profileForDataVersion(dataVersion):
    """ Return the profile for the given version, or None if worlds it saves cannot be read. """
    for profile in PROFILES:
        if profile.acceptsDataVersion(dataVersion):
            return profile
    return None


def releaseForDataVersion(dataVersion):
    """ The name of the release that saves worlds with this data version, or None. """
    for release, version in RELEASES:
        if version == dataVersion:
            return release
    return None
