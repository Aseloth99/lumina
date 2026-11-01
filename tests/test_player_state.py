"""LivePlayer durum makinesi — GERÇEK libmpv ile (vo=null/ao=null, pencere gerekmez).

Doğrulananlar:
* oynat -> started, süre okunur; duraklat/devam zamanı gerçekten durdurur/ilerletir
* pause komutu başarısızsa durum DEĞİŞMEZ
* açılamayan/bozuk dosya 8 sn'lik zaman aşımı beklenmeden (end-file) bildirilir
* eski dosyanın geç gelen olayları (oturum kimliği) yeni oturumu etkilemez
* mpv_options() içindeki her seçenek gerçek libmpv tarafından kabul edilir
* shutdown hızlıdır ve gözlemcileri gerçekten kaldırır
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # tests/ (_media)

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from _media import DURATION_MS, make_ramp_video  # noqa: E402


class PlayerStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        from app.core.mpv_backend import get_mpv

        cls.mpv_mod = get_mpv()
        if cls.mpv_mod is None:
            raise unittest.SkipTest("libmpv bulunamadı")
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(cls._tmp.name)
        cls.v1, cls.v2 = tmp / "a.mp4", tmp / "b.mp4"
        cls.missing = tmp / "yok.mp4"
        cls.corrupt = tmp / "bozuk.mp4"
        try:
            ok = make_ramp_video(cls.v1) and make_ramp_video(cls.v2, gray_offset=-100)
        except Exception as exc:
            raise unittest.SkipTest(f"video üretilemedi: {exc}")
        if not ok:
            raise unittest.SkipTest("bu ortamda mp4v kodlayıcı yok")
        cls.corrupt.write_bytes(b"bu bir video degil" * 100)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _player(self):
        from app.ui.player import LivePlayer

        p = LivePlayer()
        mod = self.mpv_mod
        p._new_mpv = lambda _m: mod.MPV(  # type: ignore[method-assign]
            vo="null", ao="null", keep_open="yes", osc=False, ytdl=False,
            log_handler=lambda *a: None,
        )
        self.events: dict[str, list] = {"started": [], "finished": [], "failed": [], "paused": []}
        p.started.connect(lambda: self.events["started"].append(time.monotonic()))
        p.finished.connect(lambda: self.events["finished"].append(time.monotonic()))
        p.failed.connect(lambda msg: self.events["failed"].append((time.monotonic(), msg)))
        p.paused.connect(lambda: self.events["paused"].append(1))
        self.addCleanup(p.shutdown)
        return p

    @staticmethod
    def _wait(predicate, timeout=6.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QCoreApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        QCoreApplication.processEvents()
        return predicate()

    def test_play_starts_and_reports_duration(self) -> None:
        p = self._player()
        p.play(str(self.v1))
        self.assertTrue(self._wait(lambda: self.events["started"]), "started gelmedi")
        self.assertTrue(p.playing)
        self.assertFalse(p.opening)
        self.assertTrue(self._wait(lambda: p.duration() > 0))
        self.assertAlmostEqual(p.duration(), DURATION_MS, delta=300)

    def test_pause_and_resume_really_stop_and_advance_time(self) -> None:
        p = self._player()
        p.play(str(self.v1))
        self.assertTrue(self._wait(lambda: self.events["started"]))
        self.assertTrue(self._wait(lambda: p.position() > 200))
        self.assertTrue(p.pause())
        self.assertTrue(p.is_paused)
        self.assertFalse(p.playing)
        frozen = p.position()
        time.sleep(0.5)
        self.assertEqual(p.position(), frozen, "duraklatılınca zaman ilerlememeli")
        self.assertTrue(p.resume())
        self.assertFalse(p.is_paused)
        self.assertTrue(p.playing)
        self.assertTrue(self._wait(lambda: p.position() > frozen + 150), "devam edince zaman ilerlemeli")

    def test_failed_pause_command_does_not_change_state(self) -> None:
        p = self._player()
        p.play(str(self.v1))
        self.assertTrue(self._wait(lambda: self.events["started"]))
        with mock.patch("app.ui.player.mpv_set", return_value=False):
            self.assertFalse(p.pause())
        self.assertTrue(p.playing, "komut başarısızsa oynatıcı 'oynuyor' kalmalı")
        self.assertFalse(p.is_paused)
        self.assertEqual(self.events["paused"], [])

    def test_missing_file_fails_fast_not_after_8s_timeout(self) -> None:
        p = self._player()
        t0 = time.monotonic()
        p.play(str(self.missing))
        self.assertTrue(self._wait(lambda: self.events["failed"], timeout=3.0), "hata bildirilmedi")
        elapsed = self.events["failed"][0][0] - t0
        self.assertLess(elapsed, 3.0, "hata 8 sn'lik zaman aşımını beklemeden gelmeli")
        self.assertIn("açılamadı", self.events["failed"][0][1])
        self.assertFalse(p.opening)
        self.assertFalse(p.playing)

    def test_corrupt_file_reports_format_problem(self) -> None:
        p = self._player()
        p.play(str(self.corrupt))
        self.assertTrue(self._wait(lambda: self.events["failed"], timeout=3.0))
        self.assertIn("biçimi", self.events["failed"][0][1])

    def test_stale_events_from_previous_file_are_ignored(self) -> None:
        p = self._player()
        p.play(str(self.v1))
        self.assertTrue(self._wait(lambda: self.events["started"]))
        old_id = p._entry_id
        self.assertIsNotNone(old_id, "mpv playlist_entry_id okunabilmeli")
        p.play(str(self.v2))
        self.assertTrue(p.opening)
        self.assertNotEqual(p._entry_id, old_id)
        # Eski dosyanın geç gelen olayları (kuyrukta bekleyen), yeni oturumu bitirmemeli/başlatmamalı
        p._on_mpv_started(old_id)
        p._on_mpv_eof(old_id)
        p._on_mpv_error(old_id, -13)
        self.assertTrue(p.opening, "eski oturum olayı yeni açılışı etkilememeli")
        self.assertEqual(self.events["finished"], [])
        self.assertEqual(self.events["failed"], [])
        # Yeni dosyanın gerçek olayı gelince normal başlar
        self.assertTrue(self._wait(lambda: len(self.events["started"]) >= 2))
        self.assertTrue(p.playing)

    def test_mpv_options_are_all_accepted_by_libmpv(self) -> None:
        """Bir seçenek adı yanlış/kaldırılmışsa MPV() hata verir ve uygulama sessizce OpenCV'ye düşerdi."""
        from app.ui.player import mpv_options

        opts = dict(mpv_options(None))
        opts.update(vo="null", ao="null")
        opts.pop("wid", None)
        m = self.mpv_mod.MPV(log_handler=lambda *a: None, **opts)
        try:
            self.assertEqual(m._get_property("video-sync"), "audio")
        finally:
            m.terminate()

    def test_shutdown_is_fast_and_unobserves(self) -> None:
        p = self._player()
        p.play(str(self.v1))
        self.assertTrue(self._wait(lambda: self.events["started"]))
        self.assertTrue(p._observers)
        t0 = time.monotonic()
        p.shutdown()
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertEqual(p._observers, [])
        self.assertIsNone(p._mpv)


if __name__ == "__main__":
    unittest.main()
