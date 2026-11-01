"""Beş uygulama arası tutarlılık işi (Lumina): nötr gri tema + 10 vurgu rengi, özel kutular/pencereler, tepsi iskeleti,
Çıkış yolu, iki güncelleme kutusu, duyuru şeridi, saklama konumları ve metin kuralları.

Testler kullanıcının gerçek ayarlarına dokunmaz (ayarlar geçici INI klasöründe; veri klasörleri geçici dizine yönlendirilir).
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QCoreApplication, QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QIcon  # noqa: E402
from PyQt6.QtWidgets import QApplication, QCheckBox, QMenu  # noqa: E402

from app.core.remote_notice import Notice  # noqa: E402
from app.resources import theme as theme_mod  # noqa: E402
from app.resources.locales import LOCALES, t  # noqa: E402
from app.ui.message_box import MessageBox  # noqa: E402

# MediaCompressor `ACCENTS` ile birebir aynı olması gereken aile (anahtar sırası dahil)
MC_ACCENTS = {
    "cyan": ("#00ACC1", "#00838F", "#4DD0E1", "#80DEEA"),
    "blue": ("#2196F3", "#1976D2", "#42A5F5", "#90CAF9"),
    "teal": ("#00897B", "#00695C", "#4DB6AC", "#80CBC4"),
    "indigo": ("#3F51B5", "#303F9F", "#7986CB", "#9FA8DA"),
    "violet": ("#7C4DFF", "#651FFF", "#B388FF", "#D1C4E9"),
    "rose": ("#E91E63", "#C2185B", "#F06292", "#F8BBD0"),
    "orange": ("#FB8C00", "#EF6C00", "#FFB74D", "#FFE0B2"),
    "red": ("#E53935", "#C62828", "#EF5350", "#FFCDD2"),
    "green": ("#43A047", "#2E7D32", "#66BB6A", "#C8E6C9"),
    "yellow": ("#F9A825", "#F57F17", "#FDD835", "#FFF59D"),
}
# Lacivert/slate ve eski gök mavisi: arayüzde (stil sayfaları, simgeler, diyaloglar) hiç kalmamalı
FORBIDDEN_HEX = (
    "#090b10", "#1b2030", "#141824", "#0b0f19", "#0e1322", "#0f1526", "#141b2d", "#1e293b", "#334155", "#94a3b8",
    "#cbd5e1", "#64748b", "#10131a", "#07080c", "#12161f", "#1e1b4b", "#e2e8f0", "#f1f5f9", "#f8fafc", "#0f172a",
    "#475569", "#e8eef6", "#7dd3fc", "#38bdf8", "#0284c7", "#0ea5e9", "#0369a1", "#d8dee8", "#eef1f6", "#818cf8",
)


def pump(ms: int = 30) -> None:
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.003)
    QCoreApplication.processEvents()


class _SettingsCase(unittest.TestCase):
    """Geçici INI ayarları + geçici veri klasörleri."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._ini = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._ini.name)
        cls.app = QApplication.instance() or QApplication([])
        cls._env = mock.patch.dict(
            os.environ, {"APPDATA": cls._ini.name, "LOCALAPPDATA": cls._ini.name, "USERPROFILE": cls._ini.name}
        )
        cls._env.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._env.stop()
        cls._ini.cleanup()


# ------------------------------------------------------------------------------------------------ renk sistemi
class ThemeTest(unittest.TestCase):
    def test_accent_family_is_the_same_as_mediacompressor(self) -> None:
        self.assertEqual(list(theme_mod.ACCENTS), list(MC_ACCENTS), "anahtarlar ve sıra")
        for key, (accent, accent2, hi_dark, hi_light) in MC_ACCENTS.items():
            spec = theme_mod.ACCENTS[key]
            self.assertEqual((spec["accent"], spec["accent2"], spec["hi_dark"], spec["hi_light"]), (accent, accent2, hi_dark, hi_light), key)
            self.assertEqual(spec["label_key"], f"accent_{key}")
            for lang in ("tr", "en"):
                self.assertNotEqual(t(spec["label_key"], lang), spec["label_key"], (lang, key))

    def test_default_accent_is_blue_the_closest_to_the_old_sky_blue(self) -> None:
        self.assertEqual(theme_mod.DEFAULT_ACCENT, "blue")
        self.assertEqual(theme_mod.normalize_accent("nonsense"), "blue")
        self.assertEqual(theme_mod.normalize_accent(None), "blue")

    def test_surfaces_are_neutral_gray_and_match_the_mediacompressor_palette(self) -> None:
        self.assertEqual(
            {k: theme_mod.DARK[k] for k in ("base", "raised", "sunken", "surface", "text", "text_muted")},
            {"base": "#1a1a1a", "raised": "#2a2a2a", "sunken": "#1f1f1f", "surface": "#252525", "text": "#e8e8e8", "text_muted": "#9e9e9e"},
        )
        self.assertEqual(
            {k: theme_mod.LIGHT[k] for k in ("base", "raised", "sunken", "surface", "border", "text", "text_muted")},
            {"base": "#e0e0e0", "raised": "#f5f5f5", "sunken": "#d8d8d8", "surface": "#ececec", "border": "#c5c5c5", "text": "#1a1a1a", "text_muted": "#616161"},
        )
        for table in (theme_mod.DARK, theme_mod.LIGHT):
            for key in ("base", "raised", "sunken", "surface", "border", "hi", "hi2", "lo", "text", "text_muted", "ink", "ink2", "stage", "panel"):
                r, g, b = (int(table[key][i:i + 2], 16) for i in (1, 3, 5))
                self.assertTrue(r == g == b, f"{key} {table[key]} nötr gri değil")

    def test_no_navy_slate_or_sky_blue_in_any_generated_stylesheet(self) -> None:
        for theme in ("dark", "light"):
            for accent in theme_mod.ACCENTS:
                css = (theme_mod.build_stylesheet(theme, accent) + theme_mod.dialog_stylesheet(theme, accent)).lower()
                for bad in FORBIDDEN_HEX:
                    self.assertNotIn(bad, css, (theme, accent, bad))

    def test_no_navy_slate_or_sky_blue_literal_is_left_in_the_source(self) -> None:
        skip = {"theme.py"}  # (yasaklı değerler bu dosyada yalnızca yorum/alan adı olarak bile geçmez; yine de kendisi hariç)
        for path in sorted((ROOT / "app").rglob("*.py")):
            if path.name in skip:
                continue
            text = path.read_text(encoding="utf-8").lower()
            for bad in FORBIDDEN_HEX:
                self.assertNotIn(bad, text, f"{path.relative_to(ROOT)}: {bad}")

    def test_text_pairs_keep_4_5_to_1_contrast_in_every_theme_and_accent(self) -> None:
        for theme in ("dark", "light"):
            for accent in theme_mod.ACCENTS:
                p = theme_mod.palette(theme, accent)
                worst = p["raised"] if p["is_dark"] else p["sunken"]
                pairs = {
                    "metin/zemin": (p["text"], p["base"]),
                    "metin/kart": (p["text"], p["raised"]),
                    "soluk metin/zemin": (p["text_muted"], p["base"]),
                    "soluk metin/yüzey": (p["text_muted"], p["surface"] if not p["is_dark"] else p["raised"]),
                    "vurgu metni/zor yüzey": (p["accent_text"], worst),
                    "vurgu metni/zemin": (p["accent_text"], p["base"]),
                    "dolgu metni/dolgu": (p["on_fill"], p["fill"]),
                    "dolgu metni/üzerine gelme": (p["on_fill"], p["fill_hover"]),
                }
                for name, (fg, bg) in pairs.items():
                    self.assertGreaterEqual(theme_mod.contrast(fg, bg), 4.5, (theme, accent, name, fg, bg))
                for kind, color in theme_mod.message_colors(theme).items():
                    bg = p["raised"] if p["is_dark"] else p["sunken"]
                    self.assertGreaterEqual(theme_mod.contrast(color, bg), 4.5, (theme, kind))

    def test_active_state_feeds_icons_and_dialogs(self) -> None:
        theme_mod.reset_active()
        try:
            theme_mod.set_active("light", "rose")
            self.assertEqual(theme_mod.active_accent(), "rose")
            self.assertEqual(theme_mod.active_palette()["accent"], theme_mod.ACCENTS["rose"]["accent"])
            self.assertFalse(theme_mod.active_palette()["is_dark"])
        finally:
            theme_mod.reset_active()

    def test_system_theme_read_failure_defaults_to_dark(self) -> None:
        from app.resources import styles

        with mock.patch("PyQt6.QtGui.QGuiApplication.styleHints", side_effect=RuntimeError("yok")), \
                mock.patch.object(styles.sys, "platform", "win32"), \
                mock.patch.dict(sys.modules, {"winreg": mock.Mock(OpenKey=mock.Mock(side_effect=ValueError("bozuk")),
                                                                  HKEY_CURRENT_USER=1)}):
            self.assertTrue(styles.system_is_dark())
            self.assertEqual(styles.resolve_theme("system"), "dark")


# ------------------------------------------------------------------------------------------------ ana pencere
class WindowTest(_SettingsCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.resize(1000, 640)
        cls.win.show()
        pump(60)

    @classmethod
    def tearDownClass(cls) -> None:
        MessageBox.set_test_hook(None)
        cls.win.close()
        cls.win.deleteLater()
        QCoreApplication.sendPostedEvents(None, 0)
        super().tearDownClass()

    def setUp(self) -> None:
        self.addCleanup(MessageBox.set_test_hook, None)

    def _settings_menu(self) -> QMenu:
        captured: list[QMenu] = []

        def fake_exec(menu, *_a, **_k):
            captured.append(menu)

        with mock.patch.object(QMenu, "exec", new=fake_exec):
            self.win.show_settings_menu()
        return captured[0]

    @staticmethod
    def _rows(menu: QMenu) -> list[str]:
        return ["-" if a.isSeparator() else a.text() for a in menu.actions()]

    def test_default_theme_order_is_system_dark_light_everywhere(self) -> None:
        menu = self._settings_menu()
        theme_menu = next(a.menu() for a in menu.actions() if a.menu() is not None and a.text() == t("theme", self.win.lang))
        self.assertEqual([a.data() for a in theme_menu.actions()], ["system", "dark", "light"])
        from app.ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self.win)
        self.addCleanup(dlg.deleteLater)
        self.assertEqual([dlg.combo_theme.itemData(i) for i in range(3)], ["system", "dark", "light"])

    def test_settings_menu_has_color_submenu_and_exit_with_ctrl_q(self) -> None:
        menu = self._settings_menu()
        rows = self._rows(menu)
        lang = self.win.lang
        accent_act = next(a for a in menu.actions() if a.text() == t("accent", lang))
        self.assertEqual([a.data() for a in accent_act.menu().actions()], list(theme_mod.ACCENTS))
        checked = [a.data() for a in accent_act.menu().actions() if a.isChecked()]
        self.assertEqual(checked, [self.win.accent])
        self.assertEqual(rows[-1], t("quit", lang))
        quit_act = menu.actions()[-1]
        self.assertEqual(quit_act.shortcut().toString(), "Ctrl+Q")
        # sıra: Tema, Renk, Dil
        order = [r for r in rows if r in (t("theme", lang), t("accent", lang), t("language", lang))]
        self.assertEqual(order, [t("theme", lang), t("accent", lang), t("language", lang)])

    def test_set_accent_persists_restyles_and_changes_icons(self) -> None:
        from app.ui.icons import menu_icon

        try:
            before = menu_icon("settings").pixmap(32, 32).toImage().pixelColor(16, 16).name()
            self.win.set_accent("green")
            self.assertEqual(self.win.accent, "green")
            self.assertEqual(self.win.settings.value("accent"), "green")
            css = self.app.styleSheet()
            self.assertIn(theme_mod.palette(self.win._resolved_theme(), "green")["fill"], css)
            self.assertNotIn(theme_mod.ACCENTS["blue"]["accent2"], css)
            self.assertEqual(theme_mod.active_accent(), "green")
            doc = menu_icon("globe").pixmap(32, 32).toImage()
            colors = {doc.pixelColor(x, y).name() for x in range(32) for y in range(32) if doc.pixelColor(x, y).alpha() > 200}
            self.assertIn(theme_mod.ACCENTS["green"]["accent"].lower(), colors, "menü simgeleri vurguya boyanır")
            self.assertNotEqual(before, "")
        finally:
            self.win.set_accent("blue")

    def test_unknown_accent_in_settings_falls_back_to_blue(self) -> None:
        self.win.set_accent("nonsense")
        self.assertEqual(self.win.accent, "blue")

    def test_color_dots_in_settings_follow_the_window(self) -> None:
        from app.ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self.win)
        self.addCleanup(dlg.deleteLater)
        self.assertEqual(list(dlg.accent_buttons), list(theme_mod.ACCENTS))
        self.assertTrue(dlg.accent_buttons["blue"].isChecked())
        dlg.accent_buttons["violet"].click()
        try:
            self.assertEqual(self.win.accent, "violet")
            self.assertEqual(dlg.lbl_accent_name.text(), t("accent_violet", self.win.lang))
        finally:
            self.win.set_accent("blue")

    def test_ctrl_q_and_exit_row_quit_every_window_of_the_process(self) -> None:
        closed: list[object] = []
        with mock.patch.object(self.win, "close", side_effect=lambda: closed.append(self.win) or True):
            self.win.quit_application()
        self.assertIn(self.win, closed)
        # kısayol gerçekten quit_application'a bağlı (kapat değil)
        src = (ROOT / "app/ui/main_window.py").read_text(encoding="utf-8")
        self.assertRegex(src, r'bind\(QKeySequence\("Ctrl\+Q"\), self\.quit_application\)')
        self.assertRegex(src, r'bind\(QKeySequence\("Alt\+F4"\), self\.close\)')

    def test_errors_use_the_custom_message_box_not_a_native_one(self) -> None:
        seen: list[tuple] = []

        def hook(box):
            seen.append((box.windowTitle(), box.text(), box.icon()))
            return MessageBox.StandardButton.Ok

        MessageBox.set_test_hook(hook)
        self.win._on_image_failed("x.jpg", "Dosya açılamadı (bulunamadı veya erişilemiyor).", self.win._load_token)
        self.win._on_playback_failed("Video açılamadı.")
        self.assertEqual(len(seen), 2, seen)
        self.assertEqual(seen[0][0], t("error", self.win.lang))
        self.assertIn(t("load_fail", self.win.lang).split("{}")[0].strip(), seen[0][1])
        self.assertEqual(seen[0][2], MessageBox.Icon.Warning)

    def test_shell_result_is_a_toast_and_failures_use_the_box(self) -> None:
        shown: list[str] = []
        with mock.patch.object(self.win, "_flash", side_effect=lambda text, *a, **k: shown.append(text)), \
                mock.patch.object(MessageBox, "warning") as warn:
            self.win._notice(t("shell_ok", self.win.lang))
            self.win._notice("hata", warn=True)
        self.assertEqual(shown, [t("shell_ok", self.win.lang)])
        warn.assert_called_once()

    def test_windows_only_integration_is_gated(self) -> None:
        from app.ui import main_window, settings_dialog

        self.assertEqual(main_window.SYSTEM_INTEGRATION, sys.platform == "win32")
        self.assertEqual(settings_dialog.SYSTEM_INTEGRATION, sys.platform == "win32")
        with mock.patch.object(settings_dialog, "SYSTEM_INTEGRATION", False):
            dlg = settings_dialog.SettingsDialog(self.win)
            self.addCleanup(dlg.deleteLater)
            self.assertIsNone(dlg.card_sys)
            self.assertFalse(hasattr(dlg, "btn_shell"))
            dlg._retranslate_ui()
            dlg._apply_style()

    def test_sys_integration_title_is_not_windows(self) -> None:
        for lang, wanted in (("tr", "Sistem Entegrasyonu"), ("en", "System Integration")):
            self.assertEqual(t("sys_integration", lang), wanted)
        from app.ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self.win)
        self.addCleanup(dlg.deleteLater)
        if dlg.card_sys is not None:
            self.assertEqual(dlg.card_sys.lbl_title.text(), t("sys_integration", self.win.lang))
            self.assertNotIn("Windows", dlg.card_sys.lbl_title.text())

    # ---- duyuru şeridi
    def test_notice_bar_row_pushes_the_capsules_down_and_is_hidden_in_fullscreen(self) -> None:
        win = self.win
        self.assertFalse(win.notice_row.isVisible())
        top = win.brand_bar.y()
        win._on_notice(Notice("n1", "Bakım var", "https://macik.com.tr/x"))
        pump(60)
        win.stage.layout_chrome()
        self.assertTrue(win.notice_row.isVisible())
        self.assertGreater(win.brand_bar.y(), top, "kapsüller duyuru satırının altına iner")
        self.assertGreaterEqual(win.brand_bar.y(), win.notice_row.geometry().bottom() - 1)
        self.assertEqual(win.settings.value("dismissed_notice", ""), "", "gösterilmek duyuruyu kapatmaz")
        win.notice_bar.dismiss()
        pump(60)
        win.stage.layout_chrome()
        self.assertFalse(win.notice_row.isVisible())
        self.assertEqual(win.brand_bar.y(), top)
        self.assertEqual(win.settings.value("dismissed_notice"), "n1")
        win._on_notice(Notice("n1", "Bakım var", ""))
        self.assertIsNone(win.notice_bar.current(), "kapatılan duyuru dönmez")
        win.settings.remove("dismissed_notice")

    # ---- boş durum
    def test_empty_state_is_a_title_a_one_sentence_guide_and_open_buttons(self) -> None:
        for lang in ("tr", "en"):
            title, sub = t("empty_title", lang), t("empty_sub", lang)
            self.assertTrue(title and sub)
            self.assertEqual(sub.count(". "), 0, "tek cümle")
            self.assertTrue(sub.endswith("."))
        self.assertTrue(self.win.empty.btn_file.text() and self.win.empty.btn_folder.text())
        self.assertEqual(self.win.empty.btn_file.text(), t("open_file", self.win.lang))
        self.assertTrue(self.win.empty.icon.pixmap() is not None and not self.win.empty.icon.pixmap().isNull(), "vektör süs")

    def test_volume_popup_uses_vector_icons_not_emoji(self) -> None:
        popup = self.win.volume_popup
        for muted in (False, True):
            popup.set_muted(muted)
            self.assertFalse(popup.icon_lbl.pixmap().isNull())
            self.assertEqual(popup.icon_lbl.text(), "")
        popup.set_muted(False)


# ------------------------------------------------------------------------------------------------ pencereler/kutular
class ChromeDialogsTest(_SettingsCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.show()
        pump(40)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.close()
        cls.win.deleteLater()
        QCoreApplication.sendPostedEvents(None, 0)
        super().tearDownClass()

    def test_settings_and_update_windows_are_frameless_custom_title_dialogs(self) -> None:
        from app.ui.chrome_dialog import ChromeDialog
        from app.ui.settings_dialog import SettingsDialog
        from app.ui.update_dialog import UpdateDialog

        for dlg in (SettingsDialog(self.win), UpdateDialog("2.0.0", "notlar", False, self.win, lang="tr")):
            self.addCleanup(dlg.deleteLater)
            self.assertIsInstance(dlg, ChromeDialog)
            self.assertTrue(dlg.windowFlags() & Qt.WindowType.FramelessWindowHint, type(dlg).__name__)
            self.assertTrue(dlg.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
            self.assertEqual(dlg._cd_bar.height(), 38, "38 px başlık çubuğu")

    def test_no_native_message_or_input_dialog_is_left_in_the_app(self) -> None:
        pattern = re.compile(r"\bQMessageBox\b|\bQInputDialog\b|QErrorMessage|QProgressDialog")
        for path in sorted((ROOT / "app").rglob("*.py")):
            if path.name == "message_box.py":  # ortak modül yalnızca QMessageBox'ın enum'larını takma ad olarak kullanır
                continue
            self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), str(path.relative_to(ROOT)))

    def test_message_box_follows_theme_and_accent(self) -> None:
        theme_mod.set_active("light", "orange")
        try:
            box = MessageBox(self.win)
            box.setText("x")
            box.prepare()
            self.addCleanup(box.deleteLater)
            from app.ui import message_box as mb

            self.assertFalse(mb.current_palette()["is_dark"])
            self.assertEqual(mb.current_palette()["accent"].lower(), theme_mod.ACCENTS["orange"]["accent"].lower())
            self.assertFalse(box._pal["is_dark"])
        finally:
            theme_mod.set_active("dark", "blue")

    def test_message_box_buttons_and_language_are_the_apps(self) -> None:
        from app.ui.message_box import button_text

        self.assertEqual(button_text(MessageBox.StandardButton.Cancel, "tr"), "İptal")
        self.assertEqual(button_text(MessageBox.StandardButton.Yes, "en"), "Yes")


# ------------------------------------------------------------------------------------------------ güncelleme kutuları
class UpdateSettingsTest(_SettingsCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.close()
        cls.win.deleteLater()
        QCoreApplication.sendPostedEvents(None, 0)
        super().tearDownClass()

    def _dialog(self):
        from app.ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self.win)
        self.addCleanup(dlg.deleteLater)
        return dlg

    def test_two_update_boxes_with_the_shared_texts_and_hint(self) -> None:
        self.win.settings.remove("auto_update_check")
        self.win.settings.remove("auto_update_install")
        for lang, check, install, hint in (
            ("tr", "Güncellemeleri Otomatik Kontrol Et", "Güncellemeleri Otomatik Kur",
             "Yeni sürüm bulunduğunda arka planda indirilir ve uygulama kullanılmıyorken kurulur."),
            ("en", "Check for Updates Automatically", "Install Updates Automatically",
             "New versions are downloaded in the background and installed while the app is not in use."),
        ):
            self.win.lang = lang
            dlg = self._dialog()
            self.assertEqual((dlg.chk_auto_check.text(), dlg.chk_auto_install.text()), (check, install))
            self.assertEqual(dlg.lbl_update_hint.text(), hint)
            self.assertEqual(dlg.chk_auto_check.toolTip(), hint)
            boxes = [b for b in dlg.card_update.findChildren(QCheckBox)]
            self.assertEqual(len(boxes), 2)
        self.win.lang = "tr"

    def test_auto_check_is_visible_persists_and_gates_the_install_box(self) -> None:
        from app.core.settings_store import make_settings

        self.win.settings.remove("auto_update_check")
        dlg = self._dialog()
        self.assertTrue(dlg.chk_auto_check.isChecked(), "varsayılan: açık (updater.py'nin okuduğu gizli anahtar)")
        self.assertTrue(dlg.chk_auto_install.isEnabled())
        dlg.chk_auto_check.setChecked(False)
        self.assertEqual(str(make_settings().value("auto_update_check")).lower(), "false")
        self.assertFalse(dlg.chk_auto_install.isEnabled(), "kontrol kapalıyken kur pasif")
        from app.ui.updates import UpdateService

        self.assertFalse(UpdateService.auto_install_enabled(), "kontrol kapalı: sessiz kurulum da kapalı")
        dlg.chk_auto_check.setChecked(True)
        self.assertTrue(dlg.chk_auto_install.isEnabled())
        self.assertTrue(UpdateService.auto_install_enabled())
        self.assertNotIn("startup", " ".join(self.win.settings.allKeys()).lower(), "Başlat ayarı eklenmedi")
        self.assertFalse(any("notif" in k.lower() for k in self.win.settings.allKeys()), "bildirim ayarı eklenmedi")


# ------------------------------------------------------------------------------------------------ tepsi
class TrayConsistencyTest(_SettingsCase):
    def setUp(self) -> None:
        from app.core.tray_owner import TrayOwnership

        self._lock_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._lock_dir.cleanup)
        self.lock = TrayOwnership(os.path.join(self._lock_dir.name, "t.lock"))

    def _tray(self, win):
        from app.ui.tray import LuminaTray

        tray = LuminaTray(self.app, QIcon(), windows=lambda: [win], ownership=self.lock, available=lambda: False)
        tray._build_menu()
        self.addCleanup(tray.shutdown)
        return tray

    class _Win:
        def __init__(self) -> None:
            self.lang, self.theme, self.lang_choice = "tr", "dark", "tr"
            self.visible, self.calls = True, []

        def isVisible(self):
            return self.visible

        def isMinimized(self):
            return False

        def hide(self):
            self.visible = False

        def show(self):
            self.visible = True

        def windowState(self):
            return Qt.WindowState.WindowNoState

        def raise_(self):
            self.calls.append("raise")

        def activateWindow(self):
            self.calls.append("activate")

        def open_settings_dialog(self, *_):
            self.calls.append("settings")

    def test_skeleton_order(self) -> None:
        tray = self._tray(self._Win())
        rows = ["-" if a.isSeparator() else a.text() for a in tray.menu.actions() if a.isVisible()]
        self.assertEqual(rows, ["Gizle", "Ayarlar", "Tema", "Dil", "-", "Dosya Aç…", "-", "Çıkış"])
        self.assertEqual([a.text().replace("✓", "").strip() for a in tray.theme_menu.actions()], ["Sistem", "Koyu", "Açık"])
        self.assertNotIn("Pencereyi", " ".join(rows))

    def test_show_hide_label_follows_the_window(self) -> None:
        win = self._Win()
        tray = self._tray(win)
        self.assertEqual(tray.show_action.text(), "Gizle")
        tray.show_action.trigger()
        self.assertFalse(win.visible)
        tray.menu.aboutToShow.emit()
        self.assertEqual(tray.show_action.text(), "Göster")
        tray.show_action.trigger()
        self.assertTrue(win.visible)
        self.assertIn("raise", win.calls)
        win.lang = "en"
        tray.refresh()
        self.assertEqual(tray.show_action.text(), "Hide")
        win.visible = False
        tray.refresh()
        self.assertEqual(tray.show_action.text(), "Show")

    def test_settings_row_opens_the_settings_window(self) -> None:
        win = self._Win()
        tray = self._tray(win)
        tray.settings_action.trigger()
        pump(30)
        self.assertEqual(win.calls.count("settings"), 1)
        self.assertIn("raise", win.calls)

    def test_single_click_shows_the_window(self) -> None:
        from PyQt6.QtWidgets import QSystemTrayIcon

        win = self._Win()
        win.visible = False
        tray = self._tray(win)
        tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
        self.assertTrue(win.visible)


# ------------------------------------------------------------------------------------------------ metinler
class TextRulesTest(_SettingsCase):
    CONTROL_KEYS = (
        "open_file", "open_folder", "new_window", "rot_left", "rot_right", "fullscreen", "exit_fullscreen", "slideshow", "copy_file",
        "copy_image", "undo", "rename", "save_as", "open_with", "reveal", "copy_path", "copy_name", "sort", "autoplay",
        "default_apps", "check_updates", "check_btn", "auto_update_check", "auto_update_install", "update_later", "update_now",
        "update_retry", "update_install_anyway", "update_notes", "update_open_page", "strip_show", "strip_hide", "move_to", "copy_to",
        "tab_general", "tab_live_photo", "tab_about", "sys_integration", "update_group", "quit", "tray_show", "tray_hide",
        "shell_on", "shell_off", "save_frame", "rotate_save", "crop_save", "subtitle_open", "delete_perm_title", "rename_title",
    )
    LOWER = {"ve", "ile", "de", "da", "ya", "ki", "mi", "mı", "and", "or", "to", "of", "for", "in", "as", "the", "a", "on", "by", "from"}

    @staticmethod
    def _words(text: str) -> list[str]:
        text = re.sub(r"\(.*?\)|…|·|\d+", " ", text)
        return [w for w in re.split(r"[\s/&]+", text) if w and w[0].isalpha()]

    def test_control_labels_are_title_case_in_both_languages(self) -> None:
        for lang in ("tr", "en"):
            for key in self.CONTROL_KEYS:
                for word in self._words(LOCALES[lang][key]):
                    if word.lower() in self.LOWER or word.upper() == word:
                        continue
                    self.assertTrue(word[0].isupper(), f"{lang}.{key}: '{LOCALES[lang][key]}' ('{word}' büyük harfle başlamalı)")

    def test_ellipsis_is_one_character_and_labels_have_no_trailing_colon(self) -> None:
        for lang, table in LOCALES.items():
            for key, value in table.items():
                self.assertNotIn("...", value, f"{lang}.{key}")
            for key in ("merge_target_folder", "update_notes", "merge_error_title", "accent", "theme", "language"):
                self.assertFalse(table[key].rstrip().endswith(":"), f"{lang}.{key}")

    def test_the_word_windows_is_not_in_user_facing_labels(self) -> None:
        for lang, table in LOCALES.items():
            for key, value in table.items():
                if key == "update_err_not_pe":  # PE doğrulama hatası: gerçekten Windows'a özgü
                    continue
                self.assertNotIn("Windows", value, f"{lang}.{key}")
        src = (ROOT / "app/ui/settings_dialog.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r'"Windows (Entegrasyonu|Integration)"', "gömülü TR/EN ikilisi kalmadı")
        self.assertNotRegex(src, r'if lang == "tr" else', "koda gömülü TR/EN ikilisi yok")

    def test_one_current_format_and_second_person_plural(self) -> None:
        self.assertEqual(t("update_status_current", "tr").format("1.0.0"), "Güncel · v1.0.0")
        self.assertEqual(t("update_status_current", "en").format("1.0.0"), "Up to date · v1.0.0")
        for key, value in LOCALES["tr"].items():
            self.assertNotRegex(value, r"\b(kaldığın|bırak|seç|dene|aç|bağlantını|kullanıyorsun)\b", f"tr.{key}: 'sen' hitabı")
        self.assertEqual(t("resume_from", "tr").format("1:23"), "Kaldığınız yerden devam: 1:23")

    def test_tr_en_keys_match_and_new_keys_exist(self) -> None:
        self.assertEqual(set(LOCALES["tr"]), set(LOCALES["en"]))
        for key in ("accent", "accent_desc", "quit", "tray_hide", "auto_update_check", "sys_integration", "about_features"):
            for lang in ("tr", "en"):
                self.assertTrue(LOCALES[lang][key])
        self.assertEqual((t("tray_show", "tr"), t("tray_hide", "tr"), t("tray_quit", "tr")), ("Göster", "Gizle", "Çıkış"))

    def test_t_without_language_uses_the_users_choice_from_the_app_settings(self) -> None:
        from app.core.settings_store import make_settings

        settings = make_settings()
        old = settings.value("lang", "auto")
        try:
            settings.setValue("lang", "en")
            self.assertEqual(t("open_file"), "Open File")
            settings.setValue("lang", "tr")
            self.assertEqual(t("open_file"), "Dosya Aç")
        finally:
            settings.setValue("lang", old)


# ------------------------------------------------------------------------------------------------ saklama
class StorageTest(unittest.TestCase):
    def test_crash_log_has_the_same_name_and_place_as_the_other_apps(self) -> None:
        from app.core import crashlog

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp}), mock.patch.object(sys, "platform", "win32"):
            path = crashlog.crash_log_path()
        self.assertEqual(path.name, "crash.log")
        self.assertEqual(path.parent.name, "logs")
        self.assertEqual(path.parent.parent.name, "Lumina")


if __name__ == "__main__":
    unittest.main()
