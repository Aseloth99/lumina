"""Sistem tepsisi simgesinin sahibi: aynı anda tek Lumina süreci simge gösterir.

Lumina her dosya için ayrı süreç/pencere açabilir (tek kopya kilidi yok). Her sürecin kendi tepsi simgesi olursa
tepsi N simgeyle dolar; bu yüzden ilk çalışan süreç "tepsi sahibi" olur, diğerleri simgesiz sürer ve sahip çıkınca
(ya da çökünce) bir sonraki yoklamada biri devralır.

Kilit, `QLockFile` ile adlandırılmış bir dosyadır. Süreç ölürse (çökme, görev yöneticisi, elektrik kesintisi)
dosya kalır ama KİLİT DÜŞER: `QLockFile` kilidin içindeki süreç numarasının hâlâ çalışıp çalışmadığına bakar ve
çalışmıyorsa kilidi bayat sayıp siler. Yaşa göre bayatlama kapatılır (`setStaleLockTime(0)`): saatlerce açık kalan
sahibin kilidi, "30 saniyeden eski" diye başkası tarafından çalınmasın.

Bu modül Qt arayüzüne bağlı değildir (yalnızca `QtCore.QLockFile`); `TrayOwnership` iki "süreci" aynı süreçte iki
nesneyle taklit ederek de sınanabilir (aynı süreçteki ikinci `QLockFile` de kilidi alamaz).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PyQt6.QtCore import QLockFile

LOCK_NAME = "Lumina.tray.lock"


def default_lock_path() -> Path:
    """Kullanıcıya özel kilit dosyası: Windows'ta %TEMP% zaten kullanıcıya özeldir; POSIX'te /tmp ortaktır, uid eklenir."""
    name = LOCK_NAME
    getuid = getattr(os, "getuid", None)
    if callable(getuid):
        name = f"{LOCK_NAME[:-5]}.{getuid()}.lock"
    return Path(tempfile.gettempdir()) / name


class TrayOwnership:
    """"Tepsi sahibi" olma hakkı. `try_acquire()` yoklama için güvenle tekrar çağrılabilir."""

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path is not None else default_lock_path()
        self._lock: QLockFile | None = None

    @property
    def owned(self) -> bool:
        return self._lock is not None and self._lock.isLocked()

    def try_acquire(self) -> bool:
        """Sahipliği almayı dener (beklemez). Zaten sahipse True. Başka canlı süreç sahipse False."""
        if self.owned:
            return True
        lock = QLockFile(str(self.path))
        lock.setStaleLockTime(0)  # yalnızca "sahibi artık çalışmıyor" ise bayat; yaşa bakılmaz
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            got = lock.tryLock(0)
        except OSError:
            got = False
        if got:
            self._lock = lock
        return got

    def release(self) -> None:
        """Sahipliği bırakır (çıkışta); bırakılmazsa süreç ölünce kilit zaten bayatlar."""
        lock, self._lock = self._lock, None
        if lock is not None:
            try:
                lock.unlock()
            except Exception:  # noqa: BLE001 — çıkışı asla bozma
                pass
