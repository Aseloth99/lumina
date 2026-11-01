"""HDR (doğrusal float / scRGB) görüntüleri SDR 8-bit'e ton eşleme — Qt'siz, yalnızca numpy.

Qt Widgets 8-bit sRGB gösterir; gerçek HDR çıkışı (DXGI scRGB swapchain) bu katmanda yoktur.
Bu modül HDR dosyalarını (JPEG XR `.jxr`/`.wdp`/`.hdp` — Xbox Game Bar, oyun ve HDR ekran
görüntüleri) görüntülenebilir hale getirir:

* Girdi doğrusal scRGB'dir (BT.709 birincilleri, 1.0 = 80 nit). Piksel değerleri SDR beyazının
  çok üstüne çıkabilir (örn. 5.7 ≈ 458 nit).
* Referans beyaz BT.2408'e göre 203 nit alınır: değer / (203/80) → "kâğıt beyazı" 1.0 olur.
* Vurgular, kâğıt beyazının üstünde yumuşak bir omuz (genişletilmiş Reinhard) ile 1.0'a
  sıkıştırılır; ton eşleme `max(R,G,B)` üzerinden ölçeklenir, böylece renk tonu korunur ve
  kanal kırpılması olmaz. SDR aralığındaki (≤ diz noktası) değerler değişmez.
* Doğrusal → sRGB (OETF) bir arama tablosuyla 8-bite çevrilir; bant bant (satır grubu)
  işlenir, 8 MP'lik görselde bellek tepe noktası küçük kalır.
"""

from __future__ import annotations

import numpy as np

SCRGB_WHITE_NITS = 80.0
REFERENCE_WHITE_NITS = 203.0  # ITU-R BT.2408 HDR referans beyazı
PAPER_WHITE = REFERENCE_WHITE_NITS / SCRGB_WHITE_NITS  # ≈ 2.5375 (scRGB biriminde)
KNEE = 0.75  # bu değerin altı (kâğıt beyazına oranla) değişmeden geçer
PEAK_PERCENTILE = 99.95
_BAND_ROWS = 128
_LUT_SIZE = 16384

JXR_EXTS = frozenset({".jxr", ".wdp", ".hdp"})


def _build_srgb_lut() -> np.ndarray:
    x = np.linspace(0.0, 1.0, _LUT_SIZE, dtype=np.float64)
    y = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1.0 / 2.4) - 0.055)
    return np.clip(np.rint(y * 255.0), 0, 255).astype(np.uint8)


_SRGB_LUT = _build_srgb_lut()


def linear_to_srgb8(linear: np.ndarray) -> np.ndarray:
    """[0,1] doğrusal float → sRGB kodlu uint8 (arama tablosu)."""
    idx = np.clip(linear * (_LUT_SIZE - 1) + 0.5, 0, _LUT_SIZE - 1).astype(np.uint16)
    return _SRGB_LUT[idx]


def estimate_peak(rgb: np.ndarray, paper_white: float = PAPER_WHITE) -> float:
    """Kâğıt beyazına göre ölçeklenmiş tepe (maxRGB, ~%99.95). Alt örnekleme ile hızlıdır."""
    step = max(1, int(max(rgb.shape[0], rgb.shape[1]) // 640))
    sample = rgb[::step, ::step, :3].astype(np.float32, copy=False)
    peak = float(np.percentile(sample.max(axis=-1), PEAK_PERCENTILE))
    return max(peak, 0.0) / paper_white


def _shoulder_scale(m: np.ndarray, white: float, knee: float) -> np.ndarray:
    """m (>=0, kâğıt beyazı=1) için ölçek = g(m)/m. g: diz noktasına kadar özdeşlik, sonra omuz."""
    out = np.ones_like(m)
    hot = m > knee
    if not hot.any():
        return out
    mh = m[hot]
    span = 1.0 - knee
    u = (mh - knee) / span
    uw = max((white - knee) / span, 1e-3)
    g = knee + span * (u * (1.0 + u / (uw * uw))) / (1.0 + u)
    out[hot] = g / mh
    return out


def tonemap_scrgb_to_srgb8(
    rgb: np.ndarray,
    *,
    paper_white: float = PAPER_WHITE,
    knee: float = KNEE,
) -> np.ndarray:
    """(H,W,>=3) doğrusal scRGB float → (H,W,3) uint8 sRGB. Alfa yok sayılır."""
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise ValueError("RGB(A) dizisi bekleniyordu")
    height, width = rgb.shape[:2]
    inv = np.float32(1.0 / paper_white)
    peak = estimate_peak(rgb, paper_white)
    white = max(peak, 1.0 + 1e-3)
    out = np.empty((height, width, 3), dtype=np.uint8)
    for y0 in range(0, height, _BAND_ROWS):
        band = np.asarray(rgb[y0 : y0 + _BAND_ROWS, :, :3], dtype=np.float32) * inv
        # NaN/inf (bozuk veya sınırsız pikseller) kırpılır; gamut dışı negatifler sıfırlanır
        np.nan_to_num(band, copy=False, nan=0.0, posinf=1.0e4, neginf=0.0)
        np.maximum(band, 0.0, out=band)
        m = band.max(axis=-1)
        if peak > knee:
            band *= _shoulder_scale(m, white, knee)[..., None]
        np.minimum(band, 1.0, out=band)
        out[y0 : y0 + _BAND_ROWS] = linear_to_srgb8(band)
    return out


def hdr_array_to_pil(arr: np.ndarray):
    """Çözülmüş JXR/HDR dizisi → PIL.Image (RGB / RGBA / L). Float → ton eşleme, tamsayı → 8-bit."""
    from PIL import Image

    if arr.ndim == 2:
        arr = arr[..., None]
    channels = arr.shape[2]
    alpha = None
    if arr.dtype.kind == "f":
        if channels >= 4:
            a = arr[..., 3]
            if float(a.min()) < 0.999:  # gerçek saydamlık varsa koru
                alpha = np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)
        if channels == 1:
            arr = np.repeat(arr, 3, axis=2)
        elif channels == 2:
            arr = np.repeat(arr[..., :1], 3, axis=2)
        rgb8 = tonemap_scrgb_to_srgb8(arr)
    else:
        if arr.dtype == np.uint16:
            data = (arr >> 8).astype(np.uint8)
        elif arr.dtype == np.uint8:
            data = arr
        else:  # int16/int32 sabit nokta vb.
            peak = float(np.max(arr)) or 1.0
            data = np.clip(arr.astype(np.float32) / peak * 255.0 + 0.5, 0, 255).astype(np.uint8)
        if channels == 1:
            data = np.repeat(data, 3, axis=2)
        elif channels == 2:
            data = np.repeat(data[..., :1], 3, axis=2)
        if channels >= 4:
            alpha = data[..., 3] if int(data[..., 3].min()) < 255 else None
        rgb8 = np.ascontiguousarray(data[..., :3])
    img = Image.fromarray(rgb8, "RGB")
    if alpha is not None:
        img.putalpha(Image.fromarray(alpha, "L"))
    return img


def decode_jxr(data: bytes):
    """JPEG XR baytlarını PIL.Image'a çözer (HDR ise SDR'ye ton eşlenmiş)."""
    import imagecodecs

    return hdr_array_to_pil(imagecodecs.jpegxr_decode(data))
