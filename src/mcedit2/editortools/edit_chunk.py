"""
    player
"""

import logging
from PySide6 import QtWidgets
from PySide6.QtCore import QT_TRANSLATE_NOOP

from mcedit2.editortools import EditorTool
from mcedit2.ui.editortools.select_chunk import Ui_selectChunkWidget


log = logging.getLogger(__name__)


class ChunkToolWidget(QtWidgets.QWidget, Ui_selectChunkWidget):
    def __init__(self, *args, **kwargs):
        super(ChunkToolWidget, self).__init__(*args, **kwargs)
        self.setupUi(self)


class ChunkTool(EditorTool):
    name = QT_TRANSLATE_NOOP("EditorTool", "Inspect Chunk")
    iconName = "edit_chunk"

    def __init__(self, editorSession, *args, **kwargs):
        """
        :type editorSession: EditorSession
        """
        super(ChunkTool, self).__init__(editorSession, *args, **kwargs)
        self.mousePos = None
        self.toolWidget = ChunkToolWidget()

    def mousePress(self, event):
        self.setMousePos(event.blockPosition)

    def setMousePos(self, pos):
        x, y, z = self.mousePos = pos
        cx = x >> 4
        cz = z >> 4
        self.editorSession.inspectChunk(cx, cz)

