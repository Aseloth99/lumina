"""Performans korumaları: event filter hızlı çıkışı, taşırken yeniden yerleşim yok, önbellek bayt sınırı."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QEvent, QPoint, QSettings  # noqa: E402
from PyQt6.QtGui import QImage, QMoveEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


class EventFilterAndMoveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._settings_tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._settings_tmp.name)
        cls.app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.resize(900, 600)
        cls.win.show()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.close()
        cls.win.deleteLater()
        cls._settings_tmp.cleanup()

    def test_irrelevant_events_skip_expensive_checks(self) -> None:
        """Paint/Timer/UpdateRequest gibi olaylar pahalı kontrol zincirine girmemeli."""
        with mock.patch.object(QApplication, "activePopupWidget") as popup:
            for t in (QEvent.Type.Timer, QEvent.Type.UpdateRequest, QEvent.Type.Paint, QEvent.Type.LayoutRequest):
                self.assertFalse(self.win.eventFilter(self.win, QEvent(t)))
            popup.assert_not_called()

    def test_mouse_events_still_take_the_full_path(self) -> None:
        with mock.patch.object(QApplication, "activePopupWidget", return_value=None) as popup:
            self.win.eventFilter(self.win, QEvent(QEvent.Type.MouseMove))
            self.assertTrue(popup.called, "fare olayları pencere taşıma/boyutlandırma yoluna girmeli")

    def test_frameless_filter_fast_path_returns_false(self) -> None:
        from app.ui.frameless import FramelessWindow

        with mock.patch.object(QApplication, "activePopupWidget") as popup:
            self.assertFalse(FramelessWindow.eventFilter(self.win, self.win, QEvent(QEvent.Type.Timer)))
            popup.assert_not_called()

    def test_move_does_not_relayout_whole_shell(self) -> None:
        w = self.win
        with mock.patch.object(w, "_relayout_shell") as relayout, mock.patch.object(w, "_place_busy_chip") as busy:
            w.moveEvent(QMoveEvent(QPoint(120, 90), QPoint(100, 80)))
            relayout.assert_not_called()
            busy.assert_called_once()

    def test_resize_still_relayouts(self) -> None:
        w = self.win
        with mock.patch.object(w, "_relayout_shell") as relayout:
            w.resize(w.width() + 13, w.height() + 7)
            QApplication.processEvents()
            self.assertTrue(relayout.called)


class ImageCacheBytesTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.core import image_loader

        self.il = image_loader
        self.il.clear_image_cache()
        self.addCleanup(self.il.clear_image_cache)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _file(self, name: str) -> pathlib.Path:
        p = pathlib.Path(self._tmp.name) / name
        p.write_bytes(b"x")
        return p

    def test_byte_cap_evicts_lru_but_keeps_latest(self) -> None:
        img = QImage(4000, 3000, QImage.Format.Format_RGB888)  # ~36 MB
        per = img.sizeInBytes()
        with mock.patch.object(self.il, "_MAX_CACHE_BYTES", per * 3 + 1):
            paths = [self._file(f"{i}.jpg") for i in range(6)]
            for p in paths:
                self.il.put_cached_image(p, QImage(img), 4000, 3000)
            self.assertIsNone(self.il.get_cached_image(paths[0]), "en eski öğe bayt sınırı yüzünden atılmalı")
            self.assertIsNotNone(self.il.get_cached_image(paths[-1]), "son eklenen korunmalı")
            self.assertLessEqual(len(self.il._image_cache), 3)

    def test_single_oversized_image_is_still_cached(self) -> None:
        big = QImage(2000, 2000, QImage.Format.Format_RGB888)
        with mock.patch.object(self.il, "_MAX_CACHE_BYTES", 1000):
            p = self._file("big.jpg")
            self.il.put_cached_image(p, big, 2000, 2000)
            self.assertIsNotNone(self.il.get_cached_image(p), "tek dev görsel bile gösterilebilmeli")


if __name__ == "__main__":
    unittest.main()
