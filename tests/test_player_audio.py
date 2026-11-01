"""Ses aygıtı tercihi: motor kurulmadan önce verilen tercih silinmemeli.

Eski hata: set_audio_device motor yokken çağrılınca aygıt listesi yalnızca ["auto"]
döndüğünden kayıtlı aygıt sessizce "auto"ya düşüyordu. Gerçek mpv gerekmez (sahte nesne).
"""

from __future__ import annotations

import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtWidgets import QApplication  # noqa: E402


class _FakeMpv:
    def __init__(self, devices: list[str]) -> None:
        self.audio_device_list = [{"name": n, "description": n} for n in devices]
        self.props: dict[str, object] = {}

    def _set_property(self, name: str, value) -> None:
        self.props[name] = value


class PlayerAudioDeviceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _player(self):
        from app.ui.player import LivePlayer

        return LivePlayer()

    def test_preference_survives_before_engine(self) -> None:
        p = self._player()
        p.set_audio_device("wasapi/{abc}")
        self.assertEqual(p.preferred_audio_device(), "wasapi/{abc}")

    def test_known_device_applied_after_engine(self) -> None:
        p = self._player()
        p.set_audio_device("wasapi/{abc}")
        p._mpv = _FakeMpv(["auto", "wasapi/{abc}"])
        p._apply_audio_settings()
        self.assertEqual(p.current_audio_device(), "wasapi/{abc}")
        self.assertEqual(p._mpv.props["audio-device"], "wasapi/{abc}")

    def test_unknown_device_falls_back_but_keeps_preference(self) -> None:
        p = self._player()
        p.set_audio_device("wasapi/{removed}")
        p._mpv = _FakeMpv(["auto", "wasapi/{other}"])
        p._apply_audio_settings()
        self.assertEqual(p.current_audio_device(), "auto")
        self.assertEqual(p.preferred_audio_device(), "wasapi/{removed}")


if __name__ == "__main__":
    unittest.main()
