"""
    language_test

    The interface language: choosing it, loading its translations, and the Chinese translation file.
"""
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from mcedit2.util import language

I18N = Path(language.translationsDir())
PLACEHOLDER = re.compile(r"%\(\w+\)[sd]|%[sd]|\{\d*\}|</?b>")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def setting(monkeypatch):
    """Set the language setting for one test without writing the user's settings file."""
    value = {"lang": ""}
    monkeypatch.setattr(language.LangSetting, "value", lambda default=None: value["lang"])
    return value


def tsMessages():
    tree = ET.parse(I18N / "zh_CN.ts")
    for ctx in tree.getroot().iter("context"):
        for m in ctx.iter("message"):
            tr = m.find("translation")
            if tr.get("type") not in ("vanished", "obsolete", "unfinished") and tr.text:
                yield ctx.findtext("name"), m.findtext("source"), tr.text


def testChineseIsAvailable():
    codes = dict(language.availableLanguages())
    assert codes["en"] == "English"
    assert codes["zh_CN"] == "简体中文"
    assert language.languageChoices()[0][0] == ""


@pytest.mark.parametrize("asked, expected", [
    ("zh_CN", "zh_CN"), ("zh_TW", "zh_CN"), ("zh", "zh_CN"), ("en_US", "en"), ("fr_FR", None), ("", None)])
def testMatch(asked, expected):
    assert language._match(asked, ["en", "zh_CN"]) == expected


def testSettingWinsOverSystem(setting, monkeypatch):
    monkeypatch.setattr(QtCore.QLocale, "system", staticmethod(lambda: QtCore.QLocale("zh_CN")))
    setting["lang"] = "en"
    assert language.chooseLanguage() == "en"
    setting["lang"] = ""
    assert language.chooseLanguage() == "zh_CN"
    monkeypatch.setattr(QtCore.QLocale, "system", staticmethod(lambda: QtCore.QLocale("fr_FR")))
    assert language.chooseLanguage() == "en"


def testChineseTranslationsInstall(app, setting):
    setting["lang"] = "zh_CN"
    try:
        assert language.installTranslators(app) == "zh_CN"
        translate = QtCore.QCoreApplication.translate
        assert translate("EditorTool", "Clone") == "克隆"
        assert translate("mainWindow", "Open World...") == "打开世界..."
        assert translate("ViewAction", "Zoom In") == "放大"
        # Qt's own strings, from qtbase_zh_CN.qm
        assert translate("QPlatformTheme", "Cancel") == "取消"
        assert language.arrowLocale() == "zh_cn"
    finally:
        setting["lang"] = "en"
        language.installTranslators(app)
    assert QtCore.QCoreApplication.translate("EditorTool", "Clone") == "Clone"


def testToolNamesAreTranslatable(app, setting):
    from mcedit2.editortools import toolDisplayName
    from mcedit2.editortools.clone import CloneTool
    assert CloneTool.name == "Clone"  # the ID stays English
    setting["lang"] = "zh_CN"
    try:
        language.installTranslators(app)
        assert toolDisplayName(CloneTool.name) == "克隆"
    finally:
        setting["lang"] = "en"
        language.installTranslators(app)


def testTranslationsKeepPlaceholders():
    bad = []
    for ctx, src, text in tsMessages():
        if sorted(PLACEHOLDER.findall(src)) != sorted(PLACEHOLDER.findall(text)):
            bad.append((ctx, src, text))
    assert bad == []


def testQmMatchesTs(app):
    """The .qm that MCEdit loads has every translation in the .ts (run scripts/update_translations.py)."""
    translator = QtCore.QTranslator()
    assert translator.load(str(I18N / "zh_CN.qm"))
    stale = [(ctx, src) for ctx, src, text in tsMessages() if translator.translate(ctx, src) != text]
    assert stale == []
