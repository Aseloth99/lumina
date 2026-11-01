"""Sağ ayrıntı çekmecesi — EXIF, video akışı ve dosya bilgisi."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QSize, QThreadPool, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPalette, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.exif import (
    SECTION_AUDIO,
    SECTION_CAMERA,
    SECTION_GENERAL,
    SECTION_LOCATION,
    SECTION_VIDEO,
    collect_details,
)
from app.ui.icons import close_glyph_icon


class _ElidedValue(QLabel):
    """Value label for long unbreakable text (a folder path): shortened in the middle, full text in the tooltip.

    Word wrap cannot split a path, so the tail of it used to be cut off at the card edge.
    """

    def __init__(self, text: str, parent=None) -> None:
        super().__init__("", parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._refit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refit()

    def _refit(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(0, self.width()))
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class HistogramView(QWidget):
    """R/G/B (üst üste, 'toplamalı' karışım) ve parlaklık eğrisi. Veri: histogram.compute çıktısı."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(84)
        self._data: dict | None = None
        self._dark = True

    def set_data(self, data: dict | None, dark: bool = True) -> None:
        self._data = data
        self._dark = dark
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self._data:
            return
        from app.core import histogram

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width() - 2, self.height() - 2
        base = QColor(255, 255, 255, 18) if self._dark else QColor(0, 0, 0, 14)
        p.fillRect(0, 0, self.width(), self.height(), base)
        p.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Plus if self._dark else QPainter.CompositionMode.CompositionMode_SourceOver
        )
        colors = {"r": QColor(239, 68, 68), "g": QColor(34, 197, 94), "b": QColor(59, 130, 246)}
        for name, color in colors.items():
            norm = histogram.normalized(self._data[name])
            path = QPainterPath()
            path.moveTo(1, h + 1)
            for i, v in enumerate(norm):
                path.lineTo(1 + i * w / 255.0, 1 + h - float(v) * h)
            path.lineTo(1 + w, h + 1)
            path.closeSubpath()
            fill = QColor(color)
            fill.setAlpha(95 if self._dark else 70)
            p.fillPath(path, fill)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        luma = histogram.normalized(self._data["luma"])
        line = QPainterPath()
        for i, v in enumerate(luma):
            x, y = 1 + i * w / 255.0, 1 + h - float(v) * h
            line.moveTo(x, y) if i == 0 else line.lineTo(x, y)
        p.setPen(QPen(QColor(232, 232, 232, 200) if self._dark else QColor(51, 51, 51, 200), 1.2))
        p.drawPath(line)


class _ExifSignals(QObject):
    ready = pyqtSignal(str, dict, int)


class _ExifWorker(QRunnable):
    def __init__(self, path: str, extra: dict[str, str], token: int) -> None:
        super().__init__()
        self.path = path
        self.extra = extra
        self.token = token
        self.signals = _ExifSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        data = collect_details(Path(self.path))
        data.update({k: v for k, v in self.extra.items() if v})
        self.signals.ready.emit(self.path, data, self.token)


class DetailsPanel(QFrame):
    closed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("detailsPanel")
        self.setFixedWidth(320)
        self.setAutoFillBackground(True)
        self._dark = True
        self._token = 0
        self._labels: dict[str, str] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 10, 14)
        layout.setSpacing(10)

        # Header
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName("detailsTitle")
        self.title.setStyleSheet("font-size: 15px; font-weight: 700; letter-spacing: 0.5px;")
        header.addWidget(self.title, 1)

        self.btn_close = QPushButton()
        self.btn_close.setObjectName("detailsClose")
        self.btn_close.setProperty("role", "sys")
        self.btn_close.setFixedSize(30, 30)
        self.btn_close.setIconSize(QSize(14, 14))
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_close.clicked.connect(self.closed.emit)
        header.addWidget(self.btn_close, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        # Histogram kartı: yalnızca fotoğraflarda ve veri hazırken görünür
        self.hist_card = QFrame()
        self.hist_card.setObjectName("detailsCard")
        hist_lay = QVBoxLayout(self.hist_card)
        hist_lay.setContentsMargins(10, 8, 10, 10)
        hist_lay.setSpacing(6)
        self.hist_title = QLabel()
        hist_lay.addWidget(self.hist_title)
        self.hist_view = HistogramView()
        hist_lay.addWidget(self.hist_view)
        self.hist_card.hide()
        layout.addWidget(self.hist_card)

        # Scroll area for cards
        self.scroll = QScrollArea()
        self.scroll.setObjectName("detailsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)

        self.body = QWidget()
        self.body.setObjectName("detailsBody")
        self.body.setAutoFillBackground(False)
        self.rows = QVBoxLayout(self.body)
        self.rows.setContentsMargins(0, 0, 6, 0)
        self.rows.setSpacing(10)
        self.rows.addStretch()
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, 1)
        self.hide()

    def apply_chrome(self, dark: bool) -> None:
        self._dark = dark
        self._style_hist_card()
        self.hist_view.set_data(self.hist_view._data, dark)
        colors = self._colors()
        bg = QColor(32, 32, 32, 235) if dark else QColor(245, 245, 245, 240)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, bg)
        pal.setColor(QPalette.ColorRole.Base, bg)
        pal.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
        pal.setColor(QPalette.ColorRole.WindowText, pal.color(QPalette.ColorRole.Text))
        self.setPalette(pal)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.viewport().setStyleSheet("background: transparent;")
        self.body.setAutoFillBackground(False)
        self.update()

    def _colors(self) -> dict:
        """Panel renkleri: nötr gri palet + seçili vurgu (başlıklar vurgu renginde, 4,5:1 okunur)."""
        from app.resources import theme

        return theme.palette("dark" if self._dark else "light", theme.active_accent())

    def set_close_icon(self, color: QColor) -> None:
        self.btn_close.setIcon(close_glyph_icon(color))

    def set_heading(self, text: str) -> None:
        self.title.setText(text)

    def set_field_labels(self, labels: dict[str, str]) -> None:
        self._labels = labels
        self._style_hist_card()

    def set_histogram(self, data: dict | None) -> None:
        """Fotoğrafın histogramı; `None` kartı gizler (video, yükleniyor, hata)."""
        self._style_hist_card()
        self.hist_view.set_data(data, self._dark)
        self.hist_card.setVisible(bool(data))

    def _style_hist_card(self) -> None:
        dark = self._dark
        self.hist_card.setStyleSheet(
            "QFrame#detailsCard {"
            f" background: {'rgba(255, 255, 255, 0.04)' if dark else 'rgba(0, 0, 0, 0.03)'};"
            f" border: 1px solid {'rgba(255, 255, 255, 0.08)' if dark else 'rgba(0, 0, 0, 0.07)'};"
            " border-radius: 8px; }"
        )
        self.hist_title.setText(self._labels.get("sec_histogram", "Histogram"))
        self.hist_title.setStyleSheet(
            "font-size: 11px; font-weight: 700; letter-spacing: 0.8px; background: transparent; border: none;"
            f" color: {self._colors()['accent_text']};"
        )

    def show_path(self, path: Path | None, extra: dict[str, str] | None = None) -> None:
        self._token += 1
        self._clear_rows()
        if path is None:
            return
        token = self._token
        worker = _ExifWorker(str(path), extra or {}, token)
        worker.signals.ready.connect(self._on_ready)
        QThreadPool.globalInstance().start(worker)

    def _on_ready(self, _path: str, data: dict, token: int) -> None:
        if token != self._token:
            return
        self._clear_rows()

        sections = [
            ("sec_general", SECTION_GENERAL),
            ("sec_video", SECTION_VIDEO),
            ("sec_audio", SECTION_AUDIO),
            ("sec_camera", SECTION_CAMERA),
            ("sec_location", SECTION_LOCATION),
        ]

        for sec_key, keys in sections:
            active_keys = [k for k in keys if data.get(k)]
            if not active_keys:
                continue
            card = self._create_section_card(sec_key, active_keys, data)
            self.rows.insertWidget(self.rows.count() - 1, card)

    def _create_section_card(self, sec_key: str, keys: list[str], data: dict[str, str]) -> QFrame:
        card = QFrame()
        card.setObjectName("detailsCard")
        is_dark = self._dark
        card_bg = "rgba(255, 255, 255, 0.04)" if is_dark else "rgba(0, 0, 0, 0.03)"
        border_col = "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(0, 0, 0, 0.07)"
        colors = self._colors()
        title_col = colors["accent_text"]
        key_col = colors["text_muted"]
        val_col = colors["text"]

        card.setStyleSheet(
            f"QFrame#detailsCard {{"
            f"  background: {card_bg};"
            f"  border: 1px solid {border_col};"
            f"  border-radius: 8px;"
            f"}}"
        )
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(10, 8, 10, 10)
        card_lay.setSpacing(6)

        # Section Title
        sec_title = QLabel(self._labels.get(sec_key, sec_key))
        sec_title.setStyleSheet(
            f"font-size: 11px; font-weight: 700; text-transform: uppercase; "
            f"letter-spacing: 0.8px; color: {title_col}; background: transparent; border: none;"
        )
        card_lay.addWidget(sec_title)

        # Separator line
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {border_col}; border: none;")
        card_lay.addWidget(sep)

        # Fields
        for k in keys:
            v = str(data[k])
            row_box = QWidget()
            row_box.setStyleSheet("background: transparent; border: none;")
            row_lay = QVBoxLayout(row_box)
            row_lay.setContentsMargins(0, 2, 0, 2)
            row_lay.setSpacing(1)

            lbl_k = QLabel(self._labels.get(k, k))
            lbl_k.setStyleSheet(f"font-size: 11px; color: {key_col}; background: transparent; border: none;")

            long_unbreakable = " " not in v and len(v) > 24
            lbl_v = _ElidedValue(v) if long_unbreakable else QLabel(v)
            lbl_v.setStyleSheet(
                f"font-size: 12px; font-weight: 500; color: {val_col}; "
                f"background: transparent; border: none;"
            )
            lbl_v.setWordWrap(True)
            lbl_v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

            row_lay.addWidget(lbl_k)
            row_lay.addWidget(lbl_v)

            # Special actions
            if k == "folder":
                folder_path = v
                btn_open = QPushButton(self._labels.get("open_dir", "Klasörde Göster ↗"))
                btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_open.setStyleSheet(
                    f"font-size: 11px; font-weight: 600; color: {title_col}; "
                    "background: transparent; border: none; text-align: left; padding: 2px 0;"
                )
                btn_open.clicked.connect(lambda _=None, f=folder_path: QDesktopServices.openUrl(QUrl.fromLocalFile(f)))
                row_lay.addWidget(btn_open)

            elif k == "gps":
                coords = v
                btn_map = QPushButton(self._labels.get("open_in_maps", "Haritada Göster ↗"))
                btn_map.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_map.setStyleSheet(
                    f"font-size: 11px; font-weight: 600; color: {title_col}; "
                    "background: transparent; border: none; text-align: left; padding: 2px 0;"
                )

                def _open_map(_=None, c=coords) -> None:
                    QDesktopServices.openUrl(QUrl(f"https://www.google.com/maps?q={c.replace(' ', '')}"))

                btn_map.clicked.connect(_open_map)
                row_lay.addWidget(btn_map)

            card_lay.addWidget(row_box)

        return card

    def _clear_rows(self) -> None:
        while self.rows.count() > 1:
            item = self.rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
