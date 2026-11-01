"""Harf-duyarsız dosya arama: Linux'ta `IMG_1.HEIC` + `IMG_1.mov`, farklı kökler, birleştirme adları."""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import fs_case  # noqa: E402
from app.core.matcher import (  # noqa: E402
    merge_live_photos_by_id,
    paired_sidecars,
    resolve_live_video,
    still_for_video,
)

sys.path.insert(0, str(ROOT / "tests"))
from _synthetic import apple_pair, sample_jpg  # noqa: E402

JPG = sample_jpg()
HEIC, MOV = apple_pair()


class _Dir(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        fs_case.invalidate()

    def _touch(self, name: str, data: bytes = b"x") -> pathlib.Path:
        p = self.dir / name
        p.write_bytes(data)
        fs_case.invalidate(self.dir)
        return p


class FindCiTest(_Dir):
    def test_finds_entry_with_different_case(self) -> None:
        self._touch("IMG_0001.MoV")
        found = fs_case.find_ci(self.dir, "img_0001.mov")
        self.assertIsNotNone(found)
        self.assertEqual(found.name.lower(), "img_0001.mov")

    def test_missing_returns_none(self) -> None:
        self._touch("a.mov")
        self.assertIsNone(fs_case.find_ci(self.dir, "b.mov"))
        self.assertIsNone(fs_case.find_ci(self.dir / "yok", "a.mov"))
        self.assertFalse(fs_case.exists_ci(self.dir, "b.mov"))

    def test_existing_file_ignores_directories(self) -> None:
        (self.dir / "KLASOR.MOV").mkdir()
        self.assertIsNone(fs_case.existing_file(self.dir / "klasor.mov"))

    def test_listing_is_refreshed_after_invalidate(self) -> None:
        self.assertIsNone(fs_case.find_ci(self.dir, "later.MOV"))
        self._touch("LATER.mov")  # _touch önbelleği geçersiz kılar
        self.assertEqual(fs_case.find_ci(self.dir, "later.MOV").name.lower(), "later.mov")


class SidecarCaseTest(_Dir):
    def test_sidecar_with_other_stem_and_extension_case_is_found(self) -> None:
        photo = self.dir / "Photo_01.jpg"
        shutil.copy(JPG, photo)
        side = self._touch("PHOTO_01.Mov", b"\x00\x00\x00\x18ftypqt  " + b"\x00" * 64)
        match = resolve_live_video(photo, None, None)
        self.assertIsNotNone(match, "farklı büyük/küçük harfli sidecar bulunmalı")
        self.assertFalse(match.is_temp)
        self.assertEqual(pathlib.Path(match.video_path).name.lower(), side.name.lower())

    def test_paired_sidecars_lists_actual_file_once(self) -> None:
        photo = self.dir / "Pic.jpg"
        shutil.copy(JPG, photo)
        side = self._touch("pic.mov")
        found = paired_sidecars(photo)
        self.assertEqual([p.name.lower() for p in found], [side.name.lower()])

    def test_still_for_video_finds_photo_with_other_case(self) -> None:
        shutil.copy(JPG, self.dir / "Shot.JPG")
        video = self._touch("Shot.mov")
        still = still_for_video(video)
        self.assertIsNotNone(still)
        self.assertEqual(still.name.lower(), "shot.jpg")

    def test_hevc_sidecar_name_is_found(self) -> None:
        photo = self.dir / "Clip.jpg"
        shutil.copy(JPG, photo)
        side = self._touch("clip_hevc.MOV")
        match = resolve_live_video(photo, None, None)
        self.assertIsNotNone(match)
        self.assertEqual(pathlib.Path(match.video_path).name.lower(), side.name.lower())


class MergeCaseTest(_Dir):
    def test_video_extension_case_is_preserved(self) -> None:
        shutil.copy(HEIC, self.dir / "IMG_A.HEIC")
        shutil.copy(MOV, self.dir / "baska.Mov")
        res = merge_live_photos_by_id(self.dir)
        self.assertEqual(res["merged_count"], 1, res["errors"])
        self.assertEqual(res["merged_pairs"][0]["new_video"], "IMG_A.Mov")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["IMG_A.HEIC", "IMG_A.Mov"])

    def test_case_only_difference_counts_as_taken_target(self) -> None:
        shutil.copy(HEIC, self.dir / "IMG_A.HEIC")
        shutil.copy(MOV, self.dir / "ESKI.MOV")
        blocker = self.dir / "img_a.MOV"  # harf farkıyla dolu hedef (Linux'ta farklı dosya)
        blocker.write_bytes(b"baska video")
        res = merge_live_photos_by_id(self.dir)
        self.assertEqual(blocker.read_bytes(), b"baska video", "hedef ezilmemeli")
        self.assertTrue(res["success"])
        names = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(len(names), len(set(n.lower() for n in names)), f"harf farkıyla çakışma oluştu: {names}")


if __name__ == "__main__":
    unittest.main()
