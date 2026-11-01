"""Döngü, oynatma hızı ve kare kare ilerleme — GERÇEK libmpv (vo=null/ao=null) ve OpenCV yedeği.

Sentetik video 10 fps (kare = 100 ms), 10 sn.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # tests/ (_media)

from PyQt6.QtCore import QCoreApplication, QSettings  # noqa: E402
from PyQt6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from _media import DURATION_MS, make_ramp_video  # noqa: E402

FRAME_MS = 100


def _wait(predicate, timeout=6.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return predicate()


class _MediaCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._settings_tmp = tempfile.TemporaryDirectory()
        cls._media_tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._settings_tmp.name)
        cls.app = QApplication.instance() or QApplication([])
        cls.video = pathlib.Path(cls._media_tmp.name) / "clip.mp4"
        try:
            ok = make_ramp_video(cls.video)
        except Exception as exc:
            raise unittest.SkipTest(f"video üretilemedi: {exc}")
        if not ok:
            raise unittest.SkipTest("bu ortamda mp4v kodlayıcı yok")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._settings_tmp.cleanup()
        cls._media_tmp.cleanup()


class PlayerTransportTest(_MediaCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from app.core.mpv_backend import get_mpv

        cls.mpv_mod = get_mpv()
        if cls.mpv_mod is None:
            raise unittest.SkipTest("libmpv bulunamadı")

    def _player(self):
        from app.ui.player import LivePlayer

        p = LivePlayer()
        mod = self.mpv_mod
        p._new_mpv = lambda _m: mod.MPV(  # type: ignore[method-assign]
            vo="null", ao="null", keep_open="yes", osc=False, ytdl=False,
            log_handler=lambda *a: None,
        )
        self.started: list[int] = []
        self.finished: list[int] = []
        p.started.connect(lambda: self.started.append(1))
        p.finished.connect(lambda: self.finished.append(1))
        self.addCleanup(p.shutdown)
        return p

    def _playing_player(self):
        p = self._player()
        p.play(str(self.video))
        self.assertTrue(_wait(lambda: self.started))
        self.assertTrue(_wait(lambda: p.position() > 150))
        return p

    # ---------------------------------------------------------------- kare kare
    def test_step_frame_pauses_and_moves_one_frame(self) -> None:
        p = self._playing_player()
        self.assertTrue(p.step_frame(1))
        self.assertTrue(p.is_paused)
        self.assertFalse(p.playing)
        time.sleep(0.2)
        base = p.position()
        for i in (1, 2, 3):
            self.assertTrue(p.step_frame(1))
            self.assertTrue(_wait(lambda i=i: abs(p.position() - (base + i * FRAME_MS)) <= 5))
        for i in (1, 2):
            self.assertTrue(p.step_frame(-1))
            self.assertTrue(_wait(lambda i=i: abs(p.position() - (base + (3 - i) * FRAME_MS)) <= 5))

    def test_step_frame_reports_progress_while_paused(self) -> None:
        p = self._playing_player()
        p.step_frame(1)
        time.sleep(0.2)
        seen: list[int] = []
        p.progress.connect(lambda pos, _dur: seen.append(pos))
        p.step_frame(1)
        self.assertTrue(_wait(lambda: seen), "kare adımından sonra konum arayüze bildirilmeli")
        self.assertAlmostEqual(seen[-1], p.position(), delta=5)

    def test_step_frame_resumes_with_resume(self) -> None:
        p = self._playing_player()
        p.step_frame(1)
        frozen = p.position()
        self.assertTrue(p.resume())
        self.assertTrue(_wait(lambda: p.position() > frozen + 200))

    def test_step_frame_without_media_does_nothing(self) -> None:
        p = self._player()
        self.assertFalse(p.step_frame(1))
        self.assertFalse(p.step_frame(-1))

    # --------------------------------------------------------------------- hız
    def test_speed_steps_and_clamping(self) -> None:
        from app.ui.player import SPEED_STEPS

        p = self._player()
        got: list[float] = []
        p.speed_changed.connect(got.append)
        self.assertEqual(p.speed, 1.0)
        self.assertEqual(p.step_speed(1), 1.25)
        self.assertEqual(p.step_speed(-1), 1.0)
        self.assertEqual(p.step_speed(-1), 0.75)
        for _ in range(10):
            p.step_speed(-1)
        self.assertEqual(p.speed, SPEED_STEPS[0])
        for _ in range(20):
            p.step_speed(1)
        self.assertEqual(p.speed, SPEED_STEPS[-1])
        self.assertEqual(p.set_speed(99), SPEED_STEPS[-1])
        self.assertEqual(p.set_speed(0.0001), SPEED_STEPS[0])
        self.assertEqual(p.reset_speed(), 1.0)
        self.assertEqual(got[0], 1.25)
        self.assertEqual(got[-1], 1.0)

    def test_speed_really_changes_playback_rate(self) -> None:
        p = self._playing_player()
        p.set_speed(2.0)
        self.assertEqual(float(p._mpv._get_property("speed")), 2.0)
        t0, pos0 = time.monotonic(), p.position()
        time.sleep(0.8)
        advanced = (p.position() - pos0) / 1000.0
        elapsed = time.monotonic() - t0
        self.assertGreater(advanced / elapsed, 1.5, "2x hızda saat ~2 kat hızlı ilerlemeli")

    def test_speed_persists_across_files(self) -> None:
        p = self._player()
        p.set_speed(0.5)
        p.play(str(self.video))
        self.assertTrue(_wait(lambda: self.started))
        self.assertEqual(float(p._mpv._get_property("speed")), 0.5)

    # ------------------------------------------------------------------- döngü
    def _seek_near_end_fast(self, p) -> None:
        p.set_speed(4.0)
        p.seek(DURATION_MS - 600)

    def test_without_loop_playback_finishes(self) -> None:
        p = self._playing_player()
        self.assertFalse(p.loop)
        self._seek_near_end_fast(p)
        self.assertTrue(_wait(lambda: self.finished, timeout=5.0), "döngü kapalıyken video bitmeli")

    def test_loop_restarts_instead_of_finishing(self) -> None:
        p = self._playing_player()
        got: list[bool] = []
        p.loop_changed.connect(got.append)
        p.set_loop(True)
        self.assertEqual(got, [True])
        self.assertEqual(p._mpv._get_property("loop-file"), "inf")
        self._seek_near_end_fast(p)
        # Başa sarılmasını bekle: konum, atlama noktasının (9.4 sn) çok altına iner.
        self.assertTrue(_wait(lambda: p.position() < 4000, timeout=5.0), "döngüde başa sarmalı")
        time.sleep(0.3)
        self.assertEqual(self.finished, [], "döngü açıkken 'bitti' bildirilmemeli")
        self.assertTrue(p.playing)

    def test_set_loop_false_restores_normal_end(self) -> None:
        p = self._playing_player()
        p.set_loop(True)
        p.set_loop(False)
        self.assertFalse(p._mpv._get_property("loop-file"))
        self._seek_near_end_fast(p)
        self.assertTrue(_wait(lambda: self.finished, timeout=5.0))

    def test_play_keeps_loop_preference_unless_overridden(self) -> None:
        p = self._player()
        p.set_loop(True)
        p.play(str(self.video))
        self.assertTrue(_wait(lambda: self.started))
        self.assertTrue(p.loop)
        self.assertEqual(p._mpv._get_property("loop-file"), "inf")
        p.play(str(self.video), loop=False)
        self.assertFalse(p.loop)


class FallbackDecodeThreadTest(_MediaCase):
    """OpenCV yedek çözücü (libmpv yokken): kare adımı ve hız."""

    def _thread(self):
        from app.ui.player import DecodeThread

        t = DecodeThread()
        self.positions: list[int] = []
        self.ready: list[int] = []
        self.ended: list[int] = []
        t.progress.connect(lambda _g, pos, _d: self.positions.append(pos))
        t.ready.connect(lambda _g: self.ready.append(1))
        t.ended.connect(lambda _g: self.ended.append(1))
        self.addCleanup(lambda: (t.requestInterruption(), t.wait(2000)))
        return t

    def test_step_forward_and_back(self) -> None:
        t = self._thread()
        t.configure(str(self.video), False, 1)
        t.start()
        self.assertTrue(_wait(lambda: self.ready))
        t.set_paused(True)
        time.sleep(0.3)
        QCoreApplication.processEvents()
        base = self.positions[-1]
        n = len(self.positions)
        t.request_step(1)
        self.assertTrue(_wait(lambda: len(self.positions) > n))
        self.assertAlmostEqual(self.positions[-1], base + FRAME_MS, delta=15)
        n = len(self.positions)
        t.request_step(-1)
        self.assertTrue(_wait(lambda: len(self.positions) > n))
        self.assertAlmostEqual(self.positions[-1], base, delta=15)

    def test_speed_scales_wall_clock(self) -> None:
        t = self._thread()
        t.configure(str(self.video), False, 1)
        t.set_speed(4.0)
        t.start()
        self.assertTrue(_wait(lambda: self.ready))
        t.request_seek(8000)
        t0 = time.monotonic()
        self.assertTrue(_wait(lambda: self.ended, timeout=6.0))
        elapsed = time.monotonic() - t0
        # 2 sn'lik kalan video 4x'te ~0.5 sn sürer; 1x'te 2 sn sürerdi.
        self.assertLess(elapsed, 1.5)

    def test_loop_flag_can_change_while_running(self) -> None:
        t = self._thread()
        t.configure(str(self.video), False, 1)
        t.set_speed(4.0)
        t.start()
        self.assertTrue(_wait(lambda: self.ready))
        t.set_loop(True)
        t.request_seek(9000)
        time.sleep(1.0)
        QCoreApplication.processEvents()
        self.assertEqual(self.ended, [], "döngü açıkken video bitmemeli")


class MainWindowTransportTest(_MediaCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.resize(1100, 700)
        cls.win.show()
        QCoreApplication.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.close()
        cls.win.deleteLater()
        super().tearDownClass()

    def setUp(self) -> None:
        # Önceki testten/oturumdan kalan döngü tercihi sonucu etkilemesin.
        self.win.settings.remove("loop_video")
        self.win.player.set_loop(False)

    def test_controls_exist_in_seek_row(self) -> None:
        w = self.win
        row = w.bottom_dock.seek_row
        for btn in (w.btn_frame_back, w.btn_frame_fwd, w.btn_speed, w.btn_loop):
            self.assertIs(btn.parentWidget(), row)
            self.assertFalse(btn.icon().isNull() and not btn.text())
        self.assertEqual(w.btn_speed.text(), "1×")

    def test_shortcuts_are_registered(self) -> None:
        keys = {s.key().toString() for s in self.win.findChildren(QShortcut)}
        for expected in (",", ".", "Ctrl+Left", "Ctrl+Right", "[", "]", "<", ">", "Ctrl+Up", "Ctrl+Down",
                         "Backspace", "Ctrl+L"):
            self.assertIn(QKeySequence(expected).toString(), keys, expected)

    def test_controls_are_inert_without_video(self) -> None:
        w = self.win
        w.current_match = None
        before = w.player.speed
        w._step_speed(1)
        w._toggle_loop()
        w._step_frame(1)
        self.assertEqual(w.player.speed, before)
        self.assertFalse(w.player.loop)

    def test_loop_toggle_persists_and_updates_button(self) -> None:
        from app.core.matcher import LiveMatch

        w = self.win
        w.current_match = LiveMatch(str(self.video), False, "video")
        w.current_idx = max(w.current_idx, 0)
        self.addCleanup(lambda: setattr(w, "current_match", None))
        w._toggle_loop()
        self.assertTrue(w.player.loop)
        self.assertEqual(w.btn_loop.property("active"), "true")
        self.assertTrue(w._setting_bool("loop_video", False))
        w._toggle_loop()
        self.assertFalse(w.player.loop)
        self.assertEqual(w.btn_loop.property("active"), "false")
        self.assertFalse(w._setting_bool("loop_video", True))

    def test_speed_button_reflects_player_speed(self) -> None:
        from app.core.matcher import LiveMatch

        w = self.win
        w.current_match = LiveMatch(str(self.video), False, "video")
        w.current_idx = max(w.current_idx, 0)
        self.addCleanup(lambda: (w.player.reset_speed(), setattr(w, "current_match", None)))
        w._step_speed(-1)
        self.assertEqual(w.btn_speed.text(), "0.75×")
        self.assertEqual(w.btn_speed.property("active"), "true")
        w._reset_speed()
        self.assertEqual(w.btn_speed.text(), "1×")
        self.assertEqual(w.btn_speed.property("active"), "false")


if __name__ == "__main__":
    unittest.main()
