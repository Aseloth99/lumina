"""Uzaktan duyuru satırı: yalnızca sunucu bir duyuru verdiyse ve kullanıcı kapatmadıysa görünür.

FlexiKey/MediaCompressor/TransferTCP'deki `notice_bar.py` ile aynı kalıp: pencerenin üstünde tek satır, ✕ ile
kapatılana dek kalır. Kasten renk kuralı koymaz: yazı rengi uygulamanın o anki temasından miras alınır (iki temada da
okunur), zemin yarı saydam gridir. Duyuru düz metin olarak gösterilir (HTML yorumlanmaz); satıra tıklamak (güvenilir bir
adres verildiyse) tarayıcıda açar, ✕ kapatır.
"""

from __future__ import annotations

from PyQt6.QtCore import QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy

from app.core.remote_notice import Notice, is_trusted_url


class NoticeBar(QFrame):
    dismissed = pyqtSignal(str)  # duyuru kimliği

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("notice_bar")
        self._notice: Notice | None = None
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)  # yalnızca yazı kadar yer kaplar
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 10, 6)
        row.setSpacing(8)
        self.text_label = QLabel(self)
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)
        self.text_label.setWordWrap(True)
        self.text_label.setOpenExternalLinks(False)
        self.arrow = QLabel("↗", self)
        self.close_label = QLabel("✕", self)
        self.close_label.setCursor(Qt.CursorShape.PointingHandCursor)
        for label in (self.arrow, self.close_label):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.text_label, 1)
        row.addWidget(self.arrow, 0)
        row.addWidget(self.close_label, 0)
        self.hide()

    def show_notice(self, notice: Notice) -> None:
        self._notice = notice
        self.text_label.setText(notice.text)
        has_link = bool(notice.url) and is_trusted_url(notice.url)
        self.arrow.setVisible(has_link)
        self.setCursor(Qt.CursorShape.PointingHandCursor if has_link else Qt.CursorShape.ArrowCursor)
        edge = "#E0A030" if notice.level == "warn" else "rgba(127, 127, 127, 140)"
        self.setStyleSheet(
            "QFrame#notice_bar { background: rgba(127, 127, 127, 38); border-radius: 8px;"
            f" border-left: 3px solid {edge}; }}"
        )
        self.show()

    def current(self) -> Notice | None:
        return self._notice

    def dismiss(self) -> None:
        notice, self._notice = self._notice, None
        self.hide()
        if notice is not None:
            self.dismissed.emit(notice.id)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._notice is None:
            return super().mousePressEvent(event)
        if self.childAt(event.position().toPoint()) is self.close_label:
            self.dismiss()
        elif self._notice.url and is_trusted_url(self._notice.url):
            QDesktopServices.openUrl(QUrl(self._notice.url))
        else:
            super().mousePressEvent(event)
