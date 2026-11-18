"""Başlık çubuğu glifleri: büyüt/geri yükle düğmesi pencerenin GERÇEK durumunu gösterir.

* Glif her `WindowStateChange`'te yenilenir: yalnızca uygulamanın kendi `toggle_maximize`'ından değil, Win+Yukarı/Aşağı, Aero Snap,
  görev çubuğu ya da doğrudan `showMaximized()` / `setWindowState()` ile gelen değişimlerde de.
* İpucu duruma göre: "Ekranı Kapla" / "Geri Yükle" (EN "Maximize" / "Restore Down"), dil değişince de.
* Geri yükle glifinde arkadaki kare öndekinin içinden geçmez (kırpılır).
* ChromeDialog'un büyüt düğmesi drawText ile "□/❐" değil, aynı vektör glifleri çizer.
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

from PyQt6.QtCore import QCoreApplication, QPoint, QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter, QRegion  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402


def pump(n: int = 5) -> None:
    for _ in range(n):
        QCoreApplication.processEvents()


def _image(icon) -> QImage:
    return icon.pixmap(32).toImage()


class RestoreGlyphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _render(self, icon_fn, size: int = 64) -> QImage:
        return icon_fn(QColor("#ffffff")).pixmap(size).toImage()

    def test_back_square_is_clipped_where_the_front_square_covers_it(self) -> None:
        from app.ui.icons import restore_icon

        img = self._render(restore_icon)
        self.assertEqual(img.width(), 64, "2× (64 px) kopya var")
        s = 2  # 32'lik ızgara -> 64 px
        # Arkadaki karenin sol kenarı (x=11.5) ve alt kenarı (y=20) öndeki karenin İÇİNDE çizilmez
        self.assertEqual(img.pixelColor(int(11.5 * s), 17 * s).alpha(), 0, "sol kenar öndeki karenin içinden geçmemeli")
        self.assertEqual(img.pixelColor(15 * s, 20 * s).alpha(), 0, "alt kenar öndeki karenin içinden geçmemeli")
        # ...ama öndeki karenin dışında kalan kısımları çizilir
        self.assertGreater(img.pixelColor(int(11.5 * s), 9 * s).alpha(), 128)
        self.assertGreater(img.pixelColor(int(22.5 * s), 20 * s).alpha(), 128)
        # Öndeki kare tam çizilir
        self.assertGreater(img.pixelColor(8 * s, 18 * s).alpha(), 128)
        self.assertGreater(img.pixelColor(14 * s, 24 * s).alpha(), 128)

    def test_maximize_glyph_is_a_single_rounded_square(self) -> None:
        from app.ui.icons import maximize_icon

        img = self._render(maximize_icon)
        s = 2
        self.assertGreater(img.pixelColor(int(8.5 * s), 16 * s).alpha(), 128)
        self.assertGreater(img.pixelColor(int(23.5 * s), 16 * s).alpha(), 128)
        self.assertEqual(img.pixelColor(16 * s, 16 * s).alpha(), 0, "içi boş (fırça yok)")

    def test_update_arrow_glyph(self) -> None:
        from app.ui.icons import update_arrow_icon

        img = self._render(update_arrow_icon)
        s = 2
        self.assertGreater(img.pixelColor(16 * s, 20 * s).alpha(), 128, "gövde")
        self.assertGreater(img.pixelColor(13 * s, int(12 * s)).alpha(), 100, "sol ok başı")
        self.assertGreater(img.pixelColor(19 * s, int(12 * s)).alpha(), 100, "sağ ok başı")
        self.assertEqual(img.pixelColor(10 * s, 22 * s).alpha(), 0)


class MainWindowMaxButtonTest(unittest.TestCase):
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
        w.lang = "tr"
        w.retranslate_ui()
        if w.isFullScreen() or w.isMaximized():
            w.showNormal()
        pump()

    def _assert_glyph(self, maximized: bool) -> None:
        from app.ui.icons import maximize_icon, restore_icon

        w = self.win
        ink = w._toolbar_ink()
        expected = (restore_icon if maximized else maximize_icon)(ink)
        self.assertEqual(_image(w.btn_max.icon()), _image(expected), "geri yükle" if maximized else "büyüt")

    def test_glyph_follows_a_state_change_that_bypasses_toggle_maximize(self) -> None:
        w = self.win
        self.assertFalse(w.isMaximized())
        self._assert_glyph(False)
        w.setWindowState(Qt.WindowState.WindowMaximized)  # Win+Yukarı / görev çubuğu / Aero Snap gibi: toggle yok
        pump()
        self.assertTrue(w.isMaximized())
        self._assert_glyph(True)
        w.setWindowState(Qt.WindowState.WindowNoState)
        pump()
        self.assertFalse(w.isMaximized())
        self._assert_glyph(False)

    def test_glyph_follows_show_maximized_and_toggle(self) -> None:
        w = self.win
        w.showMaximized()
        pump()
        self._assert_glyph(True)
        w.toggle_maximize()
        pump()
        self.assertFalse(w.isMaximized())
        self._assert_glyph(False)
        w.btn_max.click()
        pump()
        self.assertTrue(w.isMaximized())
        self._assert_glyph(True)

    def test_tooltip_follows_state_and_language(self) -> None:
        w = self.win
        self.assertEqual(w.btn_max.toolTip(), "Ekranı Kapla")
        w.showMaximized()
        pump()
        self.assertEqual(w.btn_max.toolTip(), "Geri Yükle")
        w.lang = "en"
        w.retranslate_ui()
        self.assertEqual(w.btn_max.toolTip(), "Restore Down")
        w.showNormal()
        pump()
        self.assertEqual(w.btn_max.toolTip(), "Maximize")

    def test_glyph_uses_the_same_ink_as_minimize(self) -> None:
        from app.ui.icons import minimize_icon

        w = self.win
        ink = w._toolbar_ink()
        self.assertEqual(_image(w.btn_min.icon()), _image(minimize_icon(ink)))
        self._assert_glyph(False)


class ChromeDialogMaxGlyphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _render(self, widget: QWidget) -> QImage:
        img = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        widget.render(img, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
        return img

    def _reference(self, button, paint) -> QImage:
        img = QImage(button.size(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = button.GLYPH
        p.translate((button.width() - size) / 2, (button.height() - size) / 2)
        p.scale(size / 32.0, size / 32.0)
        paint(p, QColor(button._pal["text"]))
        p.end()
        return img

    def test_dialog_max_button_draws_the_vector_glyphs_and_follows_the_state(self) -> None:
        from app.ui.chrome_dialog import ChromeDialog
        from app.ui.icons import paint_maximize_glyph, paint_restore_glyph

        dlg = ChromeDialog(None, resizable=True)
        self.addCleanup(dlg.deleteLater)
        dlg.resize(400, 300)
        dlg.show()
        pump()
        btn = dlg._cd_max
        self.assertIsNotNone(btn)
        self.assertEqual(self._render(btn), self._reference(btn, paint_maximize_glyph))
        self.assertIn(btn.toolTip(), ("Ekranı Kapla", "Maximize"))
        dlg.showMaximized()
        pump()
        btn = dlg._cd_max
        self.assertTrue(btn._maximized)
        self.assertEqual(self._render(btn), self._reference(btn, paint_restore_glyph))
        self.assertIn(btn.toolTip(), ("Geri Yükle", "Restore Down"))
        dlg.showNormal()
        pump()
        self.assertFalse(dlg._cd_max._maximized)
        dlg.close()

    def test_set_maximized_still_works(self) -> None:
        from app.ui.chrome_dialog import _MaxButton
        from app.ui.message_box import current_palette

        btn = _MaxButton(current_palette())
        self.addCleanup(btn.deleteLater)
        before = self._render(btn)
        btn.set_maximized(True)
        self.assertNotEqual(self._render(btn), before)
        btn.set_maximized(False)
        self.assertEqual(self._render(btn), before)


if __name__ == "__main__":
    unittest.main()
