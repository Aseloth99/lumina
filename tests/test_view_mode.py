"""Sığdır / Doldur seçimi hatırlanır: başka bir dosyaya geçince ve uygulama yeniden açılınca sıfırlanmaz.

Kullanıcı Doldur'a geçip başka bir dosyaya gidince görünüm Sığdır'a dönüyordu (her `display_current` kipi sıfırlıyordu).
Yakınlaştırma ve 1:1 yine dosya başına sıfırlanır; yalnızca Sığdır/Doldur seçimi kalıcıdır.
"""

from __future__ import annotations

import inspect
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from _synthetic import sample_jpg  # noqa: E402

SAMPLE = sample_jpg()


class ViewModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._settings_tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._settings_tmp.name)
        cls.app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow

        cls.MainWindow = MainWindow
        cls.win = MainWindow()
        cls.dir = tempfile.TemporaryDirectory()
        cls.files = []
        for name in ("a.jpg", "b.jpg"):
            target = pathlib.Path(cls.dir.name) / name
            shutil.copy(SAMPLE, target)
            cls.files.append(target)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.pool.waitForDone(5000)
        cls.app.processEvents()
        cls.win.deleteLater()
        cls.app.processEvents()
        cls.dir.cleanup()
        cls._settings_tmp.cleanup()

    def _show(self, index: int) -> None:
        w = self.win
        w.image_files = list(self.files)
        w.current_idx = index
        w.display_current()
        w.pool.waitForDone(5000)
        self.app.processEvents()

    def test_fill_stays_on_when_the_next_file_is_shown(self) -> None:
        w = self.win
        w._on_fill_clicked()
        self.assertTrue(w._view_fill)
        self._show(0)
        self.assertTrue(w.canvas._fill, "ilk dosyada Doldur")
        self.assertEqual(w.btn_fill.property("active"), "true")
        self.assertEqual(w.btn_fit.property("active"), "false")
        self._show(1)
        self.assertTrue(w.canvas._fill, "başka dosyaya geçince Doldur sıfırlanmamalı")
        self.assertEqual(w.btn_fill.property("active"), "true")

    def test_fit_is_remembered_too(self) -> None:
        w = self.win
        w._on_fill_clicked()
        self._show(0)
        w._on_fit_clicked()
        self.assertFalse(w._view_fill)
        self._show(1)
        self.assertFalse(w.canvas._fill)
        self.assertEqual(w.btn_fit.property("active"), "true")
        self.assertEqual(w.btn_fill.property("active"), "false")

    def test_zoom_and_actual_size_are_still_per_file(self) -> None:
        w = self.win
        w._on_fill_clicked()
        self._show(0)
        w._on_actual_size_clicked()  # 1:1 yalnızca bu dosya için
        self.assertFalse(w.canvas._fitted)
        self._show(1)
        self.assertTrue(w.canvas._fitted, "yeni dosya yeniden sığdırılır/doldurulur (1:1 hatırlanmaz)")
        self.assertTrue(w.canvas._fill, "ama seçilen Doldur kipi korunur")

    def test_the_choice_is_saved_and_a_new_window_starts_with_it(self) -> None:
        w = self.win
        w._on_fill_clicked()
        self.assertTrue(w._setting_bool("view_fill"))
        second = self.MainWindow()
        self.addCleanup(second.deleteLater)
        self.assertTrue(second._view_fill, "yeniden açılışta son kip: Doldur")
        self.assertTrue(second.canvas._fill)
        w._on_fit_clicked()
        self.assertFalse(w._setting_bool("view_fill"))
        third = self.MainWindow()
        self.addCleanup(third.deleteLater)
        self.assertFalse(third._view_fill)
        self.assertFalse(third.canvas._fill)

    def test_fill_preference_survives_the_video_surface_reset(self) -> None:
        from app.ui.player import LivePlayer, VideoSurfaceFallback

        surface = VideoSurfaceFallback()
        self.addCleanup(surface.deleteLater)
        surface.remember_cover(True)
        surface.reset_view()  # video gerçekten başlayınca yapılan sıfırlama Doldur'u siler...
        self.assertFalse(surface._cover)
        surface.restore_cover_preference()  # ...ve tercih geri uygulanır
        self.assertTrue(surface._cover)
        surface.remember_cover(False)
        surface.reset_view()
        surface.restore_cover_preference()
        self.assertFalse(surface._cover)
        # libmpv yolu (gerçek pencere gerekir): başlangıç işleyicisi tercihi gerçekten çağırıyor mu?
        self.assertIn("restore_cover_preference()", inspect.getsource(LivePlayer))


if __name__ == "__main__":
    unittest.main()
