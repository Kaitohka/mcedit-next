"""
    fallback_shapes

    The game draws some blocks with code of its own and not from their block models, so they have
    no model that MCEdit can read: chests, signs, beds, heads and so on. Without something to show, they
    would be invisible. Each gets a plain box of about the right size, with a texture from its block.
"""
import re

WOOL_COLORED = re.compile(r"minecraft:(\w+?)_(wall_banner|banner|bed)$")

FACING_ROTATIONS = {"north": 0, "east": 90, "south": 180, "west": 270}

# Name patterns and boxes as (x1, y1, z1, x2, y2, z2) in sixteenths of a block. The first match wins.
# Things on a wall are placed for facing north, which is against the block to the south.
SHAPES = (
    (re.compile(r"_wall_hanging_sign$"), (0, 0, 7, 16, 10, 9)),
    (re.compile(r"_hanging_sign$"), (1, 0, 7, 15, 10, 9)),
    (re.compile(r"_wall_sign$"), (0, 4, 14, 16, 12, 16)),
    (re.compile(r"_sign$"), (2, 0, 7, 14, 14, 9)),
    (re.compile(r"_wall_banner$"), (1, 0, 14, 15, 16, 16)),
    (re.compile(r"_banner$"), (2, 0, 7, 14, 16, 9)),
    (re.compile(r"_wall_(head|skull)$"), (4, 4, 8, 12, 12, 16)),
    (re.compile(r"(head|skull)$"), (4, 0, 4, 12, 8, 12)),
    (re.compile(r"chest$"), (1, 0, 1, 15, 14, 15)),
    (re.compile(r"_bed$"), (0, 0, 0, 16, 9, 16)),
    (re.compile(r"shulker_box$"), (0, 0, 0, 16, 16, 16)),
    (re.compile(r":conduit$"), (5, 5, 5, 11, 11, 11)),
    (re.compile(r":decorated_pot$"), (1, 0, 1, 15, 16, 15)),
    (re.compile(r":end_portal$"), (0, 12, 0, 16, 12, 16)),
    (re.compile(r":end_gateway$"), (0, 0, 0, 16, 16, 16)),
)
DEFAULT_SHAPE = (2, 2, 2, 14, 14, 14)

NO_FALLBACK = (
    "minecraft:moving_piston",  # exists only for a moment, and draws its moving block
    "minecraft:chest",  # drawn from its block entity, by TileEntityModelRenderer, which a box would hide
)
RENDER_INVISIBLE = 0
RENDER_LIQUID = 1
RENDER_ENTITY = 2
MINECRAFT_PREFIX = "minecraft:"
MOD_FLUID_SHAPE = (0, 0, 0, 16, 14, 16)
FACES = ("down", "up", "north", "south", "west", "east")


def _texture(name, particleTexture):
    match = WOOL_COLORED.match(name)
    if match:  # all beds and banners give oak planks as their particle texture
        return "minecraft:block/%s_wool" % match.group(1)
    if not particleTexture or particleTexture.endswith(":missingno"):
        return "MCEDIT_UNKNOWN"
    return particleTexture


def fallbackModel(attributes):
    """
    Return a model to draw the given block state with, or None if it has its own or should be invisible.

    :param attributes: The block state's attributes, as from a block pack.
    :return: A dict with a block model in `model`, and its rotation in `x` and `y`.
    """
    renderType = attributes.get("renderType")
    name = attributes["internalName"]
    if name in NO_FALLBACK:
        return None

    # The renderer only knows how to draw the game's own fluids.
    isModFluid = renderType == RENDER_LIQUID and not name.startswith(MINECRAFT_PREFIX)
    if isModFluid:
        box = MOD_FLUID_SHAPE
    elif attributes.get("hasEntity") and renderType in (RENDER_INVISIBLE, RENDER_ENTITY):
        box = next((shape for pattern, shape in SHAPES if pattern.search(name)), DEFAULT_SHAPE)
    else:
        return None

    faces = {face: {"texture": "#all"} for face in FACES}
    if box[1] == box[4]:  # a flat plane, seen from above and below
        faces = {face: {"texture": "#all"} for face in ("up", "down")}

    facing = re.search(r"facing=(\w+)", attributes["blockState"])
    return {
        "model": {
            "textures": {"all": _texture(name, attributes.get("particleTexture"))},
            "elements": [{"from": list(box[:3]), "to": list(box[3:]), "faces": faces}],
        },
        "x": 0,
        "y": FACING_ROTATIONS.get(facing.group(1), 0) if facing else 0,
    }
