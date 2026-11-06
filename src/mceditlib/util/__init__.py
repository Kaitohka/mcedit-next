"""
    util.py
"""
import collections
from contextlib import contextmanager
from math import floor
import os

WorldInfo = collections.namedtuple("WorldInfo", "displayName lastPlayedTime versionInfo")

@contextmanager
def notclosing(f):
    yield f

def chunk_pos(x, z):
    return int(floor(x)) >> 4, int(floor(z)) >> 4

def exhaust(_iter):
    """
    Functions named ending in "Iter" return an iterable object that does
    long-running work and yields progress information on each call. exhaust()
    is used to implement the non-Iter equivalents

    :type _iter: Iterable
    """
    i = None
    for i in _iter:
        pass
    return i

def displayName(filename):
    shortname = os.path.basename(filename)
    if shortname == "level.dat":
        shortname = os.path.basename(os.path.dirname(filename))

    return shortname

def matchEntityTags(ref, kw):
    tag = ref.rootTag  # xxx getattr ref?
    for k in kw:
        if k == 'UUID':
            value = ref.UUID
        else:
            value = tag[k].value
        if value != kw[k]:
            return False

    return True
