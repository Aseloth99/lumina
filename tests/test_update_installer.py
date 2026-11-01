"""UpdateInstaller: indirme doğrulama matrisi (yerel HTTP sunucusu) ve GERÇEK süreç içi kurulum (self_update).

Kurulum testleri gerçek dosya işlemleriyle çalışır (Türkçe karakterli, boşluklu ve %!& içeren klasörde) ama hiçbir
süreç başlatmaz: yeni sürümü başlatan `self_update.spawn` sahtelenir. Eski batch betiği testlerinin güvencesi (paketten
yalnızca exe, exe'siz paketle uygulamaya dokunulmaz, özel karakterli yollar, bozuk paket) burada korunur.
"""

from __future__ import annotations

import hashlib
import http.server
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.core import self_update as su  # noqa: E402
from app.core import update_installer as ui  # noqa: E402
from app.core.update_installer import PACKAGE_PREFIX, UpdateInstaller, package_path  # noqa: E402

BODY = b"PK\x03\x04" + b"fake-update-payload" * 500
SHA = hashlib.sha256(BODY).hexdigest()
TURKISH = set("ğüşıöçĞÜŞİÖÇ")


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


def pump(cond, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return cond()


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # sessiz
        pass

    def _body(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/ok.zip", "/Pages/AppDownload") or path.startswith("/odd"):
            self._body(BODY)  # uzantısız adres ve sıra dışı adlar da aynı paketi verir
        elif path == "/html":
            self._body(b"<html>404 not found</html>")
        elif path == "/redirect.zip":
            self.send_response(302)
            self.send_header("Location", "http://evil.example/x.zip")
            self.end_headers()
        elif path == "/short.zip":
            self.send_response(200)
            self.send_header("Content-Length", str(len(BODY) + 1000))
            self.end_headers()
            self.wfile.write(BODY)
            self.wfile.flush()
            self.close_connection = True
        else:
            self.send_response(404)
            self.end_headers()


class DownloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        self.inst = UpdateInstaller()
        self.inst.lang = "tr"
        self.addCleanup(self.inst.cleanup)
        self.failed: list[str] = []
        self.done: list[str] = []
        self.inst.download_failed.connect(self.failed.append)
        self.inst.download_complete.connect(self.done.append)

    def test_valid_checksum_succeeds_and_is_verified(self) -> None:
        self.assertTrue(self.inst.download_update(f"{self.base}/ok.zip", len(BODY), SHA))
        self.assertTrue(self.inst.last_verified)
        path = pathlib.Path(self.done[0])
        self.assertEqual(path.read_bytes(), BODY)
        self.assertEqual(str(path), package_path(), "paket %TEMP%'te sabit adla (pid'li) durur")
        self.assertTrue(path.name.startswith(PACKAGE_PREFIX))

    def test_the_local_name_never_comes_from_the_server(self) -> None:
        # Uzantısız adres, sıra dışı/düşmanca adlar: yerel dosya adı hep aynı, paket türü içeriğe bakılarak anlaşılır.
        for url in (f"{self.base}/Pages/AppDownload?slug=lumina&file=Lumina.exe", f"{self.base}/odd..%2F..%2Fevil.exe",
                    f"{self.base}/odd%20name%25%21.bin"):
            self.done.clear()
            self.assertTrue(self.inst.download_update(url, len(BODY), SHA), url)
            self.assertEqual(self.done, [package_path()], url)
            self.assertEqual(su.sniff_package(self.done[0]), "zip")

    def test_wrong_checksum_fails_and_removes_file(self) -> None:
        self.assertFalse(self.inst.download_update(f"{self.base}/ok.zip", len(BODY), "0" * 64))
        self.assertIn("checksum", self.failed[0])
        self.assertEqual(self.inst.last_fail_kind, "checksum")
        self.assertIsNone(self.inst.downloaded_file)
        self.assertFalse(os.path.exists(package_path()))

    def test_checksum_may_be_uppercase_or_prefixed(self) -> None:
        for value in (SHA.upper(), "sha256:" + SHA, "SHA256:" + SHA.upper()):
            self.assertTrue(self.inst.download_update(f"{self.base}/ok.zip", len(BODY), value), value)
            self.assertTrue(self.inst.last_verified)

    def test_missing_checksum_is_downloaded_but_flagged_unverified(self) -> None:
        self.assertTrue(self.inst.download_update(f"{self.base}/ok.zip", len(BODY), None))
        self.assertFalse(self.inst.last_verified)

    def test_unverified_html_error_page_is_rejected_instead_of_offered(self) -> None:
        self.assertFalse(self.inst.download_update(f"{self.base}/html", None, None))
        self.assertEqual(self.done, [])
        self.assertIn("exe ya da zip değil", self.failed[0])
        self.assertFalse(os.path.exists(package_path()))

    def test_malformed_checksum_rejected_before_download(self) -> None:
        self.assertFalse(self.inst.download_update(f"{self.base}/ok.zip", len(BODY), "xyz"))
        self.assertEqual(self.done, [])
        self.assertIn("geçersiz", self.failed[0])

    def test_size_mismatch_fails(self) -> None:
        self.assertFalse(self.inst.download_update(f"{self.base}/ok.zip", len(BODY) + 5, SHA))
        self.assertIn("boyut", self.failed[0].lower())

    def test_size_may_arrive_as_text_float_or_garbage(self) -> None:
        for size in (str(len(BODY)), float(len(BODY)), f"{len(BODY)}.0", None, 0, "", "abc", float("nan")):
            self.done.clear()
            self.assertTrue(self.inst.download_update(f"{self.base}/ok.zip", size, SHA), repr(size))
        self.assertFalse(self.inst.download_update(f"{self.base}/ok.zip", 10**12, SHA), "sınırı aşan boyut indirilmeden reddedilir")
        self.assertIn("sınırı", self.failed[-1])

    def test_redirect_to_untrusted_host_is_refused(self) -> None:
        self.assertFalse(self.inst.download_update(f"{self.base}/redirect.zip", None, SHA))
        self.assertIn("Yönlendirme reddedildi", self.failed[0])

    def test_incomplete_download_fails(self) -> None:
        self.assertFalse(self.inst.download_update(f"{self.base}/short.zip", None, SHA))
        self.assertEqual(self.done, [])

    def test_http_error_is_reported(self) -> None:
        self.assertFalse(self.inst.download_update(f"{self.base}/yok.zip", None, SHA))
        self.assertIn("404", self.failed[0])

    def test_cancel_during_the_final_read_ends_as_cancelled(self) -> None:
        # İptal son okuma sürerken gelirse: doğrulama/kurulum yok, 'cancelled', paket silinmiş.
        inst = self.inst

        class _Response:
            headers = {"Content-Length": str(len(BODY))}
            sent = False

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self, _n):
                if not self.sent:
                    self.sent = True
                    return BODY
                inst.cancel_download()
                return b""

        opener = mock.Mock()
        opener.open.return_value = _Response()
        with mock.patch("app.core.update_installer.urllib.request.build_opener", return_value=opener):
            self.assertFalse(inst.download_update(f"{self.base}/ok.zip", len(BODY), SHA))
        self.assertEqual(self.failed, ["cancelled"])
        self.assertEqual(self.done, [])
        self.assertIsNone(inst.downloaded_file)
        self.assertFalse(os.path.exists(package_path()))

    def test_insecure_or_untrusted_urls_never_hit_network(self) -> None:
        for url in ("http://macik.com.tr/x.zip", "https://evil.example/x.zip"):
            self.failed.clear()
            self.assertFalse(self.inst.download_update(url, None, SHA), url)
            self.assertEqual(len(self.failed), 1)

    def test_messages_follow_the_language_and_english_has_no_turkish(self) -> None:
        self.assertFalse(self.inst.download_update("https://evil.example/x.zip", None, SHA))
        self.assertIn("güvenilen bir sunucuya", self.failed[-1])
        self.assertIn("evil.example", self.failed[-1])
        self.inst.lang = "en"
        self.assertFalse(self.inst.download_update("https://evil.example/x.zip", None, SHA))
        self.assertIn("trusted server", self.failed[-1])
        self.assertFalse(self.inst.download_update(f"{self.base}/ok.zip", len(BODY), "0" * 64))
        self.assertIn("could not be verified", self.failed[-1])
        self.assertFalse(self.inst.download_update(f"{self.base}/redirect.zip", None, SHA))
        self.assertIn("Redirect refused", self.failed[-1])
        self.assertIn("over HTTPS", self.failed[-1], "yönlendirme nedeni de aynı dilde (http hedefi)")
        for text in self.failed[-3:]:
            self.assertFalse(TURKISH & set(text), text)

    def test_a_new_download_sweeps_old_leftovers_but_keeps_the_saved_ready_package(self) -> None:
        tmp = tempfile.gettempdir()
        saved = os.path.join(tmp, f"{PACKAGE_PREFIX}7790.pkg")
        junk = os.path.join(tmp, f"{PACKAGE_PREFIX}7791.pkg")
        for path in (saved, junk):
            with open(path, "wb") as f:
                f.write(b"x")
            os.utime(path, (1_000_000_000, 1_000_000_000))  # çok eski
            self.addCleanup(lambda p=path: os.path.exists(p) and os.remove(p))
        self.inst.keep_package = saved
        self.assertTrue(self.inst.download_update(f"{self.base}/ok.zip", len(BODY), SHA))
        self.assertTrue(os.path.exists(saved), "kayıtlı hazır paket yeni indirme yüzünden silinmez")
        self.assertFalse(os.path.exists(junk), "sahipsiz eski artık silinir")

    def test_progress_signal_carries_sizes_beyond_32_bits(self) -> None:
        got: list = []
        self.inst.download_progress.connect(lambda d, t, s: got.append((d, t, s)))
        self.inst.download_progress.emit(5 * 1024**3, 6 * 1024**3, 1.5)
        self.assertEqual(got, [(5 * 1024**3, 6 * 1024**3, 1.5)])


class StalePackageTest(unittest.TestCase):
    def test_sweep_removes_only_old_packages_that_are_not_kept(self) -> None:
        tmp = tempfile.gettempdir()
        old = os.path.join(tmp, f"{PACKAGE_PREFIX}7771.pkg")
        fresh = os.path.join(tmp, f"{PACKAGE_PREFIX}7772.pkg")
        kept = os.path.join(tmp, f"{PACKAGE_PREFIX}7773.pkg")
        other = os.path.join(tmp, "Baska_Dosya_7774.pkg")
        for path in (old, fresh, kept, other):
            with open(path, "wb") as f:
                f.write(b"x")
            self.addCleanup(lambda p=path: os.path.exists(p) and os.remove(p))
        long_ago = time.time() - ui.STALE_PACKAGE_SECONDS - 60
        for path in (old, kept, other):
            os.utime(path, (long_ago, long_ago))
        ui.sweep_stale_packages(keep=kept)
        self.assertFalse(os.path.exists(old), "eski, sahipsiz paket silinir")
        self.assertTrue(os.path.exists(fresh), "yeni paket (başka süreç indiriyor olabilir) kalır")
        self.assertTrue(os.path.exists(kept), "kayıtlı hazır paket kalır")
        self.assertTrue(os.path.exists(other), "başka bir uygulamanın dosyasına dokunulmaz")


class InstallTest(unittest.TestCase):
    """Gerçek kurulum: başlatıcı (kullanıcının Lumina.exe'si) yeniden adlandırılıp yenisi yerine konur."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Türkçe karakterli, boşluklu ve eski batch betiğini bozan karakterler (% ! &) içeren klasör
        self.folder = os.path.join(self._tmp.name, "güncelleme ışık %20 ! & klasörü")
        os.makedirs(self.folder)
        self.exe = os.path.join(self.folder, "Lumina.exe")
        self.old_bytes = fake_pe(0x8664, b"-old")
        with open(self.exe, "wb") as f:
            f.write(self.old_bytes)
        with open(os.path.join(self.folder, "veri.txt"), "w", encoding="utf-8") as f:
            f.write("kullanıcının dosyası")
        for patcher in (
            mock.patch.object(ui, "can_self_update", return_value=True),
            mock.patch.object(ui, "app_executable", return_value=pathlib.Path(self.exe)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.inst = UpdateInstaller()
        self.inst.lang = "tr"

    def pkg(self, data: bytes, name: str = "Lumina_Update_test.pkg") -> str:
        path = os.path.join(self._tmp.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def read(self, path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    def test_zip_in_a_subfolder_replaces_only_the_exe(self) -> None:
        new = fake_pe(0x8664, b"-new")
        pkg = self.pkg(make_zip({"Lumina/Lumina.exe": new, "Lumina/yeni_veri.txt": b"yeni", "readme.txt": b"x"}))
        with mock.patch.object(su, "spawn") as spawn:
            self.assertIsNone(self.inst.install_blocking(pkg, relaunch=True, visible=True))
        self.assertEqual(self.read(self.exe), new)
        self.assertEqual(self.read(self.exe + ".old"), self.old_bytes, "eski sürüm geri alma için saklanır")
        self.assertEqual(sorted(os.listdir(self.folder)), sorted(["Lumina.exe", "Lumina.exe.old", "veri.txt"]),
                         "pakette başka dosya olsa da klasöre yalnızca exe yazılır")
        self.assertFalse(os.path.exists(pkg), "kurulan paket silinir")
        self.assertTrue(self.inst.swapped)
        exe, args = spawn.call_args[0]
        self.assertEqual(exe, self.exe)
        self.assertEqual(list(args)[:2], ["--wait-pid", str(os.getpid())])
        self.assertIn("--updated", args)
        self.assertEqual(args[-1], "--show-window")

    def test_raw_exe_package_and_extra_args(self) -> None:
        new = fake_pe(0x8664, b"-new2")
        photo = os.path.join(self._tmp.name, "Fotoğraflar", "gün batımı.jpg")
        with mock.patch.object(su, "spawn") as spawn:
            self.assertIsNone(self.inst.install_blocking(self.pkg(new), relaunch=True, visible=True, extra_args=[photo]))
        self.assertEqual(self.read(self.exe), new)
        self.assertEqual(list(spawn.call_args[0][1])[-2:], ["--show-window", photo], "açık görüntü yeni sürüme argüman olarak geçer")

    def test_exit_install_swaps_but_starts_nothing(self) -> None:
        new = fake_pe(0x8664, b"-new3")
        with mock.patch.object(su, "spawn") as spawn:
            self.assertIsNone(self.inst.install_blocking(self.pkg(make_zip({"Lumina.exe": new})), relaunch=False))
        spawn.assert_not_called()
        self.assertEqual(self.read(self.exe), new)
        self.assertTrue(os.path.exists(self.exe + ".old"))

    def test_archive_without_the_exe_leaves_the_app_untouched(self) -> None:
        pkg = self.pkg(make_zip({"baska_dosya.txt": b"x"}))
        with mock.patch.object(su, "spawn") as spawn:
            error = self.inst.install_blocking(pkg, relaunch=True)
        self.assertIn("exe", error)
        self.assertEqual(self.read(self.exe), self.old_bytes)
        self.assertEqual(sorted(os.listdir(self.folder)), ["Lumina.exe", "veri.txt"], ".new/.old artığı yok, klasöre dokunulmadı")
        spawn.assert_not_called()
        self.assertFalse(self.inst.swapped)
        self.assertFalse(os.path.exists(pkg), "kurulamayan paket silinir (tekrar dene yeniden indirir)")

    def test_bad_packages_are_refused_in_the_users_language(self) -> None:
        cases = {
            "html": (b"<html>404</html>", "exe ya da zip değil", "neither an exe nor a zip"),
            "bozuk zip": (make_zip({"Lumina.exe": fake_pe()})[:-30], "bozuk", "damaged"),
            "PE olmayan exe": (make_zip({"Lumina.exe": b"MZ-sahte"}), "geçerli bir Windows uygulaması değil", "not a valid Windows"),
            "başka mimari": (fake_pe(0x014C), "mimari", "architecture"),
        }
        for label, (data, tr_text, en_text) in cases.items():
            for lang, expected in (("tr", tr_text), ("en", en_text)):
                self.inst.lang = lang
                with self.subTest(label, lang=lang):
                    with mock.patch.object(su, "spawn"):
                        error = self.inst.install_blocking(self.pkg(data), relaunch=True)
                    self.assertIn(expected, error)
                    if lang == "en":
                        self.assertFalse(TURKISH & set(error), error)
                    self.assertEqual(self.read(self.exe), self.old_bytes)
                    self.assertEqual(sorted(os.listdir(self.folder)), ["Lumina.exe", "veri.txt"])

    def test_checksum_is_checked_again_just_before_installing(self) -> None:
        pkg = self.pkg(fake_pe(0x8664, b"-new"))
        with mock.patch.object(su, "spawn") as spawn:
            self.assertIn("checksum", self.inst.install_blocking(pkg, relaunch=True, expected_checksum="0" * 64))
        spawn.assert_not_called()
        self.assertEqual(self.read(self.exe), self.old_bytes)
        self.assertFalse(os.path.exists(pkg))
        good = self.pkg(fake_pe(0x8664, b"-new"))
        with mock.patch.object(su, "spawn"):
            self.assertIsNone(self.inst.install_blocking(good, relaunch=True, expected_checksum=hashlib.sha256(self.read(good)).hexdigest()))

    def test_unwritable_folder_is_reported_before_downloading(self) -> None:
        missing = os.path.join(self._tmp.name, "yok", "Lumina.exe")
        with mock.patch.object(ui, "app_executable", return_value=pathlib.Path(missing)):
            with self.assertRaises(su.PackageError) as ctx:
                self.inst.check_writable()
        self.assertEqual(ctx.exception.code, "not_writable")
        tr = self.inst.package_message(ctx.exception)
        self.assertIn("yazılamıyor", tr)
        self.assertIn("taşıyıp", tr, "uygulamayı yazılabilir bir klasöre taşıma önerisi")
        self.assertIn(os.path.dirname(missing), tr)
        self.inst.lang = "en"
        en = self.inst.package_message(ctx.exception)
        self.assertIn("not writable", en)
        self.assertFalse(TURKISH & set(en), en)
        self.inst.check_writable()  # gerçek klasör: sorun yok
        self.assertEqual(sorted(os.listdir(self.folder)), ["Lumina.exe", "veri.txt"], "yazma yoklaması iz bırakmaz")

    @unittest.skipUnless(sys.platform == "win32", "DLL arama dizini yalnızca Windows'ta")
    def test_the_new_launcher_does_not_inherit_the_running_apps_dll_directory(self) -> None:
        """K3: PyInstaller `SetDllDirectoryW(<eski sürüm>\\_internal)` çağırır; yeni başlatıcı onu miras alırsa eski VCRUNTIME140.dll'e bağlanıp
        eski sürüm klasörünü kilitler. Yeniden başlatan kurulum yalnızca varsayılan DLL dizini altında çalışır, sonra ayar geri yüklenir."""
        from app.core import dll_dir

        marker = os.path.join(self._tmp.name, "eski sürüm", "_internal")
        os.makedirs(marker)
        seen: list[str] = []
        real = su.install_and_relaunch

        def spy(*a, **k):
            seen.append(dll_dir.current_dll_directory())
            return real(*a, **k)

        previous = dll_dir.current_dll_directory()
        kernel32 = __import__("ctypes").WinDLL("kernel32", use_last_error=True)
        kernel32.SetDllDirectoryW.argtypes = [__import__("ctypes").c_wchar_p]
        kernel32.SetDllDirectoryW(marker)
        try:
            with mock.patch.object(su, "spawn"), mock.patch.object(su, "install_and_relaunch", spy):
                self.assertIsNone(self.inst.install_blocking(self.pkg(fake_pe(0x8664, b"-n")), relaunch=True))
            after = dll_dir.current_dll_directory()
        finally:
            kernel32.SetDllDirectoryW(previous or None)
        self.assertEqual(seen, [""], "yeni sürüm başlatılırken DLL dizini varsayılan")
        self.assertEqual(os.path.normcase(after), os.path.normcase(marker), "çalışan süreç kendi DLL dizinini geri aldı")

    def test_running_from_source_never_touches_anything(self) -> None:
        pkg = self.pkg(fake_pe(0x8664, b"-new"))
        with mock.patch.object(ui, "can_self_update", return_value=False), mock.patch.object(su, "spawn") as spawn:
            error = self.inst.install_blocking(pkg, relaunch=True)
        self.assertIn("derlenmiş", error)
        spawn.assert_not_called()
        self.assertEqual(self.read(self.exe), self.old_bytes)
        self.assertTrue(os.path.exists(pkg))

    def test_recovery_point_is_visible_next_to_the_launcher(self) -> None:
        self.assertFalse(self.inst.recovery_point_exists())
        with mock.patch.object(su, "spawn"):
            self.assertIsNone(self.inst.install_blocking(self.pkg(fake_pe(0x8664, b"-n")), relaunch=False))
        self.assertTrue(self.inst.recovery_point_exists(), "kurulumdan sonra .old var: ikinci kurulum onu ezmemeli")

    def test_missing_package_file(self) -> None:
        self.assertIn("bulunamadı", self.inst.install_blocking(os.path.join(self._tmp.name, "yok.pkg"), relaunch=True))

    def test_install_runs_off_the_gui_thread_and_reports_by_signal(self) -> None:
        done, failed, started = [], [], []
        self.inst.install_complete.connect(lambda: done.append(threading.current_thread().name))
        self.inst.install_failed.connect(failed.append)
        self.inst.install_started.connect(lambda: started.append(1))
        gui_thread = threading.current_thread()
        seen_threads: list = []
        real = su.install_and_relaunch

        def spy(*a, **k):
            seen_threads.append(threading.current_thread())
            return real(*a, **k)

        with mock.patch.object(su, "spawn"), mock.patch.object(su, "install_and_relaunch", spy):
            self.assertTrue(self.inst.install_update(self.pkg(make_zip({"Lumina.exe": fake_pe(0x8664, b"-n")}))))
            self.assertTrue(pump(lambda: done or failed))
        self.assertEqual((len(started), failed), (1, []))
        self.assertEqual(len(done), 1)
        self.assertIsNot(seen_threads[0], gui_thread, "kurulum arayüz iş parçacığında çalışmaz (donma yok)")

    def test_failed_install_is_reported_by_signal_and_a_second_one_is_refused_meanwhile(self) -> None:
        failed: list[str] = []
        self.inst.install_failed.connect(failed.append)
        gate = threading.Event()
        real_stage = su.stage_new_exe

        def slow_stage(*a, **k):
            gate.wait(5)
            return real_stage(*a, **k)

        with mock.patch.object(su, "stage_new_exe", slow_stage):
            self.assertTrue(self.inst.install_update(self.pkg(b"<html>")))
            self.assertTrue(self.inst.install_running())
            self.assertFalse(self.inst.install_update(self.pkg(b"<html>")), "aynı anda ikinci kurulum başlamaz")
            gate.set()
            self.assertTrue(self.inst.wait_install(10))
        self.assertTrue(pump(lambda: failed))
        self.assertIn("exe ya da zip değil", failed[0])

    def test_a_prefixed_or_uppercase_checksum_is_compared_correctly_when_installing(self) -> None:
        # Sunucu `sha256:` önekli/büyük harfli verebilir; kayıtlı hazır paket de bu ham metinle saklanır (çıkışta kurulum, 'Tekrar dene')
        data = fake_pe(0x8664, b"-n")
        digest = hashlib.sha256(make_zip({"Lumina.exe": data})).hexdigest()
        for value in (digest.upper(), "sha256:" + digest, "SHA256:" + digest.upper(), "  " + digest + " "):
            pkg = self.pkg(make_zip({"Lumina.exe": data}))
            with mock.patch.object(su, "spawn"):
                self.assertIsNone(self.inst.install_blocking(pkg, relaunch=False, expected_checksum=value), value)
            os.replace(self.exe + ".old", self.exe)  # sonraki tur için eski haline dön

    def test_a_malformed_checksum_is_refused_and_the_package_dropped(self) -> None:
        pkg = self.pkg(fake_pe(0x8664, b"-n"))
        with mock.patch.object(su, "spawn") as spawn:
            error = self.inst.install_blocking(pkg, relaunch=True, expected_checksum="xyz")
        self.assertIn("geçersiz biçim", error)
        spawn.assert_not_called()
        self.assertFalse(os.path.exists(pkg))
        self.assertEqual(self.read(self.exe), self.old_bytes)

    @staticmethod
    def _busy(src_name: str):
        real = os.replace

        def flaky(src, dst):
            if src == src_name:
                raise OSError(13, "Başka bir işlem dosyayı kullanıyor", None, 32)
            return real(src, dst)

        return flaky

    def test_a_transient_swap_failure_keeps_the_package_so_that_retry_does_not_download_again(self) -> None:
        pkg = self.pkg(make_zip({"Lumina.exe": fake_pe(0x8664, b"-n")}))
        with mock.patch.object(su.os, "replace", self._busy(self.exe)), mock.patch.object(su.time, "sleep"):
            error = self.inst.install_blocking(pkg, relaunch=False)
        self.assertIn("değiştirilemedi", error)
        self.assertTrue(os.path.exists(pkg), "geçici paylaşım ihlalinde paket HEMEN silinmez")
        self.assertEqual(self.inst.kept_package, pkg)
        self.assertEqual(self.read(self.exe), self.old_bytes)
        self.assertEqual(sorted(os.listdir(self.folder)), ["Lumina.exe", "veri.txt"], ".new/.old artığı yok")
        self.assertFalse(self.inst.swapped)
        # 'Tekrar dene': aynı paket (indirme yok) bu kez kurulur
        with mock.patch.object(su, "spawn"):
            self.assertIsNone(self.inst.install_blocking(pkg, relaunch=False))
        self.assertIsNone(self.inst.kept_package)
        self.assertTrue(self.inst.swapped)

    def test_a_failure_while_putting_the_new_exe_in_place_keeps_the_package_too(self) -> None:
        pkg = self.pkg(make_zip({"Lumina.exe": fake_pe(0x8664, b"-n")}))
        with mock.patch.object(su.os, "replace", self._busy(self.exe + ".new")), mock.patch.object(su.time, "sleep"):
            error = self.inst.install_blocking(pkg, relaunch=False)
        self.assertIn("yerine konamadı", error)
        self.assertTrue(os.path.exists(pkg))
        self.assertEqual(self.inst.kept_package, pkg)
        self.assertEqual(self.read(self.exe), self.old_bytes, "eski sürüm geri geldi")

    def test_a_write_failure_keeps_the_package_but_a_broken_package_does_not(self) -> None:
        pkg = self.pkg(make_zip({"Lumina.exe": fake_pe(0x8664, b"-n")}))
        with mock.patch.object(su, "stage_new_exe", side_effect=su.PackageError("disk dolu", "write", "disk dolu")):
            self.assertIn("yazılamadı", self.inst.install_blocking(pkg, relaunch=False))
        self.assertEqual((os.path.exists(pkg), self.inst.kept_package), (True, pkg))
        broken = self.pkg(make_zip({"baska.txt": b"x"}), "Lumina_Update_broken.pkg")
        self.assertIn("exe", self.inst.install_blocking(broken, relaunch=False))
        self.assertFalse(os.path.exists(broken), "paketin kendisi kullanılamıyorsa silinir")
        self.assertIsNone(self.inst.kept_package, "kept_package her kurulum denemesinde sıfırlanır")

    def test_can_self_update_needs_a_frozen_windows_build_with_the_launcher(self) -> None:
        real = _REAL_CAN_SELF_UPDATE  # setUp'ın sahte yaması değil, gerçek işlev
        launcher = pathlib.Path(self.exe)
        with mock.patch.object(ui, "is_frozen", return_value=True),                 mock.patch.object(ui, "supports_auto_install", return_value=True),                 mock.patch.object(ui, "launcher_path", return_value=launcher):
            self.assertTrue(real())
            with mock.patch.object(ui, "launcher_path", return_value=None):
                self.assertFalse(real(), "başlatıcısız (runtime klasöründen doğrudan) çalışan exe değiştirilmez")
            with mock.patch.object(ui, "supports_auto_install", return_value=False):
                self.assertFalse(real())
            with mock.patch.object(ui, "is_frozen", return_value=False):
                self.assertFalse(real(), "kaynaktan çalışırken python.exe değiştirilmez")


class PackageOwnerTest(unittest.TestCase):
    """K2b: paket adındaki süreç numarası; sahibi ölmüş paketler süpürülür, yaşayanlarınki ve kayıtlısı kalır."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        patcher = mock.patch.object(ui.tempfile, "gettempdir", return_value=self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.children: list = []
        self.addCleanup(self._reap)

    def _reap(self) -> None:
        for child in self.children:
            try:
                child.kill()
                child.wait(10)
            except OSError:
                pass

    def dead_pid(self) -> int:
        child = subprocess.Popen([sys.executable, "-B", "-c", "pass"])
        child.wait(30)
        return child.pid

    def live_pid(self) -> int:
        child = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(120)"])
        self.children.append(child)
        return child.pid

    def make(self, name: str) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(b"x")
        return path

    def test_package_pid_reads_only_our_own_names(self) -> None:
        self.assertEqual(ui.package_pid(os.path.join(self.tmp, "Lumina_Update_12572.pkg")), 12572)
        for other in ("Lumina_Update_test.pkg", "Lumina_Update_12.pkg.part", "Baska_12.pkg", "lumina_update_5.pkg", "Lumina_Update_.pkg"):
            self.assertIsNone(ui.package_pid(other), other)

    def test_pid_alive(self) -> None:
        self.assertTrue(ui.pid_alive(os.getpid()))
        self.assertFalse(ui.pid_alive(0))
        self.assertFalse(ui.pid_alive(-5))
        self.assertTrue(ui.pid_alive(self.live_pid()))
        self.assertFalse(ui.pid_alive(self.dead_pid()), "sonlanmış süreç")
        self.assertFalse(ui.pid_alive(0x7FFFFFF0), "böyle bir süreç yok")

    def test_sweep_removes_only_packages_whose_owner_is_gone(self) -> None:
        dead, live = self.dead_pid(), self.live_pid()
        dead_pkg = self.make(f"{PACKAGE_PREFIX}{dead}.pkg")
        live_pkg = self.make(f"{PACKAGE_PREFIX}{live}.pkg")
        own_pkg = self.make(f"{PACKAGE_PREFIX}{os.getpid()}.pkg")
        kept_pkg = self.make(f"{PACKAGE_PREFIX}{self.dead_pid()}.pkg")
        odd_name = self.make(f"{PACKAGE_PREFIX}test.pkg")
        foreign = self.make(f"Baska_Uygulama_{dead}.pkg")
        other_ext = self.make(f"{PACKAGE_PREFIX}{dead}.pkg.bak")
        removed = ui.sweep_orphan_packages(keep=kept_pkg)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(dead_pkg), "sahibi ölmüş paket silinir (yaşına bakılmaz)")
        for path, why in (
            (live_pkg, "yaşayan başka bir pencerenin paketi"),
            (own_pkg, "kendi sürecimizin paketi"),
            (kept_pkg, "kayıtlı hazır paket"),
            (odd_name, "kendi pid örüntümüze uymayan ad"),
            (foreign, "başka uygulamanın dosyası"),
            (other_ext, "farklı uzantı"),
        ):
            self.assertTrue(os.path.exists(path), why)

    def test_an_old_package_of_a_live_process_is_still_left_alone(self) -> None:
        # Günlerce açık kalan pencerenin çıkışta kuracağı paket yaşına bakılarak silinmez
        live_pkg = self.make(f"{PACKAGE_PREFIX}{self.live_pid()}.pkg")
        os.utime(live_pkg, (1_000_000_000, 1_000_000_000))
        self.assertEqual(ui.sweep_orphan_packages(), 0)
        self.assertTrue(os.path.exists(live_pkg))

    def test_package_owner_gone(self) -> None:
        self.assertTrue(ui.package_owner_gone(os.path.join(self.tmp, f"{PACKAGE_PREFIX}{self.dead_pid()}.pkg")))
        self.assertTrue(ui.package_owner_gone(os.path.join(self.tmp, f"{PACKAGE_PREFIX}{os.getpid()}.pkg")))
        self.assertTrue(ui.package_owner_gone(os.path.join(self.tmp, f"{PACKAGE_PREFIX}test.pkg")))
        self.assertFalse(ui.package_owner_gone(os.path.join(self.tmp, f"{PACKAGE_PREFIX}{self.live_pid()}.pkg")))

    def test_pid_alive_never_uses_os_kill_on_windows(self) -> None:
        if os.name != "nt":
            self.skipTest("yalnızca Windows")
        with mock.patch.object(ui.os, "kill", side_effect=AssertionError("Windows'ta os.kill süreci ÖLDÜRÜR")):
            ui.pid_alive(self.dead_pid())
            ui.pid_alive(os.getpid() + 4)


class DownloadLockTest(unittest.TestCase):
    """K2c: aynı anda tek Lumina süreci indirir; bayat (sahibi ölmüş) kilit kendini düşürür; kilit kullanılamıyorsa indirme engellenmez."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "Lumina.update.lock")

    def lock(self) -> ui.DownloadLock:
        lock = ui.DownloadLock(self.path)
        self.addCleanup(lock.release)
        return lock

    def test_only_one_holder_at_a_time_and_release_hands_it_over(self) -> None:
        first, second = self.lock(), self.lock()
        self.assertTrue(first.try_acquire())
        self.assertTrue(first.held)
        self.assertTrue(first.try_acquire(), "zaten tutuyorsa yeniden denemek güvenli")
        self.assertFalse(second.try_acquire(), "başka canlı indirme sürerken alınamaz")
        self.assertFalse(second.held)
        first.release()
        self.assertFalse(first.held)
        self.assertTrue(second.try_acquire())
        first.release()  # iki kez bırakmak zararsız

    def test_an_unusable_lock_file_never_blocks_the_download(self) -> None:
        blocker = os.path.join(self._tmp.name, "dosya")
        with open(blocker, "w") as f:
            f.write("x")
        lock = ui.DownloadLock(os.path.join(blocker, "alt", "Lumina.update.lock"))  # üst yol bir dosya: oluşturulamaz
        self.assertTrue(lock.try_acquire(), "kilit yoksa mevcut davranışa düşülür: indir")
        self.assertFalse(lock.held)

    def test_the_lock_of_a_killed_process_drops_by_itself(self) -> None:
        code = (
            "import sys, time\n"
            "sys.path.insert(0, sys.argv[2])\n"
            "from app.core.update_installer import DownloadLock\n"
            "lock = DownloadLock(sys.argv[1])\n"
            "print('got' if lock.try_acquire() and lock.held else 'no', flush=True)\n"
            "time.sleep(120)\n"
        )
        root = str(pathlib.Path(__file__).resolve().parents[1])
        child = subprocess.Popen([sys.executable, "-B", "-c", code, self.path, root],
                                 stdout=subprocess.PIPE, text=True, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.addCleanup(lambda: (child.kill(), child.wait(10), child.stdout.close()))
        self.assertEqual(child.stdout.readline().strip(), "got")
        mine = self.lock()
        self.assertFalse(mine.try_acquire(), "sahibi canlıyken (başka süreç) alınamaz")
        child.kill()  # çökme / görev yöneticisi: kilit dosyası kalır ama sahibi yok
        child.wait(10)
        deadline = time.monotonic() + 5
        got = False
        while time.monotonic() < deadline and not got:
            got = mine.try_acquire()
            time.sleep(0.05)
        self.assertTrue(got and mine.held, "bayat kilit kendini düşürür")

    def test_an_old_lock_of_a_live_process_is_not_stolen_by_age(self) -> None:
        first, second = self.lock(), self.lock()
        self.assertTrue(first.try_acquire())
        old = time.time() - 6 * 3600
        os.utime(self.path, (old, old))
        self.assertFalse(second.try_acquire(), "uzun süren indirmenin kilidi 'eski' diye çalınmaz")

    def test_default_lock_path_lives_in_the_temp_folder_and_is_named_for_lumina_updates(self) -> None:
        path = ui.default_download_lock_path()
        self.assertEqual(path.parent, pathlib.Path(tempfile.gettempdir()))
        self.assertTrue(path.name.startswith("Lumina.update") and path.name.endswith(".lock"), path.name)
        self.assertNotEqual(path.name, "Lumina.tray.lock", "tepsi sahiplik kilidiyle çakışmaz")


_REAL_CAN_SELF_UPDATE = ui.can_self_update


if __name__ == "__main__":
    unittest.main()
