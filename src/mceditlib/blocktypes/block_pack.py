"""
    block_pack

Read a block pack: the description of a game's blocks written by the export mod in exporter/. A pack is
a folder or a zip file, and may also hold the game assets that its blocks need.
"""
import gzip
import io
import json
import logging
import os
import zipfile

log = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSIONS = (1,)

MANIFEST_FILE = "manifest.json"
BLOCKS_FILE = "blocks.json.gz"
BLOCK_ENTITIES_FILE = "block_entities.json"
REPORT_FILE = "report.json"
BIOMES_FILE = "biomes.json"

MINECRAFT_NAMESPACE = "minecraft"


class BlockPackError(IOError):
    """
    The folder or file is not a block pack this version can read.
    """


class FolderFiles(object):
    """
    The files of a folder, with the same methods as a zip file. Names are relative and use '/'.

    The list of files is read once. Models are looked for in many places, and trying to open a file that
    isn't there can take milliseconds, which adds up to many seconds for a large pack.
    """

    def __init__(self, folder):
        self.filename = folder
        self._names = None

    def open(self, name):
        if name not in self._knownNames():
            raise KeyError(name)
        try:
            return open(os.path.join(self.filename, name), "rb")
        except EnvironmentError:
            raise KeyError(name) from None

    def read(self, name):
        with self.open(name) as f:
            return f.read()

    def _knownNames(self):
        if self._names is None:
            self._names = set()
            for root, _, files in os.walk(self.filename):
                for filename in files:
                    path = os.path.relpath(os.path.join(root, filename), self.filename)
                    self._names.add(path.replace(os.path.sep, "/"))
        return self._names

    def namelist(self):
        return sorted(self._knownNames())


class ZipFiles(object):
    """
    The files of a zip file. Zipping a folder often puts everything inside a folder of that name, so if
    the zip has its manifest one folder down, that folder is what's used.
    """

    def __init__(self, path):
        self.filename = path
        try:
            self.zipFile = zipfile.ZipFile(path)
        except (EnvironmentError, zipfile.BadZipFile) as e:
            raise BlockPackError("Could not read %s: %r" % (path, e)) from e

        self.prefix = ""
        names = self.zipFile.namelist()
        if MANIFEST_FILE not in names:
            for name in names:
                parts = name.split("/")
                if len(parts) == 2 and parts[1] == MANIFEST_FILE:
                    self.prefix = parts[0] + "/"
                    break

    def open(self, name):
        return self.zipFile.open(self.prefix + name)

    def read(self, name):
        return self.zipFile.read(self.prefix + name)

    def namelist(self):
        return [name[len(self.prefix):] for name in self.zipFile.namelist()
                if name.startswith(self.prefix) and not name.endswith("/")]


class BlockPack(object):
    def __init__(self, path):
        """
        Load a block pack from a folder or zip file. The manifest is written last, so its presence
        also means that the export finished.

        :type path: str
        """
        self.path = path
        self.folder = path
        self.files = FolderFiles(path) if os.path.isdir(path) else ZipFiles(path)
        self.manifest = self._loadJson(MANIFEST_FILE)

        schemaVersion = self.manifest.get("schemaVersion")
        if schemaVersion not in SUPPORTED_SCHEMA_VERSIONS:
            raise BlockPackError("%s has block pack schema version %s, but this version of MCEdit reads %s." %
                                 (path, schemaVersion, SUPPORTED_SCHEMA_VERSIONS))

        try:
            with gzip.open(io.BytesIO(self.files.read(BLOCKS_FILE)), "rb") as f:
                self.blocks = json.loads(f.read())["blocks"]
        except (OSError, KeyError, ValueError) as e:
            raise BlockPackError("Could not read %s in %s: %r" % (BLOCKS_FILE, path, e)) from e

        self.blockEntities = self._loadJson(BLOCK_ENTITIES_FILE)["blockEntities"]
        self.problems = self._loadJson(REPORT_FILE)["problems"]

    def _loadJson(self, name):
        try:
            return json.loads(self.files.read(name))
        except (OSError, KeyError, ValueError) as e:
            raise BlockPackError("Could not read %s in %s: %r" % (name, self.path, e)) from e

    @property
    def dataVersion(self):
        return self.manifest["dataVersion"]

    @property
    def mods(self):
        """
        The mods this pack has blocks from, as a dict of ID to version. If the manifest does not list
        them, they are taken from the namespaces of the blocks and their versions are None.
        """
        listed = self.manifest.get("mods")
        if listed is not None:
            return {mod["id"]: mod.get("version") for mod in listed if mod["id"] != MINECRAFT_NAMESPACE}

        namespaces = {block["id"].partition(":")[0] for block in self.blocks}
        return {namespace: None for namespace in namespaces if namespace != MINECRAFT_NAMESPACE}

    @property
    def biomes(self):
        """ Definitions of biomes that the game's assets don't have, as a dict by name. Optional in a pack. """
        try:
            return json.loads(self.files.read(BIOMES_FILE))["biomes"]
        except (KeyError, OSError):
            return {}
        except ValueError as e:
            log.warning("Could not read %s in %s: %r", BIOMES_FILE, self.path, e)
            return {}

    def states(self):
        """
        Iterate over every block state as a complete dict of attributes. Adds `internalName`,
        `blockState` as MCEdit writes it, `displayName`, `unlocalizedName` and `defaultState`.
        """
        for block in self.blocks:
            for state in block["states"]:
                attributes = dict(block["common"])
                attributes.update(state)
                name = attributes.pop("state")
                attributes["internalName"] = block["id"]
                attributes["blockState"] = "[%s]" % name if name else ""
                attributes["displayName"] = block["displayName"]
                attributes["unlocalizedName"] = block["descriptionId"]
                attributes["defaultState"] = name == block["defaultState"]
                yield attributes
