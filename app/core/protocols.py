"""Canlı fotoğraf protokol ofsetleri — Samsung, Windows/Motion V2, Google V1, HEIC mpvd."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from app.core.motion_parser import (
    TempVideoStore,
    extract_embedded_video,
    find_embedded_video_offset,
    verify_mp4_at,
)

_XMP_WINDOW = 1024 * 1024
_TAIL_WINDOW = 4 * 1024 * 1024

_SAMSUNG_NAME = b"MotionPhoto_Data"
_SEFT = b"SEFT"
_SEFH = b"SEFH"
_MPVD = b"mpvd"

_ITEM_SEMANTIC = re.compile(rb'Item:Semantic="([^"]+)"')
_ITEM_LENGTH = re.compile(rb'Item:Length="(\d+)"')
_VIDEO_LENGTH = re.compile(
    rb"(?:VideoLength|MotionPhotoVideoOffset|MicroVideoOffset)[^0-9]{0,24}(\d{2,12})"
)
_MOTION_FLAG = re.compile(rb"(?:GCamera:MotionPhoto|Camera:MotionPhoto|OLivePhotoVersion)[^<]{0,8}1")


def _read_head(path: Path, n: int = _XMP_WINDOW) -> bytes:
    try:
        with path.open("rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


def _read_tail(path: Path, n: int = _TAIL_WINDOW) -> tuple[bytes, int]:
    try:
        size = path.stat().st_size
        window = min(size, n)
        with path.open("rb") as fh:
            fh.seek(size - window)
            return fh.read(window), size - window
    except OSError:
        return b"", 0


def find_samsung_offset(path: Path) -> int | None:
    """SEFT / MotionPhoto_Data — video işaretçiden hemen sonra başlar."""
    tail, base = _read_tail(path)
    if not tail:
        return None
    name_idx = tail.rfind(_SAMSUNG_NAME)
    if name_idx >= 0:
        offset = base + name_idx + len(_SAMSUNG_NAME)
        if offset + 16 < path.stat().st_size:
            return offset

    seft = tail.rfind(_SEFT)
    if seft >= 8:
        sef_size = int.from_bytes(tail[seft - 4 : seft], "little")
        sefh_rel = seft - 4 - sef_size - 4
        if 0 <= sefh_rel < seft and tail[sefh_rel : sefh_rel + 4] == _SEFH:
            # Alan tablosunda 0x300A (MotionPhoto_Data) ara
            count = int.from_bytes(tail[sefh_rel + 8 : sefh_rel + 12], "little")
            cursor = sefh_rel + 12
            for _ in range(min(count, 32)):
                if cursor + 12 > seft:
                    break
                marker = int.from_bytes(tail[cursor + 2 : cursor + 4], "little")
                field_off = int.from_bytes(tail[cursor + 4 : cursor + 8], "little")
                cursor += 12
                if marker == 0x0A30 or marker == 0x300A:
                    # field_off: SEF başına göre video konumu (ters yönde)
                    video_at = (base + sefh_rel) - field_off
                    if video_at > 0:
                        return video_at
    return None


def find_motion_v2_offset(path: Path) -> int | None:
    """Google/Windows/OPPO Container:Directory — ofset = dosya_sonu - Item:Length."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    head = _read_head(path)
    semantics = _ITEM_SEMANTIC.findall(head)
    lengths = [int(x) for x in _ITEM_LENGTH.findall(head)]
    video_len = None
    if semantics and lengths and len(semantics) == len(lengths):
        for sem, ln in zip(semantics, lengths, strict=False):
            if sem in (b"MotionPhoto", b"Motion", b"Video") and ln > 64:
                video_len = ln
                break
        if video_len is None:
            extra = [ln for ln in lengths if ln > 64]
            if extra:
                video_len = extra[-1]
    if video_len is None:
        match = _VIDEO_LENGTH.search(head)
        if match:
            video_len = int(match.group(1))
    if video_len is None or video_len < 64 or video_len >= size:
        return None
    offset = size - video_len
    return offset if offset >= 128 else None


def find_mpvd_offset(path: Path) -> int | None:
    """HEIC Motion Photo Video Data kutusu (payload, 8 bayt başlıktan sonra)."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            data = fh.read(min(size, 8 * 1024 * 1024))
    except OSError:
        return None
    idx = 0
    while True:
        found = data.find(_MPVD, idx)
        if found < 4:
            return None
        if found >= 64:
            box_size = int.from_bytes(data[found - 4 : found], "big")
            remaining = size - (found - 4)
            if 16 <= box_size <= remaining + 8 or box_size == 0:
                return found + 4
        idx = found + 4


def find_protocol_offset(path: Path) -> tuple[int, str] | None:
    """Öncelik: Samsung → Motion Photo V2 (Windows) → mpvd → Micro/V1/ftyp.

    Her aday GERÇEK bir MP4 `ftyp` kutusuyla doğrulanır; doğrulanamayan aday (örn. Ultra HDR
    GainMap JPEG'i) atlanır ve bir sonraki yönteme geçilir. Dönen ofset düzeltilmiş (gerçek
    başlangıç) ofsettir.
    """
    for finder, source in (
        (find_samsung_offset, "samsung"),
        (find_motion_v2_offset, "motion_v2"),
        (find_mpvd_offset, "mpvd"),
    ):
        offset = finder(path)
        if offset is None:
            continue
        real = verify_mp4_at(path, offset)
        if real is not None:
            return real, source
    generic = find_embedded_video_offset(path)
    if generic is not None:
        real = verify_mp4_at(path, generic)
        if real is not None:
            src = "micro_v1" if _MOTION_FLAG.search(_read_head(path, 256 * 1024)) else "embedded"
            return real, src
    return None


_LIVP_STILL_EXT = {".heic", ".heif", ".jpg", ".jpeg", ".png"}
_LIVP_VIDEO_EXT = {".mov", ".mp4", ".m4v"}


def _stream_member(zf: zipfile.ZipFile, member: str, dest: Path) -> None:
    """ZIP üyesini RAM'e okumadan diske akıt (tam dosya RAM'e okunmaz)."""
    with zf.open(member) as src, dest.open("wb") as out:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)


def extract_livp(
    path: Path, store: TempVideoStore | None = None
) -> tuple[Path, str] | None:
    """Apple/OEM .livp (ZIP) → (still_path, video_path).

    Geçici dizin `store` aracılığıyla temizlenir; çıkarma yarıda kalırsa her şey silinir.
    """
    import shutil
    import tempfile

    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            still = next((n for n in names if Path(n).suffix.lower() in _LIVP_STILL_EXT), None)
            video = next((n for n in names if Path(n).suffix.lower() in _LIVP_VIDEO_EXT), None)
            if not still or not video:
                return None
            tmpdir = Path(tempfile.mkdtemp(prefix="lumina_livp_"))
            still_out = tmpdir / Path(still).name
            video_out = tmpdir / Path(video).name
            if store is not None:
                store.add_dir(str(tmpdir))
                store.add(str(still_out))
                store.add(str(video_out))
            try:
                _stream_member(zf, still, still_out)
                _stream_member(zf, video, video_out)
            except (OSError, zipfile.BadZipFile, RuntimeError):
                shutil.rmtree(tmpdir, ignore_errors=True)
                return None
            return still_out, str(video_out)
    except (OSError, zipfile.BadZipFile):
        return None


def extract_at(path: Path, offset: int, store: TempVideoStore | None) -> str | None:
    return extract_embedded_video(path, offset, store)
