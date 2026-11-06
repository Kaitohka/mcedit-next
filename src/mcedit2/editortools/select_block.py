"""
    select block
"""

import logging

from PySide6 import QtWidgets
from PySide6.QtCore import QT_TRANSLATE_NOOP

from mcedit2.editortools import EditorTool
from mcedit2.ui.editortools.select_block import Ui_selectBlockWidget


log = logging.getLogger(__name__)


class SelectBlockToolWidget(QtWidgets.QWidget, Ui_selectBlockWidget):
    def __init__(self, *args, **kwargs):
        super(SelectBlockToolWidget, self).__init__(*args, **kwargs)
        self.setupUi(self)


class SelectBlockTool(EditorTool):
    name = QT_TRANSLATE_NOOP("EditorTool", "Inspect Block")
    iconName = "edit_block"
    selectionRay = None
    currentEntity = None

    def __init__(self, editorSession, *args, **kwargs):
        """
        :type editorSession: EditorSession
        """
        super(SelectBlockTool, self).__init__(editorSession, *args, **kwargs)
        self.mousePos = None
        self.toolWidget = SelectBlockToolWidget()

    def mousePress(self, event):
        self.setMousePos(event.blockPosition)

    def setMousePos(self, pos):
        self.mousePos = pos
        self.editorSession.inspectBlock(pos)









