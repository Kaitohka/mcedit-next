
cdef enum:
    BIOME_NONE = 0
    BIOME_GRASS = 1
    BIOME_FOLIAGE = 2
    BIOME_FOLIAGE_PINE = 3
    BIOME_FOLIAGE_BIRCH = 4
    BIOME_REDSTONE = 5

cdef struct ModelQuad:
    float[32] xyzuvstc
    char[4] cullface  # isCulled, dx, dy, dz
    char[4] quadface  # face, dx, dy, dz
    char biomeTintType
    int ctm  # 0, or 1 + the index of the face's CTMQuad if it has a connected texture

# How a connected texture connects, and what to show for each of the 256 ways its eight neighbours can. A rect is
# (left, top, width, height) on the atlas, with the top at the higher v, as textures are stored bottom row first.
# A texture chosen by the block's position rather than its neighbours (method > 0) has a list of tiles instead, each
# whole, in rects[i][0:4].
cdef struct CTMSpec:
    unsigned char connect  # 0: the same state, 1: the same block, 2: never
    unsigned char frontCheck  # a neighbour with the same block in front of it does not connect
    unsigned char method  # connected.METHOD_*
    unsigned char symmetry  # connected.SYMMETRY_*
    int count  # tiles, for a method other than neighbours
    int width
    int height
    int loops
    int weightSum  # 0 for even chances
    int weights[256]
    unsigned char overlay  # 1: the chosen tiles are drawn over the face, which is kept
    unsigned char rule  # connected.RULE_*
    unsigned char tint  # connected.TINT_*, for overlays
    unsigned char hasObscured
    float obscured[4]  # with RULE_EDGES, the rect for a face covered by the same block in front of it
    unsigned char source  # connected.SOURCE_*, for Fusion's random
    unsigned char hasSeed
    long long seed
    unsigned char *connectIDs  # for OptiFine's overlay, by ID: the blocks whose edges it draws
    unsigned char *matchIDs  # and those with the same overlay
    unsigned char whole[256]  # 1: one rect for the whole face, 0: one for each quarter, 2: layers
    unsigned char layers[256]  # with 2, how many rects are drawn one over another, each over the whole face
    float rects[256][16]

# A face with a connected texture: its texture's right, up and front as block offsets, and the rect its texture
# coordinates were made for.
cdef struct CTMQuad:
    int spec
    char right[3]
    char up[3]
    char normal[3]
    float bl, btop, bw, bh

cdef struct ModelQuadList:
    int count
    ModelQuad *quads

cdef class ModelQuadListObj(object):
    cdef ModelQuadList quadList

cdef class BlockModels:
    cdef object resourceLoader
    cdef object blocktypes
    cdef object modelBlockJsons
    cdef object modelStateJsons
    cdef object quadsByResourcePathVariant
    cdef object blockStatesByResourcePathVariant
    cdef object _texturePaths
    cdef public object firstTextures
    cdef object cookedModels
    cdef ModelQuadList cookedModelsByID[65536][16]
    cdef dict cookedModelsByBlockState
    cdef object cooked

    cdef ModelQuad * unknownModelQuads

    cdef ModelQuadList fluidQuads[9]

    cdef CTMSpec *ctmSpecs
    cdef int ctmSpecCount
    cdef CTMQuad *ctmQuads
    cdef int ctmQuadCount
    cdef public object ctmBlockGroups
    cdef public object connectedTextures
    cdef public object connectedByBlock
    cdef object _texturesByID

    cdef cookedModelsForState(self, tuple nameAndState)
