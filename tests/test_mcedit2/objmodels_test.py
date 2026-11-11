"""
    objmodels_test

    Reading Forge's and NeoForge's OBJ models into faces of four corners, as NeoForge reads them.
"""
import math

import numpy as np
import pytest

from mcedit2.rendering import objmodels

PYRAMID = """# a pyramid
mtllib pyramid.mtl
v 0 0 0
v 1 0 0
v 1 0 1
v 0 0 1
v 0.5 1 0.5
vt 0 0
vt 1 0
vt 1 1
vt 0 1
vt 0.5 1
usemtl stone
f 1/1 2/2 3/3 4/4
usemtl side
f 2/1 1/2 5/5
f -2/-4 -3/-3 -1/-1
"""
PYRAMID_MTL = "newmtl stone\nmap_Kd somemod:block/stone\nnewmtl side\nmap_Kd #side\n"
PATH = "assets/somemod/models/block/pyramid.obj"
FILES = {"assets/somemod/models/block/pyramid.mtl": PYRAMID_MTL}


def faces(text=PYRAMID, **options):
    return objmodels.objFaces(text, options.pop("flipV", True), FILES.__getitem__, PATH, **options)


def testFacesAndMaterialsAreRead():
    raw, libraries = objmodels.parseObj(PYRAMID)
    assert libraries == ["pyramid.mtl"]
    assert [face.material for face in raw] == ["stone", "side", "side"]
    assert raw[0].corners[2] == ((1.0, 0.0, 1.0), (1.0, 1.0), None)
    # Negative indices count back from the last.
    assert [corner[0] for corner in raw[2].corners] == [(0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (0.5, 1.0, 0.5)]


def testMaterialsGiveTexturesColorsAndTints():
    materials = objmodels.parseMtl("newmtl stone\nKd 0.5 1 1\nmap_Kd -s 1 1 1 somemod:block/stone\n"
                                   "forge_TintIndex 0\nnewmtl other\nmap_Kd #all\nKa 1 1 1\n")
    assert materials["stone"].texture == "somemod:block/stone"
    assert materials["stone"].color == (0.5, 1.0, 1.0, 1.0)
    assert materials["stone"].tintindex == 0
    assert materials["other"].texture == "#all"
    assert materials["other"].tintindex == -1
    assert objmodels.textureName("C:\\work\\assets\\somemod\\textures\\block\\thing.png") == "somemod:block/thing"


def testTrianglesRepeatTheirLastCornerAndLargerFacesKeepTheirFirstFour():
    text = "v 0 0 0\nv 1 0 0\nv 1 0 1\nv 0 0 1\nv 0.5 0 1.5\nmtllib m.mtl\nusemtl stone\nf 1 2 3\nf 1 2 3 4 5\n"
    files = {"assets/somemod/models/block/m.mtl": "newmtl stone\nmap_Kd somemod:block/stone\n"}
    triangle, pentagon = objmodels.objFaces(text, False, files.__getitem__, PATH)
    assert [p[:3] for p in triangle.corners] == [(0, 0, 0), (1, 0, 0), (1, 0, 1), (1, 0, 1)]
    assert [p[:3] for p in pentagon.corners] == [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]
    # With no texture coordinates, the corners take the four corners of the texture.
    assert [p[3:] for p in triangle.corners] == [(0, 0), (0, 1), (1, 1), (1, 0)]


def testSidesFollowTheNormalsAndTexturesFlip():
    pyramid = faces()
    assert [face.side for face in pyramid] == ["down", "north", "south"]
    assert [face.texture for face in pyramid] == ["somemod:block/stone", "#side", "#side"]
    assert pyramid[0].corners[0][4] == 1.0  # v of 0 at the bottom is 1 from the top
    assert faces(flipV=False)[0].corners[0][4] == 0.0


def testFacesWithoutAMaterialAreNotDrawn():
    assert len(faces(PYRAMID.replace("usemtl stone\n", ""))) == 2
    assert len(faces(PYRAMID.replace("usemtl side", "usemtl unknown"))) == 1


def testFacesOnTheSidesOfTheBlockAreCulled():
    pyramid = faces()
    assert pyramid[0].cullface == "down"  # on the bottom, looking down
    assert pyramid[1].cullface is None
    assert faces(automaticCulling=False)[0].cullface is None


SPLIT = """mtllib pyramid.mtl
usemtl stone
v 0 0 0
v 1 0 0
v 1 0 1
f 1 2 3
o lid
f 1 2 3
g handle
f 1 2 3
o base
f 1 2 3
"""


def testPartsCanBeHidden():
    assert len(faces(SPLIT)) == 4
    assert len(faces(SPLIT, visibility={"lid": False})) == 2  # with the part inside it
    # Once "o" has come first, "g" makes a part inside it, named from the outer one.
    assert len(faces(SPLIT, visibility={"lid/handle": False})) == 3
    assert len(faces(SPLIT, visibility={"lid": False, "base": False})) == 1
    assert len(faces(SPLIT, visibility={"handle": False})) == 4


def testTransformsMoveTheModelAboutTheBlock():
    # A translation moves the model by blocks.
    moved = faces(transform=objmodels.rootTransform({"translation": [0, 0.5, 0]}))
    assert moved[0].corners[0][:3] == pytest.approx((0, 0.5, 0))
    assert moved[0].cullface is None  # no longer on the bottom of the block

    # A turn about the center of the block, given as the origin.
    turned = faces(transform=objmodels.rootTransform({"rotation": {"y": 90}, "origin": [0, 0, 0]}))
    assert turned[1].side == "west"
    assert turned[0].corners[1][:3] == pytest.approx((0, 0, 0))  # from (1, 0, 0)

    # With no origin, the game scales about its "opposing corner", which is a block out from the center.
    scaled = faces(transform=objmodels.rootTransform({"scale": 0.5}))
    assert scaled[0].corners[0][:3] == pytest.approx((0.75, 0.75, 0.75))
    centered = faces(transform=objmodels.rootTransform({"scale": 0.5, "origin": [0, 0, 0]}))
    assert centered[0].corners[0][:3] == pytest.approx((0.25, 0.25, 0.25))


def testRotationForms():
    quarter = math.sqrt(0.5)
    expected = objmodels.rootTransform({"rotation": {"y": 90}})
    assert np.allclose(objmodels.rootTransform({"rotation": [{"y": 90}]}), expected)
    assert np.allclose(objmodels.rootTransform({"rotation": [0, 90, 0]}), expected)
    assert np.allclose(objmodels.rootTransform({"rotation": [0, quarter, 0, quarter]}), expected)
    assert objmodels.rootTransform("identity") is None
    assert objmodels.rootTransform({"translation": [0, 0, 0]}) is None
    matrix = objmodels.rootTransform([[1, 0, 0, 1], [0, 1, 0, 2], [0, 0, 1, 3]])
    assert list(matrix[:3, 3]) == [1, 2, 3]
    with pytest.raises(ValueError):
        objmodels.rootTransform({"rotation": {"w": 90}})


def testAmbientMaterialsAreNotShaded():
    files = {"assets/somemod/models/block/pyramid.mtl": PYRAMID_MTL.replace("newmtl side\n", "newmtl side\nKa 1 1 1\n")}
    shaded = objmodels.objFaces(PYRAMID, True, files.__getitem__, PATH)
    assert [face.shade for face in shaded] == [True, False, False]
    assert [face.shade for face in objmodels.objFaces(PYRAMID, True, files.__getitem__, PATH,
                                                      emissiveAmbient=False)] == [True, True, True]
    assert not any(face.shade for face in faces(shadeQuads=False))


def testModelNamesFromEachVersion():
    assert objmodels.modelPaths("somemod:models/block/thing.obj") == ["assets/somemod/models/block/thing.obj"]
    assert objmodels.modelPaths("somemod:thing.obj") == ["assets/somemod/models/thing.obj",
                                                         "assets/somemod/models/block/thing.obj"]
    assert objmodels.materialPath("assets/somemod/models/block/thing.obj", "thing.mtl") == \
        "assets/somemod/models/block/thing.mtl"
    assert objmodels.materialPath("assets/somemod/models/block/thing.obj", "somemod:models/x.mtl") == \
        "assets/somemod/models/x.mtl"
