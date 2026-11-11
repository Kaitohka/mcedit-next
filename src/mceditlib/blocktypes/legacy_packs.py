"""
    legacy_packs

    Block packs for worlds from Minecraft 1.12.2 and earlier, which keep blocks by number. Forge writes in level.dat
    which number it gave each block's name in that world. A pack made by the exporter in the same version of the game
    says, for each of its blocks, which state each of the sixteen data values reads as. Together they say what each
    number and data value in the world is, and the pack has the rest: how the block looks and behaves.

    The game's own blocks keep the tables MCEdit has for them. Only the blocks of mods are taken from packs.
"""
import logging

from mceditlib.blocktypes import BlockType
from mceditlib.blocktypes import pack_library

log = logging.getLogger(__name__)

BLOCK_KEY_MARK = u"\x01"  # FML for 1.7.10 puts this before the name of a block in ItemData
MINECRAFT = "minecraft:"


class LegacyProfile(object):
    """
    Stands in for a FormatProfile, for pack_library.selectPacks: a pack must be for the same version of the game.
    Data versions began with 1.9, so worlds from 1.7.10 and 1.8.9 have none, and are told apart by what Forge
    wrote: 1.7.10 keeps its numbers in ItemData, and 1.8 on in Registries.
    """

    def __init__(self, dataVersion, before1_8=False):
        self.firstDataVersion = self.lastDataVersion = dataVersion
        self.before1_8 = before1_8

    def acceptsPack(self, pack):
        if pack.dataVersion != self.firstDataVersion:
            return False
        if self.firstDataVersion:
            return True
        isFrom1_7 = str(pack.manifest.get("minecraftVersion", "")).startswith("1.7")
        return isFrom1_7 == self.before1_8


def fmlBlockIDs(levelTag):
    """
    The numbers Forge gave blocks in a world, as {name: number}, from level.dat. Empty if Forge did not save the world.

    :type levelTag: nbt.TAG_Compound
    """
    fml = levelTag.get("FML")
    if fml is None:
        return {}
    found = {}
    if "ItemData" in fml:  # 1.7.10
        for entry in fml["ItemData"]:
            key = entry["K"].value
            if key.startswith(BLOCK_KEY_MARK):
                found[key[1:]] = entry["V"].value
    registries = fml.get("Registries")
    if registries is not None and "minecraft:blocks" in registries:  # 1.8 to 1.12
        for entry in registries["minecraft:blocks"].get("ids", ()):
            found[entry["K"].value] = entry["V"].value
    return found


def fmlMods(levelTag):
    """ The mods that last saved a world, as {id: version}, or None if level.dat does not say. """
    fml = levelTag.get("FML")
    if fml is None or "ModList" not in fml:
        return None
    return {entry["ModId"].value: entry["ModVersion"].value for entry in fml["ModList"]
            if "ModId" in entry and "ModVersion" in entry}


def selectLegacyPacks(levelTag):
    """ The block packs for a world from 1.12.2 or earlier, as a pack_library.PackSelection. """
    data = levelTag["Data"]
    dataVersion = data["DataVersion"].value if "DataVersion" in data else 0
    fml = levelTag.get("FML")
    before1_8 = fml is not None and "ItemData" in fml
    return pack_library.selectPacks(fmlMods(levelTag), LegacyProfile(dataVersion, before1_8))


def applyLegacyPacks(blocktypes, packs, blockIDs):
    """
    Give the blocks of mods in a world their states and attributes from block packs.

    :param blocktypes: The world's PCBlockTypeSet, which already has its numbers from Forge.
    :param packs: Block packs, the most important first.
    :param blockIDs: The world's numbers for block names, as from fmlBlockIDs.
    :return: The number of blocks given their states.
    """
    done = set()
    count = 0
    for pack in packs:
        for block in pack.blocks:
            name = block["id"]
            legacy = block.get("legacy")
            if legacy is None or name.startswith(MINECRAFT) or name in done or name not in blockIDs:
                continue
            done.add(name)
            _applyBlock(blocktypes, block, legacy, blockIDs[name])
            count += 1
    log.info("Took %d blocks of mods from block packs", count)
    return count


def _applyBlock(blocktypes, block, legacy, ID):
    name = block["id"]
    metaStates = {int(meta): state for meta, state in legacy.get("metaStates", {}).items()}

    # What Forge's numbers alone gave this block, before it was known.
    for meta in range(16):
        old = blocktypes.statesByID.pop((ID, meta), None)
        if old is not None:
            blocktypes.blockJsons.pop(old, None)
            blocktypes.IDsByState.pop(old, None)
    blocktypes.discardIDs([ID])

    jsons = []
    for state in block["states"]:
        attributes = dict(block["common"])
        attributes.update(state)
        stateName = attributes.pop("state")
        nameAndState = "%s[%s]" % (name, stateName) if stateName else name
        meta = attributes.get("meta")
        if meta is None:
            # Exported from 1.7.10, where each state is a data value
            meta = int(stateName.partition("=")[2] or 0)
        blocktypes.IDsByState[nameAndState] = ID, meta
        attributes.update({
            "internalName": name,
            "blockState": nameAndState,
            "displayName": block["displayName"],
            "unlocalizedName": block.get("descriptionId", ""),
            "defaultState": stateName == block["defaultState"],
            "unknown": False,
        })
        jsons.append(attributes)

    # Each data value is the state the game reads it as. A state that isn't saved, such as how a fence connects,
    # is reached by name but never by number.
    # A data value the game reads as a state it saves under another data value (glass at 1 is glass, saved at 0)
    # looks like that one.
    for meta in range(16):
        blocktypes.metaAliases.pop((ID, meta), None)
    for meta, stateName in metaStates.items():
        nameAndState = "%s[%s]" % (name, stateName) if stateName else name
        blocktypes.statesByID[ID, meta] = nameAndState
        savedAs = blocktypes.IDsByState.get(nameAndState)
        if savedAs is not None and savedAs != (ID, meta):
            blocktypes.metaAliases[ID, meta] = savedAs[1]

    blocktypes.IDsByName[name] = ID
    blocktypes.namesByID[ID] = name
    blocktypes.defaultBlockstates[name] = "[%s]" % block["defaultState"] if block["defaultState"] else ""
    blocktypes.addBlocksFromJSON(jsons)
    blocktypes.addBlocktypes(BlockType(ID, meta, blocktypes) for meta in sorted(metaStates))
