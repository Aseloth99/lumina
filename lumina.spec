# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

icon_path = Path(SPECPATH) / "app" / "resources" / "lumina.ico"
if not icon_path.is_file():
    from app.resources.brand import ensure_app_icon
    ensure_app_icon()

ICON = str(icon_path.resolve())

datas = [
    (ICON, "app/resources"),
]
binaries = []
hiddenimports = []

# Tek libmpv kopyası (kökte). mpv_backend._find_mpv_dll_dir önce _MEIPASS kökünü arar;
# ikinci kopya (vendor/mpv) hiç kullanılmıyordu ve exe'yi ~111 MB şişiriyordu.
mpv_dll = Path(SPECPATH) / "vendor" / "mpv" / "libmpv-2.dll"
if mpv_dll.is_file():
    binaries.append((str(mpv_dll.resolve()), "."))
else:
    raise SystemExit(f"HATA: libmpv bulunamadi: {mpv_dll}")

# pillow_avif yok: Pillow 12 AVIF'i kendisi çözer (PIL._avif) ve .avif önce imagecodecs ile açılır;
# eklenti exe'ye 16 MB'lık üçüncü bir AVIF çözücü ekliyordu (onefile her açılışta bunu da açıyordu).
for pkg in ("pillow_heif", "cv2", "rawpy", "resvg_py"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # paket eksikse sessizce devam etme
        print(f"UYARI: collect_all({pkg!r}) basarisiz, pakete girmeyecek: {exc}")

# resvg_py ZORUNLU: yoksa SVG'ler QtSvg yedeğiyle çizilir (clip-path yok sayılır) ve yakınlaştırınca vektörden
# yeniden çizim (bindirme, işçi süreç) hiç çalışmaz. Eskiden yalnızca yukarıdaki sessiz uyarı çıkıyordu
# ("skipping data collection for module 'resvg_py' as it is not a package" = modül bulunamadı demektir).
import importlib.util

if importlib.util.find_spec("resvg_py") is None:
    raise SystemExit("HATA: resvg_py kurulu degil (pip install resvg-py==0.5.0); SVG destegi eksik derlenmesin.")

# Sürüm bilgisi: app/core/version.py tek kaynak; exe dosya özelliklerine yazılır.
import re

_ver_text = (Path(SPECPATH) / "app" / "core" / "version.py").read_text(encoding="utf-8")
_m = re.search(r'__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"', _ver_text)
_v = tuple(int(x) for x in _m.groups()) if _m else (1, 0, 0)
_ver_str = ".".join(str(x) for x in _v)
_version_file = Path(SPECPATH) / "build" / "version_info.txt"
_version_file.parent.mkdir(parents=True, exist_ok=True)
_version_file.write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({_v[0]}, {_v[1]}, {_v[2]}, 0), prodvers=({_v[0]}, {_v[1]}, {_v[2]}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Lumina'),
      StringStruct('FileDescription', 'Lumina'),
      StringStruct('FileVersion', '{_ver_str}'),
      StringStruct('InternalName', 'Lumina'),
      StringStruct('OriginalFilename', 'Lumina.exe'),
      StringStruct('ProductName', 'Lumina'),
      StringStruct('ProductVersion', '{_ver_str}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
    encoding="utf-8",
)

hiddenimports += [
    # imagecodecs codec modulleri tembel yuklenir; PyInstaller bulamaz (JXL/AVIF exe'de bozuk kaliyordu)
    "imagecodecs",
    "imagecodecs._imagecodecs",
    "imagecodecs._shared",
    "imagecodecs._shared_cython",  # imagecodecs>=2026.8: tum codec modulleri buna bagli (yoksa JXR/JXL/AVIF exe'de cozulemez)
    "imagecodecs._jpegxl",
    "imagecodecs._avif",
    "imagecodecs._jpegxr",
    "app.core.hdr",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtSvg",
    "PyQt6.sip",
    "PIL",
    "PIL.Image",
    "PIL.ImageOps",
    "PIL.PsdImagePlugin",
    "PIL.AvifImagePlugin",
    "rawpy",
    "numpy",
    "cv2",
    "app",
    "app.main",
    "app.ui.main_window",
    "app.ui.player",
    "app.ui.canvas",
    "app.ui.details_panel",
    "app.ui.drops",
    "app.ui.filmstrip",
    "app.ui.frameless",
    "app.ui.icons",
    "app.ui.live_button",
    "app.ui.session",
    "app.ui.update_dialog",
    "app.ui.updates",
    "app.core.cache",
    "app.core.crashlog",
    "app.core.paths",
    "app.core.version",
    "app.core.updater",
    "app.core.update_installer",
    "app.core.exif",
    "app.core.folder_scan",
    "app.core.image_loader",
    "app.core.matcher",
    "app.core.motion_parser",
    "app.core.protocols",
    "app.core.registry",
    "app.core.thumb_loader",
    "app.core.svg_loader",
    "app.core.svg_region",  # bölge çizimi (saf Python): işçi süreçte tembel içe aktarılır, PyInstaller göremez
    "app.core.svg_worker",
    "app.ui.svg_overlay",
    "resvg_py",
    "app.core.uuid_parser",
    "app.core.video_io",
    "app.core.mpv_backend",
    "app.core.preview_decoder",
    "app.core.preview_store",
    "app.ui.seek_preview",
    "app.ui.chrome_widgets",
    "app.core.live_worker",
    "app.core.tracks",
    "app.core.resume_store",
    "app.core.histogram",
    "app.core.image_edit",
    "app.core.fileops",
    "app.core.edit_worker",
    "app.core.settings_store",
    "app.ui.media_actions",
    "app.ui.crop_overlay",
    "app.core.qt_safe",
    "app.core.update_policy",
    "mpv",
    "app.core.win_launcher",
    "app.core.win_shell",
    "app.resources.brand",
    "app.resources.locales",
    "app.resources.styles",
    "xml",
    "xml.etree",
    "xml.etree.ElementTree",
    "xml.parsers",
    "xml.parsers.expat",
    "plistlib",
]

a = Analysis(
    ["app/main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Qt modülleri — kullanılmıyor
        "PyQt5", "PySide2", "PySide6",
        "PyQt6.QtWebEngine", "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtWebEngineQuick", "PyQt6.QtWebChannel",
        "PyQt6.Qt3DAnimation", "PyQt6.Qt3DCore", "PyQt6.Qt3DExtras",
        "PyQt6.Qt3DInput", "PyQt6.Qt3DLogic", "PyQt6.Qt3DRender",
        "PyQt6.QtQuick", "PyQt6.QtQuick3D", "PyQt6.QtQuickWidgets", "PyQt6.QtQml",
        "PyQt6.QtBluetooth", "PyQt6.QtCharts", "PyQt6.QtDataVisualization",
        "PyQt6.QtLocation", "PyQt6.QtPositioning", "PyQt6.QtSensors",
        "PyQt6.QtSerialPort", "PyQt6.QtSql", "PyQt6.QtTest", "PyQt6.QtXml",
        "PyQt6.QtDesigner", "PyQt6.QtHelp", "PyQt6.QtNfc", "PyQt6.QtRemoteObjects",
        "PyQt6.QtTextToSpeech", "PyQt6.QtSpatialAudio", "PyQt6.QtPdf", "PyQt6.QtPdfWidgets",
        "PyQt6.QtDBus", "PyQt6.QtNetwork",
        # Bilim / makine öğrenmesi — kullanılmıyor
        "tkinter", "matplotlib", "pandas", "scipy", "torch",
        "sklearn", "tensorflow", "keras", "torchvision",
        # OpenCV'nin ağır ML modülleri — sadece cv2.VideoCapture kullanıyoruz
        "cv2.dnn", "cv2.dnn_superres", "cv2.face", "cv2.tracking",
        "cv2.ml", "cv2.aruco", "cv2.barcode", "cv2.bgsegm",
        "cv2.bioinspired", "cv2.ccalib", "cv2.datasets", "cv2.dpm",
        "cv2.fuzzy", "cv2.hfs", "cv2.img_hash", "cv2.line_descriptor",
        "cv2.mcc", "cv2.optflow", "cv2.phase_unwrapping", "cv2.plot",
        "cv2.quality", "cv2.rapid", "cv2.reg", "cv2.rgbd", "cv2.saliency",
        "cv2.sfm", "cv2.stereo", "cv2.structured_light", "cv2.superres",
        "cv2.surface_matching", "cv2.videostab", "cv2.xfeatures2d",
        "cv2.ximgproc", "cv2.xobjdetect", "cv2.xphoto",
        # Standart kütüphaneden gereksizler
        "unittest", "doctest", "pdb", "profile", "cProfile",
        "xmlrpc", "http.server",
        "ftplib", "imaplib", "smtplib", "poplib",
        "curses", "readline", "rlcompleter",
        # collect_all("rawpy") rawpy.enhance'i (kullanılmıyor) ve onunla scikit-image, imageio,
        # tifffile, lxml, bs4'ü sürüklüyordu (~20 MB); pywin/win32ui (MFC, ~7 MB) yalnızca makepy'nin
        # GUI ilerleme penceresi içindir (Shell.Application dinamik Dispatch'i bunlara ihtiyaç duymaz).
        "rawpy.enhance", "skimage", "imageio", "tifffile", "lxml", "bs4",
        "pywin", "win32ui",
        "pillow_avif",
    ],
    noarchive=False,
    optimize=2,  # optimize=1 yerine 2: docstring'leri de kaldırır
)

# Onefile exe her açılışta TÜM içeriğini %TEMP%'e açar (ölçüm: ~470 MB yazma, ~2,4 sn CPU; ilk kare
# bundan sonra gelir). Hiç yüklenmeyen dosyalar bu yüzden doğrudan açılış süresidir:
#  - opengl32sw.dll (20 MB): Qt'nin yazılımsal OpenGL'i; Lumina OpenGL kullanmaz (mpv D3D11 çizer).
#  - Qt6Pdf + qpdf görüntü eklentisi, Qt6Network + TUIO dokunma eklentisi: kullanılmıyor.
#  - Pythonwin (MFC): bkz. excludes.
#  - cv2/data (Haar sınıflandırıcıları, 10 MB): yüz algılama yok.
#  - Qt çevirileri: uygulama yalnızca tr/en; yalnızca *_tr.qm / *_en.qm tutulur.
_DROP_BINARIES = (
    "opengl32sw.dll",
    "qt6pdf.dll",
    "imageformats\\qpdf.dll",
    "qt6network.dll",
    "generic\\qtuiotouchplugin.dll",
    "pythonwin\\mfc140u.dll",
    "pythonwin\\win32ui.pyd",
)


def _norm(name):
    return name.replace("/", "\\").lower()


def _keep_binary(entry):
    return not _norm(entry[0]).endswith(_DROP_BINARIES)


def _keep_data(entry):
    name = _norm(entry[0])
    if name.startswith("cv2\\data\\"):
        return False
    if "\\qt6\\translations\\" in name and name.endswith(".qm"):
        return name.endswith(("_tr.qm", "_en.qm"))
    return True


a.binaries = [b for b in a.binaries if _keep_binary(b)]
a.datas = [d for d in a.datas if _keep_data(d)]
pyz = PYZ(a.pure)

import os

if os.environ.get("LUMINA_ONEDIR") == "1":
    # Klasör çıktısı (dist/Lumina/): hızlı açılış başlatıcısının yükü (scripts/build_fast_exe.py).
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="Lumina",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        icon=ICON,
        version=str(_version_file),
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Lumina")
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="Lumina",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,          # UPX kapalı: kurulu degil/olculmedi, antivirus yanlis-pozitif riski
        console=False,
        disable_windowed_traceback=False,
        icon=ICON,
        version=str(_version_file),
    )

