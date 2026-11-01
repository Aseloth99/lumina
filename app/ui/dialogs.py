"""Özel mesaj kutusunu (`message_box`) Lumina'nın teması, simgesi ve diline bağlar (süreç başına bir kez yeterli)."""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from app.resources import theme
from app.resources.locales import get_active_language
from app.ui import message_box


def configure_dialogs() -> None:
    """Kutular her açılışta o anki temayı/vurguyu (`theme.active_palette`) ve kullanıcının seçtiği dili okur."""
    message_box.configure(
        palette_provider=theme.active_palette,
        app_icon_provider=lambda: QApplication.windowIcon(),
        lang_provider=get_active_language,
    )
