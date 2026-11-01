"""Kalıcı, tek iş parçacıklı seek-önizleme decoder'ı.

Neden: eski tasarım her hover'da havuza ayrı worker atıyor, ortak (kilitsiz) VideoCapture
önbelleği tutuyor ve süreç genelinde stderr'i yönlendiriyordu. Burada:

* Tek bir arka plan thread'i, tek bir VideoCapture'a sahiptir (capture hiçbir zaman
  paylaşılmaz, kilit gerekmez).
* Hover (kesin kare) isteği her zaman storyboard üretiminin önüne geçer; yalnızca EN SON
  istek bekletilir (eski istekler atlanır).
* Tembel: video yalnızca önizlemeye ilk ihtiyaç duyulunca (fare seek çubuğuna gelince ya da ilk kare
  isteğinde) açılır. Eskiden her video açılışında storyboard baştan sona çözülüyordu: kimse önizlemeye
  bakmasa da 2 dk'lık 1080p videoda ~2,6 sn, 4K HEVC'de ~3,2 sn işlemci.
* Storyboard kaba → ince sırayla üretilir (ilk 8 kare video boyunca eşit dağılır), böylece
  önizleme ilk hover'dan hemen sonra her yerde kullanılabilir.
* Kaynak değişince (kaynak kimliği "nesil") eski istekler/işler geçersiz olur; capture'ı
  yalnızca thread kapatır. `release_source(wait=True)` geçici dosyaların silinebilmesi için
  capture bırakılana dek (sınırlı süre) bekler.

Qt sinyali (`frame_ready`) çalışan thread'den yayılır; alıcı GUI thread'inde kuyruklanır.
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, pyqtSignal

from app.core.video_io import (
    bgr_to_qimage,
    downscale_bgr,
    load_cv2,
    open_capture,
    orient_frame,
)

_BACKGROUND_PAUSE_S = 0.12  # oynatma sürerken storyboard karelerinin arası (CPU'yu rahat bırak)


def board_positions(duration_ms: int) -> list[int]:
    """Storyboard zamanları, kaba → ince sırayla (her önek videoya eşit dağılır)."""
    dur = max(1, int(duration_ms))
    slots = 16 if dur <= 60_000 else 32
    while slots > 4 and dur / slots < 120:
        slots //= 2
    bits = slots.bit_length() - 1
    order = sorted(range(slots), key=lambda j: int(format(j, f"0{bits}b")[::-1], 2))
    return [int((j + 0.5) / slots * dur) for j in order]


class PreviewDecoder(QObject):
    # (kaynak kimliği, kare zamanı ms, QImage)
    frame_ready = pyqtSignal(object, int, object)

    def __init__(self, parent: QObject | None = None, max_w: int = 188, max_h: int = 106) -> None:
        super().__init__(parent)
        self._max_w = int(max_w)
        self._max_h = int(max_h)
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._src: tuple | None = None  # (identity, path, duration_hint_ms)
        self._gen = 0
        self._frame_req: tuple[int, int] | None = None  # (nesil, ms)
        # Bu kaynağın önizlemesi istendi (hover): video açılır, storyboard üretilir.
        self._armed = False
        self._background = False
        self._stop = False
        self._closed = False
        self._released = threading.Event()
        self._released.set()

    # ------------------------------------------------------------------ API
    def set_source(self, identity, path: str, duration_hint_ms: int = 0) -> None:
        """Yeni kaynak: eski işler iptal. Video henüz açılmaz (bkz. `activate`)."""
        with self._cond:
            if self._closed:
                return
            if self._src is not None and self._src[0] == identity:
                return
            self._src = (identity, str(path), int(duration_hint_ms or 0))
            self._gen += 1
            self._frame_req = None
            self._armed = False
            self._released.clear()
            self._ensure_thread_locked()
            self._cond.notify_all()

    def activate(self, identity) -> None:
        """Önizleme lazım olacak (fare seek çubuğunda): videoyu aç, storyboard'u kaba → ince üret."""
        with self._cond:
            if self._closed or self._src is None or self._src[0] != identity or self._armed:
                return
            self._armed = True
            self._cond.notify_all()

    def request_frame(self, identity, ms: int) -> None:
        """Belirli zamanın karesini iste (kaynağı da etkinleştirir). Yalnızca en son istek korunur."""
        with self._cond:
            if self._closed or self._src is None or self._src[0] != identity:
                return
            self._armed = True
            self._frame_req = (self._gen, max(0, int(ms)))
            self._cond.notify_all()

    def set_background(self, flag: bool) -> None:
        """True: oynatma sürüyor, storyboard üretimini ağırlaştırma (hover yine anında)."""
        with self._cond:
            self._background = bool(flag)

    def release_source(self, wait: bool = True, timeout: float = 1.5) -> None:
        """Kaynağı bırak. wait=True: capture kapanana (sınırlı süre) kadar bekle."""
        with self._cond:
            self._src = None
            self._gen += 1
            self._frame_req = None
            alive = self._thread is not None and self._thread.is_alive()
            if alive:
                self._released.clear()
            self._cond.notify_all()
        if wait and alive:
            self._released.wait(timeout)

    def shutdown(self, timeout: float = 1.0) -> None:
        with self._cond:
            self._closed = True
            self._stop = True
            self._src = None
            self._gen += 1
            self._frame_req = None
            thread = self._thread
            self._cond.notify_all()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)

    # ------------------------------------------------------------- iç işler
    def _ensure_thread_locked(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="LuminaPreview", daemon=True)
            self._thread.start()

    @staticmethod
    def _duration_ms(cap, hint_ms: int) -> int:
        if hint_ms > 0:
            return int(hint_ms)
        cv2 = load_cv2()
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
            if fps < 1.0 or fps > 240.0:
                fps = 30.0
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if frames > 1:
                return max(1, int(frames / fps * 1000))
        except Exception:
            pass
        return 1000

    def _run(self) -> None:
        cap = None
        rot = 0
        local_gen = -1
        opened_gen = -1  # videosu açılan (ya da açılması denenen: başarısızsa yinelenmez) nesil
        identity = None
        board: list[int] = []
        try:
            while True:
                with self._cond:
                    while True:
                        if self._stop:
                            return
                        if self._gen != local_gen:
                            break
                        if self._src is not None and self._armed and opened_gen != self._gen:
                            break  # önizleme istendi: videoyu şimdi aç
                        if cap is not None and (self._frame_req is not None or board):
                            break
                        self._cond.wait()
                    gen = self._gen
                    src = self._src
                    armed = self._armed
                    background = self._background
                    req = None
                    if cap is not None and gen == local_gen:
                        req = self._frame_req
                        self._frame_req = None

                if gen != local_gen:
                    local_gen = gen
                    self._close(cap)
                    cap, board, identity = None, [], None
                    # Bu nesil için henüz açık bir dosya yok (silinecek geçici dosyalar bekletilmesin).
                    self._released.set()
                    continue

                if cap is None:
                    if src is None or not armed or opened_gen == gen:
                        continue
                    opened_gen = gen
                    with self._cond:
                        if self._gen != gen:
                            continue
                        self._released.clear()
                    identity, path, hint_ms = src
                    cap, rot = open_capture(path)
                    if cap is None:
                        self._released.set()
                        continue
                    board = board_positions(self._duration_ms(cap, hint_ms))
                    continue

                if req is not None and req[0] == gen:
                    self._decode_and_emit(cap, rot, identity, req[1])
                    continue

                if board:
                    self._decode_and_emit(cap, rot, identity, board.pop(0))
                    if background and board:
                        with self._cond:
                            if not self._stop and self._frame_req is None and self._gen == local_gen:
                                self._cond.wait(_BACKGROUND_PAUSE_S)
        finally:
            self._close(cap)
            self._released.set()

    @staticmethod
    def _close(cap) -> None:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

    def _decode_and_emit(self, cap, rot: int, identity, ms: int) -> None:
        cv2 = load_cv2()
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(ms))
            ok, frame = cap.read()
            if not ok or frame is None:
                return
            image = bgr_to_qimage(downscale_bgr(orient_frame(frame, rot), self._max_w, self._max_h))
            if image.isNull():
                return
        except Exception:
            return
        if self._closed:
            return
        self.frame_ready.emit(identity, int(ms), image)
