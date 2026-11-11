"""
    worldfolder
"""
from collections import defaultdict
import logging
from mceditlib.exceptions import ChunkNotPresent

log = logging.getLogger(__name__)

from mceditlib.pc.regionfile import RegionFile
import os


# Region files that are not for blocks work just like those that are, so the code that looks after them all is
# told that they are in dimensions with these on the end of the name. Whatever looks after a world's chunks and its
# undo history then does the same for all of them.
ENTITY_STORE = "@entities"
POI_STORE = "@poi"
STORE_FOLDERS = {ENTITY_STORE: "entities", POI_STORE: "poi"}


def splitStore(dimName):
    """ Return the name of the dimension, and the folder in it, for a dimension name that may name a store. """
    for store, folder in STORE_FOLDERS.items():
        if dimName.endswith(store):
            return dimName[:-len(store)], folder
    return dimName, "region"


def isStoreName(dimName):
    return splitStore(dimName)[1] != "region"


# Since Minecraft 26.1 the game's own dimensions are kept in dimensions/minecraft/ instead of the world folder,
# DIM-1 and DIM1. The rest of MCEdit still calls them by their old names.
DIMENSIONS_FOLDER_LAYOUT = {
    "": "dimensions/minecraft/overworld",
    "DIM-1": "dimensions/minecraft/the_nether",
    "DIM1": "dimensions/minecraft/the_end",
}


def usesDimensionsFolder(filename):
    """ Whether the world in the given folder keeps the game's own dimensions in dimensions/minecraft/. """
    return os.path.isdir(os.path.join(filename, *DIMENSIONS_FOLDER_LAYOUT[""].split("/")))


class AnvilWorldFolder(object):
    def __init__(self, filename, create=False, readonly=False, externalChunks=False):
        '''

        :type filename: str or unicode
        :type create: bool
        :param externalChunks: Whether chunks too large for a region file can be kept in files of their own.
        '''
        if not os.path.exists(filename):
            if create:
                os.mkdir(filename)
            else:
                raise IOError("World not found")

        elif not os.path.isdir(filename):
            raise IOError("AnvilWorldFolder: Not a folder: %s" % filename)

        self.filename = filename
        self.readonly = readonly
        self.externalChunks = externalChunks
        self.regionFiles = {}
        # Where each dimension is, for those not in the folder of the same name. A folder that holds an undo
        # revision keeps every dimension under its plain name.
        self.dimensionFolders = DIMENSIONS_FOLDER_LAYOUT if not create and usesDimensionsFolder(filename) else {}
        self._dimensionNames = set(self._findDimensions())
        self._regionPositionsByDim = defaultdict(set)
        self._regionPositionsByDim.update({dimName: set(self._listRegionFiles(dimName))
                                           for dimName in self._dimensionNames})


    def __repr__(self):
        return "AnvilWorldFolder(%s)" % repr(self.filename)

    # --- File paths ---

    def getFilePath(self, path):
        path = path.replace("/", os.path.sep)
        path = path.lstrip(os.path.sep)
        return os.path.join(self.filename, path)

    def getFolderPath(self, path):
        path = self.getFilePath(path)
        if not os.path.exists(path) and not self.readonly:
            os.makedirs(path)

        return path

    # --- Ordinary files ---

    def containsFile(self, path):
        return os.path.exists(self.getFilePath(path))

    def _requireWritable(self):
        if self.readonly:
            raise IOError("World %s is opened read only." % self.filename)

    def deleteFile(self, path):
        self._requireWritable()
        return os.unlink(self.getFilePath(path))

    def readFile(self, path):
        with open(self.getFilePath(path), "rb") as f:
            return f.read()

    def writeFile(self, path, data):
        self._requireWritable()
        path = self.getFilePath(path)
        dirpath = os.path.dirname(path)
        if not os.path.exists(dirpath):
            os.makedirs(dirpath)
        with open(path, "wb") as f:
            f.write(data)

    def listFolder(self, path):
        """
        Returns the path of each file or folder in the given folder. All paths returned
        are relative to the world folder and use '/' as the path separator.

        :param path: Folder to list
        :type path: unicode
        :return: List of file/folder paths
        :rtype: Iterator[unicode]
        """
        folder = self.getFolderPath(path)
        if not os.path.isdir(folder):  # only when read only, since otherwise it was just created
            return
        for filename in os.listdir(folder):
            yield "%s/%s" % (path, filename)

    def listAllFiles(self):
        for root, dirs, files in os.walk(self.filename):
            root = os.path.relpath(root, self.filename).replace(os.path.sep, "/")
            for filename in files:
                if filename.endswith((".mca", ".mcr", ".mcc")) or filename.startswith("##MCEDIT"):
                    continue
                else:
                    yield filename if root == "." else "%s/%s" % (root, filename)

    # --- Region files and dimensions ---

    def _findDimensions(self):
        for found in self._findDimensionFolders():
            dimName, _ = splitStore(found)
            store = found[len(dimName):]
            for name, folder in self.dimensionFolders.items():
                if dimName == folder:
                    dimName = name
            yield dimName + store

    def _findDimensionFolders(self):
        yield ""
        worldDirs = os.listdir(self.filename)
        for dirname in worldDirs:
            if dirname.startswith("##"):
                continue
            try:
                if os.path.exists(os.path.join(self.filename, dirname, "region")):
                    log.debug("Found dimension {0}".format(dirname))
                    yield dirname
            except Exception as e:
                log.info(u"Error detecting dimension {0}: {1}".format(dirname, e))
                continue

        # Since Minecraft 1.16, custom dimensions are in dimensions/<namespace>/<path>/, and the path
        # may have any number of folders.
        for root, dirs, _ in os.walk(os.path.join(self.filename, "dimensions")):
            if "region" in dirs:
                yield os.path.relpath(root, self.filename).replace(os.path.sep, "/")

        # Since Minecraft 1.17, some of what is in a chunk is kept in region files of its own.
        for root, dirs, _ in os.walk(self.filename):
            dirs[:] = [d for d in dirs if not d.startswith("##")]
            for store, folder in STORE_FOLDERS.items():
                if folder in dirs:
                    dimName = os.path.relpath(root, self.filename).replace(os.path.sep, "/")
                    yield ("" if dimName == "." else dimName) + store

    def listDimensions(self):
        return iter(self._dimensionNames)


    def getRegionFolderPath(self, dimName):
        dimName, folder = splitStore(dimName)
        dimName = self.dimensionFolders.get(dimName, dimName)
        if dimName == "":
            return self.getFolderPath(folder)
        else:
            return self.getFolderPath("%s/%s" % (dimName, folder))

    def _listRegionFiles(self, dimName):
        regionDir = self.getRegionFolderPath(dimName)
        if not os.path.isdir(regionDir):  # only when read only, since otherwise it was just created
            return

        regionFiles = os.listdir(regionDir)
        for filename in regionFiles:
            if filename.endswith(".mcc"):  # holds one oversized chunk, found through its region file
                continue
            bits = filename.split('.')
            if len(bits) < 4 or bits[0] != 'r' or bits[3] != "mca":
                log.info("Unexpected file in region folder: %s", filename)
                continue

            try:
                rx, rz = map(int, bits[1:3])
            except ValueError:
                log.info("Unexpected file in region folder: %s", filename)
                continue

            yield rx, rz

    def getRegionFilename(self, rx, rz, dimName):
        return os.path.join(self.getRegionFolderPath(dimName), "r.%s.%s.%s" % (rx, rz, "mca"))

    def getRegionFile(self, rx, rz, dimName):
        """

        :type rx: int or dtype
        :type rz: int or dtype
        :type dimName: unicode or str
        :return:
        :rtype: RegionFile
        """
        regionFile = self.regionFiles.get((rx, rz, dimName))
        if regionFile:
            return regionFile
        path = self.getRegionFilename(rx, rz, dimName)
        if not os.path.exists(path):
            if self.readonly:
                return None
            self._dimensionNames.add(dimName)
            self._regionPositionsByDim[dimName].add((rx, rz))
        try:
            regionFile = RegionFile(path, self.readonly, allowExternal=self.externalChunks)
        except Exception:
            log.exception("Failed to open region file.")
            return None
        self.regionFiles[rx, rz, dimName] = regionFile
        return regionFile

    def getRegionForChunk(self, cx, cz, dimName):
        rx = cx >> 5
        rz = cz >> 5
        return self.getRegionFile(rx, rz, dimName)

    def close(self):
        self.regionFiles = {}

    # --- Chunks and chunk listing ---

    def chunkCount(self, dimName):
        """

        :param dimName: Name of dimension
        :type dimName: unicode or str
        :return: Number of chunks in given dimension
        :rtype: int
        """
        count = 0
        for rx, rz in self._regionPositionsByDim[dimName]:
            regionFile = self.getRegionFile(rx, rz, dimName)
            if regionFile is None:
                continue

            count += regionFile.chunkCount
        return count

    def chunkPositions(self, dimName):
        """
        Iterate over chunk positions
        :type dimName: unicode or str
        :return: iterator
        :rtype: iterator [(int, int)]
        """
        for rx, rz in set(self._regionPositionsByDim[dimName]):
            regionFile = self.getRegionFile(rx, rz, dimName)
            if regionFile is None:
                continue

            # The game can leave empty region files. Only saving should ever change a world, so they stay.
            for cx, cz in regionFile.chunkPositions():
                cx += rx << 5
                cz += rz << 5
                yield (cx, cz)

    def containsChunk(self, cx, cz, dimName):
        rx = cx >> 5
        rz = cz >> 5
        if (rx, rz) not in self._regionPositionsByDim[dimName]:
            return False

        regionFile = self.getRegionForChunk(cx, cz, dimName)
        return regionFile is not None and regionFile.containsChunk(cx, cz)

    def deleteChunk(self, cx, cz, dimName):
        self._requireWritable()
        rx = cx >> 5
        rz = cz >> 5
        rf = self.getRegionFile(rx, rz, dimName)
        if rf:
            rf.deleteChunk(cx & 0x1f, cz & 0x1f)
            if rf.chunkCount == 0:
                del self.regionFiles[rx, rz, dimName]
                self._regionPositionsByDim[dimName].discard((rx, rz))
                os.unlink(rf.path)

    def readChunkBytes(self, cx, cz, dimName):
        if not self.containsChunk(cx, cz, dimName):
            raise ChunkNotPresent((cx, cz))
        return self.getRegionForChunk(cx, cz, dimName).readChunkBytes(cx, cz)

    def writeChunkBytes(self, cx, cz, dimName, data):
        self._requireWritable()
        self.getRegionForChunk(cx, cz, dimName).writeChunkBytes(cx, cz, data)

    def copyChunkFrom(self, sourceFolder, cx, cz, dimName):
        """
        Copy chunk from another source folder without decompression
        :param sourceFolder:
        :type sourceFolder: AnvilWorldFolder
        :param cx:
        :type cx: int
        :param cz:
        :type cz: int
        :param dimName:
        :type dimName: unicode
        :return:
        :rtype:
        """
        self._requireWritable()
        data, fmt = sourceFolder.getRegionForChunk(cx, cz, dimName).readChunkCompressed(cx, cz)
        self.getRegionForChunk(cx, cz, dimName).writeChunkCompressed(cx, cz, data, fmt)
