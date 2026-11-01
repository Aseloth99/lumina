"""Videoda 'kaldığın yerden devam': dosya başına son konum — Qt yok, test edilebilir.

Yalnızca uzun videolar için tutulur (kısa klip / Canlı Fotoğraf başa sarılır). Konum başlangıca
çok yakınsa veya video neredeyse bittiyse kayıt silinir; böylece "devam" yalnızca anlamlıyken çıkar.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path

_log = logging.getLogger(__name__)

MIN_DURATION_MS = 3 * 60 * 1000  # 3 dk'dan kısa videolar için devam yok
MIN_POSITION_MS = 10 * 1000  # ilk 10 sn: baştan izlemiş sayılır
END_MARGIN_MS = 15 * 1000  # son 15 sn: bitirmiş sayılır
MAX_ENTRIES = 300


def file_key(path: str | Path) -> str | None:
    """Yol + boyut: dosya değişince (üzerine yazıldı) eski konum geçersiz olur."""
    try:
        p = Path(path)
        return f"{os.path.normcase(str(p.resolve()))}|{p.stat().st_size}"
    except OSError:
        return None


def should_resume(pos_ms: int, dur_ms: int) -> bool:
    return (
        dur_ms >= MIN_DURATION_MS
        and pos_ms >= MIN_POSITION_MS
        and pos_ms <= dur_ms - END_MARGIN_MS
    )


class ResumeStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {k: v for k, v in raw.items() if isinstance(v, dict) and "pos" in v}
        except FileNotFoundError:
            self._data = {}  # ilk çalışma: dosya henüz yok, hata değil
        except (OSError, ValueError):
            self._data = {}  # okunamayan/bozuk dosya

    def get(self, media: str | Path) -> int | None:
        key = file_key(media)
        entry = self._data.get(key) if key else None
        if not entry:
            return None
        try:
            pos, dur = int(entry["pos"]), int(entry.get("dur", 0))
        except (TypeError, ValueError):
            return None
        return pos if should_resume(pos, dur) else None

    def update(self, media: str | Path, pos_ms: int, dur_ms: int) -> bool:
        """Konumu kaydet (anlamlıysa) ya da sil. Değişiklik olduysa True."""
        key = file_key(media)
        if not key:
            return False
        if should_resume(int(pos_ms), int(dur_ms)):
            old = self._data.get(key)
            if old and abs(int(old.get("pos", 0)) - int(pos_ms)) < 1000:
                return False
            self._data[key] = {"pos": int(pos_ms), "dur": int(dur_ms), "ts": int(time.time())}
            self._trim()
            self._save()
            return True
        if key in self._data:
            del self._data[key]
            self._save()
            return True
        return False

    def clear(self, media: str | Path) -> None:
        key = file_key(media)
        if key and key in self._data:
            del self._data[key]
            self._save()

    def _trim(self) -> None:
        if len(self._data) <= MAX_ENTRIES:
            return
        oldest = sorted(self._data, key=lambda k: self._data[k].get("ts", 0))
        for k in oldest[: len(self._data) - MAX_ENTRIES]:
            del self._data[k]

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._data, fh)
                os.replace(tmp, self._path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError:
            _log.warning("devam konumu kaydedilemedi", exc_info=True)  # konum kaybolabilir; uygulama etkilenmesin
