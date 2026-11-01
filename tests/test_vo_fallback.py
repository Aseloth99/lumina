"""Video çıkışı (VO) başlatılamazsa (GL yok) Linux'ta yazılımsal çıkışa otomatik geri dönüş."""

from __future__ import annotations

import os
import pathlib
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtWidgets import QApplication  # noqa: E402


class _FakeMpv:
    def __init__(self) -> None:
        self.terminated = False
        self.commands: list = []

    def unobserve_property(self, *a) -> None:
        pass

    def command(self, *a) -> None:
        self.commands.append(a)

    def terminate(self) -> None:
        self.terminated = True


class VoFallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _player(self):
        from app.ui.player import LivePlayer

        p = LivePlayer()
        self.addCleanup(p.shutdown)
        p._path = "klip.mp4"
        p.loop = True
        p._mpv = _FakeMpv()
        p._accept_entry = lambda _eid: True  # type: ignore[method-assign]
        self.plays: list = []
        p.play = lambda path, loop=False: self.plays.append((path, loop))  # type: ignore[method-assign]
        self.failures: list[str] = []
        p.failed.connect(self.failures.append)
        return p

    def test_linux_vo_init_failure_retries_with_software_vo(self) -> None:
        from app.ui import player as player_mod

        p = self._player()
        old = p._mpv
        p._opening = True
        with mock.patch.object(player_mod.sys, "platform", "linux"):
            p._on_mpv_error(1, -15)
        self.assertEqual(p._vo_override, {"vo": "x11"})
        self.assertEqual(self.plays, [("klip.mp4", True)], "aynı dosya yeniden oynatılmalı")
        self.assertEqual(self.failures, [], "geri dönüş sürerken hata gösterilmemeli")
        self.assertTrue(old.terminated or old.commands, "eski mpv örneği sökülmeli")
        self.assertIsNone(p._mpv)

    def test_fallback_is_tried_only_once_then_error_is_reported(self) -> None:
        from app.ui import player as player_mod

        p = self._player()
        p._opening = True
        with mock.patch.object(player_mod.sys, "platform", "linux"):
            p._on_mpv_error(1, -15)
            p._mpv = _FakeMpv()
            p._opening = True
            p._on_mpv_error(2, -15)
        self.assertEqual(len(self.plays), 1)
        self.assertEqual(len(self.failures), 1)
        self.assertIn("Video çıkışı", self.failures[0])

    def test_windows_and_macos_have_no_fallback_chain(self) -> None:
        from app.ui import player as player_mod

        for plat in ("win32", "darwin"):
            p = self._player()
            p._opening = True
            with mock.patch.object(player_mod.sys, "platform", plat):
                p._on_mpv_error(1, -15)
            self.assertEqual(p._vo_override, {}, plat)
            self.assertEqual(self.plays, [], plat)
            self.assertEqual(len(self.failures), 1, plat)
            self.failures.clear()

    def test_other_errors_never_trigger_fallback(self) -> None:
        from app.ui import player as player_mod

        p = self._player()
        p._opening = True
        with mock.patch.object(player_mod.sys, "platform", "linux"):
            p._on_mpv_error(1, -17)
        self.assertEqual(self.plays, [])
        self.assertEqual(p._vo_override, {})
        self.assertEqual(len(self.failures), 1)


if __name__ == "__main__":
    unittest.main()
