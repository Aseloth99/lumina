"""Yakınlaştırma kalitesi: büyük fotoğrafın ekran boyu kopyası (hare yok) ve videoda imleç altına yakınlaştırma.

1. Büyük görüntü sığdırılınca boyama kaynaktan seyrek örnek alıyordu: tek piksellik siyah/beyaz çizgiler ekranda
   hareye (iri dalgalara) dönüşüyordu. Artık arka planda piksel ortalamasıyla küçültülmüş kopya boyanır.
2. Video tekerlekle hep ortaya doğru yakınlaşıyordu; artık fotoğraftaki gibi imlecin altındaki nokta yerinde kalır.
   mpv'de video-pan-x/y ölçeklenmiş video boyutunun kesridir (gerçek mpv ile osd-dimensions üzerinden doğrulandı).
"""

from __future__ import annotations

import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
from PyQt6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from app.ui.canvas import LOD_MAX_SCALE, PhotoCanvas  # noqa: E402
from app.ui.player import MpvSurface, VideoSurfaceFallback  # noqa: E402


def _stripes(w: int, h: int) -> QImage:
    arr = np.zeros((h, w, 3), np.uint8)
    arr[:, ::2] = 255  # tek piksellik dikey çizgiler: seyrek örneklemede hare, ortalamada düz gri
    return QImage(arr.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888).copy()


def _center_std(canvas: PhotoCanvas) -> float:
    img = canvas.viewport().grab().toImage().convertToFormat(QImage.Format.Format_Grayscale8)
    raw = np.frombuffer(img.constBits().asstring(img.sizeInBytes()), np.uint8)
    arr = raw.reshape(img.height(), img.bytesPerLine())[:, : img.width()]
    h, w = arr.shape
    return float(arr[h // 3 : 2 * h // 3, w // 3 : 2 * w // 3].std())


def _wait_for(cond, timeout: float = 3.0) -> bool:
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return cond()


class CanvasLodTests(unittest.TestCase):
    def setUp(self) -> None:
        self.canvas = PhotoCanvas()
        self.canvas.resize(1200, 800)
        self.canvas.show()
        app.processEvents()

    def tearDown(self) -> None:
        self.canvas.close()
        self.canvas.deleteLater()
        app.processEvents()

    def _lod_ready(self) -> bool:
        return self.canvas.pixmap_item.lod_scale() > 0

    def test_fitted_large_image_uses_averaged_copy(self) -> None:
        self.canvas.set_qimage(_stripes(4800, 3200))
        app.processEvents()
        self.assertGreater(_center_std(self.canvas), 30, "kopya yokken çizgiler hareli/tırtıklı görünmeli")
        self.assertTrue(_wait_for(self._lod_ready))
        item = self.canvas.pixmap_item
        self.assertAlmostEqual(item.lod_scale(), self.canvas.displayed_scale() * self.canvas.devicePixelRatioF(), 4)
        self.assertLess(_center_std(self.canvas), 8, "ortalama kopyada çizgiler düz griye dönmeli")
        self.assertTrue(item.lod_painted)

    def test_zoom_in_switches_back_to_original_and_fit_reuses_copy(self) -> None:
        self.canvas.set_qimage(_stripes(4800, 3200))
        self.assertTrue(_wait_for(self._lod_ready))
        lod = self.canvas.pixmap_item.lod_scale()
        for _ in range(12):
            self.canvas.zoom_by(1.18)
        app.processEvents()
        self.assertGreater(self.canvas.displayed_scale(), 1.0)
        self.canvas.viewport().repaint()
        self.assertFalse(self.canvas.pixmap_item.lod_painted, "yakınlaştırınca asıl görüntünün ayrıntısı boyanmalı")
        self.assertGreater(_center_std(self.canvas), 60)
        self.canvas.fit_in_window()
        self.canvas.viewport().repaint()
        self.assertTrue(self.canvas.pixmap_item.lod_painted)
        self.assertEqual(self.canvas.pixmap_item.lod_scale(), lod)

    def test_small_image_gets_no_copy(self) -> None:
        self.canvas.set_qimage(_stripes(1000, 600))  # sığdırma ölçeği >= LOD_MAX_SCALE
        self.assertGreaterEqual(self.canvas.displayed_scale(), LOD_MAX_SCALE)
        self.canvas._update_lod()
        app.processEvents()
        self.assertEqual(self.canvas.pixmap_item.lod_scale(), 0.0)

    def test_new_image_discards_copy_of_previous_one(self) -> None:
        self.canvas.set_qimage(_stripes(4800, 3200))
        self.canvas._update_lod()  # iş başladı, sonucu henüz gelmedi
        self.canvas.set_qimage(_stripes(3000, 6000))
        self.assertTrue(_wait_for(self._lod_ready))
        item = self.canvas.pixmap_item
        self.assertEqual(item.pixmap().width(), 3000)
        self.assertAlmostEqual(item.lod_scale(), self.canvas.displayed_scale() * self.canvas.devicePixelRatioF(), 4)

    def test_rotation_rebuilds_copy_for_new_fit(self) -> None:
        self.canvas.set_qimage(_stripes(4800, 3200))
        self.assertTrue(_wait_for(self._lod_ready))
        before = self.canvas.pixmap_item.lod_scale()
        self.canvas.rotate_step(90)
        self.assertTrue(_wait_for(lambda: abs(self.canvas.pixmap_item.lod_scale() - before) > 1e-4))
        self.canvas.viewport().repaint()
        self.assertTrue(self.canvas.pixmap_item.lod_painted)


class _FakeMpv:
    """mpv_set/mpv_get'in kullandığı _set_property/_get_property; osd-dimensions gerçek mpv formülüyle hesaplanır."""

    def __init__(self, surface, video=(3840, 2160)) -> None:
        self.surface = surface
        self.video = video
        self.props: dict = {"video-zoom": 0.0, "video-pan-x": 0.0, "video-pan-y": 0.0}

    def _set_property(self, name, value) -> None:
        self.props[name] = value

    def rect(self) -> tuple[float, float, float, float]:
        """Ekrandaki video dikdörtgeni (sol, üst, genişlik, yükseklik)."""
        w, h = self.surface.width(), self.surface.height()
        vw, vh = self.video
        fit = min(w / vw, h / vh) * 2 ** float(self.props.get("video-zoom", 0.0))
        dw, dh = vw * fit, vh * fit
        cx = w / 2 + float(self.props.get("video-pan-x", 0.0)) * dw
        cy = h / 2 + float(self.props.get("video-pan-y", 0.0)) * dh
        return cx - dw / 2, cy - dh / 2, dw, dh

    def _get_property(self, name):
        if name == "osd-dimensions":
            w, h = self.surface.width(), self.surface.height()
            left, top, dw, dh = self.rect()
            return {"w": w, "h": h, "ml": left, "mt": top, "mr": w - left - dw, "mb": h - top - dh}
        if name == "dwidth":
            return self.video[0]
        if name == "dheight":
            return self.video[1]
        return self.props.get(name)

    def video_point(self, pos: QPointF) -> tuple[float, float]:
        left, top, dw, dh = self.rect()
        return (pos.x() - left) / dw, (pos.y() - top) / dh


class MpvZoomAnchorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = MpvSurface()
        self.s.resize(1600, 1000)
        self.mpv = _FakeMpv(self.s)
        self.s.set_mpv_instance(self.mpv)
        self.s.set_has_video(True)

    def tearDown(self) -> None:
        self.s.deleteLater()
        app.processEvents()

    def test_point_under_cursor_stays_put(self) -> None:
        # Video pencereden taşınca: taşmadan önce (16:9 video, 16:10 pencere) dikey kaydırma kenar boşluğu
        # açacağından sınırlanır, fotoğraf tuvalindeki gibi.
        self.s.zoom_by(2.0)
        cursor = QPointF(1250, 300)
        for step in (1.18, 1.18, 1.18, 1 / 1.18, 1.18):
            before = self.mpv.video_point(cursor)
            self.s.zoom_by(step, cursor)
            after = self.mpv.video_point(cursor)
            self.assertAlmostEqual(before[0], after[0], 4)
            self.assertAlmostEqual(before[1], after[1], 4)
        self.assertGreater(self.mpv.props["video-pan-x"], -1.0)
        self.assertNotEqual(self.mpv.props["video-pan-x"], 0.0)

    def test_buttons_and_keys_still_zoom_to_centre(self) -> None:
        self.s.zoom_by(1.5)
        self.assertEqual(self.mpv.props["video-pan-x"], 0.0)
        self.assertEqual(self.mpv.props["video-pan-y"], 0.0)

    def test_pan_never_opens_a_gap_and_resets_at_fit(self) -> None:
        corner = QPointF(1599, 999)
        for _ in range(15):
            self.s.zoom_by(1.18, corner)
            left, top, dw, dh = self.mpv.rect()
            self.assertLessEqual(left, 0.01)
            self.assertLessEqual(top, 0.01)
            self.assertGreaterEqual(left + dw, 1599.99)
            self.assertGreaterEqual(top + dh, 999.99)
        for _ in range(30):
            self.s.zoom_by(1 / 1.18, corner)
        self.assertEqual(self.s._zoom, 1.0)
        self.assertEqual(self.mpv.props["video-pan-x"], 0.0)
        self.assertEqual(self.mpv.props["video-pan-y"], 0.0)

    def test_drag_moves_video_with_the_cursor(self) -> None:
        self.s.zoom_by(3.0)
        left0, top0, _, _ = self.mpv.rect()

        def ev(kind, x, y, buttons):
            return QMouseEvent(kind, QPointF(x, y), QPointF(x, y), Qt.MouseButton.LeftButton, buttons,
                               Qt.KeyboardModifier.NoModifier)

        self.s.mousePressEvent(ev(QEvent.Type.MouseButtonPress, 800, 500, Qt.MouseButton.LeftButton))
        self.s.mouseMoveEvent(ev(QEvent.Type.MouseMove, 900, 540, Qt.MouseButton.LeftButton))
        left1, top1, _, _ = self.mpv.rect()
        self.assertAlmostEqual(left1 - left0, 100.0, 3)
        self.assertAlmostEqual(top1 - top0, 40.0, 3)


class FallbackZoomAnchorTests(unittest.TestCase):
    def test_point_under_cursor_stays_put(self) -> None:
        s = VideoSurfaceFallback()
        s.resize(1600, 1000)
        img = QImage(1920, 1080, QImage.Format.Format_RGB32)
        img.fill(QColor("gray"))
        s.set_frame(img)

        def point(pos: QPointF) -> tuple[float, float]:
            target, scale, eff_pw, eff_ph = s._layout(s._zoom)
            center = QPointF(target.center()) + s._pan
            return (pos.x() - center.x()) / scale, (pos.y() - center.y()) / scale

        cursor = QPointF(1200, 300)
        s.zoom_by(1.18, cursor)  # ilk adım sığdırmadan çıkar
        for step in (1.18, 1.18, 1 / 1.18):
            before = point(cursor)
            s.zoom_by(step, cursor)
            after = point(cursor)
            self.assertAlmostEqual(before[0], after[0], 3)
            self.assertAlmostEqual(before[1], after[1], 3)
        s.deleteLater()


if __name__ == "__main__":
    unittest.main()
