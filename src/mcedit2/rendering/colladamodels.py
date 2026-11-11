"""
    colladamodels

Collada (.dae) models, as some mods' own model loaders read, such as HBM's doors: the triangles of each geometry a
scene's nodes place, with each node's matrix, made into faces as an OBJ model's are. Only what a model exported from
Blender has is read: triangles and polylists of positions, normals and texture coordinates, and nodes with a matrix
or translate, rotate and scale. The models these loaders read are made with y up whatever the file says its up axis
is, as HBM's say Z_UP, and are read that way, as the loaders do.
"""
import logging
import math
import xml.etree.ElementTree as ElementTree

import numpy as np

from mcedit2.rendering import objmodels

log = logging.getLogger(__name__)


def _local(tag):
    """ A tag without its namespace. """
    return tag.rpartition("}")[2]


def _children(element, name):
    return [child for child in element if _local(child.tag) == name]


def _child(element, name):
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _floats(text):
    return [float(v) for v in (text or "").split()]


def _ints(text):
    return [int(v) for v in (text or "").split()]


def _sources(mesh):
    """ Each source of a mesh by its id, as a list of tuples of its accessor's stride. """
    sources = {}
    for source in _children(mesh, "source"):
        array = _child(source, "float_array")
        if array is None:
            continue
        values = _floats(array.text)
        stride = 1
        technique = _child(source, "technique_common")
        accessor = _child(technique, "accessor") if technique is not None else None
        if accessor is not None:
            stride = int(accessor.get("stride", "1"))
        sources[source.get("id")] = [tuple(values[i:i + stride]) for i in range(0, len(values) - stride + 1, stride)]
    return sources


def _meshTriangles(mesh):
    """ The triangles of a mesh, each three corners of (position, texture coordinates or None, normal or None). """
    sources = _sources(mesh)
    vertexInputs = {}
    vertices = _child(mesh, "vertices")
    if vertices is not None:
        for put in _children(vertices, "input"):
            vertexInputs[put.get("semantic")] = put.get("source", "").lstrip("#")
    triangles = []
    for primitive in mesh:
        kind = _local(primitive.tag)
        if kind not in ("triangles", "polylist", "polygons"):
            continue
        inputs = {}
        stride = 0
        for put in _children(primitive, "input"):
            offset = int(put.get("offset", "0"))
            stride = max(stride, offset + 1)
            semantic = put.get("semantic")
            source = put.get("source", "").lstrip("#")
            if semantic == "VERTEX":
                inputs["POSITION"] = (offset, vertexInputs.get("POSITION"))
                for extra in ("NORMAL", "TEXCOORD"):
                    if extra in vertexInputs and extra not in inputs:
                        inputs[extra] = (offset, vertexInputs[extra])
            elif semantic in ("NORMAL", "TEXCOORD") and (semantic not in inputs or put.get("set", "0") == "0"):
                inputs[semantic] = (offset, source)
        if "POSITION" not in inputs or stride == 0:
            continue

        def corner(indices, at):
            values = []
            for semantic in ("POSITION", "TEXCOORD", "NORMAL"):
                if semantic not in inputs:
                    values.append(None)
                    continue
                offset, source = inputs[semantic]
                table = sources.get(source)
                index = indices[at * stride + offset]
                values.append(table[index] if table is not None and index < len(table) else None)
            return values

        lists = [_ints(p.text) for p in _children(primitive, "p")]
        if kind == "polylist":
            counts = _ints((_child(primitive, "vcount").text if _child(primitive, "vcount") is not None else ""))
            indices = lists[0] if lists else []
            at = 0
            for count in counts:
                polygon = [corner(indices, at + i) for i in range(count)]
                at += count
                triangles.extend([polygon[0], polygon[i], polygon[i + 1]] for i in range(1, count - 1))
        else:
            for indices in lists:
                count = len(indices) // stride
                if kind == "triangles":
                    triangles.extend([corner(indices, i), corner(indices, i + 1), corner(indices, i + 2)]
                                     for i in range(0, count - 2, 3))
                else:
                    polygon = [corner(indices, i) for i in range(count)]
                    triangles.extend([polygon[0], polygon[i], polygon[i + 1]] for i in range(1, count - 1))
    return triangles


def _nodeMatrix(node):
    """ The matrix a node places its contents with, from its own matrix, or its translate, rotate and scale. """
    m = np.identity(4)
    for part in node:
        kind = _local(part.tag)
        values = _floats(part.text)
        if kind == "matrix" and len(values) == 16:
            m = m @ np.array(values).reshape(4, 4)
        elif kind == "translate" and len(values) == 3:
            t = np.identity(4)
            t[:3, 3] = values
            m = m @ t
        elif kind == "scale" and len(values) == 3:
            m = m @ np.diag(values + [1.0])
        elif kind == "rotate" and len(values) == 4:
            x, y, z, angle = values
            length = math.sqrt(x * x + y * y + z * z) or 1.0
            x, y, z = x / length, y / length, z / length
            c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            r = np.identity(4)
            r[:3, :3] = [[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                         [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                         [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]]
            m = m @ r
    return m


def colladaTriangles(text, honourUpAxis=False):
    """
    Every triangle of a Collada file's scene, with the nodes' matrices applied: a list of three corners each, of
    ((x, y, z), (u, v) or None, (nx, ny, nz) or None).

    :param honourUpAxis: turn the model so that its up axis is y, as the file says; the mods' loaders don't
    """
    root = ElementTree.fromstring(text)
    asset = _child(root, "asset")
    upAxis = "Y_UP"
    scale = 1.0
    if asset is not None:
        up = _child(asset, "up_axis")
        if up is not None and up.text:
            upAxis = up.text.strip()
        unit = _child(asset, "unit")
        if unit is not None:
            scale = float(unit.get("meter", "1"))
    # From the file's axes to Minecraft's, with y up.
    axes = {"Z_UP": np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=float),
            "X_UP": np.array([[0, -1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=float)}
    base = (axes.get(upAxis, np.identity(4)) if honourUpAxis else np.identity(4)) @ np.diag([scale, scale, scale, 1.0])

    geometries = {}
    library = _child(root, "library_geometries")
    for geometry in (_children(library, "geometry") if library is not None else []):
        mesh = _child(geometry, "mesh")
        if mesh is not None:
            geometries[geometry.get("id")] = _meshTriangles(mesh)

    result = []

    def place(node, matrix):
        matrix = matrix @ _nodeMatrix(node)
        normalMatrix = np.linalg.inv(matrix[:3, :3]).T if abs(np.linalg.det(matrix[:3, :3])) > 1e-12 else None
        for instance in _children(node, "instance_geometry"):
            for triangle in geometries.get(instance.get("url", "").lstrip("#"), []):
                corners = []
                for position, uv, normal in triangle:
                    p = matrix @ np.array(list(position[:3]) + [1.0])
                    n = None
                    if normal is not None and normalMatrix is not None:
                        n = normalMatrix @ np.array(normal[:3])
                        length = np.linalg.norm(n)
                        n = tuple(n / length) if length else None
                    corners.append((tuple(p[:3]), tuple(uv[:2]) if uv is not None else None, n))
                result.append(corners)
        for child in _children(node, "node"):
            place(child, matrix)

    scenes = _child(root, "library_visual_scenes")
    for scene in (_children(scenes, "visual_scene") if scenes is not None else []):
        for node in _children(scene, "node"):
            place(node, base)
    if not result and not scenes:
        for triangles in geometries.values():
            for triangle in triangles:
                result.append([(tuple((base @ np.array(list(p[:3]) + [1.0]))[:3]), uv, n) for p, uv, n in triangle])
    return result


def colladaFaces(text, texture="#default", flipV=True, transform=None):
    """
    The faces of a Collada model as an OBJ model's are made, each triangle with its last corner twice, all with one
    texture: the loaders that read these give the model's textures as "default" and the like, not by material.

    :param transform: a 4x4 matrix to place the model with, or None
    """
    faces = []
    for triangle in colladaTriangles(text):
        positions = [corner[0] for corner in triangle]
        normal = objmodels._faceNormal(positions)
        points = []
        for position, uv, _ in triangle + [triangle[-1]]:
            if transform is not None:
                position = tuple(transform @ (tuple(position) + (1.0,)))[:3]
            u, v = uv if uv is not None else (0.0, 0.0)
            points.append((position[0], position[1], position[2], u, 1.0 - v if flipV else v))
        if transform is not None:
            normal = objmodels._faceNormal([p[:3] for p in points[:3]])
        faces.append(objmodels.ObjFace(texture, points, objmodels.nearestSide(normal),
                                       objmodels.cullSide(points, normal), True, -1, (1.0, 1.0, 1.0, 1.0)))
    return faces
