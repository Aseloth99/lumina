"""Uygulama genelinde tek güncelleme denetleyicisi ve akışı.

İki akış var (Lumina bir görüntüleyicidir: tepsi yok, pencere kapanınca süreç biter):

* Sessiz (varsayılan, "Güncellemeleri otomatik kur"): yeni sürüm bulununca arka planda indirilip doğrulanır, Lumina
  KAPANIRKEN kurulur. Yeniden başlatma yok, pencere/kutu yok. İndirme yarıda kalırsa `last_update_check` damgalanmaz
  (24 saat denetimsiz kalmasın); tamamlanan paket (yol + sürüm + sha) ayarlarda saklanır, sonraki açılışta kullanılır.
* Elle: başlıktaki "Güncelle · vX" → güncelleme penceresi → "Şimdi güncelle" → indir → kur (iş parçacığında) →
  uygulama TEMİZ çıkar ve yeni sürüm (açık görüntüyle) başlar. Hatalar pencerede satır içi gösterilir.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
from dataclasses import dataclass
from functools import partial
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication

from app.core import self_update as su
from app.core.settings_store import make_settings
from app.core.update_installer import (
    PACKAGE_PREFIX,
    PACKAGE_SUFFIX,
    DownloadLock,
    UpdateInstaller,
    can_self_update,
    package_owner_gone,
    same_path,
    sweep_orphan_packages,
)
from app.core.update_policy import normalize_checksum, supports_auto_install, validate_update_url
from app.core.updater import UpdateChecker, _as_bool, _skipped_version
from app.resources.locales import t
from app.ui.update_dialog import UpdateDialog

_log = logging.getLogger(__name__)

READY_KEY = "update_ready"  # QSettings: hazır paketin kalıcı kaydı (JSON)


class _DownloadThread(QObject):
    finished = pyqtSignal()

    def __init__(self, installer: UpdateInstaller, url: str, size, checksum, parent=None):
        super().__init__(parent)
        self.installer = installer
        self.url = url
        self.size = size if size and size > 0 else None
        self.checksum = checksum or None
        self._thread: threading.Thread | None = None
        self._running = False

    def isRunning(self) -> bool:  # noqa: N802
        return self._running

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.installer.download_update(self.url, self.size, self.checksum)
        finally:
            self._running = False
            self.finished.emit()

    def wait(self, timeout_ms: int = 0) -> bool:
        if self._thread is not None and self._thread.is_alive():
            timeout_sec = (timeout_ms / 1000.0) if timeout_ms > 0 else None
            self._thread.join(timeout=timeout_sec)
            return not self._thread.is_alive()
        return True


@dataclass
class _Ready:
    """İndirilmiş VE doğrulanmış paket; kurulacağı sürüme bağlıdır (daha yeni sürüm sunulunca eski dosya kurulmaz)."""

    path: str
    version: str
    checksum: str
    size: int = 0
    mandatory: bool = False


def _is_packaged() -> bool:
    """Kendini güncelleyebilen derlenmiş Lumina mı (exe + hızlı açılış başlatıcısı)? Sessiz indirme/kurulum yalnızca onda."""
    return can_self_update()


def _is_our_package(path: str) -> bool:
    """Ayarlardan okunan yol yalnızca kendi geçici paket adımıza benziyorsa silinir/kurulur (ayar dosyası kurcalanmış olabilir)."""
    try:
        folder = os.path.abspath(os.path.dirname(path))
        name = os.path.basename(path)
        return (
            folder == os.path.abspath(tempfile.gettempdir())
            and name.startswith(PACKAGE_PREFIX)
            and name.endswith(PACKAGE_SUFFIX)
        )
    except (TypeError, ValueError):
        return False


def _remove_quietly(path: Optional[str]) -> None:
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


class UpdateService(QObject):
    notice_received = pyqtSignal(object)  # duyuru satırı (ana pencere gösterir)
    # (kaynak: "menu"/"settings", sonuç: "ok"/"new"/"fail", metin) — elle denetim pop-up açmaz, arayüz gösterir
    manual_result = pyqtSignal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.checker = UpdateChecker(self)
        self.installer = UpdateInstaller(self)
        self.dialog: Optional[UpdateDialog] = None
        self._dialog_parent = None
        self._pending: Optional[dict] = None
        self._thread: Optional[_DownloadThread] = None
        self._job: Optional[dict] = None  # çalışan indirme: {"version", "checksum", "silent"}
        self._restart_silent = False  # çalışan indirme daha yeni bir sürüm yüzünden iptal edildi: bitince yenisi başlasın
        self._ready: Optional[_Ready] = None  # sessizce indirilip doğrulanmış; Lumina kapanınca kurulur
        self._installing = False  # elle kurulum (iş parçacığı) sürüyor
        self._installing_on_exit = False
        self._unverified_path: Optional[str] = None  # sağlama toplamsız indirme: kullanıcı "Yine de kur" diyene dek bekler
        self._manual_origin: Optional[str] = None
        self._manual_failed = False
        # Her Lumina penceresi ayrı süreçtir: aynı anda yalnızca biri sessizce indirir (diğerleri ~140 MB'ı yinelemez)
        self._download_lock = DownloadLock()
        self.checker.update_available.connect(self._on_available)
        self.checker.check_complete.connect(self._on_check_complete)
        self.checker.check_failed.connect(self._on_check_failed)
        self.checker.notice_received.connect(self.notice_received.emit)
        self.installer.download_progress.connect(self._on_progress)
        self.installer.download_complete.connect(self._on_complete)
        self.installer.download_failed.connect(self._on_failed)
        self.installer.install_complete.connect(self._on_install_complete)
        self.installer.install_failed.connect(self._on_install_failed)
        self._restore_ready()

    def start(self) -> None:
        self._sweep_orphan_packages()
        self.checker.start()

    def _sweep_orphan_packages(self) -> None:
        """Açılışta (arka planda): sahibi artık çalışmayan pencerenin `Lumina_Update_<pid>.pkg` artığı silinir (çıkışta kurulumu
        atlayan ya da sert sonlanan pencere ~140 MB bıraktıysa). Yaşayan pencerelerin ve kayıtlı hazır paketin dosyasına dokunulmaz."""
        if not _is_packaged():
            return
        try:
            threading.Thread(
                target=sweep_orphan_packages, args=(self.installer.keep_package,), name="LuminaUpdateSweep", daemon=True
            ).start()
        except RuntimeError:  # iş parçacığı açılamadı: bir sonraki açılışta
            pass

    def shutdown(self) -> None:
        self.checker.stop()
        worker = getattr(self.checker, "_worker", None)
        if worker is not None and worker.isRunning():
            worker.wait(2000)
        self.installer.cancel_download()
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(2000)
        self.installer.wait_install()  # elle kurulum sürerken çıkılırsa yarım kalmış değiştirme bırakma
        try:
            self.install_on_exit()
        finally:
            self._download_lock.release()

    # ------------------------------------------------------------------ otomatik kurulum
    @staticmethod
    def auto_install_setting(settings) -> bool:
        """"Güncellemeleri otomatik kur" kutusunun değeri (varsayılan: açık)."""
        return _as_bool(settings.value("auto_update_install", True))

    @staticmethod
    def auto_install_enabled() -> bool:
        settings = make_settings()
        return _as_bool(settings.value("auto_update_check", True)) and UpdateService.auto_install_setting(settings)

    @staticmethod
    def _silent_allowed(mandatory: bool) -> bool:
        # Kaynaktan (geliştirme) çalışırken kurulum hedefi python.exe olurdu: sessiz indirme/kurulum yok.
        if not _is_packaged():
            return False
        return supports_auto_install() and (mandatory or UpdateService.auto_install_enabled())

    def _wants_silent(self) -> bool:
        pending = self._pending or {}
        if not self._silent_allowed(bool(pending.get("mandatory"))):
            return False
        try:  # sağlama toplamı şart: doğrulanamayan paket sessizce kurulmaz, o halde indirmeye de gerek yok
            return normalize_checksum(pending.get("checksum")) is not None
        except ValueError:
            return False

    def _maybe_silent_download(self) -> None:
        """Yeni sürüm bulununca (otomatik kur açıksa) arka planda indir; kurulum Lumina kapanırken yapılır."""
        if not self._pending:
            return
        if not self._wants_silent():
            self.checker.mark_checked()  # indirilecek bir şey yok: denetim tamam
            return
        version = self._pending_version()
        if self._is_declined(version, bool(self._pending.get("mandatory"))):
            # Kullanıcı bu sürüme "Daha Sonra" dedi (ya da indirmeyi iptal etti): kendiliğinden indirilip kurulmaz. Başlıktaki
            # düğme kalır, elle "Şimdi Güncelle" çalışır; daha yeni bir sürüm gelirse bu ret geçersiz olur.
            self.checker.mark_checked()
            return
        if self._ready is not None and self._ready.version == version and os.path.exists(self._ready.path):
            self.checker.mark_checked()
            return
        if self._thread is not None and self._thread.isRunning():
            return  # aynı sürümün indirmesi sürüyor (farklı sürümse _on_available iptal etti, bitince yeniden başlar)
        try:
            self.installer.check_writable()  # yazılamayan klasöre bandwidth harcama
        except su.PackageError as exc:
            _log.warning("Sessiz güncelleme atlandı: %s", exc)
            self.checker.mark_checked()
            return
        if not self._download_lock.try_acquire():
            # Başka bir Lumina penceresi aynı paketi şu an indiriyor (kurulumu çıkışta ilk kapanan yapar). Damga ATILMAZ:
            # o pencere indirmeyi bitirmeden kapanırsa sonraki açılış yeniden denetler.
            _log.info("Güncellemeyi başka bir Lumina penceresi indiriyor: bu pencere indirmeyecek")
            return
        self._begin_download(silent=True)

    def _is_declined(self, version: str, mandatory: bool) -> bool:
        """Kullanıcı bu sürüme "Daha Sonra" dedi (ya da indirmeyi iptal etti) ve sürüm zorunlu değil: otomatik indirilmez/kurulmaz.
        Sürüm numarasıyla karşılaştırılır: daha yeni bir sürüm gelince ret kendiliğinden geçersiz olur."""
        if mandatory:
            return False
        try:
            declined = str(make_settings().value("declined_update_version", "") or "").strip().lstrip("vV")
        except Exception:  # noqa: BLE001 — okunamayan ayar: reddedilmemiş say
            return False
        return bool(declined) and declined == str(version).strip().lstrip("vV")

    def install_on_exit(self) -> None:
        """Lumina kapanırken: indirilmiş ve doğrulanmış güncellemeyi yeniden başlatmadan kur (yeni sürüm bir sonraki açılışta)."""
        ready = self._ready
        if ready is None or self._installing_on_exit or self._installing or self.installer.swapped:
            return
        if not os.path.exists(ready.path):
            return
        if self._is_declined(ready.version, ready.mandatory):
            # "Daha Sonra" denen sürüm çıkışta kurulmaz (elle güncellemeyle kurulabilirdi); paketi %TEMP%'te bırakma.
            _log.info("Kullanıcı bu güncellemeyi erteledi: çıkışta kurulmayacak")
            self._clear_ready(delete=True)
            return
        if not self._silent_allowed(ready.mandatory):
            return
        if not self._still_newer(ready.version):
            self._clear_ready(delete=True)
            return
        if self.installer.recovery_point_exists():
            # Başka bir Lumina penceresi güncellemeyi zaten kurmuş (ya da önceki güncelleme henüz doğrulanmadı): üstüne
            # yazmak geri alma noktasını (.old) kaybettirirdi.
            _log.info("Çıkışta güncelleme atlandı: önceki güncelleme henüz doğrulanmadı")
            self._release_skipped_package(ready)
            return
        self._installing_on_exit = True
        self._ready = None
        self._clear_ready(delete=False)
        try:
            error = self.installer.install_blocking(ready.path, relaunch=False, expected_checksum=ready.checksum)
        except Exception as exc:  # noqa: BLE001 — kapanışı asla bozma
            _log.exception("çıkışta güncelleme kurulamadı")
            error = str(exc)
        if error is not None:
            _log.warning("Sessiz güncelleme kurulamadı: %s", error)
            _remove_quietly(ready.path)  # çıkışta 'Tekrar dene' yok: geçici hatada da kendi paketimiz bırakılmaz
            self.installer.kept_package = None

    def _release_skipped_package(self, ready: _Ready) -> None:
        """Çıkışta kurulum atlandı. Ayardaki kayıt hâlâ BU pakete işaret ediyorsa (önceki güncelleme doğrulanmadı) sonraki açılış
        onu kullanır, kalır. Kayıt silinmişse (başka pencere aynı güncellemeyi kurdu) paket sahipsizdir: kendi paketimiz silinir.
        Sahibi hâlâ çalışan başka bir pencerenin paketine dokunulmaz."""
        saved = self._saved_ready_path()
        if saved is not None and same_path(saved, ready.path):
            return
        if _is_our_package(ready.path) and package_owner_gone(ready.path):
            _remove_quietly(ready.path)
        self._ready = None
        self.installer.keep_package = None

    @staticmethod
    def _saved_ready_path() -> Optional[str]:
        try:
            raw = make_settings().value(READY_KEY, "")
            return str(json.loads(str(raw))["path"]) if raw else None
        except Exception:  # noqa: BLE001 — bozuk kayıt: yok say
            return None

    def _still_newer(self, version: str) -> bool:
        if self.checker._compare_versions(version, self.checker.current_version) <= 0:
            return False
        skipped = _skipped_version()
        return not (skipped and skipped.lstrip("vV") == version)

    # ------------------------------------------------------------------ hazır paketin kalıcı kaydı
    def _set_ready(self, path: str, version: str, checksum: str, mandatory: bool, persist: bool = True) -> None:
        """`persist=False`: yalnızca bellekte (bu oturum); kalıcı kayıt yok, süreç bitince bayat paket süpürmesi dosyayı temizler."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        self._ready = _Ready(path, version, checksum, size, mandatory)
        self.installer.keep_package = path
        if not persist or self._is_declined(version, mandatory):
            return  # reddedilen sürümün paketi bir sonraki açılışa taşınmaz
        try:
            make_settings().setValue(READY_KEY, json.dumps(self._ready.__dict__))
        except (OSError, ValueError, TypeError):
            pass

    def _clear_ready(self, delete: bool) -> None:
        ready, self._ready = self._ready, None
        self.installer.keep_package = None
        # Yalnızca kendi geçici paketimiz VE sahibi ölmüş/biz isek: yaşayan başka bir Lumina penceresinin paketi silinmez
        if delete and ready is not None and _is_our_package(ready.path) and package_owner_gone(ready.path):
            _remove_quietly(ready.path)
        try:
            make_settings().remove(READY_KEY)
        except (OSError, ValueError):
            pass

    def _restore_ready(self) -> None:
        """Önceki açılışta indirilip kurulamamış (ör. süreç öldürüldü) paketi yeniden kullan."""
        try:
            raw = make_settings().value(READY_KEY, "")
            if not raw:
                return
            data = json.loads(str(raw))
            ready = _Ready(
                path=str(data["path"]),
                version=str(data["version"]),
                checksum=str(data["checksum"]),
                size=int(data.get("size", 0)),
                mandatory=bool(data.get("mandatory", False)),
            )
            ok = (
                _is_our_package(ready.path)
                and os.path.isfile(ready.path)
                and os.path.getsize(ready.path) == ready.size
                and normalize_checksum(ready.checksum) is not None
                and self._still_newer(ready.version)
                and not self._is_declined(ready.version, ready.mandatory)  # "Daha Sonra" denen sürüm bir sonraki açılışa taşınmaz
            )
        except Exception:  # noqa: BLE001 — bozuk kayıt: yok say
            ok = False
            ready = None
        self._ready = ready
        if not ok:
            self._clear_ready(delete=True)  # geçersiz/eski kayıt: kendi geçici paketimizse dosyası da silinir
        elif ready is not None:
            self.installer.keep_package = ready.path

    # ------------------------------------------------------------------ pencere düğmesi
    def _sync_windows(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        for widget in app.topLevelWidgets():
            self.sync_window(widget)

    def sync_window(self, window) -> None:
        if hasattr(window, "mark_update_available"):
            version = self._pending_version() if self._pending else ""
            window.mark_update_available(bool(version), version)

    # ------------------------------------------------------------------ elle denetim (pop-up yok)
    def check_now(self, origin: str = "menu") -> None:
        if self._pending and origin == "menu":
            self.open_pending()
            return
        self._manual_origin = origin
        self._manual_failed = False
        self.checker.check_for_updates(show_message=True)

    def _on_check_failed(self) -> None:
        self._manual_failed = True

    def _on_check_complete(self, has_update: bool) -> None:
        origin, self._manual_origin = self._manual_origin, None
        failed, self._manual_failed = self._manual_failed, False
        if origin is None:
            return  # arka plan denetimi: sessiz
        if failed:
            self.manual_result.emit(origin, "fail", t("update_status_failed"))
        elif has_update and self._pending:
            self.manual_result.emit(origin, "new", t("update_new_version").format(self._pending_version()))
        else:
            self.manual_result.emit(
                origin, "ok", t("update_status_current").format(self.checker.get_current_version())
            )

    def _parent_window(self):
        app = QApplication.instance()
        return app.activeWindow() if app is not None else None

    def _on_available(self, version, url, notes, mandatory, size, checksum) -> None:
        version = str(version)
        self._pending = {
            "version": version,
            "url": url,
            "notes": notes,
            "mandatory": mandatory,
            "size": size,
            "checksum": checksum,
        }
        # Pop-up YOK: sürüm başlık çubuğundaki "Güncelle · vX" düğmesinde görünür; ayrıntı için tıklanır.
        # Daha eski bir sürüm için indirilmiş/indirilen paket artık kurulmamalı (sürüme bağlı).
        if self._ready is not None and self._ready.version != version:
            self._clear_ready(delete=True)
        if self._thread is not None and self._thread.isRunning() and (self._job or {}).get("version") != version:
            self._restart_silent = True
            self.installer.cancel_download()
        self._sync_windows()
        self._maybe_silent_download()

    # ------------------------------------------------------------------ indirme
    def _begin_download(self, silent: bool = False) -> None:
        pending = self._pending
        if not pending:
            return
        self._job = {"version": str(pending["version"]), "checksum": pending["checksum"] or "", "silent": bool(silent)}
        # Elle indirmede de kilit tutulur (başka pencerelerin sessiz indirmesi yinelenmesin); alınamazsa kullanıcı beklemez, indirir.
        self._download_lock.try_acquire()
        thread = _DownloadThread(self.installer, pending["url"], pending["size"], pending["checksum"])
        self._thread = thread
        thread.finished.connect(partial(self._on_thread_finished, thread))
        thread.start()

    def _on_thread_finished(self, thread) -> None:
        if thread is not self._thread:
            return
        self._job = None
        self._download_lock.release()  # indirme bitti (başarılı/başarısız/iptal): diğer pencereler denetleyebilir
        if self._restart_silent:
            self._restart_silent = False
            self._maybe_silent_download()

    def _start_download(self) -> None:
        """Kullanıcı pencerede "Şimdi güncelle" / "Tekrar dene" dedi: indir (hazır paket varsa onu kullan) ve kur."""
        pending = self._pending
        if not pending:
            return
        if not supports_auto_install():
            self._open_download_page()
            return
        if not _is_packaged():
            self._dialog_error(self.installer.message("source_run"))
            return
        if self._installing:
            return
        version = self._pending_version()
        ready = self._ready
        if ready is not None and ready.version == version and os.path.exists(ready.path):
            self._install_package(ready.path, ready.checksum, verify=True)
            return
        if self._thread is not None and self._thread.isRunning():
            if self._job is not None:
                self._job["silent"] = False  # arka plandaki indirmeye katıl: ilerleme pencereye akar, bitince kurulur
            return
        try:
            self.installer.check_writable()  # indirmeden ÖNCE: boşa indirme olmasın
        except su.PackageError as exc:
            self._dialog_error(self.installer.package_message(exc))
            return
        self._begin_download(silent=False)

    def _dialog_error(self, message: str) -> None:
        if self.dialog is not None:
            self.dialog.show_error(message)
        else:
            _log.warning("Güncelleme: %s", message)

    def _open_download_page(self) -> bool:
        """macOS/Linux: otomatik kurulum yok; (doğrulanmış) indirme adresini tarayıcıda aç."""
        url = str((self._pending or {}).get("url", ""))
        ok, _reason = validate_update_url(url)
        if not ok:
            return False
        return QDesktopServices.openUrl(QUrl(url))

    def _on_progress(self, downloaded, total, speed: float) -> None:
        if self.dialog and not (self._job or {}).get("silent", False):
            self.dialog.update_download_progress(int(downloaded), int(total), speed)

    def _on_complete(self, path: str) -> None:
        job = self._job or {}
        version = str(job.get("version") or self._pending_version())
        checksum = str(job.get("checksum") or "")
        verified = bool(self.installer.last_verified)
        if job.get("silent") or self.dialog is None:
            if not verified:
                # Sunucu sağlama toplamı vermedi: doğrulanmamış dosya onay sorulmadan KURULMAZ.
                _log.warning("İndirme doğrulanamadı; otomatik kurulmayacak")
                self.installer.cleanup()
                self.checker.mark_checked()
                return
            mandatory = bool((self._pending or {}).get("mandatory"))
            self._set_ready(path, version, checksum, mandatory)
            self.checker.mark_checked()  # paket elde: yarın değil, kurulana dek bir sonraki açılışlarda da kullanılır
            return
        if not verified:
            self._unverified_path = path
            self.dialog.show_unverified()  # açılır kutu yok: satır içi uyarı + "Yine de kur"
            return
        self._install_package(path, checksum, verify=False)

    def _on_failed(self, err: str) -> None:
        if err == "cancelled":
            return
        job = self._job or {}
        kind = getattr(self.installer, "last_fail_kind", "download")
        if job.get("silent") or self.dialog is None:
            _log.warning("Sessiz güncelleme indirilemedi: %s", err)
            if kind == "checksum":
                self.checker.mark_checked()  # kesin hata: her açılışta yeniden 200 MB indirme olmasın
            return
        self.dialog.show_error(err)

    # ------------------------------------------------------------------ kurulum
    def _relaunch_args(self) -> list[str]:
        """Yeniden başlatmada açık olan görüntü (varsa) komut satırında verilir: kullanıcı kaldığı yerden devam eder."""
        window = self._dialog_parent or self._parent_window()
        getter = getattr(window, "current_file", None)
        try:
            path = getter() if callable(getter) else None
        except Exception:  # noqa: BLE001
            path = None
        return [str(path)] if path and os.path.isfile(str(path)) else []

    def _install_package(self, path: str, checksum: str, *, verify: bool) -> None:
        if self._installing:
            return
        self._installing = True
        if self._ready is not None and self._ready.path == path:
            self._clear_ready(delete=False)  # dosya birazdan tüketilir; kayıt kalmasın
        if self.dialog is not None:
            self.dialog.show_download_complete()
            self.dialog.show_installing_state()
        started = self.installer.install_update(
            path,
            relaunch=True,
            visible=True,
            extra_args=self._relaunch_args(),
            expected_checksum=checksum if (verify and checksum) else None,
        )
        if not started:
            self._installing = False

    def _install_unverified(self) -> None:
        path, self._unverified_path = self._unverified_path, None
        if path and os.path.exists(path):
            self._install_package(path, "", verify=False)
        else:
            self._dialog_error(self.installer.message("file_missing"))

    def _on_install_complete(self) -> None:
        self._installing = False
        if self.dialog is not None:
            self.dialog.show_install_complete()
        QTimer.singleShot(400, self._quit_for_update)

    def _quit_for_update(self) -> None:
        """Temiz çıkış: pencereler kapanır (closeEvent: mpv/geçici dosya/geometri temizliği), süreç öldürülmez."""
        if self.dialog is not None:
            self.dialog.allow_close()  # kapanmayı reddeden pencere closeAllWindows'u durdururdu
        QApplication.closeAllWindows()
        QApplication.quit()
        if getattr(sys, "frozen", False):
            su.arm_exit_watchdog()  # çıkış yine de takılırsa sert çıkış: yeni sürüm bizi bekliyor

    def _pending_version(self) -> str:
        return str((self._pending or {}).get("version", ""))

    def _on_install_failed(self, err: str) -> None:
        self._installing = False
        kept, self.installer.kept_package = self.installer.kept_package, None
        if not self._keep_for_retry(kept):
            self._clear_ready(delete=True)  # paketin kendisi kullanılamıyor: atılır; "Tekrar dene" yeniden indirir
            if kept and _is_our_package(kept):
                _remove_quietly(kept)  # kurucu sakladı ama yeniden onaysız kurulamaz (sağlama toplamsız): sahipsiz kalmasın
        self._dialog_error(err)

    def _keep_for_retry(self, path: Optional[str]) -> bool:
        """Kurulum geçici bir ortam sorunuyla (paylaşım ihlali: antivirüs taraması, yazılamayan disk) başarısız oldu ve paket sağlam:
        "Tekrar dene" ~140 MB'ı yeniden indirmesin diye hazır paket olarak tutulur (yeniden kurulurken checksum yine doğrulanır).
        Kalıcı kayıt YOK: oturum bitince çıkışta kurulum bir kez daha dener, olmazsa bayat paket süpürmesi dosyayı temizler."""
        pending = self._pending or {}
        if not path or not _is_our_package(path) or not os.path.isfile(path):
            return False
        try:
            checksum = normalize_checksum(pending.get("checksum"))
        except ValueError:
            return False
        if checksum is None:  # sağlama toplamsız (doğrulanmamış) paket onaysız yeniden kurulmaz
            return False
        self._set_ready(path, self._pending_version(), checksum, bool(pending.get("mandatory")), persist=False)
        return self._ready is not None and self._ready.path == path

    # ------------------------------------------------------------------ pencere
    def _cancel(self) -> None:
        self._remember_declined()
        if self._thread is not None and self._thread.isRunning():
            # İşçi dosyayı kapatıp kendisi temizler: açık dosya burada silinemez, yarım indirme %TEMP%'te kalırdı.
            self.installer.cancel_download()
        else:
            self.installer.cleanup()

    def _defer(self) -> None:
        self._remember_declined()

    def _remember_declined(self) -> None:
        if not self._pending:
            return
        settings = make_settings()
        settings.setValue("declined_update_version", self._pending["version"])
        self._sync_windows()

    def _dialog_closed(self, dialog) -> None:
        if dialog is not self.dialog:
            return
        self.dialog = None
        self._dialog_parent = None
        path, self._unverified_path = self._unverified_path, None
        if path and not self._installing:
            _remove_quietly(path)  # kullanıcı "Yine de kur" demeden kapattı
            self.installer.downloaded_file = None

    def open_pending(self) -> None:
        """Rozet üzerinden mevcut güncelleme penceresini göster."""
        if not self._pending:
            return
        if self.dialog is not None:
            try:
                self.dialog.raise_()
                self.dialog.activateWindow()
                return
            except RuntimeError:  # pencere silinmiş
                self.dialog = None
        pending = self._pending
        self._dialog_parent = self._parent_window()
        dialog = UpdateDialog(
            pending["version"],
            pending["notes"],
            pending["mandatory"],
            self._dialog_parent,
            size=pending["size"],
        )
        self.dialog = dialog
        dialog.update_accepted.connect(self._start_download)
        dialog.install_confirmed.connect(self._install_unverified)
        dialog.update_deferred.connect(self._defer)
        dialog.update_cancelled.connect(self._cancel)
        dialog.finished.connect(lambda *_: self._dialog_closed(dialog))
        dialog.exec()


def attach_updates(app: QApplication) -> UpdateService:
    existing = getattr(app, "_lumina_updates", None)
    if isinstance(existing, UpdateService):
        return existing
    service = UpdateService(app)
    app._lumina_updates = service
    app.aboutToQuit.connect(service.shutdown)
    QTimer.singleShot(1200, service.start)
    return service


def get_update_service() -> Optional[UpdateService]:
    app = QApplication.instance()
    if app is None:
        return None
    existing = getattr(app, "_lumina_updates", None)
    return existing if isinstance(existing, UpdateService) else None
