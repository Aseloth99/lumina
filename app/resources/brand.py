"""Lumina uygulama ikonu — pencere, diyalog ve EXE."""

from __future__ import annotations

import shutil
from pathlib import Path


def write_app_ico(path: str | Path) -> Path:
    from PIL import Image, ImageDraw, ImageFilter

    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = size / 2, size / 2

    # İnce Canlı Fotoğraf dış hareket/dalga halkaları (zümrüt yeşili/cam)
    for r, width, alpha in [(250, 4, 45), (238, 5, 85), (226, 6, 135)]:
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(16, 185, 129, alpha), width=width)

    # Ana zümrüt lens çekirdeği (pürüzsüz degrade)
    r_core = 216
    for i in range(r_core):
        t = i / r_core
        curr_r = r_core - i
        red = int(16 * (1 - t * 0.5) + 4 * (t * 0.5))
        green = int(185 * (1 - t * 0.35) + 120 * (t * 0.35))
        blue = int(129 * (1 - t * 0.4) + 87 * (t * 0.4))
        draw.ellipse((cx - curr_r, cy - curr_r, cx + curr_r, cy + curr_r), fill=(red, green, blue, 255))

    # Derinlik katan iç ışık halkası
    r_ring = 168
    draw.ellipse((cx - r_ring, cy - r_ring, cx + r_ring, cy + r_ring), outline=(255, 255, 255, 55), width=6)

    # Üst parlama / cam yansıması
    sheen = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(sheen)
    sdraw.ellipse((cx - 165, cy - 195, cx + 165, cy - 20), fill=(255, 255, 255, 42))
    sheen = sheen.filter(ImageFilter.GaussianBlur(12))
    img = Image.alpha_composite(img, sheen)

    # Net beyaz oynatma / iris üçgeni
    draw = ImageDraw.Draw(img)
    poly = [
        (int(cx - 48), int(cy - 84)),
        (int(cx - 48), int(cy + 84)),
        (int(cx + 74), int(cy)),
    ]
    draw.polygon(poly, fill=(255, 255, 255, 255))

    img_256 = img.resize((256, 256), Image.Resampling.LANCZOS)
    img_256.save(
        dest,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    return dest


def _local_icon() -> Path:
    from app.core.paths import get_user_data_dir

    return get_user_data_dir() / "Lumina.ico"


def bundled_icon() -> Path:
    return Path(__file__).resolve().with_name("lumina.ico")


def _usable(path: Path) -> bool:
    return path.is_file() and path.stat().st_size >= 200


def ensure_app_icon(force: bool = False) -> Path:
    """İkonu yaz; kayıt defteri ve pencere aynı dosyayı kullanır."""
    bundled = bundled_icon()
    last = _local_icon()
    for dest in (bundled, last):
        try:
            if force or not _usable(dest):
                if dest != bundled and not force and _usable(bundled):
                    # Paketteki ikon write_app_ico'nun çıktısıyla bayt bayt aynı; PIL ile yeniden çizmek
                    # ilk açılışı ~100 ms geciktiriyordu.
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(bundled, dest)
                else:
                    write_app_ico(dest)
            last = dest
        except OSError:
            continue
    return last
