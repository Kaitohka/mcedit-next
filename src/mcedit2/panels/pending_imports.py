"""
    pending_imports
"""

from PySide6 import QtGui, QtWidgets
import logging

log = logging.getLogger(__name__)

class PendingImportsWidget(QtWidgets.QWidget):
    def __init__(self):
        super(PendingImportsWidget, self).__init__()
        self.importsListWidget = QtWidgets.QListView()
        self.importsListModel = QtGui.QStandardItemModel()
        self.importsListWidget.setModel(self.importsListModel)
        self.importsListWidget.clicked.connect(self.listClicked)
        self.importsListWidget.doubleClicked.connect(self.listDoubleClicked)
