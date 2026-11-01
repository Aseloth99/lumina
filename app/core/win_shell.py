"""Windows kabuk yardımcıları — Explorer, geri dönüşüm, varsayılan uygulamalar."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UndoBundle:
    originals: list[Path]
    backups: list[Path]
    folder: Path


def reveal_in_explorer(path: Path) -> None:
    path = Path(path).resolve()
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
        return
    # Linux: Try D-Bus FileManager1 to highlight item, fallback to opening parent folder
    try:
        subprocess.Popen([
            "dbus-send", "--session", "--dest=org.freedesktop.FileManager1",
            "--type=method_call", "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1.ShowItems",
            f"array:string:file://{path}", "string:"
        ])
    except Exception:
        subprocess.Popen(["xdg-open", str(path.parent)])


def open_with(path: Path, hwnd: int = 0) -> None:
    path = Path(path).resolve()
    if not path.exists():
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
        return
    if sys.platform.startswith("linux"):
        subprocess.Popen(["xdg-open", str(path)])
        return
    if sys.platform != "win32":
        return

    import ctypes
    from ctypes import wintypes

    try:
        ctypes.windll.ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    except Exception:
        pass

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", wintypes.LPVOID),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_INVOKEIDLIST = 0x0000000C
    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    sei.fMask = SEE_MASK_INVOKEIDLIST
    sei.hwnd = int(hwnd) if hwnd else 0
    sei.lpVerb = "openas"
    sei.lpFile = str(path)
    sei.nShow = 1

    success = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
    if not success:
        class OPENASINFO(ctypes.Structure):
            _fields_ = [
                ("pcszFile", wintypes.LPCWSTR),
                ("pcszClass", wintypes.LPCWSTR),
                ("oaifInFlags", wintypes.UINT),
            ]

        OAIF_EXEC = 0x00000004
        OAIF_ALLOW_REGISTRATION = 0x00000001
        info = OPENASINFO()
        info.pcszFile = str(path)
        info.pcszClass = None
        info.oaifInFlags = OAIF_EXEC | OAIF_ALLOW_REGISTRATION
        ctypes.windll.shell32.SHOpenWithDialog(int(hwnd) if hwnd else 0, ctypes.byref(info))


def open_default_apps() -> None:
    if sys.platform == "win32":
        os.startfile("ms-settings:defaultapps")  # noqa: S606 — Windows Ayarlar URI
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", "/System/Library/PreferencePanes/Extensions.prefPane"])
        return
    if sys.platform.startswith("linux"):
        for cmd in (["gnome-control-center", "default-apps"], ["systemsettings5"]):
            try:
                subprocess.Popen(cmd)
                return
            except Exception:
                continue


def copy_for_undo(paths: list[Path], dest_dir: Path) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    backups: list[Path] = []
    for path in paths:
        path = Path(path)
        dest = dest_dir / path.name
        if dest.exists():
            dest = dest_dir / f"{path.stem}_{len(backups)}{path.suffix}"
        shutil.copy2(path, dest)
        backups.append(dest)
    return backups


def restore_files(originals: list[Path], backups: list[Path]) -> list[Path]:
    restored: list[Path] = []
    for original, backup in zip(originals, backups, strict=False):
        backup = Path(backup)
        original = Path(original)
        if not backup.is_file():
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, original)
        restored.append(original)
    return restored



def _unique_name(taken, name: str) -> str:
    """`taken(ad)` doluysa `ad.1.uzantı`, `ad.2.uzantı` ... dener."""
    if not taken(name):
        return name
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    n = 1
    while True:
        candidate = f"{stem}.{n}.{suffix}" if suffix else f"{stem}.{n}"
        if not taken(candidate):
            return candidate
        n += 1


def trash_xdg(path: Path, data_home: Path | None = None) -> Path:
    """FreeDesktop.org Trash 1.0: dosyayı `$XDG_DATA_HOME/Trash/files`a taşı, `.trashinfo` yaz.

    `gio`/`trash-put` bulunmayan sistemlerde yedek olarak kullanılır; kalıcı silmenin yerine geçer.
    Dönüş: çöp kutusundaki yeni yol.
    """
    import urllib.parse
    from datetime import datetime

    path = Path(path).resolve()
    base = Path(data_home) if data_home else Path(
        os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    )
    root = base / "Trash"
    files_dir, info_dir = root / "files", root / "info"
    for d in (files_dir, info_dir):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = _unique_name(
        lambda n: (files_dir / n).exists() or (info_dir / f"{n}.trashinfo").exists(), path.name
    )
    info_text = (
        "[Trash Info]\n"
        f"Path={urllib.parse.quote(str(path))}\n"
        f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n"
    )
    info_path = info_dir / f"{name}.trashinfo"
    fd = os.open(info_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(info_text)
    target = files_dir / name
    try:
        shutil.move(str(path), str(target))
    except Exception:
        info_path.unlink(missing_ok=True)  # yarım kalan kayıt bırakma
        raise
    return target


def trash_macos_fallback(path: Path, trash_dir: Path | None = None) -> Path:
    """osascript/Finder kullanılamazsa `~/.Trash`'e taşı (kalıcı silme yok)."""
    path = Path(path).resolve()
    trash = Path(trash_dir) if trash_dir else Path.home() / ".Trash"
    trash.mkdir(parents=True, exist_ok=True)
    name = _unique_name(lambda n: (trash / n).exists(), path.name)
    target = trash / name
    shutil.move(str(path), str(target))
    return target


def send_to_recycle(path: Path) -> None:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    # 1. macOS: AppleScript Finder delete (moves to Trash)
    if sys.platform == "darwin":
        try:
            clean_path = str(path).replace('"', '\\"')
            script = f'tell application "Finder" to delete POSIX file "{clean_path}"'
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if res.returncode == 0:
                return
        except Exception:
            pass
        trash_macos_fallback(path)  # kalıcı silme yerine ~/.Trash
        return

    # 2. Linux: gio trash -> trash-put -> FreeDesktop Trash (saf Python)
    if sys.platform.startswith("linux"):
        try:
            res = subprocess.run(["gio", "trash", str(path)], capture_output=True)
            if res.returncode == 0:
                return
        except Exception:
            pass
        try:
            res = subprocess.run(["trash-put", str(path)], capture_output=True)
            if res.returncode == 0:
                return
        except Exception:
            pass
        trash_xdg(path)  # kalıcı silme yerine ev çöp kutusu
        return

    if sys.platform != "win32":
        trash_xdg(path)
        return

    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x40
    FOF_NOCONFIRMATION = 0x10
    FOF_SILENT = 0x04

    path_str = str(path).replace("/", "\\")
    buf = ctypes.create_unicode_buffer(path_str + "\0")
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buf, wintypes.LPCWSTR)
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0:
        raise OSError(f"SHFileOperationW failed (code {result})")
    if op.fAnyOperationsAborted:
        raise OSError("Silme işlemi iptal edildi")


def recycle_with_undo(paths: list[Path]) -> UndoBundle:
    existing = [Path(p) for p in paths if Path(p).is_file()]
    folder = Path(tempfile.mkdtemp(prefix="lumina-undo-"))
    backups = copy_for_undo(existing, folder)
    errors: list[str] = []
    for path in existing:
        try:
            send_to_recycle(path)
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    if errors and not any(not p.exists() for p in existing):
        shutil.rmtree(folder, ignore_errors=True)
        raise OSError("; ".join(errors))
    return UndoBundle(existing, backups, folder)


def discard_undo(bundle: UndoBundle | None) -> None:
    if bundle is None:
        return
    shutil.rmtree(bundle.folder, ignore_errors=True)
