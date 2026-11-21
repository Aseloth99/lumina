"""Küçük cilalar: Farklı Kaydet, "kopyalandı" bilgisi, silmede Geri Al, ekran üstü bilgi (OSD), fotoğraf geçişi,
yakınlaştırma yüzdesi, galeri küçük resim belirmesi, tam ekran kapsül solması, Son Açılanlar, sürükle-bırak vurgusu
ve menülerden sonra takılı kalan hover.

Dosya işlemleri yalnızca geçici dizinde yapılır; geri dönüşüm kutusuna ve kayıt defterine dokunulmaz.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QEvent, QMimeData, QPoint, QPointF, QSettings, Qt, QTimer, QUrl  # noqa: E402
from PyQt6.QtGui import QColor, QDragEnterEvent, QDragLeaveEvent, QDropEvent, QImage, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMenu, QWidget  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from _synthetic import sample_jpg  # noqa: E402

SAMPLE = sample_jpg()


def _wait(ms: int) -> None:
    end = time.monotonic() + ms / 1000.0
    app = QApplication.instance()
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def _wait_until(cond, ms: int = 2000) -> bool:
    end = time.monotonic() + ms / 1000.0
    app = QApplication.instance()
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return cond()


def _pix(w: int, h: int, color: str) -> QPixmap:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    return QPixmap.fromImage(img)


def _anims(on: bool):
    """Windows "Animasyonları göster" ayarının iki hâli (tüm modüller aynı kaynaktan okur)."""
    return mock.patch("app.ui.fade._system_animations_enabled", return_value=on)


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._settings_tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._settings_tmp.name)
        cls.app = QApplication.instance() or QApplication([])


class LocaleKeysTest(_Base):
    def test_new_keys_exist_in_both_languages(self) -> None:
        from app.resources.locales import LOCALES

        keys = (
            "copied_file", "copied_image", "copied_path", "copied_name", "deleted_recycle", "deleted_permanent",
            "saved_as", "save_as_encode_fail", "osd_seek", "osd_speed", "osd_zoom", "recent", "recent_clear",
            "drop_hint",
        )
        for lang in ("tr", "en"):
            for key in keys:
                self.assertTrue(LOCALES[lang].get(key), f"{lang}:{key}")
        self.assertEqual(LOCALES["tr"]["osd_zoom"].format(150), "%150")
        self.assertEqual(LOCALES["en"]["osd_zoom"].format(150), "150%")


class ComboArrowThemeTest(_Base):
    def test_combo_boxes_get_a_themed_flat_arrow(self) -> None:
        """Stil sayfalı açılır kutuda Windows stili oku eski kabartmalı kutu olarak (koyu temada siyah) çiziyordu."""
        from app.resources import theme
        from app.ui import message_box

        for name in ("dark", "light"):
            pal = theme.palette(name)
            qss = theme.dialog_stylesheet(name)
            self.assertIn("QComboBox#card_control::drop-down", qss)
            self.assertIn("QComboBox#card_control::down-arrow", qss)
            path = theme.combo_arrow_path(pal["text_muted"])
            img = QImage(path)
            self.assertFalse(img.isNull(), path)
            self.assertEqual(img.pixelColor(16, 20).name(), QColor(pal["text_muted"]).name())
            self.assertEqual(img.pixelColor(2, 2).alpha(), 0)
        self.assertIn("QComboBox#mb_input::down-arrow", message_box._qss(message_box.current_palette()))


class WindowFaderTest(_Base):
    def _window(self) -> QWidget:
        w = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        w.resize(60, 30)
        self.addCleanup(w.deleteLater)
        return w

    def test_instant_when_system_animations_are_off(self) -> None:
        from app.ui.fade import WindowFader

        w = self._window()
        fader = WindowFader(w)
        with _anims(False):
            fader.fade_in()
            self.assertTrue(w.isVisible())
            self.assertFalse(fader.is_running())
            fader.fade_out()
            self.assertFalse(w.isVisible())
            self.assertFalse(fader.is_running())
        self.assertAlmostEqual(w.windowOpacity(), 1.0, places=2)

    def test_fades_then_hides_and_restores_opacity(self) -> None:
        from app.ui.fade import WindowFader

        w = self._window()
        fader = WindowFader(w, fade_in_ms=60, fade_out_ms=60)
        with _anims(True):
            fader.fade_in()
            self.assertTrue(w.isVisible())
            self.assertTrue(fader.is_running())
            self.assertTrue(_wait_until(lambda: not fader.is_running(), 1500))
            fader.fade_out()
            self.assertTrue(fader.is_fading_out())
            self.assertTrue(w.isVisible(), "kaybolurken pencere hâlâ görünür")
            self.assertTrue(_wait_until(lambda: not w.isVisible(), 1500))
        self.assertAlmostEqual(w.windowOpacity(), 1.0, places=2, msg="sonraki show() tam görünür açmalı")

    def test_fade_in_reverses_a_running_fade_out(self) -> None:
        from app.ui.fade import WindowFader

        w = self._window()
        fader = WindowFader(w, fade_in_ms=400, fade_out_ms=400)
        with _anims(True):
            fader.stop()
            w.show()
            fader.fade_out()
            _wait(60)
            fader.fade_in()
            self.assertFalse(fader.is_fading_out())
            _wait(500)
            self.assertTrue(w.isVisible())


class OsdWidgetTest(_Base):
    def test_osd_is_click_through_and_disappears(self) -> None:
        from PyQt6.QtCore import QRect

        from app.ui.osd import Osd

        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        osd = Osd(owner)
        host = osd.host
        self.assertTrue(bool(host.windowFlags() & Qt.WindowType.WindowTransparentForInput))
        self.assertTrue(host.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        with _anims(False):
            osd.show_text("+5 sn", QRect(0, 0, 800, 600), msec=200)
            self.assertTrue(osd.is_visible())
            self.assertEqual(osd.text(), "+5 sn")
            g = host.geometry()
            self.assertLess(abs(g.center().x() - 400), 4, "sahnenin ortasında")
            self.assertGreaterEqual(g.height(), 36)
            self.assertTrue(_wait_until(lambda: not host.isVisible(), 1500))

    def test_drop_highlight_show_and_hide(self) -> None:
        from PyQt6.QtCore import QRect

        from app.ui.osd import DropHighlight

        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        hl = DropHighlight(owner)
        with _anims(False):
            hl.show_over(QRect(10, 10, 300, 200), "Açmak için bırakın")
            self.assertTrue(hl.is_shown())
            self.assertEqual(hl.host.geometry(), QRect(10, 10, 300, 200))
            img = hl.host.grab().toImage()
            self.assertFalse(img.isNull())
            hl.hide()
            self.assertFalse(hl.is_shown())
            self.assertFalse(hl.host.isVisible())


class CanvasPolishTest(_Base):
    def setUp(self) -> None:
        from app.ui.canvas import PhotoCanvas

        self.canvas = PhotoCanvas()
        self.canvas.resize(400, 300)
        self.canvas.show()
        self.addCleanup(self.canvas.deleteLater)
        self.app.processEvents()

    def test_crossfade_on_navigation_only_when_animations_on(self) -> None:
        c = self.canvas
        c.set_pixmap(_pix(200, 150, "#ff0000"))
        with _anims(True):
            c.set_pixmap(_pix(200, 150, "#00ff00"), crossfade=True)
            self.assertTrue(c.is_crossfading())
            self.assertTrue(_wait_until(lambda: not c.is_crossfading(), 1500))
        with _anims(False):
            c.set_pixmap(_pix(200, 150, "#0000ff"), crossfade=True)
            self.assertFalse(c.is_crossfading())
        with _anims(True):
            c.set_pixmap(_pix(200, 150, "#ffffff"))  # açıkça istenmeyince geçiş yok (ilk açılış, yenileme)
            self.assertFalse(c.is_crossfading())

    def test_crossfade_skipped_for_huge_images(self) -> None:
        c = self.canvas
        c.set_pixmap(_pix(200, 150, "#ff0000"))
        with _anims(True), mock.patch("app.ui.canvas.CROSSFADE_MAX_PIXELS", 1000):
            c.set_pixmap(_pix(200, 150, "#00ff00"), crossfade=True)
            self.assertFalse(c.is_crossfading())

    def test_clear_image_ends_crossfade(self) -> None:
        c = self.canvas
        c.set_pixmap(_pix(200, 150, "#ff0000"))
        with _anims(True):
            c.set_pixmap(_pix(200, 150, "#00ff00"), crossfade=True)
            c.clear_image()
            self.assertFalse(c.is_crossfading())

    def test_zoom_percent(self) -> None:
        c = self.canvas
        c.set_pixmap(_pix(800, 600, "#808080"))
        c.reset_view_to_actual()
        self.assertEqual(c.zoom_percent(), 100)
        c.zoom_by(2.0)
        self.assertEqual(c.zoom_percent(), 200)


class GalleryFadeTest(_Base):
    def test_new_thumbnail_fades_in_only_with_animations(self) -> None:
        from app.ui.gallery import THUMB_FADE_MS, GalleryView, ThumbnailProvider

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        files = []
        for i in range(3):
            dest = pathlib.Path(tmp.name) / f"{i}.jpg"
            shutil.copy(SAMPLE, dest)
            files.append(dest)
        provider = ThumbnailProvider(cache_dir=pathlib.Path(tmp.name) / "cache", threads=1)
        view = GalleryView(provider)
        view.resize(600, 400)
        view.show()
        self.addCleanup(provider.shutdown)
        self.addCleanup(view.shutdown)
        view.set_files(files, 0)
        self.app.processEvents()
        key = str(files[0])
        with _anims(False):
            view._on_thumb_ready(key)
            self.assertFalse(view.is_fading())
            self.assertEqual(view.thumb_opacity(key), 1.0)
        with _anims(True):
            view._on_thumb_ready(key)
            self.assertTrue(view.is_fading())
            self.assertLess(view.thumb_opacity(key), 0.5)
            self.assertTrue(_wait_until(lambda: not view.is_fading(), THUMB_FADE_MS + 1500))
            self.assertEqual(view.thumb_opacity(key), 1.0)
        view.grab()  # boyama yolu (belirme dahil) hatasız


class HoverLeaveTest(_Base):
    def test_settle_hover_sends_hover_leave_to_item_views(self) -> None:
        from PyQt6.QtGui import QCursor

        from app.ui.hover_fix import settle_hover

        seen: list[QEvent.Type] = []

        class Probe(QWidget):
            def event(self, ev):  # noqa: N802
                seen.append(ev.type())
                return super().event(ev)

        w = Probe()
        w.resize(50, 50)
        w.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        w.show()
        self.addCleanup(w.deleteLater)
        self.app.processEvents()
        w.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
        with mock.patch.object(QCursor, "pos", return_value=w.mapToGlobal(QPoint(4000, 4000))):
            seen.clear()
            settle_hover(w)
        self.assertIn(QEvent.Type.Leave, seen)
        self.assertIn(QEvent.Type.HoverLeave, seen)


class MainWindowPolishTest(_Base):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.resize(1000, 700)
        cls.win.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.close()
        cls.win.deleteLater()
        cls.app.processEvents()
        cls._settings_tmp.cleanup()

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.a = pathlib.Path(self.dir.name) / "a.jpg"
        self.b = pathlib.Path(self.dir.name) / "b.jpg"
        shutil.copy(SAMPLE, self.a)
        shutil.copy(SAMPLE, self.b)
        w = self.win
        w.image_files = [self.a, self.b]
        w.current_idx = 0
        self.flashes: list[tuple] = []
        w._flash = lambda text, action_text="", action=None, msec=3200, bottom=False: self.flashes.append(  # type: ignore[method-assign]
            (text, action_text, action)
        )
        w._stop_playback = lambda release=False: None  # type: ignore[method-assign]
        w.display_current = lambda: None  # type: ignore[method-assign]
        self.loads: list[str] = []
        w.load_path = lambda p, *a, **k: self.loads.append(str(p))  # type: ignore[method-assign]

    def tearDown(self) -> None:
        for name in ("_flash", "_stop_playback", "display_current", "load_path"):
            self.win.__dict__.pop(name, None)
        self.win._osd.hide()
        self.win._drop_hl.hide(animated=False)
        self.dir.cleanup()

    # --- kopyala ---
    def test_copy_actions_confirm_with_a_toast(self) -> None:
        w = self.win
        w._copy_text("C:\\x\\a.jpg", "copied_path")
        self.assertEqual(QApplication.clipboard().text(), "C:\\x\\a.jpg")
        w._copy_file()
        w.canvas.set_pixmap(_pix(40, 30, "#336699"))
        w._copy_image()
        texts = [f[0] for f in self.flashes]
        self.assertEqual(texts, [w.t("copied_path"), w.t("copied_file"), w.t("copied_image")])
        self.assertTrue(all(not f[1] for f in self.flashes), "kopyalama bilgisinde düğme yok")

    # --- sil / geri al ---
    def test_recycle_offers_a_real_undo_that_restores_the_file(self) -> None:
        w = self.win
        bundle = types.SimpleNamespace(originals=[str(self.a)], backups=["bk"])
        with mock.patch("app.ui.main_window.recycle_with_undo", return_value=bundle), \
                mock.patch("app.ui.main_window.discard_undo"):
            w.delete_current()
        self.assertIs(w._undo, bundle)
        self.assertEqual(w.image_files, [self.b])
        text, action_text, action = self.flashes[-1]
        self.assertEqual(text, w.t("deleted_recycle").format("a.jpg"))
        self.assertEqual(action_text, w.t("undo"))
        with mock.patch("app.ui.main_window.restore_files", return_value=[str(self.a)]) as restore, \
                mock.patch("app.ui.main_window.discard_undo"):
            action()
        restore.assert_called_once_with([str(self.a)], ["bk"])
        self.assertIsNone(w._undo)
        self.assertEqual(self.loads, [str(self.a)], "geri gelen dosya görünümde açılır")

    def test_stale_undo_button_does_nothing_after_another_delete(self) -> None:
        w = self.win
        first = types.SimpleNamespace(originals=[str(self.a)], backups=["1"])
        second = types.SimpleNamespace(originals=[str(self.b)], backups=["2"])
        with mock.patch("app.ui.main_window.recycle_with_undo", side_effect=[first, second]), \
                mock.patch("app.ui.main_window.discard_undo"):
            w.delete_current()
            stale_action = self.flashes[-1][2]
            w.image_files = [self.b]
            w.current_idx = 0
            w.delete_current()
        with mock.patch("app.ui.main_window.restore_files") as restore:
            stale_action()
        restore.assert_not_called()
        self.assertIs(w._undo, second)
        w._undo = None

    def test_permanent_delete_is_reported_without_undo(self) -> None:
        from app.ui.message_box import MessageBox

        w = self.win
        with mock.patch.object(MessageBox, "question", return_value=MessageBox.StandardButton.Yes):
            w.permanently_delete_current()
        self.assertFalse(self.a.exists())
        text, action_text, action = self.flashes[-1]
        self.assertEqual(text, w.t("deleted_permanent").format("a.jpg"))
        self.assertEqual(action_text, "")
        self.assertIsNone(action)

    # --- farklı kaydet ---
    def test_save_as_opens_in_the_files_folder_and_reports_success(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        w = self.win
        w.canvas.set_pixmap(_pix(40, 30, "#336699"))
        dest = pathlib.Path(self.dir.name) / "out.png"
        seen: dict = {}

        def fake_dialog(parent, title, start, filters):
            seen["start"] = start
            return str(dest), ""

        with mock.patch.object(QFileDialog, "getSaveFileName", side_effect=fake_dialog):
            w.save_current_as()
        self.assertEqual(pathlib.Path(seen["start"]), self.a)
        self.assertTrue(dest.is_file())
        text, action_text, action = self.flashes[-1]
        self.assertEqual(text, w.t("saved_as").format("out.png"))
        self.assertEqual(action_text, w.t("show"))
        self.assertEqual(pathlib.Path(str(w.settings.value("save_as_dir"))), dest.parent)

    def test_save_as_reports_encoder_failure(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        from app.ui.message_box import MessageBox

        w = self.win
        w.canvas.set_pixmap(_pix(40, 30, "#336699"))
        dest = pathlib.Path(self.dir.name) / "out.webp"
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(str(dest), "")), \
                mock.patch.object(QPixmap, "save", return_value=False), \
                mock.patch.object(MessageBox, "warning") as warn:
            w.save_current_as()
        warn.assert_called_once()
        self.assertIn(w.t("save_as_encode_fail").format(".webp"), warn.call_args[0][2])
        self.assertEqual(self.flashes, [], "başarısız kayıtta 'Kaydedildi' denmez")

    def test_save_as_falls_back_to_last_folder_when_files_folder_is_read_only(self) -> None:
        w = self.win
        last = tempfile.TemporaryDirectory()
        self.addCleanup(last.cleanup)
        w.settings.setValue("save_as_dir", last.name)
        with mock.patch("os.access", return_value=False):
            self.assertEqual(w._save_as_dir(self.a), pathlib.Path(last.name))

    # --- OSD ---
    def test_seek_osd_accumulates_in_the_same_direction(self) -> None:
        w = self.win
        with _anims(False):
            w._osd.hide()
            w._flash_seek(5000)
            self.assertEqual(w._osd.text(), w.t("osd_seek").format("+5"))
            w._flash_seek(5000)
            self.assertEqual(w._osd.text(), w.t("osd_seek").format("+10"))
            w._flash_seek(-5000)
            self.assertEqual(w._osd.text(), w.t("osd_seek").format("−5"))
            w._flash_seek(-2500)
            self.assertEqual(w._osd.text(), w.t("osd_seek").format("−7.5"))
            self.assertTrue(w._osd.is_visible())

    def test_speed_and_loop_osd(self) -> None:
        w = self.win
        with _anims(False):
            w._flash_speed()
            self.assertEqual(w._osd.text(), w.t("osd_speed").format(f"{w.player.speed:g}×"))
            with mock.patch.object(w, "_transport_ready", return_value=True), \
                    mock.patch.object(w.player, "set_loop"):
                before = bool(w.player.loop)
                w._toggle_loop()
            self.assertEqual(w._osd.text(), w.t("loop_off" if before else "loop_on"))

    def test_zoom_osd_shows_percent_for_photos(self) -> None:
        w = self.win
        w.stage.stack.setCurrentWidget(w.canvas)
        w.canvas.set_pixmap(_pix(800, 600, "#808080"))
        with _anims(False):
            w._on_actual_size_clicked()
            self.assertEqual(w._osd.text(), w.t("osd_zoom").format(100))
            self.assertTrue(w._osd.is_visible())

    def test_osd_hidden_with_the_window(self) -> None:
        w = self.win
        with _anims(False):
            w._flash_speed()
            w._after_screen_refit()
        self.assertFalse(w._osd.host.isVisible())

    # --- son açılanlar ---
    def test_recent_store_dedupes_limits_and_skips_missing(self) -> None:
        from app.core.recent import add_recent, clear_recent, recent_paths

        s = self.win.settings
        clear_recent(s)
        add_recent(s, self.a)
        add_recent(s, self.b)
        add_recent(s, str(self.a).upper() if os.name == "nt" else self.a)
        got = recent_paths(s)
        self.assertEqual(len(got), 2)
        self.assertEqual(os.path.normcase(got[0]), os.path.normcase(str(self.a)))
        for i in range(15):
            add_recent(s, pathlib.Path(self.dir.name) / f"x{i}.jpg", limit=5)
        self.assertEqual(recent_paths(s, existing_only=False), [str(pathlib.Path(self.dir.name) / f"x{i}.jpg") for i in range(14, 9, -1)])
        self.assertEqual(recent_paths(s), [], "artık olmayan dosyalar gösterilmez")
        clear_recent(s)
        self.assertEqual(recent_paths(s, existing_only=False), [])

    def test_recent_submenu(self) -> None:
        from app.core.recent import clear_recent

        w = self.win
        clear_recent(w.settings)
        menu = QMenu()
        self.addCleanup(menu.deleteLater)
        self.assertIsNone(w._add_recent_submenu(menu), "liste boşken menüde yer kaplamaz")
        w._remember_recent(self.a)
        w._remember_recent(pathlib.Path(self.dir.name))
        sub = w._add_recent_submenu(menu)
        self.assertIsNotNone(sub)
        self.assertEqual(sub.title(), w.t("recent"))
        acts = [a for a in sub.actions() if not a.isSeparator()]
        self.assertEqual([a.toolTip() for a in acts[:2]], [str(pathlib.Path(self.dir.name)), str(self.a)])
        acts[1].trigger()
        self.assertEqual(self.loads, [str(self.a)])
        self.assertEqual(acts[-1].text(), w.t("recent_clear"))
        acts[-1].trigger()
        self.assertIsNone(w._add_recent_submenu(QMenu()))

    # --- sürükle-bırak vurgusu ---
    def _drag(self, cls, kind=None):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.a))])
        self._mime = mime  # olay yaşarken veri silinmesin
        if cls is QDragLeaveEvent:
            return QDragLeaveEvent()
        args = (QPointF(20, 20), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        if cls is QDragEnterEvent:
            return QDragEnterEvent(QPoint(20, 20), *args[1:])
        return QDropEvent(*args)

    def test_drop_highlight_follows_external_file_drag(self) -> None:
        w = self.win
        with _anims(False):
            w.eventFilter(w.stage, self._drag(QDragEnterEvent))
            self.assertTrue(w._drop_hl.is_shown())
            stage_tl = w.stage.mapToGlobal(QPoint(0, 0))
            self.assertEqual(w._drop_hl.host.geometry().topLeft(), stage_tl)
            # Alt parçalar arasında Leave→Enter: vurgu sönmez.
            w.eventFilter(w.stage, self._drag(QDragLeaveEvent))
            w.eventFilter(w.canvas.viewport(), self._drag(QDragEnterEvent))
            _wait(150)
            self.assertTrue(w._drop_hl.is_shown())
            # Pencereden çıkınca kısa gecikmeyle söner.
            w.eventFilter(w.stage, self._drag(QDragLeaveEvent))
            self.assertTrue(_wait_until(lambda: not w._drop_hl.is_shown(), 1000))
            # Bırakınca hemen söner.
            w.eventFilter(w.stage, self._drag(QDragEnterEvent))
            self.assertTrue(w._drop_hl.is_shown())
            w.eventFilter(w.stage, self._drag(QDropEvent))
            self.assertFalse(w._drop_hl.is_shown())

    def test_drop_highlight_ignores_drags_without_files_and_other_windows(self) -> None:
        w = self.win
        other = QWidget()
        self.addCleanup(other.deleteLater)
        with _anims(False):
            w.eventFilter(other, self._drag(QDragEnterEvent))
            self.assertFalse(w._drop_hl.is_shown())
            empty = QMimeData()  # olay veriyi sahiplenmez: başvuru tutulmalı
            ev = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, empty, Qt.MouseButton.LeftButton,
                                 Qt.KeyboardModifier.NoModifier)
            w.eventFilter(w.stage, ev)
            self.assertFalse(w._drop_hl.is_shown())

    # --- tam ekran kapsülleri ---
    def test_fullscreen_chrome_hides_instantly_without_animations(self) -> None:
        w = self.win
        hosts = [w._dock_host, w._brand_host, w._nav_host]
        with _anims(False), mock.patch.object(type(w), "isFullScreen", return_value=True):
            for h in hosts:
                w._fade_host_in(h)
                self.assertTrue(h.isVisible())
            w._hide_fullscreen_chrome()
            self.assertTrue(all(not h.isVisible() for h in hosts))
            w._stop_chrome_fades()
        self.assertTrue(all(abs(h.windowOpacity() - 1.0) < 0.01 for h in hosts))

    # --- menülerden sonra hover ---
    def test_tracks_menu_settles_hover(self) -> None:
        w = self.win

        def close_soon():
            def close():
                popup = QApplication.activePopupWidget()
                if popup is not None:
                    popup.close()
                else:
                    QTimer.singleShot(20, close)

            QTimer.singleShot(50, close)

        with mock.patch("app.ui.media_actions.settle_hover") as settle, \
                mock.patch.object(w.player, "track_lists", return_value=([], [])):
            close_soon()
            w._show_tracks_menu()
        settle.assert_called_once_with(w.btn_tracks)


if __name__ == "__main__":
    unittest.main()
