"""Live Photo çözümleme arka planda: GUI'yi bloklamaz, bayat sonuç uygulanmaz, temp temizlenir."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # tests/ (_media)

from PyQt6.QtCore import QCoreApplication, QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from _media import make_ramp_video  # noqa: E402
from test_live_resolver import _jpeg_bytes  # noqa: E402


class LiveAsyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._settings_tmp = tempfile.TemporaryDirectory()
        cls._media_tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._settings_tmp.name)
        cls.app = QApplication.instance() or QApplication([])
        media = pathlib.Path(cls._media_tmp.name)
        video = media / "clip.mp4"
        if not make_ramp_video(video):
            raise unittest.SkipTest("bu ortamda mp4v kodlayıcı yok")
        jpeg = _jpeg_bytes()
        cls.live = media / "live.jpg"
        cls.live.write_bytes(jpeg + b"MotionPhoto_Data" + video.read_bytes())  # Samsung tarzı
        cls.plain = media / "plain.jpg"
        cls.plain.write_bytes(jpeg)
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.resize(900, 600)
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

    def _show(self, path: pathlib.Path) -> None:
        w = self.win
        w.image_files = [path]
        w.current_idx = 0
        w._autoplay_armed = False
        w.display_current()

    def test_match_resolves_in_background_and_temp_is_cleaned(self) -> None:
        w = self.win
        self._show(self.live)
        self.assertTrue(self._wait(lambda: w.current_match is not None), "LIVE eşleşmesi gelmedi")
        self.assertTrue(w.current_match.is_temp)
        self.assertEqual(w.current_match.source, "samsung")
        temp = pathlib.Path(w.current_match.video_path)
        self.assertTrue(temp.is_file())
        # Gezinme: önceki geçici video silinmeli
        w._stop_playback(release=True)
        self.assertFalse(temp.exists())
        self.assertIsNone(w.current_match)

    def test_stale_result_is_not_applied_after_fast_navigation(self) -> None:
        w = self.win
        self._show(self.live)      # çözümleme başlar
        self._show(self.plain)     # hemen başka dosyaya geçildi (eşleşmesi yok)
        self._wait(lambda: False, timeout=0.6)  # tüm işçilerin bitmesine izin ver
        self.assertIsNone(w.current_match, "önceki fotoğrafın LIVE sonucu yeni fotoğrafa uygulanmamalı")
        w._stop_playback(release=True)  # kalan geçici dosyaları temizle

    def test_gui_thread_is_not_blocked_by_resolution(self) -> None:
        """display_current dönerken eşleşme henüz çözülmemiş olabilir (arka planda sürüyor)."""
        w = self.win
        self._show(self.live)
        # Eşzamanlı çözümleme olsaydı current_match burada zaten dolu olurdu.
        immediately_resolved = w.current_match is not None
        self.assertTrue(self._wait(lambda: w.current_match is not None))
        self.assertFalse(immediately_resolved, "çözümleme GUI thread'inde senkron çalışmamalı")
        w._stop_playback(release=True)


if __name__ == "__main__":
    unittest.main()
