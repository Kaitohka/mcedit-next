"""
    RegionFile class.

    Reads and writes chunks to *.mcr* (Minecraft Region)
    and *.mca* (Minecraft Anvil Region) files
"""
import logging
import os
import struct
import zlib
import time

import numpy

from mceditlib import nbt
from mceditlib.exceptions import ChunkNotPresent, RegionFormatError

REGION_DEBUG = 5
logging.addLevelName("REGION_DEBUG", REGION_DEBUG)

log = logging.getLogger(__name__)

# Disable region debugging today.
log.setLevel(logging.DEBUG)

def region_debug(msg, *args, **kwargs):
    return log.log(REGION_DEBUG, msg, *args, **kwargs)

__author__ = 'Rio'


def deflate(data):
    return zlib.compress(data, 2)


def inflate(data):
    return zlib.decompress(data)


LZ4_BLOCK_MAGIC = b"LZ4Block"
LZ4_BLOCK_HEADER_SIZE = len(LZ4_BLOCK_MAGIC) + 13
LZ4_METHOD_RAW = 0x10
LZ4_METHOD_LZ4 = 0x20


def decompressLZ4Blocks(data):
    """
    Decompress the block stream written by lz4-java's LZ4BlockOutputStream, which is what Minecraft
    uses for LZ4 chunks. It is not the standard LZ4 frame format. Each block has a magic number, a
    token, and little-endian compressed size, decompressed size and checksum. A block with a
    decompressed size of zero ends the stream. The checksums are not verified.
    """
    import lz4.block

    output = []
    position = 0
    while position < len(data):
        if data[position:position + len(LZ4_BLOCK_MAGIC)] != LZ4_BLOCK_MAGIC:
            raise RegionFormatError("Bad magic number in LZ4 chunk at offset %d" % position)
        token = data[position + len(LZ4_BLOCK_MAGIC)]
        compressedSize, decompressedSize, _ = struct.unpack_from("<III", data, position + len(LZ4_BLOCK_MAGIC) + 1)
        position += LZ4_BLOCK_HEADER_SIZE
        if decompressedSize == 0:
            break

        block = data[position:position + compressedSize]
        position += compressedSize
        method = token & 0xf0
        if method == LZ4_METHOD_RAW:
            output.append(block)
        elif method == LZ4_METHOD_LZ4:
            output.append(lz4.block.decompress(block, uncompressed_size=decompressedSize))
        else:
            raise RegionFormatError("Unknown LZ4 block method 0x%x" % method)

    return b"".join(output)


class RegionFile(object):
    SECTOR_BYTES = 4096
    CHUNK_HEADER_SIZE = 5
    VERSION_GZIP = 1
    VERSION_DEFLATE = 2
    VERSION_NONE = 3
    VERSION_LZ4 = 4
    VERSION_CUSTOM = 127
    EXTERNAL_FLAG = 0x80

    def __init__(self, path, readonly=False, allowExternal=False):
        """
        Opening a file that exists changes nothing in it, whatever state it is in. Any repairs it
        needs wait until something is written to it.

        :param path: The path of the file
        :param readonly: Whether to refuse to write to it
        :param allowExternal: Whether a chunk too large for a region file can be kept in a file of its own. The
            game only understands this since Minecraft 1.15.
        """
        self.path = path
        self.readonly = readonly
        self.allowExternal = allowExternal
        self._needsRepair = False
        newFile = False
        if not os.path.exists(path):
            if readonly:
                raise IOError("Region file not found: %r" % path)
            open(path, "w").close()
            newFile = True

        filesize = os.path.getsize(path)
        mode = "rb" if readonly else "rb+"
        with open(self.path, mode) as f:

            if newFile:
                filesize = self.SECTOR_BYTES * 2
                f.truncate(filesize)
                self.offsets = numpy.zeros(self.SECTOR_BYTES//4, dtype='>u4')
                self.modTimes = numpy.zeros(self.SECTOR_BYTES//4, dtype='>u4')
            else:

                f.seek(0)
                offsetsData = f.read(self.SECTOR_BYTES)
                modTimesData = f.read(self.SECTOR_BYTES)

                if len(offsetsData) < self.SECTOR_BYTES or len(modTimesData) < self.SECTOR_BYTES:
                    # An empty or truncated file, which is fixed when something is written to it.
                    if offsetsData:  # the game leaves empty files, which are nothing to worry about
                        log.warning("Region file %s has an incomplete header, treating it as empty", path)
                    offsetsData = bytes(self.SECTOR_BYTES)
                    modTimesData = bytes(self.SECTOR_BYTES)

                self.offsets = numpy.frombuffer(offsetsData, dtype='>u4').copy()
                self.modTimes = numpy.frombuffer(modTimesData, dtype='>u4').copy()

            # A file need not be a whole number of sectors, so round up to count every byte.
            self.freeSectors = [True] * max(2, (filesize + self.SECTOR_BYTES - 1) // self.SECTOR_BYTES)
            self.freeSectors[0:2] = False, False

            if not newFile:
                needsRepair = False

                # Populate freeSectors table
                for offset in self.offsets:
                    sector = offset >> 8
                    count = offset & 0xff

                    for i in range(sector, sector + count):
                        if i >= len(self.freeSectors):
                            log.warning("Region file offset table points to sector %d (past the end of the file)", i)
                            needsRepair = True
                            break
                        if self.freeSectors[i] is False:
                            needsRepair = True
                        self.freeSectors[i] = False

                if needsRepair:
                    # A chunk that cannot be read reports its own error when it is asked for.
                    log.warning("Region file %s needs repair, which will wait until it is written to", path)
                    self._needsRepair = True

                region_debug("Found region file %s with %d/%d sectors used and %d chunks present",
                          os.path.basename(path), self.usedSectors, self.sectorCount, self.chunkCount)
            else:
                region_debug("Created new region file %s", os.path.basename(path))

    def __repr__(self):
        return "%s(\"%s\")" % (self.__class__.__name__, self.path)

    @property
    def usedSectors(self):
        return len(self.freeSectors) - sum(self.freeSectors)

    @property
    def sectorCount(self):
        return len(self.freeSectors)

    @property
    def chunkCount(self):
        return numpy.sum(self.offsets > 0)

    def chunkPositions(self):
        for index, offset in enumerate(self.offsets):
            if offset:
                cx = index & 0x1f
                cz = index >> 5
                yield (cx, cz)

    def repair(self):
        """
        Fix the following problems with the region file:
         - remove offset table entries pointing past the end of the file
         - remove entries that overlap other entries
         - relocate offsets for chunks whose xPos,yPos don't match

        A chunk is not removed for being unreadable alone, as it may be in a form that needs a mod
        to read it. The game can decide what to do with it.
        """
        self._needsRepair = False
        lostAndFound = {}
        _freeSectors = [True] * len(self.freeSectors)
        _freeSectors[0] = _freeSectors[1] = False
        deleted = 0
        recovered = 0
        log.info("Beginning repairs on {file} ({chunks} chunks)".format(file=os.path.basename(self.path), chunks=sum(self.offsets > 0)))
        for index, offset in enumerate(self.offsets):
            if offset:
                cx = index & 0x1f
                cz = index >> 5
                sectorStart = offset >> 8
                sectorCount = offset & 0xff
                try:

                    if sectorStart + sectorCount > len(self.freeSectors):
                        raise RegionFormatError("Offset {start}:{end} ({offset}) at index {index} pointed outside of "
                                                "the file".format(start=sectorStart, end=sectorStart + sectorCount, index=index, offset=offset))

                    overlaps = False
                    for i in range(sectorStart, sectorStart + sectorCount):
                        if _freeSectors[i] is False:
                            overlaps = True
                        _freeSectors[i] = False

                    try:
                        data = self.readChunkBytes(cx, cz)
                        chunkTag = nbt.load(buf=data)
                        # Since Minecraft 1.18 the chunk's tags are in its root tag.
                        lev = chunkTag["Level"] if "Level" in chunkTag else chunkTag
                        xPos = lev["xPos"].value & 0x1f
                        zPos = lev["zPos"].value & 0x1f
                    except Exception as e:
                        if overlaps:
                            raise
                        log.info("Could not read chunk {found}, leaving it as it is ({exc})".format(found=(cx, cz), exc=e))
                        continue

                    if xPos != cx or zPos != cz:
                        lostAndFound[xPos, zPos] = data
                        raise RegionFormatError("Chunk {found} was found in the slot reserved for {expected}".format(found=(xPos, zPos), expected=(cx, cz)))

                    if overlaps:
                        raise RegionFormatError("Chunk {found} (in slot {expected}) has overlapping sectors with another chunk!".format(found=(xPos, zPos), expected=(cx, cz)))

                except Exception as e:
                    log.info("Unexpected chunk data at sector {sector} ({exc})".format(sector=sectorStart, exc=e))
                    self._setOffset(cx, cz, 0)
                    deleted += 1

        for cPos, foundData in lostAndFound.items():
            cx, cz = cPos
            if self._getOffset(cx, cz) == 0:
                log.info("Found chunk {found} and its slot is empty, recovering it".format(found=cPos))
                self.writeChunkBytes(cx, cz, foundData)
                recovered += 1

        self.freeSectors = _freeSectors
        log.info("Repair complete. Removed {0} chunks, recovered {1} chunks, net {2}".format(deleted, recovered, recovered - deleted))

    def readChunkCompressed(self, cx, cz):
        """
        Read a chunk and return its compression type and the compressed data as a (data, fmt) tuple
        """
        cx &= 0x1f
        cz &= 0x1f
        offset = self._getOffset(cx, cz)
        if offset == 0:
            raise ChunkNotPresent((cx, cz))

        sectorStart = offset >> 8
        numSectors = offset & 0xff
        if numSectors == 0:
            raise ChunkNotPresent((cx, cz))

        if sectorStart + numSectors > len(self.freeSectors):
            raise ChunkNotPresent((cx, cz))

        with open(self.path, "rb") as f:
            f.seek(sectorStart * self.SECTOR_BYTES)
            data = f.read(numSectors * self.SECTOR_BYTES)
        if len(data) < 5:
            raise RegionFormatError("Chunk %s data is only %d bytes long (expected 5)" % ((cx, cz), len(data)))

        # region_debug("REGION LOAD {0},{1} sector {2}".format(cx, cz, sectorStart))

        length = struct.unpack_from(">I", data)[0]
        fmt = struct.unpack_from("B", data, 4)[0]
        data = data[5:length + 4]  # the length counts the format byte
        return data, fmt

    def _externalChunkPath(self, cx, cz):
        """
        A chunk too large for its region file is kept in a file of its own beside it. The name has the
        chunk's world position, so it is worked out from this region's position.
        """
        folder, filename = os.path.split(self.path)
        _, rx, rz, _ = filename.split(".")
        return os.path.join(folder, "c.%d.%d.mcc" % ((int(rx) << 5) + (cx & 0x1f), (int(rz) << 5) + (cz & 0x1f)))

    def readChunkBytes(self, cx, cz):
        """

        :param cx:
        :type cx:
        :param cz:
        :type cz:
        :return:
        :rtype: bytes
        """
        data, fmt = self.readChunkCompressed(cx, cz)
        if data is None:
            return None

        if fmt & self.EXTERNAL_FLAG:
            fmt &= ~self.EXTERNAL_FLAG
            path = self._externalChunkPath(cx, cz)
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except EnvironmentError as e:
                raise RegionFormatError("Chunk %s is stored in %s, which could not be read: %s"
                                        % ((cx, cz), os.path.basename(path), e)) from e

        if fmt == self.VERSION_GZIP:
            return nbt.gunzip(data)
        if fmt == self.VERSION_DEFLATE:
            return inflate(data)
        if fmt == self.VERSION_NONE:
            return bytes(data)
        if fmt == self.VERSION_LZ4:
            return decompressLZ4Blocks(data)
        if fmt == self.VERSION_CUSTOM:
            nameLength = struct.unpack_from(">H", data)[0]
            name = bytes(data[2:2 + nameLength]).decode("utf-8", "replace")
            raise RegionFormatError("Chunk %s uses the compression scheme %r, which needs the mod that added it"
                                    % ((cx, cz), name))

        raise RegionFormatError("Unknown compress format: {0}".format(fmt))

    def writeChunkBytes(self, cx, cz, uncompressedData):
        data = deflate(uncompressedData)
        self.writeChunkCompressed(cx, cz, data, self.VERSION_DEFLATE)

    def _requireWritable(self):
        if self.readonly:
            raise IOError("Region file %s is opened read only." % self.path)

    def _prepareToWrite(self):
        """ Do what opening the file put off, now that the file is being changed anyway. """
        self._requireWritable()
        size = os.path.getsize(self.path)
        if size < self.SECTOR_BYTES * 2 or size & 0xfff:
            with open(self.path, "rb+") as f:
                f.truncate(max(self.SECTOR_BYTES * 2, (size + 0xfff) & ~0xfff))
                f.seek(0)
                f.write(self.offsets.tobytes())
                f.write(self.modTimes.tobytes())
        if self._needsRepair:
            self.repair()

    def _removeExternalChunk(self, cx, cz):
        path = self._externalChunkPath(cx, cz)
        if os.path.exists(path):
            os.unlink(path)

    def writeChunkCompressed(self, cx, cz, data, format):
        self._prepareToWrite()
        cx &= 0x1f
        cz &= 0x1f
        offset = self._getOffset(cx, cz)
        sectorNumber = offset >> 8
        sectorsAllocated = offset & 0xff
        sectorsNeeded = (len(data) + self.CHUNK_HEADER_SIZE) // self.SECTOR_BYTES + 1
        if sectorsNeeded >= 256:
            if not self.allowExternal:
                err = RegionFormatError("Cannot save chunk %s with compressed length %s (exceeds 1 megabyte)" %
                                        ((cx, cz), len(data)))
                err.chunkPosition = cx, cz
                raise err

            # The chunk goes in a file of its own, with only its format left in the region file.
            with open(self._externalChunkPath(cx, cz), "wb") as f:
                f.write(data)
            data = b""
            format |= self.EXTERNAL_FLAG
            sectorsNeeded = 1
        elif self.allowExternal:
            self._removeExternalChunk(cx, cz)

        if sectorNumber != 0 and sectorsAllocated >= sectorsNeeded:
            region_debug("REGION SAVE {0},{1} rewriting {2}b".format(cx, cz, len(data)))
            self.writeSector(sectorNumber, data, format)
        else:
            # we need to allocate new sectors

            # mark the sectors previously used for this chunk as free
            for i in range(sectorNumber, sectorNumber + sectorsAllocated):
                self.freeSectors[i] = True

            runLength = 0
            runStart = 0
            try:
                runStart = self.freeSectors.index(True)

                for i in range(runStart, len(self.freeSectors)):
                    if runLength:
                        if self.freeSectors[i]:
                            runLength += 1
                        else:
                            runLength = 0
                    elif self.freeSectors[i]:
                        runStart = i
                        runLength = 1

                    if runLength >= sectorsNeeded:
                        break
            except ValueError:
                pass

            # we found a free space large enough
            if runLength >= sectorsNeeded:
                region_debug("REGION SAVE {0},{1}, reusing {2}b".format(cx, cz, len(data)))
                sectorNumber = runStart
                self._setOffset(cx, cz, sectorNumber << 8 | sectorsNeeded)
                self.writeSector(sectorNumber, data, format)
                self.freeSectors[sectorNumber:sectorNumber + sectorsNeeded] = [False] * sectorsNeeded

            else:
                # no free space large enough found -- we need to grow the
                # file

                region_debug("REGION SAVE {0},{1}, growing by {2}b".format(cx, cz, len(data)))

                with open(self.path, "rb+") as f:
                    f.seek(0, 2)
                    filesize = f.tell()

                    sectorNumber = len(self.freeSectors)

                    assert sectorNumber * self.SECTOR_BYTES == filesize

                    filesize += sectorsNeeded * self.SECTOR_BYTES
                    f.truncate(filesize)

                self.freeSectors += [False] * sectorsNeeded

                self._setOffset(cx, cz, sectorNumber << 8 | sectorsNeeded)
                self.writeSector(sectorNumber, data, format)

        self.setTimestamp(cx, cz)

    def writeSector(self, sectorNumber, data, format):
        self._requireWritable()
        with open(self.path, "rb+") as f:
            region_debug("REGION: Writing sector {0}".format(sectorNumber))

            f.seek(sectorNumber * self.SECTOR_BYTES)
            f.write(struct.pack(">I", len(data) + 1))  # // chunk length
            f.write(struct.pack("B", format))  # // chunk version number
            f.write(data)  # // chunk data
            # f.flush()

    def containsChunk(self, cx, cz):
        return self._getOffset(cx, cz) != 0

    def _getOffset(self, cx, cz):
        cx &= 0x1f
        cz &= 0x1f
        return self.offsets[cx + cz * 32]

    def _setOffset(self, cx, cz, offset):
        self._requireWritable()
        cx &= 0x1f
        cz &= 0x1f
        self.offsets[cx + cz * 32] = offset
        with open(self.path, "rb+") as f:
            f.seek(0)
            f.write(self.offsets.tobytes())

    def deleteChunk(self, cx, cz):
        self._prepareToWrite()
        if self.allowExternal:
            self._removeExternalChunk(cx, cz)
        offset = self._getOffset(cx, cz)
        sectorNumber = offset >> 8
        sectorsAllocated = offset & 0xff
        for i in range(sectorNumber, sectorNumber + sectorsAllocated):
            self.freeSectors[i] = True

        self._setOffset(cx, cz, 0)

    def getTimestamp(self, cx, cz):
        cx &= 0x1f
        cz &= 0x1f
        return self.modTimes[cx + cz * 32]

    def setTimestamp(self, cx, cz, timestamp=None):
        self._requireWritable()
        if timestamp is None:
            timestamp = time.time()

        cx &= 0x1f
        cz &= 0x1f
        self.modTimes[cx + cz * 32] = timestamp
        with open(self.path, "rb+") as f:
            f.seek(self.SECTOR_BYTES)
            f.write(self.modTimes.tobytes())
