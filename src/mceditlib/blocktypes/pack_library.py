"""
    pack_library

    Finds the block packs a user has, and chooses the ones to use for a world.
"""
import logging
import os
import zipfile
import zlib

from mceditlib import nbt
from mceditlib.anvil.format_profile import profileForDataVersion
from mceditlib.blocktypes.block_pack import BlockPack, BlockPackError
from mceditlib.nbt import NBTFormatError

log = logging.getLogger(__name__)

# These appear in a world's list of mods, but their blocks are the game's own or they have none.
# The exporter is here because anyone who exports will have it in all of their worlds.
MODS_WITHOUT_OWN_BLOCKS = ("minecraft", "neoforge", "forge", "fml", "mcp", "mcedit_exporter", "FML", "Forge")

_packFolders = []
_packCache = {}  # path -> (modification time, BlockPack)


def setPackFolders(folders):
    """ Set the folders to look for block packs in. Each block pack is a folder or zip file inside one. """
    _packFolders[:] = folders
    _packCache.clear()


def packFolders():
    return list(_packFolders)


class PackSelection(object):
    """
    The block packs chosen for a world, and what the user might want to know about the choice.

    :ivar packs: The packs to use, most important first.
    :ivar missingMods: Names of mods in the world that no chosen pack covers. Some may have no blocks at all.
    :ivar versionMismatches: (mod, version in world, version in pack) for packs made with another version.
    :ivar problems: Sentences about packs that could not be used.
    :ivar reports: (pack name, number of problems) for packs in use whose export had problems.
    """

    def __init__(self):
        self.packs = []
        self.missingMods = []
        self.versionMismatches = []
        self.problems = []
        self.reports = []


def _loadPack(path):
    modified = os.path.getmtime(path)
    cached = _packCache.get(path)
    if cached is not None and cached[0] == modified:
        return cached[1]
    pack = BlockPack(path)
    _packCache[path] = modified, pack
    return pack


def _candidatePaths():
    for folder in _packFolders:
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.isdir(path) or zipfile.is_zipfile(path):
                yield path


def worldMods(levelTag):
    """
    Return the mods that last saved a world as a dict of ID to version. Returns None if the world does
    not say, as with a loader that does not record them, which isn't the same as having no mods.

    :type levelTag: nbt.TAG_Compound
    """
    fml = levelTag.get("fml")
    if fml is None or "LoadingModList" not in fml:
        return None
    return {entry["ModId"].value: entry["ModVersion"].value for entry in fml["LoadingModList"]
            if "ModId" in entry and "ModVersion" in entry}


def selectPacks(mods, profile):
    """
    Choose block packs for a world saved by the given mods, under the given format profile. A pack is
    never used if it was made with a different game version. If the mods are known, it is used if it
    covers any of them, with those that cover the most first. If not known, every pack might be of use.
    Exporting again makes another pack with the same mods, so a pack that adds nothing to those
    already chosen is left out, and the newest of them comes first.

    :param mods: As returned by worldMods
    :rtype: PackSelection
    """
    selection = PackSelection()
    wanted = None
    if mods is not None:
        wanted = {name: version for name, version in mods.items() if name not in MODS_WITHOUT_OWN_BLOCKS}

    candidates = []
    for path in _candidatePaths():
        try:
            pack = _loadPack(path)
        except BlockPackError as e:
            log.warning("Could not use block pack %s: %s", path, e)
            selection.problems.append("%s could not be read: %s" % (os.path.basename(path), e))
            continue

        accepts = getattr(profile, "acceptsPack", None)
        fits = accepts(pack) if accepts else profile.firstDataVersion <= pack.dataVersion <= profile.lastDataVersion
        if not fits:
            selection.problems.append("%s is for Minecraft %s, which is not this world's version."
                                      % (os.path.basename(path), pack.manifest.get("minecraftVersion")))
            continue

        candidates.append(pack)

    def covers(pack):
        if wanted is None:
            return set(pack.mods) - set(MODS_WITHOUT_OWN_BLOCKS)
        return set(pack.mods) & set(wanted)

    candidates.sort(key=lambda pack: pack.manifest.get("generatedAt", ""), reverse=True)
    candidates.sort(key=lambda pack: len(covers(pack)), reverse=True)

    covered = set()
    for pack in candidates:
        adds = covers(pack) - covered
        if not adds:
            continue
        selection.packs.append(pack)
        covered |= adds
        if wanted is not None:
            for name in sorted(adds):
                if pack.mods[name] is not None and pack.mods[name] != wanted[name]:
                    selection.versionMismatches.append((name, wanted[name], pack.mods[name]))
        if pack.problems:
            selection.reports.append((os.path.basename(pack.path), len(pack.problems)))

    if wanted is not None:
        selection.missingMods = sorted(set(wanted) - covered)
    return selection


def selectionForWorld(worldFolder):
    """
    Return the PackSelection for a world folder. Reads only what it needs from level.dat.

    :rtype: PackSelection
    """
    for name in "level.dat", "level.dat_old":
        try:
            levelTag = nbt.load(os.path.join(worldFolder, name))
            profile = profileForDataVersion(levelTag["Data"]["DataVersion"].value)
            break
        except (KeyError, EnvironmentError, EOFError, zlib.error, NBTFormatError):
            continue
    else:
        return PackSelection()

    if profile is None:
        return PackSelection()
    return selectPacks(worldMods(levelTag), profile)
