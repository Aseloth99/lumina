"""self_update birim testleri (stdlib unittest): paket sezgisi, hazırlama, değiştirme, geri alma, açılış koruması."""
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import self_update as su  # noqa: E402


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


class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="su_test_ü ")
        self.dir = self._tmp.name
        self.exe = os.path.join(self.dir, "App.exe")
        with open(self.exe, "wb") as f:
            f.write(fake_pe(0x8664, b"-old"))

    def tearDown(self):
        self._tmp.cleanup()

    def pkg(self, data: bytes, name="pkg.bin") -> str:
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def read(self, path):
        with open(path, "rb") as f:
            return f.read()

    # ---- paket biçimi
    def test_sniff_by_content_not_by_name(self):
        self.assertEqual(su.sniff_package(self.pkg(fake_pe(), "x.zip")), "exe")
        self.assertEqual(su.sniff_package(self.pkg(make_zip({"a": b"1"}), "x.exe")), "zip")
        with self.assertRaises(su.PackageError):
            su.sniff_package(self.pkg(b"<html>404</html>"))
        with self.assertRaises(su.PackageError):
            su.sniff_package(os.path.join(self.dir, "yok"))

    def test_pe_machine(self):
        self.assertEqual(su.pe_machine(fake_pe(0x8664)), 0x8664)
        self.assertEqual(su.pe_machine(fake_pe(0x014C)), 0x014C)
        self.assertIsNone(su.pe_machine(b"MZ"))
        self.assertIsNone(su.pe_machine(b"\0" * 200))
        bad = bytearray(fake_pe()); bad[0x80] = ord("X")
        self.assertIsNone(su.pe_machine(bytes(bad)))
        far = bytearray(fake_pe()); far[0x3C:0x40] = (0xFFFFFFFF).to_bytes(4, "little")
        self.assertIsNone(su.pe_machine(bytes(far)))
        with open(sys.executable, "rb") as f:
            self.assertIsNotNone(su.pe_machine(f.read(4096)))

    # ---- hazırlama
    def test_stage_raw_exe(self):
        new = su.stage_new_exe(self.pkg(fake_pe(0x8664, b"-new")), self.exe, ["App.exe"])
        self.assertEqual(new, self.exe + ".new")
        self.assertTrue(self.read(new).endswith(b"-new"))

    def test_stage_from_zip_any_folder_case_insensitive_only_that_file(self):
        data = fake_pe(0x8664, b"-new")
        z = self.pkg(make_zip({"readme.txt": b"x", "Deep/er/APP.EXE": b"not me", "Folder/app.exe": data, "dll/other.dll": b"y"}))
        new = su.stage_new_exe(z, self.exe, ["App.exe"])
        self.assertEqual(self.read(new), data, "en az derindeki App.exe seçilir")
        self.assertEqual(sorted(os.listdir(self.dir)), sorted(["App.exe", "App.exe.new", "pkg.bin"]), "başka dosya yazılmaz")

    def test_stage_rejects_bad_packages_and_leaves_no_new_file(self):
        cases = {
            "exe'siz zip": make_zip({"readme.txt": b"x"}),
            "PE olmayan exe": make_zip({"App.exe": b"MZ-fake"}),
            "başka mimari": fake_pe(0x014C),
            "bozuk zip": make_zip({"App.exe": fake_pe()})[:-30],
            "html": b"<html>",
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(su.PackageError):
                    su.stage_new_exe(self.pkg(data), self.exe, ["App.exe"])
                self.assertFalse(os.path.exists(self.exe + ".new"))
        self.assertEqual(self.read(self.exe), fake_pe(0x8664, b"-old"), "çalışan exe'ye dokunulmaz")

    def test_package_errors_carry_a_machine_readable_code(self):
        cases = {
            "not_package": make_zip({"a": b"1"})[:0] + b"<html>",
            "no_exe": make_zip({"readme.txt": b"x"}),
            "not_pe": make_zip({"App.exe": b"MZ-fake"}),
            "arch": fake_pe(0x014C),
            "bad_zip": make_zip({"App.exe": fake_pe()})[:-30],
        }
        for code, data in cases.items():
            with self.subTest(code):
                with self.assertRaises(su.PackageError) as ctx:
                    su.stage_new_exe(self.pkg(data), self.exe, ["App.exe"])
                self.assertEqual(ctx.exception.code, code)
        with self.assertRaises(su.PackageError) as ctx:
            su.stage_new_exe(self.pkg(fake_pe(0x8664, b"x" * 5000)), self.exe, ["App.exe"], max_bytes=1000)
        self.assertEqual(ctx.exception.code, "too_big")
        with self.assertRaises(su.PackageError) as ctx:
            su.check_writable(os.path.join(self.dir, "yok", "klasor", "App.exe"))
        self.assertEqual(ctx.exception.code, "not_writable")
        self.assertEqual(ctx.exception.detail, os.path.join(self.dir, "yok", "klasor"))
        with self.assertRaises(su.PackageError) as ctx:
            su.sniff_package(os.path.join(self.dir, "yok"))
        self.assertEqual(ctx.exception.code, "read")
        self.assertIn("Paket", str(ctx.exception), "ileti Türkçe/günlük için duruyor")

    def test_swap_and_spawn_errors_have_codes(self):
        with self.assertRaises(su.PackageError) as ctx:
            su.swap_exe(os.path.join(self.dir, "yok.exe"), os.path.join(self.dir, "yok.exe.new"))
        self.assertEqual(ctx.exception.code, "swap")
        with mock.patch.object(su.subprocess, "Popen", side_effect=OSError("engellendi")):
            with self.assertRaises(su.PackageError) as ctx:
                su.spawn(self.exe, [])
        self.assertEqual(ctx.exception.code, "spawn")
        new = self.exe + ".new"
        with open(new, "wb") as f:
            f.write(fake_pe())
        real = os.replace
        with mock.patch.object(su.os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("x")) if a == new else real(a, b)):
            with self.assertRaises(su.PackageError) as ctx:
                su.swap_exe(self.exe, new)
        self.assertEqual(ctx.exception.code, "swap2")

    def test_stage_size_limit(self):
        with self.assertRaises(su.PackageError):
            su.stage_new_exe(self.pkg(fake_pe(0x8664, b"x" * 5000)), self.exe, ["App.exe"], max_bytes=1000)
        with self.assertRaises(su.PackageError):
            su.stage_new_exe(self.pkg(make_zip({"App.exe": fake_pe(0x8664, b"x" * 5000)})), self.exe, ["App.exe"], max_bytes=1000)
        self.assertFalse(os.path.exists(self.exe + ".new"))

    def test_check_writable(self):
        su.check_writable(self.exe)
        with self.assertRaises(su.PackageError):
            su.check_writable(os.path.join(self.dir, "yok", "klasor", "App.exe"))

    # ---- değiştirme
    def test_swap_keeps_old(self):
        new = self.exe + ".new"
        with open(new, "wb") as f:
            f.write(fake_pe(0x8664, b"-new"))
        su.swap_exe(self.exe, new)
        self.assertTrue(self.read(self.exe).endswith(b"-new"))
        self.assertTrue(self.read(self.exe + ".old").endswith(b"-old"))
        self.assertFalse(os.path.exists(new))

    def test_swap_rolls_back_when_the_second_rename_fails(self):
        new = self.exe + ".new"
        with open(new, "wb") as f:
            f.write(fake_pe(0x8664, b"-new"))
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append((src, dst))
            if src == new:
                raise PermissionError("kilitli")
            return real(src, dst)

        with mock.patch.object(su.os, "replace", flaky):
            with self.assertRaises(su.PackageError):
                su.swap_exe(self.exe, new)
        self.assertTrue(self.read(self.exe).endswith(b"-old"), "eski sürüm geri geldi")
        self.assertFalse(os.path.exists(new))

    # ---- geçici paylaşım ihlali (antivirüs taraması): 3 x 200 ms yeniden deneme
    @staticmethod
    def sharing_violation(code: int = 32) -> OSError:
        return OSError(13, "Başka bir işlem dosyayı kullanıyor", None, code)

    def staged(self):
        new = self.exe + ".new"
        with open(new, "wb") as f:
            f.write(fake_pe(0x8664, b"-new"))
        return new

    def test_a_transient_sharing_violation_is_retried_and_the_swap_succeeds(self):
        new = self.staged()
        real = os.replace
        failures = {"left": 2}
        waits: list = []

        def flaky(src, dst):
            if src == self.exe and failures["left"] > 0:  # ilk adım: exe -> .old, antivirüs taraması sürerken
                failures["left"] -= 1
                raise self.sharing_violation(32)
            return real(src, dst)

        with mock.patch.object(su.os, "replace", flaky), mock.patch.object(su.time, "sleep", waits.append):
            su.swap_exe(self.exe, new)
        self.assertEqual(waits, [su.SWAP_RETRY_DELAY] * 2, "iki başarısız denemeden sonra üçüncüsü geçti")
        self.assertEqual(su.SWAP_RETRY_DELAY, 0.2)
        self.assertEqual(su.SWAP_RETRIES, 3)
        self.assertTrue(self.read(self.exe).endswith(b"-new"))
        self.assertTrue(self.read(self.exe + ".old").endswith(b"-old"))
        self.assertFalse(os.path.exists(new))

    def test_the_second_step_is_retried_too(self):
        new = self.staged()
        real = os.replace
        failures = {"left": 1}

        def flaky(src, dst):
            if src == new and failures["left"] > 0:
                failures["left"] -= 1
                raise self.sharing_violation(33)  # ERROR_LOCK_VIOLATION
            return real(src, dst)

        with mock.patch.object(su.os, "replace", flaky), mock.patch.object(su.time, "sleep"):
            su.swap_exe(self.exe, new)
        self.assertTrue(self.read(self.exe).endswith(b"-new"))

    def test_a_persistent_sharing_violation_gives_up_after_three_retries_and_restores_the_old_exe(self):
        new = self.staged()
        real = os.replace
        calls = {"n": 0}
        waits: list = []

        def always_busy(src, dst):
            if src == new:
                calls["n"] += 1
                raise self.sharing_violation(32)
            return real(src, dst)

        with mock.patch.object(su.os, "replace", always_busy), mock.patch.object(su.time, "sleep", waits.append):
            with self.assertRaises(su.PackageError) as ctx:
                su.swap_exe(self.exe, new)
        self.assertEqual(ctx.exception.code, "swap2")
        self.assertEqual(calls["n"], 1 + su.SWAP_RETRIES, "ilk deneme + 3 yeniden deneme")
        self.assertEqual(len(waits), su.SWAP_RETRIES)
        self.assertTrue(self.read(self.exe).endswith(b"-old"), "eski sürüm geri geldi")

    def test_a_persistent_violation_on_the_first_step_reports_swap_and_leaves_the_exe_alone(self):
        new = self.staged()

        def always_busy(src, dst):
            raise self.sharing_violation(32)

        with mock.patch.object(su.os, "replace", always_busy), mock.patch.object(su.time, "sleep"):
            with self.assertRaises(su.PackageError) as ctx:
                su.swap_exe(self.exe, new)
        self.assertEqual(ctx.exception.code, "swap")
        self.assertTrue(self.read(self.exe).endswith(b"-old"))
        self.assertFalse(os.path.exists(new), ".new artığı bırakılmaz")

    def test_other_errors_are_not_retried(self):
        new = self.staged()
        waits: list = []

        def denied(src, dst):
            raise PermissionError(13, "Erişim engellendi", None, 5)  # WinError 5: kalıcı izin sorunu

        with mock.patch.object(su.os, "replace", denied), mock.patch.object(su.time, "sleep", waits.append):
            with self.assertRaises(su.PackageError):
                su.swap_exe(self.exe, new)
        self.assertEqual(waits, [], "paylaşım ihlali olmayan hata beklemeden yükseltilir")

    def test_install_and_relaunch_orders_the_steps_and_starts_the_new_exe(self):
        data = fake_pe(0x8664, b"-new")
        pkg = self.pkg(make_zip({"App.exe": data}))
        with mock.patch.object(su, "spawn") as spawn:
            su.install_and_relaunch(pkg, self.exe, ["App.exe"], visible=False, old_version="1.0.0")
        self.assertEqual(self.read(self.exe), data)
        self.assertTrue(os.path.exists(self.exe + ".old"))
        self.assertFalse(os.path.exists(pkg), "indirilen paket silinir")
        exe, args = spawn.call_args[0]
        self.assertEqual(exe, self.exe)
        self.assertEqual(list(args), ["--wait-pid", str(os.getpid()), "--updated", "1.0.0", "--minimized"])

    def test_install_failure_leaves_the_running_exe_alone(self):
        before = self.read(self.exe)
        with mock.patch.object(su, "spawn") as spawn:
            with self.assertRaises(su.PackageError):
                su.install_and_relaunch(self.pkg(make_zip({"x.txt": b"1"})), self.exe, ["App.exe"], visible=True)
        self.assertEqual(self.read(self.exe), before)
        spawn.assert_not_called()
        self.assertFalse(os.path.exists(self.exe + ".old"))

    def test_relaunch_args(self):
        self.assertEqual(su.relaunch_args(True, "1.0.0", 42), ["--wait-pid", "42", "--updated", "1.0.0", "--show-window"])
        self.assertEqual(su.relaunch_args(False, "", 7), ["--wait-pid", "7", "--minimized"])

    def test_clean_env_drops_pyinstaller_variables(self):
        with mock.patch.dict(os.environ, {"_MEIPASS2": "x", "_PYI_APPLICATION_HOME_DIR": "y", "PYTHONHOME": "z", "KEEP_ME": "1"}):
            env = su.clean_env()
        self.assertNotIn("_MEIPASS2", env)
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", env)
        self.assertNotIn("PYTHONHOME", env)
        self.assertEqual(env["KEEP_ME"], "1")

    # ---- açılış tarafı
    def test_flags(self):
        argv = ["App.exe", "--wait-pid", "123", "--updated", "1.0.0", "--show-window"]
        self.assertEqual(su.flag_value(argv, "--wait-pid"), "123")
        self.assertEqual(su.updated_from(argv), "1.0.0")
        self.assertTrue(su.wants_window(argv))
        self.assertEqual(su.flag_value(["--wait-pid=9"], "--wait-pid"), "9")
        self.assertIsNone(su.updated_from(["App.exe", "--minimized"]))
        self.assertFalse(su.wants_window(["App.exe", "--minimized"]))

    @unittest.skipUnless(os.name == "nt", "Windows")
    def test_wait_for_previous_instance(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.2)"])
        try:
            t0 = time.monotonic()
            self.assertTrue(su.wait_for_previous_instance(["x", "--wait-pid", str(child.pid)], timeout=10))
            self.assertGreaterEqual(time.monotonic() - t0, 0.6, "çıkana kadar bekledi")
            self.assertLess(time.monotonic() - t0, 8)
        finally:
            child.kill(); child.wait()
        t0 = time.monotonic()
        self.assertTrue(su.wait_for_previous_instance(["x", "--wait-pid", "999999"], timeout=10), "olmayan süreç beklenmez")
        self.assertLess(time.monotonic() - t0, 1)
        self.assertTrue(su.wait_for_previous_instance(["x"]), "bayrak yoksa beklemez")
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertFalse(su.wait_for_previous_instance(["x", "--wait-pid", str(sleeper.pid)], timeout=0.5), "zaman aşımı")
        finally:
            sleeper.kill(); sleeper.wait()

    def test_guard_rolls_back_a_version_that_keeps_dying(self):
        self.assertEqual(su.startup_guard(self.exe, "2.0.0"), su.Guard.NORMAL, "güncelleme olmadı")
        with open(self.exe + ".old", "wb") as f:
            f.write(b"MZ-previous")
        self.assertEqual(su.startup_guard(self.exe, "2.0.0"), su.Guard.PROBATION)
        self.assertEqual(su.startup_guard(self.exe, "2.0.0"), su.Guard.PROBATION)
        self.assertEqual(su.startup_guard(self.exe, "2.0.0"), su.Guard.ROLLED_BACK)
        self.assertEqual(self.read(self.exe), b"MZ-previous")
        self.assertTrue(self.read(self.exe + ".bad").endswith(b"-old"))
        self.assertEqual(su.skipped_version(self.exe), "2.0.0")
        self.assertFalse(os.path.exists(self.exe + ".old") or os.path.exists(self.exe + ".boot"))
        self.assertEqual(su.startup_guard(self.exe, "1.0.0"), su.Guard.NORMAL, "geri gelen sürüm normal çalışır")

    def test_healthy_version_cleans_up(self):
        for s in (".old", ".boot", ".bad", ".new"):
            with open(self.exe + s, "wb") as f:
                f.write(b"x")
        with open(self.exe + ".skip", "w") as f:
            f.write("9.9.9")
        su.confirm_healthy(self.exe)
        for s in (".old", ".boot", ".bad", ".new"):
            self.assertFalse(os.path.exists(self.exe + s), s)
        self.assertEqual(su.skipped_version(self.exe), "9.9.9", "atlanan sürüm unutulmaz")
        self.assertEqual(su.startup_guard(self.exe, "1.0.1"), su.Guard.NORMAL)



class LuminaAdditionsTests(unittest.TestCase):
    """Lumina'ya özgü iki ekleme: çıkışta sessiz kurulum (`install_package`, yeniden başlatma yok) ve yeni sürüme ek argüman."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="su_lu_ü ")
        self.dir = self._tmp.name
        self.exe = os.path.join(self.dir, "App.exe")
        with open(self.exe, "wb") as f:
            f.write(fake_pe(0x8664, b"-old"))

    def tearDown(self):
        self._tmp.cleanup()

    def _pkg(self, data: bytes) -> str:
        path = os.path.join(self.dir, "pkg.bin")
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_install_package_swaps_without_starting_anything(self):
        data = fake_pe(0x8664, b"-new")
        pkg = self._pkg(make_zip({"App.exe": data}))
        with mock.patch.object(su, "spawn") as spawn:
            su.install_package(pkg, self.exe, ["App.exe"])
        spawn.assert_not_called()
        with open(self.exe, "rb") as f:
            self.assertEqual(f.read(), data)
        self.assertTrue(os.path.exists(self.exe + ".old"))
        self.assertFalse(os.path.exists(pkg))

    def test_install_package_failure_leaves_the_running_exe_alone(self):
        def read_exe():
            with open(self.exe, "rb") as f:
                return f.read()

        before = read_exe()
        with mock.patch.object(su, "spawn") as spawn:
            with self.assertRaises(su.PackageError):
                su.install_package(self._pkg(b"<html>"), self.exe, ["App.exe"])
        spawn.assert_not_called()
        self.assertEqual(read_exe(), before)
        self.assertFalse(os.path.exists(self.exe + ".old"))

    def test_relaunch_appends_extra_args_after_the_flags(self):
        pkg = self._pkg(fake_pe(0x8664, b"-new"))
        with mock.patch.object(su, "spawn") as spawn:
            su.install_and_relaunch(pkg, self.exe, ["App.exe"], visible=True, old_version="1.0.0",
                                    extra_args=["C:/Fotoğraflar/bir resim.jpg"])
        args = list(spawn.call_args[0][1])
        self.assertEqual(args, ["--wait-pid", str(os.getpid()), "--updated", "1.0.0", "--show-window", "C:/Fotoğraflar/bir resim.jpg"])

    def test_relaunch_without_extra_args_is_unchanged(self):
        pkg = self._pkg(fake_pe(0x8664, b"-new"))
        with mock.patch.object(su, "spawn") as spawn:
            su.install_and_relaunch(pkg, self.exe, ["App.exe"], visible=False)
        self.assertEqual(list(spawn.call_args[0][1]), ["--wait-pid", str(os.getpid()), "--minimized"])


class ExitWatchdogTest(unittest.TestCase):
    def test_a_daemon_timer_hard_exits_the_process_if_the_quit_hangs(self):
        with mock.patch("threading.Timer") as timer:
            su.arm_exit_watchdog(7)
        args = timer.call_args[0]
        self.assertEqual(args[0], 7)
        self.assertTrue(timer.return_value.daemon, "süreci ayakta tutmamalı")
        timer.return_value.start.assert_called_once()
        with mock.patch.object(su.os, "_exit") as hard_exit:
            args[1]()
        hard_exit.assert_called_once_with(0)

    def test_the_default_delay_is_shorter_than_what_the_new_version_waits(self):
        self.assertLess(su.EXIT_WATCHDOG_SECONDS, su.WAIT_PID_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
