"""Şerit küçük resimleri — tam kare decode yok, HEIC gömülü thumbnail / JPEG draft."""

from __future__ import annotations

import io
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps
from PyQt6.QtCore import QObject, QRunnable, Qt, pyqtSignal
from PyQt6.QtGui import QImage

from app.core.image_loader import (
    HAS_IMAGECODECS,
    HAS_RAWPY,
    HEIF_EXTS,
    JXR_EXTS,
    RAW_EXTS,
    _apply_icc,
    load_jxr,
    pil_to_qimage,
)
from app.core.matcher import VIDEO_EXTS
from app.core.qt_safe import safe_emit
from app.core.svg_loader import SVG_EXTS, load_svg

THUMB_SIZE = 160


def load_thumb_pil(path: Path, size: int = THUMB_SIZE) -> Image.Image:
    path = Path(path)
    suffix = path.suffix.lower()
    icc = None
    img = None

    if suffix in RAW_EXTS and HAS_RAWPY:
        try:
            import rawpy

            with rawpy.imread(str(path)) as raw_file:
                try:
                    thumb = raw_file.extract_thumb()
                    if thumb.format == rawpy.ThumbFormat.JPEG:
                        img = Image.open(io.BytesIO(thumb.data))
                    elif thumb.format == rawpy.ThumbFormat.BITMAP:
                        img = Image.fromarray(thumb.data)
                except Exception:
                    pass
                if img is None:
                    rgb = raw_file.postprocess(use_camera_wb=True, half_size=True)
                    img = Image.fromarray(rgb)
        except Exception:
            img = None

    if suffix in JXR_EXTS:
        # Doğrusal uzayda küçült, sonra ton eşle (8 MP float görseli tam boyda eşlemekten ~10x hızlı)
        img = load_jxr(path, max_side=size)

    if suffix in SVG_EXTS:
        img = load_svg(path, long_side=size * 2)  # vektör: küçük boyda çizmek zaten ucuz, 2x ile keskin kalır

    if img is None and suffix in HEIF_EXTS:
        try:
            heif = pillow_heif.open_heif(str(path), convert_hdr_to_8bit=True)
            icc = heif.info.get("icc_profile")
            thumbs = getattr(heif, "thumbnails", None) or []
            if thumbs and not isinstance(thumbs[0], int):
                try:
                    img = thumbs[0].to_pillow()
                except Exception:
                    img = None
            if img is None:
                img = heif.to_pillow()
            if img is not None and icc and not img.info.get("icc_profile"):
                img.info["icc_profile"] = icc
        except Exception:
            img = None

    if img is None and suffix == ".jxl" and HAS_IMAGECODECS:
        try:
            import imagecodecs

            with open(path, "rb") as f:
                arr = imagecodecs.jpegxl_decode(f.read())
            img = Image.fromarray(arr)
        except Exception:
            img = None

    if img is None and suffix in {".avif", ".avifs"} and HAS_IMAGECODECS:
        try:
            import imagecodecs

            with open(path, "rb") as f:
                arr = imagecodecs.avif_decode(f.read())
            img = Image.fromarray(arr)
        except Exception:
            img = None

    if img is None:
        try:
            with Image.open(path) as raw:
                try:
                    raw.draft("RGB", (size, size))
                except Exception:
                    pass
                raw.load()
                img = raw.copy()
        except Exception:
            if suffix == ".jxl" and HAS_IMAGECODECS:
                import imagecodecs

                with open(path, "rb") as f:
                    arr = imagecodecs.jpegxl_decode(f.read())
                img = Image.fromarray(arr)
            elif suffix in {".avif", ".avifs"} and HAS_IMAGECODECS:
                import imagecodecs

                with open(path, "rb") as f:
                    arr = imagecodecs.avif_decode(f.read())
                img = Image.fromarray(arr)
            else:
                raise
    img = ImageOps.exif_transpose(img)
    img = _apply_icc(img)
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    return img


def load_thumb_qimage(path: Path, size: int = THUMB_SIZE) -> QImage:
    path = Path(path)
    if path.suffix.lower() in VIDEO_EXTS:
        from app.core.video_io import video_poster

        poster = video_poster(path)
        if poster is None or poster.isNull():
            raise OSError("video thumb")
        return poster.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return pil_to_qimage(load_thumb_pil(path, size))


class ThumbSignals(QObject):
    loaded = pyqtSignal(str, QImage, int)  # path, image, token
    failed = pyqtSignal(str, int)


class ThumbWorker(QRunnable):
    def __init__(self, path: str, token: int) -> None:
        super().__init__()
        self.path = path
        self.token = token
        self.signals = ThumbSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            image = load_thumb_qimage(Path(self.path))
            safe_emit(self.signals, "loaded", self.path, image, self.token)
        except Exception:
            safe_emit(self.signals, "failed", self.path, self.token)
