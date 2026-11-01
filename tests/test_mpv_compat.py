"""libmpv sürüm uyumu: tanınmayan/reddedilen seçenekler düşürülür, arka plan söz dizimi iki sürümde de çalışır."""

from __future__ import annotations

import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.ui.player import apply_mpv_background, create_mpv, mpv_options  # noqa: E402


class _Handle:
    pass


def _value_error(name: str, value: str = "x") -> ValueError:
    """python-mpv'nin geçersiz değer hatası: (mesaj, kod, (handle, ad, değer))."""
    return ValueError("Invalid value for mpv option", -7, (_Handle(), name.encode(), value.encode()))


def _attr_error(name: str) -> AttributeError:
    return AttributeError("Option not found", -5, (_Handle(), name.encode(), b"x"))


class _FakeMpvModule:
    """Belirtilen seçenekleri reddeden sahte `mpv` modülü."""

    def __init__(self, reject: dict[str, BaseException]):
        self.reject = reject
        self.calls: list[dict] = []
        outer = self

        class MPV:
            def __init__(self, **kwargs):
                outer.calls.append(dict(kwargs))
                for key in kwargs:
                    if key in outer.reject:
                        raise outer.reject[key]
                self.kwargs = kwargs

        self.MPV = MPV


class CreateMpvTest(unittest.TestCase):
    def test_all_accepted_creates_once(self) -> None:
        mod = _FakeMpvModule({})
        m = create_mpv(mod, {"hwdec": "auto-safe", "vo": "gpu", "wid": "1"}, log_handler=None)
        self.assertEqual(len(mod.calls), 1)
        self.assertEqual(m.kwargs["hwdec"], "auto-safe")
        self.assertIn("log_handler", m.kwargs)

    def test_rejected_optional_options_are_dropped_and_retried(self) -> None:
        mod = _FakeMpvModule({"video-latency-hacks": _attr_error("video-latency-hacks"), "scale": _value_error("scale")})
        m = create_mpv(mod, {"vo": "gpu", "scale": "bilinear", "video-latency-hacks": "yes", "profile": "fast"})
        self.assertEqual(set(m.kwargs), {"vo", "profile"})
        self.assertEqual(len(mod.calls), 3)

    def test_option_names_with_underscores_match_dashes(self) -> None:
        mod = _FakeMpvModule({"d3d11va_zero_copy": _value_error("d3d11va-zero-copy")})
        m = create_mpv(mod, {"vo": "gpu", "d3d11va_zero_copy": "yes"})
        self.assertEqual(set(m.kwargs), {"vo"})

    def test_required_option_failure_is_raised(self) -> None:
        mod = _FakeMpvModule({"vo": _value_error("vo", "gpu")})
        with self.assertRaises(ValueError):
            create_mpv(mod, {"vo": "gpu", "wid": "1"})
        mod2 = _FakeMpvModule({"wid": _value_error("wid", "1")})
        with self.assertRaises(ValueError):
            create_mpv(mod2, {"vo": "gpu", "wid": "1"})

    def test_unrelated_errors_are_raised(self) -> None:
        mod = _FakeMpvModule({"profile": RuntimeError("libmpv çöktü")})
        with self.assertRaises(RuntimeError):
            create_mpv(mod, {"vo": "gpu", "profile": "fast"})


class _FakePlayerHandle:
    """mpv_set -> _set_property; `new_syntax` False ise eski mpv: background=color reddedilir."""

    def __init__(self, new_syntax: bool):
        self.new_syntax = new_syntax
        self.props: dict[str, str] = {}

    def _set_property(self, name, value):
        if name == "background":
            if value == "color" and not self.new_syntax:
                raise ValueError("Invalid value for mpv option")
            if value != "color" and self.new_syntax:
                raise ValueError("background değeri 'color' vb. olmalı")
        elif name == "background-color" and not self.new_syntax:
            raise AttributeError("Option not found")
        self.props[name] = value


class BackgroundSyntaxTest(unittest.TestCase):
    def test_new_syntax_mpv_038_plus(self) -> None:
        m = _FakePlayerHandle(new_syntax=True)
        self.assertTrue(apply_mpv_background(m, "#07080c"))
        self.assertEqual(m.props, {"background": "color", "background-color": "#07080c"})

    def test_old_syntax_mpv_037(self) -> None:
        m = _FakePlayerHandle(new_syntax=False)
        self.assertTrue(apply_mpv_background(m, "#07080c"))
        self.assertEqual(m.props, {"background": "#07080c"})

    def test_none_instance_is_false(self) -> None:
        self.assertFalse(apply_mpv_background(None, "#000000"))

    def test_creation_options_do_not_contain_version_specific_background(self) -> None:
        for plat in ("win32", "linux", "darwin"):
            opts = mpv_options(1, platform=plat)
            self.assertNotIn("background", opts)
            self.assertNotIn("background-color", opts)


if __name__ == "__main__":
    unittest.main()
