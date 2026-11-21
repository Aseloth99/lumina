"""Yumuşak ilerleyen ilerleme çubuğu.

Düz QProgressBar her yeni değerde bir anda zıplar ("tak tak"). Bu çubuk yeni değere kısa bir yavaşlayan animasyonla
kayar. Dışarıdan bakınca QProgressBar ile aynıdır: value()/maximum()/minimum() verilen (mantıksal) değerleri döndürür,
%p / %v / %m biçimleri de bunlarla yazılır. Çizim için iç aralık büyütülür, böylece 0-100 ya da 0-5 gibi kaba
aralıklarda bile çubuk piksel piksel ilerler.

- Geri gitme (yeni iş, sıfırlama) animasyonsuz ve anında olur.
- Belirsiz kip (setRange(0, 0)) Qt'nin kendi "meşgul" animasyonuyla aynen çalışır.
- Çubuk görünmüyorsa ya da Windows'ta animasyonlar kapalıysa değer doğrudan atanır.
- Not: QProgressBar.valueChanged iç (büyütülmüş) değeri taşır; mantıksal değer için value() kullanın.
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import QEasingCurve, QVariantAnimation
from PyQt6.QtWidgets import QProgressBar

DURATION_MS = 320
_FINE_STEPS = 10_000  # iç aralık en az bu kadar adım: geniş çubukta bile ilerleme pikselden ince olur


def _system_animations_enabled() -> bool:
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        value = ctypes.c_int(1)
        # SPI_GETCLIENTAREAANIMATION: Ayarlar > Erişilebilirlik > "Windows'ta animasyonları göster"
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0):
            return bool(value.value)
    except Exception:
        pass
    return True


class SmoothProgressBar(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lo = 0
        self._hi = 100
        self._value = 0
        self._scale = 1
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(DURATION_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._paint_value)
        self._apply_range()
        super().setValue(0)

    # --- aralık -----------------------------------------------------------------------------------------------
    def _apply_range(self) -> None:
        span = self._hi - self._lo
        self._scale = max(1, _FINE_STEPS // span) if span > 0 else 1
        super().setRange(self._lo * self._scale, self._hi * self._scale)

    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802 (Qt adı)
        minimum, maximum = int(minimum), int(maximum)
        if maximum < minimum:
            maximum = minimum
        if (minimum, maximum) == (self._lo, self._hi):
            return
        self._anim.stop()
        self._lo, self._hi = minimum, maximum
        self._value = min(max(self._value, minimum), maximum)
        self._apply_range()
        super().setValue(self._value * self._scale)

    def setMinimum(self, minimum: int) -> None:  # noqa: N802
        self.setRange(minimum, max(int(minimum), self._hi))

    def setMaximum(self, maximum: int) -> None:  # noqa: N802
        self.setRange(min(self._lo, int(maximum)), maximum)

    def minimum(self) -> int:
        return self._lo

    def maximum(self) -> int:
        return self._hi

    # --- değer ------------------------------------------------------------------------------------------------
    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:  # noqa: N802
        value = min(max(int(value), self._lo), self._hi)
        if value == self._value and not self._anim.state() == QVariantAnimation.State.Running:
            return
        forward = value > self._value
        self._value = value
        target = value * self._scale
        start = super().value()
        busy = self._lo == self._hi
        if not forward or busy or not self.isVisible() or start >= target or not _system_animations_enabled():
            self._anim.stop()
            super().setValue(target)
            return
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(target)
        self._anim.start()

    def reset(self) -> None:
        self._anim.stop()
        self._value = self._lo
        super().reset()
        super().setValue(self._lo * self._scale)

    def _paint_value(self, v) -> None:
        super().setValue(int(v))

    def is_animating(self) -> bool:
        return self._anim.state() == QVariantAnimation.State.Running

    def displayed_fraction(self) -> float:
        """Çizilen (animasyondaki) doluluk, 0..1."""
        lo, hi = super().minimum(), super().maximum()
        return 0.0 if hi <= lo else (super().value() - lo) / (hi - lo)

    def finish_animation(self) -> None:
        """Animasyonu hemen bitirir (testler ve kapanış için)."""
        if self.is_animating():
            self._anim.stop()
            super().setValue(self._value * self._scale)

    def hideEvent(self, event) -> None:  # noqa: N802
        self.finish_animation()
        super().hideEvent(event)

    # --- yazı -------------------------------------------------------------------------------------------------
    def text(self) -> str:
        if self._hi == self._lo:
            return ""
        shown = super().value() / self._scale
        percent = round((shown - self._lo) * 100 / (self._hi - self._lo))
        fmt = self.format()
        return fmt.replace("%p", str(percent)).replace("%v", str(round(shown))).replace("%m", str(self._hi - self._lo))
