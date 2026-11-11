"""
    collada_test

    Collada (.dae) models, as HBM's door loader reads them: triangles placed by their nodes, y up.
"""
import numpy

from mcedit2.rendering import colladamodels, objmodels

MODEL = b"""<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <asset><unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>
  <library_geometries>
    <geometry id="square-mesh">
      <mesh>
        <source id="square-positions">
          <float_array id="square-positions-array" count="12">0 0 0 1 0 0 1 1 0 0 1 0</float_array>
          <technique_common><accessor source="#square-positions-array" count="4" stride="3"/></technique_common>
        </source>
        <source id="square-uv">
          <float_array id="square-uv-array" count="8">0 0 1 0 1 1 0 1</float_array>
          <technique_common><accessor source="#square-uv-array" count="4" stride="2"/></technique_common>
        </source>
        <vertices id="square-vertices"><input semantic="POSITION" source="#square-positions"/></vertices>
        <triangles count="2">
          <input semantic="VERTEX" source="#square-vertices" offset="0"/>
          <input semantic="TEXCOORD" source="#square-uv" offset="1" set="0"/>
          <p>0 0 1 1 2 2 0 0 2 2 3 3</p>
        </triangles>
      </mesh>
    </geometry>
  </library_geometries>
  <library_visual_scenes>
    <visual_scene id="Scene">
      <node id="frame" name="frame">
        <matrix sid="transform">1 0 0 2 0 1 0 3 0 0 1 0 0 0 0 1</matrix>
        <instance_geometry url="#square-mesh"/>
        <node id="part" name="part">
          <matrix sid="transform">1 0 0 0 0 1 0 1 0 0 1 0 0 0 0 1</matrix>
          <instance_geometry url="#square-mesh"/>
        </node>
      </node>
    </visual_scene>
  </library_visual_scenes>
</COLLADA>"""


def testTrianglesArePlacedByTheirNodes():
    triangles = colladamodels.colladaTriangles(MODEL)
    assert len(triangles) == 4
    positions = numpy.array([corner[0] for triangle in triangles for corner in triangle])
    # Moved by the frame, and the part by the frame and itself; y stays up whatever the file says.
    assert positions[:, 0].min() == 2 and positions[:, 0].max() == 3
    assert positions[:, 1].min() == 3 and positions[:, 1].max() == 5
    assert numpy.allclose(positions[:, 2], 0)
    assert triangles[0][1][1] == (1.0, 0.0)


def testFacesAreTrianglesWithOneTextureAndTheirTextureUpsideDown():
    faces = colladamodels.colladaFaces(MODEL, "#default", flipV=True,
                                       transform=objmodels.rootTransform({"translation": [0.5, 0, 0.5]}))
    assert len(faces) == 4
    face = faces[0]
    assert face.texture == "#default" and face.side == "south"
    assert face.corners[3] == face.corners[2]  # a triangle repeats its last corner
    assert face.corners[0][:3] == (2.5, 3.0, 0.5)
    assert face.corners[1][3:] == (1.0, 1.0)  # v from the bottom made v from the top
