"""Açılışta güncelleme tarafı: bayraklar (dosya yolu sanılmaz), çöken yeni sürümün geri alınması, sağlıklı sayma
(temiz çıkış = çökme değil; kendini güncelleyen süreç `.old`'u silmez), "güncellendi" bildirimi."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core import self_update as su  # noqa: E402
from app.core import update_boot as boot  # noqa: E402


class FlagsTest(unittest.TestCase):
    def test_flags_are_never_taken_for_a_file_path(self):
        argv = ["Lumina.exe", "--updated", "1.0.0", "--show-window", "C:/Fotoğraflar/bir resim.jpg"]
        self.assertEqual(boot.positional_args(argv), ["C:/Fotoğraflar/bir resim.jpg"])
        self.assertEqual(boot.positional_args(["Lumina.exe", "--wait-pid", "123", "--minimized"]), [])
        self.assertEqual(boot.positional_args(["Lumina.exe", "--wait-pid=5", "--updated=2.0", "a.jpg", "b.jpg"]), ["a.jpg", "b.jpg"])
        self.assertEqual(boot.positional_args(["Lumina.exe", "a.jpg"]), ["a.jpg"])
        self.assertEqual(boot.positional_args(["Lumina.exe"]), [])
        self.assertEqual(boot.positional_args(["Lumina.exe", "--updated"]), [], "değersiz bayrak sarkmaz")

    def test_launch_path_is_the_first_existing_positional(self):
        with tempfile.TemporaryDirectory() as tmp:
            photo = os.path.join(tmp, "gün batımı.jpg")
            open(photo, "wb").close()
            self.assertEqual(boot.launch_path(["L.exe", "--updated", "1.0.0", "--show-window", photo]), str(pathlib.Path(photo)))
            self.assertEqual(boot.launch_path(["L.exe", photo]), str(pathlib.Path(photo)))
            self.assertIsNone(boot.launch_path(["L.exe", os.path.join(tmp, "yok.jpg")]))
            self.assertIsNone(boot.launch_path(["L.exe", "--updated", "1.0.0"]), "bayrak değeri dosya yolu sanılmaz")
            self.assertIsNone(boot.launch_path(["L.exe"]))

    def test_flag_value(self):
        self.assertEqual(boot.flag_value(["x", "--updated", "1.0.0"], "--updated"), "1.0.0")
        self.assertEqual(boot.flag_value(["x", "--updated=2.0"], "--updated"), "2.0")
        self.assertIsNone(boot.flag_value(["x"], "--updated"))

    def test_prefetch_uses_the_photo_not_the_flags(self):
        from app import main as app_main

        with mock.patch("app.core.image_loader.prefetch") as prefetch:
            app_main._start_prefetch(["L.exe", "--updated", "1.0.0", "--show-window", "foto.jpg"])
            prefetch.assert_called_once_with("foto.jpg")
            prefetch.reset_mock()
            app_main._start_prefetch(["L.exe", "--updated", "1.0.0", "--show-window"])
            prefetch.assert_not_called()


class BeginTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = os.path.join(self._tmp.name, "kullanıcı klasörü")
        os.makedirs(self.folder)
        self.exe = os.path.join(self.folder, "Lumina.exe")
        self.write(self.exe, b"MZ-yeni")
        self.version = "1.0.1"
        for patcher in (
            mock.patch.object(boot, "launcher_path", return_value=pathlib.Path(self.exe)),
            mock.patch.object(boot, "get_version", return_value=self.version),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def write(path, data=b"x"):
        with open(path, "wb") as f:
            f.write(data)

    def read(self, path):
        with open(path, "rb") as f:
            return f.read()

    def test_a_normal_start_reads_the_flags_and_touches_nothing(self):
        with mock.patch.object(su, "startup_guard") as guard, mock.patch.object(su, "wait_for_previous_instance") as wait:
            state = boot.begin(["L.exe", "--updated", "1.0.0", "--show-window"])
        guard.assert_not_called()  # `.old` yok: güncelleme denemesi yok
        wait.assert_not_called()  # başlatıcı `--wait-pid`'i zaten işledi ve iletmedi
        self.assertEqual((state.guard, state.updated_from, state.show_window, state.rolled_back), ("normal", "1.0.0", True, False))
        self.assertEqual(state.launcher, self.exe)

    def test_started_without_the_launcher_there_is_nothing_to_guard(self):
        with mock.patch.object(boot, "launcher_path", return_value=None):
            state = boot.begin(["L.exe"])
        self.assertEqual((state.guard, state.launcher, state.rolled_back), ("normal", None, False))

    def test_wait_pid_given_directly_is_waited_for(self):
        with mock.patch.object(su, "wait_for_previous_instance") as wait:
            boot.begin(["L.exe", "--wait-pid", "123"])
        wait.assert_called_once()
        self.assertEqual(wait.call_args[0][0], ["L.exe", "--wait-pid", "123"])

    def test_a_new_version_that_keeps_dying_is_rolled_back_and_the_old_one_is_started_with_the_photo(self):
        self.write(self.exe + ".old", b"MZ-eski")
        photo = os.path.join(self.folder, "foto.jpg")
        self.write(photo)
        with mock.patch.object(su, "spawn") as spawn:
            self.assertEqual(boot.begin(["L.exe", "--updated", "1.0.0", photo]).guard, "probation")
            self.assertEqual(boot.begin(["L.exe", "--updated", "1.0.0", photo]).guard, "probation")
            state = boot.begin(["L.exe", "--updated", "1.0.0", photo])
        self.assertTrue(state.rolled_back)
        self.assertEqual(self.read(self.exe), b"MZ-eski", "eski sürüm geri geldi")
        self.assertEqual(su.skipped_version(self.exe), self.version, "çöken sürüm bir daha önerilmez")
        spawn.assert_called_once_with(self.exe, [str(pathlib.Path(photo))])

    @unittest.skipUnless(sys.platform == "win32", "DLL arama dizini yalnızca Windows'ta")
    def test_the_rolled_back_launcher_is_started_with_the_default_dll_directory(self):
        """K3: geri alınan eski başlatıcı, çöken yeni sürümün (PyInstaller) DLL dizinini miras almamalı."""
        import ctypes

        from app.core import dll_dir

        marker = os.path.join(self.folder, "çöken sürüm", "_internal")
        os.makedirs(marker)
        self.write(self.exe + ".old", b"MZ-eski")
        seen: list = []
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        previous = dll_dir.current_dll_directory()
        kernel32.SetDllDirectoryW(marker)
        try:
            with mock.patch.object(su, "spawn", side_effect=lambda *a, **k: seen.append(dll_dir.current_dll_directory())):
                for _ in range(3):
                    state = boot.begin(["L.exe", "--updated", "1.0.0"])
            after = dll_dir.current_dll_directory()
        finally:
            kernel32.SetDllDirectoryW(previous or None)
        self.assertTrue(state.rolled_back)
        self.assertEqual(seen, [""])
        self.assertEqual(os.path.normcase(after), os.path.normcase(marker))

    def test_rollback_without_a_photo_starts_the_old_version_plainly(self):
        self.write(self.exe + ".old", b"MZ-eski")
        with mock.patch.object(su, "spawn") as spawn:
            for _ in range(3):
                state = boot.begin(["L.exe", "--updated", "1.0.0"])
        self.assertTrue(state.rolled_back)
        spawn.assert_called_once_with(self.exe, [])

    def test_an_unexpected_error_never_prevents_the_app_from_starting(self):
        with mock.patch.object(su, "startup_guard", side_effect=RuntimeError("beklenmedik")):
            self.write(self.exe + ".old", b"MZ-eski")
            state = boot.begin(["L.exe", "--updated", "1.0.0"])
        self.assertEqual((state.rolled_back, state.updated_from), (False, "1.0.0"))
        with mock.patch.object(boot, "launcher_path", side_effect=OSError("disk")):
            self.assertFalse(boot.begin(["L.exe"]).rolled_back)

    def test_a_failing_respawn_never_raises(self):
        self.write(self.exe + ".old", b"MZ-eski")
        with mock.patch.object(su, "spawn", side_effect=su.PackageError("x", "spawn")):
            for _ in range(3):
                state = boot.begin(["L.exe"])
        self.assertTrue(state.rolled_back)


class _FakeApp:
    def __init__(self):
        self.on_quit = []

        class _Signal:
            def connect(_self, fn):
                self.on_quit.append(fn)

        self.aboutToQuit = _Signal()


class ArmTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.exe = os.path.join(self._tmp.name, "Lumina.exe")
        for suffix in ("", ".old", ".boot", ".bad", ".new"):
            with open(self.exe + suffix, "wb") as f:
                f.write(b"x")
        self.timers: list = []
        patcher = mock.patch("PyQt6.QtCore.QTimer.singleShot", lambda ms, fn: self.timers.append((ms, fn)))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(boot, "_update_in_flight", return_value=False)
        self.in_flight = patcher.start()
        self.addCleanup(patcher.stop)

    def state(self, guard="probation"):
        return boot.BootState(started=0.0, guard=guard, launcher=self.exe)

    def leftovers(self):
        return sorted(s for s in (".old", ".boot", ".bad", ".new") if os.path.exists(self.exe + s))

    def test_only_a_probation_start_proves_the_new_version(self):
        # Normal açılışta `.bad` YOKSA hiçbir şey kurulmaz (olağan yol); geri alınan süreç zaten çıkar (rolled_back).
        os.remove(self.exe + ".bad")
        for guard in ("normal", "rolled_back"):
            app = _FakeApp()
            boot.arm(app, self.state(guard))
            self.assertEqual((app.on_quit, self.timers), ([], []), guard)
        app = _FakeApp()
        boot.arm(app, boot.BootState(started=0.0, guard="probation", launcher=None))
        self.assertEqual(app.on_quit, [])
        app = _FakeApp()
        boot.arm(app, boot.BootState(started=0.0, guard="normal", launcher=None))
        self.assertEqual((app.on_quit, self.timers), ([], []))
        self.assertEqual(self.leftovers(), [".boot", ".new", ".old"])

    # ---- K1: geri almadan sonra `.bad` (bozuk sürümün tam başlatıcı kopyası) silinir
    def test_a_normal_start_with_a_leftover_bad_copy_deletes_only_that_copy_after_the_healthy_time(self):
        with open(self.exe + ".skip", "w", encoding="utf-8") as f:
            f.write("1.0.1")
        app = _FakeApp()
        boot.arm(app, self.state("normal"))
        ms, fire = self.timers[0]
        self.assertEqual(ms, int(su.HEALTHY_AFTER_SECONDS * 1000))
        self.assertEqual(self.leftovers(), [".bad", ".boot", ".new", ".old"], "süre dolmadan dokunulmaz")
        fire()
        self.assertEqual(self.leftovers(), [".boot", ".new", ".old"], "yalnızca `.bad` silinir: başka güncellemenin kurtarma noktası kalır")
        self.assertEqual(su.skipped_version(self.exe), "1.0.1", "`.skip` kalır: bozuk sürüm bir daha önerilmez")

    def test_a_normal_start_deletes_the_bad_copy_on_a_clean_exit_too(self):
        with open(self.exe + ".skip", "w", encoding="utf-8") as f:
            f.write("1.0.1")
        app = _FakeApp()
        boot.arm(app, self.state("normal"))
        app.on_quit[0]()
        self.assertEqual(self.leftovers(), [".boot", ".new", ".old"])
        self.assertEqual(su.skipped_version(self.exe), "1.0.1")

    def test_bad_cleanup_happens_once_and_never_raises(self):
        app = _FakeApp()
        boot.arm(app, self.state("normal"))
        app.on_quit[0]()
        with open(self.exe + ".bad", "wb") as f:  # sonradan yeni bir geri alma `.bad` bıraktı
            f.write(b"y")
        self.timers[0][1]()
        self.assertTrue(os.path.exists(self.exe + ".bad"), "bir süreç yalnızca bir kez temizler")
        with open(self.exe + ".bad", "wb") as f:
            f.write(b"z")
        app = _FakeApp()
        boot.arm(app, self.state("normal"))
        with mock.patch.object(boot.os, "remove", side_effect=PermissionError("kilitli")):
            app.on_quit[0]()  # kilitli dosya: istisna yok
        self.assertTrue(os.path.exists(self.exe + ".bad"), "bir sonraki açılışta yeniden denenir")

    def test_the_bad_copy_is_cleaned_even_while_this_process_installs_an_update(self):
        # `.bad` yeni güncellemenin kurtarma noktası (`.old`) değildir: güncelleme sürerken de silinmesi zararsız
        self.in_flight.return_value = True
        app = _FakeApp()
        boot.arm(app, self.state("normal"))
        app.on_quit[0]()
        self.assertEqual(self.leftovers(), [".boot", ".new", ".old"])

    def test_the_timer_confirms_after_sixty_seconds(self):
        app = _FakeApp()
        boot.arm(app, self.state())
        ms, fire = self.timers[0]
        self.assertEqual(ms, int(su.HEALTHY_AFTER_SECONDS * 1000))
        self.assertEqual(self.leftovers(), [".bad", ".boot", ".new", ".old"])
        fire()
        self.assertEqual(self.leftovers(), [], ".old ve yan dosyalar silinir")

    def test_a_clean_exit_confirms_even_after_a_few_seconds_only(self):
        app = _FakeApp()
        boot.arm(app, self.state())
        app.on_quit[0]()  # kullanıcı hemen kapattı (aboutToQuit): çökme değil
        self.assertEqual(self.leftovers(), [])

    def test_confirming_twice_is_harmless(self):
        app = _FakeApp()
        boot.arm(app, self.state())
        app.on_quit[0]()
        with open(self.exe + ".old", "wb") as f:  # sonradan başka bir güncelleme geldi
            f.write(b"y")
        self.timers[0][1]()
        self.assertTrue(os.path.exists(self.exe + ".old"), "bir süreç yalnızca bir kez doğrular")

    def test_a_process_that_installs_an_update_never_deletes_the_new_recovery_point(self):
        self.in_flight.return_value = True
        app = _FakeApp()
        boot.arm(app, self.state())
        app.on_quit[0]()
        self.timers[0][1]()
        self.assertEqual(self.leftovers(), [".bad", ".boot", ".new", ".old"])

    def test_skip_marker_survives_the_confirmation(self):
        with open(self.exe + ".skip", "w", encoding="utf-8") as f:
            f.write("0.9.0")
        app = _FakeApp()
        boot.arm(app, self.state())
        app.on_quit[0]()
        self.assertEqual(su.skipped_version(self.exe), "0.9.0")


class CrashVersusUserCloseTest(unittest.TestCase):
    """Güncellemeden sonra art arda kısa açılış/kapanış sağlam sürümü geri aldırmaz; gerçekten çöken sürüm geri alınır."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.exe = os.path.join(self._tmp.name, "Lumina.exe")
        for patcher in (
            mock.patch.object(boot, "launcher_path", return_value=pathlib.Path(self.exe)),
            mock.patch.object(boot, "get_version", return_value="1.0.1"),
            mock.patch.object(boot, "_update_in_flight", return_value=False),
            mock.patch.object(su, "spawn"),
            mock.patch("PyQt6.QtCore.QTimer.singleShot", lambda ms, fn: None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        with open(self.exe, "wb") as f:
            f.write(b"MZ-yeni")
        with open(self.exe + ".old", "wb") as f:
            f.write(b"MZ-eski")

    def start_and_close_cleanly(self):
        state = boot.begin(["L.exe", "--updated", "1.0.0"])
        app = _FakeApp()
        boot.arm(app, state)
        for fn in app.on_quit:
            fn()
        return state

    def test_five_quick_open_close_cycles_right_after_an_update_keep_the_new_version(self):
        guards = [self.start_and_close_cleanly().guard for _ in range(5)]
        self.assertEqual(guards, ["probation", "normal", "normal", "normal", "normal"])
        with open(self.exe, "rb") as f:
            self.assertEqual(f.read(), b"MZ-yeni")
        self.assertIsNone(su.skipped_version(self.exe))

    def test_a_version_that_dies_at_startup_is_rolled_back_on_the_third_start(self):
        guards = [boot.begin(["L.exe"]).guard for _ in range(3)]  # hiçbiri aboutToQuit'e varamadı (çöktü)
        self.assertEqual(guards, ["probation", "probation", "rolled_back"])
        with open(self.exe, "rb") as f:
            self.assertEqual(f.read(), b"MZ-eski")
        self.assertEqual(su.skipped_version(self.exe), "1.0.1")

    def test_after_a_rollback_the_bad_copy_goes_away_once_the_old_version_runs_healthy(self):
        # E1 mantığı: çöken yeni sürüm -> 3. açılışta geri alma -> eski sürüm normal açılır -> sağlıklı süre sonra `.bad` silinir
        guards = [boot.begin(["L.exe"]).guard for _ in range(3)]
        self.assertEqual(guards, ["probation", "probation", "rolled_back"])
        self.assertTrue(os.path.exists(self.exe + ".bad"), "geri almadan hemen sonra bozuk sürümün kopyası durur")
        timers: list = []
        with mock.patch("PyQt6.QtCore.QTimer.singleShot", lambda ms, fn: timers.append((ms, fn))):
            state = boot.begin(["L.exe"])  # geri alınan eski sürümün açılışı: `.old` yok -> normal
            app = _FakeApp()
            boot.arm(app, state)
        self.assertEqual(state.guard, "normal")
        self.assertEqual(len(timers), 1)
        self.assertTrue(os.path.exists(self.exe + ".bad"))
        timers[0][1]()
        self.assertFalse(os.path.exists(self.exe + ".bad"), "139 MB'lık bozuk kopya sağlıklı süre sonunda silindi")
        self.assertEqual(su.skipped_version(self.exe), "1.0.1", "bozuk sürüm bir daha önerilmez")
        with open(self.exe, "rb") as f:
            self.assertEqual(f.read(), b"MZ-eski")
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.exe))), ["Lumina.exe", "Lumina.exe.skip"])

    def test_one_crash_then_a_good_run_keeps_the_new_version(self):
        self.assertEqual(boot.begin(["L.exe"]).guard, "probation")  # çöktü
        self.start_and_close_cleanly()  # ikinci açılış sağlam
        self.assertEqual(boot.begin(["L.exe"]).guard, "normal")


class UpdateInFlightTest(unittest.TestCase):
    def test_reflects_the_update_service_state_without_importing_it(self):
        fake_module = mock.Mock()
        service = mock.Mock()
        fake_module.get_update_service.return_value = service
        with mock.patch.dict(sys.modules, {"app.ui.updates": fake_module}):
            for swapped, on_exit, manual, expected in (
                (False, False, False, False), (True, False, False, True), (False, True, False, True), (False, False, True, True),
            ):
                service.installer.swapped, service._installing_on_exit, service._installing = swapped, on_exit, manual
                self.assertEqual(boot._update_in_flight(), expected, (swapped, on_exit, manual))
            fake_module.get_update_service.return_value = None
            self.assertFalse(boot._update_in_flight())
        with mock.patch.dict(sys.modules):
            sys.modules.pop("app.ui.updates", None)
            self.assertFalse(boot._update_in_flight(), "hizmet hiç kurulmadıysa (çok kısa oturum) güncelleme de yok")
            self.assertNotIn("app.ui.updates", sys.modules, "kapanışta ağır modül yüklenmez")


class AnnounceTest(unittest.TestCase):
    def window(self, lang="tr"):
        win = mock.Mock()
        win.lang = lang
        return win

    def test_toast_after_an_update_in_the_users_language(self):
        with mock.patch.object(boot, "get_version", return_value="1.0.1"):
            win = self.window("tr")
            boot.announce(win, boot.BootState(started=0.0, updated_from="1.0.0"))
            self.assertEqual(win._flash.call_args[0][0], "Lumina v1.0.1 sürümüne güncellendi.")
            win = self.window("en")
            boot.announce(win, boot.BootState(started=0.0, updated_from="1.0.0"))
            self.assertEqual(win._flash.call_args[0][0], "Lumina was updated to v1.0.1.")

    def test_no_toast_for_a_normal_start_or_a_same_version_restart(self):
        with mock.patch.object(boot, "get_version", return_value="1.0.1"):
            for old in (None, "", "1.0.1"):
                win = self.window()
                boot.announce(win, boot.BootState(started=0.0, updated_from=old))
                win._flash.assert_not_called()

    def test_a_failing_toast_never_breaks_startup(self):
        win = self.window()
        win._flash.side_effect = RuntimeError("pencere yok")
        with mock.patch.object(boot, "get_version", return_value="1.0.1"):
            boot.announce(win, boot.BootState(started=0.0, updated_from="1.0.0"))


class MainWiringTest(unittest.TestCase):
    def test_a_rolled_back_start_exits_before_any_window_exists(self):
        from app import main as app_main

        state = boot.BootState(started=0.0, guard="rolled_back", rolled_back=True)
        with mock.patch.object(boot, "begin", return_value=state), \
             mock.patch.object(app_main, "QApplication", side_effect=AssertionError("pencere/uygulama kurulmamalı")), \
             mock.patch.object(app_main, "_start_prefetch", side_effect=AssertionError("ön-çözüm başlamamalı")):
            self.assertEqual(app_main.main(["Lumina.exe", "foto.jpg"]), 0)


if __name__ == "__main__":
    unittest.main()
