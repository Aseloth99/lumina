"""Registry: yedekleme/geri yükleme, `.ts` korunması, açılışta gereksiz yazmama.

Gerçek ilişkilendirmelere DOKUNMAZ: tüm yollar sahte bir alt ağaca (HKCU\\Software\\LuminaRegTest_*)
yönlendirilir ve Explorer bildirimi (SHChangeNotify) taklit edilir. Test sonunda sahte ağaç silinir;
ayrıca gerçek `Lumina.file` anahtarının test boyunca değişmediği doğrulanır.
"""

from __future__ import annotations

import os
import pathlib
import sys
import unittest
import uuid
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

if sys.platform == "win32":
    import winreg

    from app.core import registry as reg


def _delete_tree(path: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    sub = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(rf"{path}\{sub}")
    except OSError:
        return
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def _set_default(path: str, value: str) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, value)


def _get_default(path: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, None)[0]
    except OSError:
        return None


def _real_snapshot():
    """Gerçek (test dışı) Lumina kayıtlarının özeti — test boyunca değişmemeli."""
    return (
        _get_default(r"Software\Classes\Lumina.file"),
        _get_default(r"Software\Classes\*\shell\LuminaViewer\command"),
        _get_default(r"Software\Classes\.jpg"),
        _get_default(r"Software\Classes\.ts"),
    )


@unittest.skipUnless(sys.platform == "win32", "yalnızca Windows")
class RegistryTest(unittest.TestCase):
    EXE = r"C:\Fake Lumina\Lumina.exe"

    def setUp(self) -> None:
        self.real_before = _real_snapshot()
        self.root = rf"Software\LuminaRegTest_{uuid.uuid4().hex}"
        self.sw = rf"{self.root}\Software"
        self.notify = mock.Mock()
        patches = [
            mock.patch.object(reg, "_SW", self.sw),
            mock.patch.object(reg, "notify_shell", self.notify),
            mock.patch.object(reg, "default_executable", lambda: self.EXE),
            mock.patch.object(reg, "association_icon", lambda: self.EXE + ",0"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: _delete_tree(self.root))
        self.addCleanup(lambda: self.assertEqual(_real_snapshot(), self.real_before, "gerçek kayıtlar değişti!"))

    def _cls(self, ext: str) -> str:
        return rf"{self.sw}\Classes\{ext}"

    def test_install_registers_and_is_current(self) -> None:
        self.assertFalse(reg.is_installed())
        reg.install_context_menu()
        self.assertTrue(reg.is_installed())
        self.assertEqual(reg.registered_command(), reg.launcher_command())
        self.assertEqual(_get_default(self._cls(".png")), reg.PROGID)

    def test_refresh_is_noop_when_current(self) -> None:
        reg.install_context_menu()
        self.notify.reset_mock()
        # Gözlemci: yeniden yazılırsa bu değer PROGID'e geri dönerdi
        _set_default(self._cls(".png"), "KullaniciSecimi")
        self.assertFalse(reg.refresh_if_stale())
        self.assertEqual(_get_default(self._cls(".png")), "KullaniciSecimi", "güncelken hiçbir kayıt yazılmamalı")
        self.notify.assert_not_called()

    def test_refresh_rewrites_when_command_is_stale(self) -> None:
        reg.install_context_menu()
        self.notify.reset_mock()
        _set_default(rf"{self.sw}\Classes\{reg.PROGID}\shell\open\command", '"D:\\Eski\\Lumina.exe" "%1"')
        self.assertTrue(reg.refresh_if_stale())
        self.assertEqual(reg.registered_command(), reg.launcher_command())
        self.assertEqual(self.notify.call_count, 1)

    def test_refresh_does_nothing_when_not_installed(self) -> None:
        self.assertFalse(reg.refresh_if_stale())
        self.assertFalse(reg.is_installed())

    def test_previous_default_is_backed_up_and_restored(self) -> None:
        _set_default(self._cls(".jpg"), "jpegfile")
        reg.install_context_menu()
        self.assertEqual(_get_default(self._cls(".jpg")), reg.PROGID)
        reg.install_context_menu()  # ikinci kez: yedek PROGID ile ezilmemeli
        reg.uninstall_context_menu()
        self.assertEqual(_get_default(self._cls(".jpg")), "jpegfile", "kullanıcının eski varsayılanı geri gelmeli")
        self.assertFalse(reg.is_installed())
        self.assertIsNone(_get_default(rf"{self.sw}\Classes\{reg.PROGID}"))

    def test_ts_default_is_never_overridden(self) -> None:
        _set_default(self._cls(".ts"), "VSCode.ts")
        reg.install_context_menu()
        self.assertEqual(_get_default(self._cls(".ts")), "VSCode.ts", ".ts (TypeScript) varsayılanı ezilmemeli")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{self._cls('.ts')}\OpenWithProgids") as key:
            self.assertEqual(winreg.QueryValueEx(key, reg.PROGID)[0], "", "yine de 'Birlikte aç' listesinde olmalı")
        reg.uninstall_context_menu()
        self.assertEqual(_get_default(self._cls(".ts")), "VSCode.ts")

    def test_svg_default_is_never_overridden_but_listed_in_open_with(self) -> None:
        _set_default(self._cls(".svg"), "MSEdgeHTM")
        reg.install_context_menu()
        self.assertEqual(_get_default(self._cls(".svg")), "MSEdgeHTM", "SVG tarayıcıda/editörde açılmaya devam etmeli")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{self._cls('.svg')}\OpenWithProgids") as key:
            self.assertEqual(winreg.QueryValueEx(key, reg.PROGID)[0], "")
        reg.uninstall_context_menu()
        self.assertEqual(_get_default(self._cls(".svg")), "MSEdgeHTM")

    def test_context_menu_text_uses_the_app_name_and_language(self) -> None:
        with mock.patch("app.resources.locales.get_active_language", return_value="tr"):
            self.assertEqual(reg.menu_text(), "Lumina ile aç")
        with mock.patch("app.resources.locales.get_active_language", return_value="en"):
            self.assertEqual(reg.menu_text(), "Open with Lumina")
            reg.install_context_menu()
            self.assertEqual(
                _get_default(rf"{self.sw}\Classes\Applications\{reg.APP_KEY_NAME}\shell\open"), "Open with Lumina"
            )
        self.assertNotIn("Live Photo", reg.DISPLAY_NAME)

    def _legacy_verb(self) -> str:
        return rf"{self.sw}\{reg.SHELL_REL}"

    def _make_legacy_verb(self) -> None:
        _set_default(self._legacy_verb(), "Lumina ile aç")
        _set_default(self._legacy_verb() + r"\command", '"C:\\Eski\\Lumina.exe" "%1"')

    def test_no_top_level_right_click_entry_is_written(self) -> None:
        reg.install_context_menu()
        self.assertIsNone(_get_default(self._legacy_verb()), "sağ tıkta ayrı 'Lumina ile aç' satırı olmamalı")
        self.assertTrue(reg.is_installed(), "kurulum işareti artık dosya türü sınıfının open komutu")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{self._cls('.png')}\OpenWithProgids") as key:
            self.assertEqual(winreg.QueryValueEx(key, reg.PROGID)[0], "", "'Birlikte aç' listesinde kalmalı")

    def test_old_right_click_entry_is_removed_on_reinstall(self) -> None:
        self._make_legacy_verb()
        reg.install_context_menu()
        self.assertIsNone(_get_default(self._legacy_verb()))
        self.assertIsNone(_get_default(self._legacy_verb() + r"\command"))

    def test_old_right_click_entry_is_removed_at_startup_refresh(self) -> None:
        reg.install_context_menu()
        self._make_legacy_verb()
        self.notify.reset_mock()
        self.assertTrue(reg.refresh_if_stale())
        self.assertIsNone(_get_default(self._legacy_verb()))
        self.assertEqual(self.notify.call_count, 1)
        self.notify.reset_mock()
        self.assertFalse(reg.refresh_if_stale(), "ikinci açılışta artık yazılacak bir şey yok")
        self.notify.assert_not_called()

    def test_uninstall_also_removes_the_old_right_click_entry(self) -> None:
        reg.install_context_menu()
        self._make_legacy_verb()
        reg.uninstall_context_menu()
        self.assertIsNone(_get_default(self._legacy_verb()))
        self.assertFalse(reg.is_installed())

    def test_bmp_default_is_never_overridden(self) -> None:
        # Explorer'ın "Yeni" menüsü uzantının sınıf adını gösterir: .bmp "Lumina" olursa "Yeni > Lumina" çıkar.
        _set_default(self._cls(".bmp"), "Paint.Picture")
        reg.install_context_menu()
        self.assertEqual(_get_default(self._cls(".bmp")), "Paint.Picture")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{self._cls('.bmp')}\OpenWithProgids") as key:
            self.assertEqual(winreg.QueryValueEx(key, reg.PROGID)[0], "", "'Birlikte aç' listesinde kalmalı")

    def test_old_bmp_override_is_released_at_startup_refresh(self) -> None:
        reg.install_context_menu()
        _set_default(self._cls(".bmp"), reg.PROGID)  # eski sürümün bıraktığı durum
        self.notify.reset_mock()
        self.assertTrue(reg.refresh_if_stale())
        self.assertNotEqual(_get_default(self._cls(".bmp")), reg.PROGID)
        self.assertEqual(self.notify.call_count, 1)
        self.assertFalse(reg.refresh_if_stale(), "ikinci açılışta yazılacak bir şey yok")

    def test_uninstall_without_backup_clears_only_our_default(self) -> None:
        reg.install_context_menu()  # .png için önceki varsayılan yok
        reg.uninstall_context_menu()
        # Boş değer bırakmak sistemin (HKLM) varsayılanını gölgelerdi; değer silinmeli.
        self.assertIsNone(_get_default(self._cls(".png")))


class NoUnpromptedQuestionTest(unittest.TestCase):
    """Uygulama ilk açılışta kendiliğinden 'varsayılan görüntüleyici olsun mu?' diye sormaz (Ayarlar'da düğme var)."""

    def test_main_window_has_no_first_run_association_prompt(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        source = (root / "app" / "ui" / "main_window.py").read_text(encoding="utf-8")
        self.assertNotIn("_maybe_ask_association", source)
        self.assertNotIn("assoc_asked", source)
        from app.resources.locales import LOCALES

        for table in LOCALES.values():
            self.assertFalse([k for k in table if k.startswith("assoc_")], "kullanılmayan soru metinleri silinmeli")


if __name__ == "__main__":
    unittest.main()
