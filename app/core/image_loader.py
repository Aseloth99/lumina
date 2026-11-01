"""HEIC/JPEG yükleme — küçük önizleme yerine birincil görüntü, GUI'yi kilitlemeden."""

from __future__ import annotations

import collections
import importlib
import io
import os
import threading
from importlib.util import find_spec
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal
from PyQt6.QtGui import QImage
from app.core import startup_trace
from app.core.qt_safe import safe_emit
from app.core.svg_loader import SVG_EXTS, load_svg

# app.core.hdr.JXR_EXTS ile aynı (testle korunur). hdr numpy'yi yükler; JXR açılmadıkça içe aktarılmaz.
JXR_EXTS = frozenset({".jxr", ".wdp", ".hdp"})

pillow_heif.register_heif_opener()
try:
    import pillow_jxl  # noqa: F401
except ImportError:
    pass

try:
    import pillow_avif  # noqa: F401
except ImportError:
    pass


def _available(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# imagecodecs (JXL/AVIF/JXR) ve rawpy (RAW) numpy'yi de yükler (~75–150 ms): yalnızca o biçim açılınca
# içe aktarılır; burada yalnızca kurulu olup olmadıkları (dosyaya bakarak) öğrenilir.
HAS_IMAGECODECS = _available("imagecodecs")
HAS_RAWPY = _available("rawpy")


def __getattr__(name: str):
    """`image_loader.rawpy` / `image_loader.imagecodecs` (geriye uyum, testler): ilk erişimde yüklenir."""
    if name in ("rawpy", "imagecodecs"):
        return importlib.import_module(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _register_jxl_support() -> None:
    if not HAS_IMAGECODECS:
        return
    try:
        from PIL import ImageFile

        class JxlImageFile(ImageFile.ImageFile):
            format = "JXL"
            format_description = "JPEG XL image"

            def _open(self) -> None:
                import imagecodecs

                data = self.fp.read()
                arr = imagecodecs.jpegxl_decode(data)
                img = Image.fromarray(arr)
                self._size = img.size
                self._mode = img.mode
                self._arr = arr

            def load(self):
                if hasattr(self, "_arr"):
                    img = Image.fromarray(self._arr)
                    self.im = img.im
                    del self._arr
                return ImageFile.ImageFile.load(self)

        Image.register_open(
            JxlImageFile.format,
            JxlImageFile,
            lambda prefix: prefix.startswith(b"\xff\n")
            or prefix.startswith(b"\x00\x00\x00\x0cJXL \r\n\x87\n"),
        )
        Image.register_extension(JxlImageFile.format, ".jxl")
        Image.register_mime(JxlImageFile.format, "image/jxl")
    except Exception:
        pass


_register_jxl_support()

HEIF_EXTS = {".heic", ".heif", ".hif", ".heics"}
RAW_EXTS = {
    ".cr2",
    ".cr3",
    ".nef",
    ".nrw",
    ".arw",
    ".srf",
    ".sr2",
    ".dng",
    ".raf",
    ".rw2",
    ".orf",
    ".pef",
}


def _apply_icc(img: Image.Image) -> Image.Image:
    profile = img.info.get("icc_profile")
    if not profile:
        return img
    try:
        from PIL import ImageCms

        src = ImageCms.ImageCmsProfile(io.BytesIO(profile))
        dst = ImageCms.createProfile("sRGB")
        alpha = img.split()[-1] if img.mode == "RGBA" else None
        work = img.convert("RGB") if img.mode != "RGB" else img
        converted = ImageCms.profileToProfile(work, src, dst, outputMode="RGB")
        if converted is None:
            return img
        if alpha is not None:
            converted = converted.convert("RGBA")
            converted.putalpha(alpha)
        return converted
    except Exception:
        return img


def load_jxr(path: Path, max_side: int = 0) -> Image.Image:
    """JPEG XR (.jxr/.wdp/.hdp). HDR (float/scRGB) ise SDR'ye ton eşlenir (app.core.hdr).

    `max_side` > 0 ise küçük resim için doğrusal uzayda önce küçültülür, sonra ton eşlenir.
    """
    if not HAS_IMAGECODECS:
        raise OSError("JPEG XR (.jxr) için 'imagecodecs' paketi gerekli.")
    from app.core.hdr import hdr_array_to_pil

    try:
        import imagecodecs

        with open(path, "rb") as handle:
            arr = imagecodecs.jpegxr_decode(handle.read())
    except Exception as exc:  # noqa: BLE001 — kullanıcıya anlaşılır mesaj
        raise OSError(f"JPEG XR dosyası çözülemedi: {exc}") from exc
    if max_side and max(arr.shape[:2]) > max_side * 2:
        try:
            import cv2

            scale = max_side / max(arr.shape[:2])
            size = (max(1, int(arr.shape[1] * scale)), max(1, int(arr.shape[0] * scale)))
            arr = cv2.resize(arr, size, interpolation=cv2.INTER_AREA)
        except Exception:  # noqa: BLE001 — cv2 yoksa seyrek örnekleme
            k = max(1, max(arr.shape[:2]) // (max_side * 2))
            arr = arr[::k, ::k]
    return hdr_array_to_pil(arr)


def load_pil(path: Path) -> Image.Image:
    path = Path(path)
    suffix = path.suffix.lower()
    img = None

    if suffix in JXR_EXTS:
        img = load_jxr(path)

    if suffix in SVG_EXTS:
        img = load_svg(path)

    if img is None and suffix in RAW_EXTS and HAS_RAWPY:
        try:
            import rawpy

            with rawpy.imread(str(path)) as raw_file:
                rgb = raw_file.postprocess(use_camera_wb=True, half_size=False)
                img = Image.fromarray(rgb)
        except Exception:
            img = None

    if img is None and suffix in HEIF_EXTS:
        try:
            heif = pillow_heif.open_heif(str(path), convert_hdr_to_8bit=True)
            img = heif.to_pillow()
        except Exception:
            img = None

    if img is None and suffix == ".jxl" and HAS_IMAGECODECS:
        try:
            import imagecodecs

            with open(path, "rb") as f:
                arr = imagecodecs.jpegxl_decode(f.read())
            img = Image.fromarray(arr)
        except Exception:
            img = None

    if img is None and suffix in {".avif", ".avifs"} and HAS_IMAGECODECS:
        try:
            import imagecodecs

            with open(path, "rb") as f:
                arr = imagecodecs.avif_decode(f.read())
            img = Image.fromarray(arr)
        except Exception:
            img = None

    if img is None:
        try:
            with Image.open(path) as raw:
                raw.load()
                img = raw.copy()
        except Exception:
            if suffix == ".jxl" and HAS_IMAGECODECS:
                import imagecodecs

                with open(path, "rb") as f:
                    arr = imagecodecs.jpegxl_decode(f.read())
                img = Image.fromarray(arr)
            elif suffix in {".avif", ".avifs"} and HAS_IMAGECODECS:
                import imagecodecs

                with open(path, "rb") as f:
                    arr = imagecodecs.avif_decode(f.read())
                img = Image.fromarray(arr)
            else:
                raise

    img = ImageOps.exif_transpose(img)
    img = _apply_icc(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in getattr(img, "info", {})):
        if img.mode != "RGBA":
            img = img.convert("RGBA")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return img


_RAW_PREVIEW_MIN_SIDE = 800  # daha küçük gömülü önizleme bulanık görünür; atla
_LIBRAW_FLIP_DEGREES = {3: 180, 5: 90, 6: -90}  # libraw flip -> PIL rotate (saat yönünün tersi pozitif)


def extract_raw_preview(path: Path) -> Image.Image | None:
    """RAW dosyasındaki gömülü JPEG/bitmap önizlemeyi hızla (yaklaşık yüz ms) çıkar; yoksa/küçükse None.

    Tam çözümleme (postprocess) saniyeler sürer; bu önizleme onu beklemeden ilk görüntüyü verir.
    """
    if not HAS_RAWPY:
        return None
    try:
        import rawpy

        with rawpy.imread(str(path)) as raw_file:
            thumb = raw_file.extract_thumb()
            flip = int(getattr(raw_file.sizes, "flip", 0) or 0)
        if thumb.format == rawpy.ThumbFormat.JPEG:
            img = Image.open(io.BytesIO(thumb.data))
            img.load()
            has_orientation = bool(img.getexif().get(0x0112))
            img = ImageOps.exif_transpose(img)
        elif thumb.format == rawpy.ThumbFormat.BITMAP:
            img = Image.fromarray(thumb.data)
            has_orientation = False
        else:
            return None
        if max(img.size) < _RAW_PREVIEW_MIN_SIDE:
            return None
        if not has_orientation and flip in _LIBRAW_FLIP_DEGREES:
            img = img.rotate(_LIBRAW_FLIP_DEGREES[flip], expand=True)
        return img.convert("RGB")
    except Exception:  # noqa: BLE001 — önizleme yoksa tam çözümlemeye düşülür
        return None


def pil_to_qimage(img: Image.Image) -> QImage:
    """PIL tamponunun GC ile silinmemesi için QImage kopyalanır. Saydamlık yoksa RGB888 kullanılır."""
    if img.mode == "RGB":
        buf = img.tobytes("raw", "RGB")
        qimg = QImage(buf, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
        return qimg.copy()
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    buf = img.tobytes("raw", "RGBA")
    qimg = QImage(buf, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
    return qimg.copy()



class ImageLoadSignals(QObject):
    loaded = pyqtSignal(str, QImage, int, int, int)  # path, image, w, h, token
    # RAW: gömülü önizleme `loaded` ile gösterildikten sonra tam çözünürlük `upgraded` ile gelir.
    upgraded = pyqtSignal(str, QImage, int, int, int)
    failed = pyqtSignal(str, str, int)


class VideoPosterSignals(QObject):
    loaded = pyqtSignal(str, QImage, int, int, int)  # path, image, w, h, token
    failed = pyqtSignal(str, str, int)
    meta = pyqtSignal(str, int, int)  # path, duration_ms, token


class VideoPosterWorker(QRunnable):
    def __init__(self, path: str, token: int) -> None:
        super().__init__()
        self.path = path
        self.token = token
        self.signals = VideoPosterSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            from app.core.video_io import poster_and_duration

            try:
                qimg, dur = poster_and_duration(self.path)  # video tek kez açılır
            except Exception:
                qimg, dur = None, 0
            try:
                safe_emit(self.signals, "meta", self.path, int(dur), self.token)
            except Exception:
                pass
            if qimg is not None and not qimg.isNull():
                safe_emit(self.signals, "loaded", self.path, qimg, qimg.width(), qimg.height(), self.token)
            else:
                safe_emit(self.signals, "failed", self.path, "Poster alınamadı", self.token)
        except Exception as exc:
            safe_emit(self.signals, "failed", self.path, str(exc), self.token)


_image_cache: collections.OrderedDict[tuple[str, float], tuple[QImage, int, int]] = collections.OrderedDict()
_cache_lock = threading.Lock()
_MAX_CACHE_ITEMS = 16
_MAX_CACHE_BYTES = 512 * 1024 * 1024


def path_key(path: Path | str) -> str:
    """Dosyaya dokunmadan karşılaştırma anahtarı (mutlak + büyük/küçük harf normalize).

    `Path.resolve()` Windows'ta dosyayı açar (GetFinalPathNameByHandle); ağ/WSL yolunda her çağrı bir
    gidiş-dönüştür ve GUI thread'inde açılışı saniyelerce geciktiriyordu. Önbellek ve "oynatılamaz"
    kümesi için metin anahtarı yeterli (en kötü durumda aynı dosya iki anahtarla önbelleğe girer).
    """
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def same_path_text(a: Path | str, b: Path | str) -> bool:
    return path_key(a) == path_key(b)


def has_cached_entry(path: Path | str) -> bool:
    """Bu yol için (tazeliğine bakmadan) önbellekte kayıt var mı? Dosya sistemine dokunmaz."""
    key = path_key(path)
    with _cache_lock:
        return any(k[0] == key for k in _image_cache)


def get_cached_image(path: Path | str) -> tuple[QImage, int, int] | None:
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
        key = (path_key(p), mtime)
    except OSError:
        return None
    with _cache_lock:
        if key in _image_cache:
            _image_cache.move_to_end(key)
            return _image_cache[key]
    return None


def put_cached_image(path: Path | str, qimg: QImage, w: int, h: int) -> None:
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
        key = (path_key(p), mtime)
    except OSError:
        return
    with _cache_lock:
        _image_cache[key] = (qimg, w, h)
        _image_cache.move_to_end(key)
        _evict_locked()


def _evict_locked() -> None:
    """Öğe sayısı VE toplam bayt sınırı (100 MP'lik görsellerde 16 öğe GB'lara çıkabiliyordu).

    En son eklenen öğe her zaman korunur (tek dev görsel bile gösterilebilsin).
    """
    total = sum(item[0].sizeInBytes() for item in _image_cache.values())
    while len(_image_cache) > 1 and (len(_image_cache) > _MAX_CACHE_ITEMS or total > _MAX_CACHE_BYTES):
        _key, (old, _w, _h) = _image_cache.popitem(last=False)
        total -= old.sizeInBytes()


def clear_image_cache() -> None:
    with _cache_lock:
        _image_cache.clear()


# Açılış ön-çözümü: yol anahtarı -> çözüm bitince kurulan olay (bkz. prefetch).
_inflight: dict[str, threading.Event] = {}
# RAW önce gömülü önizlemeyle gösterilir (tam çözüm saniyeler sürer), SVG'yi Qt çizer (QApplication
# ister), GIF/LIVP başka yoldan açılır: bunlar ön-çözülmez.
_PREFETCH_SKIP = frozenset(RAW_EXTS | SVG_EXTS | {".gif", ".livp"})


def prefetch(path: Path | str) -> bool:
    """Komut satırındaki görseli, pencere kurulurken arka planda çözüp önbelleğe koy.

    Pencerenin kurulması (Qt, arayüz, ilk native pencere) ana thread'de ~250 ms sürer; eskiden görsel
    bundan SONRA okunmaya başlanıyordu (ağ/WSL yolunda yalnızca okuma ~300 ms). Aynı yol için açılan
    `ImageLoadWorker` bu işi bekler, ikinci kez çözmez. Uygun değilse (video, RAW, SVG...) False.
    """
    from app.core.matcher import IMAGE_EXTS

    suffix = Path(path).suffix.lower()
    if suffix not in IMAGE_EXTS or suffix in _PREFETCH_SKIP:
        return False
    key = path_key(path)
    with _cache_lock:
        if key in _inflight:
            return True
        done = _inflight[key] = threading.Event()

    def run() -> None:
        try:
            pil = load_pil(Path(path))
            qimg = pil_to_qimage(pil)
            startup_trace.mark("image_decoded")
            put_cached_image(path, qimg, pil.width, pil.height)
        except Exception:  # noqa: BLE001 — işçi yeniden dener ve hatayı kullanıcıya o bildirir
            pass
        finally:
            with _cache_lock:
                _inflight.pop(key, None)
            done.set()

    threading.Thread(target=run, name="LuminaPrefetch", daemon=True).start()
    return True


def _await_prefetch(path: Path | str, timeout: float = 120.0) -> None:
    with _cache_lock:
        pending = _inflight.get(path_key(path))
    if pending is not None:
        pending.wait(timeout)


class ImageLoadWorker(QRunnable):
    def __init__(self, path: str, token: int) -> None:
        super().__init__()
        self.path = path
        self.token = token
        self.signals = ImageLoadSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            _await_prefetch(self.path)  # açılışta aynı görsel zaten çözülüyorsa onu bekle
            cached = get_cached_image(self.path)
            if cached is not None:
                qimg, w, h = cached
                safe_emit(self.signals, "loaded", self.path, qimg, w, h, self.token)
                return

            previewed = False
            if self.token >= 0 and Path(self.path).suffix.lower() in RAW_EXTS:  # token<0: ön yükleme
                preview = extract_raw_preview(Path(self.path))
                if preview is not None:
                    pq = pil_to_qimage(preview)
                    safe_emit(self.signals, "loaded", self.path, pq, preview.width, preview.height, self.token)
                    previewed = True

            pil = load_pil(Path(self.path))
            qimg = pil_to_qimage(pil)
            if self.token >= 0:
                startup_trace.mark("image_decoded")
            put_cached_image(self.path, qimg, pil.width, pil.height)
            safe_emit(
                self.signals, "upgraded" if previewed else "loaded", self.path, qimg, pil.width, pil.height, self.token
            )
        except Exception as exc:  # noqa: BLE001 — kullanıcıya gösterilecek
            safe_emit(self.signals, "failed", self.path, str(exc), self.token)
