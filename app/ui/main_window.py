"""Ana pencere — orkestrasyon, overlay oynatma, bağlam duyarlı arayüz."""

from __future__ import annotations
import shutil
import sys
import threading
import time
from pathlib import (
    Path,
)
from PyQt6.QtCore import (
    QByteArray,
    QEvent,
    QFileSystemWatcher,
    QMimeData,
    QPoint,
    QRect,
    QSettings,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
)
from PyQt6.QtGui import (
    QAction,
    QDesktopServices,
    QActionGroup,
    QColor,
    QCursor,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from app.core import startup_trace
from app.core.settings_store import make_settings
from app.core.version import (
    get_version,
)
from app.core.cache import (
    FolderUuidCache,
)
from app.core.folder_scan import (
    FolderScanWorker,
)
from app.core.image_loader import (
    ImageLoadWorker,
    VideoPosterWorker,
    get_cached_image,
    has_cached_entry,
    path_key,
    same_path_text,
)
from app.core.matcher import (
    IMAGE_EXTS,
    VIDEO_EXTS,
    LiveMatch,
    index_after_change,
    index_in_folder,
    paired_sidecars,
    still_for_video,
)
from app.core.motion_parser import (
    TempVideoStore,
)
from app.core.protocols import (
    extract_livp,
)
from app.core.registry import (
    install_context_menu,
    is_installed,
    uninstall_context_menu,
)
from app.core.win_shell import (
    discard_undo,
    open_default_apps,
    open_with,
    recycle_with_undo,
    restore_files,
    reveal_in_explorer,
)
from app.resources.locales import (
    get_active_language,
    install_qt_translations,
    language_choices,
    t as locale_t,
    translate_error,
)
from app.resources import theme as theme_mod
from app.resources.styles import (
    resolve_theme,
)
from app.ui.canvas import (
    PhotoCanvas,
)
from app.ui.dialogs import configure_dialogs
from app.ui.message_box import InputDialog, MessageBox
from app.ui.details_panel import (
    DetailsPanel,
)
from app.ui.filmstrip import (
    Filmstrip,
)
from app.ui.frameless import (
    FramelessWindow,
)
from app.ui.icons import (
    accent_dot_icon,
    arrow_icon,
    chevron_horizontal_icon,
    language_flag_icon,
    folder_icon,
    frame_step_icon,
    fullscreen_icon,
    info_icon,
    loop_icon,
    maximize_icon,
    menu_icon,
    minimize_icon,
    more_icon,
    restore_icon,
    rotate_icon,
    tracks_icon,
    theme_dark_icon,
    theme_light_icon,
    theme_system_icon,
    trash_icon,
    volume_icon,
    zoom_icon,
)
from app.core.live_worker import (
    LiveResolveWorker,
)
from app.ui.chrome_widgets import (
    EmptyState,
    OverlayHost,
    Stage,
    VolumePopup,
    polish as _polish,
)
from app.ui.media_actions import MediaActionsMixin
from app.ui.live_button import (
    BottomDock,
    make_live_orb,
)
from app.ui.player import (
    SPEED_STEPS,
    LivePlayer,
)
from app.ui.seek_preview import (
    SeekPreviewController,
)


# Sistem entegrasyonu (varsayılan uygulama, 'Birlikte aç' kaydı) yalnızca Windows'ta sunulur.
SYSTEM_INTEGRATION = sys.platform == "win32"

# İlk kare bu süre içinde çizilmezse (çok büyük/yavaş görsel, gizli pencere) bekletilen yan işler yine başlar.
FRAME_GATE_MAX_MS = 2500

# MainWindow.eventFilter'ın kullandığı olay türleri: ses düğmesi/popup (Enter/Leave/Wheel),
# tam ekranda arayüzü uyandırma ve Delete kısayolu (fare/klavye). Diğer her olay doğrudan geçer.
_MAIN_FILTER_EVENT_TYPES = frozenset(
    {
        QEvent.Type.Enter,
        QEvent.Type.Leave,
        QEvent.Type.Wheel,
        QEvent.Type.MouseMove,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.KeyPress,
    }
)


class MainWindow(MediaActionsMixin, FramelessWindow):
    def __init__(self, initial_path: str | None = None) -> None:
        startup_trace.mark("window_init")
        super().__init__()
        self.setWindowTitle("Lumina")
        from app.resources.brand import ensure_app_icon

        self.setWindowIcon(QIcon(str(ensure_app_icon())))
        self.setMinimumSize(640, 480)
        self.setAcceptDrops(True)

        self.settings = make_settings()
        self.lang_choice = str(self.settings.value("lang", "auto") or "auto")
        self.lang = get_active_language(self.lang_choice)
        self.theme = str(self.settings.value("theme", "system"))  # ilk açılışta Windows'un açık/koyu ayarı
        if self.theme not in {"dark", "light", "system"}:
            self.theme = "system"
        self.accent = theme_mod.normalize_accent(str(self.settings.value("accent", theme_mod.DEFAULT_ACCENT) or ""))
        theme_mod.set_active(accent=self.accent)  # özel kutular/simgeler pencere kurulmadan da doğru vurguyu okusun
        configure_dialogs()
        self.setWindowTitle(self.t("title"))
        # Marka solda, pencere kontrolleri sağda ayrı cam kapsüllerdir.
        self.chrome_side = "right"
        self.settings.setValue("chrome_side", "right")
        self.sort_by = str(self.settings.value("sort_by", "date_asc"))
        if self.sort_by not in {"date_asc", "date_desc", "name_asc", "name_desc"}:
            self.sort_by = "date_asc"
        self.audio_device = str(self.settings.value("audio_device", "auto") or "auto")
        self._strip_open = self._setting_bool("strip_open", True)
        # Son seçilen görünüm kipi (Sığdır / Doldur): her yeni dosyada ve bir sonraki açılışta geçerli olur.
        self._view_fill = self._setting_bool("view_fill", False)
        self._opening_play = False
        self._scrubbing = False
        self._pending_seek: int | None = None
        self._match_token = 0
        self._scan_token = 0
        self._folder_ready = True
        self._watched_folder: str | None = None
        self._folder_watcher = QFileSystemWatcher(self)
        self._folder_watcher.directoryChanged.connect(self._on_directory_changed)
        self._dir_watch_timer = QTimer(self)
        self._dir_watch_timer.setSingleShot(True)
        self._dir_watch_timer.timeout.connect(self._rescan_active_folder)

        self.image_files: list[Path] = []
        self.current_idx = -1
        self.current_match: LiveMatch | None = None
        self._unplayable_paths: set[str] = set()
        self._load_token = 0
        self._playing = False
        self._display_path: Path | None = None
        self._has_audio = True
        self._autoplay_armed = True
        self._undo = None
        self._details_on = False
        self._update_badge = False
        self._update_version = ""

        self.temps = TempVideoStore()
        self.uuid_cache = FolderUuidCache(self)
        self.uuid_cache.ready.connect(self._on_uuid_ready)
        self.pool = QThreadPool.globalInstance()
        # Önizleme + storyboard worker'ları için en az 2 thread garantisi
        if self.pool.maxThreadCount() < 4:
            self.pool.setMaxThreadCount(4)

        self._slideshow_timer = QTimer(self)
        self._slideshow_timer.setInterval(3500)
        self._slideshow_timer.timeout.connect(self._slideshow_step)

        self._cursor_blank = False
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(5000)
        self._idle_timer.timeout.connect(self._on_idle_timeout)

        # İlk kare önceliği (bkz. _arm_frame_gate): kare çizilene dek bekletilen yan işler.
        self._frame_pending = False
        self._after_frame_queue: list = []
        self._refine_gate: threading.Event | None = None
        self._frame_gate_timer = QTimer(self)
        self._frame_gate_timer.setSingleShot(True)
        self._frame_gate_timer.timeout.connect(self._release_frame_gate)

        self._build_ui()
        startup_trace.mark("ui_built")
        self.canvas.frame_shown.connect(self._release_frame_gate)
        self._restore_or_place_window()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self._bind_shortcuts()
        self.apply_theme()
        self.retranslate_ui()
        self._sync_chrome()
        try:
            QApplication.instance().styleHints().colorSchemeChanged.connect(
                self._on_system_theme
            )
        except Exception:
            pass

        startup_trace.watch_first_paint(
            self.canvas.viewport(), lambda: not self.canvas.pixmap_item.pixmap().isNull()
        )
        if initial_path:
            QTimer.singleShot(0, lambda: self.load_path(initial_path))
        QTimer.singleShot(1500, self._bind_notice)

    def _restore_or_place_window(self) -> None:
        geo = self.settings.value("win_geo")
        if isinstance(geo, QByteArray) and not geo.isEmpty() and self.restoreGeometry(geo):
            return
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            width = min(1080, max(860, int(avail.width() * 0.62)))
            height = min(720, max(560, int(avail.height() * 0.72)))
            self.resize(width, height)
            frame = self.frameGeometry()
            frame.moveCenter(avail.center())
            self.move(frame.topLeft())
        else:
            self.resize(1080, 700)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout_shell()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        # Salt taşıma boyutu değiştirmez: dock/kapsül yeniden yerleşimi (adjustSize, raise_, QRegion
        # maskesi) gereksiz ve sürüklerken her piksel adımında çalışıyordu. Yalnızca global
        # koordinatlı üst pencereler (tam ekran overlay'leri, meşgul çipi) konumunu günceller.
        if not hasattr(self, "dock_wrap"):
            return
        if self.isFullScreen():
            self._layout_overlay_chrome()
        self._place_busy_chip()

    def _ensure_dock_in_viewer(self) -> None:
        if self.isFullScreen():
            return
        if not hasattr(self, "dock_wrap") or not hasattr(self, "viewer_col"):
            return
        viewer = getattr(self, "viewer", None)
        if viewer is None:
            return
        if self.dock_wrap.parent() is not viewer:
            self.dock_wrap.setParent(viewer)
        if self.viewer_col.indexOf(self.dock_wrap) < 0:
            self.viewer_col.addWidget(self.dock_wrap)

    def _layout_window_dock(self) -> None:
        """Pencere modunda alt barı yatayda ortala."""
        if self.isFullScreen():
            return
        if not hasattr(self, "dock_wrap") or not hasattr(self, "bottom_dock"):
            return
        self._ensure_dock_in_viewer()
        self.dock_wrap.show()
        self.dock_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.dock_wrap.setMinimumWidth(0)
        self.dock_wrap.setMaximumWidth(16777215)
        viewer = getattr(self, "viewer", None)
        avail = max(360, viewer.width() if viewer is not None else self.width() - 32)
        self.bottom_dock.update_dock_geometry(avail)
        self.bottom_dock.adjustSize()
        layout = self.dock_wrap.layout()
        if layout is not None:
            layout.setContentsMargins(10, 0, 10, 8)
        self.dock_wrap.updateGeometry()

    def _relayout_shell(self) -> None:
        if not hasattr(self, "dock_wrap") or not hasattr(self, "bottom_dock"):
            return
        if not self.isMinimized():
            self._sync_fullscreen_mode()
        if self.isFullScreen():
            if hasattr(self, "viewer_host"):
                self.viewer_host.setContentsMargins(0, 0, 0, 0)
            # Kapsüller ve alt bar ayrı şeffaf üst pencerelerde (overlay host) yüzer.
            self._layout_overlay_chrome()
        else:
            if hasattr(self, "viewer_host"):
                self.viewer_host.setContentsMargins(0, 46, 0, 0)
            self._layout_window_dock()

        if hasattr(self, "stage"):
            self.stage.layout_chrome()
        if not self.isFullScreen():
            if hasattr(self, "nav_bar"):
                self.nav_bar.raise_()
            if hasattr(self, "brand_bar"):
                self.brand_bar.raise_()
            if hasattr(self, "file_chip") and not self.file_chip.isHidden():
                self.file_chip.raise_()
            self.dock_wrap.raise_()
        self._place_busy_chip()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(self, "stage"):
            self.stage.layout_chrome()
            QTimer.singleShot(0, self.stage.layout_chrome)
            QTimer.singleShot(60, self.stage.layout_chrome)
            QTimer.singleShot(250, self.stage.layout_chrome)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if not hasattr(self, "container"):
            return
        if event.type() == QEvent.Type.WindowStateChange:
            if not self.isMinimized():
                self._sync_fullscreen_mode()
            if self.isMinimized():
                for host in (
                    getattr(self, "_dock_host", None),
                    getattr(self, "_brand_host", None),
                    getattr(self, "_chip_host", None),
                    getattr(self, "_nav_host", None),
                    getattr(self, "_busy_host", None),
                    getattr(getattr(self, "_toast", None), "host", None),
                ):
                    if host is not None:
                        host.hide()
            elif self.isFullScreen():
                self._layout_overlay_chrome()
                self._show_fullscreen_chrome()
            self._relayout_shell()
            # Pencere durumu dışarıdan değiştirilirse (showFullScreen/showNormal
            # doğrudan çağrısı) btn_full butonunu gerçek durumla senkronize et.
            self._sync_fullscreen_button()
        if hasattr(self, "stage"):
            self.stage.layout_chrome()
            QTimer.singleShot(0, self.stage.layout_chrome)

    def _sync_fullscreen_mode(self) -> None:
        """Overlay (tam ekran) modunu pencerenin GERÇEK durumuna eşitle.

        Tek durum kaynağı: giriş/çıkış yalnızca burada yapılır. toggle_fullscreen,
        F5 slayt gösterisi, Win+Aşağı, kayıtlı geometriyle açılış gibi tüm yollar
        buradan geçer. İdempotent; bayrak, yeniden giriş olmasın diye önce set edilir.
        """
        needed = ("container", "btn_full", "dock_wrap", "_dock_host", "_brand_host", "_nav_host", "_chip_host")
        if not all(hasattr(self, name) for name in needed):
            return
        active = getattr(self, "_fs_mode_active", False)
        fullscreen = self.isFullScreen()
        if fullscreen and not active:
            self._fs_mode_active = True
            self._enter_fullscreen_mode()
            self._sync_notice_row()
        elif not fullscreen and active:
            self._fs_mode_active = False
            self._exit_fullscreen_mode()
            self._sync_notice_row()

    def _sync_fullscreen_button(self) -> None:
        """btn_full butonunun active property'sini pencerenin gerçek tam ekran durumuna göre ayarla.

        NOT: Mask yönetimi burada yapılmıyor — _enter/_exit_fullscreen_mode metodları
        bu işi doğru zamanda (layout_chrome öncesinde) yapıyor. changeEvent'ten gelen
        ikinci bir clearMask/setMask çağrısı layout_chrome'dan önce nav_bar'ı clip dışına
        çıkarıp şeffaf görünmesine yol açıyor.
        """
        btn = getattr(self, "btn_full", None)
        if btn is None:
            return
        is_fs = self.isFullScreen()
        btn.setProperty("active", "true" if is_fs else "false")
        _polish(btn)


    def drag_widgets(self) -> list[QWidget]:
        targets = [self.brand_bar]
        if hasattr(self, "dock_wrap"):
            targets.append(self.dock_wrap)
        if hasattr(self, "bottom_dock"):
            targets.append(self.bottom_dock)
        return targets

    def resize_targets(self) -> list[QWidget]:
        widgets = [
            self.container,
            self.stage,
            self.brand_bar,
            self.empty,
            self.canvas,
            self.player.widget,
            self.filmstrip,
            self.details,
            self.bottom_dock,
        ]
        return [w for w in widgets if w is not None]

    def t(self, key: str) -> str:
        return locale_t(key, self.lang)

    def _setting_bool(self, key: str, default: bool = False) -> bool:
        val = self.settings.value(key, default)
        if isinstance(val, str):
            return val.lower() in {"1", "true", "yes"}
        return bool(val)

    def _build_ui(self) -> None:
        self.container = QWidget(self)
        self.container.setObjectName("centralContainer")
        self.setCentralWidget(self.container)

        root = QVBoxLayout(self.container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.stage = Stage(self.container)
        self.stage.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.stage.chrome_side = self.chrome_side

        self.details = DetailsPanel()
        self.details.closed.connect(self.hide_details)

        self.viewer = QWidget()
        self.viewer_col = viewer_col = QVBoxLayout(self.viewer)
        viewer_col.setContentsMargins(0, 0, 0, 0)
        viewer_col.setSpacing(6)

        viewer_col.addWidget(self.stage, stretch=1)

        # Üst 46px kapsül şeridi viewer'ın DIŞINDA (salt layout, widget değil).
        # viewer, mpv yüzeyinin atası olduğu için native HWND'dir; şerit onun
        # içinde kalırsa alien kapsüller viewer penceresinin arkasında kalır.
        self.viewer_host = QVBoxLayout()
        self.viewer_host.setContentsMargins(0, 46, 0, 0)
        self.viewer_host.setSpacing(0)
        self.viewer_host.addWidget(self.viewer, stretch=1)

        # Uzaktan duyuru satırı: pencerenin en üstünde tek satır; ✕ ile kapatılana dek kalır (sunucu duyuru verene kadar gizli)
        from app.ui.notice_bar import NoticeBar

        self.notice_bar = NoticeBar()
        self.notice_bar.dismissed.connect(self._on_notice_dismissed)
        self.notice_row = QWidget(self.container)
        self.notice_row.setObjectName("noticeRow")
        notice_lay = QHBoxLayout(self.notice_row)
        notice_lay.setContentsMargins(14, 8, 14, 0)
        notice_lay.addWidget(self.notice_bar)
        self.notice_row.hide()
        root.addWidget(self.notice_row)

        self.body_layout = body = QHBoxLayout()
        # Ayrıntı panelinin sağ kenarı, üstteki kapsülün sağ kenarıyla
        # aynı hizada kalsın; pencerenin en sağına yapışmasın.
        body.setContentsMargins(0, 0, 14, 0)
        body.setSpacing(8)
        body.addLayout(self.viewer_host, stretch=1)
        body.addWidget(self.details)
        root.addLayout(body, stretch=1)

        self.nav_bar = QFrame(self.container)
        self.nav_bar.setObjectName("navBar")
        self.nav_bar.setProperty("role", "glass")
        self.nav_bar.setFixedHeight(36)
        self.stage.nav_bar = self.nav_bar
        nav = QHBoxLayout(self.nav_bar)
        nav.setContentsMargins(6, 0, 6, 0)
        nav.setSpacing(2)

        self.brand_bar = QFrame(self.container)
        self.brand_bar.setObjectName("brandBar")
        self.brand_bar.setProperty("role", "glass")
        self.brand_bar.setFixedHeight(36)
        self.stage.brand_bar = self.brand_bar
        brand_layout = QHBoxLayout(self.brand_bar)
        brand_layout.setContentsMargins(12, 0, 12, 0)
        brand_layout.setSpacing(0)

        self.lbl_brand = QLabel()
        self.lbl_brand.setObjectName("brand")
        self.lbl_brand.setStyleSheet(
            f"color: {theme_mod.palette('dark', self.accent)['accent_text']}; font-weight: 800; font-size: 14px; "
            "letter-spacing: 1.6px; background: transparent;"
        )
        brand_layout.addWidget(self.lbl_brand)

        # Güncelleme düğmesi: sessiz denetim yeni sürüm bulursa görünür (pop-up yok)
        self.btn_update = QPushButton()
        self.btn_update.setProperty("role", "updatePill")
        self.btn_update.setFixedHeight(24)
        self.btn_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_update.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_update.clicked.connect(self._open_pending_update)
        self.btn_update.setVisible(False)
        nav.addWidget(self.btn_update)

        self.btn_settings = self._sys_button()
        self.btn_settings.clicked.connect(self.show_settings_menu)
        nav.addWidget(self.btn_settings)

        self.btn_min = self._sys_button()
        self.btn_min.clicked.connect(self.showMinimized)
        nav.addWidget(self.btn_min)

        self.btn_max = self._sys_button()
        self.btn_max.clicked.connect(self.toggle_maximize)
        nav.addWidget(self.btn_max)

        self.btn_close = self._sys_button()
        self.btn_close.setObjectName("closeBtn")
        self.btn_close.setText("✕")
        self.btn_close.clicked.connect(self.close)
        nav.addWidget(self.btn_close)

        self.file_chip = QFrame(self.container)
        self.file_chip.setObjectName("fileChip")
        self.file_chip.setProperty("role", "glass")
        self.file_chip.setFixedHeight(36)
        self.file_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stage.file_chip = self.file_chip
        chip_row = QHBoxLayout(self.file_chip)
        chip_row.setContentsMargins(12, 0, 12, 0)
        self.lbl_filename = QLabel("")
        self.lbl_filename.setObjectName("fileName")
        self.lbl_filename.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_filename.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lbl_filename.customContextMenuRequested.connect(self._show_file_chip_menu)
        self.file_chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_chip.customContextMenuRequested.connect(self._show_file_chip_menu)
        chip_row.addWidget(self.lbl_filename)
        self.file_chip.hide()

        def _on_chip_mouse_press(event):
            if event.button() == Qt.MouseButton.LeftButton:
                self._reveal_in_explorer()
                event.accept()
                return

        self.file_chip.mousePressEvent = _on_chip_mouse_press
        self.lbl_filename.mousePressEvent = _on_chip_mouse_press

        # "Yükleniyor" kapsülü videonun ortasında durur → kendi şeffaf üst penceresinde.
        self._busy_host = OverlayHost(self)
        self.busy_chip = QFrame()
        self.busy_chip.setObjectName("busyChip")
        self.busy_chip.setProperty("role", "glass")
        self._busy_host.adopt(self.busy_chip)
        self.stage.busy_chip = None
        busy_row = QHBoxLayout(self.busy_chip)
        busy_row.setContentsMargins(14, 8, 14, 8)
        self.lbl_busy = QLabel()
        self.lbl_busy.setObjectName("fileName")
        busy_row.addWidget(self.lbl_busy)
        self.busy_chip.hide()

        # Tam ekranda video (Direct3D11 swapchain) üstünde yüzen şeffaf üst pencereler.
        # DWM bu pencereleri gerçek alfa ile videoyla birleştirir:
        # - Alt kontrol barı tam ekranda asla kaybolmaz.
        # - Kapsüllerin yuvarlak köşeleri (radius) videoyla pürüzsüz karışır, siyah/beyaz leke oluşmaz.
        self._dock_host = OverlayHost(self)
        self._brand_host = OverlayHost(self)
        self._nav_host = OverlayHost(self)
        self._chip_host = OverlayHost(self)



        def _on_double_click_fs(event):
            if event.button() == Qt.MouseButton.LeftButton:
                self.toggle_fullscreen()
                event.accept()
                return

        def _on_double_click_max(event):
            if event.button() == Qt.MouseButton.LeftButton:
                self.toggle_maximize()
                event.accept()
                return

        self.nav_bar.mouseDoubleClickEvent = _on_double_click_max
        self.file_chip.mouseDoubleClickEvent = _on_double_click_max

        self.empty = EmptyState()
        self.empty.mouseDoubleClickEvent = _on_double_click_fs
        self.empty.btn_file.clicked.connect(self.select_file)
        self.empty.btn_folder.clicked.connect(self.select_folder)

        self.canvas = PhotoCanvas()
        self.canvas.activated.connect(self._on_canvas_activated)
        self.canvas.context_requested.connect(self.show_photo_menu)
        self.canvas.zoom_changed.connect(self._refresh_mode_buttons)
        self.canvas.double_clicked.connect(self._on_surface_double_clicked)

        self.player = LivePlayer(self)
        self.player.finished.connect(self._on_playback_finished)
        self.player.failed.connect(self._on_playback_failed)
        self.player.started.connect(self._on_playback_started)
        self.player.loading.connect(self._set_play_loading)
        self.player.progress.connect(self._on_play_progress)
        self.player.widget.clicked.connect(self._on_video_clicked)
        self.player.widget.context_requested.connect(self.show_photo_menu)
        self.player.widget.zoom_changed.connect(self._refresh_mode_buttons)
        self.player.widget.double_clicked.connect(self._on_surface_double_clicked)
        self.player.muted_changed.connect(self._refresh_audio_control)
        self.player.audio_available.connect(self._on_audio_available)
        self.player.set_audio_device(self.audio_device)

        self.stage.setMouseTracking(True)
        self.player.widget.setMouseTracking(True)
        self.canvas.setMouseTracking(True)

        drop = self.load_path
        self.stage.set_drop_handler(drop)
        self.canvas.set_drop_handler(drop)
        self.empty.set_drop_handler(drop)
        self.player.widget.set_drop_handler(drop)

        self.stage.stack.addWidget(self.empty)
        self.stage.stack.addWidget(self.canvas)
        self.stage.stack.addWidget(self.player.widget)
        self.canvas.set_fill_mode(self._view_fill)
        self.player.widget.remember_cover(self._view_fill)

        self.bottom_dock = BottomDock()
        self.dock_wrap = QWidget()
        self.dock_wrap.setObjectName("dockWrap")
        self.dock_wrap.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.dock_wrap.setAutoFillBackground(False)
        self.dock_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        dock_lay = QHBoxLayout(self.dock_wrap)
        dock_lay.setContentsMargins(10, 0, 10, 8)
        dock_lay.setSpacing(0)
        dock_lay.addWidget(self.bottom_dock, 0, Qt.AlignmentFlag.AlignHCenter)
        viewer_col.addWidget(self.dock_wrap)
        dock = self.bottom_dock.box

        self.btn_prev = QPushButton()
        self.btn_prev.setObjectName("btnPrev")
        self.btn_prev.setProperty("role", "nav")
        self.btn_prev.setFixedSize(34, 30)
        self.btn_prev.setIconSize(QSize(20, 20))
        self.btn_prev.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_prev.setAutoDefault(False)
        self.btn_prev.clicked.connect(self.prev_image)
        dock.addWidget(self.btn_prev)

        self.lbl_counter = QLabel("")
        self.lbl_counter.setObjectName("counter")
        self.lbl_counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_counter.setMinimumWidth(48)
        dock.addWidget(self.lbl_counter)

        self.btn_next = QPushButton()
        self.btn_next.setObjectName("btnNext")
        self.btn_next.setProperty("role", "nav")
        self.btn_next.setFixedSize(34, 30)
        self.btn_next.setIconSize(QSize(20, 20))
        self.btn_next.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_next.setAutoDefault(False)
        self.btn_next.clicked.connect(self.next_image)
        dock.addWidget(self.btn_next)

        self.btn_motion = make_live_orb(self.bottom_dock)
        self.btn_motion.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_motion.clicked.connect(self.toggle_live_playback)
        self.bottom_dock.orb = self.btn_motion
        dock.addWidget(self.btn_motion, 0, Qt.AlignmentFlag.AlignVCenter)

        self.btn_audio = self._icon_button(self.toggle_mute)
        self.btn_audio.setMouseTracking(True)
        self.btn_audio.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.btn_audio.installEventFilter(self)
        dock.addWidget(self.btn_audio)

        self.btn_rot_right = self._icon_button(lambda: self._rotate_current(-90))
        dock.addWidget(self.btn_rot_right)
        self.btn_zoom_out = self._icon_button(lambda: self._zoom_by(1 / 1.2))
        dock.addWidget(self.btn_zoom_out)
        self.btn_zoom_in = self._icon_button(lambda: self._zoom_by(1.2))
        dock.addWidget(self.btn_zoom_in)

        self.btn_fit = QPushButton()
        self.btn_fit.setProperty("role", "nav")
        self.btn_fit.setFixedHeight(30)
        self.btn_fit.setMinimumWidth(56)
        self.btn_fit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_fit.setAutoDefault(False)
        self.btn_fit.clicked.connect(self._on_fit_clicked)
        dock.addWidget(self.btn_fit)

        self.btn_fill = QPushButton()
        self.btn_fill.setProperty("role", "nav")
        self.btn_fill.setFixedHeight(30)
        self.btn_fill.setMinimumWidth(56)
        self.btn_fill.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_fill.setAutoDefault(False)
        self.btn_fill.clicked.connect(self._on_fill_clicked)
        dock.addWidget(self.btn_fill)

        self.btn_info = self._icon_button(self.toggle_details)
        dock.addWidget(self.btn_info)
        self.btn_trash = self._icon_button(self.delete_current)
        dock.addWidget(self.btn_trash)

        self.btn_full = self._icon_button(self.toggle_fullscreen)
        dock.addWidget(self.btn_full)
        self.btn_strip = self._icon_button(self.toggle_filmstrip)
        dock.addWidget(self.btn_strip)
        self.btn_strip.hide()

        self.volume_popup = VolumePopup(self)
        self.volume_popup.volume_changed.connect(self._on_volume_popup_changed)
        self.volume_popup.mute_clicked.connect(self.toggle_mute)
        self.volume_popup.installEventFilter(self)
        self.volume_popup.hide()
        self._vol_timer = QTimer(self)
        self._vol_timer.setSingleShot(True)
        self._vol_timer.setInterval(500)
        self._vol_timer.timeout.connect(self._check_hide_volume_popup)

        self.filmstrip = Filmstrip(self.bottom_dock)
        self.filmstrip.activated.connect(self._on_strip_activated)
        self.bottom_dock.add_filmstrip(self.filmstrip)
        self.seek_bar = self.bottom_dock.seek
        self.seek_bar.sliderPressed.connect(self._on_seek_pressed)
        self.seek_bar.sliderReleased.connect(self._on_seek_released)
        self.seek_bar.sliderMoved.connect(self._on_seek_moved)
        self.seek_bar.hover_ratio.connect(self._on_seek_hover)
        self.seek_bar.hover_ended.connect(self._hide_seek_preview)
        self._build_transport_controls()
        self.seek_preview = QFrame(self)
        self.seek_preview.setObjectName("seekPreview")
        self.seek_preview.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.seek_preview.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.seek_preview.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.seek_preview.setFixedSize(196, 128)
        prev_lay = QVBoxLayout(self.seek_preview)
        prev_lay.setContentsMargins(4, 4, 4, 4)
        prev_lay.setSpacing(2)
        self.seek_preview_image = QLabel()
        self.seek_preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.seek_preview_image.setFixedSize(188, 106)
        self.seek_preview_image.setStyleSheet(
            "QLabel { background: #141414; color: #e8e8e8; border-radius: 6px; font-size: 11px; }"
        )
        self.seek_preview_time = QLabel("0:00")
        self.seek_preview_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.seek_preview_time.setStyleSheet(
            "QLabel { color: #e8e8e8; background: transparent; font-size: 10px; font-weight: 700; }"
        )
        prev_lay.addWidget(self.seek_preview_image)
        prev_lay.addWidget(self.seek_preview_time)
        self.seek_preview.setStyleSheet(
            "QFrame#seekPreview { background: rgba(18,18,18,235); border: 1px solid rgba(255,255,255,40); "
            "border-radius: 10px; }"
        )
        self.seek_preview.hide()
        self._preview = SeekPreviewController(
            self,
            self.seek_preview,
            self.seek_preview_image,
            self.seek_preview_time,
            place=self._place_seek_preview,
            duration_ms=self._preview_duration,
        )
        self._refresh_icons()
        self._refresh_mode_buttons()

    def _icon_button(self, slot) -> QPushButton:
        btn = QPushButton()
        btn.setProperty("role", "icon")
        btn.setIconSize(QSize(19, 19))
        btn.setFixedSize(34, 30)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setAutoDefault(False)
        btn.clicked.connect(slot)
        return btn

    def _transport_button(self, slot) -> QPushButton:
        """Arama çubuğu satırındaki küçük denetim düğmesi."""
        btn = self._icon_button(slot)
        btn.setProperty("compact", "true")
        btn.setFixedSize(28, 24)
        btn.setIconSize(QSize(16, 16))
        return btn

    def _build_transport_controls(self) -> None:
        """Kare geri/ileri, hız ve döngü — yalnızca video/Canlı Fotoğraf arama çubuğuyla birlikte görünür."""
        self.btn_frame_back = self._transport_button(lambda: self._step_frame(-1))
        self.btn_frame_fwd = self._transport_button(lambda: self._step_frame(1))
        self.btn_speed = self._transport_button(self._show_speed_menu)
        self.btn_speed.setFixedSize(46, 24)
        self.btn_loop = self._transport_button(self._toggle_loop)
        # Ses/altyazı düğmesi yalnızca seçilecek parça varken görünür (bkz. _update_tracks_button).
        self.btn_tracks = self._transport_button(self._show_tracks_menu)
        self.btn_tracks.hide()
        for btn in (self.btn_frame_back, self.btn_frame_fwd, self.btn_speed, self.btn_loop, self.btn_tracks):
            self.bottom_dock.add_seek_widget(btn)
        self.player.speed_changed.connect(self._refresh_transport_controls)
        self.player.loop_changed.connect(self._refresh_transport_controls)
        self.player.set_loop(self._setting_bool("loop_video", False))
        self._init_media_actions()

    def _refresh_transport_controls(self, *_args) -> None:
        if not hasattr(self, "btn_loop"):
            return
        ink = self._toolbar_ink()
        self.btn_frame_back.setIcon(frame_step_icon(False, ink))
        self.btn_frame_fwd.setIcon(frame_step_icon(True, ink))
        self.btn_loop.setIcon(loop_icon(ink))
        self.btn_tracks.setIcon(tracks_icon(ink))
        self.btn_tracks.setToolTip(f"{self.t('tracks')} (V / B)")
        self.btn_frame_back.setToolTip(f"{self.t('frame_back')} (, / Ctrl+←)")
        self.btn_frame_fwd.setToolTip(f"{self.t('frame_fwd')} (. / Ctrl+→)")
        speed = self.player.speed
        self.btn_speed.setText(f"{speed:g}×")
        self.btn_speed.setToolTip(
            f"{self.t('speed')} ([ ] / < > / Ctrl+↑↓) · {self.t('speed_normal')}: Backspace"
        )
        self.btn_speed.setProperty("active", "true" if abs(speed - 1.0) > 1e-6 else "false")
        loop_on = bool(self.player.loop)
        self.btn_loop.setToolTip(f"{self.t('loop_on' if loop_on else 'loop_off')} (Ctrl+L)")
        self.btn_loop.setProperty("active", "true" if loop_on else "false")
        _polish(self.btn_speed)
        _polish(self.btn_loop)

    def _sys_button(self) -> QPushButton:
        btn = QPushButton()
        btn.setProperty("role", "sys")
        btn.setFixedSize(30, 26)
        btn.setIconSize(QSize(14, 14))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return btn

    def _resolved_theme(self) -> str:
        return resolve_theme(self.theme)

    def _toolbar_ink(self) -> QColor:
        return QColor(theme_mod.palette(self._resolved_theme(), self.accent)["ink"])

    def _refresh_icons(self) -> None:
        ink = self._toolbar_ink()
        self.btn_prev.setIcon(arrow_icon(True, ink))
        self.btn_next.setIcon(arrow_icon(False, ink))
        if hasattr(self, "btn_rot_left"):
            self.btn_rot_left.setIcon(rotate_icon(False, ink))
        self.btn_rot_right.setIcon(rotate_icon(True, ink))
        self.btn_zoom_out.setIcon(zoom_icon(False, ink))
        self.btn_zoom_in.setIcon(zoom_icon(True, ink))
        self.btn_full.setIcon(fullscreen_icon(ink))
        fs_key = "exit_fullscreen" if self.isFullScreen() else "fullscreen"
        self.btn_full.setToolTip(f"{self.t(fs_key)} (F11)")
        self.btn_full.setProperty("active", "true" if self.isFullScreen() else "false")
        _polish(self.btn_full)
        self.btn_info.setIcon(info_icon(ink))
        self.btn_trash.setIcon(trash_icon(ink))
        if hasattr(self, "btn_folder"):
            self.btn_folder.setIcon(folder_icon(ink))
        self._refresh_audio_control()
        self._refresh_strip_icon()
        self.btn_settings.setIcon(more_icon(ink))
        self.btn_min.setIcon(minimize_icon(ink))
        self._refresh_max_icon()
        self.details.set_close_icon(ink)
        self._refresh_live_icon()
        self._refresh_transport_controls()

    def _refresh_max_icon(self) -> None:
        ink = self._toolbar_ink()
        if self.isMaximized():
            self.btn_max.setIcon(restore_icon(ink))
        else:
            self.btn_max.setIcon(maximize_icon(ink))

    def _refresh_strip_icon(self) -> None:
        if not hasattr(self, "btn_strip"):
            return
        ink = self._toolbar_ink()
        self.btn_strip.setIcon(chevron_horizontal_icon(not self._strip_open, ink))
        key = "strip_hide" if self._strip_open else "strip_show"

        self.btn_strip.setToolTip(self.t(key))

    def _refresh_live_icon(self) -> None:
        if not hasattr(self, "btn_motion"):
            return
        self.btn_motion.setText("")
        self.btn_motion.setIcon(QIcon())
        loading = bool(self._opening_play)
        playing = bool(self._playing) and not loading
        if hasattr(self.btn_motion, "set_loading"):
            self.btn_motion.set_loading(loading)
        self.btn_motion.setProperty("playing", "true" if playing else "false")
        _polish(self.btn_motion)

    def _rotate_current(self, degrees: int = 90) -> None:
        if self._playing or (
            hasattr(self, "player")
            and self.player.widget.isVisible()
            and self.player.widget.has_content()
        ):
            self.player.widget.rotate_step(degrees)
        else:
            self.canvas.rotate_step(degrees)
            self._maybe_hint_rotate_save()

    def _refresh_audio_control(self) -> None:
        if not hasattr(self, "btn_audio"):
            return
        ink = self._toolbar_ink()
        muted = self.player.muted
        self.btn_audio.setIcon(volume_icon(muted, ink))
        tt = self.t("unmute" if muted else "mute")
        self.btn_audio.setToolTip(f"{tt} (M) · {self.player.volume}%")
        self.btn_audio.setProperty("muted", "true" if muted else "false")
        _polish(self.btn_audio)
        popup = getattr(self, "volume_popup", None)
        if popup is not None and popup.isVisible():
            popup.set_value(self.player.volume)
            popup.set_muted(muted)

    def _on_audio_available(self, has_audio: bool) -> None:
        # Ses kanalı olmayan dosyada ses düğmesini gösterme.
        self._has_audio = bool(has_audio)
        if not has_audio and hasattr(self, "volume_popup"):
            try:
                self.volume_popup.hide()
            except Exception:
                pass
        self._refresh_live_overlays()

    def toggle_mute(self) -> None:
        self.player.toggle_muted()
        if hasattr(self, "volume_popup"):
            self.volume_popup.set_value(self.player.volume)
        self._refresh_audio_control()

    def _show_volume_popup(self) -> None:
        if not hasattr(self, "volume_popup") or not hasattr(self, "btn_audio"):
            return
        self._vol_timer.stop()
        self.volume_popup.set_value(self.player.volume)
        self.volume_popup.set_muted(self.player.muted)
        center = self.btn_audio.mapToGlobal(QPoint(self.btn_audio.width() // 2, 0))
        px = center.x() - self.volume_popup.width() // 2
        py = center.y() - self.volume_popup.height() - 8
        self.volume_popup.move(px, py)
        self.volume_popup.show()
        self.volume_popup.raise_()
        self.volume_popup.update()

    def _check_hide_volume_popup(self) -> None:
        if not hasattr(self, "volume_popup") or not self.volume_popup.isVisible():
            return
        global_pos = QCursor.pos()
        if hasattr(self, "btn_audio"):
            from PyQt6.QtCore import QRect
            btn_tl = self.btn_audio.mapToGlobal(QPoint(0, 0))
            btn_rect = QRect(btn_tl, self.btn_audio.size()).adjusted(-6, -6, 6, 6)
            if btn_rect.contains(global_pos):
                return
        if self.volume_popup.frameGeometry().adjusted(-4, -4, 4, 4).contains(global_pos):
            return
        self.volume_popup.hide()

    def _on_volume_popup_changed(self, val: int) -> None:
        self.player.set_volume(val)
        self._refresh_audio_control()

    def _remember_view_mode(self, fill: bool) -> None:
        """Sığdır / Doldur seçimi hatırlanır (sonraki dosyalar ve sonraki açılış); 1:1 ve yakınlaştırma hatırlanmaz."""
        self._view_fill = bool(fill)
        self.settings.setValue("view_fill", self._view_fill)
        self.player.widget.remember_cover(self._view_fill)

    def _apply_view_mode(self) -> None:
        """Yeni bir dosya açılırken: yakınlaştırma/kaydırma sıfırlanır, ama son seçilen Sığdır/Doldur kipi korunur."""
        fill = bool(self._view_fill)
        self.canvas.set_fill_mode(fill)
        self.player.widget.reset_view()
        self.player.widget.remember_cover(fill)
        if fill:
            self.player.widget.set_cover(True)
        self._refresh_mode_buttons()

    def _on_fit_clicked(self) -> None:
        self._remember_view_mode(False)
        self.canvas.set_fill_mode(False)
        self.player.widget.reset_view()
        self._refresh_mode_buttons()

    def _on_fill_clicked(self) -> None:
        self._remember_view_mode(True)
        self.canvas.set_fill_mode(True)
        self.player.widget.set_cover(True)
        self._refresh_mode_buttons()

    def _on_actual_size_clicked(self) -> None:
        if self.stage.stack.currentWidget() is self.player.widget:
            self.player.widget.reset_view_to_actual()
        else:
            self.canvas.reset_view_to_actual()
        self._refresh_mode_buttons()

    def _zoom_by(self, factor: float) -> None:
        if self.stage.stack.currentWidget() is self.player.widget:
            self.player.widget.zoom_by(factor)
        else:
            self.canvas.zoom_by(factor)

    def _refresh_mode_buttons(self) -> None:
        if not hasattr(self, "btn_fit") or not hasattr(self, "btn_fill"):
            return
        is_video = self.stage.stack.currentWidget() is self.player.widget
        if is_video:
            zoomed = self.player.widget.zoomed()
            cover = bool(getattr(self.player.widget, "_cover", False))
            if zoomed:
                fit_active = False
                fill_active = False
            elif cover:
                fit_active = False
                fill_active = True
            else:
                fit_active = True
                fill_active = False
        else:
            fitted = bool(getattr(self.canvas, "_fitted", True))
            fill = bool(getattr(self.canvas, "_fill", False))
            if not fitted:
                fit_active = False
                fill_active = False
            elif fill:
                fit_active = False
                fill_active = True
            else:
                fit_active = True
                fill_active = False

        self.btn_fit.setProperty("active", "true" if fit_active else "false")
        self.btn_fill.setProperty("active", "true" if fill_active else "false")
        _polish(self.btn_fit)
        _polish(self.btn_fill)

    def apply_theme(self) -> None:
        resolved = self._resolved_theme()
        theme_mod.set_active(resolved, self.accent)
        pal = theme_mod.palette(resolved, self.accent)
        style = theme_mod.build_stylesheet(resolved, self.accent)
        self.setStyleSheet(style)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(style)
        if hasattr(self, "dock_wrap"):
            _polish(self.dock_wrap)
        if hasattr(self, "brand_bar"):
            _polish(self.brand_bar)
        if hasattr(self, "nav_bar"):
            _polish(self.nav_bar)
        if hasattr(self, "file_chip"):
            _polish(self.file_chip)
        self.lbl_brand.setStyleSheet(
            f"color: {pal['accent_text']}; font-weight: 800; font-size: 14px; "
            "letter-spacing: 1.6px; background: transparent;"
        )
        fill = QColor(pal["stage"])
        bg_hex = "#FF" + pal["stage"].lstrip("#").upper()
        if resolved == "dark":
            glass = QColor(14, 14, 14, 155)
            border = QColor(255, 255, 255, 50)
            thumb = QColor(36, 36, 36)
        else:
            glass = QColor(250, 250, 250, 185)
            border = QColor(200, 200, 200, 220)
            thumb = QColor(222, 222, 222)

        if hasattr(self, "player") and hasattr(self.player, "set_background_color"):
            self.player.set_background_color(bg_hex)
        elif hasattr(self, "player") and hasattr(self.player, "widget"):
            self.player.widget.set_fill(fill)

        if getattr(self, "_fs_mode_active", False):
            self._fullscreen_backdrop(True)
        self.bottom_dock.set_glass(glass, border)
        self.filmstrip.set_placeholder_color(thumb)
        self.details.apply_chrome(resolved == "dark")
        self._refresh_icons()
        self._refresh_mode_buttons()

    def _on_system_theme(self, *_args) -> None:
        if self.theme == "system":
            self.apply_theme()

    def retranslate_ui(self) -> None:
        install_qt_translations(self.lang)
        self.setWindowTitle(self.t("title"))
        self._apply_update_pill()
        if hasattr(self, "lbl_brand"):
            self._fit_brand_text(self.stage.width() or 10_000)
            if hasattr(self, "brand_bar"):
                self.brand_bar.adjustSize()
        self.btn_fit.setText(self.t("fit"))

        self.btn_fill.setText(self.t("fill"))
        self.btn_fit.setToolTip(f"{self.t('fit')} (0)")
        self.btn_fill.setToolTip(self.t("fill"))
        self.btn_settings.setToolTip(self.t("settings"))
        self.btn_prev.setToolTip(f"{self.t('prev')} (← / A)")
        self.btn_next.setToolTip(f"{self.t('next')} (→ / D)")
        if hasattr(self, "btn_rot_left"):
            self.btn_rot_left.setToolTip(f"{self.t('rot_left')} (L / Ctrl+Shift+R)")
        self.btn_rot_right.setToolTip(f"{self.t('rot_right')} (R / Ctrl+R)")
        self.btn_zoom_in.setToolTip(f"{self.t('zoom_in')} (+)")
        self.btn_zoom_out.setToolTip(f"{self.t('zoom_out')} (-)")
        self.btn_full.setToolTip(f"{self.t('fullscreen')} (F11)")
        self.btn_info.setToolTip(f"{self.t('info')} (I / Alt+Enter)")
        self.btn_trash.setToolTip(f"{self.t('delete')} (Del / Shift+Del)")
        self._refresh_transport_controls()
        if hasattr(self, "btn_folder"):
            self.btn_folder.setToolTip(f"{self.t('open_dir')} (Ctrl+E) · {self.t('open_folder')} (Ctrl+Shift+O)")
        self.empty.title.setText(self.t("empty_title"))
        self.empty.sub.setText(self.t("empty_sub"))
        self.empty.btn_file.setText(self.t("open_file"))
        self.empty.btn_folder.setText(self.t("open_folder"))
        self.lbl_busy.setText(self.t("loading"))
        self.details.btn_close.setToolTip(self.t("close_details"))
        self.details.set_heading(self.t("details_title"))
        self.details.set_field_labels(
            {
                "file": self.t("d_file"),
                "pixels": self.t("d_pixels"),
                "size": self.t("d_size"),
                "type": self.t("d_type"),
                "created": self.t("d_created"),
                "modified": self.t("d_modified"),
                "duration": self.t("d_duration"),
                "fps": self.t("d_fps"),
                "video_bitrate": self.t("d_video_bitrate"),
                "total_bitrate": self.t("d_total_bitrate"),
                "audio_bitrate": self.t("d_audio_bitrate"),
                "audio_channels": self.t("d_audio_channels"),
                "audio_samplerate": self.t("d_audio_samplerate"),
                "video_codec": self.t("d_video_codec"),
                "audio_codec": self.t("d_audio_codec"),
                "camera": self.t("d_camera"),
                "lens": self.t("d_lens"),
                "taken": self.t("d_taken"),
                "iso": self.t("d_iso"),
                "aperture": self.t("d_aperture"),
                "exposure": self.t("d_exposure"),
                "focal": self.t("d_focal"),
                "flash": self.t("d_flash"),
                "color_space": self.t("d_color_space"),
                "gps": self.t("d_gps"),
                "motion": self.t("d_motion"),
                "protocol": self.t("d_protocol"),
                "folder": self.t("d_folder"),
                "note": self.t("d_note"),
                "sec_general": self.t("sec_general"),
                "sec_video": self.t("sec_video"),
                "sec_audio": self.t("sec_audio"),
                "sec_camera": self.t("sec_camera"),
                "sec_location": self.t("sec_location"),
                "sec_histogram": self.t("sec_histogram"),
                "open_in_maps": self.t("open_in_maps"),
                "open_dir": self.t("open_dir"),
            }
        )
        self._refresh_icons()
        self._refresh_live_overlays()
        self._refresh_mode_buttons()
        self._refresh_strip_icon()

    def _fit_brand_text(self, stage_width: int) -> None:
        """Dar pencerede marka kapsülü yalnızca 'LUMINA' olur (dosya adına yer açar)."""
        full = f"LUMINA · {self.t('live_photo_title')}"
        text = full if stage_width >= 760 else "LUMINA"
        if self.lbl_brand.text() != text:
            self.lbl_brand.setText(text)

    def _set_chip_name(self, name: str) -> None:
        self._chip_name = name
        self._apply_chip_elision()

    def _apply_chip_elision(self, max_text_width: int = 10_000) -> None:
        """Uzun dosya adını ortadan kısalt (uzantı korunur): 'Squadron 42 - Sta…22.25.jxr'."""
        name = getattr(self, "_chip_name", "")
        text = ""
        if name:
            fm = self.lbl_filename.fontMetrics()
            text = fm.elidedText(name, Qt.TextElideMode.ElideMiddle, max(48, int(max_text_width)))
        if self.lbl_filename.text() != text:
            self.lbl_filename.setText(text)

    def _fit_chip_x(self, lo: int, hi: int, center: int) -> tuple[int, int]:
        """Dosya adı kapsülünü [lo, hi] aralığına sığdır (marka ve pencere düğmeleriyle çakışmaz).

        Ortalanır; sığmıyorsa aralığa kaydırılır, yine sığmıyorsa ad kısaltılır.
        """
        avail = max(72, hi - lo)
        margins = self.file_chip.layout().contentsMargins()
        self._apply_chip_elision(avail - margins.left() - margins.right() - 4)
        self.file_chip.setMaximumWidth(avail)
        self.file_chip.adjustSize()
        width = min(self.file_chip.width(), avail)
        x = max(lo, min(center - width // 2, hi - width))
        return x, width

    def _sync_chrome(self) -> None:
        loaded = bool(self.image_files) and self.current_idx >= 0
        self.bottom_dock.setVisible(loaded)
        self.dock_wrap.setVisible(loaded)
        self.nav_bar.setVisible(True)
        self.brand_bar.setVisible(True)
        self.file_chip.setVisible(loaded and bool(self.lbl_filename.text()))
        if loaded:
            self._sync_filmstrip()
        else:
            self._set_chip_name("")
            self.lbl_counter.setText("")
            self.stage.stack.setCurrentWidget(self.empty)
            self.filmstrip.clear_strip()
            self.bottom_dock.set_split(False)
            self.details.show_path(None)
            self._refresh_live_overlays()
        self.details.setVisible(self._details_on and loaded)
        if self.isFullScreen():
            self._layout_overlay_dock()
        self.stage.layout_chrome()

    def _sync_filmstrip(self) -> None:
        many = self._folder_ready and len(self.image_files) > 1
        if many and self._frame_pending:
            # Şerit = her kardeş için küçük resim çözme + Live rozet taraması; ilk kareyle yarışmasın.
            self._after_frame(self._sync_filmstrip)
            return
        self.btn_strip.setVisible(many)
        split = many and self._strip_open
        if not many:
            self.filmstrip.clear_strip()
            self.bottom_dock.set_split(False)
            self._refresh_strip_icon()
            if not self.isFullScreen():
                self._layout_window_dock()
            else:
                self._layout_overlay_dock()
            return
        self.filmstrip.set_window(self.image_files, self.current_idx)
        self.filmstrip.setVisible(split)
        self.bottom_dock.set_split(split)
        self._refresh_strip_icon()
        if not self.isFullScreen():
            self._layout_window_dock()
        else:
            self._layout_overlay_dock()

    def _bind_shortcuts(self) -> None:
        def bind(key, slot) -> None:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            # Kırpma kipinde tuşlar (Delete, ok tuşları…) çerçeveyi değil dosyayı etkilemesin.
            shortcut.activated.connect(lambda: None if self._crop_active else slot())

        # Navigasyon / video atlama
        bind(Qt.Key.Key_Left, lambda: self._nav_or_seek(-1, 5000))
        bind(Qt.Key.Key_Right, lambda: self._nav_or_seek(1, 5000))
        bind(QKeySequence("Shift+Left"), lambda: self._skip_video(-15000))
        bind(QKeySequence("Shift+Right"), lambda: self._skip_video(15000))
        bind(Qt.Key.Key_A, self.prev_image)
        bind(Qt.Key.Key_D, self.next_image)
        bind(Qt.Key.Key_Home, self.first_image)
        bind(Qt.Key.Key_End, self.last_image)

        # Oynatma & Canlı Fotoğraf
        bind(Qt.Key.Key_Space, self.toggle_live_playback)
        # Kare kare: , / . (mpv, YouTube) ve Ctrl+←/→ (oklarla ±5 sn atlamanın ince ayarı)
        bind(Qt.Key.Key_Comma, lambda: self._step_frame(-1))
        bind(Qt.Key.Key_Period, lambda: self._step_frame(1))
        bind(QKeySequence("Ctrl+Left"), lambda: self._step_frame(-1))
        bind(QKeySequence("Ctrl+Right"), lambda: self._step_frame(1))
        # Hız: [ ] (mpv/VLC), < > (YouTube; Türkçe klavyede ayrı < tuşu), Ctrl+↑/↓ (AltGr gerektirmez)
        bind(Qt.Key.Key_BracketLeft, lambda: self._step_speed(-1))
        bind(Qt.Key.Key_BracketRight, lambda: self._step_speed(1))
        bind(Qt.Key.Key_Less, lambda: self._step_speed(-1))
        bind(Qt.Key.Key_Greater, lambda: self._step_speed(1))
        bind(QKeySequence("Ctrl+Down"), lambda: self._step_speed(-1))
        bind(QKeySequence("Ctrl+Up"), lambda: self._step_speed(1))
        bind(Qt.Key.Key_Backspace, self._reset_speed)
        bind(QKeySequence("Ctrl+L"), self._toggle_loop)
        # Ses / altyazı döngüsü (VLC: B / V) ve kare kaydı (mpv: S)
        bind(Qt.Key.Key_V, self._cycle_subtitle)
        bind(Qt.Key.Key_B, self._cycle_audio)
        bind(Qt.Key.Key_S, self._save_frame)

        # Silme & Geri Alma
        bind(Qt.Key.Key_Delete, self.delete_current)
        bind(QKeySequence("Shift+Del"), self.permanently_delete_current)
        bind(QKeySequence.StandardKey.Undo, self.undo_delete)

        # Döndürme
        bind(QKeySequence("Ctrl+R"), lambda: self._rotate_current(90))
        bind(QKeySequence("R"), lambda: self._rotate_current(90))
        bind(QKeySequence("Ctrl+Shift+R"), lambda: self._rotate_current(-90))
        bind(QKeySequence("L"), lambda: self._rotate_current(-90))

        # Yakınlaştırma & Görünüm Boyutları
        bind(QKeySequence("0"), self._on_fit_clicked)
        bind(QKeySequence("Ctrl+0"), self._on_fit_clicked)
        bind(QKeySequence("1"), self._on_actual_size_clicked)
        bind(QKeySequence("Ctrl+1"), self._on_actual_size_clicked)
        bind(QKeySequence("+"), lambda: self._zoom_by(1.2))
        bind(QKeySequence("="), lambda: self._zoom_by(1.2))
        bind(QKeySequence("-"), lambda: self._zoom_by(1 / 1.2))

        # Tam Ekran, Slayt Gösterisi, Ayrıntılar
        bind(Qt.Key.Key_F11, self.toggle_fullscreen)
        bind(Qt.Key.Key_F5, self.toggle_slideshow)
        bind(QKeySequence("I"), self.toggle_details)
        bind(QKeySequence("Ctrl+I"), self.toggle_details)
        bind(QKeySequence("Alt+Return"), self.toggle_details)
        bind(QKeySequence("Alt+Enter"), self.toggle_details)

        # Dosya İşlemleri
        bind(Qt.Key.Key_F2, self.rename_current)
        bind(QKeySequence("Ctrl+S"), self.save_current_as)
        bind(QKeySequence("Ctrl+P"), self._print_image)
        bind(QKeySequence("Ctrl+C"), self._copy_file)
        bind(QKeySequence("Ctrl+Shift+C"), self._copy_image)
        bind(QKeySequence("Ctrl+O"), self.select_file)
        bind(QKeySequence("Ctrl+Shift+O"), self.select_folder)
        bind(QKeySequence("Ctrl+E"), self._reveal_in_explorer)
        bind(QKeySequence("Ctrl+,"), self.open_settings_dialog)
        bind(QKeySequence("Ctrl+Q"), self.quit_application)
        bind(QKeySequence("Alt+F4"), self.close)
        bind(Qt.Key.Key_F1, self._show_about)

    def toggle_filmstrip(self) -> None:
        if len(self.image_files) <= 1:
            return
        self._strip_open = not self._strip_open
        self.settings.setValue("strip_open", self._strip_open)
        self.filmstrip.setVisible(self._strip_open)
        self.bottom_dock.set_split(self._strip_open)
        self._refresh_strip_icon()
        if not self.isFullScreen():
            self._layout_window_dock()

    def set_chrome_side(self, side: str) -> None:
        if side not in {"left", "right"}:
            return
        self.chrome_side = side
        self.stage.chrome_side = side
        self.settings.setValue("chrome_side", side)
        self.stage.layout_chrome()

    def _refresh_live_overlays(self) -> None:
        live = self.current_match is not None
        loaded = self.current_idx >= 0
        show = live and loaded
        self.bottom_dock.sync_orb(show)
        self.btn_audio.setVisible(show and self._has_audio)
        self._sync_seek_bar()
        self._refresh_live_icon()
        self._refresh_audio_control()
        if hasattr(self, "lbl_brand"):
            self._fit_brand_text(self.stage.width() or 10_000)
            if hasattr(self, "brand_bar"):
                self.brand_bar.adjustSize()
        if show:
            tip = self.t("pause") if self._playing else self.t("play_hint")
            self.btn_motion.setToolTip(tip)

        _polish(self.btn_motion)
        if not (self.isFullScreen() and getattr(self, "_cursor_blank", False)):
            self._ensure_chrome_visible()
            if not self.isFullScreen():
                QTimer.singleShot(0, self._layout_window_dock)
        else:
            self.stage.layout_chrome()

    # --- dosya ---
    def load_path(self, target_path: str | Path) -> None:
        startup_trace.mark("load_path")
        target = Path(target_path)
        if not target.exists():
            return
        if target.is_file() and target.suffix.lower() in VIDEO_EXTS:
            still = still_for_video(target)
            if still is not None:
                target = still
        self._scan_token += 1
        if target.is_file():
            self.image_files = [target]
            self.current_idx = 0
            self._folder_ready = False
            self._sync_chrome()
            self.display_current()
            self._start_folder_scan(target.parent, target.name)
            return
        self.image_files = []
        self.current_idx = -1
        self._folder_ready = False
        self._sync_chrome()
        self._start_folder_scan(target, "")

    def _watch_folder(self, folder_str: str) -> None:
        try:
            if self._watched_folder and self._watched_folder in self._folder_watcher.directories():
                self._folder_watcher.removePath(self._watched_folder)
        except Exception:
            pass
        self._watched_folder = folder_str
        try:
            if folder_str and Path(folder_str).is_dir():
                self._folder_watcher.addPath(folder_str)
        except Exception:
            pass

    def _on_directory_changed(self, _path: str) -> None:
        # 300ms debounce ile dosya kopyalama/silme işlemlerinde listeyi ve sayaçları güncelle
        self._dir_watch_timer.start(300)

    def _rescan_active_folder(self) -> None:
        if not self._watched_folder or not Path(self._watched_folder).is_dir():
            return
        shown = self.current_file()
        current_name = shown.name if shown else ""
        self._scan_token += 1
        worker = FolderScanWorker(self._watched_folder, current_name, self._scan_token, self.sort_by)
        worker.signals.ready.connect(self._on_folder_listed)
        worker.signals.failed.connect(self._on_folder_scan_failed)
        self.pool.start(worker)

    def _start_folder_scan(self, folder: Path, current_name: str) -> None:
        self._watch_folder(str(folder))
        token = self._scan_token
        gate = threading.Event()
        if not self._frame_pending:
            gate.set()
        self._refine_gate = gate
        worker = FolderScanWorker(
            str(folder), current_name, token, self.sort_by,
            progressive=True, is_current=lambda: token == self._scan_token, refine_gate=gate,
        )
        worker.signals.ready.connect(self._on_folder_listed)
        worker.signals.failed.connect(self._on_folder_scan_failed)
        self.pool.start(worker)

    def _on_folder_listed(self, _folder: str, paths: list, index: int, token: int) -> None:
        if token != self._scan_token:
            return
        startup_trace.mark("folder_listed")
        self._watch_folder(_folder)
        shown = self.current_file()
        old_files, old_idx = self.image_files, self.current_idx
        was_ready = self._folder_ready
        self.image_files = [Path(path) for path in paths]
        self._folder_ready = True
        if not self.image_files:
            self.current_idx = -1
            self._sync_chrome()
            return
        if shown is not None:
            if was_ready:
                # Gösterilen dosya dışarıdan silinmişse konum korunur (sonraki, yoksa önceki).
                self.current_idx = index_after_change(old_files, old_idx, self.image_files)
            else:
                self.current_idx = index_in_folder(self.image_files, shown)
        else:
            self.current_idx = max(0, min(index, len(self.image_files) - 1))
        same = (
            shown is not None
            and self.current_file() is not None
            and shown.name.lower() == self.current_file().name.lower()
        )
        self._sync_chrome()
        self.lbl_counter.setText(f"{self.current_idx + 1}  /  {len(self.image_files)}")
        self.btn_prev.setEnabled(self.current_idx > 0)
        self.btn_next.setEnabled(self.current_idx < len(self.image_files) - 1)
        if not same:
            self.display_current()
        else:
            self._preload_adjacent()

    def _on_folder_scan_failed(self, _folder: str, token: int) -> None:
        if token != self._scan_token:
            return
        self._folder_ready = True
        self._sync_chrome()

    def _sync_seek_bar(self) -> None:
        show = self.current_match is not None and self.current_idx >= 0
        self.bottom_dock.set_seek_visible(show)
        is_long = bool(self.current_match is not None and self.current_match.source == "video")
        self.bottom_dock.set_video_mode(is_long)
        if not show:
            self._scrubbing = False
            self._pending_seek = None
            return
        # Storyboard oynatma/decode ile yarışmamalı. İlk hover'da poster veya
        # on-demand kare gösterilir; tam storyboard yalnızca video duraklayınca
        # hazırlanır.

    def _video_can_seek(self) -> bool:
        return bool(
            self.current_match is not None
            and (self._playing or self._opening_play or self.player.is_paused)
        )

    def _skip_video(self, delta_ms: int) -> bool:
        if self._video_can_seek():
            self.player.skip(delta_ms)
            return True
        return self._seek_finished_video(delta_ms)

    def _seek_finished_video(self, delta_ms: int) -> bool:
        """Sonuna kadar oynayan bağımsız videoda son kare ekranda kalır: oklar dosya değiştirmek yerine
        sarar, oynatıcı oradan duraklatılmış olur (Boşluk devam ettirir). Live Photo bitince fotoğrafa
        döndüğünden onda oklar yine gezinir."""
        if self.current_match is None or self._standalone_video_path() is None:
            return False
        if not self.player.seek_from_end(delta_ms):
            return False
        if self.stage.stack.currentWidget() is not self.player.widget:
            self.stage.stack.setCurrentWidget(self.player.widget)
        self._refresh_live_overlays()
        return True

    def _transport_ready(self) -> bool:
        return self.current_match is not None and self.current_idx >= 0

    def _step_frame(self, direction: int) -> None:
        """Bir kare ileri/geri. Oynatma sürüyorsa önce duraklatılır (Boşluk ile devam edilir)."""
        if not self._video_can_seek() and not self._seek_finished_video(0):
            return
        if self.player.step_frame(direction) and self._playing and self.player.is_paused:
            self._playing = False
            self._preview.set_background(False)
            self._refresh_live_overlays()

    def _step_speed(self, direction: int) -> None:
        if self._transport_ready():
            self.player.step_speed(direction)

    def _reset_speed(self) -> None:
        if self._transport_ready():
            self.player.reset_speed()

    def _toggle_loop(self) -> None:
        if not self._transport_ready():
            return
        enabled = not self.player.loop
        self.player.set_loop(enabled)
        self.settings.setValue("loop_video", enabled)

    def _fill_speed_menu(self, menu: QMenu) -> None:
        current = self.player.speed
        for value in SPEED_STEPS:
            label = f"{value:g}×"
            if abs(value - 1.0) < 1e-6:
                label += f"  ·  {self.t('speed_normal')}"
            act = QAction(label, menu)
            act.setCheckable(True)
            act.setChecked(abs(value - current) < 1e-6)
            act.triggered.connect(lambda _checked=False, v=value: self.player.set_speed(v))
            menu.addAction(act)

    def _show_speed_menu(self) -> None:
        menu = QMenu(self)
        self._fill_speed_menu(menu)
        top_left = self.btn_speed.mapToGlobal(QPoint(0, 0))
        menu.exec(QPoint(top_left.x(), top_left.y() - menu.sizeHint().height() - 4))

    def _nav_or_seek(self, direction: int, step_ms: int) -> None:
        if self._skip_video(direction * step_ms):
            return
        if direction < 0:
            self.prev_image()
        else:
            self.next_image()

    def _on_play_progress(self, pos: int, dur: int) -> None:
        if self._scrubbing:
            return
        if self.current_match is None:
            return
        if self._playing and self.stage.stack.currentWidget() is not self.player.widget:
            self.stage.stack.setCurrentWidget(self.player.widget)
        if self.busy_chip.isVisible() and pos > 0:
            self._set_play_loading(False)
            if self.stage.stack.currentWidget() is not self.player.widget:
                self.stage.stack.setCurrentWidget(self.player.widget)

        bar = self.seek_bar
        bar.blockSignals(True)
        bar.setRange(0, max(1, int(dur)))
        bar.setValue(min(max(0, int(pos)), max(1, int(dur))))
        bar.blockSignals(False)
        # Saniyede 1 kereden fazla metin boyaması yapıp CPU harcamayı engelle
        sec = int(pos // 1000)
        dur_sec = int(dur // 1000)
        if getattr(self, "_last_progress_sec", -1) != sec or getattr(self, "_last_progress_dur_sec", -1) != dur_sec:
            self._last_progress_sec = sec
            self._last_progress_dur_sec = dur_sec
            self.bottom_dock.set_seek_times(pos, dur)

    def _on_seek_pressed(self) -> None:
        self._scrubbing = True

    def _on_seek_moved(self, value: int) -> None:
        if not self._video_can_seek():
            return
        # Zamansal debounce: slider hızlı sürüklenirken her hareket seek gönderme.
        # 50ms sonra en son değerle tek bir seek gönder — CPU kullanımını düşürür.
        self._pending_seek_value = int(value)
        self.bottom_dock.set_seek_times(value, self.player.duration())
        if not hasattr(self, "_seek_debounce_timer"):
            from PyQt6.QtCore import QTimer as _QTimer
            self._seek_debounce_timer = _QTimer(self)
            self._seek_debounce_timer.setSingleShot(True)
            self._seek_debounce_timer.timeout.connect(self._flush_seek_debounce)
        self._seek_debounce_timer.start(50)

    def _flush_seek_debounce(self) -> None:
        val = getattr(self, "_pending_seek_value", None)
        if val is not None and self._video_can_seek():
            self.player.seek(val, exact=False)


    def _on_seek_released(self) -> None:
        value = int(self.seek_bar.value())
        self._scrubbing = False
        # Bekleyen yaklaşık (exact=False) seek, aşağıdaki kesin seek'ten sonra
        # tetiklenip konumu keyframe'e kaydırmasın.
        timer = getattr(self, "_seek_debounce_timer", None)
        if timer is not None:
            timer.stop()
        self._pending_seek_value = None
        if self._video_can_seek():
            self.player.seek(value, exact=True)
            return
        if self.current_match is not None:
            self._pending_seek = value
            self.toggle_live_playback()

    def _preview_duration(self) -> int:
        return int(self.player.duration() or self.seek_bar.maximum() or 1)

    def _sync_preview_media(self) -> None:
        """Geçerli videoyu önizleme denetleyicisine bildir (aynı dosyaysa bir şey yapmaz)."""
        match = self.current_match
        path = match.video_path if match is not None else None
        if not path:
            self._preview.release_media(wait=False)
            return
        bar_max = int(self.seek_bar.maximum() or 0)
        self._preview.set_media(str(path), bar_max if bar_max > 1000 else 0)

    def _on_seek_hover(self, ratio: float) -> None:
        if self.current_match is None:
            return
        self._sync_preview_media()
        self._preview.on_hover(ratio)

    def _place_seek_preview(self, ratio: float) -> None:
        try:
            bar = self.seek_bar
            if not bar.isVisible() or bar.width() <= 0:
                return
            gx = bar.mapToGlobal(QPoint(int(ratio * bar.width()), 0))
            px = gx.x() - self.seek_preview.width() // 2
            py = bar.mapToGlobal(QPoint(0, 0)).y() - self.seek_preview.height() - 10
            # Ekran/pencere sınırlarına klemple
            if self.window() is not None:
                win_geo = self.window().geometry()
                px = max(win_geo.left() + 8, min(px, win_geo.right() - self.seek_preview.width() - 8))
            self.seek_preview.move(px, py)
        except Exception:
            pass

    def _hide_seek_preview(self) -> None:
        self._preview.hide()

    def _open_media(self, path: str) -> None:
        self.load_path(path)

    def open_new_window(self) -> None:
        from app.ui.session import spawn_window

        spawn_window()

    def _on_strip_activated(self, index: int) -> None:
        if 0 <= index < len(self.image_files) and index != self.current_idx:
            self.current_idx = index
            self.display_current()

    def select_file(self) -> None:
        curr = self.current_file()
        start_dir = str(curr.parent) if curr is not None and curr.parent.is_dir() else ""
        exts_str = " ".join(f"*{ext}" for ext in sorted(IMAGE_EXTS | VIDEO_EXTS))
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.t("open_file"),
            start_dir,
            f"{self.t('filter_media').format(exts_str)};;{self.t('filter_all_files')}",
        )
        if path:
            self._open_media(path)

    def select_folder(self) -> None:
        curr = self.current_file()
        start_dir = str(curr.parent) if curr is not None and curr.parent.is_dir() else ""
        folder = QFileDialog.getExistingDirectory(self, self.t("open_folder"), start_dir)
        if folder:
            # Klasör seçimi mevcut pencerenin içeriğini değiştirir;
            # dosya açmadaki yeni pencere davranışına sapmamalı.
            self.load_path(folder)

    def set_sort_mode(self, mode: str) -> None:
        if mode not in {"date_asc", "date_desc", "name_asc", "name_desc"}:
            return
        if self.sort_by == mode:
            return
        self.sort_by = mode
        self.settings.setValue("sort_by", mode)
        curr = self.current_file()
        if curr is not None and curr.parent.is_dir():
            self._scan_token += 1
            self._start_folder_scan(curr.parent, curr.name)

    def prev_image(self) -> None:
        if self._dir_watch_timer.isActive():
            self._dir_watch_timer.stop()
            self._rescan_active_folder()
        if self.image_files and self.current_idx > 0:
            self.current_idx -= 1
            self.display_current()

    def next_image(self) -> None:
        if self._dir_watch_timer.isActive():
            self._dir_watch_timer.stop()
            self._rescan_active_folder()
        if self.image_files and self.current_idx < len(self.image_files) - 1:
            self.current_idx += 1
            self.display_current()

    def first_image(self) -> None:
        if self.image_files and self.current_idx != 0:
            self.current_idx = 0
            self.display_current()

    def last_image(self) -> None:
        if self.image_files and self.current_idx != len(self.image_files) - 1:
            self.current_idx = len(self.image_files) - 1
            self.display_current()

    def toggle_slideshow(self) -> None:
        if self._slideshow_timer.isActive():
            self._stop_slideshow()
        else:
            self._start_slideshow()

    def _start_slideshow(self) -> None:
        if not self.image_files:
            return
        if not self.isFullScreen():
            self.showFullScreen()
        self._slideshow_timer.start(3500)

    def _stop_slideshow(self) -> None:
        if self._slideshow_timer.isActive():
            self._slideshow_timer.stop()

    def _slideshow_step(self) -> None:
        if not self.image_files:
            self._stop_slideshow()
            return
        if self.current_idx >= len(self.image_files) - 1:
            self.current_idx = 0
        else:
            self.current_idx += 1
        self.display_current()

    def current_file(self) -> Path | None:
        if 0 <= self.current_idx < len(self.image_files):
            return self.image_files[self.current_idx]
        return None

    def _preload_adjacent(self) -> None:
        """Hızlı gezinme için sonraki ve önceki kareleri arka planda RAM önbelleğine al.

        Geçerli kare henüz çizilmediyse beklenir (komşuları çözmek ilk kareyle yarışmasın). Önbellek
        denetimi GUI thread'inde dosyaya dokunmaz; tazelik (mtime) işçide doğrulanır.
        """
        if self._frame_pending:
            self._after_frame(self._preload_adjacent)
            return
        if not self._folder_ready or not self.image_files:
            return
        for delta in (1, -1, 2):
            idx = self.current_idx + delta
            if 0 <= idx < len(self.image_files):
                p = self.image_files[idx]
                suf = p.suffix.lower()
                if suf not in VIDEO_EXTS and suf != ".livp" and suf != ".gif":
                    if not has_cached_entry(p):
                        worker = ImageLoadWorker(str(p), token=-1)
                        self.pool.start(worker)

    # --- ilk kare önceliği ---
    def _arm_frame_gate(self) -> None:
        """Geçerli görüntü ekrana gelene dek yan işleri (Live araması, şerit, ön yükleme, EXIF sıralaması)
        beklet: ağ/WSL yolunda ve meşgul CPU'da bunlar ilk kareyle aynı diski/CPU'yu paylaşıyordu."""
        self._frame_pending = True
        self._frame_gate_timer.start(FRAME_GATE_MAX_MS)

    def _after_frame(self, fn) -> None:
        if not self._frame_pending:
            fn()
            return
        if fn not in self._after_frame_queue:
            self._after_frame_queue.append(fn)

    def _reset_frame_gate(self) -> None:
        """Yeni dosya: önceki dosya için bekletilen işler atılır (yeni gösterim kendi işlerini kurar)."""
        self._after_frame_queue = []
        self._frame_pending = False
        self._frame_gate_timer.stop()

    def _release_frame_gate(self) -> None:
        refine = getattr(self, "_refine_gate", None)
        if refine is not None:
            refine.set()
        if not self._frame_pending:
            return
        self._frame_pending = False
        self._frame_gate_timer.stop()
        queue, self._after_frame_queue = self._after_frame_queue, []
        for fn in queue:
            fn()

    def display_current(self) -> None:
        self._cancel_crop()
        self._autoplay_armed = True
        self._stop_playback(release=True)
        self._reset_frame_gate()
        path = self.current_file()
        if path is None:
            self.current_match = None
            self.canvas.clear_image()
            self._sync_chrome()
            return

        self._apply_view_mode()
        chip_tip = f"{path.name}\n{path.parent}\n({self.t('reveal')} - Ctrl+E)"
        self._set_chip_name(path.name)
        self.lbl_filename.setToolTip(chip_tip)
        self.file_chip.setToolTip(chip_tip)
        self.file_chip.setVisible(True)
        if self._folder_ready:
            self.lbl_counter.setText(f"{self.current_idx + 1}  /  {len(self.image_files)}")
            self.btn_prev.setEnabled(self.current_idx > 0)
            self.btn_next.setEnabled(self.current_idx < len(self.image_files) - 1)
        else:
            self.lbl_counter.setText("…")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
        self._sync_filmstrip()

        display_path = path
        if path.suffix.lower() == ".livp":
            self.bottom_dock.set_video_mode(False)
            pair = extract_livp(path, self.temps)
            if pair:
                still, video = pair
                display_path = still
                self.current_match = LiveMatch(video, True, "livp")
            else:
                self.current_match = None
        elif path.suffix.lower() in VIDEO_EXTS:
            self.bottom_dock.set_video_mode(True)
            self.current_match = LiveMatch(str(path), False, "video")
            self._display_path = path
            self._has_audio = True
            self._load_token += 1
            worker = VideoPosterWorker(str(path), self._load_token)
            worker.signals.loaded.connect(self._on_image_loaded)
            worker.signals.meta.connect(self._on_poster_meta)
            worker.signals.failed.connect(self._on_poster_failed)
            self.pool.start(worker)
            self.stage.stack.setCurrentWidget(self.canvas)
            self._refresh_live_overlays()
            self._refresh_details()
            self.stage.layout_chrome()
            self._sync_preview_media()
            self._maybe_autoplay()
            return
        elif path.suffix.lower() == ".gif":
            self.bottom_dock.set_video_mode(False)
            self.current_match = None
            self.canvas.set_gif(str(path))
            self.stage.stack.setCurrentWidget(self.canvas)
            self._refresh_live_overlays()
            self._refresh_details()
            self.stage.layout_chrome()
            return
        else:
            self.bottom_dock.set_video_mode(False)

        # Önce görüntünün kendisi: Live/Motion eşi araması dosyayı birkaç kez daha okur (1–24 MiB
        # pencereler + yan dosya denetimleri); kare çizilince başlar (en geç FRAME_GATE_MAX_MS sonra).
        self._arm_frame_gate()
        if path.suffix.lower() != ".livp":
            self._after_frame(lambda p=path: self._resolve_match(p))
        self._display_path = display_path
        self._load_token += 1
        cached = get_cached_image(display_path) if has_cached_entry(display_path) else None
        if cached is not None:
            # Önbellekte hazır: 0 ms gecikmeyle anında göster!
            qimg, w, h = cached
            self._on_image_loaded(str(display_path), qimg, w, h, self._load_token)
        else:
            worker = ImageLoadWorker(str(display_path), self._load_token)
            worker.signals.loaded.connect(self._on_image_loaded)
            worker.signals.upgraded.connect(self._on_image_upgraded)
            worker.signals.failed.connect(self._on_image_failed)
            self.pool.start(worker)

        self.stage.stack.setCurrentWidget(self.canvas)
        self._refresh_live_overlays()
        self._refresh_details()
        self.stage.layout_chrome()
        # Komşuların ön yüklemesi _on_image_loaded'da (kare çizildikten sonra) başlar.

    def _resolve_match(self, path: Path, seek: bool = True) -> None:
        if path_key(path) in self._unplayable_paths:
            self.current_match = None
            self._refresh_live_overlays()
            self._refresh_details()
            return
        # 1–24 MiB'lik dosya pencereleri okunur ve gömülü video çıkarılır: GUI'yi bloklamamak
        # için arka planda çözülür, sonuç _on_match_resolved'da uygulanır.
        self._match_token += 1
        worker = LiveResolveWorker(
            path, self.uuid_cache.mapping or None, self.temps, self._match_token, seek
        )
        worker.signals.resolved.connect(self._on_match_resolved)
        self.pool.start(worker)

    def _on_match_resolved(self, token: int, path_str: str, match, seek: bool) -> None:
        if token != self._match_token:
            return  # daha yeni bir gezinme/istek var
        current = self.current_file()
        if current is None or str(current) != path_str:
            return
        self.current_match = match
        if match is None and seek:
            self.uuid_cache.seek(current)
        self._refresh_live_overlays()
        self._refresh_details()
        self._maybe_autoplay()

    def _on_uuid_ready(self, _folder: str) -> None:
        path = self.current_file()
        if path is None or self.current_match is not None:
            return
        self._resolve_match(path, seek=False)

    def _on_image_loaded(self, path: str, qimage: QImage, _w: int, _h: int, token: int) -> None:
        if token != self._load_token:
            return
        display = getattr(self, "_display_path", None)
        # İşçi, istenen yolun aynısını (str) geri verir: metin karşılaştırması yeter. Eskiden iki
        # Path.resolve() GUI thread'inde dosyayı açıyordu (ağ/WSL yolunda her biri bir gidiş-dönüş).
        if display is None or not same_path_text(path, display):
            return
        startup_trace.mark("image_set")
        self.canvas.set_qimage(QImage(qimage))
        self.canvas.attach_svg(path)  # .svg/.svgz: yakınlaştırınca görünen bölge vektörden yeniden çizilir
        self._remember_shown_image(path, qimage)
        QTimer.singleShot(0, self.canvas.fit_in_window)
        QTimer.singleShot(50, self.canvas.fit_in_window)
        # Video şu an oynatılıyor veya açılıyorsa fotoğraf tuvaline (canvas) geçme!
        # VideoPosterWorker gecikmeli tamamlandığında oynayan videonun üstüne
        # statik poster görüntüsü bindirip donmuş gibi görünmesine yol açıyordu.
        if not self._playing and not self._opening_play:
            self.stage.stack.setCurrentWidget(self.canvas)
        self._refresh_live_overlays()
        self.stage.layout_chrome()
        self._maybe_autoplay()
        self._preload_adjacent()


    def _on_image_upgraded(self, path: str, qimage: QImage, _w: int, _h: int, token: int) -> None:
        """RAW: gömülü önizleme gösterilmişti, tam çözünürlük hazır — görünümü bozmadan değiştir."""
        if token != self._load_token:
            return
        display = getattr(self, "_display_path", None)
        if display is None or not same_path_text(path, display):
            return
        self.canvas.replace_image(QImage(qimage))
        self._remember_shown_image(path, qimage)
        self.stage.layout_chrome()

    def _on_poster_meta(self, path: str, duration_ms: int, token: int) -> None:
        """Oynatmadan önce süreyi bil: seek aralığını ve etiketi hemen kur."""
        if token != self._load_token or duration_ms <= 0:
            return
        current = self.current_file()
        if current is None or not same_path_text(path, current):
            return
        dur = int(duration_ms)
        bar = self.seek_bar
        bar.blockSignals(True)
        try:
            bar.setRange(0, max(1, dur))
            bar.setValue(0)
        finally:
            bar.blockSignals(False)
        self.bottom_dock.set_seek_times(0, dur)
        self._sync_preview_media()

    def _clear_stale_canvas(self, path: str) -> None:
        """Tuvalde hâlâ ÖNCEKİ dosyanın görüntüsü varsa temizle (yeni dosyanın adıyla eski kare kalmasın).

        RAW'da gömülü önizleme gösterilmişse (aynı dosyanın karesi) dokunulmaz."""
        shown = getattr(self, "_shown_image", None)
        if shown is not None and same_path_text(shown[0], path):
            return
        self.canvas.clear_image()
        self._shown_image = None

    def _on_poster_failed(self, path: str, _message: str, token: int) -> None:
        """Videonun ilk karesi okunamadı (bozuk dosya / OpenCV'nin çözemediği kodek). Eskiden sessizce yok
        sayılıyor, tuvalde bir önceki fotoğraf bu videonun adıyla kalıyordu. Oynat düğmesi yine çalışır
        (mpv OpenCV'nin açamadığı dosyaları da oynatabilir; açamazsa hata gösterir)."""
        if token != self._load_token:
            return
        current = self.current_file()
        if current is None or not same_path_text(path, current):
            return
        self._clear_stale_canvas(path)

    def _on_image_failed(self, path: str, message: str, token: int) -> None:
        if token != self._load_token:
            return
        self._release_frame_gate()  # kare gelmeyecek: bekletilen işler (şerit, Live araması) beklemesin
        self._clear_stale_canvas(path)
        MessageBox.warning(self, self.t("error"), self.t("load_fail").format(translate_error(message, self.lang)))

    def _stop_playback(self, release: bool = False) -> None:
        self._remember_position()  # konum, oynatıcı durmadan ÖNCE okunmalı
        self._resume_timer.stop()
        self._playing = False
        self._opening_play = False
        self.player.stop()
        self._preview.set_background(False)
        self._set_play_loading(False)
        if release:
            # Geçici (gömülü Live Photo) video silinmeden ÖNCE önizleme capture'ı bırakılmalı;
            # kalıcı dosyalarda GUI'yi bekletmeye gerek yok.
            self._preview.release_media(wait=self._preview.path in self.temps.paths())
            self.temps.clear()
            self.current_match = None
        if self.current_file() is not None:
            self.stage.stack.setCurrentWidget(self.canvas)
        is_video = bool(self.current_match is not None and self.current_match.source == "video")
        self.bottom_dock.set_video_mode(is_video)
        self._update_tracks_button()
        self._refresh_live_overlays()

    def toggle_live_playback(self) -> None:
        if self.current_match is None:
            return
        if self._playing:
            self._autoplay_armed = False
            # mpv komutu başarısızsa UI "duraklatıldı" göstermesin (durum player'da kalır).
            if self.player.pause():
                self._playing = False
                self._preview.set_background(False)
                self._remember_position()
            self._refresh_live_overlays()
            return
        if self.player.is_paused:
            if not self.player.resume():
                self._refresh_live_overlays()
                return
            self._preview.set_background(True)  # oynatma sürerken storyboard'u ağırlaştırma
            self._playing = True
            if self.stage.stack.currentWidget() is not self.player.widget:
                self.stage.stack.setCurrentWidget(self.player.widget)
            self._refresh_live_overlays()
            return
        if self._opening_play:
            self._opening_play = False
            self._stop_playback(release=False)
            return
        self._opening_play = True
        self._preview.set_background(True)  # oynatma sürerken storyboard'u ağırlaştırma
        self.player.widget.set_cover(bool(getattr(self.canvas, "_fill", False)))
        self._set_play_loading(True)
        self._refresh_live_overlays()
        self.player.play(self.current_match.video_path)

    def _set_play_loading(self, loading: bool) -> None:
        show = bool(loading) and self.current_idx >= 0 and self.isVisible()
        self._busy_active = show
        if show:
            self.busy_chip.show()
            self._place_busy_chip()
            self._busy_host.show()
            self._busy_host.raise_()
        else:
            self._busy_host.hide()
        if hasattr(self.btn_motion, "set_loading"):
            self.btn_motion.set_loading(bool(loading))

    def _place_busy_chip(self) -> None:
        host = getattr(self, "_busy_host", None)
        if host is None or not getattr(self, "_busy_active", False):
            return
        self.busy_chip.adjustSize()
        hint = self.busy_chip.sizeHint()
        w, h = max(60, hint.width()), max(28, hint.height())
        center = self.stage.mapToGlobal(self.stage.rect().center())
        host.place(QRect(center.x() - w // 2, center.y() - h // 2, w, h))

    def _on_playback_started(self) -> None:
        startup_trace.mark("video_started")
        self._opening_play = False
        self._playing = True
        self._set_play_loading(False)
        self.bottom_dock.set_video_mode(True)
        if self.stage.stack.currentWidget() is not self.player.widget:
            self.stage.stack.setCurrentWidget(self.player.widget)
        if self._pending_seek is not None:
            self.player.seek(self._pending_seek)
            self._pending_seek = None
        else:
            self._apply_resume()
        if self._standalone_video_path() is not None and self._resume_enabled():
            self._resume_timer.start()
        self._update_tracks_button()
        self._refresh_live_overlays()
        self._refresh_mode_buttons()
        self._ensure_chrome_visible()

    def _autoplay_enabled(self) -> bool:
        val = self.settings.value("autoplay_once", False)
        if isinstance(val, str):
            return val.lower() in {"1", "true", "yes"}
        return bool(val)

    def _maybe_autoplay(self) -> None:
        if not self._autoplay_armed or not self._autoplay_enabled():
            return
        # Açılıyorsa / duraklatılmışsa dokunma: toggle_live_playback açılış sırasında çağrılırsa bunu
        # "iptal" sayar. Eskiden video açılırken (mpv henüz başlamadan) poster gelince _on_image_loaded
        # buraya düşüyor ve otomatik oynatmayı durduruyordu (ölçüm: 4 açılışın 3'ünde video oynamadı).
        if self.current_match is None or self._playing or self._opening_play or self.player.is_paused:
            return
        self._autoplay_armed = False  # dosya başına bir kez (ayar adı da "autoplay_once")
        self.toggle_live_playback()

    def _set_autoplay(self, enabled: bool) -> None:
        self.settings.setValue("autoplay_once", enabled)
        if enabled:
            self._autoplay_armed = True
            self._maybe_autoplay()
        elif self._playing:
            self._autoplay_armed = False
            self._stop_playback(release=False)

    def _on_playback_failed(self, message: str) -> None:
        startup_trace.mark("video_failed")
        self._playing = False
        self._opening_play = False
        self._set_play_loading(False)
        current = self.current_file()
        if current is not None:
            self._unplayable_paths.add(path_key(current))
        self.current_match = None
        if self.current_file() is not None:
            self.stage.stack.setCurrentWidget(self.canvas)
        self._refresh_live_overlays()
        MessageBox.warning(self, self.t("error"), self.t("play_fail").format(translate_error(message, self.lang)))

    def _defer_single_click(self, action) -> None:
        """Tek tıkı kısa süre beklet; çift tık gelirse iptal et (tam ekrana girerken
        ilk tık oynat/duraklat tetiklemesin)."""
        timer = getattr(self, "_click_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._fire_deferred_click)
            self._click_timer = timer
        self._deferred_click = action
        timer.start(min(QApplication.doubleClickInterval(), 300))

    def _fire_deferred_click(self) -> None:
        action = getattr(self, "_deferred_click", None)
        self._deferred_click = None
        if action is not None:
            action()

    def _on_surface_double_clicked(self) -> None:
        timer = getattr(self, "_click_timer", None)
        if timer is not None:
            timer.stop()
        self._deferred_click = None
        # Duraklat/oynat tıkı bekletilmeden uygulanmıştı (gecikme olmasın diye); çift tıkta geri al.
        t = getattr(self, "_instant_toggle_at", None)
        self._instant_toggle_at = None
        if t is not None and time.monotonic() - t <= QApplication.doubleClickInterval() / 1000 + 0.15:
            self.toggle_live_playback()
        self.toggle_fullscreen()

    def _on_video_clicked(self) -> None:
        if self.isFullScreen():
            self._wake_ui()
            return
        if self._playing or self.player.is_paused:
            # Duraklat/oynat anında olsun: çift tık beklemesi ~yarım saniyelik gecikme yapıyordu.
            self._instant_toggle_at = time.monotonic()
            self._do_video_click()
            return
        self._defer_single_click(self._do_video_click)

    def _do_video_click(self) -> None:
        if self._playing or self.player.is_paused:
            self.toggle_live_playback()
        elif self.current_match is not None and not self._opening_play:
            self.toggle_live_playback()

    def _on_playback_finished(self) -> None:
        if self.player.is_paused:
            return
        self._playing = False
        self._opening_play = False
        self._resume_timer.stop()
        finished_video = self._standalone_video_path()
        if finished_video is not None:
            self._resume.clear(finished_video)  # sonuna kadar izlendi: bir dahaki sefere baştan
        self._set_play_loading(False)
        # Bağımsız video bitince son kare ekranda kalır (mpv keep-open); başa dönüp ilk kareyi göstermesin.
        # Live Photo'da ise oynatma bitince fotoğrafa dönmek doğru.
        if finished_video is None and self.current_file() is not None:
            self.stage.stack.setCurrentWidget(self.canvas)
        self._update_tracks_button()
        self._refresh_live_overlays()
        self._refresh_mode_buttons()
        self._ensure_chrome_visible()

    def _on_canvas_activated(self) -> None:
        if self.isFullScreen():
            self._do_canvas_activated()
            return
        self._defer_single_click(self._do_canvas_activated)

    def _do_canvas_activated(self) -> None:
        if self._opening_play or self._playing:
            return
        if self.current_match is not None:
            self.toggle_live_playback()

    def open_settings_dialog(self, initial_tab: int = 0) -> None:
        from app.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        if initial_tab > 0:
            dlg._switch_tab(initial_tab)
        dlg.exec()
        # Pencerenin çocuğu olarak kalırsa her açılış bir diyalog biriktirir; hepsi güncelleme sonuçlarını dinlerdi.
        dlg.deleteLater()

    def show_settings_menu(self) -> None:
        menu = QMenu(self)

        act_settings = QAction(menu_icon("settings"), f"{self.t('settings')}…", self)
        act_settings.setShortcut(QKeySequence("Ctrl+,"))
        act_settings.triggered.connect(lambda: self.open_settings_dialog(0))
        menu.addAction(act_settings)
        menu.addSeparator()

        act_open = QAction(menu_icon("file"), self.t("open_file"), self)
        act_open.triggered.connect(self.select_file)
        menu.addAction(act_open)
        act_folder = QAction(menu_icon("folder"), self.t("open_folder"), self)
        act_folder.triggered.connect(self.select_folder)
        menu.addAction(act_folder)
        if self.current_file() is not None:
            act_reveal = QAction(menu_icon("folder"), self.t("reveal"), self)
            act_reveal.triggered.connect(self._reveal_in_explorer)
            menu.addAction(act_reveal)
        act_new = QAction(menu_icon("new_window"), self.t("new_window"), self)
        act_new.triggered.connect(self.open_new_window)
        menu.addAction(act_new)
        menu.addSeparator()

        self._add_sort_submenu(menu)
        menu.addSeparator()

        theme_menu = menu.addMenu(self.t("theme"))
        resolved = self._resolved_theme()
        theme_menu.setIcon(
            theme_system_icon()
            if self.theme == "system"
            else theme_dark_icon()
            if resolved == "dark"
            else theme_light_icon()
        )
        theme_group = QActionGroup(theme_menu)
        theme_group.setExclusive(True)
        for key, icon in (
            ("system", theme_system_icon()),
            ("dark", theme_dark_icon()),
            ("light", theme_light_icon()),
        ):
            is_active = (self.theme == key)
            text = f"✓  {self.t(key)}" if is_active else f"    {self.t(key)}"
            act = QAction(icon, text, theme_menu)
            act.setData(key)
            act.setCheckable(True)
            act.setChecked(is_active)
            act.triggered.connect(lambda checked, k=key: self.set_theme(k))
            theme_group.addAction(act)
            theme_menu.addAction(act)

        accent_menu = menu.addMenu(self.t("accent"))
        accent_menu.setIcon(accent_dot_icon(theme_mod.ACCENTS[self.accent]["accent"]))
        accent_group = QActionGroup(accent_menu)
        accent_group.setExclusive(True)
        for key, spec in theme_mod.ACCENTS.items():
            is_active = (self.accent == key)
            name = theme_mod.accent_label(key, self.lang)
            text = f"✓  {name}" if is_active else f"    {name}"
            act = QAction(accent_dot_icon(spec["accent"]), text, accent_menu)
            act.setData(key)
            act.setCheckable(True)
            act.setChecked(is_active)
            act.triggered.connect(lambda checked, k=key: self.set_accent(k))
            accent_group.addAction(act)
            accent_menu.addAction(act)

        lang_menu = menu.addMenu(self.t("language"))
        current_choice = getattr(self, "lang_choice", self.lang)
        lang_menu.setIcon(menu_icon("globe"))
        lang_group = QActionGroup(lang_menu)
        lang_group.setExclusive(True)
        for code, name in language_choices(include_auto=True, lang=self.lang):
            is_active = (current_choice == code)
            text = f"✓  {name}" if is_active else f"    {name}"
            act = QAction(language_flag_icon(code), text, lang_menu)
            act.setData(code)
            act.setCheckable(True)
            act.setChecked(is_active)
            act.triggered.connect(lambda checked, c=code: self.set_language(c))
            lang_group.addAction(act)
            lang_menu.addAction(act)

        menu.addSeparator()
        act_auto = QAction(menu_icon("autoplay"), self.t("autoplay"), self)
        act_auto.setCheckable(True)
        act_auto.setChecked(self._autoplay_enabled())
        act_auto.toggled.connect(self._set_autoplay)
        menu.addAction(act_auto)
        act_resume = QAction(menu_icon("resume"), self.t("resume_setting"), self)
        act_resume.setCheckable(True)
        act_resume.setChecked(self._resume_enabled())
        act_resume.toggled.connect(self._set_resume_enabled)
        menu.addAction(act_resume)

        # Ayrıntılar yalnızca medya yüklüyken göster
        if self.current_idx >= 0:
            act_details = QAction(menu_icon("info"), self.t("info"), self)
            act_details.triggered.connect(self.toggle_details)
            menu.addAction(act_details)


        if SYSTEM_INTEGRATION:
            menu.addSeparator()
            act_defaults = QAction(menu_icon("apps"), self.t("default_apps"), self)
            act_defaults.triggered.connect(self._open_default_apps)
            menu.addAction(act_defaults)

            menu.addSeparator()
            if is_installed():
                act_shell = QAction(menu_icon("shell"), self.t("shell_off"), self)
                act_shell.triggered.connect(self._remove_shell)
            else:
                act_shell = QAction(menu_icon("shell"), self.t("shell_on"), self)
                act_shell.triggered.connect(self._add_shell)
            menu.addAction(act_shell)

        menu.addSeparator()
        act_about = QAction(menu_icon("info"), self.t("about"), self)
        act_about.setShortcut(QKeySequence("F1"))
        act_about.triggered.connect(self._show_about)
        menu.addAction(act_about)

        act_ver = QAction(self.t("version_line").format(get_version()), self)
        act_ver.setEnabled(False)
        menu.addAction(act_ver)
        check_text = self.t("check_updates")
        if self._update_badge:
            check_text = f"● {check_text}"
        act_check = QAction(menu_icon("update"), check_text, self)
        act_check.triggered.connect(self._check_updates)
        menu.addAction(act_check)

        menu.addSeparator()
        act_quit = QAction(menu_icon("exit"), self.t("quit"), self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.quit_application)
        menu.addAction(act_quit)

        menu.exec(self.btn_settings.mapToGlobal(QPoint(0, self.btn_settings.height() + 6)))

    def _show_about(self) -> None:
        self.open_settings_dialog(2)

    def _bind_notice(self) -> None:
        """Güncelleme hizmetine bağlan (bir kez). Hizmet ilk kareden sonra kurulur; app.main kurduktan
        sonra açık pencereler için bunu yeniden çağırır, bu yüzden sıra ne olursa olsun bağlanılır."""
        from app.ui.updates import get_update_service

        if getattr(self, "_notice_bound", False):
            return
        service = get_update_service()
        if service is None:
            return
        self._notice_bound = True
        service.notice_received.connect(self._on_notice)
        service.manual_result.connect(self._on_manual_update_result)
        service.sync_window(self)  # pencere güncelleme bulunduktan sonra açıldıysa düğme hemen görünsün

    def _on_notice(self, notice) -> None:
        """Sunucudan gelen duyuru: pencere üstündeki satırda, kullanıcı ✕ ile kapatana dek durur."""
        if str(self.settings.value("dismissed_notice", "") or "") == notice.id:
            return
        self.notice_bar.show_notice(notice)
        self._sync_notice_row()

    def _on_notice_dismissed(self, notice_id: str) -> None:
        self.settings.setValue("dismissed_notice", notice_id)
        self._sync_notice_row()

    def _sync_notice_row(self) -> None:
        """Satır, duyuru varken (tam ekran dışında) görünür; kapsüller satırın altındaki şeride kayar."""
        row = getattr(self, "notice_row", None)
        if row is None:
            return
        row.setVisible(self.notice_bar.current() is not None and not self.isFullScreen())
        stage = getattr(self, "stage", None)
        if stage is not None:
            QTimer.singleShot(0, stage.layout_chrome)

    def _add_sort_submenu(self, parent_menu: QMenu) -> QMenu:
        sort_menu = parent_menu.addMenu(menu_icon("sort"), self.t("sort"))
        group = QActionGroup(sort_menu)
        group.setExclusive(True)
        options = [
            ("date_asc", self.t("sort_date_asc")),
            ("date_desc", self.t("sort_date_desc")),
            ("name_asc", self.t("sort_name_asc")),
            ("name_desc", self.t("sort_name_desc")),
        ]
        for mode, label in options:
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(self.sort_by == mode)
            act.triggered.connect(lambda _, m=mode: self.set_sort_mode(m))
            group.addAction(act)
            sort_menu.addAction(act)
        return sort_menu

    def _show_file_chip_menu(self, pos: QPoint) -> None:
        path = self.current_file()
        if path is None:
            return
        menu = QMenu(self)

        act_reveal = QAction(menu_icon("folder"), self.t("reveal"), self)
        act_reveal.setShortcut(QKeySequence("Ctrl+E"))
        act_reveal.triggered.connect(self._reveal_in_explorer)
        menu.addAction(act_reveal)

        act_folder = QAction(menu_icon("folder"), self.t("open_folder"), self)
        act_folder.setShortcut(QKeySequence("Ctrl+Shift+O"))
        act_folder.triggered.connect(self.select_folder)
        menu.addAction(act_folder)

        menu.addSeparator()

        act_copy_name = QAction(menu_icon("copy"), self.t("copy_name"), self)
        act_copy_name.triggered.connect(lambda: QApplication.clipboard().setText(path.name))
        menu.addAction(act_copy_name)

        act_copy_path = QAction(menu_icon("copy"), self.t("copy_path"), self)
        act_copy_path.triggered.connect(lambda: QApplication.clipboard().setText(str(path.resolve())))
        menu.addAction(act_copy_path)

        menu.addSeparator()
        self._add_sort_submenu(menu)

        sender = self.sender()
        target = sender if isinstance(sender, QWidget) else self.file_chip
        menu.exec(target.mapToGlobal(pos))

    def _on_dock_folder_clicked(self) -> None:
        path = self.current_file()
        if path is not None and path.exists():
            self._reveal_in_explorer()
        else:
            self.select_folder()

    def _show_folder_button_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)

        path = self.current_file()
        if path is not None:
            act_reveal = QAction(menu_icon("folder"), self.t("reveal"), self)
            act_reveal.setShortcut(QKeySequence("Ctrl+E"))
            act_reveal.triggered.connect(self._reveal_in_explorer)
            menu.addAction(act_reveal)

        act_folder = QAction(menu_icon("folder"), self.t("open_folder"), self)
        act_folder.setShortcut(QKeySequence("Ctrl+Shift+O"))
        act_folder.triggered.connect(self.select_folder)
        menu.addAction(act_folder)

        act_file = QAction(menu_icon("file"), self.t("open_file"), self)
        act_file.setShortcut(QKeySequence("Ctrl+O"))
        act_file.triggered.connect(self.select_file)
        menu.addAction(act_file)

        menu.addSeparator()
        self._add_sort_submenu(menu)

        menu.exec(self.btn_folder.mapToGlobal(pos))

    def _check_updates(self) -> None:
        from app.ui.updates import get_update_service

        service = get_update_service()
        if service is not None:
            service.check_now()

    def mark_update_available(self, available: bool = True, version: str = "") -> None:
        """Bekleyen güncellemeyi menüde rozetle ve başlık kapsülündeki "Güncelle · vX" düğmesini göster."""
        self._update_badge = bool(available)
        if available and version:
            self._update_version = str(version)
        elif not available:
            self._update_version = ""
        self._apply_update_pill()

    def _apply_update_pill(self) -> None:
        if not hasattr(self, "btn_update"):
            return
        version = self._update_version
        self.btn_update.setVisible(bool(version))
        if version:
            self.btn_update.setText(self.t("update_pill").format(version))
            self.btn_update.setToolTip(self.t("update_pill_tip").format(version))
            self.btn_update.adjustSize()
        self.btn_update.style().unpolish(self.btn_update)
        self.btn_update.style().polish(self.btn_update)
        QTimer.singleShot(0, self._relayout_chrome)

    def _relayout_chrome(self) -> None:
        """Başlık kapsülü genişliği değişti (güncelleme düğmesi göründü/gitti): kapsülleri yeniden yerleştir."""
        stage = getattr(self, "stage", None)
        if stage is not None:
            stage.layout_chrome()

    def _open_pending_update(self) -> None:
        from app.ui.updates import get_update_service

        service = get_update_service()
        if service is not None:
            service.open_pending()

    def _on_manual_update_result(self, origin: str, kind: str, text: str) -> None:
        """Menüden yapılan elle denetimin sonucu: pop-up yerine kısa bildirim."""
        if origin == "menu":
            self._flash(text)

    def _on_select_theme(self, mode: str, menu: QMenu | None = None) -> None:
        if menu is not None:
            menu.close()
        self.set_theme(mode)

    def _on_select_language(self, lang: str, menu: QMenu | None = None) -> None:
        if menu is not None:
            menu.close()
        self.set_language(lang)

    def set_theme(self, mode: str) -> None:
        try:
            self.theme = mode
            self.settings.setValue("theme", mode)
            self.apply_theme()
            self.retranslate_ui()
            self._refresh_live_overlays()
            if not (self.isFullScreen() and getattr(self, "_cursor_blank", False)):
                self._ensure_chrome_visible()
            self.stage.layout_chrome()
        except Exception as exc:
            import traceback
            traceback.print_exc()

    def set_accent(self, key: str) -> None:
        """Vurgu rengini değiştirir (kaydeder ve tüm stil sayfalarını/simgeleri yeniler)."""
        try:
            self.accent = theme_mod.normalize_accent(key)
            self.settings.setValue("accent", self.accent)
            self.apply_theme()
            self.retranslate_ui()
            self._refresh_live_overlays()
            self.stage.layout_chrome()
        except Exception:
            import traceback
            traceback.print_exc()

    def set_language(self, lang: str) -> None:
        try:
            self.lang_choice = lang
            self.lang = get_active_language(lang)
            self.settings.setValue("lang", lang)
            self.retranslate_ui()
            self.apply_theme()
            self._refresh_live_overlays()
            self.stage.layout_chrome()
        except Exception:
            import traceback
            traceback.print_exc()

    def _notice(self, text: str, warn: bool = False) -> None:
        """Başarı bildirimi kısa toast olur; hata (karar gerektirmese de ayrıntı taşır) özel kutuda gösterilir."""
        if warn:
            MessageBox.warning(self, self.t("title"), text)
        else:
            self._flash(text)

    def _add_shell(self) -> None:
        try:
            install_context_menu()
            self._notice(self.t("shell_ok"))
        except OSError as exc:
            self._notice(self.t("shell_fail").format(exc), warn=True)

    def _remove_shell(self) -> None:
        try:
            uninstall_context_menu()
            self._notice(self.t("shell_ok"))
        except OSError as exc:
            self._notice(self.t("shell_fail").format(exc), warn=True)

    def _open_default_apps(self) -> None:
        try:
            open_default_apps()
        except OSError as exc:
            MessageBox.warning(self, self.t("error"), str(exc))

    def quit_application(self) -> None:
        """Ctrl+Q / ⚙ menüsü "Çıkış": bu sürecin tüm pencereleri normal kapanış yolundan kapanır; son pencere
        yok olunca uygulama (ve tepsi simgesi) kendiliğinden gider. X ve Alt+F4 de aynı sonucu verir (arka plan yok)."""
        from app.ui import session

        windows = list(session.windows)
        if self not in windows:
            windows.append(self)
        for win in windows:
            try:
                win.close()
            except RuntimeError:  # pencere bu arada yok olmuş
                pass

    def hide_details(self) -> None:
        if self._details_on:
            self.toggle_details()

    def toggle_details(self) -> None:
        loaded = self.current_idx >= 0
        # Medya yüklü değilse kısayol ve menü çağrısı panel durumunu değiştirmesin
        if not loaded:
            return
        self._details_on = not self._details_on
        self.details.setVisible(self._details_on and loaded)
        if hasattr(self, "btn_info"):
            self.btn_info.setProperty("active", "true" if self._details_on else "false")
            _polish(self.btn_info)
        if self._details_on:
            self._refresh_details()
        self.stage.layout_chrome()
        if self.canvas._fitted:
            QTimer.singleShot(0, self.canvas.fit_in_window)


    def _refresh_details(self) -> None:
        if not self._details_on:
            return
        path = self.current_file()
        extra = {}
        if path is not None and path.suffix.lower() not in VIDEO_EXTS:
            extra["motion"] = self.t("yes") if self.current_match else self.t("no")
            extra["protocol"] = self.current_match.source if self.current_match else ""
        if hasattr(self, "player") and hasattr(self.player, "get_stream_metadata"):
            extra.update(self.player.get_stream_metadata())
        self.details.show_path(path, extra)
        self._update_histogram()

    def show_metadata(self) -> None:
        if not self._details_on:
            self.toggle_details()

    def toggle_maximize(self) -> None:
        super().toggle_maximize()
        self._refresh_max_icon()

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        # Giriş/çıkış değişiklik olayından da tetiklenir; bu çağrı güvenlik ağıdır (idempotent).
        self._sync_fullscreen_mode()
        self._sync_chrome()
        self._refresh_icons()
        self._relayout_shell()
        QTimer.singleShot(0, self._relayout_shell)
        QTimer.singleShot(60, self._relayout_shell)
        QTimer.singleShot(0, self._clear_fullscreen_button_state)
        QTimer.singleShot(80, self._clear_fullscreen_button_state)

    def _clear_fullscreen_button_state(self) -> None:
        btn = getattr(self, "btn_full", None)
        if btn is None:
            return
        btn.setDown(False)
        btn.clearFocus()
        btn.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
        QApplication.sendEvent(btn, QEvent(QEvent.Type.Leave))
        _polish(btn)
        btn.update()

    def _fullscreen_backdrop(self, on: bool) -> None:
        """Tam ekranda video/fotoğraf kenarları (letterbox) açık temada da siyah olsun; sinema gibi.
        Pencereli modda tema rengine geri döner."""
        if on:
            self.container.setStyleSheet(
                "QWidget#centralContainer, QWidget#stage { background: #000000; border: none; }"
            )
            bg = "#FF000000"
        else:
            self.container.setStyleSheet("")
            bg = "#FF07080C" if self._resolved_theme() == "dark" else "#FFEEF1F6"
        if hasattr(self, "player") and hasattr(self.player, "set_background_color"):
            self.player.set_background_color(bg)

    def _enter_fullscreen_mode(self) -> None:
        self._fullscreen_backdrop(True)
        if hasattr(self, "body_layout"):
            self.body_layout.setContentsMargins(0, 0, 0, 0)
            self.body_layout.setSpacing(0)
        if hasattr(self, "viewer_col"):
            self.viewer_col.setContentsMargins(0, 0, 0, 0)
            self.viewer_col.setSpacing(0)

        # Tam ekranda köşe mask kaldır (ekranın tamamını kullan)
        if hasattr(self, "stage"):
            self.stage.clearMask()

        # Kapsülleri şeffaf overlay host'lara taşı (DWM Direct3D11 swapchain üstüne pürüzsüz çizer)
        if hasattr(self, "dock_wrap") and hasattr(self, "_dock_host"):
            if hasattr(self, "viewer_col") and self.viewer_col.indexOf(self.dock_wrap) >= 0:
                self.viewer_col.removeWidget(self.dock_wrap)
            self._dock_host.adopt(self.dock_wrap)

        if hasattr(self, "brand_bar") and hasattr(self, "_brand_host"):
            self._brand_host.adopt(self.brand_bar)
        if hasattr(self, "nav_bar") and hasattr(self, "_nav_host"):
            self._nav_host.adopt(self.nav_bar)
        if hasattr(self, "file_chip") and hasattr(self, "_chip_host"):
            self._chip_host.adopt(self.file_chip)

        self._layout_overlay_chrome()
        self._show_fullscreen_chrome()

        self.btn_full.setProperty("active", "true")
        _polish(self.btn_full)
        if hasattr(self, "_idle_timer"):
            self._idle_timer.start(5000)

        if hasattr(self, "canvas") and getattr(self.canvas, "_fitted", False):
            QTimer.singleShot(0, self.canvas.fit_in_window)
            QTimer.singleShot(50, self.canvas.fit_in_window)

    def _exit_fullscreen_mode(self) -> None:
        self._fullscreen_backdrop(False)
        if hasattr(self, "_idle_timer"):
            self._idle_timer.stop()
        self.unsetCursor()
        self._cursor_blank = False

        # Devam eden animasyonları durdur
        for a in getattr(self, "_chrome_anims", []):
            try:
                a.stop()
            except Exception:
                pass
        if hasattr(self, "_chrome_anims"):
            self._chrome_anims.clear()

        # Overlay host'lardan pencereli mod ebeveynlerine geri bırak
        if hasattr(self, "_brand_host"):
            self._brand_host.release(self.container)
        if hasattr(self, "_nav_host"):
            self._nav_host.release(self.container)
        if hasattr(self, "_chip_host"):
            self._chip_host.release(self.container)
        if hasattr(self, "_dock_host"):
            viewer = getattr(self, "viewer", None)
            if viewer is not None:
                self._dock_host.release(viewer)
                if hasattr(self, "viewer_col") and self.viewer_col.indexOf(self.dock_wrap) < 0:
                    self.viewer_col.addWidget(self.dock_wrap)

        # Tüm kapsüllerin grafik efektlerini temizle (pencereli modda %100 netlik)
        for w in (getattr(self, "brand_bar", None), getattr(self, "file_chip", None), getattr(self, "nav_bar", None), getattr(self, "dock_wrap", None)):
            if w is not None:
                w.setGraphicsEffect(None)
                w.show()
                w.raise_()

        if hasattr(self, "body_layout"):
            self.body_layout.setContentsMargins(0, 0, 14, 0)
            self.body_layout.setSpacing(8)
        if hasattr(self, "viewer_col"):
            self.viewer_col.setContentsMargins(0, 0, 0, 0)
            self.viewer_col.setSpacing(6)
        if hasattr(self, "viewer_host"):
            self.viewer_host.setContentsMargins(0, 46, 0, 0)

        # dock_wrap'ı pencereli modda tekrar konumlandır
        self._layout_window_dock()

        # Pencere modunda üst kapsülleri göster
        if hasattr(self, "nav_bar"):
            self.nav_bar.show()
            self.nav_bar.raise_()
        if hasattr(self, "brand_bar"):
            self.brand_bar.show()
            self.brand_bar.raise_()
        loaded = self.current_idx >= 0 and bool(self.image_files)
        if hasattr(self, "file_chip") and loaded and bool(self.lbl_filename.text()):
            self.file_chip.show()
            self.file_chip.raise_()

        # Pencere moduna dönerken rounded mask'ı geri uygula
        if hasattr(self, "stage"):
            self.stage._apply_rounded_mask()
            self.stage.layout_chrome()

        self.apply_theme()
        self.hook_resize_filters()

        self.btn_full.setProperty("active", "false")
        _polish(self.btn_full)

        if hasattr(self, "canvas") and getattr(self.canvas, "_fitted", False):
            QTimer.singleShot(0, self.canvas.fit_in_window)
            QTimer.singleShot(50, self.canvas.fit_in_window)

    def _layout_overlay_chrome(self) -> None:
        if not self.isFullScreen():
            return
        origin = self.mapToGlobal(QPoint(0, 0))
        cw = self.width()
        ch = self.height()
        top_y = 16
        inset = 20

        # Sol üstte brand kapsülü (36px yükseklik)
        if hasattr(self, "_brand_host") and hasattr(self, "brand_bar"):
            self.brand_bar.adjustSize()
            bw = self.brand_bar.sizeHint().width() + 12
            self._brand_host.place(QRect(origin.x() + inset, origin.y() + top_y, bw, 36))

        # Sağ üstte pencere/nav kontrolleri (36px yükseklik)
        if hasattr(self, "_nav_host") and hasattr(self, "nav_bar"):
            self.nav_bar.adjustSize()
            nw = self.nav_bar.sizeHint().width() + 6
            self._nav_host.place(QRect(origin.x() + max(inset, cw - nw - inset), origin.y() + top_y, nw, 36))

        # Üst ortada dosya adı kapsülü (36px yükseklik)
        loaded = getattr(self, "current_idx", -1) >= 0 and bool(getattr(self, "image_files", []))
        lbl = getattr(self, "lbl_filename", None)
        lbl_has_text = bool(lbl.text()) if lbl is not None else False
        if hasattr(self, "_chip_host") and hasattr(self, "file_chip"):
            if loaded and lbl_has_text:
                gap = 10
                lo = self.brand_bar.width() + 12 + inset + gap if hasattr(self, "brand_bar") else inset
                nav_w = self.nav_bar.sizeHint().width() + 6 if hasattr(self, "nav_bar") else 0
                hi = cw - nav_w - inset - gap
                chip_x, chip_w = self._fit_chip_x(lo, hi, cw // 2)
                self._chip_host.place(QRect(origin.x() + chip_x, origin.y() + top_y, chip_w, 36))
            else:
                self._chip_host.hide()

        # Altta kontrol barı
        self._layout_overlay_dock()

    def _show_fullscreen_chrome(self) -> None:
        if not self.isFullScreen():
            return
        curr_fn = self.current_file() if hasattr(self, "current_file") else None
        loaded = bool(getattr(self, "image_files", [])) or bool(getattr(self, "_playing", False)) or (curr_fn is not None)
        if hasattr(self, "_dock_host") and loaded:
            self._dock_host.show()
            self._dock_host.raise_()
            if hasattr(self, "dock_wrap"):
                self.dock_wrap.show()
            if hasattr(self, "bottom_dock"):
                self.bottom_dock.show()
        if hasattr(self, "_brand_host"):
            self._brand_host.show()
            self._brand_host.raise_()
        lbl = getattr(self, "lbl_filename", None)
        lbl_has_text = bool(lbl.text()) if lbl is not None else False
        if hasattr(self, "_chip_host") and loaded and lbl_has_text:
            self._chip_host.show()
            self._chip_host.raise_()
        if hasattr(self, "_nav_host"):
            self._nav_host.show()
            self._nav_host.raise_()

    def _hide_fullscreen_chrome(self) -> None:
        if not self.isFullScreen():
            return
        for host in (
            getattr(self, "_dock_host", None),
            getattr(self, "_brand_host", None),
            getattr(self, "_chip_host", None),
            getattr(self, "_nav_host", None),
        ):
            if host is not None:
                host.hide()

    def _fade_fullscreen_chrome(self, fade_in: bool) -> None:
        if not self.isFullScreen():
            return
        if fade_in:
            self._show_fullscreen_chrome()
        else:
            self._hide_fullscreen_chrome()

    def _layout_overlay_dock(self) -> None:
        if not hasattr(self, "dock_wrap") or not hasattr(self, "bottom_dock"):
            return
        if not self.isFullScreen():
            return

        origin = self.mapToGlobal(QPoint(0, 0))
        cw = self.width()
        ch = self.height()
        side = 48
        self.bottom_dock.update_dock_geometry(max(360, cw - side))
        self.bottom_dock.adjustSize()
        dock_w = max(self.bottom_dock.sizeHint().width(), self.bottom_dock.minimumWidth())
        dw = min(cw - side, dock_w + 24)
        dh = self.bottom_dock.sizeHint().height() + 8

        dx = (cw - dw) // 2
        dy = max(12, ch - dh - 20)
        global_geo = QRect(origin.x() + int(dx), origin.y() + int(dy), int(dw), int(dh))
        if hasattr(self, "_dock_host"):
            self._dock_host.place(global_geo)

    def _ensure_chrome_visible(self) -> None:
        if QApplication.activePopupWidget() is not None:
            return
        if self.isFullScreen():
            self._wake_ui()
            return

        loaded = self.current_idx >= 0 and bool(self.image_files)
        if hasattr(self, "dock_wrap") and loaded:
            self.dock_wrap.show()
            self.dock_wrap.raise_()
            if hasattr(self, "bottom_dock"):
                self.bottom_dock.show()
        if hasattr(self, "nav_bar"):
            self.nav_bar.show()
            self.nav_bar.raise_()
        if hasattr(self, "brand_bar"):
            self.brand_bar.show()
            self.brand_bar.raise_()
        if hasattr(self, "file_chip") and loaded and bool(self.lbl_filename.text()):
            self.file_chip.show()
            self.file_chip.raise_()
        if hasattr(self, "stage"):
            self.stage.layout_chrome()

    def _on_idle_timeout(self) -> None:
        if not self.isFullScreen():
            return
        if hasattr(self, "volume_popup") and self.volume_popup.isVisible():
            return
        if QApplication.activePopupWidget() is not None:
            return
        from PyQt6.QtGui import QCursor
        cursor_pos = QCursor.pos()
        for host in (
            getattr(self, "_dock_host", None),
            getattr(self, "_brand_host", None),
            getattr(self, "_chip_host", None),
            getattr(self, "_nav_host", None),
        ):
            if host is not None and host.isVisible():
                if host.geometry().contains(cursor_pos):
                    self._idle_timer.start(3000)
                    return
        self.setCursor(Qt.CursorShape.BlankCursor)
        self._cursor_blank = True
        self._hide_fullscreen_chrome()

    def _wake_ui(self) -> None:
        if QApplication.activePopupWidget() is not None:
            return

        if self.isFullScreen() and hasattr(self, "_idle_timer"):
            self._idle_timer.start(5000)

        if not getattr(self, "_cursor_blank", False):
            return

        self.unsetCursor()
        self._cursor_blank = False

        if self.isFullScreen():
            self._layout_overlay_chrome()
            self._show_fullscreen_chrome()
        else:
            self._ensure_chrome_visible()

    def eventFilter(self, watched, event):  # noqa: N802
        # Uygulama genelinde her olaya çağrılır; aşağıdaki mantık yalnızca bu türleri kullanır.
        if event.type() not in _MAIN_FILTER_EVENT_TYPES:
            return super().eventFilter(watched, event)

        if hasattr(self, "btn_audio") and watched is self.btn_audio:
            if event.type() == QEvent.Type.Enter:
                self._show_volume_popup()
            elif event.type() == QEvent.Type.Leave:
                self._vol_timer.start(500)
            elif event.type() == QEvent.Type.Wheel:
                delta = 5 if event.angleDelta().y() > 0 else -5
                new_vol = max(0, min(100, self.player.volume + delta))
                self.player.set_volume(new_vol)
                self.volume_popup.set_value(new_vol)
                self._show_volume_popup()
                return True
        elif hasattr(self, "volume_popup") and watched is self.volume_popup:
            if event.type() == QEvent.Type.Enter:
                self._vol_timer.stop()
            elif event.type() == QEvent.Type.Leave:
                self._vol_timer.start(500)

        if QApplication.activePopupWidget() is not None:
            return False

        from PyQt6.QtGui import QWindow
        if isinstance(watched, QWindow):
            if self.isFullScreen() and event.type() in (
                QEvent.Type.MouseMove,
                QEvent.Type.MouseButtonPress,
                QEvent.Type.KeyPress,
                QEvent.Type.Wheel,
            ):
                self._wake_ui()
            return False

        if isinstance(watched, QWidget) and watched.window() is not self:
            return False

        if self.isFullScreen():
            if event.type() in (
                QEvent.Type.MouseMove,
                QEvent.Type.MouseButtonPress,
                QEvent.Type.KeyPress,
                QEvent.Type.Wheel,
            ):
                self._wake_ui()

        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Delete and not self._crop_active:
                from PyQt6.QtWidgets import QLineEdit, QTextEdit, QPlainTextEdit
                if isinstance(watched, (QLineEdit, QTextEdit, QPlainTextEdit)):
                    return super().eventFilter(watched, event)
                shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                if shift:
                    self.permanently_delete_current()
                else:
                    self.delete_current()
                return True

        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)

        if key == Qt.Key.Key_F11:
            self.toggle_fullscreen()
        elif key == Qt.Key.Key_F5:
            self.toggle_slideshow()
        elif key == Qt.Key.Key_F2:
            self.rename_current()
        elif key == Qt.Key.Key_Escape:
            if self._slideshow_timer.isActive():
                self._stop_slideshow()
            elif self.isFullScreen():
                self.toggle_fullscreen()
            elif self._playing:
                self._stop_playback(release=False)
            else:
                super().keyPressEvent(event)
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._zoom_by(1.2)
        elif key == Qt.Key.Key_Minus:
            self._zoom_by(1 / 1.2)
        elif key == Qt.Key.Key_0:
            self._on_fit_clicked()
        elif key == Qt.Key.Key_1:
            self._on_actual_size_clicked()
        elif key == Qt.Key.Key_I:
            self.toggle_details()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and alt:
            self.toggle_details()
        elif key == Qt.Key.Key_Delete:
            if shift:
                self.permanently_delete_current()
            else:
                self.delete_current()
        elif key == Qt.Key.Key_Home:
            self.first_image()
        elif key == Qt.Key.Key_End:
            self.last_image()
        elif key == Qt.Key.Key_R:
            if ctrl and shift:
                self._rotate_current(-90)
            elif ctrl or not modifiers:
                self._rotate_current(90)
        elif key == Qt.Key.Key_L and not modifiers:
            self._rotate_current(-90)
        elif key == Qt.Key.Key_Z and ctrl:
            self.undo_delete()
        elif key == Qt.Key.Key_S and ctrl:
            self.save_current_as()
        elif key == Qt.Key.Key_P and ctrl:
            self._print_image()
        elif key == Qt.Key.Key_C and ctrl:
            if shift:
                self._copy_image()
            else:
                self._copy_file()
        elif key == Qt.Key.Key_O and ctrl:
            if shift:
                self.select_folder()
            else:
                self.select_file()
        elif key == Qt.Key.Key_E and ctrl:
            self._reveal_in_explorer()
        else:
            super().keyPressEvent(event)

    def show_photo_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)

        def add(m, icon, text, slot, shortcut: str | None = None) -> QAction:
            act = QAction(text, self) if icon is None else QAction(icon, text, self)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(slot)
            m.addAction(act)
            return act

        path = self.current_file()
        is_video = bool(path is not None and path.suffix.lower() in VIDEO_EXTS)
        is_active_play = bool(self._playing or (hasattr(self, "player") and getattr(self.player, "playing", False)))
        has_playable = is_video or (self.current_match is not None) or is_active_play or self.player.is_paused
        if has_playable:
            if is_active_play:
                add(menu, menu_icon("pause"), self.t("pause"), self.toggle_live_playback, "Space")
            else:
                lbl = self.t("play_live") if (self.current_match is not None and not is_video) else self.t("play")
                add(menu, menu_icon("play"), lbl, self.toggle_live_playback, "Space")
            if self._transport_ready():
                loop_act = add(menu, menu_icon("loop"), self.t("loop"), self._toggle_loop, "Ctrl+L")
                loop_act.setCheckable(True)
                loop_act.setChecked(bool(self.player.loop))
                self._fill_speed_menu(menu.addMenu(menu_icon("speed"), self.t("speed")))
                add(menu, menu_icon("frame_back"), self.t("frame_back"), lambda: self._step_frame(-1), ",")
                add(menu, menu_icon("frame_fwd"), self.t("frame_fwd"), lambda: self._step_frame(1), ".")
            if self._video_can_seek():
                add(menu, menu_icon("save_frame"), self.t("save_frame"), self._save_frame, "S")
            if self._standalone_video_path() is not None:
                add(menu, menu_icon("tracks"), self.t("subtitle_open"), self._open_subtitle_file)
            menu.addSeparator()

        copy_menu = menu.addMenu(menu_icon("copy"), self.t("copy"))
        add(copy_menu, menu_icon("copy"), self.t("copy_file"), self._copy_file, "Ctrl+C")
        add(copy_menu, menu_icon("copy"), self.t("copy_image"), self._copy_image, "Ctrl+Shift+C")

        add(menu, menu_icon("save"), self.t("save_as"), self.save_current_as, "Ctrl+S")
        add(menu, menu_icon("rename"), self.t("rename"), self.rename_current, "F2")
        if path is not None and path.is_file():
            add(menu, menu_icon("move_to"), self.t("move_to"), self._move_to_folder)
            add(menu, menu_icon("copy_to"), self.t("copy_to"), self._copy_to_folder)
        if self._can_crop():
            add(menu, menu_icon("crop"), self.t("crop"), self._start_crop)
        add(menu, menu_icon("print"), self.t("print"), self._print_image, "Ctrl+P")
        add(menu, menu_icon("open_with"), self.t("open_with"), self._open_with)
        add(menu, menu_icon("folder"), self.t("reveal"), self._reveal_in_explorer, "Ctrl+E")
        self._add_sort_submenu(menu)
        menu.addSeparator()

        rot_menu = menu.addMenu(menu_icon("rotate_right"), self.t("rotate"))
        add(rot_menu, menu_icon("rotate_right"), self.t("rot_right"), lambda: self._rotate_current(90), "Ctrl+R")
        add(rot_menu, menu_icon("rotate_left"), self.t("rot_left"), lambda: self._rotate_current(-90), "Ctrl+Shift+R")
        if self._can_save_rotation():
            rot_menu.addSeparator()
            add(rot_menu, menu_icon("save"), self.t("rotate_save"), self._save_rotation)

        view_menu = menu.addMenu(menu_icon("view"), self.t("view"))
        fs_key = "exit_fullscreen" if self.isFullScreen() else "fullscreen"
        add(view_menu, menu_icon("fullscreen"), self.t(fs_key), self.toggle_fullscreen, "F11")
        add(view_menu, menu_icon("slideshow"), self.t("slideshow"), self.toggle_slideshow, "F5")
        add(view_menu, menu_icon("fit"), self.t("fit"), self._on_fit_clicked, "0")
        add(view_menu, menu_icon("actual"), self.t("actual_size"), self._on_actual_size_clicked, "1")
        add(view_menu, menu_icon("fill"), self.t("fill"), self._on_fill_clicked)
        menu.addSeparator()

        add(menu, menu_icon("trash"), self.t("delete"), self.delete_current, "Del")
        add(menu, menu_icon("trash_forever"), self.t("permanently_delete"), self.permanently_delete_current, "Shift+Del")
        if self._undo is not None:
            add(menu, menu_icon("undo"), self.t("undo"), self.undo_delete, "Ctrl+Z")
        menu.addSeparator()

        add(menu, menu_icon("info"), self.t("info"), self.toggle_details, "Alt+Enter")
        menu.exec(global_pos)

    def _copy_image(self) -> None:
        pix = self.canvas.pixmap_item.pixmap()
        if pix.isNull():
            return
        QApplication.clipboard().setPixmap(pix)

    def _copy_file(self) -> None:
        path = self.current_file()
        if path is None:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path.resolve()))])
        QApplication.clipboard().setMimeData(mime)

    def _print_image(self) -> None:
        pix = self.canvas.pixmap_item.pixmap()
        if pix.isNull():
            return
        try:
            from PyQt6.QtPrintSupport import QPrintDialog, QPrinter
            from PyQt6.QtWidgets import QDialog
        except ImportError:
            MessageBox.warning(self, self.t("error"), self.t("print_fail").format("QtPrintSupport"))
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        painter = QPainter(printer)
        try:
            page = printer.pageRect(QPrinter.Unit.DevicePixel)
            scaled = pix.scaled(
                int(page.width()),
                int(page.height()),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = int(page.x() + (page.width() - scaled.width()) / 2)
            y = int(page.y() + (page.height() - scaled.height()) / 2)
            painter.drawPixmap(x, y, scaled)
        finally:
            painter.end()

    def _delete_targets(self, path: Path) -> list[Path]:
        """Silinecek dosya + eşlenik sidecar'lar (çözümlenmiş yola göre tekilleştirilmiş)."""
        targets = [path]
        if path.suffix.lower() not in VIDEO_EXTS:
            targets.extend(paired_sidecars(path))
        unique: list[Path] = []
        seen: set[str] = set()
        for item in targets:
            try:
                key = str(item.resolve()).lower()
            except OSError:
                continue
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def _drop_from_listing(self, removed: list[Path]) -> None:
        """Silinen dosyaları klasör listesinden düş ve yeni geçerli öğeyi göster."""
        gone: set[str] = set()
        for p in removed:
            try:
                gone.add(str(p.resolve()).lower())
            except OSError:
                gone.add(str(p).lower())
            gone.add(p.name.lower())

        remain: list[Path] = []
        for p in self.image_files:
            try:
                p_key = str(p.resolve()).lower()
            except OSError:
                p_key = str(p).lower()
            if p_key not in gone and p.name.lower() not in gone:
                remain.append(p)

        next_idx = index_after_change(self.image_files, self.current_idx, remain)
        self.image_files = remain
        self.current_idx = next_idx
        self._sync_chrome()
        self.display_current()

    def delete_current(self) -> None:
        path = self.current_file()
        if path is None:
            return
        self._stop_playback(release=True)
        self.canvas.stop_gif()
        unique = self._delete_targets(path)
        try:
            discard_undo(self._undo)
            self._undo = recycle_with_undo(unique)
        except Exception as exc:
            MessageBox.warning(self, self.t("error"), self.t("delete_fail").format(exc))
            # Oynatma durduruldu; görünümü geri kur ki LIVE düğmesi/seek çubuğu kaybolmasın.
            self.display_current()
            return
        self._drop_from_listing(unique)

    def undo_delete(self) -> None:
        if self._undo is None:
            return
        try:
            restored = restore_files(self._undo.originals, self._undo.backups)
        except Exception as exc:
            MessageBox.warning(self, self.t("error"), self.t("undo_fail").format(exc))
            return
        discard_undo(self._undo)
        self._undo = None
        if not restored:
            return
        self.load_path(restored[0])

    def permanently_delete_current(self) -> None:
        path = self.current_file()
        if path is None:
            return
        reply = MessageBox.question(
            self,
            self.t("delete_perm_title"),
            self.t("delete_perm_confirm").format(path.name),
            MessageBox.StandardButton.Yes | MessageBox.StandardButton.No,
            MessageBox.StandardButton.No,
        )
        if reply != MessageBox.StandardButton.Yes:
            return

        # Onaydan SONRA durdur: iptalde oynatma/arayüz durumu bozulmasın.
        self._stop_playback(release=True)
        self.canvas.stop_gif()
        unique = self._delete_targets(path)

        errors: list[str] = []
        deleted: list[Path] = []
        for item in unique:
            try:
                if item.is_file():
                    item.unlink()
                deleted.append(item)
            except Exception as exc:
                errors.append(f"{item.name}: {exc}")

        if errors and not deleted:
            MessageBox.warning(self, self.t("error"), self.t("delete_fail").format("; ".join(errors)))
            self.display_current()
            return

        self._drop_from_listing(deleted)

    def rename_current(self) -> None:
        path = self.current_file()
        if path is None or not path.is_file():
            return
        ext = path.suffix
        prompt = f"{self.t('rename_prompt')} ({ext})" if ext else self.t("rename_prompt")
        new_input, ok = InputDialog.getText(
            self,
            self.t("rename_title"),
            prompt,
            QLineEdit.EchoMode.Normal,
            path.stem,
        )
        if not ok or not new_input.strip():
            return
        new_input = new_input.strip()
        # Eğer kullanıcı uzantıyı da yazdıysa (örn: 08244.mp4), mükerrer uzantı ekleme
        if ext and new_input.lower().endswith(ext.lower()):
            stem_part = new_input[:-len(ext)].rstrip(".")
        else:
            stem_part = new_input.rstrip(".")
        if not stem_part or stem_part == path.stem:
            return
        invalid = r'<>:"/\|?*'
        if any(c in stem_part for c in invalid):
            MessageBox.warning(self, self.t("error"), self.t("rename_fail").format(self.t("rename_invalid_chars")))
            return
        final_filename = f"{stem_part}{ext}"
        dest = path.parent / final_filename
        if dest.exists() and dest != path:
            MessageBox.warning(self, self.t("error"), self.t("rename_fail").format(self.t("rename_exists").format(final_filename)))
            return
        try:
            self._stop_playback(release=True)
            self.canvas.stop_gif()
            path.rename(dest)
            for sc in paired_sidecars(path):
                if sc.is_file():
                    sc_dest = sc.parent / f"{dest.stem}{sc.suffix}"
                    if not sc_dest.exists():
                        sc.rename(sc_dest)
            self.image_files[self.current_idx] = dest
            self._set_chip_name(dest.name)
            self.display_current()
        except Exception as exc:
            MessageBox.warning(self, self.t("error"), self.t("rename_fail").format(exc))

    def save_current_as(self) -> None:
        path = self.current_file()
        if path is None or not path.is_file():
            return
        ext = path.suffix.lower()
        filters = ";;".join([
            self.t("filter_all_files"),
            self.t("filter_jpeg"),
            self.t("filter_png"),
            self.t("filter_webp"),
            self.t("filter_bmp"),
        ])
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            self.t("save_as"),
            str(path.name),
            filters,
        )
        if not save_path:
            return
        dest = Path(save_path)
        try:
            pix = self.canvas.pixmap_item.pixmap()
            if not pix.isNull() and dest.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"} and dest.suffix.lower() != ext:
                pix.save(str(dest))
            else:
                shutil.copy2(path, dest)
        except Exception as exc:
            MessageBox.warning(self, self.t("error"), self.t("save_as_fail").format(exc))

    def _open_with(self) -> None:
        path = self.current_file()
        if path is None:
            return
        open_with(path, int(self.winId()))

    def _reveal_in_explorer(self) -> None:
        path = self.current_file()
        if path is None:
            return
        reveal_in_explorer(path)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if urls:
            self.load_path(urls[0].toLocalFile())

    def closeEvent(self, event) -> None:  # noqa: N802
        if hasattr(self, "volume_popup"):
            self.volume_popup.hide()
        if hasattr(self, "seek_preview"):
            self.seek_preview.hide()
        for host in (
            getattr(self, "_dock_host", None),
            getattr(self, "_brand_host", None),
            getattr(self, "_chip_host", None),
            getattr(self, "_nav_host", None),
            getattr(self, "_busy_host", None),
            getattr(getattr(self, "_toast", None), "host", None),
        ):
            if host is not None:
                try:
                    host.close()
                except Exception:
                    pass
        if hasattr(self, "dock_wrap"):
            try:
                self.dock_wrap.close()
            except Exception:
                pass
        self._stop_slideshow()
        self._cancel_crop()
        self._remember_position()
        self.settings.setValue("win_geo", self.saveGeometry())
        self.settings.sync()
        self._playing = False
        self._opening_play = False
        try:
            self.uuid_cache.invalidate()
            self.uuid_cache.shutdown()
        except Exception:
            pass
        try:
            self.filmstrip.shutdown()
        except Exception:
            pass
        self.player.shutdown()
        self._preview.shutdown()  # capture'ı bırak: geçici dosyalar silinebilsin
        self.temps.clear()
        discard_undo(self._undo)
        self.pool.waitForDone(5000)
        event.accept()
