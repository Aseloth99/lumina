"""HDR/JXR: ton eşleme özellikleri ve gerçek JPEG XR dosyasıyla yükleme hattı."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from app.core import hdr  # noqa: E402


def _srgb8(linear: float) -> int:
    return int(hdr.linear_to_srgb8(np.array([linear], dtype=np.float32))[0])


class ToneMapTest(unittest.TestCase):
    def test_sdr_range_is_passed_through_unchanged(self) -> None:
        """Kâğıt beyazının altındaki değerler (diz noktası altı) yalnızca sRGB kodlanır."""
        paper = hdr.PAPER_WHITE
        values = [0.0, 0.01, 0.1, 0.3, 0.5]
        arr = np.array([[[v * paper] * 3 for v in values]], dtype=np.float32)  # 1xNx3
        out = hdr.tonemap_scrgb_to_srgb8(arr)
        for i, v in enumerate(values):
            self.assertEqual(int(out[0, i, 0]), _srgb8(v), f"değer {v}")

    def test_highlights_are_compressed_but_stay_distinguishable(self) -> None:
        paper = hdr.PAPER_WHITE
        ramp = np.linspace(0.0, 6.0, 512, dtype=np.float32)
        arr = np.repeat(ramp[None, :, None], 3, axis=2)
        out = hdr.tonemap_scrgb_to_srgb8(arr)[0, :, 0].astype(int)
        self.assertTrue(np.all(np.diff(out) >= 0), "monoton olmalı")
        self.assertLessEqual(int(out.max()), 255)
        # Düz kırpma 1.0'dan (=80 nit) sonrasını 255'e ezerdi; ton eşleme kâğıt beyazına kadar ayrımı korur
        at = lambda v: int(out[int(round(v / 6.0 * 511))])  # noqa: E731
        self.assertLess(at(0.5 * paper), at(1.0 * paper))
        self.assertLess(at(1.0 * paper), at(1.8 * paper))
        self.assertGreater(at(1.0 * paper), 200, "kâğıt beyazı belirgin parlak olmalı")
        self.assertGreater(at(2.0 * paper), at(1.0 * paper), "kâğıt beyazı üstü vurgular da ayrışmalı")

    def test_naive_clip_would_lose_highlight_detail(self) -> None:
        """Düz kırpma iki farklı HDR vurgusunu aynı değere ezer; ton eşleme etmez."""
        arr = np.array([[[2.5, 2.5, 2.5], [5.0, 5.0, 5.0], [0.3, 0.3, 0.3]]], dtype=np.float32)
        naive = hdr.linear_to_srgb8(np.clip(arr, 0, 1))
        self.assertEqual(int(naive[0, 0, 0]), int(naive[0, 1, 0]))
        mapped = hdr.tonemap_scrgb_to_srgb8(arr)
        self.assertLess(int(mapped[0, 0, 0]), int(mapped[0, 1, 0]))

    def test_hue_is_preserved_for_saturated_highlight(self) -> None:
        arr = np.array([[[6.0, 3.0, 0.0]]], dtype=np.float32)
        r, g, b = (int(x) for x in hdr.tonemap_scrgb_to_srgb8(arr)[0, 0])
        self.assertEqual(b, 0)
        self.assertGreater(r, g)
        # doğrusal oranlar korunur: g/r ≈ 0.5
        lin = lambda c: ((c / 255 + 0.055) / 1.055) ** 2.4 if c / 255 > 0.04045 else c / 255 / 12.92  # noqa: E731
        self.assertAlmostEqual(lin(g) / lin(r), 0.5, delta=0.03)

    def test_negative_nan_and_inf_do_not_break(self) -> None:
        arr = np.array([[[-1.0, np.nan, np.inf], [0.2, 0.2, 0.2]]], dtype=np.float32)
        out = hdr.tonemap_scrgb_to_srgb8(arr)
        self.assertEqual(out.dtype, np.uint8)
        self.assertEqual(int(out[0, 0, 0]), 0)
        self.assertEqual(int(out[0, 0, 1]), 0)

    def test_band_processing_matches_single_pass(self) -> None:
        rng = np.random.default_rng(3)
        arr = (rng.random((300, 41, 4), dtype=np.float32) ** 3) * 6.0
        whole = hdr.tonemap_scrgb_to_srgb8(arr)
        old = hdr._BAND_ROWS
        try:
            hdr._BAND_ROWS = 7
            banded = hdr.tonemap_scrgb_to_srgb8(arr)
        finally:
            hdr._BAND_ROWS = old
        self.assertTrue(np.array_equal(whole, banded))

    def test_all_sdr_image_is_not_dimmed_by_peak_handling(self) -> None:
        arr = np.full((8, 8, 3), 0.8 * hdr.PAPER_WHITE * 0.5, dtype=np.float32)
        out = hdr.tonemap_scrgb_to_srgb8(arr)
        self.assertEqual(int(out[0, 0, 0]), _srgb8(0.4))


class ArrayToPilTest(unittest.TestCase):
    def test_float_rgba_opaque_becomes_rgb(self) -> None:
        arr = np.ones((4, 5, 4), dtype=np.float32)
        img = hdr.hdr_array_to_pil(arr)
        self.assertEqual((img.mode, img.size), ("RGB", (5, 4)))

    def test_float_with_real_transparency_keeps_alpha(self) -> None:
        arr = np.ones((4, 5, 4), dtype=np.float32)
        arr[..., 3] = 0.5
        img = hdr.hdr_array_to_pil(arr)
        self.assertEqual(img.mode, "RGBA")
        self.assertAlmostEqual(img.getpixel((0, 0))[3], 128, delta=1)

    def test_float16_and_grayscale(self) -> None:
        img = hdr.hdr_array_to_pil(np.full((3, 3, 3), 1.0, dtype=np.float16))
        self.assertEqual(img.mode, "RGB")
        gray = hdr.hdr_array_to_pil(np.full((3, 3), 0.5, dtype=np.float32))
        self.assertEqual(gray.mode, "RGB")

    def test_integer_dtypes_are_not_tone_mapped(self) -> None:
        u16 = np.full((2, 2, 3), 40000, dtype=np.uint16)
        self.assertEqual(hdr.hdr_array_to_pil(u16).getpixel((0, 0)), (156, 156, 156))
        u8 = np.full((2, 2, 3), 77, dtype=np.uint8)
        self.assertEqual(hdr.hdr_array_to_pil(u8).getpixel((1, 1)), (77, 77, 77))


class RealJxrPipelineTest(unittest.TestCase):
    """Gerçek JPEG XR kodlayıp Lumina yükleyicilerinden geçirir (imagecodecs yoksa atlanır)."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import imagecodecs

            cls.codecs = imagecodecs
            probe = np.ones((16, 16, 3), dtype=np.float32)
            imagecodecs.jpegxr_decode(imagecodecs.jpegxr_encode(probe))
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"JPEG XR kodlayıcı/çözücü yok: {exc}")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        yy, xx = np.mgrid[0:96, 0:128]
        hdr_img = np.zeros((96, 128, 3), dtype=np.float32)
        hdr_img[..., 0] = xx / 127.0 * 5.0  # kırmızı 0..5 (HDR vurgu)
        hdr_img[..., 1] = yy / 95.0 * 1.0
        hdr_img[..., 2] = 0.2
        cls.path = pathlib.Path(cls.tmp.name) / "hdr test.jxr"
        cls.path.write_bytes(imagecodecs.jpegxr_encode(hdr_img))

    def test_load_pil_decodes_and_tone_maps(self) -> None:
        from app.core.image_loader import load_pil

        img = load_pil(self.path)
        self.assertEqual(img.size, (128, 96))
        self.assertEqual(img.mode, "RGB")
        left, right = img.getpixel((2, 48)), img.getpixel((125, 48))
        self.assertLess(left[0], right[0], "kırmızı artışı korunmalı")
        self.assertLess(right[0], 256)

    def test_thumbnail_is_downscaled_and_consistent(self) -> None:
        from app.core.thumb_loader import load_thumb_pil

        thumb = load_thumb_pil(self.path, size=32)
        self.assertLessEqual(max(thumb.size), 32)
        self.assertEqual(thumb.mode, "RGB")

    def test_qimage_conversion_and_matcher_knows_extension(self) -> None:
        from PyQt6.QtGui import QGuiApplication

        QGuiApplication.instance() or QGuiApplication([])
        from app.core.image_loader import load_pil, pil_to_qimage
        from app.core.matcher import IMAGE_EXTS

        self.assertIn(".jxr", IMAGE_EXTS)
        qimg = pil_to_qimage(load_pil(self.path))
        self.assertEqual((qimg.width(), qimg.height()), (128, 96))

    def test_corrupt_jxr_gives_clear_error(self) -> None:
        from app.core.image_loader import load_pil

        bad = pathlib.Path(self.tmp.name) / "bozuk.jxr"
        bad.write_bytes(b"II\xbc\x01" + b"\x00" * 64)
        with self.assertRaises(OSError) as ctx:
            load_pil(bad)
        self.assertIn("JPEG XR", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


class MainWindowJxrTest(unittest.TestCase):
    """JXR dosyası klasörden açılınca tam boyutlu görüntü tuvale gelmeli, hata iletişim kutusu çıkmamalı."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import imagecodecs

            data = imagecodecs.jpegxr_encode(np.full((60, 80, 3), 1.5, dtype=np.float32))
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"JPEG XR kodlayıcı yok: {exc}")
        from PyQt6.QtCore import QSettings
        from PyQt6.QtWidgets import QApplication

        cls._settings_tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._settings_tmp.cleanup)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls._settings_tmp.name)
        cls.app = QApplication.instance() or QApplication([])
        cls.dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.dir.cleanup)
        cls.path = pathlib.Path(cls.dir.name) / "oyun hdr.jxr"
        cls.path.write_bytes(data)

    def test_open_displays_image_without_error_dialog(self) -> None:
        import time
        from unittest import mock

        from app.ui.message_box import MessageBox
        from app.ui.main_window import MainWindow

        win = MainWindow()
        self.addCleanup(win.deleteLater)
        with mock.patch.object(MessageBox, "critical") as critical, mock.patch.object(
            MessageBox, "warning"
        ) as warning:
            win.load_path(self.path)
            deadline = time.time() + 10
            while time.time() < deadline:
                self.app.processEvents()
                if win.canvas.pixmap_item.pixmap().width() == 80:
                    break
                time.sleep(0.02)
        self.assertEqual(win.canvas.pixmap_item.pixmap().size().width(), 80)
        self.assertEqual(win.canvas.pixmap_item.pixmap().size().height(), 60)
        critical.assert_not_called()
        warning.assert_not_called()
