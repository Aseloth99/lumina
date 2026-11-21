"""Ekran üstü kısa bilgiler (OSD) ve sürükle-bırak vurgusu.

* `Osd`: sarma (±5 sn), oynatma hızı, döngü ve yakınlaştırma yüzdesi için sahnenin ortasında beliren, kendiliğinden
  kaybolan küçük cam kapsül. Tıklamaları engellemez (fare olayları altındaki pencereye geçer).
* `DropHighlight`: dosya pencerenin üstüne sürüklenirken sahneyi vurgu rengiyle çerçeveler ("Açmak için bırakın").

İkisi de video (mpv'nin kendi Direct3D yüzeyi) üstünde görünsün diye şeffaf üst pencerede (`OverlayHost`) yaşar ve
Windows'ta animasyonlar kapalıysa geçişsiz açılıp kapanır.
"""

from __future__ import annotations

from PyQt6.QtCore import QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from app.resources import theme as theme_mod
from app.ui.chrome_widgets import OverlayHost
from app.ui.fade import WindowFader

OSD_HOLD_MS = 900


def _click_through(host: QWidget) -> None:
    """Pencere fareyi (ve sürükle-bırakı) altındaki pencereye geçirir; odak almaz."""
    host.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
    host.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)


class Osd:
    """Sahnenin ortasında kısa süre görünen tek satırlık bilgi kapsülü."""

    def __init__(self, owner: QWidget) -> None:
        self._host = OverlayHost(owner)
        _click_through(self._host)
        self._chip = QFrame()
        self._chip.setObjectName("osdChip")
        self._chip.setProperty("role", "glass")
        row = QHBoxLayout(self._chip)
        row.setContentsMargins(18, 0, 18, 0)
        self._label = QLabel()
        self._label.setObjectName("osdText")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._label)
        self._host.adopt(self._chip)
        self._host.hide()
        self._fader = WindowFader(self._host)
        self._timer = QTimer(self._host)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fader.fade_out)

    @property
    def host(self) -> OverlayHost:
        return self._host

    def text(self) -> str:
        return self._label.text()

    def is_visible(self) -> bool:
        """Görünür ve kaybolmuyor (yeni bilgi eskisinin üstüne eklenebilir: ör. +5 sn → +10 sn)."""
        return self._host.isVisible() and not self._fader.is_fading_out()

    def show_text(self, text: str, anchor: QRect, msec: int = OSD_HOLD_MS) -> None:
        """`anchor`: küresel koordinatlarda sahne; kapsül onun ortasında durur."""
        self._label.setText(text)
        self._chip.adjustSize()
        hint = self._chip.sizeHint()
        w = max(hint.width(), 72)
        h = max(hint.height(), 36)
        self._host.place(QRect(anchor.x() + (anchor.width() - w) // 2, anchor.y() + (anchor.height() - h) // 2, w, h))
        self._fader.fade_in()
        self._host.raise_()
        self._timer.start(max(200, int(msec)))

    def hide(self) -> None:
        self._timer.stop()
        self._fader.stop(hide=True)


class _DropFrame(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.text = ""

    def paintEvent(self, _event) -> None:  # noqa: N802
        pal = theme_mod.active_palette()
        accent = QColor(pal["accent_edge"])
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        tint = QColor(accent)
        tint.setAlpha(34)
        pen = QPen(accent, 2.4)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern([5.0, 3.5])
        p.setPen(pen)
        p.setBrush(tint)
        p.drawRoundedRect(rect, 12, 12)
        if self.text:
            font = QFont(self.font())
            font.setPixelSize(15)
            font.setBold(True)
            p.setFont(font)
            fm = p.fontMetrics()
            pw = fm.horizontalAdvance(self.text) + 36
            ph = 40
            pill = QRectF((self.width() - pw) / 2, (self.height() - ph) / 2, pw, ph)
            path = QPainterPath()
            path.addRoundedRect(pill, ph / 2, ph / 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal["fill"]))
            p.drawPath(path)
            p.setPen(QColor(pal["on_fill"]))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, self.text)
        p.end()


class DropHighlight:
    """Dosya sürüklenirken sahnenin üstünde kesik çizgili vurgu çerçevesi."""

    def __init__(self, owner: QWidget) -> None:
        self._host = OverlayHost(owner)
        _click_through(self._host)
        self._frame = _DropFrame()
        self._host.adopt(self._frame)
        self._host.hide()
        self._fader = WindowFader(self._host, fade_in_ms=120, fade_out_ms=160)

    @property
    def host(self) -> OverlayHost:
        return self._host

    def is_shown(self) -> bool:
        return self._host.isVisible() and not self._fader.is_fading_out()

    def show_over(self, anchor: QRect, text: str) -> None:
        self._frame.text = text
        self._host.place(QRect(anchor))
        self._frame.update()
        self._fader.fade_in()
        self._host.raise_()

    def hide(self, animated: bool = True) -> None:
        if animated:
            self._fader.fade_out()
        else:
            self._fader.stop(hide=True)
