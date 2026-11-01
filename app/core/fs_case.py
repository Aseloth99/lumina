"""Büyük/küçük harfe duyarlı dosya sistemlerinde (Linux, bazı macOS birimleri) harf-duyarsız arama.

Windows'ta `IMG_1.MOV` ile `img_1.mov` aynı dosyadır; Linux'ta farklıdır. Kullanıcıların
fotoğraf/video çiftleri çoğu zaman farklı harf büyüklüğüyle gelir (`IMG_1.HEIC` + `IMG_1.mov`).
Klasör listesi kısa süreli önbelleğe alınır (klasör değişim zamanı + TTL), böylece gezinirken
her fotoğraf için yeniden tarama yapılmaz.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

_TTL_SECONDS = 5.0
_MAX_DIRS = 8

_lock = threading.Lock()
# klasör -> (dizin mtime_ns, zaman, {küçük harf ad: gerçek ad})
_listings: OrderedDict[str, tuple[int, float, dict[str, str]]] = OrderedDict()


def _listing(folder: Path) -> dict[str, str]:
    key = os.fspath(folder)
    try:
        mtime = os.stat(key).st_mtime_ns
    except OSError:
        return {}
    now = time.monotonic()
    with _lock:
        cached = _listings.get(key)
        if cached and cached[0] == mtime and now - cached[1] < _TTL_SECONDS:
            _listings.move_to_end(key)
            return cached[2]
    names: dict[str, str] = {}
    try:
        with os.scandir(key) as entries:
            for entry in entries:
                # Aynı ada harf farkıyla birden çok dosya varsa ilkini (sıralı) tut: deterministik
                names.setdefault(entry.name.lower(), entry.name)
    except OSError:
        return {}
    with _lock:
        _listings[key] = (mtime, now, names)
        _listings.move_to_end(key)
        while len(_listings) > _MAX_DIRS:
            _listings.popitem(last=False)
    return names


def invalidate(folder: Path | str | None = None) -> None:
    """Önbelleği temizle (dosya yeniden adlandırdıktan sonra)."""
    with _lock:
        if folder is None:
            _listings.clear()
        else:
            _listings.pop(os.fspath(folder), None)


def find_ci(folder: Path | str, name: str) -> Path | None:
    """`folder` içinde `name` ile (harf-duyarsız) eşleşen mevcut dosyanın yolu; yoksa None."""
    folder = Path(folder)
    exact = folder / name
    if exact.exists():
        return exact
    real = _listing(folder).get(name.lower())
    if real is None:
        return None
    found = folder / real
    return found if found.exists() else None


def exists_ci(folder: Path | str, name: str) -> bool:
    """Ad (harf farkıyla bile olsa) klasörde dolu mu? Yeniden adlandırma çakışma denetimi için."""
    return find_ci(folder, name) is not None


def existing_file(candidate: Path) -> Path | None:
    """Aday yol bir dosyaysa onu, değilse harf-duyarsız eşleşen gerçek dosyayı döndür."""
    candidate = Path(candidate)
    if candidate.is_file():
        return candidate
    found = find_ci(candidate.parent, candidate.name)
    return found if found is not None and found.is_file() else None
