"""SeekPreviewController: anlık kaba kare, kademeli iyileşme, dosya değişiminde sızıntı yok.

Gerçek (sentetik) video + gerçek Qt widget'ları (offscreen). Eski hatalar:
  * yeni videoda eski videonun karesi görünmesi,
  * 200. sn için 1. sn karesinin "kesin" sayılması,
  * zaman etiketinin imleci değil gelen karenin zamanını yazması.
"""

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

from PyQt6.QtCore import QCoreApplication, QObject  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFrame, QLabel  # noqa: E402

from _media import DURATION_MS, expected_gray, gray_of, make_ramp_video  # noqa: E402


class SeekPreviewControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls._tmp = tempfile.TemporaryDirectory()
        cls.v1 = pathlib.Path(cls._tmp.name) / "one.mp4"
        cls.v2 = pathlib.Path(cls._tmp.name) / "two.mp4"
        try:
            ok = make_ramp_video(cls.v1) and make_ramp_video(cls.v2, gray_offset=-100)
        except Exception as exc:
            raise unittest.SkipTest(f"video üretilemedi: {exc}")
        if not ok:
            raise unittest.SkipTest("bu ortamda mp4v kodlayıcı yok")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        from app.ui.seek_preview import SeekPreviewController

        self.owner = QObject()
        self.frame = QFrame()
        self.image = QLabel(self.frame)
        self.image.setFixedSize(188, 106)
        self.time = QLabel(self.frame)
        self.placed: list[float] = []
        self.ctrl = SeekPreviewController(
            self.owner,
            self.frame,
            self.image,
            self.time,
            place=self.placed.append,
            duration_ms=lambda: DURATION_MS,
        )
        self.addCleanup(self.ctrl.shutdown)
        self.addCleanup(self.frame.close)

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

    def _shown_gray(self) -> int | None:
        pix = self.image.pixmap()
        if pix is None or pix.isNull():
            return None
        return gray_of(pix.toImage())

    def test_opening_a_video_decodes_nothing_until_the_first_hover(self) -> None:
        self.ctrl.set_media(str(self.v1), DURATION_MS)
        self._wait(lambda: False, timeout=0.6)
        self.assertEqual(len(self.ctrl._store), 0, "hover olmadan önizleme karesi çözülmemeli")
        self.ctrl.on_hover(0.3)
        self.assertTrue(self._wait(lambda: len(self.ctrl._store) >= 8), "ilk hover storyboard'u başlatmalı")
        self.assertTrue(
            self._wait(lambda: self._shown_gray() is not None and abs(self._shown_gray() - expected_gray(3000)) <= 9),
            f"ilk hover'ın karesi gelmeli (gösterilen gri: {self._shown_gray()})",
        )

    def test_hover_shows_coarse_frame_then_refines(self) -> None:
        self.ctrl.set_media(str(self.v1), DURATION_MS)
        # İlk hover storyboard'u (kaba) başlatır; hazır olana dek bekle — oynatma gerekmez
        self.ctrl.on_hover(0.05)
        self.assertTrue(self._wait(lambda: len(self.ctrl._store) >= 8))
        self.ctrl.on_hover(0.55)  # 5500 ms
        self.assertTrue(self.frame.isVisible())
        # ANINDA en yakın kaba kare gösterilir (decode beklemeden)
        self.assertIsNotNone(self._shown_gray(), "hover anında kare gösterilmeli")
        # Kesin kare gelince imlecin zamanına yakın olmalı
        self.assertTrue(
            self._wait(lambda: self._shown_gray() is not None
                       and abs(self._shown_gray() - expected_gray(5500)) <= 9),
            f"kesin kare gelmedi (gösterilen gri: {self._shown_gray()})",
        )
        self.assertEqual(self.time.text(), "0:05", "etiket imlecin zamanını yazmalı")

    def test_far_jump_is_not_served_from_wrong_time(self) -> None:
        """Eski hata: tek kare varken tüm zamanlar için o kare 'kesin' sayılıyordu."""
        from app.core.preview_store import identity_for

        self.ctrl.set_media(str(self.v1), DURATION_MS)
        ident = identity_for(str(self.v1))
        self.ctrl.on_hover(0.1)
        self.assertTrue(self._wait(lambda: len(self.ctrl._store) >= 1))
        self.ctrl._store.clear()
        self.ctrl._store.reset(ident)
        self.ctrl._store.add(ident, 1000, self.image.grab().toImage())
        self.assertIsNone(self.ctrl._store.within(9000, 250))

    def test_media_change_never_shows_previous_videos_frame(self) -> None:
        self.ctrl.set_media(str(self.v1), DURATION_MS)
        self.ctrl.on_hover(0.1)
        self.assertTrue(self._wait(lambda: len(self.ctrl._store) >= 8))
        self.ctrl.on_hover(0.8)
        self.assertIsNotNone(self._shown_gray())
        self.ctrl.set_media(str(self.v2), DURATION_MS)
        self.assertEqual(len(self.ctrl._store), 0, "dosya değişince depo boşalmalı")
        self.assertIsNone(self._shown_gray(), "yeni videoda eski karenin kalıntısı olmamalı")
        self.ctrl.on_hover(0.8)
        # video2'nin 8 sn'deki karesi (gri ≈ 106); video1'inki ≈ 204 olurdu
        self.assertTrue(
            self._wait(lambda: self._shown_gray() is not None
                       and abs(self._shown_gray() - max(0, expected_gray(8000) - 100)) <= 25),
            f"video2 karesi gösterilmedi (gri: {self._shown_gray()})",
        )

    def test_release_media_hides_and_clears(self) -> None:
        self.ctrl.set_media(str(self.v1), DURATION_MS)
        self.ctrl.on_hover(0.1)
        self.assertTrue(self._wait(lambda: len(self.ctrl._store) >= 1))
        self.ctrl.on_hover(0.5)
        self.ctrl.release_media(wait=True)
        self.assertFalse(self.frame.isVisible())
        self.assertIsNone(self.ctrl.path)
        self.ctrl.on_hover(0.5)  # medya yokken hover yok sayılır
        self.assertFalse(self.frame.isVisible())


if __name__ == "__main__":
    unittest.main()
