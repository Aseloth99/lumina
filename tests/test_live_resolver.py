"""Live/Motion Photo çözümleme (Qt'siz çekirdek): protokoller, Ultra HDR yanlış-pozitifi, .livp, temp.

Sentetik dosyalar: gerçek bir JPEG + gerçek bir MP4 (sentetik video) birleştirilir; çıkarılan
videonun gerçekten oynatılabilir (OpenCV ile kare okunabilir) olduğu doğrulanır.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import time
import unittest
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # tests/ (_media)

from app.core.matcher import resolve_live_video  # noqa: E402
from app.core.motion_parser import (  # noqa: E402
    TempVideoStore,
    sweep_stale_temps,
    verify_mp4_at,
)
from app.core.protocols import extract_livp  # noqa: E402
from _media import make_ramp_video  # noqa: E402
from _synthetic import apple_pair  # noqa: E402



def _jpeg_bytes() -> bytes:
    import cv2
    import numpy as np

    ok, buf = cv2.imencode(".jpg", np.full((16, 16, 3), 120, dtype=np.uint8))
    assert ok
    return bytes(buf)


def _with_xmp(jpeg: bytes, xmp: str) -> bytes:
    payload = b"http://ns.adobe.com/xap/1.0/\x00" + xmp.encode("utf-8")
    seg = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    return jpeg[:2] + seg + jpeg[2:]


def _can_decode(video_path: str) -> bool:
    import cv2

    cap = cv2.VideoCapture(video_path)
    try:
        ok, frame = cap.read()
        return bool(ok and frame is not None)
    finally:
        cap.release()


class MotionPhotoProtocolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = pathlib.Path(cls._tmp.name)
        video = cls.dir / "clip.mp4"
        if not make_ramp_video(video):
            raise unittest.SkipTest("bu ortamda mp4v kodlayıcı yok")
        cls.video = video.read_bytes()
        cls.jpeg = _jpeg_bytes()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        self.store = TempVideoStore()
        self.addCleanup(self.store.clear)

    def _write(self, name: str, data: bytes) -> pathlib.Path:
        p = self.dir / name
        p.write_bytes(data)
        return p

    def _assert_valid_match(self, match, sources: tuple[str, ...]) -> None:
        self.assertIsNotNone(match, "Live video bulunmalıydı")
        self.assertTrue(match.is_temp)
        self.assertIn(match.source, sources)
        data = pathlib.Path(match.video_path).read_bytes()
        self.assertEqual(data[4:8], b"ftyp", "çıkarılan dosya MP4 ftyp ile başlamalı (ofset doğru)")
        self.assertEqual(data, self.video, "çıkarılan video orijinaliyle bayt bayt aynı olmalı")
        self.assertTrue(_can_decode(match.video_path), "çıkarılan video oynatılabilir olmalı")

    def test_google_micro_video_v1(self) -> None:
        xmp = (f'<x:xmpmeta><rdf:Description GCamera:MicroVideo="1" GCamera:MicroVideoVersion="1" '
               f'GCamera:MicroVideoOffset="{len(self.video)}"/></x:xmpmeta>')
        p = self._write("micro.jpg", _with_xmp(self.jpeg, xmp) + self.video)
        self._assert_valid_match(resolve_live_video(p, None, self.store), ("motion_v2", "micro_v1"))

    def test_motion_photo_v2_container(self) -> None:
        xmp = ('<x:xmpmeta><Container:Directory><Container:Item Item:Mime="image/jpeg" Item:Semantic="Primary" '
               'Item:Length="0"/><Container:Item Item:Mime="video/mp4" Item:Semantic="MotionPhoto" '
               f'Item:Length="{len(self.video)}"/></Container:Directory></x:xmpmeta>')
        p = self._write("v2.jpg", _with_xmp(self.jpeg, xmp) + self.video)
        self._assert_valid_match(resolve_live_video(p, None, self.store), ("motion_v2",))

    def test_samsung_motionphoto_data(self) -> None:
        p = self._write("samsung.jpg", self.jpeg + b"MotionPhoto_Data" + self.video)
        self._assert_valid_match(resolve_live_video(p, None, self.store), ("samsung",))

    def test_generic_trailing_mp4(self) -> None:
        p = self._write("plain.jpg", self.jpeg + self.video)
        self._assert_valid_match(resolve_live_video(p, None, self.store), ("embedded", "micro_v1"))

    def test_ultra_hdr_gainmap_is_not_a_video(self) -> None:
        """Eski hata: son Item:Length (GainMap JPEG) video sanılıp sahte LIVE rozeti çıkıyordu."""
        gain = self.jpeg  # GainMap = ikinci bir JPEG
        xmp = ('<x:xmpmeta><Container:Directory>'
               '<Container:Item Item:Mime="image/jpeg" Item:Semantic="Primary" Item:Length="0"/>'
               f'<Container:Item Item:Mime="image/jpeg" Item:Semantic="GainMap" Item:Length="{len(gain)}"/>'
               '</Container:Directory></x:xmpmeta>')
        p = self._write("ultrahdr.jpg", _with_xmp(self.jpeg, xmp) + gain)
        self.assertIsNone(resolve_live_video(p, None, self.store), "GainMap video sayılmamalı")
        self.assertEqual(self.store.paths(), (), "sahte video için geçici dosya oluşmamalı")

    def test_bogus_metadata_offset_falls_through_to_sidecar(self) -> None:
        """İlk dedektör sahte ofset verirse bir sonrakine/sidecar'a geçilmeli."""
        xmp = f'<x:xmpmeta><rdf:Description GCamera:MicroVideoOffset="{len(self.jpeg)}"/></x:xmpmeta>'
        p = self._write("bogus.jpg", _with_xmp(self.jpeg, xmp) + self.jpeg)
        side = self._write("bogus.mov", self.video)
        match = resolve_live_video(p, None, self.store)
        self.assertIsNotNone(match)
        self.assertFalse(match.is_temp)
        # Windows dosya sistemi büyük/küçük harfe duyarsız: bogus.MOV == bogus.mov
        self.assertEqual(pathlib.Path(match.video_path).name.lower(), side.name.lower())

    def test_verify_mp4_at(self) -> None:
        mp4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
        p = self._write("v.bin", b"JUNK" * 4 + mp4)
        self.assertEqual(verify_mp4_at(p, 0), 16, "küçük kaymayı düzeltmeli")
        self.assertEqual(verify_mp4_at(p, 16), 16)
        heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heicmif1"
        self.assertIsNone(verify_mp4_at(self._write("i.bin", heic), 0), "HEIC görüntü kabı video değildir")
        self.assertIsNone(verify_mp4_at(self._write("j.bin", b"\xff\xd8\xff\xe0" + b"\x00" * 100), 0))
        self.assertIsNone(verify_mp4_at(self.dir / "yok.bin", 0))

    def test_apple_style_pair_uses_sidecar_mov(self) -> None:
        heic, mov = apple_pair()
        work = pathlib.Path(tempfile.mkdtemp(prefix="lumina_test_apple_"))
        self.addCleanup(shutil.rmtree, work, True)
        shutil.copy(heic, work / heic.name)
        shutil.copy(mov, work / mov.name)
        match = resolve_live_video(work / heic.name, None, self.store)
        self.assertIsNotNone(match)
        self.assertFalse(match.is_temp)
        self.assertEqual(pathlib.Path(match.video_path).name, mov.name)


class LivpAndTempTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        self.store = TempVideoStore()
        self.addCleanup(self.store.clear)

    def _livp(self, members: dict[str, bytes]) -> pathlib.Path:
        p = self.dir / "photo.livp"
        with zipfile.ZipFile(p, "w") as z:
            for name, data in members.items():
                z.writestr(name, data)
        return p

    def test_livp_extracts_streamed_and_cleans_up(self) -> None:
        still_data, video_data = _jpeg_bytes(), os.urandom(3 * 1024 * 1024 + 17)
        p = self._livp({"IMG_1.jpg": still_data, "IMG_1.mov": video_data})
        result = extract_livp(p, self.store)
        self.assertIsNotNone(result)
        still, video = result
        self.assertEqual(still.read_bytes(), still_data)
        self.assertEqual(pathlib.Path(video).read_bytes(), video_data)
        tmpdir = still.parent
        self.assertIn(str(tmpdir), self.store.dirs())
        self.store.clear()
        self.assertFalse(tmpdir.exists(), "geçici dizin (yalnız dosyalar değil) silinmeli")

    def test_livp_without_video_returns_none_and_leaves_no_dir(self) -> None:
        before = {p.name for p in pathlib.Path(tempfile.gettempdir()).glob("lumina_livp_*")}
        p = self._livp({"IMG_1.jpg": _jpeg_bytes()})
        self.assertIsNone(extract_livp(p, self.store))
        after = {p.name for p in pathlib.Path(tempfile.gettempdir()).glob("lumina_livp_*")}
        self.assertEqual(before, after)

    def test_sweep_removes_only_old_lumina_temps(self) -> None:
        old_t = time.time() - 3 * 24 * 3600
        old_mp4 = self.dir / "lumina_abc.mp4"
        new_mp4 = self.dir / "lumina_new.mp4"
        other = self.dir / "baska_uygulama.mp4"
        old_dir = self.dir / "lumina_livp_old"
        old_dir.mkdir()
        (old_dir / "x.mov").write_bytes(b"x")
        for f in (old_mp4, new_mp4, other):
            f.write_bytes(b"x")
        for f in (old_mp4, other, old_dir):
            os.utime(f, (old_t, old_t))
        removed = sweep_stale_temps(24 * 3600, temp_dir=str(self.dir))
        self.assertEqual(removed, 2)
        self.assertFalse(old_mp4.exists())
        self.assertFalse(old_dir.exists())
        self.assertTrue(new_mp4.exists(), "güncel dosya (başka pencere kullanıyor olabilir) silinmemeli")
        self.assertTrue(other.exists(), "Lumina'ya ait olmayan dosya silinmemeli")

    def test_store_keeps_locked_file_for_retry(self) -> None:
        """Silinemeyen (açık/kilitli) dosya kaybolmamalı; sonraki clear'da tekrar denenmeli."""
        f = self.dir / "lumina_locked.mp4"
        f.write_bytes(b"x")
        self.store.add(str(f))
        handle = open(f, "rb")  # Windows'ta silmeyi engeller
        try:
            self.store.clear()
            if os.name == "nt":
                self.assertEqual(self.store.paths(), (str(f),))
        finally:
            handle.close()
        self.store.clear()
        self.assertEqual(self.store.paths(), ())
        self.assertFalse(f.exists())


if __name__ == "__main__":
    unittest.main()
