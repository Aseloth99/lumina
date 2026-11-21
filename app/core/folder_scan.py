"""Klasör medya listesi — GUI dışında, yalnızca isimler."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from app.core.explorer_sort import resolve_sort
from app.core.matcher import index_in_folder, list_folder_media
from app.core.qt_safe import safe_emit

REFINE_GATE_MAX_S = 3.0


class FolderScanSignals(QObject):
    ready = pyqtSignal(str, list, int, int)  # folder, path strings, current idx, token
    failed = pyqtSignal(str, int)
    # Kullanılan sabit sıralama, Gezgin'den mi okundu, token ("Gezgin ile aynı" kipinde listeden ÖNCE yayınlanır).
    sort_resolved = pyqtSignal(str, bool, int)


class FolderScanWorker(QRunnable):
    def __init__(
        self,
        folder: str,
        current_name: str,
        token: int,
        sort_by: str = "date_asc",
        progressive: bool = False,
        is_current: Callable[[], bool] | None = None,
        refine_gate: threading.Event | None = None,
        fallback_sort: str = "date_asc",
    ) -> None:
        super().__init__()
        self.folder = folder
        self.current_name = current_name
        self.token = token
        # sort_by "explorer" ise sıra taramada Gezgin'den okunur (COM; arayüz iş parçacığı beklemez), okunamazsa fallback_sort.
        self.sort_by = sort_by
        self.fallback_sort = fallback_sort
        # progressive: önce EXIF okumadan (mtime) hızlı liste, ardından EXIF ile inceltilmiş liste.
        self.progressive = progressive
        self._is_current = is_current
        # EXIF inceltmesi her görseli açar (<=300 dosya); açılan fotoğraf ekrana gelene dek bekler.
        self._refine_gate = refine_gate
        self.signals = FolderScanSignals()
        self.setAutoDelete(True)

    def _stale(self) -> bool:
        return self._is_current is not None and not self._is_current()

    def _emit(self, files: list[Path]) -> None:
        idx = index_in_folder(files, Path(self.current_name)) if self.current_name else 0
        safe_emit(self.signals, "ready", self.folder, [str(path) for path in files], idx, self.token)

    def run(self) -> None:
        try:
            folder = Path(self.folder)
            if self.sort_by not in ("date_asc", "date_desc", "name_asc", "name_desc"):
                self.sort_by, from_explorer = resolve_sort(self.sort_by, self.fallback_sort, self.folder)
                if self._stale():
                    return
                safe_emit(self.signals, "sort_resolved", self.sort_by, from_explorer, self.token)
            if self.progressive and self.sort_by.startswith("date"):
                quick = list_folder_media(folder, sort_by=self.sort_by, refine_exif=False)
                if self._stale():
                    return
                self._emit(quick)
                if not quick or self._stale():
                    return
                if self._refine_gate is not None:
                    self._refine_gate.wait(REFINE_GATE_MAX_S)
                    if self._stale():
                        return
                files = list_folder_media(folder, sort_by=self.sort_by)
                if self._stale() or files == quick:
                    return
                self._emit(files)
                return
            files = list_folder_media(folder, sort_by=self.sort_by)
            if self._stale():
                return
            self._emit(files)
        except OSError:
            safe_emit(self.signals, "failed", self.folder, self.token)
