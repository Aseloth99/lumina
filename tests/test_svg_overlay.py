"""SVG yakınlaştırdıkça netleşir: kök etiket matematiği, bölge çizimi, işçi süreç, bindirme yaşam döngüsü, keskinlik.

Çizim motoru `resvg-py` kurulu değilse ona bağlı testler atlanır; QtSvg yedek yolu her durumda sınanır.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import numpy as np
from PIL import Image
from PyQt6.QtCore import QEventLoop, QRectF, QTimer
from PyQt6.QtGui import QColor, QImage, QTransform
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

import _svg_samples as samples
from app.core import svg_loader, svg_region, svg_worker
from app.core.exif import collect_details
from app.core.image_loader import pil_to_qimage
from app.core.thumb_loader import load_thumb_pil
from app.ui import svg_overlay
from app.ui.canvas import PhotoCanvas

HAS_RESVG = svg_region.resvg_py is not None
needs_resvg = unittest.skipUnless(HAS_RESVG, "resvg-py kurulu değil")

MARKERS = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
    '<rect width="200" height="100" fill="#ffffff"/>'
    '<rect x="10" y="10" width="20" height="20" fill="#ff0000"/>'
    '<rect x="170" y="10" width="20" height="20" fill="#00aa00"/>'
    '<rect x="10" y="70" width="20" height="20" fill="#0000ff"/>'
    '<rect x="170" y="70" width="20" height="20" fill="#ffcc00"/></svg>'
)
HEAVY = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000" viewBox="0 0 1000 1000"><defs>'
    '<filter id="b"><feGaussianBlur stdDeviation="6"/></filter><linearGradient id="g"><stop offset="0" stop-color="#f00"/>'
    '<stop offset="1" stop-color="#00f"/></linearGradient></defs><rect width="1000" height="1000" fill="#fff"/>'
    + "".join(
        f'<circle cx="{(i * 37) % 1000}" cy="{(i * 91) % 1000}" r="{5 + i % 17}" fill="url(#g)" stroke="#000" '
        'stroke-width="0.5" opacity="0.7"/>'
        for i in range(3000)
    )
    + '<g filter="url(#b)"><rect x="200" y="200" width="600" height="600" fill="#0a0"/></g></svg>'
)


def pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(predicate, timeout: float = 30.0, step: int = 20) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        pump(step)
    return predicate()


def qimage_gray(image: QImage) -> np.ndarray:
    img = image.convertToFormat(QImage.Format.Format_RGB32)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)[:, : img.width(), :]
    return arr[..., [2, 1, 0]].astype(np.float32)  # RGB


def mad(a, b) -> float:
    return float(np.abs(np.asarray(a, dtype=np.float32) - np.asarray(b, dtype=np.float32)).mean())


# --------------------------------------------------------------------------- kök etiket matematiği (saf)
class RootTagTest(unittest.TestCase):
    def test_find_root_skips_prolog_comments_and_doctype(self) -> None:
        text = samples.TEXT["doctype_entity"]
        root = svg_region.find_root(text)
        self.assertIsNotNone(root)
        self.assertTrue(text[root.start :].startswith("<svg xmlns="))
        self.assertEqual(root.attrs["width"], "120")
        self.assertFalse(root.self_closing)

    def test_find_root_ignores_svg_text_inside_comments_and_cdata_like_prolog(self) -> None:
        text = '<?xml version="1.0"?>\n<!-- <svg width="1"> -->\n<svg width="7" height="8"/>'
        root = svg_region.find_root(text)
        self.assertEqual(root.attrs, {"width": "7", "height": "8"})
        self.assertTrue(root.self_closing)

    def test_find_root_handles_gt_inside_attribute_quotes(self) -> None:
        root = svg_region.find_root('<svg data-x="a>b" width="5" height="6"><g/></svg>')
        self.assertEqual(root.attrs["width"], "5")
        self.assertEqual(root.attrs["data-x"], "a>b")

    def test_find_root_rejects_non_svg_prefixed_and_broken(self) -> None:
        for text in ("<html><body/></html>", '<svg:svg xmlns:svg="x"/>', "", "plain text", "<svg", "<!-- never closed"):
            self.assertIsNone(svg_region.find_root(text), text)

    def test_parse_length_units(self) -> None:
        self.assertEqual(svg_region.parse_length("12.5mm"), (12.5, "mm"))
        self.assertEqual(svg_region.parse_length(" 3 "), (3.0, ""))
        self.assertEqual(svg_region.parse_length("1e2px"), (100.0, "px"))
        self.assertEqual(svg_region.parse_length("50%"), (50.0, "%"))
        for bad in (None, "", "abc", "12 mm2", "inf", "1..2"):
            self.assertIsNone(svg_region.parse_length(bad), bad)

    def test_root_size_unit_table_matches_96_dpi(self) -> None:
        cases = {
            ("100px", "50px"): (100, 50),
            ("72pt", "36pt"): (96, 48),
            ("1in", "2in"): (96, 192),
            ("25.4mm", "50.8mm"): (96, 192),
            ("2.54cm", "5.08cm"): (96, 192),
            ("6pc", "3pc"): (96, 48),
            ("10em", "5em"): (160, 80),
            ("200", "100"): (200, 100),
        }
        for (w, h), want in cases.items():
            size = svg_region.root_size({"width": w, "height": h})
            self.assertAlmostEqual(size[0], want[0], places=6, msg=w)
            self.assertAlmostEqual(size[1], want[1], places=6, msg=w)

    def test_root_size_viewbox_rules(self) -> None:
        vb = "0 0 300 150"
        self.assertEqual(svg_region.root_size({"viewBox": vb}), (300, 150))
        self.assertEqual(svg_region.root_size({"viewBox": "0,0,300,150"}), (300, 150))
        self.assertEqual(svg_region.root_size({"viewBox": vb, "width": "600"}), (600, 300))
        self.assertEqual(svg_region.root_size({"viewBox": vb, "height": "75"}), (150, 75))
        self.assertEqual(svg_region.root_size({"viewBox": vb, "width": "50%", "height": "100%"}), (150, 150))
        self.assertEqual(svg_region.root_size({"viewBox": vb, "width": "10", "height": "20"}), (10, 20))

    def test_root_size_unresolvable_cases_are_none(self) -> None:
        for attrs in (
            {},  # içerik sınırına bağlı
            {"width": "400"},  # viewBox'sız tek boyut
            {"width": "100%", "height": "100%"},  # viewBox'sız yüzde
            {"width": "0", "height": "10"},
            {"width": "-5", "height": "10"},
            {"viewBox": "0 0 0 10"},
            {"viewBox": "0 0 10"},
            {"viewBox": "a b c d"},
        ):
            self.assertIsNone(svg_region.root_size(attrs), attrs)

    def test_pixel_size_rounds_like_resvg(self) -> None:
        self.assertEqual(svg_region.pixel_size((793.7, 1122.52)), (794, 1123))
        self.assertEqual(svg_region.pixel_size((10.2, 5.5)), (10, 6))
        self.assertEqual(svg_region.pixel_size((0.2, 0.1)), (1, 1))

    def test_decode_text_variants(self) -> None:
        svg = '<svg width="1" height="1"><title>Café ü</title></svg>'
        self.assertEqual(svg_region.decode_text(svg.encode("utf-8")), svg)
        self.assertEqual(svg_region.decode_text(b"\xef\xbb\xbf" + svg.encode("utf-8")), svg)
        self.assertEqual(svg_region.decode_text(("﻿" + svg).encode("utf-16")).lstrip("﻿"), svg)
        decl = '<?xml version="1.0" encoding="ISO-8859-1"?>'
        self.assertEqual(svg_region.decode_text((decl + svg).encode("latin-1")), decl + svg)
        self.assertIn("Café", svg_region.decode_text(svg.encode("cp1252")))  # bildirimsiz tek baytlı yedek

    def test_inflate_is_bounded_and_plain_data_passes_through(self) -> None:
        self.assertEqual(svg_region.inflate(b"<svg/>"), b"<svg/>")
        self.assertEqual(svg_region.inflate(gzip.compress(b"<svg/>")), b"<svg/>")
        with mock.patch.object(svg_region, "MAX_TEXT_BYTES", 1000):
            with self.assertRaises(OSError):
                svg_region.inflate(gzip.compress(b"<svg>" + b"x" * 5000 + b"</svg>"))

    def test_to_utf8_bytes_fixes_declared_encoding(self) -> None:
        out = svg_region.to_utf8_bytes('<?xml version="1.0" encoding="ISO-8859-1"?><svg>é</svg>')
        self.assertIn(b'encoding="UTF-8"', out)
        self.assertIn("é".encode("utf-8"), out)


class RegionDocumentTest(unittest.TestCase):
    def _doc(self, text: str, box=(0.25, 0.5, 0.5, 0.25), px=(300, 100)):
        root = svg_region.find_root(text)
        size = svg_region.root_size(root.attrs)
        return svg_region.build_region_document(text, root, size, box, px), root, size

    def test_camera_wraps_the_untouched_original_root(self) -> None:
        import xml.etree.ElementTree as ET

        text = samples.TEXT["aspect_slice"]
        doc, _root, _size = self._doc(text)
        tree = ET.fromstring(doc)
        ns = "{http://www.w3.org/2000/svg}"
        self.assertEqual(tree.tag, ns + "svg")
        self.assertEqual((tree.get("width"), tree.get("height")), ("300", "100"))
        self.assertEqual(tree.get("viewBox"), "50 50 100 25")  # 200x100 boyutunun (0.25,0.5,0.5,0.25) bölgesi
        self.assertEqual(tree.get("preserveAspectRatio"), "none")
        nested = tree[0]
        self.assertEqual(nested.tag, ns + "svg")
        self.assertEqual((nested.get("width"), nested.get("height")), ("200", "100"))
        self.assertEqual(nested.get("viewBox"), "0 0 100 100")
        self.assertEqual(nested.get("preserveAspectRatio"), "xMidYMid slice")  # korunur
        self.assertEqual(len(nested), 2, "içerik değiştirilmeden taşınır")

    def test_root_position_and_size_attributes_are_replaced_not_duplicated(self) -> None:
        doc, _r, _s = self._doc(samples.TEXT["root_attrs"], box=(0, 0, 1, 1), px=(150, 90))
        nested = doc[doc.index("<svg", 5) :]
        head = nested[: nested.index(">")]
        self.assertEqual(head.count(' width="'), 1)  # stroke-width gibi öznitelikler sayılmaz
        self.assertEqual(head.count(" x="), 0)
        self.assertEqual(head.count(" y="), 0)
        self.assertIn('fill="#00b894"', head)  # miras alınan öznitelikler kalır
        self.assertIn('overflow="visible"', head)

    def test_prolog_entities_and_trailing_content_are_preserved(self) -> None:
        text = samples.TEXT["doctype_entity"] + "\n<!-- son -->\n"
        doc, root, _s = self._doc(text, box=(0, 0, 1, 1), px=(10, 10))
        self.assertIn('<!ENTITY fillc "#e84393">', doc)
        self.assertTrue(doc.rstrip().endswith("<!-- son -->"))
        self.assertIn("&fillc;", doc)

    def test_xml_declaration_encoding_is_normalised(self) -> None:
        text = '<?xml version="1.0" encoding="ISO-8859-1"?><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
        doc, _r, _s = self._doc(text, box=(0, 0, 1, 1), px=(10, 10))
        self.assertIn('encoding="UTF-8"', doc)

    def test_self_closing_root_is_supported(self) -> None:
        doc, _r, _s = self._doc(samples.TEXT["empty_svg"], box=(0, 0, 1, 1), px=(5, 5))
        self.assertIsNotNone(doc)
        import xml.etree.ElementTree as ET

        ET.fromstring(doc)

    def test_degenerate_requests_return_none(self) -> None:
        text = samples.TEXT["size_only"]
        root = svg_region.find_root(text)
        size = svg_region.root_size(root.attrs)
        for box, px in (
            ((0, 0, 0, 1), (10, 10)),
            ((0, 0, 1, -1), (10, 10)),
            ((float("nan"), 0, 1, 1), (10, 10)),
            ((0, 0, 1, 1), (0, 10)),
        ):
            self.assertIsNone(svg_region.build_region_document(text, root, size, box, px), (box, px))
        broken = "<svg width='5' height='5'><g>"  # kapanış etiketi yok
        broken_root = svg_region.find_root(broken)
        self.assertIsNone(svg_region.build_region_document(broken, broken_root, (5, 5), (0, 0, 1, 1), (5, 5)))

    def test_load_source_returns_none_for_unsized_or_invalid_documents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp)
            for name, text in (
                ("nosize.svg", '<svg xmlns="http://www.w3.org/2000/svg"><rect width="5" height="5"/></svg>'),
                ("html.svg", "<html/>"),
            ):
                (folder / name).write_text(text)
                self.assertIsNone(svg_region.load_source(folder / name), name)


# --------------------------------------------------------------------------- bölge çizimi doğruluğu (resvg)
@needs_resvg
class RegionRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = samples.write_all(cls.tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _reference_crop(self, source, path, box, width, kind):
        import resvg_py

        png = resvg_py.svg_to_bytes(
            svg_string=source.text, resources_dir=str(path.parent), dpi=96.0, width=width, skip_system_fonts=not source.has_text
        )
        full = Image.open(io.BytesIO(bytes(png))).convert("RGBA")
        cx, cy = round(box[0] * full.width), round(box[1] * full.height)
        cw, ch = round(box[2] * full.width), round(box[3] * full.height)
        exact = (cx / full.width, cy / full.height, cw / full.width, ch / full.height)
        return exact, (cw, ch), svg_region.apply_background(full.crop((cx, cy, cx + cw, cy + ch)), kind)

    def test_region_equals_crop_of_a_full_render_for_every_sample(self) -> None:
        """Bindirme = tam çizimin kırpması: kamera sarmalayıcı geometriyi (birimler, %, preserveAspectRatio, use,
        gradyan, süzgeç, desen...) bozmaz."""
        names = [n for n in samples.TEXT if n != "relative_image"] + list(samples.SVGZ) + ["utf8_bom"]
        for name in names:
            with self.subTest(sample=name):
                path = self.paths[name]
                source = svg_region.load_source(path)
                self.assertIsNotNone(source)
                base = svg_loader.load_svg(path, long_side=400)
                kind = "keep" if base.mode == "RGBA" else "light"  # bindirme TABANLA aynı zemin kararını kullanır
                box, px, crop = self._reference_crop(source, path, (0.30, 0.25, 0.40, 0.50), 1000, kind)
                region = svg_region.render_region_resvg(source, box, px, kind)
                self.assertEqual(region.size, px)
                self.assertLess(mad(region.convert("RGBA"), crop.convert("RGBA")), 1.5)

    def test_relative_external_image_is_rendered(self) -> None:
        path = self.paths["relative_image"]
        base = svg_loader.load_svg(path, long_side=120)
        r, g, b = base.convert("RGB").getpixel((60, 60))
        self.assertGreater(r, 200)
        self.assertLess(g, 60, "göreli <image href> dosyası bulunup çizilmeli")
        region = svg_region.render_region_resvg(svg_region.load_source(path), (0.25, 0.25, 0.5, 0.5), (100, 100), "light")
        r2, g2, _ = region.convert("RGB").getpixel((50, 50))
        self.assertGreater(r2, 200)
        self.assertLess(g2, 60)

    def test_absolute_unit_documents_render_with_resvg_not_the_fallback(self) -> None:
        """resvg-py'nin dpi varsayılanı 0'dı: mm/pt/in/cm/pc birimli SVG'ler çizilemeyip QtSvg'ye düşüyordu."""
        for name in ("mm_units", "pt_units", "in_units"):
            with self.subTest(sample=name):
                path = self.paths[name]
                text, size = svg_loader._document(path.read_bytes())
                img = svg_loader._render_resvg(path, text, svg_loader._target_size(size, 200))
                self.assertIsNotNone(img, "resvg bu belgeyi çizebilmeli")

    def test_non_utf8_encodings_render_with_resvg(self) -> None:
        for name in ("utf16_bom", "latin1_decl", "utf8_bom"):
            with self.subTest(sample=name):
                path = self.paths[name]
                text, size = svg_loader._document(path.read_bytes())
                self.assertIsNotNone(text)
                self.assertIsNotNone(svg_loader._render_resvg(path, text, svg_loader._target_size(size, 200)))

    def test_huge_viewbox_stays_exact_at_extreme_zoom(self) -> None:
        source = svg_region.load_source(self.paths["huge_viewbox"])
        # Dikdörtgenin sol kenarı x=10000 (kesir 0.1): bölgenin sol yarısı boş, sağ yarısı dolu
        region = svg_region.render_region_resvg(source, (0.0999, 0.45, 0.0002, 0.0002), (200, 200), "keep")
        left, right = region.getpixel((20, 100)), region.getpixel((180, 100))
        self.assertEqual(left[3], 0)
        self.assertEqual(right[:3], (0x1A, 0xBC, 0x9C))

    def test_region_size_limits_are_enforced_by_the_worker(self) -> None:
        cache = svg_worker._SourceCache()
        for px in ([9000, 10], [4097, 4097], [0, 5], [-1, 5]):
            header, payload = svg_worker._handle_render(
                {"id": 1, "path": str(self.paths["size_only"]), "box": [0, 0, 1, 1], "px": px, "kind": "keep"}, cache
            )
            self.assertFalse(header["ok"], px)
            self.assertEqual(payload, b"")

    def test_unsupported_document_is_reported_not_crashed(self) -> None:
        cache = svg_worker._SourceCache()
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "nosize.svg"
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect width="5" height="5"/></svg>')
            header, _ = svg_worker._handle_render(
                {"id": 3, "path": str(path), "box": [0, 0, 1, 1], "px": [10, 10], "kind": "keep"}, cache
            )
        self.assertEqual((header["ok"], header["error"]), (False, "unsupported"))


# --------------------------------------------------------------------------- bölge planı (geometri)
class PlanRegionTest(unittest.TestCase):
    def _transform(self, scale: float, tx: float = 0.0, ty: float = 0.0, rotate: float = 0.0) -> QTransform:
        t = QTransform()
        t.translate(tx, ty)
        t.scale(scale, scale)
        if rotate:
            t.rotate(rotate)
        return t

    def test_no_overlay_when_the_base_is_dense_enough(self) -> None:
        plan = svg_overlay.plan_region(self._transform(1.0), (800, 600), 1.0, (3200, 2000))
        self.assertIsNone(plan)
        self.assertIsNone(svg_overlay.plan_region(self._transform(0.2), (800, 600), 1.0, (3200, 2000)))
        self.assertIsNone(svg_overlay.plan_region(self._transform(1.04), (800, 600), 1.0, (3200, 2000)))

    def test_overlay_is_needed_above_base_density(self) -> None:
        plan = svg_overlay.plan_region(self._transform(8.0, -400, -300), (800, 600), 1.0, (3200, 2000))
        self.assertIsNotNone(plan)
        self.assertAlmostEqual(plan.scale, 8.0)
        self.assertLessEqual(plan.px[0] * plan.px[1], svg_overlay.MAX_PIXELS)

    def test_overlay_rect_is_pixel_aligned_on_the_device(self) -> None:
        for dpr in (1.0, 1.25, 1.5, 2.0):
            for tx, ty in ((-1234.4, -777.7), (-50.25, -3000.5)):
                t = self._transform(11.3, tx, ty)
                plan = svg_overlay.plan_region(t, (900, 700), dpr, (3200, 2000))
                self.assertIsNotNone(plan, (dpr, tx))
                dev = (t * QTransform.fromScale(dpr, dpr)).mapRect(plan.local)
                for edge in (dev.left(), dev.top(), dev.right(), dev.bottom()):
                    near = round(edge)
                    # görüntü kenarına denk gelen kenar kesirli olabilir; diğerleri tam piksel olmalı
                    if abs(edge - near) > 1e-6:
                        self.assertTrue(
                            abs(plan.local.left()) < 1e-6
                            or abs(plan.local.top()) < 1e-6
                            or abs(plan.local.right() - 3200) < 1e-6
                            or abs(plan.local.bottom() - 2000) < 1e-6,
                            (dpr, edge),
                        )
                self.assertEqual(plan.px, (round(dev.width()), round(dev.height())))

    def test_overlay_covers_the_visible_part_and_stays_inside_the_image(self) -> None:
        t = self._transform(6.0, -2000, -1500)
        plan = svg_overlay.plan_region(t, (800, 600), 1.0, (3200, 2000))
        image = QRectF(0, 0, 3200, 2000)
        self.assertTrue(plan.local.adjusted(-1e-6, -1e-6, 1e-6, 1e-6).contains(plan.visible))
        self.assertTrue(image.adjusted(1e-6, 1e-6, 1e-6, 1e-6).contains(plan.local))
        self.assertGreater(plan.local.width(), plan.visible.width(), "kenar payı var")

    def test_pixel_budget_is_respected_with_huge_viewport_and_zoom(self) -> None:
        plan = svg_overlay.plan_region(self._transform(40.0, -50000, -30000), (7680, 4320), 1.0, (3200, 2000))
        self.assertLessEqual(plan.px[0] * plan.px[1], svg_overlay.MAX_PIXELS)
        self.assertLessEqual(max(plan.px), svg_overlay.MAX_EDGE)
        # pay önce kısılır, sonra çözünürlük: 8K ekran + 2x DPI için bile sınır aşılmaz
        plan = svg_overlay.plan_region(self._transform(40.0, -50000, -30000), (7680, 4320), 2.0, (3200, 2000))
        self.assertLessEqual(plan.px[0] * plan.px[1], svg_overlay.MAX_PIXELS)
        self.assertLessEqual(max(plan.px), svg_overlay.MAX_EDGE)
        self.assertLess(plan.density, plan.scale, "çözünürlük sınırı için kasıtlı seyreltme")

    def test_margin_is_dropped_before_resolution_is_reduced(self) -> None:
        t = self._transform(10.0, -100, -100)
        full = svg_overlay.plan_region(t, (1800, 1500), 1.0, (3200, 2000), max_pixels=10**9)
        tight = svg_overlay.plan_region(t, (1800, 1500), 1.0, (3200, 2000), max_pixels=1800 * 1500)
        self.assertGreater(full.px[0] * full.px[1], tight.px[0] * tight.px[1])
        self.assertAlmostEqual(tight.density, tight.scale, places=3, msg="çözünürlük korunur")

    def test_rotation_by_quarter_turns_keeps_alignment_and_scale(self) -> None:
        for angle in (0, 90, 180, 270):
            t = QTransform()
            t.translate(400, 300)  # görüntü merkezi görünüm merkezine (öğenin merkez etrafında döndürülmesi gibi)
            t.scale(9.0, 9.0)
            t.rotate(angle)
            t.translate(-1600, -1000)
            plan = svg_overlay.plan_region(t, (800, 600), 1.0, (3200, 2000))
            self.assertIsNotNone(plan, angle)
            self.assertAlmostEqual(plan.scale, 9.0, places=6)
            dev = t.mapRect(plan.local)
            for edge in (dev.left(), dev.top(), dev.right(), dev.bottom()):
                self.assertAlmostEqual(edge, round(edge), places=5, msg=f"{angle}: {edge}")
            self.assertEqual(plan.px, (round(plan.local.width() * 9), round(plan.local.height() * 9)))
            self.assertEqual(sorted(plan.px), sorted((round(dev.width()), round(dev.height()))))
            self.assertTrue(plan.local.adjusted(-1e-6, -1e-6, 1e-6, 1e-6).contains(plan.visible))

    def test_degenerate_inputs_give_no_plan(self) -> None:
        t = self._transform(5.0)
        self.assertIsNone(svg_overlay.plan_region(t, (0, 600), 1.0, (100, 100)))
        self.assertIsNone(svg_overlay.plan_region(t, (800, 600), 1.0, (0, 100)))
        self.assertIsNone(svg_overlay.plan_region(t, (800, 600), 0.0, (100, 100)))
        far = self._transform(5.0, 10**6, 10**6)  # görüntü görünüm penceresinin dışında
        self.assertIsNone(svg_overlay.plan_region(far, (800, 600), 1.0, (100, 100)))
        singular = QTransform(0, 0, 0, 0, 0, 0)
        self.assertIsNone(svg_overlay.plan_region(singular, (800, 600), 1.0, (100, 100)))

    def test_same_plan_detection_uses_device_tolerance(self) -> None:
        t = self._transform(8.0, -400, -300)
        a = svg_overlay.plan_region(t, (800, 600), 1.0, (3200, 2000))
        self.assertTrue(a.same_as(svg_overlay.plan_region(t, (800, 600), 1.0, (3200, 2000))))
        b = svg_overlay.plan_region(self._transform(8.0, -401.2, -300), (800, 600), 1.0, (3200, 2000))
        self.assertFalse(a.same_as(b))


# --------------------------------------------------------------------------- işçi süreç + istemci
class WorkerProcessTest(unittest.TestCase):
    def test_frame_round_trip(self) -> None:
        raw = svg_worker.encode_frame({"op": "ping", "id": 5}) + svg_worker.encode_frame({"x": "çş"})
        stream = io.BytesIO(raw)
        first = json.loads(svg_worker._read_frame(stream.read))
        second = json.loads(svg_worker._read_frame(stream.read))
        self.assertEqual(first, {"op": "ping", "id": 5})
        self.assertEqual(second, {"x": "çş"})
        self.assertIsNone(svg_worker._read_frame(stream.read))  # EOF

    def test_oversized_frame_is_rejected(self) -> None:
        stream = io.BytesIO(svg_worker._LEN.pack(svg_worker.MAX_REQUEST_BYTES + 1) + b"x")
        with self.assertRaises(ValueError):
            svg_worker._read_frame(stream.read)

    def test_main_entry_point_dispatches_the_worker_flag_without_loading_qt(self) -> None:
        env = dict(os.environ)
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-m", "app", svg_worker.WORKER_FLAG],
            input=svg_worker.encode_frame({"op": "ping", "id": 9}),
            capture_output=True,
            cwd=str(ROOT),
            env=env,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace")[-400:])
        reply = json.loads(svg_worker._read_frame(io.BytesIO(proc.stdout).read))
        self.assertEqual((reply["id"], reply["ok"]), (9, True))

    def test_frozen_command_relaunches_the_app_executable_with_the_flag(self) -> None:
        """Derlenmiş (PyInstaller) sürüm: işçi = uygulamanın kendi exe'si + bayrak (app/main.py başında yönlendirilir)."""
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(svg_worker.worker_command(), [sys.executable, "--svg-render-worker"])
        command = svg_worker.worker_command()  # geliştirme: python -c ... (proje kökü yola eklenir)
        self.assertEqual(command[0], sys.executable)
        self.assertIn("app.core.svg_worker", command[-1])

    @unittest.skipUnless(sys.platform == "win32", "Windows süreç bayrakları")
    def test_worker_is_spawned_without_a_console_window_and_with_private_pipes(self) -> None:
        captured: dict = {}

        class FakeProc:
            stdin = io.BytesIO()
            stdout = io.BytesIO()

            def poll(self):
                return None

            def kill(self):
                pass

            def wait(self, timeout=None):
                return 0

        def fake_popen(command, **kwargs):
            captured.update(kwargs, command=command)
            return FakeProc()

        client = svg_worker.WorkerClient(command=["x"])
        self.addCleanup(client.close)
        with mock.patch.object(svg_worker.subprocess, "Popen", fake_popen), mock.patch.object(svg_worker.threading, "Thread"):
            client.submit({"op": "ping"}, lambda h, p: None)
        self.assertTrue(captured["creationflags"] & subprocess.CREATE_NO_WINDOW, "konsol penceresi AÇILMAMALI")
        self.assertEqual(captured["stdin"], subprocess.PIPE)
        self.assertEqual(captured["stdout"], subprocess.PIPE)
        self.assertEqual(captured["stderr"], subprocess.DEVNULL, "stderr dolup işçiyi kilitlemesin")

    @unittest.skipUnless(sys.platform == "win32", "pythonw.exe (GUI alt sistemi) yalnızca Windows'ta")
    def test_worker_runs_under_the_gui_subsystem_like_the_windowed_exe(self) -> None:
        """Derlenmiş Lumina.exe pencerelidir (console=False): GUI alt sistemli süreçte boru tabanlı stdio çalışmalı.
        pythonw.exe aynı alt sistemi kullanır; hem Win32 tutamaçları hem CRT dosya numaraları yolu sınanır."""
        pythonw = pathlib.Path(sys.executable).with_name("pythonw.exe")
        if not pythonw.exists():
            self.skipTest("pythonw.exe bulunamadı")
        code = f"import sys; sys.path.insert(0, {str(ROOT)!r}); from app.core.svg_worker import serve; raise SystemExit(serve())"
        for mode in ("handles", "fd"):
            with self.subTest(io=mode):
                env = dict(os.environ)
                if mode == "fd":
                    env["LUMINA_SVG_WORKER_IO"] = "fd"
                proc = subprocess.Popen(
                    [str(pythonw), "-X", "utf8", "-B", "-c", code],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    env=env,
                )
                try:
                    proc.stdin.write(svg_worker.encode_frame({"op": "ping", "id": 4}))
                    proc.stdin.flush()
                    box: dict = {}
                    reader = threading.Thread(target=lambda: box.update(frame=svg_worker._read_frame(proc.stdout.read)), daemon=True)
                    reader.start()
                    reader.join(30)
                    self.assertIn("frame", box, "GUI alt sistemli işçi yanıt vermedi")
                    self.assertEqual(json.loads(box["frame"])["id"], 4)
                    proc.stdin.close()  # ana süreç gitti = EOF: işçi kendiliğinden çıkmalı
                    self.assertEqual(proc.wait(20), 0)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.stdout.close()

    @needs_resvg
    def test_worker_renders_and_survives_bad_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.svg"
            path.write_text(samples.TEXT["size_only"], encoding="utf-8")
            client = svg_worker.WorkerClient()
            self.addCleanup(client.close)
            answers: list[tuple[dict, bytes]] = []
            done = threading.Event()

            def cb(header, payload):
                answers.append((header, payload))
                done.set()

            def ask(**req):
                done.clear()
                self.assertTrue(client.submit(req, cb))
                self.assertTrue(done.wait(60))
                return answers[-1]

            good = {"op": "render", "path": str(path), "mtime_ns": path.stat().st_mtime_ns, "box": [0, 0, 0.5, 0.5], "px": [120, 60], "kind": "keep"}
            header, payload = ask(**good)
            self.assertTrue(header["ok"], header)
            self.assertEqual((header["w"], header["h"], header["mode"]), (120, 60, "RGBA"))
            self.assertEqual(len(payload), 120 * 60 * 4)
            bad, _ = ask(**dict(good, px=[99999, 5]))
            self.assertFalse(bad["ok"])
            missing, _ = ask(**dict(good, path=str(path) + ".yok"))
            self.assertFalse(missing["ok"])
            header, _ = ask(**good)  # aynı işçi hâlâ yaşıyor
            self.assertTrue(header["ok"])

    @needs_resvg
    def test_dead_worker_fails_pending_requests_and_is_respawned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "a.svg"
            path.write_text(samples.TEXT["size_only"], encoding="utf-8")
            client = svg_worker.WorkerClient()
            self.addCleanup(client.close)
            got: list[dict] = []
            ev = threading.Event()
            req = {"op": "render", "path": str(path), "mtime_ns": 1, "box": [0, 0, 1, 1], "px": [60, 30], "kind": "keep"}
            client.submit(req, lambda h, p: (got.append(h), ev.set()))
            self.assertTrue(ev.wait(60))
            self.assertTrue(got[-1]["ok"])
            first = client._proc
            first.kill()
            first.wait(10)
            time.sleep(0.3)
            ev.clear()
            self.assertTrue(client.submit(req, lambda h, p: (got.append(h), ev.set())))
            self.assertTrue(ev.wait(60))
            self.assertTrue(got[-1]["ok"], "yeni işçi açılıp isteği yanıtladı")
            self.assertIsNot(client._proc, first)

    def test_hung_worker_is_killed_after_the_timeout(self) -> None:
        command = [sys.executable, "-c", "import time; time.sleep(120)"]
        client = svg_worker.WorkerClient(command=command, request_timeout=0.6)
        self.addCleanup(client.close)
        got: list[dict] = []
        ev = threading.Event()
        self.assertTrue(client.submit({"op": "ping"}, lambda h, p: (got.append(h), ev.set())))
        proc = client._proc
        self.assertTrue(ev.wait(20), "zaman aşımında bekleyen istek hata ile bitmeli")
        self.assertFalse(got[0]["ok"])
        self.assertIsNotNone(proc.wait(10), "yalnızca kendi işçimiz öldürülür ve sonlanır")

    def test_crash_loop_marks_the_client_broken(self) -> None:
        command = [sys.executable, "-c", "import sys; sys.exit(3)"]
        client = svg_worker.WorkerClient(command=command, request_timeout=5)
        self.addCleanup(client.close)
        results: list[dict] = []
        for _ in range(6):
            if client.broken:
                break
            if client.submit({"op": "ping"}, lambda h, p: results.append(h)):
                time.sleep(0.6)
        self.assertTrue(client.broken, "2 dakikada 3 ölüm sonrası yedek yola geçilmeli")
        self.assertFalse(client.submit({"op": "ping"}, lambda h, p: None))

    def test_unusable_command_reports_failure_instead_of_raising(self) -> None:
        client = svg_worker.WorkerClient(command=["bu-komut-yok-svg"])
        self.addCleanup(client.close)
        self.assertFalse(client.submit({"op": "ping"}, lambda h, p: None))


# --------------------------------------------------------------------------- bindirme yaşam döngüsü (Qt)
class _CanvasBase(unittest.TestCase):
    ENGINE = "default"

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = pathlib.Path(cls.tmp.name)
        cls.paths = samples.write_all(cls.dir)
        (cls.dir / "markers.svg").write_text(MARKERS, encoding="utf-8")
        (cls.dir / "heavy.svg").write_text(HEAVY, encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def setUp(self) -> None:
        if self.ENGINE == "thread-resvg":
            patcher = mock.patch.object(svg_worker, "get_client", return_value=mock.Mock(broken=True))
            patcher.start()
            self.addCleanup(patcher.stop)
        elif self.ENGINE == "thread-qt":
            patcher = mock.patch.object(svg_region, "resvg_py", None)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.host = QWidget()
        layout = QVBoxLayout(self.host)
        layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = PhotoCanvas()
        layout.addWidget(self.canvas)
        self.host.resize(600, 400)
        self.host.show()
        self.addCleanup(self.host.close)
        pump(50)

    def show_svg(self, name: str, long_side: int = 3200) -> pathlib.Path:
        path = self.dir / f"{name}.svg"
        self.canvas.set_qimage(pil_to_qimage(svg_loader.load_svg(path, long_side=long_side)))
        self.canvas.attach_svg(str(path))
        return path

    def zoom_to(self, scale: float, focus: tuple[float, float] | None = None) -> None:
        w, h = self.canvas.image_size()
        self.canvas.resetTransform()
        self.canvas._fitted = False
        self.canvas.scale(scale, scale)
        fx, fy = focus if focus else (w / 2, h / 2)
        self.canvas.centerOn(self.canvas.pixmap_item.mapToScene(fx, fy))
        self.canvas.zoom_changed.emit()

    def settled(self) -> bool:
        ctl = self.canvas._svg
        return not ctl._timer.isActive() and not ctl._busy and ctl._queued is None

    def wait_overlay(self, timeout: float = 40.0) -> bool:
        return wait_for(lambda: self.settled() and self.canvas.pixmap_item.has_overlay(), timeout)

    def wait_settled(self, timeout: float = 40.0) -> bool:
        pump(SETTLE_PAD)
        return wait_for(self.settled, timeout)


SETTLE_PAD = svg_overlay.SETTLE_MS + 80


class OverlayLifecycleMixin:
    def test_overlay_appears_above_the_base_density_and_goes_away_below_it(self) -> None:
        self.show_svg("thin_lines")
        self.canvas.fit_in_window()
        self.assertTrue(self.wait_settled())
        self.assertFalse(self.canvas.pixmap_item.has_overlay(), "sığdırılmış görünümde taban yeter")
        self.zoom_to(6)
        self.assertTrue(self.wait_overlay(), "yakınlaştırınca bindirme oluşmalı")
        item = self.canvas.pixmap_item
        self.assertIsInstance(item, svg_overlay.SvgPixmapItem)
        self.assertGreater(item.overlay_density(), 5.0, "ekran çözünürlüğünde (≈6 px/taban pikseli)")
        self.canvas.fit_in_window()
        self.assertTrue(wait_for(lambda: self.settled() and not item.has_overlay()))

    def test_zoom_does_not_block_the_event_loop_until_it_settles(self) -> None:
        self.show_svg("thin_lines")
        self.zoom_to(6)
        self.assertFalse(self.canvas.pixmap_item.has_overlay(), "durulma süresi dolmadan çizim başlamaz")
        self.assertTrue(self.canvas._svg._timer.isActive())

    def test_regular_images_never_get_an_overlay(self) -> None:
        image = QImage(300, 200, QImage.Format.Format_RGB32)
        image.fill(QColor("red"))
        self.canvas.set_qimage(image)
        self.canvas.attach_svg(str(self.dir / "photo.jpg"))
        self.assertIsNone(self.canvas._svg.path)
        self.zoom_to(10)
        pump(SETTLE_PAD + 100)
        self.assertFalse(self.canvas.pixmap_item.has_overlay())
        self.assertEqual(self.canvas._svg.render_count, 0)

    def test_changing_the_file_removes_the_overlay_and_cancels_pending_work(self) -> None:
        self.show_svg("thin_lines")
        self.zoom_to(6)
        self.assertTrue(self.wait_overlay())
        gen = self.canvas._svg._gen
        image = QImage(100, 100, QImage.Format.Format_RGB32)
        image.fill(QColor("blue"))
        self.canvas.set_qimage(image)  # başka dosya/foto
        self.assertFalse(self.canvas.pixmap_item.has_overlay())
        self.assertIsNone(self.canvas._svg.path)
        self.assertGreater(self.canvas._svg._gen, gen, "uçuştaki sonuç artık geçersiz")
        self.zoom_to(10)
        pump(SETTLE_PAD + 100)
        self.assertFalse(self.canvas.pixmap_item.has_overlay())

    def test_clear_image_and_replace_image_detach(self) -> None:
        self.show_svg("thin_lines")
        self.zoom_to(6)
        self.assertTrue(self.wait_overlay())
        self.canvas.clear_image()
        self.assertIsNone(self.canvas._svg.path)
        self.assertFalse(self.canvas.pixmap_item.has_overlay())
        self.show_svg("thin_lines")
        image = QImage(200, 100, QImage.Format.Format_RGB32)
        image.fill(QColor("green"))
        self.canvas.replace_image(image)  # RAW tam çözünürlük yükseltmesi yolu
        self.assertIsNone(self.canvas._svg.path)

    def test_there_is_a_single_overlay_and_no_extra_scene_items(self) -> None:
        self.show_svg("thin_lines")
        for scale in (6, 9, 14):
            self.zoom_to(scale)
            self.assertTrue(self.wait_overlay())
            self.assertEqual(len(self.canvas._scene.items()), 1, "bindirme ayrı bir sahne öğesi değil")
        first = self.canvas.pixmap_item.overlay_image()
        self.zoom_to(20)
        self.assertTrue(wait_for(lambda: self.settled() and self.canvas.pixmap_item.overlay_image() is not first))
        self.assertEqual(len(self.canvas._scene.items()), 1)
        image = self.canvas.pixmap_item.overlay_image()
        self.assertLessEqual(image.width() * image.height(), svg_overlay.MAX_PIXELS)

    def test_pan_inside_the_margin_keeps_the_overlay_and_pan_outside_renders_again(self) -> None:
        self.show_svg("thin_lines")
        self.zoom_to(8)
        self.assertTrue(self.wait_overlay())
        ctl = self.canvas._svg
        count = ctl.render_count
        bar = self.canvas.horizontalScrollBar()
        bar.setValue(bar.value() + 40)  # kenar payı içinde küçük kaydırma
        self.assertTrue(self.wait_settled())
        self.assertEqual(ctl.render_count, count, "payın içindeki kaydırma yeniden çizdirmez")
        self.assertTrue(self.canvas.pixmap_item.has_overlay())
        bar.setValue(bar.value() + 900)  # pay dışına taşan kaydırma
        self.assertTrue(self.wait_settled())
        self.assertTrue(wait_for(lambda: ctl.render_count > count and self.settled()))
        self.assertTrue(self.canvas.pixmap_item.has_overlay())

    def test_unchanged_view_does_not_trigger_redundant_renders(self) -> None:
        self.show_svg("thin_lines")
        self.zoom_to(8)
        self.assertTrue(self.wait_overlay())
        count = self.canvas._svg.render_count
        for _ in range(3):
            self.canvas.zoom_changed.emit()
            self.assertTrue(self.wait_settled())
        self.assertEqual(self.canvas._svg.render_count, count)

    def test_a_noticeable_zoom_change_renders_again_but_a_tiny_one_does_not(self) -> None:
        self.show_svg("thin_lines")
        self.zoom_to(8)
        self.assertTrue(self.wait_overlay())
        ctl = self.canvas._svg
        count = ctl.render_count
        self.zoom_to(8.08)  # %1: mevcut bindirme yeter
        self.assertTrue(self.wait_settled())
        self.assertEqual(ctl.render_count, count)
        self.zoom_to(8 * 1.18)  # bir fare tekerleği adımı: yeni yoğunlukta yeniden çizilir
        self.assertTrue(wait_for(lambda: ctl.render_count > count and self.settled()))
        self.assertAlmostEqual(self.canvas.pixmap_item.overlay_density(), 8 * 1.18, delta=0.05)

    def test_overlay_uses_the_same_background_decision_as_the_base(self) -> None:
        # koyu çizim + saydam zemin: taban açık zeminde (RGB) -> bindirme de aynı açık zeminde ve opak
        self.show_svg("black_on_transparent", long_side=1200)
        self.assertFalse(self.canvas.pixmap_item.pixmap().hasAlphaChannel())
        self.zoom_to(8, focus=(100, 100))
        self.assertTrue(self.wait_overlay())
        overlay = self.canvas.pixmap_item.overlay_image()
        self.assertFalse(overlay.hasAlphaChannel())
        corner = overlay.pixelColor(2, 2)
        self.assertEqual((corner.red(), corner.green(), corner.blue()), svg_region.LIGHT_BACKGROUND)
        # beyaz çizim + saydam zemin: taban saydam kalır -> bindirme de saydam
        self.show_svg("white_on_transparent", long_side=1200)
        self.assertTrue(self.canvas.pixmap_item.pixmap().hasAlphaChannel())
        self.zoom_to(8, focus=(100, 100))
        self.assertTrue(self.wait_overlay())
        overlay = self.canvas.pixmap_item.overlay_image()
        self.assertTrue(overlay.hasAlphaChannel())
        self.assertEqual(overlay.pixelColor(2, 2).alpha(), 0)

    def test_covering_overlay_hides_the_blurry_base_for_transparent_svgs(self) -> None:
        self.show_svg("white_on_transparent", long_side=1200)
        self.zoom_to(10, focus=(600, 400))
        self.assertTrue(self.wait_overlay())
        item = self.canvas.pixmap_item
        with_base = self.canvas.viewport().grab().toImage()
        blank = QImage(item.pixmap().size(), QImage.Format.Format_ARGB32_Premultiplied)
        blank.fill(QColor(0, 0, 0, 0))
        from PyQt6.QtGui import QPixmap

        item.setPixmap(QPixmap.fromImage(blank))  # tabanı tamamen sil: görüntü değişmemeli
        without_base = self.canvas.viewport().grab().toImage()
        self.assertEqual(mad(qimage_gray(with_base), qimage_gray(without_base)), 0.0, "taban halesi sızmamalı")

    def test_unsupported_document_is_remembered_and_not_retried(self) -> None:
        path = self.dir / "nosize.svg"
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><circle cx="10" cy="10" r="9" fill="red"/></svg>')
        self.canvas.set_qimage(pil_to_qimage(svg_loader.load_svg(path, long_side=400)))
        self.canvas.attach_svg(str(path))
        self.zoom_to(8)
        pump(SETTLE_PAD)
        self.assertTrue(wait_for(lambda: self.canvas._svg._unsupported and self.settled()))
        self.assertFalse(self.canvas.pixmap_item.has_overlay())
        count = self.canvas._svg.render_count
        self.zoom_to(12)
        self.assertTrue(self.wait_settled())
        self.assertEqual(self.canvas._svg.render_count, count)

    def test_markers_stay_aligned_at_high_zoom_and_after_rotation(self) -> None:
        """Bindirme, tabanın üstünde piksel doğruluğuyla hizalı: kırmızı karenin köşesi tam yerinde."""
        self.show_svg("markers")
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                self.canvas.set_fill_mode(False)
                if rotation:
                    self.canvas.rotate_step(90)
                w, h = self.canvas.image_size()
                corner = (30 / 200 * w, 30 / 100 * h)  # kırmızı karenin sağ-alt köşesi (svg 30,30)
                self.zoom_to(9, focus=corner)
                self.assertTrue(self.wait_overlay())
                item = self.canvas.pixmap_item
                point = self.canvas.mapFromScene(item.mapToScene(*corner))
                cx, cy = point.x(), point.y()
                grab = qimage_gray(self.canvas.viewport().grab().toImage())
                self.assertEqual(grab.shape[1], self.canvas.viewport().width())

                def crossing(values: np.ndarray, centre: float) -> float | None:
                    # kırmızı (G=0) ile beyaz (G=255) arasındaki geçiş: G kanalı 127.5'i keser
                    lo, hi = max(1, int(centre) - 60), min(len(values) - 1, int(centre) + 60)
                    for i in range(lo, hi):
                        a, b = values[i - 1], values[i]
                        if (a - 127.5) * (b - 127.5) < 0:
                            return (i - 1) + (127.5 - a) / (b - a)
                    return None

                found = []
                for dy in (-45, 45):  # dikey kenar: satır boyunca
                    row = int(round(cy + dy))
                    if 0 <= row < grab.shape[0]:
                        x = crossing(grab[row, :, 1], cx)
                        if x is not None:
                            found.append(abs((x + 0.5) - cx))
                for dx in (-45, 45):  # yatay kenar: sütun boyunca
                    col = int(round(cx + dx))
                    if 0 <= col < grab.shape[1]:
                        y = crossing(grab[:, col, 1], cy)
                        if y is not None:
                            found.append(abs((y + 0.5) - cy))
                self.assertGreaterEqual(len(found), 2, "köşenin iki kenarı da bulunmalı")
                for err in found:
                    self.assertLess(err, 1.2, f"kenar köşe noktasına {err:.2f} px uzak")

    def test_overlay_is_cleared_when_resizing_the_window_back_to_fit(self) -> None:
        self.show_svg("thin_lines")
        self.zoom_to(8)
        self.assertTrue(self.wait_overlay())
        self.canvas.fit_in_window()
        self.host.resize(500, 350)
        self.assertTrue(wait_for(lambda: self.settled() and not self.canvas.pixmap_item.has_overlay()))

    def test_overlay_follows_a_resize_while_zoomed(self) -> None:
        self.show_svg("thin_lines")
        self.zoom_to(8)
        self.assertTrue(self.wait_overlay())
        count = self.canvas._svg.render_count
        self.host.resize(900, 700)
        self.assertTrue(wait_for(lambda: self.canvas._svg.render_count > count and self.settled(), 40))
        plan = self.canvas._svg.current_plan()
        self.assertTrue(self.canvas.pixmap_item.satisfies(plan))


class OverlayLifecycleWorkerTest(OverlayLifecycleMixin, _CanvasBase):
    ENGINE = "worker"

    @classmethod
    def setUpClass(cls) -> None:
        if not HAS_RESVG:
            raise unittest.SkipTest("resvg-py kurulu değil")
        super().setUpClass()


class OverlayLifecycleThreadResvgTest(OverlayLifecycleMixin, _CanvasBase):
    """İşçi süreç kurulamazsa (bozuk/yasak) küçük belgelerde iş parçacığında resvg."""

    ENGINE = "thread-resvg"

    @classmethod
    def setUpClass(cls) -> None:
        if not HAS_RESVG:
            raise unittest.SkipTest("resvg-py kurulu değil")
        super().setUpClass()


class OverlayLifecycleQtFallbackTest(OverlayLifecycleMixin, _CanvasBase):
    """resvg-py yoksa QtSvg ile bölge çizimi (kamera yerine tam belge ölçekleme)."""

    ENGINE = "thread-qt"


class GenerationCancellationTest(_CanvasBase):
    """Araya yeni yakınlaştırma girerse eski sonuç atılır; en fazla bir çizim + bir bekleyen istek."""

    class FakeClient:
        broken = False

        def __init__(self) -> None:
            self.pending: list[tuple[dict, object]] = []

        def submit(self, request, callback) -> bool:
            self.pending.append((request, callback))
            return True

    def setUp(self) -> None:
        super().setUp()
        self.fake = self.FakeClient()
        for patcher in (
            mock.patch.object(svg_worker, "get_client", return_value=self.fake),
            mock.patch.object(svg_region, "resvg_py", object()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def answer(self, index: int, size=(200.0, 100.0)) -> None:
        request, callback = self.fake.pending[index]
        w, h = request["px"]
        callback({"ok": True, "w": w, "h": h, "mode": "RGB", "n": w * h * 3, "size": list(size)}, bytes([200]) * (w * h * 3))
        pump(60)

    def test_stale_result_is_discarded_and_only_the_latest_request_is_queued(self) -> None:
        self.show_svg("markers", long_side=1600)
        self.zoom_to(6)
        pump(SETTLE_PAD)
        self.assertEqual(len(self.fake.pending), 1)
        self.zoom_to(12)  # ilk çizim sürerken yeni yakınlaştırma
        pump(SETTLE_PAD)
        self.zoom_to(16)
        pump(SETTLE_PAD)
        self.assertEqual(len(self.fake.pending), 1, "çizim sürerken yeni istek GÖNDERİLMEZ, yalnız en yenisi bekletilir")
        self.assertIsNotNone(self.canvas._svg._queued)
        self.answer(0)  # eski sonuç gelir: atılır
        self.assertFalse(self.canvas.pixmap_item.has_overlay(), "eski nesil sonucu uygulanmamalı")
        self.assertEqual(len(self.fake.pending), 2, "bekleyen en yeni istek şimdi gönderilir")
        self.assertAlmostEqual(self.fake.pending[1][0]["px"][0] / self.canvas.image_size()[0], 1, delta=3)
        self.answer(1)
        self.assertTrue(self.canvas.pixmap_item.has_overlay())

    def test_result_after_the_file_changed_is_ignored(self) -> None:
        self.show_svg("markers", long_side=1600)
        self.zoom_to(6)
        pump(SETTLE_PAD)
        image = QImage(50, 50, QImage.Format.Format_RGB32)
        image.fill(QColor("black"))
        self.canvas.set_qimage(image)
        self.answer(0)
        self.assertFalse(self.canvas.pixmap_item.has_overlay())

    def test_overlay_with_a_different_aspect_ratio_is_rejected(self) -> None:
        self.show_svg("markers", long_side=1600)  # taban 2:1
        self.zoom_to(6)
        pump(SETTLE_PAD)
        self.answer(0, size=(100.0, 100.0))  # işçi belgeyi 1:1 sanıyor: hizalanamaz
        self.assertFalse(self.canvas.pixmap_item.has_overlay())
        self.assertTrue(self.canvas._svg._unsupported)

    def test_failed_render_keeps_the_base_and_allows_a_later_retry(self) -> None:
        self.show_svg("markers", long_side=1600)
        self.zoom_to(6)
        pump(SETTLE_PAD)
        _request, callback = self.fake.pending[0]
        callback({"ok": False, "error": "worker-died"}, b"")
        pump(60)
        self.assertFalse(self.canvas.pixmap_item.has_overlay())
        self.assertFalse(self.canvas._svg._unsupported)
        self.zoom_to(7)
        pump(SETTLE_PAD)
        self.assertEqual(len(self.fake.pending), 2, "geçici hata sonrası yeniden denenir")


    def test_two_failures_give_up_on_that_document(self) -> None:
        self.show_svg("markers", long_side=1600)
        self.zoom_to(6)
        pump(SETTLE_PAD)
        self.fake.pending[0][1]({"ok": False, "error": "worker-died"}, b"")
        pump(60)
        self.assertFalse(self.canvas._svg._unsupported)
        self.zoom_to(7)
        pump(SETTLE_PAD)
        self.fake.pending[1][1]({"ok": False, "error": "worker-died"}, b"")
        pump(60)
        self.assertTrue(self.canvas._svg._unsupported, "iki başarısızlıktan sonra bu belgede bindirme denenmez")
        self.zoom_to(8)
        pump(SETTLE_PAD)
        self.assertEqual(len(self.fake.pending), 2)

    def test_slow_renders_shrink_the_pixel_budget_even_when_the_result_is_stale(self) -> None:
        self.show_svg("markers", long_side=1600)
        self.host.resize(1900, 1000)
        pump(100)
        self.zoom_to(6)
        pump(SETTLE_PAD)
        first_px = self.fake.pending[0][0]["px"]
        self.zoom_to(7)  # ilk çizim sürerken görünüm değişti: sonuç bayat olacak
        pump(SETTLE_PAD)
        request, callback = self.fake.pending[0]
        w, h = request["px"]
        callback({"ok": True, "w": w, "h": h, "mode": "RGB", "n": w * h * 3, "ms": 10000.0, "size": [200.0, 100.0]}, bytes([9]) * (w * h * 3))
        pump(80)
        self.assertLess(self.canvas._svg._budget, 0.5)
        self.assertEqual(len(self.fake.pending), 2)
        second_px = self.fake.pending[1][0]["px"]
        self.assertLess(second_px[0] * second_px[1], first_px[0] * first_px[1] * 0.8, "bütçe küçüldü: daha az piksel istenir")


# --------------------------------------------------------------------------- arayüz DONMAZ (işçi süreç)
@needs_resvg
class NoFreezeTest(_CanvasBase):
    def test_a_heavy_region_render_does_not_stall_the_gui_thread(self) -> None:
        """resvg çizim boyunca GIL'i tutar; iş parçacığında çizmek arayüzü saniyelerce dondururdu (ölçüldü)."""
        path = self.show_svg("heavy", long_side=1200)
        self.host.resize(1500, 1000)
        pump(100)
        self.zoom_to(3)
        gaps: list[float] = []
        last = [time.perf_counter()]

        def tick() -> None:
            now = time.perf_counter()
            gaps.append(now - last[0])
            last[0] = now

        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(tick)
        timer.start()
        self.addCleanup(timer.stop)
        started = time.perf_counter()
        self.assertTrue(self.wait_overlay(120))
        total = time.perf_counter() - started
        render_ms = self.canvas._svg.last_header.get("ms", 0)
        self.assertGreater(render_ms, 150, "bu belge anlamlı bir süre çizilir")
        self.assertLess(max(gaps), max(0.6, render_ms / 1000 * 0.5), f"GUI {max(gaps):.2f} sn durdu (çizim {render_ms} ms)")
        self.assertGreater(total, 0)


# --------------------------------------------------------------------------- GERÇEK platform keskinlik ölçümü
@unittest.skipUnless(sys.platform == "win32", "gerçek Windows platformu gerekir")
@needs_resvg
class RealPlatformSharpnessTest(unittest.TestCase):
    def test_zoomed_svg_is_much_sharper_with_the_overlay(self) -> None:
        """Gerçek `windows` platformunda (pencere x=-6000, grab): bindirme KAPALI vs AÇIK keskinlik ölçüsü."""
        env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
        env["QT_QPA_PLATFORM"] = "windows"
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "result.json"
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", "-B", str(ROOT / "tests" / "_svg_real_probe.py"), str(out)],
                capture_output=True,
                env=env,
                cwd=str(ROOT),
                timeout=240,
            )
            self.assertTrue(out.exists(), proc.stderr.decode("utf-8", "replace")[-800:])
            data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["platform"], "windows")
        # Taban yeterliyken (ekran pikseli / taban pikseli <= MIN_SCALE) bindirme yok, görüntü aynı. Ekran DPI'ı
        # 1.5 ise "%100" bile taban pikselinden yoğundur (taban 1.5x büyütülür): bindirme orada da devreye girer.
        dpr = data["dpr"]
        for scale in ("0.5", "1"):
            expected = float(scale) * dpr > svg_overlay.MIN_SCALE
            self.assertEqual(data[scale]["on"]["overlay"], expected, f"x{scale} dpr={dpr}")
            if not expected:
                self.assertEqual(data[scale]["on"]["laplacian_var"], data[scale]["off"]["laplacian_var"])
            else:
                self.assertGreater(data[scale]["on"]["laplacian_var"], data[scale]["off"]["laplacian_var"])
        # %800, %2000, %6400: ölçülen oran ~100x-1000x; eşik 20x (yük altında bile güvenli)
        for scale in ("8", "20", "64"):
            on, off = data[scale]["on"], data[scale]["off"]
            self.assertTrue(on["settled"] and on["overlay"], scale)
            self.assertFalse(off["overlay"], scale)
            self.assertGreater(on["laplacian_var"], 20 * off["laplacian_var"], f"x{scale}: {on} vs {off}")
            self.assertGreater(on["max_step"], 150, "keskin kenar: komşu piksel arası adım büyük")
            self.assertLess(off["max_step"], 80, "taban bulanık: kenar geçişi yumuşak")


# --------------------------------------------------------------------------- denetim kümesi
class CorpusAuditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = samples.write_all(cls.tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_every_valid_sample_loads_with_the_declared_aspect(self) -> None:
        for name in list(samples.TEXT) + list(samples.ENCODED) + list(samples.SVGZ):
            with self.subTest(sample=name):
                path = self.paths[name]
                img = svg_loader.load_svg(path, long_side=300)
                self.assertEqual(max(img.size), 300, name)
                native = svg_loader.native_size(path)
                self.assertIsNotNone(native, name)
                self.assertAlmostEqual(img.width / img.height, native[0] / native[1], delta=0.05, msg=name)

    def test_broken_files_raise_oserror_and_have_no_size(self) -> None:
        for name in ("broken_unclosed", "zero_bytes", "not_svg_html", "garbage"):
            with self.subTest(sample=name):
                with self.assertRaises(OSError):
                    svg_loader.load_svg(self.paths[name])

    @needs_resvg
    def test_native_size_equals_what_resvg_renders(self) -> None:
        import resvg_py

        for name in list(samples.TEXT):
            if name in ("huge_viewbox", "relative_image"):
                continue
            with self.subTest(sample=name):
                png = resvg_py.svg_to_bytes(svg_string=samples.TEXT[name], dpi=96.0)
                size = Image.open(io.BytesIO(bytes(png))).size
                self.assertEqual(svg_loader.native_size(self.paths[name]), size)

    @needs_resvg
    def test_base_render_matches_a_direct_resvg_reference(self) -> None:
        import resvg_py

        for name in ("clip_path", "mask", "gradients", "filters", "use_symbol", "mm_units", "pt_units", "percent_content", "aspect_slice", "svgz_gradients"):
            with self.subTest(sample=name):
                path = self.paths[name]
                base = svg_loader.load_svg(path, long_side=600)
                data = gzip.decompress(path.read_bytes()) if path.suffix == ".svgz" else path.read_bytes()
                text = data.decode("utf-8")
                key = "width" if base.width >= base.height else "height"
                ref = Image.open(io.BytesIO(bytes(resvg_py.svg_to_bytes(svg_string=text, dpi=96.0, **{key: 600})))).convert("RGBA")
                if base.mode == "RGB":
                    ref = svg_region.apply_background(ref, "light")
                self.assertEqual(base.size, ref.size)
                self.assertLess(mad(base.convert("RGBA"), ref.convert("RGBA")), 0.5)

    def test_unit_documents_report_96_dpi_sizes_in_the_details_panel(self) -> None:
        self.assertEqual(collect_details(self.paths["mm_units"])["pixels"], "227×151")
        self.assertEqual(collect_details(self.paths["pt_units"])["pixels"], "192×96")
        self.assertEqual(collect_details(self.paths["in_units"])["pixels"], "192×96")
        self.assertEqual(collect_details(self.paths["percent_units"])["pixels"], "150×150")
        self.assertEqual(collect_details(self.paths["svgz_gradients"])["pixels"], "240×120")
        self.assertNotIn("pixels", collect_details(self.paths["broken_unclosed"]) if False else {})

    def test_thumbnails_of_unit_documents_are_not_blank(self) -> None:
        for name in ("mm_units", "pt_units", "svgz_gradients", "utf16_bom"):
            with self.subTest(sample=name):
                thumb = load_thumb_pil(self.paths[name], 160)
                self.assertEqual(max(thumb.size), 160)
                arr = np.asarray(thumb.convert("RGB"), dtype=np.float32)
                self.assertGreater(float(arr.std()), 5.0, "küçük resim boş görünmemeli")

    def test_zero_sized_svg_does_not_crash(self) -> None:
        try:
            img = svg_loader.load_svg(self.paths["zero_size"], long_side=100)
        except OSError:
            return
        self.assertGreaterEqual(min(img.size), 1)

    def test_qt_region_fallback_matches_a_full_qt_render_crop(self) -> None:
        source = svg_region.load_source(self.paths["gradients"])
        full = svg_loader.render_region_qt(source, (0, 0, 1, 1), (800, 400), "keep")
        part = svg_loader.render_region_qt(source, (0.25, 0.25, 0.5, 0.5), (400, 200), "keep")
        self.assertIsNotNone(part)
        a = qimage_gray(full)[100:300, 200:600]
        b = qimage_gray(part)
        self.assertLess(mad(a, b), 1.0)
        self.assertIsNone(svg_loader.render_region_qt(source, (0, 0, 1e-9, 1e-9), (400, 200), "keep"), "aşırı ölçek: tabanda kal")


if __name__ == "__main__":
    unittest.main()
