#!/usr/bin/env python3
"""Check that a block pack is complete, and that its model references resolve.

    python scripts/validate_block_pack.py <pack folder> [--assets <jar, zip or folder>]...

Without --assets, checks the structure only. With --assets, every state that claims a blockstate
file must resolve to a variant or multipart entry there, and its models must exist. That proves
the data matches what the game's own files say, independently of the mod that wrote the pack.
Exit code 0 means no errors. Warnings do not fail the check.
"""
import argparse
import json
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from mceditlib.blocktypes.block_pack import BlockPack, BlockPackError  # noqa: E402

REQUIRED_STATE_ATTRIBUTES = ("internalName", "blockState", "displayName", "opaqueCube", "normalCube", "collidable",
                             "opacity", "brightness", "hasEntity", "materialLiquid", "materialMapColor", "renderType",
                             "renderLayer", "resourcePath", "resourceVariant", "hasBlockstateFile")
RENDER_MODEL = 3


class Assets:
    """Files from any number of jars, zips and folders, the first match winning."""

    def __init__(self, paths):
        self.sources = []
        for path in paths:
            self.sources.append(path if os.path.isdir(path) else zipfile.ZipFile(path))

    def read(self, name):
        for source in self.sources:
            try:
                if isinstance(source, zipfile.ZipFile):
                    return source.read(name)
                with open(os.path.join(source, name), "rb") as f:
                    return f.read()
            except (KeyError, FileNotFoundError):
                continue
        return None


def splitId(identifier):
    namespace, _, path = identifier.rpartition(":")
    return namespace or "minecraft", path


def whenMatches(when, state):
    """Evaluate a multipart `when` clause, as described by the blockstates format."""
    for key, value in when.items():
        if key == "OR":
            if not any(whenMatches(option, state) for option in value):
                return False
        elif key == "AND":
            if not all(whenMatches(option, state) for option in value):
                return False
        elif state.get(key) not in str(value).lower().split("|"):
            return False
    return True


def parseState(name):
    return dict(pair.split("=", 1) for pair in name.split(",") if "=" in pair)


def variantMatches(key, state):
    """A variants key may name a subset of the properties, or be empty to match everything."""
    return all(state.get(k) == v for k, v in parseState(key).items())


def modelsForState(blockstates, attributes):
    """Return the model names a state uses, or None if its blockstate file does not cover it."""
    state = parseState(attributes["resourceVariant"])
    if "variants" in blockstates:
        for key, entry in blockstates["variants"].items():
            if variantMatches(key, state):
                entries = entry if isinstance(entry, list) else [entry]
                return [e["model"] for e in entries]
        return None

    models = []
    for part in blockstates.get("multipart", []):
        if "when" not in part or whenMatches(part["when"], state):
            apply = part["apply"]
            models.extend(a["model"] for a in (apply if isinstance(apply, list) else [apply]))
    return models


def validate(pack, assets):
    errors = []
    warnings = []

    counts = pack.manifest["counts"]
    states = list(pack.states())
    if counts["blocks"] != len(pack.blocks):
        errors.append("manifest says %d blocks, found %d" % (counts["blocks"], len(pack.blocks)))
    if counts["states"] != len(states):
        errors.append("manifest says %d states, found %d" % (counts["states"], len(states)))

    ids = [block["id"] for block in pack.blocks]
    if len(ids) != len(set(ids)):
        errors.append("duplicate block ids")

    for block in pack.blocks:
        names = [state["state"] for state in block["states"]]
        if len(names) != len(set(names)):
            errors.append("%s has duplicate states" % block["id"])
        if block["defaultState"] not in names:
            errors.append("%s: the default state %r is not among its states" % (block["id"], block["defaultState"]))
        expected = 1
        for values in block["properties"].values():
            expected *= len(values)
        if expected != len(names):
            errors.append("%s has %d states, but its properties allow %d" % (block["id"], len(names), expected))

    for attributes in states:
        missing = [a for a in REQUIRED_STATE_ATTRIBUTES if a not in attributes]
        if missing:
            errors.append("%s%s is missing %s" % (attributes.get("internalName"), attributes.get("blockState"), missing))

    known = set(ids)
    for entity in pack.blockEntities:
        unknown = [b for b in entity["blocks"] if b not in known and pack.manifest["scope"] != "mods"]
        if unknown:
            errors.append("block entity %s refers to unknown blocks %s" % (entity["id"], unknown))
    for problem in pack.problems:
        warnings.append("reported by the mod: " + problem)

    stats = {"resolved": 0, "noFile": 0, "fallback": 0}
    if assets:
        seenModels = {}

        def modelExists(name):
            if name not in seenModels:
                namespace, path = splitId(name)
                seenModels[name] = assets.read("assets/%s/models/%s.json" % (namespace, path)) is not None
            return seenModels[name]

        blockstateFiles = {}
        for attributes in states:
            if attributes["renderType"] != RENDER_MODEL or attributes.get("modelSource") == "fallback":
                stats["fallback"] += attributes.get("modelSource") == "fallback"
                continue
            label = "%s%s" % (attributes["internalName"], attributes["blockState"])
            resource = attributes["resourcePath"]
            if resource not in blockstateFiles:
                namespace, path = splitId(resource)
                raw = assets.read("assets/%s/blockstates/%s.json" % (namespace, path))
                blockstateFiles[resource] = json.loads(raw) if raw else None

            blockstates = blockstateFiles[resource]
            if blockstates is None:
                stats["noFile"] += 1
                if attributes["hasBlockstateFile"]:
                    errors.append("%s claims a blockstate file for %s, which was not found" % (label, resource))
                continue

            models = modelsForState(blockstates, attributes)
            if models is None:
                errors.append("%s: no variant in %s matches %r" % (label, resource, attributes["resourceVariant"]))
                continue
            stats["resolved"] += 1
            for model in models:
                if not modelExists(model):
                    errors.append("%s uses model %s, which was not found" % (label, model))

    return errors, warnings, stats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pack")
    parser.add_argument("--assets", action="append", default=[], help="a jar, zip or folder of game assets")
    args = parser.parse_args()

    try:
        pack = BlockPack(args.pack)
    except BlockPackError as e:
        print("ERROR: %s" % e)
        return 1

    manifest = pack.manifest
    print("Minecraft %s (data version %s), %s, scope %s" % (manifest["minecraftVersion"], manifest["dataVersion"],
                                                            manifest["loader"], manifest["scope"]))
    print("%(blocks)d blocks, %(states)d states, %(blockEntities)d block entities" % manifest["counts"])

    # A pack holds the files of its own blocks, and comes before the game's.
    assetPaths = [args.pack] + args.assets if args.assets else None
    errors, warnings, stats = validate(pack, Assets(assetPaths) if assetPaths else None)
    if args.assets:
        print("Model states resolved against assets: %(resolved)d, with no blockstate file: %(noFile)d, "
              "marked to use a fallback: %(fallback)d" % stats)
    for warning in warnings[:20]:
        print("warning: %s" % warning)
    if len(warnings) > 20:
        print("... and %d more warnings" % (len(warnings) - 20))
    for error in errors[:50]:
        print("ERROR: %s" % error)
    if len(errors) > 50:
        print("... and %d more errors" % (len(errors) - 50))
    print("OK" if not errors else "FAILED: %d errors" % len(errors))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
