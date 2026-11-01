"""Kullanıcı dizinleri — Windows AppData, macOS Application Support, Linux XDG."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ID = "Lumina"


def launcher_path() -> Path | None:
    """Hızlı açılış başlatıcısı (kullanıcının Lumina.exe'si): yalnızca derlenmiş sürümde ve başlatıcıyla açıldıysa."""
    launcher = os.environ.get("LUMINA_LAUNCHER")
    if getattr(sys, "frozen", False) and launcher and Path(launcher).is_file():
        return Path(launcher)
    return None


def app_executable() -> Path:
    """Kullanıcının çalıştırdığı Lumina.exe (derlenmiş sürümde).

    Hızlı açılış başlatıcısıyla (launcher/) çalışan süreç, %LOCALAPPDATA%\\Lumina\\runtime\\<sürüm>\\ altındaki
    açılmış kopyadır; başlatıcı kendi yolunu `LUMINA_LAUNCHER` ile bildirir. "Birlikte aç" kaydı, güncelleyici
    ve görev çubuğu sabitlemesi sürümle değişen o kopyayı değil, bu exe'yi göstermeli.
    """
    return launcher_path() or Path(sys.executable)


def get_application_path() -> Path:
    if getattr(sys, "frozen", False):
        return app_executable().resolve().parent
    return Path(__file__).resolve().parents[2]


def get_user_data_dir(app_id: str = APP_ID) -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / app_id
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_id
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / app_id


def ensure_user_data_dir(app_id: str = APP_ID) -> Path:
    folder = get_user_data_dir(app_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder
