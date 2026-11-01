"""Kırpma kipi: tuvalin üstünde sürüklenebilir kırpma çerçevesi + küçük araç çubuğu.

Yalnızca kullanıcı "Kırp…" dediğinde oluşur. Çerçeve, görüntünün oranlarında (0..1) tutulur; böylece
pencere boyutu değişse de doğru kalır. Sonuç `finished` sinyaliyle (sol, üst, sağ, alt) oranları veya
iptalde None olarak döner.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton, QWidget

_HANDLE = 9.0  # tutamaç yarıçapı (px)
_HIT = 14.0  # tutamaç yakalama mesafesi (px)
_MIN_FRAC = 0.03  # çerçeve en az görüntünün %3'ü


def clamp_rect(rect: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    l, t, r, b = rect
    l, t = max(0.0, min(l, 1.0 - _MIN_FRAC)), max(0.0, min(t, 1.0 - _MIN_FRAC))
    r, b = min(1.0, max(r, l + _MIN_FRAC)), min(1.0, max(b, t + _MIN_FRAC))
    return l, t, r, b


def fit_ratio(
    rect: tuple[float, float, float, float], ratio: float, image_aspect: float, anchor: str = "center"
) -> tuple[float, float, float, float]:
    """`rect`i (görüntü oranlarında) piksel en-boy oranı `ratio` (= genişlik/yükseklik) olacak şekilde ayarla.

    `image_aspect` = görüntü genişliği / yüksekliği. Çerçeve en fazla küçülür (taşmaz), merkez korunur.
    """
    l, t, r, b = rect
    w, h = r - l, b - t
    # piksel oranı: (w*W)/(h*H) = ratio  ->  w/h = ratio / image_aspect
    target = ratio / image_aspect
    if w / h > target:
        w = h * target
    else:
        h = w / target
    cx, cy = (l + r) / 2, (t + b) / 2
    if anchor == "center":
        l, t = cx - w / 2, cy - h / 2
    l = max(0.0, min(l, 1.0 - w))
    t = max(0.0, min(t, 1.0 - h))
    return l, t, l + w, t + h


class CropOverlay(QWidget):
    finished = pyqtSignal(object)  # (l, t, r, b) | None

    RATIOS = (("free", None), ("1:1", 1.0), ("4:3", 4 / 3), ("16:9", 16 / 9))

    def __init__(self, canvas, labels: dict[str, str]) -> None:
        super().__init__(canvas.viewport())
        self._canvas = canvas
        self._labels = labels
        self._frac = (0.1, 0.1, 0.9, 0.9)
        self._ratio: float | None = None
        self._drag: str | None = None
        self._drag_start = QPointF()
        self._frac_start = self._frac
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setGeometry(canvas.viewport().rect())
        canvas.viewport().installEventFilter(self)

        self._bar = QFrame(self)
        self._bar.setObjectName("cropBar")
        # Tuval 'background: transparent; border: none' stilini çocuklarına da uygular; çubuk kendi (koyu cam)
        # biçimini taşır.
        from app.resources import theme

        pal = theme.palette("dark", theme.active_accent())  # çubuk her zaman koyu cam; vurgu seçili renk
        self._bar.setStyleSheet(
            "QFrame#cropBar { background: rgba(20, 20, 20, 235); border: 1px solid rgba(255, 255, 255, 40);"
            " border-radius: 17px; }"
            "QPushButton { background: rgba(255, 255, 255, 20); color: #e8e8e8; border: 1px solid rgba(255, 255, 255, 40);"
            " border-radius: 8px; padding: 0 12px; font-size: 12px; font-weight: 600; }"
            "QPushButton:hover { background: rgba(255, 255, 255, 46); color: #ffffff; }"
            f"QPushButton:checked, QPushButton#cropSave {{ background: {pal['fill']}; border: 1px solid {pal['accent_edge']};"
            f" color: {pal['on_fill']}; }}"
            f"QPushButton#cropSave:hover {{ background: {pal['fill_hover']}; }}"
        )
        row = QHBoxLayout(self._bar)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for key, ratio in self.RATIOS:
            btn = QPushButton(labels.get(key, key))
            btn.setCheckable(True)
            btn.setFixedHeight(28)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setChecked(ratio is None)
            btn.clicked.connect(lambda _c=False, r=ratio: self._set_ratio(r))
            self._group.addButton(btn)
            row.addWidget(btn)
        row.addSpacing(10)
        self.btn_cancel = QPushButton(labels.get("cancel", "İptal"))
        self.btn_cancel.setFixedHeight(28)
        self.btn_cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_cancel.clicked.connect(lambda: self.finished.emit(None))
        row.addWidget(self.btn_cancel)
        self.btn_save = QPushButton(labels.get("save", "Kaydet"))
        self.btn_save.setObjectName("cropSave")
        self.btn_save.setFixedHeight(28)
        self.btn_save.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_save.clicked.connect(lambda: self.finished.emit(self._frac))
        row.addWidget(self.btn_save)
        self._place_bar()
        self.show()
        self.raise_()
        self.setFocus()

    # ----------------------------------------------------------------- geometri
    def image_rect(self) -> QRectF:
        """Görüntünün (döndürme dahil) bu katmandaki dikdörtgeni."""
        item = self._canvas.pixmap_item
        poly = self._canvas.mapFromScene(item.sceneBoundingRect())
        return QRectF(poly.boundingRect())

    def image_aspect(self) -> float:
        r = self.image_rect()
        return r.width() / r.height() if r.height() > 0 else 1.0

    def frac(self) -> tuple[float, float, float, float]:
        return self._frac

    def _rect_px(self) -> QRectF:
        ir = self.image_rect()
        l, t, r, b = self._frac
        return QRectF(
            ir.left() + l * ir.width(),
            ir.top() + t * ir.height(),
            (r - l) * ir.width(),
            (b - t) * ir.height(),
        )

    def _place_bar(self) -> None:
        self._bar.adjustSize()
        self._bar.move(max(8, (self.width() - self._bar.width()) // 2), max(8, self.height() - self._bar.height() - 16))

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._canvas.viewport() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self._canvas.viewport().rect())
            self._place_bar()
            self.update()
        return super().eventFilter(obj, event)

    # ----------------------------------------------------------------- oran
    def _effective_ratio(self) -> float | None:
        """Dikey görüntüde 4:3 -> 3:4, 16:9 -> 9:16 (kare değişmez)."""
        if self._ratio is None:
            return None
        if self.image_aspect() < 1.0 and self._ratio != 1.0:
            return 1.0 / self._ratio
        return self._ratio

    def _set_ratio(self, ratio: float | None) -> None:
        self._ratio = ratio
        eff = self._effective_ratio()
        if eff is not None:
            self._frac = fit_ratio(self._frac, eff, self.image_aspect())
        self.update()

    # ----------------------------------------------------------------- çizim
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ir = self.image_rect()
        cr = self._rect_px()
        dim = QColor(0, 0, 0, 150)
        p.fillRect(QRectF(ir.left(), ir.top(), ir.width(), cr.top() - ir.top()), dim)
        p.fillRect(QRectF(ir.left(), cr.bottom(), ir.width(), ir.bottom() - cr.bottom()), dim)
        p.fillRect(QRectF(ir.left(), cr.top(), cr.left() - ir.left(), cr.height()), dim)
        p.fillRect(QRectF(cr.right(), cr.top(), ir.right() - cr.right(), cr.height()), dim)
        # üçler kuralı kılavuzu
        guide = QPen(QColor(255, 255, 255, 70), 1)
        p.setPen(guide)
        for i in (1, 2):
            x = cr.left() + cr.width() * i / 3
            y = cr.top() + cr.height() * i / 3
            p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
            p.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))
        p.setPen(QPen(QColor(255, 255, 255, 230), 1.6))
        p.drawRect(cr)
        p.setBrush(QColor(255, 255, 255))
        p.setPen(QPen(QColor(26, 26, 26), 1.2))
        for pt in self._handles(cr).values():
            p.drawEllipse(pt, _HANDLE / 2 + 1, _HANDLE / 2 + 1)

    @staticmethod
    def _handles(cr: QRectF) -> dict[str, QPointF]:
        cx, cy = cr.center().x(), cr.center().y()
        return {
            "tl": cr.topLeft(), "tr": cr.topRight(), "bl": cr.bottomLeft(), "br": cr.bottomRight(),
            "t": QPointF(cx, cr.top()), "b": QPointF(cx, cr.bottom()),
            "l": QPointF(cr.left(), cy), "r": QPointF(cr.right(), cy),
        }

    def _hit_test(self, pos: QPointF) -> str | None:
        cr = self._rect_px()
        for name, pt in self._handles(cr).items():
            if abs(pos.x() - pt.x()) <= _HIT and abs(pos.y() - pt.y()) <= _HIT:
                return name
        if cr.contains(pos):
            return "move"
        return None

    # ----------------------------------------------------------------- fare
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            event.accept()
            return
        if self._bar.geometry().contains(event.position().toPoint()):
            event.ignore()
            return
        self._drag = self._hit_test(event.position())
        self._drag_start = event.position()
        self._frac_start = self._frac
        event.accept()

    _CURSORS = {
        "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
        "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
        "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
        "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
        "move": Qt.CursorShape.SizeAllCursor,
    }

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is None:
            hit = self._hit_test(event.position())
            self.setCursor(self._CURSORS.get(hit, Qt.CursorShape.ArrowCursor))
            event.accept()
            return
        ir = self.image_rect()
        if ir.width() < 1 or ir.height() < 1:
            return
        dx = (event.position().x() - self._drag_start.x()) / ir.width()
        dy = (event.position().y() - self._drag_start.y()) / ir.height()
        l, t, r, b = self._frac_start
        mode = self._drag
        if mode == "move":
            w, h = r - l, b - t
            nl = max(0.0, min(l + dx, 1.0 - w))
            nt = max(0.0, min(t + dy, 1.0 - h))
            self._frac = (nl, nt, nl + w, nt + h)
        else:
            if "l" in mode:
                l += dx
            if "r" in mode:
                r += dx
            if "t" in mode:
                t += dy
            if "b" in mode:
                b += dy
            rect = clamp_rect((min(l, r), min(t, b), max(l, r), max(t, b)))
            if self._ratio is not None:
                rect = self._apply_ratio_on_drag(rect, mode)
            self._frac = rect
        self.update()
        event.accept()

    def _apply_ratio_on_drag(self, rect, mode: str):
        aspect = self.image_aspect()
        ratio = self._effective_ratio() or 1.0
        l, t, r, b = rect
        target = ratio / aspect  # w/h (oran uzayında)
        w, h = r - l, b - t
        # Karşı köşe/kenar sabit kalır: sürüklenen kenarın genişliğine göre yüksekliği türet.
        if mode in ("l", "r"):
            h = w / target
        elif mode in ("t", "b"):
            w = h * target
        else:
            h = w / target
        if "l" in mode:
            nl, nr = r - w, r
        elif "r" in mode:
            nl, nr = l, l + w
        else:
            nl = (l + r) / 2 - w / 2
            nr = nl + w
        if "t" in mode:
            nt, nb = b - h, b
        elif "b" in mode:
            nt, nb = t, t + h
        else:
            nt = (t + b) / 2 - h / 2
            nb = nt + h
        if nl < 0 or nt < 0 or nr > 1 or nb > 1 or nr - nl < _MIN_FRAC or nb - nt < _MIN_FRAC:
            return self._frac  # sınıra dayandı: çerçeve olduğu yerde kalır
        return nl, nt, nr, nb

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag = None
        event.accept()

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.accept()  # kırparken yakınlaştırma yok

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.finished.emit(None)
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finished.emit(self._frac)
        else:
            event.accept()  # diğer tuşlar (Delete, ok tuşları…) sessizce yutulur
