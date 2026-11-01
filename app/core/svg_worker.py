"""SVG bölge çizimi için işçi SÜREÇ ve istemcisi (Qt YOK).

Neden ayrı süreç: `resvg_py.svg_to_bytes` çizim boyunca GIL'i TUTAR (ölçüm: 4096x4096 çizim 5 sn, bu sürede
başka hiçbir Python iş parçacığı çalışamadı). Çizimi bir iş parçacığına koymak arayüzü yine dondurur; ağır
SVG'lerde (harita, binlerce yol, bulanıklık süzgeci) saniyelerce. Ayrı süreçte çizim ana süreci hiç etkilemez
ve resvg'nin olası çökmesi (bozuk/kötü amaçlı SVG) görüntüleyiciyi düşürmez.

Protokol (stdin/stdout, çerçeveli): 4 bayt uzunluk (LE) + UTF-8 JSON; yanıtın `n` alanı > 0 ise ardından `n` bayt
ham piksel (RGB ya da RGBA) gelir.
  istek: {"op": "render", "id": 1, "path": ..., "mtime_ns": ..., "box": [x, y, w, h], "px": [w, h], "kind": "light"|"keep"}
         {"op": "ping", "id": 2}   {"op": "quit"}
  yanıt: {"id": 1, "ok": true, "w": .., "h": .., "mode": "RGBA", "n": .., "ms": .., "size": [SW, SH]}
         {"id": 1, "ok": false, "error": "..."}

Derlenmiş (PyInstaller) sürümde işçi, uygulamanın kendi exe'sinin `--svg-render-worker` bayrağıyla açılır
(app/main.py başında yönlendirilir); geliştirmede `python -c` ile.
"""

from __future__ import annotations

import atexit
import json
import os
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

WORKER_FLAG = "--svg-render-worker"
MAX_REQUEST_BYTES = 1 << 20
MAX_REGION_PIXELS = 16_000_000
MAX_REGION_EDGE = 8192
_LEN = struct.Struct("<I")


# --------------------------------------------------------------------------- çerçeveleme
def _read_exact(read: Callable[[int], bytes], count: int) -> bytes | None:
    """`count` bayt oku; akış bitti (EOF) ise None."""
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = read(min(remaining, 1 << 20))
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_frame(read: Callable[[int], bytes], limit: int = MAX_REQUEST_BYTES) -> bytes | None:
    head = _read_exact(read, _LEN.size)
    if head is None:
        return None
    (length,) = _LEN.unpack(head)
    if length > limit:
        raise ValueError("frame too large")
    return _read_exact(read, length) if length else b""


def encode_frame(message: dict) -> bytes:
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return _LEN.pack(len(body)) + body


# --------------------------------------------------------------------------- işçi süreç
class _SourceCache:
    """Son kaynak (dosya + değişiklik zamanı): aynı belgenin ardışık bölgeleri dosyayı yeniden okumaz."""

    def __init__(self) -> None:
        self.key: tuple | None = None
        self.source = None


def _handle_render(req: dict, cache: _SourceCache) -> tuple[dict, bytes]:
    from app.core import svg_region

    rid = req.get("id")
    try:
        px = (int(req["px"][0]), int(req["px"][1]))
        box = tuple(float(v) for v in req["box"])
        kind = "light" if req.get("kind") == "light" else "keep"
        if len(box) != 4 or px[0] < 1 or px[1] < 1:
            raise ValueError("bad region")
        if px[0] * px[1] > MAX_REGION_PIXELS or max(px) > MAX_REGION_EDGE:
            raise ValueError("region too large")
        path = Path(req["path"])
        key = (str(path), req.get("mtime_ns"))
        if cache.key != key:
            cache.key, cache.source = None, None
            cache.source = svg_region.load_source(path)
            cache.key = key
        if cache.source is None:
            return {"id": rid, "ok": False, "error": "unsupported"}, b""
        started = time.perf_counter()
        img = svg_region.render_region_resvg(cache.source, box, px, kind)
        data = img.tobytes()
        header = {
            "id": rid,
            "ok": True,
            "w": img.width,
            "h": img.height,
            "mode": img.mode,
            "n": len(data),
            "ms": round((time.perf_counter() - started) * 1000.0, 1),
            "size": list(cache.source.size),
        }
        return header, data
    except Exception as exc:  # noqa: BLE001 — tek istek başarısız olur, işçi yaşamaya devam eder
        return {"id": rid, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}, b""


def _win_channels():
    """Windows: stdin/stdout'u CRT dosya numaralarından değil Win32 tutamaçlarından (GetStdHandle) oku/yaz.

    Derlenmiş exe pencereli (GUI alt sistemi) olduğundan CRT'nin 0/1 numaraları güvenilir değildir; üst sürecin
    `STARTF_USESTDHANDLES` ile verdiği boru tutamaçları ise GetStdHandle ile her zaman gelir. Sonra STDOUT NUL'a
    yönlendirilir: kitaplıkların stdout'a yazdığı her şey ikili kanalı bozmaz.
    """
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetStdHandle.restype = ctypes.c_void_p
    k32.GetStdHandle.argtypes = [wintypes.DWORD]
    k32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.ReadFile.restype = wintypes.BOOL
    k32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.WriteFile.restype = wintypes.BOOL
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.SetStdHandle.argtypes = [wintypes.DWORD, ctypes.c_void_p]
    k32.SetStdHandle.restype = wintypes.BOOL
    invalid = ctypes.c_void_p(-1).value
    h_in = k32.GetStdHandle(0xFFFFFFF6)  # STD_INPUT_HANDLE (-10)
    h_out = k32.GetStdHandle(0xFFFFFFF5)  # STD_OUTPUT_HANDLE (-11)
    if h_in in (None, 0, invalid) or h_out in (None, 0, invalid):
        raise OSError("std handles unavailable")

    def read(count: int) -> bytes:
        buffer = ctypes.create_string_buffer(count)
        got = wintypes.DWORD(0)
        if not k32.ReadFile(h_in, buffer, count, ctypes.byref(got), None):
            error = ctypes.get_last_error()
            if error in (109, 232):  # ERROR_BROKEN_PIPE / ERROR_NO_DATA: ana süreç gitti = EOF
                return b""
            raise OSError(error, "ReadFile")
        return buffer.raw[: got.value]

    def write(data: bytes) -> None:
        view = memoryview(data)
        while view:
            chunk = bytes(view[: 1 << 20])
            written = wintypes.DWORD(0)
            if not k32.WriteFile(h_out, chunk, len(chunk), ctypes.byref(written), None):
                raise OSError(ctypes.get_last_error(), "WriteFile")
            view = view[written.value :]

    nul = k32.CreateFileW("NUL", 0x40000000, 3, None, 3, 0, None)  # GENERIC_WRITE, share rw, OPEN_EXISTING
    if nul not in (None, 0, invalid):
        k32.SetStdHandle(0xFFFFFFF5, nul)
    return read, write


def _fd_channels(stdin_fd: int, stdout_fd: int):
    """POSIX / yedek: dosya numaraları (fd 1 önce ayrılıp boşa yönlendirilir)."""
    out_fd = stdout_fd
    try:
        out_fd = os.dup(stdout_fd)
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, stdout_fd)
        os.close(null)
    except OSError:
        out_fd = stdout_fd

    def read(count: int) -> bytes:
        return os.read(stdin_fd, count)

    def write(data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(out_fd, view[: 1 << 20])
            view = view[written:]

    return read, write


def serve(stdin_fd: int = 0, stdout_fd: int = 1) -> int:
    """İşçi döngüsü: istek oku, bölgeyi çiz, yanıtla. stdin kapanınca (ana süreç öldü) çıkar.

    `LUMINA_SVG_WORKER_IO=fd` Windows'ta da CRT dosya numaralarını zorlar (yalnızca sınama için).
    """
    channels = None
    if sys.platform == "win32" and os.environ.get("LUMINA_SVG_WORKER_IO") != "fd":
        try:
            channels = _win_channels()
        except Exception:  # noqa: BLE001 — tutamaç yoksa dosya numaralarına düş
            channels = None
    read, write = channels or _fd_channels(stdin_fd, stdout_fd)

    cache = _SourceCache()
    while True:
        try:
            frame = _read_frame(read)
        except (OSError, ValueError):
            return 1
        if frame is None:
            return 0
        try:
            req = json.loads(frame.decode("utf-8"))
        except ValueError:
            continue
        op = req.get("op")
        if op == "quit":
            return 0
        try:
            if op == "ping":
                from app.core import svg_region

                header, payload = {"id": req.get("id"), "ok": True, "resvg": svg_region.resvg_py is not None}, b""
            elif op == "render":
                header, payload = _handle_render(req, cache)
            else:
                header, payload = {"id": req.get("id"), "ok": False, "error": "unknown op"}, b""
            write(encode_frame(header) + payload if payload else encode_frame(header))
        except OSError:
            return 0  # ana süreç gitti


# --------------------------------------------------------------------------- istemci
def worker_command() -> list[str]:
    """İşçiyi açan komut: derlenmişte uygulamanın kendisi + bayrak, geliştirmede `python -c`."""
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_FLAG]
    root = str(Path(__file__).resolve().parents[2])
    code = f"import sys; sys.path.insert(0, {root!r}); from app.core.svg_worker import serve; raise SystemExit(serve())"
    return [sys.executable, "-X", "utf8", "-B", "-c", code]


class WorkerClient:
    """Tek bir işçi sürece istek gönderir; yanıtlar okuyucu iş parçacığında `callback(header, payload)` ile gelir.

    İşçi tembel açılır, ölürse bir sonraki istekte yeniden açılır; 2 dakikada 3 ölüm olursa `broken` olur (çağıran
    yedek yola düşer). Zaman aşımında işçi (yalnızca kendi Popen nesnesi) öldürülür.
    """

    def __init__(self, command: list[str] | None = None, request_timeout: float = 90.0) -> None:
        self._command = command
        self._timeout = request_timeout
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._pending: dict[int, tuple[Callable[[dict, bytes], None], float]] = {}
        self._next_id = 0
        self._deaths: list[float] = []
        self._closed = False
        atexit.register(self.close)

    @property
    def broken(self) -> bool:
        cutoff = time.monotonic() - 120.0
        return self._closed or len([t for t in self._deaths if t >= cutoff]) >= 3

    def _spawn(self) -> subprocess.Popen:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            self._command or worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            cwd=None,
        )
        threading.Thread(target=self._read_loop, args=(proc,), name="LuminaSvgReader", daemon=True).start()
        threading.Thread(target=self._watch, args=(proc,), name="LuminaSvgWatch", daemon=True).start()
        return proc

    def submit(self, request: dict, callback: Callable[[dict, bytes], None]) -> bool:
        """İsteği gönder; işçi yoksa/bozuksa False (callback çağrılmaz)."""
        with self._lock:
            if self.broken:
                return False
            try:
                if self._proc is None or self._proc.poll() is not None:
                    self._proc = self._spawn()
                self._next_id += 1
                rid = self._next_id
                self._pending[rid] = (callback, time.monotonic())
                proc = self._proc
                proc.stdin.write(encode_frame(dict(request, id=rid)))
                proc.stdin.flush()
                return True
            except (OSError, ValueError):
                self._pending.pop(self._next_id, None)
                self._note_death_locked(self._proc)
                return False

    def _note_death_locked(self, proc: subprocess.Popen | None) -> None:
        self._deaths.append(time.monotonic())
        if proc is not None and self._proc is proc:
            self._proc = None
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass

    def _read_loop(self, proc: subprocess.Popen) -> None:
        stream = proc.stdout
        try:
            while True:
                frame = _read_frame(stream.read, limit=MAX_REQUEST_BYTES)
                if frame is None:
                    break
                header = json.loads(frame.decode("utf-8"))
                payload = b""
                count = int(header.get("n") or 0)
                if count:
                    payload = _read_exact(stream.read, count)
                    if payload is None:
                        break
                with self._lock:
                    entry = self._pending.pop(header.get("id"), None)
                if entry is not None:
                    try:
                        entry[0](header, payload)
                    except Exception:  # noqa: BLE001 — geri çağrının hatası okuyucuyu öldürmesin
                        pass
        except (OSError, ValueError):
            pass
        self._on_dead(proc)

    @staticmethod
    def _close_pipes(proc: subprocess.Popen) -> None:
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass

    def _on_dead(self, proc: subprocess.Popen) -> None:
        try:
            proc.wait(timeout=2.0)  # zombi bırakma
        except Exception:  # noqa: BLE001
            pass
        self._close_pipes(proc)
        with self._lock:
            owned = self._proc is proc
            if owned:
                self._proc = None
                if not self._closed:
                    self._deaths.append(time.monotonic())
            stale = list(self._pending.items()) if owned else []
            for rid, _ in stale:
                self._pending.pop(rid, None)
        for _rid, (callback, _t) in stale:
            try:
                callback({"ok": False, "error": "worker-died"}, b"")
            except Exception:  # noqa: BLE001
                pass

    def _watch(self, proc: subprocess.Popen) -> None:
        while proc.poll() is None:
            time.sleep(0.5)
            with self._lock:
                oldest = min((t for _cb, t in self._pending.values()), default=None)
            if oldest is not None and time.monotonic() - oldest > self._timeout:
                try:
                    proc.kill()  # yalnızca KENDİ başlattığımız işçi
                except OSError:
                    pass
                return

    def close(self) -> None:
        with self._lock:
            self._closed = True
            proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.write(encode_frame({"op": "quit"}))
            proc.stdin.flush()
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=1.0)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except OSError:
                pass
        for stream in (proc.stdout,):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


_client: WorkerClient | None = None
_client_lock = threading.Lock()


def get_client() -> WorkerClient:
    """Süreç genelinde tek işçi istemcisi."""
    global _client
    with _client_lock:
        if _client is None:
            _client = WorkerClient()
        return _client
