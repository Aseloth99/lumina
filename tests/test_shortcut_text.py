"""Menülerdeki kısayol metni Ctrl+Shift+N biçiminde kalmalı ("Kontrol+Üst Karakter+N" olmamalı)."""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtGui import QKeySequence  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


class ShortcutTextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _text(self, seq: str) -> str:
        return QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText)

    @unittest.skipIf(sys.platform == "darwin", "macOS simge kullanır")
    def test_turkish_ui_keeps_english_key_names(self) -> None:
        from app.resources.locales import install_qt_translations
        install_qt_translations("tr")
        self.assertEqual(self._text("Ctrl+Shift+N"), "Ctrl+Shift+N")
        self.assertEqual(self._text("Ctrl+Q"), "Ctrl+Q")
        self.assertEqual(self._text("Alt+F4"), "Alt+F4")

    @unittest.skipIf(sys.platform == "darwin", "macOS simge kullanır")
    def test_switching_language_does_not_stack_translators(self) -> None:
        from app.resources import locales
        for lang in ("tr", "en", "tr"):
            locales.install_qt_translations(lang)
        self.assertLessEqual(len(locales._qt_translators), 2)
        self.assertEqual(self._text("Ctrl+Shift+N"), "Ctrl+Shift+N")


if __name__ == "__main__":
    unittest.main()
