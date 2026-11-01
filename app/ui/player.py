"""Video oynatıcı — libmpv native (D3D11) pencere gömme; OpenCV yedek.

VLC gibi: mpv kendi native penceresine (HWND) doğrudan Direct3D11 ile hızlandırılmış
donanım çözümü + sunum yapar. Qt/OpenGL/ANGLE çeviri katmanı yok — GPU kopyasız,
CPU'ya iniş yok. Bu yaklaşım QOpenGLWidget + render-API'ye göre hem çok daha az
CPU/GPU kullanır hem de pencere gösterim zamanlamasına bağlı yarış durumlarından
(kare gelmeme) tamamen bağımsızdır.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QThread,
    QTimer,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap, QWheelEvent
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QWidget,
)

from app.core import startup_trace
from app.core.mpv_backend import get_mpv, mpv_available
from app.core.video_io import (
    bgr_to_qimage,
    open_capture,
    orient_frame,
    quiet_opencv,
    video_poster,  # noqa: F401
)
from app.ui.drops import FileDropMixin

os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
os.environ.setdefault("AV_LOG_FORCE_NOCOLOR", "1")
os.environ.setdefault("AV_LOG_LEVEL", "quiet")

# OpenCV burada YÜKLENMEZ: yalnızca yedek çözücü/poster/önizleme ilk kez gerektiğinde
# (video_io.load_cv2). Eskiden modül içe aktarılırken cv2 (+numpy) yükleniyor, her açılışa
# — fotoğraf açarken bile — ~60–160 ms ekliyordu.
_quiet_opencv = quiet_opencv
_open_capture = open_capture
_orient_frame = orient_frame
_bgr_to_qimage = bgr_to_qimage


# Hız basamakları ([ ] ve düğme ile gezilir).
SPEED_STEPS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)


def mpv_set(mpv_obj, name: str, value) -> bool:
    """Canlı mpv özelliği yaz.

    python-mpv `obj[name] = v` aslında `options/name` yazar; bu duraklatma,
    ses ve zoom gibi *o anki* durumu değiştirmez. Özellikler `_set_property`
    üzerinden gitmeli.
    """
    if mpv_obj is None:
        return False
    prop = str(name).replace("_", "-")
    try:
        mpv_obj._set_property(prop, value)
        return True
    except Exception:
        return False


def mpv_get(mpv_obj, name: str, default=None):
    if mpv_obj is None:
        return default
    prop = str(name).replace("_", "-")
    try:
        val = mpv_obj._get_property(prop)
        return default if val is None else val
    except Exception:
        return default


def mpv_options(wid: str | int | None, audio_device: str | None = None, platform: str | None = None) -> dict:
    """libmpv başlatma seçenekleri — VLC sınıfı düşük CPU profili.

    `vo=gpu` + Direct3D11 + `hwdec=auto`: GPU kopyasız sunum.
    `video-sync=audio` + `framedrop=vo`: video ses saatine bağlı, gerekirse kare atlanır.
    (Eski `desync` ses-görüntü kaymasına açıktı; `video-latency-hacks` mpv'nin kaldırılabilir
    diye işaretlediği bir ayardır — ikisi de ölçüm olmadan eklenmişti, çıkarıldı.)

    NOT: `ao` seçeneği yok. mpv Windows'ta WASAPI'yi otomatik seçer.
    """
    plat = platform if platform is not None else sys.platform
    opts: dict = {
        # Donanım çözümü: Windows D3D11 zero-copy; Linux VAAPI/NVDEC, macOS VideoToolbox (auto-safe seçer)
        "hwdec": "auto-safe",
        "audio-exclusive": "no",
        "mute": "no",
        "volume": 100,
        "keep-open": "yes",
        "sub-auto": "fuzzy",
        # Giriş — mpv kendi kısayollarını devralmasın
        "input-default-bindings": False,
        "input-vo-keyboard": False,
        "input-cursor": False,
        "osc": False,
        "ytdl": False,
        # ── Düşük CPU, ses-senkron ───────────────────────────────────────────
        "video-sync": "audio",
        "framedrop": "vo",
        # Yazılım decoder thread'ini sınırla (hwdec başarısız olursa yedek)
        "vd-lavc-threads": 4,
        # Pahalı upscaler devre dışı
        "scale": "bilinear",
        "dscale": "bilinear",
        "cscale": "bilinear",
        "interpolation": False,
        # Hız profili
        "profile": "fast",
        # Buffer
        "cache": "no",
        "demuxer-max-bytes": "8MiB",
        "demuxer-readahead-secs": 3,
        # Arka plan rengi (letterbox) burada DEĞİL: söz dizimi mpv sürümüne göre değişir
        # (<0.38: background=#rrggbb, >=0.38: background=color + background-color);
        # örnek oluşturulduktan sonra apply_mpv_background() ile uygulanır.
    }
    if plat == "win32":
        opts["d3d11va-zero-copy"] = "yes"
        # RTSS/RivaTuner hook önleme
        opts["d3d11-exclusive-fs"] = False
    if wid:
        opts["wid"] = str(wid)
        opts["vo"] = "gpu"
        if plat == "win32":
            opts["gpu-context"] = "d3d11"
            opts["gpu-api"] = "d3d11"
        # Linux (X11/XWayland) ve macOS: gpu-context/gpu-api otomatik seçilir (x11egl, cocoa...)
    else:
        opts["vo"] = "libmpv"
    return opts










def apply_mpv_background(m, color: str) -> bool:
    """Video dolgu/letterbox rengini mpv sürümüne uygun söz dizimiyle ayarla."""
    if mpv_set(m, "background", "color") and mpv_set(m, "background-color", color):
        return True
    return bool(mpv_set(m, "background", color))  # mpv < 0.38


# Yalnız bunlar olmadan oynatma anlamsızdır; diğer seçenekler sürüm farkında düşürülebilir.
_REQUIRED_MPV_OPTIONS = frozenset({"wid", "vo"})


def _failed_option_name(exc: BaseException) -> str | None:
    """python-mpv hatasından başarısız seçenek adını çıkar: (..., (handle, b'ad', b'değer'))."""
    for arg in reversed(getattr(exc, "args", ())):
        if isinstance(arg, tuple) and len(arg) >= 2 and isinstance(arg[1], (bytes, str)):
            name = arg[1].decode("utf-8", "replace") if isinstance(arg[1], bytes) else arg[1]
            return name.replace("_", "-")
    return None


def create_mpv(mpv_mod, opts: dict, **extra):
    """libmpv örneği oluştur; mpv sürümü tanımadığı (veya değerini reddettiği) zorunlu olmayan
    seçenekleri çıkarıp yeniden dene. Böylece eski (Ubuntu 0.37) ve yeni (0.41) libmpv aynı
    seçenek kümesiyle çalışır; uygulama bir seçenek yüzünden sessizce OpenCV'ye düşmez."""
    opts = dict(opts)
    dropped: list[str] = []
    for _ in range(len(opts) + 1):
        try:
            return mpv_mod.MPV(**opts, **extra)
        except Exception as exc:  # noqa: BLE001 — python-mpv hata türleri sürüme göre değişir
            name = _failed_option_name(exc)
            key = next((k for k in opts if k.replace("_", "-") == name), None)
            if key is None or key in _REQUIRED_MPV_OPTIONS:
                raise
            dropped.append(key)
            opts.pop(key)
    raise RuntimeError(f"mpv seçenekleri uygulanamadı: {dropped}")


def media_looks_hevc(path: str | Path) -> bool:
    path = Path(path)
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size <= 4 * 1024 * 1024:
                blob = fh.read()
            else:
                blob = fh.read(2 * 1024 * 1024)
                fh.seek(size - 2 * 1024 * 1024)
                blob += fh.read()
    except OSError:
        return False
    return b"hvc1" in blob or b"hev1" in blob or b"hvcC" in blob


class MpvSurface(FileDropMixin, QWidget):
    """libmpv native pencere yüzeyi — VLC gibi doğrudan D3D11 ile HWND'ye çizim.

    Qt'nin kendi çizim motoru bu widget'a hiç dokunmaz (`paintEngine()` None döner);
    mpv, widget'ın native pencere kimliğini (`wid`) alıp kendi Direct3D11 takas
    zincirini doğrudan o pencereye kurar. Zoom/pan/rotate/cover mpv özellikleri
    (video-zoom, video-pan-x/y, video-rotate, panscan) üzerinden uygulanır; bu
    özellikler render arka ucundan bağımsızdır, dolayısıyla davranış değişmez.
    """

    clicked = pyqtSignal()
    context_requested = pyqtSignal(QPoint)
    zoom_changed = pyqtSignal()
    double_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Kardeş widget'lar (kapsüller, alt bar) native HWND olmasın: native child
        # pencereler alfa karışımı yapamaz, yuvarlak köşeler siyah/beyaz görünür.
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings, True)
        # Native (gerçek HWND) pencere — mpv'nin sabit bir kimliğe ihtiyacı var.
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        # ClickFocus: Tıklandığında Qt aktif pencere durumunu korur ve arka plan penceresi öne fırlamaz
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._drop_handler = None
        self._mpv = None
        self._has_video = False
        self._fill = QColor(Qt.GlobalColor.transparent)
        self._cover = False
        self._cover_pref = False  # kullanıcının seçtiği Doldur kipi: video başlayınca (reset_view sonrası) geri uygulanır
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._rotation = 0
        self._press = None
        self._drag = None
        self._sub_label = QLabel(self)
        self._sub_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        self._sub_label.setWordWrap(True)
        self._sub_label.setStyleSheet(
            "QLabel { color: #fff; background: rgba(0,0,0,150); border-radius: 6px; "
            "padding: 4px 10px; font-size: 13px; font-weight: 600; }"
        )
        self._sub_label.hide()
        # mpv() oluşturulmadan önce kararlı bir native pencere gerekiyor (play() ilk çağrıldığında
        # tembel init edilir). Windows'ta burada zorlanmaz: widget henüz ebeveynsiz, yani üst düzey bir
        # `Qt::Window` olarak oluşurdu ve Qt'nin ~190 ms'lik GPU tanımlamasını tetiklerdi (bkz.
        # frameless.top_level_window_type). WA_NativeWindow sayesinde HWND, pencere oluşurken çocuk
        # pencere olarak kurulur; native_window_id() de gerekirse oluşturur.
        if sys.platform != "win32":
            self.winId()


    def paintEngine(self):  # noqa: N802
        # Qt'nin bu widget'a QPainter ile çizmesini tamamen engelle; içerik
        # mpv tarafından doğrudan native pencereye (D3D11) çiziliyor.
        return None

    def native_window_id(self) -> int:
        return int(self.winId())

    def push_to_bottom(self) -> None:
        """Win32 SetWindowPos ile bu native HWND'yi z-order'da en alta it.

        Qt'nin raise_()/lower_() fonksiyonları native HWND'lere etki etmez.
        Bu metot doğrudan Win32 çağrısı yaparak MpvSurface'ı en alta alır;
        böylece stage'in diğer Qt child widget'ları (nav_bar, brand_bar) görünür olur.
        """
        if sys.platform == "win32":
            try:
                import ctypes
                HWND_BOTTOM = 1  # noqa: N806
                SWP_NOMOVE = 0x0002  # noqa: N806
                SWP_NOSIZE = 0x0001  # noqa: N806
                SWP_NOACTIVATE = 0x0010  # noqa: N806
                hwnd = int(self.winId())
                if hwnd:
                    ctypes.windll.user32.SetWindowPos(
                        hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                    )
            except Exception:
                pass
        else:
            try:
                self.lower()
            except Exception:
                pass

    def set_mpv_instance(self, mpv_instance) -> None:
        self._mpv = mpv_instance

    def _set_video_prop(self, name: str, value) -> None:
        if not self._has_video or self._mpv is None:
            return
        mpv_set(self._mpv, name, value)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self:
            et = event.type()
            if et in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
                    return True
            if et == QEvent.Type.Drop:
                urls = event.mimeData().urls()
                if urls and self._drop_handler:
                    self._drop_handler(urls[0].toLocalFile())
                    event.acceptProposedAction()
                    return True
        return super().eventFilter(obj, event)

    def set_has_video(self, has_video: bool) -> None:
        self._has_video = bool(has_video)

    def has_content(self) -> bool:
        return self._has_video

    @property
    def _pixmap(self) -> QPixmap:
        return QPixmap()

    def set_subtitle(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            self._sub_label.hide()
            return
        self._sub_label.setText(text)
        self._sub_label.adjustSize()
        w = min(self.width() - 40, max(160, self._sub_label.sizeHint().width()))
        self._sub_label.setFixedWidth(w)
        self._sub_label.adjustSize()
        self._sub_label.move(
            max(8, (self.width() - self._sub_label.width()) // 2),
            max(8, self.height() - self._sub_label.height() - 18),
        )
        self._sub_label.show()
        self._sub_label.raise_()

    def rotate_step(self, degrees: int) -> None:
        self._rotation = (self._rotation + int(degrees)) % 360
        self._set_video_prop("video-rotate", self._rotation)
        self.zoom_changed.emit()

    def reset_view(self) -> None:
        self._cover = False
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._rotation = 0
        if self._has_video and self._mpv is not None:
            self._set_video_prop("panscan", 0.0)
            self._set_video_prop("video-zoom", 0.0)
            self._set_video_prop("video-pan-x", 0.0)
            self._set_video_prop("video-pan-y", 0.0)
            self._set_video_prop("video-rotate", 0)
            self._set_video_prop("video-unscaled", "no")
        self.zoom_changed.emit()

    def reset_view_to_actual(self) -> None:
        self._cover = False
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        if self._has_video and self._mpv is not None:
            self._set_video_prop("panscan", 0.0)
            self._set_video_prop("video-zoom", 0.0)
            self._set_video_prop("video-pan-x", 0.0)
            self._set_video_prop("video-pan-y", 0.0)
            self._set_video_prop("video-unscaled", "yes")
        self.zoom_changed.emit()

    def zoomed(self) -> bool:
        return self._zoom > 1.05 or self._pan.manhattanLength() > 0.01

    def remember_cover(self, cover: bool) -> None:
        """Seçilen Sığdır/Doldur kipini saklar; video gerçekten başlayınca `reset_view()` onu silmesin diye geri uygulanır."""
        self._cover_pref = bool(cover)

    def restore_cover_preference(self) -> None:
        if self._cover_pref:
            self.set_cover(True)

    def set_cover(self, cover: bool) -> None:
        self._cover = bool(cover)
        if self._cover:
            self._zoom = 1.0
            self._pan = QPointF(0.0, 0.0)
        if self._has_video and self._mpv is not None:
            self._set_video_prop("panscan", 1.0 if self._cover else 0.0)
            self._set_video_prop("video-zoom", 0.0)
            self._set_video_prop("video-pan-x", 0.0)
            self._set_video_prop("video-pan-y", 0.0)
            self._set_video_prop("video-unscaled", "no")
        self.zoom_changed.emit()

    def zoom_by(self, factor: float) -> None:
        self._cover = False
        import math

        next_zoom = min(8.0, max(1.0, self._zoom * float(factor)))
        self._zoom = next_zoom
        if self._zoom <= 1.05:
            self._zoom = 1.0
            self._pan = QPointF(0.0, 0.0)
        if self._has_video and self._mpv is not None:
            self._set_video_prop("panscan", 0.0)
            self._set_video_prop("video-unscaled", "no")
            self._set_video_prop("video-zoom", math.log2(self._zoom) if self._zoom > 1.0 else 0.0)
            if self._zoom <= 1.0:
                self._set_video_prop("video-pan-x", 0.0)
                self._set_video_prop("video-pan-y", 0.0)
        self.zoom_changed.emit()

    def set_fill(self, color: QColor) -> None:
        # Native pencere doğrudan mpv tarafından çizildiği için dolgu rengi
        # yalnızca API uyumluluğu için tutulur (fallback yüzeyde gerçek etkisi var).
        self._fill = QColor(color)

    def set_frame(self, _image: QImage) -> None:
        # Native yüzeyde kare Python'a hiç inmiyor; API uyumluluğu için no-op.
        pass

    def clear(self) -> None:
        self._has_video = False
        self._cover = False
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._rotation = 0
        if hasattr(self, "_sub_label"):
            self._sub_label.hide()
        self.zoom_changed.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        sub = getattr(self, "_sub_label", None)
        if sub is not None and sub.isVisible():
            self.set_subtitle(sub.text())

    def _forward_to_overlay(self, event) -> bool:
        """Tam ekranda veya video üzerindeki overlay widget'larına tıklamayı doğrudan ilet."""
        win = self.window()
        if win is None or win.isFullScreen():
            return False
        global_pos = event.globalPosition().toPoint()
        # dock_wrap, nav_bar, brand_bar, file_chip kontrolleri
        for w_name in ("dock_wrap", "nav_bar", "brand_bar", "file_chip"):
            w = getattr(win, w_name, None)
            if w is not None and w.isVisible():
                pos = w.mapFromGlobal(global_pos)
                if w.rect().contains(pos):
                    child = w.childAt(pos) or w
                    from PyQt6.QtGui import QMouseEvent
                    local_ev = QMouseEvent(
                        event.type(),
                        QPointF(child.mapFromGlobal(global_pos)),
                        event.globalPosition(),
                        event.button(),
                        event.buttons(),
                        event.modifiers(),
                    )
                    from PyQt6.QtWidgets import QApplication
                    QApplication.sendEvent(child, local_ev)
                    return True
        return False

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        win = self.window()
        if win is not None and hasattr(win, "_wake_ui"):
            win._wake_ui()
        if not self._has_video:
            return
        if self._forward_to_overlay(event):
            event.accept()
            return
        self.zoom_by(1.18 if event.angleDelta().y() > 0 else 1 / 1.18)
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        win = self.window()
        if win is not None and hasattr(win, "_wake_ui"):
            win._wake_ui()
        if self._forward_to_overlay(event):
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position()
            self._press_global = event.globalPosition().toPoint()
            self._drag = event.position() if self.zoomed() else None
            self._is_dragging_win = False
            # Native HWND tıklanınca Lumina penceresini aktif tut
            if win is not None and not win.isFullScreen():
                win.activateWindow()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        win = self.window()
        if win is not None and hasattr(win, "_wake_ui"):
            win._wake_ui()
        if self._drag is not None and self.zoomed() and self._mpv is not None:
            delta = event.position() - self._drag
            self._drag = event.position()
            w = max(1, self.width())
            h = max(1, self.height())
            dx = delta.x() / w
            dy = delta.y() / h
            self._pan += QPointF(dx, dy)
            self._set_video_prop("video-pan-x", self._pan.x())
            self._set_video_prop("video-pan-y", self._pan.y())
            event.accept()
            return
        elif not self.zoomed() and event.buttons() & Qt.MouseButton.LeftButton:
            if getattr(self, "_press_global", None) is not None:
                delta = event.globalPosition().toPoint() - self._press_global
                if delta.manhattanLength() >= 6:
                    if win is not None and hasattr(win, "_start_drag"):
                        origin = self._press_global
                        self._press_global = None
                        self._is_dragging_win = True
                        win._start_drag(origin, event.globalPosition().toPoint())
                        event.accept()
                        return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._forward_to_overlay(event):
            self._press = None
            self._drag = None
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if getattr(self, "_is_dragging_win", False):
            self._is_dragging_win = False
            self._press_global = None
            self._press = None
            win = self.window()
            if win is not None and hasattr(win, "_end_manual_move"):
                win._end_manual_move()
            return
        dragged = self._drag is not None
        self._drag = None
        self._press_global = None
        if dragged:
            self._press = None
            return
        if self._press is None:
            return
        is_short_click = (event.position() - self._press).manhattanLength() < 8
        self._press = None
        if is_short_click:
            self.clicked.emit()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._forward_to_overlay(event):
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.context_requested.emit(event.globalPos())


class VideoSurfaceFallback(FileDropMixin, QWidget):
    """OpenCV yedeği — libmpv olmadığında QPainter ile çizim."""

    clicked = pyqtSignal()
    context_requested = pyqtSignal(QPoint)
    zoom_changed = pyqtSignal()
    double_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent; border: none;")
        self.setAcceptDrops(True)
        self._drop_handler = None
        self._pixmap = QPixmap()
        self._press = None
        self._padding = 8
        self._fill = QColor(Qt.GlobalColor.transparent)
        self._cover = False
        self._cover_pref = False
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self._drag = None
        self._rotation = 0
        self._sub_label = QLabel(self)
        self._sub_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        self._sub_label.setWordWrap(True)
        self._sub_label.setStyleSheet(
            "QLabel { color: #fff; background: rgba(0,0,0,150); border-radius: 6px; "
            "padding: 4px 10px; font-size: 13px; font-weight: 600; }"
        )
        self._sub_label.hide()

    def has_content(self) -> bool:
        return not self._pixmap.isNull()

    def set_subtitle(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            self._sub_label.hide()
            return
        self._sub_label.setText(text)
        self._sub_label.adjustSize()
        w = min(self.width() - 40, max(160, self._sub_label.sizeHint().width()))
        self._sub_label.setFixedWidth(w)
        self._sub_label.adjustSize()
        self._sub_label.move(
            max(8, (self.width() - self._sub_label.width()) // 2),
            max(8, self.height() - self._sub_label.height() - 18),
        )
        self._sub_label.show()
        self._sub_label.raise_()

    def rotate_step(self, degrees: int) -> None:
        self._rotation = (self._rotation + int(degrees)) % 360
        self.update()
        self.zoom_changed.emit()

    def reset_view(self) -> None:
        self._cover = False
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self._rotation = 0
        self.update()
        self.zoom_changed.emit()

    def reset_view_to_actual(self) -> None:
        self._cover = False
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self.update()
        self.zoom_changed.emit()

    def zoomed(self) -> bool:
        return self._zoom > 1.02

    def remember_cover(self, cover: bool) -> None:
        self._cover_pref = bool(cover)

    def restore_cover_preference(self) -> None:
        if self._cover_pref:
            self.set_cover(True)

    def set_cover(self, cover: bool) -> None:
        self._cover = bool(cover)
        if self._cover:
            self._zoom = 1.0
            self._pan = QPointF(0, 0)
        self.update()
        self.zoom_changed.emit()

    def zoom_by(self, factor: float) -> None:
        self._cover = False
        next_zoom = min(8.0, max(1.0, self._zoom * float(factor)))
        self._zoom = next_zoom
        if self._zoom <= 1.02:
            self._zoom = 1.0
            self._pan = QPointF(0, 0)
        self.update()
        self.zoom_changed.emit()

    def set_fill(self, color: QColor) -> None:
        self._fill = QColor(color)
        self.update()

    def set_frame(self, image: QImage) -> None:
        self._pixmap = QPixmap.fromImage(image)
        self.update()

    def clear(self) -> None:
        self._pixmap = QPixmap()
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._sub_label.isVisible():
            self.set_subtitle(self._sub_label.text())

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        if self._fill.isValid() and self._fill.alpha() > 0:
            painter.fillRect(self.rect(), self._fill)
        if self._pixmap.isNull():
            return
        pix = self._pixmap
        pw = pix.width()
        ph = pix.height()
        if pw <= 0 or ph <= 0:
            return

        target = self.rect().adjusted(
            self._padding, self._padding, -self._padding, -self._padding
        )
        tw = target.width()
        th = target.height()
        if tw <= 0 or th <= 0:
            return

        eff_pw = ph if self._rotation in (90, 270) else pw
        eff_ph = pw if self._rotation in (90, 270) else ph

        if self._cover:
            scale = max(tw / eff_pw, th / eff_ph)
        else:
            scale = min(tw / eff_pw, th / eff_ph)
            if self._zoom <= 1.02 and scale > 1.0:
                scale = 1.0

        if self._zoom > 1.02:
            scale *= self._zoom

        dw = int(pw * scale)
        dh = int(ph * scale)
        dx = target.x() + (tw - int(eff_pw * scale)) // 2 + int(self._pan.x())
        dy = target.y() + (th - int(eff_ph * scale)) // 2 + int(self._pan.y())

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setClipRect(target)

        if self._rotation != 0:
            center = QPointF(
                target.x() + tw / 2.0 + self._pan.x(),
                target.y() + th / 2.0 + self._pan.y(),
            )
            painter.translate(center)
            painter.rotate(self._rotation)
            draw_rect = QRect(-dw // 2, -dh // 2, dw, dh)
            painter.drawPixmap(draw_rect, pix)
        else:
            draw_rect = QRect(dx, dy, dw, dh)
            painter.drawPixmap(draw_rect, pix)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self._pixmap.isNull():
            return
        self.zoom_by(1.18 if event.angleDelta().y() > 0 else 1 / 1.18)
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position()
            self._press_global = event.globalPosition().toPoint()
            self._drag = event.position() if self.zoomed() else None
            self._is_dragging_win = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None and self.zoomed():
            delta = event.position() - self._drag
            self._pan += delta
            self._drag = event.position()
            self.update()
            event.accept()
            return
        elif not self.zoomed() and event.buttons() & Qt.MouseButton.LeftButton:
            if getattr(self, "_press_global", None) is not None:
                delta = event.globalPosition().toPoint() - self._press_global
                if delta.manhattanLength() >= 6:
                    win = self.window()
                    if win is not None and hasattr(win, "_start_drag"):
                        origin = self._press_global
                        self._press_global = None
                        self._is_dragging_win = True
                        win._start_drag(origin, event.globalPosition().toPoint())
                        event.accept()
                        return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if getattr(self, "_is_dragging_win", False):
            self._is_dragging_win = False
            self._press_global = None
            self._press = None
            win = self.window()
            if win is not None and hasattr(win, "_end_manual_move"):
                win._end_manual_move()
            return
        dragged = self._drag is not None
        self._drag = None
        self._press_global = None
        if dragged:
            self._press = None
            return
        if self._press is not None and (event.position() - self._press).manhattanLength() < 8:
            self.clicked.emit()
        self._press = None

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.context_requested.emit(event.globalPos())


# VideoSurface: mpv varsa MpvSurface, yoksa VideoSurfaceFallback
def VideoSurface(parent: QWidget | None = None):  # noqa: N802
    if mpv_available():
        startup_trace.mark("mpv_loaded")
        return MpvSurface(parent)
    return VideoSurfaceFallback(parent)


class DecodeThread(QThread):
    """OpenCV yedek çözücü — yalnız libmpv yoksa çalışır."""

    ready = pyqtSignal(int)
    frame = pyqtSignal(int, QImage)
    failed = pyqtSignal(int, str)
    ended = pyqtSignal(int)
    progress = pyqtSignal(int, int, int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DecodeThread")
        self._path = ""
        self._loop = False
        self._gen = 0
        self._seek_ms = -1
        self._step = 0
        self._speed = 1.0
        self._paused = False
        self._pos_ms = 0
        self._lock = threading.Lock()

    def configure(self, path: str, loop: bool, gen: int) -> None:
        self._path = path
        self._loop = loop
        self._gen = gen
        with self._lock:
            self._seek_ms = -1
            self._step = 0
            self._paused = False

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = bool(paused)

    def set_loop(self, loop: bool) -> None:
        self._loop = bool(loop)

    def set_speed(self, speed: float) -> None:
        with self._lock:
            self._speed = max(0.05, float(speed))

    def request_seek(self, ms: int) -> None:
        with self._lock:
            self._seek_ms = max(0, int(ms))
            self._step = 0

    def request_step(self, direction: int) -> None:
        """Bir kare ileri (+1) / geri (-1). Art arda istekler birikir."""
        with self._lock:
            self._step += 1 if direction > 0 else -1

    def run(self) -> None:
        path = self._path
        gen = self._gen
        _quiet_opencv()
        cap, rot = _open_capture(path)
        if cap is None:
            self.failed.emit(gen, "Video açılamadı.")
            return
        try:
            import cv2

            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            if fps < 1 or fps > 240:
                fps = 30.0
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            dur = int(frames / fps * 1000) if frames > 1 else 0
            first = True
            index = 0
            wall0 = time.perf_counter()
            last_speed = 1.0
            while not self.isInterruptionRequested():
                with self._lock:
                    seek_ms = self._seek_ms
                    self._seek_ms = -1
                    step = self._step
                    self._step = 0
                    paused = self._paused
                    speed = self._speed
                if speed != last_speed:
                    # Hız değişti: duvar saatini mevcut konuma göre yeniden eşitle.
                    last_speed = speed
                    wall0 = time.perf_counter() - (self._pos_ms / 1000.0) / speed
                if step != 0:
                    try:
                        shown = int(cap.get(cv2.CAP_PROP_POS_FRAMES) or 0) - 1  # son gösterilen karenin indisi
                        target = max(0, shown + step)
                        if step != 1:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, float(target))
                        ok, frame = cap.read()
                    except Exception:
                        ok, frame = False, None
                    if ok and frame is not None:
                        image = _bgr_to_qimage(_orient_frame(frame, rot))
                        self.frame.emit(gen, image)
                        pos = int(cap.get(cv2.CAP_PROP_POS_MSEC) or 0)
                        self._pos_ms = pos
                        if dur <= 0:
                            dur = max(pos, 1)
                        self.progress.emit(gen, pos, dur)
                    wall0 = time.perf_counter() - (self._pos_ms / 1000.0) / speed
                    continue
                if seek_ms >= 0:
                    try:
                        cap.set(cv2.CAP_PROP_POS_MSEC, float(seek_ms))
                    except Exception:
                        pass
                    wall0 = time.perf_counter() - (seek_ms / 1000.0) / speed
                    index = 0
                    ok, frame = cap.read()
                    if ok and frame is not None:
                        image = _bgr_to_qimage(_orient_frame(frame, rot))
                        self.frame.emit(gen, image)
                        pos = int(cap.get(cv2.CAP_PROP_POS_MSEC) or seek_ms)
                        self._pos_ms = pos
                        if dur <= 0:
                            dur = max(pos, 1)
                        self.progress.emit(gen, pos, dur)
                    if paused:
                        self.msleep(40)
                        continue
                    media_t = (self._pos_ms / 1000.0) if self._pos_ms > 0 else 0.0
                    remain = (wall0 + media_t / speed) - time.perf_counter()
                    if remain > 0.002:
                        self.msleep(int(remain * 1000))
                    continue
                if paused:
                    self.msleep(30)
                    wall0 = time.perf_counter() - (self._pos_ms / 1000.0 if self._pos_ms > 0 else 0.0) / speed
                    continue
                ok, frame = cap.read()
                if not ok or frame is None:
                    if self._loop:
                        try:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        except Exception:
                            break
                        wall0 = time.perf_counter()
                        index = 0
                        continue
                    self.ended.emit(gen)
                    break
                index += 1
                image = _bgr_to_qimage(_orient_frame(frame, rot))
                self.frame.emit(gen, image)
                pos = int(cap.get(cv2.CAP_PROP_POS_MSEC) or 0)
                self._pos_ms = pos
                if dur <= 0:
                    dur = max(pos, 1)
                self.progress.emit(gen, pos, dur)
                if first:
                    first = False
                    self.ready.emit(gen)
                    media0 = pos / 1000.0 if pos > 0 else 0.0
                    wall0 = time.perf_counter() - media0 / speed
                media_t = pos / 1000.0 if pos > 0 else (index - 1) / fps
                remain = (wall0 + media_t / speed) - time.perf_counter()
                if remain > 0.002:
                    self.msleep(int(remain * 1000))
                elif remain < -0.150:
                    # Gecikme durumunda döngüye girmeden duvar saatini doğrudan eşitle
                    wall0 = time.perf_counter() - media_t / speed
        finally:
            try:
                cap.release()
            except Exception:
                pass


class LivePlayer(QObject):
    """Ana video oynatıcı: libmpv birincil, OpenCV yedek."""

    finished = pyqtSignal()
    started = pyqtSignal()
    stopped = pyqtSignal()
    paused = pyqtSignal()
    failed = pyqtSignal(str)
    loading = pyqtSignal(bool)
    progress = pyqtSignal(int, int)  # pos_ms, dur_ms
    muted_changed = pyqtSignal(bool)
    volume_changed = pyqtSignal(int)
    subtitle_changed = pyqtSignal(str)
    audio_available = pyqtSignal(bool)
    speed_changed = pyqtSignal(float)
    loop_changed = pyqtSignal(bool)
    tracks_changed = pyqtSignal()  # ses/altyazı parça listesi veya seçimi değişti

    # İş parçacığı güvenli köprü sinyalleri
    # NOT: time-pos intentionally NOT observed — her karede Python çağrısı CPU harcar.
    # Konum bilgisi QTimer (~3Hz) ile mpv.time_pos property polling'i ile alınır.
    # İlk int = mpv "playlist_entry_id" (oturum kimliği): olayın hangi dosya yüklemesine ait
    # olduğunu söyler. Eski dosyanın geç gelen olayı yeni oturumu etkilemez (_accept_entry).
    _sig_duration = pyqtSignal(int, float)
    _sig_sub_text = pyqtSignal(str)
    _sig_eof = pyqtSignal(int)
    _sig_started = pyqtSignal(int)      # playback-restart: D3D11 hazır, ilk kare sunulabilir
    _sig_file_loaded = pyqtSignal(int)  # file-loaded: dosya parse tamam ama surface henüz hazır değil
    _sig_error = pyqtSignal(int, int)   # end-file reason=error: (entry_id, mpv hata kodu)
    _sig_tracks = pyqtSignal(int)       # track-list değişti (örn. altyazı dosyası eklendi)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.widget = VideoSurface()
        self.loop = False
        self._speed = 1.0
        self._playing = False
        self._opening = False
        self._is_paused = False
        # Video sonuna kadar oynadı, son karesi ekranda (mpv keep-open): sarılabilir, oynamıyor.
        self._ended = False
        self._gen = 0
        self._pos_ms = 0
        self._dur_ms = 0
        self._volume_percent = 100
        self._muted = False
        self._path = ""
        self._mpv = None
        self._bg_color = "#FF07080C"
        self._thread: DecodeThread | None = None
        self._last_progress_emit = 0.0
        self._has_audio_track = True
        self._audio_device = "auto"  # etkin aygıt (motor doğruladıktan sonra)
        self._desired_audio_device = "auto"  # kayıtlı tercih; motor hazır olmadan silinmez
        self._entry_id: int | None = None  # şu anki yüklemenin mpv playlist_entry_id'si (GUI thread)
        self._ev_entry_id = 0  # mpv olay thread'inin gördüğü son start-file kimliği (yalnız o thread)
        self._observers: list[tuple[str, object]] = []
        self._event_handlers: list[object] = []
        self._vo_override: dict = {}  # VO geri dönüşü sonrası kalıcı çıkış seçeneği (ör. {"vo": "x11"})
        self._vo_fallback_index = 0
        self._played_once = False  # bu süreçte en az bir oynatma başarıyla başladı mı (VO çalışıyor mu)
        # 333ms = saniyede 3 güncelleme; seek çubuğu için fazlasıyla yeterli
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(333)
        self._progress_timer.timeout.connect(self._on_progress_tick)

        # Sinyal bağlantıları (mpv iş parçacığından GUI'ye güvenli aktarım)
        self._sig_duration.connect(self._on_mpv_duration)
        self._sig_sub_text.connect(self._on_mpv_sub_text)
        self._sig_eof.connect(self._on_mpv_eof)
        self._sig_started.connect(self._on_mpv_started)
        self._sig_file_loaded.connect(self._on_mpv_file_loaded)
        self._sig_error.connect(self._on_mpv_error)
        self._sig_tracks.connect(self._on_mpv_tracks)

        # mpv örneği ilk oynatma anında (play çağrılınca) tembel başlatılır

    def set_background_color(self, color_hex: str) -> None:
        """Video letterbox / dolgu rengini temaya göre ayarla (siyah şeritleri yok eder)."""
        self._bg_color = color_hex
        if self._mpv is not None:
            apply_mpv_background(self._mpv, color_hex)
        if hasattr(self.widget, "set_fill"):
            from PyQt6.QtGui import QColor
            self.widget.set_fill(QColor(color_hex))

    def _new_mpv(self, mpv_mod):
        """libmpv örneğini oluştur; kullanılamıyorsa None (çağıran OpenCV yedeğine düşer).

        Test/otomasyon ortamları bu metodu geçersiz kılıp pencere gerektirmeyen bir örnek
        (`vo=null`) döndürebilir.
        """
        if not isinstance(self.widget, MpvSurface):
            return None
        wid = self.widget.native_window_id()
        # "offscreen" gibi sahte QPA platformlarında gerçek bir Win32 HWND yok;
        # mpv'yi böyle geçersiz bir pencereye D3D11 ile bağlamaya çalışmak çöker.
        try:
            from PyQt6.QtGui import QGuiApplication

            platform_ok = QGuiApplication.platformName() not in {"offscreen", "minimal"}
        except Exception:
            platform_ok = True
        if not wid or not platform_ok:
            return None
        opts = mpv_options(wid, audio_device=self._audio_device)
        opts.update(self._vo_override)
        return create_mpv(mpv_mod, opts)

    def _init_mpv(self) -> None:
        mpv_mod = get_mpv()
        if mpv_mod is None:
            return
        try:
            m = self._new_mpv(mpv_mod)
        except Exception:
            m = None
        if m is None:
            self._spawn_fallback_thread()
            return
        try:
            apply_mpv_background(m, self._bg_color)
            self._wire_mpv_events(m)
            self._mpv = m
            self._apply_audio_settings()
            if isinstance(self.widget, MpvSurface):
                self.widget.set_mpv_instance(m)
        except Exception:
            self._mpv = None
            self._spawn_fallback_thread()

    def _wire_mpv_events(self, m) -> None:
        """mpv olay thread'inden GUI'ye oturum kimlikli köprü.

        Her olay, o ana dek görülen son `start-file` kimliğiyle damgalanır; olaylar mpv
        olay thread'inde sıralı işlendiğinden damga doğru dosya yüklemesini gösterir. GUI
        tarafı `_accept_entry` ile eski dosyanın geç gelen olaylarını eler.
        """
        # time-pos observe ETMİYORUZ — her karede Python→Qt signal üretir (~60Hz CPU yük).
        # Konum 3 Hz QTimer polling'i ile okunur.
        def on_duration(_name, val):
            if val is not None:
                self._sig_duration.emit(self._ev_entry_id, float(val))

        def on_sub_text(_name, val):
            self._sig_sub_text.emit(str(val or ""))

        def on_eof(_name, val):
            if val:
                self._sig_eof.emit(self._ev_entry_id)

        def on_tracks(_name, _val):
            self._sig_tracks.emit(self._ev_entry_id)

        for name, handler in (
            ("duration", on_duration),
            ("sub-text", on_sub_text),
            ("eof-reached", on_eof),
            ("track-list", on_tracks),
        ):
            m.observe_property(name, handler)
            self._observers.append((name, handler))

        # ── Event Ayrımı ────────────────────────────────────────────────
        # file-loaded: dosya parse edildi, ama D3D11 swap chain henüz ilk
        #   kareyi sunmaya hazır değil.
        # playback-restart: D3D11 surface ve decoder tamamen hazır, ilk kare
        #   sunulabilir. Asıl başlatma sinyalini buradan gönder.
        # Bu ayrım, "ses geliyor ama görüntü donuyor" hatasını önler.
        def on_start_file(event):
            try:
                self._ev_entry_id = int(event.data.playlist_entry_id)
            except Exception:
                self._ev_entry_id = 0

        def on_file_loaded(_event=None):
            self._sig_file_loaded.emit(self._ev_entry_id)

        def on_playback_restart(_event=None):
            self._sig_started.emit(self._ev_entry_id)

        def on_end_file(event):
            # reason=4 (ERROR): dosya açılamadı/çözülemedi. Eskiden yalnızca 8 sn'lik
            # zaman aşımıyla anlaşılıyordu; mpv bunu birkaç ms'de bildirir.
            try:
                data = event.data
                if int(data.reason) == 4:
                    self._sig_error.emit(int(data.playlist_entry_id), int(data.error))
            except Exception:
                pass

        for name, handler in (
            ("start-file", on_start_file),
            ("file-loaded", on_file_loaded),
            ("playback-restart", on_playback_restart),
            ("end-file", on_end_file),
        ):
            self._event_handlers.append(m.event_callback(name)(handler))

    def _accept_entry(self, entry_id: int) -> bool:
        """Olay şu anki dosya yüklemesine mi ait? (Kimlik bilinmiyorsa eski davranış: kabul.)"""
        return self._entry_id is None or int(entry_id) == self._entry_id

    def _current_entry_id(self) -> int | None:
        """mpv'nin şu anki playlist girdisinin kimliği (loadfile'dan hemen sonra)."""
        if self._mpv is None:
            return None
        try:
            playlist = self._mpv.playlist or []
            for entry in reversed(playlist):
                if isinstance(entry, dict) and entry.get("id") is not None and (
                    entry.get("current") or entry.get("playing") or len(playlist) == 1
                ):
                    return int(entry["id"])
        except Exception:
            pass
        return None

    def _spawn_fallback_thread(self) -> DecodeThread:
        thread = DecodeThread(self)
        thread.ready.connect(self._on_fb_ready)
        thread.frame.connect(self._on_fb_frame)
        thread.failed.connect(self._on_fb_failed)
        thread.ended.connect(self._on_fb_ended)
        thread.progress.connect(self._on_fb_progress)
        self._thread = thread
        return thread

    # ------------------ MPV Observer Handler'ları (GUI thread) ------------------

    def _on_progress_tick(self) -> None:
        """QTimer callback (~3 Hz) — mpv konum polling'i.

        time-pos observe yerine bu yaklaşım tercih edildi:
        observe_property her karede (60fps = 60×/s) Python signal üretir ve
        Qt event loop'una yük bindirir. Timer ile 3×/s polling CPU yükünü
        dramatik şekilde düşürür; kullanıcı farkı görmez.

        NOT: _opening kontrolü KALDIRILDI. Başlangıç tespiti yalnızca mpv'nin
        'file-loaded' / 'playback-restart' olaylarından gelir (_sig_started).
        Timer ile time-pos polling yaparak başlatmak, D3D11 yüzeyine ilk kare
        gelmeden sinyali ateşliyordu → görüntü takılıyor, duraklatınca geliyor.
        """
        if self._mpv is None:
            return
        if not self._playing:
            return
        # Konum güncelle — _pos_ms cache'i güncelle, sonra emit
        val = mpv_get(self._mpv, "time-pos")
        if val is not None:
            self._pos_ms = max(0, int(float(val) * 1000))
        dur = self._dur_ms
        self.progress.emit(self._pos_ms, dur)


    def _on_mpv_duration(self, entry_id: int, sec: float) -> None:
        if not self._accept_entry(entry_id):
            return
        ms = max(0, int(sec * 1000))
        self._dur_ms = ms
        self.progress.emit(self._pos_ms, ms)

    def _on_mpv_tracks(self, entry_id: int) -> None:
        if self._accept_entry(entry_id):
            self.tracks_changed.emit()

    def _on_mpv_sub_text(self, text: str) -> None:
        self.widget.set_subtitle(text)
        self.subtitle_changed.emit(text)

    def _stop_fallback_thread(self) -> None:
        thread = self._thread
        self._thread = None
        if thread is None:
            return
        try:
            for sig, slot in (
                (thread.ready, self._on_fb_ready),
                (thread.frame, self._on_fb_frame),
                (thread.failed, self._on_fb_failed),
                (thread.ended, self._on_fb_ended),
                (thread.progress, self._on_fb_progress),
            ):
                try:
                    sig.disconnect(slot)
                except TypeError:
                    pass
            if thread.isRunning():
                thread.requestInterruption()
                thread.wait(800)
            thread.deleteLater()
        except Exception:
            pass

    def _refresh_audio_track_state(self) -> None:
        """Ses kanalı yoksa ses düğmesi gizlensin diye arayüze bildir."""
        has_audio = True
        if self._mpv is not None:
            try:
                tracks = self._mpv.track_list or []
                has_audio = any(
                    isinstance(t, dict) and t.get("type") == "audio" for t in tracks
                )
            except Exception:
                has_audio = True
        if has_audio != self._has_audio_track:
            self._has_audio_track = has_audio
        self.audio_available.emit(has_audio)

    def _on_mpv_file_loaded(self, entry_id: int = 0) -> None:
        """file-loaded: dosya parse edildi.

        D3D11 swap chain henüz ilk kareyi sunmaya hazır değil — burada
        yalnızca mpv'nin keep-open=yes nedeniyle pause=True'da takılmasını önle.
        Asıl başlatma mantığı playback-restart'ta (_on_mpv_started) yapılır.
        """
        if self._mpv is None or not self._opening or not self._accept_entry(entry_id):
            return
        # keep-open=yes bazen dosya yüklenince mpv'yi pause=True bırakır.
        # Bunu burada düzeltiyoruz; D3D11 surface hazır olmadığından kare gelmiyor
        # ama en azından decoder çalışmaya başlıyor.
        try:
            if mpv_get(self._mpv, "pause"):
                mpv_set(self._mpv, "pause", False)
        except Exception:
            pass

    def _on_mpv_started(self, entry_id: int = 0) -> None:
        """playback-restart: D3D11 surface hazır, ilk kare sunulabilir.

        playback-restart olayı hem dosya yüklenmesinde hem de seek sonrasında
        tetiklenir. _opening=False ise bu bir seek restart'ı — sadece timeri başlat.
        Eski dosyanın geç gelen olayı (kimlik uyuşmuyor) yeni oturumu başlatmaz.
        """
        if not self._accept_entry(entry_id):
            return
        if not self._opening:
            if self._playing:
                if not self._progress_timer.isActive():
                    self._progress_timer.start()
            return
        self._played_once = True
        self._opening = False
        self._playing = True
        self._is_paused = False
        self._stop_fallback_thread()
        if isinstance(self.widget, MpvSurface):
            self.widget.set_has_video(True)
            self.widget.reset_view()
            # reset_view() Doldur'u siler; kullanıcı Doldur seçtiyse yeni video da Doldur başlar
            self.widget.restore_cover_preference()
        self._apply_audio_settings()
        # playback-restart anında D3D11 surface hazır — pause=False kesin yap
        mpv_set(self._mpv, "pause", False)
        self._progress_timer.setInterval(333)
        self._progress_timer.start()
        self.loading.emit(False)
        self._refresh_audio_track_state()
        self.tracks_changed.emit()
        self.started.emit()




    _MPV_ERROR_TEXT = {
        -13: "Dosya açılamadı (bulunamadı veya erişilemiyor).",
        -14: "Ses çıkışı başlatılamadı.",
        -15: "Video çıkışı başlatılamadı.",
        -16: "Oynatılacak bir şey bulunamadı.",
        -17: "Dosya biçimi tanınmadı (desteklenmiyor veya bozuk).",
        -18: "Bu dosya desteklenmiyor.",
    }

    # Video çıkışı (VO) başlatılamazsa (GL/EGL yok: sanal makine, uzak masaüstü) sırayla denenecek
    # yazılımsal çıkışlar. Windows'ta D3D11 zaten her yerde bulunur; geri dönüş tanımlı değil.
    _VO_FALLBACKS: dict = {"linux": ({"vo": "x11"},)}

    def _vo_fallback_chain(self) -> tuple:
        key = "linux" if sys.platform.startswith("linux") else sys.platform
        return self._VO_FALLBACKS.get(key, ())

    def _try_vo_fallback(self) -> bool:
        """VO başlatma hatasında bir sonraki çıkışla mpv'yi yeniden kurup aynı dosyayı yeniden oynat."""
        chain = self._vo_fallback_chain()
        if self._vo_fallback_index >= len(chain) or not self._path:
            return False
        self._vo_override = dict(chain[self._vo_fallback_index])
        self._vo_fallback_index += 1
        path, loop = self._path, self.loop
        self._progress_timer.stop()
        self._discard_mpv()
        self._opening = False
        self._playing = False
        self.play(path, loop)  # _init_mpv geçersiz kılınan çıkışla yeni örnek kurar
        return True

    def _on_mpv_error(self, entry_id: int, code: int) -> None:
        """end-file(reason=error): hatayı 8 sn'lik zaman aşımını beklemeden bildir."""
        if not self._accept_entry(entry_id) or not (self._opening or self._playing):
            return
        # -15: VO başlatılamadı. mpv 0.37 bunu çoğu zaman genel hata (-20) olarak bildirir; henüz hiç
        # oynatma başarılı olmadıysa (GL bağlamı hiç kurulamadı) -20 de VO sorunu sayılır.
        if (int(code) == -15 or (int(code) == -20 and not self._played_once)) and self._try_vo_fallback():
            return
        self._progress_timer.stop()
        self._opening = False
        self._playing = False
        self._is_paused = False
        self._ended = False
        self.loading.emit(False)
        self.failed.emit(self._MPV_ERROR_TEXT.get(int(code), f"Video açılamadı (mpv hata kodu {int(code)})."))

    def _on_mpv_eof(self, entry_id: int = 0) -> None:
        if not self._accept_entry(entry_id):
            return
        if not self._playing and not self._opening:
            return
        if self.loop:
            if self._mpv is not None:
                try:
                    self._mpv.seek(0, "absolute+exact")
                except Exception:
                    pass
            return
        self._playing = False
        self._opening = False
        self._is_paused = False
        # mpv son kareyi tutar (keep-open): ok tuşlarıyla geri sarılabilsin.
        self._ended = self._mpv is not None
        self._progress_timer.stop()
        self.loading.emit(False)
        self.finished.emit()

    # ------------------ OpenCV Fallback Handler'ları ------------------

    def _on_fb_ready(self, gen: int) -> None:
        if gen != self._gen or not self._opening:
            return
        self._opening = False
        self._playing = True
        self.loading.emit(False)
        self.started.emit()

    def _on_fb_frame(self, gen: int, image: QImage) -> None:
        if gen != self._gen:
            return
        if not self._playing and not self._opening and not self._is_paused:
            return
        self.widget.set_frame(QImage(image))

    def _on_fb_progress(self, gen: int, pos: int, dur: int) -> None:
        if gen != self._gen:
            return
        self._pos_ms = pos
        self._dur_ms = max(self._dur_ms, dur)
        self.progress.emit(pos, max(self._dur_ms, dur))

    def _on_fb_failed(self, gen: int, message: str) -> None:
        if gen != self._gen:
            return
        self._opening = False
        self._playing = False
        self.loading.emit(False)
        self.failed.emit(message)

    def _on_fb_ended(self, gen: int) -> None:
        if gen != self._gen or self._is_paused:
            return
        self._playing = False
        self._opening = False
        self.loading.emit(False)
        self.widget.clear()
        self.finished.emit()

    # ------------------ Genel Oynatıcı Kontrolleri ------------------

    def position(self) -> int:
        if self._mpv is not None:
            try:
                val = self._mpv.time_pos
                if val is not None:
                    return int(val * 1000)
            except Exception:
                pass
        return self._pos_ms

    def duration(self) -> int:
        if self._mpv is not None:
            try:
                val = self._mpv.duration
                if val is not None:
                    return int(val * 1000)
            except Exception:
                pass
        return self._dur_ms

    def skip(self, delta_ms: int) -> None:
        dur = self.duration()
        pos = self.position()
        if dur <= 0 and pos <= 0:
            return
        target = max(0, pos + int(delta_ms))
        if dur > 0:
            target = min(target, max(0, dur - 80))
        self.seek(target)

    @property
    def ended(self) -> bool:
        """Video sonuna kadar oynadı ve son karesi ekranda (yalnızca mpv; döngüde hiç olmaz)."""
        return self._ended

    def seek_from_end(self, delta_ms: int) -> bool:
        """Bitmiş videoda ileri/geri sar. Oynatıcı oradan "duraklatılmış" olur: Boşluk o noktadan devam
        ettirir, ok tuşları sarmayı sürdürür. Video bitmemişse ya da mpv yoksa False."""
        if not self._ended or self._mpv is None:
            return False
        if not mpv_set(self._mpv, "pause", True):
            return False
        self._ended = False
        self._is_paused = True
        self.skip(delta_ms)
        self.paused.emit()
        return True

    def seek(self, ms: int, exact: bool = True) -> None:
        ms = max(0, int(ms))
        self._pos_ms = ms
        if self._mpv is not None:
            try:
                mode = "absolute+exact" if exact else "absolute+keyframes"
                self._mpv.seek(ms / 1000.0, mode)
                self.progress.emit(ms, self.duration())
                return
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.isRunning():
            thread.request_seek(ms)
        self.progress.emit(ms, self.duration())

    # ------------------ Ses / altyazı parçaları ------------------

    def track_lists(self) -> tuple[list[dict], list[dict]]:
        """(ses parçaları, altyazı parçaları) — mpv `track-list`; motor yoksa boş."""
        from app.core.tracks import split_tracks

        if self._mpv is None:
            return [], []
        try:
            return split_tracks(self._mpv.track_list)
        except Exception:
            return [], []

    @staticmethod
    def selected_track_id(tracks: list[dict]) -> int | None:
        for t in tracks:
            if t.get("selected"):
                try:
                    return int(t["id"])
                except (KeyError, TypeError, ValueError):
                    return None
        return None

    def set_audio_track(self, track_id: int) -> bool:
        ok = mpv_set(self._mpv, "aid", int(track_id))
        if ok:
            self.tracks_changed.emit()
        return ok

    def set_subtitle_track(self, track_id: int | None) -> bool:
        """`None` = altyazı kapalı."""
        ok = mpv_set(self._mpv, "sid", "no" if track_id is None else int(track_id))
        if ok:
            if track_id is None:
                self.widget.set_subtitle("")
            self.tracks_changed.emit()
        return ok

    def add_subtitle_file(self, path: str) -> bool:
        """Harici altyazı dosyası yükle ve seç (mpv `sub-add`)."""
        if self._mpv is None:
            return False
        try:
            self._mpv.command("sub-add", str(path), "select")
        except Exception:
            return False
        self.tracks_changed.emit()
        return True

    # ------------------ Kare kaydetme ------------------

    def save_frame(self, dest: Path | str) -> bool:
        """Geçerli kareyi PNG olarak kaydet (özgün çözünürlük, altyazısız). Başarıyı döner."""
        dest = Path(dest)
        if self._mpv is not None:
            try:
                self._mpv.command("screenshot-to-file", str(dest), "video")
                if self._wait_file(dest):
                    return True
            except Exception:
                pass
        return self._save_frame_opencv(dest)

    @staticmethod
    def _wait_file(dest: Path, timeout: float = 1.5) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if dest.is_file() and dest.stat().st_size > 0:
                    return True
            except OSError:
                pass
            time.sleep(0.02)
        return False

    def _save_frame_opencv(self, dest: Path) -> bool:
        """libmpv yokken/başarısızken: OpenCV ile geçerli konumdaki kareyi oku ve yaz."""
        if not self._path:
            return False
        try:
            import cv2

            cap, rot = _open_capture(self._path)
            if cap is None:
                return False
            try:
                cap.set(cv2.CAP_PROP_POS_MSEC, float(self.position()))
                ok, frame = cap.read()
            finally:
                cap.release()
            if not ok or frame is None:
                return False
            okk, buf = cv2.imencode(".png", _orient_frame(frame, rot))
            if not okk:
                return False
            dest.write_bytes(buf.tobytes())  # imwrite Windows'ta Unicode yolları yazamaz
            return True
        except Exception:
            return False

    # ------------------ Döngü / hız / kare kare ------------------

    def set_loop(self, loop: bool) -> None:
        self.loop = bool(loop)
        mpv_set(self._mpv, "loop-file", "inf" if self.loop else "no")
        if self._thread is not None:
            self._thread.set_loop(self.loop)
        self.loop_changed.emit(self.loop)

    @property
    def speed(self) -> float:
        return self._speed

    def set_speed(self, speed: float) -> float:
        speed = min(SPEED_STEPS[-1], max(SPEED_STEPS[0], float(speed)))
        self._speed = speed
        mpv_set(self._mpv, "speed", speed)
        if self._thread is not None:
            self._thread.set_speed(speed)
        self.speed_changed.emit(speed)
        return speed

    def step_speed(self, direction: int) -> float:
        """Bir sonraki (+1) / önceki (-1) hız basamağı."""
        if direction > 0:
            nxt = next((s for s in SPEED_STEPS if s > self._speed + 1e-6), SPEED_STEPS[-1])
        else:
            nxt = next((s for s in reversed(SPEED_STEPS) if s < self._speed - 1e-6), SPEED_STEPS[0])
        return self.set_speed(nxt)

    def reset_speed(self) -> float:
        return self.set_speed(1.0)

    def step_frame(self, direction: int) -> bool:
        """Oynatma sürüyorsa duraklat, sonra bir kare ileri (+1) / geri (-1) git.

        Oynatma/duraklatma durumunda değilse (video yok, bitmiş) False döner.
        """
        if not (self._playing or self._is_paused):
            return False
        if self._playing and not self.pause():
            return False
        if self._mpv is not None:
            try:
                self._mpv.command("frame-step" if direction > 0 else "frame-back-step")
            except Exception:
                return False
            # Konum özelliği kare sunulduktan birkaç ms sonra güncellenir.
            QTimer.singleShot(60, self._emit_progress_now)
            return True
        thread = self._thread
        if thread is not None and thread.isRunning():
            thread.request_step(direction)
            return True
        return False

    def _emit_progress_now(self) -> None:
        """Duraklatılmışken de konumu arayüze bildir (kare adımından sonra)."""
        if self._mpv is None:
            return
        val = mpv_get(self._mpv, "time-pos")
        if val is None:
            return
        self._pos_ms = max(0, int(float(val) * 1000))
        self.progress.emit(self._pos_ms, self._dur_ms)

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def opening(self) -> bool:
        return self._opening

    @property
    def volume(self) -> int:
        return self._volume_percent

    def set_volume(self, percent: int) -> None:
        self._volume_percent = max(0, min(100, int(percent)))
        mpv_set(self._mpv, "volume", self._volume_percent)
        if self._volume_percent > 0 and self._muted:
            self.set_muted(False)
        self.volume_changed.emit(self._volume_percent)

    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        mpv_set(self._mpv, "mute", self._muted)
        if not self._muted:
            # Ses düzeyi sessizlikten bağımsızdır; açılınca kullanıcının düzeyi geri gelmeli.
            mpv_set(self._mpv, "volume", self._volume_percent)
        self.muted_changed.emit(self._muted)

    def toggle_muted(self) -> None:
        self.set_muted(not self._muted)

    def audio_devices(self) -> list[dict]:
        """Sistemdeki ses çıkış aygıtlarını listele."""
        if self._mpv is not None:
            try:
                devs = self._mpv.audio_device_list
                if devs:
                    return list(devs)
            except Exception:
                pass
        return [{"name": "auto", "description": "Varsayılan Aygıt"}]

    def current_audio_device(self) -> str:
        return self._audio_device

    def preferred_audio_device(self) -> str:
        """Kullanıcının seçtiği (henüz doğrulanmamış olabilecek) aygıt."""
        return self._desired_audio_device

    def set_audio_device(self, device_name: str) -> None:
        name = str(device_name or "auto").strip() or "auto"
        self._desired_audio_device = name
        if self._mpv is None:
            # Motor yokken aygıt listesi yok: tercihi koru, _apply_audio_settings
            # motor kurulunca gerçek listeyle doğrular.
            return
        self._audio_device = self._sanitize_audio_device(name)
        if not mpv_set(self._mpv, "audio-device", self._audio_device):
            self._audio_device = "auto"
            mpv_set(self._mpv, "audio-device", "auto")

    def _sanitize_audio_device(self, device_name: str) -> str:
        name = str(device_name or "auto").strip() or "auto"
        if name == "auto":
            return name
        known = []
        try:
            known = [str(d.get("name") or "") for d in (self.audio_devices() or [])]
        except Exception:
            known = []
        if known and name not in known:
            return "auto"
        return name

    def _apply_audio_settings(self) -> None:
        """Ses aygıtı / ses / mute — canlı özellik olarak (options/ değil)."""
        device = self._sanitize_audio_device(self._desired_audio_device or "auto")
        self._audio_device = device
        if not mpv_set(self._mpv, "audio-device", device):
            self._audio_device = "auto"
            mpv_set(self._mpv, "audio-device", "auto")
        mpv_set(self._mpv, "mute", self._muted)
        # Sessizlik yalnızca `mute` ile: eskiden sessizken yeni dosya açılınca volume=0 yazılıyor, sonra
        # ses açılınca (set_muted(False) yalnızca mute'u değiştirir) video SESSİZ kalıyordu.
        mpv_set(self._mpv, "volume", self._volume_percent)

    def play(self, path: str, loop: bool | None = None) -> None:
        """`loop` verilmezse mevcut döngü tercihi korunur (set_loop ile değişir)."""
        if self._playing or self._opening:
            self._progress_timer.stop()
            self._playing = False
            self._opening = False
        if loop is not None:
            self.loop = bool(loop)
        loop = self.loop
        self._path = path
        self._opening = True
        self._is_paused = False
        self._ended = False
        self._pos_ms = 0
        self._dur_ms = 0
        self._gen += 1
        self.loading.emit(True)

        if mpv_available() and self._mpv is None:
            self._init_mpv()

        if self._mpv is not None:
            try:
                mpv_set(self._mpv, "loop-file", "inf" if loop else "no")
                mpv_set(self._mpv, "speed", self._speed)
                self._apply_audio_settings()
                # Önce dosyayı yükle, sonra pause=False — ters sıra bazı kodeklerde
                # çift seek yapmasına ve CPU spike'ına yol açıyor.
                self._mpv.play(str(path))
                # loadfile eşzamanlıdır: bu yüklemenin kimliği artık playlist'te. Kuyruktaki
                # olaylar play() döndükten sonra işlendiğinden eski dosyanınkiler elenir.
                self._entry_id = self._current_entry_id()
                mpv_set(self._mpv, "pause", False)
                # Timer'ı başlatmıyoruz — _on_mpv_started (file-loaded eventi) başlatır.
                # _opening sırasında timer çalışması gereksiz, çünkü _playing=False.
                # 8 saniye içinde file-loaded gelmezse hata ver.
                _timeout_gen = self._gen
                QTimer.singleShot(8000, lambda g=_timeout_gen: self._check_play_timeout(g))
                return

            except Exception:
                try:
                    self._mpv.stop()
                except Exception:
                    pass
                self._progress_timer.stop()
                self._opening = False
                self._playing = False
                self.loading.emit(False)
                self.failed.emit("Video açılamadı.")
                return

        # Fallback OpenCV — yalnızca mpv kurulamadığında (modül yok / offscreen).
        thread = self._thread or self._spawn_fallback_thread()
        thread.configure(self._path, self.loop, self._gen)
        thread.set_speed(self._speed)
        thread.start()

    def _check_play_timeout(self, gen: int | None = None) -> None:
        """Oturum bazlı açılış zaman aşımı — eski oturum yenisini etkilemez.

        `gen` parametresi verilmişse bu timeout başlatıldığındaki `_gen` değeridir.
        Eğer `_gen` değişmişse (yeni dosya açıldı) bu timeout etkisiz olur.
        """
        if gen is not None and gen != self._gen:
            return  # Eski oturum — yeni dosyaya müdahale etme
        # Tek motor ilkesi: mpv açamazsa hata ver, ikinci bir decoder çalıştırma.
        if not self._opening:
            return
        if self._mpv is not None:
            try:
                self._mpv.stop()
            except Exception:
                pass
        self._progress_timer.stop()
        self._opening = False
        self._playing = False
        self.loading.emit(False)
        self.failed.emit("Video açılamadı.")



    def _start_fallback(self, gen: int) -> None:
        # Yalnızca mpv hiç kurulamadıysa (modül yok / desteklenmeyen platform).
        if gen != self._gen or self._mpv is not None:
            return
        thread = self._thread or self._spawn_fallback_thread()
        thread.configure(self._path, self.loop, gen)
        thread.start()

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    def pause(self) -> bool:
        """Duraklat. mpv komutu başarısız olursa durum DEĞİŞMEZ ve False döner."""
        if not self._playing and not self._opening:
            return False
        # Canlı `pause` özelliği — options/pause değil. Aksi halde video arka planda akar.
        if self._mpv is not None and not mpv_set(self._mpv, "pause", True):
            return False
        self._is_paused = True
        self._playing = False
        self._opening = False
        self._progress_timer.stop()
        if self._thread is not None:
            self._thread.set_paused(True)
        self.paused.emit()
        return True

    def resume(self) -> bool:
        """Devam et. mpv komutu başarısız olursa durum DEĞİŞMEZ ve False döner."""
        if not self._is_paused:
            return False
        if self._mpv is not None and not mpv_set(self._mpv, "pause", False):
            return False
        self._is_paused = False
        self._playing = True
        self._progress_timer.setInterval(333)
        self._progress_timer.start()
        if self._thread is not None:
            self._thread.set_paused(False)
        QTimer.singleShot(80, self._on_progress_tick)
        # Kare adımı henüz bitmediyse mpv adım bitince yeniden duraklatır ve bu "devam"ı yutar.
        QTimer.singleShot(60, self._ensure_resumed)
        QTimer.singleShot(220, self._ensure_resumed)
        return True

    def _ensure_resumed(self) -> None:
        """Durumumuz 'oynuyor' ama mpv hâlâ duraklıysa (kare adımıyla yarış) devam komutunu yinele."""
        if self._mpv is None or not self._playing or self._is_paused:
            return
        if mpv_get(self._mpv, "pause"):
            mpv_set(self._mpv, "pause", False)


    def stop(self) -> None:
        self._is_paused = False
        self._ended = False
        self._playing = False
        self._opening = False
        self._pos_ms = 0
        self._dur_ms = 0
        self._gen += 1
        self._progress_timer.stop()
        if self._mpv is not None:
            try:
                mpv_set(self._mpv, "pause", True)
                self._mpv.stop()
            except Exception:
                pass
        if self._thread is not None and self._thread.isRunning():
            self._thread.requestInterruption()
            self._thread.wait(500)
        self.widget.clear()
        self.widget.set_subtitle("")
        self.loading.emit(False)
        self.progress.emit(0, 0)
        self.stopped.emit()

    def _discard_mpv(self) -> None:
        """Mevcut mpv örneğini güvenle sök (gözlemciler, olay kayıtları, sınırlı süreli terminate)."""
        mpv_obj = self._mpv
        self._mpv = None
        self._entry_id = None
        if mpv_obj is None:
            return
        # Gözlemci/olay kayıtlarını (tutulan referanslarla) gerçekten kaldır; eskiden
        # unobserve_property(name) handler'sız çağrılıp TypeError yutuluyordu.
        for name, handler in self._observers:
            try:
                mpv_obj.unobserve_property(name, handler)
            except Exception:
                pass
        for wrapper in self._event_handlers:
            try:
                wrapper.unregister_mpv_events()
            except Exception:
                pass
        self._observers.clear()
        self._event_handlers.clear()
        try:
            mpv_obj.command("quit")
        except Exception:
            pass
        # terminate() olay thread'ini sınırsız bekler; kapanışta arayüz asılmasın.
        killer = threading.Thread(target=self._terminate_quietly, args=(mpv_obj,), daemon=True)
        killer.start()
        killer.join(2.0)

    def shutdown(self) -> None:
        self._progress_timer.stop()
        self._discard_mpv()
        self._is_paused = False
        self._ended = False
        self._playing = False
        self._opening = False
        self._pos_ms = 0
        self._dur_ms = 0
        self._gen += 1
        if self._thread is not None:
            if self._thread.isRunning():
                self._thread.requestInterruption()
                self._thread.wait(1000)
            self._thread.deleteLater()
            self._thread = None
        self.widget.clear()
        self.widget.set_subtitle("")
        self.loading.emit(False)
        self.progress.emit(0, 0)

    @staticmethod
    def _terminate_quietly(mpv_obj) -> None:
        try:
            mpv_obj.terminate()
        except Exception:
            pass

    def get_stream_metadata(self) -> dict[str, str]:
        """mpv örneğinden aktif video ve ses akışı meta verilerini oku."""
        meta: dict[str, str] = {}
        if self._mpv is None:
            return meta
        try:
            v_codec = None
            try:
                v_codec = self._mpv.get_property("video-codec")
            except Exception:
                v_codec = getattr(self._mpv, "video_codec", None)
            if v_codec:
                meta["video_codec"] = str(v_codec).upper()
        except Exception:
            pass

        try:
            a_codec = None
            try:
                a_codec = self._mpv.get_property("audio-codec-name")
            except Exception:
                a_codec = getattr(self._mpv, "audio_codec_name", None)
            if a_codec:
                meta["audio_codec"] = str(a_codec).upper()
        except Exception:
            pass

        try:
            sr = self._mpv.get_property("audio-params/samplerate")
            if sr:
                meta["audio_samplerate"] = f"{sr} Hz"
        except Exception:
            pass

        try:
            ch = self._mpv.get_property("audio-params/channel-count")
            if ch:
                if ch == 2:
                    meta["audio_channels"] = "2 (Stereo)"
                elif ch == 1:
                    meta["audio_channels"] = "1 (Mono)"
                else:
                    meta["audio_channels"] = f"{ch} kanal"
        except Exception:
            pass

        return meta
