"""Açılır menüden sonra düğmede takılı kalan üzerine gelme (hover) görünümünü temizler.

QMenu açıkken fareyi kendisi yakalar; düğme "fare ayrıldı" olayını hiç almaz. Menü dışarıya tıklanarak kapatılınca
düğme hâlâ fare üstündeymiş gibi :hover ile çizilir ve fare bir daha üstüne gelip gidene dek öyle kalır.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QCursor, QHoverEvent
from PyQt6.QtWidgets import QApplication, QWidget


def settle_hover(widget: QWidget | None) -> None:
    """Fare artık `widget`in üstünde değilse ona bir "ayrıldı" olayı gönderir ve yeniden çizer."""
    if widget is None:
        return
    try:
        inside = widget.isVisible() and widget.rect().contains(widget.mapFromGlobal(QCursor.pos()))
    except RuntimeError:  # pencere kapanırken widget silinmiş olabilir
        return
    if inside or not widget.testAttribute(Qt.WidgetAttribute.WA_UnderMouse):
        return
    widget.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
    QApplication.sendEvent(widget, QEvent(QEvent.Type.Leave))
    if widget.testAttribute(Qt.WidgetAttribute.WA_Hover):
        # Öğe görünümleri (galeri ızgarası) üzerine gelinen karoyu HoverLeave ile bırakır; düz Leave yetmez.
        outside = QPointF(-1.0, -1.0)
        QApplication.sendEvent(widget, QHoverEvent(QEvent.Type.HoverLeave, outside, QPointF(QCursor.pos()), outside))
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
