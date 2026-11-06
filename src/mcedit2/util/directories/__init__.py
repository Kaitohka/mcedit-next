"""
    directories

    Get platform specific user data folders.
"""

import logging
import os
import sys
log = logging.getLogger(__name__)

if sys.platform == "win32":
    from .win32 import getUserFilesDirectory
elif sys.platform == "darwin":
    from .mac import getUserFilesDirectory
else:
    from .posix import getUserFilesDirectory


def getUserSchematicsDirectory():
    return os.path.join(getUserFilesDirectory(), "schematics")

def getUserPluginsDirectory():
    return os.path.join(getUserFilesDirectory(), "plugins")
