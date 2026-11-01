"""Dosya taşıma/kopyalama: eşlenik dosyalar (HEIC + MOV) birlikte, ad çakışmasında ortak sonek — Qt yok."""

from __future__ import annotations

import shutil
from pathlib import Path


def group_destinations(dest_dir: Path, sources: list[Path]) -> list[Path]:
    """Her kaynak için hedef yol. Herhangi biri doluysa TÜM adlara aynı ' (n)' soneki eklenir; böylece
    IMG_0001.HEIC + IMG_0001.MOV çifti aynı gövde adını korur."""
    n = 1
    while True:
        suffix = "" if n == 1 else f" ({n})"
        dests = [dest_dir / f"{src.stem}{suffix}{src.suffix}" for src in sources]
        if not any(d.exists() for d in dests):
            return dests
        n += 1


def transfer(sources: list[Path], dest_dir: Path, move: bool) -> list[tuple[Path, Path]]:
    """Dosyaları `dest_dir`e taşı/kopyala. Dönüş: [(kaynak, hedef)] başarıyla yapılanlar.

    Hata olursa (örn. disk dolu) o ana kadar taşınanlar geri alınır ve istisna yeniden fırlatılır —
    yarım kalmış çift (yalnız HEIC taşınmış) bırakılmaz.
    """
    dest_dir = Path(dest_dir)
    dests = group_destinations(dest_dir, sources)
    done: list[tuple[Path, Path]] = []
    try:
        for src, dst in zip(sources, dests):
            if move:
                shutil.move(str(src), str(dst))
            else:
                shutil.copy2(src, dst)
            done.append((src, dst))
    except Exception:
        for src, dst in reversed(done):
            try:
                if move:
                    shutil.move(str(dst), str(src))
                else:
                    dst.unlink()
            except OSError:
                pass
        raise
    return done


def undo_move(done: list[tuple[Path, Path]]) -> list[Path]:
    """Taşımayı geri al: hedefteki dosyaları özgün yerlerine koy. Özgün yer doluysa o dosya atlanır.
    Dönüş: geri konulan yollar."""
    restored: list[Path] = []
    for src, dst in done:
        if dst.exists() and not src.exists():
            try:
                shutil.move(str(dst), str(src))
                restored.append(src)
            except OSError:
                pass
    return restored
