"""SVG kök etiketi + bölge çizimi (saf Python; Qt YOK, çizim işçi sürecinde de içe aktarılır).

Amaç: vektörü "yakınlaştırdıkça netleşsin" diye görünen BÖLGEYİ ekran çözünürlüğünde yeniden çizmek. resvg-py
bölge kırpma seçeneği sunmaz; bu yüzden belge bir "kamera" ile sarılır:

    <svg width=tw height=th viewBox="bölge" preserveAspectRatio="none">   <- kamera (dış kök)
      <svg width=SW height=SH ...özgün kök özniteliklerinin kalanı...>   <- özgün kök (iç içe)
        ...özgün içerik, DEĞİŞTİRİLMEDEN...
      </svg>
    </svg>

Yalnızca KÖK ETİKET metin olarak yeniden yazılır; içerik, `%` birimleri, gradyanlar, `use`, CSS, filtreler,
DOCTYPE varlıkları olduğu gibi kalır (özgün kök, kendi viewBox/preserveAspectRatio'suyla iç içe bir <svg> olarak
çizilir). XML ağacı KURULMAZ (varlık patlamasına/bellek şişmesine yol açmaz); .svgz için açılmış boyut sınırlıdır.

Bölge, taban görüntünün [0,1]x[0,1] kesirleriyle verilir (taban görüntü SVG'nin boyut dikdörtgeninin tamamıdır).
"""

from __future__ import annotations

import io
import math
import re
import zlib
from dataclasses import dataclass
from pathlib import Path

try:
    import resvg_py
except Exception:  # noqa: BLE001 — kurulu değilse çağıran QtSvg'ye düşer
    resvg_py = None

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TEXT_BYTES = 192 * 1024 * 1024  # .svgz açılmış boyutu (gzip bombasına karşı)
MAX_ROOT_TAG_CHARS = 4 * 1024 * 1024
LIGHT_BACKGROUND = (246, 247, 249)
RESVG_DPI = 96.0  # resvg-py'nin varsayılanı 0: mm/pt/in/cm/pc birimli SVG'ler "invalid size" ile çizilemiyordu
RESVG_FONT_SIZE = 16.0  # em/ex birimleri için (resvg-py varsayılanı)

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

_UNIT_PX = {
    "": 1.0,
    "px": 1.0,
    "pt": 96.0 / 72.0,
    "pc": 16.0,
    "mm": 96.0 / 25.4,
    "cm": 96.0 / 2.54,
    "in": 96.0,
    "em": RESVG_FONT_SIZE,
    "ex": RESVG_FONT_SIZE / 2.0,
}
_LENGTH_RE = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(px|pt|pc|mm|cm|in|em|ex|%)?\s*$")
_ATTR_RE = re.compile(r"""([^\s=/>"']+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_TAG_NAME_RE = re.compile(r"<([^\s/>]+)")
_TAG_END_RE = re.compile(r"""(?:[^>"']|"[^"]*"|'[^']*')*>""")
_XML_ENCODING_RE = re.compile(r"""(<\?xml[^>]*?\bencoding\s*=\s*)(["'])[^"']*\2""", re.IGNORECASE)
_DECLARED_ENCODING_RE = re.compile(rb"""<\?xml[^>]*?\bencoding\s*=\s*["']([A-Za-z0-9_.:-]+)["']""", re.IGNORECASE)


# --------------------------------------------------------------------------- okuma / çözme
def read_svg_bytes(path: Path) -> bytes:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise OSError(f"SVG çok büyük / SVG too large ({size // (1024 * 1024)} MB)")
    return path.read_bytes()


def inflate(data: bytes) -> bytes:
    """.svgz ise gzip'i sınırlı boyutta aç; değilse olduğu gibi döndür."""
    if data[:2] != b"\x1f\x8b":
        return data
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = inflater.decompress(data, MAX_TEXT_BYTES + 1)
    if len(out) > MAX_TEXT_BYTES:
        raise OSError("SVGZ açılınca çok büyük / SVGZ too large when decompressed")
    return out


def decode_text(data: bytes) -> str:
    """Bayt dizisini metne çevir: BOM, UTF-8, bildirilen kodlama, son çare cp1252 (resvg yalnızca UTF-8 ister)."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    declared = _DECLARED_ENCODING_RE.search(data[:512])
    if declared:
        try:
            return data.decode(declared.group(1).decode("ascii"))
        except (LookupError, UnicodeDecodeError):
            pass
    return data.decode("cp1252", "replace")


def to_utf8_bytes(text: str) -> bytes:
    """Metni UTF-8 bayta çevir; XML bildirimindeki `encoding` UTF-8'e düzeltilir (yoksa Qt metni yanlış çözer)."""
    head = _XML_ENCODING_RE.sub(r'\1"UTF-8"', text[:512], count=1)
    return (head + text[512:]).encode("utf-8")


def bytes_to_text(data: bytes) -> str:
    return decode_text(inflate(data))


def has_text_elements(text: str) -> bool:
    """Metin yoksa resvg'nin sistem yazı tiplerini taramasına gerek yok."""
    return "<text" in text or ":text" in text


# --------------------------------------------------------------------------- kök etiket
@dataclass(frozen=True)
class RootTag:
    start: int  # `<svg` başlangıcı
    end: int  # etiketin `>` sonrası
    name: str
    attrs: dict[str, str]
    self_closing: bool


def _skip_doctype(text: str, pos: int) -> int:
    """`<!DOCTYPE ...>` (iç alt küme dahil) sonrasının konumu; kapanmıyorsa -1."""
    i, n = pos + 2, len(text)
    quote = ""
    in_subset = False
    while i < n:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
        elif in_subset and text.startswith("<!--", i):
            close = text.find("-->", i + 4)
            if close < 0:
                return -1
            i = close + 3
            continue
        elif ch in "\"'":
            quote = ch
        elif ch == "[":
            in_subset = True
        elif ch == "]":
            in_subset = False
        elif ch == ">" and not in_subset:
            return i + 1
        i += 1
    return -1


def parse_attrs(chunk: str) -> dict[str, str]:
    return {m.group(1): m.group(2) if m.group(2) is not None else m.group(3) for m in _ATTR_RE.finditer(chunk)}


def find_root(text: str) -> RootTag | None:
    """Belgenin kök `<svg>` etiketi; kök svg değilse/bozuksa None. Yalnızca önsöz taranır, DOM kurulmaz."""
    n = len(text)
    i = 1 if text.startswith("﻿") else 0
    while i < n:
        j = text.find("<", i)
        if j < 0:
            return None
        if text.startswith("<?", j):
            close = text.find("?>", j + 2)
            if close < 0:
                return None
            i = close + 2
            continue
        if text.startswith("<!--", j):
            close = text.find("-->", j + 4)
            if close < 0:
                return None
            i = close + 3
            continue
        if text.startswith("<!", j):
            i = _skip_doctype(text, j)
            if i < 0:
                return None
            continue
        name_match = _TAG_NAME_RE.match(text, j)
        if name_match is None or name_match.group(1) != "svg":
            return None  # kök svg değil (ya da önekli ad: desteklenmez)
        end_match = _TAG_END_RE.match(text, name_match.end(), min(n, name_match.end() + MAX_ROOT_TAG_CHARS))
        if end_match is None:
            return None
        end = end_match.end()
        inner = text[name_match.end() : end - 1]
        self_closing = inner.rstrip().endswith("/")
        return RootTag(j, end, "svg", parse_attrs(inner), self_closing)
    return None


# --------------------------------------------------------------------------- boyut matematiği
def parse_length(value: str | None) -> tuple[float, str] | None:
    """`"12.5mm"` -> (12.5, "mm"); geçersiz/boşsa None."""
    if value is None:
        return None
    match = _LENGTH_RE.match(value)
    if match is None:
        return None
    number = float(match.group(1))
    if not math.isfinite(number):
        return None
    return number, (match.group(2) or "")


def parse_viewbox(value: str | None) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    parts = re.split(r"[\s,]+", value.strip())
    if len(parts) != 4:
        return None
    try:
        x, y, w, h = (float(p) for p in parts)
    except ValueError:
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        return None
    return x, y, w, h


def root_size(attrs: dict[str, str]) -> tuple[float, float] | None:
    """resvg'nin bu kökten çıkaracağı boyut (96 dpi px); belirlenemiyorsa (içerik sınırlarına bağlıysa) None.

    Kurallar (resvg ile karşılaştırılarak doğrulanmıştır): width/height yok -> viewBox boyutu; yalnız biri
    var -> diğeri viewBox oranından; `%` -> viewBox boyutunun yüzdesi; viewBox'sız ve eksik/yüzdeli boyut
    içerik sınırlarından hesaplanır (None döner, bölge çizimi bu belgelerde kapalıdır).
    """
    box = parse_viewbox(attrs.get("viewBox"))
    width = parse_length(attrs.get("width"))
    height = parse_length(attrs.get("height"))

    def resolve(length: tuple[float, str] | None, full: float | None) -> float | None:
        if length is None:
            return None
        number, unit = length
        if unit == "%":
            return None if full is None else full * number / 100.0
        return number * _UNIT_PX[unit]

    w = resolve(width, box[2] if box else None)
    h = resolve(height, box[3] if box else None)
    if w is not None and w <= 0 or h is not None and h <= 0:
        return None
    if w is None and h is None:
        return (box[2], box[3]) if box and width is None and height is None else None
    if box:
        if w is None:
            w = h * box[2] / box[3]
        elif h is None:
            h = w * box[3] / box[2]
    if w is None or h is None:
        return None
    return w, h


def pixel_size(size: tuple[float, float]) -> tuple[int, int]:
    """resvg yuvarlaması (en yakın tam sayı, en az 1)."""
    return max(1, int(math.floor(size[0] + 0.5))), max(1, int(math.floor(size[1] + 0.5)))


# --------------------------------------------------------------------------- bölge belgesi
def _fmt(value: float) -> str:
    return f"{value:.9g}"


_DROPPED_ROOT_ATTRS = frozenset({"width", "height", "x", "y", "overflow"})


def _nested_root_tag(tag: str, name: str, size: tuple[float, float]) -> str:
    inner = tag[1 + len(name) :]

    def drop(match: re.Match[str]) -> str:
        return "" if match.group(1) in _DROPPED_ROOT_ATTRS else match.group(0)

    # overflow="visible": iç içe <svg> varsayılan olarak kırpma + ara katman ister (ölçüm: çizim süresi ~2 kat);
    # kamera bölgesi zaten boyut dikdörtgeninin içindedir, kırpma görüntüyü değiştirmez.
    return f'<{name} overflow="visible" width="{_fmt(size[0])}" height="{_fmt(size[1])}"{_ATTR_RE.sub(drop, inner)}'


def build_region_document(
    text: str,
    root: RootTag,
    size: tuple[float, float],
    box: tuple[float, float, float, float],
    px: tuple[int, int],
) -> str | None:
    """Görüntünün `box` (boyut dikdörtgeninin kesirleri) bölgesini `px` piksele çizen belge; kurulamazsa None."""
    sw, sh = size
    rx, ry, rw, rh = box[0] * sw, box[1] * sh, box[2] * sw, box[3] * sh
    if not all(math.isfinite(v) for v in (rx, ry, rw, rh)) or rw <= 0 or rh <= 0:
        return None
    if px[0] < 1 or px[1] < 1:
        return None
    close = text.rfind("</" + root.name, root.end)
    if close < 0 and not root.self_closing:
        return None
    if root.self_closing:
        tail_end = root.end
        body = ""
    else:
        gt = text.find(">", close)
        if gt < 0:
            return None
        tail_end = gt + 1
        body = text[root.end : tail_end]
    prolog = _XML_ENCODING_RE.sub(r'\1"UTF-8"', text[: root.start], count=1)
    nested = _nested_root_tag(text[root.start : root.end], root.name, size)
    camera = (
        f'<svg xmlns="{SVG_NS}" xmlns:xlink="{XLINK_NS}" width="{px[0]}" height="{px[1]}" '
        f'viewBox="{_fmt(rx)} {_fmt(ry)} {_fmt(rw)} {_fmt(rh)}" preserveAspectRatio="none">'
    )
    return f"{prolog}{camera}{nested}{body}</svg>{text[tail_end:]}"


# --------------------------------------------------------------------------- kaynak + çizim
@dataclass(frozen=True)
class SvgSource:
    text: str
    root: RootTag
    size: tuple[float, float]
    has_text: bool
    resources_dir: str


def load_source(path: Path) -> SvgSource | None:
    """Bölge çizimi için kaynak; okunamıyor/kök svg değil/boyut belirsizse None (tabandan başka bir şey gösterilmez)."""
    text = bytes_to_text(read_svg_bytes(Path(path)))
    root = find_root(text)
    if root is None:
        return None
    size = root_size(root.attrs)
    if size is None:
        return None
    return SvgSource(text, root, size, has_text_elements(text), str(Path(path).parent))


def apply_background(img, kind: str):
    """`light`: saydam alanı açık zemine oturt (RGB); `keep`: olduğu gibi (RGBA)."""
    from PIL import Image

    if kind == "light":
        base = Image.new("RGBA", img.size, LIGHT_BACKGROUND + (255,))
        base.alpha_composite(img)
        return base.convert("RGB")
    return img


def render_region_resvg(source: SvgSource, box: tuple[float, float, float, float], px: tuple[int, int], kind: str):
    """resvg ile bölgeyi çiz (PIL RGB/RGBA); resvg yok ya da belge kurulamazsa hata fırlatır."""
    from PIL import Image

    if resvg_py is None:
        raise RuntimeError("resvg-py yok")
    doc = build_region_document(source.text, source.root, source.size, box, px)
    if doc is None:
        raise ValueError("bölge belgesi kurulamadı")
    png = resvg_py.svg_to_bytes(
        svg_string=doc,
        resources_dir=source.resources_dir,
        dpi=RESVG_DPI,
        skip_system_fonts=not source.has_text,
    )
    img = Image.open(io.BytesIO(bytes(png)))
    img.load()
    img = img.convert("RGBA")
    if img.size != tuple(px):
        img = img.resize(tuple(px), Image.Resampling.BILINEAR)
    return apply_background(img, kind)
