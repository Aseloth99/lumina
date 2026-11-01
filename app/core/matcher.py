"""Live Photo eşleştirme — Samsung → Windows/V2 → Apple sidecar/UUID."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.fs_case import exists_ci, existing_file, find_ci, invalidate
from app.core.motion_parser import TempVideoStore
from app.core.protocols import extract_at, extract_livp, find_protocol_offset
from app.core.uuid_parser import extract_content_identifier

IMAGE_EXTS = {
    # Apple & Next-gen HEIF
    ".heic",
    ".heif",
    ".hif",
    ".heics",
    # Modern Web & AV1
    ".avif",
    ".avifs",
    ".webp",
    ".jxl",
    ".jp2",
    # JPEG XR / HDR ekran görüntüleri (Xbox Game Bar, oyunlar)
    ".jxr",
    ".wdp",
    ".hdp",
    # Standard & Photography
    ".jpg",
    ".jpeg",
    ".jfif",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".gif",
    ".ico",
    ".tga",
    # Camera RAW (Professional DSLR / Mirrorless / Drone)
    ".dng",
    ".cr2",
    ".cr3",
    ".nef",
    ".nrw",
    ".arw",
    ".srf",
    ".sr2",
    ".raf",
    ".rw2",
    ".orf",
    ".pef",
    # Professional Design
    ".psd",
    ".psb",
    # Vector (app.core.svg_loader rasterizes it)
    ".svg",
    ".svgz",
    # Live Photo archives
    ".livp",
}
# AV1 dahil: konteyner .mp4 / .webm / .mkv (OpenCV+FFmpeg, gerekirse Qt)
VIDEO_EXTS = {
    ".mov",
    ".mp4",
    ".m4v",
    ".webm",
    ".mkv",
    ".avi",
    ".ts",
    ".m2ts",
    ".mts",
    ".3gp",
    ".ogv",
    ".wmv",
    ".flv",
    ".mpeg",
    ".mpg",
}


def _windows_name_key(name: str) -> tuple[tuple[int, object], ...]:
    """Windows Explorer'a yakın doğal ad sırası: 2, 10'dan önce gelir."""
    parts = re.split(r"(\d+)", name.casefold())
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in parts
        if part
    )


_EXIF_DATE_CACHE: dict[str, tuple[float, float]] = {}  # path_str -> (mtime, exif_ts)


def _taken_timestamp(path: Path, fallback: float) -> float:
    """Windows Fotoğraflar benzeri sıralama için EXIF çekim tarihini oku. mtime tabanlı önbellek kullanır."""
    if path.suffix.lower() not in IMAGE_EXTS:
        return fallback
    key = str(path)
    cached = _EXIF_DATE_CACHE.get(key)
    if cached is not None and cached[0] == fallback:
        return cached[1]

    result = fallback
    try:
        from PIL import Image

        with Image.open(path) as image:
            exif = image.getexif()
            raw = exif.get(36867) or exif.get(306)
            if raw:
                text = str(raw).strip().replace("T", " ")[:19]
                for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                    try:
                        result = datetime.strptime(text, fmt).timestamp()
                        break
                    except ValueError:
                        continue
    except Exception:
        pass

    if len(_EXIF_DATE_CACHE) > 5000:
        _EXIF_DATE_CACHE.clear()
    _EXIF_DATE_CACHE[key] = (fallback, result)
    return result



def list_folder_media(folder: Path, sort_by: str = "date_asc", refine_exif: bool = True) -> list[Path]:
    """Klasördeki fotoğraflar + Live sidecar olmayan videolar. resolve() yok.

    `refine_exif=False` her dosyayı açıp EXIF okumaz (yalnızca mtime): hızlı ilk liste için."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    images: list[tuple[str, Path, float]] = []
    videos: list[tuple[str, Path, float]] = []
    stems: set[str] = set()
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                ext = Path(entry.name).suffix.lower()
                path = Path(entry.path)
                try:
                    st = entry.stat(follow_symlinks=False)
                    ts = float(st.st_mtime)
                except OSError:
                    ts = 0.0

                if ext in IMAGE_EXTS:
                    images.append((entry.name, path, ts))
                    stems.add(path.stem.lower())
                elif ext in VIDEO_EXTS:
                    videos.append((entry.name, path, ts))
    except OSError:
        return []
    standalone: list[tuple[str, Path, float]] = []
    for name, video, ts in videos:
        stem = video.stem.lower()
        base = stem[: -len("_hevc")] if stem.endswith("_hevc") else stem
        if stem in stems or base in stems:
            continue
        standalone.append((name, video, ts))

    all_items = images + standalone

    # Küçük ve orta boy klasörlerde (<= 300 öğe) EXIF çekim tarihi varsa hassaslaştır
    if refine_exif and sort_by.startswith("date") and 0 < len(all_items) <= 300:
        refined: list[tuple[str, Path, float]] = []
        for name, path, ts in all_items:
            exif_ts = _taken_timestamp(path, ts)
            refined.append((name, path, exif_ts))
        all_items = refined

    if sort_by == "date_desc":
        ordered = sorted(
            all_items,
            key=lambda item: (item[2], _windows_name_key(item[0])),
            reverse=True,
        )
    elif sort_by == "name_asc":
        ordered = sorted(
            all_items,
            key=lambda item: _windows_name_key(item[0]),
        )
    elif sort_by == "name_desc":
        ordered = sorted(
            all_items,
            key=lambda item: _windows_name_key(item[0]),
            reverse=True,
        )
    else:  # Varsayılan: "date_asc" (kronolojik: eskiden yeniye)
        ordered = sorted(
            all_items,
            key=lambda item: (item[2], _windows_name_key(item[0])),
        )

    return [path for _name, path, _ts in ordered]


def sidecar_badge_names(paths: list[Path]) -> set[str]:
    """Verilen fotoğraflardan, aynı adlı Live sidecar videosu olanların adları (küçük harf).

    paired_sidecars ile aynı aday adlar; ama klasör başına tek dizin okumasıyla (dosya başına
    çok sayıda stat yerine), arka plan iş parçacığında kullanılmak üzere.
    """
    listings: dict[str, set[str]] = {}
    live: set[str] = set()
    for path in paths:
        folder = os.fspath(path.parent)
        names = listings.get(folder)
        if names is None:
            try:
                with os.scandir(folder) as entries:
                    names = {entry.name.lower() for entry in entries}
            except OSError:
                names = set()
            listings[folder] = names
        for cand, _source in _sidecar_candidates(path):
            if cand.name.lower() in names:
                live.add(path.name.lower())
                break
    return live


def index_in_folder(files: list[Path], target: Path) -> int:
    name = target.name.lower()
    for i, path in enumerate(files):
        if path.name.lower() == name:
            return i
    return 0


def index_after_change(old_files: list[Path], old_idx: int, new_files: list[Path]) -> int:
    """Liste değişince (izleyici yeniden taraması, uygulama içi silme) geçerli konumu belirle.

    Gösterilen dosya hâlâ varsa adına göre izlenir. Silindiyse konum korunur: eski listede
    ondan sonra gelen ilk sağ kalan dosya (yani aynı sıraya kayan "sonraki"), yoksa önceki
    sağ kalan dosya seçilir. Liste boşsa -1.
    """
    if not new_files:
        return -1
    if not (0 <= old_idx < len(old_files)):
        return 0
    where = {path.name.lower(): i for i, path in enumerate(new_files)}
    for path in old_files[old_idx:]:
        hit = where.get(path.name.lower())
        if hit is not None:
            return hit
    for path in reversed(old_files[:old_idx]):
        hit = where.get(path.name.lower())
        if hit is not None:
            return hit
    return min(old_idx, len(new_files) - 1)


@dataclass(frozen=True, slots=True)
class LiveMatch:
    video_path: str
    is_temp: bool
    source: str


def _sidecar_candidates(image_path: Path) -> list[tuple[Path, str]]:
    stem = image_path.stem
    folder = image_path.parent
    names = (
        (f"{stem}.MOV", "filename"),
        (f"{stem}.mov", "filename"),
        (f"{stem}.MP4", "filename"),
        (f"{stem}.mp4", "filename"),
        (f"{stem}.M4V", "filename"),
        (f"{stem}_HEVC.MOV", "hevc"),
        (f"{stem}_HEVC.mov", "hevc"),
        (f"{stem}_HEVC.MP4", "hevc"),
        (f"{stem}_HEVC.mp4", "hevc"),
    )
    seen: set[str] = set()
    out: list[tuple[Path, str]] = []
    for name, source in names:
        candidate = folder / name
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((candidate, source))
    return out


def paired_sidecars(image_path: Path) -> list[Path]:
    """Silmede birlikte gidecek Live sidecar videoları."""
    image_path = Path(image_path)
    found: list[Path] = []
    seen: set[str] = set()
    for candidate, _source in _sidecar_candidates(image_path):
        try:
            actual = existing_file(candidate)
            if actual is None:
                continue
            key = str(actual.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(actual)
        except OSError:
            continue
    return found


def still_for_video(video_path: Path) -> Path | None:
    """MOV/MP4 açılınca eş HEIC/JPEG varsa onu göster."""
    video_path = Path(video_path)
    stem = video_path.stem
    if stem.upper().endswith("_HEVC"):
        stem = stem[: -len("_HEVC")]
    folder = video_path.parent
    for ext in (".heic", ".jpg", ".jpeg", ".heif", ".png"):
        found = existing_file(folder / f"{stem}{ext}")
        if found is not None:
            return found
    return None


def resolve_live_video(
    image_path: Path,
    uuid_map: dict[str, Path] | None = None,
    store: TempVideoStore | None = None,
) -> LiveMatch | None:
    """
    1. .livp ZIP
    2. Samsung SEFT / MotionPhoto_Data
    3. Windows Motion Photo V2 (Container Length)
    4. HEIC mpvd / Google V1 / kuyruk ftyp
    5. stem.MOV / stem_HEVC.MOV
    6. ContentIdentifier UUID
    """
    image_path = Path(image_path)
    if not image_path.is_file():
        return None

    if image_path.suffix.lower() == ".livp":
        extracted = extract_livp(image_path, store)
        if extracted:
            _still, video = extracted
            return LiveMatch(video, True, "livp")

    hit = find_protocol_offset(image_path)
    if hit is not None:
        offset, source = hit
        extracted = extract_at(image_path, offset, store)
        if extracted:
            return LiveMatch(extracted, True, source)

    for candidate, source in _sidecar_candidates(image_path):
        # Linux'ta `IMG_1.HEIC` + `IMG_1.mov` farklı harf büyüklüğüyle gelebilir: harf-duyarsız ara
        found = existing_file(candidate)
        if found is not None and found.resolve() != image_path.resolve():
            return LiveMatch(str(found), False, source)

    if uuid_map:
        photo_id = extract_content_identifier(image_path)
        if photo_id:
            mapped = uuid_map.get(photo_id)
            if mapped and Path(mapped).is_file():
                return LiveMatch(str(mapped), False, "uuid")

    return None


def merge_live_photos_by_id(
    folder: Path | str,
    progress_callback: object | None = None,
) -> dict:
    """
    Aynı klasördeki fotoğraf ve video dosyalarını Apple/Google ContentIdentifier
    GUID kimlikleri üzerinden eşleştirir. Farklı ada sahip olan video yan dosyalarını
    fotoğrafın adıyla (örn. IMG_9999.MOV -> IMG_1234.MOV) güvenle yeniden adlandırır.

    Güvenlik kuralları:
    - Sadece aynı klasördeki dosyalar eşleştirilir.
    - Sadece ContentIdentifier GUID kimlikleri BİREBİR AYNI olan çiftler işlenir.
    - Zaten aynı ada sahip olan veya hedef adı dolu olan dosyaların üzerine ASLA yazılmaz.
    """
    folder = Path(folder)
    res: dict = {
        "success": True,
        "total_scanned": 0,
        "total_images": 0,
        "total_videos": 0,
        "id_images": 0,
        "id_videos": 0,
        "already_paired": 0,
        "merged_count": 0,
        "merged_pairs": [],
        "errors": [],
    }
    if not folder.is_dir():
        res["success"] = False
        res["errors"].append(f"Klasör bulunamadı: {folder}")
        return res

    image_paths: list[Path] = []
    video_paths: list[Path] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                ext = Path(entry.name).suffix.lower()
                p = Path(entry.path)
                if ext in IMAGE_EXTS:
                    image_paths.append(p)
                elif ext in VIDEO_EXTS:
                    video_paths.append(p)
    except Exception as e:
        res["success"] = False
        res["errors"].append(str(e))
        return res

    res["total_images"] = len(image_paths)
    res["total_videos"] = len(video_paths)
    total_files = len(image_paths) + len(video_paths)
    res["total_scanned"] = total_files

    processed = 0

    images_by_guid: dict[str, list[Path]] = {}
    for img in image_paths:
        processed += 1
        if callable(progress_callback):
            progress_callback(processed, total_files, img.name)
        cid = extract_content_identifier(img)
        if cid:
            res["id_images"] += 1
            images_by_guid.setdefault(cid.lower(), []).append(img)

    videos_by_guid: dict[str, list[Path]] = {}
    for vid in video_paths:
        processed += 1
        if callable(progress_callback):
            progress_callback(processed, total_files, vid.name)
        cid = extract_content_identifier(vid)
        if cid:
            res["id_videos"] += 1
            videos_by_guid.setdefault(cid.lower(), []).append(vid)

    for guid, imgs in images_by_guid.items():
        vids = videos_by_guid.get(guid)
        if not vids:
            continue

        paired_vids: set[Path] = set()
        for img in imgs:
            stem = img.stem.lower()
            for vid in vids:
                v_stem = vid.stem.lower()
                if v_stem == stem or v_stem == f"{stem}_hevc":
                    paired_vids.add(vid)
                    res["already_paired"] += 1

        unpaired_vids = [v for v in vids if v not in paired_vids]
        if not unpaired_vids:
            continue

        for img in imgs:
            if not unpaired_vids:
                break
            stem = img.stem.lower()
            if any(v.stem.lower() in (stem, f"{stem}_hevc") for v in paired_vids):
                continue

            vid = unpaired_vids.pop(0)
            target_ext = vid.suffix  # videonun kendi uzantı harf büyüklüğü korunur (eskiden küçültülüyordu)
            target_name = f"{img.stem}{target_ext}"
            target_path = folder / target_name

            existing = find_ci(folder, target_name)  # harf farkıyla bile dolu mu?
            if existing is not None:
                if existing.resolve() == vid.resolve():
                    res["already_paired"] += 1
                    continue
                alt_name = f"{img.stem}_HEVC{target_ext}"
                alt_path = folder / alt_name
                if exists_ci(folder, alt_name):
                    res["errors"].append(
                        f"{vid.name} -> '{target_name}' ve '{alt_name}' zaten mevcut."
                    )
                    continue
                target_name = alt_name
                target_path = alt_path

            try:
                vid.rename(target_path)
                invalidate(folder)
                res["merged_count"] += 1
                res["merged_pairs"].append({
                    "image": img.name,
                    "old_video": vid.name,
                    "new_video": target_name,
                    "guid": guid,
                })
                paired_vids.add(target_path)
            except Exception as e:
                res["errors"].append(
                    f"{vid.name} -> {target_name} adlandırma hatası: {e}"
                )

    return res

