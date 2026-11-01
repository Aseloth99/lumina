"""Lumina"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

_T0_MS = time.time() * 1000.0  # açılış izi (LUMINA_STARTUP_TRACE): betiğin ilk satırı

os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
os.environ.setdefault("AV_LOG_FORCE_NOCOLOR", "1")
os.environ.setdefault("AV_LOG_LEVEL", "quiet")
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
os.environ.setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", "d3d11va,dxva2,cuda,qsv")


def _deps_ok() -> bool:
    """Bağımlılıklar kurulu mu? Yalnızca bulunur, yüklenmez: eskiden cv2 (+numpy) yalnızca bu
    denetim için her açılışta içe aktarılıyordu (fotoğraf açarken hiç gerekmezken)."""
    from importlib.util import find_spec

    try:
        return all(find_spec(name) is not None for name in ("cv2", "PIL", "pillow_heif", "PyQt6"))
    except (ImportError, ValueError):
        return False


def _python_for_launch() -> Path:
    exe = Path(sys.executable).resolve()
    if exe.name.lower() == "python.exe":
        console_attached = False
        if os.name == "nt":
            try:
                import ctypes

                console_attached = bool(ctypes.windll.kernel32.GetConsoleWindow())
            except (AttributeError, OSError):
                pass
        terminal_attached = console_attached or sys.stdout.isatty() or any(
            os.environ.get(name)
            for name in (
                "TERM",
                "WT_SESSION",
                "TERM_PROGRAM",
                "ConEmuANSI",
                "PSModulePath",
                "VSCODE_PID",
            )
        )
        if not terminal_attached:
            silent = exe.with_name("pythonw.exe")
            if silent.is_file():
                return silent
    return exe


def _bootstrap() -> None:
    if os.environ.get("LUMINA_BOOTSTRAPPED") == "1":
        return
    if getattr(sys, "frozen", False):
        return
    py = _python_for_launch()
    if py != Path(sys.executable).resolve():
        env = os.environ.copy()
        env["LUMINA_BOOTSTRAPPED"] = "1"
        raise SystemExit(
            subprocess.call(
                [str(py), str(Path(__file__).resolve()), *sys.argv[1:]],
                env=env,
            )
        )
    if not _deps_ok():
        sys.stderr.write(
            "Lumina: Eksik bagimliliklar var (PyQt6 / pillow-heif / opencv / Pillow). 'pip install -r requirements.txt' calistirin veya dist\\Lumina.exe kullanin.\n"
        )
        raise SystemExit(1)


if __name__ == "__main__":
    _bootstrap()
    from app.core import startup_trace

    startup_trace.mark("launcher_start", _T0_MS)
    from app.core.crashlog import install_crash_log
    from app.main import main

    install_crash_log()
    raise SystemExit(main())
