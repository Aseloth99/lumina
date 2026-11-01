"""Sessiz çöküşleri diske yaz — GUI açılmadan ölen süreçler için."""

from __future__ import annotations

import faulthandler
import os
import sys
import traceback
from pathlib import Path

_log_handle = None


LOG_NAME = "crash.log"


def crash_log_path() -> Path:
    r"""Çökme günlüğü: `%LOCALAPPDATA%\Lumina\logs\crash.log` (FlexiKey/MediaCompressor/TransferTCP ile aynı ad ve konum).

    Eski sürümler `Lumina\lumina-crash.log` (kökte) yazıyordu; o dosyaya artık yazılmaz (varsa olduğu gibi kalır).
    """
    from app.core.paths import get_user_data_dir

    folder = get_user_data_dir() / "logs"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder = Path(os.environ.get("TEMP") or ".") / "Lumina" / "logs"
        folder.mkdir(parents=True, exist_ok=True)
    return folder / LOG_NAME


MAX_LOG_BYTES = 1024 * 1024  # 1 MiB; aşılırsa eski günlük .1'e taşınır (tek yedek)


def rotate_if_large(path: Path, max_bytes: int = MAX_LOG_BYTES) -> bool:
    """Günlük çok büyüdüyse `<ad>.1`e taşı. (Eskiden hiç döndürülmüyor ve 27 MB'a ulaşıyordu.)"""
    try:
        if path.is_file() and path.stat().st_size > max_bytes:
            backup = path.with_name(path.name + ".1")
            try:
                backup.unlink()
            except FileNotFoundError:
                pass
            path.replace(backup)
            return True
    except OSError:
        pass
    return False


_win_filter = None  # ctypes geri çağrısı GC'ye gitmesin


def _install_windows_crash_filter(handle) -> bool:
    """Windows'ta yalnızca *işlenmemiş* istisnada yığın dök.

    `faulthandler.enable()` Windows'ta vectored exception handler kurar; bu, yüksek bit'i açık
    (0x8xxxxxxx / 0xExxxxxxx) HER istisnada çalışır — başka bir yerde yakalanıp işlenecek olanlarda da.
    libmpv/D3D11 oynatma sırasında yaklaşık saniyede bir böyle zararsız istisna fırlatır (0xE24C4A02).
    Her seferinde tüm thread'lerin Python çerçeveleri, çalışmaya devam eden thread'ler yazılırken
    okunuyor, bu yarış erişim ihlaliyle süreci öldürüyordu (video oynatırken çökme). Günlük de şişiyordu.
    `SetUnhandledExceptionFilter` yalnızca süreç gerçekten ölürken çağrılır.
    """
    global _win_filter
    try:
        import ctypes
        from ctypes import wintypes

        class _ExceptionRecord(ctypes.Structure):
            _fields_ = [
                ("ExceptionCode", wintypes.DWORD),
                ("ExceptionFlags", wintypes.DWORD),
                ("ExceptionRecord", ctypes.c_void_p),
                ("ExceptionAddress", ctypes.c_void_p),
            ]

        class _ExceptionPointers(ctypes.Structure):
            _fields_ = [
                ("ExceptionRecord", ctypes.POINTER(_ExceptionRecord)),
                ("ContextRecord", ctypes.c_void_p),
            ]

        filter_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.POINTER(_ExceptionPointers))

        def _on_unhandled(info) -> int:
            try:
                rec = info.contents.ExceptionRecord.contents
                handle.write(
                    f"Windows fatal exception: code 0x{rec.ExceptionCode:08x} "
                    f"at 0x{(rec.ExceptionAddress or 0):x}\n"
                )
                handle.flush()
                faulthandler.dump_traceback(handle, all_threads=True)
                handle.flush()
            except Exception:
                pass
            return 0  # EXCEPTION_CONTINUE_SEARCH: süreç her zamanki gibi sonlansın

        _win_filter = filter_type(_on_unhandled)
        ctypes.windll.kernel32.SetUnhandledExceptionFilter(_win_filter)
        return True
    except Exception:
        _win_filter = None
        return False


def install_crash_log() -> Path:
    global _log_handle
    path = crash_log_path()
    if _log_handle is not None:
        return path
    rotate_if_large(path)
    _log_handle = path.open("a", encoding="utf-8", errors="replace")
    if sys.platform == "win32" and _install_windows_crash_filter(_log_handle):
        pass
    elif sys.platform == "win32":
        # Süzgeç kurulamadı: yedek olarak faulthandler, ama yalnızca istisnayı alan thread'in yığını
        # (çalışan diğer thread'lerle yarışmasın).
        faulthandler.enable(_log_handle, all_threads=False)
    else:
        faulthandler.enable(_log_handle, all_threads=True)

    def _hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            _log_handle.write(text + "\n")
            _log_handle.flush()
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook
    return path
