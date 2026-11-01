"""Kalıcı silme: onay reddedilince oynatma/durum bozulmamalı; onaylanınca liste güncellenmeli.

Yalnızca geçici dizindeki kopyalar silinir; geri dönüşüm kutusuna dokunulmaz.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.ui.message_box import MessageBox  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from _synthetic import sample_jpg  # noqa: E402

SAMPLE = sample_jpg()


class DeleteFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._settings_tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._settings_tmp.name)
        cls.app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.deleteLater()
        cls._settings_tmp.cleanup()

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.a = pathlib.Path(self.dir.name) / "a.jpg"
        self.b = pathlib.Path(self.dir.name) / "b.jpg"
        shutil.copy(SAMPLE, self.a)
        shutil.copy(SAMPLE, self.b)
        w = self.win
        w.image_files = [self.a, self.b]
        w.current_idx = 0
        self.stops: list[bool] = []
        self.displays: list[int] = []
        w._stop_playback = lambda release=False: self.stops.append(release)  # type: ignore[method-assign]
        w.display_current = lambda: self.displays.append(1)  # type: ignore[method-assign]

    def tearDown(self) -> None:
        for name in ("_stop_playback", "display_current"):
            self.win.__dict__.pop(name, None)
        self.dir.cleanup()

    def test_declined_confirmation_keeps_playback_and_files(self) -> None:
        with mock.patch.object(MessageBox, "question", return_value=MessageBox.StandardButton.No):
            self.win.permanently_delete_current()
        self.assertEqual(self.stops, [], "onay reddedilince oynatma durdurulmamalı")
        self.assertTrue(self.a.exists() and self.b.exists())
        self.assertEqual(self.win.image_files, [self.a, self.b])

    def test_confirmed_deletes_file_and_updates_listing(self) -> None:
        with mock.patch.object(MessageBox, "question", return_value=MessageBox.StandardButton.Yes):
            self.win.permanently_delete_current()
        self.assertEqual(self.stops, [True])
        self.assertFalse(self.a.exists())
        self.assertTrue(self.b.exists())
        self.assertEqual(self.win.image_files, [self.b])
        self.assertEqual(self.win.current_idx, 0)
        self.assertEqual(self.displays, [1])

    def test_delete_targets_deduplicates(self) -> None:
        targets = self.win._delete_targets(self.a)
        self.assertEqual(targets, [self.a])


if __name__ == "__main__":
    unittest.main()
