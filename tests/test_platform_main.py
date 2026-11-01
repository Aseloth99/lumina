"""Platform seçimleri: yazı tipi tercihi, Wayland'da X11 tercihi (libmpv `wid` gömmesi için)."""

from __future__ import annotations

import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import main as app_main  # noqa: E402


class FontPickTest(unittest.TestCase):
    def test_each_platform_prefers_its_own_ui_font(self) -> None:
        avail = {"Segoe UI", "Segoe UI Variable Text", "Ubuntu", "Noto Sans", "Helvetica Neue", "Arial"}
        self.assertEqual(app_main.pick_font_family(avail, "win32"), "Segoe UI Variable Text")
        self.assertEqual(app_main.pick_font_family(avail, "darwin"), "Helvetica Neue")
        self.assertEqual(app_main.pick_font_family(avail, "linux"), "Ubuntu")
        self.assertEqual(app_main.pick_font_family({"Noto Sans"}, "linux"), "Noto Sans")

    def test_falls_back_to_system_default_then_arial(self) -> None:
        self.assertEqual(app_main.pick_font_family({"Foo"}, "linux", system_default="Sistem Yazısı"), "Sistem Yazısı")
        self.assertEqual(app_main.pick_font_family(set(), "darwin"), "Arial")
        self.assertEqual(app_main.pick_font_family(set(), "freebsd13"), "Arial")


class PreferX11Test(unittest.TestCase):
    def test_wayland_session_gets_xcb_first_list(self) -> None:
        env = {"XDG_SESSION_TYPE": "wayland"}
        self.assertTrue(app_main.prefer_x11_for_embedding(env, "linux"))
        self.assertEqual(env["QT_QPA_PLATFORM"], "xcb;wayland")

    def test_wayland_display_alone_is_enough(self) -> None:
        env = {"WAYLAND_DISPLAY": "wayland-0"}
        self.assertTrue(app_main.prefer_x11_for_embedding(env, "linux"))

    def test_user_choice_and_other_platforms_are_untouched(self) -> None:
        env = {"XDG_SESSION_TYPE": "wayland", "QT_QPA_PLATFORM": "wayland"}
        self.assertFalse(app_main.prefer_x11_for_embedding(env, "linux"))
        self.assertEqual(env["QT_QPA_PLATFORM"], "wayland")
        for plat in ("win32", "darwin"):
            env = {"XDG_SESSION_TYPE": "wayland"}
            self.assertFalse(app_main.prefer_x11_for_embedding(env, plat))
            self.assertNotIn("QT_QPA_PLATFORM", env)

    def test_x11_session_is_left_alone(self) -> None:
        env = {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}
        self.assertFalse(app_main.prefer_x11_for_embedding(env, "linux"))
        self.assertNotIn("QT_QPA_PLATFORM", env)


if __name__ == "__main__":
    unittest.main()
