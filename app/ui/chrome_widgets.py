"""Ana pencerenin yardımcı arayüz parçaları: overlay host, boş durum, sahne, ses sürgüsü/popup.

MainWindow'dan ayrıldı (bağımsız sınıflar; MainWindow'a bağımlılıkları yok).
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from app.resources import theme as theme_mod
from app.resources.locales import t
from app.ui.drops import FileDropMixin


def polish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class OverlayHost(QWidget):
    """Video üstünde yüzen şeffaf üst pencere (tek bir kapsülü taşır).

    mpv, Direct3D11 ile kendi native HWND'sine çizer. Aynı pencere içindeki
    Qt widget'ları bu yüzeyin üstünde güvenilir şekilde görünemez (alt bar
    kaybolur) ve yuvarlak köşeleri videoyla karışamaz (siyah/beyaz köşe).
    Ayrı, şeffaf bir üst pencereyi ise DWM videonun üstünde gerçek alfa ile
    birleştirir. Pencere odak almaz; ana pencerenin kısayolları çalışmaya
    devam eder. Sahibi ana pencere olduğundan onunla birlikte küçülür/gizlenir.
    """

    def __init__(self, owner: QWidget) -> None:
        super().__init__(
            owner,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._child: QWidget | None = None

    def enterEvent(self, event) -> None:  # noqa: N802
        owner = self.parentWidget() or self.parent()
        if owner is not None and hasattr(owner, "_wake_ui"):
            owner._wake_ui()
        super().enterEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        owner = self.parentWidget() or self.parent()
        if owner is not None and hasattr(owner, "_wake_ui"):
            owner._wake_ui()
        super().mouseMoveEvent(event)

    def child_widget(self) -> QWidget | None:
        return self._child

    def adopt(self, widget: QWidget) -> None:
        if self._child is widget and widget.parentWidget() is self:
            return
        was_hidden = widget.isHidden()
        self._child = widget
        widget.setParent(self)
        self.layout().addWidget(widget)
        # Gizli bir kapsülü (örn. dosya adı yokken boş kapsül) zorla gösterme.
        if not was_hidden:
            widget.show()

    def release(self, new_parent: QWidget) -> QWidget | None:
        widget = self._child
        self._child = None
        self.hide()
        if widget is None:
            return None
        self.layout().removeWidget(widget)
        widget.setParent(new_parent)
        return widget

    def place(self, global_rect: QRect) -> None:
        if self.geometry() != global_rect:
            self.setGeometry(global_rect)


class Toast(QObject):
    """Kısa süreli bilgi kapsülü (opsiyonel tek eylem düğmesiyle): 'Kare kaydedildi · Göster'.

    Yalnızca gerektiğinde görünür ve kendiliğinden kapanır; videonun üstünde de görünsün diye
    şeffaf üst pencerede (OverlayHost) yaşar.
    """

    def __init__(self, owner: QWidget) -> None:
        super().__init__(owner)
        self._host = OverlayHost(owner)
        self._chip = QFrame()
        self._chip.setObjectName("toastChip")
        self._chip.setProperty("role", "glass")
        self._host.adopt(self._chip)
        row = QHBoxLayout(self._chip)
        row.setContentsMargins(14, 6, 8, 6)
        row.setSpacing(10)
        self._label = QLabel()
        self._label.setObjectName("fileName")
        row.addWidget(self._label)
        self._button = QPushButton()
        self._button.setProperty("role", "nav")
        self._button.setFixedHeight(24)
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._button.clicked.connect(self._on_action)
        row.addWidget(self._button)
        self._action = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self._chip.hide()
        self._host.hide()

    @property
    def host(self) -> OverlayHost:
        return self._host

    def is_visible(self) -> bool:
        return self._host.isVisible()

    def text(self) -> str:
        return self._label.text()

    def show_message(
        self, text: str, anchor: QRect, action_text: str = "", action=None, msec: int = 3200, bottom_limit: int | None = None
    ) -> None:
        """`anchor`: küresel koordinatlarda yer alan alan (sahne); kapsül üst-ortada, kapsüllerin altında durur.

        `bottom_limit` verilirse (küresel y) kapsül altta, o çizginin hemen üstünde durur (ör. kontrol çubuğunun üstü)."""
        self._label.setText(text)
        self._action = action if action_text else None
        self._button.setVisible(bool(action_text))
        self._button.setText(action_text)
        self._chip.show()
        self._chip.adjustSize()
        hint = self._chip.sizeHint()
        w = min(max(hint.width(), 120), max(160, anchor.width() - 40))
        h = max(hint.height(), 34)
        y = anchor.y() + 56 if bottom_limit is None else max(anchor.y(), bottom_limit - h - 12)
        self._host.place(QRect(anchor.x() + (anchor.width() - w) // 2, y, w, h))
        self._host.show()
        self._host.raise_()
        self._timer.start(msec)

    def hide(self) -> None:
        self._timer.stop()
        self._host.hide()

    def _on_action(self) -> None:
        action, self._action = self._action, None
        self.hide()
        if action is not None:
            action()


class EmptyState(FileDropMixin, QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._drop_handler = None
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)

        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setStyleSheet("background: transparent;")
        self.refresh_style()
        self.title = QLabel()
        self.title.setObjectName("emptyTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub = QLabel()
        self.sub.setObjectName("emptySub")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        row = QHBoxLayout()
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.setSpacing(10)
        self.btn_file = QPushButton()
        self.btn_file.setProperty("role", "nav")
        self.btn_folder = QPushButton()
        self.btn_folder.setProperty("role", "nav")
        row.addWidget(self.btn_file)
        row.addWidget(self.btn_folder)

        layout.addWidget(self.icon)
        layout.addWidget(self.title)
        layout.addWidget(self.sub)
        layout.addSpacing(8)
        layout.addLayout(row)

    def refresh_style(self) -> None:
        """Süs simgesi seçili vurgunun rengini alır (vurgu değişince yeniden çağrılır)."""
        from app.ui.icons import sparkle_icon

        pal = theme_mod.active_palette()
        self.icon.setPixmap(sparkle_icon(QColor(pal["accent_text"])).pixmap(44, 44))


class Stage(FileDropMixin, QWidget):
    """Görüntü/video alanı + yüzen çubuklar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stage")
        self.setAcceptDrops(True)
        self._drop_handler = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.stack = QStackedWidget(self)
        layout.addWidget(self.stack)

    def _apply_rounded_mask(self) -> None:
        """Yuvarlak köşe maskesi — mpv native HWND CSS border-radius'a uymaz.

        QRegion mask tüm çocuk widget'lara (mpv yüzeyi dahil) uygulanır;
        bu sayede video kapsülün köşeleri keskin siyah değil, yuvarlak görünür.
        Tam ekranda mask kaldırılır (ekranın tamamı kullanılır).
        """
        win = self.window()
        is_fullscreen = win is not None and win.isFullScreen()
        if is_fullscreen:
            self.clearMask()
            return
        from PyQt6.QtGui import QPainterPath, QRegion
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        region = QRegion(path.toFillPolygon().toPolygon())
        self.setMask(region)



    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_rounded_mask()
        self.layout_chrome()


    def layout_chrome(self) -> None:
        if getattr(self, "_in_layout_chrome", False):
            return
        self._in_layout_chrome = True
        try:
            self.stack.setGeometry(self.rect())
            self.stack.lower()

            nav = getattr(self, "nav_bar", None)
            brand_bar = getattr(self, "brand_bar", None)
            chip = getattr(self, "file_chip", None)
            busy = getattr(self, "busy_chip", None)
            inset = 14
            side = getattr(self, "chrome_side", "right")

            stage_w = self.width()
            stage_h = self.height()

            # Chrome widget'lar container'a taşındıysa (Win32 HWND z-order fix),
            # stage koordinatlarını container koordinat sistemine dönüştür.
            win = self.window()
            container = getattr(win, "container", None)
            if container is not None and nav is not None and nav.parent() is container:
                origin = self.mapTo(container, QPoint(0, 0))
            else:
                origin = QPoint(0, 0)
            ox = origin.x()
            is_fs = win is not None and hasattr(win, "isFullScreen") and win.isFullScreen()
            if is_fs:
                if win is not None and hasattr(win, "_layout_overlay_chrome"):
                    win._layout_overlay_chrome()
                elif win is not None and hasattr(win, "_layout_overlay_dock"):
                    win._layout_overlay_dock()
                return

            # Pencere modunda kapsüller üst 46px şerittedir; video/fotoğraf asla kapsüllere denk gelmez.
            # Duyuru satırı görünürse tüm gövde (ve şerit) onun yüksekliği kadar aşağı iner: şerit sahnenin 40 px üstündedir.
            oy = 6
            if container is not None and nav is not None and nav.parent() is container:
                oy = max(6, origin.y() - 40)


            if brand_bar is not None and win is not None and hasattr(win, "_fit_brand_text"):
                win._fit_brand_text(stage_w)
            if brand_bar is not None and not brand_bar.isHidden():
                max_brand_w = max(260, stage_w // 2)
                brand_bar.setMaximumWidth(max_brand_w)
                brand_bar.adjustSize()
                brand_w = min(max(brand_bar.sizeHint().width() + 6, 180), max_brand_w)
                brand_bar.setFixedWidth(brand_w)
                brand_bar.setFixedHeight(36)
                brand_bar.move(ox + inset, oy)
                brand_bar.raise_()
            if nav is not None and not nav.isHidden():
                max_nav_w = max(160, stage_w - inset * 2)
                nav.setMaximumWidth(max_nav_w)
                nav.adjustSize()
                target_w = min(max(nav.sizeHint().width() + 4, 150), max_nav_w)
                nav.setFixedWidth(target_w)
                nav.setFixedHeight(36)
                if side == "left":
                    x = inset
                else:
                    x = max(inset, stage_w - target_w - inset)
                nav.move(ox + x, oy)
                nav.raise_()
            lbl_name = getattr(win, "lbl_filename", None)
            if chip is not None and lbl_name is not None and not lbl_name.text():
                chip.hide()  # dosya adı yokken boş kapsül asla görünmesin
            if chip is not None and (not chip.isHidden() or (win is not None and getattr(win, "current_idx", -1) >= 0 and bool(getattr(win, "image_files", [])))):
                if win is not None and hasattr(win, "_fit_chip_x"):
                    gap = 10
                    lo = (brand_bar.geometry().right() + 1 + gap) if brand_bar is not None and not brand_bar.isHidden() else ox + inset
                    hi = (nav.x() - gap) if nav is not None and not nav.isHidden() else ox + stage_w - inset
                    chip_x, chip_w = win._fit_chip_x(lo, hi, ox + stage_w // 2)
                    chip.setFixedHeight(36)
                    chip.setGeometry(chip_x, oy, chip_w, 36)
                else:
                    chip.setMaximumWidth(max(140, stage_w // 2))
                    chip.adjustSize()
                    chip.setFixedHeight(36)
                    chip.move(ox + max(inset, (stage_w - chip.width()) // 2), oy)
                chip.raise_()
            if busy is not None and not busy.isHidden():
                busy.adjustSize()
                busy.move(
                    ox + max(inset, (stage_w - busy.width()) // 2),
                    oy + max(48, stage_h // 2 - 16),
                )
                busy.raise_()

            if win is not None and hasattr(win, "isFullScreen") and win.isFullScreen() and hasattr(win, "_layout_overlay_dock"):
                win._layout_overlay_dock()
        finally:
            self._in_layout_chrome = False




    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            if hasattr(win, "toggle_fullscreen"):
                win.toggle_fullscreen()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class VolumeSlider(QSlider):
    """Dikey ses çubuğu: tıklanan konuma doğrudan atlar, sürüklemeyi izler."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _apply_pos(self, y: float) -> None:
        # Dikey slider'da üst 0 piksel maksimum değere, alt 0 minimuma karşılık gelir
        h = max(1, self.height() - 14)
        clamped_y = max(7.0, min(float(self.height() - 7), float(y)))
        ratio = 1.0 - ((clamped_y - 7.0) / h)
        ratio = max(0.0, min(1.0, ratio))
        span = max(1, self.maximum() - self.minimum())
        val = int(self.minimum() + ratio * span)
        self.setValue(val)
        self.sliderMoved.emit(val)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._apply_pos(event.position().y())
            self.sliderPressed.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._apply_pos(event.position().y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._apply_pos(event.position().y())
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class VolumePopup(QFrame):
    volume_changed = pyqtSignal(int)
    mute_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("volumePopup")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setFixedSize(52, 176)
        self._glass = QColor(18, 18, 18, 230)
        self._border = QColor(255, 255, 255, 36)
        self.refresh_style()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 14, 8, 12)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.label = QLabel("100%")
        self.label.setObjectName("volLabel")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)

        self.slider = VolumeSlider(self)
        self.slider.setRange(0, 100)
        self.slider.setValue(100)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self.slider, 1, Qt.AlignmentFlag.AlignHCenter)

        self.icon_lbl = QLabel()
        self.icon_lbl.setObjectName("volIcon")
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.icon_lbl.setToolTip(f"{t('mute')} / {t('unmute')}")
        self.icon_lbl.mousePressEvent = self._on_icon_pressed
        layout.addWidget(self.icon_lbl)
        self._muted = False
        self._update_icon(100)

    def refresh_style(self) -> None:
        """Koyu cam üzerinde okunur yazı; kaydırıcı dolgusu seçili vurguyu izler."""
        accent = theme_mod.palette("dark")["accent_hi"]
        self.setStyleSheet(f"""
            QLabel#volLabel {{
                color: #e8e8e8;
                font-size: 10px;
                font-weight: 700;
                background: transparent;
            }}
            QLabel#volIcon {{
                background: transparent;
            }}
            QSlider::groove:vertical {{
                background: rgba(255, 255, 255, 0.16);
                width: 5px;
                border-radius: 2px;
            }}
            QSlider::sub-page:vertical {{
                background: rgba(255, 255, 255, 0.16);
                border-radius: 2px;
            }}
            QSlider::add-page:vertical {{
                background: {accent};
                border-radius: 2px;
            }}
            QSlider::handle:vertical {{
                background: #ffffff;
                height: 14px;
                margin: 0 -5px;
                border-radius: 7px;
            }}
        """)
        if hasattr(self, "icon_lbl"):
            self._update_icon(self.slider.value())

    def showEvent(self, event) -> None:  # noqa: N802
        self.refresh_style()  # vurgu pencere açıkken değişmiş olabilir
        super().showEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        r = card.width() / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawRoundedRect(card.translated(0, 1.4), r, r)
        from PyQt6.QtGui import QLinearGradient
        fill = QLinearGradient(0, card.top(), 0, card.bottom())
        top = QColor(self._glass)
        top.setAlpha(min(255, self._glass.alpha() + 12))
        fill.setColorAt(0.0, top)
        fill.setColorAt(1.0, self._glass)
        p.setPen(QPen(self._border, 1.1))
        p.setBrush(fill)
        p.drawRoundedRect(card, r, r)
        p.end()

    def set_value(self, val: int) -> None:
        val = max(0, min(100, int(val)))
        self.slider.blockSignals(True)
        self.slider.setValue(val)
        self.slider.blockSignals(False)
        self.label.setText(f"{val}%")
        self._update_icon(val)

    def _update_icon(self, val: int) -> None:
        from app.ui.icons import volume_icon

        muted = bool(getattr(self, "_muted", False))
        if muted:
            self.label.setText(t("muted_label"))
        self.icon_lbl.setPixmap(volume_icon(muted or val <= 0, QColor("#cfcfcf")).pixmap(20, 20))

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self._update_icon(self.slider.value())

    def _on_icon_pressed(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.mute_clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def _on_slider_changed(self, val: int) -> None:
        self.label.setText(f"{val}%")
        self._update_icon(val)
        self.volume_changed.emit(val)

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = 5 if event.angleDelta().y() > 0 else -5
        new_val = max(0, min(100, self.slider.value() + delta))
        self.slider.setValue(new_val)
        event.accept()
