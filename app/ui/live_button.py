"""İnce cam bar + yuvarlak bağlı nane oynat düğmesi + yan yana film şeridi."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QConicalGradient,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)


def _fmt_ms(ms: int) -> str:
    total = max(0, int(ms) // 1000)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class SeekSlider(QSlider):
    """YouTube tarzı: tıklanınca o noktaya atlar, pürüzsüz daire tutamaç çizer."""

    hover_ratio = pyqtSignal(float)
    hover_ended = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False
        self._pressed = False

    def _ratio_at(self, x: float) -> float:
        margin = 6.0
        grooved = max(1.0, float(self.width()) - 2 * margin)
        return max(0.0, min(1.0, (float(x) - margin) / grooved))

    def _apply_pos(self, x: float) -> None:
        span = max(1, self.maximum() - self.minimum())
        self.setValue(int(self.minimum() + self._ratio_at(x) * span))

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.hover_ended.emit()
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self._apply_pos(event.position().x())
            self.sliderPressed.emit()
            self.sliderMoved.emit(self.value())
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.hover_ratio.emit(self._ratio_at(event.position().x()))
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._apply_pos(event.position().x())
            self.sliderMoved.emit(self.value())
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = False
            self._apply_pos(event.position().x())
            self.sliderReleased.emit()
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        step = max(1, (self.maximum() - self.minimum()) // 40)
        delta = step if event.angleDelta().y() > 0 else -step
        self.setValue(max(self.minimum(), min(self.maximum(), self.value() + delta)))
        self.sliderMoved.emit(self.value())
        self.sliderReleased.emit()
        self.update()
        event.accept()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        if w <= 12 or h <= 4:
            return

        margin = 6.0
        cy = h / 2.0
        groove_h = 3.5
        groove_w = max(1.0, w - 2 * margin)

        span = max(1, self.maximum() - self.minimum())
        ratio = max(0.0, min(1.0, (self.value() - self.minimum()) / float(span)))
        hx = margin + ratio * groove_w

        # Arka plan oluk (groove)
        groove_rect = QRectF(margin, cy - groove_h / 2.0, groove_w, groove_h)
        painter.setPen(Qt.PenStyle.NoPen)
        is_light = getattr(self, "_light_mode", False)
        groove_color = QColor(0, 0, 0, 35) if is_light else QColor(255, 255, 255, 45)
        painter.setBrush(groove_color)
        painter.drawRoundedRect(groove_rect, groove_h / 2.0, groove_h / 2.0)

        # İlerleme (aktif dolgu - nane yeşili)
        active_w = hx - margin
        if active_w > 0:
            active_rect = QRectF(margin, cy - groove_h / 2.0, active_w, groove_h)
            fill_color = QColor(13, 148, 136, 240) if is_light else QColor(45, 212, 166, 235)
            painter.setBrush(fill_color)
            painter.drawRoundedRect(active_rect, groove_h / 2.0, groove_h / 2.0)

        # Tutamaç (pürüzsüz antialiased yuvarlak daire)
        active = self._hover or self._pressed
        r = 6.0 if active else 5.0

        if active:
            # Dış hafif parlama halkası
            halo_r = r + 3.0
            painter.setBrush(QColor(13, 148, 136, 60) if is_light else QColor(45, 212, 166, 75))
            painter.drawEllipse(QPointF(hx, cy), halo_r, halo_r)

        painter.setPen(QPen(QColor(0, 0, 0, 60) if is_light else QColor(255, 255, 255, 180), 1.0))
        painter.setBrush(QColor(255, 255, 255, 255) if is_light else QColor(245, 245, 245, 255))
        painter.drawEllipse(QPointF(hx, cy), r, r)


def _accent_hi() -> str:
    """Yükleniyor durumundaki düğme dolgusu: seçili vurgunun parlak tonu."""
    from app.resources import theme

    return theme.ACCENTS[theme.active_accent()]["hi_dark"]


class GlassOrb(QPushButton):
    """Büyük nane daire, beyaz üçgen / duraklat simgesi, parıltılı cam."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("liveOrb")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(38, 38)
        self.setIconSize(QSize(18, 18))
        self.setText("")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._loading = False
        self._angle = 0
        self._spin = QTimer(self)
        self._spin.timeout.connect(self._on_spin)
        self.hide()

    def set_loading(self, loading: bool) -> None:
        self._loading = bool(loading)
        if self._loading:
            if not self._spin.isActive():
                self._spin.start(16)
        else:
            self._spin.stop()
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def _on_spin(self) -> None:
        self._angle = (self._angle + 10) % 360
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0)
        hover = self.underMouse() and self.isEnabled()
        playing = self.property("playing") == "true"

        # Dış hafif gölge/hale
        if hover:
            halo = QRectF(box).adjusted(-1.5, -1.5, 1.5, 1.5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(45, 212, 166, 60))
            painter.drawEllipse(halo)

        if self._loading:
            fill = QColor(_accent_hi())
            fill.setAlpha(240)
        elif playing:
            fill = QColor(13, 148, 136, 250 if hover else 235)
        else:
            fill = QColor(45, 212, 166, 255 if hover else 245)

        painter.setPen(QPen(QColor(255, 255, 255, 90 if hover else 50), 1.4))
        painter.setBrush(fill)
        painter.drawEllipse(box)

        ring = QRectF(box).adjusted(5.0, 5.0, -5.0, -5.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1.8))
        painter.drawEllipse(ring)

        if self._loading:
            sweep = QRectF(box).adjusted(7.0, 7.0, -7.0, -7.0)
            cone = QConicalGradient(sweep.center(), float(self._angle))
            cone.setColorAt(0.0, QColor(255, 255, 255, 0))
            cone.setColorAt(1.0, QColor(255, 255, 255, 240))
            pen = QPen(cone, 2.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(sweep, 0, 260 * 16)
            return

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 250))
        cx, cy = box.center().x(), box.center().y()
        if playing:
            painter.drawRoundedRect(QRectF(cx - 5.0, cy - 6.0, 3.5, 12.0), 1.2, 1.2)
            painter.drawRoundedRect(QRectF(cx + 1.5, cy - 6.0, 3.5, 12.0), 1.2, 1.2)
        else:
            tri = QPolygonF(
                [
                    QPointF(cx - 4.5, cy - 6.5),
                    QPointF(cx + 7.5, cy),
                    QPointF(cx - 4.5, cy + 6.5),
                ]
            )
            painter.drawPolygon(tri)


class BottomDock(QFrame):
    """Cam kart:
    - Şerit kapalı: Kompakt, ekranın ortasında tek araç kutusu.
    - Şerit açık: Yan yana ikiye bölünür (sol yarı araçlar, sağ yarı film şeridi).
    - Oynatma butonu üst bara yuvarlak kavisle (radius notch) bağlanır.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("bottomDock")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        self._glass = QColor(14, 14, 14, 215)
        self._border = QColor(255, 255, 255, 28)
        self.orb: GlassOrb | None = None
        self.filmstrip: QWidget | None = None
        self._split = False
        self._is_video = False

        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(10, 5, 10, 5)
        self.column.setSpacing(2)

        # İsteğe bağlı oynatma arama çubuğu + süre
        seek_row = QHBoxLayout()
        seek_row.setContentsMargins(4, 0, 4, 0)
        seek_row.setSpacing(6)
        self.lbl_pos = QLabel("0:00")
        self.lbl_pos.setObjectName("seekTime")
        self.lbl_pos.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_pos.setMinimumWidth(32)
        self.lbl_pos.setStyleSheet("color: rgba(232,232,232,0.85); font-size: 10px; font-weight: 600; background: transparent;")
        self.lbl_dur = QLabel("0:00")
        self.lbl_dur.setObjectName("seekTime")
        self.lbl_dur.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_dur.setMinimumWidth(32)
        self.lbl_dur.setStyleSheet("color: rgba(232,232,232,0.85); font-size: 10px; font-weight: 600; background: transparent;")
        self.seek = SeekSlider()
        self.seek.setObjectName("seekBar")
        self.seek.setRange(0, 1000)
        self.seek.setValue(0)
        self.seek.setFixedHeight(16)
        self.seek.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        seek_row.addWidget(self.lbl_pos)
        seek_row.addWidget(self.seek, 1)
        seek_row.addWidget(self.lbl_dur)
        self.seek_row = QWidget()
        self.seek_row.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.seek_row.setLayout(seek_row)
        self._seek_layout = seek_row
        self.seek_row.hide()
        self.column.addWidget(self.seek_row)

        # Ana yatay içerik satırı
        self.main_row = QHBoxLayout()
        self.main_row.setContentsMargins(0, 0, 0, 0)
        self.main_row.setSpacing(8)

        # Sol taraf: Araç kutusu taşıyıcısı
        self.left_pane = QWidget()
        self.left_pane.setObjectName("dockToolsPane")
        self.left_pane.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        left_lay = QHBoxLayout(self.left_pane)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(0)
        left_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.cluster = QWidget()
        self.cluster.setObjectName("dockCluster")
        self.cluster.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.cluster.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.box = QHBoxLayout(self.cluster)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(4)
        self.box.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        left_lay.addWidget(self.cluster)

        # Orta ayırıcı çizgi (dikey 1px cam çizgi)
        self.divider = QFrame()
        self.divider.setFixedWidth(1)
        self.divider.setFixedHeight(22)
        self.divider.setStyleSheet("background-color: rgba(255, 255, 255, 0.16);")
        self.divider.hide()

        # Sağ taraf: Film şeridi taşıyıcısı
        self.strip_host = QWidget()
        self.strip_host.setObjectName("dockStripPane")
        self.strip_host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        strip_lay = QHBoxLayout(self.strip_host)
        strip_lay.setContentsMargins(6, 0, 18, 0)
        strip_lay.setSpacing(0)
        strip_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.strip_host.hide()

        self.main_row.addWidget(self.left_pane, 0, Qt.AlignmentFlag.AlignVCenter)
        self.main_row.addWidget(self.divider, 0, Qt.AlignmentFlag.AlignVCenter)
        self.main_row.addWidget(self.strip_host, 1, Qt.AlignmentFlag.AlignVCenter)

        self.column.addLayout(self.main_row)

    def add_filmstrip(self, strip: QWidget) -> None:
        self.filmstrip = strip
        strip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.strip_host.layout().addWidget(strip)

    def add_seek_widget(self, widget: QWidget) -> None:
        """Arama çubuğu satırının sonuna (süre etiketinden sonra) bir denetim ekle."""
        self._seek_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_video_mode(self, is_video: bool) -> None:
        self._is_video = bool(is_video)
        self.update()

    def set_seek_visible(self, visible: bool) -> None:
        self.seek_row.setVisible(bool(visible))
        self.seek.setVisible(bool(visible))
        if not visible:
            self.seek.setValue(0)
            self.lbl_pos.setText("0:00")
            self.lbl_dur.setText("0:00")
            self.column.setContentsMargins(10, 5, 10, 5)
        else:
            self.column.setContentsMargins(10, 3, 10, 4)
        self.update()
        self.adjustSize()

    def set_seek_times(self, pos_ms: int, dur_ms: int) -> None:
        self.lbl_pos.setText(_fmt_ms(pos_ms))
        self.lbl_dur.setText(_fmt_ms(dur_ms))

    def update_dock_geometry(self, window_width: int) -> None:
        """Pencere genişliğine ve içerik miktarına göre dock boyutunu ayarla."""
        max_dock_w = min(2200 if window_width >= 2560 else 1280, max(360, window_width - 32))
        is_video = bool(self._is_video or (self.seek is not None and self.seek.isVisible()))

        split_active = self._split
        if split_active:
            # Dar pencerede şerit sığmıyorsa (düğme kümesi + şerit pencereden geniş olurdu) şerit geçici gizlenir;
            # pencere genişleyince bu fonksiyon yeniden çalışır ve şerit geri gelir.
            room = max_dock_w - (self.cluster.sizeHint().width() + 16) - 84
            split_active = room >= 100
            for part in (self.divider, self.strip_host, self.filmstrip):
                if part is not None:
                    part.setVisible(split_active)
        if split_active:
            left_w = self.cluster.sizeHint().width() + 16
            files = getattr(self.filmstrip, "_files", [])
            strip_count = len(files)
            cell_w = 50
            if self.filmstrip is not None and hasattr(self.filmstrip, "gridSize"):
                cell_w = self.filmstrip.gridSize().width() or cell_w
            overhead = 84
            avail_for_strip = max(100, max_dock_w - left_w - overhead)
            visible_files = min(strip_count, max(2, avail_for_strip // max(1, cell_w + 2)))
            needed_strip_w = visible_files * (cell_w + 2) + 16
            needed_w = left_w + overhead + needed_strip_w
            target_w = min(needed_w, max_dock_w)
            target_w = max(target_w, left_w + 180)
            if is_video:
                target_w = max(target_w, min(max_dock_w, 640))
            self.setMinimumWidth(int(target_w))
            self.setMaximumWidth(int(target_w))
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        else:
            self.cluster.adjustSize()
            c_w = self.cluster.sizeHint().width()
            self.cluster.setMinimumWidth(c_w)
            base_w = c_w + 28
            if is_video:
                min_w = min(max_dock_w, max(base_w, 620))
                self.setMinimumWidth(int(min_w))
            else:
                self.setMinimumWidth(int(min(max_dock_w, base_w)))
            self.setMaximumWidth(16777215)
            self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
            self.adjustSize()
        self.updateGeometry()
        self.update()

    def set_split(self, split: bool) -> None:
        self._split = bool(split)
        self.divider.setVisible(self._split)
        self.strip_host.setVisible(self._split)
        if self.filmstrip is not None:
            self.filmstrip.setVisible(self._split)

        if self._split:
            self.main_row.setStretch(0, 0)
            self.main_row.setStretch(1, 0)
            self.main_row.setStretch(2, 1)
        else:
            self.main_row.setStretch(0, 0)
            self.main_row.setStretch(1, 0)
            self.main_row.setStretch(2, 0)

        parent = self.parentWidget()
        win = self.window()
        # Genişlik kaynağı: önce ebeveyn dock_wrap / stage, yoksa pencere
        parent_w = parent.width() if parent is not None and parent.width() > 100 else (win.width() if win is not None else 1000)
        self.update_dock_geometry(parent_w)

    def set_glass(self, fill: QColor, border: QColor) -> None:
        self._glass = fill
        self._border = border
        div_alpha = max(20, min(100, border.alpha() + 20))
        self.divider.setStyleSheet(
            f"background-color: rgba({border.red()}, {border.green()}, {border.blue()}, {div_alpha / 255.0:.2f});"
        )
        is_light = fill.lightness() > 128
        self.seek._light_mode = is_light
        time_color = "rgba(26, 26, 26, 0.85)" if is_light else "rgba(232, 232, 232, 0.85)"
        self.lbl_pos.setStyleSheet(f"color: {time_color}; font-size: 10px; font-weight: 600; background: transparent;")
        self.lbl_dur.setStyleSheet(f"color: {time_color}; font-size: 10px; font-weight: 600; background: transparent;")
        self.update()

    def sync_orb(self, visible: bool) -> None:
        if self.orb is not None:
            self.orb.setVisible(visible)
        self.update()
        self.adjustSize()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        has_orb = self.orb is not None and self.orb.isVisible() and self.orb.width() > 0
        has_notch = has_orb and not self._is_video and not self.seek.isVisible()
        top_offset = 6.0 if has_notch else 0.5
        card = QRectF(0.5, top_offset, float(self.width() - 1.0), float(self.height() - top_offset - 1.0))
        r = 18.0

        path = QPainterPath()

        if has_notch:
            # Orb'un tam merkezini al
            orb_r = self.orb.rect()
            orb_center = self.orb.mapTo(self, orb_r.center())
            cx = float(orb_center.x())
            top = float(card.top())
            arch_top = 0.5
            w = 36.0

            # Sol üst köşeden başla
            path.moveTo(card.left(), card.top() + r)
            path.arcTo(card.left(), card.top(), r * 2, r * 2, 180, -90)
            path.lineTo(cx - w, top)

            # Üst bara bağlanan yumuşak radius kavis (sol kavis -> tepe kubbesi -> sağ kavis)
            path.cubicTo(
                QPointF(cx - w + 10, top),
                QPointF(cx - 24, arch_top),
                QPointF(cx - 15, arch_top),
            )
            path.cubicTo(
                QPointF(cx - 7, arch_top - 0.5),
                QPointF(cx + 7, arch_top - 0.5),
                QPointF(cx + 15, arch_top),
            )
            path.cubicTo(
                QPointF(cx + 24, arch_top),
                QPointF(cx + w - 10, top),
                QPointF(cx + w, top),
            )

            # Sağ üst köşe ve geri kalanı
            path.lineTo(card.right() - r, top)
            path.arcTo(card.right() - r * 2, card.top(), r * 2, r * 2, 90, -90)
            path.lineTo(card.right(), card.bottom() - r)
            path.arcTo(card.right() - r * 2, card.bottom() - r * 2, r * 2, r * 2, 0, -90)
            path.lineTo(card.left() + r, card.bottom())
            path.arcTo(card.left(), card.bottom() - r * 2, r * 2, r * 2, 270, -90)
            path.closeSubpath()
        else:
            path.addRoundedRect(card, r, r)

        light_glass = self._glass.lightness() > 128

        # Açık temada yüzeyden ayrışan, koyu temada daha yumuşak gölge.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(40, 40, 40, 34 if light_glass else 50))
        painter.drawRoundedRect(card.translated(0, 1.6), r, r)

        # Cam gövde gradyanı
        fill = QLinearGradient(0, card.top(), 0, card.bottom())
        top_color = QColor(self._glass)
        top_color.setAlpha(min(255, self._glass.alpha() + 10))
        bottom_color = QColor(self._glass)
        fill.setColorAt(0.0, top_color)
        fill.setColorAt(1.0, bottom_color)

        painter.setPen(QPen(self._border, 1.1))
        painter.setBrush(fill)
        painter.drawPath(path)

        # Üst cam vurgu çizgisi (highlight)
        painter.setClipPath(path)
        highlight = QLinearGradient(0, card.top() - 6, 0, card.top() + 14)
        highlight.setColorAt(0.0, QColor(255, 255, 255, 24))
        highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(highlight)
        painter.drawRect(QRectF(card.x(), card.y() - 8, card.width(), 22))


def make_live_orb(parent: QWidget) -> GlassOrb:
    return GlassOrb(parent)
