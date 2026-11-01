"""Video denetiminde bulunan ve düzeltilen hatalar (her biri eski kodda bu testlerle yeniden üretildi).

1. Sessize al -> başka video aç -> sesi aç: mpv `volume` 0 kalıyordu, video SESSİZ oynuyordu
   (sessizlik hem `mute` hem `volume=0` ile yazılıyor, ses açılınca yalnızca `mute` geri alınıyordu).
2. Bozuk/çözülemeyen video: poster hatası sessizce yok sayılıyor, tuvalde bir ÖNCEKİ fotoğraf bu videonun
   adıyla kalıyordu (bozuk görselde de hata kutusundan sonra aynısı).
3. Otomatik oynatma yarışı: video açılırken (mpv başlamadan) poster gelince `_on_image_loaded` ->
   `_maybe_autoplay` -> `toggle_live_playback` açılışı "iptal" sayıp oynatmayı durduruyordu
   (gizli masaüstünde gerçek exe ile: 4 açılışın 3'ünde otomatik oynatma gerçekleşmedi).
4. Poster işçisi videoyu iki kez açıyordu (süre + ilk kare); poster önbelleği yalnızca adetle sınırlıydı
   (16 tam boy kare: 4K'da ~400 MB).
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # tests/ (_media)

from PyQt6.QtCore import QCoreApplication, QSettings  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from _media import make_ramp_video  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from _synthetic import sample_jpg  # noqa: E402

SAMPLE = sample_jpg()


def _wait(pred, timeout: float = 8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return pred()


class _Media(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._ini = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._ini.name)
        cls.app = QApplication.instance() or QApplication([])
        cls._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.dir = pathlib.Path(cls._tmp.name)
        cls.v1, cls.v2 = cls.dir / "c_v1.mp4", cls.dir / "c_v2.mp4"
        try:
            ok = make_ramp_video(cls.v1) and make_ramp_video(cls.v2, gray_offset=-60)
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"video üretilemedi: {exc}")
        if not ok:
            raise unittest.SkipTest("mp4v kodlayıcı yok")
        cls.photo = cls.dir / "a_photo.jpg"
        shutil.copy(SAMPLE, cls.photo)
        cls.broken = cls.dir / "b_broken.mp4"
        cls.broken.write_bytes(b"bu bir video degil" * 400)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()
        cls._ini.cleanup()


class MuteSurvivesFileChangeTest(_Media):
    def test_unmute_after_opening_another_video_restores_volume(self) -> None:
        from app.core.mpv_backend import get_mpv
        from app.ui.player import LivePlayer, mpv_get

        mod = get_mpv()
        if mod is None:
            self.skipTest("libmpv yok")
        p = LivePlayer()
        p._new_mpv = lambda _m: mod.MPV(vo="null", ao="null", keep_open="yes", osc=False, ytdl=False,
                                        log_handler=lambda *a: None)
        self.addCleanup(p.shutdown)
        started: list[int] = []
        p.started.connect(lambda: started.append(1))
        p.play(str(self.v1))
        self.assertTrue(_wait(lambda: started))
        p.set_muted(True)
        started.clear()
        p.play(str(self.v2))  # sessizken başka dosya
        self.assertTrue(_wait(lambda: started))
        self.assertTrue(mpv_get(p._mpv, "mute"))
        p.set_muted(False)
        self.assertFalse(mpv_get(p._mpv, "mute"))
        self.assertEqual(int(mpv_get(p._mpv, "volume")), p.volume, "ses açılınca kullanıcının düzeyi geri gelmeli")
        self.assertEqual(p.volume, 100)


class _WindowCase(_Media):
    def setUp(self) -> None:
        from app.core import image_loader
        from app.ui.main_window import MainWindow

        image_loader.clear_image_cache()
        self.win = MainWindow()
        self.win.sort_by = "name_asc"
        self.win.resize(900, 600)
        self.win.show()
        self.addCleanup(self._close)

    def _close(self) -> None:
        self.win.close()
        self.win.deleteLater()
        QCoreApplication.processEvents()

    def _photo_shown(self) -> bool:
        return not self.win.canvas.pixmap_item.pixmap().isNull()


class BrokenMediaDoesNotShowPreviousPhotoTest(_WindowCase):
    def test_broken_video_clears_previous_photo(self) -> None:
        w = self.win
        w.load_path(self.photo)
        self.assertTrue(_wait(lambda: self._photo_shown() and w._folder_ready))
        w.current_idx = [p.name for p in w.image_files].index(self.broken.name)
        w.display_current()
        self.assertEqual(w.current_file().name, self.broken.name)
        w.pool.waitForDone(8000)
        self.assertTrue(_wait(lambda: not self._photo_shown(), timeout=5.0),
                        "bozuk videonun adıyla önceki fotoğraf gösterilmemeli")

    def test_broken_image_clears_previous_photo(self) -> None:
        w = self.win
        bad = self.dir / "d_bad.jpg"
        bad.write_bytes(b"\xff\xd8 bozuk jpeg")

        def remove_bad() -> None:
            # Arka plandaki okuyucular (ön yükleme / küçük resim) dosyayı bir an daha açık tutabilir.
            w.pool.waitForDone(5000)
            for _ in range(60):
                try:
                    bad.unlink(missing_ok=True)
                    return
                except PermissionError:
                    QCoreApplication.processEvents()
                    time.sleep(0.05)
            bad.unlink(missing_ok=True)

        self.addCleanup(remove_bad)
        w.load_path(self.photo)
        self.assertTrue(_wait(self._photo_shown))
        warned: list[str] = []
        with mock.patch("app.ui.main_window.MessageBox.warning", side_effect=lambda *a, **k: warned.append(a[2])):
            w.image_files = [self.photo, bad]
            w.current_idx = 1
            w.display_current()
            self.assertTrue(_wait(lambda: bool(warned)), "hata bildirilmeli")
        self.assertFalse(self._photo_shown(), "hatalı görselin adıyla önceki fotoğraf kalmamalı")


class AutoplayIsNotCancelledByPosterTest(_WindowCase):
    def test_poster_arriving_while_opening_keeps_autoplay(self) -> None:
        w = self.win
        plays: list[str] = []
        stops: list[bool] = []
        orig_stop = w._stop_playback

        def spy_stop(release: bool = False) -> None:
            stops.append(release)
            orig_stop(release)

        with mock.patch.object(w, "_autoplay_enabled", return_value=True), \
             mock.patch.object(w.player, "play", side_effect=lambda path, loop=None: plays.append(path)), \
             mock.patch.object(w, "_stop_playback", side_effect=spy_stop):
            w.image_files = [self.v1]
            w.current_idx = 0
            w.display_current()
            self.assertEqual(len(plays), 1, "video otomatik oynatılmalı")
            self.assertTrue(w._opening_play)
            stops.clear()
            # mpv henüz başlamadan poster gelir (gerçekte çoğu zaman böyle)
            poster = QImage(32, 18, QImage.Format.Format_RGB32)
            poster.fill(0xFF202020)
            w._on_image_loaded(str(self.v1), poster, 32, 18, w._load_token)
            self.assertTrue(w._opening_play, "poster geldi diye açılış iptal edilmemeli")
            self.assertEqual(stops, [], "oynatma durdurulmamalı")
            self.assertEqual(len(plays), 1, "ikinci kez oynatılmamalı")


class PosterOpensVideoOnceTest(_Media):
    def test_worker_opens_capture_once_and_reports_both(self) -> None:
        from app.core import image_loader, video_io

        with video_io._poster_lock:
            video_io._poster_cache.clear()
            video_io._duration_cache.clear()
        opens: list[str] = []
        real_open = video_io.open_capture

        def counting_open(path):
            opens.append(path)
            return real_open(path)

        got: dict[str, object] = {}
        worker = image_loader.VideoPosterWorker(str(self.v1), 7)
        worker.signals.meta.connect(lambda p, dur, t: got.setdefault("dur", dur))
        worker.signals.loaded.connect(lambda p, img, w, h, t: got.setdefault("size", (w, h)))
        with mock.patch.object(video_io, "open_capture", counting_open):
            worker.run()
            QCoreApplication.processEvents()
            self.assertEqual(len(opens), 1, "video bir kez açılmalı")
            # Aynı video yeniden gösterilince (gezinme) hiç açılmaz
            image_loader.VideoPosterWorker(str(self.v1), 8).run()
            self.assertEqual(len(opens), 1)
        self.assertAlmostEqual(got["dur"], 10_000, delta=300)
        self.assertEqual(got["size"], (320, 180))

    def test_poster_cache_is_byte_bounded(self) -> None:
        from app.core import video_io

        big = QImage(1000, 1000, QImage.Format.Format_RGB888)  # ~3 MB
        per = big.sizeInBytes()
        with video_io._poster_lock:
            video_io._poster_cache.clear()
        with mock.patch.object(video_io, "_POSTER_CACHE_MAX_BYTES", per * 3 + 1):
            with video_io._poster_lock:
                for i in range(8):
                    video_io._store_poster_locked(f"v{i}", QImage(big))
                keys = list(video_io._poster_cache)
                total = sum(img.sizeInBytes() for img in video_io._poster_cache.values())
        self.assertLessEqual(total, per * 3 + 1)
        self.assertEqual(keys[-1], "v7", "en son poster korunmalı")
        with mock.patch.object(video_io, "_POSTER_CACHE_MAX_BYTES", 10):
            with video_io._poster_lock:
                video_io._store_poster_locked("huge", QImage(big))
                self.assertIn("huge", video_io._poster_cache, "tek büyük poster yine de tutulmalı")
        with video_io._poster_lock:
            video_io._poster_cache.clear()


if __name__ == "__main__":
    unittest.main()
