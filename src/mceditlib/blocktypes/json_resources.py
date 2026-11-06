"""
    json_resources
"""
import json
import logging
import os
from os.path import join
import traceback

log = logging.getLogger(__name__)

_cachedJsons = {}

def openResource(filename):
    return open(join(os.path.dirname(__file__), filename), "rb")

def getJsonFile(filename):
    if filename in _cachedJsons:
        return _cachedJsons[filename]

    try:
        with openResource(filename) as f:
            entries = json.loads(f.read())
        _cachedJsons[filename] = entries
        return entries
    except EnvironmentError as e:
        log.error(u"Exception while loading JSON from %s: %s", filename, e)
        traceback.print_exc()
