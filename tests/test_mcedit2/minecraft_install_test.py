"""
    minecraft_install_test

    Finding a Minecraft client jar, and adding an install when MCEdit starts without one.

    Adding an install in the dialog used to leave MCEdit asking again until it was restarted: the
    dialog's OK checked only for Minecraft 1.12 and offered to quit, while startup also accepts 1.21.1.
"""
import json
import os
import zipfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from mcedit2.util import minecraftinstall


class FakeOption:
    """Stands in for a settings option, so the tests don't write the user's settings."""
    def __init__(self, value):
        self._value = value

    def value(self, default=None):
        return self._value

    def setValue(self, value):
        self._value = value


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def group(tmp_path, monkeypatch):
    monkeypatch.setattr(minecraftinstall, "installationsOption", FakeOption([]))
    monkeypatch.setattr(minecraftinstall, "multiMCInstallsOption", FakeOption([]))
    monkeypatch.setattr(minecraftinstall, "currentInstallOption", FakeOption(""))
    monkeypatch.setattr(minecraftinstall, "_versionCacheDir", str(tmp_path / "emptyCache"))
    monkeypatch.setattr(minecraftinstall.directories, "minecraftDir", str(tmp_path / "noDefault"))
    group = minecraftinstall.MCInstallGroup()
    monkeypatch.setattr(minecraftinstall, "_installs", group)
    return group


def makeInstall(folder, versionFolder, gameVersion):
    """A .minecraft folder with one version, whose jar says it is `gameVersion`."""
    jarDir = folder / "versions" / versionFolder
    jarDir.mkdir(parents=True)
    jar = jarDir / (versionFolder + ".jar")
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("version.json", json.dumps({"id": gameVersion}))
    return jar


def testNothingFound(group):
    assert group.installs == []
    assert not group.hasUsableAssets()


def testPlainVersionFolder(group, tmp_path):
    jar = makeInstall(tmp_path / "mc", "1.21.1", "1.21.1")
    group.addInstall(minecraftinstall.MCInstall(group, str(tmp_path / "mc")))
    assert group.findAssetsJar("1.21.1") == str(jar)
    assert group.hasUsableAssets()


def testLauncherVersionFolder(group, tmp_path):
    # PCL, HMCL and others keep each version apart under a name of its own.
    jar = makeInstall(tmp_path / "mc", "1.21.1-NeoForge_21.1.209", "1.21.1")
    group.addInstall(minecraftinstall.MCInstall(group, str(tmp_path / "mc")))
    assert group.findAssetsJar("1.21.1") == str(jar)


def testFolderNamedLikeTheVersionIsCheckedInside(group, tmp_path):
    makeInstall(tmp_path / "mc", "1.21.10", "1.21.10")
    group.addInstall(minecraftinstall.MCInstall(group, str(tmp_path / "mc")))
    assert group.findAssetsJar("1.21.1") is None


def testAddingAnInstallAtStartupNeedsNoRestart(app, group, tmp_path, monkeypatch):
    """The user's steps: Minecraft not found -> Configure Installs -> add a folder -> OK."""
    makeInstall(tmp_path / "mc", "1.21.1-NeoForge", "1.21.1")
    prompts = []

    class FakeMessageBox:
        AcceptRole = ActionRole = RejectRole = None

        def __init__(self, *args):
            self.buttons = []
            prompts.append(self)

        def setWindowTitle(self, text): pass
        def setText(self, text): pass

        def addButton(self, text, role):
            self.buttons.append(text)
            return text

        def exec(self): pass

        def clickedButton(self):
            assert len(prompts) == 1, "asked again after an install was added"
            return self.buttons[1]  # Configure Installs

    class FakeInstallsDialog(minecraftinstall.MinecraftInstallsDialog):
        def exec(self):
            installs = minecraftinstall.GetInstalls()
            installs.addInstall(minecraftinstall.MCInstall(installs, str(tmp_path / "mc")))
            self.ok()
            return 1

    monkeypatch.setattr(minecraftinstall.QtWidgets, "QMessageBox", FakeMessageBox)
    monkeypatch.setattr(minecraftinstall, "MinecraftInstallsDialog", FakeInstallsDialog)
    group.ensureValidInstall()
    assert len(prompts) == 1
    assert group.hasUsableAssets()


def testOkClosesTheDialogWithNothingUsable(app, group, monkeypatch):
    def noMessages(*args, **kwargs):
        raise AssertionError("the dialog showed a message box")
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", noMessages)
    dialog = minecraftinstall.MinecraftInstallsDialog()
    dialog.show()
    dialog.ok()
    assert not dialog.isVisible()
    assert dialog.result() == QtWidgets.QDialog.Accepted
