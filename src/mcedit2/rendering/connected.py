"""
    connected

Connected textures: a face of a block shows a different part of its texture depending on which of the blocks beside it
in the face's plane are the same, so that a wall of glass looks like one pane. Three formats are read, each the way the
mod that draws them in the game does it:

- the CTM mod's, from a "ctm" section in the .mcmeta file of a block's texture;
- Fusion's, from a "fusion" section in a texture's .mcmeta file, with the tiles laid out in the texture itself;
- OptiFine's, from .properties files in optifine/ctm/, as Continuity reads them.

Each comes down to a ConnectedTexture: for each of the 256 ways the eight neighbours of a face can connect, the part of
which texture to show. Directions are in the texture's own space, up being toward v = 0 and right toward u = 1, and are
bits of a mask. Some kinds choose by where the block is rather than by its neighbours (random tiles, and tiles repeated
in a pattern); they have a list of tiles and a METHOD_ saying how to choose among them.
"""
import collections
import json
import logging
import posixpath
import re

log = logging.getLogger(__name__)

U, UR, R, DR, D, DL, L, UL = (1 << i for i in range(8))

# How a neighbour is found to connect: it is the same block state, or the same block in any state.
SAME_STATE = 0
SAME_BLOCK = 1
NEVER = 2  # shows the tile for no connections, as a texture laid out for connecting on a block that doesn't

# How a tile is chosen: by the neighbours, or by the block's position.
METHOD_NEIGHBOURS = 0
METHOD_RANDOM = 1  # OptiFine's and Continuity's "random", with weights
METHOD_REPEAT = 2  # OptiFine's "repeat": a grid of tiles laid over the world
METHOD_CTM_RANDOM = 3  # the CTM mod's "random", cells of the texture chosen by java.util.Random
METHOD_CTM_PATTERN = 4  # the CTM mod's "pattern": the texture's cells laid over the world
METHOD_PILLAR = 5  # the CTM mod's "ctmv" or "pillar": by the same block above, below and beside it, turned to match
METHOD_ELDRITCH = 6  # the CTM mod's "eldritch": the middle of the face's texture moved a random way
METHOD_OVERLAY = 7  # OptiFine's "overlay": the edges of the blocks beside it, drawn over the face
METHOD_FUSION_RANDOM = 8  # Fusion's "random": a cell of the texture chosen by Minecraft's random numbers
METHOD_CONTINUOUS = 9  # Fusion's "continuous": the texture's cells laid over the world
METHOD_ALTER_R = 10  # Chisel's "ar": a quarter of the texture, by a random number and whether x, y and z are even

# How the neighbours are found to connect: as above, or as the CTM mod's "edges" does, where a neighbour also
# connects by the block in front of it, and the face is covered when the block in front of it is the same.
RULE_NORMAL = 0
RULE_EDGES = 1

# The texture the face has without connections, where a tile is to be left as it was.
DEFAULT = "<default>"
# No tile: an overlay that draws nothing there.
SKIP = "<skip>"

SYMMETRY_NONE, SYMMETRY_OPPOSITE, SYMMETRY_ALL = 0, 1, 2

# The colour an overlay is tinted with: none, or the biome's grass or foliage.
TINT_NONE, TINT_GRASS, TINT_FOLIAGE = 0, 1, 2

# Where Fusion's "random" takes its numbers from.
SOURCE_NONE, SOURCE_POSITION, SOURCE_POSITION_FACING, SOURCE_POSITION_AXIS = 0, 1, 2, 3

# A part of a texture: the texture's file, and the part of it as fractions of its width and height.
Part = collections.namedtuple("Part", "texture x y w h")

QUARTERS = ((0.0, 0.0), (0.5, 0.0), (0.0, 0.5), (0.5, 0.5))  # top left, top right, bottom left, bottom right
# For each quarter of a face: its vertical side, its horizontal side, and the corner between them.
QUARTER_SIDES = ((U, L, UL), (U, R, UR), (D, L, DL), (D, R, DR))


class Layers(list):
    """ Whole parts drawn one over another in place of the face, as Fusion's overlay layout gives; none hides it. """


class ConnectedTexture(object):
    """
    How one texture connects.

    :ivar parts: for each mask 0..255, one Part for the whole face, a list of four for its quarters in the order of
        QUARTERS, or Layers; with a method other than METHOD_NEIGHBOURS, one Part for each tile
    :ivar connect: SAME_STATE or SAME_BLOCK
    :ivar frontCheck: whether a neighbour does not connect when the block in front of it is the same, which would
        hide its face
    :ivar textures: every texture file the parts are from
    """

    OPTIONS = dict(frontCheck=False, faces=None, method=METHOD_NEIGHBOURS, width=1, height=1, weights=None,
                   symmetry=SYMMETRY_NONE, loops=0, overlay=False, rule=RULE_NORMAL, obscured=None, tint=TINT_NONE,
                   connectBlocks=None, connectTiles=None, matchBlocks=None, matchTiles=None,
                   source=SOURCE_POSITION_FACING, seed=None)

    def __init__(self, parts, connect=SAME_STATE, frontCheck=False, faces=None, method=METHOD_NEIGHBOURS, width=1,
                 height=1, weights=None, symmetry=SYMMETRY_NONE, loops=0, **options):
        self.parts = parts
        self.connect = connect
        self.frontCheck = frontCheck
        self.faces = faces  # the sides of a block it is for, or None for all
        self.method = method
        self.width = width  # the grid of tiles, for METHOD_REPEAT and the CTM mod's
        self.height = height
        self.weights = weights  # for METHOD_RANDOM, one for each tile, or None for even chances
        self.symmetry = symmetry
        self.loops = loops
        for name in ("overlay", "rule", "obscured", "tint", "connectBlocks", "connectTiles", "matchBlocks",
                     "matchTiles", "source", "seed"):
            setattr(self, name, options.pop(name, self.OPTIONS[name]))
        assert not options, options
        # overlay: the parts are drawn over the face, which is kept. obscured: the part for a face covered by the
        # same block in front of it, with RULE_EDGES. connectBlocks and the rest: for METHOD_OVERLAY, the blocks
        # whose edges are drawn, and those with the same overlay, by name and by texture.
        allParts = [p for entry in parts for p in (entry if isinstance(entry, list) else [entry])]
        if self.obscured is not None:
            allParts.append(self.obscured)
        self.textures = sorted({p.texture for p in allParts if p.texture not in (DEFAULT, SKIP)})
        self.usesDefault = any(p.texture == DEFAULT for p in allParts)

    def __repr__(self):
        return "ConnectedTexture(%s)" % ", ".join(self.textures)

    def options(self):
        return {name: getattr(self, name) for name in self.OPTIONS}

    def withConnect(self, connect):
        """ The same texture connecting another way, as a model can ask. """
        if connect == self.connect:
            return self
        variants = self.__dict__.setdefault("_variants", {})
        if connect not in variants:
            variants[connect] = ConnectedTexture(self.parts, connect, **self.options())
        return variants[connect]


def _has(mask, *bits):
    return all(mask & bit for bit in bits)


def cell(texture, columns, rows, index, height=1.0):
    """ The part of a texture that is the cell at an index of a grid, counting along rows. """
    x, y = index % columns, index // columns
    return Part(texture, x / float(columns), y * height / rows, 1.0 / columns, height / rows)


def quarterOf(part, quarter):
    """ One quarter of a part, as QUARTERS orders them. """
    qx, qy = QUARTERS[quarter]
    return Part(part.texture, part.x + qx * part.w, part.y + qy * part.h, part.w / 2, part.h / 2)


def _wholes(choose):
    return [choose(mask) for mask in range(256)]


def _quarters(choose):
    """ Parts for each mask from choose(mask, quarter); one whole part where all four quarters agree. """
    return [[choose(mask, q) for q in range(4)] for mask in range(256)]


# --- The CTM mod ---

def ctmModTexture(base, section):
    """
    A connected texture from the "ctm" section of a texture's .mcmeta file, or None if it does not connect.

    :param base: the texture's file, as "assets/<namespace>/textures/<path>.png"
    """
    if section.get("ctm_version") != 1:
        return None
    kind = str(section.get("type", "normal")).lower()
    textures = [texturePath(t) for t in section.get("textures") or []]
    extra = section.get("extra") or {}
    connect = SAME_BLOCK if extra.get("ignore_states") else SAME_STATE
    frontCheck = not extra.get("connect_inside", False)

    if kind == "ctm" and textures:
        sheet = textures[0]

        def choose(mask, q):
            a, b, diagonal = QUARTER_SIDES[q]
            offset = (0, 1, 4, 5)[q]
            if _has(mask, a, b, diagonal):
                return cell(sheet, 4, 4, offset)
            if not mask & a and not mask & b:
                return quarterOf(Part(base, 0, 0, 1, 1), q)
            return cell(sheet, 4, 4, offset + (2 if mask & a else 0) + (8 if mask & b else 0))
        return ConnectedTexture(_quarters(choose), connect, frontCheck)

    if kind in ("ctmh", "ctm_horizontal") and textures:
        sheet = textures[0]

        def choose(mask):
            left, right = mask & L, mask & R
            index = 1 if left and right else 3 if left else 2 if right else 0
            return cell(sheet, 2, 2, index)
        return ConnectedTexture(_wholes(choose), connect, frontCheck)

    if kind == "ctm_vertical" and textures:
        sheet = textures[0]

        def choose(mask):
            up, down = mask & U, mask & D
            index = 2 if up and down else 3 if up else 1 if down else 0
            return cell(sheet, 2, 2, index)
        return ConnectedTexture(_wholes(choose), connect, frontCheck)

    if kind in ("sctm", "ctm_simple") and textures:
        sheet = textures[0]

        def choose(mask):
            up, down, left, right = mask & U, mask & D, mask & L, mask & R
            if up or down or left or right:
                if not (up and down):
                    return cell(sheet, 2, 2, 1 if left and right else 0)
                if not (left and right):
                    return cell(sheet, 2, 2, 2)
                if _has(mask, UL, UR, DL, DR):
                    return cell(sheet, 2, 2, 3)
            return cell(sheet, 2, 2, 0)
        return ConnectedTexture(_wholes(choose), connect, frontCheck=False)

    if kind == "edges" and len(textures) >= 2:
        sheet = textures[0]

        def choose(mask, q):
            a, b, diagonal = QUARTER_SIDES[q]
            offset = (0, 1, 4, 5)[q]
            if _has(mask, a, b, diagonal) or (not mask & a and not mask & b and mask & diagonal):
                return cell(sheet, 4, 4, offset)  # an edge only at the corner: the corner of the connected tile
            if not mask & a and not mask & b:
                return quarterOf(Part(base, 0, 0, 1, 1), q)
            return cell(sheet, 4, 4, offset + (2 if mask & a else 0) + (8 if mask & b else 0))
        return ConnectedTexture(_quarters(choose), connect, rule=RULE_EDGES, obscured=Part(textures[1], 0, 0, 1, 1))

    if kind == "edges_full" and textures:
        sheet = textures[0]
        return ConnectedTexture(_wholes(lambda m: edgesFullPart(base, sheet, m)), connect, rule=RULE_EDGES,
                                obscured=_x4(sheet, 2, 1))

    if kind == "ar":
        # Chisel's own: the whole face shows one quarter of the texture.
        return ConnectedTexture([cell(base, 2, 2, i) for i in range(4)], connect, method=METHOD_ALTER_R)

    if kind == "eldritch":
        return ConnectedTexture([Part(base, 0, 0, 1, 1)], connect, method=METHOD_ELDRITCH)

    if kind in ("ctmv", "pillar") and textures:
        sheet = textures[0]
        # The end, then the four quarters of the side texture by row: (0, 0), (0, 1), (1, 0), (1, 1).
        tiles = [Part(base, 0, 0, 1, 1)] + [Part(sheet, column * 0.5, row * 0.5, 0.5, 0.5)
                                            for row in (0, 1) for column in (0, 1)]
        return ConnectedTexture(tiles, connect, method=METHOD_PILLAR)

    if kind in ("r", "random", "v", "pattern"):
        if "width" in extra and "height" in extra:
            width, height = int(extra["width"]), int(extra["height"])
        elif "size" in extra:
            width = height = int(extra["size"])
        else:
            width = height = 2
        if width <= 0 or height <= 0 or width * height > 256:
            return None
        tiles = [cell(base, width, height, i) for i in range(width * height)]
        method = METHOD_CTM_RANDOM if kind in ("r", "random") else METHOD_CTM_PATTERN
        return ConnectedTexture(tiles, connect, frontCheck=False, method=method, width=width, height=height)

    if kind not in ("normal",):
        log.info("CTM type %s of %s is not drawn connected", kind, base)
    return None


def _x4(texture, row, column):
    """ The CTM mod's Submap.X4[row][column]: a cell of a four by four grid. """
    return Part(texture, column * 0.25, row * 0.25, 0.25, 0.25)


def edgesFullPart(base, sheet, mask):
    """ The tile of the CTM mod's "edges_full" for a mask of the neighbours that connect by its edge rule. """
    def connected(*bits):
        return all(mask & bit for bit in bits)

    def anyOf(*bits):
        return any(mask & bit for bit in bits)

    if mask == 0:
        return Part(base, 0, 0, 1, 1)
    top = connected(U) or connected(UL, UR)
    right = connected(R) or connected(UR, DR)
    bottom = connected(D) or connected(DL, DR)
    left = connected(L) or connected(UL, DL)
    sides = top or right or bottom or left
    if (top and bottom) or (right and left):
        return _x4(sheet, 2, 1)
    if not sides and connected(UL, DR):
        return _x4(sheet, 0, 1)
    if not sides and connected(UR, DL):
        return _x4(sheet, 0, 2)
    if not (bottom or right) and anyOf(L, DL) and anyOf(U, UR):
        return _x4(sheet, 0, 3)
    if not (bottom or left) and anyOf(U, UL) and anyOf(R, DR):
        return _x4(sheet, 1, 3)
    if not (top or left) and anyOf(R, UR) and anyOf(D, DL):
        return _x4(sheet, 2, 3)
    if not (top or right) and anyOf(D, DR) and anyOf(L, UL):
        return _x4(sheet, 3, 3)
    if bottom:
        return _x4(sheet, 1, 1)
    if right:
        return _x4(sheet, 2, 0)
    if left:
        return _x4(sheet, 2, 2)
    if top:
        return _x4(sheet, 3, 1)
    if connected(DL):
        return _x4(sheet, 1, 2)
    if connected(DR):
        return _x4(sheet, 1, 0)
    if connected(UR):
        return _x4(sheet, 3, 0)
    if connected(UL):
        return _x4(sheet, 3, 2)
    return Part(sheet, 0, 0, 1, 1)


# --- Fusion ---

# Fusion's "full" layout: the tile for each set of connected sides, by which corners between them connect.
_FULL_SIDES = {
    0: (0, 0), R: (1, 0), L | R: (2, 0), L: (3, 0), D: (0, 1), U | D: (0, 2), U: (0, 3),
}
_FULL_TWO = {  # two sides at a corner: (corner, tile with it, tile without)
    U | L: (UL, (3, 3), (5, 1)), U | R: (UR, (1, 3), (4, 1)), R | D: (DR, (1, 1), (4, 0)), L | D: (DL, (3, 1), (5, 0)),
}
_FULL_THREE = {  # three sides: their two corners, and the tiles with both, the first, the second, and neither
    U | R | D: ((UR, DR), (1, 2), (4, 2), (6, 2), (6, 0)),
    L | R | D: ((DL, DR), (2, 1), (7, 2), (5, 2), (7, 0)),
    U | L | D: ((UL, DL), (3, 2), (7, 3), (5, 3), (7, 1)),
    U | L | R: ((UL, UR), (2, 3), (4, 3), (6, 3), (6, 1)),
}
_FULL_FOUR = {  # all four sides, by the corners that connect
    UL | UR | DL | DR: (2, 2), UR | DL | DR: (7, 5), UL | DL | DR: (6, 5), UL | UR | DR: (7, 4),
    UL | UR | DL: (6, 4), UR | DL: (0, 4), UL | DR: (0, 5), DL | DR: (3, 4), UL | DL: (3, 5), UL | UR: (2, 5),
    UR | DR: (2, 4), UL: (5, 5), UR: (4, 5), DR: (4, 4), DL: (5, 4), 0: (1, 4),
}
_FULL_SIMPLE = {
    0: (0, 0), U | D | L | R: (1, 0), D: (2, 0), L: (3, 0), L | R: (0, 1), U | D: (1, 1), R: (2, 1), U: (3, 1),
    U | R | D: (0, 2), L | R | D: (1, 2), R | D: (2, 2), L | D: (3, 2), L | U | R: (0, 3), U | L | D: (1, 3),
    U | R: (2, 3), U | L: (3, 3),
}
SIDES = U | R | D | L
CORNERS = UR | DR | DL | UL


def fullTile(mask):
    """ The (column, row) of Fusion's "full" layout for a mask. """
    sides = mask & SIDES
    if sides in _FULL_SIDES:
        return _FULL_SIDES[sides]
    if sides in _FULL_TWO:
        corner, with_, without = _FULL_TWO[sides]
        return with_ if mask & corner else without
    if sides in _FULL_THREE:
        (first, second), both, onlyFirst, onlySecond, neither = _FULL_THREE[sides]
        return (both if mask & first and mask & second else onlyFirst if mask & first
                else onlySecond if mask & second else neither)
    return _FULL_FOUR[mask & CORNERS]


def piecedIndex(mask, q):
    """ The tile of a quarter in the five-tile layouts, Fusion's "pieced" and OptiFine's "ctm_compact". """
    a, b, diagonal = QUARTER_SIDES[q]
    if mask & a and mask & b:
        return 1 if mask & diagonal else 4
    if mask & b:
        return 3
    if mask & a:
        return 2
    return 0


def fusionTexture(base, section, imageSize=None, filledCells=None):
    """
    A connected texture from the "fusion" section of a texture's .mcmeta file, or None.

    :param imageSize: the texture's (width, height), which says whether a "full" layout is in an older square image
    :param filledCells: called with (columns, rows), the cells of the texture that are not wholly clear, which
        Fusion's "random" leaves out
    """
    kind = str(section.get("type", "fusion:connecting")).rpartition(":")[2]
    if kind == "random":
        columns, rows = int(section.get("columns", 1)), int(section.get("rows", 1))
        if columns <= 0 or rows <= 0 or columns * rows > 256:
            return None
        cells = filledCells(columns, rows) if filledCells else range(columns * rows)
        tiles = [cell(base, columns, rows, i) for i in cells] or [Part(base, 0, 0, 1, 1)]
        source = {"none": SOURCE_NONE, "position": SOURCE_POSITION, "position_facing": SOURCE_POSITION_FACING,
                  "position_axis": SOURCE_POSITION_AXIS}.get(str(section.get("random_source", "position_facing")),
                                                             SOURCE_POSITION_FACING)
        seed = section.get("seed")
        return ConnectedTexture(tiles, SAME_STATE, method=METHOD_FUSION_RANDOM, source=source,
                                seed=int(seed) if seed is not None else None)
    if kind == "continuous":
        columns, rows = int(section.get("columns", 1)), int(section.get("rows", 1))
        if columns <= 0 or rows <= 0 or columns * rows > 256:
            return None
        tiles = [cell(base, columns, rows, i) for i in range(columns * rows)]
        return ConnectedTexture(tiles, SAME_STATE, method=METHOD_CONTINUOUS, width=columns, height=rows)
    if kind == "scrolling":
        # Still, it shows its first frame: a frame's size from the corner it starts at.
        if not imageSize:
            return None
        width, height = imageSize
        frameWidth = min(int(section.get("frame_width", 16)), width)
        frameHeight = min(int(section.get("frame_height", 16)), height)
        start = str(section.get("from", "top_left")).lower()
        x = width - frameWidth if start.endswith("right") else 0
        y = height - frameHeight if start.startswith("bottom") else 0
        part = Part(base, x / float(width), y / float(height), frameWidth / float(width), frameHeight / float(height))
        return ConnectedTexture(_wholes(lambda m: part), NEVER)
    if kind != "connecting":
        return None
    layout = section.get("layout", "full")
    connect = connectionsMode(section.get("connections"))

    if layout == "full":
        height = 0.75 if imageSize and imageSize[0] == imageSize[1] else 1.0
        return ConnectedTexture(_wholes(lambda m: cell(base, 8, 6, _xy(fullTile(m), 8), height)), connect)
    if layout == "simple":
        return ConnectedTexture(_wholes(lambda m: cell(base, 4, 4, _xy(_FULL_SIMPLE.get(m & SIDES, (0, 0)), 4))),
                                connect)
    if layout == "horizontal":
        return ConnectedTexture(_wholes(lambda m: cell(base, 4, 1, {0: 0, R: 1, L | R: 2, L: 3}[m & (L | R)])), connect)
    if layout == "vertical":
        return ConnectedTexture(_wholes(lambda m: cell(base, 1, 4, {0: 0, D: 1, U | D: 2, U: 3}[m & (U | D)])), connect)
    if layout == "pieced":
        return ConnectedTexture(_quarters(lambda m, q: quarterOf(cell(base, 5, 1, piecedIndex(m, q)), q)), connect)
    if layout == "compact":
        return ConnectedTexture(_wholes(lambda m: cell(base, 5, 1, compactIndex(m))), connect)
    if layout == "overlay":
        return ConnectedTexture(_wholes(lambda m: Layers(cell(base, 6, 3, i) for i in overlayTiles(m))), connect)
    log.info("Fusion layout %s of %s is not drawn connected", layout, base)
    return None


def overlayTiles(mask):
    """
    The cells of Fusion's "overlay" layout, six by three, drawn one over another for a mask: at most one for each
    quarter of the face, none where nothing beside it connects.
    """
    top, topRight, right, bottomRight = mask & U, mask & UR, mask & R, mask & DR
    bottom, bottomLeft, left, topLeft = mask & D, mask & DL, mask & L, mask & UL
    if top and right and bottom and left:
        return [4 + 1 * 6]
    if not mask:
        return []

    def at(x, y):
        return x + y * 6

    tiles = []
    for quad in range(4):
        position = None
        if quad == 0:
            if left:
                pass
            elif not top:
                position = at(2, 2) if topLeft else None
            elif not right:
                position = at(1, 2)
            elif not bottom:
                position = at(3, 2)
            else:
                position = at(3, 1)
        elif quad == 1:
            if top:
                pass
            elif not right:
                position = at(0, 2) if topRight else None
            elif not bottom:
                position = at(0, 1)
            elif not left:
                position = at(3, 0)
            else:
                position = at(4, 0)
        elif quad == 2:
            if right:
                pass
            elif not bottom:
                position = at(0, 0) if bottomRight else None
            elif not left:
                position = at(1, 0)
            elif not top:
                position = at(5, 0)
            else:
                position = at(5, 1)
        else:
            if bottom:
                pass
            elif not left:
                position = at(2, 0) if bottomLeft else None
            elif not top:
                position = at(2, 1)
            elif not right:
                position = at(5, 2)
            else:
                position = at(4, 2)
        if position is not None:
            tiles.append(position)
    return tiles


def _xy(xy, columns):
    return xy[0] + xy[1] * columns


def compactIndex(mask):
    """ The tile of Fusion's "compact" layout, which is the same for the whole face. """
    sides = mask & SIDES
    count = bin(sides).count("1")
    if count < 2:
        return 0
    if count == 2:
        return 3 if sides == L | R else 2 if sides == U | D else 0
    if count == 3:
        if sides & L and sides & R:
            row = (U, UL, UR) if sides & U else (D, DL, DR)
            return 3 if _has(mask, *row) else 0
        column = (L, UL, DL) if sides & L else (R, UR, DR)
        return 2 if _has(mask, *column) else 0
    corners = mask & CORNERS
    return 1 if corners == CORNERS else 4 if corners == 0 else 0


def connectionsMode(predicate):
    """
    How Fusion's connection predicate connects: never, by block, or by state. A predicate that names blocks to match,
    as Rechiseled's name the block itself and its double slab, is taken to connect by block; others by state.
    """
    if predicate is None:
        return SAME_STATE
    kinds = set()

    def walk(p):
        if isinstance(p, list):
            for q in p:
                walk(q)
        elif isinstance(p, dict):
            kinds.add(str(p.get("type", "")).rpartition(":")[2])
            walk(p.get("predicates"))
            walk(p.get("predicate"))
        elif isinstance(p, str):
            kinds.add(p.rpartition(":")[2])

    walk(predicate)
    if kinds and kinds <= {"false", "not"}:
        return NEVER
    if kinds & {"is_same_block", "match_block", "match_state"}:
        return SAME_BLOCK
    return SAME_STATE


def modelConnections(modelDict):
    """ How a Fusion connecting model asks its faces to connect, or None if it doesn't. """
    if modelDict.get("type") not in ("fusion:connecting", "connecting") or "connections" not in modelDict:
        return None
    connections = modelDict["connections"]
    if isinstance(connections, dict) and "type" not in connections:
        connections = [value for value in connections.values() if not (isinstance(value, str) and value.startswith("#"))]
    return connectionsMode(connections)


# --- OptiFine's properties, as Continuity reads them ---

OptiFineRule = collections.namedtuple("OptiFineRule", "texture blocks tiles faces")


def parseProperties(text):
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            key, sep, value = line.partition(":")
        values[key.strip()] = value.strip()
    return values


def tileFiles(tiles, folder):
    """
    The files of an OptiFine tile list, such as "0-46" or "glass ./other minecraft:block/stone", for a properties file
    in a folder, as "assets/<namespace>/...".
    """
    namespace = folder.split("/")[1]
    files = []
    for word in tiles.split():
        match = re.match(r"^(\d+)-(\d+)$", word)
        if match:
            files.extend(posixpath.join(folder, "%d.png" % i) for i in range(int(match.group(1)), int(match.group(2)) + 1))
        elif word in ("<skip>", "<default>"):
            files.append(word)
        elif word.startswith("./"):
            files.append(posixpath.join(folder, word[2:] + ("" if word.endswith(".png") else ".png")))
        elif word.startswith("~/") or word.startswith("/"):
            files.append("assets/%s/optifine/%s.png" % (namespace, word.lstrip("~/").rstrip(".png")))
        elif ":" in word:
            ns, path = word.split(":", 1)
            path = path if path.endswith(".png") else path + ".png"
            files.append("assets/%s/%s" % (ns, path if path.startswith("textures/") else "textures/" + path))
        else:
            files.append(posixpath.join(folder, word if word.endswith(".png") else word + ".png"))
    return files


FACES = {"bottom": ("down",), "top": ("up",), "north": ("north",), "south": ("south",), "east": ("east",),
         "west": ("west",), "sides": ("north", "south", "east", "west"), "all": None}


def optifineTexture(path, text):
    """
    A rule from an OptiFine properties file: the texture or blocks it is for, and how it connects. Returns
    (texture names or None, block names or None, ConnectedTexture) or None if it is not drawn connected.

    :param path: the file, as "assets/<namespace>/optifine/ctm/.../name.properties"
    """
    values = parseProperties(text)
    method = values.get("method", "ctm")
    folder = posixpath.dirname(path)
    files = tileFiles(values.get("tiles", ""), folder)
    overlay = method.startswith("overlay")

    def tile(i):
        """ The tile at an index: a file, or for "<skip>" and "<default>", the face as it was, or no overlay. """
        name = files[i]
        if name in ("<skip>", "<default>"):
            return Part(SKIP if overlay else DEFAULT, 0, 0, 1, 1)
        return Part(name, 0, 0, 1, 1)

    textures = None
    if values.get("matchTiles"):
        textures = [textureName(t) for t in values["matchTiles"].split()]
    blocks = blockNames(values.get("matchBlocks"))
    if textures is None and blocks is None:
        name = posixpath.basename(path)
        if name.startswith("block_"):
            blocks = ["minecraft:" + name[len("block_"):-len(".properties")]]
        else:
            return None

    faces = None
    if values.get("faces"):
        faces = set()
        for word in values["faces"].split():
            sides = FACES.get(word)
            if sides is None:
                faces = None
                break
            faces.update(sides)
    connectWord = values.get("connect", "block" if blocks else "tile")
    connect = SAME_BLOCK if connectWord == "block" else SAME_STATE
    frontCheck = values.get("innerSeams", "false").lower() == "true"
    options = {}
    if overlay:
        tintBlock = values.get("tintBlock", "")
        tint = TINT_NONE
        if _int(values.get("tintIndex"), -1) >= 0:
            if "grass" in tintBlock or "fern" in tintBlock:
                tint = TINT_GRASS
            elif "leaves" in tintBlock or "vine" in tintBlock:
                tint = TINT_FOLIAGE
        options = dict(overlay=True, tint=tint)
    count = len(files)

    if method in ("ctm", "glass", "overlay_ctm") and count >= 47:
        parts = _wholes(lambda m: tile(ctm47(m)))
    elif method == "ctm_compact" and count >= 5:
        parts = _quarters(lambda m, q: quarterOf(tile(piecedIndex(m, q)), q))
    elif method in ("horizontal", "bookshelf") and count >= 4:
        parts = _wholes(lambda m: tile({0: 3, R: 0, L | R: 1, L: 2}[m & (L | R)]))
    elif method == "vertical" and count >= 4:
        parts = _wholes(lambda m: tile({0: 3, U: 0, U | D: 1, D: 2}[m & (U | D)]))
    elif method in ("horizontal+vertical", "h+v") and count >= 7:
        parts = _wholes(lambda m: tile(horizontalVerticalIndex(m)))
    elif method in ("vertical+horizontal", "v+h") and count >= 7:
        parts = _wholes(lambda m: tile(verticalHorizontalIndex(m)))
    elif method == "top" and files:
        # The sides of a block show the tile where the block above is the same.
        parts = _wholes(lambda m: tile(0) if m & U else Part(DEFAULT, 0, 0, 1, 1))
        faces = set(faces or FACES["sides"]) & set(FACES["sides"])
    elif method in ("fixed", "overlay_fixed") and files:
        parts = _wholes(lambda m: tile(0))
    elif method in ("random", "overlay_random") and files:
        tiles = [tile(i) for i in range(min(count, 256))]
        return textures, blocks, ConnectedTexture(tiles, connect, False, faces, METHOD_RANDOM,
                                                  weights=randomWeights(values.get("weights"), len(tiles)),
                                                  symmetry=_symmetry(values.get("symmetry")),
                                                  loops=_int(values.get("randomLoops"), 0, 0, 9), **options)
    elif method in ("repeat", "overlay_repeat") and files:
        width, height = _int(values.get("width"), 0), _int(values.get("height"), 0)
        if width <= 0 or height <= 0 or width * height > min(count, 256):
            return None
        tiles = [tile(i) for i in range(width * height)]
        return textures, blocks, ConnectedTexture(tiles, connect, False, faces, METHOD_REPEAT, width, height,
                                                  symmetry=_symmetry(values.get("symmetry")), **options)
    elif method == "overlay" and count >= 17:
        # The edges of the blocks beside a face, from seventeen tiles; see the mesh builder for which.
        connectTiles = values.get("connectTiles")
        return textures, blocks, ConnectedTexture(
            [tile(i) for i in range(17)], connect, frontCheck, faces, METHOD_OVERLAY,
            connectBlocks=blockNames(values.get("connectBlocks")),
            connectTiles=[textureName(t) for t in connectTiles.split()] if connectTiles else None,
            matchBlocks=blocks, matchTiles=textures, **options)
    else:
        log.info("OptiFine method %s in %s is not drawn connected", method, path)
        return None
    return textures, blocks, ConnectedTexture(parts, connect, frontCheck, faces, **options)


def blockNames(text):
    """ The blocks a list of OptiFine's names, as "glass minecraft:stone mod:thing:facing=up", names; or None. """
    if not text:
        return None
    blocks = []
    for word in text.split():
        parts = word.split(":")
        name = ":".join(parts[:2]) if len(parts) > 1 and "=" not in parts[1] else "minecraft:" + parts[0]
        blocks.append(name)
    return blocks


def _int(text, default, low=None, high=None):
    try:
        value = int(str(text).strip())
    except (TypeError, ValueError):
        return default
    if (low is not None and value < low) or (high is not None and value > high):
        return default
    return value


def _symmetry(text):
    return {"opposite": SYMMETRY_OPPOSITE, "all": SYMMETRY_ALL}.get(str(text or "").strip().lower(), SYMMETRY_NONE)


def randomWeights(text, count):
    """
    The weights of OptiFine's random tiles, "1 2-4 5" giving 1, 2, 3, 4, 5, as Continuity reads them: tiles past the
    list get the average weight. None for even chances.
    """
    if not text:
        return None
    weights = []
    for word in re.split("[ ,]", text.strip()):
        if not word:
            continue
        low, dash, high = word.partition("-")
        try:
            if dash:
                low, high = int(low), int(high)
                if 0 < low <= high:
                    weights.extend(range(low, high + 1))
            elif int(low) > 0:
                weights.append(int(low))
        except ValueError:
            continue
    if not weights:
        return None
    weights = weights[:count]
    if len(weights) < count:
        average = sum(weights) // len(weights)
        weights.extend([average] * (count - len(weights)))
    return weights


# Continuity's horizontal+vertical and vertical+horizontal: the tile when the other way does not connect, by which of
# the three neighbours on each side do. Its bits are, for h+v: 1 down left, 2 down, 4 down right, 8 up right, 16 up,
# 32 up left; for v+h: 1 left, 2 up left, 4 up right, 8 right, 16 down right, 32 down left.
_HV_SECONDARY = [
    3, 3, 6, 3, 3, 3, 3, 3, 3, 3, 6, 3, 3, 3, 3, 3,
    4, 4, 5, 4, 4, 4, 4, 4, 3, 3, 6, 3, 3, 3, 3, 3,
    3, 3, 6, 3, 3, 3, 3, 3, 3, 3, 6, 3, 3, 3, 3, 3,
    3, 3, 6, 3, 3, 3, 3, 3, 3, 3, 6, 3, 3, 3, 3, 3,
]
_VH_SECONDARY = [
    3, 6, 3, 3, 3, 6, 3, 3, 4, 5, 4, 4, 3, 6, 3, 3,
    3, 6, 3, 3, 3, 6, 3, 3, 3, 6, 3, 3, 3, 6, 3, 3,
    3, 3, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 3, 3, 3, 3,
    3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3,
]


def horizontalVerticalIndex(mask):
    """ The tile of OptiFine's "horizontal+vertical" for a mask. """
    sides = (1 if mask & L else 0) | (2 if mask & R else 0)
    if sides:
        return (3, 2, 0, 1)[sides]
    bits = 0
    if mask & D:
        bits |= 2 | (1 if mask & DL else 0) | (4 if mask & DR else 0)
    if mask & U:
        bits |= 16 | (8 if mask & UR else 0) | (32 if mask & UL else 0)
    return _HV_SECONDARY[bits]


def verticalHorizontalIndex(mask):
    """ The tile of OptiFine's "vertical+horizontal" for a mask. """
    sides = (1 if mask & D else 0) | (2 if mask & U else 0)
    if sides:
        return (3, 2, 0, 1)[sides]
    bits = 0
    if mask & L:
        bits |= 1 | (32 if mask & DL else 0) | (2 if mask & UL else 0)
    if mask & R:
        bits |= 8 | (4 if mask & UR else 0) | (16 if mask & DR else 0)
    return _VH_SECONDARY[bits]


def textureName(name):
    """ A texture as OptiFine's matchTiles names it: "glass" is "minecraft:block/glass". """
    if name.startswith("textures/"):
        name = name[len("textures/"):]
    if ":" in name:
        namespace, path = name.split(":", 1)
        return namespace + ":" + (path if "/" in path else "block/" + path)
    return "minecraft:" + (name if "/" in name else "block/" + name)


# The 47 tiles of OptiFine's "ctm": the sides that connect, and the corners missing between two that do.
_CTM47 = [
    (0, 0), (R, 0), (L | R, 0), (L, 0), (D | R, DR), (D | L, DL), (U | D | R, DR | UR), (D | L | R, DL | DR),
    (U | D | L | R, DL | DR | UL), (U | D | L | R, DL | UR | UL), (U | D | L | R, DR | UR), (U | D | L | R, DL | DR),
    (D, 0), (D | R, 0), (D | L | R, 0), (D | L, 0), (U | R, UR), (U | L, UL), (U | L | R, UR | UL), (U | D | L, DL | UL),
    (U | D | L | R, DL | DR | UR), (U | D | L | R, DR | UR | UL), (U | D | L | R, UR | UL), (U | D | L | R, DL | UL),
    (U | D, 0), (U | D | R, 0), (U | D | L | R, 0), (U | D | L, 0), (U | D | R, UR), (D | L | R, DR), (U | D | R, DR),
    (D | L | R, DL), (U | D | L | R, DR), (U | D | L | R, DL), (U | D | L | R, DR | UL), (U | D | L | R, DL | UR),
    (U, 0), (U | R, 0), (U | L | R, 0), (U | L, 0), (U | L | R, UL), (U | D | L, DL), (U | L | R, UR), (U | D | L, UL),
    (U | D | L | R, UR), (U | D | L | R, UL), (U | D | L | R, DL | DR | UR | UL),
]
_CORNER_SIDES = {UL: U | L, UR: U | R, DL: D | L, DR: D | R}
_CTM47_INDEX = {key: i for i, key in enumerate(_CTM47)}


def ctm47(mask):
    """ The tile of OptiFine's 47-tile "ctm" for a mask. A corner counts only between two sides that connect. """
    sides = mask & SIDES
    notched = 0
    for corner, between in _CORNER_SIDES.items():
        if sides & between == between and not mask & corner:
            notched |= corner
    return _CTM47_INDEX[(sides, notched)]


def texturePath(name):
    """ A texture name, "namespace:path", as its file, "assets/<namespace>/textures/<path>.png". """
    namespace, colon, path = name.rpartition(":")
    return "assets/%s/textures/%s.png" % (namespace or "minecraft", path)


def mcmetaTexture(path, mcmetaText, imageSize=None, filledCells=None):
    """ The connected texture an .mcmeta file gives its texture, or None. """
    try:
        meta = json.loads(mcmetaText)
    except ValueError:
        return None
    if isinstance(meta.get("ctm"), dict):
        return ctmModTexture(path, meta["ctm"])
    if isinstance(meta.get("fusion"), dict):
        return fusionTexture(path, meta["fusion"], imageSize, filledCells)
    return None
