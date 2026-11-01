from __future__ import annotations

import locale
import os
import re
import sys

LOCALES = {
    "tr": {
        "title": "Lumina",
        "brand": "LUMINA",
        "live_photo_title": "Canlı Fotoğraf",
        "video_title": "Video",
        "photo_title": "Fotoğraf",
        "open_file": "Dosya Aç",

        "open_folder": "Klasör Aç",
        "new_window": "Yeni Pencere",
        "live_badge": "CANLI",
        "play": "Oynat",
        "pause": "Duraklat",
        "settings": "Ayarlar",
        "language": "Dil",
        "theme": "Tema",
        "dark": "Koyu",
        "light": "Açık",
        "system": "Sistem",
        "info": "Ayrıntılar",
        "file_info": "Dosya: {}\nBoyut: {:.2f} MB\nÇözünürlük: {}×{}\nHareket: {}",
        "yes": "Var",
        "no": "Yok",
        "prev": "Önceki",
        "next": "Sonraki",
        "fit": "Sığdır",
        "fill": "Doldur",
        "actual_size": "Gerçek Boyut (1:1)",
        "rot_left": "Sola Döndür",
        "rot_right": "Sağa Döndür",
        "zoom_in": "Yakınlaştır",
        "zoom_out": "Uzaklaştır",
        "fullscreen": "Tam Ekran",
        "exit_fullscreen": "Tam Ekrandan Çık",
        "slideshow": "Slayt Gösterisi",
        "copy": "Kopyala",
        "copy_image": "Görüntüyü Kopyala",
        "copy_file": "Dosyayı Kopyala",
        "rotate": "Döndür",
        "view": "Görünüm",
        "print": "Yazdır",
        "delete": "Geri Dönüşüm Kutusuna Taşı",
        "permanently_delete": "Kalıcı olarak sil",
        "delete_perm_title": "Kalıcı Olarak Sil",
        "delete_perm_confirm": "'{}' dosyasını kalıcı olarak silmek istediğinizden emin misiniz?\nBu işlem geri alınamaz.",
        "undo": "Geri Al",
        "rename": "Yeniden Adlandır",
        "rename_title": "Yeniden Adlandır",
        "rename_prompt": "Yeni dosya adı:",
        "rename_fail": "Yeniden adlandırılamadı:\n{}",
        "save_as": "Farklı Kaydet",
        "save_as_fail": "Kaydedilemedi:\n{}",
        "open_with": "Birlikte Aç…",
        "reveal": "Klasörde Göster",
        "open_dir": "Dizini Aç",
        "copy_path": "Tam Dosya Yolunu Kopyala",
        "copy_name": "Dosya Adını Kopyala",
        "sort": "Sıralama",
        "sort_date_asc": "Tarihe Göre (Eskiden Yeniye)",
        "sort_date_desc": "Tarihe Göre (Yeniden Eskiye)",
        "sort_name_asc": "İsme Göre (A'dan Z'ye)",
        "sort_name_desc": "İsme Göre (Z'den A'ya)",
        "play_live": "CANLI Oynat",
        "autoplay": "Canlı Fotoğrafları Otomatik Oynat",
        "default_apps": "Varsayılan Uygulamalar",
        "empty_title": "Fotoğraf ya da videoyu buraya bırakın",
        "empty_sub": "Ya da aşağıdaki düğmelerle bir dosya veya klasör açın (Canlı Fotoğraf, HEIC, JPEG, video).",
        "play_hint": "Oynat · Boşluk",
        "mute": "Sesi Kapat",
        "unmute": "Sesi Aç",
        "muted_label": "Sessiz",
        "frame_back": "Önceki Kare",
        "frame_fwd": "Sonraki Kare",
        "speed": "Oynatma Hızı",
        "speed_normal": "Normal Hız",
        "loop": "Döngü",
        "loop_on": "Döngü: açık",
        "loop_off": "Döngü: kapalı",
        "tracks": "Ses ve Altyazı",
        "audio_track": "Ses",
        "subtitle_track": "Altyazı",
        "track": "Parça",
        "subtitle_off": "Kapalı",
        "subtitle_open": "Altyazı Dosyası Aç…",
        "subtitle_filter": "Altyazı dosyaları (*.srt *.ass *.ssa *.vtt *.sub *.sup);;Tüm dosyalar (*.*)",
        "audio_now": "Ses: {}",
        "subtitle_now": "Altyazı: {}",
        "resume_from": "Kaldığınız yerden devam: {}",
        "resume_restart": "Baştan",
        "resume_setting": "Videoda Kaldığım Yerden Devam Et",
        "resume_setting_desc": "3 dakikadan uzun videolarda son izleme konumunu hatırlar.",
        "save_frame": "Kareyi PNG Olarak Kaydet",
        "frame_saved": "Kare kaydedildi: {}",
        "frame_save_fail": "Kare kaydedilemedi.",
        "show": "Göster",
        "sec_histogram": "Histogram",
        "rotate_saved": "Döndürme dosyaya kaydedildi",
        "rotate_save": "Döndürmeyi Kaydet",
        "rotate_hint": "Döndürüldü",
        "rotate_fail": "Döndürme kaydedilemedi:\n{}",
        "crop": "Kırp…",
        "crop_free": "Serbest",
        "crop_cancel": "İptal",
        "crop_save": "Kopya Kaydet",
        "crop_saving": "Kırpılıyor…",
        "crop_saved": "Kırpılmış kopya kaydedildi: {}",
        "crop_fail": "Kırpma başarısız:\n{}",
        "crop_suffix": "kırpılmış",
        "move_to": "Klasöre Taşı…",
        "copy_to": "Klasöre Kopyala…",
        "move_pick": "Taşınacak Klasörü Seçin",
        "copy_pick": "Kopyalanacak Klasörü Seçin",
        "moved": "Taşındı: {}",
        "copied": "Kopyalandı: {}",
        "undo_move": "Geri Al",
        "move_fail": "İşlem başarısız:\n{}",
        "audio_device": "Ses Çıkış Aygıtı",
        "audio_device_auto": "Otomatik (Varsayılan)",
        "close_details": "Ayrıntıları Kapat",
        "chrome_pos": "Kontrol Kapsülü",
        "chrome_left": "Kapsül · Sol Üst",
        "chrome_right": "Kapsül · Sağ Üst",
        "strip_show": "Film Şeridini Aç",
        "strip_hide": "Film Şeridini Gizle",
        "loading": "Yükleniyor…",
        "shell": "'Birlikte Aç' Listesi",
        "shell_on": "'Birlikte Aç' Listesine Ekle",
        "shell_off": "'Birlikte Aç' Listesinden Çıkar",
        "shell_ok": "Dosya Gezgini menüsü güncellendi.",
        "shell_fail": "Kayıt defteri yazılamadı:\n{}",
        "load_fail": "Görüntü açılamadı:\n{}",
        "play_fail": "Video oynatılamadı:\n{}",
        "delete_fail": "Silinemedi:\n{}",
        "undo_fail": "Geri alınamadı:\n{}",
        "print_fail": "Yazdırılamadı:\n{}",
        "error": "Hata",
        "version_line": "Sürüm {}",
        "check_updates": "Güncellemeleri Kontrol Et",
        "checking_updates": "Kontrol ediliyor…",
        "check_btn": "Kontrol Et",
        "update_group": "Güncellemeler",
        "update_pill": "Güncelle · v{}",
        "update_pill_tip": "Yeni sürüm v{} hazır; ayrıntılar ve kurulum için tıklayın",
        "tray_update_now": "Güncelle · v{version}",
        "tray_show": "Göster",
        "tray_quit": "Çıkış",
        "update_new_version": "Yeni sürüm: v{}",
        "update_status_current": "Güncel · v{}",
        "update_status_failed": "Denetlenemedi",
        "auto_update_install": "Güncellemeleri Otomatik Kur",
        "auto_update_install_tip": "Yeni sürüm bulunduğunda arka planda indirilir ve uygulama kullanılmıyorken kurulur.",
        "update_check_title": "Güncelleme Kontrolü",
        "update_current": "Lumina güncel.\n\nMevcut sürüm: {}",
        "update_invalid": "Güncelleme bilgisi eksik veya geçersiz.",
        "update_network": "Güncelleme kontrolü yapılamadı.\nLütfen internet bağlantınızı kontrol edin.",
        "update_error": "Güncelleme kontrolü sırasında bir hata oluştu:\n{}",
        "update_available_title": "Güncelleme Mevcut",
        "update_available_heading": "Lumina Sürüm {} Mevcut",
        "update_version_line": "Mevcut sürüm: {} → Yeni sürüm: {}",
        "update_notes": "Sürüm Notları",
        "update_available_plain": "Güncelleme mevcut.",
        "update_cancel": "İptal",
        "update_later": "Daha Sonra",
        "update_now": "Şimdi Güncelle",
        "update_downloading": "İndiriliyor…",
        "update_downloaded_bytes": "{} indirildi",
        "update_download_done": "İndirme tamamlandı.",
        "update_installing": "Kuruluyor, lütfen bekleyin…",
        "update_restarting": "Kurulum tamamlandı. Uygulama yeniden başlatılıyor…",
        "update_open_page": "İndirme Sayfasını Aç",
        "update_unverified": (
            "Sunucu bu güncelleme için sağlama toplamı (SHA-256) vermedi; "
            "dosyanın bütünlüğü doğrulanamadı.\n\nYine de kurulsun mu?"
        ),
        "update_size_line": "İndirme boyutu: {size}",
        "update_failed_inline": "Güncelleme tamamlanamadı: {error}",
        "update_retry": "Tekrar Dene",
        "update_install_anyway": "Yine de Kur",
        "update_installed_toast": "{app} v{version} sürümüne güncellendi.",
        "update_err_url_invalid": "Geçersiz güncelleme adresi.",
        "update_err_url_credentials": "Güncelleme adresinde kimlik bilgisi olamaz.",
        "update_err_url_insecure": "Güncelleme yalnızca HTTPS ile indirilebilir.",
        "update_err_url_port": "Güncelleme adresi beklenmeyen bir port kullanıyor.",
        "update_err_url_untrusted": "Güncelleme adresi güvenilen bir sunucuya ait değil ({detail}).",
        "update_err_url_redirect": "Yönlendirme reddedildi: {detail}",
        "update_err_checksum_format": "Sunucunun verdiği sağlama toplamı geçersiz biçimde.",
        "update_err_too_big": "Güncelleme boyutu izin verilen sınırı aşıyor.",
        "update_err_incomplete": "İndirme eksik kaldı (boyut uyuşmuyor).",
        "update_err_size_mismatch": "İndirilen dosyanın boyutu beklenenle uyuşmuyor.",
        "update_err_empty": "İndirilen dosya boş.",
        "update_err_checksum_mismatch": "Dosya bütünlüğü doğrulanamadı (checksum).",
        "update_err_network": "İndirilemedi: {detail}",
        "update_err_read": "Paket okunamadı: {detail}",
        "update_err_not_package": "İndirilen dosya bir exe ya da zip değil.",
        "update_err_no_exe": "Pakette uygulamanın exe dosyası yok.",
        "update_err_bad_zip": "Zip dosyası bozuk: {detail}",
        "update_err_write": "Yeni sürüm uygulamanın klasörüne yazılamadı: {detail}",
        "update_err_not_pe": "Paketteki dosya geçerli bir Windows uygulaması değil.",
        "update_err_arch": "Paket başka bir işlemci mimarisi için hazırlanmış ({detail}).",
        "update_err_not_writable": (
            "Lumina'nın bulunduğu klasöre yazılamıyor ({detail}). "
            "Uygulamayı yazılabilir bir klasöre (ör. Masaüstü ya da Belgeler) taşıyıp yeniden deneyin."
        ),
        "update_err_swap": "Çalışan uygulama dosyası değiştirilemedi: {detail}",
        "update_err_swap2": "Yeni sürüm yerine konamadı: {detail}",
        "update_err_spawn": "Yeni sürüm başlatılamadı: {detail}",
        "update_err_file_missing": "Güncelleme dosyası bulunamadı.",
        "update_err_source_run": "Otomatik kurulum yalnızca derlenmiş Lumina.exe'de çalışır (kaynaktan çalışırken yok).",
        "update_err_other": "{detail}",
        "details_title": "Ayrıntılar",
        "d_file": "Dosya",
        "d_pixels": "Çözünürlük",
        "d_size": "Boyut",
        "d_type": "Tür",
        "d_created": "Oluşturma",
        "d_modified": "Değiştirilme",
        "d_duration": "Süre / Uzunluk",
        "d_fps": "Kare Hızı",
        "d_video_bitrate": "Video Bit Hızı",
        "d_total_bitrate": "Toplam Bit Hızı",
        "d_audio_bitrate": "Ses Bit Hızı",
        "d_audio_channels": "Ses Kanalları",
        "d_audio_samplerate": "Örnekleme Hızı",
        "d_video_codec": "Video Kodlayıcı",
        "d_audio_codec": "Ses Kodlayıcı",
        "d_camera": "Kamera",
        "d_lens": "Lens Modeli",
        "d_taken": "Çekim Tarihi",
        "d_iso": "ISO Hızı",
        "d_aperture": "Diyafram Açıklığı",
        "d_exposure": "Pozlama Süresi",
        "d_focal": "Odak Uzaklığı",
        "d_flash": "Flaş",
        "d_color_space": "Renk Alanı",
        "d_gps": "GPS Koordinatları",
        "d_motion": "Hareket",
        "d_protocol": "Protokol",
        "d_folder": "Klasör",
        "d_note": "Not",
        "sec_general": "Genel Dosya Bilgileri",
        "sec_video": "Görüntü & Video Akışı",
        "sec_audio": "Ses Akışı",
        "sec_camera": "Kamera & Çekim Bilgileri",
        "sec_location": "Konum Bilgisi",
        "open_in_maps": "Haritada Göster ↗",
        "about": "Hakkında",
        "about_title": "Lumina",
        "about_version_line": "Sürüm {} — Fotoğraf ve Video Görüntüleyici",
        "about_desc": (
            "• 4K / 8K 120 FPS Donanım Hızlandırmalı Oynatıcı\n"
            "• Apple Live Photo & Hareketli Fotoğraf Desteği\n"
            "• RAW, HEIC, AV1, AVIF, WebP, PNG, JPG, GIF\n"
            "• Çerçevesiz modern cam tasarım"
        ),
        "accent": "Renk",
        "accent_default": "(varsayılan)",
        "accent_desc": "Arayüzde kullanılan vurgu rengini seçin.",
        "accent_cyan": "Turkuaz",
        "accent_blue": "Mavi",
        "accent_teal": "Zümrüt",
        "accent_indigo": "İndigo",
        "accent_violet": "Mor",
        "accent_rose": "Gül",
        "accent_orange": "Kehribar",
        "accent_red": "Kırmızı",
        "accent_green": "Yeşil",
        "accent_yellow": "Sarı",
        "quit": "Çıkış",
        "tray_hide": "Gizle",
        "auto_update_check": "Güncellemeleri Otomatik Kontrol Et",
        "sys_integration": "Sistem Entegrasyonu",
        "about_features": (
            "• Apple Live Photo & Google Motion Photo tam desteği\n"
            "• Donanım Hızlandırmalı Akıcı Video Oynatıcı (Direct3D11)\n"
            "• RAW, HEIC, AVIF, JPEG XL (JXL), WebP, PNG, JPG\n"
            "• Kimlik Tabanlı Akıllı Canlı Fotoğraf Birleştirme"
        ),
        "ok": "Tamam",
        "filter_all_files": "Tüm Dosyalar (*.*)",
        "filter_media": "Medya ({})",
        "filter_jpeg": "JPEG Resim (*.jpg *.jpeg)",
        "filter_png": "PNG Resim (*.png)",
        "filter_webp": "WebP Resim (*.webp)",
        "filter_bmp": "BMP Resim (*.bmp)",
        "rename_invalid_chars": "Dosya adı geçersiz karakterler içeremez.",
        "rename_exists": "'{}' zaten mevcut.",
        "video_open_fail": "Video açılamadı.",
        "lang_auto": "Otomatik (Sistem)",
        "settings_window_title": "Ayarlar",
        "tab_general": "Genel",
        "tab_playback": "Oynatma & Ses",
        "tab_live_photo": "Canlı Fotoğraf",
        "tab_about": "Hakkında",
        "settings_lang_desc": "Uygulama dilini değiştirin.",
        "settings_theme_desc": "Arayüz temasını belirleyin.",
        "settings_autoplay_desc": "Canlı fotoğrafları açılışta otomatik oynat.",
        "settings_default_apps_desc": "Lumina'yı varsayılan fotoğraf ve video görüntüleyici yapın.",
        "settings_shell_desc": "Lumina'yı 'Birlikte Aç' listesine ekleyin: görsel ve video dosyalarına sağ tıklayınca Birlikte Aç içinde görünür.",
        "settings_audio_desc": "Video ve Canlı Fotoğraf seslerinin çalınacağı çıkış aygıtını seçin.",
        "merge_section_title": "Kimlik Tabanlı Eşleştirme (Apple & Google Content ID)",
        "merge_section_desc": (
            "Aynı klasörde bulunan fakat adları eşleşmeyen Live Photo (fotoğraf ve video) çiftlerini "
            "benzersiz ContentIdentifier GUID kimliği üzerinden bulur ve videoyu fotoğrafın adıyla "
            "güvenle eşleştirip birleştirir."
        ),
        "merge_target_folder": "Hedef Klasör",
        "merge_current_folder": "Mevcut Klasör",
        "merge_browse_folder": "Klasör Seç…",
        "merge_btn_start": "Kimlik Üzerinden Birleştirmeyi Başlat",
        "merge_running": "Klasör taranıyor ve eşleştiriliyor…",
        "merge_result_title": "Birleştirme Sonucu",
        "merge_result_summary": "Taranan Dosya: {}\nFotoğraf: {}\nVideo: {}\nZaten Eşleşmiş: {}\nYeni Birleştirilen: {}",
        "merge_error_title": "Hata",
        "merge_more_pairs": "… ve {} çift daha.",
        "merge_no_folder": "Lütfen geçerli bir klasör seçin.",
    },
    "en": {
        "title": "Lumina",
        "brand": "LUMINA",
        "live_photo_title": "Live Photo",
        "video_title": "Video",
        "photo_title": "Photo",
        "open_file": "Open File",

        "open_folder": "Open Folder",
        "new_window": "New Window",
        "live_badge": "LIVE",
        "play": "Play",
        "pause": "Pause",
        "settings": "Settings",
        "language": "Language",
        "theme": "Theme",
        "dark": "Dark",
        "light": "Light",
        "system": "System",
        "info": "Details",
        "file_info": "File: {}\nSize: {:.2f} MB\nResolution: {}×{}\nMotion: {}",
        "yes": "Yes",
        "no": "No",
        "prev": "Previous",
        "next": "Next",
        "fit": "Fit",
        "fill": "Fill",
        "actual_size": "Actual Size (1:1)",
        "rot_left": "Rotate Left",
        "rot_right": "Rotate Right",
        "zoom_in": "Zoom In",
        "zoom_out": "Zoom Out",
        "fullscreen": "Fullscreen",
        "exit_fullscreen": "Exit Fullscreen",
        "slideshow": "Slideshow",
        "copy": "Copy",
        "copy_image": "Copy Image",
        "copy_file": "Copy File",
        "rotate": "Rotate",
        "view": "View",
        "print": "Print",
        "delete": "Move to Recycle Bin",
        "permanently_delete": "Permanently Delete",
        "delete_perm_title": "Permanently Delete",
        "delete_perm_confirm": "Are you sure you want to permanently delete '{}'?\nThis action cannot be undone.",
        "undo": "Undo",
        "rename": "Rename",
        "rename_title": "Rename",
        "rename_prompt": "New file name:",
        "rename_fail": "Could not rename:\n{}",
        "save_as": "Save As",
        "save_as_fail": "Could not save:\n{}",
        "open_with": "Open With…",
        "reveal": "Show in Folder",
        "open_dir": "Open Directory",
        "copy_path": "Copy Full Path",
        "copy_name": "Copy File Name",
        "sort": "Sort By",
        "sort_date_asc": "Date (Oldest to Newest)",
        "sort_date_desc": "Date (Newest to Oldest)",
        "sort_name_asc": "Name (A to Z)",
        "sort_name_desc": "Name (Z to A)",
        "play_live": "Play LIVE",
        "autoplay": "Auto-Play Live Photos",
        "default_apps": "Default Apps",
        "empty_title": "Drop a photo or video here",
        "empty_sub": "Or open a file or folder with the buttons below (Live Photo, HEIC, JPEG, video).",
        "play_hint": "Play · Space",
        "mute": "Mute",
        "unmute": "Unmute",
        "muted_label": "Muted",
        "frame_back": "Previous Frame",
        "frame_fwd": "Next Frame",
        "speed": "Playback Speed",
        "speed_normal": "Normal Speed",
        "loop": "Loop",
        "loop_on": "Loop: on",
        "loop_off": "Loop: off",
        "tracks": "Audio and Subtitles",
        "audio_track": "Audio",
        "subtitle_track": "Subtitles",
        "track": "Track",
        "subtitle_off": "Off",
        "subtitle_open": "Open Subtitle File…",
        "subtitle_filter": "Subtitle files (*.srt *.ass *.ssa *.vtt *.sub *.sup);;All files (*.*)",
        "audio_now": "Audio: {}",
        "subtitle_now": "Subtitles: {}",
        "resume_from": "Resuming from {}",
        "resume_restart": "Start over",
        "resume_setting": "Resume Videos Where I Left Off",
        "resume_setting_desc": "Remembers the last position of videos longer than 3 minutes.",
        "save_frame": "Save Frame as PNG",
        "frame_saved": "Frame saved: {}",
        "frame_save_fail": "Could not save the frame.",
        "show": "Show",
        "sec_histogram": "Histogram",
        "rotate_saved": "Rotation saved to file",
        "rotate_save": "Save Rotation",
        "rotate_hint": "Rotated",
        "rotate_fail": "Could not save the rotation:\n{}",
        "crop": "Crop…",
        "crop_free": "Free",
        "crop_cancel": "Cancel",
        "crop_save": "Save Copy",
        "crop_saving": "Cropping…",
        "crop_saved": "Cropped copy saved: {}",
        "crop_fail": "Crop failed:\n{}",
        "crop_suffix": "cropped",
        "move_to": "Move to Folder…",
        "copy_to": "Copy to Folder…",
        "move_pick": "Choose Destination Folder",
        "copy_pick": "Choose Destination Folder",
        "moved": "Moved: {}",
        "copied": "Copied: {}",
        "undo_move": "Undo",
        "move_fail": "Operation failed:\n{}",
        "audio_device": "Audio Output Device",
        "audio_device_auto": "Automatic (Default)",
        "close_details": "Close Details",
        "chrome_pos": "Control Capsule",
        "chrome_left": "Capsule · Top Left",
        "chrome_right": "Capsule · Top Right",
        "strip_show": "Show Filmstrip",
        "strip_hide": "Hide Filmstrip",
        "loading": "Loading…",
        "shell": "'Open With' List",
        "shell_on": "Add to 'Open With' List",
        "shell_off": "Remove from 'Open With' List",
        "shell_ok": "Explorer menu updated.",
        "shell_fail": "Could not write the registry:\n{}",
        "load_fail": "Could not open image:\n{}",
        "play_fail": "Could not play video:\n{}",
        "delete_fail": "Could not delete:\n{}",
        "undo_fail": "Could not undo:\n{}",
        "print_fail": "Could not print:\n{}",
        "error": "Error",
        "version_line": "Version {}",
        "check_updates": "Check for Updates",
        "checking_updates": "Checking for updates…",
        "check_btn": "Check",
        "update_group": "Updates",
        "update_pill": "Update · v{}",
        "update_pill_tip": "Version v{} is ready; click for details and installation",
        "tray_update_now": "Update · v{version}",
        "tray_show": "Show",
        "tray_quit": "Exit",
        "update_new_version": "New version: v{}",
        "update_status_current": "Up to date · v{}",
        "update_status_failed": "Could not check",
        "auto_update_install": "Install Updates Automatically",
        "auto_update_install_tip": "New versions are downloaded in the background and installed while the app is not in use.",
        "update_check_title": "Update Check",
        "update_current": "Lumina is up to date.\n\nCurrent version: {}",
        "update_invalid": "Update information is missing or invalid.",
        "update_network": "Could not check for updates.\nPlease check your internet connection.",
        "update_error": "An error occurred while checking for updates:\n{}",
        "update_available_title": "Update Available",
        "update_available_heading": "Lumina Version {} is Available",
        "update_version_line": "Current version: {} → New version: {}",
        "update_notes": "Release Notes",
        "update_available_plain": "An update is available.",
        "update_cancel": "Cancel",
        "update_later": "Later",
        "update_now": "Update Now",
        "update_downloading": "Downloading…",
        "update_downloaded_bytes": "{} downloaded",
        "update_download_done": "Download complete.",
        "update_installing": "Installing, please wait…",
        "update_restarting": "Install finished. Restarting the app…",
        "update_open_page": "Open Download Page",
        "update_unverified": (
            "The server did not provide a checksum (SHA-256) for this update, so the "
            "file's integrity could not be verified.\n\nInstall anyway?"
        ),
        "update_size_line": "Download size: {size}",
        "update_failed_inline": "The update could not be completed: {error}",
        "update_retry": "Try Again",
        "update_install_anyway": "Install Anyway",
        "update_installed_toast": "{app} was updated to v{version}.",
        "update_err_url_invalid": "Invalid update address.",
        "update_err_url_credentials": "The update address must not contain credentials.",
        "update_err_url_insecure": "The update can only be downloaded over HTTPS.",
        "update_err_url_port": "The update address uses an unexpected port.",
        "update_err_url_untrusted": "The update address does not belong to a trusted server ({detail}).",
        "update_err_url_redirect": "Redirect refused: {detail}",
        "update_err_checksum_format": "The checksum provided by the server is malformed.",
        "update_err_too_big": "The update is larger than the allowed limit.",
        "update_err_incomplete": "The download is incomplete (size mismatch).",
        "update_err_size_mismatch": "The downloaded file's size does not match the expected size.",
        "update_err_empty": "The downloaded file is empty.",
        "update_err_checksum_mismatch": "The file's integrity could not be verified (checksum).",
        "update_err_network": "Could not download: {detail}",
        "update_err_read": "The package could not be read: {detail}",
        "update_err_not_package": "The downloaded file is neither an exe nor a zip.",
        "update_err_no_exe": "The package does not contain the application's exe.",
        "update_err_bad_zip": "The zip file is damaged: {detail}",
        "update_err_write": "The new version could not be written to the application folder: {detail}",
        "update_err_not_pe": "The file in the package is not a valid Windows application.",
        "update_err_arch": "The package was built for a different processor architecture ({detail}).",
        "update_err_not_writable": (
            "Lumina's folder is not writable ({detail}). "
            "Move the app to a writable folder (for example the Desktop or Documents) and try again."
        ),
        "update_err_swap": "The running application file could not be replaced: {detail}",
        "update_err_swap2": "The new version could not be put in place: {detail}",
        "update_err_spawn": "The new version could not be started: {detail}",
        "update_err_file_missing": "The update file was not found.",
        "update_err_source_run": (
            "A"
            "utomatic installation only works in the packaged Lumina.exe (not when running from source)."
        ),
        "update_err_other": "{detail}",
        "details_title": "Details",
        "d_file": "File",
        "d_pixels": "Resolution",
        "d_size": "Size",
        "d_type": "Type",
        "d_created": "Created",
        "d_modified": "Modified",
        "d_duration": "Duration",
        "d_fps": "Frame Rate",
        "d_video_bitrate": "Video Bit Rate",
        "d_total_bitrate": "Total Bit Rate",
        "d_audio_bitrate": "Audio Bit Rate",
        "d_audio_channels": "Audio Channels",
        "d_audio_samplerate": "Sample Rate",
        "d_video_codec": "Video Codec",
        "d_audio_codec": "Audio Codec",
        "d_camera": "Camera",
        "d_lens": "Lens Model",
        "d_taken": "Date Taken",
        "d_iso": "ISO Speed",
        "d_aperture": "Aperture",
        "d_exposure": "Exposure Time",
        "d_focal": "Focal Length",
        "d_flash": "Flash",
        "d_color_space": "Color Space",
        "d_gps": "GPS Coordinates",
        "d_motion": "Motion",
        "d_protocol": "Protocol",
        "d_folder": "Folder",
        "d_note": "Note",
        "sec_general": "General File Info",
        "sec_video": "Video & Stream Metrics",
        "sec_audio": "Audio Stream",
        "sec_camera": "Camera & Capture",
        "sec_location": "Location (GPS)",
        "open_in_maps": "Open in Maps ↗",
        "about": "About",
        "about_title": "Lumina",
        "about_version_line": "Version {} — Photo and Video Viewer",
        "about_desc": (
            "• 4K / 8K 120 FPS hardware-accelerated playback\n"
            "• Apple Live Photo & Motion Photo support\n"
            "• RAW, HEIC, AV1, AVIF, WebP, PNG, JPG, GIF\n"
            "• Frameless modern glass design"
        ),
        "accent": "Color",
        "accent_default": "(default)",
        "accent_desc": "Choose the accent color used across the interface.",
        "accent_cyan": "Cyan",
        "accent_blue": "Blue",
        "accent_teal": "Teal",
        "accent_indigo": "Indigo",
        "accent_violet": "Violet",
        "accent_rose": "Rose",
        "accent_orange": "Orange",
        "accent_red": "Red",
        "accent_green": "Green",
        "accent_yellow": "Yellow",
        "quit": "Exit",
        "tray_hide": "Hide",
        "auto_update_check": "Check for Updates Automatically",
        "sys_integration": "System Integration",
        "about_features": (
            "• Full Apple Live Photo & Google Motion Photo support\n"
            "• Hardware Accelerated Smooth Video Player (Direct3D11)\n"
            "• RAW, HEIC, AVIF, JPEG XL (JXL), WebP, PNG, JPG\n"
            "• Smart Identity-based Live Photo Pairing"
        ),
        "ok": "OK",
        "filter_all_files": "All Files (*.*)",
        "filter_media": "Media ({})",
        "filter_jpeg": "JPEG Image (*.jpg *.jpeg)",
        "filter_png": "PNG Image (*.png)",
        "filter_webp": "WebP Image (*.webp)",
        "filter_bmp": "BMP Image (*.bmp)",
        "rename_invalid_chars": "The file name cannot contain invalid characters.",
        "rename_exists": "'{}' already exists.",
        "video_open_fail": "Could not open video.",
        "lang_auto": "Auto (System)",
        "settings_window_title": "Settings",
        "tab_general": "General",
        "tab_playback": "Playback & Audio",
        "tab_live_photo": "Live Photo",
        "tab_about": "About",
        "settings_lang_desc": "Change application language.",
        "settings_theme_desc": "Choose interface theme.",
        "settings_autoplay_desc": "Automatically play Live Photos when opened.",
        "settings_default_apps_desc": "Make Lumina your default photo and video viewer.",
        "settings_shell_desc": "Add Lumina to the 'Open With' list: it appears under Open With when you right-click an image or video.",
        "settings_audio_desc": "Select audio output device for video & Live Photo playback.",
        "merge_section_title": "Identity Matching (Apple & Google Content ID)",
        "merge_section_desc": (
            "Finds photo and video pairs in the same folder sharing the same "
            "ContentIdentifier GUID and safely renames mismatched videos to match the still image."
        ),
        "merge_target_folder": "Target Folder",
        "merge_current_folder": "Current Folder",
        "merge_browse_folder": "Browse…",
        "merge_btn_start": "Start Merging by Identity",
        "merge_running": "Scanning and pairing files…",
        "merge_result_title": "Merge Result",
        "merge_result_summary": "Scanned: {}\nPhotos: {}\nVideos: {}\nAlready Paired: {}\nNewly Merged: {}",
        "merge_error_title": "Error",
        "merge_more_pairs": "… and {} more pairs.",
        "merge_no_folder": "Please select a valid folder.",
    },
}

SUPPORTED_LANGUAGES: tuple[str, ...] = tuple(LOCALES.keys())
FALLBACK_LANGUAGE = "tr"
AUTO_LANGUAGE = "auto"

LANGUAGE_NAMES: dict[str, str] = {
    "tr": "Türkçe",
    "en": "English",
}

# (Bayrak simgeleri `app.ui.icons.language_flag_icon` ile vektör olarak çizilir; emoji bayrak tablosu kaldırıldı.)


def language_display_name(code: str) -> str:
    native = LANGUAGE_NAMES.get(code, code.upper())
    code_label = "US" if code == "en" else code.upper()
    return f"{code_label} · {native}"

_LANGUAGE_NAME_TO_CODE: dict[str, str] = {
    "turkce": "tr",
    "türkçe": "tr",
    "turkish": "tr",
    "turk": "tr",
    "english": "en",
    "ingilizce": "en",
    "german": "de",
    "deutsch": "de",
    "almanca": "de",
    "french": "fr",
    "francais": "fr",
    "français": "fr",
    "fransizca": "fr",
    "fransızca": "fr",
}

_WIN_PRIMARY_LANG_TO_CODE: dict[int, str] = {
    0x1F: "tr",
    0x09: "en",
    0x07: "de",
    0x0C: "fr",
    0x0A: "es",
    0x10: "it",
}


def _normalize_lang_code(raw: str | None) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    if not s or s in ("c", "c.utf8", "c.utf-8", "posix"):
        return None
    if ":" in s or ";" in s:
        for part in s.replace(";", ":").split(":"):
            code = _normalize_lang_code(part)
            if code:
                return code
        return None
    for sep in (".", "@"):
        if sep in s:
            s = s.split(sep, 1)[0]
    s = s.strip()
    if not s:
        return None
    if s in _LANGUAGE_NAME_TO_CODE:
        cand = _LANGUAGE_NAME_TO_CODE[s]
        if cand in LOCALES:
            return cand
    tokens = s.replace("-", "_").split("_")
    if not tokens:
        return None
    first = tokens[0].strip()
    if first in _LANGUAGE_NAME_TO_CODE:
        first = _LANGUAGE_NAME_TO_CODE[first]
    if first in LOCALES:
        return first
    return None


def _locale_code_from_system() -> str | None:
    # Windows'ta Qt'nin bölgesel ayarı, Windows görüntüleme dilinden
    # farklı olabilir. Görüntüleme dilini doğrudan Win32 API'den öncele.
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            for func in ("GetUserDefaultUILanguage", "GetSystemDefaultUILanguage"):
                try:
                    lcid = int(getattr(kernel32, func)())
                    code = _WIN_PRIMARY_LANG_TO_CODE.get(lcid & 0x3FF)
                    if code and code in LOCALES:
                        return code
                except Exception:
                    continue
            buf = ctypes.create_unicode_buffer(85)
            if kernel32.GetUserDefaultLocaleName(buf, 85):
                code = _normalize_lang_code(buf.value)
                if code:
                    return code
        except Exception:
            pass

    for env in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        try:
            val = os.environ.get(env)
            if val:
                for part in val.replace(";", ":").split(":"):
                    code = _normalize_lang_code(part)
                    if code:
                        return code
        except Exception:
            pass
    try:
        from PyQt6.QtCore import QLocale
        ql = QLocale.system()
        try:
            for ui_lang in (ql.uiLanguages() or []):
                code = _normalize_lang_code(ui_lang)
                if code:
                    return code
        except Exception:
            pass
        for candidate in (ql.bcp47Name(), ql.name()):
            try:
                code = _normalize_lang_code(candidate)
            except Exception:
                code = None
            if code:
                return code
    except Exception:
        pass
    try:
        if sys.platform == "win32":
            import ctypes
            try:
                kernel32 = ctypes.windll.kernel32
                for func in ("GetUserDefaultUILanguage", "GetSystemDefaultUILanguage"):
                    try:
                        lcid = int(getattr(kernel32, func)())
                        code = _WIN_PRIMARY_LANG_TO_CODE.get(lcid & 0x3FF)
                        if code and code in LOCALES:
                            return code
                    except Exception:
                        continue
                try:
                    buf = ctypes.create_unicode_buffer(85)
                    if kernel32.GetUserDefaultLocaleName(buf, 85):
                        code = _normalize_lang_code(buf.value)
                        if code:
                            return code
                except Exception:
                    pass
            except Exception:
                pass
    except Exception:
        pass
    try:
        try:
            loc = locale.getlocale()[0] or ""
            code = _normalize_lang_code(loc)
            if code:
                return code
        except Exception:
            pass
        try:
            loc2 = (locale.getdefaultlocale()[0] or "")
            code = _normalize_lang_code(loc2)
            if code:
                return code
        except Exception:
            pass
    except Exception:
        pass
    return None


def get_system_language() -> str:
    """Sistem dilini desteklenen kodlardan birine çözümler."""
    return _locale_code_from_system() or FALLBACK_LANGUAGE


def _resolve_language_choice(value: str | None) -> str:
    if value and value != AUTO_LANGUAGE and value in LOCALES:
        return value
    return get_system_language()


def get_active_language(lang: str | None = None) -> str:
    """Geçerli dil kodunu döner. 'auto'/boş ise sistem diline çözümler."""
    if isinstance(lang, str) and lang.strip():
        if lang in LOCALES:
            return lang
        if lang == AUTO_LANGUAGE or lang == "":
            return get_system_language()
    try:
        from app.core.settings_store import make_settings

        saved = str(make_settings().value("lang", AUTO_LANGUAGE) or AUTO_LANGUAGE)
        return _resolve_language_choice(saved)
    except Exception:
        pass
    return get_system_language()


def language_choices(
    include_auto: bool = False,
    lang: str | None = None,
) -> list[tuple[str, str]]:
    """Dil seçici için (kod, etiket) çiftleri döner."""
    detected = get_system_language()
    active = lang or detected
    try:
        table = LOCALES.get(active) or LOCALES[FALLBACK_LANGUAGE]
        base = table.get("lang_auto", "Auto (System)")
    except Exception:
        base = "Auto (System)"
    choices: list[tuple[str, str]] = []
    if include_auto:
        detected_label = language_display_name(detected)
        if base.endswith(")"):
            auto_label = f"{base[:-1]} - {detected_label})"
        else:
            auto_label = f"{base} - {detected_label}"
        choices.append((AUTO_LANGUAGE, auto_label))
    for code in LOCALES.keys():
        choices.append((code, language_display_name(code)))
    return choices


def t(key: str, lang: str | None = None) -> str:
    """Belirtilen dilde metni döner."""
    active = get_active_language(lang)
    table = LOCALES.get(active) or LOCALES[FALLBACK_LANGUAGE]
    return table.get(key, LOCALES[FALLBACK_LANGUAGE].get(key, key))



# Oynatıcı ve görüntü yükleyicinin ürettiği (Türkçe) kullanıcıya dönük hata metinleri. Çekirdek metni
# değiştirmeden yalnızca gösterimde çevrilir; karşılığı olmayan metin (mpv/PIL'in kendi İngilizce hataları
# dahil) olduğu gibi kalır. `tests/test_error_text.py` player.py'deki sabit metinlerin hepsini denetler.
_ERRORS_EN: dict[str, str] = {
    "Dosya açılamadı (bulunamadı veya erişilemiyor).": "Could not open the file (not found or inaccessible).",
    "Ses çıkışı başlatılamadı.": "Could not start audio output.",
    "Video çıkışı başlatılamadı.": "Could not start video output.",
    "Oynatılacak bir şey bulunamadı.": "Nothing to play was found.",
    "Dosya biçimi tanınmadı (desteklenmiyor veya bozuk).": "The file format was not recognised (unsupported or corrupt).",
    "Bu dosya desteklenmiyor.": "This file is not supported.",
    "Video açılamadı.": "Could not open the video.",
    "JPEG XR (.jxr) için 'imagecodecs' paketi gerekli.": "The 'imagecodecs' package is required for JPEG XR (.jxr).",
    "Poster alınamadı": "Could not get the poster frame",
    # app.core.image_edit (döndürmeyi kaydet / kırp)
    "geçersiz EXIF yönü": "invalid EXIF orientation",
    "JPEG değil": "not a JPEG file",
    "animasyonlu, çok sayfalı ya da 8 bitten derin görüntü: döndürme kaydedilmedi (özgün dosya korundu)":
        "animated, multi-page or deeper than 8-bit image: the rotation was not saved (the original was kept)",
}
_ERROR_PATTERNS_EN: list[tuple[str, str]] = [
    (r"^Video açılamadı \(mpv hata kodu (?P<code>-?\d+)\)\.$", "Could not open the video (mpv error code {code})."),
    (r"^JPEG XR dosyası çözülemedi: (?P<x>.+)$", "Could not decode the JPEG XR file: {x}"),
    (r"^(?P<ext>\S*) için döndürmeyi kaydetme desteklenmiyor$", "Saving the rotation is not supported for {ext}"),
]


def translate_error(message: str, lang: str | None = None) -> str:
    """Çekirdeğin Türkçe hata metnini etkin dile çevirir (Türkçede ve bilinmeyen metinde olduğu gibi döner)."""
    if not message or get_active_language(lang) == "tr":
        return message
    text = message.strip()
    if text in _ERRORS_EN:
        return _ERRORS_EN[text]
    for pattern, template in _ERROR_PATTERNS_EN:
        match = re.match(pattern, text, re.DOTALL)
        if match:
            return template.format(**match.groupdict())
    return message


_qt_translators: list = []


def install_qt_translations(lang: str | None = None) -> None:
    """Qt'nin kendi yazılarını (Tamam / İptal / Evet / Hayır, metin kutusu sağ tık menüsü…) uygulama diline çevirir.

    Paketlenmiş uygulamada da çalışır: PyInstaller Qt çevirilerini (`qtbase_*.qm`) pakete koyar. Çeviri
    dosyası bulunamazsa sessizce İngilizce kalır; her çağrı öncekini kaldırıp yenisini kurar.
    """
    try:
        from PyQt6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator
    except ImportError:
        return
    app = QCoreApplication.instance()
    if app is None:
        return
    for old in _qt_translators:
        try:
            app.removeTranslator(old)
        except RuntimeError:  # önceki QApplication yok edilmiş (testler, yeniden başlatma)
            pass
    _qt_translators.clear()
    code = lang or get_system_language()
    folders = [QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)]
    bundle = getattr(sys, "_MEIPASS", "")
    if bundle:
        folders.append(os.path.join(bundle, "PyQt6", "Qt6", "translations"))
    for folder in folders:
        translator = QTranslator(app)
        if translator.load(QLocale(code), "qtbase", "_", folder):
            app.installTranslator(translator)
            _qt_translators.append(translator)
            break
    _install_key_names(app)


def _install_key_names(app) -> None:
    """Kısayol adlarını (menülerdeki "Ctrl+Shift+N") çevirmeden bırakır.

    Qt'nin Türkçe çevirisi bunları "Kontrol+Üst Karakter+N" yapıyordu; klavyedeki tuşlar ise Ctrl/Shift/Alt
    yazar. En son kurulan çevirici önce sorulduğu için bu, qtbase çevirisinin "QShortcut" bölümünü geçersiz
    kılar. macOS'ta Qt zaten ⌘⇧N gibi simgeler kullanır, bunun etkisi olmaz.
    """
    from PyQt6.QtCore import QTranslator

    class _KeyNames(QTranslator):
        def isEmpty(self) -> bool:  # noqa: N802
            return False

        def translate(self, context, source_text, disambiguation=None, n=-1):
            return source_text if context == "QShortcut" else None

    translator = _KeyNames()  # parent'sız: uygulama yok edilince C++ tarafı Python'dan önce silinmesin
    app.installTranslator(translator)
    _qt_translators.append(translator)
