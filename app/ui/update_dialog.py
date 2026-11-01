"""Lumina güncelleme penceresi.

Durumlar: boşta (sürüm, boyut, notlar; Daha sonra / Şimdi güncelle) → indiriliyor (ilerleme + hız + İptal) → kuruluyor
→ uygulama yeniden başlar. Hata olursa pencere AÇIK kalır: satır içi kırmızımsı iletiyle birlikte "Tekrar dene"
(güncelleme hatalarında açılır kutu YOK). İndirme sürerken Esc/X indirmeyi iptal edip kapatır.

Pencere çerçevesi (özel üst çubuk, 12 px köşe, vurgu çizgisi) `ChromeDialog` tabanından gelir; içerik `body_host` içindedir.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from app.core.update_policy import supports_auto_install
from app.core.version import get_version
from app.resources import theme as theme_mod
from app.resources.locales import t
from app.ui.chrome_dialog import ChromeDialog
from app.ui.message_box import _SHADOW


_THEMES = ("dark", "light")


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0:
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} PB"


class UpdateDialog(ChromeDialog):
    update_accepted = pyqtSignal()  # "Şimdi güncelle" ve "Tekrar dene"
    install_confirmed = pyqtSignal()  # doğrulanamayan paket için "Yine de kur"
    update_deferred = pyqtSignal()
    update_cancelled = pyqtSignal()  # indirme iptal edildi (düğme, Esc ya da X)

    def __init__(self, version: str, release_notes: str, is_mandatory: bool = False, parent=None, *, size: int = 0,
                 lang: str | None = None):
        super().__init__(parent)
        self.version = version
        self.release_notes = release_notes
        self.is_mandatory = is_mandatory
        self.size = int(size) if size else 0
        self._lang = lang or getattr(parent, "lang", None)
        self.is_downloading = False
        self.is_installing = False
        self._state = "idle"  # idle | downloading | unverified | installing | error
        self._allow_close = False
        self._theme = "dark"
        self.setWindowTitle(self._t("update_available_title"))
        self.setMinimumWidth(520)
        self.setModal(True)
        self._setup_ui()
        self._apply_style()
        hint = self.sizeHint()  # ChromeDialog.resize "içerik boyutu" ister: gölge payı çıkarılır
        self.resize(max(hint.width() - 2 * _SHADOW, 560), max(hint.height() - 2 * _SHADOW, 400))

    def _t(self, key: str) -> str:
        return t(key, self._lang)

    @property
    def state(self) -> str:
        return self._state

    def _apply_style(self) -> None:
        """Match the main window's theme: the bare dialog showed default grey buttons and a white notes box."""
        resolve = getattr(self.parent(), "_resolved_theme", None)
        try:
            theme = resolve() if callable(resolve) else "dark"
        except Exception:
            theme = "dark"
        self._theme = theme if theme in _THEMES else "dark"
        accent = getattr(self.parent(), "accent", None)
        self.body_host.setStyleSheet(theme_mod.dialog_stylesheet(self._theme, accent))

    def _palette_refreshed(self) -> None:
        """ChromeDialog: tema/vurgu pencere açıkken değişti."""
        self._apply_style()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self.body_host)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel(self._t("update_available_heading").format(self.version))
        title.setObjectName("detailsTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        version_label = QLabel(self._t("update_version_line").format(get_version(), self.version))
        version_label.setWordWrap(True)
        layout.addWidget(version_label)

        self.size_label = QLabel(self._t("update_size_line").format(size=_format_bytes(self.size)) if self.size > 0 else "")
        self.size_label.setObjectName("updateSize")
        self.size_label.setVisible(self.size > 0)  # boyut bilinmiyorsa satır hiç görünmez
        layout.addWidget(self.size_label)

        notes_label = QLabel(self._t("update_notes"))
        layout.addWidget(notes_label)

        self.notes_text = QTextEdit()
        self.notes_text.setReadOnly(True)
        self.notes_text.setMinimumHeight(110)
        notes = self.release_notes or ""
        if not notes.strip():
            self.notes_text.setHtml(f"<p>{self._t('update_available_plain')}</p>")
        elif Qt.mightBeRichText(notes):
            self.notes_text.setHtml(notes)
        else:
            self.notes_text.setPlainText(notes)  # düz metinde satır sonları korunur (setHtml satırları birleştirirdi)
        layout.addWidget(self.notes_text, 1)  # kalan yükseklik notlarındır; uzun notlar kaydırılır

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setMinimumHeight(18)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.progress_label = QLabel()
        self.progress_label.setVisible(False)
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_label.setWordWrap(True)
        layout.addWidget(self.progress_label)

        self.status_label = QLabel()
        self.status_label.setVisible(False)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Satır içi hata / uyarı (açılır kutu yok): metin asla kırpılmaz, sarılır.
        self.error_label = QLabel()
        self.error_label.setObjectName("updateError")
        self.error_label.setWordWrap(True)
        self.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.cancel_btn = QPushButton(self._t("update_cancel"))
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.cancel_btn.setVisible(False)  # yalnızca indirme sürerken
        buttons.addWidget(self.cancel_btn)
        buttons.addStretch()
        self.later_btn = QPushButton(self._t("update_later"))
        self.later_btn.clicked.connect(self._on_later)
        self.later_btn.setVisible(not self.is_mandatory)
        buttons.addWidget(self.later_btn)
        self.update_btn = QPushButton(self._t("update_now" if supports_auto_install() else "update_open_page"))
        self.update_btn.setProperty("class", "primary")
        self.update_btn.clicked.connect(self._on_update)
        self.update_btn.setDefault(True)
        buttons.addWidget(self.update_btn)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------ düğmeler
    def _on_update(self) -> None:
        if self._state == "unverified":
            self.install_confirmed.emit()
            return
        if not supports_auto_install():
            # İndirme/kurulum yok: tarayıcıda indirme sayfası açılır, pencere kapanır.
            self.update_accepted.emit()
            self.accept()
            return
        # Önce "indiriliyor" durumuna geç, SONRA bildir: hizmet hemen satır içi hata gösterirse (yazma izni yok, kaynaktan
        # çalışma…) o hata bu durumu ezmeli, tersi olmamalı. Düğme kilitlenir: ikinci indirme başlamaz.
        self._show_downloading_state()
        self.update_accepted.emit()

    def _on_later(self) -> None:
        if not self.is_downloading and not self.is_installing:
            self.reject()

    def _on_cancel(self) -> None:
        if self.is_downloading:
            self.reject()  # indirmeyi iptal eder ve kapatır (onay sorusu yok)

    def reject(self) -> None:
        if self.is_installing and not self._allow_close:
            return  # kurulum sürerken kapatılamaz (yarım kalmış değiştirme olmasın)
        if self.is_downloading:
            self.is_downloading = False
            self.update_cancelled.emit()  # arka planda sürüp pencere kapanınca uygulamayı habersiz yeniden başlatmasın
        elif not self.is_mandatory and not self.is_installing:
            self.update_deferred.emit()
        super().reject()

    def allow_close(self) -> None:
        """Kurulum bitti, uygulama çıkıyor: pencere kapanabilir (closeAllWindows engellenmesin)."""
        self._allow_close = True

    # ------------------------------------------------------------------ durumlar
    def _set_state(self, state: str) -> None:
        self._state = state
        self._fit()

    def _fit(self) -> None:
        """Etiketler sarıldığı için yükseklik genişliğe bağlı: içerik sığmıyorsa pencere büyür (kırpılma yok)."""
        layout = self.body_host.layout()
        if layout is None:
            return
        layout.activate()
        width = self.body_host.width() or (self.width() - 2 * _SHADOW)
        need = layout.minimumHeightForWidth(width) if layout.hasHeightForWidth() else layout.minimumSize().height()
        have = self.body_host.height()
        if need > have:
            QDialog.resize(self, self.width(), self.height() + (need - have))

    def _show_message(self, text: str, kind: str) -> None:
        self.error_label.setStyleSheet(f"color: {theme_mod.message_colors(self._theme)[kind]}; font-weight: 600;")
        self.error_label.setText(text)
        self.error_label.setVisible(True)

    def _show_downloading_state(self) -> None:
        self.is_downloading = True
        self.is_installing = False
        self.error_label.setVisible(False)
        self.update_btn.setEnabled(False)
        self.later_btn.setVisible(False)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label.setVisible(True)
        self.progress_label.setText(self._t("update_downloading"))
        self._set_state("downloading")

    def update_download_progress(self, downloaded: int, total: int, speed_mbps: float) -> None:
        downloaded_str = _format_bytes(downloaded)
        speed_str = f"{speed_mbps:.2f} MB/s" if speed_mbps > 0 else ""
        if total > 0:
            if self.progress_bar.maximum() == 0:
                self.progress_bar.setRange(0, 100)
            percent = min(int((downloaded / total) * 100), 100)
            self.progress_bar.setValue(percent)
            progress_text = f"{downloaded_str} / {_format_bytes(total)} ({percent}%)"
        else:
            self.progress_bar.setRange(0, 0)
            progress_text = self._t("update_downloaded_bytes").format(downloaded_str)
        if speed_str:
            progress_text += f" - {speed_str}"
        self.progress_label.setText(progress_text)

    def show_download_complete(self) -> None:
        self.is_downloading = False
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.progress_label.setText(self._t("update_download_done"))
        self.cancel_btn.setVisible(False)

    def show_installing_state(self) -> None:
        self.is_installing = True
        self.is_downloading = False
        self.error_label.setVisible(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.status_label.setVisible(True)
        self.status_label.setText(self._t("update_installing"))
        self.update_btn.setVisible(False)
        self.later_btn.setVisible(False)
        self.cancel_btn.setVisible(False)
        self._set_state("installing")

    def show_install_complete(self) -> None:
        self.status_label.setText(self._t("update_restarting"))

    def show_error(self, message: str) -> None:
        """Satır içi hata: pencere açık kalır; birincil düğme "Tekrar dene" olur."""
        self.is_downloading = False
        self.is_installing = False
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.status_label.setVisible(False)
        self._show_message(self._t("update_failed_inline").format(error=message), "error")
        self.update_btn.setText(self._t("update_retry"))
        self.update_btn.setVisible(True)
        self.update_btn.setEnabled(True)
        self.later_btn.setVisible(not self.is_mandatory)
        self.cancel_btn.setVisible(False)
        self._set_state("error")

    def show_unverified(self) -> None:
        """Sunucu sağlama toplamı vermedi: açılır kutu yerine satır içi uyarı + "Yine de kur"."""
        self.is_downloading = False
        self.is_installing = False
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.status_label.setVisible(False)
        self._show_message(self._t("update_unverified"), "warn")
        self.update_btn.setText(self._t("update_install_anyway"))
        self.update_btn.setVisible(True)
        self.update_btn.setEnabled(True)
        self.later_btn.setVisible(not self.is_mandatory)
        self.cancel_btn.setVisible(False)
        self._set_state("unverified")
