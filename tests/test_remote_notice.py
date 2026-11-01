"""Uzaktan duyuru: istek bicimi, duyuru bildirimi (her duyuru bir kez) ve ayar kutusu."""

from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from app.core import remote_notice as rn


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class UpdaterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import tempfile

        self.tmp = tempfile.mkdtemp(prefix="lm_notice_")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, self.tmp)

    def _run(self, reply):
        from app.core import updater
        from app.core.settings_store import make_settings

        checker = updater.UpdateChecker()
        notices, seen, captured = [], {}, {}
        checker.notice_received.connect(notices.append)

        def fake_open(req, timeout=0, context=None):
            seen["url"] = req.full_url
            return _Resp(json.dumps(reply).encode())

        with mock.patch("urllib.request.urlopen", fake_open), \
                mock.patch.object(updater._UpdateCheckWorker, "start", lambda w: captured.setdefault("w", w)):
            checker.check_for_updates(False)
            captured["w"]._run()
        self.app.processEvents()
        return seen.get("url", ""), notices

    def test_request_carries_only_the_app_name_and_version(self):
        from urllib.parse import parse_qs, urlparse

        url, _ = self._run({"updateAvailable": False})
        query = parse_qs(urlparse(url).query)
        self.assertEqual(sorted(query), ["appName", "currentVersion"])
        self.assertEqual(query["appName"], ["Lumina"])

    def test_notice_arrives_without_an_update(self):
        _, notices = self._run({"updateAvailable": False, "notice": {"id": "n1", "text": "Bakım var"}})
        self.assertEqual([n.id for n in notices], ["n1"])


class NoticeBarTest(unittest.TestCase):
    """Duyuru: pencere üstünde tek satır (notice_bar); ✕ ile kapatılana dek kalır, kapatılan duyuru bir daha çıkmaz."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _fake(self):
        from app.ui.notice_bar import NoticeBar

        values = {}
        fake = mock.MagicMock()
        fake.settings.value = lambda k, d=None: values.get(k, d)
        fake.settings.setValue = lambda k, v: values.__setitem__(k, v)
        fake.notice_bar = NoticeBar()
        self.addCleanup(fake.notice_bar.deleteLater)
        return fake, values

    def test_notice_stays_until_dismissed_and_is_not_shown_again(self):
        from app.ui.main_window import MainWindow

        fake, values = self._fake()
        notice = rn.Notice("n1", "Duyuru", "https://macik.com.tr/x")
        MainWindow._on_notice(fake, notice)
        self.assertEqual(fake.notice_bar.current().id, "n1")
        self.assertEqual(fake.notice_bar.text_label.text(), "Duyuru")
        self.assertFalse(fake.notice_bar.arrow.isHidden(), "güvenilir adres: ok işareti görünür")
        self.assertNotIn("dismissed_notice", values, "gösterilmesi duyuruyu kapatılmış saymaz (14 sn'lik toast değil)")
        fake._sync_notice_row.assert_called()
        # kullanıcı ✕ ile kapatınca kimlik saklanır; aynı duyuru bir daha çıkmaz
        fake.notice_bar.dismissed.connect(lambda nid: MainWindow._on_notice_dismissed(fake, nid))
        fake.notice_bar.dismiss()
        self.assertEqual(values["dismissed_notice"], "n1")
        self.assertIsNone(fake.notice_bar.current())
        MainWindow._on_notice(fake, notice)
        self.assertIsNone(fake.notice_bar.current(), "kapatılan duyuru yeniden gösterilmez")
        MainWindow._on_notice(fake, rn.Notice("n2", "Başka", ""))
        self.assertEqual(fake.notice_bar.current().id, "n2")
        self.assertTrue(fake.notice_bar.arrow.isHidden(), "adres yok: ok işareti yok")

    def test_untrusted_link_is_not_clickable(self):
        fake, _ = self._fake()
        fake.notice_bar.show_notice(rn.Notice("n3", "Kötü", "https://evil.example/x"))
        self.assertTrue(fake.notice_bar.arrow.isHidden())

    def test_site_link_opens_the_site(self):
        from app.ui.link_label import LinkLabel

        label = LinkLabel("macik.com.tr", rn.SITE_URL)
        self.assertEqual(label.text(), "macik.com.tr")
        with mock.patch("app.ui.link_label.QDesktopServices.openUrl") as opened:
            from PyQt6.QtCore import QPointF, Qt
            from PyQt6.QtGui import QMouseEvent
            from PyQt6.QtCore import QEvent

            event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(1, 1), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            label.mousePressEvent(event)
            self.assertEqual(opened.call_count, 1)
        evil = LinkLabel("x", "https://evil.example")
        with mock.patch("app.ui.link_label.QDesktopServices.openUrl") as opened:
            evil.mousePressEvent(event)
            self.assertEqual(opened.call_count, 0)


if __name__ == "__main__":
    unittest.main()
