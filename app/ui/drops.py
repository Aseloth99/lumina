"""Dosya sürükle-bırak — QGraphicsView sahnesinin olayı yutmasını önler."""

from __future__ import annotations


class FileDropMixin:
    _drop_handler = None

    def set_drop_handler(self, handler) -> None:
        self._drop_handler = handler
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        self._accept_file_drag(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        self._accept_file_drag(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        handler = getattr(self, "_drop_handler", None)
        if urls and handler:
            handler(urls[0].toLocalFile())
            event.acceptProposedAction()
            return
        event.ignore()

    def _accept_file_drag(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()
