"""Güncelleme akışı platforma göre: Windows'ta indir+kur, macOS/Linux'ta indirme sayfasını aç."""

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

from app.core import update_policy  # noqa: E402


class SupportsAutoInstallTest(unittest.TestCase):
    def test_only_windows_installs_in_place(self) -> None:
        self.assertTrue(update_policy.supports_auto_install("win32"))
        for other in ("darwin", "linux", "freebsd13"):
            self.assertFalse(update_policy.supports_auto_install(other), other)

    def test_default_uses_current_platform(self) -> None:
        self.assertEqual(update_policy.supports_auto_install(), sys.platform == "win32")


class UpdateServicePlatformTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._tmp.name)
        cls.app = QApplication.instance() or QApplication([])

    def _service(self, url: str):
        from app.ui.updates import UpdateService

        service = UpdateService()
        self.addCleanup(service.deleteLater)
        service._pending = {"version": "9.9.9", "url": url, "notes": "", "mandatory": False, "size": 10, "checksum": ""}
        service.installer = mock.Mock()
        return service

    def test_non_windows_opens_trusted_page_and_never_downloads(self) -> None:
        service = self._service("https://macik.com.tr/downloads/Lumina.zip")
        with mock.patch("app.ui.updates.supports_auto_install", return_value=False), mock.patch(
            "app.ui.updates.QDesktopServices.openUrl", return_value=True
        ) as open_url:
            service._start_download()
        open_url.assert_called_once()
        self.assertEqual(open_url.call_args.args[0].toString(), "https://macik.com.tr/downloads/Lumina.zip")
        service.installer.download_update.assert_not_called()
        self.assertIsNone(service._thread)

    def test_non_windows_refuses_untrusted_page(self) -> None:
        service = self._service("https://evil.example/Lumina.zip")
        with mock.patch("app.ui.updates.supports_auto_install", return_value=False), mock.patch(
            "app.ui.updates.QDesktopServices.openUrl", return_value=True
        ) as open_url:
            service._start_download()
        open_url.assert_not_called()

    def test_windows_starts_download_thread(self) -> None:
        service = self._service("https://macik.com.tr/downloads/Lumina.zip")
        with mock.patch("app.ui.updates.supports_auto_install", return_value=True), mock.patch(
            "app.ui.updates._is_packaged", return_value=True
        ), mock.patch("app.ui.updates._DownloadThread") as thread_cls:
            service._start_download()
        thread_cls.assert_called_once()
        thread_cls.return_value.start.assert_called_once()

    def test_dialog_button_label_depends_on_platform(self) -> None:
        from app.resources.locales import t
        from app.ui.update_dialog import UpdateDialog

        with mock.patch("app.ui.update_dialog.supports_auto_install", return_value=False):
            dialog = UpdateDialog("9.9.9", "notlar", False, lang="tr")
            self.addCleanup(dialog.deleteLater)
            self.assertEqual(dialog.update_btn.text(), t("update_open_page", "tr"))
        with mock.patch("app.ui.update_dialog.supports_auto_install", return_value=True):
            dialog2 = UpdateDialog("9.9.9", "notlar", False, lang="tr")
            self.addCleanup(dialog2.deleteLater)
            self.assertEqual(dialog2.update_btn.text(), t("update_now", "tr"))


if __name__ == "__main__":
    unittest.main()
