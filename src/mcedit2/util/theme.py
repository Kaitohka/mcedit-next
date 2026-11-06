"""
    theme

    The look of MCEdit's windows. The modern themes keep its layout and icons and redraw the rest:
    flat panels, rounded controls, and Minecraft's grass green as the accent. The classic theme is
    the look MCEdit had before, the system's own.

    A theme is a set of colours, put into the Qt palette (for what the stylesheet does not cover,
    such as message boxes) and into styles/modern.qss. Changing the theme applies at once.
"""
import logging
import os
from string import Template

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from mcedit2.util.resources import resourcePath
from mcedit2.util.settings import Settings

log = logging.getLogger(__name__)

ThemeSetting = Settings().getOption("ui_theme", str, "modern-dark")

DEFAULT_THEME = "modern-dark"
CLASSIC = "classic"

THEME_NAMES = {
    "modern-dark": QT_TRANSLATE_NOOP("Theme", "Modern dark"),
    "modern-light": QT_TRANSLATE_NOOP("Theme", "Modern light"),
    CLASSIC: QT_TRANSLATE_NOOP("Theme", "Classic"),
}

GRASS = "#5fb83b"

COLORS = {
    "modern-dark": {
        "window": "#1c1e21",
        "panel": "#24272b",
        "header": "#2b2f34",
        "raised": "#2f3338",
        "base": "#17191c",
        "alternate": "#1d2023",
        "hover": "#363b41",
        "pressed": "#41474e",
        "border": "#353a40",
        "borderStrong": "#4a5159",
        "text": "#e3e6ea",
        "textDim": "#9aa2ac",
        "textDisabled": "#5c636c",
        "accent": GRASS,
        "accentHover": "#71c94c",
        "accentText": "#ffffff",
        "accentSoft": "rgba(95, 184, 59, 70)",
        "accentLine": "rgba(95, 184, 59, 150)",
        "scrollHandle": "#4a5159",
        "link": "#7cc4ff",
        "arrows": "dark",
    },
    "modern-light": {
        "window": "#eceef1",
        "panel": "#f6f7f9",
        "header": "#e8ebee",
        "raised": "#ffffff",
        "base": "#ffffff",
        "alternate": "#f3f5f7",
        "hover": "#e3e7eb",
        "pressed": "#d5dade",
        "border": "#d3d8dd",
        "borderStrong": "#b6bdc5",
        "text": "#1e2226",
        "textDim": "#5a626b",
        "textDisabled": "#a4abb2",
        "accent": "#45a023",
        "accentHover": "#52b42c",
        "accentText": "#ffffff",
        "accentSoft": "rgba(69, 160, 35, 55)",
        "accentLine": "rgba(69, 160, 35, 160)",
        "scrollHandle": "#c3c9cf",
        "link": "#1f6fc5",
        "arrows": "light",
    },
}

_classicStyle = None
_currentTheme = CLASSIC


class _ThemeNotifier(QtCore.QObject):
    changed = QtCore.Signal(str)


# Widgets that draw part of themselves connect to notifier.changed, which is emitted with the
# theme's name after it is applied.
notifier = _ThemeNotifier()


def isModern():
    return _currentTheme in COLORS


def styleIcon(name):
    """ One of the small icons in styles/icons, in the colour for the current theme: "close", "float"... """
    variant = COLORS.get(_currentTheme, COLORS["modern-light"])["arrows"]
    return QtGui.QIcon(resourcePath("mcedit2/styles/icons/%s_%s.png" % (name, variant)))


def themeChoices():
    """ (setting value, translated name) for each theme, the default first. """
    return [(name, QCoreApplication.translate("Theme", THEME_NAMES[name]))
            for name in ("modern-dark", "modern-light", CLASSIC)]


def currentTheme():
    return _currentTheme


def color(name):
    """ A colour of the current theme, for widgets that draw themselves. None for the classic theme. """
    colors = COLORS.get(_currentTheme)
    return colors.get(name) if colors else None


def linkColor():
    """ The colour for highlighted names in rich text, which is readable on every theme. """
    return QtWidgets.QApplication.palette().color(QtGui.QPalette.Link).name()


def stylesheet(name):
    """ The full stylesheet of a theme. """
    classic = open(resourcePath("mcedit2/styles/mcedit2.qcss"), encoding="utf-8").read()
    if name not in COLORS:
        return classic
    values = dict(COLORS[name])
    values["icons"] = resourcePath("mcedit2/styles/icons").replace(os.sep, "/")
    template = Template(open(resourcePath("mcedit2/styles/modern.qss"), encoding="utf-8").read())
    return template.substitute(values)


def palette(name):
    c = COLORS[name]

    def qc(value):
        return QtGui.QColor(value)

    p = QtGui.QPalette()
    for group in (QtGui.QPalette.Active, QtGui.QPalette.Inactive):
        p.setColor(group, QtGui.QPalette.Window, qc(c["window"]))
        p.setColor(group, QtGui.QPalette.WindowText, qc(c["text"]))
        p.setColor(group, QtGui.QPalette.Base, qc(c["base"]))
        p.setColor(group, QtGui.QPalette.AlternateBase, qc(c["alternate"]))
        p.setColor(group, QtGui.QPalette.Text, qc(c["text"]))
        p.setColor(group, QtGui.QPalette.Button, qc(c["raised"]))
        p.setColor(group, QtGui.QPalette.ButtonText, qc(c["text"]))
        p.setColor(group, QtGui.QPalette.ToolTipBase, qc(c["raised"]))
        p.setColor(group, QtGui.QPalette.ToolTipText, qc(c["text"]))
        p.setColor(group, QtGui.QPalette.PlaceholderText, qc(c["textDim"]))
        p.setColor(group, QtGui.QPalette.Highlight, qc(c["accent"]))
        p.setColor(group, QtGui.QPalette.HighlightedText, qc(c["accentText"]))
        p.setColor(group, QtGui.QPalette.Link, qc(c["link"]))
        p.setColor(group, QtGui.QPalette.BrightText, qc("#ff5555"))
        p.setColor(group, QtGui.QPalette.Light, qc(c["hover"]))
        p.setColor(group, QtGui.QPalette.Midlight, qc(c["raised"]))
        p.setColor(group, QtGui.QPalette.Mid, qc(c["border"]))
        p.setColor(group, QtGui.QPalette.Dark, qc(c["base"]))
        p.setColor(group, QtGui.QPalette.Shadow, qc("#000000"))
    disabled = QtGui.QPalette.Disabled
    for role in (QtGui.QPalette.WindowText, QtGui.QPalette.Text, QtGui.QPalette.ButtonText):
        p.setColor(disabled, role, qc(c["textDisabled"]))
    p.setColor(disabled, QtGui.QPalette.Window, qc(c["window"]))
    p.setColor(disabled, QtGui.QPalette.Base, qc(c["panel"]))
    p.setColor(disabled, QtGui.QPalette.Button, qc(c["panel"]))
    p.setColor(disabled, QtGui.QPalette.Highlight, qc(c["border"]))
    return p


def applyTheme(app, name=None):
    """ Give the application the look of a theme: the one in the settings if no name is given. """
    global _classicStyle, _currentTheme
    if _classicStyle is None:
        _classicStyle = app.style().name()
    if name is None:
        name = ThemeSetting.value()
    if name not in THEME_NAMES:
        name = DEFAULT_THEME

    if name == CLASSIC:
        app.setStyle(_classicStyle)
        app.setPalette(app.style().standardPalette())
    else:
        app.setStyle("Fusion")
        app.setPalette(palette(name))
    _currentTheme = name
    app.setStyleSheet(stylesheet(name))
    log.info("Theme: %s", name)
    notifier.changed.emit(name)


def isDark():
    return _currentTheme == "modern-dark"


def lightenDarkPixels(image):
    """
    For the dark theme: turn the dark, colourless pixels of an icon (outlines and grey glyphs) light
    grey, so they show on a dark background. Coloured pixels are kept as they are.
    """
    import numpy
    image = image.convertToFormat(QtGui.QImage.Format_ARGB32)
    width, height = image.width(), image.height()
    bpl = image.bytesPerLine()
    pixels = numpy.frombuffer(image.constBits(), dtype=numpy.uint8, count=bpl * height)
    pixels = pixels.reshape(height, bpl)[:, :width * 4].reshape(height, width, 4).astype(numpy.float32)
    bgr = pixels[..., :3]
    high = bgr.max(axis=-1)
    low = bgr.min(axis=-1)
    saturation = numpy.where(high > 0, (high - low) / numpy.maximum(high, 1), 0)
    lightness = (high + low) / 2 / 255
    mask = (pixels[..., 3] > 0) & (saturation < 0.25) & (lightness < 0.5)
    bgr[mask] = numpy.clip(228 - bgr[mask] * 0.85, 0, 255)
    out = numpy.ascontiguousarray(pixels.astype(numpy.uint8))
    result = QtGui.QImage(out.data, width, height, width * 4, QtGui.QImage.Format_ARGB32)
    return result.copy()


class ThemedIconEngine(QtGui.QIconEngine):
    """
    An icon from a PNG file that follows the theme: on the dark theme its dark outlines are drawn
    light. Disabled icons are faded. The pixmaps are made when drawn, so a change of theme shows at once.
    """
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.source = QtGui.QImage(path)
        self.cache = {}

    def clone(self):
        return ThemedIconEngine(self.path)

    def actualSize(self, size, mode, state):
        return self.source.size().scaled(size, QtCore.Qt.KeepAspectRatio).boundedTo(size)

    def pixmap(self, size, mode, state):
        key = (size.width(), size.height(), int(mode.value) if hasattr(mode, "value") else int(mode), _currentTheme)
        pixmap = self.cache.get(key)
        if pixmap is None:
            image = self.source
            if image.isNull():
                return QtGui.QPixmap()
            image = image.scaled(size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            if isDark():
                image = lightenDarkPixels(image)
            if mode == QtGui.QIcon.Disabled:
                image = image.convertToFormat(QtGui.QImage.Format_ARGB32)
                faded = QtGui.QImage(image.size(), QtGui.QImage.Format_ARGB32)
                faded.fill(QtCore.Qt.transparent)
                p = QtGui.QPainter(faded)
                p.setOpacity(0.35)
                p.drawImage(0, 0, image)
                p.end()
                image = faded
            pixmap = QtGui.QPixmap.fromImage(image)
            self.cache[key] = pixmap
        return pixmap

    def paint(self, painter, rect, mode, state):
        ratio = painter.device().devicePixelRatioF() if painter.device() else 1.0
        size = QtCore.QSize(int(rect.width() * ratio), int(rect.height() * ratio))
        pixmap = self.pixmap(size, mode, state)
        target = QtCore.QRectF(rect)
        drawn = QtCore.QSizeF(pixmap.width() / ratio, pixmap.height() / ratio)
        target = QtCore.QRectF(target.center().x() - drawn.width() / 2, target.center().y() - drawn.height() / 2,
                               drawn.width(), drawn.height())
        painter.drawPixmap(target, pixmap, QtCore.QRectF(pixmap.rect()))


def icon(path):
    """ A QIcon for a PNG file of MCEdit's own, drawn to suit the theme. """
    return QtGui.QIcon(ThemedIconEngine(path))


def setTheme(name):
    if name == ThemeSetting.value():
        return
    ThemeSetting.setValue(name)
    applyTheme(QtWidgets.QApplication.instance(), name)
