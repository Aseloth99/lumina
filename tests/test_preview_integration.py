"""MainWindow + önizleme entegrasyonu (offscreen, gerçek sentetik video).

* Hover denetleyiciyi besler ve kare gösterir.
* Geçici (gömülü Live Photo) video gezinmede SİLİNEBİLİR: önizleme capture'ı önce bırakılır
  (oynatıcıyı durdur, kaynağı bırak, sonra sil).
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # tests/ (_media)

from PyQt6.QtCore import QCoreApplication, QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from _media import DURATION_MS, make_ramp_video  # noqa: E402


class PreviewIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._settings_tmp = tempfile.TemporaryDirectory()
        cls._media_tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._settings_tmp.name)
        cls.app = QApplication.instance() or QApplication([])
        cls.video = pathlib.Path(cls._media_tmp.name) / "clip.mp4"
        try:
            ok = make_ramp_video(cls.video)
        except Exception as exc:
            raise unittest.SkipTest(f"video üretilemedi: {exc}")
        if not ok:
            raise unittest.SkipTest("bu ortamda mp4v kodlayıcı yok")
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.resize(1000, 700)
        cls.win.show()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.close()
        cls.win.deleteLater()
        cls._settings_tmp.cleanup()
        cls._media_tmp.cleanup()

    @staticmethod
    def _wait(predicate, timeout=6.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QCoreApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        QCoreApplication.processEvents()
        return predicate()

    def test_hover_drives_controller_and_shows_frame(self) -> None:
        from app.core.matcher import LiveMatch

        w = self.win
        w.current_match = LiveMatch(str(self.video), False, "video")
        w.seek_bar.setRange(0, DURATION_MS)
        w._on_seek_hover(0.5)
        self.assertEqual(w._preview.path, str(self.video))
        self.assertTrue(self._wait(lambda: len(w._preview._store) >= 8))
        w._on_seek_hover(0.5)
        pix = w.seek_preview_image.pixmap()
        self.assertTrue(pix is not None and not pix.isNull(), "hover'da kare gösterilmeli")
        self.assertEqual(w.seek_preview_time.text(), "0:05")
        w._hide_seek_preview()
        self.assertFalse(w.seek_preview.isVisible())

    def test_temp_video_is_deletable_after_navigation_release(self) -> None:
        from app.core.matcher import LiveMatch

        w = self.win
        temp = pathlib.Path(self._media_tmp.name) / "embedded_live.mp4"
        shutil.copy(self.video, temp)
        w.temps.add(str(temp))
        w.current_match = LiveMatch(str(temp), True, "embedded")
        w.seek_bar.setRange(0, DURATION_MS)
        w._on_seek_hover(0.3)
        self.assertTrue(self._wait(lambda: len(w._preview._store) >= 2))
        # Gezinme: display_current'ın ilk işi
        w._stop_playback(release=True)
        self.assertFalse(temp.exists(), "geçici video silinmeli (capture açık kalmamalı)")
        self.assertEqual(w.temps.paths(), ())
        self.assertIsNone(w._preview.path)


if __name__ == "__main__":
    unittest.main()
