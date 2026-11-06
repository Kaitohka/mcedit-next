"""
    screen
"""

import logging
from PySide6 import QtCore, QtWidgets

log = logging.getLogger(__name__)

def centerWidgetInScreen(widget, resize=None):
    """

    :param widget:
    :type widget:
    :param resize: Fraction of screen width/height to resize widget to
    :type resize:
    :return:
    :rtype:
    """
    parent = widget.parent()
    screen = (parent or widget).screen().availableGeometry()
    w = screen.width()
    h = screen.height()
    r = widget.geometry()
    if resize is not None:
        w2 = w * resize * 0.5
        h2 = h * resize * 0.5
    else:
        w2 = r.width() * 0.5
        h2 = r.height() * 0.5
    r = QtCore.QRect(int(screen.x() + w * 0.5 - w2),
                     int(screen.y() + h * 0.5 - h2),
                     int(2 * w2),
                     int(2 * h2))

    widget.setGeometry(r)
