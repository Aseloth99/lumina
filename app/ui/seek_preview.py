"""Seek çubuğu hover önizlemesi denetleyicisi (MainWindow'dan ayrılmış sorumluluk).

Akış:
1. Dosya açılınca `set_media` yalnızca kaynağı bildirir; video henüz çözülmez (kimse önizlemeye
   bakmayabilir, bakmayana işlemci harcanmasın).
2. İlk hover: decoder videoyu açar, imlecin karesi önce gelir, ardından kaba → ince storyboard.
   Sonraki hover'larda en yakın hazır kare ANINDA gösterilir; kesin kare ~30 ms sonra istenir ve
   gelince yerine geçer (YouTube tarzı kademeli iyileşme).
3. Zaman etiketi her zaman imlecin gösterdiği zamanı yazar (karenin zamanını değil).
4. Depo dosya kimliğine (yol, boyut, mtime) bağlıdır: dosya değişince eski kareler görünmez.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QLabel, QWidget

from app.core.preview_decoder import PreviewDecoder
from app.core.preview_store import PreviewStore, identity_for
from app.ui.live_button import _fmt_ms

# İstenen zamana bu kadar yakın bir kare varsa "yeterince kesin" sayılır, yeni decode istenmez.
_EXACT_TOLERANCE_MS = 250
_REQUEST_DEBOUNCE_MS = 30


class SeekPreviewController(QObject):
    def __init__(
        self,
        parent: QObject,
        frame: QWidget,
        image_label: QLabel,
        time_label: QLabel,
        place: Callable[[float], None],
        duration_ms: Callable[[], int],
    ) -> None:
        super().__init__(parent)
        self._frame = frame
        self._image = image_label
        self._time = time_label
        self._place = place
        self._duration_ms = duration_ms
        self._store = PreviewStore()
        self._decoder = PreviewDecoder(self)
        self._decoder.frame_ready.connect(self._on_frame)
        self._path: str | None = None
        self._hover_ms: int | None = None
        self._shown_ms: int | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._request_exact)

    # ------------------------------------------------------------ kaynak
    @property
    def path(self) -> str | None:
        return self._path

    def set_media(self, path: str | None, duration_hint_ms: int = 0) -> None:
        """Önizlenecek videoyu ayarla (aynı dosya ise bir şey yapmaz)."""
        if not path:
            self.release_media()
            return
        ident = identity_for(path)
        if ident is None:
            self.release_media()
            return
        if ident == self._store.identity and self._path == path:
            return
        self._timer.stop()
        self._store.reset(ident)
        self._path = path
        self._shown_ms = None
        self._image.clear()
        self._decoder.set_source(ident, path, duration_hint_ms)

    def release_media(self, wait: bool = True) -> None:
        """Videoyu bırak ve kareleri unut.

        wait=True: decoder dosyayı bırakana dek (sınırlı süre) bekle — silinecek geçici
        dosyalar için gerekli. Kalıcı dosyalarda wait=False yeterli (GUI'yi bekletme).
        """
        self._timer.stop()
        self._decoder.release_source(wait=wait)
        self._store.clear()
        self._path = None
        self._hover_ms = None
        self._shown_ms = None
        self._image.clear()
        self.hide()

    def set_background(self, playing: bool) -> None:
        self._decoder.set_background(playing)

    def shutdown(self) -> None:
        self._timer.stop()
        self._decoder.shutdown()

    # ------------------------------------------------------------- hover
    def on_hover(self, ratio: float) -> None:
        if self._path is None:
            return
        ratio = max(0.0, min(1.0, float(ratio)))
        dur = max(1, int(self._duration_ms() or 1))
        ms = int(ratio * dur)
        if self._store.identity is not None:
            self._decoder.activate(self._store.identity)  # ilk hover: video şimdi açılır
        self._hover_ms = ms
        self._time.setText(_fmt_ms(ms))
        self._show_best(ms)
        self._place(ratio)
        self._frame.show()
        self._frame.raise_()
        self._timer.start(_REQUEST_DEBOUNCE_MS)

    def hide(self) -> None:
        self._timer.stop()
        self._frame.hide()

    # --------------------------------------------------------------- iç
    def _show_best(self, ms: int) -> None:
        hit = self._store.nearest(ms)
        if hit is None:
            if self._shown_ms is None:
                self._image.setText("…")
            return
        frame_ms, image = hit
        if frame_ms == self._shown_ms:
            return
        self._image.setPixmap(QPixmap.fromImage(image))
        self._shown_ms = frame_ms

    def _request_exact(self) -> None:
        ms = self._hover_ms
        ident = self._store.identity
        if ms is None or ident is None or not self._frame.isVisible():
            return
        if self._store.within(ms, _EXACT_TOLERANCE_MS) is not None:
            return
        self._decoder.request_frame(ident, ms)

    def _on_frame(self, identity, frame_ms: int, image) -> None:
        # Bayat sonuç (başka dosya/eski kimlik): depo reddeder.
        if not self._store.add(identity, frame_ms, image):
            return
        if self._hover_ms is not None and self._frame.isVisible():
            self._show_best(self._hover_ms)
