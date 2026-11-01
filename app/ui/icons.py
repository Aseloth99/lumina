"""Araç çubuğu ikonları — döndür / yakınlaştır / tam ekran."""

from __future__ import annotations

from math import cos, pi, sin

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


def _pixmap(size: int = 32) -> tuple[QPixmap, QPainter]:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pix, painter


def _hidpi_icon(draw) -> QIcon:
    """`draw(painter)` 32×32'lik ızgarada çizer; 32 ve 48 px kopyalar (%100/%150 ölçekte keskin)."""
    icon = QIcon()
    for size in (32, 48):
        pix, p = _pixmap(size)
        p.scale(size / 32.0, size / 32.0)
        draw(p)
        p.end()
        icon.addPixmap(pix)
    return icon


def _pen(color: QColor, width: float = 2.2) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _accent(kind: str = "accent") -> str:
    """Seçili vurgunun rengi (`accent`, `accent2`, `hi_dark`, `hi_light`); simgeler çizildiği anda okunur."""
    from app.resources import theme

    return theme.ACCENTS[theme.active_accent()][kind]


def accent_dot_icon(color: str, size: int = 16) -> QIcon:
    """Vurgu seçici için dolu daire (menüdeki "Renk" alt menüsü)."""
    pix, p = _pixmap(32)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(color)))
    p.drawEllipse(QRectF(6, 6, 20, 20))
    p.end()
    return QIcon(pix)


def sparkle_icon(color: QColor | None = None) -> QIcon:
    """Boş durum süsü: dört köşeli yıldız (metin glifi/emoji yerine vektör)."""
    color = color or QColor(_accent("hi_dark"))
    pix, p = _pixmap(64)
    p.scale(2.0, 2.0)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    star = QPainterPath()
    star.moveTo(16, 3)
    star.cubicTo(17.2, 11.5, 20.5, 14.8, 29, 16)
    star.cubicTo(20.5, 17.2, 17.2, 20.5, 16, 29)
    star.cubicTo(14.8, 20.5, 11.5, 17.2, 3, 16)
    star.cubicTo(11.5, 14.8, 14.8, 11.5, 16, 3)
    p.drawPath(star)
    p.end()
    return QIcon(pix)


def rotate_icon(clockwise: bool = True, color: QColor | None = None) -> QIcon:
    """Sade dairesel dönüş oku."""
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap(32)
    pen = QPen(color, 2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    box = QRectF(8.0, 8.0, 16.0, 16.0)
    if clockwise:
        p.drawArc(box, int(40 * 16), int(-250 * 16))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color))
        p.drawPolygon(QPolygonF([
            QPointF(22.6, 7.4),
            QPointF(27.2, 11.8),
            QPointF(19.8, 12.4),
        ]))
    else:
        p.drawArc(box, int(140 * 16), int(250 * 16))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color))
        p.drawPolygon(QPolygonF([
            QPointF(9.4, 7.4),
            QPointF(4.8, 11.8),
            QPointF(12.2, 12.4),
        ]))
    p.end()
    return QIcon(pix)


def zoom_icon(plus: bool = True, color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 2.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(6, 6, 16, 16))
    p.drawLine(QPointF(18.8, 18.8), QPointF(25.5, 25.5))
    p.drawLine(QPointF(10.5, 14), QPointF(17.5, 14))
    if plus:
        p.drawLine(QPointF(14, 10.5), QPointF(14, 17.5))
    p.end()
    return QIcon(pix)


def play_icon(size: int = 20, color: QColor | None = None) -> QIcon:
    color = color or QColor("#f5f5f5")
    pix, p = _pixmap(32)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    tri = QPolygonF([QPointF(11, 8), QPointF(24, 16), QPointF(11, 24)])
    p.drawPolygon(tri)
    p.end()
    icon = QIcon(pix)
    return icon


def pause_icon(size: int = 20, color: QColor | None = None) -> QIcon:
    color = color or QColor("#f5f5f5")
    pix, p = _pixmap(32)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawRoundedRect(QRectF(9.5, 8, 4.4, 16), 1.5, 1.5)
    p.drawRoundedRect(QRectF(18.1, 8, 4.4, 16), 1.5, 1.5)
    p.end()
    return QIcon(pix)


def stop_icon(size: int = 20, color: QColor | None = None) -> QIcon:
    color = color or QColor("#f5f5f5")
    pix, p = _pixmap(32)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawRoundedRect(QRectF(10, 10, 12, 12), 2, 2)
    p.end()
    return QIcon(pix)


def frame_step_icon(forward: bool = True, color: QColor | None = None) -> QIcon:
    """Kare kare ilerleme: üçgen + dikey çubuk (|▶ / ◀|)."""
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap(32)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    if forward:
        p.drawPolygon(QPolygonF([QPointF(8.5, 8.5), QPointF(20.5, 16), QPointF(8.5, 23.5)]))
        p.drawRoundedRect(QRectF(21.5, 8.5, 3.6, 15), 1.4, 1.4)
    else:
        p.drawPolygon(QPolygonF([QPointF(23.5, 8.5), QPointF(11.5, 16), QPointF(23.5, 23.5)]))
        p.drawRoundedRect(QRectF(6.9, 8.5, 3.6, 15), 1.4, 1.4)
    p.end()
    return QIcon(pix)


def loop_icon(color: QColor | None = None) -> QIcon:
    """Döngü (tekrarla): iki yönlü ok halkası."""
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap(32)
    p.setPen(_pen(color, 2.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    top = QPainterPath()
    top.moveTo(8.5, 17.5)
    top.lineTo(8.5, 14.5)
    top.quadTo(8.5, 11.0, 12.0, 11.0)
    top.lineTo(21.0, 11.0)
    p.drawPath(top)
    bottom = QPainterPath()
    bottom.moveTo(23.5, 14.5)
    bottom.lineTo(23.5, 17.5)
    bottom.quadTo(23.5, 21.0, 20.0, 21.0)
    bottom.lineTo(11.0, 21.0)
    p.drawPath(bottom)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawPolygon(QPolygonF([QPointF(20.5, 7.5), QPointF(25.5, 11.0), QPointF(20.5, 14.5)]))
    p.drawPolygon(QPolygonF([QPointF(11.5, 17.5), QPointF(6.5, 21.0), QPointF(11.5, 24.5)]))
    p.end()
    return QIcon(pix)


def tracks_icon(color: QColor | None = None) -> QIcon:
    """Ses/altyazı parçaları: altyazı kutusu (konuşma balonu + satırlar)."""
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap(32)
    p.setPen(_pen(color, 2.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(5.5, 8.5, 21, 15), 3.5, 3.5)
    p.drawLine(QPointF(10, 14.5), QPointF(15, 14.5))
    p.drawLine(QPointF(18, 14.5), QPointF(22, 14.5))
    p.drawLine(QPointF(10, 18.5), QPointF(13, 18.5))
    p.drawLine(QPointF(16, 18.5), QPointF(22, 18.5))
    p.end()
    return QIcon(pix)


def info_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap(32)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Dış çember
    pen = _pen(color, 1.85)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(6.5, 6.5, 19.0, 19.0))

    # Bilgi noktası (tam merkezli yuvarlak)
    p.setBrush(QBrush(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(16.0, 11.2), 1.45, 1.45)

    # Harf gövdesi ve Fluent stil kavisli/serifli ayak
    p.setPen(pen)
    p.drawLine(QPointF(14.5, 16.2), QPointF(16.0, 15.0))
    p.drawLine(QPointF(16.0, 15.0), QPointF(16.0, 21.2))
    p.drawLine(QPointF(14.2, 21.2), QPointF(17.8, 21.2))
    p.end()
    return QIcon(pix)


def trash_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 2.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(9, 11), QPointF(23, 11))
    p.drawLine(QPointF(12, 11), QPointF(13, 8))
    p.drawLine(QPointF(20, 11), QPointF(19, 8))
    p.drawLine(QPointF(13, 8), QPointF(19, 8))
    p.drawRoundedRect(QRectF(10, 11, 12, 13), 1.5, 1.5)
    p.drawLine(QPointF(14, 14), QPointF(14, 21))
    p.drawLine(QPointF(18, 14), QPointF(18, 21))
    p.end()
    return QIcon(pix)


def fullscreen_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 2.0))
    for x0, y0, dx, dy in (
        (6, 6, 8, 8),
        (26, 6, -8, 8),
        (6, 26, 8, -8),
        (26, 26, -8, -8),
    ):
        p.drawLine(QPointF(x0, y0), QPointF(x0 + dx, y0))
        p.drawLine(QPointF(x0, y0), QPointF(x0, y0 + dy))
    p.end()
    return QIcon(pix)


def volume_icon(muted: bool = False, color: QColor | None = None) -> QIcon:
    """Hoparlör simgesi; sessizde dalgalar yerine belirgin bir çarpı gösterir."""
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 2.0))
    p.setBrush(QBrush(color))
    p.drawPolygon(
        QPolygonF(
            [
                QPointF(6, 13),
                QPointF(11, 13),
                QPointF(17, 8),
                QPointF(17, 24),
                QPointF(11, 19),
                QPointF(6, 19),
            ]
        )
    )
    p.setBrush(Qt.BrushStyle.NoBrush)
    if muted:
        p.setPen(_pen(color, 2.2))
        p.drawLine(QPointF(21, 12), QPointF(27, 20))
        p.drawLine(QPointF(27, 12), QPointF(21, 20))
    else:
        p.setPen(_pen(color, 1.8))
        p.drawArc(QRectF(14, 10, 10, 12), -55 * 16, 110 * 16)
        p.drawArc(QRectF(15, 6, 17, 20), -52 * 16, 104 * 16)
    p.end()
    return QIcon(pix)


def arrow_icon(left: bool = True, color: QColor | None = None) -> QIcon:
    """Önceki / sonraki ok ikonu — disabled durumu net biçimde ayırt edilebilir."""
    color = color or QColor("#f5f5f5")
    is_dark = color.red() > 128
    icon = QIcon()
    for mode in (
        QIcon.Mode.Normal,
        QIcon.Mode.Active,
        QIcon.Mode.Selected,
        QIcon.Mode.Disabled,
    ):
        if mode == QIcon.Mode.Disabled:
            col = QColor(255, 255, 255, 92) if is_dark else QColor(0, 0, 0, 108)
            stroke = 2.35
        else:
            col = color
            stroke = 2.9
        pix, p = _pixmap(32)
        p.setPen(_pen(col, stroke))
        if left:
            p.drawLine(QPointF(19, 9), QPointF(12, 16))
            p.drawLine(QPointF(12, 16), QPointF(19, 23))
        else:
            p.drawLine(QPointF(13, 9), QPointF(20, 16))
            p.drawLine(QPointF(20, 16), QPointF(13, 23))
        p.end()
        icon.addPixmap(pix, mode, QIcon.State.Off)
        icon.addPixmap(pix, mode, QIcon.State.On)
    return icon


def chevron_icon(up: bool = True, color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 2.1))
    if up:
        p.drawLine(QPointF(8, 19), QPointF(16, 11))
        p.drawLine(QPointF(16, 11), QPointF(24, 19))
    else:
        p.drawLine(QPointF(8, 13), QPointF(16, 21))
        p.drawLine(QPointF(16, 21), QPointF(24, 13))
    p.end()
    return QIcon(pix)


def chevron_horizontal_icon(right: bool = True, color: QColor | None = None) -> QIcon:
    """Film şeridi açma/kapama için sağa/sola ok ikonu."""
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 2.1))
    if right:
        # Sağa ok: > (şeridi genişlet/aç)
        p.drawLine(QPointF(12, 9), QPointF(20, 16))
        p.drawLine(QPointF(20, 16), QPointF(12, 23))
    else:
        # Sola ok: < (şeridi daralt/kapat)
        p.drawLine(QPointF(20, 9), QPointF(12, 16))
        p.drawLine(QPointF(12, 16), QPointF(20, 23))
    p.end()
    return QIcon(pix)



def more_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawEllipse(QRectF(7.0, 14.5, 3.0, 3.0))
    p.drawEllipse(QRectF(14.5, 14.5, 3.0, 3.0))
    p.drawEllipse(QRectF(22.0, 14.5, 3.0, 3.0))
    p.end()
    return QIcon(pix)


def settings_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap(32)
    pen = _pen(color, 2.0)
    p.setPen(pen)
    p.drawEllipse(QRectF(11.0, 11.0, 10.0, 10.0))
    for i in range(8):
        rad = (i * 45) * pi / 180.0
        x1 = 16.0 + 7.0 * cos(rad)
        y1 = 16.0 + 7.0 * sin(rad)
        x2 = 16.0 + 11.0 * cos(rad)
        y2 = 16.0 + 11.0 * sin(rad)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    p.end()
    return QIcon(pix)


def minimize_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 2.0))
    p.drawLine(QPointF(9, 16.5), QPointF(23, 16.5))
    p.end()
    return QIcon(pix)


def maximize_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 1.85))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(8.5, 8.5, 15, 15), 2.2, 2.2)
    p.end()
    return QIcon(pix)


def restore_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 1.7))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(11.5, 7.5, 12.5, 12.5), 1.8, 1.8)
    p.setBrush(QBrush(Qt.GlobalColor.transparent))
    p.drawRoundedRect(QRectF(8, 11.5, 12.5, 12.5), 1.8, 1.8)
    p.end()
    return QIcon(pix)


def close_glyph_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 2.05))
    p.drawLine(QPointF(10, 10), QPointF(22, 22))
    p.drawLine(QPointF(22, 10), QPointF(10, 22))
    p.end()
    return QIcon(pix)


def folder_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(6, 11, 20, 14), 2.2, 2.2)
    p.drawLine(QPointF(6, 14), QPointF(13.5, 14))
    p.drawLine(QPointF(13.5, 14), QPointF(15.5, 11))
    p.end()
    return QIcon(pix)


def sort_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 1.8))
    # Üç yatay sıralama çizgisi
    p.drawLine(QPointF(6, 10), QPointF(17, 10))
    p.drawLine(QPointF(6, 16), QPointF(14, 16))
    p.drawLine(QPointF(6, 22), QPointF(10, 22))
    # Sağda aşağı yönlü ok
    p.drawLine(QPointF(22, 9), QPointF(22, 23))
    p.drawLine(QPointF(19, 20), QPointF(22, 23))
    p.drawLine(QPointF(25, 20), QPointF(22, 23))
    p.end()
    return QIcon(pix)


def file_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(9, 6, 14, 20), 2.0, 2.0)
    p.drawLine(QPointF(12, 12), QPointF(20, 12))
    p.drawLine(QPointF(12, 16), QPointF(20, 16))
    p.drawLine(QPointF(12, 20), QPointF(17, 20))
    p.end()
    return QIcon(pix)


def theme_dark_icon() -> QIcon:
    """Hilal ay, seçili vurgu renginde (açık ve koyu menüde görünür)."""

    def draw(p: QPainter) -> None:
        moon = QPainterPath()
        moon.addEllipse(QRectF(6, 6, 20, 20))
        bite = QPainterPath()
        bite.addEllipse(QRectF(12.5, 3.5, 17, 17))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(_accent())))
        p.drawPath(moon.subtracted(bite))

    return _hidpi_icon(draw)


def theme_light_icon() -> QIcon:
    pix, p = _pixmap()
    p.setPen(_pen(QColor("#f59e0b"), 1.6))
    p.setBrush(QBrush(QColor("#fbbf24")))
    p.drawEllipse(QRectF(10, 10, 12, 12))
    p.setPen(_pen(QColor("#f59e0b"), 1.7))
    for ang in range(0, 360, 45):
        p.save()
        p.translate(16, 16)
        p.rotate(ang)
        p.drawLine(QPointF(0, -13), QPointF(0, -10))
        p.restore()
    p.end()
    return QIcon(pix)


def theme_system_icon() -> QIcon:
    """Yarı koyu / yarı açık daire (nötr gri), iki temada da çerçevesi görünür."""

    def draw(p: QPainter) -> None:
        box = QRectF(6, 6, 20, 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#3a3a3a")))
        p.drawEllipse(box)
        p.setBrush(QBrush(QColor("#f5f5f5")))
        p.drawPie(box, 90 * 16, 180 * 16)
        p.setPen(_pen(QColor("#9e9e9e"), 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(box)

    return _hidpi_icon(draw)


def apps_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    for x, y in ((8, 8), (18, 8), (8, 18), (18, 18)):
        p.drawRoundedRect(QRectF(x, y, 6, 6), 1.4, 1.4)
    p.end()
    return QIcon(pix)


def shell_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(7, 9, 18, 14), 2.0, 2.0)
    p.drawLine(QPointF(7, 13), QPointF(25, 13))
    p.end()
    return QIcon(pix)


def autoplay_icon(color: QColor | None = None) -> QIcon:
    return play_icon(20, color or QColor("#e0e0e0"))


def copy_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 1.7))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(8, 10, 12, 14), 1.8, 1.8)
    p.drawRoundedRect(QRectF(12, 6, 12, 14), 1.8, 1.8)
    p.end()
    return QIcon(pix)


def print_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 1.7))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(7, 14, 18, 10), 1.8, 1.8)
    p.drawRect(QRectF(10, 6, 12, 8))
    p.drawLine(QPointF(10, 20), QPointF(22, 20))
    p.end()
    return QIcon(pix)


def undo_icon(color: QColor | None = None) -> QIcon:
    color = color or QColor("#e0e0e0")
    pix, p = _pixmap()
    p.setPen(_pen(color, 2.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(7, 8, 18, 16), int(40 * 16), int(240 * 16))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawPolygon(QPolygonF([QPointF(7.2, 8.2), QPointF(13.4, 7.4), QPointF(9.8, 13.6)]))
    p.end()
    return QIcon(pix)


_FLAG_ICONS_CACHE: dict[str, QIcon] = {}


def flag_tr_icon() -> QIcon:
    return language_flag_icon("tr")


def flag_en_icon() -> QIcon:
    return language_flag_icon("en")


def language_flag_icon(code: str) -> QIcon:
    """Dil kodu için bayrak ikonu; auto ise sistem dilini çözümler ve açık/koyu temaya uygun çerçeve çizer."""
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPainterPath, QPolygonF, QPen
    from PyQt6.QtCore import Qt, QPointF, QRectF
    import math

    if QApplication.instance() is None:
        return QIcon()

    raw_key = (code or "auto").lower()
    is_auto = raw_key in ("auto", "", "none")

    if is_auto:
        try:
            from app.resources.locales import get_system_language
            detected = get_system_language() or "tr"
        except Exception:
            detected = "tr"
        code_key = (detected or "tr").lower()
        cache_key = f"auto_{code_key}_{_accent()}"
    else:
        code_key = raw_key
        cache_key = code_key

    if cache_key in _FLAG_ICONS_CACHE:
        return _FLAG_ICONS_CACHE[cache_key]

    w, h = 48, 32
    pix = QPixmap(w, h)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    clip = QPainterPath()
    clip.addRoundedRect(QRectF(1.0, 1.0, w - 2, h - 2), 5.0, 5.0)
    p.setClipPath(clip)

    if code_key == "tr":
        p.fillRect(QRectF(0, 0, w, h), QColor("#E30A17"))
        # Hilal
        c_outer = QPainterPath()
        c_outer.addEllipse(QPointF(w * 0.38, h * 0.5), h * 0.32, h * 0.32)
        c_inner = QPainterPath()
        c_inner.addEllipse(QPointF(w * 0.45, h * 0.5), h * 0.25, h * 0.25)
        p.fillPath(c_outer.subtracted(c_inner), QColor("#ffffff"))
        # Yıldız
        star_center = QPointF(w * 0.63, h * 0.5)
        star_r_out = h * 0.14
        star_r_in = star_r_out * 0.382
        pts = []
        angle_offset = -math.pi / 2
        for i in range(10):
            r = star_r_out if i % 2 == 0 else star_r_in
            a = angle_offset + i * math.pi / 5
            pts.append(QPointF(star_center.x() + r * math.cos(a), star_center.y() + r * math.sin(a)))
        star_path = QPainterPath()
        star_path.addPolygon(QPolygonF(pts))
        p.fillPath(star_path, QColor("#ffffff"))

    elif code_key in ("en", "us"):
        stripe_h = h / 7.0
        for i in range(7):
            col = QColor("#B22234") if i % 2 == 0 else QColor("#FFFFFF")
            p.fillRect(QRectF(0, i * stripe_h, w, stripe_h + 0.5), col)
        canton_w = w * 0.45
        canton_h = stripe_h * 4.0
        p.fillRect(QRectF(0, 0, canton_w, canton_h), QColor("#3C3B6E"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        for r_idx in range(2):
            for c_idx in range(3):
                sx = canton_w * 0.2 + c_idx * (canton_w * 0.3)
                sy = canton_h * 0.28 + r_idx * (canton_h * 0.44)
                p.drawEllipse(QPointF(sx, sy), 1.4, 1.4)
    else:
        p.fillRect(QRectF(0, 0, w, h), QColor("#2a2a2a"))
        cx, cy = w / 2.0, h / 2.0
        r = min(w, h) * 0.4
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_accent()))
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setPen(QPen(QColor("#ffffff"), 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
        p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
        p.drawEllipse(QPointF(cx, cy), r * 0.5, r)

    # Otomatik mod rozeti (Sağ üst köşede şık mavi mini küre)
    if is_auto:
        p.setClipping(False)
        badge_cx, badge_cy, badge_r = w - 8.5, 8.5, 5.5
        p.setPen(QPen(QColor("#ffffff"), 1.2))
        p.setBrush(QColor(_accent("accent2")))
        p.drawEllipse(QPointF(badge_cx, badge_cy), badge_r, badge_r)
        p.setPen(QPen(QColor("#ffffff"), 0.9))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(badge_cx - badge_r + 1.0, badge_cy), QPointF(badge_cx + badge_r - 1.0, badge_cy))
        p.drawLine(QPointF(badge_cx, badge_cy - badge_r + 1.0), QPointF(badge_cx, badge_cy + badge_r - 1.0))
        p.drawEllipse(QPointF(badge_cx, badge_cy), badge_r * 0.5, badge_r * 0.9)

    # Açık ve koyu temalarda sınırları belirginleştiren zarif kontur (Lumina açık temada beyaz arkaplanla birleşmez)
    p.setClipping(False)
    p.setPen(QPen(QColor(0, 0, 0, 80), 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(1.0, 1.0, w - 2, h - 2), 5.0, 5.0)

    p.end()
    icon = QIcon(pix)
    _FLAG_ICONS_CACHE[cache_key] = icon
    return icon


def globe_icon(color=None, size: int = 20) -> QIcon:
    """Evrensel dil küre ikonu."""
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen
    from PyQt6.QtCore import Qt, QPointF

    if QApplication.instance() is None:
        return QIcon()

    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if color is None:
        c = QColor(_accent())
    elif isinstance(color, str):
        c = QColor(color)
    else:
        c = color
    pen = QPen(c, 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    r = (size - 4) / 2.0
    cx, cy = size / 2.0, size / 2.0
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
    p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
    p.drawEllipse(QPointF(cx, cy), r * 0.55, r)
    p.end()
    return QIcon(pix)


# --- Renkli menü ikonları -------------------------------------------------------------------------
# Menülerde (… menüsü, sağ tık, klasör/dosya kapsülü) renkli, iki tonlu simgeler. Renkler hem açık
# (beyaz) hem koyu (gri) menüde okunan orta tonlardır; araç çubuğu simgeleri tek renk (`ink`) kalır.
# Devre dışı satırlarda Qt simgeyi kendisi griler.

MENU_PALETTE = {
    "blue": "#2196F3", "blue_soft": "#90CAF9",  # "blue"/"sky" aileleri `_c` içinde seçili vurguya yönlenir (yedek değerler)
    "amber": "#f59e0b", "amber_soft": "#fcd34d", "amber_deep": "#d97706",
    "green": "#22c55e", "emerald": "#10b981",
    "violet": "#8b5cf6", "purple": "#a855f7", "purple_soft": "#e9d5ff",
    "indigo": "#7c7ff5", "sky": "#2196F3", "sky_soft": "#90CAF9",
    "teal": "#14b8a6", "red": "#ef4444", "red_soft": "#fecaca",
    "orange": "#f97316", "cyan": "#06b6d4", "yellow": "#eab308",
}


def _c(name: str) -> QColor:
    """Menü simgesi rengi. "blue"/"sky" aileleri seçili vurguyu izler; diğerleri anlam taşıyan sabit tonlardır."""
    if name in ("blue", "sky"):
        return QColor(_accent("accent"))
    if name in ("blue_soft", "sky_soft"):
        return QColor(_accent("hi_light"))
    return QColor(MENU_PALETTE[name])


def _draw_gear(p: QPainter) -> None:
    gear = QPainterPath()
    gear.addEllipse(QPointF(16, 16), 8.6, 8.6)
    for i in range(8):
        tooth = QPainterPath()
        tooth.addRoundedRect(QRectF(-2.4, -12.4, 4.8, 6.0), 1.2, 1.2)
        p.save()
        p.translate(16, 16)
        p.rotate(i * 45)
        gear = gear.united(p.transform().map(tooth))
        p.restore()
    hole = QPainterPath()
    hole.addEllipse(QPointF(16, 16), 3.6, 3.6)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("violet")))
    p.drawPath(gear.subtracted(hole))


def _draw_document(p: QPainter) -> None:
    body = QPainterPath()
    body.moveTo(9, 5.5)
    body.lineTo(18.5, 5.5)
    body.lineTo(24, 11)
    body.lineTo(24, 25)
    body.quadTo(24, 26.5, 22.5, 26.5)
    body.lineTo(9.5, 26.5)
    body.quadTo(8, 26.5, 8, 25)
    body.lineTo(8, 7)
    body.quadTo(8, 5.5, 9, 5.5)
    p.setPen(_pen(_c("blue"), 1.6))
    p.setBrush(QBrush(_c("blue_soft")))
    p.drawPath(body)
    p.setBrush(QBrush(_c("blue")))
    p.drawPolygon(QPolygonF([QPointF(18.5, 5.5), QPointF(24, 11), QPointF(18.5, 11)]))
    p.setPen(_pen(_c("blue"), 1.5))
    for y, x2 in ((15.5, 20.5), (19, 20.5), (22.5, 17)):
        p.drawLine(QPointF(11.5, y), QPointF(x2, y))


def _draw_folder(p: QPainter) -> None:
    back = QPainterPath()
    back.moveTo(5, 9.5)
    back.quadTo(5, 7.5, 7, 7.5)
    back.lineTo(12.5, 7.5)
    back.lineTo(15, 10)
    back.lineTo(25, 10)
    back.quadTo(27, 10, 27, 12)
    back.lineTo(27, 23)
    back.quadTo(27, 25, 25, 25)
    back.lineTo(7, 25)
    back.quadTo(5, 25, 5, 23)
    back.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("amber_deep")))
    p.drawPath(back)
    p.setBrush(QBrush(_c("amber_soft")))
    p.drawRoundedRect(QRectF(5, 12.5, 22, 12.5), 2.0, 2.0)


def _draw_new_window(p: QPainter) -> None:
    p.setPen(_pen(_c("teal"), 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(5.5, 7, 21, 18), 2.5, 2.5)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("teal")))
    p.drawRoundedRect(QRectF(5.5, 7, 21, 5), 2.5, 2.5)
    p.drawRect(QRectF(5.5, 9.5, 21, 2.5))
    p.setPen(_pen(_c("teal"), 2.0))
    p.drawLine(QPointF(16, 15), QPointF(16, 22))
    p.drawLine(QPointF(12.5, 18.5), QPointF(19.5, 18.5))


def _draw_sort(p: QPainter) -> None:
    p.setPen(_pen(_c("indigo"), 2.0))
    p.drawLine(QPointF(6, 10), QPointF(17, 10))
    p.drawLine(QPointF(6, 16), QPointF(14, 16))
    p.drawLine(QPointF(6, 22), QPointF(10.5, 22))
    p.drawLine(QPointF(22.5, 8.5), QPointF(22.5, 23.5))
    p.drawLine(QPointF(19, 20), QPointF(22.5, 23.5))
    p.drawLine(QPointF(26, 20), QPointF(22.5, 23.5))


def _draw_globe(p: QPainter) -> None:
    fill = _c("sky")
    fill.setAlpha(55)
    p.setPen(_pen(_c("sky"), 1.7))
    p.setBrush(QBrush(fill))
    p.drawEllipse(QPointF(16, 16), 10, 10)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(16, 16), 4.6, 10)
    p.drawLine(QPointF(6, 16), QPointF(26, 16))
    p.drawLine(QPointF(7.6, 11), QPointF(24.4, 11))
    p.drawLine(QPointF(7.6, 21), QPointF(24.4, 21))


def _draw_badge(p: QPainter, color: str) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c(color)))
    p.drawEllipse(QPointF(16, 16), 11, 11)


def _draw_autoplay(p: QPainter) -> None:
    _draw_badge(p, "green")
    p.setBrush(QBrush(QColor("#ffffff")))
    p.drawPolygon(QPolygonF([QPointF(13.2, 10.5), QPointF(22, 16), QPointF(13.2, 21.5)]))


def _draw_info(p: QPainter) -> None:
    _draw_badge(p, "blue")
    p.setBrush(QBrush(QColor("#ffffff")))
    p.drawEllipse(QPointF(16, 10.6), 1.7, 1.7)
    p.drawRoundedRect(QRectF(14.5, 14, 3, 9.5), 1.3, 1.3)


def _draw_resume(p: QPainter) -> None:
    color = _c("orange")
    p.setPen(_pen(color, 2.2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(5.5, 5.5, 21, 21), 120 * 16, 285 * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawPolygon(QPolygonF([QPointF(4.0, 7.5), QPointF(11.8, 6.6), QPointF(8.2, 13.6)]))
    p.drawPolygon(QPolygonF([QPointF(13.5, 11.5), QPointF(21, 16), QPointF(13.5, 20.5)]))


def _draw_apps(p: QPainter) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    for (x, y), color in zip(((6.5, 6.5), (17, 6.5), (6.5, 17), (17, 17)), ("red", "green", "blue", "amber")):
        p.setBrush(QBrush(_c(color)))
        p.drawRoundedRect(QRectF(x, y, 8.5, 8.5), 2.2, 2.2)


def _draw_shell(p: QPainter) -> None:
    p.setPen(_pen(_c("purple"), 1.6))
    p.setBrush(QBrush(_c("purple_soft")))
    p.drawRoundedRect(QRectF(6, 6, 20, 20), 3, 3)
    p.setPen(_pen(_c("purple"), 1.8))
    for y in (11.5, 16, 20.5):
        p.drawLine(QPointF(10, y), QPointF(22, y))


def _draw_update(p: QPainter) -> None:
    color = _c("emerald")
    p.setPen(_pen(color, 2.2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(6.5, 6.5, 19, 19), 30 * 16, 150 * 16)
    p.drawArc(QRectF(6.5, 6.5, 19, 19), 210 * 16, 150 * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawPolygon(QPolygonF([QPointF(24.6, 5.8), QPointF(25.4, 12.6), QPointF(18.8, 10.6)]))
    p.drawPolygon(QPolygonF([QPointF(7.4, 26.2), QPointF(6.6, 19.4), QPointF(13.2, 21.4)]))


def _draw_exit(p: QPainter) -> None:
    """Kapı + dışarı ok: tepsi menüsündeki "Çıkış" satırı."""
    color = _c("red")
    p.setPen(_pen(color, 2.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolyline(QPolygonF([QPointF(14.5, 6.5), QPointF(6.5, 6.5), QPointF(6.5, 25.5), QPointF(14.5, 25.5)]))
    p.drawLine(QPointF(12.0, 16.0), QPointF(26.0, 16.0))
    p.drawPolyline(QPolygonF([QPointF(21.0, 11.0), QPointF(26.0, 16.0), QPointF(21.0, 21.0)]))


def _draw_copy(p: QPainter) -> None:
    p.setPen(_pen(_c("sky"), 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(12, 5.5, 13, 15.5), 2, 2)
    p.setBrush(QBrush(_c("sky_soft")))
    p.drawRoundedRect(QRectF(7, 10.5, 13, 15.5), 2, 2)


def _draw_print(p: QPainter) -> None:
    p.setPen(_pen(_c("cyan"), 1.6))
    p.setBrush(QBrush(QColor("#ffffff")))
    p.drawRect(QRectF(10, 5.5, 12, 7))
    p.setBrush(QBrush(_c("cyan")))
    p.drawRoundedRect(QRectF(5.5, 12, 21, 10), 2.2, 2.2)
    p.setBrush(QBrush(QColor("#ffffff")))
    p.drawRect(QRectF(10, 18.5, 12, 7.5))


def _draw_trash(p: QPainter, cross: bool = False) -> None:
    p.setPen(_pen(_c("red"), 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(7.5, 10), QPointF(24.5, 10))
    p.drawLine(QPointF(13, 10), QPointF(13.8, 6.8))
    p.drawLine(QPointF(19, 10), QPointF(18.2, 6.8))
    p.drawLine(QPointF(13.8, 6.8), QPointF(18.2, 6.8))
    p.setBrush(QBrush(_c("red_soft")))
    p.drawRoundedRect(QRectF(9.5, 10, 13, 15.5), 2, 2)
    if cross:
        p.drawLine(QPointF(13, 14.5), QPointF(19, 21))
        p.drawLine(QPointF(19, 14.5), QPointF(13, 21))
    else:
        p.drawLine(QPointF(14, 14), QPointF(14, 21.5))
        p.drawLine(QPointF(18, 14), QPointF(18, 21.5))


def _draw_save(p: QPainter) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("blue")))
    p.drawRoundedRect(QRectF(6, 6, 20, 20), 3, 3)
    p.setBrush(QBrush(_c("blue_soft")))  # beyaz değil: açık menüde zeminle birleşiyordu
    p.drawRoundedRect(QRectF(10, 6, 12, 7.5), 1, 1)
    p.drawRoundedRect(QRectF(9.5, 17, 13, 7.5), 1.2, 1.2)
    p.setBrush(QBrush(_c("blue")))
    p.drawRect(QRectF(18, 7.5, 2.4, 4.5))


def _draw_rename(p: QPainter) -> None:
    p.save()
    p.translate(16, 16)
    p.rotate(45)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("amber")))
    p.drawRoundedRect(QRectF(-3.2, -11.5, 6.4, 17), 1.2, 1.2)
    p.setBrush(QBrush(_c("amber_deep")))
    p.drawRect(QRectF(-3.2, -11.5, 6.4, 3.4))
    p.setBrush(QBrush(QColor("#fde68a")))
    p.drawPolygon(QPolygonF([QPointF(-3.2, 5.5), QPointF(3.2, 5.5), QPointF(0, 11)]))
    p.restore()
    p.setPen(_pen(_c("amber_deep"), 1.6))
    p.drawLine(QPointF(17, 26), QPointF(26, 26))


def _draw_folder_badge(p: QPainter, plus: bool) -> None:
    _draw_folder(p)
    color = _c("green") if plus else _c("blue")
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawEllipse(QPointF(23, 22), 7, 7)
    p.setPen(_pen(QColor("#ffffff"), 1.9))
    if plus:
        p.drawLine(QPointF(23, 18.6), QPointF(23, 25.4))
        p.drawLine(QPointF(19.6, 22), QPointF(26.4, 22))
    else:
        p.drawLine(QPointF(19.4, 22), QPointF(26, 22))
        p.drawLine(QPointF(23.2, 19.2), QPointF(26, 22))
        p.drawLine(QPointF(23.2, 24.8), QPointF(26, 22))


def _draw_crop(p: QPainter) -> None:
    p.setPen(_pen(_c("green"), 2.4))
    p.drawLine(QPointF(10, 4.5), QPointF(10, 22))
    p.drawLine(QPointF(10, 22), QPointF(27.5, 22))
    p.drawLine(QPointF(4.5, 10), QPointF(22, 10))
    p.drawLine(QPointF(22, 10), QPointF(22, 27.5))


def _draw_open_with(p: QPainter) -> None:
    p.setPen(_pen(_c("purple"), 1.8))
    p.setBrush(QBrush(_c("purple_soft")))
    p.drawRoundedRect(QRectF(5.5, 9.5, 17, 17), 2.5, 2.5)
    p.setPen(_pen(_c("purple"), 2.1))
    p.drawLine(QPointF(14, 18), QPointF(26, 6))
    p.drawLine(QPointF(19, 6), QPointF(26, 6))
    p.drawLine(QPointF(26, 6), QPointF(26, 13))


def _draw_view(p: QPainter) -> None:
    eye = QPainterPath()
    eye.moveTo(4, 16)
    eye.quadTo(16, 4.5, 28, 16)
    eye.quadTo(16, 27.5, 4, 16)
    p.setPen(_pen(_c("indigo"), 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(eye)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("indigo")))
    p.drawEllipse(QPointF(16, 16), 4.6, 4.6)


def _draw_slideshow(p: QPainter) -> None:
    p.setPen(_pen(_c("green"), 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(4.5, 7, 23, 16), 2.5, 2.5)
    p.drawLine(QPointF(12, 26.5), QPointF(20, 26.5))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("green")))
    p.drawPolygon(QPolygonF([QPointF(13.5, 11), QPointF(20.5, 15), QPointF(13.5, 19)]))


def _draw_speed(p: QPainter) -> None:
    p.setPen(_pen(_c("orange"), 2.2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(5, 7, 22, 22), 0, 180 * 16)
    p.drawLine(QPointF(16, 18), QPointF(22, 11.5))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("orange")))
    p.drawEllipse(QPointF(16, 18), 2.4, 2.4)


def _draw_save_frame(p: QPainter) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("teal")))
    p.drawRoundedRect(QRectF(4.5, 10, 23, 15.5), 3, 3)
    p.drawRoundedRect(QRectF(11, 7, 10, 5), 1.5, 1.5)
    p.setBrush(QBrush(QColor("#ffffff")))
    p.drawEllipse(QPointF(16, 17.8), 5, 5)
    p.setBrush(QBrush(_c("teal")))
    p.drawEllipse(QPointF(16, 17.8), 2.6, 2.6)


def _draw_fit(p: QPainter) -> None:
    p.setPen(_pen(_c("sky"), 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(4.5, 6.5, 23, 19), 2.5, 2.5)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("sky_soft")))
    p.drawRoundedRect(QRectF(9, 10.5, 14, 11), 1.5, 1.5)


def _draw_actual(p: QPainter) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("sky")))
    p.drawRoundedRect(QRectF(8, 8, 3, 16), 1, 1)
    p.drawRoundedRect(QRectF(21, 8, 3, 16), 1, 1)
    p.drawEllipse(QPointF(16, 12.5), 1.9, 1.9)
    p.drawEllipse(QPointF(16, 19.5), 1.9, 1.9)


def _draw_fill(p: QPainter) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(_c("sky")))
    p.drawRoundedRect(QRectF(4.5, 6.5, 23, 19), 2.5, 2.5)
    p.setPen(_pen(QColor("#ffffff"), 1.8))
    p.drawLine(QPointF(10, 12), QPointF(22, 20))
    p.drawLine(QPointF(10, 12), QPointF(10, 15.5))
    p.drawLine(QPointF(10, 12), QPointF(13.6, 12))
    p.drawLine(QPointF(22, 20), QPointF(22, 16.5))
    p.drawLine(QPointF(22, 20), QPointF(18.4, 20))


_MENU_DRAWERS = {
    "save": _draw_save,
    "rename": _draw_rename,
    "move_to": lambda p: _draw_folder_badge(p, plus=False),
    "copy_to": lambda p: _draw_folder_badge(p, plus=True),
    "crop": _draw_crop,
    "open_with": _draw_open_with,
    "view": _draw_view,
    "slideshow": _draw_slideshow,
    "speed": _draw_speed,
    "save_frame": _draw_save_frame,
    "fit": _draw_fit,
    "actual": _draw_actual,
    "fill": _draw_fill,
    "settings": _draw_gear,
    "file": _draw_document,
    "folder": _draw_folder,
    "new_window": _draw_new_window,
    "sort": _draw_sort,
    "globe": _draw_globe,
    "autoplay": _draw_autoplay,
    "resume": _draw_resume,
    "info": _draw_info,
    "apps": _draw_apps,
    "shell": _draw_shell,
    "update": _draw_update,
    "exit": _draw_exit,
    "copy": _draw_copy,
    "print": _draw_print,
    "trash": _draw_trash,
    "trash_forever": lambda p: _draw_trash(p, cross=True),
}

# Var olan çizgi simgeleri: menüde tek renkle yeterince belirgin olanlar.
_MENU_TINTED = {
    "play": lambda: play_icon(20, _c("green")),
    "pause": lambda: pause_icon(20, _c("amber")),
    "loop": lambda: loop_icon(_c("emerald")),
    "frame_back": lambda: frame_step_icon(False, _c("indigo")),
    "frame_fwd": lambda: frame_step_icon(True, _c("indigo")),
    "tracks": lambda: tracks_icon(_c("yellow")),
    "rotate_right": lambda: rotate_icon(True, _c("teal")),
    "rotate_left": lambda: rotate_icon(False, _c("teal")),
    "fullscreen": lambda: fullscreen_icon(_c("indigo")),
    "undo": lambda: undo_icon(_c("orange")),
}

_MENU_ICON_CACHE: dict[str, QIcon] = {}
MENU_ICON_NAMES = frozenset(_MENU_DRAWERS) | frozenset(_MENU_TINTED)


def menu_icon(name: str) -> QIcon:
    """Menü satırı için renkli simge (önbellekli; QApplication kurulduktan sonra çağrılmalı)."""
    key = f"{name}|{_accent()}"  # vurgu değişince renkler yeniden çizilir
    icon = _MENU_ICON_CACHE.get(key)
    if icon is None:
        icon = _hidpi_icon(_MENU_DRAWERS[name]) if name in _MENU_DRAWERS else _MENU_TINTED[name]()
        _MENU_ICON_CACHE[key] = icon
    return icon
