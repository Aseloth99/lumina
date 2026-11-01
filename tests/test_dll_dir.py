"""K3: güncelleme sonrası yeni başlatıcı, çalışan (PyInstaller) uygulamanın `SetDllDirectoryW` ayarını MİRAS ALMAMALI.

Windows bu ayarı çocuk süreçlere aktarır: yeni başlatıcı `VCRUNTIME140.dll`'i System32 yerine eski sürümün `_internal` klasöründen yükleyip
ömrü boyunca açık tutuyor, eski sürüm klasörünün silinmesini engelliyordu (E2E H3). Testler gerçek kernel32 ve gerçek çocuk süreçle çalışır
(pencere açmaz: konsolsuz `python -c`)."""

from __future__ import annotations

import contextlib
import ctypes
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core import dll_dir  # noqa: E402

WINDOWS = sys.platform == "win32"
CHILD = (
    "import ctypes;b=ctypes.create_unicode_buffer(2000);"
    "ctypes.windll.kernel32.GetDllDirectoryW(2000,b);print(b.value)"
)


def _set_dll_directory(path):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    return kernel32.SetDllDirectoryW(path)


@contextlib.contextmanager
def dll_directory_set_to(path):
    """Test sürecinin DLL dizinini geçici olarak ayarlar (önceki değer geri yüklenir)."""
    previous = dll_dir.current_dll_directory()
    _set_dll_directory(path)
    try:
        yield
    finally:
        _set_dll_directory(previous or None)


def child_dll_directory() -> str:
    """Konsolsuz bir çocuk süreçte GetDllDirectoryW: ebeveynden miras alınan değer."""
    # UTF-8 iki yanda da sabit: yoksa çocuk (PYTHONUTF8 ortamı) ile ebeveyn (yerel kod sayfası) "sürüm" gibi yolları farklı çözer
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", CHILD], capture_output=True, encoding="utf-8",
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out.stdout.strip()


@unittest.skipUnless(WINDOWS, "DLL arama dizini yalnızca Windows'ta")
class DefaultDllDirectoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.marker = os.path.join(self._tmp.name, "eski sürüm", "_internal")
        os.makedirs(self.marker)

    def test_the_default_search_order_is_used_inside_and_the_previous_directory_is_restored_after(self) -> None:
        with dll_directory_set_to(self.marker):
            self.assertEqual(os.path.normcase(dll_dir.current_dll_directory()), os.path.normcase(self.marker))
            with dll_dir.default_dll_directory():
                self.assertEqual(dll_dir.current_dll_directory(), "", "bağlam içinde varsayılan arama sırası")
            self.assertEqual(os.path.normcase(dll_dir.current_dll_directory()), os.path.normcase(self.marker), "çalışan süreç kendi dizinini geri alır")

    def test_it_is_restored_even_when_the_body_raises(self) -> None:
        with dll_directory_set_to(self.marker):
            with self.assertRaises(RuntimeError):
                with dll_dir.default_dll_directory():
                    raise RuntimeError("kurulum hatası")
            self.assertEqual(os.path.normcase(dll_dir.current_dll_directory()), os.path.normcase(self.marker))

    def test_nothing_is_set_and_nothing_changes_when_no_directory_was_set(self) -> None:
        with dll_directory_set_to(None):
            with dll_dir.default_dll_directory():
                self.assertEqual(dll_dir.current_dll_directory(), "")
            self.assertEqual(dll_dir.current_dll_directory(), "")

    def test_a_child_started_inside_does_not_inherit_the_directory_but_one_started_outside_does(self) -> None:
        with dll_directory_set_to(self.marker):
            self.assertEqual(os.path.normcase(child_dll_directory()), os.path.normcase(self.marker), "mekanizma gerçek: çocuk ebeveynin DLL dizinini miras alır")
            with dll_dir.default_dll_directory():
                self.assertEqual(child_dll_directory(), "", "bağlam içinde başlayan çocuk eski sürümün klasörüne bağlanmaz")
            self.assertEqual(os.path.normcase(child_dll_directory()), os.path.normcase(self.marker))


class RobustnessTest(unittest.TestCase):
    def test_a_failing_kernel32_never_breaks_the_caller(self) -> None:
        ran = []
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(dll_dir, "_kernel32", side_effect=OSError("yok")):
            with dll_dir.default_dll_directory():
                ran.append(True)
        self.assertEqual(ran, [True])

    @unittest.skipIf(WINDOWS, "Windows'ta gerçek kernel32 kullanılır")
    def test_other_platforms_do_nothing(self) -> None:
        with dll_dir.default_dll_directory():
            self.assertEqual(dll_dir.current_dll_directory(), "")

    def test_on_a_non_windows_platform_the_body_just_runs(self) -> None:
        ran = []
        with mock.patch.object(sys, "platform", "linux"):
            with dll_dir.default_dll_directory():
                ran.append(dll_dir.current_dll_directory())
        self.assertEqual(ran, [""])


if __name__ == "__main__":
    unittest.main()
