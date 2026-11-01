"""Hızlı açılış başlatıcısı (launcher/) ile çalışınca uygulamanın kendi yolu.

Başlatıcı uygulamayı %LOCALAPPDATA%\\Lumina\\runtime\\<sürüm>\\ altına bir kez açar ve oradan çalıştırır;
kendi yolunu `LUMINA_LAUNCHER` ile bildirir. "Birlikte aç" kaydı, güncelleyicinin değiştireceği dosya ve
görev çubuğu sabitlemesi sürümle değişen açılmış kopyayı değil, kullanıcının Lumina.exe'sini göstermeli.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


class LauncherPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = pathlib.Path(self._tmp.name)
        self.launcher = root / "Downloads" / "Lumina.exe"
        self.launcher.parent.mkdir()
        self.launcher.write_bytes(b"MZ")
        self.unpacked = root / "runtime" / "0123456789abcdef" / "Lumina.exe"
        self.unpacked.parent.mkdir(parents=True)
        self.unpacked.write_bytes(b"MZ")

    def _frozen(self, launcher: str | None):
        env = {"LUMINA_LAUNCHER": launcher} if launcher else {}
        patches = [
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "executable", str(self.unpacked)),
            mock.patch.dict(os.environ, env, clear=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        if not launcher:
            os.environ.pop("LUMINA_LAUNCHER", None)  # patch.dict geri yükler

    def test_the_launcher_is_the_apps_own_exe(self) -> None:
        from app.core.paths import app_executable, get_application_path

        self._frozen(str(self.launcher))
        self.assertEqual(app_executable(), self.launcher)
        self.assertEqual(get_application_path(), self.launcher.resolve().parent, "güncelleyici bu klasördeki exe'yi değiştirir")

    def test_without_a_launcher_or_with_a_missing_one_the_running_exe_counts(self) -> None:
        from app.core.paths import app_executable

        self._frozen(None)
        self.assertEqual(app_executable(), self.unpacked)
        os.environ["LUMINA_LAUNCHER"] = str(self.launcher.with_name("yok.exe"))
        self.assertEqual(app_executable(), self.unpacked, "olmayan bir yola güvenilmez")

    def test_open_with_registration_points_at_the_launcher(self) -> None:
        from app.core.registry import default_executable, launcher_command

        self._frozen(str(self.launcher))
        self.assertEqual(default_executable(), str(self.launcher))
        self.assertEqual(launcher_command(), f'"{self.launcher}" "%1"')

    def test_launcher_path_is_only_known_for_a_frozen_build_started_by_the_launcher(self) -> None:
        from app.core.paths import launcher_path

        self._frozen(str(self.launcher))
        self.assertEqual(launcher_path(), self.launcher, "güncelleyici/geri alma bu dosyayı değiştirir")
        os.environ["LUMINA_LAUNCHER"] = str(self.launcher.with_name("yok.exe"))
        self.assertIsNone(launcher_path(), "olmayan bir yola güvenilmez")
        os.environ.pop("LUMINA_LAUNCHER")
        self.assertIsNone(launcher_path(), "başlatıcısız çalışan exe güncelleme hedefi değildir")

    def test_launcher_path_is_unknown_when_running_from_source(self) -> None:
        from app.core.paths import launcher_path

        with mock.patch.dict(os.environ, {"LUMINA_LAUNCHER": str(self.launcher)}):
            self.assertIsNone(launcher_path(), "python.exe asla güncelleme hedefi olmaz")

    def test_not_frozen_ignores_the_variable(self) -> None:
        from app.core.paths import app_executable

        with mock.patch.dict(os.environ, {"LUMINA_LAUNCHER": str(self.launcher)}):
            self.assertEqual(app_executable(), pathlib.Path(sys.executable))


@unittest.skipUnless(sys.platform == "win32", "Windows görev çubuğu özellikleri")
class RelaunchPropertiesTest(unittest.TestCase):
    """Sabitlenen simge başlatıcıyı çalıştırsın: pencere özellikleri gerçek bir pencereden geri okunur."""

    def test_properties_are_written_on_a_real_window(self) -> None:
        import ctypes
        from ctypes import wintypes

        from app.core import win_launcher

        user32 = ctypes.windll.user32
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        hwnd = user32.CreateWindowExW(0, "STATIC", "lumina-test", 0, 0, 0, 10, 10, None, None, None, None)
        self.assertTrue(hwnd)
        self.addCleanup(lambda: user32.DestroyWindow(hwnd))
        exe = r"C:\Users\x\Downloads\Lumina.exe"
        self.assertTrue(win_launcher.set_window_relaunch(hwnd, "Lumina.Viewer.Test", f'"{exe}"', f"{exe},0", "Lumina"))
        got = _read_props(hwnd, [2, 3, 4, 5])
        self.assertEqual(got, [f'"{exe}"', f"{exe},0", "Lumina", "Lumina.Viewer.Test"])

    def test_pin_is_left_alone_without_a_launcher(self) -> None:
        from app.core import win_launcher

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LUMINA_LAUNCHER", None)
            with mock.patch.object(win_launcher, "set_window_relaunch") as setter:
                self.assertFalse(win_launcher.pin_to_launcher(1234))
            setter.assert_not_called()


def _read_props(hwnd: int, pids: list[int]) -> list[str | None]:
    import ctypes
    from ctypes import wintypes

    from app.core import win_launcher

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.WORD), ("r2", wintypes.WORD), ("r3", wintypes.WORD),
                    ("ptr", ctypes.c_void_p), ("pad", ctypes.c_void_p)]

    def guid(text: str) -> GUID:
        g = GUID()
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
        return g

    shell32 = ctypes.windll.shell32
    shell32.SHGetPropertyStoreForWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetPropertyStoreForWindow.restype = ctypes.HRESULT
    store = ctypes.c_void_p()
    iid = guid(win_launcher._IID_PROPERTY_STORE)
    shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(iid), ctypes.byref(store))
    vtbl = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    get_value = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY),
                                   ctypes.POINTER(PROPVARIANT))(vtbl[5])
    fmtid = guid(win_launcher._PKEY_FMTID)
    out: list[str | None] = []
    for pid in pids:
        pv = PROPVARIANT()
        get_value(store, ctypes.byref(PROPERTYKEY(fmtid, pid)), ctypes.byref(pv))
        out.append(ctypes.wstring_at(pv.ptr) if pv.vt == 31 and pv.ptr else None)
        ctypes.oledll.ole32.PropVariantClear(ctypes.byref(pv))
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(store)
    return out


if __name__ == "__main__":
    unittest.main()
