"""GERÇEK platformda (offscreen değil) SVG bindirmesi keskinlik ölçümü; tests/test_svg_overlay.py alt süreç olarak çalıştırır.

Pencere görünür ekranın dışına (x=-6000) taşınır; `viewport().grab()` ile görüntü alınır. Çıktı: JSON (argv[1]).
İsteğe bağlı argv[2]: ekran görüntülerinin kaydedileceği klasör. Kullanıcının verisine/kayıt defterine dokunmaz
(AppData geçici klasöre yönlendirilir).
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="lumina_svg_real_")
for _key in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
    os.environ[_key] = _TMP
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import numpy as np  # noqa: E402
from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

import _svg_samples  # noqa: E402

app = QApplication([])

from app.core.image_loader import pil_to_qimage  # noqa: E402
from app.core.svg_loader import load_svg  # noqa: E402
from app.ui.canvas import PhotoCanvas  # noqa: E402


def pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def settled(canvas: PhotoCanvas) -> bool:
    ctl = canvas._svg
    return not ctl._timer.isActive() and not ctl._busy and ctl._queued is None


def wait_settled(canvas: PhotoCanvas, timeout: float = 20.0) -> bool:
    end = time.monotonic() + timeout
    pump(200)
    while time.monotonic() < end:
        if settled(canvas):
            pump(100)
            return True
        pump(50)
    return False


def gray(image) -> np.ndarray:
    img = image.convertToFormat(image.Format.Format_RGB32)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)[:, : img.width(), :3]
    return (0.299 * arr[..., 2] + 0.587 * arr[..., 1] + 0.114 * arr[..., 0]).astype(np.float32)


def sharpness(g: np.ndarray) -> dict:
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    gx = np.abs(np.diff(g, axis=1))
    return {"laplacian_var": float(lap.var()), "max_step": float(gx.max()), "p99_step": float(np.percentile(gx, 99.9))}


def lowpass(g: np.ndarray, k: int = 8) -> np.ndarray:
    h, w = (g.shape[0] // k) * k, (g.shape[1] // k) * k
    return g[:h, :w].reshape(h // k, k, w // k, k).mean(axis=(1, 3))


def main() -> int:
    out_path = pathlib.Path(sys.argv[1])
    shots = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None
    sample = sys.argv[3] if len(sys.argv) > 3 else "thin_lines"
    folder = pathlib.Path(_TMP) / "svg"
    folder.mkdir()
    path = _svg_samples.write_all(folder)[sample]

    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    canvas = PhotoCanvas()
    layout.addWidget(canvas)
    host.resize(900, 600)
    host.move(-6000, 0)
    host.show()
    pump(300)
    image = pil_to_qimage(load_svg(path))
    canvas.set_qimage(image)
    canvas.attach_svg(str(path))
    wait_settled(canvas)

    result: dict = {"platform": app.platformName(), "dpr": canvas.devicePixelRatioF(), "image": list(canvas.image_size())}
    width, height = canvas.image_size()
    focus = (0.70 * width, 0.50 * height)  # çember kenarı / çizgi kesişimi civarı
    for scale in (0.5, 1, 8, 20, 64):
        entry: dict = {}
        for mode in ("off", "on"):
            canvas._svg.enabled = mode == "on"
            canvas._svg.detach()
            canvas.attach_svg(str(path)) if mode == "on" else None
            canvas.resetTransform()
            canvas._fitted = False
            canvas.scale(scale, scale)
            canvas.centerOn(*focus)
            canvas.zoom_changed.emit()
            ok = wait_settled(canvas) if mode == "on" else (pump(300) or True)
            pix = canvas.viewport().grab()
            g = gray(pix.toImage())
            entry[mode] = dict(sharpness(g), settled=ok, overlay=canvas.pixmap_item.has_overlay())
            entry[mode]["_g"] = g
            if shots is not None:
                shots.mkdir(parents=True, exist_ok=True)
                pix.save(str(shots / f"{sample}_x{scale}_{mode}.png"))
        off_g, on_g = entry["off"].pop("_g"), entry["on"].pop("_g")
        entry["align_mad_lowpass"] = float(np.abs(lowpass(off_g) - lowpass(on_g)).mean())
        result[str(scale)] = entry
    canvas._svg.enabled = True
    out_path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    host.close()
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    import shutil

    shutil.rmtree(_TMP, ignore_errors=True)  # geçici profil/SVG klasörü
    os._exit(code)  # çalışan işçi süreç/iş parçacıklarını beklemeden çık (ana süreç ölünce işçi stdin EOF ile çıkar)
