"""
    qglcontext

The renderer relies on the fixed-function pipeline and display lists, so every context is a
compatibility profile context. All contexts share resources, which means textures and display
lists can be created through the global share context before any view has been shown.
"""
from OpenGL import GL
from PySide6 import QtCore, QtGui, QtWidgets
import logging
import re

log = logging.getLogger(__name__)

REQUIRED_VERSION = (1, 3)

_lastAcquiredContextInfo = None
_resourceSurface = None


def getContextInfo():
    return _lastAcquiredContextInfo


def setDefaultFormat():
    """
    Must be called before the QApplication is created.
    """
    oglFormat = QtGui.QSurfaceFormat()
    oglFormat.setRenderableType(QtGui.QSurfaceFormat.RenderableType.OpenGL)
    oglFormat.setProfile(QtGui.QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
    oglFormat.setVersion(2, 1)
    oglFormat.setDepthBufferSize(24)
    # No vertical sync: it would hold the views to the monitor's refresh rate, often 60 Hz.
    # WorldView.maxFPS limits the frame rate instead.
    oglFormat.setSwapInterval(0)
    QtGui.QSurfaceFormat.setDefaultFormat(oglFormat)
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


def makeResourceContextCurrent():
    """
    Make a context current that shares resources with every view. A QOpenGLWidget has no context
    until it is first shown, and this works at any time on the main thread.
    """
    global _resourceSurface
    if _resourceSurface is None:
        _resourceSurface = QtGui.QOffscreenSurface()
        _resourceSurface.setFormat(QtGui.QSurfaceFormat.defaultFormat())
        _resourceSurface.create()

    context = QtGui.QOpenGLContext.globalShareContext()
    if context is None or not context.makeCurrent(_resourceSurface):
        raise RuntimeError("Could not make the shared OpenGL context current.")


def validateContext():
    """
    Check that a usable OpenGL context can be obtained. Shows an error and exits if not.
    """
    global _lastAcquiredContextInfo
    detailedText = "No OpenGL context."
    try:
        makeResourceContextCurrent()
        context = QtGui.QOpenGLContext.currentContext()
        actualFormat = context.format()
        version = GL.glGetString(GL.GL_VERSION).decode("ascii", "replace")
        major, minor = re.match(r'^(\d+)\.(\d+)', version).groups()
        major, minor = int(major), int(minor)

        detailedText = "Obtained a GL context with this format:\n"
        detailedText += "Valid: %s\n" % (context.isValid(),)
        detailedText += "Version: %s.%s\n" % (actualFormat.majorVersion(), actualFormat.minorVersion())
        detailedText += "Depth buffer: %s\n" % (actualFormat.depthBufferSize(),)
        detailedText += "Rendering profile: %s\n" % (actualFormat.profile(),)
        detailedText += "\n"
        detailedText += "Driver info:\n"
        detailedText += "GL_VERSION: %s (%s, %s)\n" % (version, major, minor)
        detailedText += "GL_VENDOR: %r\n" % GL.glGetString(GL.GL_VENDOR)
        detailedText += "GL_RENDERER: %r\n" % GL.glGetString(GL.GL_RENDERER)
        log.info("%s", detailedText)
        _lastAcquiredContextInfo = detailedText

        if context.isValid() and (major, minor) >= REQUIRED_VERSION:
            return
    except Exception as e:
        log.exception("Failed to validate the OpenGL context.")
        detailedText += "\n%r" % (e,)

    app = QtWidgets.QApplication.instance()
    msgBox = QtWidgets.QMessageBox()
    msgBox.setWindowTitle(app.tr("OpenGL Error"))
    msgBox.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Close)
    msgBox.setText(app.tr("OpenGL %d.%d or greater is required. MCEdit was unable to start up OpenGL.") % REQUIRED_VERSION)
    msgBox.setInformativeText(
        app.tr("Could not create a usable OpenGL context. Verify that your "
               "graphics drivers are installed correctly.")
    )
    msgBox.setDetailedText(detailedText)
    msgBox.setIcon(QtWidgets.QMessageBox.Icon.Critical)
    msgBox.setMinimumWidth(300)
    msgBox.setSizeGripEnabled(True)
    msgBox.exec()

    raise SystemExit
