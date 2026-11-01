"""Sistem tepsisi simgesi: Lumina çalıştığı sürece görünür, süreç bitince kaybolur; ayarlarda açma/kapama yok.

* Sahiplik: birden çok süreçte tek simge (ilk süreç sahip, ikinci değil, sahip çıkınca üçüncü alır; süreç ölünce kilit düşer).
* Menü: [Güncelle · vX] — Göster/Gizle (dinamik), Ayarlar, Tema, Dil — Dosya Aç… — Çıkış; güncelleme satırı yalnızca bekleyen güncellemede.
* Çıkış: pencerenin normal kapanış yolu. Tepsi yokken çökmez. Ayarlarda tepsi kontrolü yok. tr + en metinler.

Gerçek `QSystemTrayIcon` tıklaması burada sınanamaz (offscreen); menü/eylem mantığı doğrudan sürülür. Testler kullanıcının
gerçek kilit dosyasına (%TEMP%\\Lumina.tray.lock) dokunmaz: her denetleyiciye geçici dizinde sahiplik verilir.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QCoreApplication, QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QIcon  # noqa: E402
from PyQt6.QtWidgets import QAbstractButton, QApplication, QComboBox, QLabel, QSystemTrayIcon, QWidget  # noqa: E402

from app.core.tray_owner import LOCK_NAME, TrayOwnership, default_lock_path  # noqa: E402


def pump(ms: int = 30) -> None:
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.003)
    QCoreApplication.processEvents()


def layout(menu) -> list[str]:
    """Görünür satırlar sırayla; ayırıcı "-" olarak (menünün gerçek sırası ve ayırıcıları)."""
    return ["-" if a.isSeparator() else a.text() for a in menu.actions() if a.isVisible()]


def plain(text: str) -> str:
    """Seçili satırların "✓  " / dört boşluk ön ekini at."""
    return text.replace("✓", "").strip()


class FakeWindow:
    """MainWindow'un tepsinin kullandığı yüzeyi: kayıt tutar, tema/dil değişince kendi durumunu günceller."""

    def __init__(self, lang: str = "tr", theme: str = "system", lang_choice: str = "auto") -> None:
        self.lang, self.theme, self.lang_choice = lang, theme, lang_choice
        self.calls: list[tuple] = []
        self.closed = 0
        self.state = Qt.WindowState.WindowNoState
        self.visible = True

    def isVisible(self) -> bool:
        return self.visible

    def isMinimized(self) -> bool:
        return bool(self.state & Qt.WindowState.WindowMinimized)

    def hide(self) -> None:
        self.calls.append(("hide",))
        self.visible = False

    def open_settings_dialog(self, initial_tab: int = 0) -> None:
        self.calls.append(("settings", initial_tab))

    def windowState(self):
        return self.state

    def setWindowState(self, state) -> None:
        self.calls.append(("state", state))
        self.state = state

    def show(self) -> None:
        self.calls.append(("show",))
        self.visible = True

    def raise_(self) -> None:
        self.calls.append(("raise",))

    def activateWindow(self) -> None:
        self.calls.append(("activate",))

    def set_theme(self, mode: str) -> None:
        self.calls.append(("theme", mode))
        self.theme = mode

    def set_language(self, code: str) -> None:
        self.calls.append(("lang", code))
        self.lang_choice = code
        self.lang = code if code in ("tr", "en") else "tr"

    def select_file(self) -> None:
        self.calls.append(("select_file",))

    def close(self) -> None:
        self.closed += 1


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._ini = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._ini.name)
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._ini.cleanup()

    def setUp(self) -> None:
        self._lock_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._lock_dir.cleanup)
        self.lock_path = pathlib.Path(self._lock_dir.name) / LOCK_NAME
        self._trays: list = []
        self.addCleanup(self._shutdown_all)

    def _shutdown_all(self) -> None:
        for tray in self._trays:
            tray.shutdown()

    def make_tray(self, windows=None, *, version: str | list = "", available=lambda: True, open_update=None, ownership=None):
        from app.ui.tray import LuminaTray

        wins = [FakeWindow()] if windows is None else windows
        holder = version if isinstance(version, list) else [version]
        tray = LuminaTray(
            self.app,
            QIcon(),
            windows=lambda: list(wins),
            ownership=ownership or TrayOwnership(self.lock_path),
            available=available,
            pending_version=lambda: holder[0],
            open_update=open_update or (lambda: None),
        )
        self._trays.append(tray)
        tray.wins, tray.version_holder = wins, holder
        return tray

    def started(self, **kw):
        tray = self.make_tray(**kw)
        self.assertTrue(tray.start())
        return tray


# ---------------------------------------------------------------------------------------------------------- sahiplik
class OwnershipTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._dir.cleanup)
        self.path = pathlib.Path(self._dir.name) / LOCK_NAME
        self.made: list[TrayOwnership] = []
        self.addCleanup(lambda: [o.release() for o in self.made])

    def proc(self) -> TrayOwnership:
        owner = TrayOwnership(self.path)
        self.made.append(owner)
        return owner

    def test_first_process_owns_second_does_not_and_a_third_takes_over_when_the_owner_leaves(self) -> None:
        first, second, third = self.proc(), self.proc(), self.proc()
        self.assertTrue(first.try_acquire())
        self.assertTrue(first.owned)
        self.assertFalse(second.try_acquire(), "ikinci süreç sahip olmamalı")
        self.assertFalse(second.owned)
        self.assertTrue(first.try_acquire(), "sahip yoklayınca sahip kalır (tekrar çağrılabilir)")
        first.release()
        self.assertFalse(first.owned)
        self.assertTrue(third.try_acquire(), "sahip çıkınca sıradaki alır")
        self.assertTrue(third.owned)
        self.assertFalse(second.try_acquire(), "üçüncü sahipken ikinci yine alamaz")
        self.assertFalse(second.owned)

    def test_release_without_ownership_is_harmless(self) -> None:
        owner = self.proc()
        owner.release()
        owner.release()
        self.assertFalse(owner.owned)
        self.assertTrue(owner.try_acquire())

    def test_an_old_lock_file_held_by_a_live_process_is_not_stolen_by_age(self) -> None:
        """Saatlerce açık kalan sahibin kilidi "30 sn'den eski" diye çalınmamalı (yaşa göre bayatlama kapalı)."""
        owner, other = self.proc(), self.proc()
        self.assertTrue(owner.try_acquire())
        old = time.time() - 6 * 3600
        os.utime(self.path, (old, old))
        self.assertFalse(other.try_acquire())

    def test_lock_is_released_when_the_owning_process_dies(self) -> None:
        """Gerçek ikinci süreç: sahibi öldürülünce (çökme/görev yöneticisi) kilit bayat kalmaz, ben alırım."""
        code = (
            "import sys, time\n"
            "sys.path.insert(0, sys.argv[2])\n"
            "from app.core.tray_owner import TrayOwnership\n"
            "owner = TrayOwnership(sys.argv[1])\n"
            "print('got' if owner.try_acquire() else 'no', flush=True)\n"
            "time.sleep(120)\n"
        )
        child = subprocess.Popen([sys.executable, "-B", "-c", code, str(self.path), str(ROOT)],
                                 stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (child.kill(), child.wait(10), child.stdout.close()))
        self.assertEqual(child.stdout.readline().strip(), "got")
        mine = self.proc()
        self.assertFalse(mine.try_acquire(), "sahibi canlıyken alınamaz")
        child.kill()
        child.wait(10)
        deadline = time.monotonic() + 5
        got = False
        while time.monotonic() < deadline and not got:
            got = mine.try_acquire()
            time.sleep(0.05)
        self.assertTrue(got, "sahip ölünce kilit düşmeli")

    def test_default_lock_lives_in_the_temp_folder_and_is_named_for_lumina(self) -> None:
        path = default_lock_path()
        self.assertEqual(path.parent, pathlib.Path(tempfile.gettempdir()))
        self.assertTrue(path.name.startswith("Lumina.tray") and path.name.endswith(".lock"), path.name)


# ------------------------------------------------------------------------------------------------- denetleyici: sahiplik
class ControllerOwnershipTest(_Base):
    def test_only_the_owner_shows_an_icon_and_a_waiting_process_takes_over_when_it_leaves(self) -> None:
        first = self.started()
        second, third = self.make_tray(), self.make_tray()
        self.assertFalse(second.start())
        self.assertFalse(third.start())
        self.assertEqual((first.state, second.state, third.state), ("owner", "waiting", "waiting"))
        self.assertIsNotNone(first.tray)
        self.assertIsNone(second.tray)
        self.assertIsNone(second.menu, "simgesiz süreçte menü bile kurulmaz")
        self.assertTrue(second._poll.isActive(), "bekleyen süreç sahipliği yoklar")
        first.shutdown()
        self.assertIsNone(first.tray, "çıkan sürecin simgesi kalkar")
        second._claim()  # yoklama zamanlayıcısının tıkı
        self.assertEqual(second.state, "owner")
        self.assertIsNotNone(second.tray)
        self.assertFalse(second._poll.isActive(), "sahip olunca yoklama durur")
        third._claim()
        self.assertEqual(third.state, "waiting", "ikincisi sahipken üçüncü alamaz")

    def test_poll_timer_is_the_one_that_takes_over(self) -> None:
        first = self.started()
        second = self.make_tray()
        second.start()
        second._poll.setInterval(10)
        first.shutdown()
        deadline = time.monotonic() + 3
        while second.state != "owner" and time.monotonic() < deadline:
            pump(20)
        self.assertEqual(second.state, "owner")

    def test_shutdown_removes_the_icon_and_frees_the_lock(self) -> None:
        tray = self.started()
        icon = tray.tray
        tray.shutdown()
        self.assertEqual(tray.state, "stopped")
        self.assertFalse(icon.isVisible())
        self.assertTrue(TrayOwnership(self.lock_path).try_acquire(), "çıkışta kilit bırakılmalı")
        self.assertFalse(tray.start(), "durdurulmuş denetleyici yeniden simge göstermez")

    def test_without_a_system_tray_it_runs_silently_and_never_takes_the_lock(self) -> None:
        tray = self.make_tray(available=lambda: False)
        self.assertFalse(tray.start())
        self.assertEqual(tray.state, "unavailable")
        self.assertIsNone(tray.tray)
        self.assertFalse(tray._poll.isActive())
        other = TrayOwnership(self.lock_path)
        self.addCleanup(other.release)
        self.assertTrue(other.try_acquire(), "tepsisiz süreç kilidi tutmamalı")
        tray.shutdown()  # çökmez

    def test_a_failing_tray_check_or_icon_never_breaks_the_viewer(self) -> None:
        def boom() -> bool:
            raise OSError("D-Bus yok")

        tray = self.make_tray(available=boom)
        self.assertFalse(tray.start())
        self.assertEqual(tray.state, "unavailable")

        tray = self.make_tray()
        with mock.patch("app.ui.tray.QSystemTrayIcon", side_effect=RuntimeError("simge oluşmadı")):
            self.assertFalse(tray.start())
        self.assertEqual(tray.state, "unavailable")
        self.assertTrue(TrayOwnership(self.lock_path).try_acquire(), "başarısız denemede kilit bırakılmalı")

    def test_attach_tray_is_once_per_app_and_stops_with_the_app(self) -> None:
        from app.ui import tray as tray_mod

        old = getattr(self.app, "_lumina_tray", None)
        self.addCleanup(lambda: setattr(self.app, "_lumina_tray", old))
        self.app._lumina_tray = None
        with mock.patch.object(tray_mod, "TrayOwnership", lambda: TrayOwnership(self.lock_path)), \
             mock.patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True), \
             mock.patch.object(tray_mod, "_session_windows", lambda: [FakeWindow()]):
            tray = tray_mod.attach_tray(self.app, QIcon())
            self._trays.append(tray)
            self.assertIs(tray_mod.attach_tray(self.app, QIcon()), tray)
            self.assertEqual(tray.state, "owner")
            self.app.aboutToQuit.emit()
        self.assertEqual(tray.state, "stopped")
        self.assertTrue(TrayOwnership(self.lock_path).try_acquire())

    def test_main_attaches_the_tray_without_ever_failing_startup(self) -> None:
        from app import main as app_main

        with mock.patch("app.ui.tray.attach_tray") as attach:
            app_main._attach_tray(self.app, "icon")
        attach.assert_called_once_with(self.app, "icon")
        with mock.patch("app.ui.tray.attach_tray", side_effect=RuntimeError("x")):
            app_main._attach_tray(self.app, "icon")  # istisna sızmaz


# ---------------------------------------------------------------------------------------------------------- menü
class MenuTest(_Base):
    def test_menu_items_and_order_in_turkish(self) -> None:
        tray = self.started()
        self.assertEqual(layout(tray.menu), ["Gizle", "Ayarlar", "Tema", "Dil", "-", "Dosya Aç…", "-", "Çıkış"])
        self.assertEqual([plain(a.text()) for a in tray.theme_menu.actions()], ["Sistem", "Koyu", "Açık"])
        languages = [plain(a.text()) for a in tray.lang_menu.actions()]
        self.assertTrue(languages[0].startswith("Otomatik") or "Auto" in languages[0], languages)
        self.assertTrue(any(x.endswith("Türkçe") for x in languages), languages)
        self.assertTrue(any(x.endswith("English") for x in languages), languages)
        self.assertEqual(tray.tray.toolTip(), "Lumina")

    def test_menu_items_in_english(self) -> None:
        tray = self.started(windows=[FakeWindow(lang="en", lang_choice="en")])
        self.assertEqual(layout(tray.menu), ["Hide", "Settings", "Theme", "Language", "-", "Open File…", "-", "Exit"])
        self.assertEqual([plain(a.text()) for a in tray.theme_menu.actions()], ["System", "Dark", "Light"])

    def test_every_row_has_an_icon_and_a_label(self) -> None:
        tray = self.started()
        for act in (tray.show_action, tray.settings_action, tray.open_action, tray.quit_action, tray.update_action):
            self.assertFalse(act.icon().isNull(), act.text())
            self.assertTrue(act.text().strip())
        for sub in (tray.theme_menu, tray.lang_menu):
            self.assertFalse(sub.icon().isNull(), sub.title())
            self.assertTrue(sub.title().strip())
            for act in sub.actions():
                self.assertFalse(act.icon().isNull(), act.text())
                self.assertTrue(plain(act.text()))

    def test_update_row_is_hidden_without_a_pending_update(self) -> None:
        tray = self.started()
        self.assertFalse(tray.update_action.isVisible())
        self.assertFalse(tray.update_separator.isVisible())
        self.assertNotIn("Güncelle", " ".join(layout(tray.menu)))

    def test_update_row_is_the_bold_first_row_with_the_version_and_follows_the_service_state(self) -> None:
        tray = self.started(version="2.5.0")
        self.assertEqual(
            layout(tray.menu),
            ["Güncelle · v2.5.0", "-", "Gizle", "Ayarlar", "Tema", "Dil", "-", "Dosya Aç…", "-", "Çıkış"],
        )
        self.assertIs(tray.menu.actions()[0], tray.update_action, "en üstte")
        self.assertTrue(tray.update_action.font().bold())
        self.assertIs(tray.menu.defaultAction(), tray.update_action, "stil sayfası kalınlığı :default durumundan verir")
        self.assertFalse(tray.update_action.icon().isNull())
        self.assertTrue(tray.update_separator.isSeparator())
        # Güncelleme sonradan geldi/gitti: menü açılırken (aboutToShow) satır güncel duruma gelir.
        tray.version_holder[0] = ""
        tray.menu.aboutToShow.emit()
        self.assertFalse(tray.update_action.isVisible())
        self.assertFalse(tray.update_separator.isVisible())
        tray.version_holder[0] = "3.0.1"
        tray.menu.aboutToShow.emit()
        self.assertEqual(tray.update_action.text(), "Güncelle · v3.0.1")
        self.assertTrue(tray.update_separator.isVisible())

    def test_update_row_is_correct_in_a_menu_that_is_built_later_or_rebuilt(self) -> None:
        version = ["1.2.3"]
        tray = self.started(version=version)
        old_menu = tray.menu
        tray.rebuild_menu()
        self.assertIsNot(tray.menu, old_menu)
        self.assertEqual(layout(tray.menu)[:2], ["Güncelle · v1.2.3", "-"])
        self.assertIs(tray.tray.contextMenu(), tray.menu, "tepsi yeni menüyü kullanır")
        version[0] = ""
        tray.rebuild_menu()
        self.assertEqual(layout(tray.menu)[0], "Gizle")
        version[0] = "1.2.4"
        tray.rebuild_menu()
        self.assertEqual(tray.update_action.text(), "Güncelle · v1.2.4")

    def test_update_row_text_follows_the_language(self) -> None:
        win = FakeWindow()
        tray = self.started(windows=[win], version="2.5.0")
        self.assertEqual(tray.update_action.text(), "Güncelle · v2.5.0")
        tray.set_language("en")
        self.assertEqual(tray.update_action.text(), "Update · v2.5.0")
        self.assertEqual(layout(tray.menu)[2:4], ["Hide", "Settings"])

    def test_update_row_opens_the_same_dialog_as_the_title_bar_button(self) -> None:
        opened: list[int] = []
        win = FakeWindow()
        tray = self.started(windows=[win], version="2.5.0", open_update=lambda: opened.append(1))
        tray.update_action.trigger()
        pump()
        self.assertEqual(opened, [1])
        self.assertIn(("raise",), win.calls, "pencere de öne gelir (güncelleme penceresi ona bağlansın)")

    def test_update_row_uses_the_real_update_service(self) -> None:
        from app.ui.tray import LuminaTray
        from app.ui.updates import UpdateService

        service = UpdateService()
        self.addCleanup(service.deleteLater)
        service._pending = {"version": "9.9.9", "url": "u", "notes": "n", "mandatory": False, "size": 1, "checksum": ""}
        service.open_pending = mock.Mock()
        old = getattr(self.app, "_lumina_updates", None)
        self.addCleanup(lambda: setattr(self.app, "_lumina_updates", old))
        self.app._lumina_updates = service
        tray = LuminaTray(self.app, QIcon(), windows=lambda: [FakeWindow()], ownership=TrayOwnership(self.lock_path),
                          available=lambda: True)
        self._trays.append(tray)
        self.assertTrue(tray.start())
        self.assertEqual(tray.update_action.text(), "Güncelle · v9.9.9")
        self.assertTrue(tray.update_action.isVisible())
        tray.update_action.trigger()
        pump()
        service.open_pending.assert_called_once_with()
        service._pending = None  # güncelleme yok
        tray.menu.aboutToShow.emit()
        self.assertFalse(tray.update_action.isVisible())

    def test_without_the_update_service_the_row_is_simply_hidden(self) -> None:
        from app.ui.tray import LuminaTray

        old = getattr(self.app, "_lumina_updates", None)
        self.addCleanup(lambda: setattr(self.app, "_lumina_updates", old))
        self.app._lumina_updates = None  # hizmet ilk kareden sonra kurulur
        tray = LuminaTray(self.app, QIcon(), windows=lambda: [FakeWindow()], ownership=TrayOwnership(self.lock_path),
                          available=lambda: True)
        self._trays.append(tray)
        self.assertTrue(tray.start())
        self.assertFalse(tray.update_action.isVisible())

    def test_theme_and_language_rows_show_the_checked_choice_and_drive_the_windows(self) -> None:
        a, b = FakeWindow(), FakeWindow()
        tray = self.started(windows=[a, b])
        checked = lambda sub: [plain(x.text()) for x in sub.actions() if x.isChecked()]  # noqa: E731
        self.assertEqual(checked(tray.theme_menu), ["Sistem"])
        self.assertTrue(tray.theme_menu.actions()[0].text().startswith("✓"))
        self.assertEqual(len(checked(tray.lang_menu)), 1)
        tray._theme_actions["dark"].trigger()
        self.assertEqual(a.calls[-1], ("theme", "dark"))
        self.assertEqual(b.calls[-1], ("theme", "dark"), "bu sürecin tüm pencereleri aynı temada olur")
        self.assertEqual(checked(tray.theme_menu), ["Koyu"])
        tray._lang_actions["en"].trigger()
        self.assertEqual(a.calls[-1], ("lang", "en"))
        self.assertEqual(len(checked(tray.lang_menu)), 1)
        self.assertTrue(checked(tray.lang_menu)[0].endswith("English"))
        self.assertEqual(tray.theme_menu.title(), "Theme", "tepsi de yeni dilde")
        self.assertEqual(checked(tray.theme_menu), ["Dark"])

    def test_checked_choice_follows_changes_made_in_the_window_itself(self) -> None:
        win = FakeWindow()
        tray = self.started(windows=[win])
        win.theme = "light"
        win.lang_choice, win.lang = "en", "en"  # pencere menüsünden/ayarlardan değişti
        tray.menu.aboutToShow.emit()
        self.assertEqual(layout(tray.menu), ["Hide", "Settings", "Theme", "Language", "-", "Open File…", "-", "Exit"])
        self.assertEqual([plain(x.text()) for x in tray.theme_menu.actions() if x.isChecked()], ["Light"])
        checked_langs = [plain(x.text()) for x in tray.lang_menu.actions() if x.isChecked()]
        self.assertEqual(len(checked_langs), 1)
        self.assertTrue(checked_langs[0].endswith("English"), checked_langs)

    def test_invalid_theme_is_ignored(self) -> None:
        win = FakeWindow()
        tray = self.started(windows=[win])
        tray.set_theme("neon")
        self.assertEqual(win.calls, [])


# ---------------------------------------------------------------------------------------------------------- eylemler
class ActionTest(_Base):
    def test_open_row_raises_the_window_then_runs_the_existing_file_picker(self) -> None:
        win = FakeWindow()
        tray = self.started(windows=[win])
        tray.open_action.trigger()
        pump()
        self.assertIn(("raise",), win.calls)
        self.assertEqual(win.calls.count(("select_file",)), 1)
        self.assertLess(win.calls.index(("raise",)), win.calls.index(("select_file",)))

    def test_menu_acts_only_on_its_own_oldest_window_when_none_is_active(self) -> None:
        first, second = FakeWindow(), FakeWindow()
        first.visible = False  # "Göster" satırı gizli pencereyi açar (görünürken "Gizle" olur)
        tray = self.started(windows=[first, second])
        tray.show_action.trigger()
        self.assertIn(("raise",), first.calls)
        self.assertEqual(second.calls, [])

    def test_it_follows_the_active_window_of_the_process(self) -> None:
        first, second = FakeWindow(), FakeWindow()
        tray = self.started(windows=[first, second])
        with mock.patch.object(self.app, "activeWindow", return_value=second):
            tray.show_window()
        self.assertIn(("raise",), second.calls)
        self.assertEqual(first.calls, [])

    def test_when_the_first_window_closes_the_menu_moves_on_to_the_next_one(self) -> None:
        first, second = FakeWindow(), FakeWindow()
        wins = [first, second]
        tray = self.started(windows=wins)
        wins.remove(first)
        tray.show_window()
        self.assertIn(("raise",), second.calls)

    def test_activation_restores_the_window_single_click_and_double_click(self) -> None:
        win = FakeWindow()
        tray = self.started(windows=[win])
        tray.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        self.assertEqual(win.calls.count(("raise",)), 1)
        tray.tray.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
        self.assertEqual(win.calls.count(("raise",)), 2)
        tray.tray.activated.emit(QSystemTrayIcon.ActivationReason.MiddleClick)
        tray.tray.activated.emit(QSystemTrayIcon.ActivationReason.Context)
        self.assertEqual(win.calls.count(("raise",)), 2, "orta tık/bağlam menüsü pencereyi öne getirmez")

    def test_minimized_window_is_restored_without_leaving_fullscreen(self) -> None:
        w = QWidget()
        self.addCleanup(w.deleteLater)
        w.resize(300, 200)
        w.showFullScreen()
        pump()
        w.showMinimized()
        pump()
        self.assertTrue(w.isMinimized())
        tray = self.started(windows=[w])
        self.assertTrue(tray.show_window())
        pump()
        self.assertFalse(w.isMinimized())
        self.assertTrue(w.isFullScreen(), "tam ekrandaki pencere tepsiden geri yüklenince tam ekran kalmalı")

    def test_a_normal_window_is_left_alone_apart_from_raising(self) -> None:
        win = FakeWindow()
        tray = self.started(windows=[win])
        tray.show_window()
        self.assertNotIn("state", [c[0] for c in win.calls], "küçültülmemiş pencerenin durumu değişmez")

    def test_show_window_without_windows_or_with_a_deleted_window_is_harmless(self) -> None:
        tray = self.started(windows=[])
        self.assertFalse(tray.show_window())
        tray.open_action.trigger()
        tray.update_action.trigger()
        pump()

        class Gone(FakeWindow):
            def isVisible(self):
                raise RuntimeError("wrapped C/C++ object has been deleted")

        tray2 = self.make_tray(windows=[Gone()], ownership=TrayOwnership(self.lock_path.with_name("x.lock")))
        self.assertTrue(tray2.start())
        self.assertFalse(tray2.show_window())

    def test_exit_closes_every_window_of_the_process_through_the_normal_close_path(self) -> None:
        a, b = FakeWindow(), FakeWindow()
        tray = self.started(windows=[a, b])
        with mock.patch.object(self.app, "quit") as quit_:
            tray.quit_action.trigger()
            pump()
        self.assertEqual((a.closed, b.closed), (1, 1))
        quit_.assert_not_called()  # çıkışı son pencerenin kapanışı (session._drop) yapar

    def test_exit_with_no_window_quits_the_app_directly(self) -> None:
        tray = self.started(windows=[])
        with mock.patch.object(self.app, "quit") as quit_:
            tray.quit_action.trigger()
            pump()
        quit_.assert_called_once_with()


# ------------------------------------------------------------------------------------------------- gerçek ana pencere
class RealWindowTest(_Base):
    def _window(self):
        from app.ui.main_window import MainWindow

        win = MainWindow()
        win.resize(900, 600)
        win.show()
        pump()
        return win

    def test_theme_and_language_rows_change_the_real_window(self) -> None:
        win = self._window()
        self.addCleanup(lambda: (win.close(), win.deleteLater(), pump()))
        tray = self.started(windows=[win])
        tray._theme_actions["light"].trigger()
        self.assertEqual(win.theme, "light")
        self.assertEqual(win.settings.value("theme"), "light")
        tray._theme_actions["dark"].trigger()
        self.assertEqual(win.theme, "dark")
        tray._lang_actions["en"].trigger()
        self.assertEqual((win.lang_choice, win.lang), ("en", "en"))
        self.assertEqual(layout(tray.menu), ["Hide", "Settings", "Theme", "Language", "-", "Open File…", "-", "Exit"])
        tray._lang_actions["tr"].trigger()
        self.assertEqual(win.lang, "tr")
        self.assertEqual(layout(tray.menu)[0], "Gizle")

    def test_open_row_calls_the_windows_real_file_picker(self) -> None:
        win = self._window()
        self.addCleanup(lambda: (win.close(), win.deleteLater(), pump()))
        tray = self.started(windows=[win])
        with mock.patch.object(type(win), "select_file", autospec=True) as picker:
            tray.open_action.trigger()
            pump()
        picker.assert_called_once_with(win)

    def test_exit_runs_the_real_close_path_geometry_temps_and_player(self) -> None:
        win = self._window()
        win.settings.remove("win_geo")
        tray = self.started(windows=[win])
        with mock.patch.object(win.temps, "clear", wraps=win.temps.clear) as clear_temps, \
             mock.patch.object(win.player, "shutdown", wraps=win.player.shutdown) as player_shutdown:
            tray.quit_action.trigger()
            pump()
        self.assertFalse(win.isVisible(), "pencere kapandı")
        self.assertIsNotNone(win.settings.value("win_geo"), "kapanışta pencere konumu kaydedilir")
        clear_temps.assert_called_once()
        player_shutdown.assert_called_once()
        win.deleteLater()
        pump()

    def test_settings_have_no_tray_toggle(self) -> None:
        from app.ui.settings_dialog import SettingsDialog

        win = self._window()
        self.addCleanup(lambda: (win.close(), win.deleteLater(), pump()))
        for lang in ("tr", "en"):
            win.lang = lang
            win.retranslate_ui()
            dlg = SettingsDialog(win)
            self.addCleanup(dlg.deleteLater)
            for tab in (0, 1, 2):
                dlg._switch_tab(tab)
                for widget in dlg.findChildren(QWidget):
                    texts = []
                    if isinstance(widget, (QAbstractButton, QLabel)):
                        texts.append(widget.text())
                    if isinstance(widget, QComboBox):
                        texts += [widget.itemText(i) for i in range(widget.count())]
                    texts.append(widget.toolTip())
                    for text in texts:
                        self.assertNotRegex(text.lower(), r"tepsi|tray", (lang, tab, widget.objectName(), text))
        win.lang = "tr"
        win.retranslate_ui()
        for key in win.settings.allKeys():
            self.assertNotIn("tray", key.lower(), "tepsi için kalıcı ayar anahtarı olmamalı")


# ------------------------------------------------------------------------------------------------ metinler ve kaynak
class TextsAndSourceTest(unittest.TestCase):
    def test_tray_texts_exist_in_both_languages_and_nothing_else_mentions_the_tray(self) -> None:
        from app.resources.locales import LOCALES

        wanted = {"tray_update_now", "tray_show", "tray_hide", "tray_quit"}
        for lang, table in LOCALES.items():
            self.assertEqual({k for k in table if "tray" in k}, wanted, lang)
            for value in table.values():
                self.assertNotRegex(value.lower(), r"tepsi|tray", f"{lang}: ayarlarda/arayüzde tepsi anlatan metin olmamalı")
        self.assertEqual(LOCALES["tr"]["tray_update_now"].format(version="1.0"), "Güncelle · v1.0")
        self.assertEqual(LOCALES["en"]["tray_update_now"].format(version="1.0"), "Update · v1.0")
        self.assertEqual((LOCALES["tr"]["tray_show"], LOCALES["tr"]["tray_quit"]), ("Göster", "Çıkış"))
        self.assertEqual((LOCALES["en"]["tray_show"], LOCALES["en"]["tray_quit"]), ("Show", "Exit"))
        self.assertEqual(set(LOCALES["tr"]), set(LOCALES["en"]), "tr/en anahtarları aynı olmalı")

    def test_settings_code_never_reads_or_writes_a_tray_option(self) -> None:
        for name in ("app/ui/settings_dialog.py", "app/core/settings_store.py"):
            text = (ROOT / name).read_text(encoding="utf-8").lower()
            self.assertNotIn("tray", text, name)
            self.assertNotIn("tepsi", text, name)

    def test_both_themes_style_the_default_menu_row_bold(self) -> None:
        """Menü stil sayfası eylemin kendi yazı tipini ezer: "Güncelle · vX" kalınlığı `QMenu::item:default`'tan gelir."""
        from app.resources.styles import STYLES

        for theme in ("dark", "light"):
            self.assertRegex(STYLES[theme], r"QMenu::item:default\s*\{\s*font-weight:\s*700", theme)

    def test_exit_menu_icon_is_registered(self) -> None:
        from app.ui.icons import MENU_ICON_NAMES

        self.assertIn("exit", MENU_ICON_NAMES)

    def test_startup_does_not_load_the_tray_modules(self) -> None:
        """Tepsi ilk kareden sonra kurulur: açılış yolu (app.main + pencere) tepsi modüllerini yüklememeli."""
        code = (
            "import os, sys, json\n"
            "os.environ['QT_QPA_PLATFORM'] = 'offscreen'\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "import app.main\n"
            "from PyQt6.QtCore import QSettings\n"
            "from PyQt6.QtWidgets import QApplication\n"
            "QSettings.setDefaultFormat(QSettings.Format.IniFormat)\n"
            "QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, os.environ['LUMINA_SANDBOX'])\n"
            "app = QApplication([])\n"
            "from app.ui.main_window import MainWindow\n"
            "w = MainWindow()\n"
            "print(json.dumps({m: m in sys.modules for m in ('app.ui.tray', 'app.core.tray_owner')}))\n"
        )
        import json

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            env = dict(os.environ, APPDATA=tmp, LOCALAPPDATA=tmp, LUMINA_SANDBOX=tmp)
            out = subprocess.run([sys.executable, "-B", "-c", code], cwd=str(ROOT), env=env,
                                 capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        loaded = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(loaded, {k: False for k in loaded}, f"açılışta yüklenmemeli: {loaded}")


if __name__ == "__main__":
    unittest.main()
