"""mpv seçenekleri platforma göre: D3D11 anahtarları yalnızca Windows'ta (diğerlerinde mpv reddeder)."""

from __future__ import annotations

import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.ui.player import mpv_options  # noqa: E402

D3D11_KEYS = {"d3d11va-zero-copy", "d3d11-exclusive-fs"}


class MpvOptionsPlatformTest(unittest.TestCase):
    def test_windows_keeps_d3d11_pipeline(self) -> None:
        opts = mpv_options(12345, platform="win32")
        self.assertEqual(opts["wid"], "12345")
        self.assertEqual(opts["vo"], "gpu")
        self.assertEqual(opts["gpu-context"], "d3d11")
        self.assertEqual(opts["gpu-api"], "d3d11")
        self.assertEqual(opts["d3d11va-zero-copy"], "yes")
        self.assertIs(opts["d3d11-exclusive-fs"], False)

    def test_other_platforms_have_no_d3d11_keys(self) -> None:
        for plat in ("linux", "darwin", "freebsd13"):
            opts = mpv_options(777, platform=plat)
            self.assertFalse(D3D11_KEYS & set(opts), plat)
            self.assertNotIn("gpu-context", opts, plat)
            self.assertNotIn("gpu-api", opts, plat)
            self.assertEqual(opts["wid"], "777")
            self.assertEqual(opts["vo"], "gpu")
            self.assertEqual(opts["hwdec"], "auto-safe")

    def test_common_low_cpu_profile_is_shared(self) -> None:
        for plat in ("win32", "linux", "darwin"):
            opts = mpv_options(1, platform=plat)
            self.assertEqual(opts["video-sync"], "audio")
            self.assertEqual(opts["profile"], "fast")
            self.assertIs(opts["input-default-bindings"], False)

    def test_without_window_id_uses_render_api_vo(self) -> None:
        for plat in ("win32", "linux", "darwin"):
            opts = mpv_options(None, platform=plat)
            self.assertNotIn("wid", opts)
            self.assertEqual(opts["vo"], "libmpv")

    def test_default_platform_is_current(self) -> None:
        self.assertEqual(mpv_options(5), mpv_options(5, platform=sys.platform))


if __name__ == "__main__":
    unittest.main()
