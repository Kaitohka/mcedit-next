import os

from PySide6 import QtCore, QtGui
import numpy
from mcedit2.util import glutils
from mcedit2.util.resources import resourcePath

__author__ = 'Rio'


def loadPNGTexture(filename, *a, **kw):
    try:
        w, h, ndata = loadPNGFile(filename)
        tex = glutils.Texture(name=os.path.basename(filename), image=ndata.ravel(),
                              width=w, height=h, *a, **kw)
        return tex
    except Exception as e:
        print("Exception loading ", filename, ": ", repr(e))
        return glutils.Texture()


def loadPNGImage(img):
    # RGBA bytes with the bottom row first, as OpenGL expects. (QGLWidget.convertToGLFormat)
    glImg = img.convertToFormat(QtGui.QImage.Format.Format_RGBA8888).flipped(QtCore.Qt.Orientation.Vertical)
    if glImg.isNull():
        raise ValueError("Loading png file %s: Conversion to GL format failed." % img)

    w = glImg.width()
    h = glImg.height()
    # Rows are 32-bit aligned, and 4 bytes per pixel is always aligned, so there is no padding.
    ndata = numpy.frombuffer(glImg.constBits(), dtype='uint8').copy()

    assert len(ndata) == w * h * 4
    return w, h, ndata.reshape((h, w, 4))


def loadPNGData(data):
    img = QtGui.QImage.fromData(data)
    return loadPNGImage(img)


def loadPNGFile(filename):
    img = QtGui.QImage()
    if not os.path.isabs(filename):
        filename = resourcePath("mcedit2/assets/mcedit2/textures/%s" % filename)

    if not img.load(filename):
        raise IOError("Failed to read PNG file " + filename)
    return loadPNGImage(img)
