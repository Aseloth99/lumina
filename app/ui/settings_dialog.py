"""Lumina Modern Ayarlar Penceresi.

Genel, Canlı Fotoğraf Araçları (Kimlik Eşleştirme) ve Hakkında sekmeleri. Değişiklikler anında uygulanır (Kaydet yok).
Pencere çerçevesi (özel üst çubuk, 12 px köşe, vurgu çizgisi) diğer pencerelerle aynı `ChromeDialog` tabanından gelir.
"""

from __future__ import annotations

import html
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.matcher import merge_live_photos_by_id
from app.core.registry import is_installed
from app.core.updater import _as_bool
from app.core.version import get_version
from app.resources import theme as theme_mod
from app.resources.locales import language_choices, t
from app.ui.chrome_dialog import ChromeDialog
from app.ui.icons import (
    apps_icon,
    folder_icon,
    language_flag_icon,
    play_icon,
    settings_icon,
    shell_icon,
    theme_dark_icon,
    theme_light_icon,
    theme_system_icon,
)
from app.ui.update_check_ui import UpdateCheckAnimator
from app.ui.updates import UpdateService, get_update_service

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow

# "Sistem Entegrasyonu" (varsayılan uygulama, 'Birlikte aç' kaydı) yalnızca Windows'ta sunulur.
SYSTEM_INTEGRATION = sys.platform == "win32"


class MergeWorker(QObject):
    """Büyük klasörleri arka planda tarayan iş parçacığı."""

    progress = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(dict)
    finished = pyqtSignal()

    def __init__(self, folder: Path, parent=None) -> None:
        super().__init__(parent)
        self.folder = folder
        self._thread: threading.Thread | None = None
        self._running = False

    def isRunning(self) -> bool:  # noqa: N802
        return self._running

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            def on_progress(cur: int, tot: int, name: str) -> None:
                self.progress.emit(cur, tot, name)

            res = merge_live_photos_by_id(self.folder, progress_callback=on_progress)
            self.finished_signal.emit(res)
        finally:
            self._running = False
            self.finished.emit()

    def wait(self, timeout_ms: int = 0) -> bool:
        if self._thread is not None and self._thread.is_alive():
            timeout_sec = (timeout_ms / 1000.0) if timeout_ms > 0 else None
            self._thread.join(timeout=timeout_sec)
            return not self._thread.is_alive()
        return True


class SettingsDialog(ChromeDialog):
    """Lumina ayarlar penceresi: kenar çubuklu sekmeler, kaydırmasız sayfalar, anında uygulama."""

    def __init__(self, parent: MainWindow) -> None:
        super().__init__(parent)
        self.main_win: MainWindow = parent
        self._merge_worker: MergeWorker | None = None

        self.setWindowTitle(self._t("settings_window_title"))
        # "Genel" sayfası (5 kart) ~560 px ister; daha azında kartlar (ve açılır kutular) sıkışıp metni kırpardı
        self.resize(660, 640)
        self.setMinimumSize(660, 640)

        self._build_ui()
        self._apply_style()

    def _t(self, key: str) -> str:
        return t(key, getattr(self.main_win, "lang", "tr"))

    # ------------------------------------------------------------------ iskelet
    def _build_ui(self) -> None:
        body_layout = QHBoxLayout(self.body_host)
        body_layout.setContentsMargins(12, 12, 12, 12)
        body_layout.setSpacing(12)

        # Sol Kenar Çubuğu (Sidebar)
        sidebar = QFrame(self.body_host)
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(150)
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(8, 8, 8, 8)
        sb_layout.setSpacing(6)

        self.nav_buttons: list[QPushButton] = []
        for idx, label_text in enumerate((self._t("tab_general"), self._t("tab_live_photo"), self._t("tab_about"))):
            btn = QPushButton(label_text, sidebar)
            btn.setObjectName("nav_btn")
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setFixedHeight(38)
            btn.clicked.connect(lambda _, i=idx: self._switch_tab(i))
            sb_layout.addWidget(btn)
            self.nav_buttons.append(btn)

        sb_layout.addStretch()
        body_layout.addWidget(sidebar)

        # Sağ İçerik Sayfaları (QStackedWidget) — Kaydırmasız doğrudan sayfalar
        self.stack = QStackedWidget(self.body_host)
        self.stack.setObjectName("stack")
        self.stack.addWidget(self._build_general_page())
        self.stack.addWidget(self._build_live_photo_page())
        self.stack.addWidget(self._build_about_page())
        body_layout.addWidget(self.stack, stretch=1)

        if self.nav_buttons:
            self.nav_buttons[0].setChecked(True)
            self.stack.setCurrentIndex(0)

    def _switch_tab(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)

    def _colors(self) -> dict:
        return theme_mod.palette(self.main_win._resolved_theme(), getattr(self.main_win, "accent", None))

    # ------------------------------------------------------------------ 1. Genel
    def _build_general_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        # Dil Kartı
        self.card_lang = self._create_card(self._t("language"), self._t("settings_lang_desc"))
        self.combo_lang = QComboBox(self.card_lang)
        self.combo_lang.setObjectName("card_control")
        cur_lang = getattr(self.main_win, "lang_choice", self.main_win.lang)
        for code, name in language_choices(include_auto=True, lang=self.main_win.lang):
            self.combo_lang.addItem(language_flag_icon(code), name, code)
        idx = self.combo_lang.findData(cur_lang)
        if idx >= 0:
            self.combo_lang.setCurrentIndex(idx)
        self.combo_lang.currentIndexChanged.connect(self._on_language_changed)
        self.card_lang.layout().addWidget(self.combo_lang)
        layout.addWidget(self.card_lang)

        # Tema Kartı: Sistem, Koyu, Açık (her uygulamada aynı sıra)
        self.card_theme = self._create_card(self._t("theme"), self._t("settings_theme_desc"))
        self.combo_theme = QComboBox(self.card_theme)
        self.combo_theme.setObjectName("card_control")
        self.combo_theme.addItem(theme_system_icon(), self._t("system"), "system")
        self.combo_theme.addItem(theme_dark_icon(), self._t("dark"), "dark")
        self.combo_theme.addItem(theme_light_icon(), self._t("light"), "light")
        t_idx = self.combo_theme.findData(self.main_win.theme)
        if t_idx >= 0:
            self.combo_theme.setCurrentIndex(t_idx)
        self.combo_theme.currentIndexChanged.connect(self._on_theme_changed)
        self.card_theme.layout().addWidget(self.combo_theme)
        layout.addWidget(self.card_theme)

        # Renk (vurgu) Kartı: 10 renk, MediaCompressor/TransferTCP ile aynı anahtarlar ve değerler
        self.card_accent = self._create_card(self._t("accent"), self._t("accent_desc"))
        dots = QHBoxLayout()
        dots.setSpacing(8)
        dots.setContentsMargins(0, 2, 0, 0)
        self.accent_group = QButtonGroup(self)
        self.accent_group.setExclusive(True)
        self.accent_buttons: dict[str, QPushButton] = {}
        current = theme_mod.normalize_accent(getattr(self.main_win, "accent", None))
        for key in theme_mod.ACCENT_KEYS:
            btn = QPushButton(self.card_accent)
            btn.setCheckable(True)
            btn.setFixedSize(28, 28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setProperty("accent_key", key)
            btn.setChecked(key == current)
            self.accent_group.addButton(btn)
            self.accent_buttons[key] = btn
            btn.clicked.connect(lambda checked, k=key: self._on_accent_changed(k) if checked else None)
            dots.addWidget(btn)
        dots.addStretch()
        self.card_accent.layout().addLayout(dots)
        self.lbl_accent_name = QLabel(self.card_accent)
        self.lbl_accent_name.setObjectName("hint_label")
        self.card_accent.layout().addWidget(self.lbl_accent_name)
        self._refresh_accent_dots()
        layout.addWidget(self.card_accent)

        # Otomatik Oynatma Kartı
        self.card_auto = self._create_card(self._t("autoplay"), self._t("settings_autoplay_desc"))
        self.chk_autoplay = QCheckBox(self._t("autoplay"), self.card_auto)
        self.chk_autoplay.setObjectName("card_checkbox")
        self.chk_autoplay.setChecked(self.main_win._autoplay_enabled())
        self.chk_autoplay.toggled.connect(self.main_win._set_autoplay)
        self.card_auto.layout().addWidget(self.chk_autoplay)
        self.chk_resume = QCheckBox(self._t("resume_setting"), self.card_auto)
        self.chk_resume.setObjectName("card_checkbox")
        self.chk_resume.setToolTip(self._t("resume_setting_desc"))
        self.chk_resume.setChecked(self.main_win._resume_enabled())
        self.chk_resume.toggled.connect(self.main_win._set_resume_enabled)
        self.card_auto.layout().addWidget(self.chk_resume)
        layout.addWidget(self.card_auto)

        # Sistem Entegrasyonu Kartı (yalnızca Windows)
        self.card_sys = None
        if SYSTEM_INTEGRATION:
            self.card_sys = self._create_card(self._t("sys_integration"), self._t("settings_default_apps_desc"))
            btn_layout = QHBoxLayout()
            btn_layout.setSpacing(8)

            self.btn_defaults = QPushButton(self._t("default_apps"), self.card_sys)
            self.btn_defaults.setObjectName("action_btn_secondary")
            self.btn_defaults.clicked.connect(self.main_win._open_default_apps)
            btn_layout.addWidget(self.btn_defaults)

            self.btn_shell = QPushButton(self.card_sys)
            self.btn_shell.setObjectName("action_btn_secondary")
            self._refresh_shell_button()
            self.btn_shell.clicked.connect(self._toggle_shell)
            btn_layout.addWidget(self.btn_shell)
            btn_layout.addStretch()
            self.card_sys.layout().addLayout(btn_layout)
            layout.addWidget(self.card_sys)

        layout.addStretch()
        return page

    def _refresh_shell_button(self) -> None:
        if not hasattr(self, "btn_shell"):
            return
        pal = self._colors()
        if is_installed():
            self.btn_shell.setText(self._t("shell_off"))
            self.btn_shell.setIcon(shell_icon(QColor("#ef4444")))
        else:
            self.btn_shell.setText(self._t("shell_on"))
            self.btn_shell.setIcon(shell_icon(QColor(pal["accent_text"])))

    def _toggle_shell(self) -> None:
        if is_installed():
            self.main_win._remove_shell()
        else:
            self.main_win._add_shell()
        self._refresh_shell_button()

    def _on_language_changed(self, index: int) -> None:
        code = self.combo_lang.itemData(index)
        if code:
            self.main_win.set_language(code)
            self._retranslate_ui()

    def _on_theme_changed(self, index: int) -> None:
        code = self.combo_theme.itemData(index)
        if code:
            self.main_win.set_theme(code)
            self._apply_style()

    def _on_accent_changed(self, key: str) -> None:
        self.main_win.set_accent(key)
        self._refresh_accent_dots()
        self._apply_style()

    def _selected_accent(self) -> str:
        for key, btn in self.accent_buttons.items():
            if btn.isChecked():
                return key
        return theme_mod.DEFAULT_ACCENT

    def _refresh_accent_dots(self) -> None:
        """Seçili nokta metin renginde halkayla çevrilir (iki temada da görünür); adı altta yazılır."""
        pal = self._colors()
        selected = self._selected_accent()
        for key, btn in self.accent_buttons.items():
            color = theme_mod.ACCENTS[key]["accent"]
            ring = pal["text"] if key == selected else color
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {color}; border-radius: 14px; border: 2px solid {ring}; padding: 0px; "
                "min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; }"
            )
            name = theme_mod.accent_label(key, self.main_win.lang)
            if key == theme_mod.DEFAULT_ACCENT:
                name = f"{name} {self._t('accent_default')}"
            btn.setToolTip(name)
            btn.setAccessibleName(name)
        label = theme_mod.accent_label(selected, self.main_win.lang)
        self.lbl_accent_name.setText(label)

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(self._t("settings_window_title"))
        if hasattr(self, "nav_buttons") and len(self.nav_buttons) >= 3:
            self.nav_buttons[0].setText(self._t("tab_general"))
            self.nav_buttons[1].setText(self._t("tab_live_photo"))
            self.nav_buttons[2].setText(self._t("tab_about"))

        if hasattr(self, "card_lang"):
            self.card_lang.lbl_title.setText(self._t("language"))
            if self.card_lang.lbl_sub:
                self.card_lang.lbl_sub.setText(self._t("settings_lang_desc"))

        if hasattr(self, "card_theme"):
            self.card_theme.lbl_title.setText(self._t("theme"))
            if self.card_theme.lbl_sub:
                self.card_theme.lbl_sub.setText(self._t("settings_theme_desc"))
            self.combo_theme.blockSignals(True)
            self.combo_theme.setItemText(0, self._t("system"))
            self.combo_theme.setItemText(1, self._t("dark"))
            self.combo_theme.setItemText(2, self._t("light"))
            self.combo_theme.blockSignals(False)

        if hasattr(self, "card_accent"):
            self.card_accent.lbl_title.setText(self._t("accent"))
            if self.card_accent.lbl_sub:
                self.card_accent.lbl_sub.setText(self._t("accent_desc"))
            self._refresh_accent_dots()

        if hasattr(self, "card_auto"):
            self.card_auto.lbl_title.setText(self._t("autoplay"))
            if self.card_auto.lbl_sub:
                self.card_auto.lbl_sub.setText(self._t("settings_autoplay_desc"))
            self.chk_autoplay.setText(self._t("autoplay"))
            self.chk_resume.setText(self._t("resume_setting"))
            self.chk_resume.setToolTip(self._t("resume_setting_desc"))

        if getattr(self, "card_sys", None) is not None:
            self.card_sys.lbl_title.setText(self._t("sys_integration"))
            if self.card_sys.lbl_sub:
                self.card_sys.lbl_sub.setText(self._t("settings_default_apps_desc"))
            self.btn_defaults.setText(self._t("default_apps"))
            self._refresh_shell_button()

        if hasattr(self, "card_merge"):
            self.card_merge.lbl_title.setText(self._t("merge_section_title"))
            if self.card_merge.lbl_sub:
                self.card_merge.lbl_sub.setText(self._t("merge_section_desc"))
            self.lbl_folder.setText(self._t("merge_target_folder"))
            self.btn_browse.setText(self._t("merge_browse_folder"))
            self.btn_merge.setText(self._t("merge_btn_start"))

        if hasattr(self, "lbl_ver"):
            self.lbl_ver.setText(self._t("about_version_line").format(get_version()))
        if hasattr(self, "lbl_desc"):
            self.lbl_desc.setText(self._t("about_features"))
        if hasattr(self, "btn_update") and not self.update_check.running:
            self.btn_update.setText(self._t("check_btn"))
        if hasattr(self, "chk_auto_check"):
            self.chk_auto_check.setText(self._t("auto_update_check"))
            self.chk_auto_check.setToolTip(self._t("auto_update_install_tip"))
            self.chk_auto_install.setText(self._t("auto_update_install"))
            self.chk_auto_install.setToolTip(self._t("auto_update_install_tip"))
            self.lbl_update_hint.setText(self._t("auto_update_install_tip"))
        if hasattr(self, "card_update"):
            self.card_update.lbl_title.setText(self._t("update_group"))

    # ------------------------------------------------------------------ 2. Canlı Fotoğraf Araçları
    def _build_live_photo_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        self.card_merge = self._create_card(self._t("merge_section_title"), self._t("merge_section_desc"))

        folder_layout = QHBoxLayout()
        folder_layout.setSpacing(8)

        self.lbl_folder = QLabel(self._t("merge_target_folder"), self.card_merge)
        self.lbl_folder.setStyleSheet("font-weight: 600;")
        folder_layout.addWidget(self.lbl_folder)

        self.txt_folder = QLineEdit(self.card_merge)
        self.txt_folder.setObjectName("txt_folder")
        cur_folder = getattr(self.main_win, "_watched_folder", None)
        if cur_folder and Path(cur_folder).is_dir():
            self.txt_folder.setText(str(cur_folder))
        else:
            self.txt_folder.setText(str(Path.home() / "Pictures"))
        folder_layout.addWidget(self.txt_folder, stretch=1)

        self.btn_browse = QPushButton(self._t("merge_browse_folder"), self.card_merge)
        self.btn_browse.setObjectName("action_btn_secondary")
        self.btn_browse.clicked.connect(self._on_browse_folder)
        folder_layout.addWidget(self.btn_browse)

        self.card_merge.layout().addLayout(folder_layout)

        self.btn_merge = QPushButton(self._t("merge_btn_start"), self.card_merge)
        self.btn_merge.setObjectName("action_btn_primary")
        self.btn_merge.setFixedHeight(38)
        self.btn_merge.clicked.connect(self._on_start_merge)
        self.card_merge.layout().addWidget(self.btn_merge)

        self.progress_bar = QProgressBar(self.card_merge)
        self.progress_bar.setObjectName("merge_progress")
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        self.card_merge.layout().addWidget(self.progress_bar)

        self.lbl_merge_status = QLabel(self.card_merge)
        self.lbl_merge_status.setObjectName("merge_status")
        self.lbl_merge_status.setWordWrap(True)
        self.lbl_merge_status.hide()
        self.card_merge.layout().addWidget(self.lbl_merge_status)

        layout.addWidget(self.card_merge)
        layout.addStretch()
        return page

    def _on_browse_folder(self) -> None:
        initial = self.txt_folder.text() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, self._t("open_folder"), initial)
        if selected:
            self.txt_folder.setText(selected)

    def _on_start_merge(self) -> None:
        pal = self._colors()
        folder_str = self.txt_folder.text().strip()
        if not folder_str or not Path(folder_str).is_dir():
            err = theme_mod.message_colors(self.main_win._resolved_theme())["error"]
            self.lbl_merge_status.setText(f"<font color='{err}'>{self._t('merge_no_folder')}</font>")
            self.lbl_merge_status.show()
            return

        target_path = Path(folder_str)

        self.btn_merge.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.lbl_merge_status.setText(f"<font color='{pal['accent_text']}'>{self._t('merge_running')}</font>")
        self.lbl_merge_status.show()

        self._merge_worker = MergeWorker(target_path)
        self._merge_worker.progress.connect(self._on_merge_progress)
        self._merge_worker.finished_signal.connect(self._on_merge_finished)
        self._merge_worker.start()

    def _on_merge_progress(self, current: int, total: int, filename: str) -> None:
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
        muted = self._colors()["text_muted"]
        self.lbl_merge_status.setText(
            f"<font color='{muted}'>{self._t('merge_running')} ({current}/{total})<br><small>{html.escape(filename)}</small></font>"
        )

    def _on_merge_finished(self, res: dict) -> None:
        self.btn_merge.setEnabled(True)
        self.progress_bar.hide()
        pal = self._colors()

        if not res.get("success", False):
            # Dosya adları/hata metinleri rich-text'e girdiğinden kaçışlanır (<, & içerebilir).
            errs = "<br>".join(html.escape(str(e)) for e in res.get("errors", []))
            err = theme_mod.message_colors(self.main_win._resolved_theme())["error"]
            self.lbl_merge_status.setText(
                f"<font color='{err}'><b>{html.escape(self._t('merge_error_title'))}</b><br>{errs}</font>"
            )
            self.lbl_merge_status.show()
            return

        merged_count = res.get("merged_count", 0)
        total_scanned = res.get("total_scanned", 0)
        total_images = res.get("total_images", 0)
        total_videos = res.get("total_videos", 0)
        already_paired = res.get("already_paired", 0)

        color = pal["accent_text"] if merged_count > 0 else pal["text_muted"]
        summary = html.escape(
            self._t("merge_result_summary").format(
                total_scanned, total_images, total_videos, already_paired, merged_count
            )
        ).replace("\n", "<br>")
        msg = f"<font color='{color}'><b>✓ {html.escape(self._t('merge_result_title'))}</b></font><br>{summary}"
        pairs = res.get("merged_pairs", [])
        if merged_count > 0 and pairs:
            e = html.escape
            pairs_detail = "<br>".join(
                f"<small>• {e(str(p['image']))} ↔ {e(str(p['old_video']))} → <b>{e(str(p['new_video']))}</b></small>"
                for p in pairs[:5]
            )
            if len(pairs) > 5:
                pairs_detail += f"<br><small>{html.escape(self._t('merge_more_pairs').format(len(pairs) - 5))}</small>"
            msg += f"<br><br>{pairs_detail}"

        self.lbl_merge_status.setText(msg)
        self.lbl_merge_status.show()

        # Ana pencerenin izlediği klasörde birleştirme yapıldıysa listeyi yenile
        folder_str = self.txt_folder.text().strip()
        watched = getattr(self.main_win, "_watched_folder", None)
        try:
            same = bool(watched) and Path(folder_str).resolve() == Path(watched).resolve()
        except OSError:
            same = False
        if same:
            self.main_win._rescan_active_folder()

    # ------------------------------------------------------------------ 3. Hakkında
    def _build_about_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        self.lbl_app = QLabel("LUMINA", page)
        layout.addWidget(self.lbl_app)

        self.lbl_ver = QLabel(self._t("about_version_line").format(get_version()), page)
        layout.addWidget(self.lbl_ver)

        self.lbl_desc = QLabel(self._t("about_features"), page)
        self.lbl_desc.setWordWrap(True)
        layout.addWidget(self.lbl_desc)

        layout.addSpacing(6)

        self.card_update = self._create_card(self._t("update_group"))
        service = get_update_service()
        settings = self.main_win.settings

        # İki güncelleme kutusu her uygulamada aynı: "Otomatik Kontrol Et" + "Otomatik Kur" (kur, kontrol kapalıyken pasif)
        self.chk_auto_check = QCheckBox(self._t("auto_update_check"), self.card_update)
        self.chk_auto_check.setObjectName("card_checkbox")
        self.chk_auto_check.setToolTip(self._t("auto_update_install_tip"))
        self.chk_auto_check.setChecked(_as_bool(settings.value("auto_update_check", True)))
        self.chk_auto_check.toggled.connect(self._on_auto_check_toggled)
        self.card_update.layout().addWidget(self.chk_auto_check)

        self.chk_auto_install = QCheckBox(self._t("auto_update_install"), self.card_update)
        self.chk_auto_install.setObjectName("card_checkbox")
        self.chk_auto_install.setToolTip(self._t("auto_update_install_tip"))
        self.chk_auto_install.setChecked(UpdateService.auto_install_setting(settings))
        self.chk_auto_install.setEnabled(self.chk_auto_check.isChecked())
        self.chk_auto_install.toggled.connect(lambda on: settings.setValue("auto_update_install", bool(on)))
        self.card_update.layout().addWidget(self.chk_auto_install)

        self.lbl_update_hint = QLabel(self._t("auto_update_install_tip"), self.card_update)
        self.lbl_update_hint.setObjectName("hint_label")
        self.lbl_update_hint.setWordWrap(True)
        self.card_update.layout().addWidget(self.lbl_update_hint)

        update_row = QHBoxLayout()
        update_row.setSpacing(12)
        self.btn_update = QPushButton(self._t("check_btn"), self.card_update)
        self.btn_update.setObjectName("action_btn_secondary")
        self.btn_update.setFixedHeight(36)
        self.btn_update.clicked.connect(self._on_check_updates_clicked)
        update_row.addWidget(self.btn_update)
        self.lbl_update_status = QLabel("", self.card_update)
        self.lbl_update_status.setWordWrap(True)
        update_row.addWidget(self.lbl_update_status, 1)
        self.card_update.layout().addLayout(update_row)
        layout.addWidget(self.card_update)
        self.update_check = UpdateCheckAnimator(self.btn_update, self.lbl_update_status, self)

        if service is not None:
            service.manual_result.connect(self._on_manual_update_result)
            pending = getattr(service, "_pending", None)
            if pending:  # sessiz denetim zaten yeni sürüm bulmuş
                self.update_check.finish("new", self._t("update_new_version").format(pending["version"]))

        from app.core.remote_notice import SITE_URL
        from app.ui.link_label import LinkLabel

        self.link_site = LinkLabel("macik.com.tr", SITE_URL, page)
        layout.addWidget(self.link_site, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addStretch()
        return page

    def _on_auto_check_toggled(self, on: bool) -> None:
        self.main_win.settings.setValue("auto_update_check", bool(on))
        self.chk_auto_install.setEnabled(bool(on))

    def _on_check_updates_clicked(self) -> None:
        if self.update_check.running:
            return
        service = get_update_service()
        self.update_check.start(self._t("checking_updates"))
        if service is None:
            self.update_check.finish("fail", self._t("update_status_failed"))
            return
        service.check_now("settings")

    def _on_manual_update_result(self, origin: str, kind: str, text: str) -> None:
        if origin == "settings":
            self.update_check.finish(kind, text)

    def _create_card(self, title: str, subtitle: str = "") -> QFrame:
        card = QFrame(self.body_host)
        card.setObjectName("setting_card")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(12, 10, 12, 10)
        c_layout.setSpacing(5)

        card.lbl_title = QLabel(title, card)  # type: ignore[attr-defined]
        card.lbl_title.setObjectName("card_title")  # type: ignore[attr-defined]
        c_layout.addWidget(card.lbl_title)  # type: ignore[attr-defined]

        card.lbl_sub = None  # type: ignore[attr-defined]
        if subtitle:
            card.lbl_sub = QLabel(subtitle, card)  # type: ignore[attr-defined]
            card.lbl_sub.setObjectName("card_subtitle")  # type: ignore[attr-defined]
            card.lbl_sub.setWordWrap(True)  # type: ignore[attr-defined]
            c_layout.addWidget(card.lbl_sub)  # type: ignore[attr-defined]

        return card

    # ------------------------------------------------------------------ görünüm
    def _palette_refreshed(self) -> None:
        """ChromeDialog: tema/vurgu pencere açıkken değişti; gövde stilini ve renge bağlı simgeleri yenile."""
        self._apply_style()

    def _apply_style(self) -> None:
        pal = self._colors()
        ink = QColor(pal["ink"])
        self.body_host.setStyleSheet(theme_mod.dialog_stylesheet(self.main_win._resolved_theme(), getattr(self.main_win, "accent", None)))
        if hasattr(self, "nav_buttons") and len(self.nav_buttons) >= 3:
            self.nav_buttons[0].setIcon(settings_icon(ink))
            self.nav_buttons[1].setIcon(play_icon(18, ink))
            self.nav_buttons[2].setIcon(apps_icon(ink))
        if hasattr(self, "btn_defaults"):
            self.btn_defaults.setIcon(apps_icon(ink))
        if hasattr(self, "btn_browse"):
            self.btn_browse.setIcon(folder_icon(ink))
        if hasattr(self, "btn_merge"):
            self.btn_merge.setIcon(play_icon(18, QColor(pal["on_fill"])))
        if hasattr(self, "lbl_folder"):
            self.lbl_folder.setStyleSheet("font-weight: 600;")
        if hasattr(self, "lbl_app"):
            self.lbl_app.setStyleSheet(
                f"font-size: 24px; font-weight: 800; color: {pal['accent_text']}; letter-spacing: 2px;"
            )
        if hasattr(self, "lbl_ver"):
            self.lbl_ver.setStyleSheet(f"font-size: 13px; color: {pal['text_muted']}; font-weight: 500;")
        if hasattr(self, "lbl_desc"):
            self.lbl_desc.setStyleSheet(f"font-size: 12px; color: {pal['text']};")
        if hasattr(self, "link_site"):
            self.link_site.refresh_style()
        if hasattr(self, "accent_buttons"):
            self._refresh_accent_dots()
        self._refresh_shell_button()
