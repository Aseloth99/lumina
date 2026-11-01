"""İlk açılışta tema "Sistem" gelmeli (Windows'un açık/koyu ayarını izler); kullanıcı seçimi korunmalı."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


class DefaultThemeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._tmp.name)
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _theme_of_new_window(self) -> str:
        from app.ui.main_window import MainWindow

        win = MainWindow()
        self.addCleanup(win.deleteLater)
        return win.theme

    def test_first_run_follows_the_system(self) -> None:
        from app.core.settings_store import make_settings

        make_settings().remove("theme")
        self.assertEqual(self._theme_of_new_window(), "system")

    def test_garbage_value_falls_back_to_system(self) -> None:
        from app.core.settings_store import make_settings

        make_settings().setValue("theme", "bozuk")
        self.assertEqual(self._theme_of_new_window(), "system")

    def test_explicit_user_choice_is_kept(self) -> None:
        from app.core.settings_store import make_settings

        for choice in ("dark", "light"):
            make_settings().setValue("theme", choice)
            self.assertEqual(self._theme_of_new_window(), choice)


if __name__ == "__main__":
    unittest.main()
