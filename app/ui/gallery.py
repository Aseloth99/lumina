"""Galeri görünümü — geçerli klasörün küçük resim ızgarası (G ile açılır, Enter / çift tık ile tek görünüm).

Binlerce dosyada akıcı kalsın diye QListView + model/delegate: model yalnızca yolları tutar, hücreleri delegate
çizer. Küçük resimler galerinin kendi iş parçacığı havuzunda üretilir; önce ekranda görünenler (kaydırınca sıra
yeniden kurulur, artık görünmeyenler kuyruktan düşer). Önbellek iki katmanlı: bellekte bayt sınırlı bir LRU, diskte
`<veri klasörü>/thumbs` (anahtar = yol + boyut + değişiklik zamanı; dosya değişince kendiliğinden yenilenir).
"""

from __future__ import annotations

import hashlib
import io
import os
import threading
import zipfile
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageOps
from PyQt6.QtCore import (
    QAbstractListModel,
    QEvent,
    QMimeData,
    QModelIndex,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import QAbstractItemView, QFrame, QListView, QStyle, QStyledItemDelegate

from app.core.image_loader import _apply_icc, pil_to_qimage
from app.core.matcher import VIDEO_EXTS
from app.core.qt_safe import safe_emit
from app.core.thumb_loader import load_thumb_pil
from app.core.video_io import video_thumb
from app.ui.filmstrip import _BadgeWorker
from app.ui.live_button import _fmt_ms

THUMB_SIDE = 288  # saklanan kare küçük resmin kenarı (px); en büyük karo %150 ölçekte bile çok az büyütülür
CELL_MIN, CELL_MAX, CELL_DEFAULT = 96, 280, 176  # hücre (karo + boşluk) hedef kenarı, mantıksal px
CELL_STEP = 1.15  # Ctrl+tekerlek / + / - adımı
GAP = 8  # karolar arası boşluk
RADIUS = 7.0
DISK_VERSION = 1  # küçük resim biçimi değişirse artırılır: eski dosyalar artık eşleşmez, budamayla gider
DISK_BUDGET = 320 * 1024 * 1024
MEM_BUDGET = 192 * 1024 * 1024
_LIVP_STILLS = (".heic", ".heif", ".jpg", ".jpeg", ".png")


# ---------------------------------------------------------------------------------------------------- üretim
def _square_pil(img: Image.Image, side: int) -> Image.Image:
    """Ortadan kare kırp (dikey görselde biraz yukarıdan: yüzler çoğunlukla üst yarıda), en fazla `side`'a küçült."""
    w, h = img.size
    s = min(w, h)
    left = (w - s) // 2
    top = int((h - s) * 0.4)
    img = img.crop((left, top, left + s, top + s))
    if s > side:
        img = img.resize((side, side), Image.Resampling.LANCZOS)
    return img


def _square_qimage(img: QImage, side: int) -> QImage:
    w, h = img.width(), img.height()
    s = min(w, h)
    img = img.copy(QRect((w - s) // 2, int((h - s) * 0.4), s, s))
    if s > side:
        img = img.scaled(side, side, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return img


def _livp_still(path: Path, size: int) -> Image.Image:
    """.livp (ZIP: fotoğraf + video) içindeki fotoğraf."""
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(_LIVP_STILLS) and not n.startswith("__MACOSX")]
        if not names:
            raise OSError("livp: fotoğraf yok")
        data = archive.read(names[0])
    with Image.open(io.BytesIO(data)) as raw:
        try:
            raw.draft("RGB", (size, size))
        except Exception:  # noqa: BLE001 — draft yalnızca JPEG'de hızlandırır
            pass
        raw.load()
        img = raw.copy()
    img = _apply_icc(ImageOps.exif_transpose(img))
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    return img


def make_thumb(path: Path, side: int = THUMB_SIDE) -> tuple[QImage, int]:
    """Kare (ortadan kırpılmış, EXIF yönü uygulanmış) küçük resim ve video süresi (ms; fotoğrafta 0).

    Çözüm uzun kenarı 2 × `side`: 2:1'e kadar her oranda kısa kenar karoyu doldurur. Biçimler ana görüntüleyicinin
    küçük resim yükleyicisinden gelir (HEIC gömülü önizleme, RAW, SVG, JXR, AVIF, JXL…)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTS:
        frame, dur = video_thumb(path, side * 2)
        if frame is None or frame.isNull():
            raise OSError("video: kare okunamadı")
        return _square_qimage(frame, side), dur
    img = _livp_still(path, side * 2) if suffix == ".livp" else load_thumb_pil(path, side * 2)
    img = _square_pil(img, side)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    return pil_to_qimage(img), 0


class ThumbDiskCache:
    """Diskteki küçük resimler: `<anahtar>.thumb` (JPEG, saydamsa PNG) + videoda `<anahtar>.dur` (süre, ms)."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)

    @staticmethod
    def key(path: Path, st: os.stat_result) -> str:
        raw = f"{DISK_VERSION}|{THUMB_SIDE}|{os.path.normcase(os.path.abspath(path))}|{st.st_size}|{st.st_mtime_ns}"
        return hashlib.sha1(raw.encode("utf-8", "surrogatepass")).hexdigest()

    def load(self, key: str) -> tuple[QImage, int] | None:
        file = self.folder / f"{key}.thumb"
        image = QImage()
        if not image.load(str(file)):
            return None
        dur = 0
        try:
            dur = int((self.folder / f"{key}.dur").read_text(encoding="ascii").strip() or 0)
        except (OSError, ValueError):
            dur = 0
        try:
            os.utime(file)  # budama en eski kullanılanları siler
        except OSError:
            return image, dur
        return image, dur

    def store(self, key: str, image: QImage, dur: int) -> bool:
        self.folder.mkdir(parents=True, exist_ok=True)
        final = self.folder / f"{key}.thumb"
        tmp = self.folder / f"{key}.{os.getpid()}.{threading.get_ident()}.tmp"
        fmt = "PNG" if image.hasAlphaChannel() else "JPG"
        if not image.save(str(tmp), fmt, 85 if fmt == "JPG" else -1):
            tmp.unlink(missing_ok=True)
            return False
        if dur > 0:
            (self.folder / f"{key}.dur").write_text(str(int(dur)), encoding="ascii")
        os.replace(tmp, final)
        return True

    def prune(self, budget: int = DISK_BUDGET) -> int:
        """Klasör `budget`'ı aşarsa en eski kullanılanlardan başlayıp %80'ine iner; silinen dosya sayısı."""
        try:
            entries = [e for e in os.scandir(self.folder) if e.is_file() and e.name.endswith(".thumb")]
        except OSError:
            return 0
        stats = []
        total = 0
        for entry in entries:
            try:
                st = entry.stat()
            except OSError:
                continue
            stats.append((st.st_mtime_ns, st.st_size, entry.path))
            total += st.st_size
        if total <= budget:
            return 0
        removed = 0
        for _mtime, size, file in sorted(stats):
            if total <= budget * 0.8:
                break
            try:
                os.remove(file)
                Path(file).with_suffix(".dur").unlink(missing_ok=True)
            except OSError:
                continue
            total -= size
            removed += 1
        return removed


# ---------------------------------------------------------------------------------------------------- iş parçacıkları
class _ThumbSignals(QObject):
    done = pyqtSignal(str, QImage, int, object)  # yol, küçük resim, süre ms, değişiklik zamanı (ns)
    failed = pyqtSignal(str)


class _ThumbJob(QRunnable):
    def __init__(self, path: str, disk: ThumbDiskCache | None) -> None:
        super().__init__()
        self.path = path
        self.disk = disk
        self.signals = _ThumbSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        path = Path(self.path)
        try:
            st = path.stat()
            key = ThumbDiskCache.key(path, st)
            hit = self.disk.load(key) if self.disk is not None else None
            if hit is None:
                image, dur = make_thumb(path)
                if self.disk is not None:
                    try:
                        self.disk.store(key, image, dur)
                    except OSError:
                        pass  # disk dolu / salt okunur: küçük resim yine gösterilir
            else:
                image, dur = hit
        except Exception:  # noqa: BLE001 — çözülemeyen dosya karoda uzantısıyla görünür
            safe_emit(self.signals, "failed", self.path)
            return
        safe_emit(self.signals, "done", self.path, image, int(dur), st.st_mtime_ns)


class _StatSignals(QObject):
    changed = pyqtSignal(list)


class _StatJob(QRunnable):
    """Bellekteki küçük resimlerin dosyaları değişti mi (klasör yeniden tarandıktan sonra; yalnızca görünenler)."""

    def __init__(self, items: dict[str, int]) -> None:
        super().__init__()
        self.items = items
        self.signals = _StatSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        changed = []
        for path, mtime in self.items.items():
            try:
                if os.stat(path).st_mtime_ns != mtime:
                    changed.append(path)
            except OSError:
                changed.append(path)
        if changed:
            safe_emit(self.signals, "changed", changed)


class _PruneJob(QRunnable):
    def __init__(self, disk: ThumbDiskCache) -> None:
        super().__init__()
        self.disk = disk
        self.setAutoDelete(True)

    def run(self) -> None:
        self.disk.prune()


class ThumbnailProvider(QObject):
    """Küçük resimleri arka planda üretir/okur; bellek önbelleği GUI iş parçacığındadır."""

    ready = pyqtSignal(str)  # bu yolun küçük resmi (ya da hatası) hazır
    invalidated = pyqtSignal()  # bazı küçük resimler bayatladı: görünenler yeniden istenmeli

    def __init__(self, parent: QObject | None = None, cache_dir: Path | None = None, threads: int | None = None) -> None:
        super().__init__(parent)
        self._disk = ThumbDiskCache(cache_dir) if cache_dir is not None else None
        self._pool = QThreadPool(self)
        if threads is None:
            threads = max(2, min(4, (os.cpu_count() or 4) // 2))
        self._pool.setMaxThreadCount(threads)
        self._limit = threads + 1  # havuz kuyruğu kısa kalsın: kaydırınca yeni görünenler hemen öne geçer
        self._mem: OrderedDict[str, QImage] = OrderedDict()
        self._mem_bytes = 0
        self._budget = MEM_BUDGET
        self._keep = 0
        self._mtimes: dict[str, int] = {}
        self._durations: dict[str, int] = {}
        self._failed: set[str] = set()
        self._unverified: set[str] = set()
        self._queue: list[str] = []
        self._inflight: set[str] = set()
        self._stored = 0
        self._closed = False
        if self._disk is not None:
            self._pool.start(_PruneJob(self._disk))

    # --- sorgular (delegate) ---
    def image(self, key: str) -> QImage | None:
        img = self._mem.get(key)
        if img is not None:
            self._mem.move_to_end(key)
        return img

    def duration(self, key: str) -> int:
        return self._durations.get(key, 0)

    def failed(self, key: str) -> bool:
        return key in self._failed

    def pending(self) -> int:
        return len(self._queue) + len(self._inflight)

    # --- istekler (görünüm) ---
    def request(self, keys: list[str]) -> None:
        """Sırayla istenen yollar (önce görünenler). Önceki kuyruk atılır: artık görünmeyenler üretilmez."""
        if self._closed:
            return
        self._keep = len(keys)
        check = {k: self._mtimes[k] for k in keys if k in self._unverified and k in self._mtimes}
        if check:
            self._unverified.difference_update(check)
            job = _StatJob(check)
            job.signals.changed.connect(self._on_changed)
            self._pool.start(job)
        seen: set[str] = set()
        queue = []
        for key in keys:
            if key in seen or key in self._mem or key in self._inflight or key in self._failed:
                continue
            seen.add(key)
            queue.append(key)
        self._queue = queue
        self._pump()

    def mark_stale(self) -> None:
        """Klasör yeniden listelendi: bellekteki küçük resimler görününce dosyaya göre doğrulanır, hatalılar yeniden denenir."""
        self._unverified = set(self._mem)
        self._failed.clear()

    def forget(self, keys) -> None:
        for key in keys:
            img = self._mem.pop(key, None)
            if img is not None:
                self._mem_bytes -= img.sizeInBytes()
            self._mtimes.pop(key, None)
            self._durations.pop(key, None)
            self._failed.discard(key)

    def shutdown(self) -> None:
        self._closed = True
        self._queue = []
        self._pool.clear()
        self._pool.waitForDone(3000)

    # --- iç ---
    def _pump(self) -> None:
        while self._queue and len(self._inflight) < self._limit:
            key = self._queue.pop(0)
            self._inflight.add(key)
            job = _ThumbJob(key, self._disk)
            job.signals.done.connect(self._on_done)
            job.signals.failed.connect(self._on_failed)
            self._pool.start(job)

    def _on_done(self, key: str, image: QImage, dur: int, mtime) -> None:
        self._inflight.discard(key)
        old = self._mem.pop(key, None)
        if old is not None:
            self._mem_bytes -= old.sizeInBytes()
        self._mem[key] = image
        self._mem_bytes += image.sizeInBytes()
        self._mtimes[key] = int(mtime)
        if dur > 0:
            self._durations[key] = int(dur)
        self._evict()
        if self._disk is not None:
            self._stored += 1
            if self._stored % 400 == 0:
                self._pool.start(_PruneJob(self._disk))
        self.ready.emit(key)
        self._pump()

    def _on_failed(self, key: str) -> None:
        self._inflight.discard(key)
        self._failed.add(key)
        self.ready.emit(key)
        self._pump()

    def _on_changed(self, keys: list) -> None:
        self.forget(keys)
        for key in keys:
            self.ready.emit(key)
        self.invalidated.emit()

    def _evict(self) -> None:
        # Görünen + ön yüklenen kadarı (son kullanılanlar) her durumda kalır; çok küçük karolarda bütçe aşılabilir.
        while self._mem_bytes > self._budget and len(self._mem) > self._keep:
            key, img = self._mem.popitem(last=False)
            self._mem_bytes -= img.sizeInBytes()
            self._mtimes.pop(key, None)


# ---------------------------------------------------------------------------------------------------- model
class GalleryModel(QAbstractListModel):
    PathRole = Qt.ItemDataRole.UserRole

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._paths: list[Path] = []
        self._keys: list[str] = []
        self._rows: dict[str, int] | None = None
        self._live: set[str] = set()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._keys)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return self._paths[row].name
        if role == self.PathRole:
            return self._keys[row]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled

    def mimeTypes(self) -> list[str]:  # noqa: N802
        return ["text/uri-list"]

    def mimeData(self, indexes) -> QMimeData:  # noqa: N802
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self._keys[i.row()]) for i in indexes if i.isValid()])
        return mime

    def supportedDragActions(self) -> Qt.DropAction:  # noqa: N802
        return Qt.DropAction.CopyAction  # Gezgin'e sürüklemek kopyalar, asla taşımaz

    def set_files(self, paths: list[Path]) -> None:
        self.beginResetModel()
        self._paths = list(paths)
        self._keys = [str(p) for p in self._paths]
        self._rows = None
        self._live = set()
        self.endResetModel()

    def same_files(self, paths: list[Path]) -> bool:
        return len(paths) == len(self._paths) and all(a == b for a, b in zip(paths, self._paths))

    def key(self, row: int) -> str:
        return self._keys[row]

    def keys(self) -> list[str]:
        return self._keys

    def row_of(self, key: str) -> int:
        if self._rows is None:
            self._rows = {k: i for i, k in enumerate(self._keys)}
        return self._rows.get(key, -1)

    def is_video(self, row: int) -> bool:
        return self._paths[row].suffix.lower() in VIDEO_EXTS

    def is_live(self, row: int) -> bool:
        path = self._paths[row]
        return path.suffix.lower() == ".livp" or path.name.lower() in self._live

    def suffix(self, row: int) -> str:
        return self._paths[row].suffix.lstrip(".").upper()

    def set_live(self, names) -> None:
        self._live = set(names)


# ---------------------------------------------------------------------------------------------------- çizim
class GalleryDelegate(QStyledItemDelegate):
    """Kare, yuvarlak köşeli karolar: video süresi hapı, Canlı Fotoğraf rozeti, seçim halkası, üzerine gelince ad."""

    def __init__(self, view: GalleryView, provider: ThumbnailProvider) -> None:
        super().__init__(view)
        self._view = view
        self._provider = provider
        self._scaled: OrderedDict[str, tuple[int, QPixmap]] = OrderedDict()
        self.live_text = "LIVE"
        self.set_palette({"is_dark": True, "stage": "#0f0f0f", "text_muted": "#9e9e9e", "accent_edge": "#4DD0E1"})

    def set_palette(self, pal: dict) -> None:
        dark = bool(pal.get("is_dark", True))
        stage = QColor(pal.get("stage", "#0f0f0f"))
        self._placeholder = stage.lighter(185) if dark else stage.darker(108)
        self._muted = QColor(pal.get("text_muted", "#9e9e9e"))
        self._ring = QColor(pal.get("accent_edge", "#4DD0E1"))
        self._hover = QColor(255, 255, 255, 26) if dark else QColor(0, 0, 0, 18)
        self._view.viewport().update()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return self._view.gridSize()

    def clear_scaled(self, key: str | None = None) -> None:
        if key is None:
            self._scaled.clear()
        else:
            self._scaled.pop(key, None)

    def _tile_pixmap(self, key: str, side: int, dpr: float) -> QPixmap | None:
        phys = max(1, round(side * dpr))
        hit = self._scaled.get(key)
        if hit is not None and hit[0] == phys:
            self._scaled.move_to_end(key)
            return hit[1]
        img = self._provider.image(key)
        if img is None:
            return None
        scaled = img.scaled(phys, phys, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        pix = QPixmap(phys, phys)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, phys, phys), RADIUS * dpr, RADIUS * dpr)
        p.setClipPath(clip)
        p.drawImage(0, 0, scaled)
        p.end()
        pix.setDevicePixelRatio(dpr)
        self._scaled[key] = (phys, pix)
        while len(self._scaled) > 1500:
            self._scaled.popitem(last=False)
        return pix

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        model = index.model()
        row = index.row()
        key = model.key(row)
        tile = QRect(option.rect).adjusted(GAP // 2, GAP // 2, -(GAP // 2), -(GAP // 2))
        side = min(tile.width(), tile.height())
        tile.setSize(QSize(side, side))
        tile_f = QRectF(tile)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pix = self._tile_pixmap(key, side, self._view.devicePixelRatioF())
        if pix is not None:
            painter.drawPixmap(tile.topLeft(), pix)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._placeholder)
            painter.drawRoundedRect(tile_f, RADIUS, RADIUS)
            if self._provider.failed(key):
                font = QFont(option.font)
                font.setBold(True)
                font.setPixelSize(max(11, side // 9))
                painter.setFont(font)
                painter.setPen(self._muted)
                painter.drawText(tile, Qt.AlignmentFlag.AlignCenter, model.suffix(row) or "?")

        if hover and not selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._hover)
            painter.drawRoundedRect(tile_f, RADIUS, RADIUS)

        small = side < 100  # en küçük karolarda rozetler yazısız (yalnızca simge)
        pill_w = 0
        if model.is_video(row):
            pill_w = self._draw_video_pill(painter, option, tile_f, self._provider.duration(key), small)
        if model.is_live(row):
            self._draw_live_badge(painter, option, tile_f, small)
        if (hover or selected) and side >= 120:
            self._draw_caption(painter, option, tile_f, str(index.data(Qt.ItemDataRole.DisplayRole) or ""), pill_w)
        if selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self._ring, 2.6))
            painter.drawRoundedRect(tile_f.adjusted(-2.2, -2.2, 2.2, 2.2), RADIUS + 2, RADIUS + 2)
        painter.restore()

    @staticmethod
    def _badge_font(option, small: bool) -> QFont:
        font = QFont(option.font)
        font.setPixelSize(10 if small else 11)
        font.setBold(True)
        return font

    def _draw_video_pill(self, painter: QPainter, option, tile: QRectF, dur: int, small: bool) -> int:
        font = self._badge_font(option, small)
        text = _fmt_ms(dur) if dur > 0 and not small else ""
        painter.setFont(font)
        fm = painter.fontMetrics()
        h = 18.0 if small else 20.0
        tri_w = 7.0
        w = 8.0 + tri_w + (5.0 + fm.horizontalAdvance(text) if text else 0.0) + 7.0
        box = QRectF(tile.right() - w - 6.0, tile.bottom() - h - 6.0, w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 165))
        painter.drawRoundedRect(box, h / 2, h / 2)
        painter.setBrush(QColor(255, 255, 255, 240))
        cx, cy = box.left() + 8.0, box.center().y()
        painter.drawPolygon(QPolygonF([QPointF(cx, cy - 4.5), QPointF(cx + tri_w, cy), QPointF(cx, cy + 4.5)]))
        if text:
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(
                QRectF(cx + tri_w + 5.0, box.top(), w, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text
            )
        return int(w) + 6

    def _draw_live_badge(self, painter: QPainter, option, tile: QRectF, small: bool) -> None:
        font = self._badge_font(option, small)
        painter.setFont(font)
        fm = painter.fontMetrics()
        text = "" if small else self.live_text
        h = 18.0 if small else 20.0
        w = 8.0 + 11.0 + (5.0 + fm.horizontalAdvance(text) if text else 0.0) + 7.0
        box = QRectF(tile.left() + 6.0, tile.top() + 6.0, w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawRoundedRect(box, h / 2, h / 2)
        cx, cy = box.left() + 8.0 + 5.5, box.center().y()
        painter.setBrush(QColor(255, 255, 255, 245))
        painter.drawEllipse(QPointF(cx, cy), 2.0, 2.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor(255, 255, 255, 235), 1.2)
        pen.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(pen)
        painter.drawEllipse(QPointF(cx, cy), 5.2, 5.2)
        painter.setPen(QPen(QColor(255, 255, 255, 235), 1.1))
        painter.drawEllipse(QPointF(cx, cy), 3.5, 3.5)
        if text:
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(QRectF(cx + 5.5 + 5.0, box.top(), w, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)

    def _draw_caption(self, painter: QPainter, option, tile: QRectF, name: str, reserve: int) -> None:
        h = min(46.0, tile.height() * 0.36)
        band = QRectF(tile.left(), tile.bottom() - h, tile.width(), h)
        grad = QLinearGradient(band.topLeft(), band.bottomLeft())
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0, QColor(0, 0, 0, 175))
        clip = QPainterPath()
        clip.addRoundedRect(tile, RADIUS, RADIUS)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(band, grad)
        painter.restore()
        font = QFont(option.font)
        font.setPixelSize(12)
        painter.setFont(font)
        fm = painter.fontMetrics()
        avail = int(tile.width() - 16 - reserve)
        if avail < 24:
            return
        text = fm.elidedText(name, Qt.TextElideMode.ElideMiddle, avail)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(
            QRectF(tile.left() + 8.0, tile.bottom() - 26.0, avail, 20.0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text,
        )


# ---------------------------------------------------------------------------------------------------- görünüm
_NAV_KEYS = frozenset(
    {
        Qt.Key.Key_Left,
        Qt.Key.Key_Right,
        Qt.Key.Key_Up,
        Qt.Key.Key_Down,
        Qt.Key.Key_Home,
        Qt.Key.Key_End,
        Qt.Key.Key_PageUp,
        Qt.Key.Key_PageDown,
        Qt.Key.Key_Return,
        Qt.Key.Key_Enter,
        Qt.Key.Key_Escape,
        Qt.Key.Key_Plus,
        Qt.Key.Key_Equal,
        Qt.Key.Key_Minus,
    }
)


def clamp_cell(size) -> int:
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = CELL_DEFAULT
    return max(CELL_MIN, min(CELL_MAX, size))


class GalleryView(QListView):
    open_requested = pyqtSignal(int)
    close_requested = pyqtSignal()
    current_changed = pyqtSignal(int)
    context_requested = pyqtSignal(int, QPoint)
    cell_changed = pyqtSignal(int)
    file_dropped = pyqtSignal(str)

    def __init__(self, provider: ThumbnailProvider, cell: int = CELL_DEFAULT, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("gallery")
        self.provider = provider
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setMovement(QListView.Movement.Static)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setSpacing(0)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSelectionRectVisible(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setAcceptDrops(True)
        self._model = GalleryModel(self)
        self.setModel(self._model)
        self._delegate = GalleryDelegate(self, provider)
        self.setItemDelegate(self._delegate)
        self._syncing = False
        self._cols = 1
        self._cell = clamp_cell(cell)
        self._top_inset = 0
        self._wheel_rest = 0
        self._badge_token = 0
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._request_timer = QTimer(self)
        self._request_timer.setSingleShot(True)
        self._request_timer.timeout.connect(self._request_visible)
        self.selectionModel().currentChanged.connect(self._on_current)
        self.verticalScrollBar().valueChanged.connect(self._schedule_request)
        provider.ready.connect(self._on_thumb_ready)
        provider.invalidated.connect(self._schedule_request)
        self._apply_margins()
        self._regrid()

    # --- dış arayüz ---
    def gallery_model(self) -> GalleryModel:
        return self._model

    def cell(self) -> int:
        return self._cell

    def columns(self) -> int:
        return self._cols

    def set_palette(self, pal: dict) -> None:
        self._delegate.set_palette(pal)

    def set_live_text(self, text: str) -> None:
        self._delegate.live_text = text
        self.viewport().update()

    def refresh_scale(self) -> None:
        """Ekran ölçeği değişti: eski ölçekte hazırlanmış karo piksellerini at, yeniden çiz."""
        self._delegate.clear_scaled()
        self.viewport().update()

    def set_top_inset(self, px: int) -> None:
        if px != self._top_inset:
            self._top_inset = px
            self._apply_margins()

    def set_files(self, paths: list[Path], current: int) -> None:
        """Dosya listesini değiştir; aynı listeyse yalnızca seçim. Kaydırma konumu korunur, seçili karo görünür kalır."""
        if self._model.same_files(paths):
            self.select_row(current, QAbstractItemView.ScrollHint.EnsureVisible)
            return
        first_fill = self._model.rowCount() == 0
        value = self.verticalScrollBar().value()
        self._syncing = True
        try:
            self._model.set_files(paths)
        finally:
            self._syncing = False
        self._delegate.clear_scaled()
        self.provider.mark_stale()
        self.doItemsLayout()
        if not first_fill:
            self.verticalScrollBar().setValue(value)
        hint = QAbstractItemView.ScrollHint.PositionAtCenter if first_fill else QAbstractItemView.ScrollHint.EnsureVisible
        self.select_row(current, hint)
        self._start_badges()
        self._schedule_request()

    def select_row(self, row: int, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:
        if not 0 <= row < self._model.rowCount():
            return
        index = self._model.index(row, 0)
        self._syncing = True
        try:
            if self.currentIndex() != index or not self.selectionModel().isSelected(index):
                self.selectionModel().setCurrentIndex(index, self.selectionModel().SelectionFlag.ClearAndSelect)
        finally:
            self._syncing = False
        self.scrollTo(index, hint)

    def current_row(self) -> int:
        index = self.currentIndex()
        return index.row() if index.isValid() else -1

    def zoom(self, steps: int) -> None:
        self.set_cell(round(self._cell * (CELL_STEP ** steps)))

    def set_cell(self, size: int) -> None:
        size = clamp_cell(size)
        if size == self._cell:
            return
        anchor = self.currentIndex()
        anchor_visible = anchor.isValid() and self.viewport().rect().intersects(self.visualRect(anchor))
        if not anchor_visible:
            first, _last = self.visible_range()
            anchor = self._model.index(first, 0)
        self._cell = size
        self._delegate.clear_scaled()
        self._regrid()
        self.doItemsLayout()
        if anchor.isValid():
            hint = QAbstractItemView.ScrollHint.PositionAtCenter if anchor_visible else QAbstractItemView.ScrollHint.PositionAtTop
            self.scrollTo(anchor, hint)
        self._schedule_request()
        self.cell_changed.emit(size)

    def visible_range(self) -> tuple[int, int]:
        """Görünen satırların [ilk, son) aralığı (kısmen görünenler dahil)."""
        n = self._model.rowCount()
        if n == 0:
            return 0, 0
        gh = max(1, self.gridSize().height())
        top = self.visualRect(self._model.index(0, 0)).top()
        first_line = max(0, -top // gh)
        lines = self.viewport().height() // gh + 2
        return min(n, first_line * self._cols), min(n, (first_line + lines) * self._cols)

    def shutdown(self) -> None:
        self._badge_token += 1
        self._request_timer.stop()
        self._pool.clear()
        self._pool.waitForDone(2000)

    # --- iç ---
    def _apply_margins(self) -> None:
        self.setViewportMargins(10, 8 + self._top_inset, 4, 8)

    def _regrid(self) -> None:
        # Kaydırma çubuğunun yeri hep ayrılır: çubuk görününce/gizlenince sütun sayısı değişip titremesin.
        # Qt'nin ızgara yerleşimi satırı genişlik − 1'de kırar (QRect.right()); bir piksel pay bırakılır.
        bar = max(
            self.verticalScrollBar().sizeHint().width(),
            self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, None, self.verticalScrollBar()),
        )
        avail = max(CELL_MIN, self.maximumViewportSize().width() - bar - 2)
        cols = max(1, avail // self._cell)
        side = max(CELL_MIN // 2, avail // cols)
        self._cols = cols
        grid = QSize(side, side)
        if grid != self.gridSize():
            self.setGridSize(grid)
        self.verticalScrollBar().setSingleStep(max(24, side // 3))  # tekerlek çentiği (3 adım) ≈ bir satır

    def _schedule_request(self, *_args) -> None:
        if not self._request_timer.isActive():
            self._request_timer.start(0)

    def _request_visible(self) -> None:
        n = self._model.rowCount()
        if n == 0:
            return
        first, last = self.visible_range()
        span = max(self._cols, last - first)
        keys = self._model.keys()
        order = keys[first:last] + keys[last : min(n, last + span)] + keys[max(0, first - self._cols * 2) : first][::-1]
        self.provider.request(order)

    def _on_thumb_ready(self, key: str) -> None:
        row = self._model.row_of(key)
        if row >= 0:
            self._delegate.clear_scaled(key)
            self.update(self._model.index(row, 0))

    def _start_badges(self) -> None:
        self._badge_token += 1
        token = self._badge_token
        paths = [Path(k) for k in self._model.keys() if Path(k).suffix.lower() not in VIDEO_EXTS and not k.lower().endswith(".livp")]
        if not paths:
            return
        worker = _BadgeWorker(paths, token)
        worker.signals.done.connect(self._on_badges)
        self._pool.start(worker)

    def _on_badges(self, live: list, token: int) -> None:
        if token != self._badge_token:
            return
        self._model.set_live(live)
        self.viewport().update()

    def _on_current(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not self._syncing and current.isValid():
            self.current_changed.emit(current.row())

    # --- Qt olayları ---
    def keyboardSearch(self, search: str) -> None:  # noqa: N802
        """Harfler pencerenin kısayollarıdır (G, A, D…); ada göre arama kapalı."""

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ShortcutOverride and event.key() in _NAV_KEYS:
            mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
            if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                mods &= ~Qt.KeyboardModifier.ShiftModifier  # bazı klavyelerde + için Shift gerekir
            if mods == Qt.KeyboardModifier.NoModifier:
                event.accept()
                return True
        return super().event(event)

    def moveCursor(self, action, modifiers) -> QModelIndex:  # noqa: N802
        """Ok tuşları sıra boyunca ilerler: → satır sonunda alt satırın başına geçer (Qt'nin ızgara hareketi durur)."""
        n = self._model.rowCount()
        if n == 0:
            return QModelIndex()
        row = self.current_row()
        if row < 0:
            return self._model.index(0, 0)
        cols = max(1, self._cols)
        page = cols * max(1, self.viewport().height() // max(1, self.gridSize().height()))
        cursor = QAbstractItemView.CursorAction
        target = {
            cursor.MoveLeft: row - 1,
            cursor.MovePrevious: row - 1,
            cursor.MoveRight: row + 1,
            cursor.MoveNext: row + 1,
            cursor.MoveUp: row - cols if row >= cols else row,
            cursor.MoveDown: row + cols if row + cols < n else (n - 1 if row // cols < (n - 1) // cols else row),
            cursor.MovePageUp: row - page,
            cursor.MovePageDown: row + page,
            cursor.MoveHome: 0,
            cursor.MoveEnd: n - 1,
        }.get(action, row)
        return self._model.index(max(0, min(n - 1, target)), 0)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.AltModifier:
            row = self.current_row()
            if row >= 0:
                self.open_requested.emit(row)
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.close_requested.emit()
            event.accept()
            return
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom(1)
            event.accept()
            return
        if key == Qt.Key.Key_Minus:
            self.zoom(-1)
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._wheel_rest += event.angleDelta().y()
            steps = int(self._wheel_rest / 120)
            if steps:
                self._wheel_rest -= steps * 120
                self.zoom(steps)
            event.accept()
            return
        super().wheelEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        index = self.indexAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and index.isValid():
            self.open_requested.emit(index.row())
            event.accept()
            return
        event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        index = self.indexAt(event.pos())
        if not index.isValid():
            return
        if self.currentIndex() != index:
            self.selectionModel().setCurrentIndex(index, self.selectionModel().SelectionFlag.ClearAndSelect)
        self.context_requested.emit(index.row(), event.globalPos())

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._regrid()
        super().resizeEvent(event)
        self._schedule_request()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        # Kendi karosunu galeriye bırakmak bir şey yapmaz (yoksa sahne onu "dosya aç" sayardı).
        if event.source() is self or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if event.source() is self or not urls:
            event.ignore()
            return
        event.acceptProposedAction()
        self.file_dropped.emit(urls[0].toLocalFile())
