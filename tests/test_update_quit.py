"""Kurulum bitince Lumina GERÇEKTEN çıkmalı: görünür "Kuruluyor" penceresi çıkışı iptal ettirmemeli.

Diğer uygulamalarda E2E'de (gerçek exe) bulunan hata: pencere kurulum sırasında kapanmayı reddeder; Qt 6'da `quit()` görünür pencereleri
kapatmayı dener, reddeden varsa çıkış iptal olur. Lumina bunu `allow_close()` ile çözer; ama mevcut test `closeAllWindows`/`quit`'i
sahte nesneyle değiştirdiği için çıkışın gerçekten olduğunu göstermiyordu: burada GERÇEK UpdateDialog ve gerçek olay döngüsü var.
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6 import sip  # noqa: E402
from PyQt6.QtCore import QCoreApplication, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.ui.update_dialog import UpdateDialog  # noqa: E402
from app.ui.updates import UpdateService  # noqa: E402


class _Service:
    """UpdateService'in gerçek `_quit_for_update`'i; yalnızca ihtiyaç duyduğu `dialog` alanı var."""

    def __init__(self, dialog):
        self.dialog = dialog

    def _quit_for_update(self):
        UpdateService._quit_for_update(self)


class QuitForUpdateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def test_the_event_loop_ends_even_though_the_installing_dialog_is_open(self):
        dialog = UpdateDialog("1.1.0", "notlar", False, None, size=1000, lang="tr")
        dialog.show_installing_state()
        dialog.show()
        self.assertTrue(dialog.isVisible())
        self.addCleanup(sip.delete, dialog)  # kurulum durumundaki pencere close()'u reddeder: gerçekten yok et (modal pencere sızmasın)
        service = _Service(dialog)
        QTimer.singleShot(0, lambda: service._quit_for_update())
        QTimer.singleShot(3000, lambda: QCoreApplication.exit(99))
        self.assertEqual(self.qapp.exec(), 0, "döngü 0 ile bitmeli (99 = açık pencere çıkışı iptal ettirdi)")


if __name__ == "__main__":
    unittest.main()
