"""Lumina renk sistemi: nötr gri koyu/açık yüzeyler ve 10 vurgu rengi.

* Koyu ve açık zemin ailesi MediaCompressor/TransferTCP/FlexiKey ile aynıdır (nötr gri; lacivert/slate yok).
* Vurgu rengi 10 anahtarlı Material ailesidir (`ACCENTS`, MediaCompressor ile aynı anahtar, ad ve onaltılık değerler).
  Varsayılan `blue`: Lumina'nın eski gök mavisine en yakın anahtar.
* `palette(tema, vurgu)` her yüzey/yazı/vurgu rengini hazır verir; metin-zemin çiftleri 4,5:1 kontrastı sağlar
  (`tests/test_theme.py` tüm vurgu x tema birleşimlerini denetler).
* `active_*` durumu en son uygulanan temayı/vurguyu tutar: simgeler, menüler ve özel kutular (`message_box`) buradan okur.
"""

from __future__ import annotations

DEFAULT_ACCENT = "blue"

# Vurgu renkleri: MediaCompressor `ACCENTS` ile aynı (anahtar, etiket anahtarı, onaltılık değerler).
ACCENTS: dict[str, dict[str, str]] = {
    "cyan": {"label_key": "accent_cyan", "accent": "#00ACC1", "accent2": "#00838F", "hi_dark": "#4DD0E1", "hi_light": "#80DEEA"},
    "blue": {"label_key": "accent_blue", "accent": "#2196F3", "accent2": "#1976D2", "hi_dark": "#42A5F5", "hi_light": "#90CAF9"},
    "teal": {"label_key": "accent_teal", "accent": "#00897B", "accent2": "#00695C", "hi_dark": "#4DB6AC", "hi_light": "#80CBC4"},
    "indigo": {"label_key": "accent_indigo", "accent": "#3F51B5", "accent2": "#303F9F", "hi_dark": "#7986CB", "hi_light": "#9FA8DA"},
    "violet": {"label_key": "accent_violet", "accent": "#7C4DFF", "accent2": "#651FFF", "hi_dark": "#B388FF", "hi_light": "#D1C4E9"},
    "rose": {"label_key": "accent_rose", "accent": "#E91E63", "accent2": "#C2185B", "hi_dark": "#F06292", "hi_light": "#F8BBD0"},
    "orange": {"label_key": "accent_orange", "accent": "#FB8C00", "accent2": "#EF6C00", "hi_dark": "#FFB74D", "hi_light": "#FFE0B2"},
    "red": {"label_key": "accent_red", "accent": "#E53935", "accent2": "#C62828", "hi_dark": "#EF5350", "hi_light": "#FFCDD2"},
    "green": {"label_key": "accent_green", "accent": "#43A047", "accent2": "#2E7D32", "hi_dark": "#66BB6A", "hi_light": "#C8E6C9"},
    "yellow": {"label_key": "accent_yellow", "accent": "#F9A825", "accent2": "#F57F17", "hi_dark": "#FDD835", "hi_light": "#FFF59D"},
}
ACCENT_KEYS = tuple(ACCENTS)

# Nötr gri yüzeyler (MediaCompressor DARK/LIGHT sözlükleri). `stage` = fotoğraf/video zemini, `ink` = araç çubuğu simgeleri.
DARK: dict[str, str] = {
    "base": "#1a1a1a", "raised": "#2a2a2a", "sunken": "#1f1f1f", "surface": "#252525", "border": "#3a3a3a",
    "hi": "#3a3a3a", "hi2": "#353535", "lo": "#0f0f0f",
    "text": "#e8e8e8", "text_muted": "#9e9e9e", "ink": "#e0e0e0", "ink2": "#d0d0d0",
    "danger": "#d32f2f", "danger_hi": "#f44336", "warn": "#FFC107",
    "stage": "#0f0f0f", "panel": "#202020",
}
LIGHT: dict[str, str] = {
    "base": "#e0e0e0", "raised": "#f5f5f5", "sunken": "#d8d8d8", "surface": "#ececec", "border": "#c5c5c5",
    "hi": "#ffffff", "hi2": "#fafafa", "lo": "#bdbdbd",
    "text": "#1a1a1a", "text_muted": "#616161", "ink": "#2a2a2a", "ink2": "#333333",
    "danger": "#e53935", "danger_hi": "#ff5252", "warn": "#8A5300",
    "stage": "#ececec", "panel": "#f5f5f5",
}

_STATE: dict = {"theme": None, "accent": DEFAULT_ACCENT}


# ---------------------------------------------------------------------------------------------------------------
# Renk yardımcıları
# ---------------------------------------------------------------------------------------------------------------
def _rgb(color: str) -> tuple[int, int, int]:
    h = color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(r: float, g: float, b: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(v))) for v in (r, g, b)))


def luminance(color: str) -> float:
    def lin(v: int) -> float:
        s = v / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(v) for v in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: str, b: str, amount: float) -> str:
    """`a`'dan `b`'ye `amount` oranında opak karışım."""
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return _hex(ra + (rb - ra) * amount, ga + (gb - ga) * amount, ba + (bb - ba) * amount)


def rgba(color: str, alpha: float) -> str:
    r, g, b = _rgb(color)
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


def best_ink(fill: str) -> str:
    """Dolgunun üstündeki yazı rengi: beyaz 4,5:1'i sağlıyorsa beyaz, yoksa daha yüksek kontrastlı olan."""
    white, dark = "#ffffff", "#1a1a1a"
    cw, cd = contrast(fill, white), contrast(fill, dark)
    return white if cw >= 4.5 or cw >= cd else dark


def readable(color: str, background: str, target: float = 4.5) -> str:
    """`color`'ı, `background` üzerinde `target` kontrastına ulaşana dek (koyu zeminde beyaza, açıkta siyaha) çeker."""
    if contrast(color, background) >= target:
        return color
    toward = "#ffffff" if luminance(background) < 0.4 else "#000000"
    for step in range(1, 21):
        candidate = mix(color, toward, step / 20.0)
        if contrast(candidate, background) >= target:
            return candidate
    return toward


# ---------------------------------------------------------------------------------------------------------------
# Palet
# ---------------------------------------------------------------------------------------------------------------
def normalize_accent(key: str | None) -> str:
    return key if key in ACCENTS else DEFAULT_ACCENT


def normalize_theme(name: str | None) -> str:
    return "light" if name == "light" else "dark"


def accent_label(key: str, lang: str | None = None) -> str:
    from app.resources.locales import t

    return t(ACCENTS[normalize_accent(key)]["label_key"], lang)


def palette(theme: str, accent: str | None = None) -> dict:
    """Çözülmüş tema ("dark"/"light") ve vurgu anahtarı için tüm renkler (`#rrggbb`; `is_dark` bool)."""
    dark = normalize_theme(theme) == "dark"
    p: dict = dict(DARK if dark else LIGHT)
    spec = ACCENTS[normalize_accent(accent if accent is not None else _STATE["accent"])]
    p["is_dark"] = dark
    p["accent"] = spec["accent"]
    p["accent2"] = spec["accent2"]
    p["accent_hi"] = spec["hi_dark"] if dark else spec["hi_light"]
    fill = spec["accent2"]  # dolu yüzey (seçili öğe, birincil düğme, güncelleme kapsülü)
    ink = best_ink(fill)
    p["fill"] = fill
    p["on_fill"] = ink
    p["fill_hover"] = mix(fill, "#000000" if ink == "#ffffff" else "#ffffff", 0.14)
    # Kenar/odak çizgisi: koyuda parlak ton, açıkta derin ton
    p["accent_edge"] = spec["hi_dark"] if dark else spec["accent2"]
    worst_bg = p["raised"] if dark else p["sunken"]  # metnin durabileceği en zor yüzey
    p["accent_text"] = readable(spec["hi_dark"] if dark else spec["accent2"], worst_bg)
    p["muted"] = p["text_muted"]
    p["link"] = p["accent_text"]
    return p


def set_active(theme: str | None = None, accent: str | None = None) -> None:
    """En son uygulanan (çözülmüş) temayı ve vurguyu kaydeder; simge/menü/kutular buradan okur."""
    if theme is not None:
        _STATE["theme"] = normalize_theme(theme)
    if accent is not None:
        _STATE["accent"] = normalize_accent(accent)


def active_accent() -> str:
    return _STATE["accent"]


def active_theme() -> str:
    """Çözülmüş tema; hiç uygulanmadıysa sistemin açık/koyu ayarı (okuma hatasında koyu)."""
    theme = _STATE["theme"]
    if theme is None:
        from app.resources.styles import resolve_theme

        theme = resolve_theme("system")
    return theme


def reset_active() -> None:
    _STATE["theme"] = None
    _STATE["accent"] = DEFAULT_ACCENT


def active_palette() -> dict:
    return palette(active_theme(), active_accent())


def accent_ink(theme: str | None = None) -> str:
    """Vurgunun düz yazı/simge olarak kullanıldığı renk (zeminde okunur)."""
    return palette(theme or active_theme())["accent_text"]


# ---------------------------------------------------------------------------------------------------------------
# Ana pencere stil sayfası
# ---------------------------------------------------------------------------------------------------------------
def build_stylesheet(theme: str, accent: str | None = None) -> str:
    p = palette(theme, accent)
    dark = p["is_dark"]
    # Katmanlı yüzeylerde ince çizgi/vurgu için üst örtü rengi: koyuda beyaz, açıkta siyah
    ov = "255, 255, 255" if dark else "0, 0, 0"

    def a(alpha: float) -> str:
        return f"rgba({ov}, {alpha})"

    if dark:
        container_bg = (
            f"qradialgradient(cx:0.48, cy:0.08, radius:1.05, fx:0.48, fy:0.0, "
            f"stop:0 {p['raised']}, stop:0.55 {p['sunken']}, stop:1 {p['base']})"
        )
        stage_border = a(0.06)
        capsule_bg = p["sunken"]
        capsule_border = a(0.16)
        nav_bg, nav_hover_bg = a(0.08), a(0.18)
        nav_border, nav_hover_border = a(0.16), a(0.32)
        nav_text, nav_hover_text = p["ink2"], "#ffffff"
        nav_dis_text, nav_dis_border, nav_dis_bg = a(0.30), a(0.08), a(0.03)
        arrow_bg, arrow_hover_bg = a(0.09), a(0.22)
        arrow_border, arrow_hover_border = a(0.18), a(0.36)
        arrow_dis_border, arrow_dis_bg = a(0.06), a(0.02)
        icon_bg, icon_border, icon_hover_bg, icon_hover_border = a(0.07), a(0.14), a(0.18), a(0.30)
        icon_hover_text = "#ffffff"
        muted_bg, muted_border, muted_text = "rgba(239, 68, 68, 0.20)", "rgba(239, 68, 68, 0.45)", "#f87171"
        active_bg, active_border, active_text = rgba(p["accent"], 0.35), p["accent_edge"], "#ffffff"
        active_hover_bg, active_hover_border = rgba(p["accent"], 0.50), p["accent_hi"]
        sys_hover = a(0.10)
        menu_bg, menu_border = "rgba(30, 30, 30, 0.97)", a(0.12)
        menu_text = p["text"]
        checked_bg = rgba(p["accent"], 0.22)
        sep = a(0.08)
        tip_bg, tip_border = "rgba(37, 37, 37, 0.96)", a(0.18)
        panel_border, rule = a(0.10), a(0.12)
        scroll, scroll_hover = a(0.20), a(0.35)
        details_title, details_value = p["text"], p["text"]
        file_chip_name = p["text"]
        dis_text = a(0.30)
    else:
        container_bg = (
            f"qradialgradient(cx:0.5, cy:0.1, radius:1.0, fx:0.5, fy:0.0, stop:0 {p['raised']}, stop:1 {p['base']})"
        )
        stage_border = a(0.08)
        capsule_bg = "rgba(255, 255, 255, 0.94)"
        capsule_border = a(0.14)
        nav_bg, nav_hover_bg = "rgba(255, 255, 255, 0.65)", "rgba(255, 255, 255, 0.92)"
        nav_border, nav_hover_border = a(0.14), a(0.28)
        nav_text, nav_hover_text = p["ink"], p["text"]
        nav_dis_text, nav_dis_border, nav_dis_bg = a(0.30), a(0.08), "rgba(255, 255, 255, 0.30)"
        arrow_bg, arrow_hover_bg = "rgba(255, 255, 255, 0.65)", "rgba(255, 255, 255, 0.92)"
        arrow_border, arrow_hover_border = a(0.14), a(0.28)
        arrow_dis_border, arrow_dis_bg = a(0.08), "rgba(255, 255, 255, 0.30)"
        icon_bg, icon_border, icon_hover_bg, icon_hover_border = "rgba(255, 255, 255, 0.65)", a(0.14), "rgba(255, 255, 255, 0.92)", a(0.28)
        icon_hover_text = p["text"]
        muted_bg, muted_border, muted_text = "rgba(254, 226, 226, 0.88)", "rgba(220, 38, 38, 0.38)", p["ink"]
        active_bg, active_border, active_text = rgba(p["accent2"], 0.22), p["accent_edge"], p["accent_text"]
        active_hover_bg, active_hover_border = rgba(p["accent2"], 0.32), p["accent_edge"]
        sys_hover = a(0.08)
        menu_bg, menu_border = "rgba(255, 255, 255, 0.96)", a(0.10)
        menu_text = p["ink"]
        checked_bg = rgba(p["accent2"], 0.14)
        sep = a(0.08)
        tip_bg, tip_border = "rgba(255, 255, 255, 0.96)", a(0.16)
        panel_border, rule = a(0.10), a(0.12)
        scroll, scroll_hover = a(0.20), a(0.35)
        details_title, details_value = p["text"], p["text"]
        file_chip_name = p["ink"]
        dis_text = a(0.30)

    return f"""
        QMainWindow {{
            background: {p['base']};
        }}
        QWidget#centralContainer {{
            background: {container_bg};
            color: {p['text']};
            border-radius: 10px;
        }}
        QWidget#stage {{
            background: {p['stage']};
            border: 1px solid {stage_border};
            border-radius: 12px;
        }}
        QFrame[role="glass"], QFrame#brandBar, QFrame#navBar, QFrame#fileChip {{
            background-color: {capsule_bg};
            border: 1px solid {capsule_border};
            border-radius: 18px;
            min-height: 36px;
            max-height: 36px;
        }}

        QPushButton {{ outline: none; }}
        QPushButton:focus {{ outline: none; }}
        QPushButton[role="nav"] {{
            background-color: {nav_bg};
            color: {nav_text};
            border: 1px solid {nav_border};
            border-radius: 8px;
            padding: 4px 12px;
            min-width: 52px;
            font-size: 12px;
            font-weight: 600;
        }}
        QPushButton[role="nav"]:hover {{
            background-color: {nav_hover_bg};
            border: 1px solid {nav_hover_border};
            color: {nav_hover_text};
        }}
        QPushButton[role="nav"]:disabled {{
            color: {nav_dis_text};
            border: 1px solid {nav_dis_border};
            background-color: {nav_dis_bg};
        }}
        QPushButton#btnPrev, QPushButton#btnNext {{
            min-width: 34px;
            max-width: 34px;
            background-color: {arrow_bg};
            border: 1px solid {arrow_border};
            border-radius: 8px;
            padding: 0px;
        }}
        QPushButton#btnPrev:hover, QPushButton#btnNext:hover {{
            background-color: {arrow_hover_bg};
            border: 1px solid {arrow_hover_border};
        }}
        QPushButton#btnPrev:disabled, QPushButton#btnNext:disabled {{
            border: 1px solid {arrow_dis_border};
            background-color: {arrow_dis_bg};
        }}
        QPushButton[role="icon"] {{
            background-color: {icon_bg};
            color: {nav_text};
            border: 1px solid {icon_border};
            border-radius: 8px;
            padding: 4px;
        }}
        QPushButton[role="icon"][compact="true"] {{
            padding: 1px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
        }}
        QPushButton[role="icon"]:hover {{
            background-color: {icon_hover_bg};
            border: 1px solid {icon_hover_border};
            color: {icon_hover_text};
        }}
        QPushButton[role="icon"][muted="true"] {{
            background-color: {muted_bg};
            border: 1px solid {muted_border};
            color: {muted_text};
        }}
        QPushButton[role="icon"][active="true"] {{
            background-color: {active_bg};
            border: 1px solid {active_border};
            color: {active_text};
        }}
        QPushButton[role="icon"][active="true"]:hover {{
            background-color: {active_hover_bg};
            border: 1px solid {active_hover_border};
            color: {active_text};
        }}
        QPushButton[role="sys"] {{
            background: transparent;
            color: {nav_text};
            border: none;
            border-radius: 6px;
            padding: 0px;
            min-width: 28px;
            min-height: 24px;
        }}
        QPushButton[role="sys"]:hover {{
            background: {sys_hover};
        }}
        QPushButton[role="updatePill"] {{
            background-color: {p['fill']};
            color: {p['on_fill']};
            border: 1px solid {p['accent_edge']};
            border-radius: 12px;
            padding: 0px 12px;
            margin: 0px 6px 0px 2px;
            font-size: 11px;
            font-weight: 700;
        }}
        QPushButton[role="updatePill"]:hover {{
            background-color: {p['fill_hover']};
        }}
        QPushButton#closeBtn {{
            font-size: 11px;
            font-weight: 700;
        }}
        QPushButton#closeBtn:hover, QPushButton#detailsClose:hover {{
            background: #ef4444;
            color: white;
        }}
        QFrame#bottomDock, QWidget#dockWrap {{
            background: transparent;
            border: none;
        }}
        QPushButton#liveOrb {{
            background: transparent;
            border: none;
            padding: 0px;
            min-width: 44px;
            max-width: 44px;
            min-height: 44px;
            max-height: 44px;
        }}
        QPushButton#liveOrb:hover {{
            background: transparent;
            border: none;
        }}
        QPushButton#liveOrb[playing="true"] {{
            background: transparent;
        }}
        QPushButton[role="nav"][active="true"] {{
            background-color: {p['fill']};
            border: 1px solid {p['accent_edge']};
            color: {p['on_fill']};
            font-weight: 700;
        }}
        QPushButton[role="nav"][active="true"]:hover {{
            background-color: {p['fill_hover']};
            border: 1px solid {p['accent_edge']};
            color: {p['on_fill']};
        }}
        QLabel {{ color: {p['text']}; font-size: 13px; }}
        QLabel#brand {{
            font-weight: 800;
            font-size: 14px;
            letter-spacing: 1.4px;
            color: {p['accent_text']};
        }}
        QLabel#fileName {{ font-weight: 600; color: {p['text_muted']}; }}
        QFrame#fileChip QLabel#fileName {{ color: {file_chip_name}; }}
        QLabel#counter {{ font-weight: 700; color: {p['text_muted']}; min-width: 48px; font-size: 12px; }}
        QLabel#emptyTitle {{ font-size: 22px; font-weight: 700; color: {p['text']}; }}
        QLabel#emptySub {{ font-size: 13px; color: {p['text_muted']}; }}
        QMenu {{
            background-color: {menu_bg};
            border: 1px solid {menu_border};
            border-radius: 10px;
            padding: 4px;
        }}
        QMenu::item {{
            color: {menu_text};
            padding: 5px 14px 5px 22px;
            border-radius: 5px;
            font-size: 12px;
        }}
        QMenu::icon {{ padding-left: 6px; }}
        QMenu::item:selected {{
            background-color: {p['fill']};
            color: {p['on_fill']};
        }}
        QMenu::item:checked {{
            background-color: {checked_bg};
            color: {p['accent_text']};
            font-weight: 700;
        }}
        QMenu::item:checked:selected {{
            background-color: {p['fill']};
            color: {p['on_fill']};
            font-weight: 700;
        }}
        QMenu::item:default {{ font-weight: 700; }}
        QMenu::item:disabled {{ color: {dis_text}; }}
        QMenu::indicator {{
            width: 0px;
            height: 0px;
            margin: 0px;
        }}

        QMenu::separator {{ height: 1px; background: {sep}; margin: 3px 6px; }}
        QToolTip {{
            color: {p['text']};
            background-color: {tip_bg};
            border: 1px solid {tip_border};
            border-radius: 8px;
            padding: 5px 8px;
        }}
        QListWidget#filmstrip {{
            background: transparent;
            border: none;
            padding: 0px;
            outline: none;
            selection-background-color: transparent;
            selection-color: transparent;
        }}
        QListWidget#filmstrip::item {{
            background: transparent;
            border: none;
            padding: 0px;
        }}
        QListWidget#filmstrip::item:hover,
        QListWidget#filmstrip::item:selected,
        QListWidget#filmstrip::item:selected:active,
        QListWidget#filmstrip::item:selected:!active {{
            background: transparent;
            border: none;
        }}
        QSlider#seekBar {{
            background: transparent;
            min-height: 16px;
            max-height: 16px;
            border: none;
        }}
        QSlider#seekBar::groove:horizontal {{
            height: 0px;
            background: transparent;
            border: none;
        }}
        QSlider#seekBar::sub-page:horizontal {{
            background: transparent;
            border: none;
        }}
        QSlider#seekBar::handle:horizontal {{
            width: 0px;
            height: 0px;
            background: transparent;
            border: none;
        }}
        QFrame#detailsPanel {{
            background-color: {p['panel']};
            border: 1px solid {panel_border};
            border-radius: 14px;
        }}
        QFrame#detailsPanel QWidget#detailsBody,
        QScrollArea#detailsScroll,
        QScrollArea#detailsScroll > QWidget > QWidget {{
            background: transparent;
            border: none;
        }}
        QLabel#detailsTitle {{ font-size: 16px; font-weight: 800; color: {details_title}; }}
        QLabel#detailsKey {{ font-size: 11px; font-weight: 600; color: {p['text_muted']}; letter-spacing: 0.3px; }}
        QLabel#detailsValue {{ font-size: 13px; color: {details_value}; }}
        QFrame#detailsRule {{
            background: {rule};
            border: none;
            max-height: 1px;
        }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollBar:vertical {{
            background-color: transparent;
            width: 8px;
            margin: 2px;
            border-radius: 4px;
        }}
        QScrollBar::handle:vertical {{
            background: {scroll};
            border-radius: 4px;
            min-height: 24px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {scroll_hover};
        }}
        QScrollBar:horizontal {{
            background-color: transparent;
            height: 8px;
            margin: 2px;
            border-radius: 4px;
        }}
        QScrollBar::handle:horizontal {{
            background: {scroll};
            border-radius: 4px;
            min-width: 24px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {scroll_hover};
        }}
        QScrollBar::add-line, QScrollBar::sub-line,
        QScrollBar::add-page, QScrollBar::sub-page {{
            background: none;
            border: none;
        }}
        QDialog {{
            background-color: {p['base']};
            color: {p['text']};
        }}
    """


# ---------------------------------------------------------------------------------------------------------------
# Pencere içi diyaloglar (Ayarlar, Güncelleme): ChromeDialog gövdesine uygulanır
# ---------------------------------------------------------------------------------------------------------------
def dialog_stylesheet(theme: str, accent: str | None = None) -> str:
    p = palette(theme, accent)
    dark = p["is_dark"]
    btn2_bg = p["hi2"] if dark else p["surface"]
    btn2_hover = p["hi"] if dark else p["hi"]
    edge = p["border"] if dark else p["border"]
    return f"""
        QStackedWidget#stack, QWidget#page {{ background: transparent; }}
        QLabel {{ color: {p['text']}; background: transparent; }}
        QLabel#window_title {{ color: {p['text']}; font-size: 13px; font-weight: 600; }}
        QLabel#detailsTitle {{ color: {p['text']}; font-size: 16px; font-weight: 700; }}
        QFrame#sidebar {{
            background-color: {p['surface']};
            border-radius: 8px;
            border: 1px solid {edge};
        }}
        QPushButton#nav_btn {{
            text-align: left;
            padding-left: 10px;
            color: {p['text_muted']};
            font-size: 13px;
            font-weight: 500;
            background: transparent;
            border: none;
            border-radius: 6px;
        }}
        QPushButton#nav_btn:hover {{
            background-color: {p['raised']};
            color: {p['text']};
        }}
        QPushButton#nav_btn:checked {{
            background-color: {p['fill']};
            color: {p['on_fill']};
            font-weight: 700;
        }}
        QFrame#setting_card {{
            background-color: {p['raised']};
            border: 1px solid {edge};
            border-radius: 8px;
        }}
        QLabel#card_title {{ color: {p['text']}; font-size: 13px; font-weight: 700; }}
        QLabel#card_subtitle {{ color: {p['text_muted']}; font-size: 11px; }}
        QLabel#hint_label {{ color: {p['text_muted']}; font-size: 11px; }}
        QComboBox#card_control {{
            background-color: {p['sunken']};
            border: 1px solid {edge};
            border-radius: 6px;
            color: {p['text']};
            padding: 6px 10px;
            font-size: 12px;
            font-weight: 500;
        }}
        QComboBox#card_control:hover {{ border-color: {p['accent_edge']}; }}
        QComboBox#card_control QAbstractItemView {{
            background-color: {p['raised']};
            color: {p['text']};
            selection-background-color: {p['fill']};
            selection-color: {p['on_fill']};
            border: 1px solid {edge};
            border-radius: 6px;
        }}
        QCheckBox#card_checkbox {{
            color: {p['text']};
            font-size: 12px;
            font-weight: 500;
            spacing: 8px;
        }}
        QCheckBox#card_checkbox:disabled {{ color: {p['text_muted']}; }}
        QLineEdit#txt_folder {{
            background-color: {p['sunken']};
            border: 1px solid {edge};
            border-radius: 6px;
            color: {p['text']};
            padding: 6px 10px;
            font-size: 12px;
        }}
        QLineEdit#txt_folder:focus {{ border-color: {p['accent_edge']}; }}
        QPushButton#action_btn_primary, QPushButton[class="primary"] {{
            background-color: {p['fill']};
            color: {p['on_fill']};
            border: none;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 700;
            padding: 6px 14px;
        }}
        QPushButton#action_btn_primary:hover, QPushButton[class="primary"]:hover {{
            background-color: {p['fill_hover']};
        }}
        QPushButton#action_btn_primary:disabled, QPushButton[class="primary"]:disabled {{
            background-color: {p['surface']};
            color: {p['text_muted']};
            border: 1px solid {edge};
        }}
        QPushButton#action_btn_secondary, QPushButton[class="secondary"] {{
            background-color: {btn2_bg};
            border: 1px solid {edge};
            color: {p['text']};
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
            padding: 6px 12px;
        }}
        QPushButton#action_btn_secondary:hover, QPushButton[class="secondary"]:hover {{
            background-color: {btn2_hover};
            border-color: {p['accent_edge']};
        }}
        QPushButton#action_btn_secondary:disabled, QPushButton[class="secondary"]:disabled {{
            color: {p['text_muted']};
        }}
        QProgressBar#merge_progress {{
            background-color: {p['surface']};
            border: none;
            border-radius: 3px;
        }}
        QProgressBar#merge_progress::chunk {{ background-color: {p['accent']}; border-radius: 3px; }}
        QLabel#merge_status {{
            font-size: 12px;
            color: {p['text']};
            padding: 8px;
            background-color: {p['sunken']};
            border-radius: 6px;
            border: 1px solid {edge};
        }}
        QLabel#updateSize {{ color: {p['text_muted']}; }}
        QTextEdit {{
            background-color: {p['sunken']};
            color: {p['text']};
            border: 1px solid {edge};
            border-radius: 8px;
            padding: 8px;
        }}
        QProgressBar {{
            background-color: {p['surface']};
            border: none;
            border-radius: 4px;
            text-align: center;
            color: {p['text']};
        }}
        QProgressBar::chunk {{ background-color: {p['accent']}; border-radius: 4px; }}
        QPushButton {{
            background-color: {btn2_bg};
            color: {p['text']};
            border: 1px solid {edge};
            border-radius: 6px;
            padding: 7px 16px;
            font-size: 12px;
            font-weight: 500;
        }}
        QPushButton:hover {{ background-color: {btn2_hover}; border-color: {p['accent_edge']}; }}
        QPushButton:disabled {{
            background-color: {p['surface']};
            color: {p['text_muted']};
            border: 1px solid {edge};
        }}
    """


# Satır içi iletiler (hata/uyarı) için okunur renkler: koyu/açık zeminde 4,5:1
def message_colors(theme: str) -> dict[str, str]:
    p = palette(theme)
    bg = p["raised"] if p["is_dark"] else p["sunken"]
    return {"error": readable("#f87171" if p["is_dark"] else "#b91c1c", bg),
            "warn": readable("#fbbf24" if p["is_dark"] else "#92400e", bg)}
