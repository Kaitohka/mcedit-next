"""
    mcedockwidget
"""

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt
import logging

from mcedit2.util import theme

log = logging.getLogger(__name__)


class DockTitleBar(QtWidgets.QWidget):
    """
    The title bar of a dock panel in the modern themes: its title, and buttons to float and close it.
    The native one can't be styled well (its buttons stay a few pixels tall). Mouse presses that
    the buttons don't take go on to the dock widget, so it is dragged and docked as before.
    """
    def __init__(self, dock):
        super().__init__(dock)
        self.dock = dock
        self.setObjectName("dockTitleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.label = QtWidgets.QLabel(dock.windowTitle())
        self.label.setObjectName("dockTitleLabel")
        self.floatButton = self._button(self.tr("Float or dock"), self.toggleFloating)
        self.closeButton = self._button(self.tr("Close"), dock.close)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 3, 3)
        layout.setSpacing(2)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.floatButton)
        layout.addWidget(self.closeButton)

        dock.windowTitleChanged.connect(self.label.setText)
        dock.featuresChanged.connect(self.featuresChanged)
        self.featuresChanged(dock.features())
        self.updateIcons()

    def _button(self, toolTip, slot):
        button = QtWidgets.QToolButton(self)
        button.setObjectName("dockTitleButton")
        button.setToolTip(toolTip)
        button.setAutoRaise(True)
        button.setIconSize(QtCore.QSize(12, 12))
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(slot)
        return button

    def updateIcons(self):
        self.floatButton.setIcon(theme.styleIcon("float"))
        self.closeButton.setIcon(theme.styleIcon("close"))

    def featuresChanged(self, features):
        self.floatButton.setVisible(bool(features & QtWidgets.QDockWidget.DockWidgetFloatable))
        self.closeButton.setVisible(bool(features & QtWidgets.QDockWidget.DockWidgetClosable))

    def toggleFloating(self):
        self.dock.setFloating(not self.dock.isFloating())


class MCEDockWidget(QtWidgets.QDockWidget):
    wasVisible = None

    def __init__(self, *a, **kw):
        super(MCEDockWidget, self).__init__(*a, **kw)
        self._unfocusedOpacity = 1.0
        self._titleBar = None
        theme.notifier.changed.connect(self.themeChanged)
        self.themeChanged(theme.currentTheme())

    def themeChanged(self, name):
        if theme.isModern():
            if self._titleBar is None:
                self._titleBar = DockTitleBar(self)
            self._titleBar.updateIcons()
            self.setTitleBarWidget(self._titleBar)
            self._titleBar.show()
        elif self._titleBar is not None:
            self.setTitleBarWidget(None)
            self._titleBar.hide()

    def setUnfocusedOpacity(self, value):
        self._unfocusedOpacity = value

    def animate(self, value):
        self.setWindowOpacity(value)

    def enterEvent(self, event):
        if self._unfocusedOpacity == 1.0:
           return
        self.animation = animation = QtCore.QPropertyAnimation(self, b'windowOpacity')
        animation.setDuration(100)
        animation.setStartValue(self.windowOpacity())
        animation.setEndValue(1.0)
        animation.valueChanged.connect(self.animate)
        animation.start()

    def leaveEvent(self, event):
        if self._unfocusedOpacity == 1.0:
           return
        self.animation = animation = QtCore.QPropertyAnimation(self, b'windowOpacity')
        animation.setDuration(250)
        animation.setStartValue(self.windowOpacity())
        animation.setEndValue(self._unfocusedOpacity)
        animation.valueChanged.connect(self.animate)
        animation.start()
