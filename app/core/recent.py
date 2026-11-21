"""Son açılan dosya ve klasörler ("Son Açılanlar").

Liste QSettings'te (`recent_paths`) tutulur; her Lumina penceresi ayrı bir süreç olduğundan okumadan önce ve yazdıktan
sonra `sync()` çağrılır: bir pencerede açılan dosya ötekinin menüsünde de görünür. Windows atlama listesi (jump list)
kullanılmaz; kabuk kaydı gerektirmez.
"""

from __future__ import annotations

import os
from pathlib import Path

KEY = "recent_paths"
LIMIT = 10


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _read(settings) -> list[str]:
    raw = settings.value(KEY, [])
    if raw is None:
        return []
    if isinstance(raw, str):  # INI biçimi tek öğeli listeyi düz metin olarak geri verir
        raw = [raw] if raw else []
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def recent_paths(settings, limit: int = LIMIT, existing_only: bool = True) -> list[str]:
    """En yeniden eskiye; aynı yol bir kez. `existing_only`: artık olmayan yerel yollar atlanır (ağ yolları
    \\\\sunucu\\… denetlenmez: bağlantı yoksa menü açılırken takılmasın)."""
    try:
        settings.sync()
    except Exception:  # noqa: BLE001 — okunamazsa eldeki değer kullanılır
        pass
    seen: set[str] = set()
    out: list[str] = []
    for text in _read(settings):
        key = _norm(text)
        if key in seen:
            continue
        seen.add(key)
        if existing_only and not text.startswith(("\\\\", "//")) and not os.path.exists(text):
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return out


def add_recent(settings, path: str | Path, limit: int = LIMIT) -> list[str]:
    """`path`i listenin başına koy (varsa yerinden alınır); en çok `limit` öğe tutulur."""
    text = str(path)
    if not text:
        return _read(settings)
    try:
        text = str(Path(text).absolute())
    except OSError:
        pass
    try:
        settings.sync()
    except Exception:  # noqa: BLE001
        pass
    key = _norm(text)
    items = [text] + [p for p in _read(settings) if _norm(p) != key]
    items = items[: max(1, int(limit))]
    settings.setValue(KEY, items)
    try:
        settings.sync()
    except Exception:  # noqa: BLE001
        pass
    return items


def clear_recent(settings) -> None:
    settings.remove(KEY)
    try:
        settings.sync()
    except Exception:  # noqa: BLE001
        pass
