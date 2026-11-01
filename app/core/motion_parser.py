"""Gömülü Motion Photo (Google / Samsung) MP4 ayrıştırıcı.

HEIC/JPEG ISO-BMFF konteyneri zaten `ftyp` ile başlar. Gömülü video
dosyanın *sonuna* eklenir; ilk `ftyp` asla video değildir.
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path

FTYP = b"ftyp"
_IMAGE_BRANDS = {
    b"heic",
    b"heix",
    b"hevc",
    b"hevx",
    b"mif1",
    b"msf1",
    b"miaf",
    b"avif",
    b"avis",
    b"jpeg",
    b"jp2 ",
}
_VIDEO_BRANDS = {
    b"isom",
    b"iso2",
    b"iso4",
    b"iso5",
    b"iso6",
    b"mp41",
    b"mp42",
    b"mp71",
    b"M4V ",
    b"M4A ",
    b"avc1",
    b"qt  ",
    b"dash",
    b"msdh",
    b"mmp4",
}

_XMP_OFFSET_RE = re.compile(
    rb"(?:MicroVideoOffset|MotionPhotoVideoOffset)[^0-9]{0,24}(\d{2,12})"
)

_SEARCH_TAIL = 24 * 1024 * 1024
_XMP_HEAD = 512 * 1024
_MIN_EMBED_OFFSET = 128


class TempVideoStore:
    """Oynatıcı serbest bırakıldıktan sonra silinecek geçici dosyalar/dizinler.

    Arka plan işçileri (çözümleme) `add`, GUI `clear` çağırır; bu yüzden kilitlidir.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paths: list[str] = []
        self._dirs: list[str] = []

    def add(self, path: str) -> str:
        with self._lock:
            self._paths.append(path)
        return path

    def add_dir(self, path: str) -> str:
        """Bizim oluşturduğumuz (içindekiler silinince kaldırılacak) geçici dizin."""
        with self._lock:
            self._dirs.append(path)
        return path

    def clear(self) -> None:
        with self._lock:
            paths, self._paths = self._paths, []
            dirs, self._dirs = self._dirs, []
        remaining: list[str] = []
        for path in paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError:
                remaining.append(path)  # açık (kilitli) dosya: sonraki clear'da tekrar dene
        remaining_dirs: list[str] = []
        for folder in dirs:
            try:
                os.rmdir(folder)
            except FileNotFoundError:
                pass
            except OSError:
                remaining_dirs.append(folder)
        if remaining or remaining_dirs:
            with self._lock:
                self._paths = remaining + self._paths
                self._dirs = remaining_dirs + self._dirs

    def paths(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._paths)

    def dirs(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._dirs)


_STALE_FILE_PREFIXES = ("lumina_",)
_STALE_DIR_PREFIXES = ("lumina_livp_", "lumina-undo-")


def sweep_stale_temps(max_age_seconds: float = 24 * 3600, temp_dir: str | None = None) -> int:
    """Çökme/zorla kapanma artığı eski geçici dosya ve dizinleri sil. Silinen sayısını döndürür.

    Yalnızca `max_age_seconds`'tan eski olanlar silinir; başka bir Lumina penceresinin
    kullandığı güncel dosyalara dokunulmaz.
    """
    import shutil
    import time

    base = temp_dir or tempfile.gettempdir()
    cutoff = time.time() - max_age_seconds
    removed = 0
    try:
        entries = list(os.scandir(base))
    except OSError:
        return 0
    for entry in entries:
        name = entry.name
        try:
            if entry.is_dir(follow_symlinks=False) and name.startswith(_STALE_DIR_PREFIXES):
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry.path, ignore_errors=True)
                    removed += 0 if os.path.exists(entry.path) else 1
            elif (
                entry.is_file(follow_symlinks=False)
                and name.startswith(_STALE_FILE_PREFIXES)
                and name.lower().endswith(".mp4")
                and entry.stat().st_mtime < cutoff
            ):
                os.remove(entry.path)
                removed += 1
        except OSError:
            continue
    return removed


def _box_size(size_bytes: bytes) -> int:
    return int.from_bytes(size_bytes, "big")


def _looks_like_video_brand(brand: bytes) -> bool:
    if brand in _IMAGE_BRANDS:
        return False
    if brand in _VIDEO_BRANDS:
        return True
    # Bilinmeyen marka: gömülü ofset dosya başı değilse video kabul et
    return True


def find_xmp_video_offset(path: Path) -> int | None:
    """Google MicroVideoOffset: dosya sonundan geriye bayt sayısı."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            head = fh.read(min(size, _XMP_HEAD))
    except OSError:
        return None
    match = _XMP_OFFSET_RE.search(head)
    if not match:
        return None
    from_end = int(match.group(1))
    if from_end < 16 or from_end >= size:
        return None
    offset = size - from_end
    return offset if offset >= _MIN_EMBED_OFFSET else None


def find_embedded_video_offset(path: Path) -> int | None:
    """Gömülü MP4'ün dosya içi başlangıç ofseti veya None."""
    xmp = find_xmp_video_offset(path)
    if xmp is not None:
        return xmp

    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size < 64:
        return None

    window = min(size, _SEARCH_TAIL)
    try:
        with path.open("rb") as fh:
            fh.seek(size - window)
            data = fh.read(window)
    except OSError:
        return None

    idx = data.rfind(FTYP)
    while idx >= 4:
        file_offset = (size - window) + idx - 4
        brand = data[idx + 4 : idx + 8]
        box = _box_size(data[idx - 4 : idx])
        remaining = size - file_offset
        valid_size = box == 0 or box == 1 or 8 <= box <= remaining + 8
        if (
            file_offset >= _MIN_EMBED_OFFSET
            and valid_size
            and _looks_like_video_brand(brand)
        ):
            return file_offset
        idx = data.rfind(FTYP, 0, idx)

    return None


_VERIFY_SEARCH = 64  # ofsetten sonra ftyp için taranacak bayt
_MAX_FTYP_BOX = 4096


def verify_mp4_at(path: Path, offset: int, search: int = _VERIFY_SEARCH) -> int | None:
    """`offset`te (veya hemen ardındaki `search` bayt içinde) gerçek bir MP4 `ftyp` kutusu var mı?

    Evet: gerçek başlangıç ofsetini döndürür. Hayır: None. Metadata'dan gelen ofset her zaman
    video göstermez — örn. Ultra HDR JPEG'lerde son `Item:Length` GainMap JPEG'in uzunluğudur;
    bu doğrulama olmadan sahte "LIVE" rozeti çıkıyor ve oynatma başarısız oluyordu.
    """
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, int(offset)))
            head = fh.read(search + 16)
    except OSError:
        return None
    idx = head.find(FTYP)
    while idx >= 0:
        if idx >= 4:
            box = _box_size(head[idx - 4 : idx])
            brand = head[idx + 4 : idx + 8]
            if 8 <= box <= _MAX_FTYP_BOX and len(brand) == 4 and _looks_like_video_brand(brand):
                return int(offset) + idx - 4
        idx = head.find(FTYP, idx + 1)
    return None


def extract_embedded_video(
    path: Path, offset: int, store: TempVideoStore | None = None
) -> str | None:
    """Ofsetten itibaren MP4'ü geçici dosyaya akıtır. RAM'e tam kopya yok.

    Yazım sırasında hata olursa yarım dosya silinir (eskiden sızıp takip edilmiyordu).
    """
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(prefix="lumina_", suffix=".mp4", delete=False)
        # Silinebilmesi için hemen kaydet: yazım ortasında hata olsa da store temizler.
        if store is not None:
            store.add(tmp.name)
        with path.open("rb") as src, tmp:
            src.seek(offset)
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                tmp.write(chunk)
        return tmp.name
    except OSError:
        if tmp is not None:
            try:
                tmp.close()
            except OSError:
                pass
            try:
                os.remove(tmp.name)
            except OSError:
                pass
        return None
