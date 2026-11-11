#!/usr/bin/env python3
"""Make the block pack of every release from 1.13 on, from the reports of its data generator.

Usage: python scripts/versions/make_block_packs.py [--only ID ...]

Run scripts/versions/fetch_versions.py and scripts/versions/make_reports.py first. Writes
src/mceditlib/blocktypes/packs/minecraft_<release>/ for the first release of each run of releases with the same
blocks, and src/mceditlib/blocktypes/packs/releases.json, which says which pack each release uses.

The reports give every block, its properties and its default state, but not what MCEdit needs to draw and light
it: whether it is a full cube, how much light it lets through, and so on. Those are taken from the pack the
export mod made in 1.21.1 for blocks it has, under their 1.21.1 names if they were renamed. A block that is not
in it gets them from the 1.21.1 block most like it, or failing that from its model. Each pack's report.json lists
every block whose attributes were worked out like this.

Releases after 1.21.1 take their blocks from a pack the export mod made in that release's own client instead, when
there is one: exporter/test_universal.py leaves one for each release under worlds_local/exporter_test/. Only the
game's own blocks are taken from it. Packs before 1.21.1 still come from the reports, since the export mod's builds
for older releases differ from 1.21.1 in a few attributes (some tint colours need a world to work out).
"""
import argparse
import datetime
import gzip
import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_versions import ROOT, VERSIONS  # noqa: E402
from make_test_worlds import atLeast, versionKey  # noqa: E402

sys.path.insert(0, str(ROOT / "src"))
from mceditlib.anvil.biome_types import BiomeTypes, LEGACY_BIOME_NAMES  # noqa: E402

PACKS = ROOT / "src" / "mceditlib" / "blocktypes" / "packs"
BASE_PACK = PACKS / "minecraft_1_21_1"
BASE_RELEASE = "1.21.1"
RELEASES_FILE = PACKS / "releases.json"
EXPORTS = ROOT / "worlds_local" / "exporter_test"

# Blocks renamed since, by their old name. Their attributes come from the block under its new name.
RENAMED = {
    "minecraft:grass": "minecraft:short_grass",  # 1.20.3
    "minecraft:grass_path": "minecraft:dirt_path",  # 1.17
    "minecraft:sign": "minecraft:oak_sign",  # 1.14
    "minecraft:wall_sign": "minecraft:oak_wall_sign",  # 1.14
}

# Climates of the biomes that 1.13 to 1.16.1 save by number and do not describe in their assets, for those not
# in biomes.csv. As (temperature, downfall).
EXTRA_LEGACY_CLIMATES = {
    168: (0.95, 0.9), 169: (0.95, 0.9),  # bamboo jungle and its hills
    170: (2.0, 0.0), 171: (2.0, 0.0), 172: (2.0, 0.0), 173: (2.0, 0.0),  # the Nether's
    174: (0.8, 0.4), 175: (0.5, 0.5),  # dripstone and lush caves
}

FULL_CUBE_MODELS = {"block/cube", "minecraft:block/cube"}

# MCEdit's render types
RENDER_INVISIBLE = 0
RENDER_ENTITY = 2
RENDER_MODEL = 3


def stateString(properties):
    return ",".join("%s=%s" % pair for pair in sorted(properties.items()))


# --- Reading a release ---

class Release(object):
    def __init__(self, name):
        self.name = name
        folder = VERSIONS / name
        with open(folder / "reports" / "blocks.json", encoding="utf-8") as f:
            self.report = json.load(f)
        try:
            with open(folder / "reports" / "registries.json", encoding="utf-8") as f:
                registries = json.load(f)
        except FileNotFoundError:
            registries = {}  # 1.13 has no registries report
        # Empty when not known, which allows any.
        self.blockEntityTypes = set(registries.get("minecraft:block_entity_type", {}).get("entries", {}))
        self.folder = folder
        self._jar = None
        self._lang = None
        self._models = {}

    @property
    def jar(self):
        if self._jar is None:
            self._jar = zipfile.ZipFile(self.folder / "client.jar")
        return self._jar

    def signature(self):
        """ What decides whether two releases can share a pack: their blocks, properties and default states. """
        blocks = []
        for name in sorted(self.report):
            block = self.report[name]
            default = next((s.get("properties", {}) for s in block["states"] if s.get("default")), {})
            blocks.append([name, block.get("properties", {}), stateString(default)])
        return json.dumps(blocks, sort_keys=True)

    def lang(self):
        if self._lang is None:
            try:
                self._lang = json.loads(self.jar.read("assets/minecraft/lang/en_us.json"))
            except KeyError:
                self._lang = {}
        return self._lang

    def readJson(self, path):
        try:
            return json.loads(self.jar.read(path))
        except (KeyError, ValueError):
            return None

    def model(self, name):
        """ A block model by name, as blockstate files and parents refer to it. """
        if name not in self._models:
            namespace, _, path = name.rpartition(":")
            self._models[name] = self.readJson("assets/%s/models/%s.json" % (namespace or "minecraft", path))
        return self._models[name]

    def blockstate(self, blockName):
        namespace, _, path = blockName.partition(":")
        return self.readJson("assets/%s/blockstates/%s.json" % (namespace, path))

    def firstModelName(self, blockName):
        state = self.blockstate(blockName)
        if state is None:
            return None
        if "variants" in state:
            variant = next(iter(state["variants"].values()), None)
        else:
            variant = next((part.get("apply") for part in state.get("multipart", ())), None)
        if isinstance(variant, list):
            variant = variant[0] if variant else None
        return variant.get("model") if variant else None

    def modelFacts(self, blockName):
        """ (whether the block's first model is a full cube, its particle texture, whether it has any shape) """
        name = self.firstModelName(blockName)
        textures = {}
        fullCube = False
        hasShape = False
        seen = set()
        foundElements = False
        while name and name not in seen:
            seen.add(name)
            model = self.model(name)
            if model is None:
                break
            for key, value in model.get("textures", {}).items():
                textures.setdefault(key, value)
            if not foundElements and (model.get("elements") is not None or name in FULL_CUBE_MODELS):
                foundElements = True
                fullCube = name in FULL_CUBE_MODELS
                hasShape = True
            name = model.get("parent")

        def plain(texture):
            # From 26.x a texture can be given as an object with the texture in "sprite".
            return texture.get("sprite") if isinstance(texture, dict) else texture

        textures = {key: plain(value) for key, value in textures.items()}
        particle = textures.get("particle")
        for _ in range(8):
            if not particle or not particle.startswith("#"):
                break
            particle = textures.get(particle[1:])
        if particle and ":" not in particle:
            particle = "minecraft:" + particle
        if particle and particle.startswith("#"):
            particle = None
        return fullCube, particle, hasShape

    def biomeDefinitions(self):
        """ Biome definitions for a pack, for releases whose client has none. """
        if any("/worldgen/biome/" in name for name in self.jar.namelist()):
            return None
        found = {}
        for path in sorted((self.folder / "reports").rglob("*.json")):
            parts = path.relative_to(self.folder / "reports").parts
            if "biome" in parts and parts[-2] == "biome":
                namespace = parts[parts.index("biome") - 2] if parts.index("biome") >= 2 else "minecraft"
                with open(path, encoding="utf-8") as f:
                    found["%s:%s" % (namespace, path.stem)] = json.load(f)
        if found:
            return found
        # Up to 1.16.1 the game describes its biomes nowhere outside its code.
        climates = {ID: (t.temperature, t.rainfall) for ID, t in BiomeTypes().types.items()}
        climates.update(EXTRA_LEGACY_CLIMATES)
        return {name: {"temperature": climates[ID][0], "downfall": climates[ID][1]}
                for ID, name in LEGACY_BIOME_NAMES.items() if ID in climates}


# --- The 1.21.1 pack ---

class BasePack(object):
    def __init__(self):
        with gzip.open(BASE_PACK / "blocks.json.gz", "rb") as f:
            self.blocks = {block["id"]: block for block in json.loads(f.read())["blocks"]}
        with open(BASE_PACK / "block_entities.json", encoding="utf-8") as f:
            self.blockEntities = json.load(f)["blockEntities"]
        self.entityByBlock = {name: entity["id"] for entity in self.blockEntities for name in entity["blocks"]}

    def template(self, name, propertyNames):
        """
        The 1.21.1 block most like a block it doesn't have: one with the same properties whose name ends the same
        way, the more of it the better, and then the shortest name.
        """
        words = name.partition(":")[2].split("_")
        best = None
        for other, block in self.blocks.items():
            if set(block["properties"]) != propertyNames:
                continue
            otherWords = other.partition(":")[2].split("_")
            shared = 0
            while shared < min(len(words), len(otherWords)) and words[-1 - shared] == otherWords[-1 - shared]:
                shared += 1
            if shared == 0:
                continue
            key = (shared, -len(other))
            if best is None or key > best[0]:
                best = key, other
        return best[1] if best else None


# --- Making a pack ---

def matchingState(block, properties):
    """ The per-state attributes of the state of a 1.21.1 block with the same properties, or of its default. """
    wanted = stateString({k: v for k, v in properties.items() if k in block["properties"]})
    default = None
    for state in block["states"]:
        if state["state"] == wanted:
            return state
        if state["state"] == block["defaultState"]:
            default = state
    return default or block["states"][0]


def entityFor(name, release, base, templateName):
    """ The block entity a block has, if any. """
    for blockName in (name, RENAMED.get(name)):
        if blockName in base.entityByBlock and (not release.blockEntityTypes
                                                or base.entityByBlock[blockName] in release.blockEntityTypes):
            return base.entityByBlock[blockName]
    path = name.partition(":")[2]
    candidates = sorted(release.blockEntityTypes, key=len, reverse=True)
    for entity in candidates:
        entityPath = entity.partition(":")[2]
        if path == entityPath or path.endswith("_" + entityPath):
            return entity
    entity = base.entityByBlock.get(templateName)
    if entity and (not release.blockEntityTypes or entity in release.blockEntityTypes):
        return entity
    return None


def makeBlock(name, reportBlock, release, base, notes):
    properties = reportBlock.get("properties", {})
    states = reportBlock["states"]
    default = next((s.get("properties", {}) for s in states if s.get("default")), states[0].get("properties", {}))
    path = name.partition(":")[2]
    descriptionId = "block.%s.%s" % tuple(name.split(":", 1))
    displayName = release.lang().get(descriptionId) or path.replace("_", " ").title()

    source = name if name in base.blocks else RENAMED.get(name)
    how = None
    if source not in base.blocks:
        source = base.template(name, set(properties))
        how = "like %s" % source if source else "from its model"
    elif source != name:
        how = "as %s" % source
    elif set(base.blocks[source]["properties"]) != set(properties) or any(
            set(values) - set(base.blocks[source]["properties"].get(key, ())) for key, values in properties.items()):
        how = "properties differ from %s" % BASE_RELEASE

    fullCube, particle, hasShape = release.modelFacts(name)
    hasBlockstate = release.blockstate(name) is not None
    entity = entityFor(name, release, base, source)

    if source is not None:
        template = base.blocks[source]
        common = dict(template["common"])
        stateAttributes = lambda props: {k: v for k, v in matchingState(template, props).items()
                                         if k not in ("state", "resourceVariant")}
        if source != name:
            common["resourcePath"] = name
            if particle:
                common["particleTexture"] = particle
    else:
        common = {
            "opaqueCube": fullCube,
            "normalCube": fullCube,
            "collidable": True,
            "opacity": 15 if fullCube else 0,
            "brightness": 0,
            "useNeighborBrightness": not fullCube,
            "materialLiquid": False,
            "materialMapColor": 0,
            "renderLayer": "solid" if fullCube else "cutout",
            "renderType": 3 if hasBlockstate else 0,
            "resourcePath": name,
            "particleTexture": particle,
            "tinted": False,
        }
        stateAttributes = lambda props: {}
    common["hasEntity"] = entity is not None
    common["hasBlockstateFile"] = hasBlockstate
    if common.get("renderType") in (RENDER_INVISIBLE, RENDER_ENTITY) and entity is None and hasShape:
        # Drawn by its block entity in 1.21.1, but this release has none and gives it a model, as 26.2 does beds.
        common["renderType"] = RENDER_MODEL
        if not how:
            how = "drawn by its model, not a block entity"

    packStates = []
    for state in states:
        props = state.get("properties", {})
        attributes = {"state": stateString(props)}
        attributes.update(stateAttributes(props))
        attributes["resourceVariant"] = stateString(props)
        packStates.append(attributes)

    if how:
        notes.append({"block": name, "attributes": how})
    block = {
        "id": name,
        "displayName": displayName,
        "descriptionId": descriptionId,
        "defaultState": stateString(default),
        "properties": properties,
        "common": common,
        "states": packStates,
    }
    return block, entity


def makePack(releases, folderName, base, dataVersions):
    release = releases[0]
    notes = []
    blocks = []
    entities = {}
    for name in sorted(release.report):
        block, entity = makeBlock(name, release.report[name], release, base, notes)
        blocks.append(block)
        if entity:
            entities.setdefault(entity, []).append(name)

    out = PACKS / folderName
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    with gzip.open(out / "blocks.json.gz", "wb", compresslevel=9) as f:
        f.write(json.dumps({"blocks": blocks}, separators=(",", ":")).encode("utf-8"))
    writeJson(out / "block_entities.json",
              {"blockEntities": [{"id": e, "blocks": sorted(b)} for e, b in sorted(entities.items())]})
    # Not problems with the pack, which is complete, but blocks to check on screen first.
    writeJson(out / "report.json", {"problems": [], "workedOut": notes})
    biomes = release.biomeDefinitions()
    if biomes:
        writeJson(out / "biomes.json", {"biomes": biomes})
    writeJson(out / "manifest.json", {
        "schemaVersion": 1,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "vanilla",
        "minecraftVersion": release.name,
        "releases": [r.name for r in releases],
        "dataVersion": dataVersions[release.name],
        "loader": "vanilla",
        "source": "data generator reports, with attributes from the 1.21.1 pack",
        "mods": [{"id": "minecraft", "version": release.name}],
        "counts": {"blocks": len(blocks), "states": sum(len(b["states"]) for b in blocks),
                   "blockEntities": len(entities), "workedOut": len(notes)},
    })
    return len(blocks), len(notes)


def exportedPack(releases, exports):
    """
    The newest pack the export mod made in the client of one of these releases, with its manifest, if it holds
    exactly the blocks and states the release's reports list. None if there is none.
    """
    for release in releases:
        expected = {name: len(block["states"]) for name, block in release.report.items()}
        for loader in ("neoforge", "fabric", "forge"):
            runs = exports / ("%s-%s" % (release.name, loader)) / "mcedit_export"
            if not runs.is_dir():
                continue
            for run in sorted(runs.iterdir(), reverse=True):
                try:
                    with open(run / "manifest.json", encoding="utf-8") as f:
                        manifest = json.load(f)
                    with gzip.open(run / "blocks.json.gz", "rb") as f:
                        blocks = [b for b in json.loads(f.read())["blocks"] if b["id"].startswith("minecraft:")]
                    with open(run / "block_entities.json", encoding="utf-8") as f:
                        entities = json.load(f)["blockEntities"]
                except (OSError, ValueError, KeyError):
                    continue
                if manifest.get("minecraftVersion") != release.name:
                    continue
                if {b["id"]: len(b["states"]) for b in blocks} != expected:
                    print("%s: %s does not match the reports, not used" % (release.name, run), flush=True)
                    continue
                return release, manifest, blocks, entities
    return None


def copyExportedPack(releases, folderName, exported, dataVersions):
    release, manifest, blocks, entities = exported
    entities = [dict(e, blocks=sorted(b for b in e["blocks"] if b.startswith("minecraft:"))) for e in entities
                if e["id"].startswith("minecraft:")]
    entities = [e for e in entities if e["blocks"]]
    out = PACKS / folderName
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    blocks = sorted(blocks, key=lambda b: b["id"])
    with gzip.open(out / "blocks.json.gz", "wb", compresslevel=9) as f:
        f.write(json.dumps({"blocks": blocks}, separators=(",", ":")).encode("utf-8"))
    writeJson(out / "block_entities.json", {"blockEntities": sorted(entities, key=lambda e: e["id"])})
    writeJson(out / "report.json", {"problems": [], "workedOut": []})
    biomes = releases[0].biomeDefinitions()
    if biomes:
        writeJson(out / "biomes.json", {"biomes": biomes})
    writeJson(out / "manifest.json", {
        "schemaVersion": 1,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "vanilla",
        "minecraftVersion": releases[0].name,
        "releases": [r.name for r in releases],
        "dataVersion": dataVersions[releases[0].name],
        "loader": "vanilla",
        "source": "the export mod in the %s client with %s %s" % (
            release.name, manifest.get("loader"), manifest.get("loaderVersion")),
        "mods": [{"id": "minecraft", "version": releases[0].name}],
        "counts": {"blocks": len(blocks), "states": sum(len(b["states"]) for b in blocks),
                   "blockEntities": len(entities), "workedOut": 0},
    })
    return len(blocks)


def writeJson(path, value):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, indent=2, sort_keys=False)
        f.write("\n")


def dataVersionsFromProfiles():
    from mceditlib.anvil.format_profile import RELEASES
    return dict(RELEASES)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", help="make only the packs that these releases use")
    parser.add_argument("--exports", type=Path, default=EXPORTS,
                        help="where exporter/test_universal.py left its exports (default: %(default)s)")
    args = parser.parse_args()

    with open(VERSIONS / "releases.json", encoding="utf-8") as f:
        names = [r["id"] for r in json.load(f) if atLeast(r["id"], "1.13")]
    names.sort(key=versionKey)
    releases = [Release(name) for name in names]
    dataVersions = dataVersionsFromProfiles()
    base = BasePack()
    baseSignature = next(r.signature() for r in releases if r.name == BASE_RELEASE)

    # Runs of releases next to each other with the same blocks.
    groups = []
    for release in releases:
        signature = release.signature()
        if groups and groups[-1][0] == signature:
            groups[-1][1].append(release)
        else:
            groups.append([signature, [release]])

    packOf = {}
    for signature, members in groups:
        if signature == baseSignature:
            folderName = BASE_PACK.name  # made by the export mod, which knows better than the reports
        else:
            folderName = "minecraft_" + members[0].name.replace(".", "_")
            if not args.only or any(r.name in args.only for r in members):
                exported = None
                if versionKey(members[0].name) > versionKey(BASE_RELEASE):
                    exported = exportedPack(members, args.exports)
                if exported:
                    count = copyExportedPack(members, folderName, exported, dataVersions)
                    print("%s: %d blocks, from the export mod in %s, for %s" % (
                        folderName, count, exported[0].name, ", ".join(r.name for r in members)), flush=True)
                else:
                    count, worked = makePack(members, folderName, base, dataVersions)
                    print("%s: %d blocks, %d worked out, for %s" % (folderName, count, worked,
                                                                     ", ".join(r.name for r in members)),
                          flush=True)
        for release in members:
            packOf[release.name] = folderName

    writeJson(RELEASES_FILE, {"packs": packOf})
    print("%d releases, %d packs" % (len(packOf), len(set(packOf.values()))))


if __name__ == "__main__":
    sys.exit(main())
