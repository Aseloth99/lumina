"""İlerleme çubuğu yeni değere zıplamadan, kısa bir animasyonla kayar; dışarıdan QProgressBar gibi davranır."""

from __future__ import annotations

import os
import pathlib
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtWidgets import QApplication

from app.ui import smooth_progress
from app.ui.smooth_progress import SmoothProgressBar


def _pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)


class SmoothProgressTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls._orig = smooth_progress._system_animations_enabled
        smooth_progress._system_animations_enabled = lambda: True  # makinenin ayarından bağımsız

    @classmethod
    def tearDownClass(cls):
        smooth_progress._system_animations_enabled = cls._orig

    def bar(self, lo=0, hi=100):
        bar = SmoothProgressBar()
        bar.setRange(lo, hi)
        bar.resize(400, 18)
        bar.show()
        self.app.processEvents()
        self.addCleanup(bar.deleteLater)
        self.addCleanup(bar.close)
        return bar

    def test_moves_gradually_and_reports_the_logical_value(self):
        bar = self.bar()
        bar.setValue(60)
        self.assertEqual(bar.value(), 60, "value() hemen hedefi döndürmeli (kod ve testler buna bakar)")
        self.assertTrue(bar.is_animating())
        _pump(self.app, smooth_progress.DURATION_MS / 3000)
        mid = bar.displayed_fraction()
        self.assertGreater(mid, 0.0)
        self.assertLess(mid, 0.6, "çubuk bir anda hedefe zıplamamalı")
        _pump(self.app, smooth_progress.DURATION_MS / 1000 + 0.2)
        self.assertFalse(bar.is_animating())
        self.assertAlmostEqual(bar.displayed_fraction(), 0.6, 4)

    def test_coarse_range_still_moves_finer_than_one_step(self):
        bar = self.bar(0, 5)
        bar.setValue(1)
        _pump(self.app, smooth_progress.DURATION_MS / 3000)
        self.assertTrue(0.0 < bar.displayed_fraction() < 0.2)
        self.assertEqual(bar.maximum(), 5)
        self.assertEqual(bar.minimum(), 0)

    def test_going_back_is_instant(self):
        bar = self.bar()
        bar.setValue(80)
        bar.finish_animation()
        bar.setValue(0)
        self.assertFalse(bar.is_animating())
        self.assertEqual(bar.displayed_fraction(), 0.0)
        bar.setValue(30)
        bar.reset()
        self.assertEqual(bar.value(), 0)
        self.assertEqual(bar.displayed_fraction(), 0.0)

    def test_new_target_mid_animation_continues_from_where_it_is(self):
        bar = self.bar()
        bar.setValue(40)
        _pump(self.app, 0.08)
        before = bar.displayed_fraction()
        bar.setValue(90)
        self.assertGreaterEqual(bar.displayed_fraction(), before)
        bar.finish_animation()
        self.assertAlmostEqual(bar.displayed_fraction(), 0.9, 4)

    def test_hidden_bar_and_disabled_animations_set_directly(self):
        bar = SmoothProgressBar()
        self.addCleanup(bar.deleteLater)
        bar.setValue(50)
        self.assertFalse(bar.is_animating())
        self.assertAlmostEqual(bar.displayed_fraction(), 0.5, 4)
        bar = self.bar()
        smooth_progress._system_animations_enabled = lambda: False
        try:
            bar.setValue(70)
        finally:
            smooth_progress._system_animations_enabled = lambda: True
        self.assertFalse(bar.is_animating())
        self.assertAlmostEqual(bar.displayed_fraction(), 0.7, 4)

    def test_busy_mode_and_text(self):
        bar = self.bar()
        bar.setRange(0, 0)
        self.assertEqual((bar.minimum(), bar.maximum()), (0, 0))
        self.assertEqual(bar.text(), "")
        bar.setRange(0, 100)
        bar.setValue(42)
        bar.finish_animation()
        bar.setFormat("%p%")  # varsayılan biçim dile bağlı (Türkçede "%42")
        self.assertEqual(bar.text(), "42%")
        bar.setFormat("%v / %m")
        self.assertEqual(bar.text(), "42 / 100")
        bar.setMaximum(200)
        self.assertEqual(bar.maximum(), 200)
        self.assertEqual(bar.value(), 42)
        self.assertAlmostEqual(bar.displayed_fraction(), 0.21, 4)


if __name__ == "__main__":
    unittest.main()
