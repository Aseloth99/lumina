"""Testler için sentetik örnek medya (depoda gerçek fotoğraf ya da video yoktur).

* `sample_jpg()`: 1920x1080 JPEG (renk geçişi ve düz şekiller).
* `apple_pair()`: (HEIC, MOV) çifti; ikisi de AYNI Apple `ContentIdentifier` GUID'ini taşır.
  Gerçek bir iPhone çifti değildir: GUID, `uuid_parser`'ın taradığı bölgeye ISO-BMFF `free` kutusu olarak eklenir.

Dosyalar, süreç başına bir kez oluşturulan geçici bir klasöre yazılır ve süreç biterken silinir.
"""

from __future__ import annotations

import atexit
import pathlib
import shutil
import struct
import tempfile

GUID = "6d1f0c3e-2b7a-4c58-9e41-a3b5c7d9e0f2"
_cache: dict[str, pathlib.Path] = {}
_root: pathlib.Path | None = None


def _folder() -> pathlib.Path:
    global _root
    if _root is None:
        _root = pathlib.Path(tempfile.mkdtemp(prefix="lumina_synth_"))
        atexit.register(shutil.rmtree, _root, True)
    return _root


def _free_box(anchor: bytes) -> bytes:
    """Üst düzey `free` kutusu: bir ISO-BMFF okuyucusu atlar; `uuid_parser` çapa + GUID'i bulur."""
    payload = anchor + b"\x00" + GUID.encode("ascii") + b"\x00"
    return struct.pack(">I", 8 + len(payload)) + b"free" + payload


def sample_jpg() -> pathlib.Path:
    """1920x1080 sentetik fotoğraf (gün batımı benzeri geçiş + dağ silueti)."""
    if "jpg" not in _cache:
        import numpy as np
        from PIL import Image, ImageDraw

        width, height = 1920, 1080
        y = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
        x = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
        sky = np.stack(
            [
                40 + 190 * y + 25 * x,
                30 + 90 * y + 40 * (1 - x),
                90 + 60 * (1 - y) + 20 * x,
            ],
            axis=-1,
        )
        image = Image.fromarray(np.clip(sky, 0, 255).astype(np.uint8), "RGB")
        draw = ImageDraw.Draw(image)
        draw.ellipse((1180, 520, 1420, 760), fill=(255, 214, 140))
        ridge = [(0, 1080), (0, 760)]
        for i in range(0, 1921, 120):
            ridge.append((i, 700 + int(60 * np.sin(i / 210.0)) + (i * 7) % 40))
        ridge += [(1920, 1080)]
        draw.polygon(ridge, fill=(48, 36, 74))
        path = _folder() / "sample.jpg"
        image.save(path, "JPEG", quality=88)
        _cache["jpg"] = path
    return _cache["jpg"]


def _tiny_mov(path: pathlib.Path) -> None:
    """Küçük, gerçek bir video (OpenCV mp4v). Kodlayıcı yoksa yalnızca `ftyp` kutusu yazılır."""
    try:
        import cv2
        import numpy as np

        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 90))
        if writer.isOpened():
            for i in range(10):
                writer.write(np.full((90, 160, 3), 20 + 20 * i, dtype=np.uint8))
            writer.release()
            if path.is_file() and path.stat().st_size > 0:
                return
    except Exception:  # noqa: BLE001 — kodlayıcı yoksa aşağıdaki en küçük kapsayıcıya düşülür
        pass
    path.write_bytes(struct.pack(">I", 20) + b"ftypqt  " + b"\x00" * 4 + b"qt  ")


def apple_pair() -> tuple[pathlib.Path, pathlib.Path]:
    """(IMG_0001.HEIC, IMG_0001.MOV): aynı ContentIdentifier GUID'i, yan yana eşleşen adlar."""
    if "heic" not in _cache:
        import pillow_heif
        from PIL import Image

        pillow_heif.register_heif_opener()
        folder = _folder()
        heic = folder / "IMG_0001.HEIC"
        Image.new("RGB", (640, 480), (70, 120, 200)).save(heic, quality=60)
        with heic.open("ab") as handle:
            handle.write(_free_box(b"ContentIdentifier"))
        mov = folder / "IMG_0001.MOV"
        _tiny_mov(mov)
        with mov.open("ab") as handle:
            handle.write(_free_box(b"com.apple.quicktime.content.identifier"))
        _cache["heic"], _cache["mov"] = heic, mov
    return _cache["heic"], _cache["mov"]
