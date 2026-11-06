"""
    minecraftinstall
"""

import hashlib
import itertools
import json
import re
import zipfile

import time
from PySide6 import QtCore, QtNetwork, QtWidgets
import logging
import os
from PySide6.QtCore import Qt
from mcedit2.resourceloader import ResourceLoader
from mcedit2.ui.minecraft_installs import Ui_installsWidget
from mcedit2.util import settings
from mcedit2.util.directories import getUserFilesDirectory
from mceditlib import directories

from mceditlib.anvil.format_profile import profileForDataVersion
from mceditlib.anvil.modern import worldDataVersion
from mceditlib.blocktypes import pack_library

log = logging.getLogger(__name__)

installationsOption = settings.Settings().getOption("minecraft_installs/installations", "json", [])
multiMCInstallsOption = settings.Settings().getOption("minecraft_installs/multimc_installs", "json", [])
currentInstallOption = settings.Settings().getOption("minecraft_installs/current_install_path", str, "")
currentVersionOption = settings.Settings().getOption("minecraft_installs/current_version", str, "")
currentResourcePackOption = settings.Settings().getOption("minecraft_installs/current_resource_pack", str, "")
allowSnapshotsOption = settings.Settings().getOption("minecraft_installs/allow_snapshots_", bool, False)

_installs = None

_netManager = QtNetwork.QNetworkAccessManager()

_versionCacheDir = os.path.join(getUserFilesDirectory(), 'versionCache')

def jarGameVersion(path):
    """ Return the version of the game a client jar is for, or None if it does not look like one. """
    try:
        with zipfile.ZipFile(path) as jar:
            return json.loads(jar.read("version.json"))["id"]
    except (EnvironmentError, zipfile.BadZipFile, KeyError, ValueError):
        return None


def assetsVersionForWorld(filename):
    """
    Return the version of the game to take block models and textures from for the given file, if it
    is a world that needs a particular one. Otherwise returns None.
    """
    isWorld = os.path.isdir(filename) or os.path.basename(filename) in ("level.dat", "level.dat_old")
    if not isWorld:
        return None
    dataVersion = worldDataVersion(filename)
    profile = profileForDataVersion(dataVersion) if dataVersion is not None else None
    return profile.assetsVersion if profile is not None else None


def findJarBesideWorld(worldFolder, version):
    """
    Launchers that keep each version of the game apart put a world in versions/<name>/saves/, with a
    full copy of the game at versions/<name>/<name>.jar. This is often what a modded game looks like,
    and has no plain copy of the game anywhere else.
    """
    gameFolder = os.path.dirname(os.path.dirname(worldFolder))
    path = os.path.join(gameFolder, os.path.basename(gameFolder) + ".jar")
    if os.path.isfile(path) and jarGameVersion(path) == version:
        return path
    return None


def getModernResourceLoader(filename, version):
    worldFolder = filename if os.path.isdir(filename) else os.path.dirname(filename)
    installs = GetInstalls()
    jar = findJarBesideWorld(worldFolder, version) or installs.findAssetsJar(version) or installs.requestAssetsJar(version)

    loader = ResourceLoader(jar)
    resourcePack = currentResourcePackOption.value() or None
    install = installs.getCurrentInstall()
    if resourcePack and install is not None:
        try:
            loader.addZipFile(install.getResourcePackPath(resourcePack))
        except Exception as e:
            log.warning("Failed to load resource pack: %r\nPack: %s", e, resourcePack)

    # The search path goes from most to least important.
    for pack in pack_library.selectionForWorld(worldFolder).packs:
        loader.addBlockPack(pack)

    modsFolder = os.path.join(os.path.dirname(os.path.dirname(worldFolder)), "mods")
    if os.path.isdir(modsFolder):
        log.info("Loading mods from %s", modsFolder)
        loader.addModsFolder(modsFolder)

    loader.addZipFile(jar)
    return loader


def getResourceLoaderForFilename(filename):
    filename = os.path.normpath(filename)
    version = assetsVersionForWorld(filename)
    if version is not None:
        return getModernResourceLoader(filename, version)

    installs = GetInstalls()
    if not installs.findVersionWithAssets():
        installs.requestAssetsJar(installs._requiredVersion)

    # Is this world inside a MultiMC instance?
    for instance in installs.instances:
        savesFolder = os.path.normpath(instance.saveFileDir)
        if filename.startswith(savesFolder):
            return instance.getResourceLoader()

    # Nope. Use the version and respack chosen in the world list.
    # ... should search for installs matching this one, but vanilla installs are still multi-version...
    loader = getSelectedResourceLoader()
    if not os.path.isdir(filename):
        worldFolder = os.path.dirname(filename)
    else:
        worldFolder = filename
    savesFolder = os.path.dirname(worldFolder)
    mcFolder = os.path.dirname(savesFolder)
    modsFolder = os.path.join(mcFolder, "mods")
    if os.path.isdir(modsFolder):
        log.info("Loading mods from %s", modsFolder)
        loader.addModsFolder(modsFolder)

    return loader


def getSelectedResourceLoader():
    install = GetInstalls().getCurrentInstall()
    if install is None:
        return GetInstalls().getDefaultResourceLoader()

    v = currentVersionOption.value()
    if not v:
        v = list(install.versions)[0]
    p = currentResourcePackOption.value() or None
    return install.getResourceLoader(v, p)


def GetInstalls():
    global _installs
    if _installs is None:
        _installs = MCInstallGroup()
    return _installs


def md5hash(filename):
    md5 = hashlib.md5()
    with open(filename, "rb") as f:
        md5.update(f.read())
        return md5.hexdigest()


class MCInstallGroup(QtCore.QObject):
    
    # Worlds in the format from before Minecraft 1.13 use the assets of the last version with that format.
    _requiredVersion = '1.12'
    _requiredMajorMinor = (1, 12)
    # Worlds from newer versions use assets of their own, and this is the one most worlds will need.
    _primaryVersion = '1.21.1'
            
    def __init__(self):
        """
        Represents all Minecraft installs known to MCEdit. Loads installs from settings and detects the current install
        in ~/.minecraft or equivalent.

        Also represents MultiMC instances as separate installs, each with a single version and a single saves folder.

        :return:
        :rtype:
        """
        super(MCInstallGroup, self).__init__()
        self._installations = list(self._loadInstalls())
        self._mmcInstalls = list(self._loadMMCInstalls())
        path = currentInstallOption.value()
        install = self.getInstall(path)
        if install is None:
            if len(self._installations):
                path = self._installations[0].path
                currentInstallOption.setValue(path)
            else:
                currentInstallOption.setValue("")

        self.getDefaultInstall()

    def _loadMMCInstalls(self):
        for install in multiMCInstallsOption.value():
            configFile = install["configFile"]
            try:
                install = MultiMCInstall(self, configFile)
                yield install
            except MultiMCInstallError as e:
                log.warning("Not using MultiMC with config file %s: %s", configFile, e)

    def _loadInstalls(self):
        for install in installationsOption.value():
            name = install["name"]
            path = install["path"]
            try:
                install = MCInstall(self, path, name)
                install.checkUsable()
                yield install
            except MCInstallError as e:
                log.warning("Not using install %s: %s", install.path, e)

    def _saveInstalls(self):
        installationsOption.setValue([i.getJsonSettingValue() for i in self._installations])
        multiMCInstallsOption.setValue([i.getJsonSettingValue() for i in self._mmcInstalls])
        log.info("MCInstall saved settings: %s", installationsOption.value())

    def getDefaultInstall(self):
        """
        Probes for a minecraft installation in the default install folder, and adds it to the group.

        :return:
        :rtype: MCInstall
        """
        minecraftDir = directories.minecraftDir
        defaultInstall = MCInstall(self, minecraftDir, "(Default)")
        try:
            defaultInstall.checkUsable()
        except MCInstallError as e:
            log.warning("Default install not usable: %s", e)
            return None
        else:
            value = defaultInstall.getJsonSettingValue()
            if value not in installationsOption.value():
                self._installations.append(defaultInstall)
                self._saveInstalls()
            if currentInstallOption.value() == "":
                currentInstallOption.setValue(defaultInstall.path)
            return defaultInstall

    def selectedInstallPath(self):
        path = currentInstallOption.value()
        for install in self._installations:
            if install.path == path:
                return path

        if len(self._installations):
            install = self._installations[0]
            currentInstallOption.setValue(install.path)
            return install.path

    @property
    def installs(self):
        return list(self._installations)

    def getInstall(self, path):
        for install in self._installations:
            if install.path == path:
                return install
        return None

    def getCurrentInstall(self):
        return self.getInstall(self.selectedInstallPath())

    def addInstall(self, install):
        self._installations.append(install)
        self._saveInstalls()

    def removeInstall(self, path):
        self._installations = [i for i in self._installations if i.path != path]
        self._saveInstalls()

    def addMMCInstall(self, install):
        self._mmcInstalls.append(install)
        self._saveInstalls()

    def removeMMCInstall(self, index):
        del self._mmcInstalls[index]
        self._saveInstalls()

    def ensureValidInstall(self):
        """
        Called on app startup. Display install config dialog if no installs were found

        :return:
        :rtype:
        """
        while not self.hasUsableAssets():
            msgBox = QtWidgets.QMessageBox()
            msgBox.setWindowTitle(self.tr("Minecraft not found."))
            msgBox.setText(self.tr("MCEdit requires an installed Minecraft version %s to "
                           "access block textures, models, and metadata.") % self._primaryVersion)

            download = msgBox.addButton(self.tr("Download Minecraft"), QtWidgets.QMessageBox.AcceptRole)
            configure = msgBox.addButton(self.tr("Configure Installs"), QtWidgets.QMessageBox.ActionRole)
            exit_ = msgBox.addButton(self.tr("Exit MCEdit"), QtWidgets.QMessageBox.RejectRole)

            msgBox.exec()
            result = msgBox.clickedButton()
            log.info("Result: %s", result)
            if result == download:
                self.downloadVersionWithAssets(self._primaryVersion)
            elif result == configure:
                installsWidget = MinecraftInstallsDialog()
                installsWidget.exec()
            else:
                raise SystemExit

    def hasUsableAssets(self):
        """
        True if MCEdit can start: there is a client jar for the version most worlds need, or for the
        version that worlds from before 1.13 need.
        """
        return bool(self.findAssetsJar(self._primaryVersion) or self.findVersionWithAssets())

    @property
    def mmcInstalls(self):
        return self._mmcInstalls

    @property
    def instances(self):
        for mmcInstall in self._mmcInstalls:
            for instance in mmcInstall.instances:
                yield instance

    def findAssetsJar(self, version):
        """ Return the path of the client jar for exactly the given version of the game, or None. """
        cachedVersionPath = os.path.join(_versionCacheDir, version + ".jar")
        if os.path.exists(cachedVersionPath):
            return cachedVersionPath

        installs = list(itertools.chain(self.installs, self.mmcInstalls))
        for install in installs:
            if version in install.versions:
                jarPath = install.getVersionJarPath(version)
                if zipfile.is_zipfile(jarPath):
                    return jarPath

        # Launchers that keep each version apart give it a folder of its own, such as
        # versions/1.21.1-NeoForge/1.21.1-NeoForge.jar, which is a full copy of the game.
        for install in installs:
            for name in install.versions:
                if name != version and name.startswith(version):
                    jarPath = install.getVersionJarPath(name)
                    if jarGameVersion(jarPath) == version:
                        return jarPath
        return None

    def requestAssetsJar(self, version):
        """
        Ask to download the given version of the game, and return the path of its client jar. Raises
        MCInstallError if there is still none after that.
        """
        msgBox = QtWidgets.QMessageBox()
        msgBox.setWindowTitle(self.tr("Minecraft %s not found.") % version)
        msgBox.setText(self.tr("This world needs the block textures and models from Minecraft %s, which was not "
                               "found. MCEdit can download it from Mojang (about 30 MB) and keep it for later.")
                       % version)
        download = msgBox.addButton(self.tr("Download Minecraft %s") % version, QtWidgets.QMessageBox.AcceptRole)
        msgBox.addButton(QtWidgets.QMessageBox.Cancel)
        msgBox.exec()
        if msgBox.clickedButton() is download:
            self.downloadVersionWithAssets(version)

        jar = self.findAssetsJar(version)
        if jar is None:
            raise MCInstallError("Minecraft %s is needed to show this world." % version)
        return jar

    def findVersionWithAssets(self):
        cachedVersionPath = os.path.join(_versionCacheDir, self._requiredVersion + ".jar")
        if os.path.exists(cachedVersionPath):
            return cachedVersionPath
        
        def matchVersion(version):
            major, minor, rev = splitVersion(version)
            # Later versions changed the names of blocks, models and textures.
            if (major, minor) == self._requiredMajorMinor:
                if rev == "":
                    return version
                try:
                    rev = int(rev[1:])  # skip revs like ".2-pre1" and ".1-OptiFine_HD_U_C7", only accept full releases
                    return version
                except ValueError:
                    return

        def jarOkay(path):
            return zipfile.is_zipfile(path)

        for install in self.installs:
            for v in install.versions:
                if matchVersion(v):
                    jarPath = install.getVersionJarPath(v)
                    if jarOkay(jarPath):
                        return jarPath

        for mmcInstall in self.mmcInstalls:
            for v in mmcInstall.versions:
                if matchVersion(v):
                    jarPath = mmcInstall.getVersionJarPath(v)
                    if jarOkay(jarPath):
                        return jarPath

    def downloadVersionWithAssets(self, version=None):
        version = version or self._requiredVersion
        progressDialog = QtWidgets.QProgressDialog(self.tr("Downloading Minecraft %s") % version,
                                               "Cancel",
                                               0, 4)
        
        try:
            self._downloadVersionWithAssets(progressDialog, version)
            
        finally:
            progressDialog.reset()
                    
    def _downloadVersionWithAssets(self, progressDialog, version):

        def displayFailure(msg):
            result = QtWidgets.QMessageBox.warning(None,
                                       self.tr("Failed to download the required Minecraft version."),
                                       self.tr("An error occured while downloading Minecraft from the official servers.\n\n")
                                       + msg,
                                       QtWidgets.QMessageBox.Retry | QtWidgets.QMessageBox.Cancel)
            
            return result == QtWidgets.QMessageBox.Retry
            
                    
        manifestURL = 'https://launchermeta.mojang.com/mc/game/version_manifest.json'
        
        versionInfoURL = None
        clientURL = None
        
        shouldRetry = True
        while shouldRetry:
            if versionInfoURL is None:
                # -- Download version manifest --
                log.info("Downloading version manifest")
                progressDialog.setLabelText(self.tr("Downloading version manifest"))
                        
                request = QtNetwork.QNetworkRequest(QtCore.QUrl(manifestURL))
                manifestReply = _netManager.get(request)
                
                while not manifestReply.isFinished():
                    QtWidgets.QApplication.processEvents()
                    progressDialog.setValue(1)
                    
                log.info("Finished")
                code = manifestReply.error()
                if code:
                    shouldRetry = displayFailure(self.tr("Failed to download version manifest. (Error code: %s)") % str(code))
                    continue
                    
                try:
                    data = manifestReply.readAll()
                    log.info("Parsing version manifest")
                    progressDialog.setLabelText(self.tr("Parsing version manifest"))
 
                    jsonManifest = json.loads(data.data())
                    
                    versions = jsonManifest['versions']
                    
                    targetInfo = [v for v in versions if v['id'] == version][0]
                    versionInfoURL = targetInfo['url']

                except Exception as e:
                    log.exception("Error parsing version manifest")
                    shouldRetry = displayFailure(self.tr("Failed to parse version manifest. (Exception: %s)") % str(e))
                    continue
            
            if clientURL is None:
                # -- Download version info file --
                request = QtNetwork.QNetworkRequest(QtCore.QUrl(versionInfoURL))
                versionInfoDownloadReply = _netManager.get(request)
                log.info("Downloading version info")
                progressDialog.setLabelText(self.tr("Downloading version info"))

                while not versionInfoDownloadReply.isFinished():
                    QtWidgets.QApplication.processEvents()
                    progressDialog.setValue(2)
                
                code = versionInfoDownloadReply.error()
                if code:
                    shouldRetry = displayFailure(self.tr("Failed to download version info. (Error code: %s)") % str(code))
                    continue
            
                try:
                    data = versionInfoDownloadReply.readAll()
                    log.info("Parsing Minecraft %s info", version)
                    progressDialog.setLabelText(self.tr("Parsing version info"))
                    
                    jsonVersion = json.loads(data.data())
                    downloads = jsonVersion['downloads']
                    client = downloads['client']
                    clientURL = client['url']
                
                except Exception as e:
                    log.exception("Error parsing version info file")
                    shouldRetry = displayFailure(self.tr("Failed to parse version info file. (Exception: %s)") % str(e))
                    continue
            
            # -- Download client jar --
            request = QtNetwork.QNetworkRequest(QtCore.QUrl(clientURL))
            versionDownloadReply = _netManager.get(request)
            log.info("Downloading Minecraft jar")
            progressDialog.setLabelText(self.tr("Downloading Minecraft jar"))
            
            while not versionDownloadReply.isFinished():
                QtWidgets.QApplication.processEvents()
                progressDialog.setValue(3)
            
            code = versionDownloadReply.error()
            if code:
                shouldRetry = displayFailure(self.tr("Failed to download Minecraft jar. (Error code: %s)") % str(code))
                continue
            
            data = versionDownloadReply.readAll()
            if not os.path.exists(_versionCacheDir):
                os.makedirs(_versionCacheDir)
                
            with open(os.path.join(_versionCacheDir, version + '.jar'), 'wb') as f:
                f.write(data.data())
                
            shouldRetry = False
            log.info("Downloaded %s", version + '.jar')
            

    def getDefaultResourceLoader(self):
        assetsVersion = self.findVersionWithAssets()
        loader = ResourceLoader(assetsVersion)
        loader.addZipFile(assetsVersion)
        return loader

class MCInstall(object):
    def __init__(self, installGroup, path, name="Unnamed"):
        self.installGroup = installGroup
        self.name = name
        self.path = path
        self.versionsDir = os.path.join(self.path, "versions")

    def checkUsable(self):
        """
        Raises MCInstallError explaining why this install is unusable, or else does nothing
        :return:
        :rtype:
        """
        log.info("Checking install at %s", self.path)
        translate = QtCore.QCoreApplication.translate
        if not os.path.exists(self.path):
            raise MCInstallError(translate("MCInstall", "Minecraft folder does not exist."))
        if not os.path.exists(self.versionsDir):
            raise MCInstallError(translate("MCInstall", "Minecraft versions folder does not exist."))
        if not len(self.versions):
            raise MCInstallError(translate("MCInstall", "Minecraft folder has no minecraft versions"))
        log.info("Found versions:\n%s", self.versions)

    @property
    def versions(self):
        versions = os.listdir(self.versionsDir)
        return [v for v in versions
                if os.path.exists(self.getVersionJarPath(v))
                and (usableVersion(v) or allowSnapshotsOption.value())]

    def getVersionJarPath(self, version):
        return os.path.join(self.versionsDir, version, "%s.jar" % version)

    @property
    def resourcePacks(self):
        respackFolder = os.path.join(self.path, "resourcepacks")
        if not os.path.isdir(respackFolder):
            return []
        return os.listdir(respackFolder)

    def getSaveDirs(self):
        return [os.path.join(self.path, "saves")]  # xxx profile.json

    def getResourcePackPath(self, filename):
        return os.path.join(self.path, "resourcepacks", filename)

    def getJsonSettingValue(self):
        return {"name": self.name,
                "path": self.path}

    def getResourceLoader(self, version, resourcePack):
        assetsVersion = self.installGroup.findVersionWithAssets()
        loader = ResourceLoader(assetsVersion)
        if resourcePack:
            try:
                loader.addZipFile(self.getResourcePackPath(resourcePack))
            except Exception as e:
                log.warning("Failed to load resource pack: %r\nPack: %s", e, resourcePack)
        path = self.getVersionJarPath(version)
        if not os.path.exists(path):
            if len(self.versions):
                log.warning("Version %s not found, falling back to first version found", version)
                path = self.getVersionJarPath(self.versions[0])
        loader.addZipFile(path)
        major, minor, rev = splitVersion(version)

        # Need assets version for multipart block models
        if (major, minor) != (self.installGroup._requiredMajorMinor):
            loader.addZipFile(assetsVersion)

        info = ["%s (%s)" % (z.filename, md5hash(z.filename)) for z in loader.zipFiles]
        log.info("Created ResourceLoader with search path:\n%s", ",\n".join(info))
        return loader


class MMCInstance(object):
    def __init__(self, install, path):
        instanceCfg = os.path.join(path, "instance.cfg")
        if not os.path.exists(instanceCfg):
            raise MultiMCInstanceError("instance.cfg not found: %s" % instanceCfg)

        instanceSettings = QtCore.QSettings(instanceCfg, QtCore.QSettings.IniFormat)

        self.version = instanceSettings.value("IntendedVersion", "")
        if not self.version:
            raise MultiMCInstanceError("Instance %s has no IntendedVersion" % os.path.basename(path))

        self.name = instanceSettings.value("name", "(unnamed)")
        self.install = install
        self.saveFileDir = os.path.join(path, "minecraft", "saves")
        self.modsDir = os.path.join(path, "minecraft", "mods")

    @property
    def versions(self):
        return [self.version]

    def getVersionJarPath(self):
        return self.install.getVersionJarPath(self.version)

    def getResourceLoader(self, resourcePack=None):
        assetsVersion = self.install.installGroup.findVersionWithAssets()
        loader = ResourceLoader(assetsVersion)
        if resourcePack:
            loader.addZipFile(resourcePack)
        loader.addZipFile(self.getVersionJarPath())
        major, minor, rev = splitVersion(self.version)

        # Need assetsVersion for multipart block models
        if (major, minor) != (self.install.installGroup._requiredMajorMinor):
            loader.addZipFile(assetsVersion)

        loader.addModsFolder(self.modsDir)

        info = ["%s (%s)" % (z.filename, md5hash(z.filename)) for z in loader.zipFiles]
        log.info("Created ResourceLoader with search path:\n%s", ",\n".join(info))
        return loader


class MultiMCInstall(object):
    def __init__(self, installGroup, configPath):
        self.installGroup = installGroup
        self.configPath = configPath
        if not os.path.exists(configPath):
            raise MultiMCInstallError("Config file does not exist", configPath)

        # MultiMC is built with Qt, so why not use Qt's settings loader?
        mmcSettings = QtCore.QSettings(configPath, QtCore.QSettings.IniFormat)
        instanceDir = mmcSettings.value("InstanceDir", "")
        if not instanceDir:
            raise MultiMCInstallError("InstanceDir not set")

        self.mmcDir = os.path.dirname(configPath)
        if not os.path.isabs(instanceDir):
            self.instanceDir = os.path.join(self.mmcDir, instanceDir)
        else:
            self.instanceDir = instanceDir

        self.versionsDir = os.path.join(self.mmcDir, "versions")

        self.name = os.path.basename(self.mmcDir)
        # read versions.dat? (qt binary json format)
        # read groups from instGroups.json?

    @property
    def instances(self):
        for filename in os.listdir(self.instanceDir):
            path = os.path.join(self.instanceDir, filename)
            if not os.path.isdir(path):
                continue

            try:
                instance = MMCInstance(self, path)
            except MultiMCInstanceError:
                log.error("Could not read MultiMC Instance")
                continue

            yield instance

    def getJsonSettingValue(self):
        return {"configFile": self.configPath}

    def getVersionJarPath(self, version):
        return os.path.join(self.versionsDir, version, version + ".jar")

    @property
    def versions(self):
        for version in os.listdir(self.versionsDir):
            if os.path.exists(os.path.join(self.versionsDir, version, version + ".jar")):
                yield version

def splitVersion(version):
    """
    Split a Minecraft version ID into major, minor, and revision. If the version could not be parsed, return (0, 0, "")
    The revision is returned with the leading period. For example, if "1.8.1-pre3" is passed, (1, 8, ".1-pre3") will
    be returned.

    :param version:
    :type version: unicode
    :return: major, minor, revision
    :rtype: int, int, unicode
    """
    try:
        match = re.search(r"(\d+)\.(\d+)(.*)", version)
        if match is None:
            return 0, 0, ""
        groups = match.groups()
        if len(groups) < 2:
            return 0, 0, ""
        if len(groups) < 3:
            return int(groups[0]), int(groups[1]), ""
        return int(groups[0]), int(groups[1]), groups[2]
    except ValueError:
        return 0, 0, ""

def usableVersion(version):
    """
    Return True if the version has unstitched texture data in assets/minecraft/textures/blocks (v1.6+)
    (earlier versions have unstitched textures elsewhere, so don't bother with them)
    :param version:
    :type version:
    :return:
    :rtype:
    """
    major, minor, rev = splitVersion(version)
    if major < 1:
        return False
    if minor < 6:
        return False
    return True


class MCInstallError(ValueError):
    """
    Raised for invalid or unusable Minecraft installs.
    """


class MultiMCInstallError(ValueError):
    """
    Raised for invalid or unreadable MultiMC installs.
    """


class MultiMCInstanceError(ValueError):
    """
    Raised for invalid or unreadable MultiMC instances.
    """


class NameItem(QtWidgets.QTableWidgetItem):
    def setData(self, data, role):
        if role != Qt.EditRole or data != "(Default)":
            super(NameItem, self).setData(data, role)


class PathItem(QtWidgets.QTableWidgetItem):
    def setData(self, data, role):
        if role != Qt.EditRole or os.path.exists(data):
            super(PathItem, self).setData(data, role)


class MinecraftInstallsDialog(QtWidgets.QDialog, Ui_installsWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setupUi(self)


        # populate list view
        path = currentInstallOption.value()
        for i, install in enumerate(GetInstalls().installs):
            self._addInstall(install)
            if path == install.path:
                self._hiliteRow(i)

        for path in GetInstalls().mmcInstalls:
            self._addMMCInstall(path)

        self.minecraftInstallsTable.cellChanged.connect(self.itemChanged)
        self.minecraftInstallsTable.itemSelectionChanged.connect(self.itemSelectionChanged)

        self.addButton.clicked.connect(self.addInstall)
        self.removeButton.clicked.connect(self.removeInstall)
        self.selectButton.clicked.connect(self.selectInstall)
        self.okButton.clicked.connect(self.ok)

        self.snapshotsCheckBox.setChecked(allowSnapshotsOption.value())
        self.snapshotsCheckBox.toggled.connect(allowSnapshotsOption.setValue)
        allowSnapshotsOption.valueChanged.connect(self.allowSnapshotsChanged)

        self.addMMCButton.clicked.connect(self.addMMCInstall)
        self.removeMMCButton.clicked.connect(self.removeMMCInstall)

    def itemSelectionChanged(self):
        row = self.minecraftInstallsTable.currentRow()
        enabled = row != -1
        self.removeButton.setEnabled(enabled)
        self.selectButton.setEnabled(enabled)

    def itemChanged(self, row, column):
        install = GetInstalls().installs[row]
        text = self.minecraftInstallsTable.item(row, column).text()
        if column == 0:
            install.name = text
        if column == 2:
            install.path = text  # xxxx validate me!

    def allowSnapshotsChanged(self):
        installs = GetInstalls().installs
        for row in range(self.minecraftInstallsTable.rowCount()):
            install = installs[row]
            versionsString = ", ".join(sorted(install.versions, reverse=True))
            versionsItem = self.minecraftInstallsTable.item(row, 1)
            versionsItem.setText(versionsString)

    def _addInstall(self, install):
        minecraftInstallsTable = self.minecraftInstallsTable
        row = minecraftInstallsTable.rowCount()
        minecraftInstallsTable.setRowCount(row+1)
        nameItem = NameItem(install.name)
        if install.name == "(Default)":
            nameItem.setFlags(nameItem.flags() & ~Qt.ItemIsEditable)
        minecraftInstallsTable.setItem(row, 0, nameItem)

        versionsString = ", ".join(sorted(install.versions, reverse=True))
        versionsItem = QtWidgets.QTableWidgetItem(versionsString)
        versionsItem.setFlags(versionsItem.flags() & ~Qt.ItemIsEditable)
        self.minecraftInstallsTable.setItem(row, 1, versionsItem)

        pathItem = PathItem(install.path)
        if install.name == "(Default)":
            pathItem.setFlags(pathItem.flags() & ~Qt.ItemIsEditable)
        minecraftInstallsTable.setItem(row, 2, pathItem)
        self._hiliteRow(row)
        currentInstallOption.setValue(install.path)

    def _addMMCInstall(self, install):
        mmcTable = self.multiMCTable
        row = mmcTable.rowCount()
        mmcTable.setRowCount(row + 1)
        nameItem = NameItem(install.name)
        nameItem.setFlags(nameItem.flags() & ~Qt.ItemIsEditable)
        mmcTable.setItem(row, 0, nameItem)

        instancesString = ", ".join(sorted((i.name for i in install.instances)))
        instancesItem = QtWidgets.QTableWidgetItem(instancesString)
        instancesItem.setFlags(instancesItem.flags() & ~Qt.ItemIsEditable)
        mmcTable.setItem(row, 1, instancesItem)

        pathItem = PathItem(install.configPath)
        mmcTable.setItem(row, 2, pathItem)

    def _hiliteRow(self, hiliteRow):
        for row in range(self.minecraftInstallsTable.rowCount()):
            for column in range(self.minecraftInstallsTable.columnCount()):
                item = self.minecraftInstallsTable.item(row, column)
                font = item.font()
                font.setBold(row == hiliteRow)
                item.setFont(font)

    def addInstall(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, self.tr("Choose a Minecraft installation folder (.minecraft)"))
        installs = GetInstalls()
        if not folder:
            return
        try:
            install = MCInstall(installs, folder)
            install.checkUsable()
        except MCInstallError as e:
            message = self.tr("This Minecraft install is unusable.\n(%s)") % e
            QtWidgets.QMessageBox.warning(self, self.tr("Minecraft Install Unusable"), message)
        else:
            installs.addInstall(install)
            self._addInstall(install)

    def removeInstall(self):
        row = self.minecraftInstallsTable.currentRow()
        item = self.minecraftInstallsTable.item(row, 2)
        if item is None: return

        path = item.data(Qt.EditRole)
        GetInstalls().removeInstall(path)
        self.minecraftInstallsTable.removeRow(row)

    def selectInstall(self):
        row = self.minecraftInstallsTable.currentRow()
        item = self.minecraftInstallsTable.item(row, 2)
        if item is None: return

        path = item.data(Qt.EditRole)
        currentInstallOption.setValue(path)
        self._hiliteRow(row)

    def addMMCInstall(self):
        result = QtWidgets.QFileDialog.getOpenFileName(self,
                                                   self.tr("Choose a MultiMC configuration file (multimc.cfg)"),
                                                   filter=self.tr("MultiMC configuration files (multimc.cfg)"))
        installs = GetInstalls()
        if not result:
            return
        configPath = result[0]
        if not configPath:
            return

        try:
            install = MultiMCInstall(installs, configPath)
        except MultiMCInstallError as e:
            message = self.tr("This MultiMC install is unusable.\n(%s)") % e
            QtWidgets.QMessageBox.warning(self, self.tr("MultiMC Install Unusable"), message)
        else:
            installs.addMMCInstall(install)
            self._addMMCInstall(install)

    def removeMMCInstall(self):
        row = self.multiMCTable.currentRow()
        GetInstalls().removeMMCInstall(row)
        self.multiMCTable.removeRow(row)

    def ok(self):
        # Whether MCEdit has a version of the game it can use is checked by whoever opened this
        # dialog (MCInstallGroup.ensureValidInstall at startup), which can also offer to download one.
        self.accept()
