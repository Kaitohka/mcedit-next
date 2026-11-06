"""
    general
"""

import logging
from PySide6 import QtWidgets

from mcedit2.util import language, theme
from mcedit2.widgets.layout import Column

log = logging.getLogger(__name__)


class GeneralPrefs(QtWidgets.QWidget):
    def __init__(self):
        super(GeneralPrefs, self).__init__()
        self.labelText = self.tr("General")

        layout = QtWidgets.QFormLayout()

        self.languageInput = QtWidgets.QComboBox()
        for code, label in language.languageChoices():
            self.languageInput.addItem(label, code)
        language.LangSetting.connectAndCall(self.showLanguage)
        self.languageInput.activated.connect(self.languageChosen)

        layout.addRow(language.MENU_TITLE, self.languageInput)

        self.themeInput = QtWidgets.QComboBox()
        for name, label in theme.themeChoices():
            self.themeInput.addItem(label, name)
        theme.ThemeSetting.connectAndCall(self.showTheme)
        self.themeInput.activated.connect(self.themeChosen)

        layout.addRow(self.tr("Theme"), self.themeInput)

        self.setLayout(Column(layout, None))

    def showLanguage(self, code):
        index = self.languageInput.findData(code or "")
        self.languageInput.setCurrentIndex(max(index, 0))

    def showTheme(self, name):
        index = self.themeInput.findData(name)
        self.themeInput.setCurrentIndex(max(index, 0))

    def themeChosen(self, index):
        theme.setTheme(self.themeInput.itemData(index))

    def languageChosen(self, index):
        language.setLanguage(self.languageInput.itemData(index) or "", self)
