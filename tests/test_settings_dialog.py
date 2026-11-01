"""Ayarlar penceresi: oluşturulabilmeli (eski hata: QObject import'u yok → hiç açılmıyordu),
birleştirme sonucu kaçışlı/yerelleştirilmiş olmalı ve izlenen klasörde liste yenilenmeli."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


class SettingsDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._settings_tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._settings_tmp.name)
        cls.app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.deleteLater()
        cls._settings_tmp.cleanup()

    def _dialog(self):
        from app.ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self.win)
        self.addCleanup(dlg.deleteLater)
        return dlg

    def test_dialog_can_be_created_and_tabs_switch(self) -> None:
        dlg = self._dialog()
        for tab in (0, 1, 2):
            dlg._switch_tab(tab)

    def test_merge_result_is_escaped_and_localized(self) -> None:
        dlg = self._dialog()
        res = {
            "success": True, "merged_count": 1, "total_scanned": 10, "total_images": 6,
            "total_videos": 4, "already_paired": 3,
            "merged_pairs": [{"image": "<b>x</b>.jpg", "old_video": "a&b.mov", "new_video": "<i>y</i>.mov"}],
        }
        dlg._on_merge_finished(res)
        text = dlg.lbl_merge_status.text()
        self.assertNotIn("<b>x</b>", text, "dosya adındaki HTML kaçışlanmalı")
        self.assertIn("&lt;b&gt;x&lt;/b&gt;.jpg", text)
        self.assertIn("a&amp;b.mov", text)

    def test_merge_error_is_escaped(self) -> None:
        dlg = self._dialog()
        dlg._on_merge_finished({"success": False, "errors": ["<script>alert(1)</script>"]})
        self.assertNotIn("<script>", dlg.lbl_merge_status.text())

    def test_rescan_runs_when_merged_folder_is_the_watched_one(self) -> None:
        dlg = self._dialog()
        with tempfile.TemporaryDirectory() as folder:
            self.win._watched_folder = folder
            dlg.txt_folder.setText(folder)
            with mock.patch.object(self.win, "_rescan_active_folder") as rescan:
                dlg._on_merge_finished({"success": True, "merged_count": 0})
                rescan.assert_called_once()
            other = tempfile.mkdtemp()
            self.addCleanup(lambda: __import__("shutil").rmtree(other, True))
            dlg.txt_folder.setText(other)
            with mock.patch.object(self.win, "_rescan_active_folder") as rescan:
                dlg._on_merge_finished({"success": True, "merged_count": 0})
                rescan.assert_not_called()
        self.win._watched_folder = None


if __name__ == "__main__":
    unittest.main()
