"""Sağ üstteki "⋯" menüsü dışarı tıklanarak kapatılınca düğme üzerine gelinmiş (hover) görünümde kalmamalı.

Kullanıcı: "üç noktaya basıyorum sonra boş bir yere basıyorum, dropdown kapalı olsa bile hover efekti kalıyor."
QMenu açıkken fareyi yakaladığı için düğme "ayrıldı" olayını hiç almıyordu."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QEvent, QPoint, QSettings, Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QCursor  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton  # noqa: E402


def _enter(widget) -> None:
    widget.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
    QApplication.sendEvent(widget, QEvent(QEvent.Type.Enter))


def _close_popup_soon(app) -> None:
    def close():
        popup = app.activePopupWidget()
        if popup is not None:
            popup.close()
        else:
            QTimer.singleShot(20, close)

    QTimer.singleShot(50, close)


class HoverSettleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._tmp.name)
        cls.app = QApplication.instance() or QApplication([])

    def test_helper_clears_stale_hover_only_when_the_cursor_left(self):
        from app.ui.hover_fix import settle_hover

        btn = QPushButton("x")
        btn.resize(40, 30)
        btn.show()
        self.addCleanup(btn.deleteLater)
        self.app.processEvents()
        _enter(btn)
        QCursor.setPos(btn.mapToGlobal(QPoint(5000, 5000)))
        settle_hover(btn)
        self.assertFalse(btn.underMouse())
        settle_hover(None)  # sessizce geçer

    def test_more_menu_does_not_leave_the_button_hovered(self):
        from app.ui.main_window import MainWindow

        win = MainWindow()
        self.addCleanup(win.deleteLater)
        self.addCleanup(win.close)
        win.resize(900, 600)
        win.show()
        self.app.processEvents()
        btn = win.btn_settings
        _enter(btn)
        QCursor.setPos(win.mapToGlobal(QPoint(450, 400)))  # menü dışında boş bir yer
        _close_popup_soon(self.app)
        win.show_settings_menu()
        self.app.processEvents()
        self.assertFalse(btn.underMouse(), "menü kapandıktan sonra düğme hover görünümünde kalmamalı")


if __name__ == "__main__":
    unittest.main()
