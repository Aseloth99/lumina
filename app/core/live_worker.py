"""Live/Motion Photo eşleştirmesini arka planda çöz (GUI thread'ini bloklama).

`resolve_live_video` dosyadan 1–24 MiB'lik pencereler okur ve gömülü videoyu geçici dosyaya
çıkarır; HDD/USB/ağ diskinde bu GUI'yi saniyelerce dondurabilirdi (GUI iş parçacığı
büyük dosya çözümüyle bloklanmamalı).
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from app.core.matcher import resolve_live_video
from app.core.motion_parser import TempVideoStore
from app.core.qt_safe import safe_emit


class _ResolveSignals(QObject):
    # token, fotoğraf yolu, LiveMatch | None, seek (UUID aramasına devam edilsin mi)
    resolved = pyqtSignal(int, str, object, bool)


class LiveResolveWorker(QRunnable):
    def __init__(
        self,
        path: Path,
        uuid_map: dict | None,
        store: TempVideoStore,
        token: int,
        seek: bool,
    ) -> None:
        super().__init__()
        self.path = Path(path)
        self._uuid_map = uuid_map
        self._store = store
        self.token = token
        self._seek = seek
        self.signals = _ResolveSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            match = resolve_live_video(self.path, self._uuid_map, self._store)
        except Exception:
            match = None
        safe_emit(self.signals, "resolved", self.token, str(self.path), match, self._seek)
