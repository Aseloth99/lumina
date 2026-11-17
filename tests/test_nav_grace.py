"""Oklarla gezinirken kendiliğinden oynayan video okları "yutmasın".

Otomatik oynatma açıkken oklarla bir videoya gelinince video hemen oynar; eskiden bundan sonra ← / → videoyu
±5 sn sarıyordu, klasörde ilerlemek için önce videoyu durdurmak gerekiyordu. Artık gezinerek gelinen video
oynamaya başladıktan sonraki ilk 3 saniye oklar gezinmeyi sürdürür (basılı tutulan tuş videolar arasında
akar). Kullanıcı videoyla bilerek ilgilenirse (oynat/duraklat, zaman çubuğu, sarma, kare, hız, ses) pencere
hemen kapanır. Doğrudan açılan dosyada (Gezgin, Dosya Aç) pencere yoktur: → baştan sarar.
"""

from __future__ import annotations

import os
import pathlib
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtGui import QImage, QShortcut  # noqa: E402

from test_video_audit import _WindowCase  # noqa: E402


class _GraceCase(_WindowCase):
    def setUp(self) -> None:
        super().setUp()
        w = self.win
        self.plays: list[str] = []
        self.skips: list[int] = []
        for target, attr, effect in (
            (w, "_autoplay_enabled", lambda: True),
            (w.player, "play", lambda path, loop=None: self.plays.append(path)),
            (w.player, "skip", lambda ms: self.skips.append(ms)),
            (w.player, "pause", lambda: True),
            (w.player, "step_frame", lambda d: True),
        ):
            patcher = mock.patch.object(target, attr, side_effect=effect)
            patcher.start()
            self.addCleanup(patcher.stop)
        w.image_files = [self.photo, self.v1, self.v2]
        w._folder_ready = True

    def _open_photo_directly(self) -> None:
        w = self.win
        w.current_idx = 0
        w.display_current()
        w.current_match = None  # fotoğrafın Live eşi yok

    def _browse_to_first_video(self, started: bool = True) -> None:
        w = self.win
        self._open_photo_directly()
        w.next_image()
        self.assertEqual(w.current_file(), self.v1)
        self.assertEqual(self.plays[-1], str(self.v1), "otomatik oynatma")
        if started:
            w._on_playback_started()

    def _current(self) -> pathlib.Path:
        return self.win.current_file()


class ArrowsKeepBrowsingTest(_GraceCase):
    def test_arrows_move_on_right_after_arriving_at_a_playing_video(self) -> None:
        w = self.win
        self._browse_to_first_video()
        self.assertTrue(w._video_can_seek(), "video oynuyor: eskiden ok burada sarardı")
        w._nav_or_seek(1, 5000)
        self.assertEqual(self._current(), self.v2)
        self.assertEqual(self.skips, [])

    def test_left_goes_back_too(self) -> None:
        w = self.win
        self._browse_to_first_video()
        w._nav_or_seek(-1, 5000)
        self.assertEqual(self._current(), self.photo)
        self.assertEqual(self.skips, [])

    def test_holding_the_key_flows_through_videos_and_stops_at_the_end(self) -> None:
        w = self.win
        self._browse_to_first_video(started=False)
        w._nav_or_seek(1, 5000)  # v1 daha açılırken bile
        self.assertEqual(self._current(), self.v2)
        self.assertTrue(w._nav_grace, "her gezinmede pencere yeniden açılır")
        w._on_playback_started()
        w._nav_or_seek(1, 5000)  # klasörün sonu: basılı tutulan tuş son videoyu sarmasın
        self.assertEqual(self._current(), self.v2)
        self.assertEqual(self.skips, [])

    def test_after_three_seconds_arrows_seek_again(self) -> None:
        from app.ui import main_window

        w = self.win
        self.assertEqual(main_window.NAV_GRACE_MS, 3000)
        self.assertEqual(w._nav_grace_timer.interval(), 3000)
        self._browse_to_first_video()
        w._nav_grace_timer.timeout.emit()
        w._nav_or_seek(1, 5000)
        self.assertEqual(self._current(), self.v1)
        self.assertEqual(self.skips, [5000])

    def test_the_three_seconds_count_from_when_playback_starts(self) -> None:
        w = self.win
        self._browse_to_first_video(started=False)
        self.assertTrue(w._nav_grace)
        self.assertFalse(w._nav_grace_timer.isActive(), "yavaş açılan dosyada açılış süresi pencereden yemesin")
        w._on_playback_started()
        self.assertTrue(w._nav_grace_timer.isActive())

    def test_deliberate_interaction_ends_the_window_at_once(self) -> None:
        w = self.win
        actions = {
            "duraklat (Boşluk / tık)": w.toggle_live_playback,
            "zaman çubuğu": w._on_seek_pressed,
            "Shift+← sarma": lambda: w._skip_video(-15000),
            "kare kare": lambda: w._step_frame(1),
            "hız": lambda: w._step_speed(1),
            "ses": lambda: w._volume_key(5),
            "sessiz": w.toggle_mute,
        }
        for name, action in actions.items():
            with self.subTest(name):
                self.plays.clear()
                self._browse_to_first_video()
                w._has_audio = True
                self.assertTrue(w._nav_grace)
                action()
                self.assertFalse(w._nav_grace, f"{name}: oklar artık sarmalı")
                self.assertFalse(w._nav_grace_timer.isActive())

    def test_live_photo_arrows_always_navigate(self) -> None:
        w = self.win
        self._open_photo_directly()
        w.current_match = mock.Mock(source="embedded", video_path=str(self.v1))
        w._playing = True  # Live Photo oynuyor (birkaç saniyelik klipte ±5 sn sarmanın anlamı yok)
        with mock.patch.object(w, "_video_can_seek", return_value=True):
            w._nav_or_seek(1, 5000)
        self.assertEqual(self._current(), self.v1)
        self.assertEqual(self.skips, [])


class NoWindowWhenNotBrowsingTest(_GraceCase):
    def test_a_directly_opened_video_seeks_right_away(self) -> None:
        w = self.win
        w.current_idx = 1
        w.display_current()  # Gezgin'den / Dosya Aç'tan açıldı
        w._on_playback_started()
        self.assertFalse(w._nav_grace)
        w._nav_or_seek(1, 5000)
        self.assertEqual(self._current(), self.v1)
        self.assertEqual(self.skips, [5000])

    def test_no_window_without_autoplay(self) -> None:
        w = self.win
        with mock.patch.object(w, "_autoplay_enabled", return_value=False):
            self._open_photo_directly()
            w.next_image()
        self.assertFalse(w._nav_grace)
        self.assertEqual(self.plays, [])
        w._nav_or_seek(1, 5000)  # oynamayan video: ok zaten gezinir
        self.assertEqual(self._current(), self.v2)

    def test_turning_autoplay_on_in_settings_does_not_open_a_window(self) -> None:
        w = self.win
        with mock.patch.object(w, "_autoplay_enabled", return_value=False):
            self._open_photo_directly()
            w.next_image()
        with mock.patch.object(w.settings, "setValue"):
            w._set_autoplay(True)
        self.assertEqual(self.plays, [str(self.v1)])
        self.assertFalse(w._nav_grace)


class KeysTest(_GraceCase):
    def _keys(self) -> set[str]:
        return {s.key().toString() for s in self.win.findChildren(QShortcut)}

    def test_always_navigate_and_audio_keys_are_bound(self) -> None:
        keys = self._keys()
        for key in ("A", "D", "PgUp", "PgDown", "Shift+Left", "Shift+Right", "M", "Up", "Down"):
            self.assertIn(key, keys)

    def test_volume_keys_change_level_and_unmute_when_raising(self) -> None:
        w = self.win
        self._browse_to_first_video()
        w._has_audio = True
        with mock.patch.object(w.player, "set_volume") as set_vol, \
             mock.patch.object(w.player, "set_muted") as set_muted, \
             mock.patch.object(type(w.player), "volume", new_callable=mock.PropertyMock, return_value=98), \
             mock.patch.object(type(w.player), "muted", new_callable=mock.PropertyMock, return_value=True):
            w._volume_key(5)
            set_muted.assert_called_once_with(False)
            set_vol.assert_called_once_with(100)
            set_muted.reset_mock()
            w._volume_key(-5)
            set_muted.assert_not_called()

    def test_audio_keys_do_nothing_on_a_plain_photo(self) -> None:
        w = self.win
        self._open_photo_directly()
        with mock.patch.object(w.player, "set_volume") as set_vol, \
             mock.patch.object(w.player, "toggle_muted") as toggle:
            w._volume_key(5)
            w._mute_key()
        set_vol.assert_not_called()
        toggle.assert_not_called()


class WindowTitleAndSlideshowTest(_GraceCase):
    def test_title_names_the_file(self) -> None:
        w = self.win
        self._browse_to_first_video()
        self.assertEqual(w.windowTitle(), f"{self.v1.name} — Lumina")
        w.image_files = []
        w.current_idx = -1
        w._sync_chrome()
        self.assertEqual(w.windowTitle(), "Lumina")

    def test_slideshow_waits_for_a_playing_video(self) -> None:
        w = self.win
        self._browse_to_first_video()
        w._slideshow_step()
        self.assertEqual(self._current(), self.v1, "oynayan video yarıda kesilmez")
        w._playing = False
        w._slideshow_step()
        self.assertEqual(self._current(), self.v2)

    def test_slideshow_does_not_reload_a_single_file(self) -> None:
        w = self.win
        w.image_files = [self.photo]
        w.current_idx = 0
        w.display_current()
        with mock.patch.object(w, "display_current") as shown:
            w._slideshow_step()
        shown.assert_not_called()


class CanvasZoomLimitsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _canvas(self, w: int, h: int):
        from app.ui.canvas import PhotoCanvas

        canvas = PhotoCanvas()
        canvas.resize(400, 300)
        canvas.show()
        self.addCleanup(canvas.close)
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(0xFF336699)
        canvas.set_qimage(img)
        QCoreApplication.processEvents()
        canvas.fit_in_window()
        return canvas

    def test_zooming_out_stops_at_fit_and_stays_fitted(self) -> None:
        canvas = self._canvas(1600, 1200)
        fit = canvas.displayed_scale()
        canvas.zoom_by(2.0)
        for _ in range(10):
            canvas.zoom_by(1 / 1.18)
        self.assertAlmostEqual(canvas.displayed_scale(), fit, delta=fit * 0.01)
        self.assertTrue(canvas._fitted, "sığdırılmış hâle döner (pencere büyüyünce yine sığar)")

    def test_small_image_can_go_down_to_actual_size_but_not_below(self) -> None:
        canvas = self._canvas(100, 75)
        self.assertGreater(canvas.displayed_scale(), 1.0, "küçük görsel pencereye büyütülür")
        for _ in range(30):
            canvas.zoom_by(1 / 1.18)
        self.assertAlmostEqual(canvas.displayed_scale(), 1.0, delta=0.01)

    def test_zooming_in_has_a_ceiling(self) -> None:
        from app.ui.canvas import MAX_PIXEL_SCALE

        canvas = self._canvas(1600, 1200)
        for _ in range(80):
            canvas.zoom_by(1.18)
        self.assertLessEqual(canvas.displayed_scale(), MAX_PIXEL_SCALE * 1.001)

    def test_wheel_factor_follows_the_delta(self) -> None:
        from app.ui.canvas import WHEEL_STEP, wheel_zoom_factor

        self.assertAlmostEqual(wheel_zoom_factor(120), WHEEL_STEP)
        self.assertAlmostEqual(wheel_zoom_factor(-120), 1 / WHEEL_STEP)
        self.assertAlmostEqual(wheel_zoom_factor(30) ** 4, WHEEL_STEP, "dokunmatik yüzeyin dört küçük parçası = bir çentik")
        self.assertEqual(wheel_zoom_factor(0), 1.0, "yatay kaydırma yakınlaştırmaz")


if __name__ == "__main__":
    unittest.main()
