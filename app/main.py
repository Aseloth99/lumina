"""Lumina giriş noktası."""

from __future__ import annotations

import os
import sys
import time

_MODULE_T0_MS = time.time() * 1000.0  # açılış izi: giriş betiğinin ilk satırı (ağır içe aktarmalardan önce)

if len(sys.argv) > 1 and sys.argv[1] == "--svg-render-worker":
    # SVG bölge çizimi işçi süreci (app/core/svg_worker.py): arayüz/Qt yüklenmeden doğrudan çizim döngüsüne gir.
    # (resvg çizerken GIL'i tutar; ana süreçte çizmek arayüzü dondururdu.)
    from app.core.svg_worker import serve

    raise SystemExit(serve())

os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
os.environ.setdefault("AV_LOG_FORCE_NOCOLOR", "1")
os.environ.setdefault("AV_LOG_LEVEL", "quiet")
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
if sys.platform == "win32":
    os.environ.setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", "d3d11va,dxva2,cuda,qsv")

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont, QFontDatabase, QIcon
from PyQt6.QtWidgets import QApplication

from app.core import update_boot
from app.core.crashlog import install_crash_log
from app.core.version import get_version
from app.resources.brand import ensure_app_icon
from app.ui.session import spawn_window


# Yazı tipi tercihleri (ilk bulunan kullanılır); sonunda sistemin genel yazı tipine düşülür.
FONT_CANDIDATES = {
    "win32": ("Segoe UI Variable Text", "Segoe UI", "Arial"),
    "darwin": (".AppleSystemUIFont", "SF Pro Text", "Helvetica Neue", "Arial"),
    "linux": ("Inter", "Ubuntu", "Noto Sans", "Cantarell", "DejaVu Sans", "Arial"),
}


def pick_font_family(available: set[str], platform: str | None = None, system_default: str = "") -> str:
    key = platform if platform is not None else sys.platform
    key = "linux" if key.startswith("linux") else key
    for family in FONT_CANDIDATES.get(key, FONT_CANDIDATES["linux"]):
        if family in available:
            return family
    return system_default or "Arial"


def prefer_x11_for_embedding(environ=None, platform: str | None = None) -> bool:
    """Linux/Wayland: libmpv `wid` gömmesi X11 penceresi ister; XWayland üzerinden xcb'yi tercih et.

    Kullanıcı QT_QPA_PLATFORM'u kendisi verdiyse dokunulmaz. Liste biçimi (`xcb;wayland`) xcb
    eklentisi yoksa yerel Wayland'e düşer.
    """
    env = os.environ if environ is None else environ
    plat = platform if platform is not None else sys.platform
    if not plat.startswith("linux") or env.get("QT_QPA_PLATFORM"):
        return False
    if env.get("XDG_SESSION_TYPE", "").lower() == "wayland" or env.get("WAYLAND_DISPLAY"):
        env["QT_QPA_PLATFORM"] = "xcb;wayland"
        return True
    return False


def _setup_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _set_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.HRESULT
        set_app_id("Lumina.Viewer")
    except Exception:
        return


SERVICES_MAX_DELAY_MS = 1000


def _once(fn):
    done = []

    def run(*_args) -> None:
        if not done:
            done.append(True)
            fn()

    return run


def _attach_background_services(app) -> None:
    # Qt yuvasında (zamanlayıcı/sinyal) çalışır: yakalanmayan istisna PyQt6'da süreci sonlandırır.
    try:
        from app.ui.updates import attach_updates

        attach_updates(app)
    except Exception:  # noqa: BLE001 — güncelleme denetimi kurulamazsa görüntüleyici yine çalışır
        return
    try:
        from app.ui.session import windows

        for win in list(windows):  # hizmet pencerenin kendi 1,5 sn'lik bağlanma denemesinden sonra kurulduysa
            win._bind_notice()
    except Exception:  # noqa: BLE001
        pass


def _attach_tray(app, icon) -> None:
    """Sistem tepsisi simgesi: Lumina çalıştığı sürece görünür (yalnızca tepsi sahibi süreçte), süreç bitince kaybolur.
    Qt yuvasında çalışır: kurulamazsa görüntüleyici simgesiz sürer."""
    try:
        from app.ui.tray import attach_tray

        attach_tray(app, icon)
    except Exception:  # noqa: BLE001
        pass


def _enter_sandbox(folder: str) -> None:
    """Duman testi/ölçüm: ayarlar kayıt defteri yerine bu klasördeki INI'de (kullanıcınınkine dokunulmaz)."""
    from PyQt6.QtCore import QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, folder)


def _start_prefetch(argv: list[str]) -> None:
    """Açılan görseli pencere kurulurken arka planda çözmeye başla (image_loader.prefetch).

    Modül burada ana thread'de yüklenir (pencere zaten yükleyecekti); iş parçacığı yalnızca çözer, iki
    thread aynı modülleri aynı anda içe aktarmaz.
    """
    args = update_boot.positional_args(argv)  # `--updated V --show-window` gibi bayraklar dosya yolu sanılmaz
    if not args:
        return
    try:
        from app.core.image_loader import prefetch

        prefetch(args[0])
    except Exception:  # noqa: BLE001 — ön-çözüm yalnızca hızlandırır; normal yükleme yine çalışır
        pass


def main(argv: list[str] | None = None) -> int:
    from app.core import startup_trace

    startup_trace.mark_process_created()
    startup_trace.mark("main_module", _MODULE_T0_MS)
    startup_trace.mark("main_enter")
    sandbox = startup_trace.sandbox_dir()
    if sandbox:
        _enter_sandbox(sandbox)
    argv = sys.argv if argv is None else argv
    # Güncelleme izi (QApplication'dan ÖNCE): `--wait-pid` beklemesi, çöken yeni sürümün geri alınması.
    boot = update_boot.begin(argv)
    if boot.rolled_back:
        return 0  # eski sürüm yeniden başlatıldı; bu (bozuk) süreç çıkar
    _start_prefetch(argv)
    install_crash_log()
    prefer_x11_for_embedding()
    try:
        from app.core.video_io import configure_qt_media_backend

        configure_qt_media_backend()
        # OpenCV/FFmpeg burada yüklenmez (fotoğraf açılışında hiç gerekmez); ilk video işinde
        # video_io.load_cv2 yükler ve sessizleştirir.
    except Exception:
        pass
    _setup_dpi()
    _set_app_id()
    from PyQt6.QtCore import Qt
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    # mpv video yüzeyi native HWND'dir; kardeşleri native olursa yuvarlak
    # köşeler alfa karışımı yapamaz (siyah/beyaz köşe sorunu).
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings, True)
    app = QApplication(argv)
    app.setApplicationName("LivePhotoViewer")
    app.setOrganizationName("Lumina")
    app.setApplicationDisplayName("Lumina")
    app.setApplicationVersion(get_version())
    update_boot.arm(app, boot)  # deneme sürecindeki yeni sürüm 60 sn / temiz çıkışta kendini kanıtlar
    for effect in (
        Qt.UIEffect.UI_AnimateCombo,
        Qt.UIEffect.UI_AnimateMenu,
        Qt.UIEffect.UI_FadeMenu,
        Qt.UIEffect.UI_AnimateTooltip,
    ):
        app.setEffectEnabled(effect, False)
    startup_trace.mark("qapp")
    icon = QIcon(str(ensure_app_icon()))
    app.setWindowIcon(icon)
    if not sandbox:
        try:
            from app.core.win_launcher import ensure_app_exe
            from app.core.registry import refresh_if_stale

            if sys.platform == "win32":
                ensure_app_exe()
            # Yalnızca kayıtlı komut bayatsa (exe taşındı/güncellendi) yaz; güncelse hiçbir şey yazılmaz.
            refresh_if_stale()
        except Exception:
            pass

    # Önceki çökme/zorla kapanmalardan kalan eski geçici dosyaları (24 saatten eski) arka planda sil.
    try:
        import threading

        from app.core.motion_parser import sweep_stale_temps

        threading.Thread(target=sweep_stale_temps, name="LuminaTempSweep", daemon=True).start()
    except Exception:
        pass

    font_family = pick_font_family(
        set(QFontDatabase.families()),
        system_default=QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family(),
    )
    font = QFont(font_family, 10)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)

    initial = update_boot.launch_path(argv)

    startup_trace.mark("pre_window")
    window = spawn_window(initial)
    startup_trace.mark("window_shown")
    QTimer.singleShot(0, lambda: startup_trace.mark("event_loop"))
    if boot.updated_from:
        QTimer.singleShot(1500, lambda: update_boot.announce(window, boot))  # "vX sürümüne güncellendi"
    if not sandbox:
        # Güncelleme denetimi (1,2 sn sonra başlar) ilk kareyi beklemesin: modülleri
        # (urllib/ssl/hashlib) yüklemek ve kurmak ilk kare çizildikten sonra (en geç SERVICES_MAX_DELAY_MS).
        # Tepsi simgesi de ilk kareden sonra; güncelleme hizmeti önce kurulur ki "Güncelle · vX" satırı ona bağlansın.
        attach = _once(lambda: (_attach_background_services(app), _attach_tray(app, icon)))
        window.canvas.frame_shown.connect(attach)
        QTimer.singleShot(SERVICES_MAX_DELAY_MS, attach)

    quit_ms = os.environ.get("LUMINA_QUIT_MS")
    if quit_ms:
        QTimer.singleShot(int(quit_ms), app.quit)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
