"""Çöp kutusu yedekleri: gio/trash-put/Finder yoksa dosya KALICI silinmemeli (FreeDesktop Trash, ~/.Trash)."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core import win_shell  # noqa: E402


class _Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)

    def _file(self, name: str, data: bytes = b"icerik") -> pathlib.Path:
        p = self.root / name
        p.write_bytes(data)
        return p


class XdgTrashTest(_Tmp):
    def test_moves_file_and_writes_trashinfo(self) -> None:
        src = self._file("Fotoğraf 1.jpg", b"veri")
        data_home = self.root / "xdg"
        target = win_shell.trash_xdg(src, data_home)
        self.assertFalse(src.exists())
        self.assertEqual(target, data_home / "Trash" / "files" / "Fotoğraf 1.jpg")
        self.assertEqual(target.read_bytes(), b"veri")
        info = (data_home / "Trash" / "info" / "Fotoğraf 1.jpg.trashinfo").read_text(encoding="utf-8")
        self.assertTrue(info.startswith("[Trash Info]\n"))
        path_line = [ln for ln in info.splitlines() if ln.startswith("Path=")][0]
        self.assertEqual(urllib.parse.unquote(path_line[5:]), str(src.resolve()))
        self.assertRegex(info, r"DeletionDate=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")

    def test_name_collisions_get_unique_names_and_never_overwrite(self) -> None:
        data_home = self.root / "xdg"
        a = self._file("a.jpg", b"1")
        t1 = win_shell.trash_xdg(a, data_home)
        b = self._file("a.jpg", b"2")
        t2 = win_shell.trash_xdg(b, data_home)
        self.assertNotEqual(t1, t2)
        self.assertEqual((t1.read_bytes(), t2.read_bytes()), (b"1", b"2"))
        self.assertEqual(len(list((data_home / "Trash" / "info").glob("*.trashinfo"))), 2)

    def test_default_location_follows_xdg_data_home(self) -> None:
        src = self._file("x.png")
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "datahome")}):
            target = win_shell.trash_xdg(src)
        self.assertEqual(target.parent, self.root / "datahome" / "Trash" / "files")

    @unittest.skipIf(os.name == "nt", "POSIX izinleri")
    def test_private_permissions(self) -> None:
        src = self._file("p.jpg")
        data_home = self.root / "xdg"
        win_shell.trash_xdg(src, data_home)
        info = next((data_home / "Trash" / "info").iterdir())
        self.assertEqual(info.stat().st_mode & 0o077, 0, "info dosyası başkalarına açık olmamalı")

    def test_failed_move_does_not_leave_orphan_info(self) -> None:
        src = self._file("m.jpg")
        data_home = self.root / "xdg"
        with mock.patch("app.core.win_shell.shutil.move", side_effect=OSError("disk dolu")):
            with self.assertRaises(OSError):
                win_shell.trash_xdg(src, data_home)
        self.assertTrue(src.exists(), "taşınamayan dosya yerinde kalmalı")
        self.assertEqual(list((data_home / "Trash" / "info").glob("*")), [])


class MacTrashTest(_Tmp):
    def test_fallback_moves_to_trash_folder_with_unique_name(self) -> None:
        trash = self.root / ".Trash"
        a = self._file("a.mov", b"1")
        t1 = win_shell.trash_macos_fallback(a, trash)
        b = self._file("a.mov", b"2")
        t2 = win_shell.trash_macos_fallback(b, trash)
        self.assertNotEqual(t1, t2)
        self.assertEqual((t1.read_bytes(), t2.read_bytes()), (b"1", b"2"))


class SendToRecycleFallbackTest(_Tmp):
    def test_linux_without_trash_tools_uses_xdg_not_unlink(self) -> None:
        src = self._file("z.jpg", b"onemli")
        data_home = self.root / "xdg"
        with mock.patch.object(win_shell.sys, "platform", "linux"), mock.patch(
            "app.core.win_shell.subprocess.run", side_effect=FileNotFoundError("gio yok")
        ), mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(data_home)}):
            win_shell.send_to_recycle(src)
        self.assertFalse(src.exists())
        self.assertEqual((data_home / "Trash" / "files" / "z.jpg").read_bytes(), b"onemli")

    def test_macos_without_finder_uses_trash_folder_not_unlink(self) -> None:
        src = self._file("y.mov", b"onemli")
        home = self.root / "home"
        with mock.patch.object(win_shell.sys, "platform", "darwin"), mock.patch(
            "app.core.win_shell.subprocess.run", side_effect=FileNotFoundError("osascript yok")
        ), mock.patch("app.core.win_shell.Path.home", return_value=home):
            win_shell.send_to_recycle(src)
        self.assertFalse(src.exists())
        self.assertEqual((home / ".Trash" / "y.mov").read_bytes(), b"onemli")


if __name__ == "__main__":
    unittest.main()
