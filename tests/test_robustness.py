"""safe_emit (silinmiş QObject) ve çökme günlüğü döndürme."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.core.crashlog import rotate_if_large  # noqa: E402
from app.core.qt_safe import safe_emit  # noqa: E402


class _Sig(QObject):
    ping = pyqtSignal(int)


class SafeEmitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_emit_on_live_object(self) -> None:
        sig = _Sig()
        got: list[int] = []
        sig.ping.connect(got.append)
        self.assertTrue(safe_emit(sig, "ping", 7))
        self.assertEqual(got, [7])

    def test_deleted_qobject_does_not_raise(self) -> None:
        """Eski hata: 'wrapped C/C++ object of type ThumbSignals has been deleted' günlüğü şişiriyordu."""
        try:
            from PyQt6 import sip
        except ImportError:  # pragma: no cover
            self.skipTest("sip yok")
        sig = _Sig()
        sip.delete(sig)
        # Çıplak kullanım (öz­nitelik erişimi + emit) hata verirdi:
        with self.assertRaises((RuntimeError, AttributeError)):
            sig.ping.emit(1)
        self.assertFalse(safe_emit(sig, "ping", 1))

    def test_attribute_access_on_deleted_owner_is_guarded(self) -> None:
        """Asıl günlük hatası: `self.signals.loaded` ifadesinin KENDİSİ silinmiş nesnede patlıyordu."""
        from PyQt6 import sip

        sig = _Sig()
        sip.delete(sig)
        with self.assertRaises((RuntimeError, AttributeError)):
            _ = sig.ping
        self.assertFalse(safe_emit(sig, "ping", 1))


class CrashLogRotationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = pathlib.Path(self._tmp.name) / "crash.log"

    def test_large_log_is_rotated_to_single_backup(self) -> None:
        self.log.write_bytes(b"x" * 5000)
        self.assertTrue(rotate_if_large(self.log, max_bytes=1000))
        self.assertFalse(self.log.exists())
        backup = self.log.with_name(self.log.name + ".1")
        self.assertEqual(backup.stat().st_size, 5000)
        # ikinci döndürme eski yedeği ezer (tek yedek tutulur)
        self.log.write_bytes(b"y" * 3000)
        self.assertTrue(rotate_if_large(self.log, max_bytes=1000))
        self.assertEqual(backup.stat().st_size, 3000)

    def test_small_or_missing_log_is_untouched(self) -> None:
        self.assertFalse(rotate_if_large(self.log, max_bytes=1000))  # yok
        self.log.write_bytes(b"x" * 10)
        self.assertFalse(rotate_if_large(self.log, max_bytes=1000))
        self.assertEqual(self.log.read_bytes(), b"x" * 10)


if __name__ == "__main__":
    unittest.main()
