"""Güncelleme indirme, SHA-256 doğrulama ve süreç içi kurulum.

Güvenlik/sağlamlık kuralları `app.core.update_policy` içindedir: yalnızca HTTPS ve güvenilen alan adı
(yönlendirmeler dahil), boyut sınırı/uyuşması, geçerli SHA-256. İndirilen dosya `%TEMP%\\Lumina_Update_<pid>.pkg`
(sunucudan gelen ad kullanılmaz; paket türü içeriğe bakılarak anlaşılır).

Kurulumda betik, taskkill ve PowerShell YOK: `app.core.self_update` kullanıcının Lumina.exe'sini (hızlı açılış
başlatıcısı, `paths.app_executable()`) yeniden adlandırarak yenisiyle değiştirir. Kurulum iş parçacığında yürür
(arayüz donmaz) ve sonucu sinyalle bildirir; yeniden başlatmada çağıran QApplication.quit() ile TEMİZ çıkar.
"""

from __future__ import annotations

import glob
import hashlib
import os
import re
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import QLockFile, QObject, pyqtSignal

from app.core import self_update as su
from app.core.dll_dir import default_dll_directory
from app.core.paths import app_executable, launcher_path
from app.core.update_policy import (
    MAX_UPDATE_BYTES,
    is_frozen,
    is_local_dev_host,
    normalize_checksum,
    supports_auto_install,
    validate_update_url,
)
from app.core.version import get_version
from app.resources.locales import t

APP_EXE_NAMES = ("Lumina.exe",)
PACKAGE_PREFIX = "Lumina_Update_"
PACKAGE_SUFFIX = ".pkg"
STALE_PACKAGE_SECONDS = 2 * 3600  # bu kadar süredir dokunulmamış, kimsenin sahiplenmediği paket artığı silinir
# Kurulum bu hatalarla başarısız olursa paket SAĞLAMDIR (ortam sorunu: geçici paylaşım ihlali, disk): silinmez, 'Tekrar dene'
# 138 MB'ı yeniden indirmez. Paketin kendisi bozuksa (biçim, mimari, boyut, checksum) silinir.
KEEP_PACKAGE_CODES = frozenset({"swap", "swap2", "write"})
DOWNLOAD_LOCK_NAME = "Lumina.update.lock"
_PACKAGE_NAME = re.compile(rf"{re.escape(PACKAGE_PREFIX)}(\d+){re.escape(PACKAGE_SUFFIX)}")


class _Rejected(Exception):
    """İndirme kuralı reddetti (ör. güvenilmeyen yönlendirme). `code`/`detail` yerel metne çevrilir."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}:{detail}" if detail else code)
        self.code = code
        self.detail = detail


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Yönlendirmeyi izlemeden ÖNCE hedef adresi doğrula (güvenilmeyen sunucuya gitme)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        ok, reason = validate_update_url(newurl)
        if not ok:
            raise _Rejected("url_redirect", reason)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def can_self_update() -> bool:
    """Kendini güncelleyebilir mi: derlenmiş Lumina, Windows ve hızlı açılış başlatıcısıyla açılmış.

    Kaynaktan çalışırken (python -m app) `app_executable()` python.exe'dir: değiştirilirse geliştirme ortamı bozulurdu."""
    return is_frozen() and supports_auto_install() and launcher_path() is not None


def package_path(pid: Optional[int] = None) -> str:
    """İndirilen paketin yolu. Pid, aynı anda açık birkaç Lumina sürecinin aynı dosyaya yazmasını önler."""
    return os.path.join(tempfile.gettempdir(), f"{PACKAGE_PREFIX}{pid if pid is not None else os.getpid()}{PACKAGE_SUFFIX}")


def sweep_stale_packages(keep: Optional[str] = None) -> None:
    """Çöken/öldürülen süreçlerin %TEMP%'te bıraktığı eski paketleri sil (yeni indirme başlarken)."""
    pattern = os.path.join(glob.escape(tempfile.gettempdir()), f"{PACKAGE_PREFIX}*{PACKAGE_SUFFIX}")
    now = time.time()
    for path in glob.glob(pattern):
        try:
            if keep and os.path.abspath(path) == os.path.abspath(keep):
                continue
            if now - os.path.getmtime(path) > STALE_PACKAGE_SECONDS:
                os.remove(path)
        except OSError:
            pass


def package_pid(path: str) -> Optional[int]:
    """`Lumina_Update_<pid>.pkg` adındaki süreç numarası; kendi adımıza uymayan dosyada None."""
    match = _PACKAGE_NAME.fullmatch(os.path.basename(str(path)))
    return int(match.group(1)) if match else None


def _pid_alive_windows(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: öyle bir süreç yok. Erişim engeli = yaşıyor sayılır
    try:
        return kernel32.WaitForSingleObject(handle, 0) != 0  # 0 (WAIT_OBJECT_0): süreç sonlanmış
    finally:
        kernel32.CloseHandle(handle)


def pid_alive(pid: int) -> bool:
    """Süreç çalışıyor mu. Emin olunamıyorsa True: yaşayan bir sürecin dosyası asla silinmesin."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        if os.name == "nt":
            return _pid_alive_windows(pid)  # Windows'ta os.kill(pid, 0) SÜRECİ ÖLDÜRÜR: asla kullanılmaz
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:  # noqa: BLE001 — erişim engeli/beklenmeyen hata: yaşıyor say
        return True


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def package_owner_gone(path: str) -> bool:
    """Bu paketi indiren süreç artık yok mu (ya da biziz)? Adı kendi örüntümüze uymuyorsa da sahibi bilinmez: silinebilir."""
    pid = package_pid(path)
    return pid is None or pid == os.getpid() or not pid_alive(pid)


def sweep_orphan_packages(keep: Optional[str] = None) -> int:
    """%TEMP%'teki `Lumina_Update_<pid>.pkg` dosyalarından SAHİBİ (pid) artık çalışmayanları siler (kaç dosya silindi).

    Çıkışta kurulumu atlayan ya da sert sonlanan bir pencerenin paketi yeni sürüm açılsa da kalmasın. Yaşayan bir sürecin
    (başka Lumina penceresi: indiriyor ya da çıkışta kuracak), kayıtlı hazır paketin (`keep`) ve kendi adımıza uymayan
    dosyaların üstüne gidilmez. Kendi sürecimizin paketi de atlanır."""
    removed = 0
    pattern = os.path.join(glob.escape(tempfile.gettempdir()), f"{PACKAGE_PREFIX}*{PACKAGE_SUFFIX}")
    for path in glob.glob(pattern):
        pid = package_pid(path)
        if pid is None or (keep and same_path(path, keep)) or pid == os.getpid() or pid_alive(pid):
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def default_download_lock_path() -> Path:
    """Kullanıcıya özel kilit dosyası (Windows'ta %TEMP% zaten kullanıcıya özeldir; POSIX'te /tmp ortaktır, uid eklenir)."""
    name = DOWNLOAD_LOCK_NAME
    getuid = getattr(os, "getuid", None)
    if callable(getuid):
        name = f"{DOWNLOAD_LOCK_NAME[:-5]}.{getuid()}.lock"
    return Path(tempfile.gettempdir()) / name


class DownloadLock:
    """Aynı anda tek Lumina süreci sessizce paket indirir (her dosya ayrı süreç/pencere: hepsi aynı ~140 MB'ı indirmesin).

    `tray_owner.TrayOwnership` gibi `QLockFile`: sahibi çalışmayan (çöken/öldürülen) sürecin kilidi kendiliğinden düşer,
    kilidin yaşına bakılmaz (`setStaleLockTime(0)`). `try_acquire()`:
    * True: indirebilirsin (kilit alındı ya da kilit dosyası kullanılamıyor: eski davranışa düşülür, indirme engellenmez);
    * False: başka CANLI bir süreç şu an indiriyor."""

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path is not None else default_download_lock_path()
        self._lock: Optional[QLockFile] = None

    @property
    def held(self) -> bool:
        return self._lock is not None and self._lock.isLocked()

    def try_acquire(self) -> bool:
        if self.held:
            return True
        lock = QLockFile(str(self.path))
        lock.setStaleLockTime(0)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            got = lock.tryLock(0)
        except OSError:
            return True
        if got:
            self._lock = lock
            return True
        return lock.error() != QLockFile.LockError.LockFailedError  # izin/bilinmeyen hata: kilitsiz devam

    def release(self) -> None:
        lock, self._lock = self._lock, None
        if lock is not None:
            try:
                lock.unlock()
            except Exception:  # noqa: BLE001 — çıkışı asla bozma
                pass


class UpdateInstaller(QObject):
    # int imzası 2 GB üstünde taşardı: büyük dosya boyutları için `object`.
    download_progress = pyqtSignal(object, object, float)
    download_complete = pyqtSignal(str)
    download_failed = pyqtSignal(str)
    install_started = pyqtSignal()
    install_complete = pyqtSignal()
    install_failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lang: Optional[str] = None  # None: uygulamanın etkin dili (hata metinleri)
        self.downloaded_file: Optional[str] = None
        # True: sunucunun verdiği SHA-256 ile doğrulandı. False: sunucu sağlama toplamı vermedi.
        self.last_verified = False
        self.last_fail_kind = "download"
        self.swapped = False  # bu süreçte yeni exe yerine kondu (çıkışta temizlik/kurtarma bunu bilmeli)
        self.keep_package: Optional[str] = None  # kayıtlı hazır paket: eski-paket temizliği ona dokunmaz
        # Son kurulum geçici bir ortam sorunuyla (KEEP_PACKAGE_CODES) başarısız oldu: paket silinmedi, 'Tekrar dene' onu kullanabilir
        self.kept_package: Optional[str] = None
        self._cancel = False
        self._install_thread: Optional[threading.Thread] = None
        self.speed_samples: deque = deque()
        self.smoothed_speed = 0.0
        self._last_progress_emit = 0.0

    # ----------------------------------------------------------- metinler
    def message(self, code: str, detail: str = "") -> str:
        """`update_err_<kod>` metni (uygulamanın dilinde); bilinmeyen kodda ayrıntının kendisi."""
        key = f"update_err_{code}"
        text = t(key, self.lang)
        if text == key:
            return detail or code
        return text.format(detail=detail)

    def url_message(self, reason: str) -> str:
        code, _, detail = reason.partition(":")
        return self.message(code, detail)

    def package_message(self, exc: su.PackageError) -> str:
        return self.message(getattr(exc, "code", "other"), getattr(exc, "detail", "") or str(exc))

    # ----------------------------------------------------------- yardımcılar
    def exe_path(self) -> str:
        return str(app_executable())

    def check_writable(self) -> None:
        """Başlatıcının klasörüne yazılabiliyor mu (indirmeden ÖNCE). PackageError(not_writable)."""
        su.check_writable(self.exe_path())

    def recovery_point_exists(self) -> bool:
        """Başlatıcının yanında `.old` var mı: önceki (henüz doğrulanmamış) bir güncelleme ya da başka bir Lumina penceresi
        kurmuş demektir; üstüne yazılırsa geri alma noktası kaybolur."""
        return os.path.exists(su.with_suffix(self.exe_path(), ".old"))

    def cancel_download(self) -> None:
        self._cancel = True

    def cleanup(self) -> None:
        path, self.downloaded_file = self.downloaded_file, None
        if path:
            try:
                os.remove(path)
            except OSError:
                pass

    def _emit_progress(self, downloaded: int, total: int, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_progress_emit) < 0.1:
            return
        self._last_progress_emit = now
        self.download_progress.emit(downloaded, total, self._smooth_speed(self._calculate_speed()))

    def _add_speed_sample(self, bytes_downloaded: int) -> None:
        now = time.time()
        self.speed_samples.append((now, bytes_downloaded))
        cutoff = now - 1.5
        while self.speed_samples and self.speed_samples[0][0] < cutoff:
            self.speed_samples.popleft()

    def _calculate_speed(self) -> float:
        if len(self.speed_samples) < 2:
            return 0.0
        first_time, first_bytes = self.speed_samples[0]
        last_time, last_bytes = self.speed_samples[-1]
        span = last_time - first_time
        if span <= 0:
            return 0.0
        return ((last_bytes - first_bytes) / span) / (1024 * 1024)

    def _smooth_speed(self, new_speed: float) -> float:
        if self.smoothed_speed == 0.0:
            self.smoothed_speed = new_speed
        else:
            self.smoothed_speed = 0.3 * new_speed + 0.7 * self.smoothed_speed
        return self.smoothed_speed

    @staticmethod
    def sha256_file(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _fail(self, code: str, detail: str = "", kind: str = "download") -> bool:
        self.cleanup()
        self.last_fail_kind = kind  # "download" | "checksum": hatanın hangi aşamada olduğu
        self.download_failed.emit(self.message(code, detail))
        return False

    def _cancelled(self) -> bool:
        self.cleanup()
        self.download_failed.emit("cancelled")
        return False

    # --------------------------------------------------------------- indirme
    def download_update(
        self,
        download_url: str,
        expected_size: Optional[int] = None,
        expected_checksum: Optional[str] = None,
    ) -> bool:
        self._cancel = False
        self.last_verified = False
        self.last_fail_kind = "download"
        self.speed_samples.clear()
        self.smoothed_speed = 0.0
        self._last_progress_emit = 0.0
        self.cleanup()

        ok, reason = validate_update_url(download_url)
        if not ok:
            return self._fail(*reason.partition(":")[::2])
        try:
            checksum = normalize_checksum(expected_checksum)
        except ValueError:
            return self._fail("checksum_format")
        try:
            expected_size = int(float(expected_size)) if expected_size else 0
        except (TypeError, ValueError, OverflowError):
            expected_size = 0
        expected_size = max(expected_size, 0)
        if expected_size > MAX_UPDATE_BYTES:
            return self._fail("too_big")

        try:
            sweep_stale_packages(keep=self.keep_package)
            # Yerel ad: başka bir yerden cleanup() çağrılsa da (downloaded_file=None) aşağıdaki adımlar dosyayı bilir.
            target = self.downloaded_file = package_path()
            parsed = urllib.parse.urlparse(download_url)
            ssl_context = ssl._create_unverified_context() if is_local_dev_host(parsed.hostname) else None
            opener = urllib.request.build_opener(
                _SafeRedirectHandler, urllib.request.HTTPSHandler(context=ssl_context)
            )
            cache_buster = int(time.time() * 1000)
            sep = "&" if parsed.query else "?"
            req = urllib.request.Request(f"{download_url}{sep}_t={cache_buster}")
            req.add_header("User-Agent", "AppUpp/1.0 Lumina-Updater")
            req.add_header("Cache-Control", "no-cache, no-store, must-revalidate")
            req.add_header("Pragma", "no-cache")
            req.add_header("Expires", "0")

            with opener.open(req, timeout=30) as response:
                header_length = int(response.headers.get("Content-Length", 0) or 0)
                total_size = header_length or expected_size
                if total_size > MAX_UPDATE_BYTES:
                    return self._fail("too_big")
                downloaded = 0
                if total_size > 0:
                    self._emit_progress(0, total_size, force=True)
                with open(target, "wb") as handle:
                    while True:
                        if self._cancel:
                            handle.close()
                            return self._cancelled()
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > MAX_UPDATE_BYTES:
                            handle.close()
                            return self._fail("too_big")
                        self._add_speed_sample(downloaded)
                        self._emit_progress(downloaded, total_size)
            if self._cancel:  # iptal son parçadan sonra geldi: doğrulama/kurulum yok, iptal olarak bitir
                return self._cancelled()
            self._emit_progress(downloaded, total_size, force=True)

            # Boyut: hem sunucu başlığı hem API'nin bildirdiği boyutla uyuşmalı.
            if header_length and downloaded != header_length:
                return self._fail("incomplete")
            if expected_size and downloaded != expected_size:
                return self._fail("size_mismatch")
            if downloaded <= 0:
                return self._fail("empty")

            if checksum is not None:
                if self.sha256_file(target) != checksum:
                    return self._fail("checksum_mismatch", kind="checksum")
                self.last_verified = True
            else:
                try:  # doğrulanamayan dosyada en azından exe/zip olduğundan emin ol (HTML hata sayfası vb. sorulmasın)
                    su.sniff_package(target)
                except su.PackageError as exc:
                    return self._fail(exc.code, exc.detail)

            self.download_complete.emit(target)
            return True
        except _Rejected as exc:
            return self._fail(exc.code, self.url_message(exc.detail) if exc.detail else "")
        except Exception as exc:  # noqa: BLE001 — ağ/dosya hatası: kullanıcıya satır içi gösterilir
            return self._fail("network", str(exc))

    # ---------------------------------------------------------------- kurulum
    def install_blocking(
        self,
        update_file: str,
        *,
        relaunch: bool,
        visible: bool = True,
        extra_args: Iterable[str] = (),
        expected_checksum: Optional[str] = None,
    ) -> Optional[str]:
        """Paketi kurar. Başarıda None, hatada kullanıcıya gösterilecek (yerel dilde) metin. Çalışan uygulama hatada olduğu gibi kalır.

        relaunch=True: yeni sürüm `--wait-pid` ile başlatılır; çağıran TEMİZ çıkmalı. False: yalnızca değiştirilir (çıkışta sessiz kurulum).

        Hata geçici bir ortam sorunuysa (`KEEP_PACKAGE_CODES`: paylaşım ihlali, yazılamayan disk) paket SİLİNMEZ ve `kept_package`
        olarak bildirilir; paketin kendisi kullanılamıyorsa silinir (tekrar denemede yeniden indirilir)."""
        self.kept_package = None
        try:
            if not update_file or not os.path.exists(update_file):
                return self.message("file_missing")
            if not can_self_update():
                return self.message("source_run")
            if expected_checksum:
                try:  # sunucunun "sha256:" öneki / büyük harfi ile saklanan kayıt da doğru karşılaştırılır
                    expected = normalize_checksum(expected_checksum)
                except ValueError:
                    self._discard(update_file)
                    return self.message("checksum_format")
                if expected is not None and self.sha256_file(update_file) != expected:
                    self._discard(update_file)
                    return self.message("checksum_mismatch")
            exe = self.exe_path()
            if relaunch:
                # Yeni başlatıcı bu sürecin DLL dizinini miras almasın (eski `_internal\VCRUNTIME140.dll`'e bağlanıp eski klasörü kilitlerdi)
                with default_dll_directory():
                    su.install_and_relaunch(
                        update_file, exe, APP_EXE_NAMES, visible=visible, old_version=get_version(), extra_args=extra_args
                    )
            else:
                su.install_package(update_file, exe, APP_EXE_NAMES)
            self.swapped = True
            return None
        except su.PackageError as exc:
            if getattr(exc, "code", "") in KEEP_PACKAGE_CODES and os.path.exists(update_file):
                self.kept_package = update_file
            else:
                self._discard(update_file)
            return self.package_message(exc)
        except Exception as exc:  # noqa: BLE001 — kurulum hatası uygulamayı asla düşürmez
            self._discard(update_file)
            return self.message("other", str(exc))

    @staticmethod
    def _discard(path: str) -> None:
        """Kurulamayan paketi sil (bozuk ya da uyumsuz; tekrar denemede yeniden indirilir). Kurulum ortasında değiştirilmiş exe varsa dokunma."""
        try:
            os.remove(path)
        except OSError:
            pass

    def install_update(
        self,
        installer_path: Optional[str] = None,
        *,
        relaunch: bool = True,
        visible: bool = True,
        extra_args: Iterable[str] = (),
        expected_checksum: Optional[str] = None,
    ) -> bool:
        """Kurulumu arka plan iş parçacığında başlatır; sonuç `install_complete` / `install_failed(metin)` sinyalleriyle gelir."""
        if self._install_thread is not None and self._install_thread.is_alive():
            return False  # aynı anda ikinci kurulum olmaz
        update_file = installer_path or self.downloaded_file or ""
        args = list(extra_args)
        self.install_started.emit()

        def work() -> None:
            error = self.install_blocking(
                update_file, relaunch=relaunch, visible=visible, extra_args=args, expected_checksum=expected_checksum
            )
            if error is None:
                self.install_complete.emit()
            else:
                self.install_failed.emit(error)

        self._install_thread = threading.Thread(target=work, name="LuminaUpdateInstall", daemon=True)
        self._install_thread.start()
        return True

    def install_running(self) -> bool:
        return self._install_thread is not None and self._install_thread.is_alive()

    def wait_install(self, timeout: float = 120.0) -> bool:
        """Kapanırken: yarım kalmış bir değiştirme (exe adlandırılmış ama yenisi yerine konmamış) bırakma."""
        thread = self._install_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
            return not thread.is_alive()
        return True
