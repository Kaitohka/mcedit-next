"""
    cutaway.py
"""

from math import floor
import logging

from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import QT_TRANSLATE_NOOP
from PySide6 import QtCore

from mcedit2.rendering import worldscene
from mcedit2.rendering.scenegraph import scenenode
from mcedit2.util import profiler
from mcedit2.widgets.layout import Column, Row
from mcedit2.worldview.viewcontrols import ViewControls
from mcedit2.worldview.worldruler import WorldViewRulerGrid
from mcedit2.worldview.worldview import WorldView
from mcedit2.worldview.viewaction import ViewAction, MoveViewMouseAction
from mceditlib import faces
from mceditlib.geometry import Vector
from mceditlib.selection import BoundingBox

log = logging.getLogger(__name__)


def RecenterButton(view):
    dim = view.dimension

    def _Recenter():
        pos = dim.bounds.origin + dim.bounds.size / 2
        view.centerOnPoint(pos)

    button = QtWidgets.QPushButton(QtCore.QCoreApplication.translate("CutawayWorldView", "Recenter"))
    button.clicked.connect(_Recenter)
    return button


def CutawayWorldViewFrame(dimension, textureAtlas, geometryCache, sharedGLWidget):
    viewFrame = QtWidgets.QWidget()
    view = CutawayWorldView(dimension, textureAtlas, geometryCache, sharedGLWidget)

    grid = WorldViewRulerGrid(view)

    x = QtWidgets.QPushButton("x")
    y = QtWidgets.QPushButton("y")
    z = QtWidgets.QPushButton("z")
    buttons = dict(list(zip('xyz', (x, y, z))))

    def to(d):
        def _clicked():
            view.axis = d
            for b in list(buttons.values()):
                b.setChecked(b is buttons[d])

        return _clicked


    x.clicked.connect(to('x'))
    y.clicked.connect(to('y'))
    z.clicked.connect(to('z'))

    depthLabel = QtWidgets.QLabel("?")

    def updateDepthLabel():
        center = view.centerPoint
        text = "%s = %d" % (view.axis.upper(), center[view.dim])
        depthLabel.setText(text)

    view.viewportMoved.connect(updateDepthLabel)
    view.__updateDepthLabel = updateDepthLabel

    viewFrame.viewControls = ViewControls(view)

    buttonBar = Row(None, depthLabel, x, y, z, RecenterButton(view), viewFrame.viewControls.getShowHideButton())

    viewFrame.setLayout(Column(buttonBar, (grid, 1)))
    viewFrame.worldView = view

    return viewFrame


class SlicedWorldScene(scenenode.Node):
    def __init__(self, dimension, textureAtlas):
        super(SlicedWorldScene, self).__init__()
        self.sliceScenes = {}
        self.dimension = dimension
        self.textureAtlas = textureAtlas
        self.dim = 0

    def setTextureAtlas(self, textureAtlas):
        self.textureAtlas = textureAtlas
        for scene in self.sliceScenes.values():
            scene.setTextureAtlas(scene)

    depthLimit = 0  # Number of depths to display
    advancedDepthLimit = 4  # Additional depths to calculate and cache
    loadRadius = 320

    _pos = 0

    @property
    def pos(self):
        return self._pos

    @pos.setter
    def pos(self, value):
        self._pos = value
        for depth, scene in self.sliceScenes.items():
            scene.visible = depth == value

    def discardAllChunks(self):
        for mesh in self.sliceScenes.values():
            mesh.discardAllChunks()

    def invalidateChunk(self, cx, cz):
        for mesh in self.sliceScenes.values():
            mesh.invalidateChunk(cx, cz)

    def clear(self):
        self.sliceScenes.clear()

    def updateMeshes(self):
        v = self.pos
        for depth in range(v - self.depthLimit - self.advancedDepthLimit,
                           v + self.depthLimit + self.advancedDepthLimit + 1):
            if depth not in self.sliceScenes:
                scene = self.sliceScenes[depth] = worldscene.WorldScene(self.dimension, self.textureAtlas,
                                                                        bounds=self.visibleBounds(depth))
                scene.sliceDepth = depth
                scene.visible = depth == v
                i = 0
                for i, child in enumerate(self.children):
                    if child.sliceDepth > depth:
                        break
                self.insertChild(i, scene)
        for old in list(self.sliceScenes.keys()):
            if old < v - self.depthLimit - self.advancedDepthLimit or old > v + self.depthLimit + self.advancedDepthLimit:
                scene = self.sliceScenes.pop(old)
                scene.discardAllChunks()
                self.removeChild(scene)

    def visibleBounds(self, depth=None):
        r = self.loadRadius
        d = r * 2
        # off = list(self.centerPoint)
        off = [-r, -r, -r]
        size = [d, d, d]
        if depth is None:
            depth = self.pos

        off[self.dim] = depth
        size[self.dim] = 1

        return BoundingBox(off, size)

    def wantsChunk(self, c):
        return any([mesh.wantsChunk(c) for mesh in self.sliceScenes.values()])

    def chunkNotPresent(self, cPos):
        for mesh in self.sliceScenes.values():
            mesh.chunkNotPresent(cPos)

    @profiler.iterator("SlicedMesh")
    def workOnChunk(self, c, sections=None):
        # The user can change slices while this generator is suspended.
        for i, mesh in list(self.sliceScenes.items()):
            for _ in mesh.workOnChunk(c, sections):
                yield _

    def chunkInvalid(self, c, deleted):
        for mesh in list(self.sliceScenes.values()):
            mesh.invalidateChunk(*c)

    def setVisibleLayers(self, layerNames):
        for scene in self.sliceScenes.values():
            scene.setVisibleLayers(layerNames)


class CutawayWorldView(WorldView):
    def __init__(self, *a, **kw):
        axis = kw.pop('axis', 'x')
        WorldView.__init__(self, *a, **kw)
        self.axis = axis
        self.viewportMoved.connect(self.updateMeshPos)
        self.viewActions.extend((
            MoveViewMouseAction(),
            CutawaySliceUpAction(),
            CutawaySliceDownAction(),
        ))

    @property
    def cameraVector(self):
        if self.axis == 'x':
            return Vector(-1, 0, 0)
        if self.axis == 'y':
            return Vector(0, -1, 0)
        if self.axis == 'z':
            return Vector(0, 0, -1)


    def createWorldScene(self):
        return SlicedWorldScene(self.dimension, self.textureAtlas)

    def sizeInBlocks(self):
        w, h = self.width(), self.height()
        w *= self.scale
        h *= self.scale

        return w, h

    def updateMatrices(self):
        w, h = self.sizeInBlocks()
        projection = QtGui.QMatrix4x4()
        projection.ortho(-w / 2, w / 2, -h / 2, h / 2, -1000, 2000)
        self.matrixState.projection = projection

        modelview = QtGui.QMatrix4x4()
        if self.axis == 'x':
            modelview.rotate(90., 0., 1., 0.)
        elif self.axis == 'y':
            modelview.rotate(90., 1., 0., 0.)

        modelview.translate(-self.centerPoint[0], -self.centerPoint[1], -self.centerPoint[2])
        self.matrixState.modelview = modelview

    #
    # def viewBounds(self):
    #    min = self.unprojectPoint(0, 0)[0]
    #    max = self.unprojectPoint(self.width(), self.height(), 1.0)[0]
    #
    #    box = BoundingBox(min, (0, 0, 0)).union(BoundingBox(max, (0, 0, 0)))
    #    return box
    #
    #def beginPan(self, x, y):
    #    point, ray = self.unprojectPoint(x, y)
    #    self.dragStart = point
    #    log.debug("Drag start %s", self.dragStart)
    #
    #def continuePan(self, x, y):
    #    point, ray = self.unprojectPoint(x, y)
    #    d = point - self.dragStart
    #    self.centerPoint -= d
    #    log.debug("Drag continue delta %s", d)
    #
    #def endPan(self):
    #    self.dragStart = None
    #    log.debug("Drag end")

    def slicedPoint(self, x, y):
        ray = self.rayAtPosition(x, y)
        pos = list(ray.point)
        pos[self.dim] = self.centerPoint[self.dim]
        return Vector(*pos)

    def viewCenter(self):
        """Return the world position at the center of the view."""
        return self.slicedPoint(self.width() / 2, self.height() / 2)

    _axis = "x"

    @property
    def axis(self):
        return self._axis

    @axis.setter
    def axis(self, value):
        center = self.viewCenter()
        self._axis = value
        self._updateMatrices()
        self.centerOnPoint(center)
        self.worldScene.discardAllChunks()
        self.worldScene.clear()
        self.worldScene.dim = self.dim
        self.updateMeshPos()
        self.worldScene.updateMeshes()

        yp = {
            'x': (0, 90),
            'y': (0, 0),
            'z': (0, 90),
        }
        self.compassNode.yawPitch = yp[value]

    @property
    def dim(self):
        return 'xyz'.index(self.axis)

    def updateMeshPos(self):
        self.worldScene.pos = int(floor(self.centerPoint[self.dim]))
        self.worldScene.updateMeshes()

    def resizeEvent(self, event):
        super(CutawayWorldView, self).resizeEvent(event)
        self.worldScene.loadRadius = 4 * max(event.size().width(), event.size().height())

    def augmentMouseEvent(self, event):
        pos = event.position().toPoint()
        x, y = pos.x(), pos.y()

        # View actions read the position with event.x() and event.y().
        event.x = lambda: x
        event.y = lambda: y

        ray = self.rayAtPosition(x, y)

        event.point = self.slicedPoint(x, y)
        blockPos = event.point.intfloor()

        self.mouseBlockPos = event.blockPosition = blockPos
        vec = [0, 0, 0]
        vec[self.dim] = 1
        self.mouseBlockFace = event.blockFace = faces.Face.fromVector(vec)
        self.mouseRay = event.ray = ray
        event.view = self


class CutawaySliceUpAction(ViewAction):
    settingsKey = "worldview.cutaway.slice_up"
    button = ViewAction.WHEEL_UP
    labelText = QT_TRANSLATE_NOOP("ViewAction", "Slice Up")
    acceptsMouseWheel = True

    def buttonPressEvent(self, event):
        p = list(event.view.centerPoint)
        p[event.view.dim] += 1

        event.view.centerPoint = p

class CutawaySliceDownAction(ViewAction):
    settingsKey = "worldview.cutaway.slice_down"
    button = ViewAction.WHEEL_DOWN
    labelText = QT_TRANSLATE_NOOP("ViewAction", "Slice Down")
    acceptsMouseWheel = True

    def buttonPressEvent(self, event):
        p = list(event.view.centerPoint)
        p[event.view.dim] -= 1

        event.view.centerPoint = p
