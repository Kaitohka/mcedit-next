#cython: boundscheck=False, language_level=3
"""
    ${NAME}
"""
import logging

cimport numpy
import numpy as np

from mcedit2.rendering import renderstates
from mcedit2.rendering.scenegraph.vertex_array import VertexNode
from mcedit2.rendering.layers import Layer
from mcedit2.rendering.vertexarraybuffer import QuadVertexArrayBuffer
from mceditlib.blocktypes import STATE_REDSTONE_WIRE, STATE_WATERLOGGED
cimport mcedit2.rendering.blockmodels as blockmodels

from libc.stdlib cimport malloc, realloc, free
from libc.math cimport sqrt as c_sqrt, log as c_log
from libc.string cimport memcpy

log = logging.getLogger(__name__)

cdef enum:
    quadFloats = 32
    vertexBytes = 32


lPowerLevels = [p/15. * 0.6 + 0.4 for p in range(16)]

lRedstonePowerColors = [
    (p if p > 0.4 else 0.3, max(0.0, p * p * 0.7 - 0.5), max(0.0, p * p * 0.6 - 0.7))
    for p in lPowerLevels
]
cdef unsigned char redstonePowerColors[16 * 4]
for i, (r, g, b) in enumerate(lRedstonePowerColors):
    redstonePowerColors[i*4] = b * 255
    redstonePowerColors[i*4+1] = g * 255
    redstonePowerColors[i*4+2] = r * 255
    redstonePowerColors[i*4+3] = 255

cdef unsigned char * foliageBitsPine = [0x61, 0x99, 0x61, 0xFF];   # BGRA
cdef unsigned char * foliageBitsBirch = [0x55, 0xA7, 0x80, 0xFF];  # BGRA

# The eight neighbours of a face, as steps up and right in its texture, in the order of the bits of a connection mask:
# up, up right, right, down right, down, down left, left, up left.
cdef int[8] _CTM_UP
cdef int[8] _CTM_RIGHT
_CTM_UP[:] = [1, 1, 0, -1, -1, -1, 0, 1]
_CTM_RIGHT[:] = [0, 1, 1, 1, 0, -1, -1, -1]


cdef inline char _ctmSame(blockmodels.CTMSpec *spec, unsigned short ID, unsigned char meta,
                          unsigned short nID, unsigned char nMeta, unsigned short *groups):
    if spec.connect == 0:
        return nID == ID and nMeta == meta
    if spec.connect == 2:
        return 0
    return nID != 0 and groups[nID] == groups[ID]


# --- Tiles chosen by where a block is, as the mods that draw them choose ---

cdef long long _positionSeed(int x, int y, int z):
    """ The game's seed for a block position (Mth.getSeed, MathHelper.hashCode), with Java's wrapping arithmetic. """
    cdef int xs = <int>(<unsigned int>x * <unsigned int>3129871)
    cdef long long l = (<long long>xs) ^ (<long long>z * 116129781LL) ^ <long long>y
    l = <long long>(<unsigned long long>l * <unsigned long long>l * <unsigned long long>42317861LL
                    + <unsigned long long>l * <unsigned long long>11LL)
    return l >> 16


cdef unsigned long long _mix64(unsigned long long z):
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL
    return z ^ (z >> 31)


cdef int _mix32(unsigned long long z):
    z = (z ^ (z >> 33)) * 0x62a9d9ed799705f5ULL
    return <int>(((z ^ (z >> 28)) * 0xcb24d0a5c88c35b3ULL) >> 32)


cdef int _faceOrdinal(char *normal):
    """ Direction.ordinal(): down, up, north, south, west, east. """
    if normal[1] < 0:
        return 0
    if normal[1] > 0:
        return 1
    if normal[2] < 0:
        return 2
    if normal[2] > 0:
        return 3
    if normal[0] < 0:
        return 4
    return 5


cdef int _symmetric(int face, int symmetry):
    if symmetry == 1 and face % 2 == 1:
        return face - 1  # up, south and east look like down, north and west
    if symmetry == 2:
        return 0
    return face


cdef int _floorMod(int a, int b):
    a = a % b
    return a + b if a < 0 else a


# java.util.Random, as the CTM mod uses one.
cdef unsigned long long _javaNext(unsigned long long *seed, int bits):
    seed[0] = (seed[0] * 0x5DEECE66DULL + 0xBULL) & ((1ULL << 48) - 1)
    return seed[0] >> (48 - bits)


cdef int _javaNextInt(unsigned long long *seed, int bound):
    cdef int r = <int>_javaNext(seed, 31)
    cdef int m = bound - 1
    cdef int u
    if bound & m == 0:
        return <int>((<long long>bound * <long long>r) >> 31)
    u = r
    r = u % bound
    while <int>(<unsigned int>u - <unsigned int>r + <unsigned int>m) < 0:
        u = <int>_javaNext(seed, 31)
        r = u % bound
    return r


cdef int _positionTile(blockmodels.CTMSpec *spec, blockmodels.CTMQuad *cq, int x, int y, int z):
    """ The tile a face at a block position shows, for a texture chosen by position. """
    cdef int face = _faceOrdinal(cq.normal)
    cdef int index, r, t, sx, sy, w, h
    cdef unsigned long long seed
    w = spec.width
    h = spec.height
    if spec.method == 1:  # OptiFine's random, as Continuity draws it
        face = _symmetric(face, spec.symmetry)
        r = _mix32(<unsigned long long>((<unsigned long long>_positionSeed(x, y, z)
                                         ^ _mix64(0x9e3779b97f4a7c15ULL * <unsigned long long>(1 + face)))
                                        + 0x9e3779b97f4a7c15ULL * <unsigned long long>(1 + spec.loops)))
        r = r & 0x7FFFFFFF
        if spec.weightSum <= 0:
            return r % spec.count
        t = r % spec.weightSum
        index = 0
        while index < spec.count - 1 and t >= spec.weights[index]:
            t -= spec.weights[index]
            index += 1
        return index
    if spec.method == 2:  # OptiFine's repeat
        face = _symmetric(face, spec.symmetry)
        if face == 0:
            sx, sy = x, -z - 1
        elif face == 1:
            sx, sy = x, z
        elif face == 2:
            sx, sy = -x - 1, -y
        elif face == 3:
            sx, sy = x, -y
        elif face == 4:
            sx, sy = z, -y
        else:
            sx, sy = -z - 1, -y
        return w * _floorMod(sy, h) + _floorMod(sx, w)
    if spec.method == 3:  # the CTM mod's random
        seed = (<unsigned long long>(_positionSeed(x, y, z) + face) ^ 0x5DEECE66DULL) & ((1ULL << 48) - 1)
        _javaNext(&seed, 1)
        sx = _javaNextInt(&seed, w)
        sy = _javaNextInt(&seed, h)
        return sx + sy * w
    if spec.method == 4:  # the CTM mod's pattern
        if face <= 1:
            sx = x % w
            sy = ((1 if face == 1 else -1) * z + 1) % h
        elif face <= 3:
            sx = x % w
            sy = -y % h
        else:
            sx = (z + 1) % w
            sy = -y % h
        if face == 2 or face == 5:
            sx = (w - sx - 1) % w
        if sx < 0:
            sx += w
        if sy < 0:
            sy += h
        return sx + sy * w
    if spec.method == 8:  # Fusion's random, with Minecraft's random numbers
        return _fusionRandomTile(spec, face, x, y, z)
    if spec.method == 10:  # Chisel's "ar"
        seed = (<unsigned long long>_positionSeed(x, y, z) ^ 0x5DEECE66DULL) & ((1ULL << 48) - 1)
        _javaNext(&seed, 1)
        index = _javaNextInt(&seed, 2) * 2
        r = 1
        if x % 2 == 0:
            r = not r
        if y % 2 == 0:
            r = not r
        if z % 2 == 0:
            r = not r
        return index + (0 if r else 1)
    if spec.method == 9:  # Fusion's continuous
        if face >= 4:
            sx, sy = z, -y
        elif face <= 1:
            sx, sy = x, -z - 1
        else:
            sx, sy = x, -y
        if face == 2 or face == 5:
            sx = -sx - 1
        if face == 1:
            sy = -sy - 1
        return _floorMod(sy, h) * w + _floorMod(sx, w)
    return 0


cdef int _fusionRandomTile(blockmodels.CTMSpec *spec, int face, int x, int y, int z):
    """ The tile of Fusion's random texture: its seed from the position, as it picks one. """
    cdef long long position = (<long long>x * 3129871LL) ^ (<long long>z * 116129781LL) ^ <long long>y
    cdef long long seed
    cdef unsigned long long state
    position = <long long>(<unsigned long long>position * <unsigned long long>position * <unsigned long long>42317861LL
                           + <unsigned long long>position * <unsigned long long>11LL)
    position = position >> 16
    if spec.source == 0:
        seed = 1
    elif spec.source == 1:
        seed = position
    elif spec.source == 2:
        seed = <long long>(<unsigned long long>position * <unsigned long long>(face + 1))
    else:
        seed = <long long>(<unsigned long long>position * <unsigned long long>((0 if face >= 4 else 1 if face <= 1 else 2) + 1))
    if spec.hasSeed:
        seed ^= spec.seed
    state = (<unsigned long long>seed ^ 0x5DEECE66DULL) & ((1ULL << 48) - 1)
    _javaNext(&state, 32)  # nextLong first, as Fusion does
    _javaNext(&state, 32)
    return _javaNextInt(&state, spec.count)


def positionTile(int method, int x, int y, int z, normal, int count=1, int width=1, int height=1, int symmetry=0,
                 int loops=0, weights=None):
    """ The tile a texture chosen by position shows on a face at (x, y, z) facing along normal; for tests. """
    cdef blockmodels.CTMSpec *spec = <blockmodels.CTMSpec *>malloc(sizeof(blockmodels.CTMSpec))
    cdef blockmodels.CTMQuad cq
    cdef int k, result
    if spec == NULL:
        raise MemoryError
    spec.method = method
    spec.symmetry = symmetry
    spec.count = count
    spec.width = width
    spec.height = height
    spec.loops = loops
    spec.weightSum = sum(weights) if weights else 0
    for k in range(min(len(weights or ()), 256)):
        spec.weights[k] = weights[k]
    for k in range(3):
        cq.normal[k] = normal[k]
    result = _positionTile(spec, &cq, x, y, z)
    free(spec)
    return result


cdef inline char _inArea(int x, int y, int z):
    """ Whether a place is in the section or the one block around it, which is all the mesh builder can look at. """
    return 0 <= x <= 17 and 0 <= y <= 17 and 0 <= z <= 17


cdef inline char _sameAt(blockmodels.CTMSpec *spec, unsigned short ID, unsigned char meta,
                         unsigned short[:, :, :] blocks, unsigned char[:, :, :] data, unsigned short *groups,
                         int x, int y, int z):
    if not _inArea(x, y, z):
        return 0
    return _ctmSame(spec, ID, meta, blocks[y, z, x], data[y, z, x], groups)


cdef inline void _wholeRect(float *q, float *rect, blockmodels.CTMQuad *cq):
    """ Give a face of four corners the part of the atlas in a rect, where its texture coordinates had its own. """
    cdef int i
    cdef float un, vn
    for i in range(4):
        un = (q[i * 8 + 3] - cq.bl) / cq.bw
        vn = (cq.btop - q[i * 8 + 4]) / cq.bh
        q[i * 8 + 3] = rect[0] + un * rect[2]
        q[i * 8 + 4] = rect[1] - vn * rect[3]


cdef inline void _rotatedRect(float *q, float *rect, blockmodels.CTMQuad *cq, int rotation):
    """ As _wholeRect, with the texture turned a quarter at a time, as the CTM mod's Quad.rotate turns it. """
    cdef int i
    cdef float un, vn, u, v
    for i in range(4):
        un = (q[i * 8 + 3] - cq.bl) / cq.bw
        vn = (cq.btop - q[i * 8 + 4]) / cq.bh
        if rotation == 1:
            u, v = vn, 1 - un
        elif rotation == 2:
            u, v = 1 - un, 1 - vn
        elif rotation == 3:
            u, v = 1 - vn, un
        else:
            u, v = un, vn
        q[i * 8 + 3] = rect[0] + u * rect[2]
        q[i * 8 + 4] = rect[1] - v * rect[3]


cdef double _javaNextDouble(unsigned long long *seed):
    return ((<long long>_javaNext(seed, 26) << 27) + <long long>_javaNext(seed, 27)) * (1.0 / (1LL << 53))


cdef double _javaGaussians(unsigned long long *seed, double *second):
    """ java.util.Random.nextGaussian twice: the first, and the second it keeps for the next call. """
    cdef double v1, v2, ss, multiplier
    while True:
        v1 = 2 * _javaNextDouble(seed) - 1
        v2 = 2 * _javaNextDouble(seed) - 1
        ss = v1 * v1 + v2 * v2
        if ss < 1 and ss != 0:
            break
    multiplier = c_sqrt(-2 * c_log(ss) / ss)
    second[0] = v2 * multiplier
    return v1 * multiplier


cdef int _pillarFace(blockmodels.CTMSpec *spec, blockmodels.CTMQuad *cq, float *q,
                     unsigned short[:, :, :] blocks, unsigned char[:, :, :] data, unsigned short *groups,
                     int x, int y, int z, unsigned short ID, unsigned char meta):
    """ The CTM mod's pillar: by the same blocks above, below and beside it, the end or a piece of the side. """
    cdef char up = _sameAt(spec, ID, meta, blocks, data, groups, x, y + 1, z)
    cdef char down = _sameAt(spec, ID, meta, blocks, data, groups, x, y - 1, z)
    cdef char north = _sameAt(spec, ID, meta, blocks, data, groups, x, y, z - 1)
    cdef char south = _sameAt(spec, ID, meta, blocks, data, groups, x, y, z + 1)
    cdef char west = _sameAt(spec, ID, meta, blocks, data, groups, x - 1, y, z)
    cdef char east = _sameAt(spec, ID, meta, blocks, data, groups, x + 1, y, z)
    cdef int face = _faceOrdinal(cq.normal)
    cdef char horizontal = face >= 2
    cdef int rotation = 0
    cdef int tile = 1  # the side's top left quarter
    cdef char connected
    if up or down:
        north = south = west = east = 0
    elif east or west:
        north = south = 0
    if horizontal and (up or down):
        tile = 3 if up and down else 4 if up else 2
    elif east or west:
        rotation = 1
        tile = 3 if east and west else 4 if east else 2
    elif north or south:
        tile = 3 if north and south else 4 if north else 2
        if face == 0:
            rotation += 2
    connected = up or down or north or south or east or west
    if connected and not (up or down):
        if face == 5:
            rotation += 1
        elif face == 2:
            rotation += 2
        elif face == 4:
            rotation += 3
    if ((face == 0 and up) or (face == 1 and down) or (face == 2 and south) or (face == 3 and north)
            or (face == 4 and east) or (face == 5 and west)):
        connected = 0
    if not (up or down or north or south or east or west) and horizontal:
        connected = 1
    _rotatedRect(q, &spec.rects[tile if connected else 0][0], cq, rotation % 4)
    return 1


cdef int _eldritchFace(blockmodels.CTMSpec *spec, blockmodels.CTMQuad *cq, float *q, int wx, int wy, int wz):
    """ The CTM mod's eldritch: four quarters, the middle of the texture moved by a random amount, by position. """
    cdef unsigned long long seed = (<unsigned long long>(_positionSeed(wx, wy, wz) + _faceOrdinal(cq.normal))
                                    ^ 0x5DEECE66DULL) & ((1ULL << 48) - 1)
    cdef double second
    cdef float offx = <float>(_javaGaussians(&seed, &second) * 0.08)
    cdef float offy = <float>(second * 0.08)
    cdef float[32] face
    cdef float *out
    cdef int i, k, a, b
    cdef float s, t
    cdef float umin = q[3], umax = q[3], vmin = q[4], vmax = q[4]
    for i in range(32):
        face[i] = q[i]
    for i in range(4):
        umin = min(umin, q[i * 8 + 3])
        umax = max(umax, q[i * 8 + 3])
        vmin = min(vmin, q[i * 8 + 4])
        vmax = max(vmax, q[i * 8 + 4])
    for a in range(2):
        for b in range(2):
            out = q + (a * 2 + b) * 32
            for i in range(4):
                s = a * 0.5 + (0.5 if i >= 2 else 0)
                t = b * 0.5 + (0.5 if i == 1 or i == 2 else 0)
                for k in range(5):
                    out[i * 8 + k] = ((1 - s) * (1 - t) * face[k] + (1 - s) * t * face[8 + k]
                                      + s * t * face[16 + k] + s * (1 - t) * face[24 + k])
                out[i * 8 + 5] = face[5]
                out[i * 8 + 6] = face[6]
                out[i * 8 + 7] = face[7]
                if s == 0.5 and t == 0.5:
                    # The middle of the face: its texture coordinates moved, v counting down the texture.
                    out[i * 8 + 3] += offx * (umax - umin)
                    out[i * 8 + 4] -= offy * (vmax - vmin)
    return 4


cdef inline char _opaqueAt(unsigned char *opaque, unsigned short[:, :, :] blocks, int x, int y, int z):
    if not _inArea(x, y, z):
        return 0
    return opaque[blocks[y, z, x]]


cdef char _appliesOverlay(blockmodels.CTMSpec *spec, unsigned short ID, unsigned char meta,
                          unsigned short[:, :, :] blocks, unsigned char[:, :, :] data, unsigned short *groups,
                          unsigned char *opaque, int x, int y, int z):
    """ Whether the block at a place draws its edge over the face: a full block it connects to, not the same. """
    cdef unsigned short nID
    if not _inArea(x, y, z):
        return 0
    nID = blocks[y, z, x]
    return (opaque[nID] and spec.connectIDs != NULL and spec.connectIDs[nID]
            and not _ctmSame(spec, ID, meta, nID, data[y, z, x], groups))


cdef char _overlayCorner(blockmodels.CTMSpec *spec, unsigned short ID, unsigned char meta,
                         unsigned short[:, :, :] blocks, unsigned char[:, :, :] data, unsigned short *groups,
                         unsigned char *opaque, int x, int y, int z, int *dx, int *dy, int *dz,
                         int nxn, int nyn, int nzn, int i, int j):
    """ Whether the block across the corner between two sides draws its edge there, and is not hidden in front. """
    cdef int cx = x + dx[i] + dx[j], cy = y + dy[i] + dy[j], cz = z + dz[i] + dz[j]
    return (_appliesOverlay(spec, ID, meta, blocks, data, groups, opaque, cx, cy, cz)
            and not _opaqueAt(opaque, blocks, cx + nxn, cy + nyn, cz + nzn))


cdef int _oneSide(int *tiles, char *same, int a, int b, int c, int tile, int cornerAB, int cornerBC,
                  char cornerABApplies, char cornerBCApplies):
    """ An overlay from one side, with the corners beside it where blocks with the same overlay lead there. """
    cdef char ab, bc
    cdef int n = 1
    tiles[0] = tile
    if same[b]:
        ab = bc = 1
    else:
        ab, bc = same[a], same[c]
    if ab and cornerABApplies:
        tiles[n] = cornerAB
        n += 1
    if bc and cornerBCApplies:
        tiles[n] = cornerBC
        n += 1
    return n


cdef int _twoSides(int *tiles, char *same, int a, int b, int tile, int cornerTile, char cornerApplies):
    """ An overlay from two sides at a corner, with the corner across from them where it applies. """
    tiles[0] = tile
    if (same[a] or same[b]) and cornerApplies:
        tiles[1] = cornerTile
        return 2
    return 1


cdef int _overlayTiles(blockmodels.CTMSpec *spec, blockmodels.CTMQuad *cq,
                       unsigned short[:, :, :] blocks, unsigned char[:, :, :] data, unsigned short *groups,
                       unsigned char *opaque, int x, int y, int z, unsigned short ID, unsigned char meta, int *tiles):
    """
    OptiFine's overlay, as Continuity draws it: which of its seventeen tiles go over the face, from the blocks on its
    four sides and the corners between them. Writes them to tiles, and returns how many.
    """
    cdef int[4] dx, dy, dz
    cdef char[4] applies, same
    cdef int i, nx, ny, nz, count = 0
    cdef int applications = 0
    cdef int nxn = cq.normal[0], nyn = cq.normal[1], nzn = cq.normal[2]
    # left, down, right, up
    dx[0], dy[0], dz[0] = -cq.right[0], -cq.right[1], -cq.right[2]
    dx[1], dy[1], dz[1] = -cq.up[0], -cq.up[1], -cq.up[2]
    dx[2], dy[2], dz[2] = cq.right[0], cq.right[1], cq.right[2]
    dx[3], dy[3], dz[3] = cq.up[0], cq.up[1], cq.up[2]
    for i in range(4):
        nx, ny, nz = x + dx[i], y + dy[i], z + dz[i]
        applies[i] = 0
        same[i] = 0
        if not _inArea(nx, ny, nz) or _opaqueAt(opaque, blocks, nx + nxn, ny + nyn, nz + nzn):
            continue  # the block beside it is hidden in front
        if _appliesOverlay(spec, ID, meta, blocks, data, groups, opaque, nx, ny, nz):
            applies[i] = 1
            applications |= 1 << i
        same[i] = spec.matchIDs != NULL and spec.matchIDs[blocks[ny, nz, nx]]

    if applications == 0b1111:
        tiles[0] = 8
        return 1
    if applications == 0b0111:
        tiles[0] = 5
        return 1
    if applications == 0b1011:
        tiles[0] = 6
        return 1
    if applications == 0b1101:
        tiles[0] = 13
        return 1
    if applications == 0b1110:
        tiles[0] = 12
        return 1
    if applications == 0b0101:
        tiles[0], tiles[1] = 9, 7
        return 2
    if applications == 0b1010:
        tiles[0], tiles[1] = 1, 15
        return 2
    if applications == 0b0011:
        return _twoSides(tiles, same, 2, 3, 4, 14, _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz, nxn, nyn, nzn, 2, 3))
    if applications == 0b0110:
        return _twoSides(tiles, same, 3, 0, 3, 16, _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz, nxn, nyn, nzn, 3, 0))
    if applications == 0b1100:
        return _twoSides(tiles, same, 0, 1, 10, 2, _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz, nxn, nyn, nzn, 0, 1))
    if applications == 0b1001:
        return _twoSides(tiles, same, 1, 2, 11, 0, _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz, nxn, nyn, nzn, 1, 2))
    if applications == 0b0001:
        return _oneSide(tiles, same, 1, 2, 3, 9, 0, 14,
                        _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz, nxn, nyn, nzn, 1, 2),
                        _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz, nxn, nyn, nzn, 2, 3))
    if applications == 0b0010:
        return _oneSide(tiles, same, 2, 3, 0, 1, 14, 16,
                        _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz, nxn, nyn, nzn, 2, 3),
                        _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz, nxn, nyn, nzn, 3, 0))
    if applications == 0b0100:
        return _oneSide(tiles, same, 3, 0, 1, 7, 16, 2,
                        _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz, nxn, nyn, nzn, 3, 0),
                        _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz, nxn, nyn, nzn, 0, 1))
    if applications == 0b1000:
        return _oneSide(tiles, same, 0, 1, 2, 15, 2, 0,
                        _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz, nxn, nyn, nzn, 0, 1),
                        _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz, nxn, nyn, nzn, 1, 2))
    if (same[0] or same[1]) and _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz,
                                                 nxn, nyn, nzn, 0, 1):
        tiles[count] = 2
        count += 1
    if (same[1] or same[2]) and _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz,
                                                 nxn, nyn, nzn, 1, 2):
        tiles[count] = 0
        count += 1
    if (same[2] or same[3]) and _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz,
                                                 nxn, nyn, nzn, 2, 3):
        tiles[count] = 14
        count += 1
    if (same[3] or same[0]) and _overlayCorner(spec, ID, meta, blocks, data, groups, opaque, x, y, z, dx, dy, dz,
                                                 nxn, nyn, nzn, 3, 0):
        tiles[count] = 16
        count += 1
    return count


# How far in front of a face its overlays are drawn, so that they are not hidden by it.
cdef float OVERLAY_LIFT = 1.0 / 512


cdef int _layers(float *q, float **rects, int count, blockmodels.CTMQuad *cq, char keepFace, unsigned char *tint):
    """
    Draw whole tiles one over another on a face: after the face itself where it is kept, as overlays are, or in its
    place. Each is lifted a little in front of the last. Returns how many faces there are then.
    """
    cdef int written = 1 if keepFace else 0
    cdef int i, k, v, channel
    cdef float *out
    cdef unsigned char *color
    cdef unsigned short value
    cdef float[32] face
    for i in range(32):
        face[i] = q[i]
    for i in range(count):
        if rects[i][2] == 0:
            continue  # a tile that draws nothing
        out = q + written * 32
        for k in range(32):
            out[k] = face[k]
        _wholeRect(out, rects[i], cq)
        for v in range(4):
            out[v * 8 + 0] += cq.normal[0] * OVERLAY_LIFT * (written + 1)
            out[v * 8 + 1] += cq.normal[1] * OVERLAY_LIFT * (written + 1)
            out[v * 8 + 2] += cq.normal[2] * OVERLAY_LIFT * (written + 1)
            if tint != NULL:
                color = <unsigned char *>&out[v * 8 + 7]
                for channel in range(3):
                    value = color[channel]
                    value *= tint[channel]
                    value >>= 8
                    color[channel] = <unsigned char>value
        written += 1
    return written


cdef int connectedFace(blockmodels.BlockModels models, int index, float *q,
                       unsigned short[:, :, :] blocks, unsigned char[:, :, :] data, unsigned short *groups,
                       int x, int y, int z, unsigned short ID, unsigned char meta, int wx, int wy, int wz,
                       unsigned char *opaque, unsigned char *grassColor, unsigned char *foliageColor):
    """
    Give a face with a connected texture the part of its texture its neighbours, or its position, call for. Writes the
    faces it becomes from there: one over the face, four for its quarters, the face with overlays over it, or layers
    in its place, none at all where layers have none. Returns how many.

    :param wx, wy, wz: the block's position in the world
    :param opaque: whether each ID is an opaque cube
    :param grassColor, foliageColor: the biome's colors there, for overlays tinted with them
    """
    cdef blockmodels.CTMQuad *cq = &models.ctmQuads[index]
    cdef blockmodels.CTMSpec *spec = &models.ctmSpecs[cq.spec]
    cdef int mask = 0
    cdef int i, k, nx, ny, nz, quarter, qx, qy, a, b, count
    cdef float un, vn, s0, t0, s, t, cu, cv
    cdef float *rect
    cdef float[32] face
    cdef float *out
    cdef float[4] ss
    cdef float[4] ts
    cdef float *chosen[4]
    cdef int[4] tiles
    cdef char obscured = 0
    cdef int fx, fy, fz
    cdef unsigned char *tint = NULL
    if spec.tint == 1:
        tint = grassColor
    elif spec.tint == 2:
        tint = foliageColor

    if spec.method == 5:
        return _pillarFace(spec, cq, q, blocks, data, groups, x, y, z, ID, meta)
    if spec.method == 6:
        return _eldritchFace(spec, cq, q, wx, wy, wz)
    if spec.method == 7:
        count = _overlayTiles(spec, cq, blocks, data, groups, opaque, x, y, z, ID, meta, tiles)
        for i in range(count):
            chosen[i] = &spec.rects[tiles[i]][0]
        return _layers(q, chosen, count, cq, 1, tint)
    if spec.method != 0:
        k = _positionTile(spec, cq, wx, wy, wz)
        if k < 0 or k >= spec.count:
            return 1
        if spec.overlay:
            chosen[0] = &spec.rects[k][0]
            return _layers(q, chosen, 1, cq, 1, tint)
        _wholeRect(q, &spec.rects[k][0], cq)
        return 1

    if spec.rule == 1:
        # The CTM mod's edges: covered by the same block in front of it, the face shows the covered tile.
        if _sameAt(spec, ID, meta, blocks, data, groups, x + cq.normal[0], y + cq.normal[1], z + cq.normal[2]):
            obscured = 1
    for i in range(8):
        if obscured:
            break
        nx = x + _CTM_UP[i] * cq.up[0] + _CTM_RIGHT[i] * cq.right[0]
        ny = y + _CTM_UP[i] * cq.up[1] + _CTM_RIGHT[i] * cq.right[1]
        nz = z + _CTM_UP[i] * cq.up[2] + _CTM_RIGHT[i] * cq.right[2]
        if spec.rule == 1:
            # A neighbour connects if it is the same, or the block in front of it is; one across a corner only
            # where one of the two beside it connects that way and is not covered.
            fx, fy, fz = cq.normal[0], cq.normal[1], cq.normal[2]
            if not (_sameAt(spec, ID, meta, blocks, data, groups, nx, ny, nz)
                    or _sameAt(spec, ID, meta, blocks, data, groups, nx + fx, ny + fy, nz + fz)):
                continue
            if i % 2 == 1:
                a = (i + 7) % 8
                b = (i + 1) % 8
                if not ((_sameAt(spec, ID, meta, blocks, data, groups,
                                 x + _CTM_UP[a] * cq.up[0] + _CTM_RIGHT[a] * cq.right[0],
                                 y + _CTM_UP[a] * cq.up[1] + _CTM_RIGHT[a] * cq.right[1],
                                 z + _CTM_UP[a] * cq.up[2] + _CTM_RIGHT[a] * cq.right[2])
                         and not _sameAt(spec, ID, meta, blocks, data, groups,
                                         x + _CTM_UP[a] * cq.up[0] + _CTM_RIGHT[a] * cq.right[0] + fx,
                                         y + _CTM_UP[a] * cq.up[1] + _CTM_RIGHT[a] * cq.right[1] + fy,
                                         z + _CTM_UP[a] * cq.up[2] + _CTM_RIGHT[a] * cq.right[2] + fz))
                        or (_sameAt(spec, ID, meta, blocks, data, groups,
                                    x + _CTM_UP[b] * cq.up[0] + _CTM_RIGHT[b] * cq.right[0],
                                    y + _CTM_UP[b] * cq.up[1] + _CTM_RIGHT[b] * cq.right[1],
                                    z + _CTM_UP[b] * cq.up[2] + _CTM_RIGHT[b] * cq.right[2])
                            and not _sameAt(spec, ID, meta, blocks, data, groups,
                                            x + _CTM_UP[b] * cq.up[0] + _CTM_RIGHT[b] * cq.right[0] + fx,
                                            y + _CTM_UP[b] * cq.up[1] + _CTM_RIGHT[b] * cq.right[1] + fy,
                                            z + _CTM_UP[b] * cq.up[2] + _CTM_RIGHT[b] * cq.right[2] + fz))):
                    continue
            mask |= 1 << i
            continue
        # The area is the section with one block around it; nothing further out can be looked at.
        if (nx < 1 or ny < 1 or nz < 1 or nx > 16 or ny > 16 or nz > 16) and (
                nx < 0 or ny < 0 or nz < 0 or nx > 17 or ny > 17 or nz > 17
                or nx + cq.normal[0] < 0 or ny + cq.normal[1] < 0 or nz + cq.normal[2] < 0
                or nx + cq.normal[0] > 17 or ny + cq.normal[1] > 17 or nz + cq.normal[2] > 17):
            continue
        if not _ctmSame(spec, ID, meta, blocks[ny, nz, nx], data[ny, nz, nx], groups):
            continue
        if spec.frontCheck and _ctmSame(spec, ID, meta, blocks[ny + cq.normal[1], nz + cq.normal[2], nx + cq.normal[0]],
                                        data[ny + cq.normal[1], nz + cq.normal[2], nx + cq.normal[0]], groups):
            continue  # its face is hidden by the same block in front of it
        mask |= 1 << i

    if obscured and spec.hasObscured:
        _wholeRect(q, spec.obscured, cq)
        return 1

    if spec.overlay:
        chosen[0] = &spec.rects[mask][0]
        return _layers(q, chosen, 1, cq, 1, tint)

    if spec.whole[mask] == 2:
        for i in range(spec.layers[mask]):
            chosen[i] = &spec.rects[mask][i * 4]
        return _layers(q, chosen, spec.layers[mask], cq, 0, NULL)

    if spec.whole[mask]:
        _wholeRect(q, &spec.rects[mask][0], cq)
        return 1

    # Four quarters, each a quarter of the face in its own corners' terms, with the texture of its quarter.
    for i in range(32):
        face[i] = q[i]
    for a in range(2):
        for b in range(2):
            out = q + (a * 2 + b) * 32
            s0 = a * 0.5
            t0 = b * 0.5
            ss[0] = s0
            ts[0] = t0
            ss[1] = s0
            ts[1] = t0 + 0.5
            ss[2] = s0 + 0.5
            ts[2] = t0 + 0.5
            ss[3] = s0 + 0.5
            ts[3] = t0
            for i in range(4):
                s = ss[i]
                t = ts[i]
                for k in range(5):
                    out[i * 8 + k] = ((1 - s) * (1 - t) * face[k] + (1 - s) * t * face[8 + k]
                                      + s * t * face[16 + k] + s * (1 - t) * face[24 + k])
                out[i * 8 + 5] = face[5]
                out[i * 8 + 6] = face[6]
                out[i * 8 + 7] = face[7]
            cu = 0
            cv = 0
            for i in range(4):
                cu += (out[i * 8 + 3] - cq.bl) / cq.bw / 4
                cv += (cq.btop - out[i * 8 + 4]) / cq.bh / 4
            qx = 1 if cu >= 0.5 else 0
            qy = 1 if cv >= 0.5 else 0
            quarter = qy * 2 + qx
            rect = &spec.rects[mask][quarter * 4]
            for i in range(4):
                un = ((out[i * 8 + 3] - cq.bl) / cq.bw - qx * 0.5) * 2
                vn = ((cq.btop - out[i * 8 + 4]) / cq.bh - qy * 0.5) * 2
                out[i * 8 + 3] = rect[0] + un * rect[2]
                out[i * 8 + 4] = rect[1] - vn * rect[3]
    return 4


def _knownIDs(blocktypes):
    """
    The IDs of blocks the mesher looks for by name, kept with the block types so that they are looked up once
    rather than for every section. They are found again once the block types have gained names.
    """
    nameCount = len(getattr(blocktypes, "IDsByName", ()))
    cached = getattr(blocktypes, "_meshKnownIDs", None)
    if cached is None or cached[0] != nameCount:
        cached = blocktypes._meshKnownIDs = (nameCount, {})
    return cached[1]


class BlockModelMesh(object):
    renderstate = renderstates.RenderstateAlphaTest
    def __init__(self, sectionUpdate):
        """

        :param sectionUpdate:
        :type sectionUpdate: mcedit2.rendering.chunkupdate.SectionUpdate
        :return:
        :rtype:
        """
        self.sectionUpdate = sectionUpdate
        self.sceneNode = None
        self.layer = Layer.Blocks

    def createVertexArrays(self):
        cdef numpy.ndarray[numpy.uint16_t, ndim=3] areaBlocks
        cdef numpy.ndarray[numpy.uint8_t, ndim=3] areaBlockLight
        cdef numpy.ndarray[numpy.uint8_t, ndim=3] areaSkyLight
        cdef numpy.ndarray[numpy.uint8_t, ndim=3] areaData
        cdef numpy.ndarray[numpy.uint8_t, ndim=2] areaBiomes
        cdef numpy.ndarray[numpy.uint8_t, ndim=1] renderType
        cdef numpy.ndarray[numpy.uint8_t, ndim=1] opaqueCube
        cdef numpy.ndarray[numpy.uint8_t, ndim=1] stateFlags
        cdef numpy.ndarray[numpy.uint16_t, ndim=1] selfCullGroup

        cdef blockmodels.BlockModels blockModels

        cdef short cy = self.sectionUpdate.cy
        cdef int chunkX = self.sectionUpdate.chunkUpdate.chunk.chunkPosition[0] * 16
        cdef int chunkZ = self.sectionUpdate.chunkUpdate.chunk.chunkPosition[1] * 16

        atlas = self.sectionUpdate.chunkUpdate.updateTask.textureAtlas
        blockModels = atlas.blockModels
        if not blockModels.cooked:
            log.warning("createVertexArrays: Block models not cooked, aborting.")
            return

        blocktypes = self.sectionUpdate.blocktypes
        areaBlocks = self.sectionUpdate.areaBlocks
        areaBlockLight = self.sectionUpdate.areaLights("BlockLight")
        areaSkyLight = self.sectionUpdate.areaLights("SkyLight")
        areaData = self.sectionUpdate.areaData
        areaBiomes = self.sectionUpdate.areaBiomes
        renderType = self.sectionUpdate.renderType
        opaqueCube = np.array(blocktypes.opaqueCube)
        stateFlags = blocktypes.stateFlags
        selfCullGroup = blocktypes.selfCullGroup

        biomeColors = atlas.biomeColors
        cdef numpy.ndarray grassColors = biomeColors.grass
        cdef numpy.ndarray foliageColors = biomeColors.foliage
        cdef numpy.ndarray waterColors = biomeColors.water
        cdef unsigned char * grassBits = <unsigned char *>grassColors.data
        cdef unsigned char * foliageBits = <unsigned char *>foliageColors.data
        cdef unsigned char * waterBits = <unsigned char *>waterColors.data

        # Worlds from before Minecraft 1.13 do not save all of a block's state, so what's missing
        # is worked out from its neighbors. Their water texture also has its color already.
        cdef char computeActualStates = blocktypes.hasHiddenStates
        cdef char tintWater = not blocktypes.hasHiddenStates
        cdef unsigned char waterloggedFlag = STATE_WATERLOGGED
        cdef unsigned char redstoneWireFlag = STATE_REDSTONE_WIRE


        #faceQuadVerts = []
        knownIDs = _knownIDs(blocktypes)

        def getID(internalName):
            ID = knownIDs.get(internalName)
            if ID is None:
                bt = blocktypes.get(internalName, None)
                ID = knownIDs[internalName] = 0 if bt is None else bt.ID
            return ID

        cdef unsigned short waterID = getID("minecraft:water")
        # Since Minecraft 1.13, flowing fluids are states of the fluid and not blocks of their own.
        cdef unsigned short waterFlowID = getID("minecraft:flowing_water") or waterID
        cdef unsigned short lavaID = getID("minecraft:lava")
        cdef unsigned short lavaFlowID = getID("minecraft:flowing_lava") or lavaID

        # glass, stained glass are special cased to return False for `shouldSideBeRendered`
        cdef unsigned short glassID = getID("minecraft:glass")
        cdef unsigned short stainedGlassID = getID("minecraft:stained_glass") or glassID

        # more special cases...

        cdef unsigned short anvilID = getID("minecraft:anvil")
        cdef unsigned short carpetID = getID("minecraft:carpet")
        cdef unsigned short dragonEggID = getID("minecraft:dragon_egg")
        cdef unsigned short endPortalID = getID("minecraft:end_portal")
        cdef unsigned short fenceID = getID("minecraft:fence")
        cdef unsigned short fenceGateID = getID("minecraft:fence_gate")
        cdef unsigned short hopperID = getID("minecraft:hopper")
        cdef unsigned short leavesID = getID("minecraft:leaves")
        cdef unsigned short leaves2ID = getID("minecraft:leaves2")
        cdef unsigned short paneID = getID("minecraft:glass_pane")
        cdef unsigned short barsID = getID("minecraft:iron_bars")
        cdef unsigned short pistonHeadID = getID("minecraft:piston_head")
        cdef unsigned short netherPortalID = getID("minecraft:portal")
        cdef unsigned short pComparatorID = getID("minecraft:powered_comparator")
        cdef unsigned short upComparatorID = getID("minecraft:unpowered_comparator")
        cdef unsigned short pRepeaterID = getID("minecraft:powered_repeater")
        cdef unsigned short upRepeaterID = getID("minecraft:unpowered_repeater")
        cdef unsigned short stoneSlabID = getID("minecraft:stone_slab")
        cdef unsigned short stoneSlab2ID = getID("minecraft:stone_slab2")
        cdef unsigned short woodenSlabID = getID("minecraft:wooden_slab")

        cdef unsigned short grassID = getID("minecraft:grass")
        cdef unsigned short snowID = getID("minecraft:snow")
        cdef unsigned short snowLayerID = getID("minecraft:snow_layer")
        cdef unsigned short netherFenceID = getID("minecraft:nether_brick_fence")
        cdef unsigned short stainedGlassPaneID = getID("minecraft:stained_glass_pane")

        cdef unsigned short woodenDoorID = getID("minecraft:wooden_door")
        cdef unsigned short ironDoorID = getID("minecraft:iron_door")
        cdef unsigned short birchDoorID = getID("minecraft:birch_door")
        cdef unsigned short spruceDoorID = getID("minecraft:spruce_door")
        cdef unsigned short jungleDoorID = getID("minecraft:jungle_door")
        cdef unsigned short acaciaDoorID = getID("minecraft:acacia_door")
        cdef unsigned short darkOakDoorID = getID("minecraft:dark_oak_door")

        cdef unsigned short cobbleWallID = getID("minecraft:cobblestone_wall")
        # Every kind of wooden fence and fence gate, which 1.8 added more of.
        woodFenceIDs = {getID("minecraft:" + name) for name in (
            "fence", "spruce_fence", "birch_fence", "jungle_fence", "dark_oak_fence", "acacia_fence")} - {0}
        fenceGateIDs = {getID("minecraft:" + name) for name in (
            "fence_gate", "spruce_fence_gate", "birch_fence_gate", "jungle_fence_gate", "dark_oak_fence_gate",
            "acacia_fence_gate")} - {0}
        cdef unsigned short tripwireID = getID("minecraft:tripwire")
        

        cdef unsigned short redstoneWireID = getID("minecraft:redstone_wire")
        cdef list powerSources

        powerSources = [
            getID("minecraft:stone_pressure_plate"),
            getID("minecraft:wooden_pressure_plate"),
            getID("minecraft:light_weighted_pressure_plate"),
            getID("minecraft:heavy_weighted_pressure_plate"),
            getID("minecraft:stone_button"),
            getID("minecraft:wooden_button"),
            getID("minecraft:trapped_chest"),
            getID("minecraft:redstone_block"),
            getID("minecraft:daylight_detector"),
            getID("minecraft:daylight_detector_inverted"),
            getID("minecraft:lever"),
            getID("minecraft:detector_rail"),
            getID("minecraft:tripwire_hook"),
            getID("minecraft:redstone_torch"),
            getID("minecraft:unlit_redstone_torch"),
        ]
        powerSources = [p for p in powerSources if p != 0]

        #cdef char fancyGraphics = self.sectionUpdate.fancyGraphics
        cdef char fancyGraphics = True

        if fancyGraphics:
            opaqueCube[leavesID] = False
            opaqueCube[leaves2ID] = False


        waterTexTuple = self.sectionUpdate.chunkUpdate.textureAtlas.texCoordsByName["assets/minecraft/textures/%s/water_still.png" % blocktypes.textureFolder]
        cdef float[4] waterTex
        waterTex[0] = waterTexTuple[0]
        waterTex[1] = waterTexTuple[1]
        waterTex[2] = waterTexTuple[2]
        waterTex[3] = waterTexTuple[3]
        lavaTexTuple = self.sectionUpdate.chunkUpdate.textureAtlas.texCoordsByName["assets/minecraft/textures/%s/lava_still.png" % blocktypes.textureFolder]
        cdef float[4] lavaTex
        lavaTex[0] = lavaTexTuple[0]
        lavaTex[1] = lavaTexTuple[1]
        lavaTex[2] = lavaTexTuple[2]
        lavaTex[3] = lavaTexTuple[3]

        cdef float * fluidTex

        cdef unsigned short y, z, x, ID, meta
        cdef short dx, dy, dz,
        cdef unsigned short nx, ny, nz, nID, upID
        cdef unsigned char nMeta
        cdef blockmodels.ModelQuadList quads
        cdef blockmodels.ModelQuad quad

        cdef short rx, ry, rz
        cdef unsigned char bl, sl
        cdef unsigned char tintType
        cdef unsigned char biomeID
        cdef unsigned char fluidMeta
        cdef char isWaterlogged, topFaceOnly, isWater

        cdef size_t buffer_ptr = 0
        cdef size_t buffer_size = 256
        cdef float * vertexBuffer = <float *>malloc(buffer_size * sizeof(float) * quadFloats)
        cdef float * xyzuvstc
        cdef numpy.ndarray vabuffer
        cdef unsigned char * vertexColor
        cdef unsigned short color
        cdef size_t vertex, channel
        cdef unsigned char * tintColor

        cdef char doCull = 0
        cdef char foundActualState
        cdef char redstonePower
        cdef char wallCount


        cdef blockmodels.ModelQuadListObj quadListObj
        cdef int outQuads, outIndex
        cdef float * outQuad
        cdef numpy.ndarray[numpy.uint16_t, ndim=1] ctmGroupArray = blockModels.ctmBlockGroups
        cdef unsigned short * ctmGroups = NULL
        cdef unsigned short[:, :, :] blockView = areaBlocks
        cdef unsigned char[:, :, :] dataView = areaData
        cdef unsigned char * opaqueBits = <unsigned char *>opaqueCube.data
        if ctmGroupArray is not None:
            ctmGroups = <unsigned short *>ctmGroupArray.data

        if vertexBuffer == NULL:
            return
        for y in range(1, 17):
            ry = y - 1 + (cy << 4)
            for z in range(1, 17):
                rz = z - 1
                for x in range(1, 17):
                    rx = x - 1
                    ID = areaBlocks[y, z, x]
                    if ID == 0:
                        continue
                    meta = areaData[y, z, x]
                    actualState = None
                    redstonePower = 0
                    isWaterlogged = stateFlags[<size_t>ID * 16 + meta] & waterloggedFlag

                    if renderType[ID] == 3:  # model blocks
                        # if this block has actualStates, get its actualState
                        # using its neighbors and look up that state's models
                        # in blockModels.... ... ...

                        # to get its actual state, we need to get its current state from
                        # its id and meta, parse the state into properties,
                        # change some property values into others according to
                        # actualState logic, then use the new state to look up the model
                        # ... ... ...
                        # all without doing a dict lookup for every block...
                        #
                        # absolutely disgusting

                        if not computeActualStates and stateFlags[<size_t>ID * 16 + meta] & redstoneWireFlag:
                            redstonePower = blocktypes.redstonePower(ID, meta)

                        if computeActualStates:
                            def parseProps(ID, meta):
                                state = blocktypes.statesByID.get((ID, meta))
                                if state is None:
                                    state = blocktypes.defaultBlockstates.get(ID)
                                if state is None or '[' not in state:
                                    return {}
                                state = state.split('[')[1]
                                props = state[:-1].split(",")
                                props = [p.split("=") for p in props]
                                return {k:v for k,v in props}

                            def combineProps(props):
                                props = [k + "=" + v for k, v in props.items()]
                                return tuple(sorted(props))

                            if grassID and (ID == grassID):
                                if (areaBlocks[y+1, z, x] == snowID
                                    or areaBlocks[y+1, z, x] == snowLayerID):
                                    actualState = "minecraft:grass", ("snowy=true",)

                            def gateFacesSide(nID, nMeta, dx):
                                # A fence gate joins a fence or wall beside one of its posts: on its east or west
                                # side when it faces north or south, and the other way round.
                                if nID not in fenceGateIDs:
                                    return False
                                facing = parseProps(nID, nMeta).get("facing")
                                return (facing in ("north", "south")) == (dx != 0)

                            if ID in woodFenceIDs or netherFenceID and ID == netherFenceID:
                                # A fence joins fences of its own material, fence gates and solid blocks.
                                props = []
                                for direction, dx, dz in [
                                    ("north", 0, -1),
                                    ("south", 0, 1),
                                    ("west", -1, 0),
                                    ("east", 1, 0),
                                ]:
                                    nID = areaBlocks[y, z+dz, x+dx]
                                    props.append(direction + "=" + ("true"
                                                 if opaqueCube[nID]
                                                    or (ID in woodFenceIDs and nID in woodFenceIDs)
                                                    or (ID == netherFenceID and nID == netherFenceID)
                                                    or gateFacesSide(nID, areaData[y, z+dz, x+dx], dx)
                                                 else "false"))
                                actualState = blocktypes.namesByID[ID], tuple(sorted(props))

                            if (paneID and (ID == paneID)
                                or stainedGlassPaneID and (ID == stainedGlassPaneID)
                                or barsID and (ID == barsID)):
                                props = {}
                                if ID == stainedGlassPaneID:
                                    props = parseProps(ID, meta)

                                for direction, dx, dz in [
                                    ("north", 0, -1),
                                    ("south", 0, 1),
                                    ("west", -1, 0),
                                    ("east", 1, 0),
                                ]:
                                    nID = areaBlocks[y, z+dz, x+dx]
                                    props[direction] = ("true"
                                                 if opaqueCube[nID]
                                                    or (paneID and nID == paneID)
                                                    or (barsID and nID == barsID)
                                                    or (stainedGlassPaneID and nID == stainedGlassPaneID)
                                                    or (glassID and nID == glassID)
                                                    or (stainedGlassID and nID == stainedGlassID)
                                                 else "false")
                                actualState = blocktypes.namesByID[ID], combineProps(props)

                            if ((woodenDoorID and ID == woodenDoorID)
                                or (ironDoorID and ID == ironDoorID)
                                or (birchDoorID and ID == birchDoorID)
                                or (spruceDoorID and ID == spruceDoorID)
                                or (jungleDoorID and ID == jungleDoorID)
                                or (acaciaDoorID and ID == acaciaDoorID)
                                or (darkOakDoorID and ID == darkOakDoorID)
                                ):
                                props = parseProps(ID, meta)
                                if len(props):
                                    if props['half'] == 'upper':
                                        nID = areaBlocks[y-1, z, x]
                                        if nID == ID:
                                            lowerProps = parseProps(areaBlocks[y-1, z, x], areaData[y-1, z, x])
                                            for p in ['facing', 'hinge', 'open']:
                                                props[p] = lowerProps[p]
                                            actualState = blocktypes.namesByID[ID], combineProps(props)
                        
                            if cobbleWallID and ID == cobbleWallID:
                                # A wall joins walls, fence gates and solid blocks. It has no post in the
                                # middle of a straight run with nothing on top.
                                props = parseProps(ID, meta)
                                for direction, dx, dz in [
                                    ("north", 0, -1),
                                    ("south", 0, 1),
                                    ("west", -1, 0),
                                    ("east", 1, 0),
                                ]:
                                    nID = areaBlocks[y, z+dz, x+dx]
                                    props[direction] = ("true"
                                                        if nID == ID or opaqueCube[nID]
                                                           or gateFacesSide(nID, areaData[y, z+dz, x+dx], dx)
                                                        else "false")
                                straight = (props['north'] == props['south'] != props['east'] == props['west'])
                                props['up'] = "false" if straight and areaBlocks[y+1, z, x] == 0 else "true"
                                actualState = blocktypes.namesByID[ID], combineProps(props)
                            
                            if tripwireID and ID == tripwireID:
                                props = parseProps(ID, meta)
                                for direction, dx, dz in [
                                    ("north", 0, -1),
                                    ("south", 0, 1),
                                    ("west", -1, 0),
                                    ("east", 1, 0),
                                ]:
                                    nID = areaBlocks[y, z+dz, x+dx]
                                    if nID == ID:
                                        props[direction] = "true"
                                
                                actualState = blocktypes.namesByID[ID], combineProps(props)
                                                
                            if redstoneWireID and ID == redstoneWireID:
                                props = parseProps(ID, meta)
                                def isConnectible(nID, nMeta, dx, dz):
                                    if (nID == redstoneWireID
                                        or (upComparatorID and nID == upComparatorID)
                                        or (pComparatorID and nID == pComparatorID)
                                        or (nID in powerSources)
                                        ):
                                        return True
                                    elif ((pRepeaterID and nID == pRepeaterID)
                                          or (upRepeaterID and nID == upRepeaterID)):
                                        nProps = parseProps(nID, nMeta)
                                        if (dz != 0 and nProps['facing'] in ('north', 'south')
                                            or dx != 0 and nProps['facing'] in ('east', 'west')):
                                            return True

                                if len(props):
                                    for direction, dx, dz in [
                                        ("north", 0, -1),
                                        ("south", 0, 1),
                                        ("west", -1, 0),
                                        ("east", 1, 0),
                                    ]:
                                        nID = areaBlocks[y, z+dz, x+dx]
                                        nMeta = areaData[y, z+dz, x+dx]
                                        if isConnectible(nID, nMeta, dx, dz):
                                            props[direction] = "side"

                                        elif opaqueCube[nID] > 0:  # xxx isFullOpaqueCube
                                            nID = areaBlocks[y+1, z+dz, x+dx]
                                            nMeta = areaData[y+1, z+dz, x+dx]
                                            if isConnectible(nID, nMeta, dx, dz):
                                                props[direction] = "up"
                                        else:
                                            nID = areaBlocks[y-1, z+dz, x+dx]
                                            nMeta = areaData[y-1, z+dz, x+dx]
                                            if isConnectible(nID, nMeta, dx, dz):
                                                props[direction] = "side"

                                    redstonePower = int(props['power'])

                                actualState = "minecraft:redstone_wire", combineProps(props)

                        if actualState is None:
                            quads = blockModels.cookedModelsByID[ID][meta]
                        else:
                            quadListObj = blockModels.cookedModelsForState(actualState)
                            quads = quadListObj.quadList

                        if areaBiomes is not None:
                            biomeID = areaBiomes[z, x]
                        else:
                            biomeID = 1

                        for i in range(quads.count):
                            doCull = 0
                            quad = quads.quads[i]
                            if quad.cullface[0]:
                                nx = x + quad.cullface[1]
                                ny = y + quad.cullface[2]
                                nz = z + quad.cullface[3]
                                nID = areaBlocks[ny, nz, nx]
                                if not computeActualStates:
                                    if selfCullGroup[ID] and selfCullGroup[ID] == selfCullGroup[nID]:
                                        doCull = 1
                                    else:
                                        doCull = opaqueCube[nID]
                                elif (ID == endPortalID
                                    and quad.cullface[2] == -1):
                                    doCull = 0
                                elif (ID == anvilID
                                      or ID == dragonEggID
                                      or ID == fenceID
                                      or ID == fenceGateID
                                      or ID == hopperID
                                      or ID == pistonHeadID
                                      ):
                                    doCull = 0
                                elif ID == carpetID and quad.cullface[2] == 1:
                                    doCull = 0
                                elif ((ID == glassID or ID == stainedGlassID)
                                      and ((glassID and nID == glassID)
                                           or (stainedGlassID and nID == stainedGlassID))
                                      ):
                                    doCull = 1
                                elif ((ID == paneID
                                       or ID == barsID)
                                      and (ID == nID and meta == areaData[ny, nz, nx])):
                                    doCull = 1
                                elif (ID == netherPortalID and False):  # hairy, do later
                                    doCull = 1
                                elif (ID == stoneSlabID
                                      or ID == stoneSlab2ID
                                      or ID == woodenSlabID):
                                    if ((stoneSlabID and nID == stoneSlabID)
                                        or (stoneSlab2ID and nID == stoneSlab2ID)
                                        or (woodenSlabID and nID == woodenSlabID)):
                                        if (meta & 0x8) == (areaData[ny, nz, nx] & 0x8):
                                            doCull = 1
                                    else:
                                        doCull = opaqueCube[nID]
                                else:
                                    doCull = opaqueCube[nID]

                                if doCull:
                                    continue

                            nx = x + quad.quadface[1]
                            ny = y + quad.quadface[2]
                            nz = z + quad.quadface[3]
                            bl = areaBlockLight[ny, nz, nx]  # xxx block.useNeighborLighting
                            sl = areaSkyLight[ny, nz, nx]

                            xyzuvstc = vertexBuffer + buffer_ptr * quadFloats
                            memcpy(xyzuvstc, quad.xyzuvstc, sizeof(float) * quadFloats)

                            if quad.biomeTintType:
                                if quad.biomeTintType == blockmodels.BIOME_GRASS:
                                    tintColor = grassBits + <size_t>biomeID * 4
                                if quad.biomeTintType == blockmodels.BIOME_FOLIAGE:
                                    tintColor = foliageBits + <size_t>biomeID * 4
                                if quad.biomeTintType == blockmodels.BIOME_FOLIAGE_PINE:
                                    tintColor = foliageBitsPine
                                if quad.biomeTintType == blockmodels.BIOME_FOLIAGE_BIRCH:
                                    tintColor = foliageBitsBirch
                                if quad.biomeTintType == blockmodels.BIOME_REDSTONE:
                                    tintColor = &redstonePowerColors[redstonePower * 4]
                                    # print("REDSTONE TINT", redstonePower, tintColor[0], tintColor[1], tintColor[2])

                                vertexColor = <unsigned char *>xyzuvstc
                                for vertex in range(4):
                                    for channel in range(3):
                                        color = vertexColor[vertexBytes * vertex + vertexBytes - 4 + channel]
                                        # image format is ARGB8, but this is with respect to 4-byte words
                                        # when the words are little endian, the byte ordering becomes BGRA
                                        # what i REALLY SHOULD do is get the pixel as an int and bit shift the bytes out.
                                        color *= tintColor[2-channel]
                                        color >>= 8
                                        vertexColor[vertexBytes * vertex + vertexBytes - 4 + channel] = <unsigned char>color

                            outQuads = 1
                            if quad.ctm and ctmGroups != NULL:
                                outQuads = connectedFace(blockModels, quad.ctm - 1, xyzuvstc, blockView, dataView,
                                                         ctmGroups, x, y, z, ID, meta,
                                                         chunkX + rx, ry, chunkZ + rz, opaqueBits,
                                                         grassBits + <size_t>biomeID * 4,
                                                         foliageBits + <size_t>biomeID * 4)

                            for outIndex in range(outQuads):
                                outQuad = xyzuvstc + outIndex * quadFloats
                                outQuad[0] += rx
                                outQuad[1] += ry
                                outQuad[2] += rz
                                outQuad[5] += sl
                                outQuad[6] += bl

                                outQuad[8] += rx
                                outQuad[9] += ry
                                outQuad[10] += rz
                                outQuad[13] += sl
                                outQuad[14] += bl

                                outQuad[16] += rx
                                outQuad[17] += ry
                                outQuad[18] += rz
                                outQuad[21] += sl
                                outQuad[22] += bl

                                outQuad[24] += rx
                                outQuad[25] += ry
                                outQuad[26] += rz
                                outQuad[29] += sl
                                outQuad[30] += bl

                            buffer_ptr += outQuads

                            # Room for eight more, as a face with a connected texture can become five.
                            if buffer_ptr + 8 >= buffer_size:
                                buffer_size *= 2
                                vertexBuffer = <float *>realloc(vertexBuffer, buffer_size * sizeof(float) * quadFloats)

                    topFaceOnly = 0
                    fluidMeta = meta
                    if renderType[ID] == 1:
                        if ID == waterFlowID or ID == waterID:
                            fluidTex = waterTex
                        elif ID == lavaFlowID or ID == lavaID:
                            fluidTex = lavaTex
                        elif isWaterlogged:  # bubble columns
                            fluidTex = waterTex
                            fluidMeta = 0
                        else:
                            continue
                        if fluidMeta > 8:
                            fluidMeta = 8  # "falling" water - always full cube
                    elif isWaterlogged:
                        # Water is drawn opaque, and would hide the block it is around. Only its surface shows.
                        fluidTex = waterTex
                        fluidMeta = 0
                        topFaceOnly = 1
                    else:
                        continue

                    if True:
                        isWater = fluidTex == <float *>waterTex
                        if areaBiomes is not None:
                            biomeID = areaBiomes[z, x]
                        else:
                            biomeID = 1

                        # upID = areaBlocks[y+1, z, x]
                        # if upID == waterID or upID == waterFlowID or upID == lavaID or upID == lavaFlowID:
                        #     quads = blockModels.fluidQuads[8]  # block above has fluid - fill this fluid block
                        # else:
                        quads = blockModels.fluidQuads[fluidMeta]

                        bl = areaBlockLight[y, z, x]  # xxx block.useNeighborLighting
                        sl = areaSkyLight[y, z, x]
                        for i in range(6):
                            quad = quads.quads[i]
                            if topFaceOnly and quad.quadface[2] != 1:
                                continue
                            nx = x + quad.quadface[1]
                            ny = y + quad.quadface[2]
                            nz = z + quad.quadface[3]
                            nID = areaBlocks[ny, nz, nx]
                            if opaqueCube[nID]:
                                continue
                            nMeta = areaData[ny, nz, nx]
                            if isWater and stateFlags[<size_t>nID * 16 + nMeta] & waterloggedFlag:
                                continue  # the water carries on into that block
                            if nID == waterID or nID == waterFlowID or nID == lavaID or nID == lavaFlowID:
                                if nMeta > 7 or 7 - (nMeta & 0x7) >= 7 - (fluidMeta & 0x7):
                                    continue  # cull face as the neighboring block is fuller

                            xyzuvstc = vertexBuffer + buffer_ptr * quadFloats
                            memcpy(xyzuvstc, quad.xyzuvstc, sizeof(float) * quadFloats)

                            if tintWater and isWater:
                                tintColor = waterBits + <size_t>biomeID * 4
                                vertexColor = <unsigned char *>xyzuvstc
                                for vertex in range(4):
                                    for channel in range(3):
                                        color = vertexColor[vertexBytes * vertex + vertexBytes - 4 + channel]
                                        color *= tintColor[2-channel]
                                        color >>= 8
                                        vertexColor[vertexBytes * vertex + vertexBytes - 4 + channel] = <unsigned char>color

                            xyzuvstc[0] += rx
                            xyzuvstc[1] += ry
                            xyzuvstc[2] += rz
                            xyzuvstc[3] += fluidTex[0]
                            xyzuvstc[4] += fluidTex[1]
                            xyzuvstc[5] += sl
                            xyzuvstc[6] += bl


                            xyzuvstc[8] += rx
                            xyzuvstc[9] += ry
                            xyzuvstc[10] += rz
                            xyzuvstc[11] += fluidTex[0]
                            xyzuvstc[12] += fluidTex[1]
                            xyzuvstc[13] += sl
                            xyzuvstc[14] += bl


                            xyzuvstc[16] += rx
                            xyzuvstc[17] += ry
                            xyzuvstc[18] += rz
                            xyzuvstc[19] += fluidTex[0]
                            xyzuvstc[20] += fluidTex[1]
                            xyzuvstc[21] += sl
                            xyzuvstc[22] += bl


                            xyzuvstc[24] += rx
                            xyzuvstc[25] += ry
                            xyzuvstc[26] += rz
                            xyzuvstc[27] += fluidTex[0]
                            xyzuvstc[28] += fluidTex[1]
                            xyzuvstc[29] += sl
                            xyzuvstc[30] += bl

                            buffer_ptr += 1

                            if buffer_ptr + 4 >= buffer_size:
                                buffer_size *= 2
                                vertexBuffer = <float *>realloc(vertexBuffer, buffer_size * sizeof(float) * quadFloats)

        if buffer_ptr:  # now buffer size
            vertexArray = QuadVertexArrayBuffer(buffer_ptr)
            vabuffer = vertexArray.buffer
            memcpy(vabuffer.data, vertexBuffer, buffer_ptr * sizeof(float) * quadFloats)
            self.sceneNode = VertexNode(vertexArray)
            self.sceneNode.name = "cy=%d" % self.sectionUpdate.cy
        free(vertexBuffer)
