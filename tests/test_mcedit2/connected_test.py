"""
    connected_test

    Connected textures in the CTM mod's, Fusion's and OptiFine's formats, picked as each mod picks them.
"""
from mcedit2.rendering import connected as c
from mcedit2.rendering.connected import U, UR, R, DR, D, DL, L, UL

ALL = U | UR | R | DR | D | DL | L | UL
BASE = "assets/mod/textures/block/glass.png"
SHEET = "assets/mod/textures/block/glass-ctm.png"


def ctmMod(kind="ctm", **extra):
    section = {"ctm_version": 1, "type": kind, "textures": ["mod:block/glass-ctm"]}
    if extra:
        section["extra"] = extra
    return c.ctmModTexture(BASE, section)


def testCtmModQuartersComeFromTheBaseOrTheSheet():
    texture = ctmMod()
    alone = texture.parts[0]
    assert [p.texture for p in alone] == [BASE] * 4
    assert (alone[3].x, alone[3].y, alone[3].w) == (0.5, 0.5, 0.5)
    # Fully surrounded, each quarter shows its corner cell of the sheet: 0, 1, 4 and 5 of the four by four.
    surrounded = texture.parts[ALL]
    assert [(p.texture, p.x, p.y) for p in surrounded] == [
        (SHEET, 0.0, 0.0), (SHEET, 0.25, 0.0), (SHEET, 0.0, 0.25), (SHEET, 0.25, 0.25)]
    # The top left quarter with only the block above connected is cell 2; with only the left, cell 8.
    assert (texture.parts[U][0].x, texture.parts[U][0].y) == (0.5, 0.0)
    assert (texture.parts[L][0].x, texture.parts[L][0].y) == (0.0, 0.5)
    # Both sides but not the corner: cell 10.
    assert (texture.parts[U | L][0].x, texture.parts[U | L][0].y) == (0.5, 0.5)


def testCtmModConnectsByStateAndChecksTheBlockInFront():
    assert ctmMod().connect == c.SAME_STATE and ctmMod().frontCheck
    loose = ctmMod(ignore_states=True, connect_inside=True)
    assert loose.connect == c.SAME_BLOCK and not loose.frontCheck
    assert c.ctmModTexture(BASE, {"ctm_version": 1, "layer": "CUTOUT"}) is None  # no type: it doesn't connect


def testCtmModHorizontalAndVertical():
    horizontal = ctmMod("ctmh")
    assert [(horizontal.parts[m].x, horizontal.parts[m].y) for m in (0, L | R, L, R)] == [
        (0, 0), (0.5, 0), (0.5, 0.5), (0, 0.5)]
    vertical = ctmMod("ctm_vertical")
    assert [(vertical.parts[m].x, vertical.parts[m].y) for m in (0, U | D, U, D)] == [
        (0, 0), (0, 0.5), (0.5, 0.5), (0.5, 0)]


def testOptiFine47Tiles():
    assert c.ctm47(0) == 0
    assert c.ctm47(R) == 1
    assert c.ctm47(U | D | L | R | UL | UR | DL | DR) == 26
    assert c.ctm47(U | D | L | R) == 46
    # A corner only counts between two sides that connect.
    assert c.ctm47(R | UR) == 1
    assert sorted({c.ctm47(m) for m in range(256)}) == list(range(47))


def testOptiFinePropertiesFile():
    rule = c.optifineTexture("assets/minecraft/optifine/ctm/glass/glass.properties",
                             "method=ctm\ntiles=0-46\nmatchBlocks=glass\nfaces=sides\n")
    textures, blocks, texture = rule
    assert blocks == ["minecraft:glass"] and textures is None
    assert texture.parts[0].texture == "assets/minecraft/optifine/ctm/glass/0.png"
    assert texture.parts[ALL].texture == "assets/minecraft/optifine/ctm/glass/26.png"
    assert texture.faces == {"north", "south", "east", "west"}
    assert texture.connect == c.SAME_BLOCK and not texture.frontCheck

    textures, blocks, texture = c.optifineTexture("assets/minecraft/optifine/ctm/shelf.properties",
                                                  "method=horizontal\ntiles=a b c d\nmatchTiles=bookshelf\n")
    assert textures == ["minecraft:block/bookshelf"] and blocks is None
    assert [texture.parts[m].texture.rsplit("/", 1)[1] for m in (R, L | R, L, 0)] == ["a.png", "b.png", "c.png",
                                                                                     "d.png"]


def testOptiFineCompactUsesQuarters():
    _, _, texture = c.optifineTexture("assets/m/optifine/ctm/x/x.properties", "method=ctm_compact\ntiles=0-4\n"
                                                                              "matchBlocks=m:x\n")
    assert [p.texture.rsplit("/", 1)[1] for p in texture.parts[ALL]] == ["1.png"] * 4
    assert [p.texture.rsplit("/", 1)[1] for p in texture.parts[U | L | R]] == ["4.png", "4.png", "3.png", "3.png"]


def testFusionLayouts():
    full = c.fusionTexture(BASE, {"type": "connecting", "layout": "full"}, (128, 96))
    assert (full.parts[0].x, full.parts[0].y) == (0, 0)
    assert (full.parts[ALL].x, full.parts[ALL].y) == (2 / 8.0, 2 / 6.0)
    assert (full.parts[U | D | L | R].x, full.parts[U | D | L | R].y) == (1 / 8.0, 4 / 6.0)
    # An older square image has the six rows in its top three quarters.
    square = c.fusionTexture(BASE, {"type": "connecting", "layout": "full"}, (128, 128))
    assert square.parts[ALL].y == 2 * 0.75 / 6
    pieced = c.fusionTexture(BASE, {"type": "connecting", "layout": "pieced"})
    assert [round(p.x * 5, 3) // 1 for p in pieced.parts[ALL]] == [1, 1, 1, 1]
    assert c.fusionTexture(BASE, {"type": "connecting", "connections": {"type": "fusion:is_same_block"}}).connect \
        == c.SAME_BLOCK
    assert c.fusionTexture(BASE, {"type": "base"}) is None


def testFusionConnectionsFromTheTextureAndTheModel():
    # A texture laid out for connecting, on a block that doesn't connect, shows its tile for no connections.
    assert c.connectionsMode({"type": "false"}) == c.NEVER
    assert c.connectionsMode({"type": "fusion:is_same_state"}) == c.SAME_STATE
    rechiseled = {"type": "fusion:or", "predicates": [{"type": "fusion:match_block", "block": "mod:beams"},
                                                      {"type": "fusion:match_state", "block": "mod:beams_slab"}]}
    assert c.connectionsMode(rechiseled) == c.SAME_BLOCK
    model = {"type": "fusion:connecting", "parent": "block/cube", "connections": {"down": rechiseled, "up": "#down"}}
    assert c.modelConnections(model) == c.SAME_BLOCK
    assert c.modelConnections({"parent": "block/cube"}) is None
    texture = c.fusionTexture(BASE, {"type": "connecting", "layout": "pieced", "connections": {"type": "false"}})
    assert texture.connect == c.NEVER
    assert texture.withConnect(c.SAME_BLOCK).connect == c.SAME_BLOCK
    assert texture.withConnect(c.SAME_BLOCK) is texture.withConnect(c.SAME_BLOCK)
    assert texture.withConnect(c.SAME_BLOCK).parts is texture.parts


# --- Tiles chosen by position, and the other OptiFine methods ---

def _int32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def _int64(v):
    v &= 0xFFFFFFFFFFFFFFFF
    return v - (1 << 64) if v & (1 << 63) else v


def _seed(x, y, z):
    """ Mth.getSeed, with Java's arithmetic. """
    l = _int64(_int32(x * 3129871) ^ _int64(z * 116129781) ^ y)
    l = _int64(l * l * 42317861 + l * 11)
    return l >> 16


class _JavaRandom(object):
    def __init__(self, seed):
        self.seed = (seed ^ 0x5DEECE66D) & ((1 << 48) - 1)

    def next(self, bits):
        self.seed = (self.seed * 0x5DEECE66D + 0xB) & ((1 << 48) - 1)
        return _int32(self.seed >> (48 - bits))

    def nextInt(self, bound):
        r = self.next(31)
        m = bound - 1
        if bound & m == 0:
            return (bound * r) >> 31
        u = r
        r = u % bound
        while _int32(u - r + m) < 0:
            u = self.next(31)
            r = u % bound
        return r


def _ctmRandom(x, y, z, face, w, h):
    rand = _JavaRandom(_seed(x, y, z) + face)
    rand.next(1)
    return rand.nextInt(w) + rand.nextInt(h) * w


GOLDEN = 0x9e3779b97f4a7c15
MASK64 = (1 << 64) - 1


def _mix64(z):
    z = ((z ^ (z >> 30)) * 0xbf58476d1ce4e5b9) & MASK64
    z = ((z ^ (z >> 27)) * 0x94d049bb133111eb) & MASK64
    return z ^ (z >> 31)


def _mix32(z):
    z = ((z ^ (z >> 33)) * 0x62a9d9ed799705f5) & MASK64
    return _int32((((z ^ (z >> 28)) * 0xcb24d0a5c88c35b3) & MASK64) >> 32)


def _optifineRandom(x, y, z, face, count, loops=0):
    r = _mix32(((_seed(x, y, z) & MASK64) ^ _mix64((GOLDEN * (1 + face)) & MASK64)) + GOLDEN * (1 + loops) & MASK64)
    return (r & 0x7FFFFFFF) % count


UP_FACE, NORTH_FACE, EAST_FACE = (0, 1, 0), (0, 0, -1), (1, 0, 0)


def testRandomTilesAreChosenAsTheModsChooseThem():
    from mcedit2.rendering.modelmesh import positionTile
    for x, y, z in [(0, 0, 0), (5, 64, -3), (-100, 12, 77), (123456, -60, -98765)]:
        assert positionTile(c.METHOD_CTM_RANDOM, x, y, z, UP_FACE, 9, 3, 3) == _ctmRandom(x, y, z, 1, 3, 3)
        assert positionTile(c.METHOD_CTM_RANDOM, x, y, z, NORTH_FACE, 4, 2, 2) == _ctmRandom(x, y, z, 2, 2, 2)
        assert positionTile(c.METHOD_RANDOM, x, y, z, EAST_FACE, 5) == _optifineRandom(x, y, z, 5, 5)
        assert positionTile(c.METHOD_RANDOM, x, y, z, EAST_FACE, 5, loops=3) == _optifineRandom(x, y, z, 5, 5, 3)
        # "opposite" symmetry gives east the tile west would have.
        assert (positionTile(c.METHOD_RANDOM, x, y, z, EAST_FACE, 5, symmetry=c.SYMMETRY_OPPOSITE)
                == _optifineRandom(x, y, z, 4, 5))
    # Over many blocks, each tile comes up, and weights lean toward the heavier tile.
    tiles = [positionTile(c.METHOD_RANDOM, x, 0, z, UP_FACE, 2, weights=[1, 9]) for x in range(20) for z in range(20)]
    assert 300 < tiles.count(1) < 400


def testPatternsRepeatOverTheWorld():
    from mcedit2.rendering.modelmesh import positionTile
    # OptiFine's repeat, three by two, on top faces: x across, z down.
    tops = [[positionTile(c.METHOD_REPEAT, x, 0, z, UP_FACE, 6, 3, 2) for x in range(4)] for z in range(3)]
    assert tops == [[0, 1, 2, 0], [3, 4, 5, 3], [0, 1, 2, 0]]
    # On a north face, x runs the other way, and up the world is up the texture.
    assert [positionTile(c.METHOD_REPEAT, x, 0, 0, NORTH_FACE, 4, 4, 1) for x in range(4)] == [3, 2, 1, 0]
    assert [positionTile(c.METHOD_REPEAT, 0, y, 0, (0, 0, 1), 2, 1, 2) for y in range(3)] == [0, 1, 0]
    # The CTM mod's pattern, two by two, on a south face.
    assert [positionTile(c.METHOD_CTM_PATTERN, x, 0, 0, (0, 0, 1), 4, 2, 2) for x in range(-2, 2)] == [0, 1, 0, 1]
    assert [positionTile(c.METHOD_CTM_PATTERN, 0, y, 0, (0, 0, 1), 4, 2, 2) for y in range(2)] == [0, 2]


def testCtmModRandomAndPatternTextures():
    texture = ctmMod("random", size=3)
    assert texture.method == c.METHOD_CTM_RANDOM and (texture.width, texture.height) == (3, 3)
    assert [(p.texture, round(p.x, 3), round(p.y, 3)) for p in texture.parts[:4]] == [
        (BASE, 0, 0), (BASE, 0.333, 0), (BASE, 0.667, 0), (BASE, 0, 0.333)]
    pattern = ctmMod("pattern", width=4, height=2)
    assert pattern.method == c.METHOD_CTM_PATTERN and len(pattern.parts) == 8 and pattern.parts[5].y == 0.5
    assert ctmMod("v").method == c.METHOD_CTM_PATTERN and ctmMod("v").width == 2


def testOptiFineRandomRepeatAndTop():
    folder = "assets/minecraft/optifine/ctm/stone/"
    textures, blocks, texture = c.optifineTexture(folder + "stone.properties",
                                                  "method=random\ntiles=0-3\nmatchBlocks=stone\nweights=1 2\n"
                                                  "symmetry=opposite\nrandomLoops=2\n")
    assert texture.method == c.METHOD_RANDOM and [p.texture for p in texture.parts] == [
        folder + "%d.png" % i for i in range(4)]
    assert texture.weights == [1, 2, 1, 1] and texture.symmetry == c.SYMMETRY_OPPOSITE and texture.loops == 2

    textures, blocks, texture = c.optifineTexture(folder + "bricks.properties",
                                                  "method=repeat\ntiles=0-5\nwidth=3\nheight=2\nmatchBlocks=stone\n")
    assert texture.method == c.METHOD_REPEAT and (texture.width, texture.height) == (3, 2)
    assert c.optifineTexture(folder + "bad.properties", "method=repeat\ntiles=0-3\nwidth=3\nheight=2\n"
                                                        "matchBlocks=stone\n") is None

    textures, blocks, texture = c.optifineTexture(folder + "log.properties", "method=top\ntiles=top\nmatchBlocks=oak_log\n")
    assert texture.faces == {"north", "south", "east", "west"} and texture.usesDefault
    assert texture.parts[0].texture == c.DEFAULT and texture.parts[U].texture == folder + "top.png"
    assert texture.textures == [folder + "top.png"]


def testOptiFineHorizontalVertical():
    # Joined left or right, the horizontal tiles; otherwise by the column above and below.
    assert [c.horizontalVerticalIndex(m) for m in (R, L | R, L)] == [0, 1, 2]
    assert c.horizontalVerticalIndex(0) == 3
    assert c.horizontalVerticalIndex(U) == 4 and c.horizontalVerticalIndex(U | D) == 5
    assert c.horizontalVerticalIndex(D) == 6
    assert c.horizontalVerticalIndex(U | UL) == 3  # a corner joined beside it stops the column
    assert [c.verticalHorizontalIndex(m) for m in (U, U | D, D, 0)] == [0, 1, 2, 3]
    assert c.verticalHorizontalIndex(R) == 4 and c.verticalHorizontalIndex(L | R) == 5
    assert c.verticalHorizontalIndex(L) == 6
    _, _, texture = c.optifineTexture("assets/minecraft/optifine/ctm/a/a.properties",
                                      "method=h+v\ntiles=0-6\nmatchBlocks=stone\n")
    assert texture.parts[U | D].texture.endswith("/5.png")


# --- Overlays, edges, pillars and Fusion's other types ---

def testCtmModEdgesPillarAndEldritch():
    edges = c.ctmModTexture(BASE, {"ctm_version": 1, "type": "edges",
                                   "textures": ["mod:block/glass-ctm", "mod:block/covered"]})
    assert edges.rule == c.RULE_EDGES and edges.obscured.texture == "assets/mod/textures/block/covered.png"
    # A neighbour only across the corner shows the corner of the connected tile, not the plain texture.
    assert edges.parts[UL][0].texture == SHEET
    full = c.ctmModTexture(BASE, {"ctm_version": 1, "type": "edges_full", "textures": ["mod:block/glass-ctm"]})
    assert full.parts[0] == c.Part(BASE, 0, 0, 1, 1)
    assert (full.parts[U | D].x, full.parts[U | D].y) == (0.25, 0.5)  # Submap.X4[2][1]
    assert (full.parts[D].x, full.parts[D].y) == (0.25, 0.25)  # X4[1][1]
    assert full.obscured == c.Part(SHEET, 0.25, 0.5, 0.25, 0.25)
    pillar = c.ctmModTexture(BASE, {"ctm_version": 1, "type": "ctmv", "textures": ["mod:block/glass-ctm"]})
    assert pillar.method == c.METHOD_PILLAR and pillar.parts[0].texture == BASE and len(pillar.parts) == 5
    assert c.ctmModTexture(BASE, {"ctm_version": 1, "type": "eldritch"}).method == c.METHOD_ELDRITCH


def testFusionOverlayLayoutAndOtherTypes():
    overlay = c.fusionTexture(BASE, {"type": "connecting", "layout": "overlay"})
    assert overlay.parts[0] == []  # nothing beside it: nothing drawn
    assert len(overlay.parts[ALL]) == 1 and overlay.parts[ALL][0].x == 4 / 6.0
    assert all(len(overlay.parts[m]) <= 4 for m in range(256))
    random = c.fusionTexture(BASE, {"type": "fusion:random", "rows": 2, "columns": 2, "random_source": "position",
                                    "seed": 7}, filledCells=lambda columns, rows: [0, 3])
    assert random.method == c.METHOD_FUSION_RANDOM and [(p.x, p.y) for p in random.parts] == [(0, 0), (0.5, 0.5)]
    assert random.source == c.SOURCE_POSITION and random.seed == 7
    continuous = c.fusionTexture(BASE, {"type": "continuous", "rows": 2, "columns": 3})
    assert continuous.method == c.METHOD_CONTINUOUS and (continuous.width, continuous.height) == (3, 2)
    scrolling = c.fusionTexture(BASE, {"type": "scrolling", "from": "bottom_right", "frame_width": 16,
                                       "frame_height": 16}, imageSize=(64, 32))
    assert scrolling.parts[0] == c.Part(BASE, 0.75, 0.5, 0.25, 0.5)


def testOptiFineOverlaysAndSkippedTiles():
    folder = "assets/minecraft/optifine/ctm/overlay/"
    tiles = " ".join(["<skip>"] + ["t%d" % i for i in range(1, 17)])
    textures, blocks, texture = c.optifineTexture(folder + "dirt.properties",
                                                  "method=overlay\ntiles=%s\nmatchBlocks=dirt\n"
                                                  "connectBlocks=grass_block\ntintIndex=0\ntintBlock=grass_block\n"
                                                  % tiles)
    assert texture.method == c.METHOD_OVERLAY and texture.overlay and texture.tint == c.TINT_GRASS
    assert texture.connectBlocks == ["minecraft:grass_block"] and texture.matchBlocks == ["minecraft:dirt"]
    assert texture.parts[0].texture == c.SKIP and texture.parts[1].texture == folder + "t1.png"
    assert c.SKIP not in texture.textures
    _, _, fixed = c.optifineTexture(folder + "f.properties", "method=overlay_fixed\ntiles=t\nmatchBlocks=dirt\n")
    assert fixed.overlay and fixed.parts[0].texture == folder + "t.png"
    # Outside overlays, a skipped tile leaves the face as it was.
    _, _, plain = c.optifineTexture(folder + "h.properties", "method=horizontal\ntiles=a <skip> c d\nmatchBlocks=x\n")
    assert plain.parts[L | R].texture == c.DEFAULT


def _alterR(x, y, z):
    rand = _JavaRandom(_seed(x, y, z))
    rand.next(1)
    index = rand.nextInt(2) * 2
    flag = True
    for v in (x, y, z):
        if int(abs(v)) % 2 == 0:
            flag = not flag
    return index + (0 if flag else 1)


def testChiselAlterRChoosesAQuarterByPosition():
    from mcedit2.rendering.modelmesh import positionTile
    texture = c.ctmModTexture(BASE, {"ctm_version": 1, "type": "ar"})
    assert texture.method == c.METHOD_ALTER_R and [(p.x, p.y) for p in texture.parts] == [
        (0, 0), (0.5, 0), (0, 0.5), (0.5, 0.5)]
    for x, y, z in [(0, 0, 0), (1, 2, 3), (-5, 64, 17), (100, -3, -8)]:
        assert positionTile(c.METHOD_ALTER_R, x, y, z, UP_FACE, 4) == _alterR(x, y, z)
