"""Mesaj kutusu sığma denetimi (hem offscreen testte hem gerçek platform yardımcısında kullanılır).

Denetler: genişlik 360..560, yükseklik <= ekranın %70'i, metin blokları belge yüksekliğini ve en uzun satırı
barındırıyor (kesilme yok), tüm etiketlerde sizeHint <= boyut, düğmeler metnine yetiyor, çocuklar kapsayıcıdan
taşmıyor, düzen asgari boyutu pencereden büyük değil, kaydırma görünümü içerikten dar değil, düğmeler üst üste binmiyor.
"""
from __future__ import annotations

import math

from PyQt6.QtWidgets import QAbstractButton, QLabel, QScrollArea, QWidget


def audit_box(box, avail_h: int, avail_w: int | None = None, tag: str = "") -> list[str]:
    from app.ui import message_box as mb

    out: list[str] = []

    def add(kind: str, detail: str) -> None:
        out.append(f"[{tag}] {kind}: {detail}")

    chrome_w = box.width() - 2 * mb._SHADOW
    if avail_w is None or avail_w >= mb._MAX_W + 2 * mb._SHADOW + 24:
        # 560'ı yalnızca düğme satırı gerektiriyorsa aşabilir
        if chrome_w < mb._MIN_W or (chrome_w > mb._MAX_W and box._row_min_w < chrome_w - 1):
            add("genişlik", f"{chrome_w}px 360..560 dışında (düğme satırı {box._row_min_w}px)")
    if box.height() > avail_h * mb._MAX_H_RATIO + 1:
        add("yükseklik", f"{box.height()}px > ekranın %70'i ({avail_h * mb._MAX_H_RATIO:.0f}px)")
    lay = box.layout()
    if lay is not None:
        mn = lay.minimumSize()
        if mn.height() > box.height() + 1 or mn.width() > box.width() + 1:
            add("düzen sıkışmış", f"asgari {mn.width()}x{mn.height()} > pencere {box.width()}x{box.height()}")

    for blk in box._blocks:
        doc = blk._measure_doc(blk.width())
        need_h = math.ceil(doc.size().height())
        if need_h > blk.height():
            add("metin yüksekliği", f"gerekli {need_h}px > var {blk.height()}px: {blk.toPlainText()[:30]!r}")
        if doc.idealWidth() > blk.width() + 1:
            add("metin genişliği", f"en uzun satır {doc.idealWidth():.0f}px > {blk.width()}px")
        if blk.verticalScrollBar().maximum() > 0:
            add("metin kendi içinde kayıyor", blk.toPlainText()[:30])
    scroll = box._scroll
    if scroll is not None and box._content is not None:
        if scroll.viewport().width() < box._content.width():
            add("kaydırma görünümü dar", f"{scroll.viewport().width()}px < içerik {box._content.width()}px")
        if scroll.horizontalScrollBar().isVisible():
            add("yatay kaydırma", "belirdi")
    for w in box.findChildren(QLabel):
        if not w.text() or w.__class__.__name__ == "ElidedLabel" or not w.isVisible():
            continue
        sh = w.sizeHint()
        if sh.width() > w.width() + 1 or sh.height() > w.height() + 1:
            add("etiket sizeHint", f"{sh.width()}x{sh.height()} > {w.width()}x{w.height()}: {w.text()[:30]!r}")
    buttons = [b for b in box.findChildren(QAbstractButton) if b.isVisible() and b.text()]
    for b in buttons:
        if b.width() < b.sizeHint().width() - 1:
            add("düğme genişliği", f"{b.text()!r}: {b.width()}px < {b.sizeHint().width()}px")
        if b.height() < min(b.sizeHint().height(), 30):
            add("düğme yüksekliği", f"{b.text()!r}: {b.height()}px")
    for i, a in enumerate(buttons):
        for b in buttons[i + 1:]:
            if a.parentWidget() is b.parentWidget() and a.geometry().intersects(b.geometry()):
                add("düğmeler üst üste", f"{a.text()!r} / {b.text()!r}")
    for w in box.findChildren(QWidget):
        par = w.parentWidget()
        if par is None or not w.isVisible() or isinstance(par, QScrollArea) or par is scroll.viewport():
            continue
        if par is box._content:
            continue
        g = w.geometry()
        if g.right() > par.width() + 1 or g.bottom() > par.height() + 1 or g.left() < -1 or g.top() < -1:
            add("ebeveyn dışına taşıyor", f"{w.objectName() or w.__class__.__name__} {g.x()},{g.y()} {g.width()}x{g.height()} "
                                         f"ebeveyn {par.width()}x{par.height()}")
    return out
