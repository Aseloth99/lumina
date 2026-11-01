"""libmpv yükleyici ve ortam hazırlığı — Windows/PyInstaller uyumlu."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_mpv_module = None
_mpv_initialized = False


def _find_mpv_library() -> Path | None:
    """libmpv dosyasının tam yolu. `LUMINA_MPV_DIR` ortam değişkeni (varsa) en önce aranır."""
    candidates: list[Path] = []
    override = os.environ.get("LUMINA_MPV_DIR")
    if override:
        candidates.append(Path(override))
    # 1. PyInstaller _MEIPASS
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass)
        candidates.append(p)
        candidates.append(p / "vendor" / "mpv")
    # 2. Executable dizini
    exe_dir = Path(sys.executable).resolve().parent
    candidates.append(exe_dir)
    candidates.append(exe_dir / "vendor" / "mpv")
    # 3. Geliştirme ağacı: Lumina/vendor/mpv
    project_root = Path(__file__).resolve().parents[2]
    candidates.append(project_root / "vendor" / "mpv")
    candidates.append(project_root)

    # 4. Platforma özel sistem dizinleri
    if sys.platform == "win32":
        prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        candidates.append(Path(prog_files) / "mpv.net")
        candidates.append(Path(prog_files) / "mpv")
        dll_names = ("libmpv-2.dll", "mpv-2.dll", "mpv-1.dll")
    elif sys.platform == "darwin":
        candidates.extend([
            Path("/opt/homebrew/lib"),
            Path("/usr/local/lib"),
            Path("/opt/local/lib"),
            Path.home() / "lib",
        ])
        dll_names = ("libmpv.2.dylib", "libmpv.1.dylib", "libmpv.dylib")
    else:
        # Linux
        candidates.extend([
            Path("/usr/lib/x86_64-linux-gnu"),
            Path("/usr/lib/aarch64-linux-gnu"),
            Path("/usr/lib64"),
            Path("/usr/lib"),
            Path("/usr/local/lib"),
            Path.home() / ".local" / "lib",
        ])
        dll_names = ("libmpv.so.2", "libmpv.so.1", "libmpv.so")

    for d in candidates:
        try:
            if d.is_dir():
                for dll_name in dll_names:
                    if (d / dll_name).is_file():
                        return d / dll_name
        except OSError:
            continue
    return None


def _find_mpv_dll_dir() -> Path | None:
    lib = _find_mpv_library()
    return lib.parent if lib is not None else None


def _patch_find_library(lib_path: Path) -> bool:
    """python-mpv Linux/macOS'ta `ctypes.util.find_library('mpv')` ile arar; bu yalnız ldconfig önbelleğine
    / standart dizinlere bakar. Sistem kütüphaneyi bulamıyorsa (kullanıcı dizini, Apple Silicon Homebrew,
    paketlenmiş kopya) bulduğumuz tam yolu döndüren bir yama uygula. Yama uygulandıysa True."""
    import ctypes.util

    original = ctypes.util.find_library
    try:
        if original("mpv"):
            return False
    except Exception:  # noqa: BLE001 — find_library bazı sistemlerde araç yoksa hata verebilir
        pass

    def patched(name, _orig=original, _path=str(lib_path)):
        return _path if name == "mpv" else _orig(name)

    ctypes.util.find_library = patched
    return True


def init_mpv():
    global _mpv_module, _mpv_initialized
    if _mpv_initialized:
        return _mpv_module
    _mpv_initialized = True

    lib_file = _find_mpv_library()
    dll_dir = lib_file.parent if lib_file is not None else None
    if dll_dir is not None:
        dll_dir_str = str(dll_dir.resolve())
        # python-mpv direct library path override
        os.environ["MPV_LD_LIBRARY_PATH"] = dll_dir_str

        if sys.platform == "win32":
            try:
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(dll_dir_str)
            except OSError:
                pass
            cur_path = os.environ.get("PATH", "")
            if dll_dir_str not in cur_path:
                os.environ["PATH"] = dll_dir_str + os.pathsep + cur_path
        elif sys.platform == "darwin":
            cur_dyld = os.environ.get("DYLD_LIBRARY_PATH", "")
            if dll_dir_str not in cur_dyld:
                os.environ["DYLD_LIBRARY_PATH"] = (
                    dll_dir_str + (os.pathsep + cur_dyld if cur_dyld else "")
                )
        else:
            cur_ld = os.environ.get("LD_LIBRARY_PATH", "")
            if dll_dir_str not in cur_ld:
                os.environ["LD_LIBRARY_PATH"] = (
                    dll_dir_str + (os.pathsep + cur_ld if cur_ld else "")
                )

    if lib_file is not None and sys.platform != "win32":
        _patch_find_library(lib_file.resolve())

    try:
        import mpv

        _mpv_module = mpv
    except Exception:
        vendor_dir = str((Path(__file__).resolve().parents[2] / "vendor").resolve())
        if vendor_dir not in sys.path:
            sys.path.insert(0, vendor_dir)
        try:
            import mpv

            _mpv_module = mpv
        except Exception:
            _mpv_module = None

    return _mpv_module


def mpv_available() -> bool:
    return init_mpv() is not None


def get_mpv():
    return init_mpv()
