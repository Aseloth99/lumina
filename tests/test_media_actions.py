"""Bağlama duyarlı medya eylemleri: parça seçimi, kaldığın yerden devam, kare kaydı, histogram,
döndürmeyi kaydet, kırpma, taşı/kopyala, RAW önizleme geçişi. Gerçek libmpv (vo=null) + offscreen pencere."""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # tests/ (_media)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from PyQt6.QtCore import QCoreApplication, QPointF, QSettings, Qt  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from _media import make_multitrack_video, make_ramp_video  # noqa: E402

from _synthetic import sample_jpg  # noqa: E402

SAMPLE_JPG = sample_jpg()


def wait(predicate, timeout=8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return bool(predicate())


class _WindowCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._ini = tempfile.TemporaryDirectory()
        cls._work = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._ini.name)
        cls.app = QApplication.instance() or QApplication([])
        cls.work = pathlib.Path(cls._work.name)
        from app.core.mpv_backend import get_mpv

        cls.mpv_mod = get_mpv()
        from app.core.resume_store import ResumeStore
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.resize(1100, 700)
        cls.win.show()
        cls.win._resume = ResumeStore(cls.work / "resume.json")  # gerçek kullanıcı verisine yazma
        if cls.mpv_mod is not None:
            mod = cls.mpv_mod
            cls.win.player._new_mpv = lambda _m: mod.MPV(  # type: ignore[method-assign]
                vo="null", ao="null", keep_open="yes", osc=False, ytdl=False, log_handler=lambda *a: None
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.close()
        cls.win.deleteLater()
        cls._ini.cleanup()
        cls._work.cleanup()

    def _fresh(self, name: str) -> pathlib.Path:
        folder = pathlib.Path(tempfile.mkdtemp(dir=self.work))
        return folder / name

    def _open(self, path: pathlib.Path) -> None:
        w = self.win
        w.load_path(path)
        self.assertTrue(wait(lambda: w.current_file() is not None and w.current_file().name == path.name))
        QCoreApplication.processEvents()

    def _play(self, path: pathlib.Path) -> None:
        w = self.win
        self._open(path)
        w.toggle_live_playback()
        self.assertTrue(wait(lambda: w._playing), "oynatma başlamadı")


class StillActionsTest(_WindowCase):
    def test_histogram_shown_for_photo_only(self) -> None:
        w = self.win
        dst = self._fresh("h.jpg")
        dst.parent.mkdir(exist_ok=True)
        shutil.copy(SAMPLE_JPG, dst)
        self._open(dst)
        w.toggle_details()
        self.assertTrue(wait(lambda: not w.details.hist_card.isHidden()), "fotoğrafta histogram görünmeli")
        self.assertEqual(w.details.hist_view._data["r"].shape, (256,))
        w.toggle_details()

    def test_rotate_hint_then_lossless_save(self) -> None:
        from app.core import image_edit

        w = self.win
        dst = self._fresh("r.jpg")
        shutil.copy(SAMPLE_JPG, dst)
        self._open(dst)
        self.assertTrue(wait(lambda: not w.canvas.pixmap_item.pixmap().isNull()))
        self.assertFalse(w._can_save_rotation(), "döndürülmemişken kaydet seçeneği olmamalı")
        before = dst.read_bytes()
        w._rotate_current(90)
        self.assertTrue(w._toast.is_visible(), "ilk döndürmede kaydet önerisi çıkmalı")
        self.assertEqual(w._toast.text(), w.t("rotate_hint"))
        w._toast.hide()
        w._rotate_current(90)  # aynı fotoğrafta ikinci kez: öneri tekrarlanmaz
        self.assertFalse(w._toast.is_visible())
        w._rotate_current(-90)
        self.assertTrue(w._can_save_rotation())
        w._save_rotation()
        self.assertTrue(wait(lambda: image_edit.jpeg_orientation(dst) == 6), "EXIF yönü yazılmalı")
        after = dst.read_bytes()
        self.assertEqual(before[before.index(b"\xff\xda"):], after[after.index(b"\xff\xda"):], "piksel verisi değişmemeli")
        self.assertTrue(wait(lambda: w.canvas._rotation == 0), "yeniden yüklenince görünüm döndürmesi sıfırlanmalı")

    def test_crop_saves_copy_and_blocks_destructive_shortcuts(self) -> None:
        w = self.win
        dst = self._fresh("c.jpg")
        shutil.copy(SAMPLE_JPG, dst)
        self._open(dst)
        self.assertTrue(wait(lambda: not w.canvas.pixmap_item.pixmap().isNull()))
        self.assertTrue(w._can_crop())
        w._start_crop()
        self.assertTrue(w._crop_active)
        QTest.keyClick(w, Qt.Key.Key_Delete)  # kırparken dosyayı silmemeli
        QCoreApplication.processEvents()
        self.assertTrue(dst.exists())
        overlay = w._crop_overlay
        overlay._set_ratio(1.0)
        l, t, r, b = overlay.frac()
        aspect = overlay.image_aspect()
        self.assertAlmostEqual(((r - l) * aspect) / (b - t), 1.0, places=2)
        overlay.finished.emit(overlay.frac())
        self.assertFalse(w._crop_active)
        out = [p for p in dst.parent.iterdir() if "kırpılmış" in p.name]
        self.assertTrue(wait(lambda: any("kırpılmış" in p.name for p in dst.parent.iterdir())), "kırpılmış kopya yok")
        out = [p for p in dst.parent.iterdir() if "kırpılmış" in p.name]
        self.assertTrue(wait(lambda: out[0].stat().st_size > 0))
        self.assertTrue(wait(lambda: w._toast.text() != w.t("crop_saving")))
        with Image.open(out[0]) as im:
            self.assertEqual(im.width, im.height, "1:1 kırpma kare olmalı")
        self.assertTrue(dst.exists(), "özgün dosya korunmalı")

    def test_crop_cancel_creates_nothing(self) -> None:
        w = self.win
        dst = self._fresh("d.jpg")
        shutil.copy(SAMPLE_JPG, dst)
        self._open(dst)
        self.assertTrue(wait(lambda: not w.canvas.pixmap_item.pixmap().isNull()))
        w._start_crop()
        QTest.keyClick(w._crop_overlay, Qt.Key.Key_Escape)
        self.assertFalse(w._crop_active)
        self.assertEqual([p.name for p in dst.parent.iterdir()], ["d.jpg"])

    @staticmethod
    def _mouse(widget, kind, pos, buttons=Qt.MouseButton.LeftButton) -> None:
        """Fare olayını doğrudan işleyiciye ver (offscreen'de sendEvent/QTest fare grab'ini taşımaz)."""
        from PyQt6.QtGui import QMouseEvent

        move = kind == QMouseEvent.Type.MouseMove
        button = Qt.MouseButton.NoButton if move else Qt.MouseButton.LeftButton
        held = Qt.MouseButton.NoButton if kind == QMouseEvent.Type.MouseButtonRelease else buttons
        ev = QMouseEvent(kind, QPointF(pos), QPointF(widget.mapToGlobal(pos)), button, held, Qt.KeyboardModifier.NoModifier)
        handler = {
            QMouseEvent.Type.MouseButtonPress: widget.mousePressEvent,
            QMouseEvent.Type.MouseMove: widget.mouseMoveEvent,
            QMouseEvent.Type.MouseButtonRelease: widget.mouseReleaseEvent,
        }[kind]
        handler(ev)

    def _drag(self, ov, start, delta) -> None:
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QMouseEvent

        a = QPoint(int(start.x()), int(start.y()))
        b = QPoint(a.x() + int(delta[0]), a.y() + int(delta[1]))
        self._mouse(ov, QMouseEvent.Type.MouseButtonPress, a)
        self._mouse(ov, QMouseEvent.Type.MouseMove, b)
        self._mouse(ov, QMouseEvent.Type.MouseButtonRelease, b)

    def test_crop_overlay_drag_moves_and_resizes_within_image(self) -> None:
        w = self.win
        dst = self._fresh("e.jpg")
        shutil.copy(SAMPLE_JPG, dst)
        self._open(dst)
        self.assertTrue(wait(lambda: not w.canvas.pixmap_item.pixmap().isNull()))
        w._start_crop()
        ov = w._crop_overlay
        start = ov.frac()
        # sağ-alt köşeyi sol-üste doğru çek: çerçeve küçülür
        self._drag(ov, ov._rect_px().bottomRight(), (-60, -40))
        shrunk = ov.frac()
        self.assertLess(shrunk[2], start[2])
        self.assertLess(shrunk[3], start[3])
        self.assertEqual((shrunk[0], shrunk[1]), (start[0], start[1]), "karşı köşe sabit kalmalı")
        # içeriği çek: taşıma, boyut sabit
        self._drag(ov, ov._rect_px().center(), (30, 20))
        moved = ov.frac()
        self.assertAlmostEqual(moved[2] - moved[0], shrunk[2] - shrunk[0], places=3)
        self.assertGreater(moved[0], shrunk[0])
        # sınır dışına sürüklenemez
        self._drag(ov, ov._rect_px().center(), (5000, 5000))
        l, t, r, b = ov.frac()
        self.assertTrue(0.0 <= l < r <= 1.0 and 0.0 <= t < b <= 1.0)
        # oran kilitliyken köşe sürüklemek oranı korur
        ov._set_ratio(4 / 3)
        self._drag(ov, ov._rect_px().topLeft(), (25, 25))
        l, t, r, b = ov.frac()
        self.assertAlmostEqual(((r - l) * ov.image_aspect()) / (b - t), 4 / 3, places=2)
        ov.finished.emit(None)

    def test_move_and_undo(self) -> None:
        w = self.win
        src = self._fresh("m.jpg")
        shutil.copy(SAMPLE_JPG, src)
        dest_dir = pathlib.Path(tempfile.mkdtemp(dir=self.work))
        self._open(src)
        with mock.patch("app.ui.media_actions.QFileDialog.getExistingDirectory", return_value=str(dest_dir)):
            w._move_to_folder()
        self.assertFalse(src.exists())
        self.assertTrue((dest_dir / "m.jpg").exists())
        self.assertTrue(w._toast.is_visible())
        self.assertEqual(w._toast.text(), w.t("moved").format("m.jpg"))
        w._toast._on_action()  # Geri al
        self.assertTrue(src.exists())
        self.assertFalse((dest_dir / "m.jpg").exists())

    def test_copy_keeps_original_and_renames_on_collision(self) -> None:
        w = self.win
        src = self._fresh("k.jpg")
        shutil.copy(SAMPLE_JPG, src)
        dest_dir = pathlib.Path(tempfile.mkdtemp(dir=self.work))
        (dest_dir / "k.jpg").write_bytes(b"var olan")
        self._open(src)
        with mock.patch("app.ui.media_actions.QFileDialog.getExistingDirectory", return_value=str(dest_dir)):
            w._copy_to_folder()
        self.assertTrue(src.exists())
        self.assertEqual((dest_dir / "k.jpg").read_bytes(), b"var olan", "var olan dosya ezilmemeli")
        self.assertTrue((dest_dir / "k (2).jpg").exists())

    def test_hint_not_offered_for_unsupported_format(self) -> None:
        w = self.win
        dst = self._fresh("x.jpg")
        shutil.copy(SAMPLE_JPG, dst)
        self._open(dst)
        w._toast.hide()
        with mock.patch("app.core.image_edit.can_rotate_save", return_value=False):
            w._rotate_current(90)
        self.assertFalse(w._toast.is_visible())


@unittest.skipIf(make_multitrack_video is None, "ffmpeg yok")
class VideoActionsTest(_WindowCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if cls.mpv_mod is None:
            raise unittest.SkipTest("libmpv yok")
        folder = cls.work / "vid"
        folder.mkdir()
        cls.multi = folder / "multi.mkv"
        if not make_multitrack_video(cls.multi):
            raise unittest.SkipTest("ffmpeg ile çok parçalı video üretilemedi")
        cls.ramp = folder / "ramp.mp4"
        if not make_ramp_video(cls.ramp):
            raise unittest.SkipTest("mp4v kodlayıcı yok")

    def test_tracks_button_appears_only_when_there_is_something_to_choose(self) -> None:
        w = self.win
        self._play(self.ramp)  # tek akış, ses/altyazı yok
        self.assertTrue(wait(lambda: w._playing))
        self.assertTrue(w.btn_tracks.isHidden(), "seçilecek parça yokken düğme gizli olmalı")
        w._stop_playback()
        self._play(self.multi)
        self.assertTrue(wait(lambda: not w.btn_tracks.isHidden()), "2 ses + altyazı varken düğme görünmeli")
        w._stop_playback()
        self.assertTrue(w.btn_tracks.isHidden(), "durunca düğme gizlenmeli")

    def test_cycle_subtitle_and_audio_report_what_changed(self) -> None:
        w = self.win
        self._play(self.multi)
        self.assertTrue(wait(lambda: len(w.player.track_lists()[1]) == 2))
        audio, subs = w.player.track_lists()
        self.assertEqual(len(audio), 2)
        self.assertEqual(w.player.selected_track_id(subs), 1)
        w._cycle_subtitle()  # 1 -> 2
        self.assertEqual(w._toast.text(), w.t("subtitle_now").format("İngilizce · SRT"))
        self.assertTrue(wait(lambda: w.player.selected_track_id(w.player.track_lists()[1]) == 2))
        w._cycle_subtitle()  # 2 -> kapalı
        self.assertEqual(w._toast.text(), w.t("subtitle_now").format(w.t("subtitle_off")))
        self.assertTrue(wait(lambda: w.player.selected_track_id(w.player.track_lists()[1]) is None))
        w._cycle_subtitle()  # kapalı -> 1
        self.assertTrue(wait(lambda: w.player.selected_track_id(w.player.track_lists()[1]) == 1))
        w._cycle_audio()
        self.assertEqual(w._toast.text(), w.t("audio_now").format("İngilizce · AAC Mono"))
        self.assertTrue(wait(lambda: w.player.selected_track_id(w.player.track_lists()[0]) == 2))
        w._stop_playback()

    def test_external_subtitle_file_is_added_and_selected(self) -> None:
        w = self.win
        self._play(self.ramp)
        srt = self.ramp.parent / "dis.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:03,000\nDis altyazi\n", encoding="utf-8")
        changed: list[int] = []
        w.player.tracks_changed.connect(lambda: changed.append(1))
        self.assertTrue(w.player.add_subtitle_file(str(srt)))
        self.assertTrue(wait(lambda: any(t.get("external") for t in w.player.track_lists()[1])))
        self.assertTrue(changed)
        self.assertTrue(wait(lambda: not w.btn_tracks.isHidden()), "altyazı eklenince düğme görünmeli")
        w._stop_playback()

    def test_save_frame_writes_png_next_to_video(self) -> None:
        w = self.win
        self._play(self.multi)
        self.assertTrue(wait(lambda: w.player.position() > 300))
        w._save_frame()
        pngs = [p for p in self.multi.parent.glob("multi_*.png")]
        self.assertEqual(len(pngs), 1, pngs)
        with Image.open(pngs[0]) as im:
            self.assertEqual(im.size, (320, 180), "özgün çözünürlük")
            self.assertEqual(im.format, "PNG")
        self.assertEqual(w._toast.text(), w.t("frame_saved").format(pngs[0].name))
        w._save_frame()  # aynı anda ikinci kayıt: çakışmadan yeni ad
        self.assertTrue(wait(lambda: len(list(self.multi.parent.glob("multi_*.png"))) == 2))
        w._stop_playback()

    def test_save_frame_ignored_without_playback(self) -> None:
        w = self.win
        self._open(self.ramp)
        before = set(self.ramp.parent.glob("*.png"))
        w._save_frame()
        self.assertEqual(set(self.ramp.parent.glob("*.png")), before)

    def test_save_frame_opencv_fallback(self) -> None:
        w = self.win
        self._play(self.ramp)
        self.assertTrue(wait(lambda: w.player.position() > 300))
        dest = self.ramp.parent / "fallback.png"
        with mock.patch.object(w.player._mpv, "command", side_effect=RuntimeError("mpv yok")):
            self.assertTrue(w.player.save_frame(dest))
        with Image.open(dest) as im:
            self.assertEqual(im.size, (320, 180))
        w._stop_playback()

    def test_resume_remembers_long_video_position_and_offers_restart(self) -> None:
        from app.core import resume_store

        w = self.win
        w._resume.clear(self.ramp)
        with mock.patch.multiple(
            resume_store, MIN_DURATION_MS=5000, MIN_POSITION_MS=1500, END_MARGIN_MS=1500
        ):
            self._play(self.ramp)  # 10 sn'lik video: "uzun" sayılır
            self.assertTrue(wait(lambda: w.player.duration() > 9000))
            w.player.seek(6000)
            self.assertTrue(wait(lambda: w.player.position() >= 5900))
            w.toggle_live_playback()  # duraklat -> kaydet
            self.assertTrue(wait(lambda: w.player.is_paused))
            self.assertAlmostEqual(w._resume.get(self.ramp), 6000, delta=400)
            w._stop_playback()
            w._toast.hide()

            w.toggle_live_playback()  # yeniden oynat: kaldığı yerden
            self.assertTrue(wait(lambda: w._playing))
            self.assertTrue(wait(lambda: w.player.position() >= 5500), "kaldığı yerden devam etmeli")
            self.assertTrue(w._toast.is_visible())
            self.assertIn("0:0", w._toast.text())
            # 'Kaldığın yerden devam' altta durur: kapsülün üstü sahnenin ortasından aşağıda
            stage_mid = w.stage.mapToGlobal(w.stage.rect().center()).y()
            self.assertGreater(w._toast.host.geometry().top(), stage_mid, "devam kapsülü altta olmalı")
            w._toast._on_action()  # Baştan
            self.assertTrue(wait(lambda: w.player.position() < 2000))
            w._stop_playback()

    def test_resume_disabled_setting_and_short_video(self) -> None:
        w = self.win
        w._resume.clear(self.ramp)
        w._set_resume_enabled(False)
        self.addCleanup(lambda: w._set_resume_enabled(True))
        self._play(self.ramp)
        self.assertTrue(wait(lambda: w.player.duration() > 9000))
        w.player.seek(6000)
        w.toggle_live_playback()
        self.assertIsNone(w._resume.get(self.ramp))
        w._stop_playback()

    def test_finishing_a_video_clears_resume_point(self) -> None:
        from app.core import resume_store

        w = self.win
        with mock.patch.multiple(resume_store, MIN_DURATION_MS=5000, MIN_POSITION_MS=1500, END_MARGIN_MS=1500):
            w._resume.update(self.ramp, 6000, 10_000)
            self.assertIsNotNone(w._resume.get(self.ramp))
            self._play(self.ramp)
            w.player.set_speed(4.0)
            w.player.seek(9300)
            self.assertTrue(wait(lambda: not w._playing, timeout=6.0), "video bitmeli")
            self.assertIsNone(w._resume.get(self.ramp))
            # Bağımsız video bitince son kare kalır: ilk kareyi gösteren fotoğraf tuvaline dönülmez
            self.assertIs(w.stage.stack.currentWidget(), w.player.widget)
        w.player.reset_speed()


class OfferRulesTest(unittest.TestCase):
    """Menü/öneri kuralları pencere kurmadan: mixin yöntemleri sahte bir 'pencere' ile çağrılır."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def _stub(self, path: pathlib.Path, rotation: int = 0, lang: str = "tr"):
        from types import SimpleNamespace

        from app.resources.locales import t

        pixmap = SimpleNamespace(isNull=lambda: False)
        return SimpleNamespace(
            current_file=lambda: path,
            _playing=False,
            _crop_active=False,
            player=SimpleNamespace(is_paused=False),
            canvas=SimpleNamespace(_rotation=rotation, pixmap_item=SimpleNamespace(pixmap=lambda: pixmap)),
            lang=lang,
            t=lambda key: t(key, lang),
            _toast=SimpleNamespace(hide=lambda: None),
        )

    def test_rotation_save_not_offered_for_animated_or_deep_images(self) -> None:
        from app.ui.media_actions import MediaActionsMixin

        frames = [Image.new("RGB", (30, 20), c) for c in ("red", "green")]
        anim = self.dir / "anim.webp"
        frames[0].save(anim, save_all=True, append_images=frames[1:], duration=100, loop=0)
        deep = self.dir / "deep.png"
        Image.fromarray(np.full((20, 30), 40000, dtype=np.uint16)).save(deep)
        still = self.dir / "still.png"
        frames[0].save(still)
        for path, offered in ((anim, False), (deep, False), (still, True)):
            self.assertEqual(MediaActionsMixin._can_save_rotation(self._stub(path, rotation=90)), offered, path.name)

    def test_crop_not_offered_for_livp(self) -> None:
        import zipfile

        from app.ui.media_actions import MediaActionsMixin

        livp = self.dir / "IMG_0001.livp"
        with zipfile.ZipFile(livp, "w") as z:
            z.writestr("IMG_0001.JPEG", SAMPLE_JPG.read_bytes())
            z.writestr("IMG_0001.MOV", b"\x00" * 64)
        jpg = self.dir / "a.jpg"
        Image.new("RGB", (30, 20)).save(jpg)
        self.assertFalse(MediaActionsMixin._can_crop(self._stub(livp)), ".livp kırpılamaz (arşiv)")
        self.assertTrue(MediaActionsMixin._can_crop(self._stub(jpg)))

    def test_edit_errors_are_shown_in_the_ui_language(self) -> None:
        from app.ui.media_actions import MediaActionsMixin

        heic = self.dir / "x.heic"
        heic.write_bytes(b"0")
        from app.core import image_edit

        with self.assertRaises(ValueError) as ctx:
            image_edit.rotate_and_save(heic, 90)
        with mock.patch("app.ui.media_actions.MessageBox.warning") as warn:
            MediaActionsMixin._on_rotation_saved(self._stub(heic, lang="en"), str(ctx.exception), 1, heic)
            MediaActionsMixin._on_crop_saved(self._stub(heic, lang="en"), image_edit.UNSAFE_REENCODE, heic)
        rotate_text, crop_text = (c.args[2] for c in warn.call_args_list)
        self.assertEqual(rotate_text, "Could not save the rotation:\nSaving the rotation is not supported for .heic")
        self.assertIn("the rotation was not saved", crop_text)


class RawPreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _qimage(self, w: int, h: int, color) -> QImage:
        q = QImage(w, h, QImage.Format.Format_RGB32)
        q.fill(color)
        return q

    def test_worker_emits_preview_then_upgraded_for_raw(self) -> None:
        from app.core import image_loader

        events: list[tuple[str, int]] = []
        worker = image_loader.ImageLoadWorker("C:/x/foto.CR2", token=5)
        worker.signals.loaded.connect(lambda p, q, w, h, t: events.append(("loaded", w)))
        worker.signals.upgraded.connect(lambda p, q, w, h, t: events.append(("upgraded", w)))
        preview = Image.new("RGB", (1200, 800), "red")
        full = Image.new("RGB", (6000, 4000), "blue")
        with mock.patch.object(image_loader, "extract_raw_preview", return_value=preview), \
             mock.patch.object(image_loader, "load_pil", return_value=full), \
             mock.patch.object(image_loader, "get_cached_image", return_value=None), \
             mock.patch.object(image_loader, "put_cached_image"):
            worker.run()
        QCoreApplication.processEvents()
        self.assertEqual(events, [("loaded", 1200), ("upgraded", 6000)])

    def test_worker_without_preview_emits_single_loaded(self) -> None:
        from app.core import image_loader

        events: list[str] = []
        worker = image_loader.ImageLoadWorker("C:/x/foto.CR2", token=5)
        worker.signals.loaded.connect(lambda *a: events.append("loaded"))
        worker.signals.upgraded.connect(lambda *a: events.append("upgraded"))
        with mock.patch.object(image_loader, "extract_raw_preview", return_value=None), \
             mock.patch.object(image_loader, "load_pil", return_value=Image.new("RGB", (50, 50))), \
             mock.patch.object(image_loader, "get_cached_image", return_value=None), \
             mock.patch.object(image_loader, "put_cached_image"):
            worker.run()
        QCoreApplication.processEvents()
        self.assertEqual(events, ["loaded"])

    def test_preload_token_skips_preview_extraction(self) -> None:
        from app.core import image_loader

        worker = image_loader.ImageLoadWorker("C:/x/foto.CR2", token=-1)
        with mock.patch.object(image_loader, "extract_raw_preview") as ex, \
             mock.patch.object(image_loader, "load_pil", return_value=Image.new("RGB", (10, 10))), \
             mock.patch.object(image_loader, "get_cached_image", return_value=None), \
             mock.patch.object(image_loader, "put_cached_image"):
            worker.run()
        ex.assert_not_called()

    def test_extract_raw_preview_is_safe_on_non_raw(self) -> None:
        from app.core import image_loader

        self.assertIsNone(image_loader.extract_raw_preview(SAMPLE_JPG))
        self.assertIsNone(image_loader.extract_raw_preview(pathlib.Path("C:/yok/yok.dng")))

    def test_canvas_replace_image_refits_when_fitted_and_keeps_zoom_otherwise(self) -> None:
        from app.ui.canvas import PhotoCanvas

        canvas = PhotoCanvas()
        canvas.resize(400, 300)
        canvas.show()
        canvas.set_qimage(self._qimage(200, 100, Qt.GlobalColor.red))
        QCoreApplication.processEvents()
        fitted_scale = canvas.displayed_scale()
        canvas.replace_image(self._qimage(800, 400, Qt.GlobalColor.blue))
        self.assertTrue(canvas._fitted)
        self.assertAlmostEqual(canvas.displayed_scale() * 800, fitted_scale * 200, delta=2.0)

        canvas.set_qimage(self._qimage(200, 100, Qt.GlobalColor.red))
        canvas.zoom_by(2.0)
        zoomed_scale = canvas.displayed_scale()
        self.assertFalse(canvas._fitted)
        canvas.replace_image(self._qimage(800, 400, Qt.GlobalColor.blue))
        self.assertFalse(canvas._fitted, "yakınlaştırılmış görünüm korunmalı")
        # aynı görsel boyut: yeni görüntü 4x büyük -> ölçek 4x küçük
        self.assertAlmostEqual(canvas.displayed_scale() * 4, zoomed_scale, delta=zoomed_scale * 0.05)

    def test_canvas_replace_image_keeps_rotation(self) -> None:
        from app.ui.canvas import PhotoCanvas

        canvas = PhotoCanvas()
        canvas.resize(400, 300)
        canvas.show()
        canvas.set_qimage(self._qimage(200, 100, Qt.GlobalColor.red))
        canvas.rotate_step(90)
        canvas.replace_image(self._qimage(800, 400, Qt.GlobalColor.blue))
        self.assertEqual(canvas._rotation, 90)


class FlipMappingTest(unittest.TestCase):
    def test_raw_preview_orientation_when_exif_missing(self) -> None:
        from app.core import image_loader

        class _Sizes:
            flip = 6

        class _Thumb:
            format = None
            data = None

        arr = np.zeros((900, 1200, 3), np.uint8)
        thumb = _Thumb()
        thumb.format = image_loader.rawpy.ThumbFormat.BITMAP if image_loader.HAS_RAWPY else None
        thumb.data = arr

        class _Raw:
            sizes = _Sizes()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_thumb(self):
                return thumb

        if not image_loader.HAS_RAWPY:
            self.skipTest("rawpy yok")
        with mock.patch.object(image_loader.rawpy, "imread", return_value=_Raw()):
            img = image_loader.extract_raw_preview(pathlib.Path("x.dng"))
        self.assertEqual(img.size, (900, 1200), "flip=6 (90° sağa) -> dikey")

    def test_small_embedded_preview_is_ignored(self) -> None:
        from app.core import image_loader

        if not image_loader.HAS_RAWPY:
            self.skipTest("rawpy yok")

        class _Thumb:
            format = image_loader.rawpy.ThumbFormat.BITMAP
            data = np.zeros((120, 160, 3), np.uint8)

        class _Raw:
            class sizes:  # noqa: N801
                flip = 0

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_thumb(self):
                return _Thumb()

        with mock.patch.object(image_loader.rawpy, "imread", return_value=_Raw()):
            self.assertIsNone(image_loader.extract_raw_preview(pathlib.Path("x.dng")))


if __name__ == "__main__":
    unittest.main()
