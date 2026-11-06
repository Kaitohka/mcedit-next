"""
    worldlist_paint_test

    Draw rows of the world list. The delegate used QtGui.QStyleOptionViewItem, which is in QtWidgets
    in Qt 6, and the world list crashed as soon as it had a world in it.
"""
import importlib.util
import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

from mcedit2 import worldlist

TESTS = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "modern_world_test", TESTS / "test_mceditlib" / "modern_world_test.py")
worlds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worlds)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def worldFolders(tmp_path):
    modern = tmp_path / "modern"
    modern.mkdir()
    worlds.writeLevelDat(modern)
    section = worlds.makeSection(0, [worlds.state("minecraft:stone")])
    worlds.writeRegion(modern / "region" / "r.0.0.mca", {(0, 0): worlds.makeChunk(0, 0, [section])})

    old = tmp_path / "AnvilWorld"
    shutil.copytree(TESTS.parent / "test_files" / "AnvilWorld", old)
    return [str(modern / "level.dat"), str(old / "level.dat")]


def testWorldListRowsDraw(app, worldFolders):
    model = worldlist.WorldListModel(worldFolders)
    assert model.rowCount(QtCore.QModelIndex()) == 2

    view = QtWidgets.QListView()
    delegate = worldlist.WorldListItemDelegate()
    view.setItemDelegate(delegate)
    view.setModel(model)
    view.resize(400, 300)
    view.show()

    # grab() paints every visible row through the delegate.
    image = view.grab().toImage()
    assert not image.isNull()

    # Paint each row directly as well, so a failure points at the delegate and not at the view.
    target = QtGui.QImage(400, 100, QtGui.QImage.Format_ARGB32)
    for row in range(model.rowCount(QtCore.QModelIndex())):
        index = model.index(row, 0)
        option = QtWidgets.QStyleOptionViewItem()
        option.rect = QtCore.QRect(0, 0, 400, 100)
        painter = QtGui.QPainter(target)
        try:
            delegate.paint(painter, option, index)
        finally:
            painter.end()
        name = model.data(index, worldlist.WorldListModel.WorldInfoRole)[0]
        assert delegate.itemWidget.displayNameLabel.text() == name
    view.close()


def testFlagsForInvalidIndex(app, worldFolders):
    model = worldlist.WorldListModel(worldFolders)
    assert model.flags(QtCore.QModelIndex()) == QtCore.Qt.NoItemFlags
