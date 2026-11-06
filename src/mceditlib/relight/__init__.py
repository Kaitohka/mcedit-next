"""
    __init__
"""
import logging

log = logging.getLogger(__name__)

updateLightsByCoord = updateLightsInSelection = NotImplemented


def setMethod(name):
    if name == "pure":
        from mceditlib.relight import pure_python
        setModule(pure_python)
    if name == "cython":
        from mceditlib.relight import with_cython
        setModule(with_cython)


def setModule(mod):
    global updateLightsByCoord, updateLightsInSelection
    updateLightsByCoord = mod.updateLightsByCoord
    updateLightsInSelection = mod.updateLightsInSelection

setMethod("cython")
