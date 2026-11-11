"""
    objmodels

Forge's and NeoForge's OBJ models: Wavefront .obj files, with their materials in .mtl files, which mods use for
blocks in place of the game's own model files. Here they are read into faces of four corners each, for
blockmodels to draw as it does the faces of the game's models, the way NeoForge's ObjModel reads them:

- A face takes its first four corners; a triangle has its last corner twice. Faces with more corners are not split.
- Faces with no material are not drawn. A material names its texture with map_Kd, which may be a texture variable
  of the model such as "#all", and may tint the face with Kd and forge_TintIndex.
- Parts made with "o" and "g" can be hidden by the model's "visibility", by their name or, for a part in another,
  "outer/inner".
- The model's "transform" moves, turns and scales it, about the point its "origin" gives.
- With "automatic_culling", on by default, a face lying on a side of the block and looking out of it is hidden
  when the block beside it covers that side, as a face of the game's models with "cullface" is.

A position is in blocks, with the block from 0 to 1 on each axis. A texture coordinate in an .obj file runs from
the bottom of the texture up, but the game reads it from the top down unless the model asks it to flip it, which
most do.
"""
import collections
import math
import posixpath
import re

import numpy as np

# A face ready to draw: its texture as the material names it (a texture name, "#variable", or None for the missing
# texture), its corners as (x, y, z, u, v) with v from the top of the texture, the side it looks toward most, the
# side whose neighbor hides it or None, whether it is shaded by the side it looks toward, its tint index or -1, and
# the color of its material as (r, g, b, a) from 0 to 1.
ObjFace = collections.namedtuple("ObjFace", "texture corners side cullface shade tintindex color")

# A face as read from the file: its material name or None, its corners as (position, uv or None, normal or None),
# and the names of the parts it is in, outermost first.
_RawFace = collections.namedtuple("_RawFace", "material corners parts")

_Material = collections.namedtuple("_Material", "texture color ambient tintindex")

DEFAULT_COORDS = ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0))
SIDES = (("down", (0, -1, 0)), ("up", (0, 1, 0)), ("north", (0, 0, -1)), ("south", (0, 0, 1)),
         ("west", (-1, 0, 0)), ("east", (1, 0, 0)))
EPSILON = 1.0e-5

# A texture given as a file, as some exporters write it, becomes a texture name as NeoForge makes it.
_FILE_PATH = re.compile(r"(?:.*[\\/]assets[\\/](?P<namespace>[a-z_-]+)[\\/]textures[\\/])?(?P<path>[a-z_\\/-]+)\.png")


def _lines(text):
    """ The words of each line, with comments and blank lines left out and lines ending in a backslash joined. """
    pending = []
    for line in text.splitlines():
        if line.startswith("#"):
            line = ""
        continuation = line.endswith("\\")
        words = (line[:-1] if continuation else line).split()
        pending.extend(words)
        if continuation:
            continue
        if pending:
            yield pending
        pending = []
    if pending:
        yield pending


def _vector(words, count, default):
    values = [float(w) for w in words[1:1 + count]]
    return tuple(values + list(default[len(values):]))


def parseObj(text):
    """
    The faces of an .obj file, and the material files it names.

    :return: ([_RawFace, ...], [material file, ...])
    """
    positions = []
    uvs = []
    normals = []
    faces = []
    libraries = []
    material = None
    group = None
    obj = None
    objAboveGroup = False
    for words in _lines(text):
        key = words[0]
        if key == "v":
            x, y, z, w = _vector(words, 4, (0.0, 0.0, 0.0, 1.0))
            positions.append((x / w, y / w, z / w))
        elif key == "vt":
            uvs.append(_vector(words, 2, (0.0, 0.0)))
        elif key == "vn":
            normals.append(_vector(words, 3, (0.0, 0.0, 0.0)))
        elif key == "f":
            corners = []
            for corner in words[1:]:
                indices = corner.split("/")
                position = positions[_index(indices[0], len(positions))]
                uv = uvs[_index(indices[1], len(uvs))] if len(indices) > 1 and indices[1] and uvs else None
                normal = normals[_index(indices[2], len(normals))] if len(indices) > 2 and indices[2] and normals \
                    else None
                corners.append((position, uv, normal))
            if corners:
                parts = tuple(p for p in (group, obj) if p is not None) or ("",)
                faces.append(_RawFace(material, corners, parts))
        elif key == "usemtl":
            material = " ".join(words[1:])
        elif key == "mtllib":
            libraries.append(" ".join(words[1:]))
        elif key == "g" and len(words) > 1:
            if objAboveGroup:
                obj = group + "/" + words[1]
            else:
                group, obj = words[1], None
        elif key == "o" and len(words) > 1:
            if objAboveGroup or group is None:
                objAboveGroup = True
                group, obj = words[1], None
            else:
                obj = group + "/" + words[1]
    return faces, libraries


def _index(text, count):
    """ An index of an .obj file, which counts from 1, or back from the end when negative. """
    index = int(text)
    return index - 1 if index > 0 else count + index


def parseMtl(text):
    """ The materials of an .mtl file: {name: _Material}. """
    materials = {}
    name = None
    fields = {}

    def finish():
        if name is not None:
            materials[name] = _Material(fields.get("texture"), fields.get("color", (1.0, 1.0, 1.0, 1.0)),
                                        fields.get("ambient", (0.0, 0.0, 0.0, 0.0)), fields.get("tintindex", -1))

    for words in _lines(text):
        key = words[0]
        if key == "newmtl":
            finish()
            name = " ".join(words[1:])
            fields = {}
        elif name is None:
            continue
        elif key == "map_Kd" and len(words) > 1:
            fields["texture"] = words[-1]  # options such as "-s 1 1 1" come first
        elif key == "Kd":
            fields["color"] = _vector(words, 4, (0.0, 0.0, 0.0, 1.0))
        elif key == "Ka":
            fields["ambient"] = _vector(words, 4, (0.0, 0.0, 0.0, 1.0))
        elif key in ("forge_TintIndex", "neoforge_TintIndex") and len(words) > 1:
            fields["tintindex"] = int(words[1])
    finish()
    return materials


def textureName(texture):
    """ A material's texture as the model would name it: "#variable" or "namespace:path", or None for none. """
    if texture is None or texture.startswith("#"):
        return texture
    match = _FILE_PATH.match(texture)
    if match:
        path = match.group("path").replace("\\", "/")
        return match.group("namespace") + ":" + path if match.group("namespace") else path
    return texture


# Transforms


def _rotationMatrix(axis, degrees):
    """ A turn about an axis by the right-hand rule, as a 4x4 matrix for column vectors. """
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    m = np.identity(4)
    i, j = {"x": (1, 2), "y": (2, 0), "z": (0, 1)}[axis]
    m[i, i] = c
    m[i, j] = -s
    m[j, i] = s
    m[j, j] = c
    return m


def _quaternionMatrix(x, y, z, w):
    m = np.identity(4)
    m[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                 [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                 [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return m


def _axisRotation(value):
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError("expected one axis and its angle, got %r" % (value,))
    (axis, degrees), = value.items()
    if axis not in ("x", "y", "z"):
        raise ValueError("unknown axis %r" % axis)
    return _rotationMatrix(axis, float(degrees))


def _rotation(value):
    """ A rotation as NeoForge's transforms give it: axis turns, three angles in degrees, or a quaternion. """
    if isinstance(value, dict):
        return _axisRotation(value)
    if isinstance(value, list) and value:
        if isinstance(value[0], dict):
            m = np.identity(4)
            for turn in value:
                m = m @ _axisRotation(turn)
            return m
        if len(value) == 3:
            x, y, z = (float(v) for v in value)
            return _rotationMatrix("x", x) @ _rotationMatrix("y", y) @ _rotationMatrix("z", z)
        if len(value) == 4:
            return _quaternionMatrix(*(float(v) for v in value))
    raise ValueError("expected a rotation, got %r" % (value,))


def _floats(value, count):
    if not isinstance(value, list) or len(value) != count:
        raise ValueError("expected %d numbers, got %r" % (count, value))
    return [float(v) for v in value]


def _translation(x, y, z):
    m = np.identity(4)
    m[:3, 3] = (x, y, z)
    return m


ORIGINS = {"center": (0.5, 0.5, 0.5), "corner": (0.0, 0.0, 0.0), "opposing-corner": (1.0, 1.0, 1.0)}


def rootTransform(value):
    """
    The 4x4 matrix, for column vectors, of a model's "transform", or None if it changes nothing. As in the game, an
    origin is about the block's center, and a transform with none turns and scales about its "opposing corner".
    """
    if value is None or value == "identity":
        return None
    if isinstance(value, str):
        raise ValueError("unknown transform %r" % value)
    if isinstance(value, list):
        return _matrix(value)
    if not isinstance(value, dict):
        raise ValueError("expected a transform, got %r" % (value,))
    if "matrix" in value:
        return _matrix(value["matrix"])
    m = np.identity(4)
    if "translation" in value:
        m = m @ _translation(*_floats(value["translation"], 3))
    left = value.get("rotation", value.get("left_rotation"))
    if left is not None:
        m = m @ _rotation(left)
    if "scale" in value:
        scale = value["scale"]
        scale = [float(scale)] * 3 if isinstance(scale, (int, float)) else _floats(scale, 3)
        m = m @ np.diag(scale + [1.0])
    right = value.get("right_rotation", value.get("post-rotation"))
    if right is not None:
        m = m @ _rotation(right)
    origin = value.get("origin", "opposing-corner")
    origin = ORIGINS[origin] if isinstance(origin, str) and origin in ORIGINS else _floats(origin, 3)
    m = _translation(*origin) @ m @ _translation(*(-o for o in origin))
    return None if np.allclose(m, np.identity(4)) else m


def _matrix(rows):
    if not isinstance(rows, list) or len(rows) != 3:
        raise ValueError("expected a matrix of three rows, got %r" % (rows,))
    m = np.identity(4)
    for i, row in enumerate(rows):
        m[i, :] = _floats(row, 4)
    return m


# Faces


def _normalize(v):
    length = math.sqrt(sum(c * c for c in v))
    return tuple(c / length for c in v) if length else (0.0, 0.0, 0.0)


def _faceNormal(points):
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = points[:3]
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    return _normalize((uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx))


def nearestSide(normal):
    """ The side a normal points toward most, as the game's Direction.getNearest picks it. """
    best, bestDot = SIDES[2][0], -float("inf")
    for side, (dx, dy, dz) in SIDES:
        dot = normal[0] * dx + normal[1] * dy + normal[2] * dz
        if dot > bestDot:
            best, bestDot = side, dot
    return best


def cullSide(points, normal):
    """ The side of the block a face lies on and looks out of, or None, tried in the game's order. """
    for side, axis, value, outward in (("west", 0, 0.0, -1), ("east", 0, 1.0, 1), ("north", 2, 0.0, -1),
                                       ("south", 2, 1.0, 1), ("down", 1, 0.0, -1), ("up", 1, 1.0, 1)):
        if all(abs(p[axis] - value) < EPSILON for p in points) and normal[axis] * outward > 0:
            return side
    return None


def _visible(parts, visibility):
    """ Whether a face in these parts shows: each part, outermost first, must not be hidden. """
    return all(visibility.get(part, True) for part in parts)


def objFaces(text, flipV=False, readText=None, objPath="", materialOverride=None, visibility=None, transform=None,
             automaticCulling=True, shadeQuads=True, emissiveAmbient=True):
    """
    The faces of an .obj file as ObjFace, ready to draw.

    :param readText: reads another file, as "assets/<namespace>/...", for the materials
    :param materialOverride: the model's "mtl_override", read in place of the files the .obj file names
    :param visibility: the model's "visibility": {part name: False} hides that part
    :param transform: the model's "transform" as from rootTransform(), or None
    """
    faces, libraries = parseObj(text)
    if materialOverride:
        libraries = [materialOverride]
    materials = {}
    for library in libraries:
        if readText is None:
            break
        try:
            materials.update(parseMtl(readText(materialPath(objPath, library))))
        except Exception:
            continue
    visibility = visibility or {}

    if transform is not None:
        # The transform is about the block's center, and the positions here are from its corner.
        transform = _translation(0.5, 0.5, 0.5) @ transform @ _translation(-0.5, -0.5, -0.5)
        normalMatrix = np.linalg.inv(transform[:3, :3]).T

    result = []
    for face in faces:
        material = materials.get(face.material) if face.material is not None else None
        if material is None or not _visible(face.parts, visibility):
            continue  # NeoForge draws no face without a material
        corners = [face.corners[min(i, len(face.corners) - 1)] for i in range(4)]
        if any(normal is None for _, _, normal in face.corners):
            faceNormal = _faceNormal([position for position, _, _ in face.corners[:3]]) \
                if len(face.corners) >= 3 else (0.0, 1.0, 0.0)
            normals = [faceNormal] * 4
        else:
            normals = [normal for _, _, normal in corners]
        points = []
        for i, (position, uv, _) in enumerate(corners):
            if uv is None:
                uv = DEFAULT_COORDS[i]
            u, v = uv
            if transform is not None:
                position = tuple(transform @ (position + (1.0,)))[:3]
            points.append((position[0], position[1], position[2], u, 1.0 - v if flipV else v))
        normal = normals[0]
        if transform is not None:
            normal = _normalize(tuple(normalMatrix @ normal))
        shade = shadeQuads
        if emissiveAmbient:
            ambient = material.ambient
            shade = shadeQuads and int((ambient[0] + ambient[1] + ambient[2]) * 15 / 3.0) == 0
        cullface = cullSide(points, normal) if automaticCulling else None
        result.append(ObjFace(textureName(material.texture), points, nearestSide(normal), cullface, shade,
                              material.tintindex, material.color))
    return result


def modelPaths(name):
    """
    Where a model named in a model or blockstate file may be, as "assets/<namespace>/models/<path>". NeoForge and
    Forge from 1.13 give the whole path from the namespace, as "somemod:models/block/thing.obj". Forge 1.12 gives
    the path from models/, and from models/block/ in a blockstate file that is not of Forge's own kind.
    """
    namespace, colon, path = name.rpartition(":")
    namespace = namespace if colon else "minecraft"
    if path.startswith("models/"):
        return ["assets/%s/%s" % (namespace, path)]
    return ["assets/%s/models/%s" % (namespace, path), "assets/%s/models/block/%s" % (namespace, path)]


def materialPath(objPath, library):
    """ An .mtl file an .obj file names, which may be a resource name or a file beside the .obj file. """
    if ":" in library:
        namespace, path = library.split(":", 1)
        return "assets/%s/%s" % (namespace, path if path.startswith("models/") else "models/" + path)
    return posixpath.normpath(posixpath.join(posixpath.dirname(objPath), library))
