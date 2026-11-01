"""Menü simgeleri renkli: her menü simgesi çizilir (32 ve 48 px), renklidir (gri değil); … menüsünde
etkin her satırın simgesi vardır (eskiden "Hakkında" metinde ℹ karakteriydi, "Kaldığım yerden devam et" ve
"Güncellemeleri kontrol et" simgesizdi)."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QCoreApplication, QSettings, QSize  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


def _saturated_pixels(icon, size: int = 32) -> int:
    img = icon.pixmap(QSize(size, size)).toImage()
    count = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = QColor(img.pixel(x, y))
            if img.pixelColor(x, y).alpha() > 128 and c.hsvSaturation() > 80:
                count += 1
    return count


class MenuIconTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._ini = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._ini.name)
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._ini.cleanup()

    def test_every_menu_icon_is_drawn_and_colored(self) -> None:
        from app.ui.icons import MENU_ICON_NAMES, menu_icon

        self.assertGreaterEqual(len(MENU_ICON_NAMES), 20)
        for name in sorted(MENU_ICON_NAMES):
            icon = menu_icon(name)
            self.assertFalse(icon.isNull(), name)
            self.assertGreater(_saturated_pixels(icon), 3, f"{name} renkli olmalı")
            self.assertIs(menu_icon(name), icon, "önbellekten gelmeli")

    def test_theme_icons_are_visible_on_dark_menus(self) -> None:
        """Eski koyu tema simgesi lacivert bir daireydi: koyu menüde görünmüyordu."""
        from app.ui.icons import theme_dark_icon, theme_system_icon

        for icon in (theme_dark_icon(), theme_system_icon()):
            img = icon.pixmap(QSize(32, 32)).toImage()
            bright = sum(
                1 for y in range(32) for x in range(32)
                if img.pixelColor(x, y).alpha() > 128 and img.pixelColor(x, y).lightness() > 110
            )
            self.assertGreater(bright, 20)

    def test_settings_menu_rows_all_have_icons(self) -> None:
        from app.ui import main_window as mw

        captured = []

        class Spy(mw.QMenu):
            def exec(self, *_a, **_k):  # noqa: A003 — QMenu.exec
                captured.append(self)

        win = mw.MainWindow()
        self.addCleanup(win.deleteLater)
        with mock.patch.object(mw, "QMenu", Spy):
            win.show_settings_menu()
        QCoreApplication.processEvents()
        self.assertEqual(len(captured), 1)
        missing = [
            a.text() for a in captured[0].actions()
            if not a.isSeparator() and a.isEnabled() and a.icon().isNull()
        ]
        self.assertEqual(missing, [], "etkin her satırın simgesi olmalı")
        texts = [a.text() for a in captured[0].actions()]
        self.assertFalse(any(t.startswith("ℹ") for t in texts), "Hakkında artık simgeli")


if __name__ == "__main__":
    unittest.main()
