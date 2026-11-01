"""Güncelleme deneyimi: pop-up yok; başlık kapsülünde "Güncelle · vX" düğmesi; otomatik kur = Lumina kapanırken.

Lumina bir görüntüleyicidir (pencere kapanınca süreç biter), bu yüzden otomatik kurulum "kullanılmıyorken yeniden
başlat" yerine "kapatırken kur, yeniden başlatma" biçimindedir; kullanıcı hemen yeniden açarsa betik onu öldürmez.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QCoreApplication, QEvent, QEventLoop, QSettings, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.ui.message_box import MessageBox  # noqa: E402

SHA = hashlib.sha256(b"paket").hexdigest()

PENDING = {"version": "9.9.9", "url": "https://macik.com.tr/d.zip", "notes": "n", "mandatory": False, "size": 10,
           "checksum": SHA}


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._tmp.name)
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        from app.core.settings_store import make_settings
        from app.resources import locales

        settings = make_settings()
        for key in ("last_update_check", "declined_update_version", "update_ready"):
            settings.remove(key)
        # Etkin dil makineden (kayıt defteri/Windows dili) bağımsız Türkçe olsun; açıkça verilen dil aynen geçerlidir.
        patcher = mock.patch.object(locales, "get_active_language", lambda lang=None: lang if lang in locales.LOCALES else "tr")
        patcher.start()
        self.addCleanup(patcher.stop)
        # İndirme kilidi gerçek %TEMP%'teki (kullanıcının çalışan Lumina'sının) kilidine değil teste özel dosyaya yazılır
        from app.core import update_installer as installer_module

        lock_path = pathlib.Path(self._tmp.name) / f"Lumina.update.{time.monotonic_ns()}.lock"
        patcher = mock.patch.object(installer_module, "default_download_lock_path", return_value=lock_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        created: list = []
        real_init = installer_module.DownloadLock.__init__

        def tracking_init(lock, *args, **kwargs):
            real_init(lock, *args, **kwargs)
            created.append(lock)

        patcher = mock.patch.object(installer_module.DownloadLock, "__init__", tracking_init)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: [lock.release() for lock in created])  # açık kilit dosyası tearDownClass'ta silinemezdi

    def _service(self, auto: bool = True, mandatory: bool = False):
        from app.core.settings_store import make_settings
        from app.ui.updates import UpdateService

        make_settings().setValue("auto_update_install", auto)
        packaged = mock.patch("app.ui.updates._is_packaged", return_value=True)  # sessiz kurulum yalnızca exe'de
        packaged.start()
        self.addCleanup(packaged.stop)
        service = UpdateService()
        self.addCleanup(service.deleteLater)
        service._begin_download = mock.Mock()
        service.installer.check_writable = mock.Mock()  # gerçek klasöre yoklama dosyası yazılmasın
        service._pending = dict(PENDING, mandatory=mandatory)
        return service

    def _package(self, data: bytes = b"PK\x03\x04x") -> str:
        """Gerçek bir geçici paket dosyası (kendi adımızla: ayardan okunan yol yalnızca böyle kabul edilir)."""
        from app.core.update_installer import package_path

        path = package_path(900000 + len(self._cleanups))
        with open(path, "wb") as handle:
            handle.write(data)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    @staticmethod
    def _stamp():
        from app.core.settings_store import make_settings

        return make_settings().value("last_update_check", None)


class AvailableTest(_Base):
    def test_no_dialog_is_opened_and_silent_download_starts_when_auto_is_on(self):
        service = self._service(auto=True)
        with mock.patch("app.ui.updates.UpdateDialog", side_effect=AssertionError("pop-up açılmamalı")), \
             mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._on_available("9.9.9", PENDING["url"], "n", False, 10, SHA)
        service._begin_download.assert_called_once_with(silent=True)
        self.assertIsNone(self._stamp(), "indirme bitmeden denetim damgası atılmaz (kapanışta iptal olursa 24 saat denetimsiz kalırdı)")

    def test_auto_off_shows_button_but_downloads_nothing_and_stamps_the_check(self):
        service = self._service(auto=False)
        with mock.patch("app.ui.updates.UpdateDialog", side_effect=AssertionError("pop-up açılmamalı")), \
             mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._on_available("9.9.9", PENDING["url"], "n", False, 10, SHA)
        service._begin_download.assert_not_called()
        self.assertIsNotNone(service._pending)
        self.assertIsNotNone(self._stamp(), "indirilecek bir şey yok: denetim tamamlandı")

    def test_mandatory_downloads_even_when_auto_is_off(self):
        service = self._service(auto=False)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._on_available("9.9.9", PENDING["url"], "n", True, 10, SHA)
        service._begin_download.assert_called_once_with(silent=True)

    def test_without_a_checksum_nothing_is_downloaded_silently(self):
        # Doğrulanamayan paket sessizce kurulmaz: bu yüzden bandwidth harcayıp indirmeye hiç başlanmaz.
        for checksum in ("", "bozuk"):
            service = self._service(auto=True)
            with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
                service._on_available("9.9.9", PENDING["url"], "n", False, 10, checksum)
            service._begin_download.assert_not_called()

    def test_unwritable_folder_skips_the_silent_download(self):
        from app.core import self_update as su

        service = self._service(auto=True)
        service.installer.check_writable = mock.Mock(side_effect=su.PackageError("yok", "not_writable", "C:/x"))
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._on_available("9.9.9", PENDING["url"], "n", False, 10, SHA)
        service._begin_download.assert_not_called()
        self.assertIsNotNone(self._stamp())

    def test_default_is_on(self):
        from app.core.settings_store import make_settings
        from app.ui.updates import UpdateService

        settings = make_settings()
        settings.remove("auto_update_install")
        self.assertTrue(UpdateService.auto_install_setting(settings))

    def test_a_newer_offer_discards_the_package_downloaded_for_the_older_version(self):
        # Eski hata: sürüme bağlı olmayan hazır dosya, daha yeni sürüm sunulunca bile kurulurdu.
        from app.ui.updates import _Ready

        service = self._service(auto=True)
        path = self._package()
        service._ready = _Ready(path, "9.9.9", SHA, 5, False)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._on_available("10.0.0", PENDING["url"], "n", False, 10, SHA)
        self.assertIsNone(service._ready)
        self.assertFalse(os.path.exists(path), "eski sürümün paketi silinir")
        service._begin_download.assert_called_once_with(silent=True)

    def test_a_running_download_of_an_older_version_is_cancelled_and_the_new_one_starts_after_it(self):
        service = self._service(auto=True)
        service._thread = mock.Mock()
        service._thread.isRunning.return_value = True
        service._job = {"version": "9.9.9", "checksum": SHA, "silent": True}
        service.installer = mock.Mock()
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._on_available("10.0.0", PENDING["url"], "n", False, 10, SHA)
        service.installer.cancel_download.assert_called_once()
        service._begin_download.assert_not_called()  # iş parçacığı bitmeden ikinci indirme başlamaz
        service._thread.isRunning.return_value = False
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._on_thread_finished(service._thread)
        service._begin_download.assert_called_once_with(silent=True)

    def test_the_same_version_offered_again_does_not_start_a_second_download(self):
        service = self._service(auto=True)
        service._thread = mock.Mock()
        service._thread.isRunning.return_value = True
        service._job = {"version": "9.9.9", "checksum": SHA, "silent": True}
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._on_available("9.9.9", PENDING["url"], "n", False, 10, SHA)
        service._begin_download.assert_not_called()


class SilentDownloadTest(_Base):
    def _silent_job(self, service, version="9.9.9"):
        service._job = {"version": version, "checksum": SHA, "silent": True}

    def test_verified_download_waits_for_exit_without_any_modal(self):
        from app.core.settings_store import make_settings
        from app.ui.updates import READY_KEY

        service = self._service()
        self._silent_job(service)
        path = self._package()
        service.installer = mock.Mock(last_verified=True)
        with mock.patch.object(MessageBox, "warning", side_effect=AssertionError("pop-up")), \
             mock.patch.object(MessageBox, "information", side_effect=AssertionError("pop-up")), \
             mock.patch.object(MessageBox, "question", side_effect=AssertionError("pop-up")):
            service._on_complete(path)
        self.assertEqual((service._ready.path, service._ready.version, service._ready.checksum), (path, "9.9.9", SHA))
        service.installer.install_blocking.assert_not_called()
        self.assertIsNotNone(self._stamp(), "paket elde: denetim damgası şimdi atılır")
        self.assertIn(path.replace("\\", "/"), str(make_settings().value(READY_KEY)).replace("\\\\", "/"), "hazır paket kalıcı kaydedilir")

    def test_ready_package_is_bound_to_the_version_that_was_downloaded(self):
        service = self._service()
        self._silent_job(service, "9.9.8")  # indirme başladığında sunulan sürüm
        service._pending = dict(PENDING, version="9.9.9")  # bitmeden yenisi sunuldu
        service.installer = mock.Mock(last_verified=True)
        service._on_complete(self._package())
        self.assertEqual(service._ready.version, "9.9.8")

    def test_unverified_download_is_never_installed_silently(self):
        service = self._service()
        self._silent_job(service)
        service.installer = mock.Mock(last_verified=False)
        with mock.patch.object(MessageBox, "warning", side_effect=AssertionError("pop-up")):
            service._on_complete("C:/tmp/u.zip")
        self.assertIsNone(service._ready)
        service.installer.cleanup.assert_called_once()
        service.installer.install_blocking.assert_not_called()

    def test_silent_failures_have_no_modal_and_no_dialog(self):
        service = self._service()
        service.installer = mock.Mock(last_fail_kind="download")
        self._silent_job(service)
        with mock.patch.object(MessageBox, "warning", side_effect=AssertionError("pop-up")):
            service._on_failed("ağ koptu")
            service._on_install_failed("izin yok")

    def test_a_cancelled_silent_download_is_ignored_and_does_not_stamp(self):
        service = self._service()
        service.installer = mock.Mock(last_fail_kind="download")
        self._silent_job(service)
        service._on_failed("cancelled")
        self.assertIsNone(self._stamp(), "kapanışta iptal olan indirme yüzünden 24 saat denetimsiz kalınmaz")

    def test_a_checksum_failure_is_final_and_stamps_so_it_is_not_repeated_on_every_start(self):
        service = self._service()
        service.installer = mock.Mock(last_fail_kind="checksum")
        self._silent_job(service)
        service._on_failed("checksum")
        self.assertIsNotNone(self._stamp())

    def test_user_started_download_failure_is_shown_inline_in_the_dialog(self):
        service = self._service()
        service.installer = mock.Mock(last_fail_kind="download")
        service._job = {"version": "9.9.9", "checksum": SHA, "silent": False}
        service.dialog = mock.Mock()
        with mock.patch.object(MessageBox, "warning", side_effect=AssertionError("pop-up")):
            service._on_failed("ağ koptu")
        service.dialog.show_error.assert_called_once_with("ağ koptu")


class InstallOnExitTest(_Base):
    def _ready(self, service, version="9.9.9", mandatory=False):
        from app.ui.updates import _Ready

        path = self._package()
        service._ready = _Ready(path, version, SHA, os.path.getsize(path), mandatory)
        service.installer = mock.Mock(swapped=False)
        service.installer.install_blocking.return_value = None
        service.installer.recovery_point_exists.return_value = False
        return path

    def test_installs_without_relaunch(self):
        service = self._service(auto=True)
        path = self._ready(service)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service.install_on_exit()
        service.installer.install_blocking.assert_called_once_with(path, relaunch=False, expected_checksum=SHA)
        self.assertIsNone(service._ready)

    def test_does_nothing_when_auto_install_was_turned_off(self):
        service = self._service(auto=False)
        self._ready(service)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service.install_on_exit()
        service.installer.install_blocking.assert_not_called()

    def test_a_mandatory_package_is_installed_even_when_auto_install_is_off(self):
        service = self._service(auto=False)
        self._ready(service, mandatory=True)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service.install_on_exit()
        service.installer.install_blocking.assert_called_once()

    def test_does_nothing_without_a_downloaded_update(self):
        service = self._service(auto=True)
        service.installer = mock.Mock(swapped=False)
        service.install_on_exit()
        service.installer.install_blocking.assert_not_called()

    def test_runs_only_once(self):
        service = self._service(auto=True)
        path = self._ready(service)
        ready = service._ready
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service.install_on_exit()
            service._ready = ready  # ikinci çağrıda hazır paket yeniden görünse bile
            service.install_on_exit()
        self.assertEqual(service.installer.install_blocking.call_count, 1)
        self.assertTrue(path)

    def test_a_package_that_is_no_longer_newer_is_dropped_not_installed(self):
        service = self._service(auto=True)
        path = self._ready(service, version="1.0.0")  # çalışan sürüm zaten bu (ya da daha yeni)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service.install_on_exit()
        service.installer.install_blocking.assert_not_called()
        self.assertFalse(os.path.exists(path))

    def test_an_update_already_installed_by_this_process_is_not_installed_twice(self):
        service = self._service(auto=True)
        self._ready(service)
        service.installer.swapped = True
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service.install_on_exit()
        service.installer.install_blocking.assert_not_called()

    def test_failure_is_logged_and_never_raises(self):
        service = self._service(auto=True)
        path = self._ready(service)
        service.installer.install_blocking.return_value = "izin yok"
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service.install_on_exit()
        self.assertFalse(os.path.exists(path), "kurulamayan paket atılır")

    def test_an_unexpected_exception_never_breaks_the_shutdown(self):
        service = self._service(auto=True)
        self._ready(service)
        service.installer.install_blocking.side_effect = RuntimeError("beklenmedik")
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service.install_on_exit()  # istisna kapanışı bozmamalı

    def test_shutdown_waits_for_a_running_install_then_triggers_the_exit_install(self):
        service = self._service()
        service.installer = mock.Mock()
        service.install_on_exit = mock.Mock()
        service.shutdown()
        service.installer.wait_install.assert_called_once()
        service.install_on_exit.assert_called_once()

    def test_an_unconfirmed_earlier_update_is_never_overwritten_on_exit(self):
        # Başka bir Lumina penceresi güncellemeyi kurmuş / önceki güncelleme doğrulanmadı: `.old` (geri alma noktası) ezilmez.
        from app.core.settings_store import make_settings
        from app.ui.updates import READY_KEY

        service = self._service(auto=True)
        path = self._ready(service)
        make_settings().setValue(READY_KEY, json.dumps({"path": path, "version": "9.9.9", "checksum": SHA, "size": os.path.getsize(path), "mandatory": False}))
        service.installer.recovery_point_exists.return_value = True
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service.install_on_exit()
        service.installer.install_blocking.assert_not_called()
        self.assertTrue(os.path.exists(path), "kayıt hâlâ bu pakete işaret ediyorsa paket yarın için saklanır")

    def test_while_a_manual_install_runs_the_exit_install_stays_out(self):
        service = self._service(auto=True)
        self._ready(service)
        service._installing = True
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service.install_on_exit()
        service.installer.install_blocking.assert_not_called()


class SkippedExitInstallTest(_Base):
    """K2a: çıkışta kurulum atlanırsa (başka pencere aynı güncellemeyi kurdu) bu pencerenin kendi paketi %TEMP%'te sahipsiz kalmaz."""

    def _ready(self, service, path=None, version="9.9.9"):
        from app.ui.updates import _Ready

        path = path or self._package()
        service._ready = _Ready(path, version, SHA, os.path.getsize(path), False)
        service.installer = mock.Mock(swapped=False)
        service.installer.install_blocking.return_value = None
        service.installer.recovery_point_exists.return_value = True  # `.old` var: başka pencere kurmuş
        return path

    def _exit(self, service):
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service.install_on_exit()

    def test_our_own_package_is_deleted_when_another_window_already_installed_the_update(self):
        from app.core.settings_store import make_settings
        from app.ui.updates import READY_KEY

        service = self._service(auto=True)
        path = self._ready(service)
        self.assertTrue(make_settings().value(READY_KEY, "") in ("", None), "ilk kapanan pencere ortak kaydı sildi")
        self._exit(service)
        service.installer.install_blocking.assert_not_called()
        self.assertFalse(os.path.exists(path), "sahipsiz kalan ~140 MB paket silinir")
        self.assertIsNone(service._ready)

    def test_a_package_the_record_still_points_to_is_kept_for_the_next_start(self):
        from app.core.settings_store import make_settings
        from app.ui.updates import READY_KEY

        service = self._service(auto=True)
        path = self._ready(service)
        make_settings().setValue(READY_KEY, json.dumps({"path": path, "version": "9.9.9", "checksum": SHA, "size": 5, "mandatory": False}))
        self._exit(service)
        self.assertTrue(os.path.exists(path))

    def test_a_record_pointing_at_another_windows_package_does_not_save_ours(self):
        from app.core.settings_store import make_settings
        from app.ui.updates import READY_KEY

        service = self._service(auto=True)
        path = self._ready(service)
        make_settings().setValue(READY_KEY, json.dumps({"path": path + ".baska", "version": "9.9.9", "checksum": SHA, "size": 5, "mandatory": False}))
        self._exit(service)
        self.assertFalse(os.path.exists(path))

    def test_a_package_that_belongs_to_a_live_window_is_never_deleted(self):
        import subprocess

        from app.core.update_installer import package_path

        child = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(120)"])
        self.addCleanup(lambda: (child.kill(), child.wait(10)))
        path = package_path(child.pid)
        with open(path, "wb") as handle:
            handle.write(b"PK\x03\x04x")
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        service = self._service(auto=True)
        self._ready(service, path)
        self._exit(service)
        self.assertTrue(os.path.exists(path), "başka (yaşayan) pencerenin paketi bu pencere tarafından silinmez")

    def test_the_package_of_a_dead_window_adopted_from_the_record_is_deleted(self):
        service = self._service(auto=True)
        path = self._ready(service)  # pid 900000+n: böyle bir süreç yok
        self._exit(service)
        self.assertFalse(os.path.exists(path))

    def test_clear_ready_never_deletes_the_package_of_a_live_window(self):
        import subprocess

        from app.core.update_installer import package_path
        from app.ui.updates import _Ready

        child = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(120)"])
        self.addCleanup(lambda: (child.kill(), child.wait(10)))
        path = package_path(child.pid)
        with open(path, "wb") as handle:
            handle.write(b"PK\x03\x04x")
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        service = self._service(auto=True)
        service._ready = _Ready(path, "9.9.9", SHA, 5, False)
        service._clear_ready(delete=True)
        self.assertTrue(os.path.exists(path))


class DeclinedUpdateTest(_Base):
    """B6: "Daha Sonra" denen sürüm Lumina kapanırken kendiliğinden KURULMAZ (rozet kalır, elle 'Şimdi Güncelle' çalışır);
    daha yeni bir sürüm gelince ret geçersiz olur; zorunlu güncelleme reddedilemez."""

    def _declined(self, version="9.9.9"):
        from app.core.settings_store import make_settings

        make_settings().setValue("declined_update_version", version)

    def _ready(self, service, version="9.9.9", mandatory=False):
        from app.ui.updates import _Ready

        path = self._package()
        service._ready = _Ready(path, version, SHA, os.path.getsize(path), mandatory)
        service.installer = mock.Mock(swapped=False)
        service.installer.install_blocking.return_value = None
        service.installer.recovery_point_exists.return_value = False
        return path

    def _exit(self, service):
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service.install_on_exit()

    def test_a_declined_version_is_not_installed_on_exit_and_its_package_is_removed(self):
        service = self._service(auto=True)
        path = self._ready(service)
        self._declined("9.9.9")
        self._exit(service)
        service.installer.install_blocking.assert_not_called()
        self.assertFalse(os.path.exists(path), "reddedilen sürümün paketi %TEMP%'te kalmaz")
        self.assertIsNone(service._ready)

    def test_later_in_the_dialog_after_a_silent_download_stops_the_exit_install(self):
        # E2E B6: otomatik kur açık, sessiz indirme tıklamadan ÖNCE bitti; kullanıcı 'Daha Sonra' dedi
        service = self._service(auto=True)
        path = self._ready(service)
        self.assertIsNotNone(service._pending)
        service._defer()
        from app.core.settings_store import make_settings

        self.assertEqual(str(make_settings().value("declined_update_version")), "9.9.9")
        self.assertIsNotNone(service._pending, "rozet / başlık düğmesi kalır")
        self.assertTrue(os.path.exists(path), "oturum sürerken paket durur: elle 'Şimdi Güncelle' yeniden indirmez")
        self._exit(service)
        service.installer.install_blocking.assert_not_called()

    def test_cancelling_the_download_counts_as_declining_too(self):
        service = self._service(auto=True)
        path = self._ready(service)
        service._cancel()
        self._exit(service)
        service.installer.install_blocking.assert_not_called()
        self.assertFalse(os.path.exists(path))

    def test_a_declined_version_is_not_installed_on_exit_even_when_auto_install_is_off(self):
        service = self._service(auto=False)
        path = self._ready(service)
        self._declined()
        self._exit(service)
        service.installer.install_blocking.assert_not_called()
        self.assertFalse(os.path.exists(path))

    def test_a_newer_version_is_installed_even_though_an_older_one_was_declined(self):
        service = self._service(auto=True)
        self._ready(service, version="10.0.0")
        self._declined("9.9.9")
        self._exit(service)
        service.installer.install_blocking.assert_called_once()

    def test_the_declined_version_may_carry_a_v_prefix_or_spaces(self):
        service = self._service(auto=True)
        self._ready(service)
        self._declined(" v9.9.9 ")
        self._exit(service)
        service.installer.install_blocking.assert_not_called()

    def test_a_mandatory_update_cannot_be_declined(self):
        service = self._service(auto=True)
        self._ready(service, mandatory=True)
        self._declined()
        self._exit(service)
        service.installer.install_blocking.assert_called_once()

    def test_nothing_is_downloaded_silently_for_a_declined_version_but_the_button_stays(self):
        self._declined()
        service = self._service(auto=True)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._on_available("9.9.9", PENDING["url"], "n", False, 10, SHA)
        service._begin_download.assert_not_called()
        self.assertIsNotNone(service._pending, "başlıktaki 'Güncelle · vX' düğmesi kalır")
        self.assertIsNotNone(self._stamp(), "indirilecek bir şey yok: denetim tamamlandı")

    def test_a_newer_version_is_downloaded_silently_again(self):
        self._declined("9.9.9")
        service = self._service(auto=True)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._on_available("10.0.0", PENDING["url"], "n", False, 10, SHA)
        service._begin_download.assert_called_once_with(silent=True)

    def test_a_mandatory_declined_version_is_still_downloaded(self):
        self._declined()
        service = self._service(auto=True)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._on_available("9.9.9", PENDING["url"], "n", True, 10, SHA)
        service._begin_download.assert_called_once_with(silent=True)

    def test_a_declined_version_stays_installable_by_hand(self):
        service = self._service(auto=True)
        path = self._ready(service)
        self._declined()
        service.installer.install_update = mock.Mock(return_value=True)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._start_download()  # 'Şimdi Güncelle'
        service._begin_download.assert_not_called()
        service.installer.install_update.assert_called_once()
        self.assertEqual(service.installer.install_update.call_args[0][0], path)

    def test_a_declined_versions_package_is_not_carried_to_the_next_start(self):
        from app.core.settings_store import make_settings
        from app.ui.updates import READY_KEY

        self._declined()
        service = self._service(auto=True)
        path = self._package()
        service._set_ready(path, "9.9.9", SHA, False)
        self.assertIsNotNone(service._ready)
        self.assertTrue(make_settings().value(READY_KEY, "") in ("", None), "reddedilen sürüm için kalıcı kayıt yazılmaz")
        # Eski bir oturumdan kalan kayıt da ret sonrası geri yüklenmez (dosya ile birlikte atılır)
        make_settings().setValue(READY_KEY, json.dumps({"path": path, "version": "9.9.9", "checksum": SHA, "size": os.path.getsize(path), "mandatory": False}))
        from app.ui.updates import UpdateService

        fresh = UpdateService()
        self.addCleanup(fresh.deleteLater)
        self.assertIsNone(fresh._ready)
        self.assertFalse(os.path.exists(path))


class DuplicateDownloadTest(_Base):
    """K2c: iki Lumina penceresi (ayrı süreçler) aynı güncellemeyi ayrı ayrı indirmesin; bayat/kullanılamayan kilit indirmeyi engellemesin."""

    def test_the_second_window_does_not_download_while_the_first_one_does(self):
        first = self._service(auto=True)
        second = self._service(auto=True)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            first._on_available("9.9.9", PENDING["url"], "n", False, 10, SHA)
            second._on_available("9.9.9", PENDING["url"], "n", False, 10, SHA)
        first._begin_download.assert_called_once_with(silent=True)
        second._begin_download.assert_not_called()
        self.assertIsNone(self._stamp(), "indirmeyen pencere denetimi 'tamam' damgalamaz: indiren pencere kapanırsa sonraki açılış yeniden denetler")
        self.assertIsNotNone(second._pending, "ikinci pencerede de rozet görünür")

    def test_the_lock_is_released_when_the_download_ends_so_a_retry_can_start(self):
        first = self._service(auto=True)
        second = self._service(auto=True)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            first._on_available("9.9.9", PENDING["url"], "n", False, 10, SHA)
            thread = mock.Mock()
            first._thread = thread
            first._on_thread_finished(thread)  # indirme bitti (başarılı/başarısız/iptal)
            self.assertFalse(first._download_lock.held)
            second._on_available("9.9.9", PENDING["url"], "n", False, 10, SHA)
        second._begin_download.assert_called_once_with(silent=True)

    def test_shutdown_releases_the_lock(self):
        service = self._service(auto=True)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._on_available("9.9.9", PENDING["url"], "n", False, 10, SHA)
        self.assertTrue(service._download_lock.held)
        service.installer = mock.Mock(swapped=False)
        service.checker = mock.Mock()
        service.shutdown()
        self.assertFalse(service._download_lock.held)

    def test_an_unusable_lock_never_prevents_the_download(self):
        service = self._service(auto=True)
        blocker = os.path.join(self._tmp.name, f"dosya{time.monotonic_ns()}")
        with open(blocker, "w") as handle:
            handle.write("x")
        from app.core.update_installer import DownloadLock

        service._download_lock = DownloadLock(os.path.join(blocker, "alt", "kilit"))  # oluşturulamaz
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._on_available("9.9.9", PENDING["url"], "n", False, 10, SHA)
        service._begin_download.assert_called_once_with(silent=True)

    def test_a_manual_download_goes_ahead_even_if_another_window_holds_the_lock(self):
        from app.core.update_installer import DownloadLock
        from app.ui.updates import UpdateService

        service = self._service(auto=False)
        other = DownloadLock(service._download_lock.path)
        self.addCleanup(other.release)
        self.assertTrue(other.try_acquire())
        thread = mock.Mock()
        with mock.patch("app.ui.updates._DownloadThread", return_value=thread):
            UpdateService._begin_download(service, silent=False)  # kullanıcı 'Şimdi Güncelle' dedi: beklemez
        thread.start.assert_called_once()
        self.assertFalse(service._download_lock.held)

    def test_a_manual_download_takes_the_lock_so_other_windows_do_not_repeat_it_silently(self):
        from app.ui.updates import UpdateService

        service = self._service(auto=False)
        other = self._service(auto=True)
        with mock.patch("app.ui.updates._DownloadThread", return_value=mock.Mock()):
            UpdateService._begin_download(service, silent=False)
        self.assertTrue(service._download_lock.held)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            other._on_available("9.9.9", PENDING["url"], "n", False, 10, SHA)
        other._begin_download.assert_not_called()


class StartupSweepTest(_Base):
    """K2b: açılışta sahibi artık çalışmayan pencerenin paket artığı süpürülür (yalnızca derlenmiş Lumina'da)."""

    def _start(self, packaged: bool):
        import threading

        service = self._service(auto=True)
        service.checker = mock.Mock()
        done = threading.Event()
        calls: list = []

        def fake_sweep(keep=None):
            calls.append(keep)
            done.set()
            return 0

        with mock.patch("app.ui.updates._is_packaged", return_value=packaged), \
             mock.patch("app.ui.updates.sweep_orphan_packages", fake_sweep):
            service.start()
            swept = done.wait(2)
        return service, swept, calls

    def test_start_sweeps_orphaned_packages_but_keeps_the_saved_ready_one(self):
        service = self._service(auto=True)
        service.installer.keep_package = "C:/Temp/Lumina_Update_7.pkg"
        service.checker = mock.Mock()
        import threading

        done = threading.Event()
        calls: list = []

        def fake_sweep(keep=None):
            calls.append(keep)
            done.set()

        with mock.patch("app.ui.updates._is_packaged", return_value=True), mock.patch("app.ui.updates.sweep_orphan_packages", fake_sweep):
            service.start()
            self.assertTrue(done.wait(3), "süpürme arka planda çalışmadı")
        self.assertEqual(calls, ["C:/Temp/Lumina_Update_7.pkg"])
        service.checker.start.assert_called_once()

    def test_running_from_source_never_touches_the_temp_folder(self):
        service, swept, calls = self._start(packaged=False)
        self.assertFalse(swept)
        self.assertEqual(calls, [])
        service.checker.start.assert_called_once()


class KeptPackageRetryTest(_Base):
    """Geçici kurulum hatası (paylaşım ihlali): paket silinmez, 'Tekrar dene' yeniden indirmeden aynı paketi kurar."""

    def test_after_a_transient_install_failure_retry_reuses_the_package_without_downloading_again(self):
        from app.core.settings_store import make_settings
        from app.ui.updates import READY_KEY

        service = self._service(auto=True)
        path = self._package()
        service.installer.kept_package = path
        service._installing = True
        service._on_install_failed("Çalışan uygulama dosyası değiştirilemedi: paylaşım ihlali")
        self.assertFalse(service._installing)
        self.assertTrue(os.path.exists(path), "paket hemen silinmedi")
        self.assertEqual((service._ready.path, service._ready.version, service._ready.checksum), (path, "9.9.9", SHA))
        self.assertTrue(make_settings().value(READY_KEY, "") in ("", None), "kalıcı kayıt yok: oturum bitince süpürme temizler")
        self.assertIsNone(service.installer.kept_package)
        service.installer.install_update = mock.Mock(return_value=True)
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._start_download()  # 'Tekrar dene'
        service._begin_download.assert_not_called()
        service.installer.install_update.assert_called_once()
        self.assertEqual(service.installer.install_update.call_args[0][0], path)
        self.assertEqual(service.installer.install_update.call_args.kwargs["expected_checksum"], SHA, "sağlama toplamı yeniden doğrulanır")

    def test_a_package_without_a_checksum_is_not_kept_for_an_unconfirmed_retry(self):
        service = self._service(auto=True)
        service._pending = dict(PENDING, checksum="")
        path = self._package()
        service.installer.kept_package = path
        service._on_install_failed("kilitli")
        self.assertIsNone(service._ready)
        self.assertFalse(os.path.exists(path))

    def test_without_a_kept_package_the_failure_discards_everything_as_before(self):
        service = self._service(auto=True)
        service._on_install_failed("Paket bozuk")
        self.assertIsNone(service._ready)

    def test_a_kept_package_is_given_one_more_try_on_exit_and_removed_if_that_fails_too(self):
        service = self._service(auto=True)
        path = self._package()
        service.installer.kept_package = path
        service._on_install_failed("kilitli")
        service.installer = mock.Mock(swapped=False)
        service.installer.install_blocking.return_value = "yine kilitli"
        service.installer.recovery_point_exists.return_value = False
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service.install_on_exit()
        service.installer.install_blocking.assert_called_once()
        self.assertFalse(os.path.exists(path), "çıkışta da kurulamadıysa paket bırakılmaz")


class ReadyRecordTest(_Base):
    """Hazır paketin (yol + sürüm + sha) kalıcı kaydı: indirme tamamlanıp kurulum yapılamadan süreç ölürse paket kaybolmaz."""

    def _record(self, path, **over):
        from app.core.settings_store import make_settings
        from app.ui.updates import READY_KEY

        size = os.path.getsize(path) if os.path.exists(path) else 0
        data = {"path": path, "version": "9.9.9", "checksum": SHA, "size": size, "mandatory": False}
        data.update(over)
        make_settings().setValue(READY_KEY, json.dumps(data))

    def _fresh(self):
        from app.ui.updates import UpdateService

        service = UpdateService()
        self.addCleanup(service.deleteLater)
        return service

    def test_a_saved_package_is_reused_by_the_next_session(self):
        path = self._package()
        self._record(path)
        service = self._fresh()
        self.assertIsNotNone(service._ready)
        self.assertEqual((service._ready.path, service._ready.version), (path, "9.9.9"))

    def test_stale_changed_or_foreign_records_are_discarded(self):
        from app.core.settings_store import make_settings
        from app.ui.updates import READY_KEY

        path = self._package()
        for label, over in {
            "artık yeni değil": {"version": "1.0.0"},
            "boyut değişmiş": {"size": 1},
            "bozuk sağlama toplamı": {"checksum": "xyz"},
        }.items():
            with self.subTest(label):
                self._record(path, **over)
                self.assertIsNone(self._fresh()._ready)
                self.assertTrue(make_settings().value(READY_KEY, "") in ("", None))
                with open(path, "wb") as f:  # (kayıt temizlenirken dosya da silinir) yeniden oluştur
                    f.write(b"PK\x03\x04x")
        # Ayar dosyası kurcalanmış olabilir: kendi geçici paket adımıza benzemeyen yol ASLA silinmez/kurulmaz.
        foreign = os.path.join(self._tmp.name, "baska_dosya.pkg")
        with open(foreign, "wb") as f:
            f.write(b"PK\x03\x04x")
        self._record(foreign)
        self.assertIsNone(self._fresh()._ready)
        self.assertTrue(os.path.exists(foreign), "yabancı dosyaya dokunulmaz")
        self._record(os.path.join(os.path.dirname(path), "Lumina_Update_424242.pkg"))  # adı bizim ama dosya yok
        self.assertIsNone(self._fresh()._ready)
        make_settings().setValue(READY_KEY, "{bozuk json")
        self.assertIsNone(self._fresh()._ready)

    def test_a_rolled_back_version_is_not_restored(self):
        path = self._package()
        self._record(path)
        with mock.patch("app.ui.updates._skipped_version", return_value="9.9.9"):
            self.assertIsNone(self._fresh()._ready)

    def test_clear_removes_the_record_and_only_deletes_our_own_files(self):
        from app.core.settings_store import make_settings
        from app.ui.updates import READY_KEY

        path = self._package()
        self._record(path)
        service = self._fresh()
        service._clear_ready(delete=True)
        self.assertFalse(os.path.exists(path))
        self.assertTrue(make_settings().value(READY_KEY, "") in ("", None))


class SourceRunTest(_Base):
    """Kaynaktan çalışırken kurulum hedefi python.exe olurdu: sessiz indirme/kurulum hiç olmamalı; elle akışta satır içi ileti."""

    def test_no_silent_download_or_exit_install_when_not_packaged(self):
        from app.ui.updates import _Ready

        service = self._service(auto=True)
        path = self._package()
        service.installer = mock.Mock(swapped=False)
        with mock.patch("app.ui.updates._is_packaged", return_value=False), \
             mock.patch("app.ui.updates.supports_auto_install", return_value=True):
            service._on_available("9.9.9", PENDING["url"], "n", True, 10, SHA)
            service._ready = _Ready(path, "9.9.9", SHA, 5, True)
            service.install_on_exit()
        service._begin_download.assert_not_called()
        service.installer.install_blocking.assert_not_called()

    def test_manual_update_shows_an_inline_message_instead_of_a_box(self):
        service = self._service(auto=True)
        service.dialog = mock.Mock()
        with mock.patch("app.ui.updates._is_packaged", return_value=False), \
             mock.patch("app.ui.updates.supports_auto_install", return_value=True), \
             mock.patch.object(MessageBox, "warning", side_effect=AssertionError("pop-up")):
            service._start_download()
        service._begin_download.assert_not_called()
        message = service.dialog.show_error.call_args[0][0]
        self.assertIn("derlenmiş", message)


class CancelTest(_Base):
    def test_cancel_while_downloading_leaves_cleanup_to_the_worker(self):
        service = self._service()
        service.installer = mock.Mock()
        service._thread = mock.Mock()
        service._thread.isRunning.return_value = True
        service._cancel()
        service.installer.cancel_download.assert_called_once()
        service.installer.cleanup.assert_not_called()  # açık dosya silinemezdi; yarım indirme %TEMP%'te kalırdı

    def test_cancel_without_a_running_download_cleans_up(self):
        service = self._service()
        service.installer = mock.Mock()
        service._thread = None
        service._cancel()
        service.installer.cleanup.assert_called_once()
        service.installer.cancel_download.assert_not_called()


class ManualFlowTest(_Base):
    """Güncelleme penceresi + hizmet birlikte: boşta -> indirme -> kurma -> hata -> tekrar dene (açılır kutu hiç yok)."""

    def _open(self, service, *, auto_install=True):
        """Pencereyi (engellemeden) açar; döner: pencere."""
        patches = [
            mock.patch("app.ui.update_dialog.UpdateDialog.exec", lambda self: 0),
            mock.patch("app.ui.updates.supports_auto_install", return_value=auto_install),
            mock.patch("app.ui.update_dialog.supports_auto_install", return_value=auto_install),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        for box in ("warning", "information", "question", "critical"):
            p = mock.patch.object(MessageBox, box, side_effect=AssertionError("pop-up açılmamalı"))
            p.start()
            self.addCleanup(p.stop)
        service.open_pending()
        self.addCleanup(lambda: service.dialog and service.dialog.close())
        return service.dialog

    def test_dialog_shows_version_size_notes_and_two_buttons(self):
        service = self._service()
        service._pending = dict(PENDING, size=5 * 1024 * 1024, notes="<p>Yenilikler</p>")
        dialog = self._open(service)
        self.assertEqual(dialog.state, "idle")
        self.assertFalse(dialog.size_label.isHidden())
        self.assertIn("5.00 MB", dialog.size_label.text())
        self.assertEqual((dialog.later_btn.isHidden(), dialog.update_btn.isHidden(), dialog.cancel_btn.isHidden()), (False, False, True))

    def test_update_click_checks_writability_first_and_reports_inline_then_retry_works(self):
        from app.core import self_update as su

        service = self._service()
        service.installer.check_writable = mock.Mock(side_effect=su.PackageError("x", "not_writable", "C:/Program Files/Lumina"))
        dialog = self._open(service)
        dialog.update_btn.click()
        service._begin_download.assert_not_called()
        self.assertEqual(dialog.state, "error", "hata durumu 'indiriliyor'u ezmeli (pencere takılı kalmamalı)")
        self.assertFalse(dialog.error_label.isHidden())
        self.assertIn("C:/Program Files/Lumina", dialog.error_label.text())
        self.assertEqual(dialog.update_btn.text(), "Tekrar Dene")
        self.assertTrue(dialog.update_btn.isEnabled())
        service.installer.check_writable = mock.Mock()  # kullanıcı klasörü düzeltti
        dialog.update_btn.click()
        service._begin_download.assert_called_once_with(silent=False)
        self.assertEqual(dialog.state, "downloading")
        self.assertTrue(dialog.error_label.isHidden())

    def test_a_second_click_while_downloading_starts_nothing(self):
        service = self._service()
        dialog = self._open(service)
        dialog.update_btn.click()
        service._begin_download.assert_called_once()
        self.assertFalse(dialog.update_btn.isEnabled(), "indirme sürerken düğme kilitli")
        dialog.update_btn.click()
        service._thread = mock.Mock()
        service._thread.isRunning.return_value = True
        service._job = {"version": "9.9.9", "checksum": SHA, "silent": False}
        service._start_download()  # hizmet düzeyinde de ikinci indirme yok
        service._begin_download.assert_called_once()

    def test_joining_a_background_download_shows_its_progress_and_installs_when_done(self):
        service = self._service()
        service._thread = mock.Mock()
        service._thread.isRunning.return_value = True
        service._job = {"version": "9.9.9", "checksum": SHA, "silent": True}
        dialog = self._open(service)
        dialog.update_btn.click()
        self.assertFalse(service._job["silent"], "arka plandaki indirmeye katıldı")
        service._begin_download.assert_not_called()
        service._on_progress(1024, 4096, 1.5)
        self.assertIn("25%", dialog.progress_label.text())

    def test_verified_download_installs_with_the_open_photo_and_then_quits_cleanly(self):
        service = self._service()
        photo = self._package(b"x")
        window = mock.Mock()
        window.current_file.return_value = pathlib.Path(photo)
        dialog = self._open(service)
        service._dialog_parent = window  # (pencerenin gerçek ebeveyni testte yok)
        service.installer.install_update = mock.Mock(return_value=True)
        service.installer.last_verified = True
        service._job = {"version": "9.9.9", "checksum": SHA, "silent": False}
        dialog._show_downloading_state()
        service._on_complete("C:/tmp/Lumina_Update_1.pkg")
        service.installer.install_update.assert_called_once_with(
            "C:/tmp/Lumina_Update_1.pkg", relaunch=True, visible=True, extra_args=[photo], expected_checksum=None,
        )
        self.assertEqual(dialog.state, "installing")
        self.assertTrue(dialog.status_label.text())
        self.assertTrue(dialog.update_btn.isHidden() and dialog.later_btn.isHidden() and dialog.cancel_btn.isHidden())
        # kurulum bitti: pencere kapanabilir hale gelir, uygulama TEMİZ çıkar (zorla öldürme yok)
        with mock.patch.object(QApplication, "closeAllWindows") as close_all, mock.patch.object(QApplication, "quit") as quit_:
            service._on_install_complete()
            self.assertFalse(service._installing)
            service._quit_for_update()
        close_all.assert_called_once()
        quit_.assert_called_once()
        self.assertTrue(dialog._allow_close)

    def test_install_failure_keeps_the_dialog_open_with_an_inline_error_and_retry(self):
        service = self._service()
        dialog = self._open(service)
        service._installing = True
        dialog.show_installing_state()
        service._on_install_failed("Çalışan uygulama dosyası değiştirilemedi: kilitli")
        self.assertFalse(service._installing)
        self.assertEqual(dialog.state, "error")
        self.assertIn("Güncelleme tamamlanamadı: Çalışan uygulama dosyası değiştirilemedi: kilitli", dialog.error_label.text())
        self.assertEqual(dialog.update_btn.text(), "Tekrar Dene")
        self.assertIsNotNone(service.dialog, "pencere açık kalır")
        service.installer.check_writable = mock.Mock()
        dialog.update_btn.click()
        service._begin_download.assert_called_once_with(silent=False)

    def test_a_ready_package_is_installed_straight_away_after_verifying_its_checksum_again(self):
        from app.ui.updates import _Ready

        service = self._service()
        path = self._package()
        service._ready = _Ready(path, "9.9.9", SHA, os.path.getsize(path), False)
        service.installer.install_update = mock.Mock(return_value=True)
        dialog = self._open(service)
        dialog.update_btn.click()
        service._begin_download.assert_not_called()
        service.installer.install_update.assert_called_once()
        self.assertEqual(service.installer.install_update.call_args.kwargs["expected_checksum"], SHA)
        self.assertEqual(dialog.state, "installing")
        self.assertIsNone(service._ready)

    def test_a_package_without_a_checksum_asks_inline_and_installs_only_on_confirmation(self):
        service = self._service()
        path = self._package()
        dialog = self._open(service)
        service.installer.install_update = mock.Mock(return_value=True)
        service.installer.last_verified = False
        service._job = {"version": "9.9.9", "checksum": "", "silent": False}
        dialog._show_downloading_state()
        service._on_complete(path)
        service.installer.install_update.assert_not_called()
        self.assertEqual(dialog.state, "unverified")
        self.assertEqual(dialog.update_btn.text(), "Yine de Kur")
        dialog.update_btn.click()
        service.installer.install_update.assert_called_once()
        self.assertEqual(dialog.state, "installing")

    def test_declining_an_unverified_package_deletes_it(self):
        service = self._service()
        path = self._package()
        dialog = self._open(service)
        service.installer.last_verified = False
        service._job = {"version": "9.9.9", "checksum": "", "silent": False}
        dialog._show_downloading_state()
        service._on_complete(path)
        dialog.later_btn.click()
        self.assertFalse(os.path.exists(path))
        self.assertIsNone(service.dialog)

    def test_escape_or_close_while_downloading_cancels_the_download(self):
        service = self._service()
        dialog = self._open(service)
        dialog.update_btn.click()
        service._thread = mock.Mock()
        service._thread.isRunning.return_value = True
        service.installer = mock.Mock()
        dialog.reject()  # Esc / X
        service.installer.cancel_download.assert_called_once()
        self.assertEqual(dialog.result(), 0)
        self.assertIsNone(service.dialog)

    def test_later_remembers_the_declined_version(self):
        from app.core.settings_store import make_settings

        service = self._service()
        dialog = self._open(service)
        dialog.later_btn.click()
        self.assertEqual(str(make_settings().value("declined_update_version")), "9.9.9")

    def test_a_second_open_raises_the_existing_window_instead_of_stacking(self):
        service = self._service()
        dialog = self._open(service)
        with mock.patch("app.ui.updates.UpdateDialog", side_effect=AssertionError("ikinci pencere açılmamalı")):
            service.open_pending()
        self.assertIs(service.dialog, dialog)

    def test_relaunch_args_carry_only_an_existing_open_file(self):
        service = self._service()
        window = mock.Mock()
        service._dialog_parent = window
        window.current_file.return_value = None
        self.assertEqual(service._relaunch_args(), [])
        window.current_file.return_value = pathlib.Path("C:/yok/olmayan dosya.jpg")
        self.assertEqual(service._relaunch_args(), [])
        photo = self._package(b"x")
        window.current_file.return_value = pathlib.Path(photo)
        self.assertEqual(service._relaunch_args(), [photo])
        window.current_file.side_effect = RuntimeError("pencere kapandı")
        self.assertEqual(service._relaunch_args(), [])

    def test_non_windows_opens_the_download_page_and_closes_the_dialog(self):
        service = self._service()
        dialog = self._open(service, auto_install=False)
        with mock.patch("app.ui.updates.QDesktopServices.openUrl", return_value=True) as open_url:
            dialog.update_btn.click()
        open_url.assert_called_once()
        self.assertEqual(dialog.update_btn.text(), "İndirme Sayfasını Aç")
        service._begin_download.assert_not_called()


class CheckerFieldTest(_Base):
    """Sunucu alanları beklenmedik tipte gelse de denetim "güncel" demez, çökmez, güncellemeyi kaybetmez."""

    URL = "https://macik.com.tr/Pages/AppDownload?slug=lumina&file=Lumina.exe"

    def _run(self, data, manual=False, current="1.0.0"):
        from app.core.updater import UpdateChecker

        checker = UpdateChecker()
        self.addCleanup(checker.deleteLater)
        checker.current_version = current
        checker._manual = manual
        offers, completes, failures = [], [], []
        checker.update_available.connect(lambda *a: offers.append(a))
        checker.check_complete.connect(completes.append)
        checker.check_failed.connect(lambda: failures.append(1))
        checker._on_worker_result(data)
        return offers, completes, failures

    def _data(self, **over):
        data = {"updateAvailable": True, "version": "2.0.0", "download_url": self.URL, "release_notes": "n",
                "isMandatory": False, "fileSize": 1234, "checksum": SHA}
        data.update(over)
        return data

    def test_string_booleans_are_read_correctly(self):
        offers, completes, _ = self._run(self._data(updateAvailable="false"))
        self.assertEqual((offers, completes), ([], [False]))
        offers, _, _ = self._run(self._data(isMandatory="false"))
        self.assertFalse(offers[0][3])
        offers, _, _ = self._run(self._data(isMandatory="true"))
        self.assertTrue(offers[0][3])
        offers, _, _ = self._run(self._data(updateAvailable="true"))
        self.assertEqual(len(offers), 1)

    def test_file_size_may_be_null_text_float_or_huge(self):
        for raw, expected in ((None, 0), ("", 0), ("987654", 987654), (1234.0, 1234), ("1234.0", 1234), (-5, 0),
                              ("abc", 0), (True, 0), (10**15, 10**15), (float("nan"), 0), (3_000_000_000, 3_000_000_000)):
            with self.subTest(raw=raw):
                offers, completes, failures = self._run(self._data(fileSize=raw), manual=True)
                self.assertEqual((len(offers), completes, failures), (1, [True], []), "boyut yüzünden güncelleme kaybolmaz")
                self.assertEqual(offers[0][4], expected)

    def test_other_fields_may_be_null_or_numbers(self):
        offers, completes, _ = self._run(self._data(release_notes=None, checksum=None, version=2))
        self.assertEqual(completes, [True])
        self.assertEqual((offers[0][0], offers[0][2], offers[0][5]), ("2", "", ""))
        self.assertEqual(self._run(self._data(version="v2.0.0"))[0][0][0], "2.0.0")

    def test_an_offer_does_not_stamp_the_check_but_a_current_reply_does(self):
        self._run(self._data())
        self.assertIsNone(self._stamp())
        self._run({"updateAvailable": False})
        self.assertIsNotNone(self._stamp())

    def test_older_or_equal_version_is_current_and_stamps(self):
        offers, completes, _ = self._run(self._data(version="1.0.0"))
        self.assertEqual((offers, completes), ([], [False]))
        self.assertIsNotNone(self._stamp())

    def test_a_version_that_was_rolled_back_is_never_offered_again(self):
        with mock.patch("app.core.updater._skipped_version", return_value="2.0.0"):
            offers, completes, _ = self._run(self._data())
            self.assertEqual((offers, completes), ([], [False]))
            self.assertIsNotNone(self._stamp())
            self.assertEqual(len(self._run(self._data(version="2.0.1"))[0]), 1, "sonraki sürüm yine önerilir")

    def test_invalid_url_is_a_failed_check_not_a_current_one(self):
        offers, completes, failures = self._run(self._data(download_url="http://evil.example/x.exe"), manual=True)
        self.assertEqual((offers, completes, failures), ([], [False], [1]))

    def test_a_non_object_reply_is_a_failure_not_up_to_date(self):
        import json as _json

        from app.core import updater

        worker = updater._UpdateCheckWorker("https://macik.com.tr/api/updates/check", None)
        results, errors = [], []
        worker.result_ready.connect(results.append)
        worker.check_error.connect(errors.append)
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = _json.dumps([1, 2]).encode()
        with mock.patch("app.core.updater.urllib.request.urlopen", return_value=resp):
            worker._run()
        self.assertEqual((results, len(errors)), ([], 1), "bozuk yanıt 'güncel' sayılmaz")

    def test_manual_check_reports_failure_for_offline_and_for_bad_replies(self):
        from app.ui.updates import UpdateService

        service = UpdateService()
        self.addCleanup(service.deleteLater)
        got = []
        service.manual_result.connect(lambda o, k, t: got.append(k))
        for failing in (lambda: service.checker._on_worker_network_error("çevrimdışı"),
                        lambda: service.checker._on_worker_error("bozuk")):
            service._manual_origin = "settings"
            service.checker._manual = True
            failing()
        self.assertEqual(got, ["fail", "fail"])


class ManualCheckTest(_Base):
    def _emitted(self, service, failed, has_update):
        got = []
        service.manual_result.connect(lambda o, k, t: got.append((o, k, t)))
        service._manual_origin = "settings"
        if failed:
            service._on_check_failed()
        service._on_check_complete(has_update)
        return got

    def test_up_to_date_failed_and_new(self):
        service = self._service()
        self.assertEqual(self._emitted(service, False, False)[0][:2], ("settings", "ok"))
        self.assertEqual(self._emitted(service, True, False)[0][:2], ("settings", "fail"))
        self.assertEqual(self._emitted(service, False, True)[0][:2], ("settings", "new"))

    def test_background_check_stays_silent(self):
        service = self._service()
        got = []
        service.manual_result.connect(lambda *a: got.append(a))
        service._on_check_complete(False)
        self.assertEqual(got, [])

    def test_checker_never_opens_message_boxes_and_reports_offline_as_failure(self):
        from app.core.updater import UpdateChecker

        checker = UpdateChecker()
        self.addCleanup(checker.deleteLater)
        order = []
        checker.check_failed.connect(lambda: order.append("failed"))
        checker.check_complete.connect(lambda has: order.append("complete"))
        checker._manual = True
        with mock.patch.object(MessageBox, "information", side_effect=AssertionError("pop-up")), \
             mock.patch.object(MessageBox, "warning", side_effect=AssertionError("pop-up")):
            checker._on_worker_network_error("çevrimdışı")
            checker._show_info("t", "m")
            checker._show_warning("t", "m")
        self.assertEqual(order, ["failed", "complete"], "çevrimdışıyken 'güncel' denmemeli")


class PillTest(_Base):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.resize(1100, 600)
        cls.win.show()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.close()
        cls.win.deleteLater()
        super().tearDownClass()

    def test_pill_shows_version_follows_language_and_hides(self):
        win = self.win
        self.assertTrue(win.btn_update.isHidden())
        win.mark_update_available(True, "1.2.3")
        self.assertFalse(win.btn_update.isHidden())
        self.assertEqual(win.btn_update.text(), "Güncelle · v1.2.3")
        win.lang = "en"
        win.retranslate_ui()
        self.assertEqual(win.btn_update.text(), "Update · v1.2.3")
        win.lang = "tr"
        win.retranslate_ui()
        win.mark_update_available(False)
        self.assertTrue(win.btn_update.isHidden())

    def test_pill_does_not_overlap_the_window_buttons(self):
        win = self.win
        win.mark_update_available(True, "1.2.3")
        wait(150)
        pill = win.btn_update.geometry()
        settings = win.btn_settings.geometry()
        self.assertLessEqual(pill.right(), settings.left())
        self.assertGreaterEqual(pill.width(), win.btn_update.sizeHint().width(), "düğme sıkışıp metni kırpılmamalı")
        self.assertGreaterEqual(win.nav_bar.width(), win.nav_bar.sizeHint().width(), "kapsül düğmeyi sığdıracak kadar genişlemeli")
        win.mark_update_available(False)
        wait(50)
        self.assertLess(win.nav_bar.width(), 200, "düğme gidince kapsül eski genişliğine dönmeli")

    def test_click_opens_the_details_for_the_pending_update(self):
        win = self.win
        win.mark_update_available(True, "1.2.3")
        service = mock.Mock()
        with mock.patch("app.ui.updates.get_update_service", return_value=service):
            win.btn_update.click()
        service.open_pending.assert_called_once()
        win.mark_update_available(False)

    def test_new_window_gets_the_button_from_the_service(self):
        from app.ui.updates import UpdateService

        service = UpdateService()
        self.addCleanup(service.deleteLater)
        service._pending = dict(PENDING)
        service.sync_window(self.win)
        self.assertEqual(self.win.btn_update.text(), "Güncelle · v9.9.9")
        self.win.mark_update_available(False)


class SettingsDialogTest(_Base):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls) -> None:
        # Kapat ve ertelenmiş silmeleri hemen işle: processEvents() DeferredDelete'i işlemez; açık kalan
        # diyalog/pencere sonraki test modüllerinde yeni MainWindow gösterilirken erişim ihlaline yol açıyordu.
        cls.win.close()
        cls.win.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        super().tearDownClass()

    def _dialog(self):
        from app.ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self.win)
        self.addCleanup(lambda: (dlg.close(), dlg.deleteLater()))
        return dlg

    def test_auto_install_is_checked_by_default_and_persists(self):
        from app.core.settings_store import make_settings

        make_settings().remove("auto_update_install")
        dlg = self._dialog()
        self.assertTrue(dlg.chk_auto_install.isChecked())
        dlg.chk_auto_install.setChecked(False)
        self.assertFalse(str(make_settings().value("auto_update_install")).lower() in ("true", "1"))
        dlg.chk_auto_install.setChecked(True)

    def test_button_says_kontrol_et_and_animates_until_the_result(self):
        dlg = self._dialog()
        dlg.show()
        self.assertEqual(dlg.btn_update.text(), "Kontrol Et")
        dlg.update_check.MIN_MS = 150
        service = mock.Mock()
        with mock.patch("app.ui.settings_dialog.get_update_service", return_value=service):
            dlg._on_check_updates_clicked()
        service.check_now.assert_called_once_with("settings")
        self.assertFalse(dlg.btn_update.isEnabled())
        dlg._on_manual_update_result("settings", "ok", "Güncel · v1.0.0")  # sonuç anında geldi
        wait(40)
        self.assertFalse(dlg.btn_update.isEnabled(), "en az süre dolmadan eski haline dönmemeli")
        wait(400)
        self.assertTrue(dlg.btn_update.isEnabled())
        self.assertEqual(dlg.btn_update.text(), "Kontrol Et")
        self.assertIn("Güncel · v1.0.0", dlg.lbl_update_status.text())

    def test_results_for_other_origins_are_ignored(self):
        dlg = self._dialog()
        dlg._on_manual_update_result("menu", "ok", "Güncel")
        self.assertEqual(dlg.lbl_update_status.text(), "")

    def test_closed_dialog_is_deleted_and_stops_listening(self):
        from app.ui.settings_dialog import SettingsDialog
        from app.ui.updates import UpdateService

        service = UpdateService()
        self.addCleanup(service.deleteLater)
        heard: list = []
        with mock.patch("app.ui.settings_dialog.get_update_service", return_value=service), \
             mock.patch.object(SettingsDialog, "exec", new=lambda _self: 0), \
             mock.patch.object(SettingsDialog, "_on_manual_update_result", new=lambda _self, *a: heard.append(a)):
            self.win.open_settings_dialog()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            service.manual_result.emit("settings", "ok", "Güncel")
        self.assertEqual(self.win.findChildren(SettingsDialog), [], "kapanan diyalog pencerede birikmemeli")
        self.assertEqual(heard, [], "silinen diyalog güncelleme sonuçlarını dinlememeli")


class LocaleTest(unittest.TestCase):
    def test_new_strings_exist_in_both_languages(self):
        from app.resources.locales import t

        for lang in ("tr", "en"):
            for key in ("check_btn", "update_group", "update_pill", "update_pill_tip", "update_new_version",
                        "update_status_current", "update_status_failed", "auto_update_install",
                        "auto_update_install_tip", "update_size_line", "update_failed_inline", "update_retry",
                        "update_installed_toast", "update_install_anyway", "update_err_not_writable",
                        "update_err_source_run", "update_err_checksum_mismatch"):
                self.assertNotEqual(t(key, lang), key, (lang, key))
        self.assertEqual(t("check_btn", "tr"), "Kontrol Et")
        self.assertEqual(t("update_size_line", "tr").format(size="5 MB"), "İndirme boyutu: 5 MB")
        self.assertEqual(t("update_size_line", "en").format(size="5 MB"), "Download size: 5 MB")
        self.assertEqual(t("update_failed_inline", "en").format(error="x"), "The update could not be completed: x")
        self.assertEqual(t("update_failed_inline", "tr").format(error="x"), "Güncelleme tamamlanamadı: x")
        self.assertEqual(t("update_installed_toast", "tr").format(app="Lumina", version="1.0.1"), "Lumina v1.0.1 sürümüne güncellendi.")
        self.assertEqual(t("update_installed_toast", "en").format(app="Lumina", version="1.0.1"), "Lumina was updated to v1.0.1.")
        self.assertNotIn("kullanılmıyorken", t("auto_update_install", "tr"))


if __name__ == "__main__":
    unittest.main()
