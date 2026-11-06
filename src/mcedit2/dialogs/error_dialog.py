"""
    error_dialog
"""

import logging
import traceback
import sys
import platform

from PySide6 import QtCore, QtWidgets

from mcedit2.ui.dialogs.error_dialog import Ui_errorDialog
from mcedit2.util import qglcontext
from mcedit2.util.resources import isSrcCheckout
from mcedit2.util.screen import centerWidgetInScreen
from mcedit2.util.settings import Settings
from mcedit2.util.showprogress import MCEProgressDialog

log = logging.getLogger(__name__)

_errorShown = False

settings = Settings()


def showErrorDialog(text, tb=None, fatal=True, report=True):
    global _errorShown
    if tb is None:
        tb = sys.exc_info()
    _errorShown = True
    grabber = QtWidgets.QWidget.mouseGrabber()
    if grabber:
        grabber.releaseMouse()
        
    dialog = ErrorDialog(text, tb, fatal, report)
    dialog.exec()
    _errorShown = False


class ErrorDialog(QtWidgets.QDialog, Ui_errorDialog):
    """
    A dialog for displaying an error traceback when something goes wrong.

    Used to report compile and run errors for plugin modules and classes, and
    to report errors in MCEdit itself during signal or event handlers.
    """
    def __init__(self, text, exc_info, fatal, report):
        super(ErrorDialog, self).__init__()
        self.setupUi(self)
        self.setModal(True)
        self.exc_info = exc_info
        self.shouldReport = report

        exc_type, exc_value, exc_tb = exc_info

        self.errorDescriptionLabel.setText(text)
        tbText = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

        contextInfo = qglcontext.getContextInfo() or ""

        from mcedit2 import __version__
        tbText = "MCEdit version: %s\n" \
                 "Python version: %s\n" \
                 "Platform: %s\n" \
                 "System version: %s\n" \
                 "Processor: %s\n" \
                 "\n" \
                 "%s\n" \
                 "------\n\n" \
                 "%s\n\n" \
                 "%s" % (__version__, sys.version, sys.platform,
                         platform.platform(), platform.processor(),
                         contextInfo, text, tbText)
        self.errorText = tbText

        self.tracebackView.setText(tbText)

        self.restartMCEditLabel.setVisible(fatal)

        self.restartMCEditButton.setVisible(fatal)
        self.restartMCEditButton.clicked.connect(self.restartMCEdit)

        self.quitMCEditButton.setVisible(fatal)
        self.quitMCEditButton.clicked.connect(self.quitMCEdit)

        self.continueButton.clicked.connect(self.continueMCEdit)

        self.debugButton.setEnabled(isSrcCheckout())
        self.debugButton.clicked.connect(self.debugPdb)

        # Errors stay on this computer. There is nothing to upload to, so hide those controls.
        self.reportingLabelStack.setVisible(False)
        self.reportErrorCheckbox.setVisible(False)
        self.copyToPastebinButton.setVisible(False)
        self.pastebinURLBox.setVisible(False)

    def show(self, *args, **kwargs):
        super(ErrorDialog, self).show(*args, **kwargs)
        centerWidgetInScreen(self)

    def restartMCEdit(self):
        QtCore.QProcess.startDetached(sys.executable, sys.argv[1:])
        raise SystemExit

    def quitMCEdit(self):
        raise SystemExit

    def continueMCEdit(self):
        self.accept()

    def debugPdb(self):
        import pdb; pdb.post_mortem(self.exc_info[2])