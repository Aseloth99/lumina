"""Uçtan uca (süreç içi): gerçek UpdateService + gerçek indirme (yerel HTTP sunucusu) + gerçek dosya değiştirme.

Yalnızca iki şey sahtelenir: yeni sürümü başlatan `self_update.spawn` ve uygulamadan çıkış (`QApplication.quit`).
Uygulamanın kendi exe'si geçici klasördeki sahte bir "Lumina.exe"dir; kullanıcının kopyasına ve ağa dokunulmaz.
"""

from __future__ import annotations

import hashlib
import http.server
import io
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QCoreApplication, QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.ui.message_box import MessageBox  # noqa: E402

from app.core import self_update as su  # noqa: E402
from app.core import update_installer as ui  # noqa: E402


def fake_pe(machine: int = 0x8664, payload: bytes = b"") -> bytes:
    head = bytearray(0x80)
    head[:2] = b"MZ"
    head[0x3C:0x40] = (0x80).to_bytes(4, "little")
    return bytes(head) + b"PE\0\0" + machine.to_bytes(2, "little") + payload


def make_zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


NEW_EXE = fake_pe(0x8664, b"-yeni-surum" * 2000)
ZIP = make_zip({"Lumina/Lumina.exe": NEW_EXE, "Lumina/okubeni.txt": b"yan dosya"})
SHA = hashlib.sha256(ZIP).hexdigest()


def pump(cond, timeout: float = 15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return cond()


class _Handler(http.server.BaseHTTPRequestHandler):
    slow = False
    hits = 0  # indirme isteği sayısı (aynı paket yeniden indirilmedi mi)

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path != "/Pages/AppDownload":
            self.send_response(404)
            self.end_headers()
            return
        _Handler.hits += 1
        self.send_response(200)
        self.send_header("Content-Length", str(len(ZIP)))
        self.end_headers()
        try:
            if _Handler.slow:
                for i in range(0, len(ZIP), 2048):
                    self.wfile.write(ZIP[i:i + 2048])
                    self.wfile.flush()
                    time.sleep(0.02)
            else:
                self.wfile.write(ZIP)
        except OSError:
            pass


class FlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._tmp.name)
        cls.app = QApplication.instance() or QApplication([])
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/Pages/AppDownload?slug=lumina&file=Lumina.exe"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def setUp(self) -> None:
        from app.core.settings_store import make_settings
        from app.resources import locales

        _Handler.slow = False
        _Handler.hits = 0
        settings = make_settings()
        for key in ("last_update_check", "declined_update_version", "update_ready"):
            settings.remove(key)
        for patcher in (
            mock.patch.object(locales, "get_active_language", lambda lang=None: lang if lang in locales.LOCALES else "tr"),
            mock.patch("app.ui.updates._is_packaged", return_value=True),
            mock.patch("app.ui.updates.supports_auto_install", return_value=True),
            mock.patch("app.ui.update_dialog.supports_auto_install", return_value=True),
            mock.patch.object(ui, "can_self_update", return_value=True),
            mock.patch("app.ui.update_dialog.UpdateDialog.exec", lambda self: 0),
            # indirme kilidi gerçek %TEMP%'teki (kullanıcının çalışan Lumina'sının) kilidine değil teste özel dosyaya yazılır
            mock.patch.object(ui, "default_download_lock_path",
                              return_value=pathlib.Path(self._tmp.name) / f"Lumina.update.{time.monotonic_ns()}.lock"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        for box in ("warning", "information", "question", "critical"):
            patcher = mock.patch.object(MessageBox, box, side_effect=AssertionError("açılır kutu olmamalı"))
            patcher.start()
            self.addCleanup(patcher.stop)
        # Kullanıcının exe'si: Türkçe karakterli, boşluklu klasörde sahte başlatıcı
        self.folder = os.path.join(self._tmp.name, f"Lumina klasörü {time.monotonic_ns()}")
        os.makedirs(self.folder)
        self.exe = os.path.join(self.folder, "Lumina.exe")
        self.old = fake_pe(0x8664, b"-eski-surum")
        with open(self.exe, "wb") as f:
            f.write(self.old)
        patcher = mock.patch.object(ui, "app_executable", return_value=pathlib.Path(self.exe))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(su, "spawn")
        self.spawn = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(QApplication, "quit")
        self.quit = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(QApplication, "closeAllWindows")
        patcher.start()
        self.addCleanup(patcher.stop)
        created: list = []
        real_init = ui.DownloadLock.__init__

        def tracking_init(lock, *args, **kwargs):
            real_init(lock, *args, **kwargs)
            created.append(lock)

        patcher = mock.patch.object(ui.DownloadLock, "__init__", tracking_init)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: [lock.release() for lock in created])  # açık kilit dosyası tearDownClass'ta silinemezdi
        self.addCleanup(self._drain_quit_timer)  # (LIFO: önce çalışır, `quit` sahtesi hâlâ yerinde)

    def _drain_quit_timer(self) -> None:
        """Başarılı elle kurulum `QTimer.singleShot(400, _quit_for_update)` bırakır; sönmeden test biterse zamanlayıcı sonraki
        testin `quit` sahtesini 'çağrılmış' gösterir (sıraya bağlı kırılganlık). Bu testte söndür."""
        if self.spawn.called:
            pump(lambda: self.quit.called, 3)

    def service(self, auto: bool):
        from app.core.settings_store import make_settings
        from app.ui.updates import UpdateService

        make_settings().setValue("auto_update_install", auto)
        service = UpdateService()
        self.addCleanup(service.deleteLater)
        return service

    def read(self, path):
        with open(path, "rb") as f:
            return f.read()

    def offer(self, service, checksum=SHA, mandatory=False):
        service._on_available("1.0.1", self.url, "<p>Yenilikler</p>", mandatory, len(ZIP), checksum)

    def listing(self):
        return sorted(os.listdir(self.folder))

    # ---- elle akış
    def test_manual_flow_downloads_installs_and_restarts_cleanly(self):
        service = self.service(auto=False)
        self.offer(service)
        service.open_pending()
        dialog = service.dialog
        self.assertEqual(dialog.state, "idle")
        dialog.update_btn.click()
        self.assertTrue(pump(lambda: self.spawn.called, 20), f"kurulum bitmedi; durum={dialog.state} hata={dialog.error_label.text()}")
        self.assertEqual(self.read(self.exe), NEW_EXE, "yeni exe yerine kondu (zip içindeki iç klasörden)")
        self.assertEqual(self.read(self.exe + ".old"), self.old)
        self.assertEqual(self.listing(), ["Lumina.exe", "Lumina.exe.old"], "klasöre başka dosya yazılmadı, .new artığı yok")
        exe, args = self.spawn.call_args[0]
        self.assertEqual(exe, self.exe)
        self.assertEqual(list(args), ["--wait-pid", str(os.getpid()), "--updated", ui.get_version(), "--show-window"])
        self.assertFalse(os.path.exists(ui.package_path()), "indirilen paket temizlendi")
        self.assertTrue(pump(lambda: self.quit.called, 5), "uygulama TEMİZ çıkış yapar (QApplication.quit)")
        self.assertEqual(dialog.state, "installing")
        self.assertIn("yeniden başlatılıyor", dialog.status_label.text())
        self.assertTrue(service.installer.swapped)

    def test_the_open_photo_is_passed_to_the_new_version(self):
        service = self.service(auto=False)
        self.offer(service)
        photo = os.path.join(self.folder, "gün batımı.jpg")
        with open(photo, "wb") as f:
            f.write(b"x")
        service.open_pending()
        window = mock.Mock()
        window.current_file.return_value = pathlib.Path(photo)
        service._dialog_parent = window
        service.dialog.update_btn.click()
        self.assertTrue(pump(lambda: self.spawn.called, 20))
        self.assertEqual(list(self.spawn.call_args[0][1])[-2:], ["--show-window", photo])

    def test_wrong_checksum_shows_an_inline_error_then_retry_succeeds(self):
        service = self.service(auto=False)
        self.offer(service, checksum="0" * 64)
        service.open_pending()
        dialog = service.dialog
        dialog.update_btn.click()
        self.assertTrue(pump(lambda: dialog.state == "error", 20))
        self.assertIn("Güncelleme tamamlanamadı: Dosya bütünlüğü doğrulanamadı", dialog.error_label.text())
        self.assertEqual(self.read(self.exe), self.old, "çalışan exe'ye dokunulmadı")
        self.assertEqual(self.listing(), ["Lumina.exe"])
        self.assertFalse(self.spawn.called)
        self.assertIsNotNone(service.dialog, "pencere açık kalır")
        self.assertEqual(dialog.update_btn.text(), "Tekrar Dene")
        service._pending["checksum"] = SHA  # sunucu düzeltildi
        dialog.update_btn.click()
        self.assertTrue(pump(lambda: self.spawn.called, 20), dialog.error_label.text())
        self.assertEqual(self.read(self.exe), NEW_EXE)

    def test_closing_the_window_while_downloading_cancels_without_installing(self):
        _Handler.slow = True
        service = self.service(auto=False)
        self.offer(service)
        service.open_pending()
        dialog = service.dialog
        dialog.update_btn.click()
        self.assertTrue(pump(lambda: service._thread is not None and service._thread.isRunning()))
        time.sleep(0.2)
        dialog.reject()  # Esc / X
        self.assertTrue(pump(lambda: service._thread is None or not service._thread.isRunning(), 20))
        pump(lambda: False, 0.3)
        self.assertFalse(self.spawn.called, "iptal edilen indirme uygulamayı habersiz yeniden başlatmaz")
        self.assertEqual(self.read(self.exe), self.old)
        self.assertEqual(self.listing(), ["Lumina.exe"])
        self.assertFalse(os.path.exists(ui.package_path()), "yarım indirme silindi")
        self.assertIsNone(service.dialog)
        # yeni indirme başlatılabilir
        _Handler.slow = False
        service.open_pending()
        service.dialog.update_btn.click()
        self.assertTrue(pump(lambda: self.spawn.called, 20))

    def test_unwritable_folder_is_reported_before_anything_is_downloaded(self):
        service = self.service(auto=False)
        self.offer(service)
        service.open_pending()
        dialog = service.dialog
        with mock.patch.object(ui, "app_executable", return_value=pathlib.Path(self.folder) / "yok" / "Lumina.exe"):
            dialog.update_btn.click()
        self.assertEqual(dialog.state, "error")
        self.assertIn("yazılamıyor", dialog.error_label.text())
        self.assertIn("taşıyıp", dialog.error_label.text())
        self.assertIsNone(service._thread, "indirme hiç başlamadı")

    # ---- sessiz akış
    def test_silent_flow_downloads_in_the_background_and_installs_on_exit_without_restart(self):
        service = self.service(auto=True)
        self.offer(service)
        self.assertTrue(pump(lambda: service._ready is not None, 20), "sessiz indirme tamamlanmadı")
        self.assertEqual(service._ready.version, "1.0.1")
        self.assertIsNotNone(self._stamp(), "indirme bitince denetim damgası atıldı")
        self.assertEqual(self.read(self.exe), self.old, "çıkıştan önce hiçbir şey değişmez")
        self.assertIsNone(service.dialog, "pencere/kutu yok")
        service.shutdown()  # Lumina kapanıyor
        self.assertEqual(self.read(self.exe), NEW_EXE)
        self.assertEqual(self.read(self.exe + ".old"), self.old)
        self.assertFalse(self.spawn.called, "çıkışta kurulumda yeniden başlatma yok")
        self.assertFalse(self.quit.called)
        self.assertEqual(self.listing(), ["Lumina.exe", "Lumina.exe.old"])
        self.assertTrue(service.installer.swapped)
        self.assertIsNone(service._ready)

    def test_closing_during_a_silent_download_leaves_no_stamp_and_nothing_installed(self):
        _Handler.slow = True
        service = self.service(auto=True)
        self.offer(service)
        self.assertTrue(pump(lambda: service._thread is not None and service._thread.isRunning()))
        time.sleep(0.2)
        service.shutdown()
        self.assertEqual(self.read(self.exe), self.old)
        self.assertIsNone(self._stamp(), "yarıda kalan indirme yüzünden 24 saat denetimsiz kalınmaz")
        self.assertFalse(os.path.exists(ui.package_path()))
        self.assertEqual(self.listing(), ["Lumina.exe"])

    def test_a_package_downloaded_in_an_earlier_session_is_installed_by_the_next_one(self):
        first = self.service(auto=True)
        self.offer(first)
        self.assertTrue(pump(lambda: first._ready is not None, 20))
        # süreç öldürüldü (kapanış kurulumu yapılamadı); bir sonraki oturum kaydı okur
        second = self.service(auto=True)
        self.assertIsNotNone(second._ready)
        second.shutdown()
        self.assertEqual(self.read(self.exe), NEW_EXE)

    def test_a_tampered_package_is_never_installed(self):
        service = self.service(auto=True)
        self.offer(service)
        self.assertTrue(pump(lambda: service._ready is not None, 20))
        with open(service._ready.path, "ab") as f:  # %TEMP%'te değiştirildi
            f.write(b"kurcalandi")
        service.shutdown()
        self.assertEqual(self.read(self.exe), self.old)
        self.assertEqual(self.listing(), ["Lumina.exe"])

    # ---- B6: 'Daha Sonra' denen sürüm çıkışta kurulmaz
    def test_a_declined_update_is_not_installed_on_exit_but_can_still_be_installed_by_hand(self):
        service = self.service(auto=True)
        self.offer(service)
        self.assertTrue(pump(lambda: service._ready is not None, 20), "sessiz indirme tamamlanmadı")
        path = service._ready.path
        service._defer()  # güncelleme penceresinde 'Daha Sonra' (indirme önceden bitmişti)
        self.assertTrue(os.path.exists(path), "oturum sürerken hazır paket durur")
        service.shutdown()  # Lumina kapanıyor
        self.assertEqual(self.read(self.exe), self.old, "reddedilen sürüm çıkışta KURULMAZ")
        self.assertFalse(os.path.exists(self.exe + ".old"))
        self.assertFalse(os.path.exists(path), "paket %TEMP%'te bırakılmaz")
        self.assertFalse(service.installer.swapped)
        # bir sonraki açılış: aynı sürüm yeniden sunulur, sessizce indirilmez; düğme kalır ve elle kurulabilir
        hits = _Handler.hits
        second = self.service(auto=True)
        self.offer(second)
        pump(lambda: False, 0.4)
        self.assertEqual(_Handler.hits, hits, "reddedilen sürüm için yeniden indirme yok")
        self.assertIsNotNone(second._pending)
        second.open_pending()
        second.dialog.update_btn.click()  # 'Şimdi Güncelle'
        self.assertTrue(pump(lambda: self.spawn.called, 20), second.dialog.error_label.text())
        self.assertEqual(self.read(self.exe), NEW_EXE)

    def test_a_newer_version_resets_the_decline_and_is_installed_on_exit(self):
        from app.core.settings_store import make_settings

        make_settings().setValue("declined_update_version", "1.0.0")  # eski bir sürüme 'Daha Sonra' denmişti
        service = self.service(auto=True)
        self.offer(service)  # 1.0.1
        self.assertTrue(pump(lambda: service._ready is not None, 20))
        service.shutdown()
        self.assertEqual(self.read(self.exe), NEW_EXE)

    # ---- K2c: iki pencere, tek indirme
    def test_two_windows_download_the_package_only_once_and_leave_nothing_behind(self):
        first = self.service(auto=True)
        second = self.service(auto=True)  # ikinci Lumina penceresi (aynı kilit dosyası)
        _Handler.slow = True
        self.offer(first)
        self.assertTrue(pump(lambda: first._thread is not None and first._thread.isRunning()))
        self.offer(second)  # aynı güncellemeyi o da buldu
        pump(lambda: False, 0.3)
        self.assertIsNone(second._thread, "ikinci pencere indirmeye başlamadı")
        _Handler.slow = False
        self.assertTrue(pump(lambda: first._ready is not None, 30), "ilk pencerenin indirmesi tamamlanmadı")
        self.assertEqual(_Handler.hits, 1, "paket yalnızca bir kez indirildi")
        first.shutdown()  # ilk kapanan kurar
        self.assertEqual(self.read(self.exe), NEW_EXE)
        second.shutdown()  # ikincinin paketi yok: hiçbir şey yapmaz
        self.assertEqual(self.listing(), ["Lumina.exe", "Lumina.exe.old"])
        self.assertFalse(os.path.exists(ui.package_path()), "sahipsiz paket kalmadı")

    # ---- K2a: başka pencere kurmuşsa ikinci pencere kendi paketini siler
    def test_a_window_that_skips_the_exit_install_removes_its_own_package(self):
        service = self.service(auto=True)
        self.offer(service)
        self.assertTrue(pump(lambda: service._ready is not None, 20))
        path = service._ready.path
        # başka bir pencere güncellemeyi çoktan kurdu (geri alma noktası var) ve ortak kaydı sildi
        with open(self.exe + ".old", "wb") as f:
            f.write(self.old)
        from app.core.settings_store import make_settings

        make_settings().remove("update_ready")
        service.shutdown()
        self.assertFalse(os.path.exists(path), "kurulmayan paket %TEMP%'te sahipsiz kalmaz")
        self.assertEqual(self.read(self.exe), self.old, "geri alma noktasının üstüne yazılmadı")

    # ---- swap_exe geçici paylaşım ihlali
    def _busy_replace(self):
        real = os.replace

        def flaky(src, dst):
            if src == self.exe:
                raise OSError(13, "Başka bir işlem dosyayı kullanıyor", None, 32)
            return real(src, dst)

        return flaky

    def test_a_sharing_violation_during_the_swap_keeps_the_package_and_retry_does_not_download_again(self):
        service = self.service(auto=False)
        self.offer(service)
        service.open_pending()
        dialog = service.dialog
        with mock.patch.object(su.os, "replace", self._busy_replace()), mock.patch.object(su.time, "sleep"):
            dialog.update_btn.click()
            self.assertTrue(pump(lambda: dialog.state == "error", 20), dialog.status_label.text())
        self.assertIn("değiştirilemedi", dialog.error_label.text())
        self.assertEqual(dialog.update_btn.text(), "Tekrar Dene")
        self.assertEqual(_Handler.hits, 1)
        self.assertTrue(os.path.exists(ui.package_path()), "paket hemen silinmedi")
        self.assertEqual(self.read(self.exe), self.old)
        self.assertEqual(self.listing(), ["Lumina.exe"], ".new/.old artığı yok")
        self.assertFalse(self.spawn.called)
        dialog.update_btn.click()  # 'Tekrar dene': antivirüs taraması bitti
        self.assertTrue(pump(lambda: self.spawn.called, 20), dialog.error_label.text())
        self.assertEqual(_Handler.hits, 1, "138 MB yeniden indirilmedi")
        self.assertEqual(self.read(self.exe), NEW_EXE)
        self.assertFalse(os.path.exists(ui.package_path()), "kurulunca paket tüketilir")

    def test_a_prefixed_uppercase_checksum_still_installs_on_exit(self):
        service = self.service(auto=True)
        self.offer(service, checksum="SHA256:" + SHA.upper())
        self.assertTrue(pump(lambda: service._ready is not None, 20))
        service.shutdown()
        self.assertEqual(self.read(self.exe), NEW_EXE, "ham (önekli/büyük harfli) sağlama toplamı çıkışta yanlış reddedilmez")

    @staticmethod
    def _stamp():
        from app.core.settings_store import make_settings

        return make_settings().value("last_update_check", None)


if __name__ == "__main__":
    unittest.main()
