"""Apple ContentIdentifier (GUID) okuyucu.

HEIC: EXIF/XMP veya `apple_desktop:ContentIdentifier`
MOV:  QuickTime `com.apple.quicktime.content.identifier`

Dosyanın tamamı RAM'e alınmaz; baş + son pencereler okunur.
"""

from __future__ import annotations

import re
from pathlib import Path

UUID_RE = re.compile(
    rb"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
)

# Apple meta anahtarları — rastgele GUID'lerden ayırt etmek için
_ANCHORS = (
    b"ContentIdentifier",
    b"content.identifier",
    b"apple_desktop",
    b"com.apple.quicktime.content.identifier",
)

_HEAD = 2 * 1024 * 1024
_TAIL = 2 * 1024 * 1024


def _read_head_tail(path: Path, head: int = _HEAD, tail: int = _TAIL) -> bytes:
    size = path.stat().st_size
    if size <= head + tail:
        return path.read_bytes()
    with path.open("rb") as fh:
        prefix = fh.read(head)
        fh.seek(size - tail)
        suffix = fh.read(tail)
    return prefix + suffix


def _uuid_near_anchor(blob: bytes) -> str | None:
    for anchor in _ANCHORS:
        start = 0
        while True:
            idx = blob.find(anchor, start)
            if idx < 0:
                break
            window = blob[max(0, idx - 32) : idx + 160]
            match = UUID_RE.search(window)
            if match:
                return match.group().decode("ascii").lower()
            start = idx + len(anchor)
    return None


def extract_content_identifier(path: Path) -> str | None:
    """Dosyadan Apple ContentIdentifier GUID döndürür (küçük harf)."""
    try:
        blob = _read_head_tail(path)
    except OSError:
        return None
    anchored = _uuid_near_anchor(blob)
    if anchored:
        return anchored
    match = UUID_RE.search(blob)
    return match.group().decode("ascii").lower() if match else None
