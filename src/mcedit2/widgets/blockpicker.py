"""
    blockpicker
"""


import logging
import re

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt

from mcedit2.ui.widgets.block_picker_multiple import Ui_blockPickerMultiple
from mcedit2.widgets.blockpicker_util import BlockTypesItemWidget
from mceditlib.blocktypes import BlockType

log = logging.getLogger(__name__)


class BlockTypeListFilterModel(QtCore.QSortFilterProxyModel):
    def __init__(self, sourceModel):
        super(BlockTypeListFilterModel, self).__init__()
        self.setSourceModel(sourceModel)
        self.setFilterKeyColumn(0)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.searchID = None
        self.searchMeta = None

    def setSearchString(self, val):
        self.searchID = None
        self.searchMeta = None
        self.setFilterRegularExpression(val)

    def setSearchIdMeta(self, ID, meta):
        self.searchID = ID
        self.searchMeta = meta
        self.setFilterRegularExpression("")

    def filterAcceptsRow(self, row, parentIndex):
        if self.searchID is None:
            return super(BlockTypeListFilterModel, self).filterAcceptsRow(row, parentIndex)
        else:
            child = self.sourceModel().index(row, 0, parentIndex)
            ID, meta = child.data(Qt.UserRole)
            return ID == self.searchID and meta == self.searchMeta

    @property
    def textureAtlas(self):
        return self.sourceModel().textureAtlas

    @property
    def blocktypes(self):
        return self.sourceModel().blocktypes


class SelectedBlockTypesProxyModel(QtCore.QSortFilterProxyModel):
    def __init__(self, sourceModel):
        super(SelectedBlockTypesProxyModel, self).__init__()
        self.setSourceModel(sourceModel)
        self._selectedBlocks = []

    def filterAcceptsRow(self, row, parentIndex):
        child = self.sourceModel().index(row, 0, parentIndex)
        ID, meta = child.data(Qt.UserRole)
        return (ID, meta) in self._selectedBlocks

    def addBlocks(self, blocks):
        for block in blocks:
            self._selectedBlocks.append((block.ID, block.meta))
        self.beginResetModel()
        self.endResetModel()

    def removeBlocks(self, blocks):
        for block in blocks:
            self._selectedBlocks.remove((block.ID, block.meta))
        self.beginResetModel()
        self.endResetModel()

    def selectedBlocks(self):
        return [self.sourceModel().blocktypes[ID, meta] for ID, meta in self._selectedBlocks]

    def setSelectedBlocks(self, blocks):
        self._selectedBlocks = [(block.ID, block.meta) for block in blocks]
        self.beginResetModel()
        self.endResetModel()

    @property
    def textureAtlas(self):
        return self.sourceModel().textureAtlas

    @property
    def blocktypes(self):
        return self.sourceModel().blocktypes


class BlockTypeListModel(QtCore.QAbstractListModel):
    def __init__(self, textureAtlas):
        super(BlockTypeListModel, self).__init__()
        self.textureAtlas = textureAtlas
        self.blocktypesList = list(self.blocktypes)
        self.customBlocks = []

    @property
    def blocktypes(self):
        return self.textureAtlas.blocktypes

    def rowCount(self, index):
        # Not the live number of block types, which grows as a world turns out to have new block states.
        return len(self.blocktypesList) + len(self.customBlocks)

    def data(self, index, role):
        row = index.row()
        if row >= len(self.blocktypesList):
            block = self.customBlocks[row - len(self.blocktypesList)]
        else:
            block = self.blocktypesList[index.row()]

        if role == Qt.DisplayRole:
            return block.displayName
        if role == Qt.UserRole:
            return block.ID, block.meta

    def addCustomBlock(self, block):
        if block in self.customBlocks:
            return
        index = len(self.blocktypesList) + len(self.customBlocks)
        self.beginInsertRows(QtCore.QModelIndex(), index, index)
        self.customBlocks.append(block)
        self.endInsertRows()

    def removeCustomBlocks(self):
        self.beginRemoveRows(QtCore.QModelIndex(),
                             len(self.blocktypesList),
                             len(self.blocktypesList) + len(self.customBlocks) - 1)
        self.customBlocks = []
        self.endRemoveRows()


class BlockTypePicker(QtWidgets.QDialog, Ui_blockPickerMultiple):
    def __init__(self, multipleSelect=False):
        super(BlockTypePicker, self).__init__()
        self.setupUi(self)
        self.multipleSelect = multipleSelect
        if not self.multipleSelect:
            self.selectedBlockList.setVisible(False)

        self.selectButton.clicked.connect(self.accept)
        self.cancelButton.clicked.connect(self.reject)
        self.searchField.editTextChanged.connect(self.setSearchString)
        self.listWidget.blockSelectionChanged.connect(self.selectionDidChange)

        if not self.multipleSelect:
            self.listWidget.doubleClicked.connect(self.accept)
            self.listWidget.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        else:
            self.listWidget.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)

        self.setSizeGripEnabled(True)
        self.listModel = None
        self.filterModel = None
        self.selectedTypesModel = None

    def exec(self):
        self._ensureUpdated()
        self.searchField.setFocus()
        return super(BlockTypePicker, self).exec()

    def selectionDidChange(self, selectedBlocks, deselectedBlocks):
        if self.selectedTypesModel is None:
            return  # no session has set up the picker yet
        if self.multipleSelect:
            self.selectedTypesModel.addBlocks(selectedBlocks)
            self.selectedTypesModel.removeBlocks(deselectedBlocks)
        else:
            self.selectedTypesModel.setSelectedBlocks(selectedBlocks[:1])
        blocks = self.selectedTypesModel.selectedBlocks()

        self.selectedBlockTypesWidget.setBlocks(blocks)

    _textureAtlas = None

    @property
    def textureAtlas(self):
        return self._textureAtlas

    @textureAtlas.setter
    def textureAtlas(self, textureAtlas):
        """

        :type textureAtlas: mcedit2.rendering.textureatlas.TextureAtlas
        """
        self._textureAtlas = textureAtlas
        # Listing every block state takes a while, and most pickers are never opened. It waits until one is.
        self._needsUpdate = True

    _needsUpdate = False

    def _ensureUpdated(self):
        if self._needsUpdate:
            self._needsUpdate = False
            self.updatePicker()

    @property
    def blocktypes(self):
        return self.textureAtlas.blocktypes

    def updatePicker(self):
        if self.textureAtlas is not None:
            log.info("Updating blocktype list widget (multiple=%s) for %s",
                     self.multipleSelect, self.blocktypes)

            self.selectedBlockTypesWidget.setTextureAtlas(self.textureAtlas)

            self.listModel = BlockTypeListModel(self.textureAtlas)
            self.filterModel = BlockTypeListFilterModel(self.listModel)
            self.listWidget.setModel(self.filterModel)
            self.selectedTypesModel = SelectedBlockTypesProxyModel(self.listModel)
            if self.multipleSelect:
                self.selectedBlockList.setModel(self.selectedTypesModel)

            self.searchField.clearEditText()

    @property
    def selectedBlocks(self):
        self._ensureUpdated()
        return self.selectedTypesModel.selectedBlocks()

    @selectedBlocks.setter
    def selectedBlocks(self, val):
        self._ensureUpdated()
        self.listWidget.setSelectedBlocks(val)
        self.selectedTypesModel.setSelectedBlocks(val)

    def setSearchString(self, val):
        # Changing the filterModel's search settings clears listWidget's selection
        # So we have to work around it by disconnecting from the selection change, and then
        # restoring the selection after changing the settings.

        self._ensureUpdated()
        self.listWidget.blockSelectionChanged.disconnect(self.selectionDidChange)
        selectedBlocks = self.selectedBlocks

        match = re.match(r'([0-9]+)(?::([0-9]+))?', val)
        if match:
            ID = int(match.group(1))
            meta = int(match.group(2) or 0)

            block = self.blocktypes[ID, meta]
            if block not in self.blocktypes:
                log.info("Adding custom block %s", block)
                self.listModel.addCustomBlock(block)

            self.filterModel.setSearchIdMeta(ID, meta)
        else:
            self.filterModel.setSearchString(val)

        self.listWidget.blockSelectionChanged.connect(self.selectionDidChange)
        self.selectedBlocks = selectedBlocks

        if not self.multipleSelect:
            if self.filterModel.rowCount() > 0:
                ID, meta = self.filterModel.data(self.filterModel.index(0, 0), Qt.UserRole)
                self.listWidget.setSelectedBlocks([self.blocktypes[ID, meta]])

class BlockTypeButton(QtWidgets.QPushButton):
    def __init__(self, *args, **kwargs):
        self.multipleSelect = kwargs.pop('multipleSelect', False)
        super(BlockTypeButton, self).__init__(*args, **kwargs)

        self._blocks = []
        self.clicked.connect(self.showPicker)
        self.picker = BlockTypePicker(self.multipleSelect)
        self.setLayout(QtWidgets.QStackedLayout())
        self._viewWidget = None
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)

    blocksChanged = QtCore.Signal(list)

    _editorSession = None

    @property
    def editorSession(self):
        return self._editorSession

    @editorSession.setter
    def editorSession(self, editorSession):
        """

        :type editorSession: mcedit2.editorsession.EditorSession
        """
        self._editorSession = editorSession
        self.picker.textureAtlas = editorSession.textureAtlas
        editorSession.configuredBlocksChanged.connect(self.configuredBlocksDidChange)
        self.updateView()

    def configuredBlocksDidChange(self):
        self.picker.textureAtlas = self.editorSession.textureAtlas
        self.updateView()

    def updateView(self):
        if self.editorSession:
            log.info("Updating button with %s", self.blocks)

            layout = self.layout()
            if self._viewWidget:
                layout.removeWidget(self._viewWidget)
            self._viewWidget = BlockTypesItemWidget(self, self.blocks, self.editorSession.textureAtlas)
            layout.addWidget(self._viewWidget)

            assert isinstance(layout, QtWidgets.QLayout)
            self.setMinimumHeight(self._viewWidget.sizeHint().height())

    @property
    def block(self):
        return self._blocks[0] if len(self._blocks) else None

    @block.setter
    def block(self, value):
        self.blocks = [value]

    @property
    def blocks(self):
        return self._blocks

    @blocks.setter
    def blocks(self, value):
        value = [self.editorSession.worldEditor.blocktypes[block]
                 if not isinstance(block, BlockType)
                 else block
                 for block in value]

        old = self._blocks
        self._blocks = value
        if old != value:
            self.blocksChanged.emit(value)
        self.updateView()

    def showPicker(self):
        self.picker.selectedBlocks = self.blocks
        if self.picker.exec():
            self.blocks = self.picker.selectedBlocks
            log.info("Picked block %s", self.block)