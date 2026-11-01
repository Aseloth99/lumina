"""Testler için sentetik video yardımcıları (gri tonu zamanı kodlar).

10 sn, 10 fps, 320x180; karenin gri tonu zamanla doğrusal artar (0 sn -> 0, 10 sn -> 255).
Böylece dönen karenin zamanı gri tondan okunabilir.
"""

from __future__ import annotations

import pathlib

from PyQt6.QtGui import QImage

FPS = 10
FRAMES = 100  # 10 sn
DURATION_MS = 10_000


def make_ramp_video(path: pathlib.Path, gray_offset: int = 0) -> bool:
    """Sentetik videoyu yaz. Kodlayıcı yoksa False."""
    import cv2
    import numpy as np

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (320, 180))
    if not writer.isOpened():
        return False
    for i in range(FRAMES):
        g = max(0, min(255, int(round(255 * i / (FRAMES - 1))) + gray_offset))
        writer.write(np.full((180, 320, 3), g, dtype=np.uint8))
    writer.release()
    return path.is_file() and path.stat().st_size > 0


def gray_of(img: QImage) -> int:
    return img.pixelColor(img.width() // 2, img.height() // 2).red()


def expected_gray(ms: int) -> float:
    return 255 * (ms / 1000.0 * FPS) / (FRAMES - 1)


def find_ffmpeg() -> str | None:
    import shutil

    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def make_multitrack_video(path: pathlib.Path) -> bool:
    """6 sn mkv: 2 ses (tur 440 Hz, eng 880 Hz) + 2 altyazı (tur, eng). ffmpeg yoksa False."""
    import subprocess

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False
    folder = path.parent
    srt_tr, srt_en = folder / "t_tr.srt", folder / "t_en.srt"
    srt_tr.write_text("1\n00:00:00,000 --> 00:00:05,000\nMerhaba dunya\n", encoding="utf-8")
    srt_en.write_text("1\n00:00:00,000 --> 00:00:05,000\nHello world\n", encoding="utf-8")
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=6",
        "-i", str(srt_tr), "-i", str(srt_en),
        "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-map", "4",
        "-c:v", "mpeg4", "-c:a", "aac", "-c:s", "srt",
        "-metadata:s:a:0", "language=tur", "-metadata:s:a:1", "language=eng",
        "-metadata:s:s:0", "language=tur", "-metadata:s:s:1", "language=eng",
        str(path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except Exception:
        return False
    return path.is_file() and path.stat().st_size > 0
