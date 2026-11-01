"""SVG (.svg / .svgz): taban görüntü bir kez rasterleştirilir, sonrası görüntü hattı (PIL → QImage) aynıdır.

* Çizici: `resvg` (resvg-py) — clip-path, mask, filtre, metin ve `use` dahil SVG'yi doğru çizer. QtSvg
  `clip-path`'i yok sayar (kırpılmış logolar/illüstrasyonlar bozuk görünür); bu yüzden yalnızca resvg
  kurulu değilse ya da bir dosyayı çizemezse yedek olarak kullanılır.
* Boyut: SVG'nin kendi boyutu ne olursa olsun uzun kenar `RENDER_LONG_SIDE` pikselde çizilir; böylece
  24 px'lik bir simge de, dev bir harita da pencereye sığdırılınca net kalır. Bu TABAN görüntüdür: tuval
  ölçeği taban pikselini aşınca görünen bölge ekran çözünürlüğünde yeniden vektörden çizilir
  (`app/ui/svg_overlay.py` + `app/core/svg_region.py`): sonsuz yakınlaştırmada keskin kalır.
* Birimler: resvg-py'nin `dpi` varsayılanı 0'dır ve mm/pt/in/cm/pc birimli SVG'ler "invalid size" ile
  çizilemiyordu (QtSvg'ye düşüp clip-path kaybediliyordu); 96 dpi açıkça verilir.
* Arka plan: çoğu SVG saydamdır ve siyah çizgilerle gelir; koyu tuvalde görünmez olurdu. İçerik koyuysa
  açık bir zemine oturtulur, açıksa (beyaz simge) saydam kalır ve koyu tuvalde olduğu gibi görünür. Karar
  taban görüntüde BİR KEZ verilir (alfa var = saydam kaldı); bindirme aynı kararı kullanır (zemin yanıp sönmez).
* Güvenlik: yalnızca dosya okunur (ağdan bir şey indirilmez); dosya ve .svgz açılmış boyutu sınırlıdır;
  kök etiket dışında XML ağacı kurulmaz.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

from app.core import svg_region

try:
    import resvg_py
except Exception:  # noqa: BLE001 — kurulu değilse QtSvg ile devam
    resvg_py = None

SVG_EXTS = frozenset({".svg", ".svgz"})

RENDER_LONG_SIDE = 3200  # tam boy taban görünüm; küçük resim kendi (daha küçük) boyutunu ister
MAX_PIXELS = 12_000_000
MAX_FILE_BYTES = 64 * 1024 * 1024
QT_MAX_VIRTUAL_SIDE = 2_000_000  # yedek bölge çiziminde belgenin sanal piksel kenarı üst sınırı
FALLBACK_SIZE = (512, 512)  # ne width/height ne viewBox varsa

# İçerik ortalama parlaklığı bunun altındaysa (koyu çizim) saydam alan açık zemine oturtulur.
_DARK_CONTENT_LUMA = 0.6
_LIGHT_BACKGROUND = svg_region.LIGHT_BACKGROUND


def _read(path: Path) -> bytes:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise OSError(f"SVG çok büyük / SVG too large ({size // (1024 * 1024)} MB)")
    return path.read_bytes()


def _qt_renderer(data: bytes, text: str | None = None) -> QSvgRenderer | None:
    """QtSvg çizici. `text` (çözülmüş belge) varsa UTF-8'e çevrilip verilir: UTF-16/Latin-1 dosyaları ve
    .svgz'yi QtSvg tek başına okuyamaz; yoksa ham bayt (QtSvg gzip başlığını kendisi tanır)."""
    payload = svg_region.to_utf8_bytes(text) if text is not None else data
    renderer = QSvgRenderer(QByteArray(payload))
    return renderer if renderer.isValid() else None


def _qt_native(renderer: QSvgRenderer) -> tuple[int, int] | None:
    size = renderer.defaultSize()
    w, h = size.width(), size.height()
    if w <= 0 or h <= 0:
        box = renderer.viewBoxF()
        w, h = int(round(box.width())), int(round(box.height()))
    return (w, h) if w > 0 and h > 0 else None


def _native_size(renderer: QSvgRenderer) -> tuple[int, int]:
    return _qt_native(renderer) or FALLBACK_SIZE


def _document(data: bytes) -> tuple[str | None, tuple[float, float] | None]:
    """Metne çevrilmiş belge ve resvg'nin çıkaracağı boyut (px, 96 dpi); çözülemezse (None, None)."""
    try:
        text = svg_region.bytes_to_text(data)
    except Exception:  # noqa: BLE001 — bozuk gzip vb.: çağıran QtSvg'ye düşer
        return None, None
    root = svg_region.find_root(text)
    return text, (svg_region.root_size(root.attrs) if root is not None else None)


def native_size(path: Path) -> tuple[int, int] | None:
    """SVG'nin kendi bildirdiği boyut (ayrıntılar paneli için, 96 dpi px); okunamazsa/belirsizse None."""
    try:
        data = _read(Path(path))
    except Exception:  # noqa: BLE001 — yalnızca bilgi satırı
        return None
    text, size = _document(data)
    if size is not None:
        return svg_region.pixel_size(size)
    try:
        renderer = _qt_renderer(data, text)
    except Exception:  # noqa: BLE001
        return None
    return _qt_native(renderer) if renderer is not None else None


def _target_size(native: tuple[float, float], long_side: int) -> tuple[int, int]:
    w, h = native
    scale = long_side / max(w, h)
    out_w = max(1, int(round(w * scale)))
    out_h = max(1, int(round(h * scale)))
    if out_w * out_h > MAX_PIXELS:
        shrink = (MAX_PIXELS / (out_w * out_h)) ** 0.5
        out_w = max(1, int(out_w * shrink))
        out_h = max(1, int(out_h * shrink))
    return out_w, out_h


def _resvg_options(path: Path, text: str) -> dict:
    return {
        "resources_dir": str(path.parent),  # göreli <image href="x.png"> (svg_path ile resvg bunu çözmüyordu)
        "dpi": svg_region.RESVG_DPI,
        "skip_system_fonts": not svg_region.has_text_elements(text),
    }


def _probe_aspect(path: Path, text: str) -> tuple[int, int] | None:
    """Boyutu belgeden çözülemeyen SVG'nin (içerik sınırlarına bağlı) resvg'ye göre en-boy oranı."""
    if resvg_py is None:
        return None
    try:
        png = resvg_py.svg_to_bytes(svg_string=text, width=64, **_resvg_options(path, text))
        with Image.open(io.BytesIO(bytes(png))) as probe:
            return probe.size
    except Exception:  # noqa: BLE001
        return None


def _render_resvg(path: Path, text: str | None, size: tuple[int, int] | None) -> Image.Image | None:
    """resvg ile çiz; kurulu değilse ya da dosyayı çizemezse None (çağıran QtSvg'ye düşer)."""
    if resvg_py is None or text is None:
        return None
    options = _resvg_options(path, text)
    if size is not None:
        # resvg en-boy oranını korur: uzun kenarı vermek yeter
        options["width" if size[0] >= size[1] else "height"] = max(size)
    try:
        png = resvg_py.svg_to_bytes(svg_string=text, **options)
        img = Image.open(io.BytesIO(bytes(png)))
        img.load()
        return img.convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def _render_qt(renderer: QSvgRenderer, size: tuple[int, int]) -> Image.Image:
    out_w, out_h = size
    canvas = QImage(out_w, out_h, QImage.Format.Format_ARGB32_Premultiplied)
    if canvas.isNull():
        raise OSError("SVG çizilemedi / SVG could not be rendered")
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        renderer.render(painter, QRectF(0, 0, out_w, out_h))
    finally:
        painter.end()
    canvas = canvas.convertToFormat(QImage.Format.Format_RGBA8888)  # önceden çarpılmış alfa → düz alfa
    ptr = canvas.constBits()
    ptr.setsize(canvas.sizeInBytes())
    return Image.frombuffer(
        "RGBA", (canvas.width(), canvas.height()), bytes(ptr), "raw", "RGBA", canvas.bytesPerLine(), 1
    )


def render_region_qt(
    source: svg_region.SvgSource, box: tuple[float, float, float, float], px: tuple[int, int], kind: str
) -> QImage | None:
    """Yedek bölge çizimi (resvg yok / işçi yok): belgenin TAMAMI, istenen bölge `px` penceresine denk gelecek
    büyüklükte ve konumda QtSvg ile çizilir (QtSvg'nin iç içe <svg> "kamera" belgesini doğru çizmediği ölçüldü).
    Kurulamazsa/ölçek aşırıysa None. `kind == "light"`: açık zemin önce boyanır (opak); `keep`: saydam kalır.
    """
    fx, fy, fw, fh = box
    if fw <= 0 or fh <= 0 or px[0] < 1 or px[1] < 1:
        return None
    full_w, full_h = px[0] / fw, px[1] / fh
    if max(full_w, full_h) > QT_MAX_VIRTUAL_SIDE:
        return None  # QtSvg bu ölçekte hassasiyetini yitirir: tabanda kal
    renderer = QSvgRenderer(QByteArray(svg_region.to_utf8_bytes(source.text)))
    if not renderer.isValid():
        return None
    image = QImage(px[0], px[1], QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():
        return None
    image.fill(QColor(*_LIGHT_BACKGROUND) if kind == "light" else Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        renderer.render(painter, QRectF(-fx * full_w, -fy * full_h, full_w, full_h))
    finally:
        painter.end()
    return image.convertToFormat(QImage.Format.Format_RGB32) if kind == "light" else image


def _content_luma(img: Image.Image) -> float | None:
    """Çizilen (görünür) piksellerin alfa ağırlıklı ortalama parlaklığı 0..1; hiç içerik yoksa None."""
    import numpy as np  # yalnızca SVG çizilince (numpy açılışta yüklenmesin)

    small = img.copy()
    small.thumbnail((128, 128), Image.Resampling.BOX)
    arr = np.asarray(small, dtype=np.float32)
    alpha = arr[..., 3] / 255.0
    total = float(alpha.sum())
    if total < 0.5:
        return None
    luma = (0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]) / 255.0
    return float((luma * alpha).sum() / total)


def _settle_background(img: Image.Image) -> Image.Image:
    """Saydam alanı olan koyu içerik açık zemine oturur (RGB döner); aksi halde görüntü olduğu gibi kalır."""
    if img.getchannel("A").getextrema()[0] == 255:
        return img.convert("RGB")  # SVG kendi zeminini çiziyor
    luma = _content_luma(img)
    if luma is None or luma >= _DARK_CONTENT_LUMA:
        return img
    base = Image.new("RGBA", img.size, _LIGHT_BACKGROUND + (255,))
    base.alpha_composite(img)
    return base.convert("RGB")


def load_svg(path: Path, long_side: int = RENDER_LONG_SIDE) -> Image.Image:
    """SVG'yi `long_side` pikselde çizip PIL görüntüsü (RGB veya saydamsa RGBA) olarak döndürür."""
    path = Path(path)
    data = _read(path)
    text, doc_size = _document(data)

    renderer: QSvgRenderer | None = None
    native: tuple[float, float] | None = doc_size
    if native is None and text is not None:
        native = _probe_aspect(path, text)  # içerik sınırına bağlı boyut: yalnızca en-boy oranı gerekir
    if native is None:
        renderer = _qt_renderer(data, text)
        native = _native_size(renderer) if renderer is not None else None
    size = _target_size(native, max(1, int(long_side))) if native is not None else None

    img = _render_resvg(path, text, size)
    if img is None:
        renderer = renderer or _qt_renderer(data, text)
        if renderer is None:
            raise OSError("SVG okunamadı / invalid or unsupported SVG")
        if size is None:
            size = _target_size(_native_size(renderer), max(1, int(long_side)))
        img = _render_qt(renderer, size)
    return _settle_background(img)
