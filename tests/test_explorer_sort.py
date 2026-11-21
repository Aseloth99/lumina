"""Sıralama "Gezgin ile aynı": Lumina, açılan dosyanın klasörünü gösteren Dosya Gezgini penceresinin sırasını izler.

Kullanıcı bildirimi: Gezgin'de tarihe göre (yeniden eskiye) sıralı klasörde son fotoğrafı açınca Lumina ad sırasıyla gezdiriyor,
son fotoğraftan sonra videolar geliyordu (Gezgin'de en altta duranlar). Lumina'nın kayıtlı sırası name_asc'ti, Gezgin'inki
tarih azalan. Shell COM burada taklit edilir; gerçek Gezgin pencerelerine dokunulmaz.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QSettings  # noqa: E402

from app.core import explorer_sort  # noqa: E402
from app.core.explorer_sort import parse_sort_columns, resolve_sort  # noqa: E402


class ParseSortColumnsTest(unittest.TestCase):
    def test_explorer_columns_map_to_lumina_sorts(self) -> None:
        cases = {
            "prop:-System.ItemDate;": "date_desc",
            "prop:System.ItemDate;": "date_asc",
            "prop:+System.DateModified;": "date_asc",
            "prop:-System.DateCreated;": "date_desc",
            "prop:-System.Photo.DateTaken;System.ItemNameDisplay;": "date_desc",
            "prop:System.ItemNameDisplay;": "name_asc",
            "prop:-System.ItemNameDisplay;": "name_desc",
            "prop:System.ItemName;": "name_asc",
            "prop:-System.FileName;": "name_desc",
            "PROP:-system.itemdate;": "date_desc",
        }
        for text, expected in cases.items():
            self.assertEqual(parse_sort_columns(text), expected, text)

    def test_unsupported_or_empty_columns_give_none(self) -> None:
        for text in ("prop:-System.Size;", "prop:System.ItemTypeText;", "", "prop:", None, 42):
            self.assertIsNone(parse_sort_columns(text), repr(text))

    def test_only_the_primary_column_counts(self) -> None:
        self.assertIsNone(parse_sort_columns("prop:System.Size;-System.ItemDate;"))


class ResolveSortTest(unittest.TestCase):
    def test_fixed_mode_never_queries_explorer(self) -> None:
        query = mock.Mock(return_value="date_desc")
        self.assertEqual(resolve_sort("name_asc", "date_asc", "C:/x", query), ("name_asc", False))
        query.assert_not_called()

    def test_explorer_mode_uses_the_window_order(self) -> None:
        self.assertEqual(resolve_sort("explorer", "name_asc", "C:/x", lambda _f: "date_desc"), ("date_desc", True))

    def test_explorer_mode_falls_back_silently(self) -> None:
        self.assertEqual(resolve_sort("explorer", "name_asc", "C:/x", lambda _f: None), ("name_asc", False))

        def boom(_f):
            raise OSError("COM down")

        self.assertEqual(resolve_sort("explorer", "name_desc", "C:/x", boom), ("name_desc", False))
        self.assertEqual(resolve_sort("explorer", "bogus", "C:/x", lambda _f: None), ("date_asc", False))

    def test_hung_com_does_not_block_longer_than_the_timeout(self) -> None:
        release = threading.Event()

        def hang(_folder):
            release.wait(5)
            return "date_desc"

        with mock.patch.object(explorer_sort, "_query_shell_windows", hang), mock.patch.object(sys, "platform", "win32"):
            t = time.monotonic()
            self.assertIsNone(explorer_sort.explorer_sort_for_folder("C:/x", timeout=0.2))
            self.assertLess(time.monotonic() - t, 1.5)
        release.set()


class _FakeWindow:
    def __init__(self, path, sort, hwnd, broken=False):
        self.HWND = hwnd
        if broken:
            self.Document = types.SimpleNamespace()  # Folder yok (ör. Internet Explorer penceresi)
        else:
            self.Document = types.SimpleNamespace(
                Folder=types.SimpleNamespace(Self=types.SimpleNamespace(Path=path)), SortColumns=sort
            )


class ShellWindowsQueryTest(unittest.TestCase):
    """COM katmanı taklit edilir: pythoncom/win32com sahte modüllerle değiştirilir."""

    def _run(self, windows, folder, foreground=0):
        shell = types.SimpleNamespace(Windows=lambda: windows)
        pythoncom = types.SimpleNamespace(CoInitialize=mock.Mock(), CoUninitialize=mock.Mock())
        client = types.SimpleNamespace(Dispatch=mock.Mock(return_value=shell))
        win32com = types.SimpleNamespace(client=client)
        fake_user32 = types.SimpleNamespace(GetForegroundWindow=lambda: foreground)
        import ctypes

        with mock.patch.dict(sys.modules, {"pythoncom": pythoncom, "win32com": win32com, "win32com.client": client}), \
                mock.patch.object(ctypes, "windll", types.SimpleNamespace(user32=fake_user32), create=True):
            result = explorer_sort._query_shell_windows(folder)
        pythoncom.CoInitialize.assert_called_once()
        pythoncom.CoUninitialize.assert_called_once()
        client.Dispatch.assert_called_once_with("Shell.Application")
        return result

    def test_matching_folder_window_is_found_case_and_slash_insensitively(self) -> None:
        windows = [
            _FakeWindow(None, None, 1, broken=True),
            _FakeWindow(r"C:\Other", "prop:System.ItemNameDisplay;", 2),
            _FakeWindow(r"C:\Users\Me\Photos", "prop:-System.ItemDate;", 3),
        ]
        self.assertEqual(self._run(windows, "c:/users/me/photos/"), "date_desc")

    def test_no_window_for_the_folder_gives_none(self) -> None:
        windows = [_FakeWindow(r"C:\Other", "prop:-System.ItemDate;", 2)]
        self.assertIsNone(self._run(windows, r"C:\Users\Me\Photos"))

    def test_foreground_window_wins_when_the_folder_is_open_twice(self) -> None:
        windows = [
            _FakeWindow(r"C:\P", "prop:System.ItemNameDisplay;", 10),
            _FakeWindow(r"C:\P", "prop:-System.ItemDate;", 11),
        ]
        self.assertEqual(self._run(windows, r"C:\P", foreground=11), "date_desc")
        self.assertEqual(self._run(windows, r"C:\P", foreground=0), "name_asc")


class FolderScanFollowsExplorerTest(unittest.TestCase):
    """Kullanıcının durumu küçültülmüş: adı sonra gelen ama ESKİ videolar. Ad sırası 9 -> video; Gezgin (tarih ↓) sırası değil."""

    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])
        cls._tmp = tempfile.TemporaryDirectory()
        folder = pathlib.Path(cls._tmp.name)
        base = 1_700_000_000
        for i in range(1, 10):
            (folder / f"{i:04d}_IMG.jpg").write_bytes(b"")
            os.utime(folder / f"{i:04d}_IMG.jpg", (base + i * 100, base + i * 100))
        for i in range(1, 3):
            (folder / f"F2-T8-{i}.mp4").write_bytes(b"")
            os.utime(folder / f"F2-T8-{i}.mp4", (base - i * 100, base - i * 100))
        cls.folder = folder

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _scan(self, sort_by, fallback, explorer_answer):
        from app.core.folder_scan import FolderScanWorker

        worker = FolderScanWorker(str(self.folder), "0009_IMG.jpg", 7, sort_by, fallback_sort=fallback)
        ready, resolved = [], []
        worker.signals.ready.connect(lambda _f, paths, idx, _t: ready.append(([pathlib.Path(p).name for p in paths], idx)))
        worker.signals.sort_resolved.connect(lambda mode, from_exp, tok: resolved.append((mode, from_exp, tok)))
        with mock.patch.object(explorer_sort, "explorer_sort_for_folder", mock.Mock(return_value=explorer_answer)) as q:
            worker.run()
        return ready[-1], resolved, q

    def test_explorer_date_desc_order_is_used(self) -> None:
        (names, idx), resolved, query = self._scan("explorer", "name_asc", "date_desc")
        query.assert_called_once_with(str(self.folder))
        self.assertEqual(resolved, [("date_desc", True, 7)])
        self.assertEqual(names[0], "0009_IMG.jpg")
        self.assertEqual(idx, 0)
        self.assertEqual(names[1], "0008_IMG.jpg", "son fotoğraftan sonra Gezgin'deki gibi bir önceki fotoğraf gelmeli")
        self.assertEqual(names[-2:], ["F2-T8-1.mp4", "F2-T8-2.mp4"], "eski videolar en sonda")

    def test_no_explorer_window_uses_the_saved_order(self) -> None:
        (names, idx), resolved, _q = self._scan("explorer", "name_asc", None)
        self.assertEqual(resolved, [("name_asc", False, 7)])
        self.assertEqual(names[idx + 1], "F2-T8-1.mp4")

    def test_fixed_order_does_not_touch_com(self) -> None:
        (names, _idx), resolved, query = self._scan("name_asc", "date_asc", "date_desc")
        query.assert_not_called()
        self.assertEqual(resolved, [])
        self.assertEqual(names[0], "0001_IMG.jpg")


class SortSettingMigrationTest(unittest.TestCase):
    """`MainWindow._load_sort_setting` hafif bir taslak üzerinde (tam pencere kurmadan)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = QSettings(os.path.join(self._tmp.name, "s.ini"), QSettings.Format.IniFormat)

    def tearDown(self) -> None:
        self.settings = None
        self._tmp.cleanup()

    def _load(self):
        from app.ui.main_window import MainWindow

        stub = types.SimpleNamespace(settings=self.settings)
        stub._setting_bool = lambda key, default=False: MainWindow._setting_bool(stub, key, default)
        MainWindow._load_sort_setting(stub)
        return stub

    def test_new_user_gets_explorer_order_by_default(self) -> None:
        stub = self._load()
        self.assertEqual(stub.sort_by, "explorer")
        self.assertEqual(stub.sort_fallback, "date_asc")

    def test_explicit_choice_migrates_once_and_stays_as_fallback(self) -> None:
        self.settings.setValue("sort_by", "name_asc")
        stub = self._load()
        self.assertEqual(stub.sort_by, "explorer")
        self.assertEqual(stub.sort_fallback, "name_asc")
        self.assertEqual(self.settings.value("sort_by"), "explorer")
        # Kullanıcı sonra yine sabit bir sıra seçerse ona uyulur; geçiş tekrar çalışmaz.
        self.settings.setValue("sort_by", "date_desc")
        self.settings.setValue("sort_fallback", "date_desc")
        stub = self._load()
        self.assertEqual(stub.sort_by, "date_desc")
        self.assertEqual(stub.sort_fallback, "date_desc")

    def test_menu_label_shows_the_active_order(self) -> None:
        from app.resources.locales import t as locale_t
        from app.ui.main_window import MainWindow

        stub = self._load()
        for lang, plain, active, fallback in (
            ("tr", "Gezgin ile aynı", "Gezgin ile aynı (Tarih ↓)", "Gezgin ile aynı (Gezgin'de açık değil: Ad ↑)"),
            ("en", "Same as File Explorer", "Same as File Explorer (Date ↓)",
             "Same as File Explorer (not open in Explorer: Name ↑)"),
        ):
            stub.t = lambda key, _l=lang: locale_t(key, _l)
            stub._active_sort, stub._active_sort_from_explorer = None, False
            self.assertEqual(MainWindow._explorer_sort_label(stub), plain)
            stub._active_sort, stub._active_sort_from_explorer = "date_desc", True
            self.assertEqual(MainWindow._explorer_sort_label(stub), active)
            stub._active_sort, stub._active_sort_from_explorer = "name_asc", False
            self.assertEqual(MainWindow._explorer_sort_label(stub), fallback)

    def test_rescan_keeps_the_order_read_at_open(self) -> None:
        from app.ui.main_window import MainWindow

        stub = self._load()
        stub._active_sort = "date_desc"
        self.assertEqual(MainWindow._scan_sort_for_rescan(stub), "date_desc")
        stub._active_sort = None
        self.assertEqual(MainWindow._scan_sort_for_rescan(stub), "explorer")


if __name__ == "__main__":
    unittest.main()
