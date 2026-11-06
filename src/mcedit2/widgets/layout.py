

import traceback

import sys
from PySide6 import QtWidgets
from PySide6.QtCore import Qt


def _Box(box, *a):
    for arg in a:
        if isinstance(arg, tuple):
            item = arg[0]
        else:
            item = arg
            arg = (item,)

        if isinstance(item, QtWidgets.QLayout):
            box.addLayout(*arg)
        if isinstance(item, QtWidgets.QWidget):
            box.addWidget(*arg)
        if isinstance(item, (int, float)):
            box.addSpacing(item)
        if item is None:
            box.addStretch()

    return box

def Row(*a, **kw):
    """
    :rtype: QtWidgets.QHBoxLayout
    """
    margin = kw.pop('margin', None)
    box = QtWidgets.QHBoxLayout(**kw)
    _Box(box, *a)
    if margin is not None:
        box.setContentsMargins(margin, margin, margin, margin)
    return box


def Column(*a, **kw):
    """
    :rtype: QtWidgets.QVBoxLayout
    """
    margin = kw.pop('margin', None)
    box = QtWidgets.QVBoxLayout(**kw)
    _Box(box, *a)
    if margin is not None:
        box.setContentsMargins(margin, margin, margin, margin)
    return box

def setWidgetError(widget, exc, msg = "An error has occurred."):
    """
    Add a subwidget to `widget` that displays the error message for the exception `exc`
    :param widget:
    :param exc:
    :return:
    """
    layout = QtWidgets.QVBoxLayout()
    textArea = QtWidgets.QTextEdit()
    textArea.setReadOnly(True)
    message = msg + "\n"
    # Sometimes str(exc) returns non-ascii bytes - assume they are filenames?
    try:
        excmsg = str(exc)
    except UnicodeError:
        try:
            excmsg = str(exc).decode(sys.getfilesystemencoding(), errors='replace')
        except UnicodeError:
            excmsg = str(type(exc))


    message += excmsg + "\n\n"
    message += traceback.format_exc()
    textArea.setText(message)
    layout.addWidget(textArea)
    widget.setLayout(layout)
