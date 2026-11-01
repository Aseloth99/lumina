"""QGraphicsView tuvali — pencereyi dolduran sığdırma, zoom/pan, tıklayınca oynat."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPainter, QPixmap, QWheelEvent
from PyQt6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QSizePolicy,
)

from app.ui.drops import FileDropMixin
from app.ui.svg_overlay import SvgOverlayController, SvgPixmapItem, is_svg_path


class PhotoCanvas(FileDropMixin, QGraphicsView):
    activated = pyqtSignal()  # kısa sol tık (sürükleme değil)
    context_requested = pyqtSignal(QPoint)
    zoom_changed = pyqtSignal()
    double_clicked = pyqtSignal()
    # Yeni görüntü ekrana ilk kez çizildi (boyama bittikten sonra, olay döngüsünün sonraki turunda).
    # Ana pencere ilk kareyi geciktirmemesi gereken yan işleri (şerit, ön yükleme, Live araması) buna bağlar.
    frame_shown = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.pixmap_item = SvgPixmapItem()
        self._scene.addItem(self.pixmap_item)
        # SVG: yakınlaştırınca görünen bölge ekran çözünürlüğünde yeniden vektörden çizilir (app/ui/svg_overlay.py)
        self._svg = SvgOverlayController(self)
        self.zoom_changed.connect(self._svg.schedule)
        self.horizontalScrollBar().valueChanged.connect(self._svg.schedule)
        self.verticalScrollBar().valueChanged.connect(self._svg.schedule)

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.TextAntialiasing
        )
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setStyleSheet("background: transparent; border: none;")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(64, 64)

        self._fitted = True
        self._fill = False  # False=sığdır, True=doldur (kırpar)
        self._rotation = 0
        self._press_pos = QPoint()
        self._press_global = QPoint()
        self._padding = 8
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.viewport().installEventFilter(self)
        self._drop_handler = None
        self._movie = None
        self._announce_frame = False
        # Tuvale ait zamanlayıcı: tuval silinirse bekleyen bildirim de onunla gider (çağrı boşa düşmez).
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.setInterval(0)
        self._frame_timer.timeout.connect(self.frame_shown)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._announce_frame and not self.pixmap_item.pixmap().isNull():
            self._announce_frame = False
            self._frame_timer.start()

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.viewport():
            et = event.type()
            if et in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
                    return True
            if et == QEvent.Type.Drop:
                urls = event.mimeData().urls()
                if urls and self._drop_handler:
                    self._drop_handler(urls[0].toLocalFile())
                    event.acceptProposedAction()
                    return True
        return super().eventFilter(obj, event)

    def image_size(self) -> tuple[int, int]:
        pix = self.pixmap_item.pixmap()
        if pix.isNull():
            return (0, 0)
        return pix.width(), pix.height()

    def set_qimage(self, qimage: QImage) -> None:
        pix = QPixmap.fromImage(qimage)
        self.set_pixmap(pix)

    def attach_svg(self, path) -> None:
        """Şu an gösterilen görüntü bu .svg/.svgz dosyasının tabanıysa yakınlaştırmada vektörden yeniden çizim
        (bindirme) etkinleşir; başka dosya türünde hiçbir şey yapmaz. set_qimage/set_pixmap bağı otomatik koparır."""
        pix = self.pixmap_item.pixmap()
        if path and is_svg_path(path) and not pix.isNull():
            self._svg.attach(path, (pix.width(), pix.height()), pix.hasAlphaChannel())

    def replace_image(self, qimage: QImage) -> None:
        """Aynı fotoğrafın daha iyi sürümüyle (örn. RAW tam çözünürlük) değiştir; görünümü koru.

        Sığdırılmışsa yeniden sığdırır. Yakınlaştırılmışsa aynı bölgeyi aynı görsel boyutta tutar,
        döndürme korunur.
        """
        new = QPixmap.fromImage(qimage)
        old_item = self.pixmap_item
        if old_item.pixmap().isNull() or self._fitted:
            rotation = self._rotation
            self.set_pixmap(new)
            if rotation:
                self.rotate_step(rotation)
            return
        old_bounds = old_item.sceneBoundingRect()
        center = self.mapToScene(self.viewport().rect().center())
        fx = (center.x() - old_bounds.left()) / max(old_bounds.width(), 1.0)
        fy = (center.y() - old_bounds.top()) / max(old_bounds.height(), 1.0)
        scale = self.displayed_scale()
        rotation = self._rotation
        self.set_pixmap(new)  # sığdırır ve dönüşümü sıfırlar
        if rotation:
            self.rotate_step(rotation)
        bounds = self.pixmap_item.sceneBoundingRect()
        self._fitted = False
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.resetTransform()
        factor = scale * old_bounds.width() / max(bounds.width(), 1.0)
        self.scale(factor, factor)
        self.centerOn(bounds.left() + fx * bounds.width(), bounds.top() + fy * bounds.height())
        self.zoom_changed.emit()

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self.stop_gif()
        self._announce_frame = not pixmap.isNull()
        self._rotation = 0
        self._svg.detach()
        self._scene.clear()
        self.pixmap_item = SvgPixmapItem(pixmap)
        self.pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.pixmap_item.setTransformOriginPoint(self.pixmap_item.boundingRect().center())
        self._scene.addItem(self.pixmap_item)
        self.fit_in_window()

    def stop_gif(self) -> None:
        movie = getattr(self, "_movie", None)
        if movie is not None:
            movie.stop()
            movie.deleteLater()
            self._movie = None

    def set_gif(self, path: str) -> None:
        from PyQt6.QtGui import QMovie

        self.stop_gif()
        movie = QMovie(path)
        movie.setCacheMode(QMovie.CacheMode.CacheNone)
        movie.frameChanged.connect(self._on_gif_frame)
        self._movie = movie
        movie.start()
        self._on_gif_frame()

    def _on_gif_frame(self) -> None:
        movie = self._movie
        if movie is None:
            return
        pix = movie.currentPixmap()
        if pix.isNull():
            return
        old = self.pixmap_item.pixmap()
        if old.isNull() or old.size() != pix.size():
            self._rotation = 0
            self._svg.detach()
            self._scene.clear()
            self.pixmap_item = QGraphicsPixmapItem(pix)
            self.pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self.pixmap_item.setTransformOriginPoint(self.pixmap_item.boundingRect().center())
            self._scene.addItem(self.pixmap_item)
            self.fit_in_window()
            return
        self.pixmap_item.setPixmap(pix)

    def clear_image(self) -> None:
        self.stop_gif()
        self._svg.detach()
        self._scene.clear()
        self.pixmap_item = SvgPixmapItem()
        self._scene.addItem(self.pixmap_item)
        self.resetTransform()
        self._rotation = 0
        self._fitted = True

    def rotate_step(self, degrees: int) -> None:
        self._rotation = (self._rotation + degrees) % 360
        if not self.pixmap_item.pixmap().isNull():
            self.pixmap_item.setTransformOriginPoint(self.pixmap_item.boundingRect().center())
            self.pixmap_item.setRotation(self._rotation)
            self.fit_in_window()

    def fit_in_window(self) -> None:
        pix = self.pixmap_item.pixmap()
        if pix.isNull():
            return
        self._fitted = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.resetTransform()
        bounds = self.pixmap_item.sceneBoundingRect()
        if bounds.width() < 1 or bounds.height() < 1:
            return
        self._scene.setSceneRect(bounds)
        win = self.window()
        is_fs = win is not None and hasattr(win, "isFullScreen") and win.isFullScreen()
        pad = 0 if (is_fs or self._fill) else self._padding
        view = self.viewport().rect().adjusted(pad, pad, -pad, -pad)
        if view.width() < 2 or view.height() < 2:
            return
        sx = view.width() / bounds.width()
        sy = view.height() / bounds.height()
        scale = max(sx, sy) if self._fill else min(sx, sy)
        self.scale(scale, scale)
        self.centerOn(bounds.center())
        self.zoom_changed.emit()

    def zoom_by(self, factor: float) -> None:
        if self.pixmap_item.pixmap().isNull():
            return
        self._fitted = False
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.scale(factor, factor)
        self.zoom_changed.emit()

    def reset_view_to_fit(self) -> None:
        self._fill = False
        self.fit_in_window()

    def reset_view_to_actual(self) -> None:
        pix = self.pixmap_item.pixmap()
        if pix.isNull():
            return
        self._fitted = False
        self._fill = False
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.resetTransform()
        bounds = self.pixmap_item.sceneBoundingRect()
        if bounds.width() >= 1 and bounds.height() >= 1:
            self._scene.setSceneRect(bounds)
            self.centerOn(bounds.center())
        self.zoom_changed.emit()

    def set_fill_mode(self, fill: bool) -> None:
        self._fill = fill
        self.fit_in_window()

    def toggle_fill_mode(self) -> None:
        self.set_fill_mode(not self._fill)

    def displayed_scale(self) -> float:
        return float(self.transform().m11())

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self.pixmap_item.pixmap().isNull():
            return
        self._fitted = False
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        self.scale(factor, factor)
        self.zoom_changed.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fitted:
            self.fit_in_window()
        self._svg.schedule()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        win = self.window()
        if win is not None and hasattr(win, "_wake_ui"):
            win._wake_ui()
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._press_global = event.globalPosition().toPoint()
            self._is_dragging_win = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        win = self.window()
        if win is not None and hasattr(win, "_wake_ui"):
            win._wake_ui()
        if self._fitted and self.dragMode() != QGraphicsView.DragMode.ScrollHandDrag:
            if event.buttons() & Qt.MouseButton.LeftButton and getattr(self, "_press_global", None) is not None:
                delta = event.globalPosition().toPoint() - self._press_global
                if delta.manhattanLength() >= 6:
                    if win is not None and hasattr(win, "_start_drag"):
                        origin = self._press_global
                        self._press_global = None
                        self._is_dragging_win = True
                        win._start_drag(origin, event.globalPosition().toPoint())
                        event.accept()
                        return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if getattr(self, "_is_dragging_win", False):
            self._is_dragging_win = False
            self._press_global = None
            win = self.window()
            if win is not None and hasattr(win, "_end_manual_move"):
                win._end_manual_move()
            return
        delta = event.position().toPoint() - self._press_pos
        self._press_global = None
        if delta.manhattanLength() < 6:
            self.activated.emit()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.context_requested.emit(event.globalPos())
