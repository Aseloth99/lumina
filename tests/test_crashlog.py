"""Çökme günlüğü: yakalanıp işlenen SEH istisnaları günlüğe/çökmeye yol açmamalı; gerçek çökme yazılmalı.

Eski hata: `faulthandler.enable()` Windows'ta yakalanıp işlenen istisnalarda da (libmpv/D3D11 saniyede
bir 0xE24C4A02 fırlatır) tüm thread yığınlarını döküyor, çalışan thread'lerle yarışıp video oynatırken
süreci çökertiyordu.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _run(code: str, appdata: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, LOCALAPPDATA=appdata, PYTHONPATH=str(ROOT))
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@unittest.skipUnless(sys.platform == "win32", "Windows SEH davranışı")
class WindowsCrashFilterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = pathlib.Path(self._tmp.name) / "Lumina" / "logs" / "crash.log"  # diğer uygulamalarla aynı ad/konum

    def test_handled_exception_is_not_logged(self) -> None:
        proc = _run(
            """
            import ctypes
            from app.core.crashlog import install_crash_log
            install_crash_log()
            for _ in range(50):
                try:
                    # ctypes çağrısı SEH ile yakalayıp OSError'a çevirir: işlenen, zararsız istisna.
                    ctypes.windll.kernel32.RaiseException(0xE24C4A02, 0, 0, None)
                except OSError:
                    pass
            print("alive")
            """,
            self._tmp.name,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("alive", proc.stdout)
        text = self.log.read_text(encoding="utf-8", errors="replace") if self.log.exists() else ""
        self.assertNotIn("fatal exception", text)

    def test_real_crash_is_logged(self) -> None:
        proc = _run(
            """
            import ctypes, time
            from app.core.crashlog import install_crash_log
            install_crash_log()
            # Başlangıç adresi NULL olan bir thread: işlenmemiş erişim ihlali (gerçek çökme).
            ctypes.windll.kernel32.CreateThread(None, 0, None, None, 0, None)
            time.sleep(10)
            """,
            self._tmp.name,
        )
        self.assertNotEqual(proc.returncode, 0)
        text = self.log.read_text(encoding="utf-8", errors="replace")
        self.assertIn("Windows fatal exception: code 0xc0000005", text)
        self.assertIn("most recent call first", text)


if __name__ == "__main__":
    unittest.main()
