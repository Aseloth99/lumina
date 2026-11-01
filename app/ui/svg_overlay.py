"""SVG bindirmesi: yakınlaştırınca görünen bölge ekran çözünürlüğünde yeniden VEKTÖRDEN çizilir.

Taban görüntü (svg_loader'ın tek seferlik rasterleştirmesi, uzun kenar 3200 px) hızlı ilk görüntüdür ve bindirme
hazır olana kadar (ya da bindirme yoksa) görünür kalır. Tuval ölçeği taban pikselini aşınca (ekran pikseli / taban
pikseli > MIN_SCALE) ve yakınlaştırma/kaydırma SETTLE_MS durulunca:

  1. Görünen bölge (görüntüyü kesen görünüm penceresi + kenar payı) AYGIT pikseline oturtulur: bölgenin aygıt
     dikdörtgeni tam sayıya yuvarlanır, SVG o dikdörtgenin ön görüntüsü kadar çizilir. Böylece bindirme pikseli
     ekran pikseliyle bire bir eşleşir (alt piksel bulanıklığı olmaz).
  2. Çizim işçi SÜREÇTE yapılır (svg_worker: resvg GIL'i tuttuğundan iş parçacığı arayüzü dondururdu); işçi
     kurulamazsa küçük belgelerde iş parçacığında resvg, değilse QtSvg ile yedek yol.
  3. Her istek bir NESİL sayısı taşır; araya yeni yakınlaştırma girerse eski sonuç atılır. Aynı anda en fazla bir
     çizim sürer, bekleyen tek bir "en yeni" istek tutulur (sıra büyümez).
  4. Sonuç, taban öğenin KENDİSİNİN içinde, öğe yerel koordinatlarında boyanır (döndürme/çevirme/ölçek aynı
     dönüşümü paylaşır). Bindirme görünen alanı örtüyorsa taban hiç boyanmaz: saydam SVG'lerde taban halesi
     sızmaz, titreme olmaz. Yeni bindirme hazır olunca eskisi tek adımda değişir (gizlenme yok).

Arka plan: bindirme, tabanla AYNI kararı kullanır (tabanda alfa varsa saydam kalır, yoksa açık zemine oturur).
"""

from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QRect, QRectF, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPainter, QPixmap, QTransform
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsPixmapItem

from app.core import svg_region, svg_worker
from app.core.qt_safe import safe_emit

SVG_EXTS = frozenset({".svg", ".svgz"})
SETTLE_MS = 120  # yakınlaştırma/kaydırma durulunca çiz
MIN_SCALE = 1.05  # ekran pikseli / taban pikseli bunun altındaysa taban yeter
MARGIN = 0.25  # görünüm penceresinin her kenarına eklenen pay (küçük kaydırmalar yeniden çizdirmez)
MAX_PIXELS = 16_000_000  # sert üst sınır (aşılırsa çözünürlük kısılır)
SOFT_PIXELS = 8_000_000  # kenar payı bu bütçeyi aşmayacak kadar küçültülür (çizim süresi pikselle orantılı)
MAX_EDGE = 8192
SMALL_DOCUMENT_CHARS = 300_000  # işçi yoksa iş parçacığında resvg yalnızca bundan küçük belgelerde (GIL tutulur)
ASPECT_TOLERANCE = 0.02
SLOW_RENDER_MS = 2500  # bundan uzun süren çizimden sonra piksel bütçesi küçülür
MIN_BUDGET = 0.25
MAX_FAILURES = 2


def is_svg_path(path: str | os.PathLike | None) -> bool:
    return bool(path) and Path(os.fspath(path)).suffix.lower() in SVG_EXTS


# --------------------------------------------------------------------------- geometri
@dataclass(frozen=True)
class RegionPlan:
    local: QRectF  # bindirmenin öğe yerel (taban piksel) dikdörtgeni
    visible: QRectF  # gerçekten görünen kısım (yerel)
    px: tuple[int, int]  # ham görüntü boyutu
    scale: float  # görünümün aygıt pikseli / yerel birim
    box: tuple[float, float, float, float]  # local / taban boyutu (kesirler)

    @property
    def density(self) -> float:
        """Ham görüntünün aygıt pikseli / yerel birim (ölçeğe eşitse bire bir)."""
        return self.px[0] / self.local.width() if self.local.width() > 0 else 0.0

    def same_as(self, other: RegionPlan) -> bool:
        tol = 0.5 / max(self.scale, 1e-9)
        a, b = self.local, other.local
        return (
            self.px == other.px
            and abs(a.left() - b.left()) <= tol
            and abs(a.top() - b.top()) <= tol
            and abs(a.right() - b.right()) <= tol
            and abs(a.bottom() - b.bottom()) <= tol
        )


def plan_region(
    item_to_view: QTransform,
    viewport_size: tuple[float, float],
    dpr: float,
    image_size: tuple[int, int],
    *,
    min_scale: float = MIN_SCALE,
    margin: float = MARGIN,
    max_pixels: int = MAX_PIXELS,
    soft_pixels: int = SOFT_PIXELS,
    max_edge: int = MAX_EDGE,
) -> RegionPlan | None:
    """Hangi bölge hangi piksel boyutunda çizilmeli; bindirme gerekmiyorsa (taban yeterli) None.

    `item_to_view`: öğe yerel -> görünüm penceresi (mantıksal piksel) dönüşümü; döndürme/çevirme içerebilir.
    """
    width, height = image_size
    vw, vh = viewport_size
    if width <= 0 or height <= 0 or vw <= 0 or vh <= 0 or dpr <= 0:
        return None
    to_device = item_to_view * QTransform.fromScale(dpr, dpr)
    scale = math.hypot(to_device.m11(), to_device.m12())
    if not math.isfinite(scale) or scale <= min_scale:
        return None
    inverse, invertible = to_device.inverted()
    if not invertible:
        return None
    image_rect = QRectF(0.0, 0.0, float(width), float(height))
    view_rect = QRectF(0.0, 0.0, vw * dpr, vh * dpr)
    visible = inverse.mapRect(view_rect).intersected(image_rect)
    if visible.width() <= 0 or visible.height() <= 0:
        return None
    image_device = to_device.mapRect(image_rect)

    local = QRectF()
    pw = ph = 0.0
    soft = min(soft_pixels, max_pixels)
    for pad in (margin, margin * 0.5, margin * 0.2, 0.0):  # pay önce kısılır, çözünürlük en son
        mx, my = round(view_rect.width() * pad), round(view_rect.height() * pad)
        want = view_rect.adjusted(-mx, -my, mx, my).intersected(image_device)
        if want.width() <= 0 or want.height() <= 0:
            return None
        left, top = math.floor(want.left()), math.floor(want.top())
        device = QRect(left, top, math.ceil(want.right()) - left, math.ceil(want.bottom()) - top)
        local = inverse.mapRect(QRectF(device))
        pw, ph = local.width() * scale, local.height() * scale
        if pw * ph <= soft and max(pw, ph) <= max_edge + 0.5:
            break
    factor = 1.0
    if pw * ph > max_pixels or max(pw, ph) > max_edge + 0.5:
        factor = min(math.sqrt(max_pixels / (pw * ph)), max_edge / max(pw, ph), 1.0)
    px = (max(1, round(local.width() * scale * factor)), max(1, round(local.height() * scale * factor)))
    box = (local.x() / width, local.y() / height, local.width() / width, local.height() / height)
    return RegionPlan(local, visible, px, scale, box)


# --------------------------------------------------------------------------- öğe
class SvgPixmapItem(QGraphicsPixmapItem):
    """Taban görüntü öğesi; bir bindirme (keskin bölge) taşıyabilir. Bindirme yokken sıradan QGraphicsPixmapItem."""

    def __init__(self, pixmap: QPixmap | None = None) -> None:
        if pixmap is None:
            super().__init__()
        else:
            super().__init__(pixmap)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)
        self._overlay: QImage | None = None
        self._overlay_rect = QRectF()
        self._overlay_density = 0.0

    # -- durum
    def has_overlay(self) -> bool:
        return self._overlay is not None

    def overlay_rect(self) -> QRectF:
        return QRectF(self._overlay_rect)

    def overlay_image(self) -> QImage | None:
        return self._overlay

    def overlay_density(self) -> float:
        return self._overlay_density

    def set_overlay(self, image: QImage, rect: QRectF) -> None:
        self._overlay = image
        self._overlay_rect = QRectF(rect)
        self._overlay_density = image.width() / rect.width() if rect.width() > 0 else 0.0
        self.update()

    def clear_overlay(self) -> None:
        if self._overlay is None:
            return
        self._overlay = None
        self._overlay_rect = QRectF()
        self._overlay_density = 0.0
        self.update()

    def satisfies(self, plan: RegionPlan) -> bool:
        """Mevcut bindirme bu görünümü karşılıyor mu (görünen alanı örtüyor ve yoğunluğu uygun)?"""
        if self._overlay is None or self._overlay_density <= 0:
            return False
        if not self._overlay_rect.adjusted(-1e-3, -1e-3, 1e-3, 1e-3).contains(plan.visible):
            return False
        # Yoğunluk planın şimdi üreteceğiyle aynı olmalı (±%3): küçültülmüş/büyütülmüş bindirme bulanık kalırdı.
        tolerance = max(0.03, 1.0 / max(self._overlay.width(), 1))
        return abs(self._overlay_density / max(plan.density, 1e-9) - 1.0) <= tolerance

    # -- boyama
    def paint(self, painter, option, widget=None) -> None:  # noqa: N802
        image = self._overlay
        if image is None:
            super().paint(painter, option, widget)
            return
        bounds = self.boundingRect()
        exposed = option.exposedRect.intersected(bounds)
        covered = self._overlay_rect.adjusted(-1e-3, -1e-3, 1e-3, 1e-3).contains(exposed)
        if not covered:
            super().paint(painter, option, widget)
        painter.save()
        painter.setClipRect(bounds, Qt.ClipOperation.IntersectClip)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(self._overlay_rect, image)
        painter.restore()


# --------------------------------------------------------------------------- çizim yolları
def _image_from_raw(header: dict, payload: bytes) -> QImage | None:
    """İşçinin ham RGB/RGBA baytlarından, hızlı boyanan biçimde (önceden çarpılmış / RGB32) QImage."""
    try:
        w, h = int(header["w"]), int(header["h"])
        if header.get("mode") == "RGBA":
            raw = QImage(payload, w, h, w * 4, QImage.Format.Format_RGBA8888)
            out = raw.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        else:
            raw = QImage(payload, w, h, w * 3, QImage.Format.Format_RGB888)
            out = raw.convertToFormat(QImage.Format.Format_RGB32)
    except (KeyError, ValueError, TypeError):
        return None
    return None if out.isNull() else out


_source_lock = threading.Lock()
_source_cache: dict = {"key": None, "source": None}


def _cached_source(path: str, mtime_ns: int | None):
    with _source_lock:
        if _source_cache["key"] == (path, mtime_ns):
            return _source_cache["source"]
    source = svg_region.load_source(Path(path))
    with _source_lock:
        _source_cache["key"], _source_cache["source"] = (path, mtime_ns), source
    return source


class _ThreadSignals(QObject):
    finished = pyqtSignal(object)


class _ThreadJob(QRunnable):
    """Yedek yol: işçi süreç yokken bölgeyi bu süreçte çizer (resvg yalnızca küçük belgelerde, aksi halde QtSvg)."""

    def __init__(self, gen: int, plan: RegionPlan, request: dict) -> None:
        super().__init__()
        self.gen, self.plan, self.request = gen, plan, request
        self.signals = _ThreadSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        header: dict = {"ok": False, "error": "failed"}
        image = None
        try:
            req = self.request
            source = _cached_source(req["path"], req.get("mtime_ns"))
            if source is None:
                header = {"ok": False, "error": "unsupported"}
            else:
                if svg_region.resvg_py is not None and len(source.text) <= SMALL_DOCUMENT_CHARS:
                    pil = svg_region.render_region_resvg(source, tuple(req["box"]), tuple(req["px"]), req["kind"])
                    header = {"ok": True, "w": pil.width, "h": pil.height, "mode": pil.mode}
                    image = _image_from_raw(header, pil.tobytes())
                else:
                    from app.core.svg_loader import render_region_qt

                    image = render_region_qt(source, tuple(req["box"]), tuple(req["px"]), req["kind"])
                    header = {"ok": image is not None and not image.isNull()}
                header["size"] = list(source.size)
        except Exception as exc:  # noqa: BLE001 — tek bölge başarısız olur, tabandan devam edilir
            header = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
        safe_emit(self.signals, "finished", (self.gen, self.plan, image if header.get("ok") else None, header))


# --------------------------------------------------------------------------- denetleyici
class SvgOverlayController(QObject):
    """Tuvale bağlı bindirme yöneticisi (durulma zamanlayıcısı, nesil sayacı, tek çizim + tek bekleyen istek)."""

    _done = pyqtSignal(object)  # (nesil, plan, QImage | None, başlık)

    def __init__(self, canvas) -> None:
        super().__init__(canvas)
        self._canvas = canvas
        self.enabled = True
        self._path: str | None = None
        self._mtime_ns: int | None = None
        self._base: tuple[int, int] = (0, 0)
        self._has_alpha = False
        self._gen = 0
        self._doc_gen = 0
        self._busy = False
        self._queued: tuple[int, RegionPlan] | None = None
        self._pending_plan: RegionPlan | None = None
        self._unsupported = False
        self._failures = 0
        self._budget = 1.0  # yavaş belgelerde piksel bütçesi çarpanı (son çizim uzun sürdüyse küçülür)
        self.last_header: dict | None = None
        self.render_count = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SETTLE_MS)
        self._timer.timeout.connect(self.settle)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._done.connect(self._on_done)

    # -- yaşam döngüsü
    @property
    def path(self) -> str | None:
        return self._path

    def attach(self, path: str, base_size: tuple[int, int], has_alpha: bool) -> None:
        self.detach()
        self._path = os.fspath(path)
        try:
            self._mtime_ns = Path(self._path).stat().st_mtime_ns
        except OSError:
            self._mtime_ns = None
        self._base = (int(base_size[0]), int(base_size[1]))
        self._has_alpha = bool(has_alpha)
        self._unsupported = False
        self._failures = 0
        self._budget = 1.0
        self._doc_gen = self._gen  # bu belgeye ait istekler >= bu nesil
        self.schedule()

    def detach(self) -> None:
        """Dosya değişti/görüntü kalktı: bekleyen çizimleri iptal et, bindirmeyi kaldır."""
        self._timer.stop()
        self._gen += 1  # uçuştaki sonuç atılır
        self._queued = None
        self._pending_plan = None
        self._path = None
        item = self._canvas.pixmap_item
        if isinstance(item, SvgPixmapItem):
            try:
                item.clear_overlay()
            except RuntimeError:  # öğenin C++ nesnesi sahneyle birlikte silinmiş olabilir
                pass

    def schedule(self, *_args) -> None:
        """Görünüm değişti: durulunca yeniden değerlendir."""
        if self._path is not None and self.enabled:
            self._timer.start()

    # -- değerlendirme
    def current_plan(self) -> RegionPlan | None:
        canvas = self._canvas
        item = canvas.pixmap_item
        if not isinstance(item, SvgPixmapItem) or item.pixmap().isNull():
            return None
        viewport = canvas.viewport()
        return plan_region(
            item.sceneTransform() * canvas.viewportTransform(),
            (viewport.width(), viewport.height()),
            canvas.devicePixelRatioF(),
            self._base,
            max_pixels=int(MAX_PIXELS * self._budget),
            soft_pixels=int(SOFT_PIXELS * self._budget),
        )

    def settle(self) -> None:
        if self._path is None or not self.enabled or self._unsupported:
            return
        item = self._canvas.pixmap_item
        if not isinstance(item, SvgPixmapItem):
            return
        plan = self.current_plan()
        if plan is None:  # taban yeter: bindirmeyi kaldır, uçuştakini iptal et
            self._gen += 1
            self._queued = None
            self._pending_plan = None
            item.clear_overlay()
            return
        if item.satisfies(plan):
            return
        if self._pending_plan is not None and self._pending_plan.same_as(plan):
            return  # aynı bölge zaten çiziliyor
        self._gen += 1
        self._pending_plan = plan
        self._submit(self._gen, plan)

    def _request(self, plan: RegionPlan) -> dict:
        return {
            "op": "render",
            "path": self._path,
            "mtime_ns": self._mtime_ns,
            "box": list(plan.box),
            "px": list(plan.px),
            "kind": "keep" if self._has_alpha else "light",
        }

    def _submit(self, gen: int, plan: RegionPlan) -> None:
        if self._busy:
            self._queued = (gen, plan)  # en yenisi kalır
            return
        self._busy = True
        request = self._request(plan)
        if svg_region.resvg_py is not None:
            client = svg_worker.get_client()
            if not client.broken:

                def deliver(header: dict, payload: bytes, gen=gen, plan=plan) -> None:  # okuyucu iş parçacığı
                    image = _image_from_raw(header, payload) if header.get("ok") else None
                    safe_emit(self, "_done", (gen, plan, image, header))

                if client.submit(request, deliver):
                    return
        job = _ThreadJob(gen, plan, request)
        job.signals.finished.connect(self._on_done)
        self._pool.start(job)

    def _on_done(self, result) -> None:
        gen, plan, image, header = result
        self._busy = False
        self.last_header = header
        self.render_count += 1
        budget_before = self._budget
        elapsed = header.get("ms")
        if self._path is not None and gen >= self._doc_gen and isinstance(elapsed, (int, float)):
            if elapsed > SLOW_RENDER_MS:  # eski (atılacak) sonuç da maliyeti öğretir: sonraki bölgeler daha az piksel
                self._budget = max(MIN_BUDGET, min(1.0, self._budget * SLOW_RENDER_MS / elapsed))
        queued, self._queued = self._queued, None
        if queued is not None:
            if self._budget != budget_before:  # bekleyen istek eski bütçeyle planlanmıştı: yeniden planla
                fresh = self.current_plan()
                if fresh is not None:
                    queued = (queued[0], fresh)
                    self._pending_plan = fresh
            self._submit(*queued)
        if gen != self._gen or self._path is None:
            return  # araya yeni yakınlaştırma girdi (ya da dosya değişti): eski sonuç atılır
        self._pending_plan = None if queued is None else self._pending_plan
        if image is None or image.isNull():
            if header.get("error") == "unsupported":
                self._unsupported = True  # kök svg değil / boyut belirsiz: bu belgede bindirme yok
            else:
                self._failures += 1  # işçi öldü / zaman aşımı / çizim hatası: iki kez olursa bu belgeden vazgeç
                if self._failures >= MAX_FAILURES:
                    self._unsupported = True
            return
        size = header.get("size")
        if size and size[0] > 0 and size[1] > 0 and self._base[1] > 0:
            if abs((size[0] / size[1]) / (self._base[0] / self._base[1]) - 1.0) > ASPECT_TOLERANCE:
                self._unsupported = True  # taban başka bir en-boy oranında çizilmiş: hizalanamaz, tabanda kal
                return
        item = self._canvas.pixmap_item
        if isinstance(item, SvgPixmapItem):
            item.set_overlay(image, plan.local)
