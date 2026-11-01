"""İlk açılış hızı: fotoğraf ilk kare olarak önce gelir; yan işler (şerit küçük resimleri, komşu ön yüklemesi,
Live/Motion eşi araması, EXIF sıralaması) kare çizildikten sonra başlar; GUI thread'i dosya sistemine
gereksiz gidip gelmez (ağ/WSL yolunda her biri yavaş); açılışta kullanılmayan ağır modüller yüklenmez.

Ölçümle bulunan sorunlar (WSL yolu, CPU %100): GUI thread'inde açılış başına ~10 `Path.resolve()`
(her biri dosyayı açar), kare çizilmeden başlayan 11 küçük resim + 3 ön yükleme + Live çözümlemesi,
eşi olmayan fotoğrafta Live çözümlemesinin ikinci kez çalışması ve açılışta cv2/numpy yüklenmesi.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
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

from PyQt6.QtCore import QCoreApplication, QSettings  # noqa: E402
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


class _WindowCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._ini = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._ini.name)
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._ini.cleanup()

    def setUp(self) -> None:
        from app.core import image_loader
        from app.ui.main_window import MainWindow

        image_loader.clear_image_cache()
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.files = []
        for i in range(6):
            p = self.dir / f"img_{i:02d}.jpg"
            shutil.copy(SAMPLE, p)
            self.files.append(p)
        self.win = MainWindow()
        self.win.sort_by = "name_asc"
        self.win.resize(900, 600)
        self.win.show()
        self.addCleanup(self._close)

    def _close(self) -> None:
        self.win.close()
        self.win.deleteLater()
        QCoreApplication.processEvents()

    def shown(self) -> bool:
        return not self.win.canvas.pixmap_item.pixmap().isNull()


class SiblingWorkWaitsForFirstFrameTest(_WindowCase):
    def test_strip_preload_and_live_lookup_start_only_after_frame(self) -> None:
        from app.core import image_loader
        from app.ui import filmstrip as fs_mod
        from app.ui import main_window as mw

        target = self.files[2]
        gate = threading.Event()
        real_load = image_loader.load_pil

        def slow_load(path):
            if Path(path) == target:
                gate.wait(10)
            return real_load(path)

        counts = {"live": 0, "preload": 0, "thumb": 0}

        class CountLive(mw.LiveResolveWorker):
            def __init__(self, *a, **k):
                counts["live"] += 1
                super().__init__(*a, **k)

        class CountLoad(mw.ImageLoadWorker):
            def __init__(self, path, token):
                if token < 0:
                    counts["preload"] += 1
                super().__init__(path, token)

        class CountThumb(fs_mod.ThumbWorker):
            def __init__(self, *a, **k):
                counts["thumb"] += 1
                super().__init__(*a, **k)

        with mock.patch.object(image_loader, "load_pil", slow_load), \
             mock.patch.object(mw, "LiveResolveWorker", CountLive), \
             mock.patch.object(mw, "ImageLoadWorker", CountLoad), \
             mock.patch.object(fs_mod, "ThumbWorker", CountThumb):
            self.win.load_path(target)
            # Klasör listesi gelir (gezinme hazır) ama görüntü henüz çözülmedi.
            self.assertTrue(_pump(lambda: self.win._folder_ready), "klasör listesi gelmedi")
            self.assertEqual(len(self.win.image_files), 6)
            self.assertFalse(self.shown())
            self.assertEqual(counts, {"live": 0, "preload": 0, "thumb": 0},
                             "kare çizilmeden yan işler başlamamalı")
            self.assertEqual(self.win.filmstrip.count(), 0)

            gate.set()
            self.assertTrue(_pump(lambda: self.shown() and not getattr(self.win, "_frame_pending", False)), "kare gelmedi")
            self.assertTrue(_pump(lambda: counts["live"] == 1 and counts["preload"] > 0 and counts["thumb"] > 0),
                            f"kareden sonra yan işler başlamalı: {counts}")
            self.assertGreater(self.win.filmstrip.count(), 1)
        # Davranış aynı: gezinme ve sıra korunur
        self.assertEqual(self.win.current_file().name, target.name)
        self.win.next_image()
        self.assertEqual(self.win.current_file().name, self.files[3].name)

    def test_gate_has_a_time_limit_when_no_frame_is_painted(self) -> None:
        from app.ui import main_window as mw

        self.win.hide()  # gizli pencere hiç boyanmaz: yan işler yine de (süre sınırıyla) başlamalı
        with mock.patch.object(mw, "FRAME_GATE_MAX_MS", 150):
            self.win.load_path(self.files[0])
            self.assertTrue(_pump(lambda: not getattr(self.win, "_frame_pending", False), timeout=5.0))
        self.assertTrue(_pump(lambda: self.win._folder_ready))


class NoRedundantLiveResolutionTest(_WindowCase):
    def test_photo_without_partner_is_resolved_once(self) -> None:
        """Eşi olmayan fotoğrafta UUID araması bitince Live çözümlemesi ikinci kez çalışıyordu."""
        from app.ui import main_window as mw

        made: list[str] = []

        class CountLive(mw.LiveResolveWorker):
            def __init__(self, path, *a, **k):
                made.append(Path(path).name)
                super().__init__(path, *a, **k)

        with mock.patch.object(mw, "LiveResolveWorker", CountLive):
            self.win.load_path(self.files[1])
            self.assertTrue(_pump(lambda: bool(made) and not getattr(self.win, "_frame_pending", False)))
            self.win.uuid_cache._pool.waitForDone(5000)
            self.win.pool.waitForDone(5000)
            _pump(lambda: False, timeout=0.3)
        self.assertEqual(made, [self.files[1].name])
        self.assertIsNone(self.win.current_match)


class GuiThreadAvoidsFileRoundTripsTest(_WindowCase):
    def test_open_does_not_resolve_paths_on_gui_thread(self) -> None:
        """`Path.resolve()` Windows'ta dosyayı açar; ağ/WSL yolunda GUI thread'ini bekletiyordu."""
        orig = Path.resolve
        on_gui: list[str] = []

        def spy(self_path, strict=False):
            if threading.current_thread() is threading.main_thread():
                on_gui.append(str(self_path))
            return orig(self_path, strict)

        with mock.patch.object(Path, "resolve", spy):
            self.win.load_path(self.files[3])
            self.assertTrue(_pump(lambda: self.shown() and self.win._folder_ready and not getattr(self.win, "_frame_pending", False)))
            self.win.pool.waitForDone(5000)
            _pump(lambda: False, timeout=0.3)
        self.assertEqual(on_gui, [], "açılış GUI thread'inde resolve() çağırmamalı")

    def test_cache_keys_need_no_filesystem_access(self) -> None:
        from app.core import image_loader

        unc = r"\\wsl.localhost\Ubuntu\home\u\a.png"
        with mock.patch("os.stat", side_effect=AssertionError("dosya sistemine dokunuldu")):
            self.assertEqual(image_loader.path_key(unc), image_loader.path_key(unc.upper()))
            self.assertTrue(image_loader.same_path_text(unc, Path(unc)))
            self.assertFalse(image_loader.has_cached_entry(unc))


class ExifRefineWaitsForGateTest(unittest.TestCase):
    def test_quick_list_first_refined_list_after_gate(self) -> None:
        from PIL import Image

        from app.core.folder_scan import FolderScanWorker

        self.app = QApplication.instance() or QApplication([])  # referans tutulmalı (yoksa hemen silinir)
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name, day in (("a.jpg", 3), ("b.jpg", 1)):
                im = Image.new("RGB", (8, 8))
                ex = Image.Exif()
                ex[36867] = f"2024:01:0{day} 10:00:00"
                im.save(d / name, exif=ex)
                os.utime(d / name, (1_700_000_000, 1_700_000_000))
            gate = threading.Event()
            got: list[list[str]] = []
            w = FolderScanWorker(str(d), "a.jpg", 1, "date_asc", progressive=True, refine_gate=gate)
            w.setAutoDelete(False)
            w.signals.ready.connect(lambda _f, paths, _i, _t: got.append([Path(p).name for p in paths]))
            t = threading.Thread(target=w.run)
            t.start()
            self.assertTrue(_pump(lambda: len(got) == 1, timeout=5.0))
            time.sleep(0.2)
            QCoreApplication.processEvents()
            self.assertEqual(len(got), 1, "EXIF inceltmesi kapı açılmadan başlamamalı")
            gate.set()
            t.join(5)
            _pump(lambda: len(got) == 2, timeout=5.0)
            self.assertEqual(got, [["a.jpg", "b.jpg"], ["b.jpg", "a.jpg"]])


class CanvasFrameShownTest(unittest.TestCase):
    def test_emitted_once_after_paint_and_safe_after_delete(self) -> None:
        from PyQt6.QtGui import QImage

        from app.ui.canvas import PhotoCanvas

        self.app = QApplication.instance() or QApplication([])  # referans tutulmalı (yoksa hemen silinir)
        canvas = PhotoCanvas()
        canvas.resize(200, 150)
        canvas.show()
        hits: list[int] = []
        canvas.frame_shown.connect(lambda: hits.append(1))
        img = QImage(40, 30, QImage.Format.Format_RGB32)
        img.fill(0xFF336699)
        canvas.set_qimage(img)
        self.assertTrue(_pump(lambda: hits == [1], timeout=3.0))
        canvas.viewport().update()
        _pump(lambda: False, timeout=0.2)
        self.assertEqual(hits, [1], "aynı görüntünün sonraki boyamaları yeniden bildirilmemeli")
        # Bildirim beklerken tuval silinirse çökme olmamalı (bildirim tuvale ait zamanlayıcıdadır)
        from PyQt6 import sip

        canvas.set_qimage(img)
        canvas.repaint()
        self.assertTrue(canvas._frame_timer.isActive())
        sip.delete(canvas)
        _pump(lambda: False, timeout=0.2)


class StartupImportsTest(unittest.TestCase):
    def test_photo_open_path_does_not_import_video_or_scientific_stacks(self) -> None:
        """cv2 (+numpy), rawpy, imagecodecs açılışta yükleniyordu (fotoğraf açarken hiç gerekmezken)."""
        code = (
            "import os, sys, json\n"
            "os.environ['QT_QPA_PLATFORM'] = 'offscreen'\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "import livePhotoViewer\n"
            "assert livePhotoViewer._deps_ok()\n"
            "import app.main\n"
            "from PyQt6.QtCore import QSettings\n"
            "from PyQt6.QtWidgets import QApplication\n"
            "QSettings.setDefaultFormat(QSettings.Format.IniFormat)\n"  # kullanıcının kayıt defterine yazma
            "QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, os.environ['LUMINA_SANDBOX'])\n"
            "app = QApplication([])\n"
            "from app.ui.main_window import MainWindow\n"
            "w = MainWindow()\n"
            "print(json.dumps({m: m in sys.modules for m in ('cv2', 'numpy', 'rawpy', 'imagecodecs', 'app.core.hdr', 'app.ui.updates')}))\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, APPDATA=tmp, LOCALAPPDATA=tmp, LUMINA_SANDBOX=tmp)
            out = subprocess.run(
                [sys.executable, "-B", "-c", code], cwd=str(ROOT), env=env,
                capture_output=True, text=True, timeout=120,
            )
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        loaded = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(loaded, {k: False for k in loaded}, f"açılışta yüklenmemeli: {loaded}")

    def test_jxr_extensions_match_hdr_module(self) -> None:
        from app.core import hdr, image_loader

        self.assertEqual(image_loader.JXR_EXTS, hdr.JXR_EXTS)


class BackgroundServicesAfterFirstFrameTest(_WindowCase):
    def test_window_binds_update_notices_once_even_if_service_comes_late(self) -> None:
        """Güncelleme kurulumu ilk kareden sonraya alındı; pencere yine de (bir kez) bağlanmalı."""
        from app import main as app_main
        from app.ui.updates import UpdateService

        app = QApplication.instance()
        old = getattr(app, "_lumina_updates", None)
        self.addCleanup(lambda: setattr(app, "_lumina_updates", old))
        app._lumina_updates = None
        w = self.win
        w._bind_notice()  # pencerenin kendi denemesi: hizmet henüz yok
        self.assertFalse(getattr(w, "_notice_bound", False))
        service = UpdateService()
        self.addCleanup(service.deleteLater)

        def fake_attach(a):
            a._lumina_updates = service
            return service

        with mock.patch("app.ui.updates.attach_updates", fake_attach), \
             mock.patch("app.ui.session.windows", [w]):
            app_main._attach_background_services(app)
        self.assertTrue(getattr(w, "_notice_bound", False))
        w._bind_notice()  # ikinci çağrı yeniden bağlamamalı (bildirim iki kez çıkmasın)
        self.assertEqual(service.receivers(service.notice_received), 1)


class SpecPayloadTest(unittest.TestCase):
    """Onefile exe her açılışta tüm içeriğini açar; kullanılmayan büyük dosyalar pakete girmemeli."""

    def test_unused_payload_is_excluded(self) -> None:
        text = (ROOT / "lumina.spec").read_text(encoding="utf-8")
        for needle in ('"opengl32sw.dll"', '"qt6pdf.dll"', '"rawpy.enhance"', '"skimage"', '"win32ui"',
                       '"pillow_avif",', 'cv2\\\\data\\\\', '("_tr.qm", "_en.qm")'):
            self.assertIn(needle, text)
        self.assertNotIn('"pillow_avif", "rawpy"', text)


if __name__ == "__main__":
    unittest.main()
