"""PreviewStore: dosya kimliğine bağlı önizleme deposu (Qt'siz mantık)."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.preview_store import PreviewStore, identity_for  # noqa: E402
from app.core.preview_decoder import board_positions  # noqa: E402

ID_A = ("a.mp4", 100, 1)
ID_B = ("b.mp4", 200, 2)


class PreviewStoreTest(unittest.TestCase):
    def test_stale_identity_is_rejected(self) -> None:
        s = PreviewStore()
        s.reset(ID_A)
        self.assertTrue(s.add(ID_A, 1000, "A1"))
        s.reset(ID_B)
        self.assertEqual(len(s), 0, "kimlik değişince önceki dosyanın kareleri silinmeli")
        self.assertFalse(s.add(ID_A, 2000, "A2"), "eski dosyanın geç gelen karesi eklenmemeli")
        self.assertIsNone(s.nearest(2000))

    def test_reset_same_identity_keeps_frames(self) -> None:
        s = PreviewStore()
        s.reset(ID_A)
        s.add(ID_A, 1000, "x")
        self.assertFalse(s.reset(ID_A))
        self.assertEqual(len(s), 1)

    def test_nearest_and_within(self) -> None:
        s = PreviewStore()
        s.reset(ID_A)
        s.add(ID_A, 1000, "one")
        s.add(ID_A, 9000, "nine")
        self.assertEqual(s.nearest(2000), (1000, "one"))
        self.assertEqual(s.nearest(8000), (9000, "nine"))
        # Tek kare olsa bile uzak zaman için "kesin" sayılmamalı (eski hata: 200 sn için 1. sn karesi)
        self.assertIsNone(s.within(200_000, 500))
        self.assertEqual(s.within(1200, 500), (1000, "one"))

    def test_close_frames_are_replaced_not_duplicated(self) -> None:
        s = PreviewStore(min_gap_ms=80)
        s.reset(ID_A)
        s.add(ID_A, 1000, "old")
        s.add(ID_A, 1040, "new")
        self.assertEqual(len(s), 1)
        self.assertEqual(s.nearest(1040), (1040, "new"))

    def test_cap_keeps_distribution_and_endpoints(self) -> None:
        s = PreviewStore(max_frames=8, min_gap_ms=10)
        s.reset(ID_A)
        for ms in range(0, 10_000, 100):
            s.add(ID_A, ms, ms)
        self.assertLessEqual(len(s), 8)
        times = s.times()
        self.assertEqual(times[0], 0)
        self.assertEqual(times[-1], 9900)
        self.assertEqual(times, sorted(times))

    def test_identity_for_changes_with_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "v.mp4"
            p.write_bytes(b"abc")
            first = identity_for(p)
            self.assertIsNotNone(first)
            time.sleep(0.02)
            p.write_bytes(b"abcdef")
            os.utime(p, ns=(first[2] + 5_000_000_000, first[2] + 5_000_000_000))
            self.assertNotEqual(identity_for(p), first)
            self.assertIsNone(identity_for(pathlib.Path(d) / "yok.mp4"))


class BoardPositionsTest(unittest.TestCase):
    def test_coarse_to_fine_is_evenly_distributed(self) -> None:
        pos = board_positions(120_000)
        self.assertEqual(len(pos), 32)
        first8 = sorted(pos[:8])
        gaps = [b - a for a, b in zip(first8, first8[1:])]
        # İlk 8 kare tüm video boyunca eşit aralıklı (kaba önizleme)
        self.assertLess(max(gaps) - min(gaps), 1)
        self.assertGreater(first8[0], 0)
        self.assertLess(first8[-1], 120_000)

    def test_short_clips_get_fewer_slots_and_unique_times(self) -> None:
        pos = board_positions(3000)
        self.assertEqual(len(pos), len(set(pos)))
        self.assertLessEqual(len(pos), 16)
        self.assertGreaterEqual(len(pos), 4)
        tiny = board_positions(200)
        self.assertGreaterEqual(len(tiny), 4)


if __name__ == "__main__":
    unittest.main()
