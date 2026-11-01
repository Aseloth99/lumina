"""macik.com.tr: uzaktan duyuru satırı.

Duyuru, güncelleme denetimiyle AYNI istekte gelir (ayrı bir bağlantı yok):
  GET https://macik.com.tr/api/updates/check?appName=..&currentVersion=..
  yanıtta isteğe bağlı  "notice": {...}   <- yoksa hiçbir şey gösterilmez

Bu dosya Qt'ye bağlı değildir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

SITE_URL = "https://macik.com.tr"
TRUSTED_HOSTS = ("macik.com.tr",)
MAX_TEXT = 240
MAX_ID = 64
_CONTROL = re.compile(r"[\x00-\x1f\x7f‪-‮⁦-⁩]")  # denetim + çift yönlü yazı karakterleri


@dataclass(frozen=True)
class Notice:
    id: str
    text: str
    url: str = ""
    level: str = "info"  # "info" | "warn"


def is_trusted_url(raw: Any) -> bool:
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        parsed = urlparse(raw.strip())
        host = (parsed.hostname or "").lower()
        port_ok = parsed.port in (None, 443)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password or not port_ok:
        return False
    return any(host == h or host.endswith("." + h) for h in TRUSTED_HOSTS)


def _clean(text: Any, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    return _CONTROL.sub(" ", text).strip()[:limit]


def _pick(text: Any, lang: str) -> str:
    if isinstance(text, dict):
        for key in (lang, "tr", "en"):
            if key in text and _clean(text[key], MAX_TEXT):
                return _clean(text[key], MAX_TEXT)
        return ""
    return _clean(text, MAX_TEXT)


def parse_notice(data: Any, lang: str, now: Optional[datetime] = None) -> Optional[Notice]:
    """Yanıttaki `notice` alanını doğrular. Geçersiz/süresi dolmuş/boş ise None (hiçbir şey gösterilmez).

    "notice": {"id": "2026-10-a", "text": {"tr": "...", "en": "..."}  (veya düz metin),
               "url": "https://macik.com.tr/...", "level": "info"|"warn", "expires": "2026-12-31T00:00:00Z"}
    """
    raw = data.get("notice") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return None
    notice_id = _clean(raw.get("id"), MAX_ID)
    text = _pick(raw.get("text"), lang)
    if not notice_id or not text:
        return None
    expires = raw.get("expires")
    if isinstance(expires, str) and expires.strip():
        try:
            stamp = datetime.fromisoformat(expires.strip().replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if stamp <= (now or datetime.now(timezone.utc)):
                return None
        except ValueError:
            return None
    url = raw.get("url")
    return Notice(
        id=notice_id,
        text=text,
        url=url.strip() if is_trusted_url(url) else "",
        level="warn" if raw.get("level") == "warn" else "info",
    )
