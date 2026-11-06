"""
    gen_ui

Compile .ui files to Python modules, skipping any that are already up to date.
"""
import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)


def uiFiles(uiDir):
    for root, dirs, files in os.walk(uiDir):
        for name in files:
            if name.endswith(".ui"):
                yield os.path.join(root, name)


def compile_ui():
    from mcedit2.util import resources
    if not resources.isSrcCheckout():
        return

    src = resources.getSrcFolder()
    uiDir = os.path.join(src, "mcedit2", "ui")

    uic = shutil.which("pyside6-uic", path=os.path.dirname(sys.executable)) or shutil.which("pyside6-uic")
    if uic is None:
        raise RuntimeError("pyside6-uic was not found. Is PySide6 installed?")

    compiled = 0
    for uiPath in uiFiles(uiDir):
        pyPath = os.path.splitext(uiPath)[0] + ".py"
        if os.path.exists(pyPath) and os.path.getmtime(pyPath) >= os.path.getmtime(uiPath):
            continue
        subprocess.run([uic, uiPath, "-o", pyPath], check=True)
        compiled += 1

    log.info("Compiled %d .ui files.", compiled)


if __name__ == '__main__':
    compile_ui()
