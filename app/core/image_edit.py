"""Temel düzenleme: kayıpsız JPEG döndürme (EXIF yönü), döndürüp kaydetme, kırpma, benzersiz ad.

Widget yok. JPEG'de piksel verisine DOKUNULMAZ: yalnızca EXIF Orientation etiketi yazılır (kayıpsız).
"""

from __future__ import annotations

import os
import struct
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:  # numpy yalnızca kullanılınca (işlev içinde) yüklenir
    import numpy as np

JPEG_EXTS = {".jpg", ".jpeg", ".jpe", ".jfif"}
# Piksel yeniden kodlamasıyla (kayıpsız biçimler) döndürülebilenler.
REENCODE_EXTS = {".png", ".bmp", ".tif", ".tiff", ".webp"}
ROTATABLE_EXTS = JPEG_EXTS | REENCODE_EXTS

_ORIENTATION_TAG = 0x0112


def _np():
    """numpy yalnızca yön hesabında gerekir; modül açılışta (ana pencereyle) içe aktarılırken yüklenmesin."""
    import numpy

    return numpy


# EXIF Orientation -> piksel dönüşümü (PIL.ImageOps.exif_transpose ile aynı anlam).
_APPLY = {
    1: lambda a: a,
    2: lambda a: _np().fliplr(a),
    3: lambda a: _np().rot90(a, 2),
    4: lambda a: _np().flipud(a),
    5: lambda a: a.T,
    6: lambda a: _np().rot90(a, -1),
    7: lambda a: _np().rot90(a.T, 2),
    8: lambda a: _np().rot90(a, 1),
}


def _rot_cw(a: np.ndarray, degrees: int) -> np.ndarray:
    return _np().rot90(a, -((degrees // 90) % 4))


def combine_orientation(current: int, extra_cw_degrees: int) -> int:
    """Görüntü şu an `current` yönüyle gösteriliyor; üstüne saat yönünde `extra_cw_degrees` dönüş
    uygulanınca yazılması gereken yeni EXIF yönü (1..8)."""
    np = _np()
    current = current if current in _APPLY else 1
    base = np.arange(6).reshape(2, 3)
    target = _rot_cw(_APPLY[current](base), int(extra_cw_degrees) % 360)
    for value, fn in _APPLY.items():
        out = fn(base)
        if out.shape == target.shape and np.array_equal(out, target):
            return value
    return 1


# Yeniden kodlama yalnızca tek kareli, kanal başına 8 bit görüntülerde: aksi halde animasyon / sayfalar ilk kareye,
# 16 bit PNG/TIFF 8 bite iner ve özgün dosyanın üzerine yazılırdı.
_REENCODE_MODES = frozenset({"1", "L", "LA", "P", "PA", "RGB", "RGBA", "CMYK"})
UNSAFE_REENCODE = "animasyonlu, çok sayfalı ya da 8 bitten derin görüntü: döndürme kaydedilmedi (özgün dosya korundu)"


def _reencode_is_safe(im: Image.Image) -> bool:
    """Açılmış (henüz yüklenmemiş) görüntü kayıpsız yeniden kodlanabilir mi?"""
    if getattr(im, "n_frames", 1) > 1 or im.mode not in _REENCODE_MODES:
        return False
    for tile in im.tile:  # 16 bit RGB PNG/TIFF de "RGB" kipinde açılır; derinlik ham kipte görünür (RGB;16B)
        args = tile[3] if len(tile) > 3 else None
        raw = args[0] if isinstance(args, tuple) and args else args
        if isinstance(raw, str) and ("16" in raw or "32" in raw):
            return False
    return True


def can_rotate_save(path: Path) -> bool:
    """Döndürme dosyaya kaydedilebilir mi? JPEG: yalnızca EXIF yönü yazılır (her zaman güvenli). Diğerleri:
    dosya açılıp yeniden kodlamanın kayıpsız olacağı denetlenir."""
    ext = path.suffix.lower()
    if ext in JPEG_EXTS:
        return True
    if ext not in REENCODE_EXTS:
        return False
    try:
        with Image.open(path) as im:
            return _reencode_is_safe(im)
    except Exception:  # noqa: BLE001 — açılamayan dosya için seçenek sunulmaz
        return False


# --------------------------------------------------------------------------- JPEG (kayıpsız)

def _read_orientation_and_patch_offset(app1: bytes) -> tuple[int, int | None]:
    """APP1 Exif gövdesinden (b'Exif\\0\\0' sonrası TIFF) yön değeri ve değerin dosya içi ofseti.

    Dönüş: (yön, TIFF başlığına göre 2 baytlık değerin ofseti) — etiket yoksa (1, None).
    """
    tiff = app1
    if len(tiff) < 8:
        return 1, None
    endian = "<" if tiff[:2] == b"II" else ">" if tiff[:2] == b"MM" else None
    if endian is None:
        return 1, None
    ifd0 = struct.unpack(endian + "I", tiff[4:8])[0]
    if ifd0 + 2 > len(tiff):
        return 1, None
    count = struct.unpack(endian + "H", tiff[ifd0:ifd0 + 2])[0]
    for i in range(count):
        entry = ifd0 + 2 + i * 12
        if entry + 12 > len(tiff):
            break
        tag, typ, cnt = struct.unpack(endian + "HHI", tiff[entry:entry + 8])
        if tag == _ORIENTATION_TAG and typ == 3 and cnt == 1:
            value = struct.unpack(endian + "H", tiff[entry + 8:entry + 10])[0]
            return (value if 1 <= value <= 8 else 1), entry + 8
    return 1, None


def _segments(data: bytes):
    """(işaret, başlangıç, bitiş) — SOS'a kadar JPEG üst bilgi segmentleri."""
    pos = 2
    n = len(data)
    while pos + 4 <= n and data[pos] == 0xFF:
        marker = data[pos + 1]
        if marker == 0xDA or marker == 0xD9:  # SOS / EOI
            return
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        yield marker, pos, pos + 2 + length
        pos += 2 + length


def jpeg_orientation(path: Path) -> int:
    """Dosyadaki EXIF yönü (yoksa 1). Büyük dosyayı okumaz: yalnızca üst bilgi penceresi."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(256 * 1024)
    except OSError:
        return 1
    if head[:2] != b"\xff\xd8":
        return 1
    for marker, start, end in _segments(head):
        if marker == 0xE1 and head[start + 4:start + 10] == b"Exif\x00\x00":
            return _read_orientation_and_patch_offset(head[start + 10:end])[0]
    return 1


def _exif_with_orientation(orientation: int) -> bytes:
    exif = Image.Exif()
    exif[_ORIENTATION_TAG] = int(orientation)
    return exif.tobytes()  # b'Exif\0\0' + TIFF


def write_jpeg_orientation(src: Path, dst: Path, orientation: int) -> None:
    """Piksel verisini yeniden kodlamadan EXIF yönünü `orientation` yap. `dst == src` olabilir (atomik)."""
    if not 1 <= orientation <= 8:
        raise ValueError("geçersiz EXIF yönü")
    data = Path(src).read_bytes()
    if data[:2] != b"\xff\xd8":
        raise ValueError("JPEG değil")

    out: bytes | None = None
    for marker, start, end in _segments(data):
        if marker == 0xE1 and data[start + 4:start + 10] == b"Exif\x00\x00":
            tiff = data[start + 10:end]
            _value, off = _read_orientation_and_patch_offset(tiff)
            if off is not None:
                endian = "<" if tiff[:2] == b"II" else ">"
                patched = bytearray(data)
                struct.pack_into(endian + "H", patched, start + 10 + off, orientation)
                out = bytes(patched)
            break
    if out is None:
        # Etiket yok: yalnızca Orientation içeren yeni APP1'i SOI/JFIF'ten sonra ekle (diğer EXIF korunur
        # çünkü mevcut APP1 yoksa kaybedilecek bir şey yok; varsa aşağıda birleştirilir).
        existing = None
        for marker, start, end in _segments(data):
            if marker == 0xE1 and data[start + 4:start + 10] == b"Exif\x00\x00":
                existing = (start, end)
                break
        if existing is not None:
            exif = Image.Exif()
            exif.load(data[existing[0] + 4:existing[1]])
            exif[_ORIENTATION_TAG] = int(orientation)
            body = exif.tobytes()
            seg = b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
            out = data[:existing[0]] + seg + data[existing[1]:]
        else:
            body = _exif_with_orientation(orientation)
            seg = b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
            insert_at = 2
            for marker, start, end in _segments(data):
                if marker == 0xE0:  # JFIF'ten sonra
                    insert_at = end
                break
            out = data[:insert_at] + seg + data[insert_at:]
    _atomic_write(Path(dst), out)


def _atomic_write(dst: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- yeniden kodlama

# TIFF düzen etiketleri (boyut, şerit ofsetleri, sıkıştırma…): yeni dosyada yazıcı belirler, kaynaktan taşınmaz.
_LAYOUT_TAGS = frozenset(
    {254, 255, 256, 257, 258, 259, 262, 266, 273, 277, 278, 279, 284, 317, 320, 322, 323, 324, 325, 338, 339, 530, 532}
)
_XMP_TAG = 700
_SUB_IFD_TAGS = (0x8769, 0x8825, 0xA005)  # Exif / GPS / Interop alt dizinleri


def _kept_metadata(source: Image.Image, frame: Image.Image) -> dict:
    """Yeniden kodlamada korunacak üst bilgi: EXIF (çekim tarihi, GPS…), ICC profili ve XMP. Kaynak açıkken çağrılır.

    EXIF kaynaktan okunur (TIFF'te dosya etiketlerinde durur); yön etiketi atılır, pikseller artık dik. ICC/XMP
    döndürülmüş kareden alınır (Pillow XMP'deki yönü de sıfırlar)."""
    exif = source.getexif()
    for tag in [_ORIENTATION_TAG, *(k for k in exif if k in _LAYOUT_TAGS)]:
        exif.pop(tag, None)
    for ifd in _SUB_IFD_TAGS:  # alt dizinler dosya kapanmadan okunsun (kayıtta dosyadan okunamazdı)
        if ifd in exif:
            exif.get_ifd(ifd)
    meta: dict = {"exif": exif} if len(exif) else {}
    for key in ("icc_profile", "xmp"):
        if frame.info.get(key):
            meta[key] = frame.info[key]
    return meta


def _save_pil(img: Image.Image, dest: Path, exif: Image.Exif | None = None, meta: dict | None = None) -> None:
    """Uzantıya göre kaydet (JPEG yüksek kalite; PNG/WebP/TIFF/BMP kayıpsız). Atomik.

    `meta`: PNG/WebP/TIFF'e yazılacak exif / icc_profile / xmp (bkz. `_kept_metadata`)."""
    ext = dest.suffix.lower()
    meta = dict(meta or {})
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), suffix=dest.suffix)
    os.close(fd)
    try:
        if ext in JPEG_EXTS:
            rgb = img.convert("RGB") if img.mode != "RGB" else img
            kwargs: dict = {"quality": 95, "optimize": True, "subsampling": 0}
            if exif is not None:
                kwargs["exif"] = exif
            rgb.save(tmp, format="JPEG", **kwargs)
        elif ext == ".webp":
            img.save(tmp, format="WEBP", lossless=True, quality=100, method=4, **meta)
        elif ext in (".tif", ".tiff"):
            xmp = meta.pop("xmp", None)
            if xmp:  # TIFF yazıcısı `xmp` almaz: XMP kendi etiketinde (700)
                meta.setdefault("exif", Image.Exif())[_XMP_TAG] = xmp
            # Pillow'un libtiff (LZW) yazıcısı Exif/GPS alt dizinlerini yazamaz: varsa sıkıştırmasız (yine kayıpsız).
            sub_ifds = any(k in meta.get("exif", {}) for k in _SUB_IFD_TAGS)
            img.save(tmp, format="TIFF", compression="raw" if sub_ifds else "tiff_lzw", **meta)
        elif ext == ".bmp":
            (img.convert("RGB") if img.mode == "RGBA" else img).save(tmp, format="BMP")
        else:
            xmp = meta.pop("xmp", None)
            if xmp:  # PNG yazıcısı `xmp` almaz: XMP, iTXt "XML:com.adobe.xmp" yığınında durur
                from PIL import PngImagePlugin

                info = PngImagePlugin.PngInfo()
                info.add_itxt("XML:com.adobe.xmp", xmp)
                meta["pnginfo"] = info
            img.save(tmp, format="PNG", optimize=True, **meta)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def rotate_and_save(path: Path, extra_cw_degrees: int) -> Path:
    """Görünen döndürmeyi dosyaya kalıcı yaz (aynı dosya). JPEG kayıpsız; diğerleri kayıpsız yeniden kodlama."""
    path = Path(path)
    deg = int(extra_cw_degrees) % 360
    if deg == 0:
        return path
    ext = path.suffix.lower()
    if ext in JPEG_EXTS:
        current = jpeg_orientation(path)
        write_jpeg_orientation(path, path, combine_orientation(current, deg))
        return path
    if ext in REENCODE_EXTS:
        from PIL import ImageOps

        with Image.open(path) as im:
            if not _reencode_is_safe(im):  # özgün dosyanın üzerine kayıplı bir kopya yazılmasın
                raise ValueError(UNSAFE_REENCODE)
            im.load()
            frame = ImageOps.exif_transpose(im)  # dosyanın kendisinden: TIFF'te yön etiketi kopyada kaybolurdu
            frame = frame.rotate(-deg, expand=True)  # PIL: pozitif = saat yönünün tersi
            meta = _kept_metadata(im, frame)
        _save_pil(frame, path, meta=meta)
        return path
    raise ValueError(f"{ext} için döndürmeyi kaydetme desteklenmiyor")


# --------------------------------------------------------------------------- kırpma

def unique_path(folder: Path, stem: str, suffix: str) -> Path:
    candidate = folder / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = folder / f"{stem} {n}{suffix}"
        n += 1
    return candidate


def crop_box(width: int, height: int, frac: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """(sol, üst, sağ, alt) oranlarından piksel kutusu; sınırlara sıkıştırılır, en az 1x1."""
    l, t, r, b = frac
    left = min(max(int(round(l * width)), 0), width - 1)
    top = min(max(int(round(t * height)), 0), height - 1)
    right = min(max(int(round(r * width)), left + 1), width)
    bottom = min(max(int(round(b * height)), top + 1), height)
    return left, top, right, bottom


def crop_and_save(
    source: Path,
    frac: tuple[float, float, float, float],
    view_rotation_cw: int,
    dest: Path,
    image: Image.Image | None = None,
) -> Path:
    """Görünen (döndürülmüş) görüntüyü `frac` oranlarıyla kırpıp `dest`e kaydet.

    `image`: yüklü, gösterim yönünde PIL görüntü (verilmezse `load_pil` ile okunur).
    JPEG'de EXIF korunur (yön 1'e çekilir, küçük resim atılır).
    """
    from app.core.image_loader import load_pil

    img = image if image is not None else load_pil(Path(source))
    deg = int(view_rotation_cw) % 360
    if deg:
        img = img.rotate(-deg, expand=True)
    box = crop_box(img.width, img.height, frac)
    cropped = img.crop(box)

    exif = None
    if dest.suffix.lower() in JPEG_EXTS and Path(source).suffix.lower() in JPEG_EXTS:
        try:
            with Image.open(source) as src:
                exif = src.getexif()
            exif[_ORIENTATION_TAG] = 1
            exif.pop(0x0201, None)  # küçük resim ofsetleri artık geçersiz
            exif.pop(0x0202, None)
        except Exception:  # noqa: BLE001 — EXIF taşınamazsa görüntü yine kaydedilir
            exif = None
    _save_pil(cropped, dest, exif)
    return dest


def output_suffix(source: Path, has_alpha: bool) -> str:
    """Kırpılmış kopya için uzantı: yazılabilir biçimlerde aynısı; diğerlerinde (HEIC, RAW, JXL…) JPEG,
    saydamlık varsa PNG."""
    ext = Path(source).suffix.lower()
    if ext in ROTATABLE_EXTS:
        return ext
    return ".png" if has_alpha else ".jpg"
