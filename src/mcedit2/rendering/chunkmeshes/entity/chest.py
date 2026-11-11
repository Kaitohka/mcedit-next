"""
    chest
"""

import logging
from mcedit2.rendering.chunkmeshes.entity.modelrenderer import ModelRenderer

log = logging.getLogger(__name__)

# Since 1.15 a chest's texture is laid out for a model the right way up, where these models are upside down as
# the game's were before. normal_left.png came in at the same time, and tells the two apart.
NEW_LAYOUT_MARK = "assets/minecraft/textures/entity/chest/normal_left.png"
# The boxes of the single chest's texture, as (u, v, width, height, depth): the lid, the base and the knob.
CHEST_BOXES = ((0, 0, 14, 5, 14), (0, 19, 14, 10, 14), (0, 0, 2, 4, 1))
# Since 1.15 each half of a double chest has a texture of its own, a block and a half wide with half a knob.
HALF_CHEST_BOXES = ((0, 0, 15, 5, 14), (0, 19, 15, 10, 14), (0, 0, 1, 4, 1))

_CHEST_FOLDER = "assets/minecraft/textures/entity/chest/"
# The textures that come in the new layout, with the boxes of each.
CHEST_TEXTURES = dict(
    [(_CHEST_FOLDER + name + ".png", CHEST_BOXES) for name in ("normal", "trapped", "christmas")]
    + [(_CHEST_FOLDER + name + half + ".png", HALF_CHEST_BOXES)
       for name in ("normal", "trapped", "christmas") for half in ("_left", "_right")])


def chestTextureInOldLayout(image, boxes=CHEST_BOXES):
    """
    Turn a chest texture from 1.15 on into the layout of 1.14 and before, which ModelChest is made for. For each
    box, the top and bottom change places and are flipped top to bottom, and the sides are turned half round, with
    the front and back changing places.

    :param image: RGBA pixels, indexed [row, column, channel], top row first.
    :type image: numpy.ndarray
    :param boxes: the boxes the texture is for, as in CHEST_BOXES.
    """
    out = image.copy()
    for u, v, w, h, d in boxes:
        def region(x, y, rw, rh):
            return image[y:y + rh, x:x + rw]

        def put(x, y, patch):
            out[y:y + patch.shape[0], x:x + patch.shape[1]] = patch

        top, bottom = region(u + d, v, w, d), region(u + d + w, v, w, d)
        left, front = region(u, v + d, d, h), region(u + d, v + d, w, h)
        right, back = region(u + d + w, v + d, d, h), region(u + 2 * d + w, v + d, w, h)
        put(u + d, v, bottom[::-1])
        put(u + d + w, v, top[::-1])
        put(u, v + d, left[::-1, ::-1])
        put(u + d, v + d, back[::-1, ::-1])
        put(u + d + w, v + d, right[::-1, ::-1])
        put(u + 2 * d + w, v + d, front[::-1, ::-1])
    return out


class ModelChest(object):
    textureWidth = 64
    textureHeight = 64

    tileEntityID = "Chest"

    modelTexture = "assets/minecraft/textures/entity/chest/normal.png"

    def __init__(self):
        self.chestLid = ModelRenderer(self, 0, 0)
        self.chestLid.addBox(0.0, -5.0, -14.0, 14, 5, 14, 0.0)
        self.chestLid.setCenterPoint(1., 7., 15.)

        self.chestKnob = ModelRenderer(self, 0, 0)
        self.chestKnob.addBox(-1.0, -2.0, -15.0, 2, 4, 1, 0.0)
        self.chestKnob.setCenterPoint(8., 7., 15.)

        self.chestBelow = ModelRenderer(self, 0, 19)
        self.chestBelow.addBox(0.0, 0.0, 0.0, 14, 10, 14, 0.0)
        self.chestBelow.setCenterPoint(1., 6., 1.)


        self.parts = [
            self.chestLid,
            self.chestKnob,
            self.chestBelow,
        ]

class ModelLargeChest(object):
    textureWidth = 128
    textureHeight = 64

    modelTexture = "assets/minecraft/textures/entity/chest/normal_double.png"
    tileEntityID = "MCEDIT_LargeChest"  # gross

    def __init__(self):
        
        self.chestLid = ModelRenderer(self, 0, 0)
        self.chestLid.addBox(0.0, -5.0, -14.0, 30, 5, 14, 0.0)
        self.chestLid.setCenterPoint(1., 7., 15.)
        self.chestKnob = ModelRenderer(self, 0, 0)
        self.chestKnob.addBox(-1.0, -2.0, -15.0, 2, 4, 1, 0.0)
        self.chestKnob.setCenterPoint(16., 7., 15.)
        self.chestBelow = ModelRenderer(self, 0, 19)
        self.chestBelow.addBox(0.0, 0.0, 0.0, 30, 10, 14, 0.0)
        self.chestBelow.setCenterPoint(1., 6., 1.)

        self.parts = [
            self.chestLid,
            self.chestKnob,
            self.chestBelow,
        ]


class ModelChestHalf(object):
    """
    One half of a double chest from 1.15 on, which the game draws as two chests a block and a half wide that meet
    in the middle, each with half the knob and a texture of its own. Seen from the front, with the chest facing
    south, the left half (type=left) reaches over the west side of its block and the right half over the east.
    """
    textureWidth = 64
    textureHeight = 64

    def __init__(self, side):
        self.tileEntityID = "MCEDIT_Chest_" + side
        self.modelTexture = _CHEST_FOLDER + "normal_%s.png" % side
        x = 0. if side == "left" else 1.  # where the half starts, from the block's west side
        knobX = 0. if side == "left" else 15.

        self.chestLid = ModelRenderer(self, 0, 0)
        self.chestLid.addBox(0.0, -5.0, -14.0, 15, 5, 14, 0.0)
        self.chestLid.setCenterPoint(x, 7., 15.)

        self.chestKnob = ModelRenderer(self, 0, 0)
        self.chestKnob.addBox(0.0, -2.0, -15.0, 1, 4, 1, 0.0)
        self.chestKnob.setCenterPoint(knobX, 7., 15.)

        self.chestBelow = ModelRenderer(self, 0, 19)
        self.chestBelow.addBox(0.0, 0.0, 0.0, 15, 10, 14, 0.0)
        self.chestBelow.setCenterPoint(x, 6., 1.)

        self.parts = [
            self.chestLid,
            self.chestKnob,
            self.chestBelow,
        ]
