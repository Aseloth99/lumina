"""Görüntü histogramı (R, G, B, parlaklık) — Qt yok, yalnızca numpy."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # numpy yalnızca kullanılınca (işlev içinde) yüklenir
    import numpy as np

CHANNELS = ("r", "g", "b", "luma")


def compute(rgb: np.ndarray, max_side: int = 512) -> dict[str, np.ndarray]:
    """`rgb`: (H, W, 3|4) uint8. Hız için büyük görüntü seyreltilir (histogram biçimi korunur).

    Dönüş: her kanal için 256 elemanlı int64 dizi.
    """
    import numpy as np  # yalnızca ayrıntı paneli açıkken (açılışta yüklenmesin)

    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise ValueError("RGB(A) dizi bekleniyor")
    h, w = rgb.shape[:2]
    step = max(1, max(h, w) // max_side)
    small = rgb[::step, ::step, :3]
    out: dict[str, np.ndarray] = {}
    for i, name in enumerate(("r", "g", "b")):
        out[name] = np.bincount(small[:, :, i].ravel(), minlength=256).astype(np.int64)
    luma = (
        small[:, :, 0].astype(np.uint32) * 299
        + small[:, :, 1].astype(np.uint32) * 587
        + small[:, :, 2].astype(np.uint32) * 114
    ) // 1000
    out["luma"] = np.bincount(luma.ravel(), minlength=256).astype(np.int64)
    return out


def normalized(counts: np.ndarray, clip_percentile: float = 99.5) -> np.ndarray:
    """0..1 aralığına ölçekle; uç tepeler (saf siyah/beyaz) diğer değerleri ezmesin diye yüzdelikle kırp."""
    import numpy as np

    arr = counts.astype(np.float64)
    if arr.max() <= 0:
        return np.zeros(256)
    ceiling = max(float(np.percentile(arr[arr > 0], clip_percentile)), 1.0)
    return np.clip(arr / ceiling, 0.0, 1.0)


def qimage_to_rgb(qimage) -> np.ndarray:
    """QImage -> (H, W, 3) uint8 (kopya). Çağıran QImage'ı canlı tutmalıdır."""
    import numpy as np
    from PyQt6.QtGui import QImage

    img = qimage.convertToFormat(QImage.Format.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    ptr = img.constBits()
    ptr.setsize(bpl * h)
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, bpl)[:, : w * 3].reshape(h, w, 3)
    return arr.copy()
