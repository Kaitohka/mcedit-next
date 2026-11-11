#!/usr/bin/env python
"""
    main.py

    This is the main entry point of the MCEdit application.

    The order of statements in this file matters: OpenGL options must be set before OpenGL.GL is
    imported, and the default surface format before the QApplication is created.
"""
import os
import sys

import OpenGL

# Windows consoles use an encoding that can't represent all characters. Replace what can't be
# printed, rather than failing while printing a filename.
for stream in (sys.stdout, sys.stderr):
    if stream is not None and hasattr(stream, "reconfigure"):
        stream.reconfigure(errors="replace")

# IMPORTANT: Must set OpenGL.BLAH_BLAH **BEFORE** importing OpenGL.GL
if "-log" in sys.argv:
    sys.argv.remove('-log')
    OpenGL.FULL_LOGGING = True

if "-debug" not in sys.argv:
    OpenGL.ERROR_CHECKING = False
else:
    OpenGL.ERROR_LOGGING = True
    OpenGL.CONTEXT_CHECKING = True
    while "-debug" in sys.argv:
        sys.argv.remove('-debug')
    print("GL Errors enabled!")

from mcedit2.util.gen_ui import compile_ui
from mcedit2.util import profiler, qglcontext

import logging
log = logging.getLogger(__name__)


def setup_logging():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    logging.captureWarnings(True)

    from mcedit2.util.directories import getUserFilesDirectory
    logfile = os.path.abspath(os.path.join(getUserFilesDirectory(), 'mcedit2.log'))
    if hasattr(sys, 'frozen') and sys.platform == "darwin":
        logfile = os.path.expanduser("~/Library/Logs/mcedit2.log")

    fmt = logging.Formatter('[%(levelname)s][%(filename)s:%(lineno)d]:%(message)s')

    print("Logging to %s" % logfile)

    logfileHandler = logging.FileHandler(logfile, mode="w", encoding="utf-8")
    logfileHandler.setLevel(logging.INFO)
    logfileHandler.setFormatter(fmt)
    root_logger.addHandler(logfileHandler)

    consoleHandler = logging.StreamHandler()
    consoleHandler.setLevel(logging.WARN)

    if "-v" in sys.argv:
        sys.argv.remove("-v")
        consoleHandler.setLevel(logging.INFO)
    if "-vv" in sys.argv:
        sys.argv.remove("-vv")
        consoleHandler.setLevel(logging.DEBUG)
        logfileHandler.setLevel(logging.DEBUG)

    consoleHandler.setFormatter(fmt)
    root_logger.addHandler(consoleHandler)

    # A message below every handler's level would only be made to be thrown away. Loading chunks logs a lot of them.
    root_logger.setLevel(min(logfileHandler.level, consoleHandler.level))


editorApp = None


@profiler.function("startup")
def startup():
    global editorApp
    setup_logging()
    compile_ui()
    sys.excepthook = excepthook

    from mcedit2 import __version__
    log.info("MCEdit2 version %s starting...", __version__)

    pyi_tmpdir = getattr(sys, "_MEIPASS", None)
    if pyi_tmpdir:
        os.chdir(pyi_tmpdir)

    qglcontext.setDefaultFormat()

    from mcedit2.editorapp import MCEditApp
    from mcedit2 import support_modules  # noqa: F401  (keeps these modules in frozen builds)

    editorApp = MCEditApp(sys.argv)
    editorApp.startup()

    return editorApp


def excepthook(exc_type, exc_value, exc_tb):
    # PySide catches errors raised in Qt signal calls and virtual methods, reports them through
    # sys.excepthook, then tries to continue. Show them instead of letting them pass unnoticed.
    log.error("Unhandled Exception: \n\t%s", exc_value, exc_info=(exc_type, exc_value, exc_tb))
    if isinstance(exc_value, KeyboardInterrupt):
        sys.exit(-1)

    from mcedit2.dialogs.error_dialog import showErrorDialog
    showErrorDialog("Unhandled Exception", (exc_type, exc_value, exc_tb), fatal=True)


def main():
    app = startup()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
