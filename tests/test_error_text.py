"""Oynatici/yukleyici hata metinleri arayuz diline cevrilir; bilinmeyen metin oldugu gibi kalir."""

from __future__ import annotations

import ast
import os
import pathlib
import re
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.resources.locales import translate_error

TURKISH = re.compile(r"[ğüşıöçĞÜŞİÖÇ]")


def _player_messages() -> list[str]:
    tree = ast.parse((ROOT / "app" / "ui" / "player.py").read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        # self.failed.emit("...")  /  self.failed.emit(gen, "...")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "emit":
            owner = node.func.value
            if isinstance(owner, ast.Attribute) and owner.attr == "failed":
                found += [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        # _MPV_ERROR_TEXT = {...}
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_MPV_ERROR_TEXT" for t in node.targets):
            found += [v.value for v in node.value.values if isinstance(v, ast.Constant)]
    return found


class TranslateErrorTest(unittest.TestCase):
    def test_turkish_keeps_the_text(self):
        self.assertEqual(translate_error("Video açılamadı.", "tr"), "Video açılamadı.")

    def test_english_translates_known_messages(self):
        self.assertEqual(translate_error("Video açılamadı.", "en"), "Could not open the video.")
        self.assertEqual(translate_error("Video açılamadı (mpv hata kodu -13).", "en"),
                         "Could not open the video (mpv error code -13).")
        self.assertEqual(translate_error("JPEG XR dosyası çözülemedi: bad data", "en"),
                         "Could not decode the JPEG XR file: bad data")

    def test_unknown_text_is_untouched(self):
        raw = "cannot identify image file 'x.bin'"
        self.assertEqual(translate_error(raw, "en"), raw)
        self.assertEqual(translate_error("", "en"), "")

    def test_image_edit_messages_have_an_english_text(self):
        import tempfile

        from app.core import image_edit

        tree = ast.parse((ROOT / "app" / "core" / "image_edit.py").read_text(encoding="utf-8"))
        messages: list[str] = []
        for node in ast.walk(tree):  # raise ValueError("...") / raise ValueError(SABIT)
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                arg = node.exc.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    messages.append(arg.value)
                elif isinstance(arg, ast.Name) and isinstance(getattr(image_edit, arg.id, None), str):
                    messages.append(getattr(image_edit, arg.id))
        self.assertGreaterEqual(len(messages), 3, "mesajlar okunamadı: image_edit.py değişmiş olabilir")
        with tempfile.TemporaryDirectory() as tmp:  # biçimli metin: gerçek hatadan
            heic = pathlib.Path(tmp) / "x.heic"
            heic.write_bytes(b"0")
            with self.assertRaises(ValueError) as ctx:
                image_edit.rotate_and_save(heic, 90)
            messages.append(str(ctx.exception))
        missing = [m for m in messages if TURKISH.search(m) and translate_error(m, "en") == m]
        self.assertEqual(missing, [], "bu metinlerin İngilizce karşılığı yok (locales._ERRORS_EN)")
        self.assertEqual(translate_error(str(ctx.exception), "en"), "Saving the rotation is not supported for .heic")

    def test_every_fixed_player_message_has_an_english_text(self):
        messages = _player_messages()
        self.assertGreaterEqual(len(messages), 6, "mesajlar okunamadı: player.py değişmiş olabilir")
        missing = [m for m in messages if TURKISH.search(m) and translate_error(m, "en") == m]
        self.assertEqual(missing, [], "bu metinlerin İngilizce karşılığı yok (locales._ERRORS_EN)")


if __name__ == "__main__":
    unittest.main()
