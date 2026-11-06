"""
    theme_test

    The interface themes: modern dark and light, and the classic look.
"""
import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

from mcedit2.util import theme
from mcedit2.util.resources import resourcePath


@pytest.fixture(scope="module")
def app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    theme.applyTheme(app, theme.CLASSIC)


@pytest.mark.parametrize("name", list(theme.THEME_NAMES))
def testStylesheetIsComplete(name):
    css = theme.stylesheet(name)
    assert "$" not in css
    for path in re.findall(r"url\(([^)]+)\)", css):
        assert os.path.isfile(path), path


def testThemeChoices():
    names = [name for name, label in theme.themeChoices()]
    assert names[0] == theme.DEFAULT_THEME
    assert set(names) == set(theme.THEME_NAMES)


def testApplyingThemes(app):
    seen = []
    theme.notifier.changed.connect(seen.append)
    try:
        theme.applyTheme(app, "modern-dark")
        assert app.palette().color(QtGui.QPalette.Window).name() == theme.COLORS["modern-dark"]["window"]
        assert theme.isModern() and theme.isDark()

        theme.applyTheme(app, "modern-light")
        assert app.palette().color(QtGui.QPalette.Window).name() == theme.COLORS["modern-light"]["window"]
        assert not theme.isDark()

        theme.applyTheme(app, theme.CLASSIC)
        assert not theme.isModern()
        assert "QTreeView::item" in app.styleSheet()

        theme.applyTheme(app, "no-such-theme")
        assert theme.currentTheme() == theme.DEFAULT_THEME
    finally:
        theme.notifier.changed.disconnect(seen.append)
    assert seen == ["modern-dark", "modern-light", theme.CLASSIC, theme.DEFAULT_THEME]


def testDarkPixelsAreLightened(app):
    image = QtGui.QImage(3, 1, QtGui.QImage.Format_ARGB32)
    image.setPixelColor(0, 0, QtGui.QColor(40, 40, 40))       # a dark outline
    image.setPixelColor(1, 0, QtGui.QColor(60, 160, 40))      # green, kept
    image.setPixelColor(2, 0, QtGui.QColor(0, 0, 0, 0))       # transparent, kept
    out = theme.lightenDarkPixels(image)
    assert out.pixelColor(0, 0).lightness() > 160
    assert out.pixelColor(1, 0) == QtGui.QColor(60, 160, 40)
    assert out.pixelColor(2, 0).alpha() == 0


def testThemedIcons(app):
    path = resourcePath("mcedit2/assets/mcedit2/toolicons/move.png")
    icon = theme.icon(path)
    theme.applyTheme(app, "modern-dark")
    dark = icon.pixmap(QtCore.QSize(32, 32)).toImage()
    theme.applyTheme(app, "modern-light")
    light = icon.pixmap(QtCore.QSize(32, 32)).toImage()
    assert not dark.isNull() and not light.isNull()
    assert dark != light
    assert not icon.pixmap(QtCore.QSize(32, 32), QtGui.QIcon.Disabled).isNull()


def testDockTitleBarFollowsTheTheme(app):
    from mcedit2.widgets.mcedockwidget import DockTitleBar, MCEDockWidget
    theme.applyTheme(app, "modern-dark")
    window = QtWidgets.QMainWindow()
    dock = MCEDockWidget("Minimap", window)
    window.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
    assert isinstance(dock.titleBarWidget(), DockTitleBar)
    dock.setWindowTitle("Inspector")
    assert dock.titleBarWidget().label.text() == "Inspector"
    dock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable)
    assert dock.titleBarWidget().closeButton.isHidden()

    theme.applyTheme(app, theme.CLASSIC)
    assert dock.titleBarWidget() is None
    theme.applyTheme(app, "modern-light")
    assert isinstance(dock.titleBarWidget(), DockTitleBar)
    window.close()
