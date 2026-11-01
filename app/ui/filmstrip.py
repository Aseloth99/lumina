"""Klasör film şeridi — çubuğu doldurur, Fotoğraflar gibi komşu sırası."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QModelIndex, QObject, QPointF, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from app.core.matcher import VIDEO_EXTS, sidecar_badge_names
from app.core.qt_safe import safe_emit
from app.core.thumb_loader import ThumbWorker

STRIP_RADIUS = 16
CELL = 48


class _BadgeSignals(QObject):
    done = pyqtSignal(list, int)  # Live sidecar'ı olan dosya adları (küçük harf), token


class _BadgeWorker(QRunnable):
    """Şerit hücrelerinin Live rozetini GUI dışında bulur (dosya başına stat yerine tek dizin okuması)."""

    def __init__(self, paths: list[Path], token: int) -> None:
        super().__init__()
        self._paths = paths
        self._token = token
        self.signals = _BadgeSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            live = sorted(sidecar_badge_names(self._paths))
        except Exception:  # noqa: BLE001 — rozet yoksa hücre sade kalır
            return
        safe_emit(self.signals, "done", live, self._token)


class FilmstripDelegate(QStyledItemDelegate):
    """Küçük resimleri dikey ve yatayda kusursuz ortalar; seçim ve vurgu çerçevelerini çizer."""

    def __init__(self, parent: Filmstrip | None = None) -> None:
        super().__init__(parent)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(CELL, 50)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = option.rect
        icon = index.data(Qt.ItemDataRole.DecorationRole)

        widget = option.widget
        target_size = widget.iconSize() if widget is not None else QSize(40, 40)

        if isinstance(icon, QIcon) and not icon.isNull():
            actual = icon.actualSize(target_size)
            w = max(1, min(actual.width(), rect.width() - 4))
            h = max(1, min(actual.height(), rect.height() - 4))
            tx = rect.x() + (rect.width() - w) // 2
            ty = rect.y() + (rect.height() - h) // 2
            thumb_rect = QRect(tx, ty, w, h)

            # Görseli yumuşak yuvarlak köşeli (4px) çiz
            pix = icon.pixmap(actual)
            if not pix.isNull():
                path = QPainterPath()
                path.addRoundedRect(QRectF(thumb_rect), 4.0, 4.0)
                painter.save()
                painter.setClipPath(path)
                painter.drawPixmap(thumb_rect, pix)
                painter.restore()

            # Medya türü rozeti (Video / Canlı Fotoğraf) — sağ alt köşede zarif mini rozet
            media_type = index.data(Qt.ItemDataRole.UserRole + 1)
            if media_type in ("video", "live"):
                bw, bh = 14.0, 11.0
                bx = float(thumb_rect.right()) - bw - 1.5
                by = float(thumb_rect.bottom()) - bh - 1.5
                badge_box = QRectF(bx, by, bw, bh)
                painter.save()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(0, 0, 0, 185))
                painter.drawRoundedRect(badge_box, 2.5, 2.5)
                if media_type == "video":
                    painter.setBrush(QColor(255, 255, 255, 240))
                    tri = QPolygonF([
                        QPointF(bx + 4.5, by + 2.5),
                        QPointF(bx + 10.5, by + 5.5),
                        QPointF(bx + 4.5, by + 8.5)
                    ])
                    painter.drawPolygon(tri)
                elif media_type == "live":
                    cx, cy = bx + 7.0, by + 5.5
                    painter.setBrush(QColor(255, 255, 255, 240))
                    painter.drawEllipse(QPointF(cx, cy), 1.6, 1.6)
                    painter.setPen(QPen(QColor(255, 255, 255, 220), 0.9))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(QPointF(cx, cy), 3.4, 3.4)
                painter.restore()

            # Seçili ve fare üzerine gelme (hover) durumları
            is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
            is_hover = bool(option.state & QStyle.StateFlag.State_MouseOver)

            parent_strip = self.parent()
            is_light = (
                isinstance(parent_strip, Filmstrip)
                and parent_strip._placeholder_color.lightness() > 128
            )

            if is_selected:
                from app.resources import theme

                pal = theme.palette("light" if is_light else "dark", theme.active_accent())
                accent = QColor(pal["accent_edge"])
                sel_rect = QRectF(thumb_rect).adjusted(-2.5, -2.5, 2.5, 2.5)
                painter.setPen(QPen(accent, 1.8))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(sel_rect, 6.0, 6.0)
            elif is_hover:
                hover_pen = (
                    QPen(QColor(97, 97, 97, 140), 1.2)
                    if is_light
                    else QPen(QColor(158, 158, 158, 150), 1.2)
                )
                hover_rect = QRectF(thumb_rect).adjusted(-2.0, -2.0, 2.0, 2.0)
                painter.setPen(hover_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(hover_rect, 5.0, 5.0)

        painter.restore()


def _icon_from_pixmap(pix: QPixmap) -> QIcon:
    """Seçili durumda Qt'nin mavi renk yıkamasını kapat."""
    icon = QIcon()
    for mode in (
        QIcon.Mode.Normal,
        QIcon.Mode.Active,
        QIcon.Mode.Selected,
        QIcon.Mode.Disabled,
    ):
        icon.addPixmap(pix, mode, QIcon.State.Off)
        icon.addPixmap(pix, mode, QIcon.State.On)
    return icon


def _placeholder(size: int = 40, fill: QColor | None = None) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(fill or QColor(36, 36, 36))
    painter = QPainter(pix)
    painter.setPen(QColor(98, 98, 98, 80))
    painter.drawRect(0, 0, size - 1, size - 1)
    painter.end()
    return _icon_from_pixmap(pix)


def strip_window(count: int, current: int, radius: int = STRIP_RADIUS) -> tuple[int, int]:
    start = max(0, current - radius)
    end = min(count, current + radius + 1)
    return start, end


def strip_span(
    count: int,
    current: int,
    slots: int,
    old_start: int = 0,
    old_count: int = 0,
) -> tuple[int, int]:
    """Görünen kutuları doldur; mevcut kare içerideyse pencereyi kaydırma."""
    if count <= 0:
        return 0, 0
    slots = max(1, min(max(1, slots), count))
    old_end = old_start + old_count
    if (
        old_count == slots
        and 0 <= old_start < old_end <= count
        and old_start <= current < old_end
    ):
        edge = 2 if slots > 8 else 1
        left = old_start if old_start == 0 else old_start + edge
        right = old_end if old_end == count else old_end - edge
        if left <= current < right:
            return old_start, old_end
    start = max(0, current - slots // 2)
    end = min(count, start + slots)
    start = max(0, end - slots)
    return start, end


class Filmstrip(QListWidget):
    activated = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("filmstrip")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.viewport().setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListWidget.Movement.Static)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setIconSize(QSize(40, 40))
        self.setGridSize(QSize(CELL, 50))
        self.setItemAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.setSpacing(2)
        self.setFixedHeight(50)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setSelectionRectVisible(False)
        self.setUniformItemSizes(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setItemDelegate(FilmstripDelegate(self))
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.itemClicked.connect(self._on_item)
        self._token = 0
        self._offset = 0
        self._current = 0
        self._names: list[str] = []
        self._index: dict[str, int] = {}
        self._files: list[Path] = []
        self._radius: int | None = None
        self._thumb_cache: dict[str, QIcon] = {}
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(2)
        self._placeholder_color = QColor(36, 36, 36)
        self.hide()

    def shutdown(self) -> None:
        self._token += 1
        self._pool.waitForDone(1200)
        self._thumb_cache.clear()

    def set_placeholder_color(self, color: QColor) -> None:
        self._placeholder_color = QColor(color)

    @staticmethod
    def _cache_key(path: Path | str) -> str:
        return str(path).casefold()

    def clear_strip(self) -> None:
        self._token += 1
        self._offset = 0
        self._current = 0
        self._names = []
        self._index = {}
        self._files = []
        self._radius = None
        self._thumb_cache.clear()
        self.clear()
        self.hide()

    def visible_slots(self) -> int:
        cell = max(self.gridSize().width(), CELL)
        width = self.viewport().width()
        if width <= 1:
            width = self.width()
        if width <= 1:
            return 7
        avail = max(cell, width - 20)
        return max(1, avail // cell)

    def set_files(self, files: list[Path], current: int) -> None:
        self.set_window(files, current)

    def set_window(self, files: list[Path], current: int, radius: int | None = None) -> None:
        if len(files) <= 1:
            self.clear_strip()
            return
        self._files = list(files)
        self._current = current
        if radius is None:
            start, end = strip_span(
                len(files),
                current,
                self.visible_slots(),
                self._offset,
                self.count(),
            )
            self._radius = None
        else:
            start, end = strip_window(len(files), current, radius)
            self._radius = radius
        slice_files = files[start:end]
        names = [path.name for path in slice_files]
        if names == self._names and start == self._offset:
            self.set_current(current)
            return
        self._token += 1
        token = self._token
        self._offset = start
        self._names = names
        self._index = {name.lower(): i for i, name in enumerate(names)}
        self.clear()
        placeholder = _placeholder(40, fill=self._placeholder_color)
        cell_w = self.gridSize().width()
        for path in slice_files:
            cached = self._thumb_cache.get(self._cache_key(path), placeholder)
            item = QListWidgetItem(cached, "")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            suf = path.suffix.lower()
            if suf in VIDEO_EXTS:
                m_type = "video"
            elif suf == ".livp":
                m_type = "live"
            else:
                m_type = "image"
            item.setData(Qt.ItemDataRole.UserRole + 1, m_type)
            item.setSizeHint(QSize(cell_w, 50))
            self.addItem(item)
            if cached is placeholder:
                worker = ThumbWorker(str(path), token)
                worker.signals.loaded.connect(self._on_thumb)
                self._pool.start(worker)
        badge = _BadgeWorker([p for p in slice_files if p.suffix.lower() not in VIDEO_EXTS and p.suffix.lower() != ".livp"], token)
        badge.signals.done.connect(self._on_badges)
        self._pool.start(badge)
        self.setViewportMargins(0, 0, 0, 0)
        self.set_current(current)

    def _on_badges(self, live: list, token: int) -> None:
        if token != self._token:
            return
        names = set(live)
        for row in range(self.count()):
            item = self.item(row)
            if item is None or item.data(Qt.ItemDataRole.UserRole + 1) != "image":
                continue
            if Path(str(item.data(Qt.ItemDataRole.UserRole))).name.lower() in names:
                item.setData(Qt.ItemDataRole.UserRole + 1, "live")

    def set_current(self, folder_index: int) -> None:
        self._current = folder_index
        local = folder_index - self._offset
        if 0 <= local < self.count():
            self.setCurrentRow(local)
            item = self.item(local)
            if item is not None:
                self.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)

    def _on_item(self, item: QListWidgetItem) -> None:
        self.activated.emit(self._offset + self.row(item))

    def _on_thumb(self, path: str, image, token: int) -> None:
        pix = QPixmap.fromImage(image).scaled(
            40,
            40,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        icon = _icon_from_pixmap(pix)
        key = self._cache_key(path)
        if len(self._thumb_cache) >= 512 and key not in self._thumb_cache:
            self._thumb_cache.pop(next(iter(self._thumb_cache)), None)
        self._thumb_cache[key] = icon
        if token != self._token:
            return
        name = Path(path).name.lower()
        idx = self._index.get(name)
        if idx is None or idx >= self.count():
            return
        item = self.item(idx)
        if item is not None:
            item.setIcon(icon)

    def wheelEvent(self, event) -> None:  # noqa: N802
        bar = self.horizontalScrollBar()
        delta = event.angleDelta().y() or event.angleDelta().x()
        if bar.maximum() > 0 and delta:
            bar.setValue(bar.value() - int(delta / 3))
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if (
            self._files
            and self._radius is None
            and self.isVisible()
            and self.width() >= CELL * 3
        ):
            self.set_window(self._files, self._current, radius=None)
