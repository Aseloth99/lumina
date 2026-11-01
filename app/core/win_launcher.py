"""Gerçek Lumina.exe — Birlikte aç listesinde Python görünmesin."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _local_dir() -> Path:
    from app.core.paths import get_user_data_dir

    return get_user_data_dir()


def _csc() -> Path | None:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for bit in ("Framework64", "Framework"):
        csc = windir / "Microsoft.NET" / bit / "v4.0.30319" / "csc.exe"
        if csc.is_file():
            return csc
    return None


def _python_for_launch() -> Path:
    exe = Path(sys.executable).resolve()
    if exe.name.lower() == "python.exe":
        silent = exe.with_name("pythonw.exe")
        if silent.is_file():
            return silent
    return exe


def _script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "livePhotoViewer.py"


def _stamp(python: Path, script: Path) -> str:
    return f"3\n{python}\n{script}\n"


def _cs_string(path: Path) -> str:
    return '@"' + str(path).replace('"', '""') + '"'


def ensure_app_exe() -> Path:
    """Kayıt defteri ve Explorer bu exe'yi görür."""
    if getattr(sys, "frozen", False):
        from app.core.paths import app_executable

        return app_executable()
    from app.resources.brand import ensure_app_icon

    folder = _local_dir()
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "Lumina.exe"
    python = _python_for_launch()
    script = _script_path()
    stamp_path = folder / "launcher.src"
    wanted = _stamp(python, script)
    if exe.is_file() and stamp_path.is_file() and stamp_path.read_text(encoding="utf-8") == wanted:
        return exe
    icon = ensure_app_icon()
    csc = _csc()
    if csc is None:
        dist = Path(__file__).resolve().parents[2] / "dist" / "Lumina.exe"
        return dist if dist.is_file() else python
    cs = folder / "LuminaLauncher.cs"
    cs.write_text(
        "using System;\n"
        "using System.Diagnostics;\n"
        "using System.Reflection;\n"
        "using System.Text;\n"
        "[assembly: AssemblyTitle(\"Lumina\")]\n"
        "[assembly: AssemblyProduct(\"Lumina\")]\n"
        "[assembly: AssemblyDescription(\"Lumina\")]\n"
        "[assembly: AssemblyCompany(\"Lumina\")]\n"
        "class LuminaLaunch {\n"
        "  static int Main(string[] args) {\n"
        f"    var psi = new ProcessStartInfo({_cs_string(python)});\n"
        f"    psi.WorkingDirectory = {_cs_string(script.parent)};\n"
        "    psi.UseShellExecute = false;\n"
        "    psi.CreateNoWindow = true;\n"
        "    var sb = new StringBuilder();\n"
        "    sb.Append('\"');\n"
        f"    sb.Append({_cs_string(script)});\n"
        "    sb.Append('\"');\n"
        "    foreach (var a in args) { sb.Append(' '); sb.Append('\"'); sb.Append(a); sb.Append('\"'); }\n"
        "    psi.Arguments = sb.ToString();\n"
        "    var p = Process.Start(psi);\n"
        "    if (p == null) return 1;\n"
        "    p.WaitForExit();\n"
        "    return p.ExitCode;\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    cmd = [str(csc), "/nologo", "/target:winexe", f"/out:{exe}", str(cs)]
    if Path(icon).is_file():
        cmd.insert(3, f"/win32icon:{icon}")
    try:
        subprocess.check_call(
            cmd,
            cwd=str(folder),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        stamp_path.write_text(wanted, encoding="utf-8")
    except (OSError, subprocess.CalledProcessError):
        dist = Path(__file__).resolve().parents[2] / "dist" / "Lumina.exe"
        return dist if dist.is_file() else python
    return exe if exe.is_file() else python


# --- görev çubuğu sabitlemesi (hızlı açılış başlatıcısı) ---
# Çalışan süreç, sürümle değişen açılmış kopyadır (%LOCALAPPDATA%\Lumina\runtime\<sürüm>\Lumina.exe). Pencereye
# "yeniden başlatma komutu" yazılmazsa görev çubuğuna sabitlenen simge o kopyayı gösterir ve ilk
# güncellemede bozulur. Bu özellikler yalnızca açık bir AppUserModelID ile birlikte geçerlidir.
_PKEY_FMTID = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
_PID_RELAUNCH_COMMAND, _PID_RELAUNCH_ICON, _PID_RELAUNCH_NAME, _PID_APP_ID = 2, 3, 4, 5
_IID_PROPERTY_STORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"


def set_window_relaunch(hwnd: int, app_id: str, command: str, icon: str, name: str) -> bool:
    """Pencerenin görev çubuğu kimliğini ve sabitlenince çalıştırılacak komutu yazar (yalnızca Windows)."""
    if sys.platform != "win32" or not hwnd:
        return False
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        # vt + 3 ayrılmış WORD, ardından 16 baytlık birleşim (burada yalnızca VT_LPWSTR işaretçisi).
        _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.WORD), ("r2", wintypes.WORD), ("r3", wintypes.WORD),
                    ("pwszVal", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]

    def guid(text: str) -> GUID:
        g = GUID()
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
        return g

    VT_LPWSTR = 31
    store = ctypes.c_void_p()
    try:
        shell32 = ctypes.windll.shell32
        shell32.SHGetPropertyStoreForWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        shell32.SHGetPropertyStoreForWindow.restype = ctypes.HRESULT
        iid = guid(_IID_PROPERTY_STORE)
        shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(iid), ctypes.byref(store))
        vtbl = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        set_value = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY),
                                       ctypes.POINTER(PROPVARIANT))(vtbl[6])
        commit = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(vtbl[7])
        fmtid = guid(_PKEY_FMTID)
        # Kimlik en son: yeniden başlatma bilgileri, kimlik yazıldığı anda hazır olmalı.
        for pid, value in ((_PID_RELAUNCH_COMMAND, command), (_PID_RELAUNCH_ICON, icon),
                           (_PID_RELAUNCH_NAME, name), (_PID_APP_ID, app_id)):
            set_value(store, ctypes.byref(PROPERTYKEY(fmtid, pid)), ctypes.byref(PROPVARIANT(VT_LPWSTR, 0, 0, 0, value)))
        commit(store)
        return True
    except OSError:
        return False
    finally:
        if store:
            release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
                ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))[2]
            )
            release(store)


def pin_to_launcher(hwnd: int, app_id: str = "Lumina.Viewer") -> bool:
    """Başlatıcıyla açıldıysa sabitlenen simge başlatıcıyı (kullanıcının Lumina.exe'sini) çalıştırsın."""
    from app.core.paths import app_executable

    if not getattr(sys, "frozen", False) or not os.environ.get("LUMINA_LAUNCHER"):
        return False
    exe = str(app_executable())
    return set_window_relaunch(hwnd, app_id, f'"{exe}"', f"{exe},0", "Lumina")
