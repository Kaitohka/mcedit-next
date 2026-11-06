"""
    camera
"""

import logging
from PySide6 import QtWidgets

from mcedit2.widgets.layout import Column
from mcedit2.worldview.camera import MaxViewDistanceSetting

log = logging.getLogger(__name__)

class CameraPrefs(QtWidgets.QWidget):
    def __init__(self):
        super(CameraPrefs, self).__init__()
        self.labelText = self.tr("Camera View")
        
        layout = QtWidgets.QFormLayout()
        
        maxViewDistanceInput = QtWidgets.QSpinBox()
        maxViewDistanceInput.setMinimum(0)
        maxViewDistanceInput.valueChanged.connect(MaxViewDistanceSetting.setValue)
        MaxViewDistanceSetting.connectAndCall(maxViewDistanceInput.setValue)
        
        layout.addRow(self.tr("Max View Distance"), maxViewDistanceInput)
        
        self.setLayout(Column(layout, None))