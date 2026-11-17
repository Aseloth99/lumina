"""Video bitince ok tuşları dosya değiştirir; Shift+ok ve zaman çubuğu sarar.

Bağımsız video sonuna kadar oynayınca son karesi ekranda kalır (mpv keep-open). İzleme bitti: ← / → artık
önceki/sonraki dosyaya geçer (Windows Fotoğraflar ve çoğu görüntüleyici gibi; ← de geçer, çünkü bitmiş
videoda "5 sn geri" nadiren istenir ve iki okun farklı davranması şaşırtır). Sondan geri sarmak için
Shift+← / → ya da zaman çubuğu: video oradan duraklatılmış olur, Boşluk devam ettirir, oklar yine sarar.
Döngü açıkken video hiç "bitmez", oklar sarar. Live Photo bitince fotoğrafa döndüğü için onda oklar gezinir.
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

from test_video_audit import _Media, _WindowCase, _wait  # noqa: E402


class PlayerSeeksFromTheEndTest(_Media):
    def test_finished_video_seeks_back_paused_and_plays_on(self) -> None:
        from app.core.mpv_backend import get_mpv
        from app.ui.player import LivePlayer, mpv_get

        mod = get_mpv()
        if mod is None:
            self.skipTest("libmpv yok")
        p = LivePlayer()
        p._new_mpv = lambda _m: mod.MPV(vo="null", ao="null", keep_open="yes", osc=False, ytdl=False,
                                        log_handler=lambda *a: None)
        self.addCleanup(p.shutdown)
        started: list[int] = []
        finished: list[int] = []
        p.started.connect(lambda: started.append(1))
        p.finished.connect(lambda: finished.append(1))

        self.assertFalse(p.seek_from_end(-5000), "hiçbir şey oynamamışken sarılmaz")
        p.play(str(self.v1))  # 10 sn
        self.assertTrue(_wait(lambda: started))
        self.assertFalse(p.ended)
        p.seek(9500)
        self.assertTrue(_wait(lambda: finished, timeout=10.0), "video sonuna kadar oynamalı")
        self.assertTrue(p.ended)
        self.assertFalse(p.is_paused)

        self.assertTrue(p.seek_from_end(-5000))
        self.assertFalse(p.ended)
        self.assertTrue(p.is_paused, "sarınca duraklatılmış olur (Boşluk devam ettirir)")
        self.assertTrue(mpv_get(p._mpv, "pause"))
        self.assertTrue(_wait(lambda: 4000 <= p.position() <= 6000), f"5 sn geri: {p.position()}")
        self.assertFalse(p.seek_from_end(-5000), "artık bitmiş değil: normal sarma yolu geçerli")

        finished.clear()
        self.assertTrue(p.resume())
        self.assertTrue(_wait(lambda: finished, timeout=10.0), "oradan devam edip yine bitmeli")
        self.assertTrue(p.ended)

        # Yeni dosya / durdurma "bitti" durumunu temizler.
        p.stop()
        self.assertFalse(p.ended)


class ArrowsAfterTheEndTest(_WindowCase):
    def _show_video(self) -> None:
        w = self.win
        w.image_files = [self.v1, self.v2]
        w.current_idx = 0
        w.display_current()
        self.assertIsNotNone(w._standalone_video_path(), "bağımsız video gösteriliyor")

    def _finish(self) -> None:
        """Oynatıcıyı "sonuna kadar oynadı" durumuna getir (mpv keep-open: son kare ekranda)."""
        w = self.win
        w.player._ended = True
        w.player._playing = False
        w.player._is_paused = False
        w._playing = False
        w._opening_play = False

    def test_arrows_change_file_on_a_finished_video(self) -> None:
        w = self.win
        self._show_video()
        self._finish()
        with mock.patch.object(w.player, "seek_from_end", return_value=True) as seek,              mock.patch.object(w, "next_image") as nxt, mock.patch.object(w, "prev_image") as prv:
            w._nav_or_seek(1, 5000)
            w._nav_or_seek(-1, 5000)  # ← de geçer
        seek.assert_not_called()
        nxt.assert_called_once()
        prv.assert_called_once()

    def test_shift_arrows_still_seek_a_finished_video(self) -> None:
        w = self.win
        self._show_video()
        self._finish()
        with mock.patch.object(w.player, "seek_from_end", return_value=True) as seek,              mock.patch.object(w, "next_image") as nxt, mock.patch.object(w, "prev_image") as prv:
            w._skip_video(-15000)  # Shift+←
        self.assertEqual([c.args[0] for c in seek.call_args_list], [-15000])
        nxt.assert_not_called()
        prv.assert_not_called()

    def test_seek_bar_on_a_finished_video_brings_arrow_seeking_back(self) -> None:
        w = self.win
        self._show_video()
        self._finish()
        with mock.patch.object(w.player, "_mpv", mock.Mock()),              mock.patch("app.ui.player.mpv_set", return_value=True),              mock.patch.object(w.player, "skip") as skip:
            w._on_seek_pressed()
            self.assertFalse(w.player.ended)
            self.assertTrue(w.player.is_paused, "basınca video oradan duraklatılmış olur")
            w._scrubbing = False
            skip.reset_mock()
            with mock.patch.object(w, "next_image") as nxt:
                w._nav_or_seek(1, 5000)
            nxt.assert_not_called()
            skip.assert_called_once_with(5000)

    def test_looping_video_never_counts_as_finished(self) -> None:
        w = self.win
        self._show_video()
        w.player.loop = True
        self.addCleanup(setattr, w.player, "loop", False)
        w.player._playing = True
        w._playing = True
        with mock.patch.object(w.player, "_mpv", mock.Mock()), mock.patch.object(w.player, "_entry_id", None):
            w.player._on_mpv_eof()  # döngüde sona gelmek başa sarar, "bitti" sayılmaz
        self.assertFalse(w.player.ended)
        with mock.patch.object(w.player, "skip") as skip, mock.patch.object(w, "next_image") as nxt:
            w._nav_or_seek(1, 5000)
        skip.assert_called_once_with(5000)
        nxt.assert_not_called()

    def test_arrows_still_change_file_when_nothing_was_played(self) -> None:
        w = self.win
        self._show_video()
        with mock.patch.object(w, "next_image") as nxt:
            w._nav_or_seek(1, 5000)  # oynatıcı bitmiş değil (seek_from_end False döner)
        nxt.assert_called_once()

    def test_live_photo_keeps_navigating_after_it_played(self) -> None:
        w = self.win
        self._show_video()
        w.current_match = mock.Mock(source="live", video_path=str(self.v1))
        with mock.patch.object(w.player, "seek_from_end", return_value=True) as seek, \
             mock.patch.object(w, "next_image") as nxt:
            w._nav_or_seek(1, 5000)
        seek.assert_not_called()
        nxt.assert_called_once()


if __name__ == "__main__":
    unittest.main()
