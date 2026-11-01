"""Videoda/fotoğrafta çift tık TAM EKRAN yapar; pencereyi büyütmez ve oynatmayı duraklatmış bırakmaz.

Eski hata (gerçek Windows + gerçek mpv ile yeniden üretildi): `FramelessWindow.eventFilter` medya alanını "pencere sürükleme alanı"
sayıp çift tıkı BAŞLIK ÇUBUĞU çift tıkı gibi yorumluyor, pencereyi büyütüp (toggle_maximize) olayı yutuyordu. Medya yüzeyinin
`double_clicked` sinyali hiç yayınlanmıyor, ilk tıkın (anında) duraklatması geri alınmıyordu: video duraklamış kalıyor, pencere büyüyordu.
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

from PyQt6.QtCore import QCoreApplication, QEvent, QPointF, QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


def pump(n: int = 5) -> None:
    for _ in range(n):
        QCoreApplication.processEvents()


class DoubleClickOnMediaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._tmp.name)
        cls.app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.resize(1000, 700)
        cls.win.show()
        pump()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.hide()
        cls.win.deleteLater()
        pump()
        cls._tmp.cleanup()

    def setUp(self) -> None:
        w = self.win
        if w.isFullScreen():
            w.showNormal()
        if w.isMaximized():
            w.showNormal()
        pump()

    def _send_double_click(self, widget) -> None:
        pos = QPointF(widget.width() * 0.25, widget.height() * 0.3)
        gpos = QPointF(widget.mapToGlobal(pos.toPoint()))
        left = Qt.MouseButton.LeftButton
        for etype, button, buttons in (
            (QEvent.Type.MouseButtonPress, left, left),
            (QEvent.Type.MouseButtonRelease, left, Qt.MouseButton.NoButton),
            (QEvent.Type.MouseButtonDblClick, left, left),
            (QEvent.Type.MouseButtonRelease, left, Qt.MouseButton.NoButton),
        ):
            QApplication.sendEvent(widget, QMouseEvent(etype, pos, gpos, button, buttons, Qt.KeyboardModifier.NoModifier))
            pump(2)

    def _check(self, widget, label: str, target=None) -> None:
        """`widget` sinyali yayınlar; olaylar `target`'a (verilmezse widget'a) gider (QGraphicsView'da gerçek hedef viewport'tur)."""
        w = self.win
        emitted: list[int] = []
        widget.double_clicked.connect(lambda: emitted.append(1))
        try:
            self.assertFalse(w.isMaximized())
            self._send_double_click(target or widget)
        finally:
            widget.double_clicked.disconnect()
        self.assertEqual(len(emitted), 1, f"{label}: çift tık medya yüzeyine ulaşmalı (double_clicked yayınlanmalı)")
        self.assertFalse(w.isMaximized(), f"{label}: çift tık pencereyi BÜYÜTMEMELİ")

    def test_double_click_on_the_video_reaches_the_surface_and_does_not_maximize(self) -> None:
        w = self.win
        w.stage.stack.setCurrentWidget(w.player.widget)
        pump()
        self._check(w.player.widget, "video")

    def test_double_click_on_the_photo_reaches_the_canvas_and_does_not_maximize(self) -> None:
        w = self.win
        w.stage.stack.setCurrentWidget(w.canvas)
        pump()
        self._check(w.canvas, "fotoğraf", w.canvas.viewport())

    def test_double_click_on_the_title_area_still_maximizes(self) -> None:
        """Başlık alanında (gezinti çubuğu) çift tık eskisi gibi pencereyi büyütür/geri alır: istisna yalnızca medya yüzeyi için."""
        w = self.win
        w.stage.stack.setCurrentWidget(w.player.widget)
        pump()
        pos = QPointF(w.nav_bar.width() * 0.5, w.nav_bar.height() * 0.5)
        left = Qt.MouseButton.LeftButton
        event = QMouseEvent(QEvent.Type.MouseButtonDblClick, pos, QPointF(w.nav_bar.mapToGlobal(pos.toPoint())), left, left,
                            Qt.KeyboardModifier.NoModifier)
        w.nav_bar.mouseDoubleClickEvent(event)
        pump()
        self.assertTrue(w.isMaximized())
        w.nav_bar.mouseDoubleClickEvent(event)
        pump()
        self.assertFalse(w.isMaximized())

    def test_the_media_surface_check_covers_children_but_not_the_chrome(self) -> None:
        w = self.win
        self.assertTrue(w._is_media_surface(w.player.widget))
        self.assertTrue(w._is_media_surface(w.canvas))
        self.assertTrue(w._is_media_surface(w.canvas.viewport()))
        self.assertFalse(w._is_media_surface(w.nav_bar))
        self.assertFalse(w._is_media_surface(w.brand_bar))
        self.assertFalse(w._is_media_surface(None))


if __name__ == "__main__":
    unittest.main()
