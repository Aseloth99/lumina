"""Hızlı açılan tek dosyalık Lumina.exe'yi derler: dist/Lumina.exe + dist/Lumina.zip (güncelleme paketi).

Neden: PyInstaller'ın onefile exe'si her açılışta içindeki ~380 MB'ı %TEMP%'e açıp kapanınca siliyordu; WSL
yolundaki bir PNG'de ilk karenin ~2,8 sn'sinin ~1,9 sn'si buydu. Burada uygulama klasör (onedir) olarak
derlenir, zip'lenir ve küçük bir başlatıcının (launcher/, Rust) sonuna eklenir. Başlatıcı her sürüm için
klasörü bir kez %LOCALAPPDATA%\\Lumina\\runtime\\<anahtar>\\ altına açar, sonra doğrudan oradan başlatır
(ölçüm: ilk kare ~2,8 sn -> ~0,9 sn).

Adımlar:
 1. PyInstaller, LUMINA_ONEDIR=1 ile: build/onedir/Lumina/
 2. Klasör zip'lenir (deflate): build/payload.zip; anahtar = SHA-256'nın ilk 16 hanesi
 3. Başlatıcı derlenir (cargo, --release)
 4. Lumina.exe'nin simgesi ve sürüm bilgisi başlatıcıya kopyalanır (Gezgin / "Birlikte aç" aynı görsün)
 5. dist/Lumina.exe = başlatıcı + payload.zip + iz (ofset, uzunluk, anahtar, sihir)
 6. dist/Lumina.zip = güncelleme paketi (içinde Lumina.exe)

Kullanım: py -3.12 scripts/build_fast_exe.py
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
MAGIC = b"LUMRT\x00\x01\x00"


def run(cmd: list[str], **kw) -> None:
    print(">", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, **kw)


def build_onedir() -> Path:
    env = dict(os.environ, LUMINA_ONEDIR="1")
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(BUILD / "onedir"),
         "--workpath", str(BUILD / "pyinstaller"), str(ROOT / "lumina.spec")], cwd=ROOT, env=env)
    app = BUILD / "onedir" / "Lumina"
    if not (app / "Lumina.exe").is_file():
        raise SystemExit(f"HATA: onedir çıktısı yok: {app}")
    return app


def make_payload(app: Path) -> tuple[Path, str]:
    payload = BUILD / "payload.zip"
    files = sorted(p for p in app.rglob("*") if p.is_file())
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in files:
            z.write(p, p.relative_to(app).as_posix())
    digest = hashlib.sha256()
    with payload.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    key = digest.hexdigest()[:16]
    raw = sum(p.stat().st_size for p in files)
    print(f"payload: {len(files)} dosya, {raw / 2**20:.1f} MB -> {payload.stat().st_size / 2**20:.1f} MB, anahtar {key}")
    return payload, key


def build_launcher() -> Path:
    target = BUILD / "launcher-target"
    run(["cargo", "build", "--release"], cwd=ROOT / "launcher", env=dict(os.environ, CARGO_TARGET_DIR=str(target)))
    exe = target / "release" / "lumina_launcher.exe"
    if not exe.is_file():
        raise SystemExit(f"HATA: başlatıcı derlenmedi: {exe}")
    return exe


# --- kaynak kopyalama (simge + sürüm bilgisi): Win32 UpdateResource ---
RT_ICON, RT_GROUP_ICON, RT_VERSION = 3, 14, 16
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ENUM_NAME = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_ENUM_LANG = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD, ctypes.c_void_p)
_k32.LoadLibraryExW.restype = wintypes.HMODULE
_k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
_k32.EnumResourceNamesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, _ENUM_NAME, ctypes.c_void_p]
_k32.EnumResourceLanguagesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, _ENUM_LANG, ctypes.c_void_p]
_k32.FindResourceExW.restype = ctypes.c_void_p
_k32.FindResourceExW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD]
_k32.SizeofResource.argtypes = [wintypes.HMODULE, ctypes.c_void_p]
_k32.LoadResource.restype = ctypes.c_void_p
_k32.LoadResource.argtypes = [wintypes.HMODULE, ctypes.c_void_p]
_k32.LockResource.restype = ctypes.c_void_p
_k32.LockResource.argtypes = [ctypes.c_void_p]
_k32.BeginUpdateResourceW.restype = wintypes.HANDLE
_k32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
_k32.UpdateResourceW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD, ctypes.c_void_p, wintypes.DWORD]
_k32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
_k32.FreeLibrary.argtypes = [wintypes.HMODULE]


def _read_resources(path: Path, kinds: tuple[int, ...]) -> list[tuple[int, object, int, bytes]]:
    """(tür, ad, dil, veri): ad bir sayı (MAKEINTRESOURCE) ya da metindir."""
    h = _k32.LoadLibraryExW(str(path), None, 0x2 | 0x20)  # DATAFILE | IMAGE_RESOURCE
    if not h:
        raise OSError(ctypes.get_last_error(), f"LoadLibraryEx {path}")
    out: list[tuple[int, object, int, bytes]] = []
    try:
        for kind in kinds:
            names: list[object] = []

            def on_name(_h, _t, name, _p):
                names.append(name if name < 0x10000 else ctypes.wstring_at(name))
                return True

            _k32.EnumResourceNamesW(h, kind, _ENUM_NAME(on_name), None)
            for name in names:
                langs: list[int] = []

                def on_lang(_h, _t, _n, lang, _p):
                    langs.append(lang)
                    return True

                name_arg = name if isinstance(name, int) else ctypes.c_wchar_p(name)
                _k32.EnumResourceLanguagesW(h, kind, name_arg, _ENUM_LANG(on_lang), None)
                for lang in langs:
                    res = _k32.FindResourceExW(h, kind, name_arg, lang)
                    size = _k32.SizeofResource(h, res)
                    ptr = _k32.LockResource(_k32.LoadResource(h, res))
                    out.append((kind, name, lang, ctypes.string_at(ptr, size)))
    finally:
        _k32.FreeLibrary(h)
    return out


def copy_icon_and_version(src: Path, dst: Path) -> int:
    resources = _read_resources(src, (RT_ICON, RT_GROUP_ICON, RT_VERSION))
    upd = _k32.BeginUpdateResourceW(str(dst), False)
    if not upd:
        raise OSError(ctypes.get_last_error(), f"BeginUpdateResource {dst}")
    for kind, name, lang, data in resources:
        name_arg = name if isinstance(name, int) else ctypes.c_wchar_p(name)
        buf = ctypes.create_string_buffer(data, len(data))
        if not _k32.UpdateResourceW(upd, kind, name_arg, lang, buf, len(data)):
            _k32.EndUpdateResourceW(upd, True)
            raise OSError(ctypes.get_last_error(), "UpdateResource")
    if not _k32.EndUpdateResourceW(upd, False):
        raise OSError(ctypes.get_last_error(), "EndUpdateResource")
    return len(resources)


def assemble(launcher: Path, icon_source: Path, payload: Path, key: str) -> Path:
    stub = BUILD / "launcher-stub.exe"
    shutil.copy2(launcher, stub)
    n = copy_icon_and_version(icon_source, stub)
    print(f"başlatıcıya {n} kaynak (simge + sürüm) kopyalandı")
    DIST.mkdir(exist_ok=True)
    out = DIST / "Lumina.exe"
    offset = stub.stat().st_size
    length = payload.stat().st_size
    with out.open("wb") as w:
        w.write(stub.read_bytes())
        with payload.open("rb") as r:
            shutil.copyfileobj(r, w, 1 << 20)
        w.write(struct.pack("<QQ", offset, length) + key.encode("ascii") + MAGIC)
    print(f"EXE: {out} ({out.stat().st_size / 2**20:.1f} MB)")
    return out


def make_update_zip(exe: Path) -> Path:
    z = DIST / "Lumina.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        f.write(exe, "Lumina.exe")
        lic = DIST / "LICENSE"
        if lic.exists():
            f.write(lic, "LICENSE")
    print(f"ZIP: {z}")
    return z


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Yalnızca Windows'ta derlenir.")
    app = build_onedir()
    payload, key = make_payload(app)
    launcher = build_launcher()
    exe = assemble(launcher, app / "Lumina.exe", payload, key)
    make_update_zip(exe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
