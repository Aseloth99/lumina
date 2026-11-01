"""Qt'nin kendi kutulari (Tamam/Iptal/Evet/Hayir) uygulama diliyle gelmeli (Turkce arayuzde 'OK' kalmasin)."""

from __future__ import annotations

import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit, QMessageBox

from app.resources.locales import install_qt_translations


def _button_texts() -> list[str]:
    box = QMessageBox(
        QMessageBox.Icon.Question, "t", "x",
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        | QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    texts = [b.text() for b in box.buttons()]
    box.deleteLater()
    return texts


class QtTranslationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        install_qt_translations("en")

    def test_turkish_buttons_and_context_menu(self):
        install_qt_translations("tr")
        texts = _button_texts()
        self.assertIn("Tamam", texts)
        self.assertIn("&Evet", texts)
        edit = QLineEdit()
        menu = edit.createStandardContextMenu()
        labels = " ".join(a.text() for a in menu.actions())
        self.assertIn("Kopyala", labels.replace("&", ""))

    def test_switching_back_to_english_replaces_the_translator(self):
        install_qt_translations("tr")
        install_qt_translations("en")
        self.assertIn("OK", _button_texts())

    def test_repeated_calls_do_not_stack_translators(self):
        from app.resources.locales import _qt_translators

        for _ in range(3):
            install_qt_translations("tr")
        self.assertLessEqual(len(_qt_translators), 2)  # qtbase + kısayol adı çevirisi


if __name__ == "__main__":
    unittest.main()
