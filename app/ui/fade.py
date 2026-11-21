"""Kısa, sade geçiş animasyonları (150–250 ms) için ortak yardımcılar.

Windows'ta "Animasyonları göster" kapalıysa (Ayarlar > Erişilebilirlik) hiçbir şey canlandırılmaz: pencereler anında
açılıp kapanır, geçişler atlanır. Karar `smooth_progress._system_animations_enabled` ile aynı yerden okunur.
"""

from __future__ import annotations

from PyQt6.QtCore import QAbstractAnimation, QEasingCurve, QObject, QVariantAnimation
from PyQt6.QtWidgets import QWidget

from app.ui.smooth_progress import _system_animations_enabled

FADE_IN_MS = 160
FADE_OUT_MS = 220


def animations_enabled() -> bool:
    return _system_animations_enabled()


class WindowFader(QObject):
    """Üst düzey bir pencereyi (ör. `OverlayHost`) saydamlığıyla yumuşakça gösterir/gizler.

    `fade_in()` gizliyse 0'dan açar, kaybolmaktaysa olduğu yerden geri döndürür; `fade_out()` sonunda pencereyi
    gizler ve saydamlığı 1'e geri koyar (doğrudan `show()` çağıran eski yollar tam görünür pencere bulsun).
    """

    def __init__(self, window: QWidget, fade_in_ms: int = FADE_IN_MS, fade_out_ms: int = FADE_OUT_MS) -> None:
        super().__init__(window)
        self._window = window
        self._in_ms = int(fade_in_ms)
        self._out_ms = int(fade_out_ms)
        self._want_visible = True
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._apply)
        self._anim.finished.connect(self._on_finished)

    # --- durum ---
    def is_fading_out(self) -> bool:
        return not self._want_visible and self._anim.state() == QAbstractAnimation.State.Running

    def is_running(self) -> bool:
        return self._anim.state() == QAbstractAnimation.State.Running

    # --- eylemler ---
    def fade_in(self) -> None:
        w = self._window
        self._want_visible = True
        if not animations_enabled():
            self._anim.stop()
            w.setWindowOpacity(1.0)
            w.show()
            return
        if w.isVisible() and not self.is_running() and w.windowOpacity() >= 0.999:
            return  # zaten tam görünür
        start = w.windowOpacity() if w.isVisible() else 0.0
        self._anim.stop()
        if not w.isVisible():
            w.setWindowOpacity(0.0)
            w.show()
        self._run(start, 1.0, self._in_ms)

    def fade_out(self) -> None:
        w = self._window
        self._want_visible = False
        if not w.isVisible():
            self._anim.stop()
            w.setWindowOpacity(1.0)
            return
        if not animations_enabled():
            self.stop(hide=True)
            return
        if self.is_running() and self._anim.endValue() == 0.0:
            return  # zaten kayboluyor
        start = w.windowOpacity()
        self._anim.stop()
        self._run(start, 0.0, self._out_ms)

    def stop(self, hide: bool = False) -> None:
        """Animasyonu kes; pencere tam saydamlıkta kalır (`hide=True` ise gizlenir)."""
        self._anim.stop()
        if hide:
            self._window.hide()
        self._window.setWindowOpacity(1.0)

    # --- iç ---
    def _run(self, start: float, end: float, full_ms: int) -> None:
        span = abs(end - start)
        if span < 0.001:
            self._on_finished()
            return
        self._anim.setStartValue(float(start))
        self._anim.setEndValue(float(end))
        self._anim.setDuration(max(40, int(full_ms * span)))
        self._anim.start()

    def _apply(self, value) -> None:
        try:
            self._window.setWindowOpacity(float(value))
        except RuntimeError:  # pencere kapanırken silinmiş olabilir
            self._anim.stop()

    def _on_finished(self) -> None:
        try:
            if not self._want_visible:
                self._window.hide()
            self._window.setWindowOpacity(1.0)
        except RuntimeError:
            pass
