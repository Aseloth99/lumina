"""macik.com.tr üzerinden Lumina güncelleme kontrolü."""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
import threading
from datetime import datetime, timedelta
from typing import Optional

from PyQt6.QtCore import QObject, QSettings, QTimer, pyqtSignal

from app.core.remote_notice import parse_notice
from app.core.settings_store import make_settings
from app.core.update_policy import is_local_dev_host, validate_update_url
from app.core.version import get_version
from app.resources.locales import t

_log = logging.getLogger(__name__)


def _settings() -> QSettings:
    return make_settings()


class _UpdateCheckWorker(QObject):
    result_ready = pyqtSignal(dict)
    network_error = pyqtSignal(str)
    check_error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, url: str, ssl_context, parent=None):
        super().__init__(parent)
        self._url = url
        self._ssl_context = ssl_context
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
            req = urllib.request.Request(self._url)
            req.add_header("User-Agent", "AppUpp/1.0 Lumina-UpdateChecker")
            with urllib.request.urlopen(req, timeout=10, context=self._ssl_context) as response:
                data = json.loads(response.read().decode())
            if not isinstance(data, dict):
                self.check_error.emit("unexpected response")
            else:
                self.result_ready.emit(data)
        except urllib.error.HTTPError as exc:
            self.network_error.emit(str(exc.reason))
        except urllib.error.URLError as exc:
            reason = exc.reason if hasattr(exc, "reason") else exc
            self.network_error.emit(str(reason))
        except TimeoutError as exc:
            self.network_error.emit(str(exc))
        except OSError as exc:
            self.network_error.emit(str(exc))
        except Exception as exc:
            self.check_error.emit(str(exc))
        finally:
            self._running = False
            self.finished.emit()

    def wait(self, timeout_ms: int = 0) -> bool:
        if self._thread is not None and self._thread.is_alive():
            timeout_sec = (timeout_ms / 1000.0) if timeout_ms > 0 else None
            self._thread.join(timeout=timeout_sec)
            return not self._thread.is_alive()
        return True


class UpdateChecker(QObject):
    # (sürüm, indirme adresi, sürüm notları, zorunlu mu, dosya boyutu [bayt; sığmama sorunu olmasın diye object], sağlama toplamı)
    update_available = pyqtSignal(str, str, str, bool, object, str)
    check_complete = pyqtSignal(bool)
    check_failed = pyqtSignal()
    notice_received = pyqtSignal(object)  # remote_notice.Notice (duyuru satırı)

    CURRENT_VERSION = get_version()
    UPDATE_CHECK_URL = "https://macik.com.tr/api/updates/check"
    APP_NAME = "Lumina"
    CHECK_INTERVAL_HOURS = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_version = self.CURRENT_VERSION
        self.last_check_time: Optional[datetime] = None
        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self._auto_check)
        self._worker: Optional[_UpdateCheckWorker] = None
        self._manual = False

    def start(self) -> None:
        settings = _settings()
        if not _as_bool(settings.value("auto_update_check", True)):
            return
        self._check_if_needed()
        self.check_timer.start(self.CHECK_INTERVAL_HOURS * 3600 * 1000)

    def stop(self) -> None:
        self.check_timer.stop()

    def _auto_check(self) -> None:
        self.check_for_updates(show_message=False)

    def _check_if_needed(self) -> None:
        last_check_str = _settings().value("last_update_check", None)
        if last_check_str:
            try:
                self.last_check_time = datetime.fromisoformat(str(last_check_str))
                if datetime.now() - self.last_check_time < timedelta(hours=self.CHECK_INTERVAL_HOURS):
                    return
            except Exception:
                pass
        self.check_for_updates(show_message=False)

    def check_for_updates(self, show_message: bool = False) -> None:
        settings = _settings()
        if not _as_bool(settings.value("auto_update_check", True)) and not show_message:
            return
        if self._worker is not None and self._worker.isRunning():
            if show_message:
                self._manual = True
            return

        self._manual = bool(show_message)
        params = {
            "appName": self.APP_NAME,
            "currentVersion": self.current_version,
        }
        url = f"{self.UPDATE_CHECK_URL}?{urllib.parse.urlencode(params)}"
        parsed = urllib.parse.urlparse(url)
        # Doğrulamasız SSL yalnızca geliştirmede (paketlenmemiş) localhost için.
        ssl_context = ssl._create_unverified_context() if is_local_dev_host(parsed.hostname) else None

        self._worker = _UpdateCheckWorker(url, ssl_context, self)
        self._worker.result_ready.connect(self._on_worker_result)
        self._worker.network_error.connect(self._on_worker_network_error)
        self._worker.check_error.connect(self._on_worker_error)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_finished(self) -> None:
        worker = self.sender()
        if worker is self._worker:
            self._worker = None

    def _on_worker_result(self, data: dict) -> None:
        show_message = self._manual
        try:
            from app.resources.locales import get_active_language

            notice = parse_notice(data, get_active_language())
            if notice is not None:
                self.notice_received.emit(notice)
            # "false" gibi dize değerler bool'a doğru çevrilir (bool("false") True'dur).
            if not _as_bool(data.get("updateAvailable", False)):
                self.check_complete.emit(False)
                if show_message:
                    self._show_info(
                        t("update_check_title"),
                        t("update_current").format(self.current_version),
                    )
                self.mark_checked()
                return

            latest_version = _text(data.get("version")).lstrip("vV")
            download_url = _text(data.get("download_url"))
            release_notes = _text(data.get("release_notes"), strip=False)
            is_mandatory = _as_bool(data.get("isMandatory", False))
            file_size = _as_size(data.get("fileSize"))
            checksum = _text(data.get("checksum"))

            url_ok, _url_reason = validate_update_url(download_url) if download_url else (False, "")
            if not latest_version or not download_url or not url_ok:
                if show_message:
                    self.check_failed.emit()
                    self._show_warning(
                        t("update_check_title"),
                        t("update_invalid"),
                    )
                self.check_complete.emit(False)
                return

            skipped = _skipped_version()
            if skipped and skipped.lstrip("vV") == latest_version:
                # Bu sürüm bu bilgisayarda açılışta çöktü ve geri alındı: aynı sürüm bir daha önerilmez.
                self.check_complete.emit(False)
                if show_message:
                    self._show_info(t("update_check_title"), t("update_current").format(self.current_version))
                self.mark_checked()
                return

            if self._compare_versions(latest_version, self.current_version) > 0:
                # Damga burada ATILMAZ: sessiz indirme yarıda kalırsa (Lumina kapanırsa) 24 saat denetim yapılmazdı.
                # UpdateService indirme bitince ya da indirme gerekmiyorsa `mark_checked()` çağırır.
                self.update_available.emit(
                    latest_version,
                    download_url,
                    release_notes,
                    is_mandatory,
                    file_size,
                    checksum,
                )
                self.check_complete.emit(True)
            else:
                self.check_complete.emit(False)
                if show_message:
                    self._show_info(
                        t("update_check_title"),
                        t("update_current").format(self.current_version),
                    )
                self.mark_checked()
        except Exception as exc:
            if show_message:
                self.check_failed.emit()
            self.check_complete.emit(False)

    def _on_worker_network_error(self, reason: str) -> None:
        # Eskiden çevrimdışıyken de "Lumina güncel" denirdi; artık gerçek durum (denetlenemedi) bildirilir.
        if self._manual:
            self.check_failed.emit()
        self.check_complete.emit(False)

    def _on_worker_error(self, message: str) -> None:
        if self._manual:
            self.check_failed.emit()
        self.check_complete.emit(False)

    def mark_checked(self) -> None:
        """Denetim TAMAMLANDI damgası (24 saat boyunca yeniden denetlenmez). Bekleyen bir güncelleme varken
        indirme bitene dek atılmaz: Lumina kapanıp indirme yarıda kalırsa sonraki açılışta yeniden denetlenir."""
        _settings().setValue("last_update_check", datetime.now().isoformat())

    def _show_info(self, title: str, text: str) -> None:
        """Güncelleme sonuçları için pop-up açılmaz: ayarlardaki durum satırı / başlıktaki düğme gösterir."""
        _log.info("%s: %s", title, text)

    def _show_warning(self, title: str, text: str) -> None:
        _log.info("%s: %s", title, text)

    def _compare_versions(self, version1: str, version2: str) -> int:
        try:
            v1_parts = [int(x) for x in version1.split(".")]
            v2_parts = [int(x) for x in version2.split(".")]
            max_len = max(len(v1_parts), len(v2_parts))
            v1_parts += [0] * (max_len - len(v1_parts))
            v2_parts += [0] * (max_len - len(v2_parts))
            for left, right in zip(v1_parts, v2_parts):
                if left > right:
                    return 1
                if left < right:
                    return -1
            return 0
        except Exception:
            return 0

    def get_current_version(self) -> str:
        return self.current_version


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _text(value, strip: bool = True) -> str:
    """Sunucu alanı -> dize (None/sayı/liste gelirse bile istisna yok)."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text.strip() if strip else text


def _as_size(value) -> int:
    """fileSize: null, sayı, ondalık ya da dize olabilir; anlaşılmazsa 0 (bilinmiyor)."""
    if value is None or isinstance(value, bool):
        return 0
    try:
        size = int(float(str(value).strip().replace(",", "")))
    except (ValueError, OverflowError):
        return 0
    return size if size > 0 else 0


def _skipped_version() -> Optional[str]:
    """Geri alınan sürüm (başlatıcının yanındaki `.skip` işareti); yalnızca derlenmiş + başlatıcılı çalışmada."""
    try:
        from app.core import self_update
        from app.core.paths import launcher_path

        launcher = launcher_path()
        return self_update.skipped_version(str(launcher)) if launcher is not None else None
    except Exception:  # noqa: BLE001 — işaret okunamazsa sürüm önerilir
        return None
