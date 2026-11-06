"""
    ${NAME}
"""

import logging
import os
import sys

log = logging.getLogger(__name__)

def isSrcCheckout():
    srcFolder = getSrcFolder()
    gitPath = os.path.join(os.path.dirname(srcFolder), '.git')

    return os.path.exists(gitPath)

def getSrcFolder():
    """
    Find the 'src/' folder of a source checkout.

    ...should maybe assert that '.git' exists?
    :return: unicode
    """
    import mcedit2
    mod = os.path.realpath(mcedit2.__file__)  # src/mcedit2/__init__.py

    return os.path.dirname(os.path.dirname(mod))

def resourcePath(filename):
    """
    Return the absolute path of a filename included as a resource.

    "Resource" is not well-defined. When packaged with PyInstaller, the filename is found
    relative to the app's folder (_MEIPASS). When running from source, it is relative to the
    'src' folder. I'd imagine that when installed as a .whl (on linux) we need to use
    pkg_resources to get filenames.

    If the file does not exist, this is fatal error that usually indicates a packaging
    problem or an incorrect filename.

    :param filename:
    :return:
    """
    filename = filename.replace('/', os.path.sep)
    basedir = getattr(sys, "_MEIPASS", None)  # if pyinstaller'd
    if basedir is None:
        # should work across platforms
        basedir = getSrcFolder()

    path = os.path.join(basedir, filename)
    if not os.path.exists(path):
        raise RuntimeError("Could not get resource path for %s\n(Tried %s which does not exist)" % (filename, path))

    return path
