"""Ses / altyazı parçası etiketleri (mpv `track-list`) — Qt yok, test edilebilir."""

from __future__ import annotations

# ISO 639-2/3 (mpv'nin verdiği 3 harfli kodlar) ve 2 harfli kodlar -> (Türkçe, İngilizce) ad.
_LANGS: dict[str, tuple[str, str]] = {
    "tur": ("Türkçe", "Turkish"), "tr": ("Türkçe", "Turkish"),
    "eng": ("İngilizce", "English"), "en": ("İngilizce", "English"),
    "ger": ("Almanca", "German"), "deu": ("Almanca", "German"), "de": ("Almanca", "German"),
    "fre": ("Fransızca", "French"), "fra": ("Fransızca", "French"), "fr": ("Fransızca", "French"),
    "spa": ("İspanyolca", "Spanish"), "es": ("İspanyolca", "Spanish"),
    "ita": ("İtalyanca", "Italian"), "it": ("İtalyanca", "Italian"),
    "por": ("Portekizce", "Portuguese"), "pt": ("Portekizce", "Portuguese"),
    "rus": ("Rusça", "Russian"), "ru": ("Rusça", "Russian"),
    "ara": ("Arapça", "Arabic"), "ar": ("Arapça", "Arabic"),
    "jpn": ("Japonca", "Japanese"), "ja": ("Japonca", "Japanese"),
    "kor": ("Korece", "Korean"), "ko": ("Korece", "Korean"),
    "chi": ("Çince", "Chinese"), "zho": ("Çince", "Chinese"), "zh": ("Çince", "Chinese"),
    "hin": ("Hintçe", "Hindi"), "hi": ("Hintçe", "Hindi"),
    "pol": ("Lehçe", "Polish"), "pl": ("Lehçe", "Polish"),
    "dut": ("Felemenkçe", "Dutch"), "nld": ("Felemenkçe", "Dutch"), "nl": ("Felemenkçe", "Dutch"),
    "swe": ("İsveççe", "Swedish"), "sv": ("İsveççe", "Swedish"),
    "nor": ("Norveççe", "Norwegian"), "no": ("Norveççe", "Norwegian"),
    "dan": ("Danca", "Danish"), "da": ("Danca", "Danish"),
    "fin": ("Fince", "Finnish"), "fi": ("Fince", "Finnish"),
    "gre": ("Yunanca", "Greek"), "ell": ("Yunanca", "Greek"), "el": ("Yunanca", "Greek"),
    "heb": ("İbranice", "Hebrew"), "he": ("İbranice", "Hebrew"),
    "cze": ("Çekçe", "Czech"), "ces": ("Çekçe", "Czech"), "cs": ("Çekçe", "Czech"),
    "hun": ("Macarca", "Hungarian"), "hu": ("Macarca", "Hungarian"),
    "rum": ("Romence", "Romanian"), "ron": ("Romence", "Romanian"), "ro": ("Romence", "Romanian"),
    "ukr": ("Ukraynaca", "Ukrainian"), "uk": ("Ukraynaca", "Ukrainian"),
    "bul": ("Bulgarca", "Bulgarian"), "bg": ("Bulgarca", "Bulgarian"),
    "per": ("Farsça", "Persian"), "fas": ("Farsça", "Persian"), "fa": ("Farsça", "Persian"),
    "tha": ("Tayca", "Thai"), "th": ("Tayca", "Thai"),
    "vie": ("Vietnamca", "Vietnamese"), "vi": ("Vietnamca", "Vietnamese"),
    "ind": ("Endonezce", "Indonesian"), "id": ("Endonezce", "Indonesian"),
    "und": ("", ""),
}

_CODEC_NAMES = {
    "ac3": "AC3", "eac3": "E-AC3", "dts": "DTS", "truehd": "TrueHD", "aac": "AAC", "mp3": "MP3",
    "opus": "Opus", "vorbis": "Vorbis", "flac": "FLAC", "pcm_s16le": "PCM", "alac": "ALAC",
    "subrip": "SRT", "ass": "ASS", "ssa": "SSA", "webvtt": "WebVTT",
    "hdmv_pgs_subtitle": "PGS", "dvd_subtitle": "VobSub", "dvb_subtitle": "DVB",
}
# Kısaltma değil, sözcük olan adlar: (Türkçe, İngilizce).
_CODEC_WORDS = {"mov_text": ("Metin", "Text")}

_CHANNELS = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}


def language_name(code: str | None, lang: str = "tr") -> str:
    """'tur' -> 'Türkçe'; bilinmeyen kod BÜYÜK harfle döner; 'und'/boş -> ''."""
    if not code:
        return ""
    key = str(code).strip().lower()
    names = _LANGS.get(key)
    if names is not None:
        return names[0] if lang == "tr" else names[1]
    return key.upper()


def track_label(track: dict, lang: str = "tr", fallback: str | None = None) -> str:
    """Kullanıcıya gösterilecek kısa ad: 'Türkçe · AC3 5.1' / 'İngilizce — Yönetmen yorumu'.

    `fallback`: dili/başlığı olmayan parçanın adı (arayüz `t("track")` verir); verilmezse etkin dilde."""
    if fallback is None:
        fallback = "Parça" if lang == "tr" else "Track"
    name = language_name(track.get("lang"), lang)
    title = str(track.get("title") or "").strip()
    codec_key = str(track.get("codec") or "").lower()
    word = _CODEC_WORDS.get(codec_key)
    if word is not None:
        codec = word[0] if lang == "tr" else word[1]
    else:
        codec = _CODEC_NAMES.get(codec_key, str(track.get("codec") or "").upper())
    chans = track.get("demux-channel-count")
    chan_text = _CHANNELS.get(int(chans), f"{int(chans)}ch") if isinstance(chans, (int, float)) and chans else ""
    detail = " ".join(x for x in (codec, chan_text) if x)

    head = name or title or f"{fallback} {track.get('id', '')}".strip()
    parts = [head]
    if name and title and title.lower() != name.lower():
        parts[0] = f"{name} — {title}"
    if track.get("external"):
        parts.append("↗")
    elif detail:
        parts.append(detail)
    if track.get("forced"):
        parts.append("zorunlu" if lang == "tr" else "forced")
    return " · ".join(p for p in parts if p)


def split_tracks(track_list: list | None) -> tuple[list[dict], list[dict]]:
    """mpv `track-list` -> (ses parçaları, altyazı parçaları); yalnızca sözlük öğeler."""
    audio: list[dict] = []
    subs: list[dict] = []
    for t in track_list or []:
        if not isinstance(t, dict):
            continue
        kind = t.get("type")
        if kind == "audio":
            audio.append(t)
        elif kind == "sub":
            subs.append(t)
    return audio, subs


def needs_track_menu(audio: list[dict], subs: list[dict]) -> bool:
    """Parça düğmesi yalnızca seçecek bir şey varken gösterilir: birden çok ses veya herhangi bir altyazı."""
    return len(audio) > 1 or len(subs) > 0


def next_track_id(tracks: list[dict], current_id: int | None, allow_off: bool) -> int | None:
    """Döngüsel seçim (kısayol): ... -> sonraki -> ... -> (altyazıda) kapalı -> ilk. Kapalı = None."""
    ids = [int(t["id"]) for t in tracks if "id" in t]
    if not ids:
        return None
    if current_id is None:
        return ids[0]
    if current_id not in ids:
        return ids[0]
    i = ids.index(current_id)
    if i + 1 < len(ids):
        return ids[i + 1]
    return None if allow_off else ids[0]
