"""Tek fotoğraf için UUID arama — klasörün tamamını taramaz."""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from app.core.matcher import VIDEO_EXTS
from app.core.uuid_parser import extract_content_identifier
from app.core.qt_safe import safe_emit


class UuidSeekSignals(QObject):
    found = pyqtSignal(str, str, int)  # folder, video path, token
    finished = pyqtSignal(str, int)


class UuidSeekWorker(QRunnable):
    """Yalnızca bu fotoğrafın ContentIdentifier eşini arar; bulununca durur."""

    def __init__(self, photo: str, token: int, live_token) -> None:
        super().__init__()
        self.photo = photo
        self.token = token
        self._live_token = live_token
        self.signals = UuidSeekSignals()
        self.setAutoDelete(True)

    def _stale(self) -> bool:
        return self._live_token() != self.token

    def run(self) -> None:
        photo = Path(self.photo)
        folder = str(photo.parent)
        if self._stale():
            return
        try:
            uid = extract_content_identifier(photo)
        except OSError:
            safe_emit(self.signals, "finished", folder, self.token)
            return
        if not uid:
            safe_emit(self.signals, "finished", folder, self.token)
            return
        try:
            with os.scandir(photo.parent) as entries:
                for entry in entries:
                    if self._stale():
                        return
                    ext = Path(entry.name).suffix.lower()
                    if ext not in VIDEO_EXTS:
                        continue
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        found = extract_content_identifier(Path(entry.path))
                    except OSError:
                        continue
                    if found and found.lower() == uid.lower():
                        safe_emit(self.signals, "found", folder, entry.path, self.token)
                        return
        except OSError:
            pass
        if not self._stale():
            safe_emit(self.signals, "finished", folder, self.token)


class FolderUuidCache(QObject):
    """{uuid: path} — ihtiyaç olunca, mevcut fotoğraf için dolar."""

    ready = pyqtSignal(str)
    failed = pyqtSignal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._folder: str | None = None
        self._map: dict[str, Path] = {}
        self._token = 0
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(1)

    @property
    def folder(self) -> str | None:
        return self._folder

    @property
    def mapping(self) -> dict[str, Path]:
        return self._map

    def get(self, uuid: str) -> Path | None:
        return self._map.get(uuid.lower())

    def invalidate(self) -> None:
        self._token += 1
        self._folder = None
        self._map = {}

    def shutdown(self) -> None:
        self._token += 1
        self._pool.waitForDone(1200)

    def seek(self, photo: Path) -> None:
        photo = Path(photo)
        # Yalnızca kimlik olarak tutulur: GUI thread'inde exists()/resolve() (ağ yolunda gidiş-dönüş) gereksiz.
        self._folder = str(photo.parent)
        self._token += 1
        worker = UuidSeekWorker(str(photo), self._token, lambda: self._token)
        worker.signals.found.connect(self._on_found)
        worker.signals.finished.connect(self._on_finished)
        self._pool.start(worker)

    def _on_found(self, folder: str, video: str, token: int) -> None:
        if token != self._token:
            return
        try:
            uid = extract_content_identifier(Path(video))
        except OSError:
            uid = None
        if uid:
            self._map[uid.lower()] = Path(video)
        self.ready.emit(folder)

    def _on_finished(self, folder: str, token: int) -> None:
        # Eş bulunamadı: eşleme değişmedi. Eskiden burada da `ready` yayılıyor, ana pencere aynı
        # fotoğraf için Live çözümlemesini (dosyadan 1–24 MiB pencereler + yan dosya denetimleri) boşuna
        # ikinci kez çalıştırıyordu; sonuç değişemezdi.
        return
