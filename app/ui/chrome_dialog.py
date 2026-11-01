"""Özel başlık çubuklu (çerçevesiz) pencere gövdesi: Lumina'da Ayarlar ve Güncelleme penceresi (MediaCompressor'daki aynı kalıp).

Ana pencerenin çerçevesiyle aynı görünüm (12 px köşe, 2 px vurgu üst çizgisi, 38 px başlık çubuğu: uygulama simgesi +
başlık + ✕). Çerçeve çizimi, başlık çubuğu ve palet `message_box` modülündeki ortak kutudan gelir (uygulamanın temasını
`message_box.configure(palette_provider=...)` ile okur); bu sınıf yalnızca içerik taşıyan pencereler için "boş gövde" sunar:

    class MyDialog(ChromeDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Başlık")
            layout = QVBoxLayout(self.body_host)      # içerik burada kurulur (QDialog'un kendisine değil)

Boyutlar, işletim sistemi çerçevesindeki gibi "içerik boyutu" olarak verilir: `resize/setMinimumSize/setMinimumWidth`
çevresindeki 8 px'lik gölge payını kendisi ekler. `resizable=True` pencerelerde (Lumina'da kullanılmıyor) başlıkta büyüt/geri al düğmesi
olur ve kenarlardan (gölge payından) sistem yeniden boyutlandırması başlatılır. Tema/vurgu pencere açıkken değişirse
(Ayarlar canlı önizleme) çerçeve renkleri anında güncellenir.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QAbstractButton, QApplication, QDialog, QFrame, QScrollArea, QVBoxLayout, QWidget

from .message_box import (  # noqa: PLC2701 (ortak modülün çerçeve parçaları; aynı projede, testle korunur)
    _SHADOW,
    _TITLE_H,
    _ChromeDialog,
    _TitleBar,
    _plain,
    button_text,
    current_palette,
)

_EDGE = Qt.Edge


def _dialog_qss(p: dict) -> str:
    """Yalnızca çerçeve için: kök saydam, çerçeve kapları saydam, başlık metni. İçerikteki düğme/kutu görünümü
    uygulamanın kendi temasından gelir (eski yerel pencerelerle aynı)."""
    return f"""
        QDialog#mb_root {{ background: transparent; border: none; }}
        QWidget[mb="1"] {{ background: transparent; border: none; color: {p['text']}; }}
        QLabel#mb_title_label {{ color: {p['text']}; font-size: 9.5pt; font-weight: 600; }}
    """


class _MaxButton(QAbstractButton):
    """Başlıkta büyüt/geri al düğmesi: ana pencerenin başlığıyla aynı glif çifti (□ / ❐)."""

    def __init__(self, pal: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._pal = pal
        self._maximized = False
        self.setFixedSize(32, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        _plain(self, "mb_max")

    def set_maximized(self, maximized: bool) -> None:
        self._maximized = bool(maximized)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self.underMouse() or self.isDown():
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), 6, 6)
            painter.fillPath(path, QColor(self._pal["surface"] if not self.isDown() else self._pal["sunken"]))
        painter.setPen(QColor(self._pal["text"]))
        font = QFont("Segoe UI")
        font.setPointSize(13)
        font.setWeight(QFont.Weight.Light)
        painter.setFont(font)
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), "❐" if self._maximized else "□")

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class _FitScrollArea(QScrollArea):
    """İçeriğinin doğal boyutunu önerir (QScrollArea varsayılanı ~36x24 satır yüksekliğine kırpar) ve gerekirse küçülebilir:
    ekrandan uzun içerik (küçük ekranda Ayarlar/Gelişmiş) pencereyi ekran dışına taşırmak yerine kaydırılır."""

    def sizeHint(self) -> QSize:  # noqa: N802
        widget = self.widget()
        if widget is None:
            return super().sizeHint()
        hint = widget.sizeHint()
        frame = 2 * self.frameWidth()
        return QSize(hint.width() + frame, hint.height() + frame)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        widget = self.widget()
        width = widget.minimumSizeHint().width() if widget is not None else 0
        return QSize(width + 2 * self.frameWidth(), 160)


class ChromeDialog(_ChromeDialog):
    """Özel başlık çubuklu içerik penceresi (bkz. modül açıklaması). İçerik `self.body_host` içine kurulur."""

    _cd_ready = False  # kurulum bitene dek changeEvent/_invalidate dokunmasın

    def __init__(self, parent: QWidget | None = None, *, resizable: bool = False, scroll: bool = False):
        super().__init__(parent)
        self._cd_resizable = bool(resizable)
        self._cd_scroll: QScrollArea | None = None
        self._cd_refreshing = False
        self._cd_edges = _EDGE(0)
        self.setStyleSheet(_dialog_qss(self._pal))
        inner = self._inner = _plain(QWidget(self), "mb_inner")
        self._outer.addWidget(inner)
        self._cd_root = QVBoxLayout(inner)
        self._cd_root.setContentsMargins(0, 0, 0, 0)
        self._cd_root.setSpacing(0)
        self._cd_bar: _TitleBar | None = None
        self._cd_max: _MaxButton | None = None
        self.body_host = _plain(QWidget(inner), "cd_body")
        self._cd_root.addWidget(self._cd_bar_widget())
        if scroll:
            # İçerik (body_host) kaydırılabilir alanın içinde: ekrana sığmazsa dikey kaydırma çubuğu çıkar
            area = self._cd_scroll = _FitScrollArea(inner)
            area.setObjectName("cd_scroll")
            area.setProperty("mb", "1")
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setWidgetResizable(True)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            area.viewport().setAutoFillBackground(False)
            area.viewport().setProperty("mb", "1")
            area.setWidget(self.body_host)
            self._cd_root.addWidget(area, 1)
        else:
            self._cd_root.addWidget(self.body_host, 1)
        if self._cd_resizable:
            self.setMouseTracking(True)
        self._cd_ready = True

    # ------------------------------------------------------------------ başlık çubuğu
    def _cd_bar_widget(self) -> QWidget:
        bar = _TitleBar(self, self._pal, self._title_text, self._title_icon(), self._inner)
        # ✕ = işletim sistemi çerçevesindeki X gibi close(): closeEvent temizlikleri çalışır, QDialog.closeEvent reject()'i çağırır
        bar.close_btn.clicked.disconnect()
        bar.close_btn.clicked.connect(lambda _checked=False: self.close())
        self._cd_bar = bar
        self._cd_max = None
        if self._cd_resizable:
            self._cd_max = _MaxButton(self._pal, bar)
            self._cd_max.clicked.connect(self.toggle_maximized)
            self._cd_max.set_maximized(self.isMaximized())
            lay = bar.layout()
            lay.insertWidget(lay.indexOf(bar.close_btn), self._cd_max, 0, Qt.AlignmentFlag.AlignVCenter)
        return bar

    def _invalidate(self) -> None:
        """Başlık ya da simge değişti: başlık çubuğunu yeniden kur (yalnızca kurulduktan sonra)."""
        if not self._cd_ready:
            return
        old = self._cd_bar
        new = self._cd_bar_widget()
        self._cd_root.replaceWidget(old, new)
        old.setParent(None)
        old.deleteLater()

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # ------------------------------------------------------------------ boyut: "içerik boyutu" anlamı
    @staticmethod
    def _padded(width: int, height: int | None = None):
        extra = 2 * _SHADOW
        return (width + extra, (height + extra) if height is not None else None)

    def resize(self, *args) -> None:  # noqa: A003
        if len(args) == 1 and isinstance(args[0], QSize):
            width, height = args[0].width(), args[0].height()
        else:
            width, height = args
        width, height = self._padded(width, height)
        super().resize(width, height)

    def setMinimumSize(self, *args) -> None:  # noqa: N802
        if len(args) == 1 and isinstance(args[0], QSize):
            width, height = args[0].width(), args[0].height()
        else:
            width, height = args
        width, height = self._padded(width, height)
        super().setMinimumSize(width, height)

    def setMinimumWidth(self, width: int) -> None:  # noqa: N802
        super().setMinimumWidth(width + 2 * _SHADOW)

    def setMinimumHeight(self, height: int) -> None:  # noqa: N802
        super().setMinimumHeight(height + 2 * _SHADOW)

    def minimumWidth(self) -> int:  # noqa: N802
        return max(0, super().minimumWidth() - 2 * _SHADOW)  # "içerik boyutu" (gölge payı hariç), setMinimumWidth ile simetrik

    def minimumHeight(self) -> int:  # noqa: N802
        return max(0, super().minimumHeight() - 2 * _SHADOW)

    def setFixedSize(self, *args) -> None:  # noqa: N802
        if len(args) == 1 and isinstance(args[0], QSize):
            width, height = args[0].width(), args[0].height()
        else:
            width, height = args
        width, height = self._padded(width, height)
        super().setFixedSize(width, height)

    # ------------------------------------------------------------------ gösterim
    def _prepare_geometry(self) -> None:
        """Açılış boyutu ve konumu: içeriğin doğal boyutu, ama kullanılabilir ekran alanını aşmaz (aşan içerik kaydırılır)."""
        self.ensurePolished()
        avail = self._screen().availableGeometry()
        if not self.testAttribute(Qt.WidgetAttribute.WA_Resized):
            if self._cd_scroll is not None:
                hint = self.sizeHint()  # _FitScrollArea sayesinde kırpılmamış doğal boyut
                height = min(hint.height(), int(avail.height() * 0.92))
                width = hint.width()
                if height < hint.height():  # kaydırma çubuğuna yer aç
                    width += self._cd_scroll.verticalScrollBar().sizeHint().width() + 6
                QDialog.resize(self, max(self.minimumWidth() + 2 * _SHADOW, width), height)
            else:
                self.adjustSize()  # Qt bunu gösterirken yapar; ortalamak için boyut önceden bilinmeli
        # Her durumda ekrandan büyük açılmasın (küçük ekran / büyük sabit boyut)
        width = min(self.width(), max(avail.width() - 16, self.minimumSize().width()))
        height = min(self.height(), max(avail.height() - 16, self.minimumSize().height()))
        if (width, height) != (self.width(), self.height()):
            QDialog.resize(self, width, height)
        self._center()

    def exec(self) -> int:  # noqa: A003
        if not self.isVisible():
            self._prepare_geometry()
        return super().exec()

    def open(self) -> None:  # noqa: A003
        if not self.isVisible():
            self._prepare_geometry()
        super().open()

    # ------------------------------------------------------------------ tema/vurgu canlı değişimi
    def _cd_refresh_palette(self) -> None:
        if self._cd_refreshing:
            return
        fresh = current_palette()
        if fresh == self._pal:
            return
        self._cd_refreshing = True
        try:
            self._pal = fresh
            self.setStyleSheet(_dialog_qss(fresh))
            self._invalidate()
            self._palette_refreshed()
            self.update()
        finally:
            self._cd_refreshing = False

    def _palette_refreshed(self) -> None:
        """Tema/vurgu değişti (self._pal güncel): alt sınıflar renge bağlı simgelerini burada yeniler."""

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if not self._cd_ready:
            return
        kind = event.type()
        if kind == QEvent.Type.StyleChange:
            self._cd_refresh_palette()
        elif kind == QEvent.Type.WindowStateChange:
            maximized = self.isMaximized()
            margin = 0 if maximized else _SHADOW
            self._outer.setContentsMargins(margin, margin, margin, margin)
            if self._cd_max is not None:
                self._cd_max.set_maximized(maximized)

    # ------------------------------------------------------------------ çizim (büyütülünce düz)
    def paintEvent(self, event) -> None:  # noqa: N802
        if not self.isMaximized():
            super().paintEvent(event)
            return
        pal = self._pal
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(pal["base"]))
        painter.fillRect(0, 0, self.width(), _TITLE_H, QColor(pal["raised"]))
        painter.fillRect(0, _TITLE_H - 1, self.width(), 1, QColor(pal["edge"]))
        painter.fillRect(0, 0, self.width(), 2, QColor(pal["accent"]))

    # ------------------------------------------------------------------ kenardan yeniden boyutlandırma
    def _cd_edges_at(self, pos: QPoint):
        if not self._cd_resizable or self.isMaximized():
            return _EDGE(0)
        margin, corner = _SHADOW, 18
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        edges = _EDGE(0)
        if x < margin or (x < corner and (y < margin or y >= h - margin)):
            edges |= _EDGE.LeftEdge
        if x >= w - margin or (x >= w - corner and (y < margin or y >= h - margin)):
            edges |= _EDGE.RightEdge
        if y < margin or (y < corner and (x < margin or x >= w - margin)):
            edges |= _EDGE.TopEdge
        if y >= h - margin or (y >= h - corner and (x < margin or x >= w - margin)):
            edges |= _EDGE.BottomEdge
        return edges

    @staticmethod
    def _cd_cursor_for(edges) -> Qt.CursorShape:
        horizontal = bool(edges & (_EDGE.LeftEdge | _EDGE.RightEdge))
        vertical = bool(edges & (_EDGE.TopEdge | _EDGE.BottomEdge))
        if horizontal and vertical:
            diagonal_back = bool(edges & _EDGE.LeftEdge) == bool(edges & _EDGE.TopEdge)
            return Qt.CursorShape.SizeFDiagCursor if diagonal_back else Qt.CursorShape.SizeBDiagCursor
        return Qt.CursorShape.SizeHorCursor if horizontal else Qt.CursorShape.SizeVerCursor

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._cd_resizable and not (event.buttons() & Qt.MouseButton.LeftButton):
            edges = self._cd_edges_at(event.position().toPoint())
            if edges:
                self.setCursor(self._cd_cursor_for(edges))
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._cd_resizable and event.button() == Qt.MouseButton.LeftButton:
            edges = self._cd_edges_at(event.position().toPoint())
            handle = self.windowHandle()
            if edges and handle is not None and handle.startSystemResize(edges):
                event.accept()
                return
        super().mousePressEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._cd_resizable:
            self.unsetCursor()
        super().leaveEvent(event)


__all__ = ["ChromeDialog", "button_text"]
