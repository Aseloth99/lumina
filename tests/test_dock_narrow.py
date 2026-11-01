"""Dar pencerede alt kontrol çubuğu pencereden taşmaz: sığmayan dosya şeridi geçici gizlenir, genişleyince geri gelir."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


class DockNarrowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._tmp.name)
        cls.app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.resize(1100, 600)
        cls.win.show()

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        cls.win.deleteLater()
        cls._tmp.cleanup()

    def test_strip_hides_when_it_cannot_fit_and_returns_when_there_is_room(self):
        dock = self.win.bottom_dock
        dock._split = True
        dock.update_dock_geometry(640)  # asgari pencere genişliği
        self.assertTrue(dock.strip_host.isHidden(), "dar pencerede şerit gizlenmeli")
        self.assertLessEqual(dock.minimumWidth(), 640 - 32, "dock pencereden geniş olmamalı")
        dock.update_dock_geometry(1400)
        self.assertFalse(dock.strip_host.isHidden(), "genişleyince şerit geri gelmeli")


if __name__ == "__main__":
    unittest.main()
