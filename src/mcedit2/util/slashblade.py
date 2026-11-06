"""
    slashblade

    Find the blades of the SlashBlade mod (拔刀剑, "Slash Blade:Resharpened" for 1.21.1) in a world.

    A blade lying on the ground is a slashblade:blade_item_entity, and one on a stand is held by a
    slashblade:blade_stand_entity. Both keep the blade in their "Item" tag, as an item frame does. A
    blade can also end up in an ordinary minecraft:item. Blades with broken data are a common cause
    of the game crashing when it loads them, so MCEdit shows where they are.

    A blade is an item with the slashblade:blade_state data component. That also finds the blades
    that other mods add on top of SlashBlade, which have IDs of their own.
"""
from mceditlib import nbt

NAMESPACE = "slashblade"
BLADE_ITEM_ENTITY = "slashblade:blade_item_entity"
BLADE_STAND_ENTITY = "slashblade:blade_stand_entity"
BLADE_STATE = "slashblade:blade_state"


def _compound(tag, key):
    if tag is None or tag.tagID != nbt.ID_COMPOUND or key not in tag:
        return None
    value = tag[key]
    return value if value.tagID == nbt.ID_COMPOUND else None


def _string(tag, key):
    if tag is None or tag.tagID != nbt.ID_COMPOUND or key not in tag:
        return None
    value = tag[key]
    return value.value if value.tagID == nbt.ID_STRING else None


def isBlade(itemTag):
    """ True if this item stack is a blade. """
    if itemTag is None or itemTag.tagID != nbt.ID_COMPOUND:
        return False
    components = _compound(itemTag, "components")
    if components is not None and BLADE_STATE in components:
        return True
    itemID = _string(itemTag, "id") or ""
    namespace, _, path = itemID.partition(":")
    return namespace == NAMESPACE and path.startswith("slashblade")


def heldBlade(entityTag):
    """ The blade this entity holds or is, as its item tag, or None. """
    item = _compound(entityTag, "Item")
    return item if isBlade(item) else None


def isBladeStand(entityTag):
    return _string(entityTag, "id") == BLADE_STAND_ENTITY


def bladeName(itemTag):
    """
    A short name for a blade: the last part of its translation key, which names the blade
    ("yamato" for "item.slashblade.yamato"), or its item ID.
    """
    state = _compound(_compound(itemTag, "components"), BLADE_STATE)
    key = _string(state, "translationKey")
    if key:
        return key.rsplit(".", 1)[-1]
    return _string(itemTag, "id") or "?"


def mayHoldBlade(tag):
    """
    A quick test that rules out most tags without walking them: a blade always has "slashblade" in
    its data, as part of its ID or of the name of its data component.
    """
    return b"slashblade" in tag.save(compressed=False)


def findBlades(tag, maxDepth=32):
    """
    Every blade inside this tag, at any depth: in a chest's items, inside a shulker box in a chest,
    in a mob's hands, or in a mod's own storage. Yields (path, itemTag), where path is the list of
    names and indexes that lead to the blade.
    """
    if not mayHoldBlade(tag):
        return

    def walk(tag, path):
        if len(path) > maxDepth:
            return
        if tag.tagID == nbt.ID_COMPOUND:
            if path and isBlade(tag):
                yield path, tag
                return
            for name, subtag in tag.items():
                if subtag.tagID in (nbt.ID_COMPOUND, nbt.ID_LIST):
                    yield from walk(subtag, path + [name])
        elif tag.tagID == nbt.ID_LIST:
            for index, subtag in enumerate(tag):
                if subtag.tagID in (nbt.ID_COMPOUND, nbt.ID_LIST):
                    yield from walk(subtag, path + [index])

    yield from walk(tag, [])


def containsBlade(tag):
    for _ in findBlades(tag):
        return True
    return False


def pathText(path):
    """ "Items[3].components.minecraft:container[0]" for a path to a blade. """
    text = ""
    for part in path:
        if isinstance(part, int):
            text += "[%d]" % part
        else:
            text += ("." if text else "") + part
    return text


def isBroken(itemTag):
    state = _compound(_compound(itemTag, "components"), BLADE_STATE)
    if state is None or "isBroken" not in state:
        return False
    return bool(state["isBroken"].value)
