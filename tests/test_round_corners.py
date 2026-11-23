"""Windows 10'da DWM pencere köşesini yuvarlamaz: Lumina köşeleri maskeyle yuvarlar (diğer dört uygulama gibi).

Eski durum: yalnızca Windows 11'in DWMWA_WINDOW_CORNER_PREFERENCE ayarı kullanılıyordu; kullanıcının Windows 10'unda
pencere köşeleri sivri kalıyordu. Saydam pencere kullanılamaz (mpv videoyu yerel alt pencereye çizer), maske kullanılır.
Büyütülmüş/tam ekranda maske kalkar. Ekran açmaz (offscreen).
"""

from __future__ import annotations

import os
import pathlib
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


class RoundCornersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def make(self):
        from app.ui.frameless import FramelessWindow

        win = FramelessWindow()
        self.addCleanup(win.deleteLater)
        win.resize(400, 300)
        return win

    def test_windows_10_gets_a_rounded_mask(self):
        from app.ui import frameless

        with mock.patch.object(frameless.FramelessWindow, "needs_mask_rounding", staticmethod(lambda: True)):
            win = self.make()
            win._apply_round_mask()
            mask = win.mask()
            self.assertFalse(mask.isEmpty())
            self.assertFalse(mask.contains(QPoint(0, 0)), "sol üst köşe kesildi")
            self.assertFalse(mask.contains(QPoint(399, 299)), "sağ alt köşe kesildi")
            self.assertTrue(mask.contains(QPoint(200, 0)), "üst kenarın ortası kalır")
            self.assertTrue(mask.contains(QPoint(200, 150)))

    def test_maximized_or_fullscreen_windows_have_no_mask(self):
        from app.ui import frameless

        with mock.patch.object(frameless.FramelessWindow, "needs_mask_rounding", staticmethod(lambda: True)):
            win = self.make()
            win._apply_round_mask()
            self.assertFalse(win.mask().isEmpty())
            with mock.patch.object(win, "isMaximized", return_value=True):
                win._apply_round_mask()
            self.assertTrue(win.mask().isEmpty(), "büyütülmüş pencerede köşe kesilmez")

    def test_windows_11_keeps_the_native_dwm_rounding(self):
        from app.ui import frameless

        with mock.patch.object(frameless.FramelessWindow, "needs_mask_rounding", staticmethod(lambda: False)):
            win = self.make()
            win._apply_round_mask()
            self.assertTrue(win.mask().isEmpty())

    def test_build_number_decides(self):
        from app.ui.frameless import FramelessWindow

        if sys.platform != "win32":
            self.assertFalse(FramelessWindow.needs_mask_rounding())
            return
        for build, expected in ((19045, True), (22000, False), (26100, False)):
            fake = mock.Mock(build=build)
            with mock.patch.object(sys, "getwindowsversion", return_value=fake):
                self.assertEqual(FramelessWindow.needs_mask_rounding(), expected, build)


if __name__ == "__main__":
    unittest.main()
