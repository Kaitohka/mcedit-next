#!/usr/bin/env python3
"""Drive the real application on a copy of a test world and report every exception.

Switches through each view and tool with simulated mouse and keyboard input, makes a real edit
and undoes it, then triggers the menu and toolbar actions, closing each dialog as it opens.
It proves that code paths run without errors. It does not judge what they draw, and dialogs are
closed without using their controls.

    python scripts/smoke_ui.py [--out DIR] [--stub-assets]

Windows open on screen while it runs. With --stub-assets it uses generated assets, where every
block shows the unknown-block texture, instead of needing a Minecraft client jar.
Exit code 0 means no errors were recorded.
"""
import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Actions that would end the run, replace the session under test, deliberately raise, or change
# the user's settings. Matched against the menu text and the actions' object names, which stay
# in English when the interface is translated.
SKIPPED_ACTIONS = ("recent worlds", "recent_worlds", "exit", "quit", "raise error", "close world",
                   "close_world", "raise_error", "restart", "about", "new world", "new_world", "enable developer",
                   "enable_developer", "language")


def writeStubAssets(path):
    """Write the few assets the app needs in order to start, using a flat colour texture."""
    from PySide6 import QtCore, QtGui

    image = QtGui.QImage(256, 256, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtGui.QColor(90, 170, 60))
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    png = bytes(buffer.data())

    faces = ", ".join('"%s": {"texture": "#%s", "cullface": "%s"}' % (f, f, f)
                      for f in ("down", "up", "north", "south", "west", "east"))
    with zipfile.ZipFile(path, "w") as z:
        for name in ("colormap/grass", "colormap/foliage", "blocks/water_still", "blocks/lava_still"):
            z.writestr("assets/minecraft/textures/%s.png" % name, png)
        z.writestr("assets/minecraft/models/block/cube.json",
                   '{"elements": [{"from": [0, 0, 0], "to": [16, 16, 16], "faces": {%s}}]}' % faces)
        z.writestr("assets/minecraft/models/block/cube_all.json",
                   '{"parent": "block/cube", "textures": {"particle": "#all", "down": "#all", "up": "#all", '
                   '"north": "#all", "east": "#all", "south": "#all", "west": "#all"}}')


# Logged errors that do not depend on the code under test.
EXPECTED_LOGGED_ERRORS = (
    "Error reading JSON from session server",  # needs a network connection and a real player
    "No item selected in savesFolderComboBox",  # no Minecraft install was found
)


class LoggedErrorCatcher(logging.Handler):
    """ Much of the rendering code catches its exceptions and logs them, so look there too. """

    def __init__(self, smokeTest):
        super().__init__(logging.ERROR)
        self.smokeTest = smokeTest
        self.seen = set()

    def emit(self, record):
        message = record.getMessage()
        if any(expected in message for expected in EXPECTED_LOGGED_ERRORS) or message in self.seen:
            return
        self.seen.add(message)
        self.smokeTest.errors.append((self.smokeTest.current, "logged: " + message, message))


def snapshotFolder(folder):
    """ Return every file in a folder with a hash of its contents and its modification time. """
    found = {}
    for root, dirs, files in os.walk(folder):
        found[root] = None
        for name in files:
            if name == "session.lock":  # held while a world is open, and not part of the world
                continue
            path = os.path.join(root, name)
            with open(path, "rb") as f:
                found[path] = hashlib.sha256(f.read()).hexdigest(), os.path.getmtime(path)
    return found


class SmokeTest:
    def __init__(self, out, stubAssets, world=None, looks=(), packs=None, definitions=None, readOnly=False):
        self.out = out
        self.openReadOnly = readOnly
        self.looks = looks
        self.packs = packs
        self.definitions = definitions
        self.stubAssets = stubAssets
        self.errors = []
        self.opened = set()
        self.done = 0
        self.current = "startup"
        self.temp = Path(tempfile.mkdtemp(prefix="mcedit_smoke"))
        shutil.copytree(world or ROOT / "test_files" / "AnvilWorld_1.8", self.temp / "World")
        self.worldBefore = snapshotFolder(self.temp / "World")
        self.givenWorld = world is not None

    def record(self, excType, excValue, excTb):
        text = "".join(traceback.format_exception(excType, excValue, excTb))
        self.errors.append((self.current, "%s: %s" % (excType.__name__, excValue), text))
        print("ERROR during [%s]\n%s" % (self.current, text), flush=True)

    def pump(self, seconds=0.15):
        from PySide6 import QtWidgets
        end = time.time() + seconds
        while time.time() < end:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.005)

    def step(self, name, func):
        self.current = name
        try:
            func()
            self.done += 1
        except BaseException:
            self.record(*sys.exc_info())
        self.pump()

    def closeModals(self):
        from PySide6 import QtWidgets
        widget = QtWidgets.QApplication.activeModalWidget()
        if widget is None:
            return
        self.opened.add("%s -> %s" % (self.current, type(widget).__name__))
        try:
            if isinstance(widget, QtWidgets.QMessageBox):
                for button in widget.buttons():
                    if widget.buttonRole(button) in (QtWidgets.QMessageBox.ButtonRole.RejectRole,
                                                     QtWidgets.QMessageBox.ButtonRole.NoRole):
                        button.click()
                        return
            widget.reject()
        except Exception:
            self.record(*sys.exc_info())

    def mouseActions(self, view, label):
        from PySide6 import QtCore, QtGui, QtTest, QtWidgets
        from PySide6.QtCore import Qt

        center = view.rect().center()
        moved = center + QtCore.QPoint(30, 12)
        self.step(label + ": mouse move", lambda: QtTest.QTest.mouseMove(view, center))

        def drag(button):
            QtTest.QTest.mousePress(view, button, Qt.KeyboardModifier.NoModifier, center)
            self.pump(0.05)
            QtTest.QTest.mouseMove(view, moved)
            self.pump(0.05)
            QtTest.QTest.mouseRelease(view, button, Qt.KeyboardModifier.NoModifier, moved)

        for button, name in ((Qt.MouseButton.LeftButton, "left"),
                             (Qt.MouseButton.RightButton, "right"),
                             (Qt.MouseButton.MiddleButton, "middle")):
            self.step("%s: %s drag" % (label, name), lambda b=button: drag(b))

        wheel = QtGui.QWheelEvent(QtCore.QPointF(center), QtCore.QPointF(view.mapToGlobal(center)),
                                  QtCore.QPoint(0, 0), QtCore.QPoint(0, 120), Qt.MouseButton.NoButton,
                                  Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        self.step(label + ": wheel", lambda: QtWidgets.QApplication.sendEvent(view, wheel))

        def press(key):
            view.setFocus()
            QtTest.QTest.keyPress(view, key)
            self.pump(0.05)
            QtTest.QTest.keyRelease(view, key)

        for key, name in ((Qt.Key.Key_W, "W"), (Qt.Key.Key_Space, "Space")):
            self.step("%s: key %s" % (label, name), lambda k=key: press(k))

    def checkEditAndUndo(self, session, dim, box):
        from mceditlib.util import exhaust

        undoStack = session.undoStack
        gold = "minecraft:gold_block"
        corner = (box.minx, box.miny, box.minz)

        def fill():
            count = undoStack.count()
            with session.beginSimpleCommand("smoke test fill"):
                exhaust(dim.fillBlocksIter(box, session.worldEditor.blocktypes[gold]))
            assert undoStack.count() == count + 1, "the edit added no undo entry"
            assert dim.getBlock(*corner).internalName == gold, "the fill did not apply"

        def undo():
            undoStack.undo()
            self.pump(0.5)
            assert dim.getBlock(*corner).internalName != gold, "undo did not restore the block"

        def redo():
            undoStack.redo()
            self.pump(0.5)
            assert dim.getBlock(*corner).internalName == gold, "redo did not reapply the block"

        self.step("edit: fill", fill)
        self.pump(1)
        self.step("edit: undo", undo)
        self.step("edit: redo", redo)

    def triggerActions(self, app, session):
        from PySide6 import QtWidgets

        actions = []
        seen = set()

        def collect(menu, path, names):
            for action in menu.actions():
                text = action.text().replace("&", "")
                if action.menu() is not None:
                    collect(action.menu(), path + [text], names + [action.menu().objectName()])
                elif not action.isSeparator() and id(action) not in seen:
                    seen.add(id(action))
                    key = " ".join(path + [text] + names + [action.objectName()]).lower()
                    actions.append((" > ".join(path + [text]), key, action))

        for menu in app.mainWindow.menuBar().findChildren(QtWidgets.QMenu):
            collect(menu, [menu.title().replace("&", "")], [menu.objectName()])
        for menu in session.menus:
            collect(menu, [menu.title().replace("&", "")], [menu.objectName()])

        for label, key, action in actions:
            if not action.isEnabled() or any(word in key for word in SKIPPED_ACTIONS):
                continue
            self.step("menu: " + label, action.trigger)

        for action in session.topToolbarActions:
            if action is not None and hasattr(action, "trigger"):
                self.step("toolbar: " + action.text().replace("&", ""), action.trigger)

    def drive(self, app):
        from mceditlib.selection import BoundingBox

        if self.givenWorld:
            self.step("world list preview", lambda: app.worldList.showWorld(str(self.temp / "World")))
            self.pump(1.5)

        self.app = app
        # Whatever happens here must not touch the user's own definitions.
        app.configureBlocksDialog.model.definedBlocksFilePath = str(self.temp / "defined_blocks.json")

        self.current = "open world"
        app.loadFile(str(self.temp / "World"), readonly=self.openReadOnly)
        self.pump(6)
        session = app.currentSession()
        if session is None:
            self.record(RuntimeError, RuntimeError("The world did not open."), None)
            return

        print("Opened with dimensions %s, read-only: %s" % (sorted(session.worldEditor.listDimensions()),
                                                             session.readonly), flush=True)
        dim = session.currentDimension
        positions = sorted(dim.chunkPositions())
        cx, cz = positions[len(positions) // 2]
        box = BoundingBox((cx << 4, 60, cz << 4), (16, 16, 16))
        session.currentSelection = box

        tab = session.editorTab
        for name, button in tab.viewButtons.items():
            self.step("view %s: show" % name, button.click)
            self.pump(1.2)
            view = tab.currentView()
            self.mouseActions(view, "view " + name)
            if self.out:
                view.grabFramebuffer().save(str(self.out / ("view_%s.png" % name)))

        tab.viewButtons["Cam"].click()
        self.pump(0.5)
        cameraView = tab.currentView()
        for tool in list(session.tools):
            if session.readonly and tool.modifiesWorld:
                continue  # its button is disabled
            self.step("tool %s: choose" % tool.name, lambda t=tool.name: session.chooseTool(t))
            self.mouseActions(cameraView, "tool " + tool.name)
            session.currentSelection = box

        if self.givenWorld:
            self.checkInspector(session)
        if not session.readonly:
            self.checkEditAndUndo(session, dim, box)
        session.currentSelection = box
        if session.readonly:
            self.triggerActions(app, session)
            self.checkDimensions(session)
            self.takeLooks(session)
            self.checkUnchanged()
        else:
            self.checkUnchangedUntilSaved(session)
            session.saveConfirmed = True
            self.triggerActions(app, session)
            if self.givenWorld:
                self.checkDimensions(session)
                self.takeLooks(session)

    def checkInspector(self, session):
        from mceditlib.anvil.entities import EntityPtr

        chunk = session.currentDimension.getChunk(0, 0)
        for ref in chunk.TileEntities:
            self.step("inspect block entity %s" % ref.id, lambda p=ref.Position: session.inspectBlock(p))
        for ref in chunk.Entities:
            self.step("inspect entity %s" % ref.id, lambda r=ref: session.inspectEntity(EntityPtr.create(r)))
        self.step("inspect chunk", lambda: session.inspectChunk(0, 0))
        self.pump(0.3)

        session.inspectorWidget.inspectBlock((0, 200, 0))
        self.pump(0.3)
        self.grab(session.inspectorDockWidget, "inspector_block_entity")
        if session.currentDimension.containsChunk(-1, -1):
            session.inspectorWidget.inspectBlock((-1, -60, -1))
            self.pump(0.3)
            self.grab(session.inspectorDockWidget, "inspector_negative_y")
        session.inspectorWidget.inspectChunk(0, 0)
        self.pump(0.3)
        self.grab(session.inspectorDockWidget, "inspector_chunk")
        for ref in session.currentDimension.getChunk(0, 0).Entities:
            if ref.id == "minecraft:armor_stand":
                session.inspectorWidget.inspectEntity(EntityPtr.create(ref))
        self.pump(0.3)
        self.grab(session.inspectorDockWidget, "inspector_entity")
        session.playerPanel.show()
        self.pump(0.3)
        self.grab(session.playerPanel, "player_panel")

        tab = session.editorTab
        tab.viewButtons["2D"].click()
        self.pump(0.5)
        self.step("look at the world below zero", lambda: tab.currentView().centerOnPoint((-1, -50, -1), 30))
        self.pump(2)
        if self.out:
            tab.currentView().grabFramebuffer().save(str(self.out / "cutaway_negative_y.png"))
        tab.viewButtons["Cam"].click()
        self.pump(0.5)
        self.checkBlockDialogs(self.app, session)
        self.step("look at placed blocks", lambda: tab.currentView().centerOnPoint((5, 200, 0), 12))
        self.pump(2)
        if self.out:
            tab.currentView().grabFramebuffer().save(str(self.out / "camera_placed_blocks.png"))
        for name in session.worldEditor.listPlayers():
            self.step("read player %r" % name, lambda n=name: session.worldEditor.getPlayer(n).Position)

    def checkBlockDialogs(self, app, session):
        from mcedit2.dialogs.configure_blocks import UnknownBlocksDialog

        dialog = app.configureBlocksDialog
        self.step("configure blocks: show", lambda: dialog.showWithSession(session))
        self.pump(0.3)
        self.grab(dialog, "configure_blocks")
        dialog.hide()

        report = UnknownBlocksDialog(None, session)
        report.show()
        self.pump(0.3)
        self.grab(report, "unknown_blocks")
        report.close()

    def grab(self, widget, name):
        if self.out:
            widget.grab().save(str(self.out / (name + ".png")))

    def checkDimensions(self, session):
        view = session.editorTab.currentView()
        for dimName in sorted(session.worldEditor.listDimensions()):
            self.step("dimension %r: go" % dimName, lambda d=dimName: session.gotoDimension(d))
            self.pump(0.5)
            self.step("dimension %r: look" % dimName,
                      lambda: session.editorTab.currentView().centerOnPoint((8, 70, 8), 40))
            self.pump(3)
            if self.out:
                view = session.editorTab.currentView()
                view.grabFramebuffer().save(str(self.out / ("dim_%s.png" % (dimName or "overworld"))))

    def takeLooks(self, session):
        from PySide6 import QtWidgets
        from mcedit2.dialogs.configure_blocks import BlockDefinition

        if self.looks:
            session.gotoDimension("")
        for index, look in enumerate(self.looks):
            x, y, z, distance = [float(v) for v in look.split(",")]
            self.step("look at %s" % look, lambda: session.editorTab.currentView().centerOnPoint((x, y, z), distance))
            self.pump(6)
            if self.out:
                session.editorTab.currentView().grabFramebuffer().save(str(self.out / ("look_%d.png" % index)))

        if self.definitions:
            self.current = "apply block definitions"
            app = QtWidgets.QApplication.instance()
            with open(self.definitions, encoding="utf-8") as f:
                definitions = [BlockDefinition(defJson=entry) for entry in json.load(f)]
            app.configureBlocksDialog.model.definedBlocks = definitions
            self.step("apply block definitions", lambda: session.setConfiguredBlocks(definitions))
            self.pump(6)
            if self.out:
                session.editorTab.currentView().grabFramebuffer().save(str(self.out / "look_defined.png"))

    def checkUnchangedUntilSaved(self, session):
        if not self.givenWorld:
            return
        self.current = "world files before saving"
        session.worldEditor.syncToDisk()
        after = snapshotFolder(self.temp / "World")
        if after != self.worldBefore:
            changed = sorted(k for k in set(after) | set(self.worldBefore) if after.get(k) != self.worldBefore.get(k))
            self.record(AssertionError, AssertionError("World files changed before saving: %s" % changed), None)
        else:
            print("World files are unchanged before saving (%d entries)." % len(after), flush=True)
            self.done += 1

    def checkUnchanged(self):
        self.current = "world files are unchanged"
        after = snapshotFolder(self.temp / "World")
        if after != self.worldBefore:
            changed = sorted(k for k in set(after) | set(self.worldBefore) if after.get(k) != self.worldBefore.get(k))
            self.record(AssertionError, AssertionError("World files changed: %s" % changed), None)
        else:
            print("World files are unchanged (%d entries)." % len(after), flush=True)
            self.done += 1

    def report(self):
        print("\nsteps ok: %d, errors: %d" % (self.done, len(self.errors)), flush=True)
        print("dialogs opened:")
        for entry in sorted(self.opened):
            print("  " + entry)
        if self.errors:
            print("\ndistinct errors:")
            for key in sorted({key for _, key, _ in self.errors}):
                first = next(name for name, k, _ in self.errors if k == key)
                print("  %s\n    first during: %s" % (key[:300], first))
        return 1 if self.errors else 0

    def run(self):
        import mcedit2.main as main
        from PySide6 import QtCore
        from mcedit2.util import minecraftinstall

        QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
        if self.stubAssets:
            assets = self.temp / "versionCache"
            assets.mkdir()
            writeStubAssets(assets / "1.12.jar")
            minecraftinstall._versionCacheDir = str(assets)

        logging.getLogger().addHandler(LoggedErrorCatcher(self))

        # startup() installs main.excepthook, which would open a modal error dialog.
        main.excepthook = self.record
        app = main.startup()
        sys.excepthook = self.record

        if self.packs:
            from mceditlib.blocktypes import pack_library
            pack_library.setPackFolders([str(self.packs)])

        closer = QtCore.QTimer()
        closer.setInterval(120)
        closer.timeout.connect(self.closeModals)
        closer.start()

        def driveAndFinish():
            try:
                self.drive(app)
            except BaseException:
                self.record(*sys.exc_info())
            # A clean Qt shutdown is not part of this test, and exiting directly avoids prompts.
            code = self.report()
            sys.stdout.flush()
            os._exit(code)

        QtCore.QTimer.singleShot(1200, driveAndFinish)
        app.exec()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="save a screenshot of each view to this folder")
    parser.add_argument("--world", type=Path, help="open a copy of this world, instead of the test files one")
    parser.add_argument("--look", action="append", default=[], metavar="X,Y,Z,DISTANCE",
                        help="with --world and --out, also save a screenshot looking at this point")
    parser.add_argument("--read-only", action="store_true", help="open the world read-only")
    parser.add_argument("--block-packs", type=Path, help="use the block packs in this folder")
    parser.add_argument("--definitions", type=Path,
                        help="after the --look screenshots, apply block definitions from this file and save another")
    parser.add_argument("--stub-assets", action="store_true", help="use generated assets instead of a Minecraft jar")
    args = parser.parse_args()
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)
    sys.argv = ["mcedit2"]
    SmokeTest(args.out, args.stub_assets, args.world, args.look, args.block_packs, args.definitions, args.read_only).run()


if __name__ == "__main__":
    main()
