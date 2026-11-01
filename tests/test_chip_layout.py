"""Dosya adı kapsülü: boşken görünmez, dar pencerede marka/pencere düğmeleriyle çakışmaz, uzun ad kısalır.

Ayrıca lumina.spec: imagecodecs._shared_cython gizli içe aktarma (yoksa exe'de JXR/JXL/AVIF çözülemez).
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
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QCoreApplication, QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

LONG_NAME = "Squadron 42 - Star Citizen Screenshot 2024.04.01 - 17.19.22.25.jpg"


class ChipLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._tmp.name)
        cls.app = QApplication.instance() or QApplication([])
        sys.path.insert(0, str(ROOT / "tests"))
        from _synthetic import sample_jpg

        sample = sample_jpg()
        cls.long_file = pathlib.Path(cls._tmp.name) / LONG_NAME
        shutil.copy(sample, cls.long_file)
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.resize(1100, 600)
        cls.win.show()
        cls._pump()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.close()
        cls.win.deleteLater()
        cls._tmp.cleanup()

    @staticmethod
    def _pump(n: int = 20) -> None:
        for _ in range(n):
            QCoreApplication.processEvents()

    def test_a_empty_state_has_no_chip(self) -> None:
        self.assertFalse(self.win.file_chip.isVisible())

    def test_b_long_name_never_overlaps_neighbours(self) -> None:
        w = self.win
        w.load_path(self.long_file)
        end = time.time() + 5
        while time.time() < end and not w.lbl_filename.text():
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.assertTrue(w.file_chip.isVisible())
        for width in (1100, 900, 760, 700, 640):
            w.resize(width, 600)
            self._pump()
            brand, chip, nav = w.brand_bar.geometry(), w.file_chip.geometry(), w.nav_bar.geometry()
            self.assertLess(brand.right(), chip.left(), f"marka ile çakışıyor ({width}px)")
            self.assertLess(chip.right(), nav.left(), f"pencere düğmeleriyle çakışıyor ({width}px)")

    def test_c_name_is_elided_in_the_middle_keeping_extension(self) -> None:
        w = self.win
        w.resize(640, 600)
        self._pump()
        text = w.lbl_filename.text()
        self.assertTrue(text.endswith(".jpg"), text)
        self.assertLess(len(text), len(LONG_NAME))
        self.assertIn("…", text)

    def test_d_chip_hides_again_when_nothing_is_loaded(self) -> None:
        w = self.win
        w.image_files = []
        w.current_idx = -1
        w._sync_chrome()
        self._pump()
        self.assertFalse(w.file_chip.isVisible())
        self.assertEqual(w.lbl_filename.text(), "")


class SpecTest(unittest.TestCase):
    def test_spec_collects_imagecodecs_shared_cython(self) -> None:
        text = (ROOT / "lumina.spec").read_text(encoding="utf-8")
        self.assertIn('"imagecodecs._shared_cython"', text)


if __name__ == "__main__":
    unittest.main()
