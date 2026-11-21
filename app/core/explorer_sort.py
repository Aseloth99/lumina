"""Dosya Gezgini'nin sıralamasını oku: Lumina bir dosyayı açarken o klasör bir Gezgin penceresinde açıksa aynı sırayı izler.

Kullanıcı Gezgin'de "Tarih (yeniden eskiye)" sıralı klasörden son fotoğrafı açıyor, Lumina ise ad sırasıyla gezdiriyordu:
son fotoğraftan sonra Gezgin'de en altta duran videolar geliyordu. Gezgin'in sırası Shell COM ile okunur:
Shell.Application.Windows() -> pencere.Document.Folder.Self.Path ve pencere.Document.SortColumns
("prop:-System.ItemDate;" gibi; "-" = azalan, ilk sütun birincil sıralamadır).

COM çağrıları arayüz iş parçacığında YAPILMAZ: klasör tarama işçisinden çağrılır, ayrıca kısa bir süre sınırı vardır (Gezgin
askıda kalırsa beklenmez). Eşleşen pencere yoksa, sütun desteklenmiyorsa (boyut, tür…) ya da COM hata verirse None döner:
çağıran kayıtlı sıralamayı kullanır.
"""

from __future__ import annotations

import os
import sys
import threading

# Lumina'nın sıralama kipleri: "explorer" = Gezgin ile aynı; diğerleri sabit sıralardır.
EXPLORER = "explorer"
FIXED_SORTS = ("date_asc", "date_desc", "name_asc", "name_desc")
SORT_MODES = (EXPLORER, *FIXED_SORTS)
DEFAULT_FALLBACK = "date_asc"

# Gezgin'in birincil sıralama sütunu -> Lumina'nın sıralama türü. System.ItemDate Gezgin'in "Tarih" sütunudur (fotoğrafta
# çekim tarihi, yoksa değiştirilme); Lumina'nın tarih sıralaması da aynısını yapar (EXIF varsa çekim tarihi, yoksa mtime).
_NAME_PROPS = {"system.itemnamedisplay", "system.itemname", "system.filename", "system.itemnamedisplaywithoutextension"}
_DATE_PROPS = {"system.itemdate", "system.datemodified", "system.datecreated", "system.photo.datetaken"}

COM_TIMEOUT_S = 1.5


def parse_sort_columns(text: object) -> str | None:
    """"prop:-System.ItemDate;System.ItemNameDisplay;" -> "date_desc". Desteklenmeyen/boş -> None."""
    if not isinstance(text, str):
        return None
    body = text.strip()
    if body.lower().startswith("prop:"):
        body = body[5:]
    first = next((part.strip() for part in body.split(";") if part.strip()), "")
    if not first:
        return None
    descending = first.startswith("-")
    prop = first.lstrip("+-").strip().lower()
    if prop in _NAME_PROPS:
        kind = "name"
    elif prop in _DATE_PROPS:
        kind = "date"
    else:
        return None
    return f"{kind}_{'desc' if descending else 'asc'}"


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(path))).rstrip("\\/")


def _query_shell_windows(folder: str) -> str | None:
    """Klasörü gösteren Gezgin penceresinin sıralaması (çağıran iş parçacığında COM başlatılır)."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        target = _norm(folder)
        try:
            import ctypes

            foreground = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except Exception:
            foreground = 0
        found: list[tuple[bool, str]] = []
        for window in shell.Windows():
            try:
                document = window.Document
                path = document.Folder.Self.Path
                if not path or _norm(path) != target:
                    continue
                sort = parse_sort_columns(document.SortColumns)
                try:
                    is_front = foreground != 0 and int(window.HWND) == foreground
                except Exception:
                    is_front = False
                found.append((is_front, sort))
            except Exception:
                continue  # Internet Explorer penceresi, kitaplık, arama sonucu… : klasör görünümü değil
        if not found:
            return None
        # Aynı klasör birden çok pencerede açıksa öndeki pencere; yoksa ilki.
        for is_front, sort in found:
            if is_front:
                return sort
        return found[0][1]
    finally:
        pythoncom.CoUninitialize()


def explorer_sort_for_folder(folder: str, timeout: float = COM_TIMEOUT_S) -> str | None:
    """`folder` bir Gezgin penceresinde açıksa o pencerenin sırası ("date_desc" gibi); yoksa/okunamazsa None.

    Arayüz iş parçacığından ÇAĞIRMAYIN: COM sorgusu ayrı bir iş parçacığında çalışır ve en çok `timeout` sn beklenir."""
    if sys.platform != "win32" or not folder:
        return None
    result: list[str | None] = []

    def run() -> None:
        try:
            result.append(_query_shell_windows(folder))
        except Exception:
            result.append(None)

    worker = threading.Thread(target=run, name="lumina-explorer-sort", daemon=True)
    worker.start()
    worker.join(timeout)
    return result[0] if result else None


def resolve_sort(mode: str, fallback: str, folder: str, query=None) -> tuple[str, bool]:
    """Kullanılacak sabit sıralama ve Gezgin'den mi geldiği. `mode` sabit bir sıraysa olduğu gibi döner."""
    if fallback not in FIXED_SORTS:
        fallback = DEFAULT_FALLBACK
    if mode in FIXED_SORTS:
        return mode, False
    try:
        sort = (query or explorer_sort_for_folder)(folder)
    except Exception:
        sort = None
    if sort in FIXED_SORTS:
        return sort, True
    return fallback, False
