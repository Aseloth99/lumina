"""Arka planda çalışan düzenleme işleri (kırpma/döndürme kaydı) — GUI'yi bekletmez."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from app.core.qt_safe import safe_emit


class EditSignals(QObject):
    done = pyqtSignal(object, str, int)  # sonuç, hata metni ('' = başarılı), jeton


class EditWorker(QRunnable):
    def __init__(self, fn: Callable[[], object], token: int) -> None:
        super().__init__()
        self._fn = fn
        self.token = token
        self.signals = EditSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 — kullanıcıya gösterilir
            safe_emit(self.signals, "done", None, str(exc) or type(exc).__name__, self.token)
            return
        safe_emit(self.signals, "done", result, "", self.token)
