"""
    forge_blockstates

    Forge's own format of blockstate files, used by many mods for 1.8.9 to 1.12.2, which files mark with
    "forge_marker": 1. A block's look is put together from parts: "defaults", which apply to every state, then a
    part for each property's value, then any part written for the whole state, each over the last.

        {
            "forge_marker": 1,
            "defaults": {"model": "cube_all", "textures": {"all": "somemod:blocks/lamp"}},
            "variants": {
                "lit": {"true": {"textures": {"all": "somemod:blocks/lamp_on"}}, "false": {}},
                "facing": {"north": {}, "east": {"y": 90}, "south": {"y": 180}, "west": {"y": 270}},
                "inventory": [{}]
            }
        }

    A part can also name other models to draw with it, in "submodel", and give a model new textures.
"""
import logging

log = logging.getLogger(__name__)

# What a part may say. A key in "variants" whose value has any of these is a part for a whole state, not a property.
PART_KEYS = frozenset(("model", "textures", "x", "y", "uvlock", "weight", "submodel", "transform", "custom",
                       "smooth_lighting", "gui3d"))
# Keys of "variants" that are whole states in themselves.
WHOLE_STATE_KEYS = ("normal", "inventory")


def isForgeBlockstate(statesJson):
    return isinstance(statesJson, dict) and statesJson.get("forge_marker") == 1


def _isWholeState(key, value):
    if isinstance(value, list):
        return True
    if not isinstance(value, dict):
        return False
    if "=" in key or key in WHOLE_STATE_KEYS:
        return True
    if not value:
        return False  # a property with no values
    # A property maps each of its values to a part. A part has its own keys, and not all of them are objects.
    return any(not isinstance(v, dict) or k in PART_KEYS for k, v in value.items())


def _merge(base, part):
    """ A copy of base with part over it. Textures and submodels are merged name by name. """
    merged = dict(base)
    for key, value in part.items():
        if key in ("textures", "submodel") and isinstance(value, dict) and isinstance(merged.get(key), dict):
            combined = dict(merged[key])
            combined.update(value)
            merged[key] = combined
        else:
            merged[key] = value
    return merged


def _firstOf(value):
    if isinstance(value, list):
        return value[0] if value else {}
    return value if isinstance(value, dict) else {}


def _sameState(a, b):
    return sorted(a.split(",")) == sorted(b.split(","))


def partFor(statesJson, resourceVariant):
    """
    The part for one state, such as "facing=north,lit=true", or "normal" for a block with no properties: the
    defaults, with the part for each of its properties' values over them, then any part for the whole state.
    """
    variants = statesJson.get("variants") or {}
    values = dict(pair.split("=", 1) for pair in resourceVariant.split(",") if "=" in pair)
    part = dict(statesJson.get("defaults") or {})
    whole = None
    for key, value in variants.items():
        if _isWholeState(key, value):
            if key == resourceVariant or ("=" in key and _sameState(key, resourceVariant)):
                whole = _firstOf(value)
        elif key in values:
            propertyPart = value.get(values[key])
            if propertyPart is None:
                propertyPart = value.get(values[key].lower())
            if isinstance(propertyPart, (dict, list)):
                part = _merge(part, _firstOf(propertyPart))
    if whole is not None:
        part = _merge(part, whole)
    return part


def _entry(part, textures):
    model = part.get("model")
    if not isinstance(model, str) or not model:
        return None
    entry = {"model": model}
    for key in ("x", "y", "uvlock", "custom"):
        if key in part:
            entry[key] = part[key]
    if textures:
        entry["textures"] = textures
    return entry


def modelEntries(statesJson, resourceVariant):
    """
    The models to draw for a state, in the form a vanilla blockstate file gives them: each a dict with "model",
    and maybe "x", "y" and "textures", the textures to give the model in place of its own.
    """
    part = partFor(statesJson, resourceVariant)
    textures = part.get("textures") if isinstance(part.get("textures"), dict) else {}
    entries = []
    main = _entry(part, textures)
    if main is not None:
        entries.append(main)
    submodels = part.get("submodel")
    if isinstance(submodels, str):
        submodels = {"": {"model": submodels}}
    if isinstance(submodels, dict):
        for name, sub in submodels.items():
            sub = _firstOf(sub)
            subTextures = dict(textures)
            if isinstance(sub.get("textures"), dict):
                subTextures.update(sub["textures"])
            subEntry = _entry(sub, subTextures)
            if subEntry is not None:
                # A submodel turns with the block, as well as by itself.
                for axis in ("x", "y"):
                    subEntry[axis] = (subEntry.get(axis, 0) + part.get(axis, 0)) % 360
                entries.append(subEntry)
    return entries


def modelNames(statesJson):
    """ Every model a Forge blockstate file names, as written there. """
    names = []

    def collect(part):
        part = _firstOf(part)
        if isinstance(part.get("model"), str):
            names.append(part["model"])
        submodels = part.get("submodel")
        if isinstance(submodels, str):
            names.append(submodels)
        elif isinstance(submodels, dict):
            for sub in submodels.values():
                collect(sub)

    collect(statesJson.get("defaults") or {})
    for key, value in (statesJson.get("variants") or {}).items():
        if _isWholeState(key, value):
            for part in (value if isinstance(value, list) else [value]):
                collect(part)
        else:
            for part in value.values():
                collect(part)
    return names
