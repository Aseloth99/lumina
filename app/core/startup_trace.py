"""Açılış zaman çizelgesi (tanılama) ve otomasyon kum havuzu.

* `LUMINA_STARTUP_TRACE=<dosya>`: açılış aşamaları (`mark`) bu dosyaya `ad<TAB>epoch_ms<TAB>pid` satırları
  olarak eklenir. Ölçüm betikleri "süreç başladı -> ilk kare ekranda" süresini buradan okur. Değişken
  yoksa her çağrı tek bir `if` ile döner (ürün davranışı değişmez).
* `LUMINA_SANDBOX=<klasör>`: duman testi / ölçüm çalıştırması. Ayarlar kayıt defteri yerine bu klasördeki
  INI'ye yazılır; dosya ilişkilendirmesi (HKCU) ve güncelleme denetimi devre dışıdır.
  Kullanıcının kendi Lumina'sına/ayarlarına dokunulmaz. (Pencereyi kullanıcıdan gizlemek çağıranın işidir:
  `scripts/smoke_exe.py` süreci ayrı, görünmeyen bir masaüstünde başlatır.)
"""

from __future__ import annotations

import os
import time

_PATH = os.environ.get("LUMINA_STARTUP_TRACE") or None
_seen: set[str] = set()


def enabled() -> bool:
    return _PATH is not None


def sandbox_dir() -> str | None:
    return os.environ.get("LUMINA_SANDBOX") or None


def mark(name: str, epoch_ms: float | None = None) -> None:
    """Aşama zamanını (bir kez) yaz. `epoch_ms` verilirse o an kaydedilir (önceden alınmış zaman)."""
    if _PATH is None or name in _seen:
        return
    _seen.add(name)
    _write(name, time.time() * 1000.0 if epoch_ms is None else float(epoch_ms))


def _write(name: str, epoch_ms: float) -> None:
    try:
        with open(_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{name}\t{epoch_ms:.1f}\t{os.getpid()}\n")
    except OSError:
        pass


def mark_process_created() -> None:
    """Bu sürecin işletim sistemindeki oluşturulma anı (onefile exe'de: açma işleminden SONRA)."""
    if _PATH is None or "process_created" in _seen:
        return
    _seen.add("process_created")
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(
            k32.GetCurrentProcess(),
            ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user),
        ):
            return
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime  # 100 ns, 1601'den beri
        _write("process_created", ticks / 10_000.0 - 11_644_473_600_000.0)
    except Exception:  # noqa: BLE001 — yalnızca tanılama
        pass


def watch_first_paint(widget, has_content, name: str = "first_frame") -> None:
    """`widget` içerikle (has_content() True) ilk kez boyandıktan hemen sonra `name` işaretle."""
    if _PATH is None:
        return
    from PyQt6.QtCore import QEvent, QObject, QTimer

    class _PaintSpy(QObject):
        def eventFilter(self, obj, event):  # noqa: N802
            if event.type() == QEvent.Type.Paint and name not in _seen:
                try:
                    ready = bool(has_content())
                except Exception:  # noqa: BLE001
                    ready = False
                if ready:
                    # Boyama ve pencereye aktarma bu olayın ardından aynı turda biter.
                    QTimer.singleShot(0, lambda: mark(name))
            return False

    spy = _PaintSpy(widget)
    widget.installEventFilter(spy)
    widget._lumina_paint_spy = spy
