"""Çekirdek özellikler: parça etiketleri, devam konumu, histogram, kayıpsız döndürme ve kırpma."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

from app.core import fileops, histogram, image_edit, resume_store, tracks  # noqa: E402


class TrackLabelTest(unittest.TestCase):
    def test_language_names(self) -> None:
        self.assertEqual(tracks.language_name("tur"), "Türkçe")
        self.assertEqual(tracks.language_name("eng", "en"), "English")
        self.assertEqual(tracks.language_name("xyz"), "XYZ")
        self.assertEqual(tracks.language_name("und"), "")
        self.assertEqual(tracks.language_name(None), "")

    def test_label_has_language_codec_and_channels(self) -> None:
        t = {"id": 2, "lang": "eng", "codec": "ac3", "demux-channel-count": 6}
        self.assertEqual(tracks.track_label(t), "İngilizce · AC3 5.1")

    def test_label_with_title_and_external_and_forced(self) -> None:
        t = {"id": 3, "lang": "tur", "title": "SDH", "codec": "subrip", "external": True, "forced": True}
        self.assertEqual(tracks.track_label(t), "Türkçe — SDH · ↗ · zorunlu")
        self.assertEqual(tracks.track_label({"id": 5}), "Parça 5")

    def test_split_and_menu_rule(self) -> None:
        tl = [{"type": "video", "id": 1}, {"type": "audio", "id": 1}, "x", {"type": "sub", "id": 1}]
        audio, subs = tracks.split_tracks(tl)
        self.assertEqual((len(audio), len(subs)), (1, 1))
        self.assertTrue(tracks.needs_track_menu(audio, subs))
        self.assertFalse(tracks.needs_track_menu(audio, []))
        self.assertTrue(tracks.needs_track_menu(audio * 2, []))
        self.assertFalse(tracks.needs_track_menu([], []))

    def test_english_labels_have_no_turkish_words(self) -> None:
        from app.resources.locales import t

        self.assertEqual(tracks.track_label({"id": 5}, "en"), "Track 5")
        self.assertEqual(tracks.track_label({"id": 5}, "en", t("track", "en")), "Track 5")
        self.assertEqual(tracks.track_label({"id": 5}, "tr", t("track", "tr")), "Parça 5")
        sub = {"id": 1, "lang": "eng", "codec": "mov_text"}
        self.assertEqual(tracks.track_label(sub, "en"), "English · Text")
        self.assertEqual(tracks.track_label(sub, "tr"), "İngilizce · Metin")

    def test_cycle_subtitles_through_off(self) -> None:
        subs = [{"id": 1}, {"id": 2}]
        self.assertEqual(tracks.next_track_id(subs, None, True), 1)
        self.assertEqual(tracks.next_track_id(subs, 1, True), 2)
        self.assertIsNone(tracks.next_track_id(subs, 2, True))
        self.assertEqual(tracks.next_track_id(subs, 2, False), 1)
        self.assertIsNone(tracks.next_track_id([], 1, True))


class ResumeStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        self.media = self.dir / "film.mp4"
        self.media.write_bytes(b"x" * 100)
        self.store = resume_store.ResumeStore(self.dir / "resume.json")

    MIN = resume_store.MIN_DURATION_MS

    def test_saves_and_returns_position_for_long_video(self) -> None:
        self.assertTrue(self.store.update(self.media, 90_000, 600_000))
        self.assertEqual(self.store.get(self.media), 90_000)
        # kalıcı: yeni örnek de görür
        self.assertEqual(resume_store.ResumeStore(self.dir / "resume.json").get(self.media), 90_000)

    def test_short_video_never_resumes(self) -> None:
        self.assertFalse(self.store.update(self.media, 30_000, self.MIN - 1))
        self.assertIsNone(self.store.get(self.media))

    def test_beginning_and_end_clear_the_entry(self) -> None:
        self.store.update(self.media, 90_000, 600_000)
        self.assertTrue(self.store.update(self.media, 3_000, 600_000))  # başa döndü
        self.assertIsNone(self.store.get(self.media))
        self.store.update(self.media, 90_000, 600_000)
        self.assertTrue(self.store.update(self.media, 595_000, 600_000))  # bitirdi
        self.assertIsNone(self.store.get(self.media))

    def test_changed_file_invalidates_position(self) -> None:
        self.store.update(self.media, 90_000, 600_000)
        self.media.write_bytes(b"y" * 250)  # boyut değişti
        self.assertIsNone(self.store.get(self.media))

    def test_tiny_moves_do_not_rewrite(self) -> None:
        self.store.update(self.media, 90_000, 600_000)
        self.assertFalse(self.store.update(self.media, 90_400, 600_000))

    def test_missing_file_and_corrupt_store_are_safe(self) -> None:
        self.assertFalse(self.store.update(self.dir / "yok.mp4", 90_000, 600_000))
        bad = self.dir / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        self.assertIsNone(resume_store.ResumeStore(bad).get(self.media))

    def test_entries_are_capped(self) -> None:
        for i in range(resume_store.MAX_ENTRIES + 20):
            f = self.dir / f"v{i}.mp4"
            f.write_bytes(b"z" * (i + 1))
            self.store.update(f, 60_000, 600_000)
        self.assertLessEqual(len(self.store._data), resume_store.MAX_ENTRIES)


class HistogramTest(unittest.TestCase):
    def test_counts_per_channel(self) -> None:
        a = np.zeros((10, 10, 3), np.uint8)
        a[:, :, 0] = 255
        h = histogram.compute(a)
        self.assertEqual(int(h["r"][255]), 100)
        self.assertEqual(int(h["g"][0]), 100)
        self.assertEqual(int(h["luma"][76]), 100)  # 0.299*255
        self.assertEqual(int(h["r"].sum()), 100)

    def test_large_image_is_subsampled_but_normalised(self) -> None:
        big = np.random.randint(0, 255, (3000, 4000, 3), dtype=np.uint8)
        h = histogram.compute(big, max_side=512)
        self.assertLess(int(h["r"].sum()), big.shape[0] * big.shape[1])
        n = histogram.normalized(h["luma"])
        self.assertEqual(n.shape, (256,))
        self.assertTrue(0.0 <= float(n.min()) and float(n.max()) <= 1.0)

    def test_rgba_and_bad_shape(self) -> None:
        self.assertEqual(int(histogram.compute(np.zeros((4, 4, 4), np.uint8))["r"][0]), 16)
        with self.assertRaises(ValueError):
            histogram.compute(np.zeros((4, 4), np.uint8))

    def test_qimage_roundtrip(self) -> None:
        from PyQt6.QtGui import QColor, QImage
        from PyQt6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        q = QImage(7, 5, QImage.Format.Format_RGB32)
        q.fill(QColor(10, 20, 30))
        arr = histogram.qimage_to_rgb(q)
        self.assertEqual(arr.shape, (5, 7, 3))
        self.assertEqual(tuple(arr[0, 0]), (10, 20, 30))


class OrientationMathTest(unittest.TestCase):
    def test_identity_and_full_turn(self) -> None:
        for o in range(1, 9):
            self.assertEqual(image_edit.combine_orientation(o, 0), o)
            self.assertEqual(image_edit.combine_orientation(o, 360), o)
            cur = o
            for _ in range(4):
                cur = image_edit.combine_orientation(cur, 90)
            self.assertEqual(cur, o)

    def test_known_values(self) -> None:
        self.assertEqual(image_edit.combine_orientation(1, 90), 6)
        self.assertEqual(image_edit.combine_orientation(1, 180), 3)
        self.assertEqual(image_edit.combine_orientation(1, 270), 8)
        self.assertEqual(image_edit.combine_orientation(6, 90), 3)

    def test_matches_real_pixel_transform(self) -> None:
        base = Image.fromarray(np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3))
        for orient in range(1, 9):
            for deg in (90, 180, 270):
                exif = Image.Exif()
                exif[0x0112] = orient
                shown = ImageOps.exif_transpose(_with_exif(base, exif))
                want = shown.rotate(-deg, expand=True)
                new_o = image_edit.combine_orientation(orient, deg)
                exif2 = Image.Exif()
                exif2[0x0112] = new_o
                got = ImageOps.exif_transpose(_with_exif(base, exif2))
                self.assertTrue(np.array_equal(np.asarray(want), np.asarray(got)), (orient, deg))


def _with_exif(img: Image.Image, exif: Image.Exif) -> Image.Image:
    out = img.copy()
    out.info["exif"] = exif.tobytes()
    out.getexif().update(exif)
    return out


def _entropy_data(path: pathlib.Path) -> bytes:
    data = path.read_bytes()
    return data[data.index(b"\xff\xda"):]  # SOS'tan sonrası: sıkıştırılmış piksel verisi


class LosslessJpegRotateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        rng = np.random.default_rng(3)
        self.arr = rng.integers(0, 255, (48, 64, 3), dtype=np.uint8)

    def _jpeg(self, name: str, orientation: int | None) -> pathlib.Path:
        p = self.dir / name
        im = Image.fromarray(self.arr)
        if orientation is None:
            im.save(p, quality=90)
        else:
            exif = Image.Exif()
            exif[0x0112] = orientation
            exif[0x010F] = "TestCam"  # başka bir etiket: korunmalı
            im.save(p, quality=90, exif=exif)
        return p

    def _shown(self, p: pathlib.Path) -> np.ndarray:
        with Image.open(p) as im:
            return np.asarray(ImageOps.exif_transpose(im).convert("RGB"))

    def test_existing_tag_is_patched_in_place_pixels_untouched(self) -> None:
        p = self._jpeg("a.jpg", 1)
        before_pixels, before_view = _entropy_data(p), self._shown(p)
        size = p.stat().st_size
        image_edit.rotate_and_save(p, 90)
        self.assertEqual(p.stat().st_size, size, "yerinde yama: boyut değişmemeli")
        self.assertEqual(_entropy_data(p), before_pixels, "piksel verisi aynı kalmalı (kayıpsız)")
        self.assertTrue(np.array_equal(self._shown(p), np.rot90(before_view, -1)))
        self.assertEqual(Image.open(p).getexif().get(0x010F), "TestCam")

    def test_four_quarter_turns_restore_original(self) -> None:
        p = self._jpeg("b.jpg", 6)
        view = self._shown(p)
        for _ in range(4):
            image_edit.rotate_and_save(p, 90)
        self.assertTrue(np.array_equal(self._shown(p), view))

    def test_counter_clockwise_and_half_turn(self) -> None:
        p = self._jpeg("c.jpg", 1)
        view = self._shown(p)
        image_edit.rotate_and_save(p, 270)  # = 90 sola
        self.assertTrue(np.array_equal(self._shown(p), np.rot90(view, 1)))
        image_edit.rotate_and_save(p, 270)
        self.assertTrue(np.array_equal(self._shown(p), np.rot90(view, 2)))

    def test_jpeg_without_exif_gets_orientation(self) -> None:
        p = self._jpeg("d.jpg", None)
        view, pixels = self._shown(p), _entropy_data(p)
        image_edit.rotate_and_save(p, 90)
        self.assertEqual(_entropy_data(p), pixels)
        self.assertTrue(np.array_equal(self._shown(p), np.rot90(view, -1)))

    def test_exif_without_orientation_tag_keeps_other_tags(self) -> None:
        p = self.dir / "e.jpg"
        exif = Image.Exif()
        exif[0x010F] = "OnlyMake"
        Image.fromarray(self.arr).save(p, quality=90, exif=exif)
        view, pixels = self._shown(p), _entropy_data(p)
        image_edit.rotate_and_save(p, 180)
        self.assertEqual(_entropy_data(p), pixels)
        self.assertTrue(np.array_equal(self._shown(p), np.rot90(view, 2)))
        self.assertEqual(Image.open(p).getexif().get(0x010F), "OnlyMake")

    def test_zero_rotation_does_not_touch_file(self) -> None:
        p = self._jpeg("f.jpg", 3)
        m = p.stat().st_mtime_ns
        image_edit.rotate_and_save(p, 360)
        self.assertEqual(p.stat().st_mtime_ns, m)

    def test_png_and_webp_rotate_losslessly(self) -> None:
        for ext in (".png", ".webp", ".bmp", ".tiff"):
            p = self.dir / f"x{ext}"
            Image.fromarray(self.arr).save(p, **({"lossless": True} if ext == ".webp" else {}))
            image_edit.rotate_and_save(p, 90)
            got = np.asarray(Image.open(p).convert("RGB"))
            self.assertTrue(np.array_equal(got, np.rot90(self.arr, -1)), ext)

    def test_unsupported_format_is_refused(self) -> None:
        p = self.dir / "x.heic"
        p.write_bytes(b"0")
        self.assertFalse(image_edit.can_rotate_save(p))
        with self.assertRaises(ValueError):
            image_edit.rotate_and_save(p, 90)
        self.assertTrue(image_edit.can_rotate_save(self.dir / "A.JPG"))

    def test_no_temp_files_left_behind(self) -> None:
        p = self._jpeg("g.jpg", 1)
        image_edit.rotate_and_save(p, 90)
        self.assertEqual(sorted(x.name for x in self.dir.iterdir()), ["g.jpg"])


def _png16_rgb(path: pathlib.Path, arr: np.ndarray) -> None:
    """Kanal başına 16 bit RGB PNG (Pillow bunu yazamaz, açınca 8 bit "RGB" kipinde verir)."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    h, w, _ = arr.shape
    raw = b"".join(b"\x00" + arr[y].astype(">u2").tobytes() for y in range(h))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class SafeRotationSaveTest(unittest.TestCase):
    """Yeniden kodlanan biçimlerde döndürmeyi kaydetmek özgün dosyayı asla bozmamalı."""

    XMP = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" dc:format="lumina-test"/></rdf:RDF></x:xmpmeta>'
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        self.arr = np.random.default_rng(7).integers(0, 255, (20, 30, 3), dtype=np.uint8)

    def _assert_refused(self, p: pathlib.Path) -> None:
        before = p.read_bytes()
        self.assertFalse(image_edit.can_rotate_save(p), p.name)
        with self.assertRaises(ValueError):
            image_edit.rotate_and_save(p, 90)
        self.assertEqual(p.read_bytes(), before, f"{p.name} değişmemeli")

    def test_multi_frame_images_are_refused_and_untouched(self) -> None:
        frames = [Image.new("RGB", (30, 20), c) for c in ("red", "green", "blue")]
        for name in ("anim.webp", "anim.png", "pages.tif"):
            p = self.dir / name
            frames[0].save(p, save_all=True, append_images=frames[1:], duration=100, loop=0)
            self._assert_refused(p)

    def test_deeper_than_8_bit_is_refused_and_untouched(self) -> None:
        p = self.dir / "rgb48.png"
        _png16_rgb(p, (self.arr.astype(np.uint16) * 257))
        self._assert_refused(p)
        for ext in (".png", ".tif"):
            g = self.dir / f"gray16{ext}"
            Image.fromarray((self.arr[..., 0].astype(np.uint16) * 257)).save(g)
            self._assert_refused(g)

    def test_exif_icc_and_xmp_are_kept(self) -> None:
        from PIL import ImageCms, PngImagePlugin

        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        exif = Image.Exif()
        exif[0x010F] = "TestCam"
        exif[0x0112] = 6
        exif[0x8769] = {0x9003: "2020:01:02 03:04:05"}  # DateTimeOriginal (Exif alt dizini)
        for ext in (".png", ".webp", ".tif"):
            p = self.dir / f"m{ext}"
            kw: dict = {"exif": exif.tobytes(), "icc_profile": icc}
            if ext == ".png":
                info = PngImagePlugin.PngInfo()
                info.add_itxt("XML:com.adobe.xmp", self.XMP)
                kw["pnginfo"] = info
            elif ext == ".webp":
                kw.update(lossless=True, xmp=self.XMP)
            Image.fromarray(self.arr).save(p, **kw)
            if ext == ".tif":  # Pillow TIFF'e `xmp` yazmaz: etiketle (700) ekle
                with Image.open(p) as im:
                    im.load()
                    tags = im.getexif()
                    tags.get_ifd(0x8769)
                    tags[700] = self.XMP
                    for k in (256, 257, 258, 259, 262, 273, 277, 278, 279, 284):
                        tags.pop(k, None)
                    copy = im.copy()
                copy.save(p, exif=tags, icc_profile=icc)
            with Image.open(p) as im:
                shown = np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
            self.assertTrue(image_edit.can_rotate_save(p), ext)
            image_edit.rotate_and_save(p, 90)
            with Image.open(p) as im:
                got = im.getexif()
                self.assertEqual(got.get(0x010F), "TestCam", ext)
                self.assertEqual(got.get_ifd(0x8769).get(0x9003), "2020:01:02 03:04:05", ext)
                self.assertNotIn(0x0112, got, f"{ext}: pikseller dik, yön etiketi kalmamalı")
                self.assertEqual(im.info.get("icc_profile"), icc, ext)
                self.assertIn(b"lumina-test", im.info.get("xmp") or b"", ext)
                view = np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
            self.assertTrue(np.array_equal(view, np.rot90(shown, -1)), ext)


class CropTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def test_crop_box_clamps(self) -> None:
        self.assertEqual(image_edit.crop_box(100, 50, (0.1, 0.2, 0.6, 0.8)), (10, 10, 60, 40))
        self.assertEqual(image_edit.crop_box(100, 50, (-1, -1, 2, 2)), (0, 0, 100, 50))
        l, t, r, b = image_edit.crop_box(100, 50, (0.5, 0.5, 0.5, 0.5))
        self.assertTrue(r > l and b > t)

    def test_unique_path(self) -> None:
        (self.dir / "a (kırpılmış).jpg").write_bytes(b"1")
        self.assertEqual(image_edit.unique_path(self.dir, "a (kırpılmış)", ".jpg").name, "a (kırpılmış) 2.jpg")
        self.assertEqual(image_edit.unique_path(self.dir, "b", ".png").name, "b.png")

    def test_crop_with_rotation_matches_numpy(self) -> None:
        arr = np.random.default_rng(1).integers(0, 255, (40, 80, 3), dtype=np.uint8)
        src = self.dir / "s.png"
        Image.fromarray(arr).save(src)
        dest = self.dir / "o.png"
        # 90° sağa döndür -> 80 x 40 -> 40 x 80; sol-üst çeyrek
        image_edit.crop_and_save(src, (0.0, 0.0, 0.5, 0.5), 90, dest, image=Image.fromarray(arr))
        rotated = np.rot90(arr, -1)
        want = rotated[: rotated.shape[0] // 2, : rotated.shape[1] // 2]
        self.assertTrue(np.array_equal(np.asarray(Image.open(dest)), want))

    def test_jpeg_crop_keeps_exif_and_resets_orientation(self) -> None:
        arr = np.random.default_rng(2).integers(0, 255, (60, 90, 3), dtype=np.uint8)
        src = self.dir / "s.jpg"
        exif = Image.Exif()
        exif[0x0112] = 1
        exif[0x010F] = "Cam"
        Image.fromarray(arr).save(src, quality=92, exif=exif)
        dest = self.dir / "o.jpg"
        image_edit.crop_and_save(src, (0.2, 0.25, 0.8, 0.75), 0, dest)
        with Image.open(dest) as out:
            self.assertEqual(out.size, (54, 30))
            self.assertEqual(out.getexif().get(0x010F), "Cam")
            self.assertEqual(out.getexif().get(0x0112), 1)


class FileOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = pathlib.Path(self._tmp.name)
        self.src, self.dst = root / "src", root / "dst"
        self.src.mkdir()
        self.dst.mkdir()
        self.heic = self.src / "IMG_1.HEIC"
        self.mov = self.src / "IMG_1.MOV"
        self.heic.write_bytes(b"h")
        self.mov.write_bytes(b"m")

    def test_move_pair_keeps_same_stem(self) -> None:
        done = fileops.transfer([self.heic, self.mov], self.dst, move=True)
        self.assertEqual([d.name for _s, d in done], ["IMG_1.HEIC", "IMG_1.MOV"])
        self.assertFalse(self.heic.exists() or self.mov.exists())

    def test_collision_renames_whole_group(self) -> None:
        (self.dst / "IMG_1.MOV").write_bytes(b"x")  # yalnızca biri çakışsa da çift birlikte yeniden adlanır
        done = fileops.transfer([self.heic, self.mov], self.dst, move=False)
        self.assertEqual([d.name for _s, d in done], ["IMG_1 (2).HEIC", "IMG_1 (2).MOV"])
        self.assertTrue(self.heic.exists(), "kopyada kaynak korunur")
        self.assertEqual((self.dst / "IMG_1.MOV").read_bytes(), b"x", "var olan dosya ezilmemeli")

    def test_failure_rolls_back_partial_move(self) -> None:
        from unittest import mock

        real = fileops.shutil.move
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk dolu")
            return real(src, dst)

        with mock.patch.object(fileops.shutil, "move", side_effect=flaky):
            with self.assertRaises(OSError):
                fileops.transfer([self.heic, self.mov], self.dst, move=True)
        self.assertTrue(self.heic.exists() and self.mov.exists(), "yarım kalan taşıma geri alınmalı")
        self.assertEqual(list(self.dst.iterdir()), [])

    def test_undo_move_restores_and_skips_occupied(self) -> None:
        done = fileops.transfer([self.heic, self.mov], self.dst, move=True)
        self.heic.write_bytes(b"yeni")  # özgün yer doldu
        restored = fileops.undo_move(done)
        self.assertEqual([p.name for p in restored], ["IMG_1.MOV"])
        self.assertEqual(self.heic.read_bytes(), b"yeni")
        self.assertTrue((self.dst / "IMG_1.HEIC").exists(), "yeri dolu olan dosya hedefte kalır")


class OutputSuffixTest(unittest.TestCase):
    def test_suffix_rules(self) -> None:
        self.assertEqual(image_edit.output_suffix(pathlib.Path("a.PNG"), True), ".png")
        self.assertEqual(image_edit.output_suffix(pathlib.Path("a.jpeg"), False), ".jpeg")
        self.assertEqual(image_edit.output_suffix(pathlib.Path("a.heic"), False), ".jpg")
        self.assertEqual(image_edit.output_suffix(pathlib.Path("a.CR2"), False), ".jpg")
        self.assertEqual(image_edit.output_suffix(pathlib.Path("a.avif"), True), ".png")


if __name__ == "__main__":
    unittest.main()
