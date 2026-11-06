"""
    ${NAME}
"""

import json
import logging
import os
import zipfile
import re

log = logging.getLogger(__name__)

class ResourceNotFound(KeyError):
    pass

class ResourceLoader(object):
    def __init__(self, fallbackZipFile):
        super(ResourceLoader, self).__init__()
        self.zipFiles = []
        self.modsDirs = []
        self.fallbackZipFile = zipfile.ZipFile(fallbackZipFile)
        self.extraBiomes = {}

    def addZipFile(self, zipPath):
        try:
            zf = zipfile.ZipFile(zipPath)
        except zipfile.BadZipfile as e:
            raise IOError("Could not read %s as a zip file." % zipPath)
        self.zipFiles.append(zf)

    def addBlockPack(self, pack):
        """
        Add the assets of a block pack to the search path, and any biomes it describes.

        :type pack: mceditlib.blocktypes.block_pack.BlockPack
        """
        self.zipFiles.append(pack.files)
        for name, definition in pack.biomes.items():
            self.extraBiomes.setdefault(name, definition)

    def addModsFolder(self, modsDir):
        modsDir = os.path.normpath(modsDir)
        if modsDir in self.modsDirs:
            return
        self.modsDirs.append(modsDir)

        for modName in os.listdir(modsDir):
            mod = os.path.join(modsDir, modName)
            if not os.path.isfile(mod):
                continue
            if not zipfile.is_zipfile(mod):
                continue

            try:
                self.addZipFile(mod)
            except Exception as e:
                log.exception("Failed to add mod %s to resource loader.", modName)
                continue

    def openStream(self, path, fallback=False):
        if fallback:
            try:
                stream = self.fallbackZipFile.open(path)
            except KeyError:  # Not found in zip file
                raise ResourceNotFound("Resource %s not found in search path" % path)
            return stream

        for zipFile in self.zipFiles:
            try:
                stream = zipFile.open(path)
                break
            except KeyError:  # Not found in zip file
                continue
        else:
            raise ResourceNotFound("Resource %s not found in search path" % path)

        return stream

    def blockModelPaths(self):
        for zf in self.zipFiles:
            for name in zf.namelist():
                if re.match(r'assets/[^/]+/models/block', name):
                    yield name

    def blockTexturePaths(self):
        seen = set()
        for zf in self.zipFiles:
            for name in zf.namelist():
                if name in seen:
                    continue
                seen.add(name)
                if re.match(r'assets/[^/]+/textures/blocks?/.*\.png$', name):
                    yield zf.filename, name

    def biomeDefinitions(self):
        """
        Yield the name and contents of each biome definition in the search path. The first zip file
        that has a biome is the one to use, like any other resource.
        """
        seen = set(self.extraBiomes)
        for name, definition in self.extraBiomes.items():
            yield name, definition

        for zf in self.zipFiles:
            for path in zf.namelist():
                match = re.match(r'data/([^/]+)/worldgen/biome/(.+)\.json$', path)
                if match is None:
                    continue
                name = "%s:%s" % match.groups()
                if name in seen:
                    continue
                seen.add(name)
                try:
                    yield name, json.loads(zf.read(path))
                except ValueError as e:
                    log.warning("Could not read biome %s from %s: %r", name, zf.filename, e)
