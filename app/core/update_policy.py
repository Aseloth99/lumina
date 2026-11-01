"""Güncelleme güvenlik politikası — Qt'siz, saf ve test edilebilir.

* İndirme adresi yalnızca HTTPS ve güvenilen alan adı (macik.com.tr ve alt alanları).
  Geliştirme sırasında (paketlenmemiş çalışmada) localhost'a izin verilir.
* SHA-256 sağlama toplamı 64 haneli onaltılık olmalı; bozuk biçim reddedilir.
* İndirilen dosyanın adı sunucudan GELMEZ (sabit `%TEMP%\\Lumina_Update_<pid>.pkg`); paket türü uzantıdan değil
  içerikten anlaşılır (bkz. `self_update.sniff_package`).
"""

from __future__ import annotations

import sys
import urllib.parse

TRUSTED_DOMAIN = "macik.com.tr"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
MAX_UPDATE_BYTES = 600 * 1024 * 1024


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def supports_auto_install(platform: str | None = None) -> bool:
    """Yerinde otomatik kurulum yalnızca Windows'ta (çalışan exe yeniden adlandırılır). macOS/Linux: indirme sayfası açılır."""
    return (platform if platform is not None else sys.platform) == "win32"


def is_local_dev_host(host: str | None) -> bool:
    """localhost yalnızca geliştirmede (paketlenmemiş) güvenilir sayılır."""
    return (host or "").lower() in LOCAL_HOSTS and not is_frozen()


def _is_trusted_remote(host: str) -> bool:
    host = host.lower().rstrip(".")
    return host == TRUSTED_DOMAIN or host.endswith("." + TRUSTED_DOMAIN)


def validate_update_url(url: str) -> tuple[bool, str]:
    """(geçerli mi, neden). Gerçek indirme ve yönlendirme sonrası adres için kullanılır.

    Neden bir KOD'dur (arayüz `update_err_<kod>` metnini kendi dilinde üretir); ek bilgi `kod:bilgi` biçiminde gelir
    (ör. `url_untrusted:evil.example`). Geçerliyse neden boştur."""
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
        host = parsed.hostname or ""
        scheme = (parsed.scheme or "").lower()
        port = parsed.port
    except ValueError:
        return False, "url_invalid"
    if not host:
        return False, "url_invalid"
    if parsed.username or parsed.password:
        return False, "url_credentials"
    if is_local_dev_host(host):
        return (scheme in {"http", "https"}, "url_invalid")
    if scheme != "https":
        return False, "url_insecure"
    if port not in (None, 443):
        return False, "url_port"
    if not _is_trusted_remote(host):
        return False, f"url_untrusted:{host}"
    return True, ""


def normalize_checksum(value) -> str | None:
    """Boş -> None (doğrulama yok). Dolu ama geçersiz biçim -> ValueError (reddet)."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text.startswith("sha256:"):
        text = text[len("sha256:"):].strip()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError("Sunucunun verdiği sağlama toplamı geçersiz biçimde.")
    return text
