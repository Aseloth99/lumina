"""Güncelleme politikası (saf mantık): URL (neden kodları), checksum, indirilen paketin yeri."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core import update_policy as pol  # noqa: E402


class ValidateUrlTest(unittest.TestCase):
    def test_trusted_https_accepted(self) -> None:
        for url in (
            "https://macik.com.tr/downloads/Lumina.zip",
            "https://cdn.macik.com.tr/Lumina.zip",
            "https://MACIK.COM.TR/x.zip",
            # Sunucu adresi uzantısız olabilir (paket türü içeriğe bakılarak anlaşılır)
            "https://macik.com.tr/Pages/AppDownload?slug=lumina&file=Lumina.exe",
            "https://macik.com.tr/Pages/AppDownload?slug=lumina",
        ):
            self.assertEqual(pol.validate_update_url(url), (True, ""), url)

    def test_untrusted_or_insecure_rejected_with_a_reason_code(self) -> None:
        cases = {
            "http://macik.com.tr/Lumina.zip": "url_insecure",  # HTTPS değil
            "https://evil.example/Lumina.zip": "url_untrusted:evil.example",  # güvenilmeyen alan adı
            "https://macik.com.tr.evil.example/x.zip": "url_untrusted:macik.com.tr.evil.example",  # benzetme
            "https://evilmacik.com.tr/x.zip": "url_untrusted:evilmacik.com.tr",  # son ek hilesi
            "https://user:pw@macik.com.tr/x.zip": "url_credentials",  # kimlik bilgisi
            "https://macik.com.tr:8443/x.zip": "url_port",  # beklenmeyen port
            "ftp://macik.com.tr/x.zip": "url_insecure",
            "file:///C:/x.zip": "url_invalid",
            "": "url_invalid",
            "not a url": "url_invalid",
        }
        for url, code in cases.items():
            ok, reason = pol.validate_update_url(url)
            self.assertFalse(ok, url)
            self.assertEqual(reason, code, url)

    def test_localhost_only_when_not_frozen(self) -> None:
        self.assertTrue(pol.validate_update_url("http://127.0.0.1:8000/x.zip")[0])
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertFalse(pol.validate_update_url("http://127.0.0.1:8000/x.zip")[0])
            self.assertFalse(pol.validate_update_url("http://localhost/x.zip")[0])

    def test_every_reason_code_has_a_message_in_both_languages(self) -> None:
        from app.resources.locales import LOCALES

        for lang in ("tr", "en"):
            for code in ("url_invalid", "url_credentials", "url_insecure", "url_port", "url_untrusted", "url_redirect"):
                self.assertIn(f"update_err_{code}", LOCALES[lang], (lang, code))


class ChecksumTest(unittest.TestCase):
    def test_checksum_normalization(self) -> None:
        good = "A" * 64
        self.assertEqual(pol.normalize_checksum("sha256:" + good), "a" * 64)
        self.assertEqual(pol.normalize_checksum("  " + good + " "), "a" * 64)
        self.assertEqual(pol.normalize_checksum("SHA256:" + good), "a" * 64)
        self.assertIsNone(pol.normalize_checksum(""))
        self.assertIsNone(pol.normalize_checksum(None))
        for bad in ("xyz", "a" * 63, "g" * 64, "sha1:" + "a" * 40, "a" * 65):
            with self.assertRaises(ValueError):
                pol.normalize_checksum(bad)


class PackageLocationTest(unittest.TestCase):
    """Eski betik testlerinin (dosya adı temizleme, %/!/\" yolları) yerine: indirilen paketin adı/yolu sunucudan GELMEZ."""

    def test_package_path_is_fixed_and_never_derived_from_the_server(self) -> None:
        from app.core.update_installer import PACKAGE_PREFIX, PACKAGE_SUFFIX, package_path

        path = package_path()
        self.assertEqual(os.path.dirname(path), tempfile.gettempdir())
        name = os.path.basename(path)
        self.assertTrue(name.startswith(PACKAGE_PREFIX) and name.endswith(PACKAGE_SUFFIX), name)
        self.assertEqual(package_path(), package_path(), "aynı süreç için aynı yol")
        self.assertNotEqual(package_path(1), package_path(2), "açık birkaç Lumina süreci aynı dosyaya yazmaz")


if __name__ == "__main__":
    unittest.main()
