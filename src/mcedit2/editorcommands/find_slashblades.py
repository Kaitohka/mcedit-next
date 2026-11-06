"""
    find_slashblades

    List every blade of the SlashBlade mod (拔刀剑) in the world: on the ground, on blade stands, in
    chests and other containers, carried by mobs, and in the players' inventories and ender chests.
    Blades with broken data are a common cause of the game crashing, and those in an inventory
    can't be seen in the world. Double-clicking a row goes to that blade.
"""
import logging

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt

from mcedit2.util import slashblade
from mcedit2.util.showprogress import showProgress
from mceditlib.anvil.entities import EntityPtr
from mceditlib.exceptions import ChunkNotPresent, PlayerNotFound

log = logging.getLogger(__name__)


class BladeResult(object):
    Entity = "entity"
    Container = "container"
    Player = "player"

    def __init__(self, kind, position, holder, path, blade, entityPtr=None, dimName=None):
        self.kind = kind
        self.position = position
        self.holder = holder
        self.path = path
        self.name = slashblade.bladeName(blade)
        self.broken = slashblade.isBroken(blade)
        self.entityPtr = entityPtr
        self.dimName = dimName


def scanDimension(dimension, results):
    """ Add the blades in the entities and block entities of a dimension. Yields progress. """
    positions = list(dimension.chunkPositions())
    for i, (cx, cz) in enumerate(positions):
        try:
            chunk = dimension.getChunk(cx, cz)
        except ChunkNotPresent:
            continue
        except Exception as e:
            log.warning("Could not read chunk %s to look for blades: %r", (cx, cz), e)
            continue

        for entity in chunk.Entities:
            for path, blade in slashblade.findBlades(entity.rootTag):
                try:
                    ptr = EntityPtr.create(entity)
                except Exception:
                    ptr = None
                results.append(BladeResult(BladeResult.Entity, entity.Position, entity.id, path, blade,
                                           entityPtr=ptr, dimName=dimension.dimName))

        for tileEntity in chunk.TileEntities:
            for path, blade in slashblade.findBlades(tileEntity.rootTag):
                results.append(BladeResult(BladeResult.Container, tileEntity.Position, tileEntity.id, path,
                                           blade, dimName=dimension.dimName))

        if i % 16 == 0:
            yield i, len(positions), QtCore.QCoreApplication.translate(
                "FindSlashBlades", "Looking at chunk %d of %d") % (i, len(positions))


def scanPlayers(worldEditor, results):
    """ Add the blades in the players' inventories and ender chests. """
    for playerUUID in worldEditor.listPlayers():
        try:
            player = worldEditor.getPlayer(playerUUID)
        except PlayerNotFound:
            continue
        except Exception as e:
            log.warning("Could not read player %r to look for blades: %r", playerUUID, e)
            continue
        try:
            dimName = player.dimName
        except Exception:
            dimName = None
        try:
            position = player.Position
        except Exception:
            position = None
        holder = playerUUID or QtCore.QCoreApplication.translate("FindSlashBlades", "single player (level.dat)")
        for path, blade in slashblade.findBlades(player.rootTag):
            results.append(BladeResult(BladeResult.Player, position, holder, path, blade, dimName=dimName))


class FindSlashBladesDialog(QtWidgets.QDialog):
    def __init__(self, editorSession, parent=None):
        super().__init__(parent)
        self.editorSession = editorSession
        self.results = []
        self.setWindowTitle(self.tr("SlashBlade Blades"))

        self.summaryLabel = QtWidgets.QLabel()
        self.summaryLabel.setWordWrap(True)

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            self.tr("Where"), self.tr("Position"), self.tr("Held by"), self.tr("Path"),
            self.tr("Blade"), self.tr("State")])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self.goToRow)

        hint = QtWidgets.QLabel(self.tr(
            "Double-click a blade to go to it. Blades on the ground, on stands and in mobs can be removed "
            "with Inspect Entity, and those in containers with Inspect Block. Blades in an inventory are "
            "edited in Edit Player."))
        hint.setWordWrap(True)

        searchAgain = QtWidgets.QPushButton(self.tr("Search Again"))
        searchAgain.clicked.connect(self.search)
        close = QtWidgets.QPushButton(self.tr("Close"))
        close.clicked.connect(self.hide)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(searchAgain)
        buttons.addWidget(close)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.summaryLabel)
        layout.addWidget(self.table, 1)
        layout.addWidget(hint)
        layout.addLayout(buttons)
        self.resize(900, 420)

    def search(self):
        results = []
        dimension = self.editorSession.currentDimension

        def task():
            yield from scanDimension(dimension, results)
            scanPlayers(self.editorSession.worldEditor, results)

        if showProgress(self.tr("Looking for SlashBlade blades..."), task(), cancel=True) is False:
            self.summaryLabel.setText(self.tr("The search was stopped. These are the blades found until then."))
        else:
            dimensionLabel = self.editorSession.dimensionDisplayName(dimension.dimName)
            self.summaryLabel.setText(self.tr("%d blades found in %s and in the players' inventories.")
                                      % (len(results), dimensionLabel))
        self.setResults(results)

    def kindLabel(self, kind):
        return {
            BladeResult.Entity: self.tr("Entity"),
            BladeResult.Container: self.tr("Container"),
            BladeResult.Player: self.tr("Player"),
        }[kind]

    def setResults(self, results):
        self.results = results
        self.table.setRowCount(len(results))
        for row, result in enumerate(results):
            if result.position is None:
                position = "?"
            elif result.kind == BladeResult.Container:
                position = "%d, %d, %d" % tuple(result.position)
            else:
                position = "%0.1f, %0.1f, %0.1f" % tuple(result.position)
            if result.kind == BladeResult.Player and result.dimName is not None:
                position += "  (%s)" % self.editorSession.dimensionDisplayName(result.dimName)
            cells = [self.kindLabel(result.kind), position, result.holder, slashblade.pathText(result.path),
                     result.name, self.tr("Broken") if result.broken else ""]
            for column, text in enumerate(cells):
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    def goToRow(self, row, column):
        result = self.results[row]
        session = self.editorSession
        if result.kind == BladeResult.Entity and result.entityPtr is not None:
            session.zoomAndInspectEntity(result.entityPtr)
        elif result.kind == BladeResult.Container:
            session.zoomAndInspectBlock(result.position)
        elif result.kind == BladeResult.Player and result.position is not None:
            if result.dimName == session.currentDimension.dimName:
                session.zoomToPoint(result.position)
            else:
                QtWidgets.QMessageBox.information(
                    self, self.tr("SlashBlade Blades"),
                    self.tr("This player is in %s. Switch to that dimension to go to them.")
                    % session.dimensionDisplayName(result.dimName))
