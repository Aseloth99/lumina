"""SVG desteği: yükleme, boyut, saydam arka plan, küçük resim, ayrıntılar ve kayıt listesi."""

from __future__ import annotations

import gzip
import os
import pathlib
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtWidgets import QApplication

from app.core import registry, svg_loader
from app.core.exif import collect_details
from app.core.image_loader import load_pil, pil_to_qimage
from app.core.matcher import IMAGE_EXTS
from app.core.thumb_loader import load_thumb_pil

DARK_SHAPE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
    '<rect x="20" y="20" width="160" height="60" fill="#101010"/></svg>'
)
WHITE_SHAPE = DARK_SHAPE.replace("#101010", "#ffffff")
OPAQUE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
    '<rect width="100" height="100" fill="#204080"/>'
    '<rect x="30" y="30" width="40" height="40" fill="#101010"/></svg>'
)
CLIPPED = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
    '<defs><clipPath id="c"><circle cx="50" cy="50" r="30"/></clipPath></defs>'
    '<rect width="100" height="100" fill="#d03030" clip-path="url(#c)"/></svg>'
)
VIEWBOX_ONLY = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 150">'
    '<circle cx="150" cy="75" r="60" fill="#c00000"/></svg>'
)


class SvgLoaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = pathlib.Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _write(self, name: str, text: str) -> pathlib.Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_long_side_and_aspect_ratio(self) -> None:
        img = svg_loader.load_svg(self._write("a.svg", DARK_SHAPE), long_side=400)
        self.assertEqual(img.size, (400, 200))

    def test_default_render_size_is_large_enough_to_zoom(self) -> None:
        img = svg_loader.load_svg(self._write("big.svg", DARK_SHAPE))
        self.assertEqual(max(img.size), svg_loader.RENDER_LONG_SIDE)

    def test_dark_drawing_on_transparent_gets_a_light_background(self) -> None:
        img = svg_loader.load_svg(self._write("dark.svg", DARK_SHAPE), long_side=200)
        self.assertEqual(img.mode, "RGB")
        self.assertGreater(min(img.getpixel((2, 2))), 235, "corner must be light, not transparent/black")
        self.assertLess(max(img.getpixel((100, 50))), 40, "the shape itself keeps its colour")

    def test_white_drawing_stays_transparent_for_the_dark_canvas(self) -> None:
        img = svg_loader.load_svg(self._write("white.svg", WHITE_SHAPE), long_side=200)
        self.assertEqual(img.mode, "RGBA")
        self.assertEqual(img.getpixel((2, 2))[3], 0)
        self.assertEqual(img.getpixel((100, 50))[:3], (255, 255, 255))

    def test_opaque_svg_keeps_its_own_background(self) -> None:
        img = svg_loader.load_svg(self._write("opaque.svg", OPAQUE), long_side=100)
        self.assertEqual(img.mode, "RGB")
        r, g, b = img.getpixel((3, 3))
        self.assertEqual((r, g, b), (0x20, 0x40, 0x80))

    def test_viewbox_without_width_and_height(self) -> None:
        path = self._write("vb.svg", VIEWBOX_ONLY)
        self.assertEqual(svg_loader.native_size(path), (300, 150))
        self.assertEqual(svg_loader.load_svg(path, long_side=300).size, (300, 150))

    def test_svgz_is_read(self) -> None:
        path = self.dir / "z.svgz"
        path.write_bytes(gzip.compress(DARK_SHAPE.encode("utf-8")))
        self.assertEqual(svg_loader.load_svg(path, long_side=100).size, (100, 50))

    @unittest.skipIf(svg_loader.resvg_py is None, "resvg-py kurulu değil")
    def test_clip_path_is_honoured(self) -> None:
        img = svg_loader.load_svg(self._write("clip.svg", CLIPPED), long_side=100)
        self.assertGreater(min(img.getpixel((5, 5))[:3]), 235, "outside the clip circle nothing is drawn")
        self.assertEqual(img.getpixel((50, 50))[:3], (0xD0, 0x30, 0x30))

    def test_qtsvg_fallback_when_resvg_is_missing(self) -> None:
        old = svg_loader.resvg_py
        svg_loader.resvg_py = None
        try:
            img = svg_loader.load_svg(self._write("fallback.svg", DARK_SHAPE), long_side=200)
        finally:
            svg_loader.resvg_py = old
        self.assertEqual(img.size, (200, 100))
        self.assertLess(max(img.getpixel((100, 50))), 40)

    def test_qtsvg_fallback_when_resvg_fails(self) -> None:
        class Broken:
            @staticmethod
            def svg_to_bytes(**_kw):
                raise RuntimeError("boom")

        old = svg_loader.resvg_py
        svg_loader.resvg_py = Broken
        try:
            img = svg_loader.load_svg(self._write("fb2.svg", DARK_SHAPE), long_side=200)
        finally:
            svg_loader.resvg_py = old
        self.assertEqual(img.size, (200, 100))

    def test_broken_file_raises_oserror(self) -> None:
        with self.assertRaises(OSError):
            svg_loader.load_svg(self._write("bad.svg", "<svg><not closed"))
        self.assertIsNone(svg_loader.native_size(self.dir / "bad.svg"))

    def test_missing_file_raises_oserror(self) -> None:
        with self.assertRaises(OSError):
            svg_loader.load_svg(self.dir / "nope.svg")

    def test_oversized_file_is_refused_without_reading_it(self) -> None:
        path = self._write("huge.svg", DARK_SHAPE)
        old = svg_loader.MAX_FILE_BYTES
        svg_loader.MAX_FILE_BYTES = 10
        try:
            with self.assertRaises(OSError):
                svg_loader.load_svg(path)
        finally:
            svg_loader.MAX_FILE_BYTES = old

    def test_extreme_aspect_ratio_stays_within_pixel_budget(self) -> None:
        w, h = svg_loader._target_size((100000, 10), 3200)
        self.assertGreaterEqual(h, 1)
        self.assertLessEqual(w * h, svg_loader.MAX_PIXELS)

    def test_normal_image_pipeline_loads_svg(self) -> None:
        pil = load_pil(self._write("pipe.svg", DARK_SHAPE))
        self.assertEqual(max(pil.size), svg_loader.RENDER_LONG_SIDE)
        qimg = pil_to_qimage(pil)
        self.assertFalse(qimg.isNull())
        self.assertEqual(qimg.width(), pil.width)

    def test_thumbnail_is_small_and_sharp_enough(self) -> None:
        thumb = load_thumb_pil(self._write("thumb.svg", DARK_SHAPE), 160)
        self.assertEqual(max(thumb.size), 160)

    def test_details_show_the_declared_size(self) -> None:
        details = collect_details(self._write("det.svg", DARK_SHAPE))
        self.assertEqual(details.get("pixels"), "200×100")
        self.assertEqual(details.get("type"), "svg")

    def test_small_files_show_kb_not_zero_mb(self) -> None:
        from app.core.exif import _human_size

        self.assertEqual(_human_size(300), "300 B")
        self.assertEqual(_human_size(1536), "1.5 KB")
        self.assertEqual(_human_size(3 * 1024 * 1024), "3.00 MB")
        self.assertEqual(collect_details(self._write("kb.svg", DARK_SHAPE))["size"], f"{len(DARK_SHAPE.encode())} B")

    def test_svg_is_listed_and_registered_without_taking_over_the_default(self) -> None:
        self.assertIn(".svg", IMAGE_EXTS)
        self.assertIn(".svgz", IMAGE_EXTS)
        for ext in (".svg", ".svgz"):
            self.assertIn(ext, registry.EXTENSIONS)
            self.assertIn(ext, registry.NO_DEFAULT_OVERRIDE, "an editable vector file must keep its default app")


if __name__ == "__main__":
    unittest.main()
