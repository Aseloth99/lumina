"""PreviewDecoder: gerçek (sentetik) videoyla doğru zaman, öncelik ve kaynak değişimi.

Sentetik video: 10 sn, 10 fps, 320x180; karenin gri tonu zamanla doğrusal artar
(0 sn -> 0, 10 sn -> 255). Böylece dönen karenin zamanı gri tondan okunabilir.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # tests/ (_media)

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from _media import expected_gray as _expected_gray  # noqa: E402
from _media import gray_of as _gray_of  # noqa: E402
from _media import make_ramp_video as _make_video  # noqa: E402


class PreviewDecoderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls._tmp = tempfile.TemporaryDirectory()
        cls.video = pathlib.Path(cls._tmp.name) / "ramp.mp4"
        cls.video2 = pathlib.Path(cls._tmp.name) / "ramp2.mp4"
        try:
            ok = _make_video(cls.video) and _make_video(cls.video2, gray_offset=-100)
        except Exception as exc:  # cv2/numpy yok
            raise unittest.SkipTest(f"video üretilemedi: {exc}")
        if not ok:
            raise unittest.SkipTest("bu ortamda mp4v kodlayıcı yok")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _decoder(self):
        from app.core.preview_decoder import PreviewDecoder
        from app.core.preview_store import identity_for

        dec = PreviewDecoder()
        self.addCleanup(dec.shutdown)
        got: list[tuple] = []
        dec.frame_ready.connect(lambda ident, ms, img: got.append((ident, ms, img)))
        return dec, got, identity_for

    @staticmethod
    def _wait(predicate, timeout=6.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QCoreApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        QCoreApplication.processEvents()
        return predicate()

    def test_nothing_is_opened_or_decoded_until_a_preview_is_wanted(self) -> None:
        """Tembel: video açılınca önizleme için hiçbir şey çözülmez (eskiden storyboard'un tamamı)."""
        from unittest import mock

        from app.core import preview_decoder

        opens: list[str] = []
        real_open = preview_decoder.open_capture

        def counting_open(path):
            opens.append(path)
            return real_open(path)

        with mock.patch.object(preview_decoder, "open_capture", counting_open):
            dec, got, identity_for = self._decoder()
            ident = identity_for(self.video)
            dec.set_source(ident, str(self.video), 10_000)
            self._wait(lambda: False, timeout=0.6)
            self.assertEqual((opens, got), ([], []), "hover olmadan video açılmamalı, kare çözülmemeli")
            dec.activate(ident)
            self.assertTrue(self._wait(lambda: len(got) >= 8), "etkinleşince storyboard gelmeli")
            self.assertEqual(len(opens), 1)

    def test_storyboard_arrives_progressively_and_is_accurate(self) -> None:
        dec, got, identity_for = self._decoder()
        ident = identity_for(self.video)
        dec.set_source(ident, str(self.video), 10_000)
        dec.activate(ident)
        self.assertTrue(self._wait(lambda: len(got) >= 16), f"storyboard gelmedi ({len(got)} kare)")
        for _ident, ms, img in got:
            self.assertLessEqual(img.width(), 188)
            self.assertLessEqual(img.height(), 106)
            self.assertAlmostEqual(_gray_of(img), _expected_gray(ms), delta=9, msg=f"{ms} ms karesi yanlış zamandan")
        # kaba -> ince: ilk 8 kare video boyunca dağılmış
        first8 = sorted(ms for _i, ms, _img in got[:8])
        self.assertGreater(first8[-1] - first8[0], 6000)

    def test_hover_frame_for_far_jump_is_correct(self) -> None:
        dec, got, identity_for = self._decoder()
        ident = identity_for(self.video)
        dec.set_source(ident, str(self.video), 10_000)
        for target in (9500, 1000, 5500):
            got.clear()
            dec.request_frame(ident, target)
            self.assertTrue(self._wait(lambda: any(ms == target for _i, ms, _img in got)), f"{target} ms karesi gelmedi")
            img = next(img for _i, ms, img in got if ms == target)
            self.assertAlmostEqual(_gray_of(img), _expected_gray(target), delta=9)

    def test_only_latest_request_is_kept(self) -> None:
        dec, got, identity_for = self._decoder()
        ident = identity_for(self.video)
        dec.set_source(ident, str(self.video), 10_000)
        dec.activate(ident)
        self.assertTrue(self._wait(lambda: len(got) >= 16))
        got.clear()
        for ms in (500, 1500, 2500, 3500, 4500, 5500, 6500, 7500, 8500):
            dec.request_frame(ident, ms)
        self.assertTrue(self._wait(lambda: any(ms == 8500 for _i, ms, _img in got)))
        self.assertLess(len(got), 9, "ara istekler atlanmalı (yalnızca son istek bekletilir)")

    def test_source_change_switches_content(self) -> None:
        dec, got, identity_for = self._decoder()
        a, b = identity_for(self.video), identity_for(self.video2)
        dec.set_source(a, str(self.video), 10_000)
        dec.activate(a)
        self.assertTrue(self._wait(lambda: len(got) >= 2))
        dec.set_source(b, str(self.video2), 10_000)
        got.clear()
        dec.request_frame(b, 8000)
        self.assertTrue(self._wait(lambda: any(i == b and ms == 8000 for i, ms, _img in got)))
        img = next(img for i, ms, img in got if i == b and ms == 8000)
        # video2'nin gri tonu 100 eksik: A'nın karesi ile karışmamış olmalı
        self.assertAlmostEqual(_gray_of(img), max(0, _expected_gray(8000) - 100), delta=9)

    def test_release_source_frees_file_for_deletion(self) -> None:
        """Geçici dosya silinebilmeli: release_source capture'ı bırakana dek beklemeli."""
        dec, got, identity_for = self._decoder()
        temp = pathlib.Path(self._tmp.name) / "temp_copy.mp4"
        temp.write_bytes(self.video.read_bytes())
        ident = identity_for(temp)
        dec.set_source(ident, str(temp), 10_000)
        dec.activate(ident)
        self.assertTrue(self._wait(lambda: len(got) >= 1))
        dec.release_source(wait=True)
        temp.unlink()  # Windows'ta capture açıksa PermissionError verirdi
        self.assertFalse(temp.exists())


if __name__ == "__main__":
    unittest.main()
