"""Paketlenmiş Lumina.exe duman testi (Windows).

Kullanım:  python scripts/smoke_exe.py [exe_yolu] [acilacak_dosya]
           (dosya verilmezse sentetik bir 1920x1080 JPEG üretilip açılır)

Doğrular:
  1. exe başlar ve süreç hemen ölmez,
  2. süreç görünür bir üst düzey pencere açar (arayüz gerçekten kurulur) ve dosyanın ilk karesi çizilir,
  3. birkaç saniye sonra hâlâ yaşıyor,
  4. çökme günlüğüne bu çalışma sırasında `Traceback` düşmemiş,
  5. pencere kapatılınca süreç kendiliğinden ve temiz biter (onefile geçici klasörü silinir).
Hızlı açılış başlatıcısıyla derlenmiş exe'de (scripts/build_fast_exe.py) ayrıca:
  6. başlatıcı uygulamayı açılmış klasörden (LOCALAPPDATA/Lumina/runtime/<anahtar>/Lumina.exe) başlatıp çıkar,
  7. ikinci açılış klasörü yeniden açmaz (bildirim dosyası aynı kalır).

Kullanıcıya dokunmaz:
  * `LUMINA_SANDBOX`: ayarlar kayıt defteri yerine geçici bir INI'de; dosya ilişkilendirmesi (HKCU) ve
    güncelleme denetimi kapalı. (Eskiden dist exe'si `refresh_if_stale` ile kullanıcının
    "Birlikte aç" kaydını kendi yoluna çeviriyor, pencere konumunu gerçek ayarlara yazıyordu.)
  * Pencere ayrı, görünmeyen bir masaüstünde açılır: kullanıcının ekranında pencere belirmez, odak çalınmaz.
  * Yalnızca kendi başlattığı süreç ağacını kapatır; kullanıcının açık Lumina'sına dokunmaz.
Çıkış kodu: 0 = geçti, 1 = başarısız.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = ROOT / "dist" / "Lumina.exe"
WINDOW_TIMEOUT_S = 40
STAY_ALIVE_S = 6
EXIT_TIMEOUT_S = 20
WM_CLOSE = 0x0010
CREATE_UNICODE_ENVIRONMENT = 0x00000400
DESKTOP_ALL = 0x01FF | 0x000F0000
TH32CS_SNAPPROCESS = 0x2

user32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
        ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
        ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
        ("hStdError", wt.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


user32.CreateDesktopW.restype = wt.HANDLE
user32.CreateDesktopW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p]
user32.CloseDesktop.argtypes = [wt.HANDLE]
user32.EnumDesktopWindows.argtypes = [wt.HANDLE, EnumWindowsProc, wt.LPARAM]
k32.CreateProcessW.argtypes = [
    wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD, ctypes.c_void_p,
    wt.LPCWSTR, ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION),
]
k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
SYNCHRONIZE_QUERY = 0x00100000 | 0x1000  # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION


def process_tree(root_pid: int) -> set[int]:
    """root_pid ve torunları. PyInstaller onefile önyükleyicisi pencereyi alt süreçte açar."""
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    children: dict[int, list[int]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    tree, stack = {root_pid}, [root_pid]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in tree:
                tree.add(child)
                stack.append(child)
    return tree


def visible_windows_of(desktop, root_pid: int) -> list[tuple[int, str, tuple[int, int]]]:
    found: list[tuple[int, str, tuple[int, int]]] = []
    pids = process_tree(root_pid)

    def cb(hwnd, _lparam):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            rect = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w, h = rect.right - rect.left, rect.bottom - rect.top
            if w > 100 and h > 100:
                found.append((hwnd, buf.value, (w, h)))
        return True

    user32.EnumDesktopWindows(desktop, EnumWindowsProc(cb), 0)
    return found


def start_hidden(cmd: list[str], cwd: str, env: dict[str, str], desktop_name: str) -> PROCESS_INFORMATION:
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(STARTUPINFOW)
    si.lpDesktop = f"WinSta0\\{desktop_name}"
    pi = PROCESS_INFORMATION()
    block_text = "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0"
    block = ctypes.create_unicode_buffer(block_text, len(block_text))
    cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(cmd))
    if not k32.CreateProcessW(None, cmdline, None, None, False, CREATE_UNICODE_ENVIRONMENT,
                              ctypes.cast(block, ctypes.c_void_p), cwd, ctypes.byref(si), ctypes.byref(pi)):
        raise OSError(ctypes.get_last_error(), "CreateProcessW")
    k32.CloseHandle(pi.hThread)
    return pi


def exit_code(handle) -> int | None:
    if k32.WaitForSingleObject(handle, 0) != 0:
        return None
    code = wt.DWORD()
    k32.GetExitCodeProcess(handle, ctypes.byref(code))
    return code.value


def marks(trace: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        for line in trace.read_text(encoding="utf-8").splitlines():
            name, ms, *_ = line.split("\t")
            out.setdefault(name, float(ms))
    except OSError:
        pass
    return out




def image_path(handle) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    size = wt.DWORD(1024)
    return buf.value if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""


def follow(pi: PROCESS_INFORMATION, desktop, timeout_s: float):
    """Pencereyi açacak süreci bulur: (tanıtıcı, başlatıcı mı) ya da (None, hata).

    Hızlı açılış başlatıcısı uygulamayı açılmış klasörden başlatıp 0 ile çıkar: o zaman alt süreci izlenir (üst
    süreç kimliği, başlatıcı çıktıktan sonra da kayıtlıdır). onefile önyükleyicisi ise açık kalır.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        code = exit_code(pi.hProcess)
        if code is not None:
            if code != 0:
                return None, f"süreç erken sonlandı (çıkış kodu {code})"
            for pid in sorted(process_tree(pi.dwProcessId) - {pi.dwProcessId}):
                h = k32.OpenProcess(SYNCHRONIZE_QUERY, False, pid)
                if h:
                    return h, True
            return None, "başlatıcı çıktı ama uygulama süreci yok"
        if visible_windows_of(desktop, pi.dwProcessId):
            return pi.hProcess, False
        time.sleep(0.05)
    return None, f"{timeout_s} sn içinde uygulama başlamadı"


def run_once(exe: Path, target: Path, env: dict[str, str], trace: Path, log: Path, desk_name: str, desktop) -> bool:
    trace.unlink(missing_ok=True)
    t0 = time.time() * 1000
    pi = start_hidden([str(exe), str(target)], str(exe.parent), env, desk_name)
    app = None
    try:
        app, via_launcher = follow(pi, desktop, WINDOW_TIMEOUT_S)
        if app is None:
            print(f"BAŞARISIZ: {via_launcher}")
            return False
        if via_launcher:
            path = image_path(app)
            runtime = Path(env["LOCALAPPDATA"]) / "Lumina" / "runtime"
            print(f"başlatıcı çıktı; uygulama: {path}")
            if not path.lower().startswith(str(runtime).lower()):
                print("BAŞARISIZ: uygulama açılmış klasörden çalışmıyor")
                return False
        deadline = time.monotonic() + WINDOW_TIMEOUT_S
        windows: list = []
        while time.monotonic() < deadline:
            code = exit_code(app)
            if code is not None:
                print(f"BAŞARISIZ: süreç erken sonlandı (çıkış kodu {code})")
                return False
            windows = visible_windows_of(desktop, pi.dwProcessId)
            if windows and "first_frame" in marks(trace):
                break
            time.sleep(0.25)
        if not windows:
            print(f"BAŞARISIZ: {WINDOW_TIMEOUT_S} sn içinde görünür pencere yok")
            return False
        m = marks(trace)
        print(f"pencere açıldı: {windows[0][1]!r} {windows[0][2][0]}x{windows[0][2][1]}")
        if "first_frame" not in m:
            print("BAŞARISIZ: dosyanın ilk karesi çizilmedi")
            return False
        print(f"ilk kare: {m['first_frame'] - t0:.0f} ms (başlatmadan)")

        time.sleep(STAY_ALIVE_S)
        code = exit_code(app)
        if code is not None:
            print(f"BAŞARISIZ: pencere açıldıktan sonra süreç öldü (çıkış kodu {code})")
            return False
        print(f"{STAY_ALIVE_S} sn sonra hâlâ canlı")

        delta = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        if "Traceback" in delta:
            print("BAŞARISIZ: çökme günlüğüne Traceback düştü:\n" + delta[-1500:])
            return False
        print(f"çökme günlüğü: Traceback yok ({len(delta)} bayt)")

        for hwnd, _title, _size in visible_windows_of(desktop, pi.dwProcessId):
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if k32.WaitForSingleObject(app, EXIT_TIMEOUT_S * 1000) != 0:
            print(f"BAŞARISIZ: pencere kapatıldıktan sonra {EXIT_TIMEOUT_S} sn içinde çıkmadı")
            return False
        leftovers = list(Path(env["TEMP"]).glob("_MEI*"))
        print(f"temiz çıkış (kod {exit_code(app)}), geçici açma klasörü kaldı mı: {bool(leftovers)}")
        return exit_code(app) == 0 and not leftovers
    finally:
        for h in {app, pi.hProcess} - {None}:
            if exit_code(h) is None:  # yalnızca kendi süreç ağacımız
                subprocess.run(["taskkill", "/PID", str(pi.dwProcessId), "/T", "/F"], capture_output=True)
                k32.WaitForSingleObject(h, 5000)
        if app is not None and app != pi.hProcess:
            k32.CloseHandle(app)
        k32.CloseHandle(pi.hProcess)


def _synthetic_photo(folder: Path) -> Path:
    """Dosya verilmediyse açılacak sentetik 1920x1080 JPEG (depoda örnek fotoğraf yok)."""
    from PIL import Image

    path = folder / "smoke.jpg"
    Image.linear_gradient("L").resize((1920, 1080)).convert("RGB").save(path, "JPEG", quality=85)
    return path


def main(argv: list[str]) -> int:
    exe = Path(argv[1]) if len(argv) > 1 else DEFAULT_EXE
    if not exe.is_file():
        print(f"HATA: exe yok: {exe}")
        return 1
    sandbox = Path(tempfile.mkdtemp(prefix="lumina_smoke_"))
    for sub in ("settings", "local", "roaming", "temp"):
        (sandbox / sub).mkdir()
    target = Path(argv[2]) if len(argv) > 2 else _synthetic_photo(sandbox)
    trace = sandbox / "trace.tsv"
    log = sandbox / "local" / "Lumina" / "lumina-crash.log"
    env = dict(os.environ)
    env.pop("LUMINA_RUNTIME_DIR", None)
    env.update(
        LUMINA_SANDBOX=str(sandbox / "settings"),
        LUMINA_STARTUP_TRACE=str(trace),
        LOCALAPPDATA=str(sandbox / "local"),
        APPDATA=str(sandbox / "roaming"),
        TEMP=str(sandbox / "temp"),
        TMP=str(sandbox / "temp"),
    )
    print(f"exe: {exe} ({exe.stat().st_size / 1e6:.1f} MB)  dosya: {target.name}")
    desk_name = f"LuminaSmoke{os.getpid()}"
    desktop = user32.CreateDesktopW(desk_name, None, None, 0, DESKTOP_ALL, None)
    if not desktop:
        print("HATA: gizli masaüstü oluşturulamadı")
        return 1
    ok = False
    try:
        print("-- 1. açılış")
        ok = run_once(exe, target, env, trace, log, desk_name, desktop)
        runtime = sandbox / "local" / "Lumina" / "runtime"
        manifests = list(runtime.glob("*/.lumina-ok")) if runtime.is_dir() else []
        if ok and manifests:
            stamp = manifests[0].stat().st_mtime_ns
            print("-- 2. açılış (başlatıcı: klasör yeniden açılmamalı)")
            ok = run_once(exe, target, env, trace, log, desk_name, desktop)
            if ok and manifests[0].stat().st_mtime_ns != stamp:
                print("BAŞARISIZ: ikinci açılışta klasör yeniden açıldı")
                ok = False
        return 0 if ok else 1
    finally:
        user32.CloseDesktop(desktop)
        shutil.rmtree(sandbox, ignore_errors=True)
        print("SONUÇ:", "GEÇTİ" if ok else "BAŞARISIZ")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
