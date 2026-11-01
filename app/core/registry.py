"""Windows 'Birlikte aç' listesi ve dosya ilişkileri — HKCU, yönetici gerekmez.

İlkeler (kullanıcının sistemine saygı):
* Kayıt yalnızca kullanıcı isteğiyle (Ayarlar) yapılır; uygulama kendiliğinden hiçbir şey sormaz.
* Uzantının mevcut varsayılan ProgID'si ezilmeden önce yedeklenir; kaldırınca geri yüklenir.
* Açılışta yalnızca kayıtlı komut bayatsa (exe taşındı/güncellendi) yeniden yazılır; aksi halde
  hiçbir kayıt yazılmaz ve Explorer yenilenmez (`refresh_if_stale`).
* Dosya sağ tık menüsüne ayrı bir "Lumina ile aç" satırı EKLENMEZ (eski sürümlerin eklediği satır
  `refresh_if_stale` / kurulumda silinir); Lumina yalnızca "Birlikte aç" listesinde görünür.
* `.ts` gibi başka bir amaçla (TypeScript) yaygın uzantıların varsayılanı ezilmez; yalnızca
  "Birlikte aç" listesine eklenir.
"""

from __future__ import annotations

import sys
from pathlib import Path

SHELL_REL = r"Classes\*\shell\LuminaViewer"  # eski sürümlerin eklediği üst düzey sağ tık satırı (artık yazılmaz)
PROGID = "Lumina.file"
APP_NAME = "Lumina"
DISPLAY_NAME = "Lumina"
EXTENSIONS = (
    ".heic",
    ".heif",
    ".hif",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".tif",
    ".tiff",
    ".mov",
    ".mp4",
    ".m4v",
    ".livp",
    ".avif",
    ".bmp",
    ".gif",
    ".dng",
    ".webm",
    ".mkv",
    ".avi",
    ".heics",
    ".avifs",
    ".jxl",
    ".jp2",
    ".jxr",
    ".jfif",
    ".ts",
    ".m2ts",
    ".mts",
    ".3gp",
    ".ogv",
    ".wmv",
    ".flv",
    ".mpeg",
    ".mpg",
    ".svg",
    ".svgz",
)
# Varsayılan ProgID'si ezilmeyecek uzantılar (yalnızca "Birlikte aç" listesine eklenir):
# .ts TypeScript için, .svg/.svgz ise tarayıcıda/vektör editöründe açılması beklenen düzenlenebilir dosyalar.
# .bmp: Windows'un "Yeni > Bit Eşlem Resmi" satırı uzantının varsayılan sınıfının adını gösterir; Lumina varsayılan olunca
# o satır "Lumina" olup boş bir .bmp oluşturuyordu (Lumina açamaz). Paint varsayılan kalır, Lumina "Birlikte aç"ta.
NO_DEFAULT_OVERRIDE = frozenset({".ts", ".svg", ".svgz", ".bmp"})

APP_KEY_NAME = "Lumina.exe"
REG_APPS_REL = r"RegisteredApplications"

# Tüm HKCU yollarının kökü. Testler bunu sahte bir alt ağaca yönlendirir (gerçek ilişkilendirmelere
# dokunmadan); üretimde "Software".
_SW = "Software"


def _p(rel: str) -> str:
    return rf"{_SW}\{rel}"


def menu_text() -> str:
    """Explorer sağ tık satırının yazısı; kurulum anındaki uygulama dilinde (Windows dilinde değil)."""
    try:
        from app.resources.locales import get_active_language

        if get_active_language() != "tr":
            return f"Open with {DISPLAY_NAME}"
    except Exception:  # noqa: BLE001 — dil okunamazsa Türkçe varsayılan
        pass
    return f"{DISPLAY_NAME} ile aç"


def _command_key() -> str:
    """Kurulum işareti ve kayıtlı komut: dosya türü sınıfının `open` komutu."""
    return _p(rf"Classes\{PROGID}\shell\open\command")


def _legacy_verb_exists() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _p(SHELL_REL)):
            return True
    except OSError:
        return False


def _remove_legacy_verb() -> None:
    r"""Eski sürümün `*\shell\LuminaViewer` sağ tık satırını siler."""
    _delete_key_quiet(_p(SHELL_REL + r"\command"))
    _delete_key_quiet(_p(SHELL_REL))


def _cap_root() -> str:
    return _p("Lumina")


def _cap_key() -> str:
    return _p(r"Lumina\Capabilities")


def _prev_default_key() -> str:
    return _p(r"Lumina\PrevDefault")


def default_executable() -> str:
    from app.core.win_launcher import ensure_app_exe

    return str(ensure_app_exe())


def association_icon() -> str:
    """Birlikte aç listesinde Python ikonu çıkmasın."""
    exe = Path(default_executable())
    name = exe.name.lower()
    if name not in {"python.exe", "pythonw.exe"}:
        return f"{exe},0"
    from app.resources.brand import ensure_app_icon

    return str(ensure_app_icon())


def launcher_command(exe_path: str | None = None) -> str:
    exe = exe_path or default_executable()
    frozen = bool(getattr(sys, "frozen", False))
    name = Path(exe).name.lower()
    if frozen or name not in {"python.exe", "pythonw.exe"}:
        return f'"{exe}" "%1"'
    launcher = Path(__file__).resolve().parents[2] / "livePhotoViewer.py"
    return f'"{exe}" "{launcher}" "%1"'


def is_installed() -> bool:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _command_key()):
                return True
        except OSError:
            return False
    if sys.platform.startswith("linux"):
        desktop_file = Path.home() / ".local" / "share" / "applications" / "lumina.desktop"
        return desktop_file.is_file()
    if sys.platform == "darwin":
        # On macOS, if app is in Applications, it is installed
        exe = Path(sys.executable).resolve()
        return "Applications" in str(exe) or getattr(sys, "frozen", False)
    return False


def registered_command() -> str | None:
    """Kayıtlı 'aç' komutu (yoksa None)."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _command_key()) as key:
            value, _typ = winreg.QueryValueEx(key, None)
            return str(value)
    except OSError:
        return None


def refresh_if_stale() -> bool:
    """Kurulu ama komutu bayatlamışsa (exe taşındı/güncellendi) yeniden yaz. Değişiklik yapıldı mı?

    Güncel ise HİÇBİR kayıt yazılmaz ve Explorer yenilenmez (eskiden her açılışta 34 uzantı
    yeniden yazılıp SHCNE_ASSOCCHANGED tetikleniyordu).
    """
    if sys.platform != "win32" or not is_installed():
        return False
    if registered_command() == launcher_command():
        changed = False
        if _legacy_verb_exists():  # eski sürümden kalan "Lumina ile aç" sağ tık satırı
            _remove_legacy_verb()
            changed = True
        for ext in NO_DEFAULT_OVERRIDE:  # eski sürümün ezdiği ama artık ezilmemesi gereken varsayılanlar
            changed = _release_default(ext) or changed
        if changed:
            notify_shell()
        return changed
    install_context_menu()
    return True


def notify_shell() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_FLUSH = 0x1000
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_FLUSH, None, None)
    except (OSError, AttributeError):
        return


def _install_capabilities(icon: str) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _cap_key()) as key:
        winreg.SetValueEx(key, "ApplicationName", 0, winreg.REG_SZ, DISPLAY_NAME)
        winreg.SetValueEx(key, "ApplicationIcon", 0, winreg.REG_SZ, icon)
        winreg.SetValueEx(key, "ApplicationDescription", 0, winreg.REG_SZ, DISPLAY_NAME)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{_cap_key()}\FileAssociations") as key:
        for ext in EXTENSIONS:
            winreg.SetValueEx(key, ext, 0, winreg.REG_SZ, PROGID)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _p(REG_APPS_REL)) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _cap_key())


def _delete_key_quiet(path: str) -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def _uninstall_capabilities() -> None:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _p(REG_APPS_REL), 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, APP_NAME)
    except OSError:
        pass
    for path in (rf"{_cap_key()}\FileAssociations", _cap_key(), _prev_default_key(), _cap_root()):
        _delete_key_quiet(path)


def _read_default(path: str) -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _typ = winreg.QueryValueEx(key, None)
            return str(value)
    except OSError:
        return None


def _backup_prev_default(ext: str) -> None:
    """Uzantının mevcut varsayılan ProgID'sini (bize ait değilse) bir kez yedekle."""
    import winreg

    current = _read_default(_p(rf"Classes\{ext}"))
    if not current or current == PROGID:
        return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _prev_default_key()) as key:
        try:
            winreg.QueryValueEx(key, ext)
            return  # zaten yedeklenmiş (ilk yedek gerçek kullanıcı değeridir)
        except OSError:
            pass
        winreg.SetValueEx(key, ext, 0, winreg.REG_SZ, current)


def _release_default(ext: str) -> bool:
    """Uzantının varsayılanı bize aitse bırak: yedek varsa onu geri yükle, yoksa değeri sil (sistem varsayılanı görünür)."""
    import winreg

    if _read_default(_p(rf"Classes\{ext}")) != PROGID:
        return False
    previous = _read_value(_prev_default_key(), ext)
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _p(rf"Classes\{ext}"), 0, winreg.KEY_SET_VALUE
        ) as key:
            if previous:
                winreg.SetValueEx(key, None, 0, winreg.REG_SZ, previous)
            else:
                winreg.DeleteValue(key, "")
    except OSError:
        return False
    return True


def _purge_python_open_with(ext: str) -> None:
    import winreg

    for name in ("python.exe", "pythonw.exe"):
        _delete_key_quiet(_p(rf"Classes\{ext}\OpenWithList\{name}"))
        _delete_key_quiet(_p(rf"Classes\Applications\{name}\SupportedTypes"))
    mru_path = _p(rf"Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\OpenWithList")
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, mru_path, 0, winreg.KEY_ALL_ACCESS
        )
    except OSError:
        return
    drop = {"python.exe", "pythonw.exe"}
    with key:
        values: dict[str, str] = {}
        mru = ""
        index = 0
        while True:
            try:
                name, val, _typ = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if name == "MRUList":
                mru = str(val)
            else:
                values[name] = str(val)
        keep_letters = [
            letter
            for letter in mru
            if values.get(letter, "").lower() not in drop
        ]
        for name in list(values):
            if values[name].lower() in drop:
                try:
                    winreg.DeleteValue(key, name)
                except OSError:
                    pass
        try:
            winreg.SetValueEx(key, "MRUList", 0, winreg.REG_SZ, "".join(keep_letters))
        except OSError:
            pass


def _install_linux_desktop(exe_path: str | None = None) -> None:
    exe = exe_path or default_executable()
    launcher = Path(__file__).resolve().parents[2] / "livePhotoViewer.py"
    if bool(getattr(sys, "frozen", False)):
        exec_cmd = f'"{exe}" %F'
    else:
        exec_cmd = f'"{exe}" "{launcher}" %F'

    desktop_dir = Path.home() / ".local" / "share" / "applications"
    desktop_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = desktop_dir / "lumina.desktop"

    mimes = "image/heic;image/heif;image/jpeg;image/png;image/webp;image/avif;video/quicktime;video/mp4;"
    content = f"""[Desktop Entry]
Type=Application
Name=Lumina
Comment=Live Photo and Media Viewer
Exec={exec_cmd}
Icon=lumina
Terminal=false
Categories=Graphics;Viewer;Photography;
MimeType={mimes}
"""
    desktop_file.write_text(content, encoding="utf-8")
    import subprocess
    try:
        subprocess.run(["update-desktop-database", str(desktop_dir)], capture_output=True)
    except Exception:
        pass


def _uninstall_linux_desktop() -> None:
    desktop_file = Path.home() / ".local" / "share" / "applications" / "lumina.desktop"
    if desktop_file.is_file():
        desktop_file.unlink(missing_ok=True)
        import subprocess
        try:
            subprocess.run(["update-desktop-database", str(desktop_file.parent)], capture_output=True)
        except Exception:
            pass


def install_context_menu(exe_path: str | None = None) -> None:
    if sys.platform == "darwin":
        # macOS handles file associations via Info.plist inside the .app bundle
        return
    if sys.platform.startswith("linux"):
        _install_linux_desktop(exe_path)
        return
    if sys.platform != "win32":
        return
    import winreg

    cmd = launcher_command(exe_path)
    icon = association_icon()
    _remove_legacy_verb()  # üst düzey sağ tık satırı yok; "Birlikte aç" listesi yeterli

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _p(rf"Classes\{PROGID}")) as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, DISPLAY_NAME)
        winreg.SetValueEx(key, "FriendlyTypeName", 0, winreg.REG_SZ, DISPLAY_NAME)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _p(rf"Classes\{PROGID}\DefaultIcon")) as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, icon)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _p(rf"Classes\{PROGID}\Application")) as key:
        winreg.SetValueEx(key, "ApplicationName", 0, winreg.REG_SZ, DISPLAY_NAME)
        winreg.SetValueEx(key, "ApplicationIcon", 0, winreg.REG_SZ, icon)
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, _p(rf"Classes\{PROGID}\shell\open\command")
    ) as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, cmd)

    app_root = _p(rf"Classes\Applications\{APP_KEY_NAME}")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, app_root) as key:
        winreg.SetValueEx(key, "FriendlyAppName", 0, winreg.REG_SZ, DISPLAY_NAME)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{app_root}\DefaultIcon") as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, icon)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{app_root}\shell\open") as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, menu_text())
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{app_root}\shell\open\command") as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, cmd)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{app_root}\SupportedTypes") as key:
        for ext in EXTENSIONS:
            winreg.SetValueEx(key, ext, 0, winreg.REG_SZ, "")

    _install_capabilities(icon)
    for ext in EXTENSIONS:
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, _p(rf"Classes\{ext}\OpenWithProgids")
        ) as key:
            winreg.SetValueEx(key, PROGID, 0, winreg.REG_SZ, "")
        if ext in NO_DEFAULT_OVERRIDE:
            _release_default(ext)
        else:
            _backup_prev_default(ext)  # kullanıcının mevcut varsayılanı kaybolmasın
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _p(rf"Classes\{ext}")) as key:
                winreg.SetValueEx(key, None, 0, winreg.REG_SZ, PROGID)
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            _p(rf"Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\OpenWithProgids"),
        ) as key:
            winreg.SetValueEx(key, PROGID, 0, winreg.REG_NONE, b"")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, _p(rf"Classes\{ext}\OpenWithList\{APP_KEY_NAME}")
        ) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, "")
        _purge_python_open_with(ext)

    notify_shell()


def uninstall_context_menu() -> None:
    if sys.platform.startswith("linux"):
        _uninstall_linux_desktop()
        return
    if sys.platform != "win32":
        return
    import winreg

    exe_name = Path(default_executable()).name
    app_names = {APP_KEY_NAME, "python.exe", "pythonw.exe", exe_name}
    app_keys: list[str] = []
    for name in app_names:
        root = _p(rf"Classes\Applications\{name}")
        app_keys.extend(
            (
                rf"{root}\shell\open\command",
                rf"{root}\shell\open",
                rf"{root}\shell",
                rf"{root}\SupportedTypes",
                rf"{root}\DefaultIcon",
                root,
            )
        )
    _remove_legacy_verb()
    for key_path in (
        _p(rf"Classes\{PROGID}\shell\open\command"),
        _p(rf"Classes\{PROGID}\shell\open"),
        _p(rf"Classes\{PROGID}\shell"),
        _p(rf"Classes\{PROGID}\Application"),
        _p(rf"Classes\{PROGID}\DefaultIcon"),
        _p(rf"Classes\{PROGID}"),
        *app_keys,
    ):
        _delete_key_quiet(key_path)
    for ext in EXTENSIONS:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                _p(rf"Classes\{ext}\OpenWithProgids"),
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, PROGID)
        except OSError:
            pass
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                _p(rf"Classes\{ext}"),
                0,
                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
            ) as key:
                current, _ = winreg.QueryValueEx(key, None)
                if current == PROGID:
                    previous = _read_value(_prev_default_key(), ext)
                    # Yedek varsa kullanıcının eski varsayılanını geri yükle; yoksa değeri sil (boş değer
                    # sistem varsayılanını ezerdi).
                    if previous:
                        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, previous)
                    else:
                        winreg.DeleteValue(key, "")
        except OSError:
            pass
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                _p(rf"Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\OpenWithProgids"),
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, PROGID)
        except OSError:
            pass
        _delete_key_quiet(_p(rf"Classes\{ext}\OpenWithList\{APP_KEY_NAME}"))
    _uninstall_capabilities()
    notify_shell()


def _read_value(path: str, name: str) -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _typ = winreg.QueryValueEx(key, name)
            return str(value)
    except OSError:
        return None
