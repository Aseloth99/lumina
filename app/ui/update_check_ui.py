"""Ayarlardaki "Kontrol et" düğmesi için canlandırma: dönen simge + en az süre + kalıcı sonuç satırı.

Denetim çoğu zaman milisaniyede biter; düğme "Kontrol ediliyor…" yazıp hemen eski haline dönünce kullanıcı bir şey
olduğunu fark etmiyordu. Burada düğme en az ``MIN_MS`` boyunca dönen bir simge gösterir, sonuç ise durum satırında
(✓ güncel / ↑ yeni sürüm / ✕ denetlenemedi) renkli olarak ve kalıcı biçimde kalır.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel, QPushButton

STATE_COLORS = {
    # durum: (koyu zemin, açık zemin, önek)
    "ok": ("#5fd08a", "#1b7f45", "✓"),
    "new": ("", "", "↑"),  # renk seçili vurgudan gelir (bkz. _status_color)
    "fail": ("#ff8a80", "#c62828", "✕"),
}


def surface_is_dark(widget: QPushButton) -> bool:
    """Gerçekten çizilen zeminin açıklığına bakar (QSS temaları palet değiştirmez)."""
    try:
        image = widget.grab().toImage()
        if image.isNull():
            return False
        x = min(6, max(0, image.width() - 1))
        y = max(0, image.height() // 2)
        return QColor(image.pixelColor(x, y)).lightness() < 128
    except Exception:
        return False


class UpdateCheckAnimator(QObject):
    MIN_MS = 1200
    FRAME_MS = 45

    def __init__(self, button: QPushButton, label: QLabel, parent: QObject | None = None):
        super().__init__(parent or button)
        self._button = button
        self._label = label
        self._angle = 0
        self._running = False
        self._started = 0.0
        self._idle_text = ""
        self._dark = False
        self._result: tuple[str, str] | None = None
        self._fade: QPropertyAnimation | None = None
        self._spin = QTimer(self)
        self._spin.setInterval(self.FRAME_MS)
        self._spin.timeout.connect(self._tick)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._apply_result)

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ başlat / bitir
    def start(self, running_text: str, status_text: str | None = None) -> None:
        if self._running:
            return
        self._dark = surface_is_dark(self._button)
        self._idle_text = self._button.text()
        self._running = True
        self._result = None
        self._started = time.monotonic()
        self._angle = 0
        self._button.setEnabled(False)
        self._button.setText(running_text)
        self._button.setIconSize(QSize(16, 16))
        self._button.setIcon(self._frame())
        self._set_status(status_text or running_text, None)
        self._spin.start()

    def finish(self, kind: str, text: str) -> None:
        """Sonucu bildirir; en az süre dolmadıysa dolana kadar animasyon sürer."""
        if not self._running:
            self._show_result(kind, text)
            return
        self._result = (kind, text)
        remaining = int(self.MIN_MS - (time.monotonic() - self._started) * 1000)
        if remaining > 0:
            self._hold.start(remaining)
        else:
            self._apply_result()

    def cancel(self) -> None:
        """Animasyonu sonuç göstermeden durdurur (pencere kapanırken ya da denetleyici yokken)."""
        self._hold.stop()
        self._stop_spinner()

    # ------------------------------------------------------------------ iç işler
    def _apply_result(self) -> None:
        if self._result is None:
            return
        kind, text = self._result
        self._result = None
        self._stop_spinner()
        self._show_result(kind, text)

    def _stop_spinner(self) -> None:
        self._spin.stop()
        if self._running:
            self._running = False
            self._button.setIcon(QIcon())
            self._button.setText(self._idle_text)
            self._button.setEnabled(True)

    def _show_result(self, kind: str, text: str) -> None:
        self._set_status(text, kind)
        effect = QGraphicsOpacityEffect(self._label)
        effect.setOpacity(0.0)
        self._label.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(320)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.finished.connect(lambda: self._label.setGraphicsEffect(None))
        self._fade = fade
        fade.start()

    def _set_status(self, text: str, kind: str | None) -> None:
        if kind in STATE_COLORS:
            dark_color, light_color, prefix = STATE_COLORS[kind]
            dark = surface_is_dark(self._button)
            color = self._status_color(kind, dark_color, light_color, dark)
            self._label.setText(f"{prefix}  {text}")
            self._label.setStyleSheet(f"color: {color}; font-weight: 600;")
        else:
            self._label.setText(text)
            self._label.setStyleSheet("")

    @staticmethod
    def _status_color(kind: str, dark_color: str, light_color: str, dark: bool) -> str:
        if kind == "new":  # "yeni sürüm" durumu seçili vurguyu kullanır (4,5:1 okunur ton)
            from app.resources import theme

            return theme.palette("dark" if dark else "light", theme.active_accent())["accent_text"]
        return dark_color if dark else light_color

    def _tick(self) -> None:
        self._angle = (self._angle + 30) % 360
        self._button.setIcon(self._frame())

    def _frame(self) -> QIcon:
        size, scale = 16, 2
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#e8e8e8" if self._dark else "#333333")
        ring = QColor(color)
        ring.setAlpha(70)
        rect = QRectF(2.0, 2.0, size - 4.0, size - 4.0)
        painter.setPen(QPen(ring, 1.9))
        painter.drawEllipse(rect)
        arc = QPen(color, 1.9)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc)
        painter.drawArc(rect, int((90 - self._angle) * 16), -110 * 16)
        painter.end()
        return QIcon(pixmap)
