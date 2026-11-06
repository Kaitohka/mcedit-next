"""
    flood_fill
"""

from PySide6 import QtWidgets
import logging
import collections
import time
from PySide6.QtCore import QT_TRANSLATE_NOOP
from PySide6 import QtCore
from mcedit2.editortools import EditorTool
from mcedit2.command import SimplePerformCommand
from mcedit2.editortools.select import SelectionCursor
from mcedit2.util.showprogress import showProgress
from mcedit2.widgets.blockpicker import BlockTypeButton
from mcedit2.widgets.layout import Column, Row
from mceditlib import faces

log = logging.getLogger(__name__)

class FloodFillTool(EditorTool):
    name = QT_TRANSLATE_NOOP("EditorTool", "Flood Fill")
    iconName = "flood_fill"
    modifiesWorld = True

    def mousePress(self, event):
        pos = event.blockPosition
        if self.hoverCheckbox.isChecked():
            pos = pos + event.blockFace.vector

        command = FloodFillCommand(self.editorSession,
                                   pos,
                                   self.blockTypeWidget.block,
                                   self.indiscriminateCheckBox.isChecked(),
                                   self.getFloodDirs())
        self.editorSession.pushCommand(command)

    def mouseMove(self, event):
        self.mouseDrag(event)

    def mouseDrag(self, event):
        self.cursorNode.point = event.blockPosition
        self.cursorNode.face = event.blockFace

    def __init__(self, editorSession, *args, **kwargs):
        super(FloodFillTool, self).__init__(editorSession, *args, **kwargs)

        toolWidget = QtWidgets.QWidget()
        self.toolWidget = toolWidget

        self.cursorNode = SelectionCursor()

        self.floodXPosCheckbox = QtWidgets.QCheckBox(self.tr("X+"), checked=True)
        self.floodXNegCheckbox = QtWidgets.QCheckBox(self.tr("X-"), checked=True)
        self.floodYPosCheckbox = QtWidgets.QCheckBox(self.tr("Y+"), checked=True)
        self.floodYNegCheckbox = QtWidgets.QCheckBox(self.tr("Y-"), checked=True)
        self.floodZPosCheckbox = QtWidgets.QCheckBox(self.tr("Z+"), checked=True)
        self.floodZNegCheckbox = QtWidgets.QCheckBox(self.tr("Z-"), checked=True)
        
        floodDirsLayout = Column(Row(
            self.floodXPosCheckbox, 
            self.floodYPosCheckbox, 
            self.floodZPosCheckbox,
        ), Row(
            self.floodXNegCheckbox, 
            self.floodYNegCheckbox, 
            self.floodZNegCheckbox,
        ), )

        self.blockTypeWidget = BlockTypeButton()
        self.blockTypeWidget.block = self.editorSession.worldEditor.blocktypes["stone"]
        self.blockTypeWidget.editorSession = self.editorSession

        self.indiscriminateCheckBox = QtWidgets.QCheckBox(self.tr("Ignore block meta"))
        self.indiscriminateCheckBox.setChecked(False)

        self.hoverCheckbox = QtWidgets.QCheckBox(self.tr("Hover"))
        toolWidget.setLayout(Column(Row(QtWidgets.QLabel(self.tr("Block:")),
                                        self.blockTypeWidget),
                                    Row(self.hoverCheckbox, self.indiscriminateCheckBox),
                                    floodDirsLayout,
                                    None))

    def getFloodDirs(self):
        return {f: c.isChecked() for f, c in
                ((faces.FaceXIncreasing, self.floodXPosCheckbox),
                 (faces.FaceYIncreasing, self.floodYPosCheckbox),
                 (faces.FaceZIncreasing, self.floodZPosCheckbox),
                 (faces.FaceXDecreasing, self.floodXNegCheckbox),
                 (faces.FaceYDecreasing, self.floodYNegCheckbox),
                 (faces.FaceZDecreasing, self.floodZNegCheckbox))}
    

class FloodFillCommand(SimplePerformCommand):
    def __init__(self, editorSession, point, blockInfo, indiscriminate, floodDirs):
        super(FloodFillCommand, self).__init__(editorSession)
        self.blockInfo = blockInfo
        self.point = point
        self.indiscriminate = indiscriminate
        self.floodDirs = floodDirs

    def perform(self):
        dim = self.editorSession.currentDimension
        point = self.point

        doomedBlock = dim.getBlockID(*point)
        doomedBlockData = dim.getBlockData(*point)
        checkData = (doomedBlock not in (8, 9, 10, 11))  # always ignore data when replacing water/lava xxx forge fluids?
        indiscriminate = self.indiscriminate
        floodDirs = self.floodDirs

        log.info("Flood fill: replacing %s with %s", (doomedBlock, doomedBlockData), self.blockInfo)

        if doomedBlock == self.blockInfo.ID:
            if indiscriminate or doomedBlockData == self.blockInfo.meta:
                return

        if indiscriminate:
            checkData = False
            if doomedBlock == 2:  # grass
                doomedBlock = 3  # dirt

        x, y, z = point
        dim.setBlockID(x, y, z, self.blockInfo.ID)
        dim.setBlockData(x, y, z, self.blockInfo.meta)

        # Stay inside the world's height and the chunks it has. Without that, filling air spreads
        # through the whole sky and into chunks that don't exist, and never ends.
        bounds = dim.bounds
        minY, maxY = bounds.miny, bounds.maxy
        chunkPresent = {}

        def inWorld(nx, ny, nz):
            if not minY <= ny < maxY:
                return False
            key = (nx >> 4, nz >> 4)
            present = chunkPresent.get(key)
            if present is None:
                present = chunkPresent[key] = dim.containsChunk(*key)
            return present

        directions = [offsets for face, offsets in faces.faceDirections if floodDirs[face]]

        def spread():
            start = time.time()
            lastReport = start
            filled = 1
            coords = collections.deque([point])
            while coords:
                x, y, z = coords.popleft()
                for dx, dy, dz in directions:
                    nx, ny, nz = x + dx, y + dy, z + dz
                    if not inWorld(nx, ny, nz):
                        continue
                    b = dim.getBlockID(nx, ny, nz)
                    if indiscriminate:
                        if b == 2:
                            b = 3
                    if b == doomedBlock:
                        if checkData:
                            if dim.getBlockData(nx, ny, nz) != doomedBlockData:
                                continue

                        dim.setBlockID(nx, ny, nz, self.blockInfo.ID)
                        dim.setBlockData(nx, ny, nz, self.blockInfo.meta)
                        coords.append((nx, ny, nz))
                        filled += 1

                # Report often, so that the progress dialog can be seen and cancelled.
                now = time.time()
                if now - lastReport > 0.1:
                    lastReport = now
                    yield QtCore.QCoreApplication.translate("FloodFillCommand", "Filled {0} blocks").format(filled)

            log.info("Flood fill: filled %d blocks in %.1fs", filled, time.time() - start)


        showProgress(QtCore.QCoreApplication.translate("FloodFillCommand", "Flood fill..."), spread(), cancel=True)
