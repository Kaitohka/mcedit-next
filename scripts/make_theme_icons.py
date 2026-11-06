#!/usr/bin/env python3
"""Draw the small arrows and check mark that the modern themes' stylesheet uses.

    python scripts/make_theme_icons.py

Writes PNG files to src/mcedit2/styles/icons/. They are drawn at 32 pixels and shown smaller, so
they stay sharp on high-DPI screens. Run again after changing the colours here.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui

OUT = Path(__file__).resolve().parent.parent / "src" / "mcedit2" / "styles" / "icons"
SIZE = 32

# Arrow colour per theme: light grey on the dark theme, dark grey on the light one.
ARROW_COLORS = {"dark": "#c4cad1", "light": "#4a5259"}
CHECK_COLOR = "#ffffff"


def painter(image):
    p = QtGui.QPainter(image)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    return p


def blank():
    image = QtGui.QImage(SIZE, SIZE, QtGui.QImage.Format_ARGB32)
    image.fill(QtCore.Qt.transparent)
    return image


def chevron(points, color):
    image = blank()
    p = painter(image)
    pen = QtGui.QPen(QtGui.QColor(color), 4.0, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in points]))
    p.end()
    return image


def main():
    QtGui.QGuiApplication(sys.argv[:1])
    OUT.mkdir(parents=True, exist_ok=True)
    for theme, color in ARROW_COLORS.items():
        chevron([(8, 12), (16, 20), (24, 12)], color).save(str(OUT / ("arrow_down_%s.png" % theme)))
        chevron([(8, 20), (16, 12), (24, 20)], color).save(str(OUT / ("arrow_up_%s.png" % theme)))
        chevron([(12, 8), (20, 16), (12, 24)], color).save(str(OUT / ("arrow_right_%s.png" % theme)))
        # Title bar buttons of the dock panels
        image = blank()
        p = painter(image)
        p.setPen(QtGui.QPen(QtGui.QColor(color), 3.0, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        p.drawLine(QtCore.QPointF(10, 10), QtCore.QPointF(22, 22))
        p.drawLine(QtCore.QPointF(22, 10), QtCore.QPointF(10, 22))
        p.end()
        image.save(str(OUT / ("close_%s.png" % theme)))
        image = blank()
        p = painter(image)
        p.setPen(QtGui.QPen(QtGui.QColor(color), 2.5))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRoundedRect(QtCore.QRectF(12, 8, 12, 12), 2, 2)
        p.drawRoundedRect(QtCore.QRectF(8, 12, 12, 12), 2, 2)
        p.end()
        image.save(str(OUT / ("float_%s.png" % theme)))
    chevron([(7, 17), (13, 23), (25, 10)], CHECK_COLOR).save(str(OUT / "check.png"))
    image = blank()
    p = painter(image)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor(CHECK_COLOR))
    p.drawRoundedRect(QtCore.QRectF(8, 14, 16, 4), 2, 2)
    p.end()
    image.save(str(OUT / "partial.png"))
    print("wrote", sorted(f.name for f in OUT.iterdir()))


if __name__ == "__main__":
    main()
