"""
    session_lock

    The lock that says a world is in use, as the game has done it since Minecraft 1.15: an operating system
    lock on the file session.lock, held for as long as the world is open. What's in the file does not matter.

    While this is held, the game refuses to open the world. While the game holds it, MCEdit can't write to
    the world, which is what matters most of all: the game would overwrite the changes, or worse.
"""
import logging
import os
import struct
import time

from mceditlib.exceptions import LevelFormatError

log = logging.getLogger(__name__)

LOCK_FILE = "session.lock"
SNOWMAN = "☃".encode("utf-8")  # the game writes this, and it may as well be the same

# The game locks the whole file, including past its end, so this overlaps however little of it the game has.
LOCKED_BYTES = 1 << 30


class WorldInUse(LevelFormatError):
    """
    Another program, most likely the game, has the world open.
    """


if os.name == "nt":
    import msvcrt

    def _lock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, LOCKED_BYTES)

    def _unlock(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, LOCKED_BYTES)

else:
    import fcntl

    def _lock(f):
        fcntl.lockf(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(f):
        fcntl.lockf(f.fileno(), fcntl.LOCK_UN)


class SessionLock(object):
    def __init__(self, worldFolder):
        """
        Lock a world. Raises WorldInUse if something else has it.

        :type worldFolder: str
        """
        self.path = os.path.join(worldFolder, LOCK_FILE)
        self._file = None
        self._acquire()

    def _acquire(self):
        created = not os.path.exists(self.path)
        try:
            f = open(self.path, "r+b" if not created else "w+b", buffering=0)
        except EnvironmentError as e:
            raise WorldInUse("The world's session.lock could not be opened: %s" % e) from e

        try:
            _lock(f)
        except EnvironmentError as e:
            f.close()
            raise WorldInUse("This world is open in another program, most likely Minecraft. Close it there "
                             "first, or open the world read-only here.") from e

        if created:
            # After the lock, as on Windows the bytes of a locked file can't be read by anyone else.
            os.write(f.fileno(), SNOWMAN)
        self._file = f

    @property
    def held(self):
        return self._file is not None

    # Up to 1.14 the game did not lock the file, but wrote in it the time it opened the world, and gave the world up if
    # that changed. These keep the same kind of timestamp for such worlds.

    def writeTimestamp(self):
        """ Write the time now into the file, as a big-endian count of milliseconds, and return it. """
        stamp = int(time.time() * 1000)
        self._file.seek(0)
        self._file.write(struct.pack(">q", stamp))
        self._file.truncate()
        os.fsync(self._file.fileno())
        return stamp

    def readTimestamp(self):
        """ The timestamp in the file, or None if it does not hold one. """
        self._file.seek(0)
        data = self._file.read(8)
        return struct.unpack(">q", data)[0] if len(data) == 8 else None

    def release(self):
        if self._file is not None:
            try:
                _unlock(self._file)
            except EnvironmentError as e:
                log.warning("Could not unlock %s: %s", self.path, e)
            self._file.close()
            self._file = None

    def __del__(self):
        self.release()
