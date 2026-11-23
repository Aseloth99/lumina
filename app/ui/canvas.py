"""QGraphicsView tuvali — pencereyi dolduran sığdırma, zoom/pan, tıklayınca oynat."""

from __future__ import annotations

import math

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QRunnable, Qt, QThreadPool, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QImage, QPainter, QPixmap, QWheelEvent
from PyQt6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QSizePolicy,
)

from app.core.qt_safe import safe_emit
from app.ui.drops import FileDropMixin
from app.ui.fade import animations_enabled
from app.ui.svg_overlay import SvgOverlayController, SvgPixmapItem, is_svg_path


# Yakınlaştırma sınırı: bir görüntü pikseli en çok bu kadar ekran pikseli olur (küçük görsellerde: sığdırmanın 8 katı).
MAX_PIXEL_SCALE = 64.0
WHEEL_STEP = 1.18  # tekerleğin bir çentiği (120 birim)

# Ekran boyu kopya: büyük görüntü sığdırılınca boyama her ekran pikseli için kaynaktan birkaç piksel ÖRNEKLER
# (aradakileri atlar); ince desenlerde (kumaş, kiremit, ekran görüntüsündeki yazı) hare ve tırtık oluşur.
# Sığdırma ölçeği bunun altındaysa görüntü arka planda piksel ORTALAMASIYLA ekran boyuna küçültülür ve o boyanır.
LOD_MAX_SCALE = 0.75
LOD_SETTLE_MS = 120  # yakınlaştırma/pencere boyutu durulunca hazırla
LOD_MAX_PIXELS = 40_000_000  # Doldur kipinde kopya ekrandan büyük olabilir; bunu aşarsa hazırlanmaz

# Önceki/sonraki fotoğrafa geçerken kısa geçiş: eski kare yeni karenin üstünde saydamlaşır. Çok büyük görüntüde
# (ekran boyu kopya henüz hazır değilken her kare tüm görüntüyü ölçekler) geçiş atlanır: gezinme akıcı kalsın.
CROSSFADE_MS = 150
CROSSFADE_MAX_PIXELS = 30_000_000


def wheel_zoom_factor(delta_y: int) -> float:
    """Tekerlek hareketine orantılı yakınlaştırma çarpanı. Hassas dokunmatik yüzeyler çentik yerine küçük
    parçalar yollar: her parça tam çentik sayılırsa görüntü fırlar. Yatay kaydırma (0) hiçbir şey yapmaz."""
    return WHEEL_STEP ** (delta_y / 120.0) if delta_y else 1.0


class _LodSignals(QObject):
    ready = pyqtSignal(object)  # (nesil, QImage, ölçek, dpr)


class _LodJob(QRunnable):
    """Görüntüyü piksel ortalamasıyla (Qt SmoothTransformation küçültmesi) hedef boyuta indirir."""

    def __init__(self, gen: int, image: QImage, size: tuple[int, int], scale: float, dpr: float, signals) -> None:
        super().__init__()
        self.gen, self.image, self.size, self.scale, self.dpr = gen, image, size, scale, dpr
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            out = self.image.scaled(
                self.size[0],
                self.size[1],
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        except Exception:  # noqa: BLE001 — kopya yoksa asıl görüntü boyanır
            out = None
        self.image = None  # büyük kaynağı hemen bırak
        safe_emit(self.signals, "ready", (self.gen, out, self.scale, self.dpr))


class PhotoCanvas(FileDropMixin, QGraphicsView):
    activated = pyqtSignal()  # kısa sol tık (sürükleme değil)
    context_requested = pyqtSignal(QPoint)
    zoom_changed = pyqtSignal()
    double_clicked = pyqtSignal()
    user_zoomed = pyqtSignal()  # kullanıcı tekerlekle yakınlaştırdı/uzaklaştırdı (yüzde göstergesi için)
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
        # Ekran boyu kopya (LOD_*): durulunca tek iş parçacığında hazırlanır, nesil sayacı eski sonucu atar.
        self._lod_gen = 0
        self._lod_busy = False
        self._lod_pending = False
        self._lod_signals = _LodSignals(self)
        self._lod_signals.ready.connect(self._on_lod_ready)
        self._lod_pool = QThreadPool(self)
        self._lod_pool.setMaxThreadCount(1)
        self._lod_timer = QTimer(self)
        self._lod_timer.setSingleShot(True)
        self._lod_timer.setInterval(LOD_SETTLE_MS)
        self._lod_timer.timeout.connect(self._update_lod)
        self.zoom_changed.connect(self._lod_timer.start)
        # Geçiş: eski görünümün anlık görüntüsü, yeni karenin üstünde 1 → 0 saydamlıkla çizilir.
        self._fade_pix: QPixmap | None = None
        self._fade_opacity = 0.0
        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setDuration(CROSSFADE_MS)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.valueChanged.connect(self._on_fade_step)
        self._fade_anim.finished.connect(self._end_crossfade)

    # -- ekran boyu kopya
    def _lod_target(self) -> tuple[float, float] | None:
        """Sığdırılmış hâlde kopyanın ölçeği (aygıt pikseli / görüntü pikseli) ve dpr; gerekmiyorsa None."""
        item = self.pixmap_item
        if not self._fitted or self._movie is not None or not isinstance(item, SvgPixmapItem):
            return None
        pix = item.pixmap()
        if pix.isNull():
            return None
        t = self.transform()
        dpr = self.devicePixelRatioF()
        scale = math.hypot(t.m11(), t.m12()) * dpr
        if scale <= 0 or scale >= LOD_MAX_SCALE:
            return None
        if pix.width() * scale * pix.height() * scale > LOD_MAX_PIXELS:
            return None
        return scale, dpr

    def _update_lod(self) -> None:
        item = self.pixmap_item
        if not isinstance(item, SvgPixmapItem):
            return
        target = self._lod_target()
        if target is None:
            if self._fitted:
                item.clear_lod()  # küçük görüntü / kip değişti; yakınlaştırılmışken eski kopya uzaklaşınca işe yarar
            return
        scale, dpr = target
        if item.lod_scale() > 0 and abs(item.lod_scale() / scale - 1.0) < 0.002 and item.lod_dpr() == dpr:
            return  # zaten bu ölçekte
        if self._lod_busy:
            self._lod_pending = True  # biten iş sonrası yeniden değerlendirilir
            return
        pix = item.pixmap()
        size = (max(1, round(pix.width() * scale)), max(1, round(pix.height() * scale)))
        self._lod_gen += 1
        self._lod_busy = True
        # Raster QPixmap'in toImage()'i veriyi kopyalamaz (paylaşır); işçi yalnızca okur.
        self._lod_pool.start(_LodJob(self._lod_gen, pix.toImage(), size, scale, dpr, self._lod_signals))

    def shutdown(self) -> None:
        """Kapanışta iş havuzlarını boşaltır. Tuval Python tarafında silinirse (GIL tutulurken) ~QThreadPool süren işi
        bekler, Python'daki iş de GIL'i bekler: süreç kilitlenir (testlerde ölçüldü: 6 koşunun 5'i). Boş havuz hemen kapanır."""
        self._lod_timer.stop()
        self._lod_gen += 1  # uçuştaki sonuç atılır
        self._svg.shutdown()
        self._lod_pool.clear()
        self._lod_pool.waitForDone(5000)

    def _on_lod_ready(self, result) -> None:
        gen, image, scale, dpr = result
        self._lod_busy = False
        pending, self._lod_pending = self._lod_pending, False
        item = self.pixmap_item
        if gen == self._lod_gen and image is not None and not image.isNull() and isinstance(item, SvgPixmapItem):
            item.set_lod(QPixmap.fromImage(image), scale, dpr)
        if pending:
            self._lod_timer.start()

    def _reset_lod(self) -> None:
        """Görüntü değişti: uçuştaki kopya atılır (nesil), bekleyen değerlendirme yeni görüntü için yapılır."""
        self._lod_gen += 1

    # -- geçiş (crossfade)
    def is_crossfading(self) -> bool:
        return self._fade_pix is not None

    def _crossfade_snapshot(self, new: QPixmap) -> QPixmap | None:
        """Geçiş yapılacaksa ŞU ANKİ görünümün anlık görüntüsü (sürmekte olan geçiş dahil), yoksa None."""
        if new.isNull() or self.pixmap_item.pixmap().isNull() or not self.isVisible():
            return None
        if new.width() * new.height() > CROSSFADE_MAX_PIXELS:
            return None
        vp = self.viewport()
        if vp.width() < 2 or vp.height() < 2 or not animations_enabled():
            return None
        snap = vp.grab()
        return None if snap.isNull() else snap

    def _start_crossfade(self, snapshot: QPixmap) -> None:
        self._fade_anim.stop()
        self._fade_pix = snapshot
        self._fade_opacity = 1.0
        self._fade_anim.start()
        self.viewport().update()

    def _on_fade_step(self, value) -> None:
        self._fade_opacity = float(value)
        self.viewport().update()

    def _end_crossfade(self) -> None:
        self._fade_anim.stop()
        if self._fade_pix is not None:
            self._fade_pix = None
            self.viewport().update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._fade_pix is not None and self._fade_opacity > 0.0:
            p = QPainter(self.viewport())
            p.setOpacity(self._fade_opacity)
            p.drawPixmap(0, 0, self._fade_pix)
            p.end()
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

    def set_qimage(self, qimage: QImage, crossfade: bool = False) -> None:
        """`crossfade=True`: önceki fotoğraftan kısa bir geçişle (gezinme); ilk açılışta/geçiş uygun değilse anında."""
        pix = QPixmap.fromImage(qimage)
        self.set_pixmap(pix, crossfade=crossfade)

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

    def set_pixmap(self, pixmap: QPixmap, crossfade: bool = False) -> None:
        snapshot = self._crossfade_snapshot(pixmap) if crossfade else None
        self.stop_gif()
        self._announce_frame = not pixmap.isNull()
        self._rotation = 0
        self._svg.detach()
        self._reset_lod()
        self._scene.clear()
        self.pixmap_item = SvgPixmapItem(pixmap)
        self.pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.pixmap_item.setTransformOriginPoint(self.pixmap_item.boundingRect().center())
        self._scene.addItem(self.pixmap_item)
        self.fit_in_window()
        if snapshot is not None:
            self._start_crossfade(snapshot)

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
            self._reset_lod()
            self._scene.clear()
            self.pixmap_item = QGraphicsPixmapItem(pix)
            self.pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self.pixmap_item.setTransformOriginPoint(self.pixmap_item.boundingRect().center())
            self._scene.addItem(self.pixmap_item)
            self.fit_in_window()
            return
        self.pixmap_item.setPixmap(pix)

    def clear_image(self) -> None:
        self._end_crossfade()
        self.stop_gif()
        self._svg.detach()
        self._reset_lod()
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

    def _fit_scale(self) -> float | None:
        """Geçerli kipte (sığdır/doldur) görüntüyü pencereye oturtan ölçek; ölçülemiyorsa None."""
        bounds = self.pixmap_item.sceneBoundingRect()
        if bounds.width() < 1 or bounds.height() < 1:
            return None
        win = self.window()
        is_fs = win is not None and hasattr(win, "isFullScreen") and win.isFullScreen()
        pad = 0 if (is_fs or self._fill) else self._padding
        view = self.viewport().rect().adjusted(pad, pad, -pad, -pad)
        if view.width() < 2 or view.height() < 2:
            return None
        sx = view.width() / bounds.width()
        sy = view.height() / bounds.height()
        return max(sx, sy) if self._fill else min(sx, sy)

    def zoom_by(self, factor: float) -> None:
        """Yakınlaştır/uzaklaştır. Uzaklaştırma, görüntü pencereye oturunca durur (sığdırılmış duruma döner:
        pencere boyutu değişince yeniden sığar); pencereden küçük görselde %100'de durur. Yakınlaştırmanın
        da bir tavanı var. Eskiden sınır yoktu: tekerlek görüntüyü bir noktaya kadar küçültebiliyordu."""
        if self.pixmap_item.pixmap().isNull() or factor <= 0 or factor == 1.0:
            return
        current = self.displayed_scale()
        fit = self._fit_scale()
        if fit is None or current <= 0:
            self._fitted = False
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.scale(factor, factor)
            self.zoom_changed.emit()
            return
        floor = min(fit, 1.0)
        ceiling = max(MAX_PIXEL_SCALE, fit * 8.0)
        target = current * factor
        if factor < 1.0:
            if current <= floor * 1.0005:
                return  # zaten en küçük hâlinde
            if target <= fit * 1.0005 and current > fit * 1.0005:
                self.fit_in_window()  # sığdırmanın altına inme: tam oturt
                return
            target = max(target, floor)
        else:
            if current >= ceiling:
                return
            target = min(target, ceiling)
        self._fitted = False
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        step = target / current
        self.scale(step, step)
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

    def refresh_scale(self) -> None:
        """Ekran/ölçek değişti: sığdırılmışsa yeniden oturt, SVG bindirmesini yeni ölçekte yeniden planla."""
        if self._fitted:
            self.fit_in_window()
        self._svg.schedule()
        self.viewport().update()

    def displayed_scale(self) -> float:
        return float(self.transform().m11())

    def zoom_percent(self) -> int:
        """Görüntünün ekrandaki boyu, gerçek boyuta (1:1) göre yüzde; döndürülmüş görüntüde de doğru."""
        t = self.transform()
        return max(1, round(math.hypot(t.m11(), t.m12()) * 100))

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self.pixmap_item.pixmap().isNull():
            return
        before = self.displayed_scale()
        self.zoom_by(wheel_zoom_factor(event.angleDelta().y()))
        if self.displayed_scale() != before:
            self.user_zoomed.emit()
        event.accept()

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
