"""Tam ekran overlay modu: pencerenin GERÇEK durumundan beslenmeli.

Eski hata: kapsülleri video HWND'sinin üstüne taşıyan kod yalnızca toggle_fullscreen
içindeydi. F5 slayt gösterisi gibi showFullScreen() doğrudan çağıran yollarda alt bar
görünmez kalıyordu. Bu test, o yolu (doğrudan showFullScreen/showNormal) simüle eder.

Çalıştırma (proje kökünden):  python -m unittest discover -s tests -v
Ekran açmaz (offscreen) ve kullanıcının gerçek ayarlarına dokunmaz (geçici INI).
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QCoreApplication, QSettings, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


class FullscreenOverlayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        # Gerçek kayıt defteri ayarlarına yazmamak için INI'ye yönlendir.
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._tmp.name)
        cls.app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.resize(1000, 700)
        cls.win.show()
        cls._pump()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.hide()
        cls.win.deleteLater()
        cls._pump()
        cls._tmp.cleanup()

    @classmethod
    def _pump(cls) -> None:
        for _ in range(5):
            QCoreApplication.processEvents()

    def _assert_windowed(self) -> None:
        w = self.win
        self.assertFalse(w.isFullScreen())
        self.assertFalse(getattr(w, "_fs_mode_active", False))
        for host in (w._dock_host, w._brand_host, w._nav_host, w._chip_host):
            self.assertIsNone(host.child_widget(), "pencere modunda host boş olmalı")
        self.assertIs(w.dock_wrap.window(), w, "alt bar ana pencereye iade edilmeli")
        self.assertIs(w.brand_bar.window(), w)
        self.assertIs(w.nav_bar.window(), w)

    def _assert_overlay(self) -> None:
        w = self.win
        self.assertTrue(w.isFullScreen())
        self.assertTrue(getattr(w, "_fs_mode_active", False))
        self.assertIs(w._dock_host.child_widget(), w.dock_wrap, "alt bar overlay'e taşınmalı")
        self.assertIs(w._brand_host.child_widget(), w.brand_bar)
        self.assertIs(w._nav_host.child_widget(), w.nav_bar)

    def test_direct_show_fullscreen_enters_overlay_mode(self) -> None:
        """F5 slayt gösterisi yolu: toggle_fullscreen çağrılmadan tam ekran."""
        self._assert_windowed()
        self.win.showFullScreen()
        self._pump()
        self._assert_overlay()
        self.win.showNormal()
        self._pump()
        self._assert_windowed()

    def test_toggle_fullscreen_roundtrip(self) -> None:
        self._assert_windowed()
        self.win.toggle_fullscreen()
        self._pump()
        self._assert_overlay()
        self.win.toggle_fullscreen()
        self._pump()
        self._assert_windowed()

    def test_repeated_cycles_stay_consistent(self) -> None:
        for i in range(10):
            if i % 2:
                self.win.toggle_fullscreen()
            else:
                self.win.showFullScreen()
            self._pump()
            self._assert_overlay()
            if i % 3:
                self.win.showNormal()
            else:
                self.win.toggle_fullscreen()
            self._pump()
            self._assert_windowed()

    def test_external_exit_restores_widgets(self) -> None:
        """Dışarıdan (Win+Aşağı gibi) çıkış: kapsüller masaüstünde yüzer kalmamalı."""
        self.win.showFullScreen()
        self._pump()
        self._assert_overlay()
        self.win.setWindowState(self.win.windowState() & ~Qt.WindowState.WindowFullScreen)
        self._pump()
        self._assert_windowed()


    def test_fullscreen_backdrop_is_black_in_light_theme(self) -> None:
        """Açık temada tam ekran film izlerken yanlar beyaz kalmamalı; pencereli moda dönünce tema rengi gelir."""
        w = self.win
        self.win.showFullScreen()
        self._pump()
        self.assertIn("#000000", w.container.styleSheet())
        self.assertEqual(w.player._bg_color, "#FF000000")
        w.apply_theme()  # tema yenilense de siyah kalmalı
        self.assertEqual(w.player._bg_color, "#FF000000")
        w.showNormal()
        self._pump()
        self.assertEqual(w.container.styleSheet(), "")
        self.assertNotEqual(w.player._bg_color, "#FF000000")

    def test_double_click_does_not_toggle_playback(self) -> None:
        """Tek tık duraklat/oynat'ı beklemeden uygular (gecikme olmasın); çift tıkta ilk tık geri alınır,
        yani çift tıkla tam ekrana girerken oynatma durumu net olarak değişmez."""
        w = self.win
        calls: list[int] = []
        w.toggle_live_playback = lambda: calls.append(1)  # type: ignore[method-assign]
        w._playing = True
        try:
            self._assert_windowed()
            w.player.widget.clicked.emit()
            self.assertEqual(calls, [1], "tek tık gecikmeden uygulanmalı")
            w.player.widget.double_clicked.emit()
            QTest.qWait(450)
            self.assertEqual(len(calls), 2, "çift tıkta ilk tıkın etkisi geri alınmalı")
            self.assertTrue(w.isFullScreen())
            w.showNormal()
            self._pump()
            calls.clear()
            # Tek tık: beklemeden çalışmalı
            w.player.widget.clicked.emit()
            self.assertEqual(calls, [1])
            QTest.qWait(450)
            self.assertEqual(calls, [1])
        finally:
            del w.toggle_live_playback
            w._playing = False
            if w.isFullScreen():
                w.showNormal()
            self._pump()


if __name__ == "__main__":
    unittest.main()
