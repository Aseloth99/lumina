"""Tıklanınca tarayıcıda açılan, altı çizili bağlantı yazısı (yalnızca güvenilir macik.com.tr adresleri)."""

from __future__ import annotations

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QLabel

from app.core.remote_notice import is_trusted_url


class LinkLabel(QLabel):
    def __init__(self, text: str, url: str, parent=None):
        super().__init__(text, parent)
        self._url = url
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(url)
        self.refresh_style()

    def refresh_style(self) -> None:
        """Bağlantı rengi seçili vurgudan (koyu/açık zeminde 4,5:1 okunur ton)."""
        from app.resources import theme

        color = theme.active_palette()["accent_text"]
        self.setStyleSheet(f"color: {color}; font-size: 13px; text-decoration: underline; background: transparent;")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and is_trusted_url(self._url):
            QDesktopServices.openUrl(QUrl(self._url))
        else:
            super().mousePressEvent(event)
