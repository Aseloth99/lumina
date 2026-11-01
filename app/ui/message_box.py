"""Ortak özel mesaj kutusu: QMessageBox / QInputDialog yerine (dört PyQt6 uygulamasında aynı dosya).

Ana pencere çerçevesiyle aynı görünüm: çerçevesiz modal, 12 px köşe, 2 px vurgu üst çizgisi, 38 px özel başlık
çubuğu (uygulama simgesi + başlık + ✕), vektör çizilmiş tür simgesi (emoji yok), seçilebilir metin, sağda düğmeler.
Uygulamanın paleti `configure(palette_provider=…)` ile alınır; her kutu açılırken o anki tema/vurgu okunur.

API `QMessageBox` ile uyumludur (statik information/warning/critical/question, örnek kullanımı, enum takma adları);
`InputDialog.getText/getInt/getDouble/getItem` de aynı çerçeveyle gelir. Metin kesilmez: uzun metin kaydırılır
(en çok ekranın %70'i), boşluksuz uzun sözcükler (dosya yolu gibi) satır sonunda harf harf bölünür.
Testlerde `MessageBox.set_test_hook(fn)` ile gerçek pencere açılmadan düğme seçilebilir.
"""

from __future__ import annotations

import math
from typing import Callable

from PyQt6.QtCore import QEvent, QLocale, QPointF, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextOption,
)
from PyQt6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

__all__ = [
    "MessageBox",
    "InputDialog",
    "ElidedLabel",
    "StandardButton",
    "Icon",
    "ButtonRole",
    "configure",
    "reset_configuration",
    "current_palette",
    "button_text",
]

# QMessageBox ile aynı sabitler (takma ad): çağıran kod `MessageBox.StandardButton.Yes` yazabilir
StandardButton = QMessageBox.StandardButton
Icon = QMessageBox.Icon
ButtonRole = QMessageBox.ButtonRole

# ---------------------------------------------------------------------------------------------------------------
# Ölçüler
# ---------------------------------------------------------------------------------------------------------------
_SHADOW = 8  # pencerenin çerçeve dışı (gölge) payı
_RADIUS = 12
_TITLE_H = 38
_PAD_X = 20
_PAD_TOP = 18
_ICON_BOX = 40
_ICON_GAP = 16
_MIN_W = 360
_MAX_W = 560
_MAX_H_RATIO = 0.70
_DETAILS_H = 150

# ---------------------------------------------------------------------------------------------------------------
# Yapılandırma
# ---------------------------------------------------------------------------------------------------------------
_CFG: dict = {"palette": None, "icon": None, "accent": None, "lang": None, "texts": None}
_TEST_HOOK = None


def configure(
    palette_provider: Callable[[], dict] | None = None,
    app_icon_provider: Callable[[], QIcon | None] | None = None,
    accent_provider: Callable[[], str] | None = None,
    *,
    lang_provider: Callable[[], str] | None = None,
    button_texts=None,
) -> None:
    """Uygulamanın paletini/simgesini/dilini bağlar (yalnızca verilenler değişir; sıfırlamak için reset_configuration).

    palette_provider() -> dict: base, raised, surface, border, text, text_muted, accent, accent2, danger, warn, hi, lo
    (eksik anahtarlar nötr gri varsayılanla tamamlanır; FlexiKey'in surface2/is_dark anahtarları da anlaşılır).
    accent_provider() -> "#RRGGBB": verilirse paletin vurgusunu ezer. lang_provider() -> "tr" | "en" | "auto".
    button_texts: callable(StandardButton) -> str | None, ya da {StandardButton | "ok": "Tamam"} sözlüğü.
    """
    if palette_provider is not None:
        _CFG["palette"] = palette_provider
    if app_icon_provider is not None:
        _CFG["icon"] = app_icon_provider
    if accent_provider is not None:
        _CFG["accent"] = accent_provider
    if lang_provider is not None:
        _CFG["lang"] = lang_provider
    if button_texts is not None:
        _CFG["texts"] = button_texts


def reset_configuration() -> None:
    """Tüm sağlayıcıları ve test kancasını kaldırır (testler arası temizlik)."""
    global _TEST_HOOK
    for key in _CFG:
        _CFG[key] = None
    _TEST_HOOK = None


# ---------------------------------------------------------------------------------------------------------------
# Metinler (TR/EN) — her kelime büyük harfle başlar
# ---------------------------------------------------------------------------------------------------------------
_TEXTS = {
    "tr": {
        "ok": "Tamam", "cancel": "İptal", "yes": "Evet", "no": "Hayır", "close": "Kapat", "save": "Kaydet",
        "discard": "Kaydetme", "open": "Aç", "apply": "Uygula", "retry": "Tekrar Dene", "ignore": "Yoksay",
        "abort": "Durdur", "yes_to_all": "Tümüne Evet", "no_to_all": "Tümüne Hayır", "save_all": "Tümünü Kaydet",
        "help": "Yardım", "reset": "Sıfırla", "restore_defaults": "Varsayılanları Geri Yükle", "delete": "Sil",
        "details_show": "Ayrıntıları Göster", "details_hide": "Ayrıntıları Gizle",
        "copy": "Kopyala", "select_all": "Tümünü Seç", "close_tip": "Kapat",
    },
    "en": {
        "ok": "OK", "cancel": "Cancel", "yes": "Yes", "no": "No", "close": "Close", "save": "Save",
        "discard": "Don't Save", "open": "Open", "apply": "Apply", "retry": "Retry", "ignore": "Ignore",
        "abort": "Abort", "yes_to_all": "Yes to All", "no_to_all": "No to All", "save_all": "Save All",
        "help": "Help", "reset": "Reset", "restore_defaults": "Restore Defaults", "delete": "Delete",
        "details_show": "Show Details", "details_hide": "Hide Details",
        "copy": "Copy", "select_all": "Select All", "close_tip": "Close",
    },
}

_SB = QMessageBox.StandardButton
_STD_KEYS = {
    _SB.Ok: "ok", _SB.Cancel: "cancel", _SB.Yes: "yes", _SB.No: "no", _SB.Close: "close", _SB.Save: "save",
    _SB.Discard: "discard", _SB.Open: "open", _SB.Apply: "apply", _SB.Retry: "retry", _SB.Ignore: "ignore",
    _SB.Abort: "abort", _SB.YesToAll: "yes_to_all", _SB.NoToAll: "no_to_all", _SB.SaveAll: "save_all",
    _SB.Help: "help", _SB.Reset: "reset", _SB.RestoreDefaults: "restore_defaults",
}
# Qt'nin kendi eşlemesi: standart düğme -> rol
_BR = QMessageBox.ButtonRole
_STD_ROLES = {
    _SB.Ok: _BR.AcceptRole, _SB.Open: _BR.AcceptRole, _SB.Save: _BR.AcceptRole, _SB.SaveAll: _BR.AcceptRole,
    _SB.Retry: _BR.AcceptRole, _SB.Ignore: _BR.AcceptRole, _SB.Cancel: _BR.RejectRole, _SB.Close: _BR.RejectRole,
    _SB.Abort: _BR.RejectRole, _SB.Discard: _BR.DestructiveRole, _SB.Apply: _BR.ApplyRole,
    _SB.Reset: _BR.ResetRole, _SB.RestoreDefaults: _BR.ResetRole, _SB.Help: _BR.HelpRole, _SB.Yes: _BR.YesRole,
    _SB.YesToAll: _BR.YesRole, _SB.No: _BR.NoRole, _SB.NoToAll: _BR.NoRole,
}
# Soldan sağa dizilim: onaylayanlar önce, vazgeçenler sona (Windows düzeni: Evet Hayır İptal)
_ROLE_ORDER = {
    _BR.AcceptRole: 0, _BR.YesRole: 1, _BR.ActionRole: 2, _BR.ApplyRole: 3, _BR.DestructiveRole: 4,
    _BR.NoRole: 5, _BR.ResetRole: 6, _BR.RejectRole: 7, _BR.HelpRole: 8, _BR.InvalidRole: 9,
}
_STD_ORDER = [
    _SB.Ok, _SB.Open, _SB.Save, _SB.SaveAll, _SB.Yes, _SB.YesToAll, _SB.Abort, _SB.Retry, _SB.Ignore, _SB.Apply,
    _SB.No, _SB.NoToAll, _SB.Discard, _SB.Reset, _SB.RestoreDefaults, _SB.Close, _SB.Cancel, _SB.Help,
]


def _lang() -> str:
    code = ""
    provider = _CFG["lang"]
    if provider is not None:
        try:
            code = str(provider() or "")
        except Exception:
            code = ""
    code = code.strip().lower()
    if not code or code == "auto":
        code = QLocale.system().name().lower()  # tr_TR
    return "tr" if code.startswith("tr") else "en"


def button_text(button, lang: str | None = None) -> str:
    """Yerelleştirilmiş düğme/arayüz metni. `button`: StandardButton ya da tablo anahtarı ("delete", "details_show"…)."""
    key = button if isinstance(button, str) else _STD_KEYS.get(button, "")
    texts = _CFG["texts"]
    if texts is not None:
        try:
            got = None
            if isinstance(texts, dict):
                got = texts.get(button) or texts.get(key)
            elif callable(texts) and not isinstance(button, str):
                got = texts(button)
            if got:
                return str(got)
        except Exception:
            pass
    table = _TEXTS.get(lang or _lang(), _TEXTS["en"])
    return table.get(key, key)


# ---------------------------------------------------------------------------------------------------------------
# Renk yardımcıları ve palet
# ---------------------------------------------------------------------------------------------------------------
_DEFAULT_DARK = {
    "base": "#1a1a1a", "raised": "#2a2a2a", "sunken": "#1f1f1f", "surface": "#252525", "border": "#3a3a3a",
    "text": "#e8e8e8", "text_muted": "#9e9e9e", "accent": "#2196F3", "accent2": "#1976D2", "accent_hi": "#42A5F5",
    "danger": "#d32f2f", "danger_hi": "#f44336", "warn": "#FFC107", "hi": "#3a3a3a", "lo": "#0f0f0f",
}
_DEFAULT_LIGHT = {
    "base": "#e0e0e0", "raised": "#f5f5f5", "sunken": "#d8d8d8", "surface": "#ececec", "border": "#c5c5c5",
    "text": "#1a1a1a", "text_muted": "#616161", "accent": "#2196F3", "accent2": "#1976D2", "accent_hi": "#90CAF9",
    "danger": "#e53935", "danger_hi": "#ff5252", "warn": "#8A5300", "hi": "#ffffff", "lo": "#bdbdbd",
}
# FlexiKey gibi farklı adlandıran paletler için eş anlamlılar
_ALIASES = {
    "raised": ("raised", "surface2", "surface"),
    "surface": ("surface", "raised"),
    "sunken": ("sunken",),
    "border": ("border", "border_strong"),
    "text_muted": ("text_muted", "muted", "text_secondary"),
    "danger_hi": ("danger_hi", "danger_hover"),
}


def _color(value) -> QColor | None:
    if isinstance(value, QColor):
        return QColor(value) if value.isValid() else None
    if isinstance(value, str) and value.strip():
        c = QColor(value.strip())
        return c if c.isValid() else None
    return None


def _lum(c: QColor) -> float:
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF()) + 0.0722 * lin(c.blueF())


def _contrast(a: QColor, b: QColor) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """`a`'dan `b`'ye t oranında karışım (opak)."""
    return QColor(*(round(x * (1 - t) + y * t) for x, y in zip(a.getRgb()[:3], b.getRgb()[:3])))


def _on(fill: QColor) -> QColor:
    """Dolgunun üstüne okunur metin/işaret rengi: beyaz, beyazın kontrastı 3'ün altındaysa koyu."""
    white, dark = QColor("#ffffff"), QColor("#1a1a1a")
    if _contrast(fill, white) >= 3.0 or _contrast(fill, white) >= _contrast(fill, dark):
        return white
    return dark


def _system_is_dark() -> bool:
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except Exception:
        pass
    try:
        return QGuiApplication.palette().window().color().lightness() < 128
    except Exception:
        return True


def current_palette() -> dict:
    """Uygulama paletinin kutu için normalleştirilmiş hâli (her anahtar `#rrggbb`; `is_dark` bool)."""
    raw = None
    provider = _CFG["palette"]
    if provider is not None:
        try:
            raw = provider()
        except Exception:
            raw = None
    if not isinstance(raw, dict) or not raw:
        raw = {}
    base_c = _color(raw.get("base"))
    if isinstance(raw.get("is_dark"), bool):
        dark = raw["is_dark"]
    elif base_c is not None:
        dark = _lum(base_c) < 0.35
    else:
        dark = _system_is_dark()
    out = dict(_DEFAULT_DARK if dark else _DEFAULT_LIGHT)
    for key in list(out):
        for alias in _ALIASES.get(key, (key,)):
            c = _color(raw.get(alias))
            if c is not None:
                out[key] = c.name()
                break
    # Verilmeyen türev renkler vurgudan/zeminden üretilir
    acc = QColor(out["accent"])
    if _color(raw.get("accent")) is not None:
        if _color(raw.get("accent2")) is None:
            out["accent2"] = acc.darker(115).name()
        if _color(raw.get("accent_hi")) is None:
            out["accent_hi"] = acc.lighter(125).name()
    if _color(raw.get("danger")) is not None and not any(_color(raw.get(a)) for a in _ALIASES["danger_hi"]):
        out["danger_hi"] = QColor(out["danger"]).lighter(118).name()
    if _color(raw.get("sunken")) is None and base_c is not None:
        out["sunken"] = _mix(QColor(out["base"]), QColor("#000000"), 0.06 if dark else 0.04).name()
    accent_provider = _CFG["accent"]
    if accent_provider is not None:
        try:
            override = _color(accent_provider())
        except Exception:
            override = None
        if override is not None:
            out["accent"] = override.name()
            out["accent2"] = override.darker(115).name()
            out["accent_hi"] = override.lighter(125).name()
    for key, value in list(out.items()):
        c = _color(value)
        if c is not None and key != "is_dark":
            out[key] = c.name()
    out["is_dark"] = bool(dark)
    out["muted"] = out["text_muted"]
    # Kenar çizgisi zeminden ayırt edilemiyorsa (MC koyu temada border == base) hi/metin karışımına geç
    base, edge = QColor(out["base"]), QColor(out["border"])
    if abs(_lum(base) - _lum(edge)) < 0.02:
        alt = QColor(out["hi"])
        edge = alt if abs(_lum(base) - _lum(alt)) >= 0.02 else _mix(base, QColor(out["text"]), 0.18)
    out["edge"] = edge.name()
    out["on_accent"] = _on(QColor(out["accent2"])).name()
    out["on_accent_hi"] = _on(QColor(out["accent"])).name()
    out["on_danger"] = _on(QColor(out["danger"])).name()
    return out


def _qss(p: dict) -> str:
    return f"""
        QDialog#mb_root {{ background: transparent; border: none; }}
        QWidget[mb="1"] {{ background: transparent; border: none; color: {p['text']}; }}
        QLabel#mb_title_label {{ color: {p['text']}; font-size: 9.5pt; font-weight: 600; }}
        QTextBrowser#mb_text {{
            background: transparent; border: none; padding: 0px; color: {p['text']}; font-size: 10pt;
            selection-background-color: {p['accent']}; selection-color: {p['on_accent_hi']};
        }}
        QTextBrowser#mb_text[emph="1"] {{ font-weight: 600; }}
        QTextBrowser#mb_info {{
            background: transparent; border: none; padding: 0px; color: {p['text']}; font-size: 9.5pt;
            selection-background-color: {p['accent']}; selection-color: {p['on_accent_hi']};
        }}
        QPlainTextEdit#mb_details {{
            background-color: {p['sunken']}; color: {p['text']}; border: 1px solid {p['edge']}; border-radius: 8px;
            padding: 6px 8px; font-family: Consolas, "Cascadia Mono", Menlo, "DejaVu Sans Mono", monospace;
            font-size: 9pt; selection-background-color: {p['accent']}; selection-color: {p['on_accent_hi']};
        }}
        QLineEdit#mb_input, QSpinBox#mb_input, QDoubleSpinBox#mb_input {{
            background-color: {p['sunken']}; color: {p['text']}; border: 1px solid {p['edge']}; border-radius: 8px;
            padding: 0px 10px; min-height: 32px; font-size: 10pt;
            selection-background-color: {p['accent']}; selection-color: {p['on_accent_hi']};
        }}
        QLineEdit#mb_input:focus, QSpinBox#mb_input:focus, QDoubleSpinBox#mb_input:focus {{
            border: 1px solid {p['accent']};
        }}
        QComboBox#mb_input {{
            background-color: {p['sunken']}; color: {p['text']}; border: 1px solid {p['edge']}; border-radius: 8px;
            padding: 0px 10px; min-height: 32px; font-size: 10pt;
            selection-background-color: {p['accent']}; selection-color: {p['on_accent_hi']};
        }}
        QComboBox#mb_input:focus, QComboBox#mb_input:on {{ border: 1px solid {p['accent']}; }}
        QComboBox#mb_input QAbstractItemView {{
            background-color: {p['raised']}; color: {p['text']}; border: 1px solid {p['edge']}; outline: none;
            selection-background-color: {p['accent']}; selection-color: {p['on_accent_hi']};
        }}
        QPushButton#mb_btn {{
            background-color: {p['raised']}; color: {p['text']}; border: 1px solid {p['edge']}; border-radius: 8px;
            padding: 0px 18px; min-height: 32px; min-width: 76px; font-size: 10pt; font-weight: 600;
        }}
        QPushButton#mb_btn:hover {{ background-color: {p['surface']}; border: 1px solid {p['accent']}; }}
        QPushButton#mb_btn:pressed {{ background-color: {p['sunken']}; }}
        QPushButton#mb_btn:focus {{ border: 1px solid {p['accent']}; }}
        QPushButton#mb_btn[variant="primary"] {{
            background-color: {p['accent2']}; color: {p['on_accent']}; border: 1px solid {p['accent2']};
        }}
        QPushButton#mb_btn[variant="primary"]:hover {{ background-color: {p['accent']}; color: {p['on_accent_hi']}; border: 1px solid {p['accent']}; }}
        QPushButton#mb_btn[variant="primary"]:pressed {{ background-color: {p['accent2']}; color: {p['on_accent']}; }}
        QPushButton#mb_btn[variant="danger"] {{
            background-color: {p['danger']}; color: {p['on_danger']}; border: 1px solid {p['danger']};
        }}
        QPushButton#mb_btn[variant="danger"]:hover {{ background-color: {p['danger_hi']}; border: 1px solid {p['danger_hi']}; }}
        QPushButton#mb_link {{
            background: transparent; border: none; border-radius: 6px; color: {p['text_muted']};
            padding: 0px 6px; min-height: 28px; font-size: 9.5pt; font-weight: 600;
        }}
        QPushButton#mb_link:hover {{ color: {p['text']}; background-color: {p['surface']}; }}
        QPushButton#mb_link:focus {{ color: {p['text']}; }}
        QDialog#mb_root QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0px; }}
        QDialog#mb_root QScrollBar::handle:vertical {{
            background: {p['edge']}; border-radius: 4px; min-height: 24px;
        }}
        QDialog#mb_root QScrollBar::handle:vertical:hover {{ background: {p['accent']}; }}
        QDialog#mb_root QScrollBar::add-line:vertical, QDialog#mb_root QScrollBar::sub-line:vertical {{
            height: 0px; width: 0px; background: none; border: none;
        }}
        QDialog#mb_root QScrollBar::add-page:vertical, QDialog#mb_root QScrollBar::sub-page:vertical {{ background: none; }}
    """


def _plain(widget: QWidget, name: str = "") -> QWidget:
    """Kutu içindeki çıplak kapsayıcıyı uygulamanın genel QWidget/QLabel kurallarından korur (saydam kalır)."""
    if name:
        widget.setObjectName(name)
    widget.setProperty("mb", "1")
    return widget


# ---------------------------------------------------------------------------------------------------------------
# Parçalar
# ---------------------------------------------------------------------------------------------------------------
class ElidedLabel(QLabel):
    """Tek satırlık etiket; sığmazsa sonu "…" ile kısalır (tam metin ipucunda)."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self._color: QColor | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_color(self, color: QColor) -> None:
        self._color = color
        self.update()

    def minimumSizeHint(self) -> QSize:
        return QSize(24, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        full = self.text()
        elided = self.fontMetrics().elidedText(full, Qt.TextElideMode.ElideRight, max(0, self.width()))
        self.setToolTip(full if elided != full else "")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        shown = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, max(0, self.width()))
        painter.setPen(self._color if self._color is not None else self.palette().windowText().color())
        flags = (Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft).value
        painter.drawText(self.rect(), flags, shown)


class _CloseButton(QAbstractButton):
    """Başlık çubuğu ✕ düğmesi: vektör çizim, üzerine gelince kırmızı."""

    def __init__(self, pal: dict, tooltip: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._pal = pal
        self.setFixedSize(32, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        _plain(self, "mb_close")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hot = self.underMouse() or self.isDown()
        if hot:
            fill = QColor(self._pal["danger"] if not self.isDown() else QColor(self._pal["danger"]).darker(120).name())
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), 6, 6)
            painter.fillPath(path, fill)
            ink = QColor(self._pal["on_danger"])
        else:
            ink = QColor(self._pal["text"])
        pen = QPen(ink, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        cx, cy, d = self.width() / 2, self.height() / 2, 4.5
        painter.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
        painter.drawLine(QPointF(cx + d, cy - d), QPointF(cx - d, cy + d))

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class _TitleBar(QWidget):
    """38 px başlık: simge (16 px) + tek satır başlık + ✕. Sürüklenince pencereyi taşır."""

    def __init__(self, dialog: QDialog, pal: dict, title: str, icon: QIcon | None, parent: QWidget):
        super().__init__(parent)
        _plain(self, "mb_title")
        self.setFixedHeight(_TITLE_H)
        self._dialog = dialog
        self._drag = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 6, 0)
        lay.setSpacing(8)
        self.icon_label = None
        if icon is not None and not icon.isNull():
            self.icon_label = _plain(QLabel(self), "mb_title_icon")
            self.icon_label.setFixedSize(16, 16)
            self.icon_label.setPixmap(_icon_pixmap(icon, 16))
            lay.addWidget(self.icon_label)
        self.title_label = ElidedLabel(title, self)
        _plain(self.title_label, "mb_title_label")
        self.title_label.set_color(QColor(pal["text"]))
        lay.addWidget(self.title_label, 1)
        self.close_btn = _CloseButton(pal, button_text("close_tip"), self)
        self.close_btn.clicked.connect(lambda _checked=False: dialog.reject())
        lay.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self._dialog.pos()  # çerçevesiz: pos() == çerçeve köşesi
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._dialog.move(event.globalPosition().toPoint() - self._drag)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        super().mouseReleaseEvent(event)


def _icon_pixmap(icon: QIcon, size: int) -> QPixmap:
    try:
        dpr = QGuiApplication.primaryScreen().devicePixelRatio()
        pix = icon.pixmap(QSize(size, size), dpr)
        pix.setDevicePixelRatio(dpr)
        return pix
    except Exception:
        return icon.pixmap(size, size)


class _TypeIcon(QWidget):
    """Tür simgesi (bilgi/uyarı/hata/soru): QPainter ile vektör çizim, renkler paletten."""

    def __init__(self, kind: str, pal: dict, parent: QWidget | None = None):
        super().__init__(parent)
        _plain(self, "mb_icon")
        self.kind = kind
        self._pal = pal
        self.setFixedSize(_ICON_BOX, _ICON_BOX)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event) -> None:
        pal = self._pal
        fill = QColor({"warning": pal["warn"], "critical": pal["danger"]}.get(self.kind, pal["accent"]))
        ink = _on(fill)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = _ICON_BOX / 40.0
        painter.scale(s, s)  # çizim 40x40 birim kutusunda
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        if self.kind == "warning":
            pen = QPen(fill, 5)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            tri = QPainterPath()
            tri.moveTo(20, 6.5)
            tri.lineTo(36, 34)
            tri.lineTo(4, 34)
            tri.closeSubpath()
            painter.drawPath(tri)
        else:
            painter.drawEllipse(QRectF(3, 3, 34, 34))
        pen = QPen(ink, 3.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.kind == "warning":  # ünlem
            painter.drawLine(QPointF(20, 15), QPointF(20, 24.5))
            self._dot(painter, ink, 20, 29.6, 2.0)
        elif self.kind == "critical":  # ×
            painter.drawLine(QPointF(14, 14), QPointF(26, 26))
            painter.drawLine(QPointF(26, 14), QPointF(14, 26))
        elif self.kind == "question":  # ?
            path = QPainterPath()
            path.moveTo(14.6, 16.2)
            path.arcTo(QRectF(14.6, 10.4, 10.8, 11.6), 180, -235)
            path.lineTo(20, 24.2)
            painter.drawPath(path)
            self._dot(painter, ink, 20, 29.6, 2.0)
        else:  # bilgi: i
            self._dot(painter, ink, 20, 12.4, 2.2)
            painter.drawLine(QPointF(20, 18.2), QPointF(20, 28.4))

    @staticmethod
    def _dot(painter: QPainter, color: QColor, x: float, y: float, r: float) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QRectF(x - r, y - r, 2 * r, 2 * r))
        painter.restore()


def _open_link(url: QUrl) -> None:
    # Yalnızca güvenli şemalar; metinde dosya:/özel şema bağlantısı olsa bile açılmaz
    if url.scheme().lower() in ("http", "https", "mailto"):
        QDesktopServices.openUrl(url)


class _TextBlock(QTextBrowser):
    """Salt okunur, seçilebilir, saydam metin bloğu. QLabel'den farkı: boşluksuz uzun sözcük (yol) de satır sonunda
    bölünür; yükseklik belge ölçüsünden tam hesaplanır (kesilme olmaz). Kendi kaydırma çubuğu yoktur."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.anchorClicked.connect(_open_link)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.document().setDocumentMargin(0)
        self.viewport().setAutoFillBackground(False)
        self.viewport().setProperty("mb", "1")
        self.setProperty("mb", "1")

    def set_content(self, text: str, fmt: Qt.TextFormat) -> None:
        rich = fmt == Qt.TextFormat.RichText or (fmt == Qt.TextFormat.AutoText and Qt.mightBeRichText(text))
        if rich:
            self.setHtml(text)
        else:
            self.setPlainText(text)

    def _measure_doc(self, width: float):
        """Ölçüm için belgenin KOPYASI `width` genişliğinde tam yerleştirilir. Asıl belge QTextEdit'in kendi
        yeniden yerleşimiyle (görünüm genişliği) oynandığından ve büyük metinde tembel yerleştiğinden orada ölçmek
        eksik/eski yükseklik verir (metin kesilirdi); kopya bağımsızdır, son bloğu isteyerek yerleşimi bitiririz."""
        doc = self.document().clone()
        doc.setDocumentMargin(0)
        doc.setDefaultFont(self.font())
        option = doc.defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        doc.setDefaultTextOption(option)
        doc.setTextWidth(width)
        last = doc.lastBlock()
        if last.isValid():
            doc.documentLayout().blockBoundingRect(last)
        return doc

    def natural_width(self) -> int:
        return int(math.ceil(self._measure_doc(-1).idealWidth()))

    def height_for_width(self, width: int) -> int:
        return int(math.ceil(self._measure_doc(max(1, width)).size().height())) + 2

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        copy = menu.addAction(button_text("copy"))
        copy.setEnabled(self.textCursor().hasSelection())
        copy.triggered.connect(self.copy)
        select_all = menu.addAction(button_text("select_all"))
        select_all.triggered.connect(self.selectAll)
        menu.exec(event.globalPos())


# ---------------------------------------------------------------------------------------------------------------
# Ortak çerçeve
# ---------------------------------------------------------------------------------------------------------------
class _ChromeDialog(QDialog):
    """Çerçevesiz, yarı saydam arka planlı, kendi çizdiği 12 px köşeli + vurgu üst çizgili modal pencere."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("mb_root")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self._pal: dict = current_palette()
        self._title_text = ""
        self._win_icon = QIcon()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(_SHADOW, _SHADOW, _SHADOW, _SHADOW)
        outer.setSpacing(0)
        self._outer = outer
        self._inner: QWidget | None = None

    @staticmethod
    def set_test_hook(fn) -> None:
        """Testler için: kanca varsa exec() gerçek pencere açmaz; `fn(kutu)` sonucu "tıklanan düğme" sayılır.
        MessageBox için StandardButton (ya da addButton'dan dönen düğme / None=Esc); InputDialog için (değer, ok)
        çifti ya da StandardButton.Ok/Cancel. `None` verilirse kanca kalkar."""
        global _TEST_HOOK
        _TEST_HOOK = fn

    # -- başlık/simge ----------------------------------------------------------------------------------------
    def setWindowTitle(self, title: str) -> None:  # noqa: N802 (Qt adı)
        self._title_text = str(title)
        super().setWindowTitle(self._title_text)
        self._invalidate()

    def setWindowIcon(self, icon) -> None:  # noqa: N802
        self._win_icon = QIcon(icon) if icon is not None else QIcon()
        super().setWindowIcon(self._win_icon)
        self._invalidate()

    def _title_icon(self) -> QIcon | None:
        if not self._win_icon.isNull():
            return self._win_icon
        provider = _CFG["icon"]
        if provider is not None:
            try:
                got = provider()
                if isinstance(got, QPixmap):
                    got = QIcon(got)
                if isinstance(got, QIcon) and not got.isNull():
                    return got
            except Exception:
                pass
        app_icon = QApplication.windowIcon()
        return None if app_icon.isNull() else app_icon

    # -- ekran/konum -----------------------------------------------------------------------------------------
    def _anchor_window(self) -> QWidget | None:
        parent = self.parentWidget()
        try:
            if parent is not None:
                top = parent.window()
                if top.isVisible() and not top.isMinimized():
                    return top
        except RuntimeError:
            pass
        return None

    def _screen(self):
        anchor = self._anchor_window()
        if anchor is not None:
            screen = anchor.screen()
            if screen is not None:
                return screen
        return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

    def _center(self) -> None:
        avail = self._screen().availableGeometry()
        anchor = self._anchor_window()
        center = anchor.frameGeometry().center() if anchor is not None else avail.center()
        x = center.x() - self.width() // 2
        y = center.y() - self.height() // 2
        x = max(avail.left(), min(x, avail.right() - self.width() + 1))
        y = max(avail.top(), min(y, avail.bottom() - self.height() + 1))
        self.move(x, y)

    def _clamp_to_screen(self) -> None:
        avail = self._screen().availableGeometry()
        y = min(self.y(), avail.bottom() - self.height() + 1)
        self.move(self.x(), max(avail.top(), y))

    # -- çizim -----------------------------------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        pal = self._pal
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = _SHADOW
        rect = QRectF(s, s, self.width() - 2 * s, self.height() - 2 * s)
        # yumuşak gölge: dıştan içe artan saydamlıkta halkalar
        peak = 70 if pal["is_dark"] else 38
        for i in range(s, 0, -1):
            alpha = int(peak * (1 - i / (s + 1)) ** 2)
            painter.setPen(QPen(QColor(0, 0, 0, alpha), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(-i + 0.5, -i + 1.5, i - 0.5, i - 0.5), _RADIUS + i, _RADIUS + i)
        path = QPainterPath()
        path.addRoundedRect(rect, _RADIUS, _RADIUS)
        painter.fillPath(path, QColor(pal["base"]))
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), _TITLE_H), QColor(pal["raised"]))
        painter.fillRect(QRectF(rect.left(), rect.top() + _TITLE_H - 1, rect.width(), 1), QColor(pal["edge"]))
        painter.restore()
        painter.setPen(QPen(QColor(pal["edge"]), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), _RADIUS - 0.5, _RADIUS - 0.5)
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 2), QColor(pal["accent"]))  # 2 px vurgu çizgisi
        painter.restore()

    def _invalidate(self) -> None:  # alt sınıflar geçersiz kılar
        pass


# ---------------------------------------------------------------------------------------------------------------
# MessageBox
# ---------------------------------------------------------------------------------------------------------------
class MessageBox(_ChromeDialog):
    """QMessageBox uyumlu özel kutu. Örnek:

        box = MessageBox(self)
        box.setIcon(MessageBox.Icon.Warning)
        box.setWindowTitle("Onay")
        box.setText("Silinsin mi?")
        sil = box.addButton("Sil", MessageBox.ButtonRole.DestructiveRole)
        box.addButton(MessageBox.StandardButton.Cancel)
        box.setDefaultButton(MessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is sil: ...
    """

    StandardButton = StandardButton
    Icon = Icon
    ButtonRole = ButtonRole

    buttonClicked = pyqtSignal(object)

    def __init__(self, *args, **kwargs):
        parent = kwargs.pop("parent", None)
        icon, title, text, buttons = Icon.NoIcon, "", "", StandardButton.NoButton
        if args and isinstance(args[0], Icon):  # QMessageBox(icon, title, text, buttons=NoButton, parent=None)
            icon = args[0]
            title = args[1] if len(args) > 1 else ""
            text = args[2] if len(args) > 2 else ""
            buttons = args[3] if len(args) > 3 else StandardButton.NoButton
            parent = args[4] if len(args) > 4 else parent
        elif args:
            parent = args[0]
        super().__init__(parent)
        self._icon = icon
        self._icon_pixmap: QPixmap | None = None
        self._text = str(text)
        self._info = ""
        self._detail = ""
        self._text_format = Qt.TextFormat.AutoText
        self._details_open = False
        self._built = False
        self._dirty = True
        self._buttons: list[QAbstractButton] = []
        self._meta: dict = {}  # düğme -> {"role": ButtonRole, "std": StandardButton | None}
        self._std: dict = {}  # StandardButton -> düğme
        self._default: QAbstractButton | None = None
        self._escape: QAbstractButton | None = None
        self._clicked: QAbstractButton | None = None
        self._forced = None  # test kancasının (düğmesi olmayan) sonucu
        self._extra: QWidget | None = None  # InputDialog'un giriş alanı
        self._pref_width = 0
        self._row_min_w = 0
        self._blocks: list[_TextBlock] = []
        self._scroll: QScrollArea | None = None
        self._content: QWidget | None = None
        self._details_edit: QPlainTextEdit | None = None
        self._details_host: QWidget | None = None
        self._link_btn: QPushButton | None = None
        self._icon_widget: QWidget | None = None
        if title:
            self._title_text = str(title)
            super(_ChromeDialog, self).setWindowTitle(self._title_text)
        if buttons:
            self.setStandardButtons(buttons)

    # -- QMessageBox benzeri ayarlar -------------------------------------------------------------------------
    def setIcon(self, icon) -> None:  # noqa: N802
        self._icon = icon
        self._invalidate()

    def icon(self):
        return self._icon

    def setIconPixmap(self, pixmap: QPixmap) -> None:  # noqa: N802
        self._icon_pixmap = pixmap
        self._invalidate()

    def setText(self, text: str) -> None:  # noqa: N802
        self._text = str(text)
        self._invalidate()

    def text(self) -> str:
        return self._text

    def setInformativeText(self, text: str) -> None:  # noqa: N802
        self._info = str(text)
        self._invalidate()

    def informativeText(self) -> str:  # noqa: N802
        return self._info

    def setDetailedText(self, text: str) -> None:  # noqa: N802
        self._detail = str(text)
        self._invalidate()

    def detailedText(self) -> str:  # noqa: N802
        return self._detail

    def setTextFormat(self, fmt) -> None:  # noqa: N802
        self._text_format = fmt
        self._invalidate()

    def textFormat(self):  # noqa: N802
        return self._text_format

    def _invalidate(self) -> None:
        self._dirty = True
        if self.isVisible():
            self.prepare()

    # -- düğmeler --------------------------------------------------------------------------------------------
    def _std_text(self, std) -> str:
        return button_text(std)

    def addButton(self, arg, role=None):  # noqa: N802
        """addButton(StandardButton) | addButton("Metin", ButtonRole) | addButton(QAbstractButton, ButtonRole)."""
        std = None
        if isinstance(arg, QAbstractButton):
            btn = arg
            role = role if role is not None else _BR.ActionRole
        elif isinstance(arg, str):
            btn = QPushButton(arg)
            role = role if role is not None else _BR.ActionRole
        else:
            std = StandardButton(arg)
            if std in self._std:
                return self._std[std]
            btn = QPushButton(self._std_text(std))
            role = _STD_ROLES.get(std, _BR.ActionRole)
        btn.setParent(self)
        if isinstance(btn, QPushButton):
            btn.setObjectName("mb_btn")
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn.hide()
        btn.clicked.connect(lambda _checked=False, b=btn: self._on_button(b))
        self._buttons.append(btn)
        self._meta[btn] = {"role": role, "std": std}
        if std is not None:
            self._std[std] = btn
        self._invalidate()
        return btn

    def removeButton(self, button) -> None:  # noqa: N802
        if button in self._meta:
            std = self._meta.pop(button)["std"]
            self._buttons.remove(button)
            if std is not None:
                self._std.pop(std, None)
            if self._default is button:
                self._default = None
            if self._escape is button:
                self._escape = None
            button.setParent(None)
            self._invalidate()

    def setStandardButtons(self, buttons) -> None:  # noqa: N802
        wanted = [std for std in _STD_ORDER if int(buttons) & int(std)]
        for std in list(self._std):
            if std not in wanted:
                btn = self._std[std]
                self.removeButton(btn)
                btn.deleteLater()
        for std in wanted:
            if std not in self._std:
                self.addButton(std)

    def standardButtons(self):  # noqa: N802
        flags = StandardButton.NoButton
        for std in self._std:
            flags |= std
        return flags

    def button(self, which):
        """StandardButton -> o düğme (yoksa None)."""
        return self._std.get(which)

    def buttons(self) -> list[QAbstractButton]:
        return self._ordered()

    def buttonRole(self, button):  # noqa: N802
        meta = self._meta.get(button)
        return meta["role"] if meta else _BR.InvalidRole

    def standardButton(self, button):  # noqa: N802
        meta = self._meta.get(button)
        return meta["std"] if meta and meta["std"] is not None else StandardButton.NoButton

    def setDefaultButton(self, button) -> None:  # noqa: N802
        self._default = self._resolve_button(button)
        self._invalidate()

    def defaultButton(self):  # noqa: N802
        return self._effective_default()

    def setEscapeButton(self, button) -> None:  # noqa: N802
        self._escape = self._resolve_button(button)

    def escapeButton(self):  # noqa: N802
        return self._resolve_escape()

    def clickedButton(self):  # noqa: N802
        return self._clicked

    def _resolve_button(self, button):
        if button is None:
            return None
        if isinstance(button, QAbstractButton):
            return button if button in self._meta else None
        try:
            return self._std.get(StandardButton(button))
        except ValueError:
            return None

    def _ordered(self) -> list[QAbstractButton]:
        seq = {btn: i for i, btn in enumerate(self._buttons)}
        return sorted(self._buttons, key=lambda b: (_ROLE_ORDER.get(self._meta[b]["role"], 9), seq[b]))

    def _effective_default(self):
        if self._default is not None and self._default in self._meta:
            return self._default
        ordered = self._ordered()
        for btn in ordered:
            if self._meta[btn]["role"] in (_BR.AcceptRole, _BR.YesRole):
                return btn
        return ordered[0] if ordered else None

    def _resolve_escape(self):
        """Esc/✕ hangi düğmeye karşılık gelir: açık atanan > İptal > Hayır > Kapat > tek Reject rolü > tek düğme."""
        if self._escape is not None and self._escape in self._meta:
            return self._escape
        if len(self._buttons) == 1:
            return self._buttons[0]
        for std in (StandardButton.Cancel, StandardButton.No, StandardButton.Close, StandardButton.Abort):
            if std in self._std:
                return self._std[std]
        rejects = [b for b in self._buttons if self._meta[b]["role"] == _BR.RejectRole]
        return rejects[0] if len(rejects) == 1 else None

    @staticmethod
    def _exec_code_for(box, button) -> int:
        """QMessageBox'ın exec() dönüş kuralı: standart düğme -> değeri (Abort/Close -> Cancel, *ToAll -> Yes/No),
        özel düğme -> eklenme sırası, hiçbiri -> -1."""
        if button is None:
            return -1
        std = box._meta[button]["std"]
        if std is None:
            custom = [b for b in box._buttons if box._meta[b]["std"] is None]
            return custom.index(button)
        if std in (StandardButton.Abort, StandardButton.Close):
            std = StandardButton.Cancel
        elif std == StandardButton.NoToAll:
            std = StandardButton.No
        elif std == StandardButton.YesToAll:
            std = StandardButton.Yes
        return int(std)

    def _on_button(self, button) -> None:
        self._clicked = button
        self.buttonClicked.emit(button)
        QDialog.done(self, self._exec_code_for(self, button))

    # -- kapanış ---------------------------------------------------------------------------------------------
    def accept(self) -> None:
        btn = self._effective_default()
        if btn is not None:
            self._on_button(btn)
        else:
            super().accept()

    def reject(self) -> None:
        btn = self._resolve_escape()
        if btn is not None:
            self._on_button(btn)
        else:
            self._clicked = None
            QDialog.done(self, -1)

    # -- klavye ----------------------------------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focus = self.focusWidget()
            target = focus if isinstance(focus, QAbstractButton) and focus in self._meta else self._effective_default()
            if target is not None:
                target.click()
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Copy) and not any(b.textCursor().hasSelection() for b in self._blocks):
            parts = [self._title_text, self._text, self._info, self._detail]
            QApplication.clipboard().setText("\n\n".join(p for p in parts if p))
            event.accept()
            return
        super().keyPressEvent(event)

    # -- gösterim --------------------------------------------------------------------------------------------
    def setVisible(self, visible: bool) -> None:  # noqa: N802
        if visible:
            self.prepare()
        super().setVisible(visible)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._post_show)

    def _post_show(self) -> None:
        try:
            if not self.isVisible():
                return
            self.raise_()
            self.activateWindow()
            target = self._initial_focus()
            if target is not None:
                target.setFocus(Qt.FocusReason.OtherFocusReason)
        except RuntimeError:
            pass

    def _initial_focus(self):
        return self._effective_default()

    def exec(self) -> int:  # noqa: A003
        self.prepare()
        hook = _TEST_HOOK
        if hook is not None:
            return self._run_hook(hook)
        self._clicked = None
        self._center()
        return super().exec()

    def open(self) -> None:  # noqa: A003
        self.prepare()
        self._center()
        super().open()

    def _run_hook(self, hook) -> int:
        result = hook(self)
        self._forced = None
        button = None
        if isinstance(result, QAbstractButton):
            button = result if result in self._meta else None
        elif result is not None:
            try:
                std = StandardButton(result)
                button = self._std.get(std)
                if button is None:
                    self._forced = std
            except ValueError:
                button = None
        if button is None and self._forced is None and result is None:
            button = self._resolve_escape()
        self._clicked = button
        if button is not None:
            self.buttonClicked.emit(button)
            return self._exec_code_for(self, button)
        return int(self._forced) if self._forced is not None else -1

    def result_button(self):
        """Kutunun sonucu StandardButton olarak (özel düğme/hiçbiri -> NoButton). Statik yardımcılar bunu döndürür."""
        if self._forced is not None:
            return self._forced
        return self.standardButton(self._clicked) if self._clicked is not None else StandardButton.NoButton

    # -- düzen -----------------------------------------------------------------------------------------------
    def prepare(self) -> None:
        """Arayüzü kur ve boyutla (idempotent). exec()/show() bunu kendisi çağırır; testler önceden çağırabilir."""
        if not self._dirty and self._built:
            return
        self._build()
        self._fit()

    def _teardown(self) -> None:
        for btn in self._buttons:  # kalıcı düğmeler/giriş alanı silinen kapsayıcıyla gitmesin
            btn.setParent(self)
            btn.hide()
        if self._extra is not None:
            self._extra.setParent(self)
            self._extra.hide()
        if self._inner is not None:
            self._outer.removeWidget(self._inner)
            self._inner.setParent(None)
            self._inner.deleteLater()
            self._inner = None
        self._blocks = []

    def _kind(self) -> str:
        return {
            Icon.Information: "information", Icon.Warning: "warning", Icon.Critical: "critical",
            Icon.Question: "question",
        }.get(self._icon, "")

    def _build(self) -> None:
        self._teardown()
        pal = self._pal = current_palette()
        self.setStyleSheet(_qss(pal))
        parent = self.parentWidget()
        try:
            hidden_owner = parent is None or not parent.window().isVisible()
        except RuntimeError:
            hidden_owner = True
        on_top = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if not self.isVisible() and on_top != bool(hidden_owner):
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(hidden_owner))
        inner = self._inner = _plain(QWidget(self), "mb_inner")
        self._outer.addWidget(inner)
        root = QVBoxLayout(inner)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(_TitleBar(self, pal, self._title_text, self._title_icon(), inner))

        body = _plain(QWidget(inner), "mb_body")
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(_PAD_X, _PAD_TOP, _PAD_X, 0)
        body_lay.setSpacing(_ICON_GAP)
        self._icon_widget = None
        kind = self._kind()
        if self._icon_pixmap is not None and not self._icon_pixmap.isNull():
            lab = _plain(QLabel(body), "mb_icon")
            lab.setFixedSize(_ICON_BOX, _ICON_BOX)
            lab.setPixmap(self._icon_pixmap.scaled(
                _ICON_BOX, _ICON_BOX, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self._icon_widget = lab
        elif kind:
            self._icon_widget = _TypeIcon(kind, pal, body)
        if self._icon_widget is not None:
            body_lay.addWidget(self._icon_widget, 0, Qt.AlignmentFlag.AlignTop)

        scroll = self._scroll = QScrollArea(body)
        scroll.setObjectName("mb_scroll")
        scroll.setProperty("mb", "1")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(False)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.viewport().setAutoFillBackground(False)
        scroll.viewport().setProperty("mb", "1")
        content = self._content = _plain(QWidget(scroll), "mb_content")
        c_lay = QVBoxLayout(content)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.setSpacing(10)
        has_info = bool(self._info.strip())
        for text, name in ((self._text, "mb_text"), (self._info, "mb_info")):
            if not text.strip():
                continue
            blk = _TextBlock(content)
            blk.setObjectName(name)
            if name == "mb_text" and has_info:  # ek metin varsa ana cümle vurgulu
                blk.setProperty("emph", "1")
            blk.set_content(text, self._text_format)
            c_lay.addWidget(blk)
            self._blocks.append(blk)
        if self._extra is not None:
            c_lay.addWidget(self._extra)
            self._extra.show()
        scroll.setWidget(content)
        body_lay.addWidget(scroll, 1, Qt.AlignmentFlag.AlignTop)
        root.addWidget(body)

        # açılır ayrıntı
        self._details_host = self._details_edit = self._link_btn = None
        if self._detail.strip():
            host = self._details_host = _plain(QWidget(inner), "mb_details_host")
            h_lay = QVBoxLayout(host)
            h_lay.setContentsMargins(_PAD_X, 12, _PAD_X, 0)
            edit = self._details_edit = QPlainTextEdit(host)
            edit.setObjectName("mb_details")
            edit.setReadOnly(True)
            edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            edit.setPlainText(self._detail)
            h_lay.addWidget(edit)
            host.setVisible(self._details_open)
            root.addWidget(host)

        # düğme satırı
        row = _plain(QWidget(inner), "mb_buttons")
        r_lay = QHBoxLayout(row)
        r_lay.setContentsMargins(_PAD_X, 16, _PAD_X, 16)
        r_lay.setSpacing(10)
        if self._details_host is not None:
            link = self._link_btn = QPushButton(row)
            link.setObjectName("mb_link")
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.setAutoDefault(False)
            link.clicked.connect(self._toggle_details)
            self._refresh_link_text()
            r_lay.addWidget(link)
        r_lay.addStretch(1)
        default = self._effective_default()
        for btn in self._ordered():
            if isinstance(btn, QPushButton):
                std = self._meta[btn]["std"]
                if std is not None:
                    btn.setText(self._std_text(std))
                role = self._meta[btn]["role"]
                variant = "danger" if role == _BR.DestructiveRole else ("primary" if btn is default else "")
                btn.setProperty("variant", variant)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
            r_lay.addWidget(btn)
            btn.show()
        root.addWidget(row)
        self._built = True
        self._dirty = False

    def _refresh_link_text(self) -> None:
        if self._link_btn is not None:
            self._link_btn.setText(button_text("details_hide" if self._details_open else "details_show"))

    def _toggle_details(self) -> None:
        self._details_open = not self._details_open
        self._refresh_link_text()
        if self._details_host is not None:
            self._details_host.setVisible(self._details_open)
        self._fit()
        if self.isVisible():
            self._clamp_to_screen()

    def _size_blocks(self, width: int) -> int:
        """Metin bloklarını `width` genişliğinde tam yüksekliğiyle boyutlar; içerik yüksekliğini döndürür."""
        total, count = 0, 0
        for blk in self._blocks:
            blk.setFixedSize(width, blk.height_for_width(width))
            total += blk.height()
            count += 1
        if self._extra is not None:
            self._extra.setFixedWidth(width)
            total += self._extra.sizeHint().height()
            count += 1
        total += 10 * max(0, count - 1)
        # tek satırlık kısa metin simgeyle dikey ortalansın
        pad = max(0, (_ICON_BOX - total) // 2) if self._icon_widget is not None else 0
        self._content.layout().setContentsMargins(0, pad, 0, 0)
        self._content.setFixedSize(width, total + pad)
        return total + pad

    def _fit(self) -> None:
        """Genişlik 360..560, yükseklik ekranın %70'ini aşmaz; sığmayan içerik dikey kaydırılır (kesilmez)."""
        self.ensurePolished()
        for w in self.findChildren(QWidget):
            w.ensurePolished()
        s = _SHADOW
        avail = self._screen().availableGeometry()
        max_total_h = int(avail.height() * _MAX_H_RATIO)
        max_w = max(240, min(_MAX_W, avail.width() - 2 * s - 24))
        min_w = min(_MIN_W, max_w)
        extras = 2 * _PAD_X + ((_ICON_BOX + _ICON_GAP) if self._icon_widget is not None else 0)
        natural = max((b.natural_width() for b in self._blocks), default=0)
        width = max(min_w, min(max_w, max(natural + extras + 4, self._pref_width)))
        buttons = self._ordered()
        btn_w = sum(b.sizeHint().width() for b in buttons) + 10 * max(0, len(buttons) - 1) + 2 * _PAD_X
        if self._link_btn is not None:
            btn_w += self._link_btn.sizeHint().width() + 16
        self._row_min_w = btn_w
        width = max(width, min(btn_w, avail.width() - 2 * s - 24))
        area_w = width - extras
        btn_h = max([b.sizeHint().height() for b in buttons] + [32])
        open_details = self._details_host is not None and self._details_open
        details_h = _DETAILS_H if open_details else 0
        fixed = _TITLE_H + _PAD_TOP + ((12 + details_h) if open_details else 0) + 16 + btn_h + 16
        max_body_h = max(80, max_total_h - 2 * s - fixed)
        content_h = self._size_blocks(area_w)
        content_w = area_w
        if content_h > max_body_h:  # kaydırma çubuğu belirecek: metni onun payı kadar daralt
            bar = self._scroll.verticalScrollBar().sizeHint().width()
            content_w = area_w - max(bar, 8) - 6
            content_h = self._size_blocks(content_w)
        area_h = max(min(content_h, max_body_h), _ICON_BOX if self._icon_widget is not None else 0)
        self._scroll.setFixedSize(area_w, area_h)
        self._scroll.verticalScrollBar().setValue(0)
        if open_details:
            self._details_edit.setFixedSize(width - 2 * _PAD_X, details_h)
        self.setFixedWidth(width + 2 * s)
        lay = self.layout()
        for _ in range(3):  # iç düzenler boyut değişince eski boyutu önbellekte tutar: bekleyen istekleri işleyip yeniden ölç
            QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
            lay.invalidate()
            lay.activate()
            height = lay.sizeHint().height()
            if height == self.height() and self.minimumHeight() == self.maximumHeight() == height:
                break
            self.setFixedHeight(height)

    def accessibleSummary(self) -> str:  # noqa: N802
        return "\n".join(p for p in (self._title_text, self._text, self._info) if p)

    # -- statik kısa yollar (QMessageBox ile aynı imza) -------------------------------------------------------
    @staticmethod
    def _show(icon, parent, title, text, buttons, default, informative="", detailed="") -> StandardButton:
        box = MessageBox(parent)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        if informative:
            box.setInformativeText(informative)
        if detailed:
            box.setDetailedText(detailed)
        box.setStandardButtons(buttons)
        if default is not None and default != StandardButton.NoButton:
            box.setDefaultButton(default)
        box.exec()
        result = box.result_button()
        box.deleteLater()
        return result

    @staticmethod
    def information(parent, title, text, buttons=StandardButton.Ok, defaultButton=StandardButton.NoButton, *,
                    default=None, informative="", detailed="") -> StandardButton:
        return MessageBox._show(Icon.Information, parent, title, text, buttons, default or defaultButton,
                                informative, detailed)

    @staticmethod
    def warning(parent, title, text, buttons=StandardButton.Ok, defaultButton=StandardButton.NoButton, *,
                default=None, informative="", detailed="") -> StandardButton:
        return MessageBox._show(Icon.Warning, parent, title, text, buttons, default or defaultButton,
                                informative, detailed)

    @staticmethod
    def critical(parent, title, text, buttons=StandardButton.Ok, defaultButton=StandardButton.NoButton, *,
                 default=None, informative="", detailed="") -> StandardButton:
        return MessageBox._show(Icon.Critical, parent, title, text, buttons, default or defaultButton,
                                informative, detailed)

    @staticmethod
    def question(parent, title, text, buttons=StandardButton.Yes | StandardButton.No,
                 defaultButton=StandardButton.NoButton, *, default=None, informative="",
                 detailed="") -> StandardButton:
        return MessageBox._show(Icon.Question, parent, title, text, buttons, default or defaultButton,
                                informative, detailed)

    @staticmethod
    def about(parent, title, text) -> None:
        """QMessageBox.about yerine: simgesiz, zengin metin, tek Tamam düğmesi."""
        box = MessageBox(parent)
        box.setWindowTitle(title)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(text)
        box.setStandardButtons(StandardButton.Ok)
        box.exec()
        box.deleteLater()


# ---------------------------------------------------------------------------------------------------------------
# InputDialog
# ---------------------------------------------------------------------------------------------------------------
class InputDialog(MessageBox):
    """QInputDialog yerine: aynı çerçeve; getText/getInt/getDouble/getItem (değer, tamam_mı) döndürür."""

    def __init__(self, parent, title: str, label: str, editor: QWidget):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setText(label)
        self._pref_width = 420
        editor.setObjectName("mb_input")
        self._extra = editor
        ok = self.addButton(StandardButton.Ok)
        cancel = self.addButton(StandardButton.Cancel)
        self.setDefaultButton(ok)
        self.setEscapeButton(cancel)

    def value(self):
        ed = self._extra
        if isinstance(ed, QLineEdit):
            return ed.text()
        if isinstance(ed, QComboBox):
            return ed.currentText()
        if isinstance(ed, (QSpinBox, QDoubleSpinBox)):
            ed.interpretText()
            return ed.value()
        return None

    def _initial_focus(self):
        return self._extra

    def _post_show(self) -> None:
        super()._post_show()
        ed = self._extra
        try:
            if isinstance(ed, QLineEdit):
                ed.selectAll()
            elif isinstance(ed, (QSpinBox, QDoubleSpinBox)):
                ed.selectAll()
        except RuntimeError:
            pass

    def _run_hook(self, hook) -> int:
        result = hook(self)
        self._forced = None
        if isinstance(result, (tuple, list)) and len(result) == 2:
            value, ok = result
            self._forced = (value, bool(ok))
            self._clicked = self.button(StandardButton.Ok) if ok else self.button(StandardButton.Cancel)
            return int(StandardButton.Ok if ok else StandardButton.Cancel)
        ok = result == StandardButton.Ok
        self._clicked = self.button(StandardButton.Ok) if ok else self.button(StandardButton.Cancel)
        return int(StandardButton.Ok if ok else StandardButton.Cancel)

    def _result(self, cancel_value):
        if self._forced is not None:
            value, ok = self._forced
            return (value if ok else cancel_value), ok
        ok = self._clicked is not None and self._meta[self._clicked]["std"] == StandardButton.Ok
        return (self.value() if ok else cancel_value), bool(ok)

    @staticmethod
    def _run(dialog: InputDialog, cancel_value):
        dialog.exec()
        out = dialog._result(cancel_value)
        dialog.deleteLater()
        return out

    @staticmethod
    def getText(parent, title, label, echo=QLineEdit.EchoMode.Normal, text="", *_args, **_kw):  # noqa: N802
        if isinstance(echo, str):  # getText(parent, başlık, etiket, "ilk değer")
            text, echo = echo, QLineEdit.EchoMode.Normal
        editor = QLineEdit()
        editor.setEchoMode(echo)
        editor.setText(str(text))
        return InputDialog._run(InputDialog(parent, title, label, editor), "")

    @staticmethod
    def getInt(parent, title, label, value=0, min=-2147483647, max=2147483647, step=1, *_args, **_kw):  # noqa: N802,A002
        editor = QSpinBox()
        editor.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        editor.setRange(min, max)
        editor.setSingleStep(step)
        editor.setValue(value)
        return InputDialog._run(InputDialog(parent, title, label, editor), value)

    @staticmethod
    def getDouble(parent, title, label, value=0.0, min=-2147483647.0, max=2147483647.0, decimals=1,  # noqa: N802,A002
                  *_args, **_kw):
        editor = QDoubleSpinBox()
        editor.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        editor.setDecimals(decimals)
        editor.setRange(min, max)
        editor.setValue(value)
        return InputDialog._run(InputDialog(parent, title, label, editor), value)

    @staticmethod
    def getItem(parent, title, label, items, current=0, editable=True, *_args, **_kw):  # noqa: N802
        editor = QComboBox()
        editor.setEditable(bool(editable))
        editor.addItems([str(i) for i in items])
        if 0 <= current < editor.count():
            editor.setCurrentIndex(current)
        return InputDialog._run(InputDialog(parent, title, label, editor), "")
