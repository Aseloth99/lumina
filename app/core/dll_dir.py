"""Windows: yeni sürümü başlatmadan önce DLL arama dizinini varsayılana alır.

PyInstaller önyükleyicisi `SetDllDirectoryW(<sürüm klasörü>\\_internal)` çağırır ve Windows bu ayarı sürecin oluşturduğu ÇOCUK
süreçlere aktarır. Güncelleme sonrası yeni başlatıcı (Rust, dinamik CRT) `VCRUNTIME140.dll`'i System32'den değil ESKİ sürümün
`_internal` klasöründen yükler ve ömrü boyunca açık tutar: başlatıcının eski sürüm klasörünü silme işi (`cleanup_old`) o dosyada
takılır (std `remove_dir_all` ilk hatada bırakır; ~23 dosya bir sonraki açılışa kalır) ve yeni sürüm eski sürümün DLL'ine bağlanmış olur.

Yeni sürümü başlatan çağrılar (`self_update.install_and_relaunch`, geri almada `self_update.spawn`) bu bağlam yöneticisiyle sarılır:
çağrı süresince DLL dizini varsayılandır, sonra eski değeri geri yüklenir (çalışan süreç kendi DLL'lerini aramaya devam eder).
Windows dışında bir şey yapmaz; herhangi bir hata sessizce yutulur (güncelleme asla bu yüzden bozulmaz).
"""

from __future__ import annotations

import contextlib
import sys
from typing import Iterator

_BUFFER_CHARS = 32768  # Windows yol üst sınırı


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    kernel32.GetDllDirectoryW.restype = wintypes.DWORD
    kernel32.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    kernel32.SetDllDirectoryW.restype = wintypes.BOOL
    return kernel32


def current_dll_directory() -> str:
    """Sürecin `SetDllDirectoryW` ile ayarlanmış DLL dizini (ayarlı değilse ''). Windows dışında ''."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes

        kernel32 = _kernel32()
        buffer = ctypes.create_unicode_buffer(_BUFFER_CHARS)
        length = kernel32.GetDllDirectoryW(_BUFFER_CHARS, buffer)
        return buffer.value if 0 < length < _BUFFER_CHARS else ""
    except Exception:  # noqa: BLE001
        return ""


@contextlib.contextmanager
def default_dll_directory() -> Iterator[None]:
    """Bağlam süresince `SetDllDirectoryW(NULL)` (varsayılan arama sırası); çıkışta önceki dizin geri yüklenir."""
    kernel32 = None
    previous = ""
    if sys.platform == "win32":
        try:
            previous = current_dll_directory()
            kernel32 = _kernel32()
            if previous:
                kernel32.SetDllDirectoryW(None)  # NULL: ayarı kaldırır ('' DEĞİL: boş dize geçerli dizini arama sırasından çıkarır)
        except Exception:  # noqa: BLE001
            kernel32 = None
    try:
        yield
    finally:
        if kernel32 is not None and previous:
            try:
                kernel32.SetDllDirectoryW(previous)
            except Exception:  # noqa: BLE001
                pass
