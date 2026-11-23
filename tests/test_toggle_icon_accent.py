"""Alt çubuk simgeleri tek renk; yalnızca açık olan düğmenin (döngü, ayrıntılar, galeri, tam ekran) simgesi vurgu renginde.

Kapatılınca simge yeniden tek renge döner; vurgu rengi değişince açık simge yeni vurguyu alır. Ekran açmaz (offscreen).
"""

from __future__ import annotations

import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QSize  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton  # noqa: E402


def _ink_of(btn: QPushButton) -> QColor:
    """Simgenin en opak pikselinin rengi (çizgi rengi; kenar yumuşatması dışarıda kalır)."""
    image = btn.icon().pixmap(QSize(32, 32)).toImage()
    best = max(
        (image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())),
        key=lambda c: c.alpha(),
    )
    return QColor(best.red(), best.green(), best.blue())


def _close(a: QColor, b: str, tol: int = 8) -> bool:
    b = QColor(b)
    return max(abs(a.red() - b.red()), abs(a.green() - b.green()), abs(a.blue() - b.blue())) <= tol


class ToggleIconAccentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from app.ui.main_window import MainWindow

        self.win = MainWindow()
        self.addCleanup(self.win.deleteLater)

    def palette(self) -> dict:
        from app.resources import theme

        return theme.palette(self.win._resolved_theme(), self.win.accent)

    def test_palette_gives_a_readable_active_ink_for_every_accent(self):
        from app.resources import theme

        for name in ("dark", "light"):
            for accent in theme.ACCENTS:
                p = theme.palette(name, accent)
                self.assertIn("active_ink", p)
                self.assertNotEqual(p["active_ink"].lower(), p["ink"].lower(), (name, accent))

    def test_toggle_icons_follow_the_active_state(self):
        from app.ui.chrome_widgets import polish

        for btn in (self.win.btn_loop, self.win.btn_info, self.win.btn_gallery, self.win.btn_full):
            with self.subTest(btn=btn.objectName() or repr(btn)):
                btn.setProperty("active", "false")
                polish(btn)
                self.assertTrue(_close(_ink_of(btn), self.palette()["ink"]))
                btn.setProperty("active", "true")
                polish(btn)
                self.assertTrue(_close(_ink_of(btn), self.palette()["active_ink"]))
                self.assertFalse(_close(_ink_of(btn), self.palette()["ink"]))
                btn.setProperty("active", "false")
                polish(btn)
                self.assertTrue(_close(_ink_of(btn), self.palette()["ink"]))

    def test_other_toolbar_icons_stay_monochrome(self):
        self.win.btn_loop.setProperty("active", "true")
        from app.ui.chrome_widgets import polish

        polish(self.win.btn_loop)
        self.assertTrue(_close(_ink_of(self.win.btn_trash), self.palette()["ink"]))

    def test_accent_change_recolours_the_active_icon(self):
        from app.resources import theme
        from app.ui.chrome_widgets import polish

        self.win.btn_info.setProperty("active", "true")
        polish(self.win.btn_info)
        accents = [a for a in theme.ACCENTS if a != self.win.accent]
        self.win.set_accent(accents[0])
        self.assertTrue(_close(_ink_of(self.win.btn_info), self.palette()["active_ink"]))

    def test_empty_state_sparkle_follows_the_accent(self):
        from app.resources import theme

        for accent in ("violet", "green"):
            self.win.set_accent(accent)
            image = self.win.empty.icon.pixmap().toImage()
            best = max(
                (image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())),
                key=lambda c: c.alpha(),
            )
            self.assertTrue(_close(QColor(best.red(), best.green(), best.blue()), theme.palette(self.win._resolved_theme(), accent)["accent_text"]), accent)


if __name__ == "__main__":
    unittest.main()
