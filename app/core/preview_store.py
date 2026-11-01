"""Seek önizleme kareleri için bellek içi depo — Qt'siz, tek dosyaya özel.

Kimlik = (yol, boyut, değişiklik zamanı ns). Kimlik değişince depo boşalır; farklı
kimlikle gelen (bayat) kareler yok sayılır. Böylece bir önceki videonun karesi yeni
videoda ya da dosya değişince eski içerik görünmez.
"""

from __future__ import annotations

import bisect
import os
from typing import Any

Identity = tuple[str, int, int]


def identity_for(path: str | os.PathLike) -> Identity | None:
    """Dosya kimliği; dosya yoksa/okunamıyorsa None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (os.path.normcase(os.fspath(path)), int(st.st_size), int(st.st_mtime_ns))


class PreviewStore:
    def __init__(self, max_frames: int = 160, min_gap_ms: int = 80) -> None:
        self._max = max(4, int(max_frames))
        self._min_gap = max(1, int(min_gap_ms))
        self._identity: Identity | None = None
        self._times: list[int] = []
        self._images: list[Any] = []

    @property
    def identity(self) -> Identity | None:
        return self._identity

    def __len__(self) -> int:
        return len(self._times)

    def times(self) -> list[int]:
        return list(self._times)

    def clear(self) -> None:
        self._identity = None
        self._times.clear()
        self._images.clear()

    def reset(self, identity: Identity | None) -> bool:
        """Kimlik değiştiyse depoyu boşalt ve True döndür."""
        if identity == self._identity:
            return False
        self._times.clear()
        self._images.clear()
        self._identity = identity
        return True

    def add(self, identity: Identity | None, ms: int, image: Any) -> bool:
        """Kare ekle. Kimlik uyuşmuyorsa (bayat sonuç) eklenmez ve False döner."""
        if identity is None or identity != self._identity:
            return False
        ms = max(0, int(ms))
        i = bisect.bisect_left(self._times, ms)
        # Çok yakın mevcut kare varsa yenisiyle değiştir (taze çözüm kazanır).
        for j in (i - 1, i):
            if 0 <= j < len(self._times) and abs(self._times[j] - ms) < self._min_gap:
                self._times[j] = ms
                self._images[j] = image
                return True
        self._times.insert(i, ms)
        self._images.insert(i, image)
        if len(self._times) > self._max:
            self._drop_most_redundant()
        return True

    def _drop_most_redundant(self) -> None:
        """Komşusuna en yakın kareyi at (uçlar korunur): dağılım düzgün kalır."""
        n = len(self._times)
        if n <= 2:
            return
        best_i, best_gap = 1, None
        for i in range(1, n - 1):
            gap = self._times[i + 1] - self._times[i - 1]
            if best_gap is None or gap < best_gap:
                best_i, best_gap = i, gap
        del self._times[best_i]
        del self._images[best_i]

    def nearest(self, ms: int) -> tuple[int, Any] | None:
        """İstenen zamana en yakın kare (anlık/kaba gösterim için)."""
        if not self._times:
            return None
        ms = int(ms)
        i = bisect.bisect_left(self._times, ms)
        candidates = [j for j in (i - 1, i) if 0 <= j < len(self._times)]
        j = min(candidates, key=lambda k: abs(self._times[k] - ms))
        return self._times[j], self._images[j]

    def within(self, ms: int, tolerance_ms: int) -> tuple[int, Any] | None:
        """Yalnızca tolerans içindeki kare (kesin kare sayılabilecek olan)."""
        hit = self.nearest(ms)
        if hit is None or abs(hit[0] - int(ms)) > int(tolerance_ms):
            return None
        return hit
