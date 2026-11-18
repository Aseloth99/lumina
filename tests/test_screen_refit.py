"""Ekran yapılandırması değişince (RDP ile bağlanma, çözünürlük/ölçek, monitör tak-çıkar) pencere yeniden oturmalı.

Eski hata: Lumina tam ekrandayken bilgisayara RDP ile bağlanılınca ölçek değişti, Windows pencereyi eski mantıksal
boyutunda bıraktı: arayüz ekranın sol üstünde küçük kaldı, kalan alanda eski kare asılı durdu, görev çubuğu göründü.

Ekran açmaz (offscreen) ve kullanıcının gerçek ayarlarına dokunmaz (geçici INI). Offscreen platform tam ekrandaki
pencereye geometri vermediği için ekran/yerel pencere sahte nesnelerle taklit edilir.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QCoreApplication, QRect, QSettings, QSize  # noqa: E402
from PyQt6.QtGui import QImage, QPixmap, QResizeEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


class _FakeScreen:
    def __init__(self, geo: QRect, avail: QRect | None = None) -> None:
        self._geo = QRect(geo)
        self._avail = QRect(avail if avail is not None else geo)

    def geometry(self) -> QRect:
        return QRect(self._geo)

    def availableGeometry(self) -> QRect:  # noqa: N802
        return QRect(self._avail)


class _FakeHandle:
    def __init__(self) -> None:
        self.calls: list[QRect] = []

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        self.calls.append(QRect(rect))


def _pump() -> None:
    for _ in range(5):
        QCoreApplication.processEvents()


class HelperTest(unittest.TestCase):
    def test_roughly_fits(self) -> None:
        from app.ui.frameless import roughly_fits

        target = QRect(0, 0, 1920, 1080)
        self.assertTrue(roughly_fits(QRect(0, 0, 1920, 1080), target, 2))
        self.assertTrue(roughly_fits(QRect(-8, -8, 1936, 1096), target, 24), "büyütülmüş çerçeve taşması normal")
        self.assertFalse(roughly_fits(QRect(0, 0, 1280, 720), target, 2), "RDP sonrası küçülmüş tam ekran")

    def test_clamp_rect(self) -> None:
        from app.ui.frameless import clamp_rect

        avail = QRect(0, 0, 1280, 680)
        self.assertEqual(clamp_rect(QRect(100, 100, 600, 400), avail), QRect(100, 100, 600, 400))
        self.assertEqual(clamp_rect(QRect(3000, 2000, 600, 400), avail), QRect(680, 280, 600, 400))
        self.assertEqual(clamp_rect(QRect(-500, -50, 2000, 1500), avail), avail)
        # İkinci monitörde (negatif koordinat) çalışma alanı
        self.assertEqual(clamp_rect(QRect(0, 0, 400, 300), QRect(-1920, 0, 1920, 1040)), QRect(-400, 0, 400, 300))


class ScreenRefitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._tmp.name)
        cls.app = QApplication.instance() or QApplication([])
        from app.ui.main_window import MainWindow

        cls.win = MainWindow()
        cls.win.setGeometry(40, 40, 700, 500)
        cls.win.show()
        _pump()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.win.hide()
        cls.win.deleteLater()
        _pump()
        cls._tmp.cleanup()

    def setUp(self) -> None:
        self.addCleanup(self._reset)

    def _reset(self) -> None:
        w = self.win
        w._screen_refit_timer.stop()
        if not w.isVisible():
            w.show()
        if w.windowState():
            w.showNormal()
        _pump()
        w._screen_refit_timer.stop()
        w._refit_deferred = False
        w._restore_check = False
        w._state_cycle_ok = False
        w.setGeometry(40, 40, 700, 500)
        _pump()

    def _screen(self):
        return self.win.screen() or QApplication.primaryScreen()

    def test_screen_signals_are_watched_and_debounced(self) -> None:
        """RDP art arda birçok olay yayar: hepsi tek bir yeniden oturtmaya dönüşmeli."""
        w = self.win
        screen = self._screen()
        with mock.patch.object(w, "_after_screen_refit") as after:
            screen.geometryChanged.emit(screen.geometry())
            screen.availableGeometryChanged.emit(screen.availableGeometry())
            screen.logicalDotsPerInchChanged.emit(144.0)
            self.app.primaryScreenChanged.emit(screen)
            w.windowHandle().screenChanged.emit(screen)
            self.assertTrue(w._screen_refit_timer.isActive())
            after.assert_not_called()
            QTest.qWait(w._screen_refit_timer.interval() + 250)
            self.assertEqual(after.call_count, 1)

    def test_fullscreen_regrows_to_new_screen_keeping_overlay_mode(self) -> None:
        """Ölçek değişince küçük kalan tam ekran: yeni ekran geometrisine yerel pencere üzerinden büyür,
        QWidget.setGeometry'nin aksine tam ekran (overlay) durumu bozulmaz."""
        w = self.win
        w.toggle_fullscreen()
        _pump()
        self.assertTrue(w.isFullScreen())
        new_geo = QRect(0, 0, w.width() + 400, w.height() + 300)
        handle = _FakeHandle()
        with (
            mock.patch.object(w, "_fit_screen", return_value=_FakeScreen(new_geo)),
            mock.patch.object(w, "windowHandle", return_value=handle),
            mock.patch.object(w, "_layout_overlay_chrome", wraps=w._layout_overlay_chrome) as overlay,
        ):
            w._refit_to_screen()
        self.assertEqual(handle.calls, [new_geo])
        self.assertTrue(w.isFullScreen())
        self.assertTrue(w._fs_mode_active)
        self.assertIs(w._dock_host.child_widget(), w.dock_wrap)
        self.assertTrue(overlay.called, "overlay kapsülleri yeni boyuta yerleşmeli")
        self.assertTrue(w._restore_check, "çıkışta normal konum yeni ekrana göre denetlenmeli")

    def test_fullscreen_fallback_rebuilds_state_once(self) -> None:
        """Platform tam ekrandaki pencereye geometri vermezse tam ekran bir kez yeniden kurulur; döngüye girmez."""
        w = self.win
        w.showFullScreen()
        _pump()
        new_geo = QRect(0, 0, w.width() + 400, w.height() + 300)
        w.schedule_screen_refit()
        w._screen_refit_timer.stop()
        with (
            mock.patch.object(w, "_fit_screen", return_value=_FakeScreen(new_geo)),
            mock.patch.object(w, "windowHandle", return_value=_FakeHandle()),
            mock.patch.object(w, "showFullScreen", wraps=w.showFullScreen) as show_fs,
        ):
            w._refit_to_screen()
            _pump()
            w._refit_to_screen()
            _pump()
            self.assertEqual(show_fs.call_count, 1)
        self.assertTrue(w.isFullScreen())
        self.assertTrue(w._fs_mode_active)

    def test_fullscreen_already_fitting_is_left_alone(self) -> None:
        w = self.win
        w.showFullScreen()
        _pump()
        handle = _FakeHandle()
        with mock.patch.object(w, "windowHandle", return_value=handle):
            w._refit_to_screen()
        self.assertEqual(handle.calls, [])
        self.assertTrue(w.isFullScreen())

    def test_dpi_shrunk_fullscreen_resize_schedules_refit(self) -> None:
        """WM_DPICHANGED pencereyi eski mantıksal boyutta bırakır; ekran sinyali gelmese de boyut olayı yakalar."""
        w = self.win
        w.showFullScreen()
        _pump()
        w._screen_refit_timer.stop()
        bigger = _FakeScreen(QRect(0, 0, w.width() + 600, w.height() + 400))
        with mock.patch.object(w, "_fit_screen", return_value=bigger):
            w.resizeEvent(QResizeEvent(w.size(), QSize(w.width() + 600, w.height() + 400)))
        self.assertTrue(w._screen_refit_timer.isActive())

    def test_maximized_rebuilt_once_when_work_area_changed(self) -> None:
        w = self.win
        w.showMaximized()
        _pump()
        self.assertTrue(w.isMaximized())
        with mock.patch.object(w, "showMaximized", wraps=w.showMaximized) as show_max:
            w._state_cycle_ok = True
            w._refit_to_screen()  # gerçek çalışma alanına oturuyor: dokunma
            self.assertEqual(show_max.call_count, 0)
            moved = _FakeScreen(QRect(0, 0, 3000, 2000), QRect(1000, 600, 1500, 1000))
            w._state_cycle_ok = True
            with mock.patch.object(w, "_fit_screen", return_value=moved):
                w._refit_to_screen()
                _pump()
                w._refit_to_screen()  # yeni ekran olayı yok: yeniden kurma döngüsü olmamalı
                _pump()
            self.assertEqual(show_max.call_count, 1)
        self.assertTrue(w.isMaximized())

    def test_off_screen_normal_window_comes_back(self) -> None:
        w = self.win
        avail = self._screen().availableGeometry()
        w.setGeometry(avail.right() + 2000, avail.bottom() + 1500, 700, 500)
        _pump()
        w._refit_to_screen()
        _pump()
        self.assertTrue(avail.contains(w.geometry()), f"{w.geometry()} çalışma alanında değil: {avail}")
        self.assertEqual((w.width(), w.height()), (700, 500))

    def test_oversized_normal_window_shrinks_into_work_area(self) -> None:
        w = self.win
        avail = self._screen().availableGeometry()
        w.setGeometry(avail.x() - 50, avail.y() - 50, avail.width() + 400, avail.height() + 300)
        _pump()
        w._refit_to_screen()
        _pump()
        self.assertTrue(avail.contains(w.geometry()), f"{w.geometry()} çalışma alanında değil: {avail}")

    def test_visible_normal_window_is_not_moved(self) -> None:
        w = self.win
        before = QRect(w.geometry())
        w._refit_to_screen()
        _pump()
        self.assertEqual(w.geometry(), before)

    def test_hidden_window_defers_refit_until_shown(self) -> None:
        """Tepsideyken ekran değişirse pencere gösterilince yeni ekrana oturur."""
        w = self.win
        w.hide()
        _pump()
        with mock.patch.object(w, "_after_screen_refit") as after:
            w._refit_to_screen()
            after.assert_not_called()
            self.assertTrue(w._refit_deferred)
            w.show()
            QTest.qWait(w._screen_refit_timer.interval() + 300)
            after.assert_called_once()
        self.assertFalse(w._refit_deferred)

    def test_exit_after_screen_change_checks_restored_position(self) -> None:
        """Tam ekranken ekran değiştiyse çıkışta işletim sisteminin sakladığı (eski ekrana göre) konum denetlenir."""
        w = self.win
        w._fs_from_maximized = False
        w.showFullScreen()
        _pump()
        w._refit_to_screen()
        self.assertTrue(w._restore_check)
        w._screen_refit_timer.stop()
        with mock.patch.object(w, "_keep_on_screen") as keep:
            w.toggle_fullscreen()
            QTest.qWait(w._screen_refit_timer.interval() + 300)
            keep.assert_called()
        self.assertFalse(w.isFullScreen())
        self.assertFalse(w._restore_check)

    def test_refit_refreshes_scale_dependent_parts(self) -> None:
        w = self.win
        w.showFullScreen()
        _pump()
        gallery = mock.Mock()
        old_gallery = w.gallery
        w.gallery = gallery
        try:
            with (
                mock.patch.object(w.canvas, "refresh_scale") as canvas_refresh,
                mock.patch.object(w, "_force_full_repaint") as repaint,
                mock.patch.object(w._toast, "hide") as toast_hide,
            ):
                w._refit_to_screen()
            gallery.refresh_scale.assert_called_once()
            canvas_refresh.assert_called_once()
            repaint.assert_called_once()
            toast_hide.assert_called()
        finally:
            w.gallery = old_gallery


class ScaleCacheTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_canvas_refresh_scale_refits_and_replans_svg(self) -> None:
        from app.ui.canvas import PhotoCanvas

        canvas = PhotoCanvas()
        self.addCleanup(canvas.deleteLater)
        canvas.resize(400, 300)
        img = QImage(64, 48, QImage.Format.Format_RGB32)
        img.fill(0x336699)
        canvas.set_qimage(img)
        with (
            mock.patch.object(canvas, "fit_in_window") as fit,
            mock.patch.object(canvas._svg, "schedule") as schedule,
        ):
            canvas.refresh_scale()
            fit.assert_called_once()
            schedule.assert_called_once()
            canvas._fitted = False  # yakınlaştırılmış görünüm korunur
            canvas.refresh_scale()
            fit.assert_called_once()

    def test_gallery_refresh_scale_drops_old_dpr_tiles(self) -> None:
        from app.ui.gallery import GalleryView, ThumbnailProvider

        provider = ThumbnailProvider(cache_dir=None, threads=1)
        self.addCleanup(provider.shutdown)
        provider.request = lambda keys: None
        view = GalleryView(provider, cell=150)
        self.addCleanup(view.deleteLater)
        view._delegate._scaled["a"] = (150, QPixmap(4, 4))
        view.refresh_scale()
        self.assertEqual(len(view._delegate._scaled), 0)


if __name__ == "__main__":
    unittest.main()
