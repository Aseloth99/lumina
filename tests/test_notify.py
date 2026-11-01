"""notify.py birim testleri: her platform dalı sahte runner/tepsi ile; Windows'ta alt süreç YOK."""

import os
import sys
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtGui import QIcon, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

from app.core import notify  # noqa: E402
from app.core.notify import Notifier, show_toast  # noqa: E402

app = QApplication.instance() or QApplication([])
MI = QSystemTrayIcon.MessageIcon


class FakeTray:
    def __init__(self, visible=True, fails=False):
        self._visible = visible
        self.fails = fails
        self.calls = []

    def isVisible(self):
        return self._visible

    def showMessage(self, title, body, icon, msecs):
        if self.fails:
            raise RuntimeError("tepsi bozuk")
        self.calls.append((title, body, icon, msecs))


class WrapperTray:
    """showMessage'ı olmayan eski sarmalayıcı (notify / show_message)."""

    def __init__(self, method="notify", result=None, visible=True):
        self.messages = []
        self._visible = visible
        setattr(self, method, self._take)
        self._result = result

    def isVisible(self):
        return self._visible

    def _take(self, title, body):
        self.messages.append((title, body))
        return self._result


def no_process(*a, **k):
    raise AssertionError("alt süreç başlatılmamalıydı")


def make(platform, **kw):
    kw.setdefault("supports_messages", lambda: True)
    return Notifier("Uygulama", platform=platform, **kw)


class TrayPathTests(unittest.TestCase):
    def test_visible_tray_is_used_on_every_platform_and_never_spawns(self) -> None:
        for platform in ("win32", "darwin", "linux"):
            tray = FakeTray()
            with self.subTest(platform=platform):
                n = make(platform, tray_provider=lambda t=tray: t, runner=no_process)
                self.assertTrue(n.notify("Başlık", "İleti", timeout_ms=3000))
                self.assertEqual(tray.calls, [("Başlık", "İleti", MI.Information, 3000)])

    def test_critical_uses_critical_icon_and_custom_icon_otherwise(self) -> None:
        tray = FakeTray()
        pix = QPixmap(8, 8)
        pix.fill()
        icon = QIcon(pix)
        n = make("win32", tray_provider=lambda: tray, icon_provider=lambda: icon)
        n.notify("a", "b")
        n.notify("a", "b", critical=True)
        self.assertIs(tray.calls[0][2], icon)
        self.assertEqual(tray.calls[1][2], MI.Critical)

    def test_timeout_has_a_floor(self) -> None:
        tray = FakeTray()
        make("win32", tray_provider=lambda: tray).notify("a", "b", timeout_ms=10)
        self.assertEqual(tray.calls[0][3], 1000)

    def test_disabled_does_nothing(self) -> None:
        tray = FakeTray()
        fallback = mock.Mock()
        for provider in (lambda: False, lambda: 1 / 0):
            n = make("darwin", tray_provider=lambda: tray, enabled_provider=provider, runner=no_process,
                     inapp_fallback=fallback)
            self.assertFalse(n.notify("a", "b"))
        self.assertEqual(tray.calls, [])
        fallback.assert_not_called()

    def test_provider_is_read_at_call_time(self) -> None:
        state = {"on": False}
        tray = FakeTray()
        n = make("win32", tray_provider=lambda: tray, enabled_provider=lambda: state["on"])
        self.assertFalse(n.notify("a", "b"))
        state["on"] = True
        self.assertTrue(n.notify("a", "b"))

    def test_wrapper_trays_without_showmessage(self) -> None:
        for method in ("notify", "show_message"):
            tray = WrapperTray(method)
            with self.subTest(method=method):
                self.assertTrue(make("win32", tray_provider=lambda t=tray: t).notify("T", "M"))
                self.assertEqual(tray.messages, [("T", "M")])
        self.assertFalse(make("win32", tray_provider=lambda: WrapperTray(result=False)).notify("T", "M"))

    def test_real_qsystemtrayicon_type_is_accepted(self) -> None:
        tray = mock.Mock(spec=QSystemTrayIcon)
        tray.isVisible.return_value = True
        self.assertTrue(make("linux", tray_provider=lambda: tray).notify("T", "M"))
        self.assertEqual(tray.showMessage.call_args.args[:2], ("T", "M"))

    def test_default_supports_messages_is_qt_static(self) -> None:
        n = Notifier("A", platform="win32")
        self.assertEqual(n._supports_messages(), QSystemTrayIcon.supportsMessages())


class WindowsNeverSpawnsTests(unittest.TestCase):
    def test_no_tray_means_fallback_not_a_process(self) -> None:
        fallback = mock.Mock()
        with mock.patch.object(notify.subprocess, "run", side_effect=no_process), \
                mock.patch.object(notify.subprocess, "Popen", side_effect=no_process), \
                mock.patch.object(notify.threading, "Thread", side_effect=no_process):
            for tray in (None, FakeTray(visible=False), FakeTray(fails=True)):
                n = make("win32", tray_provider=lambda t=tray: t, runner=no_process, inapp_fallback=fallback,
                         which=lambda name: "/usr/bin/" + name)
                self.assertTrue(n.notify("Başlık", "İleti"))
        self.assertEqual(fallback.call_count, 3)
        fallback.assert_called_with("Başlık", "İleti")

    def test_without_fallback_returns_false_and_spawns_nothing(self) -> None:
        with mock.patch.object(notify.subprocess, "run", side_effect=no_process):
            self.assertFalse(make("win32", runner=no_process).notify("a", "b"))
            self.assertFalse(make("cygwin").notify("a", "b"))  # bilinmeyen platform da süreç açmaz

    def test_default_platform_on_this_machine_is_respected(self) -> None:
        self.assertEqual(Notifier("A")._platform, sys.platform)

    def test_module_has_no_powershell_or_winotify(self) -> None:
        with open(notify.__file__, encoding="utf-8") as handle:
            source = handle.read().lower()
        self.assertNotIn("import winotify", source)
        self.assertNotIn("powershell.exe", source)
        self.assertNotIn("create_no_window", source)


class MacOSTests(unittest.TestCase):
    def test_osascript_when_tray_missing(self) -> None:
        runner = mock.Mock(return_value=mock.Mock(returncode=0))
        n = make("darwin", runner=runner)
        self.assertTrue(n.notify("Başlık", "İleti"))
        cmd = runner.call_args.args[0]
        self.assertEqual(cmd[:2], ["osascript", "-e"])
        self.assertEqual(cmd[2], 'display notification "İleti" with title "Başlık"')
        self.assertEqual(runner.call_args.kwargs["timeout"], 10)

    def test_quoting_is_injection_safe(self) -> None:
        cmd = notify.build_macos_command('A"; do shell script "evil', 'x\\" & (do shell script "y")\nsatır')
        script = cmd[2]
        self.assertEqual(script.count('\n'), 0)
        self.assertNotIn('"; do shell script "evil', script.replace('\\"', ''))
        self.assertEqual(notify.applescript_quote('a"b\\c\n\x07d'), 'a\\"b\\\\c  d')
        # tırnaksız bir `"` kalmadığı için betik tek `display notification` olarak kalır
        stripped = script.replace('\\\\', '').replace('\\"', '')
        self.assertEqual(stripped.count('"'), 4)

    def test_invisible_tray_falls_to_osascript_and_failure_to_inapp(self) -> None:
        runner = mock.Mock(return_value=mock.Mock(returncode=1))
        fallback = mock.Mock()
        n = make("darwin", tray_provider=lambda: FakeTray(visible=False), runner=runner, inapp_fallback=fallback)
        self.assertTrue(n.notify("T", "M"))
        runner.assert_called_once()
        fallback.assert_called_once_with("T", "M")
        boom = mock.Mock(side_effect=OSError("osascript yok"))
        self.assertFalse(make("darwin", runner=boom).notify("T", "M"))

    def test_tray_without_message_support_uses_osascript(self) -> None:
        tray = FakeTray()
        runner = mock.Mock(return_value=None)
        n = Notifier("A", tray_provider=lambda: tray, platform="darwin", runner=runner,
                     supports_messages=lambda: False)
        self.assertTrue(n.notify("T", "M"))
        self.assertEqual(tray.calls, [])
        runner.assert_called_once()


class LinuxTests(unittest.TestCase):
    def test_notify_send_command(self) -> None:
        runner = mock.Mock(return_value=mock.Mock(returncode=0))
        n = make("linux", runner=runner, which=lambda name: "/usr/bin/" + name)
        self.assertTrue(n.notify("-Başlık", "İleti", timeout_ms=7000))
        self.assertEqual(runner.call_args.args[0],
                         ["/usr/bin/notify-send", "-a", "Uygulama", "-t", "7000", "-i", "dialog-information",
                          "--", "-Başlık", "İleti"])
        self.assertIs(runner.call_args.kwargs["check"], False)

    def test_critical_and_icon_path(self) -> None:
        runner = mock.Mock(return_value=mock.Mock(returncode=0))
        n = make("linux", runner=runner, which=lambda name: "/usr/bin/" + name, icon_provider=lambda: "/i/app.png")
        n.notify("T", "M", critical=True)
        cmd = runner.call_args.args[0]
        self.assertIn("critical", cmd)
        self.assertEqual(cmd[cmd.index("-i") + 1], "/i/app.png")

    def test_missing_notify_send_goes_to_inapp_or_false(self) -> None:
        runner = mock.Mock()
        fallback = mock.Mock()
        n = make("linux", runner=runner, which=lambda name: None, inapp_fallback=fallback)
        self.assertTrue(n.notify("T", "M"))
        runner.assert_not_called()
        fallback.assert_called_once_with("T", "M")
        self.assertFalse(make("linux", runner=runner, which=lambda name: None).notify("T", "M"))

    def test_tray_preferred_over_notify_send(self) -> None:
        tray = FakeTray()
        runner = mock.Mock()
        make("linux", tray_provider=lambda: tray, runner=runner, which=lambda n: "/x").notify("T", "M")
        runner.assert_not_called()
        self.assertEqual(len(tray.calls), 1)

    def test_default_runner_uses_subprocess_run_in_a_thread(self) -> None:
        done = threading.Event()
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"], seen["kw"] = cmd, kwargs
            done.set()

        n = make("linux", which=lambda name: "/usr/bin/notify-send")
        with mock.patch.object(notify.subprocess, "run", side_effect=fake_run):
            self.assertTrue(n.notify("T", "M"))
            self.assertTrue(done.wait(5))
        self.assertEqual(seen["cmd"][0], "/usr/bin/notify-send")
        self.assertEqual(seen["kw"]["timeout"], 10)

    def test_inapp_fallback_failure_is_swallowed(self) -> None:
        n = make("win32", inapp_fallback=mock.Mock(side_effect=RuntimeError("x")))
        self.assertFalse(n.notify("T", "M"))


class ShowToastCompatTests(unittest.TestCase):
    def setUp(self) -> None:
        # çevrimdışı (offscreen) platformda supportsMessages() False döner; gerçek Windows/macOS'u taklit et
        patcher = mock.patch.object(QSystemTrayIcon, "supportsMessages", staticmethod(lambda: True))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_same_signature_and_tray_path(self) -> None:
        tray = FakeTray()
        self.assertTrue(show_toast("Başlık", "İleti", "MediaCompressor", tray=tray, platform="win32"))
        self.assertEqual(tray.calls[0][:2], ("Başlık", "İleti"))

    def test_windows_without_tray_returns_false_without_process(self) -> None:
        with mock.patch.object(notify.subprocess, "run", side_effect=no_process):
            self.assertFalse(show_toast("a", "b", platform="win32"))
            self.assertFalse(show_toast("a", "b", "MediaCompressor", tray=FakeTray(visible=False), platform="win32"))

    def test_old_keyword_arguments_still_work(self) -> None:
        runner = mock.Mock(return_value=None)
        self.assertTrue(show_toast("T", "M", platform="darwin", tray=None, runner=runner, which=lambda n: "/x"))
        runner.assert_called_once()
        self.assertTrue(show_toast("T", "M", platform="linux", runner=runner, which=lambda n: "/usr/bin/" + n,
                                   icon_path="/i.png"))

    def test_duck_tray_with_only_notify_and_isvisible(self) -> None:
        tray = WrapperTray("notify")
        self.assertTrue(show_toast("T", "M", platform="linux", tray=tray))
        self.assertEqual(tray.messages, [("T", "M")])

    def test_never_raises(self) -> None:
        bad = mock.Mock()
        bad.isVisible.side_effect = RuntimeError("x")
        self.assertFalse(show_toast("a", "b", tray=bad, platform="win32"))


if __name__ == "__main__":
    unittest.main()
