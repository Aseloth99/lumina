"""Galeri görünümü (G): küçük resim üretimi ve önbellekleri, büyük klasörde model/istek davranışı, pencere içi
gezinme (oklar, Home/End, PgUp/PgDn), Enter / çift tık ile açma, Esc, seçim ve konumun korunması, boyut ayarı,
galeride silme + geri alma ve klasör izleyicisi.

Görseller sentetiktir (PIL ile üretilir); gerçek fotoğraf kullanılmaz.
"""

from __future__ import annotations

import io
import os
import pathlib
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # tests/ (_media)

from PIL import Image  # noqa: E402
from PyQt6.QtCore import QCoreApplication, QPoint, QPointF, QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QWheelEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


def _wait(pred, timeout: float = 8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return pred()


def _color(i: int) -> tuple[int, int, int]:
    return ((i * 53) % 256, (i * 97 + 40) % 256, (i * 151 + 80) % 256)


def _write_jpeg(path: pathlib.Path, size=(640, 480), color=(90, 140, 200)) -> None:
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)


def _two_tone(path: pathlib.Path, orientation: int | None = None) -> None:
    """400×200: sol yarı kırmızı, sağ yarı mavi; isteğe bağlı EXIF yönü."""
    img = Image.new("RGB", (400, 200), (220, 20, 20))
    img.paste((20, 20, 220), (200, 0, 400, 200))
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation
    img.save(path, "JPEG", quality=95, exif=exif.tobytes())


class _Env(unittest.TestCase):
    """Geçici ayar dosyası ve geçici veri klasörü (küçük resim önbelleği gerçek profile yazılmasın)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._ini = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._ini.name)
        cls._data = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls._env = mock.patch.dict(os.environ, {"LOCALAPPDATA": cls._data.name, "XDG_DATA_HOME": cls._data.name})
        cls._env.start()
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._env.stop()
        cls._data.cleanup()
        cls._ini.cleanup()


# ---------------------------------------------------------------------------------------------------- üretim
class MakeThumbTest(_Env):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_landscape_becomes_square_thumb(self) -> None:
        from app.ui.gallery import THUMB_SIDE, make_thumb

        path = self.dir / "wide.jpg"
        _write_jpeg(path, (1600, 900))
        img, dur = make_thumb(path)
        self.assertEqual((img.width(), img.height()), (THUMB_SIDE, THUMB_SIDE))
        self.assertEqual(dur, 0)

    def test_exif_orientation_is_applied(self) -> None:
        from app.ui.gallery import make_thumb

        path = self.dir / "rotated.jpg"
        _two_tone(path, orientation=6)  # ekranda 90° saat yönünde: sol (kırmızı) üste gelir
        img, _dur = make_thumb(path)
        top = img.pixelColor(img.width() // 2, 4)
        bottom = img.pixelColor(img.width() // 2, img.height() - 5)
        self.assertGreater(top.red(), 150)
        self.assertLess(top.blue(), 100)
        self.assertGreater(bottom.blue(), 150)
        self.assertLess(bottom.red(), 100)

    def test_small_image_is_not_upscaled(self) -> None:
        from app.ui.gallery import make_thumb

        path = self.dir / "tiny.png"
        Image.new("RGBA", (40, 60), (10, 200, 10, 128)).save(path)
        img, _dur = make_thumb(path)
        self.assertEqual((img.width(), img.height()), (40, 40))
        self.assertTrue(img.hasAlphaChannel())

    def test_livp_uses_embedded_photo(self) -> None:
        from app.ui.gallery import make_thumb

        buf = io.BytesIO()
        Image.new("RGB", (300, 300), (250, 200, 10)).save(buf, "JPEG")
        path = self.dir / "live.livp"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("IMG_0001.JPG", buf.getvalue())
            archive.writestr("IMG_0001.MOV", b"\x00" * 64)
        img, _dur = make_thumb(path)
        self.assertFalse(img.isNull())
        self.assertGreater(img.pixelColor(10, 10).red(), 200)

    def test_video_thumb_has_duration(self) -> None:
        from _media import DURATION_MS, make_ramp_video
        from app.ui.gallery import make_thumb

        path = self.dir / "clip.mp4"
        try:
            ok = make_ramp_video(path)
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"video üretilemedi: {exc}")
        if not ok:
            self.skipTest("mp4v kodlayıcı yok")
        img, dur = make_thumb(path)
        self.assertEqual(img.width(), img.height())
        self.assertLessEqual(img.width(), 288)
        self.assertAlmostEqual(dur, DURATION_MS, delta=300)

    def test_broken_file_raises(self) -> None:
        from app.ui.gallery import make_thumb

        path = self.dir / "broken.jpg"
        path.write_bytes(b"jpeg degil" * 50)
        with self.assertRaises(Exception):
            make_thumb(path)


class DiskCacheTest(_Env):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = pathlib.Path(self._tmp.name)
        self.cache = self.dir / "thumbs"
        self.addCleanup(self._tmp.cleanup)

    def _provider(self):
        from app.ui.gallery import ThumbnailProvider

        provider = ThumbnailProvider(cache_dir=self.cache, threads=2)
        self.addCleanup(provider.shutdown)
        return provider

    def test_second_session_reads_from_disk(self) -> None:
        photo = self.dir / "a.jpg"
        _write_jpeg(photo)
        first = self._provider()
        first.request([str(photo)])
        self.assertTrue(_wait(lambda: first.image(str(photo)) is not None))
        self.assertEqual(len(list(self.cache.glob("*.thumb"))), 1)
        first.shutdown()

        second = self._provider()
        with mock.patch("app.ui.gallery.make_thumb", side_effect=AssertionError("diskten okunmalıydı")):
            second.request([str(photo)])
            self.assertTrue(_wait(lambda: second.image(str(photo)) is not None))
        self.assertFalse(second.failed(str(photo)))

    def test_changed_file_gets_new_thumb(self) -> None:
        from app.ui.gallery import ThumbDiskCache

        photo = self.dir / "b.jpg"
        _write_jpeg(photo, color=(250, 10, 10))
        key1 = ThumbDiskCache.key(photo, photo.stat())
        _write_jpeg(photo, size=(500, 500), color=(10, 250, 10))
        os.utime(photo, ns=(photo.stat().st_atime_ns, photo.stat().st_mtime_ns + 5_000_000_000))
        key2 = ThumbDiskCache.key(photo, photo.stat())
        self.assertNotEqual(key1, key2)

    def test_video_duration_survives_disk_round_trip(self) -> None:
        from app.ui.gallery import ThumbDiskCache
        from PyQt6.QtGui import QImage

        disk = ThumbDiskCache(self.cache)
        img = QImage(32, 32, QImage.Format.Format_RGB32)
        img.fill(0x336699)
        self.assertTrue(disk.store("k1", img, 12_345))
        loaded = disk.load("k1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded[1], 12_345)
        self.assertEqual(loaded[0].width(), 32)

    def test_prune_removes_oldest_first(self) -> None:
        from app.ui.gallery import ThumbDiskCache

        self.cache.mkdir()
        disk = ThumbDiskCache(self.cache)
        now = time.time()
        for i in range(10):
            file = self.cache / f"{i:02d}.thumb"
            file.write_bytes(b"x" * 1000)
            os.utime(file, (now - 1000 + i, now - 1000 + i))  # 00 en eski
        removed = disk.prune(budget=5000)
        left = sorted(p.name for p in self.cache.glob("*.thumb"))
        self.assertEqual(removed, 6)
        self.assertEqual(left, ["06.thumb", "07.thumb", "08.thumb", "09.thumb"])

    def test_failed_file_is_marked_not_retried(self) -> None:
        broken = self.dir / "x.jpg"
        broken.write_bytes(b"bozuk" * 100)
        provider = self._provider()
        provider.request([str(broken)])
        self.assertTrue(_wait(lambda: provider.failed(str(broken))))
        with mock.patch("app.ui.gallery._ThumbJob") as job:
            provider.request([str(broken)])
            job.assert_not_called()


class ProviderQueueTest(_Env):
    def test_new_request_replaces_queue_visible_first(self) -> None:
        from app.ui.gallery import ThumbnailProvider

        provider = ThumbnailProvider(cache_dir=None, threads=1)
        self.addCleanup(provider.shutdown)
        started: list[str] = []
        with mock.patch("app.ui.gallery._ThumbJob") as job_cls:
            job_cls.side_effect = lambda key, _disk: started.append(key) or mock.MagicMock()
            with mock.patch.object(provider._pool, "start"):
                provider.request([f"a{i}" for i in range(50)])
                self.assertEqual(started, ["a0", "a1"])  # 1 iş parçacığı + 1 bekleyen
                provider.request([f"b{i}" for i in range(50)])  # kaydırıldı: a2… artık üretilmez
                self.assertEqual(provider._queue[:2], ["b0", "b1"])
                self.assertNotIn("a2", provider._queue)
                self.assertEqual(started, ["a0", "a1"], "eşzamanlı iş sınırı aşılmamalı")

    def test_memory_budget_keeps_requested_set(self) -> None:
        from app.ui.gallery import ThumbnailProvider
        from PyQt6.QtGui import QImage

        provider = ThumbnailProvider(cache_dir=None, threads=1)
        self.addCleanup(provider.shutdown)
        provider._budget = 3 * 64 * 64 * 4
        img = QImage(64, 64, QImage.Format.Format_ARGB32)
        provider._keep = 2
        for i in range(6):
            provider._on_done(f"k{i}", QImage(img), 0, 0)
        self.assertLessEqual(provider._mem_bytes, provider._budget)
        self.assertIsNotNone(provider.image("k5"))
        self.assertIsNone(provider.image("k0"))


# ---------------------------------------------------------------------------------------------------- büyük klasör
class BigFolderModelTest(_Env):
    def test_twenty_thousand_items_lay_out_fast_and_request_only_visible(self) -> None:
        from app.ui.gallery import GalleryView, ThumbnailProvider

        provider = ThumbnailProvider(cache_dir=None, threads=1)
        self.addCleanup(provider.shutdown)
        requests: list[list[str]] = []
        provider.request = lambda keys: requests.append(list(keys))
        view = GalleryView(provider, cell=150)
        self.addCleanup(view.deleteLater)
        view.resize(1000, 700)
        view.show()
        paths = [pathlib.Path(f"C:/yok/klasor/IMG_{i:05d}.jpg") for i in range(20_000)]

        t0 = time.perf_counter()
        view.set_files(paths, 0)
        _wait(lambda: bool(requests), timeout=2.0)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 1.5, f"20 bin öğe {elapsed:.2f} sn sürdü")
        self.assertTrue(requests)
        cols = view.columns()
        first, last = view.visible_range()
        self.assertGreaterEqual(cols, 5)
        self.assertEqual(first, 0)
        # Hesaplanan sütun sayısı Qt'nin yerleşimiyle aynı ve ızgara genişliği doldurur (sağda boş sütun kalmaz).
        model = view.gallery_model()
        top0 = view.visualRect(model.index(0, 0)).top()
        self.assertEqual(view.visualRect(model.index(cols - 1, 0)).top(), top0)
        self.assertGreater(view.visualRect(model.index(cols, 0)).top(), top0)
        self.assertLess(view.viewport().width() - cols * view.gridSize().width(), view.gridSize().width() // 2)
        self.assertLessEqual(len(requests[-1]), (last - first) * 2 + cols * 2 + cols)
        self.assertEqual(requests[-1][0], str(paths[0]), "önce görünenler istenmeli")

        requests.clear()
        t0 = time.perf_counter()
        view.select_row(19_999)
        _wait(lambda: bool(requests), timeout=2.0)
        self.assertLess(time.perf_counter() - t0, 0.5)
        first, last = view.visible_range()
        self.assertGreater(first, 19_000)
        self.assertIn(str(paths[19_999]), requests[-1])
        self.assertNotIn(str(paths[0]), requests[-1])

    def test_same_list_keeps_model_and_scroll(self) -> None:
        from app.ui.gallery import GalleryView, ThumbnailProvider

        provider = ThumbnailProvider(cache_dir=None, threads=1)
        self.addCleanup(provider.shutdown)
        provider.request = lambda keys: None
        view = GalleryView(provider, cell=150)
        self.addCleanup(view.deleteLater)
        view.resize(800, 600)
        view.show()
        paths = [pathlib.Path(f"C:/yok/IMG_{i:04d}.jpg") for i in range(500)]
        view.set_files(paths, 250)
        QCoreApplication.processEvents()
        resets: list[int] = []
        view.gallery_model().modelReset.connect(lambda: resets.append(1))
        value = view.verticalScrollBar().value()
        self.assertGreater(value, 0)
        view.set_files(list(paths), 251)
        self.assertEqual(resets, [])
        self.assertEqual(view.current_row(), 251)
        # Yeni dosya eklendi: model yenilenir, kaydırma yerinde kalır
        view.set_files(paths[:100] + [pathlib.Path("C:/yok/AAA_new.jpg")] + paths[100:], 252)
        self.assertEqual(resets, [1])
        self.assertLessEqual(abs(view.verticalScrollBar().value() - value), view.gridSize().height())


# ---------------------------------------------------------------------------------------------------- pencere
class _WindowCase(_Env):
    COUNT = 23  # asal: hangi sütun sayısında olursa olsun son satır eksik kalır

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = pathlib.Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()
        super().tearDownClass()

    def setUp(self) -> None:
        from app.core import image_loader
        from app.ui.main_window import MainWindow

        self.dir = pathlib.Path(tempfile.mkdtemp(dir=self.root))
        for i in range(self.COUNT):
            _write_jpeg(self.dir / f"img_{i:03d}.jpg", (320, 240), _color(i))
        image_loader.clear_image_cache()
        self.win = MainWindow()
        self.win.sort_by = "name_asc"
        self.win.resize(900, 600)
        self.win.show()
        self.win.activateWindow()
        self.addCleanup(self._close)

    def _close(self) -> None:
        self.win.close()
        self.win.deleteLater()
        QCoreApplication.processEvents()

    def _open(self, index: int = 0) -> None:
        w = self.win
        w.load_path(self.dir / f"img_{index:03d}.jpg")
        self.assertTrue(_wait(lambda: w._folder_ready and len(w.image_files) >= self.COUNT))
        self.assertTrue(_wait(lambda: not w.canvas.pixmap_item.pixmap().isNull()))

    def _key(self, key, mods=Qt.KeyboardModifier.NoModifier, widget=None) -> None:
        QTest.keyClick(widget or self.win.gallery or self.win, key, mods)
        QCoreApplication.processEvents()


class GalleryWindowTest(_WindowCase):
    def test_g_opens_with_current_selected_and_esc_returns(self) -> None:
        w = self.win
        self._open(7)
        self._key(Qt.Key.Key_G, widget=w)
        self.assertTrue(w._gallery_on)
        self.assertIs(w.stage.stack.currentWidget(), w.gallery)
        self.assertFalse(w.dock_wrap.isVisible(), "galeride alt bar gizli")
        self.assertEqual(w.gallery.current_row(), 7)
        self.assertTrue(w.gallery.viewport().rect().intersects(w.gallery.visualRect(w.gallery.currentIndex())))
        self.assertEqual(w.btn_gallery.property("active"), "true")
        self.assertTrue(_wait(lambda: w.gallery.provider.image(str(w.image_files[7])) is not None),
                        "görünen küçük resimler üretilmeli")

        self._key(Qt.Key.Key_Right)
        self.assertEqual(w.current_idx, 8)
        self.assertEqual(w.lbl_filename.text(), "img_008.jpg")
        self._key(Qt.Key.Key_Escape)
        self.assertFalse(w._gallery_on)
        self.assertIs(w.stage.stack.currentWidget(), w.canvas)
        self.assertEqual(w.current_file().name, "img_008.jpg")
        self.assertTrue(w.dock_wrap.isVisible())
        self.assertEqual(w.btn_gallery.property("active"), "false")

    def test_arrow_home_end_page_keys(self) -> None:
        w = self.win
        self._open(0)
        w.open_gallery()
        g = w.gallery
        cols = g.columns()
        self.assertGreater(cols, 1)
        self._key(Qt.Key.Key_Down)
        self.assertEqual(w.current_idx, cols)
        self._key(Qt.Key.Key_Left)  # satır başında ← bir önceki satırın sonuna
        self.assertEqual(w.current_idx, cols - 1)
        self._key(Qt.Key.Key_Up)  # ilk satırda ↑ yerinde kalır
        self.assertEqual(w.current_idx, cols - 1)
        self._key(Qt.Key.Key_End)
        self.assertEqual(w.current_idx, self.COUNT - 1)
        self._key(Qt.Key.Key_Right)  # sonda → yerinde
        self.assertEqual(w.current_idx, self.COUNT - 1)
        self._key(Qt.Key.Key_Home)
        self.assertEqual(w.current_idx, 0)
        self._key(Qt.Key.Key_PageDown)
        self.assertGreater(w.current_idx, 0)
        self.assertEqual(w.current_idx % cols, 0, "sayfa adımı sütunu korur")
        self._key(Qt.Key.Key_PageUp)
        self.assertEqual(w.current_idx, 0)
        self.assertTrue(w._gallery_on, "gezinme galeriyi kapatmamalı")

    def test_down_on_short_last_row_goes_to_last_item(self) -> None:
        w = self.win
        self._open(0)
        w.open_gallery()
        cols = w.gallery.columns()
        if self.COUNT % cols == 0:
            self.skipTest("son satır dolu")
        last_full_row_start = (self.COUNT // cols - 1) * cols
        w.gallery.select_row(last_full_row_start + cols - 1)
        w.current_idx = last_full_row_start + cols - 1
        self._key(Qt.Key.Key_Down)
        self.assertEqual(w.current_idx, self.COUNT - 1)

    def test_enter_opens_selected_file(self) -> None:
        w = self.win
        self._open(2)
        w.open_gallery()
        self._key(Qt.Key.Key_Right)
        self._key(Qt.Key.Key_Right)
        self._key(Qt.Key.Key_Return)
        self.assertFalse(w._gallery_on)
        self.assertEqual(w.current_file().name, "img_004.jpg")
        self.assertTrue(_wait(lambda: not w.canvas.pixmap_item.pixmap().isNull()
                              and w._display_path is not None and w._display_path.name == "img_004.jpg"))
        self.assertFalse(w._nav_grace_wanted, "galeriden açmak doğrudan açmaktır")

    def test_double_click_opens(self) -> None:
        w = self.win
        self._open(0)
        w.open_gallery()
        g = w.gallery
        QCoreApplication.processEvents()
        rect = g.visualRect(g.gallery_model().index(5, 0))
        QTest.mouseDClick(g.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center())
        QCoreApplication.processEvents()
        self.assertFalse(w._gallery_on)
        self.assertEqual(w.current_file().name, "img_005.jpg")

    def test_reopen_keeps_position(self) -> None:
        w = self.win
        self._open(0)
        w.open_gallery()
        w.gallery.select_row(19)
        w._on_gallery_current(19)
        scroll = w.gallery.verticalScrollBar().value()
        w.close_gallery()
        self.assertEqual(w.current_file().name, "img_019.jpg")
        w.toggle_gallery()
        self.assertEqual(w.gallery.current_row(), 19)
        # Alt bar gizlenince ızgara uzar; kaydırma en fazla bu fark kadar oynar, seçili karo görünür kalır.
        self.assertLessEqual(abs(w.gallery.verticalScrollBar().value() - scroll), w.gallery.gridSize().height())
        self.assertTrue(w.gallery.viewport().rect().contains(w.gallery.visualRect(w.gallery.currentIndex())))

    def test_single_view_keys_do_nothing_in_gallery(self) -> None:
        w = self.win
        self._open(3)
        w.open_gallery()
        rotation = w.canvas._rotation
        for key in (Qt.Key.Key_R, Qt.Key.Key_I, Qt.Key.Key_Space, Qt.Key.Key_A, Qt.Key.Key_D, Qt.Key.Key_0):
            self._key(key)
        self.assertEqual(w.canvas._rotation, rotation)
        self.assertFalse(w.details.isVisible())
        self.assertEqual(w.current_idx, 3)
        self.assertTrue(w._gallery_on)

    def test_size_keys_and_ctrl_wheel_are_remembered(self) -> None:
        w = self.win
        self._open(0)
        w.open_gallery()
        g = w.gallery
        start = g.cell()
        cols = g.columns()
        self._key(Qt.Key.Key_Plus)
        self.assertGreater(g.cell(), start)
        self.assertLessEqual(g.columns(), cols)
        self.assertEqual(int(w.settings.value("gallery_thumb")), g.cell())
        self._key(Qt.Key.Key_Minus)
        self._key(Qt.Key.Key_Minus)
        self.assertLess(g.cell(), start)
        bigger = g.cell()
        pos = QPointF(g.viewport().rect().center())
        event = QWheelEvent(pos, QPointF(g.mapToGlobal(pos.toPoint())), QPoint(0, 0), QPoint(0, 240),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        g.wheelEvent(event)
        self.assertGreater(g.cell(), bigger)
        self.assertEqual(int(w.settings.value("gallery_thumb")), g.cell())
        self.assertEqual(w.current_idx, 0, "boyut değişimi seçimi değiştirmemeli")

    def test_delete_and_undo_stay_in_gallery(self) -> None:
        w = self.win
        self._open(0)
        w.open_gallery()
        w.gallery.select_row(5)
        w._on_gallery_current(5)
        with mock.patch("app.core.win_shell.send_to_recycle", side_effect=lambda p: os.remove(p)):
            self._key(Qt.Key.Key_Delete)
        self.assertFalse((self.dir / "img_005.jpg").exists())
        self.assertTrue(w._gallery_on)
        self.assertEqual(w.gallery.gallery_model().rowCount(), self.COUNT - 1)
        self.assertEqual(w.current_file().name, "img_006.jpg", "silince sıradaki seçilir")
        self.assertEqual(w.gallery.current_row(), w.current_idx)

        self._key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue((self.dir / "img_005.jpg").exists())
        self.assertTrue(_wait(lambda: w.gallery.gallery_model().rowCount() == self.COUNT))
        self.assertTrue(w._gallery_on, "geri alma galeride kalır")
        self.assertTrue(_wait(lambda: w.current_file() is not None and w.current_file().name == "img_005.jpg"))
        self.assertEqual(w.gallery.current_row(), 5)

    def test_folder_watcher_updates_grid(self) -> None:
        w = self.win
        self._open(10)
        w.open_gallery()
        _write_jpeg(self.dir / "img_000a.jpg", (200, 200), (1, 2, 3))
        w._on_directory_changed(str(self.dir))  # offscreen'de izleyici olayı gecikebilir: aynı yolu tetikle
        self.assertTrue(_wait(lambda: w.gallery.gallery_model().rowCount() == self.COUNT + 1))
        self.assertTrue(w._gallery_on)
        self.assertEqual(w.current_file().name, "img_010.jpg", "seçim aynı dosyada kalmalı")
        self.assertEqual(w.gallery.current_row(), w.current_idx)

    def test_open_file_from_gallery_returns_to_single_view(self) -> None:
        w = self.win
        self._open(0)
        w.open_gallery()
        w.load_path(self.dir / "img_012.jpg")
        self.assertFalse(w._gallery_on)
        self.assertIs(w.stage.stack.currentWidget(), w.canvas)
        self.assertTrue(_wait(lambda: w._folder_ready))
        self.assertEqual(w.current_file().name, "img_012.jpg")

    def test_empty_window_ignores_g(self) -> None:
        w = self.win
        w.toggle_gallery()
        self.assertFalse(w._gallery_on)
        self.assertIsNone(w.gallery)
        self.assertFalse(w.btn_gallery.isVisible())

    def test_theme_and_language_reach_gallery(self) -> None:
        w = self.win
        self._open(0)
        w.open_gallery()
        w.set_language("en")
        self.assertEqual(w.gallery._delegate.live_text, "LIVE")
        self.assertTrue(w.btn_gallery.toolTip().startswith("Close Gallery"))
        w.set_language("tr")
        self.assertEqual(w.gallery._delegate.live_text, "CANLI")
        self.assertTrue(w.btn_gallery.toolTip().startswith("Galeriyi Kapat"))
        from app.resources import theme as theme_mod

        for mode in ("light", "dark"):
            w.set_theme(mode)
            self.assertEqual(w.gallery._delegate._ring.name().lower(),
                             theme_mod.palette(mode, w.accent)["accent_edge"].lower()[:7])
        self.assertTrue(w._gallery_on, "tema/dil değişimi galeriyi kapatmamalı")
        self.assertIs(w.stage.stack.currentWidget(), w.gallery)
        self.assertFalse(w.dock_wrap.isVisible())
        w.close_gallery()
        self.assertEqual(w.btn_gallery.toolTip(), "Galeri (G)")


class LiveBadgeTest(_WindowCase):
    COUNT = 4

    def test_live_sidecar_gets_badge(self) -> None:
        w = self.win
        (self.dir / "img_001.mov").write_bytes(b"\x00" * 256)
        self._open(0)
        w.open_gallery()
        model = w.gallery.gallery_model()
        row = model.row_of(str(self.dir / "img_001.jpg"))
        self.assertGreaterEqual(row, 0)
        self.assertTrue(_wait(lambda: model.is_live(row)))
        self.assertFalse(model.is_live(model.row_of(str(self.dir / "img_002.jpg"))))


if __name__ == "__main__":
    unittest.main()
