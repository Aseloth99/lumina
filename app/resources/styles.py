"""Lumina stilleri: stil sayfaları `app.resources.theme` içinde nötr gri palet + seçilen vurgu rengiyle üretilir."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

from app.resources import theme as _theme


class _Styles(Mapping):
    """`STYLES["dark"|"light"]`: o anki vurguyla üretilen ana pencere stil sayfası (eski sözlük arayüzü)."""

    _KEYS = ("dark", "light")

    def __getitem__(self, name: str) -> str:
        if name not in self._KEYS:
            raise KeyError(name)
        return _theme.build_stylesheet(name, _theme.active_accent())

    def __contains__(self, name) -> bool:
        return name in self._KEYS

    def __iter__(self):
        return iter(self._KEYS)

    def __len__(self) -> int:
        return len(self._KEYS)


STYLES = _Styles()


def system_is_dark() -> bool:
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication

        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return int(value) == 0
        except Exception:  # okunamıyorsa varsayılan: koyu
            return True
    if sys.platform == "darwin":
        try:
            import subprocess

            out = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return "Dark" in (out.stdout or "")
        except Exception:
            return True
    theme = f"{os.environ.get('GTK_THEME', '')} {os.environ.get('QT_STYLE_OVERRIDE', '')}".lower()
    if "dark" in theme:
        return True
    if "light" in theme:
        return False
    colorfgbg = os.environ.get("COLORFGBG", "")
    if ";" in colorfgbg:
        try:
            return int(colorfgbg.split(";")[-1]) < 8
        except ValueError:
            pass
    return True


def resolve_theme(name: str) -> str:
    if name == "system":
        return "dark" if system_is_dark() else "light"
    return name if name in ("dark", "light") else "dark"
