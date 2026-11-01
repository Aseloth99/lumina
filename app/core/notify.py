"""Ortak masaüstü bildirimi (dört PyQt6 uygulamasında aynı dosya): Notifier + eski `show_toast` uyumluluğu.

Tek yol Qt'nin çapraz platform `QSystemTrayIcon.showMessage` çağrısıdır (Windows 10/11'de toast, macOS ve Linux'ta
yerel bildirim); alt süreç/PowerShell/winotify yok. Tepsi yoksa ya da mesaj desteklemiyorsa:
  * macOS : `osascript -e 'display notification …'` (metin AppleScript için kaçırılır),
  * Linux : `notify-send` (yüklüyse),
  * Windows: ASLA alt süreç başlatılmaz (yalnız tepsi),
  * hiçbiri olmazsa uygulamanın verdiği `inapp_fallback(title, body)` (uygulama içi toast) çağrılır.
Bildirim AYARI (show_notifications vb.) burada değil çağıranda/`enabled_provider`'dadır. Hiçbir yol istisna fırlatmaz.
GUI iş parçacığından çağrılmalıdır (QSystemTrayIcon kuralı).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from typing import Callable

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QSystemTrayIcon

_DEVNULL = subprocess.DEVNULL


def applescript_quote(text) -> str:
    """AppleScript çift tırnaklı dizge içine güvenle konacak biçim: `\\` ve `"` kaçırılır, satır sonu/denetim
    karakterleri boşluğa çevrilir (aksi halde `" & do shell script "…` gibi bir metin betiğe karışabilirdi)."""
    cleaned = "".join(" " if (ord(ch) < 32 or ord(ch) == 0x7F) else ch for ch in str(text))
    return cleaned.replace("\\", "\\\\").replace('"', '\\"')


def build_macos_command(title: str, body: str) -> list[str]:
    script = f'display notification "{applescript_quote(body)}" with title "{applescript_quote(title)}"'
    return ["osascript", "-e", script]


def build_linux_command(app_name: str, title: str, body: str, *, critical: bool = False, timeout_ms: int = 5000,
                        icon: str = "", notify_send: str = "notify-send") -> list[str]:
    cmd = [notify_send, "-a", str(app_name), "-t", str(int(timeout_ms)), "-i",
           icon or ("dialog-error" if critical else "dialog-information")]
    if critical:
        cmd += ["-u", "critical"]
    # "--": '-' ile başlayan başlık/ileti seçenek sanılmasın
    return cmd + ["--", str(title), str(body)]


def _default_runner(cmd: list[str], **kwargs):
    return subprocess.run(cmd, **kwargs)


class Notifier:
    """Uygulama başına bir örnek. Sağlayıcılar çağrı anında okunur (tepsi/ayar sonradan değişebilir).

    tray_provider() -> QSystemTrayIcon | None; icon_provider() -> QIcon | dosya yolu | None;
    enabled_provider() -> bool; inapp_fallback(title, body) -> uygulama içi toast (isteğe bağlı).
    Testler için: platform ("win32" | "darwin" | "linux"), runner(cmd, **kw) (verilirse eşzamanlı çağrılır; varsayılan
    arka plan iş parçacığında subprocess.run), which, supports_messages().
    """

    def __init__(
        self,
        app_name: str,
        tray_provider: Callable[[], object] = lambda: None,
        icon_provider: Callable[[], object] = lambda: None,
        enabled_provider: Callable[[], bool] = lambda: True,
        inapp_fallback: Callable[[str, str], object] | None = None,
        *,
        platform: str | None = None,
        runner: Callable | None = None,
        which: Callable[[str], str | None] | None = None,
        supports_messages: Callable[[], bool] | None = None,
    ):
        self.app_name = app_name
        self._tray_provider = tray_provider
        self._icon_provider = icon_provider
        self._enabled_provider = enabled_provider
        self._inapp_fallback = inapp_fallback
        self._platform = platform or sys.platform
        self._runner = runner
        self._which = which or shutil.which
        self._supports_messages = supports_messages or QSystemTrayIcon.supportsMessages

    # -- genel giriş -----------------------------------------------------------------------------------------
    def notify(self, title: str, body: str, *, critical: bool = False, timeout_ms: int = 5000) -> bool:
        """Bildirim gösterildiyse (tepsi, sistem aracı ya da uygulama içi yedek) True; etkin değilse/başarısızsa False."""
        try:
            if not self._enabled():
                return False
            title, body = str(title), str(body)
            timeout_ms = max(1000, int(timeout_ms))
            if self._via_tray(title, body, critical, timeout_ms):
                return True
            if self._via_cli(title, body, critical, timeout_ms):
                return True
            return self._via_inapp(title, body)
        except Exception:
            return False

    # -- yollar ----------------------------------------------------------------------------------------------
    def _enabled(self) -> bool:
        try:
            return bool(self._enabled_provider())
        except Exception:
            return False  # ayar okunamıyorsa kullanıcıyı rahatsız etme

    def _provided(self, provider):
        try:
            return provider()
        except Exception:
            return None

    def _via_tray(self, title: str, body: str, critical: bool, timeout_ms: int) -> bool:
        tray = self._provided(self._tray_provider)
        if tray is None:
            return False
        try:
            is_visible = getattr(tray, "isVisible", None)
            if callable(is_visible) and not is_visible():
                return False
            show = getattr(tray, "showMessage", None)
            if callable(show):
                if not self._supports_messages():
                    return False
                icon = self._provided(self._icon_provider)
                if critical:
                    shown_icon = QSystemTrayIcon.MessageIcon.Critical
                elif isinstance(icon, QIcon) and not icon.isNull():
                    shown_icon = icon
                else:
                    shown_icon = QSystemTrayIcon.MessageIcon.Information
                show(title, body, shown_icon, timeout_ms)
                return True
            for name in ("notify", "show_message"):  # showMessage'ı olmayan sarmalayıcılar (eski uygulama tepsileri)
                fn = getattr(tray, name, None)
                if callable(fn):
                    return fn(title, body) is not False
        except Exception:
            return False
        return False

    def _via_cli(self, title: str, body: str, critical: bool, timeout_ms: int) -> bool:
        platform = self._platform
        if platform == "darwin":
            cmd = build_macos_command(title, body)
        elif platform.startswith("linux"):
            exe = self._which("notify-send")
            if not exe:
                return False
            icon = self._provided(self._icon_provider)
            cmd = build_linux_command(self.app_name, title, body, critical=critical, timeout_ms=timeout_ms,
                                      icon=icon if isinstance(icon, str) else "", notify_send=exe)
        else:
            return False  # Windows (ve bilinmeyen platformlar): alt süreç ASLA başlatılmaz
        return self._run(cmd)

    def _run(self, cmd: list[str]) -> bool:
        kwargs = {"stdin": _DEVNULL, "stdout": _DEVNULL, "stderr": _DEVNULL, "timeout": 10, "check": False}
        if self._runner is not None:
            try:
                result = self._runner(cmd, **kwargs)
            except Exception:
                return False
            return getattr(result, "returncode", 0) in (0, None)

        def work() -> None:
            try:
                subprocess.run(cmd, **kwargs)
            except Exception:
                pass

        try:  # arayüz iş parçacığını bekletmesin, zombi süreç bırakmasın
            threading.Thread(target=work, name="notify", daemon=True).start()
            return True
        except Exception:
            return False

    def _via_inapp(self, title: str, body: str) -> bool:
        if self._inapp_fallback is None:
            return False
        try:
            self._inapp_fallback(title, body)
            return True
        except Exception:
            return False


def show_toast(title: str, message: str, app_id: str = "MediaCompressor", icon_path: str = "", tray=None,
               platform: str | None = None, which=None, runner=None) -> bool:
    """Eski `show_toast` imzası (MediaCompressor ve testleri için): ince bir Notifier sarmalayıcısı.
    Gösterildiyse True; hiçbir koşulda istisna fırlatmaz."""
    try:
        notifier = Notifier(
            app_id,
            tray_provider=lambda: tray,
            icon_provider=lambda: icon_path or None,
            platform=platform,
            runner=runner,
            which=which,
        )
        return notifier.notify(title, message, timeout_ms=4000)
    except Exception:
        return False
