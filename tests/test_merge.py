"""merge_live_photos_by_id — sentetik Apple benzeri çiftle (HEIC + MOV aynı ContentIdentifier).

Güvenlik özellikleri: yalnızca video yeniden adlandırılır, hedef ad doluysa ASLA üzerine yazılmaz.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.matcher import merge_live_photos_by_id  # noqa: E402
from app.core.uuid_parser import extract_content_identifier  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # tests/ (_synthetic)
from _synthetic import apple_pair  # noqa: E402

HEIC, MOV = apple_pair()


class MergeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def test_sample_pair_shares_the_same_content_identifier(self) -> None:
        a, b = extract_content_identifier(HEIC), extract_content_identifier(MOV)
        self.assertIsNotNone(a)
        self.assertEqual(a, b, "çift aynı ContentIdentifier'ı taşımalı")

    def test_renames_only_the_video_to_match_the_photo(self) -> None:
        shutil.copy(HEIC, self.dir / "IMG_A.HEIC")
        shutil.copy(MOV, self.dir / "TAMAMEN_BASKA_AD.MOV")
        res = merge_live_photos_by_id(self.dir)
        self.assertTrue(res["success"], res["errors"])
        self.assertEqual(res["merged_count"], 1)
        names = {p.name.lower() for p in self.dir.iterdir()}  # uzantı büyük/küçük harfi önemsiz
        self.assertIn("img_a.heic", names, "fotoğraf asla yeniden adlandırılmamalı")
        self.assertIn("img_a.mov", names)
        self.assertNotIn("tamamen_baska_ad.mov", names)
        self.assertEqual((self.dir / "IMG_A.MOV").read_bytes(), MOV.read_bytes())

    def test_already_paired_is_left_alone(self) -> None:
        shutil.copy(HEIC, self.dir / "IMG_A.HEIC")
        shutil.copy(MOV, self.dir / "IMG_A.MOV")
        res = merge_live_photos_by_id(self.dir)
        self.assertEqual(res["merged_count"], 0)
        self.assertGreaterEqual(res["already_paired"], 1)

    def test_never_overwrites_an_existing_target(self) -> None:
        shutil.copy(HEIC, self.dir / "IMG_A.HEIC")
        shutil.copy(MOV, self.dir / "ESKI.MOV")
        blocker = self.dir / "IMG_A.MOV"
        blocker.write_bytes(b"baska bir video, silinmemeli")  # hedef ad dolu (farklı GUID'li)
        res = merge_live_photos_by_id(self.dir)
        self.assertEqual(blocker.read_bytes(), b"baska bir video, silinmemeli", "hedef dosya ezilmemeli")
        self.assertTrue(res["success"])
        names = {p.name.lower() for p in self.dir.iterdir()}
        if res["merged_count"]:
            self.assertIn("img_a_hevc.mov", names, "çakışmada alternatif ad kullanılmalı")
            self.assertNotIn("eski.mov", names)
        else:  # iki aday da doluysa hata bildirip dokunmaz
            self.assertIn("eski.mov", names)

    def test_missing_folder_reports_error(self) -> None:
        res = merge_live_photos_by_id(self.dir / "yok")
        self.assertFalse(res["success"])
        self.assertTrue(res["errors"])


if __name__ == "__main__":
    unittest.main()
