"""
    configureblocksdialog.py
"""

import json
import logging
import os
import shutil
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt

from mcedit2.ui.dialogs.configure_blocks import Ui_configureBlocks
from mcedit2.util.directories import getUserFilesDirectory
from mcedit2.widgets.blocktype_list import TexturePixmap
from mceditlib.blocktypes import pack_library
from mceditlib.blocktypes.block_pack import BlockPack, BlockPackError

log = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = "assets/minecraft/models/block/cube_all.json"


def splitResourceName(name):
    namespace, separator, path = name.rpartition(":")
    return (namespace, path) if separator else ("minecraft", name)


def modelPathFromName(name):
    """ Return the path of a model in the game's assets, from a name like "minecraft:block/cube_all". """
    if name.startswith("assets/"):
        return name
    return "assets/%s/models/%s.json" % splitResourceName(name)


def modelDisplayName(modelPath):
    """ Return a short name to list a model by, with its namespace unless it is the game's own. """
    namespace, _, rest = modelPath[len("assets/"):].partition("/models/")
    rest = rest.replace(".json", "")
    if namespace == "minecraft":
        return rest.replace("block/", "", 1)
    return "%s:%s" % (namespace, rest)


class BlockDefinition(object):
    """
    A user's description of a block, which comes before anything else that describes it. Only
    `internalName` is needed. So a definition written by hand can be as short as this:

        { "internalName": "somemod:copper_machine",
          "model": "minecraft:block/cube_all",
          "textures": { "all": "somemod:block/copper_machine" } }

    `model` and `textures` are other names for `modelPath` and `modelTextures`. With textures and no model,
    the model is a cube.

    `blockState` limits it to one state, in a form like "[facing=north]". `meta` does the same for
    worlds from before Minecraft 1.13. `opacity` and `brightness` are left as they are if not given.
    """

    def __init__(self, internalName=None, defJson=None):
        super(BlockDefinition, self).__init__()
        assert internalName or defJson, "Need at least one of internalName or defJson to create BlockDefinition"
        if defJson is None:
            defJson = {}
        self.internalName = internalName or defJson['internalName']
        self.rotationFlags = defJson.get('rotationFlags', [])
        self.meta = defJson.get('meta', 0)
        self.blockState = defJson.get('blockState', "")
        self.opacity = defJson.get('opacity', None)
        self.brightness = defJson.get('brightness', None)
        self.unlocalizedName = defJson.get('unlocalizedName', self.internalName)
        self.englishName = defJson.get('englishName', self.internalName)
        self.modelTextures = defJson.get('modelTextures', defJson.get('textures', {}))
        modelPath = defJson.get('modelPath', defJson.get('model', None))
        if modelPath is None and self.modelTextures:
            modelPath = DEFAULT_MODEL_PATH
        self.modelPath = modelPathFromName(modelPath) if modelPath else None
        self.modelRotations = defJson.get('modelRotations', [0, 0])

    def exportAsJson(self):
        keys = ['internalName',
                'rotationFlags',
                'meta',
                'blockState',
                'opacity',
                'brightness',
                'unlocalizedName',
                'englishName',
                'modelPath',
                'modelRotations',
                'modelTextures']

        d = {}
        for key in keys:
            d[key] = getattr(self, key)

        return d


class ConfigureBlocksItemDelegate(QtWidgets.QStyledItemDelegate):
    pass

class TextureListModel(QtCore.QAbstractListModel):
    def __init__(self, resourceLoader):
        super(TextureListModel, self).__init__()
        self.resourceLoader = resourceLoader
        self.textureNames = list(resourceLoader.blockTexturePaths())
        self.texturePixmaps = {}

    def zipfilePaths(self):
        zips = set()
        for zipFilename, _ in self.textureNames:
            if zipFilename in zips:
                continue
            zips.add(zipFilename)
            yield zipFilename

    def rowCount(self, parent):
        if parent.isValid():
            return 0
        return len(self.textureNames)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return

        row = index.row()
        zipFilename, texturePath = self.textureNames[row]
        if role == Qt.DisplayRole:
            return texturePath.rsplit("/", 1)[-1]
        if role == Qt.DecorationRole:
            pixmap = self.texturePixmaps.get(texturePath)
            if pixmap:
                return pixmap

            f = self.resourceLoader.openStream(texturePath)
            pixmap = TexturePixmap(f, 48, texturePath)
            self.texturePixmaps[texturePath] = pixmap
            return pixmap
        if role == self.TexturePathRole:
            return texturePath
        if role == self.ZipfilePathRole:
            return zipFilename

    TexturePathRole = Qt.UserRole
    ZipfilePathRole = Qt.UserRole + 1


class ConfigureBlocksItemModel(QtCore.QAbstractItemModel):

    def __init__(self, *args, **kwargs):
        super(ConfigureBlocksItemModel, self).__init__(*args, **kwargs)
        self.headerTitles = ["Icon",
                             "Block ID",
                             "Rotation Flags",
                             "State",
                             "Opacity",
                             "Brightness",
                             "Unlocalized Name",
                             "Name"]

        definedBlocksFilename = "defined_blocks.json"
        self.definedBlocksFilePath = os.path.join(getUserFilesDirectory(), definedBlocksFilename)
        try:
            definedBlocks = json.load(open(self.definedBlocksFilePath, "r", encoding="utf-8"))
        except FileNotFoundError:
            definedBlocks = []
        except (ValueError, EnvironmentError) as e:
            log.warning("Failed to read definitions file %s", definedBlocksFilename)
            definedBlocks = []
        if not isinstance(definedBlocks, list):
            definedBlocks = []
        self.definedBlocks = []

        for defJson in definedBlocks:
            try:
                self.definedBlocks.append(BlockDefinition(defJson=defJson))
            except (KeyError, ValueError) as e:
                log.warning("Failed to load a definition from %s: %r", definedBlocksFilename, e)

    def exportAsJson(self):
        defs = []
        for blockDef in self.definedBlocks:
            defs.append(blockDef.exportAsJson())
        return defs

    def writeToJson(self):
        json.dump(self.exportAsJson(), open(self.definedBlocksFilePath, "w", encoding="utf-8"))

    def headerData(self, column, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Vertical:
            return None
        if role == Qt.DisplayRole:
            return self.headerTitles[column]

    def columnCount(self, index):
        return len(self.headerTitles)

    def rowCount(self, index):
        if index.isValid():
            return 0
        return len(self.definedBlocks)

    def index(self, row, column, parent=QtCore.QModelIndex()):
        if parent.isValid():
            return QtCore.QModelIndex()

        return self.createIndex(row, column, None)

    def parent(self, index):
        return QtCore.QModelIndex()

    COL_ICON = 0
    COL_ID = 1
    COL_ROTATION = 2
    COL_STATE = 3
    COL_OPACITY = 4
    COL_BRIGHTNESS = 5
    COL_UNLOCALIZED = 6
    COL_ENGLISH = 7

    def flags(self, index):
        if not index.isValid():
            return 0

        flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
        if index.column() in (self.COL_ID,
                              self.COL_STATE,
                              self.COL_OPACITY,
                              self.COL_BRIGHTNESS,
                              self.COL_UNLOCALIZED,
                              self.COL_ENGLISH,
                              ):
            flags |= Qt.ItemIsEditable

        return flags

    def data(self, index, role=Qt.DisplayRole):
        row = index.row()
        column = index.column()
        blockDef = self.definedBlocks[row]
        if role == Qt.DisplayRole:
            if column == self.COL_ICON:
                return None
            if column == self.COL_ID:
                return blockDef.internalName
            if column == self.COL_ROTATION:
                return blockDef.rotationFlags
            if column == self.COL_STATE:
                return blockDef.blockState or (blockDef.meta if blockDef.meta else "")
            if column == self.COL_OPACITY:
                return blockDef.opacity
            if column == self.COL_BRIGHTNESS:
                return blockDef.brightness
            if column == self.COL_UNLOCALIZED:
                return blockDef.unlocalizedName
            if column == self.COL_ENGLISH:
                return blockDef.englishName

    def setData(self, index, value, role=Qt.DisplayRole):
        row = index.row()
        column = index.column()
        blockDef = self.definedBlocks[row]
        if role == Qt.EditRole:
            try:
                if column == self.COL_ID:
                    blockDef.internalName = value
                if column == self.COL_STATE:
                    # A number is a meta, for worlds from before Minecraft 1.13.
                    value = str(value).strip()
                    blockDef.meta = int(value) if value.isdigit() else 0
                    blockDef.blockState = "" if value.isdigit() else value
                if column == self.COL_OPACITY:
                    blockDef.opacity = int(value) if str(value).strip() else None
                if column == self.COL_BRIGHTNESS:
                    blockDef.brightness = int(value) if str(value).strip() else None
                if column == self.COL_UNLOCALIZED:
                    blockDef.unlocalizedName = value
                if column == self.COL_ENGLISH:
                    blockDef.englishName = value
            except ValueError:
                log.exception("ValueError in setData")
                return False

            self.dataChanged.emit(index, index)
            return True

        return False

    def addBlock(self, internalName):
        log.info("Adding block %s", internalName)
        blockDef = BlockDefinition(internalName)
        self.beginInsertRows(QtCore.QModelIndex(), len(self.definedBlocks), len(self.definedBlocks))
        self.definedBlocks.append(blockDef)
        log.info("Appended")
        self.endInsertRows()

    def removeBlock(self, row):
        if row >= len(self.definedBlocks):
            return
        self.beginRemoveRows(QtCore.QModelIndex(), row, row)
        del self.definedBlocks[row]
        self.endRemoveRows()

    def setBlockModelPath(self, row, modelPath):
        blockDef = self.definedBlocks[row]
        blockDef.modelPath = modelPath


class UnknownBlocksDialog(QtWidgets.QDialog):
    """
    Lists what the world has shown so far that nothing describes, and which block packs are in use.
    Only chunks that have been read are counted. Double-click a block to add a definition for it.
    """

    def __init__(self, parent, session):
        super(UnknownBlocksDialog, self).__init__(parent)
        self.setWindowTitle(self.tr("Unknown Blocks"))
        self.resize(640, 420)
        self.chosenName = None

        layout = QtWidgets.QVBoxLayout(self)
        summary = QtWidgets.QLabel(self.summaryText(session))
        summary.setWordWrap(True)
        layout.addWidget(summary)

        blocktypes = session.worldEditor.blocktypes
        report = blocktypes.unresolvedBlockReport() if hasattr(blocktypes, "unresolvedBlockReport") else []
        self.table = QtWidgets.QTableWidget(len(report), 3)
        self.table.setHorizontalHeaderLabels([self.tr("Block"), self.tr("Blocks seen"), self.tr("Unknown states")])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, entry in enumerate(report):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(entry["internalName"]))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(entry["blocks"])))
            states = entry["states"] if entry["knownBlock"] else [self.tr("(the whole block is unknown)")]
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(", ".join(states)))
        self.table.resizeColumnToContents(0)
        self.table.cellDoubleClicked.connect(self.blockChosen)
        layout.addWidget(self.table)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def summaryText(self, session):
        selection = getattr(session.worldEditor.adapter, "packSelection", None)
        if selection is None:
            return self.tr("Block packs are only used for worlds from Minecraft 1.21 and 1.21.1.")

        lines = []
        names = [os.path.basename(pack.path) for pack in selection.packs]
        lines.append(self.tr("Block packs in use: %s") % (", ".join(names) if names else self.tr("none")))
        if selection.missingMods:
            lines.append(self.tr("Mods in this world with no block pack: %s") % ", ".join(selection.missingMods))
        for mod, worldVersion, packVersion in selection.versionMismatches:
            lines.append(self.tr("%s is version %s in this world, but %s in its block pack.")
                         % (mod, worldVersion, packVersion))
        lines.extend(selection.problems)
        for name, count in selection.reports:
            lines.append(self.tr("%s had %d problems when it was exported. See report.json in the pack.") % (name, count))
        return "\n".join(lines)

    def blockChosen(self, row, column):
        self.chosenName = self.table.item(row, 0).text()
        self.accept()


class ConfigureBlocksDialog(QtWidgets.QDialog, Ui_configureBlocks):
    def __init__(self, parent):
        super(ConfigureBlocksDialog, self).__init__(parent)
        self.setupUi(self)
        self.okButton.clicked.connect(self.accept)

        self.texListNameProxyModel = None
        self.texListZipProxyModel = None

        self.model = ConfigureBlocksItemModel()
        self.itemDelegate = ConfigureBlocksItemDelegate()

        self.blocksView.setModel(self.model)
        self.blocksView.setItemDelegate(self.itemDelegate)

        self.blocksView.clicked.connect(self.currentBlockClicked)

        self.internalNameBox.editTextChanged.connect(self.nameTextChanged)

        self.addBlockButton.clicked.connect(self.addBlock)
        self.removeBlockButton.clicked.connect(self.removeBlock)

        headerWidths = [
            48,
            200,
            75,
            60,
            60,
            75,
            180,
            180,
        ]
        for i, w in enumerate(headerWidths):
            self.blocksView.setColumnWidth(i, w)

        self.setModelControlsEnabled(False)
        self.modelNameBox.activated.connect(self.modelNameChanged)

        header = self.modelTexturesTable.horizontalHeader()
        header.resizeSection(0, 50)
        header.resizeSection(2, 40)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Fixed)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)

        self.textureList.clicked.connect(self.textureClicked)

        self.textureSearchBox.editTextChanged.connect(self.textureSearched)
        self.textureZipBox.activated.connect(self.textureZipChanged)

        self.unknownBlocksButton = QtWidgets.QPushButton(self.tr("Unknown Blocks..."))
        self.unknownBlocksButton.clicked.connect(self.showUnknownBlocks)
        self.horizontalLayout.insertWidget(0, self.unknownBlocksButton)
        self.addPackButton = QtWidgets.QPushButton(self.tr("Add Block Pack..."))
        self.addPackButton.clicked.connect(self.addBlockPack)
        self.horizontalLayout.insertWidget(1, self.addPackButton)

    def textureZipChanged(self, row):
        if self.texListZipProxyModel is None:
            return

        zipFilename = self.textureZipBox.itemData(row)
        self.texListZipProxyModel.setFilterFixedString(zipFilename)

    def textureSearched(self, value):
        if self.texListNameProxyModel is None:
            return

        self.texListNameProxyModel.setFilterRegularExpression(value)

    def getConfiguredBlocks(self):
        return self.model.definedBlocks

    def setModelControlsEnabled(self, enabled):
        self.modelNameBox.setEnabled(enabled)
        self.xRotationBox.setEnabled(enabled)
        self.yRotationBox.setEnabled(enabled)
        self.zRotationBox.setEnabled(enabled)
        self.modelTexturesTable.setEnabled(enabled)

    def currentBlockClicked(self, index):
        """
        Block in the top block list was clicked. Set up the model list,

        :param index:
        :type index:
        :return:
        :rtype:
        """
        if index.isValid():
            self.setModelControlsEnabled(True)
            modelPath = self.model.definedBlocks[index.row()].modelPath
            row = self.modelNameBox.findData(modelPath, Qt.UserRole)
            if row == -1:
                row = 0
            self.modelNameBox.setCurrentIndex(row)
            self.modelNameChanged(row)

    def nameTextChanged(self, text):
        self.addBlockButton.setEnabled(len(text) > 0)

    def modelNameChanged(self, row):
        blockIndex = self.blocksView.currentIndex()
        if not blockIndex.isValid():
            return

        modelPath = self.modelNameBox.itemData(row, Qt.UserRole)
        if modelPath is None:  # modelNameBox is empty?
            return
        self.model.setBlockModelPath(blockIndex.row(), modelPath)

        modelJson = json.load(self.session.resourceLoader.openStream(modelPath))

        # Parse block model and its parents and look for unbound textures
        elements = []
        textures = {}
        while modelJson is not None:
            if 'textures' in modelJson:
                for variable, value in modelJson['textures'].items():
                    textures.setdefault(variable, value)  # a model's own textures come before its parent's
            if 'elements' in modelJson:
                elements.extend(modelJson['elements'])
            parent = modelJson.get('parent')
            if parent is not None and "builtin/" not in parent:
                modelJson = json.load(self.session.resourceLoader.openStream(modelPathFromName(parent)))
            else:
                modelJson = None

        unboundTextures = set()
        for element in elements:
            if 'faces' not in element:
                continue
            faces = element['faces']
            for side, face in faces.items():
                if 'texture' in face:
                    texture = face['texture']
                    lasttex = texture
                    for i in range(30):
                        if texture.startswith("#"):
                            lasttex = texture
                            texture = textures.get(texture[1:], texture)
                        else:
                            break
                        if lasttex == texture:
                            break
                    if texture.startswith("#"):
                        unboundTextures.add(texture)

        self.modelTexturesTable.clearContents()
        self.modelTexturesTable.setRowCount(len(unboundTextures))
        blockDef = self.model.definedBlocks[blockIndex.row()]
        boundTextures = blockDef.modelTextures

        for row, texture in enumerate(sorted(unboundTextures)):

            texVarItem = QtWidgets.QTableWidgetItem(texture)
            texVarItem.setData(Qt.UserRole, texture)

            texturePath = boundTextures.get(texture, "Unbound")
            displayName = texturePath.rsplit("/", 1)[-1]
            texPathItem = QtWidgets.QTableWidgetItem(displayName)
            texPathItem.setData(Qt.UserRole, texturePath)

            self.modelTexturesTable.setItem(row, 0, texVarItem)
            self.modelTexturesTable.setItem(row, 1, texPathItem)

    def currentBlockDef(self):
        blockIndex = self.blocksView.currentIndex()
        if not blockIndex.isValid():
            return None
        return self.model.definedBlocks[blockIndex.row()]

    def textureClicked(self, index):
        blockDef = self.currentBlockDef()
        if blockDef is None:
            return

        textureRow = self.modelTexturesTable.currentRow()
        selectedItem = self.modelTexturesTable.item(textureRow, 0)
        if selectedItem is None:
            return

        texVar = selectedItem.data(Qt.UserRole)

        texturePath = index.data(Qt.UserRole)
        blockDef.modelTextures[texVar] = texturePath

        displayName = texturePath.rsplit("/", 1)[-1]
        texPathItem = QtWidgets.QTableWidgetItem(displayName)
        texPathItem.setData(Qt.UserRole, texturePath)
        self.modelTexturesTable.setItem(textureRow, 1, texPathItem)

    def showWithSession(self, session):
        self.session = session

        self.internalNameBox.clear()
        for internalName in session.unknownBlocks():
            self.internalNameBox.addItem(internalName, internalName)

        firstModels = []
        models = []
        self.modelNameBox.clear()
        for modelPath in session.resourceLoader.blockModelPaths():
            displayName = modelDisplayName(modelPath)

            # List commonly used models first
            if displayName == "cube_all":
                firstModels.insert(0, (displayName, modelPath))
            elif displayName.startswith("cube"):
                firstModels.append((displayName, modelPath))
            else:
                models.append((displayName, modelPath))

        for displayName, modelPath in firstModels + models:
            self.modelNameBox.addItem(displayName, modelPath)

        texListModel = TextureListModel(session.resourceLoader)
        self.texListNameProxyModel = QtCore.QSortFilterProxyModel()
        self.texListNameProxyModel.setSourceModel(texListModel)
        self.texListNameProxyModel.setFilterCaseSensitivity(Qt.CaseInsensitive)

        self.texListZipProxyModel = QtCore.QSortFilterProxyModel()
        self.texListZipProxyModel.setSourceModel(self.texListNameProxyModel)
        self.texListZipProxyModel.setFilterRole(TextureListModel.ZipfilePathRole)

        self.textureList.setModel(self.texListZipProxyModel)

        self.textureZipBox.clear()
        self.textureZipBox.addItem(self.tr("[All files]"), "")
        for zipFilename in texListModel.zipfilePaths():
            self.textureZipBox.addItem(os.path.basename(zipFilename), zipFilename)

        self.show()

    def showUnknownBlocks(self):
        dialog = UnknownBlocksDialog(self, self.session)
        if dialog.exec() and dialog.chosenName:
            self.model.addBlock(dialog.chosenName)

    def addBlockPack(self):
        """ Put a copy of a block pack in the folder they are found in, if it can be read. """
        path = QtWidgets.QFileDialog.getExistingDirectory(self, self.tr("Choose a block pack folder"))
        if not path:
            return
        path = os.path.normpath(path)
        try:
            BlockPack(path)
            destination = os.path.join(pack_library.packFolders()[0], os.path.basename(path))
            if os.path.exists(destination):
                raise BlockPackError("There is already a block pack named %s." % os.path.basename(path))
            shutil.copytree(path, destination)
        except (BlockPackError, EnvironmentError) as e:
            QtWidgets.QMessageBox.warning(self, self.tr("Could not add the block pack"), str(e))
            return
        QtWidgets.QMessageBox.information(self, self.tr("Block pack added"),
                                          self.tr("The block pack will be used the next time a world it fits is opened."))

    def addBlock(self):
        internalName = self.internalNameBox.currentText()
        # index = self.internalNameBox.findText(internalName)
        # if index != -1:
        #     self.internalNameBox.removeItem(index)
        self.model.addBlock(internalName)

    def removeBlock(self):
        index = self.blocksView.currentIndex()
        if not index.isValid():
            return

        row = index.row()
        self.model.removeBlock(row)


    def done(self, result):
        self.model.writeToJson()
        super(ConfigureBlocksDialog, self).done(result)

    def close(self):
        self.model.writeToJson()
        super(ConfigureBlocksDialog, self).close()



