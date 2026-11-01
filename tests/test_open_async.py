"""Tek dosya açılışı: görüntü klasör taramasını beklemez; bayat sonuçlar atılır; liste yenilenince konum korunur.

Hepsi offscreen, ağ yok; gerçek taramaya yavaş bir sahte işçi takılır.
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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.core.folder_scan import FolderScanWorker  # noqa: E402
from app.core.matcher import index_after_change, sidecar_badge_names  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from _synthetic import sample_jpg  # noqa: E402

SAMPLE = sample_jpg()


def _paths(*names: str) -> list[Path]:
    return [Path("/x") / n for n in names]


class IndexAfterChangeTest(unittest.TestCase):
    OLD = _paths("a", "b", "c", "d", "e")

    def test_current_still_present_is_followed_by_name(self) -> None:
        new = _paths("0", "a", "b", "c", "d", "e")  # başa dosya eklendi
        self.assertEqual(index_after_change(self.OLD, 2, new), 3)
        self.assertEqual(index_after_change(self.OLD, 2, _paths("a", "c", "e")), 1)  # öncekiler silindi

    def test_deleted_current_shows_next_at_same_position(self) -> None:
        self.assertEqual(index_after_change(self.OLD, 2, _paths("a", "b", "d", "e")), 2)

    def test_deleted_last_shows_previous(self) -> None:
        self.assertEqual(index_after_change(self.OLD, 4, _paths("a", "b", "c", "d")), 3)

    def test_deleted_first_shows_new_first(self) -> None:
        self.assertEqual(index_after_change(self.OLD, 0, _paths("b", "c", "d", "e")), 0)

    def test_several_deleted_at_once(self) -> None:
        self.assertEqual(index_after_change(self.OLD, 2, _paths("a", "e")), 1)  # c,d,b gitti: sonraki sağ kalan e
        self.assertEqual(index_after_change(self.OLD, 3, _paths("a", "b")), 1)  # d,e gitti: önceki sağ kalan b
        self.assertEqual(index_after_change(self.OLD, 2, _paths("x", "y")), 1)  # hiçbiri kalmadı: konum sıkıştırılır

    def test_empty_and_invalid(self) -> None:
        self.assertEqual(index_after_change(self.OLD, 2, []), -1)
        self.assertEqual(index_after_change([], -1, _paths("a")), 0)

    def test_case_insensitive_names(self) -> None:
        self.assertEqual(index_after_change(_paths("A.JPG", "B.JPG"), 1, _paths("a.jpg", "b.jpg")), 1)


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._ini = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._ini.name)
        cls.app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.sort_by = "name_asc"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.deleteLater()
        cls._ini.cleanup()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def make(self, folder: Path, n: int) -> list[Path]:
        folder.mkdir(exist_ok=True)
        out = []
        for i in range(n):
            p = folder / f"img_{i:03d}.jpg"
            shutil.copy(SAMPLE, p)
            out.append(p)
        return out

    def pump(self, cond, timeout: float = 5.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if cond():
                return True
            time.sleep(0.005)
        return False

    def remove(self, path: Path) -> None:
        """Windows'ta arka plan işçisi dosyayı kısa süre tutabilir: biraz bekleyip yeniden dene."""
        end = time.monotonic() + 5
        while True:
            try:
                path.unlink()
                return
            except PermissionError:
                if time.monotonic() > end:
                    raise
                self.settle(0.05)

    def settle(self, seconds: float = 0.4) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)


class OpenDoesNotWaitForScanTest(_Base):
    def test_first_display_before_scan_finishes_then_navigation_works(self) -> None:
        from app.ui import main_window as mw

        files = self.make(self.dir, 6)
        gate = threading.Event()
        scan_done = threading.Event()

        class SlowScan(FolderScanWorker):
            def run(self) -> None:
                gate.wait(10)
                super().run()
                scan_done.set()

        shown: list[bool] = []
        win = self.win
        orig = win.canvas.set_qimage

        def spy(img):
            shown.append(scan_done.is_set())
            return orig(img)

        win.canvas.set_qimage = spy
        self.addCleanup(lambda: setattr(win.canvas, "set_qimage", orig))
        old = mw.FolderScanWorker
        mw.FolderScanWorker = SlowScan
        self.addCleanup(lambda: setattr(mw, "FolderScanWorker", old))

        win.load_path(files[2])
        self.assertTrue(self.pump(lambda: bool(shown)), "görüntü gösterilmedi")
        self.assertEqual(shown, [False], "görüntü tarama bitmeden gösterilmeli")
        self.assertFalse(win._folder_ready)
        self.assertEqual(win.image_files, [files[2]])

        gate.set()
        self.assertTrue(self.pump(lambda: win._folder_ready))
        self.assertEqual(len(win.image_files), 6)
        self.assertEqual(win.current_file().name, files[2].name, "geçerli dosya kararlı kalmalı")
        win.next_image()
        self.assertEqual(win.current_file().name, files[3].name)
        win.prev_image()
        win.prev_image()
        self.assertEqual(win.current_file().name, files[1].name)

    def test_stale_scan_result_is_discarded(self) -> None:
        from app.ui import main_window as mw

        a = self.make(self.dir / "A", 3)
        b = self.make(self.dir / "B", 4)
        gate = threading.Event()

        class SlowScan(FolderScanWorker):
            def run(self) -> None:
                if Path(self.folder).name == "A":
                    gate.wait(10)
                super().run()

        old = mw.FolderScanWorker
        mw.FolderScanWorker = SlowScan
        self.addCleanup(lambda: setattr(mw, "FolderScanWorker", old))
        win = self.win
        win.load_path(a[0])
        win.load_path(b[1])
        self.assertTrue(self.pump(lambda: win._folder_ready))
        self.assertEqual({p.parent for p in win.image_files}, {b[0].parent})
        gate.set()
        # eski A taraması bitse bile B listesi bozulmamalı
        self.settle(0.5)
        self.assertEqual({p.parent for p in win.image_files}, {b[0].parent})
        self.assertEqual(win.current_file().name, b[1].name)
        win._on_folder_listed(str(a[0].parent), [str(p) for p in a], 0, win._scan_token - 1)
        self.assertEqual(len(win.image_files), 4)

    def test_worker_skips_when_token_no_longer_current(self) -> None:
        self.make(self.dir, 3)
        got: list = []
        w = FolderScanWorker(str(self.dir), "", 1, "name_asc", is_current=lambda: False)
        w.signals.ready.connect(lambda *a: got.append(a))
        w.run()
        self.assertEqual(got, [])


class ProgressiveScanTest(_Base):
    def test_quick_list_then_exif_refined_list(self) -> None:
        from PIL import Image

        for name, day in (("a.jpg", 3), ("b.jpg", 1), ("c.jpg", 2)):
            im = Image.new("RGB", (8, 8))
            ex = Image.Exif()
            ex[36867] = f"2024:01:0{day} 10:00:00"
            im.save(self.dir / name, exif=ex)
            os.utime(self.dir / name, (1_700_000_000, 1_700_000_000))  # mtime hepsinde aynı
        got: list[list[str]] = []
        w = FolderScanWorker(str(self.dir), "b.jpg", 1, "date_asc", progressive=True)
        w.signals.ready.connect(lambda _f, paths, idx, tok: got.append([Path(p).name for p in paths]))
        w.run()
        self.app.processEvents()
        self.assertEqual(got[0], ["a.jpg", "b.jpg", "c.jpg"])  # hızlı: ad sırası
        self.assertEqual(got[-1], ["b.jpg", "c.jpg", "a.jpg"])  # EXIF tarihine göre

    def test_single_emit_when_exif_does_not_change_order(self) -> None:
        self.make(self.dir, 3)
        got: list = []
        w = FolderScanWorker(str(self.dir), "", 1, "name_asc", progressive=True)
        w.signals.ready.connect(lambda *a: got.append(a))
        w.run()
        self.assertEqual(len(got), 1)


class ListRefreshKeepsPositionTest(_Base):
    def open_at(self, n: int, i: int) -> list[Path]:
        files = self.make(self.dir, n)
        win = self.win
        win.load_path(files[i])
        self.assertTrue(self.pump(lambda: win._folder_ready and len(win.image_files) == n))
        self.assertEqual(win.current_idx, i)
        return files

    def refresh(self) -> None:
        self.win._rescan_active_folder()
        self.settle(0.5)

    def test_external_delete_of_shown_file_keeps_position(self) -> None:
        files = self.open_at(10, 4)
        self.remove(files[4])
        self.refresh()
        self.assertEqual(len(self.win.image_files), 9)
        self.assertEqual(self.win.current_idx, 4)
        self.assertEqual(self.win.current_file().name, files[5].name)
        self.assertEqual(self.win.lbl_counter.text().replace(" ", ""), "5/9")

    def test_external_delete_of_last_shows_previous(self) -> None:
        files = self.open_at(5, 4)
        self.remove(files[4])
        self.refresh()
        self.assertEqual(self.win.current_file().name, files[3].name)
        self.assertEqual(self.win.current_idx, 3)

    def test_external_delete_of_first_shows_new_first(self) -> None:
        files = self.open_at(5, 0)
        self.remove(files[0])
        self.refresh()
        self.assertEqual(self.win.current_file().name, files[1].name)
        self.assertEqual(self.win.current_idx, 0)

    def test_several_deleted_including_current(self) -> None:
        files = self.open_at(8, 3)
        for i in (1, 3, 4):
            self.remove(files[i])
        self.refresh()
        self.assertEqual(self.win.current_file().name, files[5].name)

    def test_other_files_changing_keeps_shown_file(self) -> None:
        files = self.open_at(6, 3)
        self.remove(files[0])
        shutil.copy(SAMPLE, self.dir / "img_000a.jpg")
        self.refresh()
        self.assertEqual(self.win.current_file().name, files[3].name)

    def test_everything_deleted_shows_empty_state(self) -> None:
        files = self.open_at(3, 1)
        for f in files:
            self.remove(f)
        self.refresh()
        self.assertEqual(self.win.image_files, [])
        self.assertEqual(self.win.current_idx, -1)

    def test_in_app_drop_matches_external_behaviour(self) -> None:
        files = self.open_at(6, 2)
        self.win._drop_from_listing([files[2]])
        self.assertEqual(self.win.current_idx, 2)
        self.assertEqual(self.win.current_file().name, files[3].name)
        self.win.current_idx = len(self.win.image_files) - 1
        last = self.win.current_file()
        self.win._drop_from_listing([last])
        self.assertEqual(self.win.current_file().name, files[4].name)


class SidecarBadgeTest(_Base):
    def test_badge_names_match_paired_sidecars(self) -> None:
        from app.core.matcher import paired_sidecars

        a = self.dir / "IMG_1.jpg"
        b = self.dir / "IMG_2.jpg"
        c = self.dir / "IMG_3.heic"
        for p in (a, b, c):
            p.write_bytes(b"x")
        (self.dir / "img_1.MOV").write_bytes(b"v")
        (self.dir / "IMG_3_HEVC.mp4").write_bytes(b"v")
        live = sidecar_badge_names([a, b, c])
        self.assertEqual(live, {"img_1.jpg", "img_3.heic"})
        for p in (a, b, c):
            self.assertEqual(bool(paired_sidecars(p)), p.name.lower() in live, p.name)

    def test_filmstrip_badge_arrives_asynchronously(self) -> None:
        from PyQt6.QtCore import Qt

        from app.ui.filmstrip import Filmstrip

        files = self.make(self.dir, 4)
        (self.dir / "img_001.mov").write_bytes(b"v")
        strip = Filmstrip()
        self.addCleanup(strip.deleteLater)
        strip.resize(400, 50)
        strip.set_window(files, 0)
        self.assertEqual(strip.item(1).data(Qt.ItemDataRole.UserRole + 1), "image")  # eşzamanlı yol dosya sorgulamaz
        self.assertTrue(self.pump(lambda: strip.item(1).data(Qt.ItemDataRole.UserRole + 1) == "live"))
        self.assertEqual(strip.item(0).data(Qt.ItemDataRole.UserRole + 1), "image")
        strip.shutdown()


if __name__ == "__main__":
    unittest.main()
