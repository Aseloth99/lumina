"""EXIF / dosya ayrıntıları — pikselleri decode etmeden başlık okumaya çalışır."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ExifTags

from app.core.image_loader import HEIF_EXTS
from app.core.matcher import VIDEO_EXTS
from app.core.svg_loader import SVG_EXTS
from app.core.svg_loader import native_size as svg_native_size

_TAG = {v: k for k, v in ExifTags.TAGS.items()}

SECTION_GENERAL = (
    "file",
    "size",
    "type",
    "created",
    "modified",
    "folder",
)
SECTION_VIDEO = (
    "pixels",
    "duration",
    "fps",
    "video_bitrate",
    "total_bitrate",
    "video_codec",
    "motion",
    "protocol",
)
SECTION_AUDIO = (
    "audio_bitrate",
    "audio_channels",
    "audio_samplerate",
    "audio_codec",
)
SECTION_CAMERA = (
    "camera",
    "lens",
    "taken",
    "aperture",
    "exposure",
    "iso",
    "focal",
    "flash",
    "color_space",
)
SECTION_LOCATION = (
    "gps",
)


def _ratio(value: Any) -> float | None:
    try:
        if hasattr(value, "numerator"):
            den = value.denominator or 1
            return float(value.numerator) / float(den)
        if isinstance(value, tuple) and len(value) == 2:
            return float(value[0]) / float(value[1] or 1)
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _gps_to_deg(values: Any, ref: str | None) -> float | None:
    try:
        d = _ratio(values[0])
        m = _ratio(values[1])
        s = _ratio(values[2])
        if d is None or m is None or s is None:
            return None
        deg = d + m / 60.0 + s / 3600.0
        if ref in {"S", "W"}:
            deg = -deg
        return deg
    except (TypeError, IndexError, ValueError):
        return None


def _fmt_ts(stamp: float) -> str:
    try:
        return datetime.fromtimestamp(stamp).strftime("%d.%m.%Y %H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


def _fill_file_times(out: dict[str, str], path: Path) -> None:
    try:
        stat = path.stat()
    except OSError:
        return
    created = _fmt_ts(getattr(stat, "st_ctime", stat.st_mtime))
    modified = _fmt_ts(stat.st_mtime)
    if created:
        out["created"] = created
    if modified:
        out["modified"] = modified


def _quicktime_taken(path: Path) -> str | None:
    key = b"com.apple.quicktime.creationdate"
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            blob = fh.read(min(size, 512 * 1024))
            if size > 768 * 1024:
                fh.seek(max(0, size - 256 * 1024))
                blob += fh.read()
    except OSError:
        return None
    idx = blob.find(key)
    if idx < 0:
        return None
    chunk = blob[idx + len(key) : idx + len(key) + 96]
    text = "".join(chr(b) for b in chunk if 32 <= b < 127)
    for token in text.replace("T", " ").split():
        if len(token) >= 19 and token[4] == "-" and token[7] == "-":
            raw = token[:19]
            try:
                parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
                return parsed.strftime("%d.%m.%Y %H:%M")
            except ValueError:
                return token[:19]
    return None


def _fill_video(out: dict[str, str], path: Path) -> None:
    taken = _quicktime_taken(path)
    if taken:
        out["taken"] = taken

    # 1. Windows Property System (Shell.Application COM)
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            shell = win32com.client.Dispatch("Shell.Application")
            folder = shell.NameSpace(str(path.parent))
            if folder:
                item = folder.ParseName(path.name)
                if item:
                    def _clean_prop(idx: int) -> str:
                        try:
                            val = folder.GetDetailsOf(item, idx)
                            if not val:
                                return ""
                            return str(val).replace("\u200e", "").replace("\u200f", "").strip()
                        except Exception:
                            return ""

                    # 27: Uzunluk / Duration (00:04:27)
                    duration = _clean_prop(27)
                    if duration:
                        out["duration"] = duration

                    # 316: Genişlik, 314: Yükseklik
                    width_s = _clean_prop(316)
                    height_s = _clean_prop(314)
                    if width_s and height_s:
                        out["pixels"] = f"{width_s}×{height_s}"

                    # 315: Resim karesi hızı / FPS
                    fps_s = _clean_prop(315)
                    if fps_s:
                        out["fps"] = fps_s

                    # 313: Veri hızı / Video Bitrate
                    v_bitrate = _clean_prop(313)
                    if v_bitrate:
                        out["video_bitrate"] = v_bitrate

                    # 320: Toplam bit hızı
                    tot_bitrate = _clean_prop(320)
                    if tot_bitrate:
                        out["total_bitrate"] = tot_bitrate

                    # 28: Bit hızı / Ses Bitrate
                    a_bitrate = _clean_prop(28)
                    if a_bitrate:
                        out["audio_bitrate"] = a_bitrate
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        pass

    # 2. OpenCV Fallback
    if "pixels" not in out or "fps" not in out or "duration" not in out:
        try:
            if path.stat().st_size < 4096:
                return
            from app.core.video_io import open_capture
            import cv2

            cap, rot = open_capture(str(path))
            if cap is not None:
                try:
                    if "pixels" not in out:
                        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                        if rot in {90, 270}:
                            width, height = height, width
                        if width > 0 and height > 0:
                            out["pixels"] = f"{width}×{height}"

                    fps_val = cap.get(cv2.CAP_PROP_FPS) or 0.0
                    if "fps" not in out and fps_val > 0:
                        out["fps"] = f"{fps_val:.2f} fps"

                    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                    if "duration" not in out and fps_val > 0 and frame_count > 0:
                        total_sec = int(frame_count / fps_val)
                        mins, secs = divmod(total_sec, 60)
                        hrs, mins = divmod(mins, 60)
                        if hrs > 0:
                            out["duration"] = f"{hrs:02d}:{mins:02d}:{secs:02d}"
                        else:
                            out["duration"] = f"{mins:02d}:{secs:02d}"
                finally:
                    cap.release()
        except Exception:
            pass


def _human_size(nbytes: int) -> str:
    """1 MB üstü eskisi gibi ("2.35 MB"); küçük dosyalar (simge, SVG) "0.00 MB" yerine KB/bayt gösterir."""
    if nbytes >= 1024 * 1024:
        return f"{nbytes / (1024 * 1024):.2f} MB"
    if nbytes >= 1024:
        return f"{nbytes / 1024:.1f} KB"
    return f"{nbytes} B"


def collect_details(path: Path) -> dict[str, str]:
    path = Path(path)
    out: dict[str, str] = {
        "file": path.name,
        "folder": str(path.parent),
        "type": path.suffix.lower().lstrip(".") or "—",
    }
    try:
        out["size"] = _human_size(path.stat().st_size)
    except OSError:
        out["size"] = "—"
    _fill_file_times(out, path)

    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTS:
        _fill_video(out, path)
        return out

    if suffix in SVG_EXTS:
        native = svg_native_size(path)
        if native:
            out["pixels"] = f"{native[0]}×{native[1]}"
        return out

    try:
        with Image.open(path) as img:
            out["pixels"] = f"{img.width}×{img.height}"
            exif = None
            try:
                exif = img.getexif()
            except Exception:
                exif = None
            if exif:
                _fill_exif(out, exif)
            if suffix in HEIF_EXTS:
                _fill_heif_info(out, img)
    except Exception:
        pass
    return out


def _fill_heif_info(out: dict[str, str], img: Image.Image) -> None:
    info = getattr(img, "info", {}) or {}
    for key in ("make", "model"):
        if key in info and "camera" not in out:
            out["camera"] = str(info[key])
    raw = info.get("exif")
    if raw and "taken" not in out:
        try:
            from PIL.TiffImagePlugin import ImageFileDirectory_v2

            if isinstance(raw, (bytes, bytearray)) and raw[:2] in {b"MM", b"II"}:
                ifd = ImageFileDirectory_v2(raw)
                dt = ifd.get(36867) or ifd.get(306)
                if dt:
                    _set_taken(out, str(dt))
        except Exception:
            pass


def _set_taken(out: dict[str, str], text: str) -> None:
    text = text.strip().replace("T", " ")
    raw = text[:19]
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(raw, fmt)
            out["taken"] = parsed.strftime("%d.%m.%Y %H:%M")
            return
        except ValueError:
            continue
    if text:
        out["taken"] = text


def _fill_exif(out: dict[str, str], exif) -> None:
    make = exif.get(_TAG.get("Make", 271))
    model = exif.get(_TAG.get("Model", 272))
    parts = [str(p).strip() for p in (make, model) if p]
    if parts:
        out["camera"] = " ".join(parts)
    dt = exif.get(_TAG.get("DateTimeOriginal", 36867)) or exif.get(_TAG.get("DateTime", 306))
    if dt:
        _set_taken(out, str(dt))

    sub_ifd = {}
    try:
        sub_ifd = exif.get_ifd(0x8769) or {}
    except Exception:
        sub_ifd = {}

    def _lookup(tag_name: str, tag_id: int):
        t_id = _TAG.get(tag_name, tag_id)
        val = exif.get(t_id)
        if val is None and sub_ifd:
            val = sub_ifd.get(t_id)
        return val

    iso = _lookup("ISOSpeedRatings", 34855)
    if iso:
        out["iso"] = str(iso if not isinstance(iso, (tuple, list)) else iso[0])

    fnum = _ratio(_lookup("FNumber", 33437))
    if fnum:
        out["aperture"] = f"f/{fnum:.1f}"

    fl = _ratio(_lookup("FocalLength", 37386))
    if fl:
        out["focal"] = f"{fl:.0f} mm"

    exp = _ratio(_lookup("ExposureTime", 33434))
    if exp:
        if 0.0 < exp < 1.0:
            denom = int(round(1.0 / exp))
            out["exposure"] = f"1/{denom} sn"
        else:
            out["exposure"] = f"{exp:.1f} sn"

    lens = _lookup("LensModel", 42036)
    if lens:
        lens_str = str(lens).strip().replace("\x00", "")
        if lens_str:
            out["lens"] = lens_str

    flash = _lookup("Flash", 37385)
    if flash is not None:
        try:
            f_val = int(flash)
            out["flash"] = "Açık (Çaktı)" if (f_val & 1) else "Kapalı (Çakmadı)"
        except Exception:
            pass

    cs = _lookup("ColorSpace", 40961)
    if cs is not None:
        try:
            cs_val = int(cs)
            if cs_val == 1:
                out["color_space"] = "sRGB"
            elif cs_val in (2, 65535):
                out["color_space"] = "Adobe RGB / Geniş Renk"
        except Exception:
            pass

    gps_ifd = None
    try:
        gps_ifd = exif.get_ifd(0x8825)
    except Exception:
        gps_ifd = None
    if gps_ifd:
        lat = _gps_to_deg(gps_ifd.get(2), gps_ifd.get(1))
        lon = _gps_to_deg(gps_ifd.get(4), gps_ifd.get(3))
        if lat is not None and lon is not None:
            out["gps"] = f"{lat:.5f}, {lon:.5f}"
