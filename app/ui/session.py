"""Açık Lumina pencereleri — Fotoğraflar gibi birden fazla kopya."""

from __future__ import annotations

windows: list = []


def spawn_window(initial_path: str | None = None):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QIcon

    from app.resources.brand import ensure_app_icon
    from app.ui.main_window import MainWindow

    win = MainWindow(initial_path)
    win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    win.setWindowIcon(QIcon(str(ensure_app_icon())))
    windows.append(win)

    def _drop(*_args) -> None:
        if win in windows:
            windows.remove(win)
        if not windows:
            from PyQt6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.quit()

    win.destroyed.connect(_drop)
    win.show()
    try:
        from app.core.win_launcher import pin_to_launcher

        pin_to_launcher(int(win.winId()))
    except Exception:
        pass
    win.raise_()
    win.activateWindow()
    return win
