#!/usr/bin/env python3
"""Open each release's test worlds with MCEdit, read every chunk, and report what could not be read.

Usage: python scripts/versions/check_worlds.py [--only ID ...] [--worlds normal debug]

Run scripts/versions/make_test_worlds.py first. Worlds are opened read-only and nothing in them is changed.
For each world this reports the adapter and format profile chosen, whether the data version in level.dat is the
one listed for the release, how many chunks each dimension has and how many could not be read, how many blocks
are of states MCEdit does not know, and the entities and block entities found. The results also go in
worlds_local/versions/check_results.json.
"""
import argparse
import collections
import json
import sys
import time
import traceback
from pathlib import Path

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_versions import ROOT, VERSIONS  # noqa: E402
from make_test_worlds import versionKey  # noqa: E402

sys.path.insert(0, str(ROOT / "src"))
from mceditlib import nbt  # noqa: E402
from mceditlib.anvil.format_profile import RELEASES  # noqa: E402
from mceditlib.worldeditor import WorldEditor  # noqa: E402

LISTED_DATA_VERSIONS = dict(RELEASES)


def levelDataVersion(world):
    data = nbt.load(str(world / "level.dat"))["Data"]
    return data["DataVersion"].value if "DataVersion" in data else None


def unknownStates(blocktypes, section, seen):
    """ Count the blocks in a section whose states the block type set does not know. """
    pairs, counts = numpy.unique(section.Blocks.astype("u4") * 16 + section.Data, return_counts=True)
    unknown = 0
    for pair, count in zip(pairs, counts):
        ID, meta = int(pair) >> 4, int(pair) & 15
        if ID == 0:
            continue
        known = (ID, meta) in blocktypes.statesByID
        if known and getattr(blocktypes, "stateFlags", None) is not None:
            from mceditlib.blocktypes import STATE_UNRESOLVED
            known = not (blocktypes.stateFlags[ID * 16 + meta] & STATE_UNRESOLVED)
        if not known:
            unknown += int(count)
            seen[blocktypes.statesByID.get((ID, meta), "%d:%d" % (ID, meta))] += int(count)
    return unknown


def checkWorld(release, kind):
    world = VERSIONS / release / "run" / kind
    result = {"release": release, "world": kind}
    if not (world / "level.dat").exists():
        result["error"] = "no world"
        return result
    dataVersion = levelDataVersion(world)
    result["dataVersion"] = dataVersion
    listed = LISTED_DATA_VERSIONS.get(release)
    if listed is not None and listed != dataVersion:
        result["dataVersionMismatch"] = "listed %s, world has %s" % (listed, dataVersion)

    start = time.time()
    try:
        editor = WorldEditor(str(world), readonly=True)
    except Exception as e:
        result["error"] = "could not open: %r" % e
        return result
    try:
        adapter = editor.adapter
        result["adapter"] = type(adapter).__name__
        profile = getattr(adapter, "profile", None)
        result["profile"] = profile.name if profile is not None else None
        blocktypes = editor.blocktypes
        unknownSeen = collections.Counter()
        dimensions = {}
        for dimName in sorted(editor.listDimensions()):
            dim = editor.getDimension(dimName)
            info = {"chunks": 0, "errors": 0, "blocks": 0, "unknownBlocks": 0, "entities": 0, "blockEntities": 0}
            firstError = None
            for cx, cz in dim.chunkPositions():
                info["chunks"] += 1
                try:
                    chunk = dim.getChunk(cx, cz)
                    for cy in chunk.sectionPositions():
                        section = chunk.getSection(cy)
                        if section is None:
                            continue
                        info["blocks"] += int((section.Blocks != 0).sum())
                        info["unknownBlocks"] += unknownStates(blocktypes, section, unknownSeen)
                    info["entities"] += len(list(chunk.Entities))
                    info["blockEntities"] += len(list(chunk.TileEntities))
                    chunk.Biomes  # noqa: to read them
                except Exception as e:
                    info["errors"] += 1
                    if firstError is None:
                        firstError = "%s: %r" % ((cx, cz), e)
            if firstError:
                info["firstError"] = firstError
            dimensions[dimName or "overworld"] = info
        result["dimensions"] = dimensions
        result["unknownStates"] = dict(unknownSeen.most_common(15))
        biomeNames = getattr(adapter, "biomeNames", None)
        if biomeNames:
            result["biomes"] = len(biomeNames)
    except Exception:
        result["error"] = traceback.format_exc(limit=4)
    finally:
        editor.close()
    result["seconds"] = round(time.time() - start, 1)
    return result


def summary(result):
    if "error" in result:
        return "%-8s %-6s ERROR %s" % (result["release"], result["world"], result["error"].splitlines()[-1][:150])
    dims = result["dimensions"]
    chunks = sum(d["chunks"] for d in dims.values())
    errors = sum(d["errors"] for d in dims.values())
    unknown = sum(d["unknownBlocks"] for d in dims.values())
    blocks = sum(d["blocks"] for d in dims.values())
    entities = sum(d["entities"] for d in dims.values())
    tiles = sum(d["blockEntities"] for d in dims.values())
    text = "%-8s %-6s %-21s %-14s dims %d chunks %4d errors %d unknown %d/%d entities %d tiles %d" % (
        result["release"], result["world"], result["adapter"], result["profile"] or "-", len(dims), chunks, errors,
        unknown, blocks, entities, tiles)
    if "dataVersionMismatch" in result:
        text += "  DATA VERSION " + result["dataVersionMismatch"]
    if errors:
        text += "  first error: " + next(d["firstError"] for d in dims.values() if d.get("firstError"))[:150]
    if result.get("unknownStates"):
        text += "  e.g. " + ", ".join(list(result["unknownStates"])[:4])
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--worlds", nargs="*", default=["normal", "debug"])
    args = parser.parse_args()

    with open(VERSIONS / "releases.json", encoding="utf-8") as f:
        releases = sorted((r["id"] for r in json.load(f)), key=versionKey)
    if args.only:
        releases = [r for r in releases if r in args.only]

    resultsFile = VERSIONS / "check_results.json"
    results = json.loads(resultsFile.read_text(encoding="utf-8")) if resultsFile.exists() else {}
    for release in releases:
        for kind in args.worlds:
            if not (VERSIONS / release / "run" / (kind + ".done")).exists():
                continue
            result = checkWorld(release, kind)
            results["%s %s" % (release, kind)] = result
            print(summary(result), flush=True)
    with open(resultsFile, "w", encoding="utf-8", newline="\n") as f:
        json.dump(results, f, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    sys.exit(main())
