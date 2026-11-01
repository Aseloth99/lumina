"""Açılışta ana thread'in beklememesi (Ekim 2026 ölçümleri, NVIDIA dizüstü):

1. Qt, süreçteki ilk `Qt::Window` türü pencereyi oluştururken GPU'yu Direct3D 9 ile tanımlıyordu
   (`Direct3DCreate9` sürücüyü yükleyip imzasını doğruluyor: her açılışta ~190 ms). Ana pencere Windows'ta
   `Qt::Dialog` türündedir (Qt bu denetimi atlar); video yüzeyi ebeveynsizken native pencere zorlamaz
   (yoksa o anda üst düzey `Qt::Window` olurdu).
2. Görsel, pencere kurulduktan sonra okunmaya başlanıyordu: artık açılışta arka planda çözülür ve
   aynı yol için açılan yükleme işçisi bu işi bekler (iki kez çözülmez).
3. İlk açılışta uygulama ikonu PIL ile ~100 ms'de yeniden çiziliyordu; paketteki aynı dosya kopyalanır.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QCoreApplication, QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from _synthetic import sample_jpg  # noqa: E402

SAMPLE = sample_jpg()


def _pump(cond, timeout: float = 8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    QCoreApplication.processEvents()
    return cond()


class _App(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._ini = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._ini.name)
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._ini.cleanup()


class WindowTypeSkipsGpuProbeTest(_App):
    def test_window_type_per_platform(self) -> None:
        from app.ui import frameless

        with mock.patch.object(frameless.sys, "platform", "win32"):
            self.assertEqual(frameless.top_level_window_type(), Qt.WindowType.Dialog)
        for plat in ("linux", "darwin"):
            with mock.patch.object(frameless.sys, "platform", plat):
                self.assertEqual(frameless.top_level_window_type(), Qt.WindowType.Window)

    @unittest.skipUnless(sys.platform == "win32", "Windows'a özgü")
    def test_windows_are_independent_dialog_type_windows(self) -> None:
        """İki pencere de sahipsiz (görev çubuğunda ayrı), küçültme/büyütme ipuçları duruyor."""
        from app.ui.session import spawn_window, windows

        first = spawn_window(None)
        second = spawn_window(None)
        self.addCleanup(lambda: [w.close() for w in (first, second)])
        self.assertTrue(_pump(lambda: first.isVisible() and second.isVisible()))
        for win in (first, second):
            self.assertEqual(win.windowType(), Qt.WindowType.Dialog)
            flags = win.windowFlags()
            for hint in (Qt.WindowType.FramelessWindowHint, Qt.WindowType.WindowMinimizeButtonHint,
                         Qt.WindowType.WindowMaximizeButtonHint, Qt.WindowType.WindowSystemMenuHint):
                self.assertTrue(flags & hint, hint)
            self.assertIsNone(win.windowHandle().transientParent(), "pencere başka pencereye bağlanmamalı")
            self.assertIn(win, windows)

    @unittest.skipUnless(sys.platform == "win32", "Windows'a özgü")
    def test_video_surface_does_not_create_a_top_level_native_window(self) -> None:
        from app.ui.player import MpvSurface

        surface = MpvSurface()  # LivePlayer bunu ebeveynsiz kurar
        self.addCleanup(surface.deleteLater)
        self.assertFalse(surface.testAttribute(Qt.WidgetAttribute.WA_WState_Created),
                         "ebeveynsizken native pencere oluşmamalı")
        self.assertTrue(surface.testAttribute(Qt.WidgetAttribute.WA_NativeWindow))
        self.assertNotEqual(surface.native_window_id(), 0, "mpv istediğinde yine oluşturulur")


class PrefetchTest(_App):
    def setUp(self) -> None:
        from app.core import image_loader

        image_loader.clear_image_cache()
        self.addCleanup(image_loader.clear_image_cache)
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_only_plain_images_are_prefetched(self) -> None:
        from app.core import image_loader

        with mock.patch.object(image_loader.threading, "Thread") as thread:
            for name in ("a.mp4", "b.cr2", "c.svg", "d.gif", "e.livp", "f.txt"):
                self.assertFalse(image_loader.prefetch(self.dir / name), name)
            thread.assert_not_called()

    def test_worker_waits_for_prefetch_and_decodes_once(self) -> None:
        from app.core import image_loader

        photo = self.dir / "photo.jpg"
        shutil.copy(SAMPLE, photo)
        real_load = image_loader.load_pil
        release = threading.Event()
        calls: list[str] = []

        def slow_load(path):
            calls.append(str(path))
            release.wait(5)
            return real_load(path)

        loaded: list[tuple[int, int]] = []
        with mock.patch.object(image_loader, "load_pil", slow_load):
            self.assertTrue(image_loader.prefetch(str(photo)))
            self.assertTrue(image_loader.prefetch(str(photo)), "aynı yol ikinci kez başlatılmaz")
            worker = image_loader.ImageLoadWorker(str(photo), 1)
            worker.setAutoDelete(False)
            worker.signals.loaded.connect(lambda _p, _img, w, h, _t: loaded.append((w, h)))
            runner = threading.Thread(target=worker.run)
            runner.start()
            time.sleep(0.2)
            self.assertEqual(loaded, [], "işçi ön-çözümü beklemeli")
            release.set()
            runner.join(10)
            self.assertTrue(_pump(lambda: bool(loaded)))
        self.assertEqual(len(calls), 1, "görsel bir kez çözülmeli")
        self.assertIsNotNone(image_loader.get_cached_image(photo))

    def test_failed_prefetch_lets_the_worker_report_the_error(self) -> None:
        from app.core import image_loader

        bad = self.dir / "bad.jpg"
        bad.write_bytes(b"\xff\xd8 bozuk")
        self.assertTrue(image_loader.prefetch(bad))
        failed: list[str] = []
        worker = image_loader.ImageLoadWorker(str(bad), 1)
        worker.setAutoDelete(False)
        worker.signals.failed.connect(lambda _p, msg, _t: failed.append(msg))
        worker.run()
        self.assertTrue(_pump(lambda: bool(failed)))

    def test_window_shows_prefetched_image_without_a_load_worker(self) -> None:
        from app.core import image_loader
        from app.ui import main_window as mw

        photo = self.dir / "photo.jpg"
        shutil.copy(SAMPLE, photo)
        self.assertTrue(image_loader.prefetch(photo))
        self.assertTrue(_pump(lambda: image_loader.get_cached_image(photo) is not None))
        started: list[str] = []

        class CountLoad(mw.ImageLoadWorker):
            def __init__(self, path, token):
                started.append(path)
                super().__init__(path, token)

        with mock.patch.object(mw, "ImageLoadWorker", CountLoad):
            win = mw.MainWindow(str(photo))
            self.addCleanup(win.deleteLater)
            win.show()
            self.addCleanup(win.close)
            self.assertTrue(_pump(lambda: not win.canvas.pixmap_item.pixmap().isNull()))
        self.assertNotIn(str(photo), started, "ön-çözülen görsel yeniden yüklenmemeli")


class IconCopyTest(unittest.TestCase):
    def test_local_icon_is_copied_from_the_bundle(self) -> None:
        from app.resources import brand

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        local = Path(tmp.name) / "sub" / "Lumina.ico"
        with mock.patch.object(brand, "_local_icon", return_value=local), \
             mock.patch.object(brand, "write_app_ico", side_effect=AssertionError("yeniden çizilmemeli")):
            self.assertEqual(brand.ensure_app_icon(), local)
        self.assertEqual(local.read_bytes(), brand.bundled_icon().read_bytes())

    def test_bundled_icon_matches_the_generator(self) -> None:
        """Kopyalamanın güvenli olması için paketteki ikon üreticinin çizdiğiyle aynı görünmeli
        (Pillow sürümü kodlamayı değiştirebilir; pikseller karşılaştırılır)."""
        from PIL import Image, ImageChops

        from app.resources import brand

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        made = brand.write_app_ico(Path(tmp.name) / "x.ico")
        with Image.open(made) as a, Image.open(brand.bundled_icon()) as b:
            self.assertEqual(a.info.get("sizes"), b.info.get("sizes"))
            diff = ImageChops.difference(a.convert("RGBA"), b.convert("RGBA"))
            self.assertLessEqual(max(hi for _lo, hi in diff.getextrema()), 2)


if __name__ == "__main__":
    unittest.main()
