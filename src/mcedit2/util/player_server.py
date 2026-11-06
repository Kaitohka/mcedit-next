"""
    player_server
"""


import json
import base64
import logging
import os
from uuid import UUID

import sys
from PySide6 import QtCore, QtNetwork

from mcedit2.util import directories
from mcedit2.util.load_png import loadPNGTexture

log = logging.getLogger(__name__)


_playerDataFolder = os.path.join(directories.getUserFilesDirectory(), "player_data")
_playerSkinsFolder = os.path.join(_playerDataFolder, "skins")
_playerDataCachePath = os.path.join(_playerDataFolder, "players.json")

if not os.path.exists(_playerSkinsFolder):
    os.makedirs(_playerSkinsFolder)

_netManager = QtNetwork.QNetworkAccessManager()


class PlayerServerError(IOError):
    pass


class PlayerDataCache(QtCore.QObject):
    def __init__(self):
        super(PlayerDataCache, self).__init__()
        self.pendingReplies = []
        if os.path.exists(_playerDataCachePath):
            with open(_playerDataCachePath, 'rb') as f:
                try:
                    nameCache = json.load(f)
                    self.nameCache = {UUID(k): v for k, v in nameCache}
                except:
                    log.exception("Error while loading player data cache.")
                    self.nameCache = {}
        else:
            self.nameCache = {}

        self.callbacks = {}
        # Players the session server has no profile for, such as offline or test accounts. Not
        # asked about again until MCEdit restarts.
        self.unknownPlayers = set()

        _netManager.finished.connect(self._finished)

    def _writeCache(self):
        with open(_playerDataCachePath, 'w', encoding='utf-8') as f:
            nameCache = [(k.hex, v) for k, v in self.nameCache.items()]
            json.dump(nameCache, f)

    def _queryServer(self, uuid, callback):
        if not isinstance(uuid, UUID):
            raise TypeError("Expected UUID, got %s", type(uuid))

        if uuid in self.callbacks:
            # Already asked: answer this caller along with the others.
            self.callbacks[uuid].append(callback)
            return
        self.callbacks[uuid] = [callback]
        url = "https://sessionserver.mojang.com/session/minecraft/profile/{}".format(uuid.hex)
        request = QtNetwork.QNetworkRequest(QtCore.QUrl(url))
        reply = _netManager.get(request)
        reply.uuid = uuid
        # Attributes live on the Python wrapper, which must stay alive until the reply finishes.
        self.pendingReplies.append(reply)

    def _finished(self, reply):
        if reply in self.pendingReplies:
            self.pendingReplies.remove(reply)
        reply.deleteLater()
        if hasattr(reply, 'uuid'):
            self._queryFinished(reply)
        else:
            self._textureFetchFinished(reply)

    def _queryFinished(self, reply):
        uuid = reply.uuid
        data = reply.readAll()

        waiting = self.callbacks.pop(uuid, [])

        def callback(result, error):
            for each in waiting:
                each(result, error)

        data = bytes(data)
        if not data.strip():
            # The server answers with no content for a UUID it has no profile for. With no network
            # there is no content either; then don't keep trying until MCEdit restarts.
            if reply.error() != QtNetwork.QNetworkReply.NetworkError.NoError:
                log.info("Could not ask the session server about %s: %s", uuid, reply.errorString())
            else:
                log.info("No player profile for %s", uuid)
            self.unknownPlayers.add(uuid)
            callback(None, PlayerServerError("No player profile for %s" % uuid))
            return
        try:
            jsonData = json.loads(data)
        except ValueError as e:
            log.warning("Session server did not answer with JSON for %s: %s", uuid, e)
            callback(None, e)
        else:
            if 'error' in jsonData:
                callback(None, PlayerServerError(jsonData))
            else:
                id = UUID(jsonData['id'])
                name = jsonData['name']
                props = jsonData['properties']

                self.nameCache[uuid] = name

                for p in props:
                    if p['name'] == 'textures':
                        textureInfo = base64.b64decode(p['value'])
                        try:
                            textureInfo = json.loads(textureInfo)
                        except:
                            log.exception("Cannot parse texture info.")
                            continue
                        # timestamp = textureInfo['timestamp']
                        # profileId = textureInfo['profileId']
                        # profileName = textureInfo['profileName']
                        textures = textureInfo.get('textures')
                        if textures:
                            skin = textures.get('SKIN')
                            if skin:
                                skinURL = skin.get('url')
                                if skinURL:
                                    # get skin
                                    texturePath = os.path.join(_playerSkinsFolder, uuid.hex + '.png')

                                    request = QtNetwork.QNetworkRequest(QtCore.QUrl(skinURL))

                                    skinReply = _netManager.get(request)

                                    skinReply.texturePath = texturePath
                                    skinReply.id = id
                                    skinReply.name = name
                                    skinReply.callback = callback
                                    self.pendingReplies.append(skinReply)

                        # cape = textures.get['CAPE']
                        # if cape:
                        #     capeURL = skin.get['cape']
                        #     if capeURL:
                        #         pass


    def _textureFetchFinished(self, reply):
        texturePath = reply.texturePath
        textureImage = reply.readAll()

        with open(texturePath, 'wb') as f:
            f.write(bytes(textureImage))

        result = {
            'id': reply.id,
            'name': reply.name,
            'texturePath': texturePath,
        }
        self._writeCache()
        reply.callback(result, None)

    # len({'id': '23fc8bb10e5d47d1b43ff19299a28ac9', 'name': 'Denox69', 'properties': [{'name': 'textures', 'value': 'eyJ0aW1lc3...WNyYWZ0Lm5ldC90ZXh0dXJlLzQxMzVkNzQxYTRjYjFmMTA1Mjc1NDU4ZDRhMWE2OWYyOGE4MjU4NjY3NWY4ZjQ0MTJjNjRiYmQyNGU5MGJjIn19fQ=='}]})

    @staticmethod
    def getPlayerInfo(uuid, callback):
        PlayerDataCache.instance._getPlayerInfo(uuid, callback)

    def _getPlayerInfo(self, uuid, callback):
        """
        Get the name and texture image for the player with the given UUID. The callback
        function will be passed the name and image path with this call signature:

            def callback(result, error):
                pass

        If an error occurs, error will be an Exception object containing the error and the
        result will be None, otherwise the result will be a dict with the keys 'id',
        'name', and 'texturePath'

        Parameters
        ----------
        uuid : UUID
        callback : Callable(tuple or None, Exception or None)

        Returns
        -------
        None
        """
        if uuid in self.unknownPlayers:
            callback(None, PlayerServerError("No player profile for %s" % uuid))
        elif uuid not in self.nameCache:
            self._queryServer(uuid, callback)
        else:
            texturePath = os.path.join(_playerSkinsFolder, uuid.hex + '.png')
            if not os.path.exists(texturePath):
                self._queryServer(uuid, callback)
            else:
                log.debug("Found name and texturePath in cache")
                name = self.nameCache[uuid]
                result = {
                    'id': uuid,
                    'name': name,
                    'texturePath': texturePath,
                }
                callback(result, None)

PlayerDataCache.instance = PlayerDataCache()