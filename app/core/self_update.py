"""Süreç içi kendini güncelleme (betik, taskkill ve PowerShell yok).

Windows'ta çalışan bir exe SİLİNEMEZ ama YENİDEN ADLANDIRILABİLİR. Bu yüzden: yeni exe `<exe>.new` olarak yazılıp
doğrulanır, çalışan exe `<exe>.old` olur, `.new` onun yerine geçer ve yeni süreç `--wait-pid <bizim pid>` ile başlatılır
(yeni süreç, biz çıkana kadar bekler: tek kopya kilidi ve tepsi simgesi çakışmasın). Eski sürüm, yeni sürüm yeterince
çalışana dek `.old` olarak saklanır; yeni sürüm açılışta iki kez ölürse eskisi geri gelir ve bozuk sürüm bir daha önerilmez.

Paket ya doğrudan bir exe ya da içinde exe bulunan bir zip'tir (içeriğe bakılır, uzantıya değil). Zip'ten yalnızca o
exe okunur; başka hiçbir dosya yazılmaz.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import zipfile
from typing import Iterable, Optional

MAX_EXE_BYTES = 600 * 1024 * 1024
SWAP_RETRIES = 3                  # geçici paylaşım ihlalinde (WinError 32/33: ör. antivirüs taraması) yeniden deneme sayısı
SWAP_RETRY_DELAY = 0.2            # denemeler arası bekleme (sn)
_SHARING_ERRORS = (32, 33)        # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
MAX_PROBATION_STARTS = 2          # yeni sürümün "bozuk" sayılmasından önceki açılış sayısı (ilki kurulum sonrası yeniden başlatma)
HEALTHY_AFTER_SECONDS = 60        # yeni sürüm bu kadar çalıştıktan sonra eskisi silinir
WAIT_PID_TIMEOUT = 15.0
EXIT_WATCHDOG_SECONDS = 10.0       # güncelleme için çıkış başladıktan sonra bu kadar içinde bitmezse süreç sert sonlandırılır (< WAIT_PID_TIMEOUT)

_ZIP_MAGIC = b"PK\x03\x04"
_SIDE_SUFFIXES = (".old", ".new", ".boot", ".bad")


class PackageError(Exception):
    """İndirilen paket kullanılamaz (biçim, mimari, boyut) ya da exe değiştirilemedi. İleti Türkçedir ve günlüğe/yedek
    olarak gösterilebilir; arayüz kendi dilinde anlatabilsin diye `code` (makine için neden: read, not_package, too_big,
    no_exe, bad_zip, write, not_pe, arch, not_writable, swap, swap2, spawn) ve `detail` (değişken kısım: işletim sistemi
    hatası, klasör) ayrıca taşınır."""

    def __init__(self, message: str, code: str = "", detail: str = ""):
        super().__init__(message)
        self.code = code
        self.detail = detail


def with_suffix(path: str, suffix: str) -> str:
    return f"{path}{suffix}"


# ------------------------------------------------------------------ paket
def sniff_package(path: str) -> str:
    """'exe' (MZ ile başlar) ya da 'zip' (PK) — dosya adına bakılmaz (sunucu adresi uzantısız olabilir)."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(4)
    except OSError as e:
        raise PackageError(f"Paket okunamadı: {e}", "read", str(e)) from e
    if head[:2] == b"MZ":
        return "exe"
    if head == _ZIP_MAGIC:
        return "zip"
    raise PackageError("İndirilen dosya bir exe ya da zip değil", "not_package")


def pe_machine(head: bytes) -> Optional[int]:
    """Windows exe'nin hedef mimarisi (IMAGE_FILE_HEADER.Machine): 0x8664 x64, 0xAA64 ARM64, 0x014C x86. PE değilse None."""
    if len(head) < 0x40 or head[:2] != b"MZ":
        return None
    pe = int.from_bytes(head[0x3C:0x40], "little")
    if pe < 0 or pe + 6 > len(head) or head[pe:pe + 4] != b"PE\0\0":
        return None
    return int.from_bytes(head[pe + 4:pe + 6], "little")


def _read_head(path: str, size: int = 4096) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(size)


def _copy_limited(src, dst, limit: int) -> int:
    total = 0
    while True:
        chunk = src.read(1024 * 1024)
        if not chunk:
            return total
        total += len(chunk)
        if total > limit:
            raise PackageError("Paket izin verilen boyuttan büyük", "too_big")
        dst.write(chunk)


def _zip_member(archive: zipfile.ZipFile, exe_names: Iterable[str]) -> zipfile.ZipInfo:
    wanted = {n.lower() for n in exe_names}
    best = None
    for info in archive.infolist():
        if info.is_dir():
            continue
        parts = info.filename.replace("\\", "/").split("/")
        if parts[-1].lower() in wanted and (best is None or len(parts) < len(best[0])):
            best = (parts, info)
    if best is None:
        raise PackageError("Pakette uygulamanın exe dosyası yok", "no_exe")
    return best[1]


def stage_new_exe(package_path: str, current_exe: str, exe_names: Iterable[str], max_bytes: int = MAX_EXE_BYTES) -> str:
    """Paketten yeni exe'yi `<current>.new` olarak yazar ve doğrular (PE başlığı, çalışan exe ile aynı mimari). Yolu döndürür."""
    new = with_suffix(current_exe, ".new")
    kind = sniff_package(package_path)
    _remove(new)
    try:
        try:
            with open(new, "wb") as out:
                if kind == "exe":
                    with open(package_path, "rb") as src:
                        _copy_limited(src, out, max_bytes)
                else:
                    try:
                        with zipfile.ZipFile(package_path) as archive:
                            info = _zip_member(archive, exe_names)
                            if info.file_size > max_bytes:
                                raise PackageError("Paket izin verilen boyuttan büyük", "too_big")
                            with archive.open(info) as src:
                                _copy_limited(src, out, max_bytes)
                    except zipfile.BadZipFile as e:
                        raise PackageError(f"Zip dosyası bozuk: {e}", "bad_zip", str(e)) from e
        except OSError as e:
            raise PackageError(f"Yeni sürüm uygulamanın klasörüne yazılamadı: {e}", "write", str(e)) from e
        theirs = pe_machine(_read_head(new))
        if theirs is None:
            raise PackageError("Paketteki dosya geçerli bir Windows uygulaması değil", "not_pe")
        try:
            mine = pe_machine(_read_head(current_exe))
        except OSError:
            mine = None
        if mine is not None and mine != theirs:
            raise PackageError(f"Paket başka bir işlemci mimarisi için hazırlanmış ({theirs:#06x}, bu bilgisayar {mine:#06x})",
                               "arch", f"{theirs:#06x} / {mine:#06x}")
    except BaseException:
        _remove(new)
        raise
    return new


def check_writable(exe: str) -> None:
    """Güncelleme exe'nin yanına yazar; klasör yazılamıyorsa indirmeye başlamadan anlaşılsın (PackageError)."""
    folder = os.path.dirname(os.path.abspath(exe))
    probe = os.path.join(folder, f".update-probe-{os.getpid()}")
    try:
        with open(probe, "wb") as handle:
            handle.write(b"x")
        os.remove(probe)
    except OSError as e:
        raise PackageError(f"Uygulamanın bulunduğu klasöre yazılamıyor ({folder}): {e}", "not_writable", folder) from e


# ------------------------------------------------------------------ değiştirme ve başlatma
def _replace(src: str, dst: str) -> None:
    """`os.replace`; yalnızca GEÇİCİ paylaşım ihlalinde (WinError 32/33: antivirüs/dizinleyici dosyayı o an tarıyor) kısa aralıklarla
    yeniden dener. Başka hatalar (izin yok, dosya yok...) hemen yükseltilir."""
    for attempt in range(SWAP_RETRIES + 1):
        try:
            os.replace(src, dst)
            return
        except OSError as e:
            if getattr(e, "winerror", None) not in _SHARING_ERRORS or attempt >= SWAP_RETRIES:
                raise
            time.sleep(SWAP_RETRY_DELAY)


def swap_exe(current: str, new: str) -> None:
    """Çalışan exe'yi `.old` yapar, `.new`'i yerine koyar. İkinci adım başarısız olursa eskisi geri gelir.
    Her adım geçici paylaşım ihlalinde `SWAP_RETRIES` kez yeniden denenir (paketi çağıran silmez: 'Tekrar dene' yeniden indirmesin)."""
    old = with_suffix(current, ".old")
    try:
        _replace(current, old)
    except OSError as e:
        _remove(new)
        raise PackageError(f"Çalışan uygulama dosyası değiştirilemedi: {e}", "swap", str(e)) from e
    try:
        _replace(new, current)
    except OSError as e:
        try:
            _replace(old, current)
        except OSError:
            pass  # eski dosya `.old` olarak kalır; en azından bir kopya sağlam
        _remove(new)
        raise PackageError(f"Yeni sürüm yerine konamadı: {e}", "swap2", str(e)) from e


def clean_env() -> dict:
    """PyInstaller'ın ortam değişkenleri yeni sürüme geçmesin (yeni exe kendi geçici klasörünü açmalı)."""
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("_MEI", "_PYI")) or key in ("PYTHONHOME", "PYTHONPATH"):
            del env[key]
    return env


def spawn(exe: str, args: Iterable[str]) -> None:
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen(
            [exe, *args], cwd=os.path.dirname(os.path.abspath(exe)), env=clean_env(), creationflags=flags,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
        )
    except OSError as e:
        raise PackageError(f"Yeni sürüm başlatılamadı: {e}", "spawn", str(e)) from e


def arm_exit_watchdog(seconds: float = EXIT_WATCHDOG_SECONDS) -> None:
    """Güncelleme için çıkış başladı ve yeni sürüm bizi bekliyor: çıkış bir nedenle takılırsa (kapanmayan iş parçacığı, çıkışı
    reddeden pencere...) süreç `seconds` sonra sert sonlandırılır; yoksa yeni sürüm 15 sn sonra vazgeçer ve eski sürüm sonsuza
    dek yaşar. Yalnızca paketlenmiş uygulamada çağrılır."""
    import threading
    timer = threading.Timer(seconds, lambda: os._exit(0))
    timer.daemon = True
    timer.start()


def relaunch_args(visible: bool, old_version: str = "", pid: Optional[int] = None) -> list[str]:
    """Güncelleme sonrası yeni sürümün komut satırı: pencere açıktıysa açık gelir, tepsideyse tepside kalır."""
    args = ["--wait-pid", str(pid if pid is not None else os.getpid())]
    if old_version:
        args += ["--updated", old_version]
    args.append("--show-window" if visible else "--minimized")
    return args


def install_package(package_path: str, current_exe: str, exe_names: Iterable[str], *, max_bytes: int = MAX_EXE_BYTES) -> None:
    """Paketi kurar (hazırla + değiştir) ama yeni sürümü BAŞLATMAZ (çıkışta sessiz kurulum). Hata PackageError."""
    new = stage_new_exe(package_path, current_exe, exe_names, max_bytes)
    swap_exe(current_exe, new)
    _remove(package_path)


def install_and_relaunch(package_path: str, current_exe: str, exe_names: Iterable[str], *, visible: bool,
                         old_version: str = "", max_bytes: int = MAX_EXE_BYTES, extra_args: Iterable[str] = ()) -> None:
    """Paketi kurar ve yeni sürümü başlatır. Dönerse çağıran uygulamadan ÇIKMALIDIR (yeni süreç bizi bekliyor).
    Hata PackageError olarak gelir; bu durumda çalışan uygulama olduğu gibi kalır.
    `extra_args`: yeni sürümün komut satırının sonuna eklenir (ör. açık dosya)."""
    install_package(package_path, current_exe, exe_names, max_bytes=max_bytes)
    spawn(current_exe, [*relaunch_args(visible, old_version), *extra_args])


# ------------------------------------------------------------------ açılış tarafı
def flag_value(argv: list[str], name: str) -> Optional[str]:
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(name + "="):
            return arg[len(name) + 1:]
    return None


def updated_from(argv: Optional[list[str]] = None) -> Optional[str]:
    """Bu açılış bir güncellemenin yeniden başlatması mı? Öyleyse eski sürüm numarası."""
    return flag_value(argv if argv is not None else sys.argv, "--updated")


def wants_window(argv: Optional[list[str]] = None) -> bool:
    """Güncelleme öncesi pencere açıktıysa yeni sürüm de pencereyi açar (ayardaki 'simge durumunda başlat'ı geçersiz kılar)."""
    return "--show-window" in (argv if argv is not None else sys.argv)


def wait_for_previous_instance(argv: Optional[list[str]] = None, timeout: float = WAIT_PID_TIMEOUT) -> bool:
    """`--wait-pid N` verildiyse N çıkana kadar bekler (en çok `timeout` sn). Uygulama kilidi alınmadan ÖNCE çağrılmalı."""
    value = flag_value(argv if argv is not None else sys.argv, "--wait-pid")
    if not value or not value.isdigit() or os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x00100000, False, int(value))  # SYNCHRONIZE
    if not handle:
        return True  # süreç zaten yok
    try:
        return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == 0
    finally:
        kernel32.CloseHandle(handle)


class Guard:
    NORMAL = "normal"
    PROBATION = "probation"
    ROLLED_BACK = "rolled_back"


def startup_guard(exe: str, version: str) -> str:
    """Güncellemeden sonraki ilk açılışlar denemedir. Yeni sürüm iki kez açılışta ölürse eskisi geri gelir (ROLLED_BACK:
    çağıran eski exe'yi başlatıp çıkar) ve bu sürüm `.skip` ile işaretlenir."""
    old = with_suffix(exe, ".old")
    if not os.path.exists(old):
        return Guard.NORMAL
    boot = with_suffix(exe, ".boot")
    try:
        with open(boot, "r", encoding="utf-8") as handle:
            starts = int(handle.read().strip() or 0) + 1
    except (OSError, ValueError):
        starts = 1
    if starts <= MAX_PROBATION_STARTS:
        _write(boot, str(starts))
        return Guard.PROBATION
    bad = with_suffix(exe, ".bad")
    _remove(bad)
    try:
        os.replace(exe, bad)
    except OSError:
        return Guard.PROBATION
    try:
        os.replace(old, exe)
    except OSError:
        try:
            os.replace(bad, exe)
        except OSError:
            pass
        return Guard.PROBATION
    _write(with_suffix(exe, ".skip"), version)
    _remove(boot)
    return Guard.ROLLED_BACK


def skipped_version(exe: str) -> Optional[str]:
    """Bu bilgisayarda geri alınmak zorunda kalan sürüm (bir daha önerilmez)."""
    try:
        with open(with_suffix(exe, ".skip"), "r", encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError:
        return None
    return value or None


def confirm_healthy(exe: str) -> None:
    """Çalışan sürüm kendini kanıtladı: güncellemenin bıraktığı `.old` ve yan dosyalar silinir."""
    for suffix in _SIDE_SUFFIXES:
        _remove(with_suffix(exe, suffix))


# ------------------------------------------------------------------ yardımcılar
def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _write(path: str, text: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        pass
