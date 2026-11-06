"""
    language

    Choose the interface language and install its translations.

    MCEdit's own strings are in mcedit2/i18n/<lang>.qm, made from <lang>.ts by
    scripts/update_translations.py. Qt's standard buttons and dialogs (OK, Cancel, the file
    dialog) are translated by Qt's qtbase_<lang>.qm, which comes with PySide6.

    The language is read once at startup: widgets are built with their text, so a change is
    applied the next time MCEdit starts.
"""
import logging
import os

from PySide6 import QtCore

from mcedit2.util.resources import resourcePath
from mcedit2.util.settings import Settings

log = logging.getLogger(__name__)

# An empty value means "use the system's language".
LangSetting = Settings().getOption("app_language", str, "")

# Names shown in the language menu, in their own language.
LANGUAGE_NAMES = {
    "en": "English",
    "zh_CN": "简体中文",
}

# Locale names for arrow's humanize(), which writes "2 hours ago" in the world list.
ARROW_LOCALES = {
    "en": "en_us",
    "zh_CN": "zh_cn",
}

_currentLanguage = "en"
_translators = []


def translationsDir():
    return resourcePath("mcedit2/i18n")


def availableLanguages():
    """
    The languages that have a .qm file, as (code, displayName), English first.
    """
    codes = [f[:-3] for f in os.listdir(translationsDir()) if f.endswith(".qm")]
    codes.sort(key=lambda c: (c != "en", c))
    return [(code, LANGUAGE_NAMES.get(code) or QtCore.QLocale(code).nativeLanguageName() or code)
            for code in codes]


def _match(lang, codes):
    """
    Find the code for `lang` among `codes`: an exact match, else the same language
    ("zh_TW" falls back to "zh_CN" rather than to English).
    """
    if not lang:
        return None
    if lang in codes:
        return lang
    base = lang.split("_")[0]
    for code in codes:
        if code.split("_")[0] == base:
            return code
    return None


def chooseLanguage():
    codes = [code for code, name in availableLanguages()]
    chosen = _match(LangSetting.value(), codes)
    if chosen is None:
        chosen = _match(QtCore.QLocale.system().name(), codes)
    return chosen or "en"


def currentLanguage():
    return _currentLanguage


def arrowLocale():
    return ARROW_LOCALES.get(_currentLanguage, "en_us")


def _qtTranslationsDirs():
    # PySide6 puts Qt's translations in PySide6/translations; a PyInstaller build copies them
    # there as well. QLibraryInfo knows where they are in either case.
    dirs = [QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.TranslationsPath)]
    try:
        import PySide6
        dirs.append(os.path.join(os.path.dirname(PySide6.__file__), "translations"))
    except Exception:
        pass
    return dirs


# These are shown in both languages, so whoever switched by mistake can find the way back.
MENU_TITLE = "Language / 语言"
SYSTEM_LABEL = "System language / 跟随系统"
RESTART_TEXT = ("The new language will be used when MCEdit is restarted.\n"
                "重新启动 MCEdit 后将使用新的语言。")


def languageChoices():
    """
    The choices for the language menu and preferences, as (settingValue, label).
    """
    return [("", SYSTEM_LABEL)] + availableLanguages()


def setLanguage(code, parent=None):
    """
    Save the chosen language. Tell the user to restart if that changes the language.
    """
    if code == LangSetting.value():
        return
    LangSetting.setValue(code)
    log.info("Language setting changed to %r", code)
    if chooseLanguage() != _currentLanguage:
        from PySide6 import QtWidgets
        QtWidgets.QMessageBox.information(parent, MENU_TITLE, RESTART_TEXT)


def installTranslators(app):
    """
    Install the translations for the chosen language on `app`. Return the language code.
    """
    global _currentLanguage
    for translator in _translators:
        app.removeTranslator(translator)
    del _translators[:]

    lang = chooseLanguage()
    _currentLanguage = lang

    translator = QtCore.QTranslator(app)
    if translator.load(os.path.join(translationsDir(), lang + ".qm")):
        app.installTranslator(translator)
        _translators.append(translator)
    else:
        log.warning("Could not load the translations for %s", lang)

    if lang != "en":
        qtTranslator = QtCore.QTranslator(app)
        for folder in _qtTranslationsDirs():
            if qtTranslator.load("qtbase_" + lang, folder):
                app.installTranslator(qtTranslator)
                _translators.append(qtTranslator)
                break
        else:
            log.info("Qt's own translations for %s were not found", lang)

    log.info("Interface language: %s (setting %r, system %s)", lang, LangSetting.value(),
             QtCore.QLocale.system().name())
    return lang
