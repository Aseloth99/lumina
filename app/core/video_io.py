"""Video kare okuma yardımcıları — GUI yok, OpenCV/FFmpeg sessiz, tembel yüklenir.

Önizleme (seek hover) için kalıcı decoder `app.core.preview_decoder` içindedir; burada
yalnızca ortak, durumsuz yardımcılar ve poster/süre okuma bulunur.
"""

from __future__ import annotations

import os
import sys
import threading
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtGui import QImage

os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
os.environ.setdefault("AV_LOG_FORCE_NOCOLOR", "1")
os.environ.setdefault("AV_LOG_LEVEL", "quiet")

_cv2_lock = threading.Lock()
_cv2_module = None
_cv2_tried = False


def load_cv2():
    """OpenCV'yi ilk kullanımda yükle (açılış maliyeti yok) ve sessizleştir."""
    global _cv2_module, _cv2_tried
    if _cv2_tried:
        return _cv2_module
    with _cv2_lock:
        if _cv2_tried:
            return _cv2_module
        try:
            import cv2
        except ImportError:
            _cv2_module = None
        else:
            if not hasattr(cv2, "VideoCapture"):
                # Yarım/bozuk kurulum (örn. opencv-python kaldırılınca ortak dosyalar silindi):
                # import başarılı ama modül boş. Yedek yollar (önizleme yok) devreye girsin.
                _cv2_module = None
            else:
                _cv2_module = cv2
                _silence_cv2_logging(cv2)
                _quiet_bundled_ffmpeg(cv2)
        _cv2_tried = True
    return _cv2_module


def _silence_cv2_logging(cv2) -> None:
    try:
        logging = getattr(getattr(cv2, "utils", None), "logging", None)
        if logging is not None:
            level = getattr(logging, "LOG_LEVEL_SILENT", getattr(logging, "LOG_LEVEL_ERROR", 0))
            logging.setLogLevel(level)
            return
    except Exception:
        pass
    try:
        if hasattr(cv2, "setLogLevel"):
            cv2.setLogLevel(getattr(cv2, "LOG_LEVEL_SILENT", 0))
    except Exception:
        pass


def quiet_opencv() -> None:
    """Geriye uyum: OpenCV'yi yükleyip sessizleştirir."""
    load_cv2()


_ffmpeg_quieted = False


def quiet_ffmpeg() -> None:
    """Geriye uyum: OpenCV'yi yükler; FFmpeg sessizleştirmesi load_cv2 içinde yapılır."""
    load_cv2()


def _quiet_bundled_ffmpeg(cv2) -> None:
    """cv2 paketindeki FFmpeg kütüphanelerinin log seviyesini kapat (bir kez, ucuz).

    Asıl sessizlik OPENCV_FFMPEG_LOGLEVEL ortam değişkeniyle sağlanır; bu yalnızca
    cv2 klasöründeki (özyinelemesiz) avutil DLL'lerine ek olarak dokunur. (Güncel opencv
    tekerleklerinde FFmpeg tek bir opencv_videoio_ffmpeg DLL'inde olduğundan çoğu zaman bir şey
    bulmaz.) cv2 ilk kez yüklenince çağrılır; eskiden açılışta cv2'yi yalnızca bunun için yüklüyordu.
    """
    global _ffmpeg_quieted
    if _ffmpeg_quieted:
        return
    _ffmpeg_quieted = True
    try:
        import ctypes

        cv2_dir = Path(cv2.__file__).resolve().parent
        for pattern in ("avutil*.dll", "libavutil*.so*", "libavutil*.dylib*"):
            for dll_path in cv2_dir.glob(pattern):
                try:
                    lib = ctypes.CDLL(str(dll_path))
                    if hasattr(lib, "av_log_set_level"):
                        lib.av_log_set_level(-8)  # AV_LOG_QUIET
                except Exception:
                    pass
    except Exception:
        pass


def capture_rotation(cap) -> int:
    try:
        cv2 = load_cv2()
        meta = getattr(cv2, "CAP_PROP_ORIENTATION_META", None)
        if meta is None:
            return 0
        return int(cap.get(meta) or 0)
    except Exception:
        return 0


def orient_frame(frame, degrees: int):
    if not degrees:
        return frame
    cv2 = load_cv2()
    rot = ((int(degrees) % 360) + 360) % 360
    if rot == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if rot == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if rot == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    return frame


def open_capture(path: str):
    """VideoCapture aç (önce donanım hızlandırmalı). (cap, rotasyon) ya da (None, 0)."""
    cv2 = load_cv2()
    if cv2 is None:
        return None, 0
    cap = None
    hw_accel = getattr(cv2, "CAP_PROP_HW_ACCELERATION", None)
    hw_any = getattr(cv2, "VIDEO_ACCELERATION_ANY", None)
    cap_ffmpeg = getattr(cv2, "CAP_FFMPEG", None)
    if hw_accel is not None and hw_any is not None and cap_ffmpeg is not None:
        try:
            cap = cv2.VideoCapture(str(path), cap_ffmpeg, [hw_accel, hw_any])
        except Exception:
            cap = None
    if cap is None or not cap.isOpened():
        cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None, 0
    auto = getattr(cv2, "CAP_PROP_ORIENTATION_AUTO", None)
    if auto is not None:
        try:
            cap.set(auto, 0)
        except Exception:
            pass
    return cap, capture_rotation(cap)


def bgr_to_qimage(frame) -> QImage:
    cv2 = load_cv2()
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    rgb = rgb.copy()
    h, w = rgb.shape[:2]
    qimg = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888)
    return qimg.copy()


def downscale_bgr(frame, max_w: int, max_h: int):
    """En-boy oranını koruyarak en fazla max_w x max_h'a küçült (büyütme yok)."""
    cv2 = load_cv2()
    h, w = frame.shape[:2]
    if w <= 0 or h <= 0:
        return frame
    scale = min(max_w / w, max_h / h, 1.0)
    if scale >= 0.999:
        return frame
    nw = max(1, int(w * scale))
    nh = max(1, int(h * scale))
    return cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)


def _capture_duration_ms(cv2, cap) -> int:
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if fps >= 1.0 and nframes > 0:
            return max(0, int(nframes / fps * 1000))
        pos = cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0
        return max(0, int(pos))
    except Exception:
        return 0


def probe_duration_ms(path: str | Path) -> int:
    """Kare çözmeden süreyi oku (ucuz meta erişim, milisaniye). Daha önce okunduysa dosya açılmaz."""
    key = str(path)
    with _poster_lock:
        known = _duration_cache.get(key)
    if known is not None:
        return known
    cv2 = load_cv2()
    if cv2 is None:
        return 0
    cap, _rot = open_capture(key)
    if cap is None:
        return 0
    try:
        dur = _capture_duration_ms(cv2, cap)
    finally:
        try:
            cap.release()
        except Exception:
            pass
    _remember_duration(key, dur)
    return dur


# Poster önbelleği: tam çözünürlüklü QImage'lar büyük olduğundan küçük bir LRU. Bayt sınırı da var:
# 4K bir kare ~25 MB; yalnızca adet sınırı (16) varken 4K videolarla dolu bir klasörde şerit küçük
# resimleri ~400 MB tam boy kare tutabiliyordu.
_POSTER_CACHE_MAX = 16
_POSTER_CACHE_MAX_BYTES = 96 * 1024 * 1024
_DURATION_CACHE_MAX = 512
_poster_cache: "OrderedDict[str, QImage]" = OrderedDict()
_duration_cache: "OrderedDict[str, int]" = OrderedDict()
_poster_lock = threading.Lock()


def _remember_duration(key: str, dur: int) -> None:
    with _poster_lock:
        _duration_cache[key] = int(dur)
        _duration_cache.move_to_end(key)
        while len(_duration_cache) > _DURATION_CACHE_MAX:
            _duration_cache.popitem(last=False)


def _store_poster_locked(key: str, img: QImage) -> None:
    _poster_cache[key] = img
    _poster_cache.move_to_end(key)
    total = sum(item.sizeInBytes() for item in _poster_cache.values())
    while len(_poster_cache) > 1 and (len(_poster_cache) > _POSTER_CACHE_MAX or total > _POSTER_CACHE_MAX_BYTES):
        _old_key, old = _poster_cache.popitem(last=False)
        total -= old.sizeInBytes()


def poster_and_duration(path: str | Path) -> tuple[QImage | None, int]:
    """İlk kare + süre, videoyu TEK kez açarak. (Eskiden poster işçisi videoyu iki kez açıyordu:
    önce süre, sonra kare için — ağ yolunda/uzun videoda açılış maliyeti iki katıydı.)"""
    key = str(path)
    with _poster_lock:
        cached = _poster_cache.get(key)
        dur = _duration_cache.get(key)
        if cached is not None and not cached.isNull() and dur is not None:
            _poster_cache.move_to_end(key)
            return cached, dur
    cv2 = load_cv2()
    if cv2 is None:
        return None, 0
    cap, rot = open_capture(key)
    if cap is None:
        return None, 0
    try:
        dur = _capture_duration_ms(cv2, cap)
        ok, frame = cap.read()
    except Exception:
        ok, frame = False, None
    finally:
        cap.release()
    _remember_duration(key, dur)
    if not ok or frame is None:
        return None, dur
    img = bgr_to_qimage(orient_frame(frame, rot))
    with _poster_lock:
        _store_poster_locked(key, img)
    return img, dur


def video_poster(path: str | Path) -> QImage | None:
    key = str(path)
    with _poster_lock:
        cached = _poster_cache.get(key)
        if cached is not None and not cached.isNull():
            _poster_cache.move_to_end(key)
            return cached
    img, _dur = poster_and_duration(key)
    return img


def configure_qt_media_backend() -> None:
    """QApplication'dan önce: FFmpeg + GPU çözüm (D3D11/DXVA/CUDA)."""
    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
    os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")
    if sys.platform == "win32":
        os.environ.setdefault(
            "QT_FFMPEG_DECODING_HW_DEVICE_TYPES",
            "d3d11va,dxva2,cuda,qsv",
        )
