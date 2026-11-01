"""Sistem tepsisi simgesi: Lumina çalıştığı sürece görünür, süreç bitince kaybolur.

* Arkada kalıcı tepsi uygulaması YOK: pencere (X) kapanınca süreç biter, simge de gider. Tepsi açma/kapama ayarı yok.
* Lumina her dosya için ayrı süreç açabildiğinden aynı anda tek süreç simge gösterir ("tepsi sahibi", bkz.
  `app.core.tray_owner`). Sahip olmayan süreçler simgesiz çalışır ve birkaç saniyede bir sahipliği yoklar: sahip
  çıkarsa (ya da çökerse) biri devralır, böylece Lumina açıkken simge hep vardır.
* Tepsi sistemde yoksa (ör. GNOME'da eklenti yok) sessizce simgesiz çalışır.
* Menü yalnızca kendi sürecinin pencerelerini yönetir: etkin pencere, yoksa en eski açık pencere.

Menü (dört uygulamada aynı iskelet): [Güncelle · vX (yalnızca güncelleme bekliyorsa)] — Göster/Gizle (pencere durumuna göre),
Ayarlar, Tema ▸ (Sistem, Koyu, Açık), Dil ▸ — Dosya Aç… — Çıkış. Simgeye tek tık pencereyi açar/öne getirir.
"""

from __future__ import annotations

import sys
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QPoint, QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QCursor, QGuiApplication, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from app.core.tray_owner import TrayOwnership
from app.resources.locales import get_active_language, language_choices, t
from app.ui.icons import language_flag_icon, menu_icon, theme_dark_icon, theme_light_icon, theme_system_icon

POLL_MS = 3000  # sahibi olmayan süreç sahipliği bu aralıkla yoklar (sahip çıkınca devralmak için); 0: yoklama yok
THEME_KEYS = ("system", "dark", "light")  # her uygulamada aynı sıra
_THEME_ICONS = {"system": theme_system_icon, "dark": theme_dark_icon, "light": theme_light_icon}


def _session_windows() -> list:
    from app.ui import session  # her çağrıda: liste yeniden atanırsa/yamalanırsa güncel olanı görsün

    return list(session.windows)


def _service_pending_version() -> str:
    """Bekleyen güncellemenin sürümü (yoksa boş). Güncelleme hizmeti ilk kareden sonra kurulur: henüz yoksa boş."""
    from app.ui.updates import get_update_service

    service = get_update_service()
    getter = getattr(service, "_pending_version", None)
    return str(getter() or "") if callable(getter) else ""


def _open_pending_update() -> None:
    from app.ui.updates import get_update_service

    service = get_update_service()
    if service is not None:
        service.open_pending()


def _choice_text(label: str, active: bool) -> str:
    """Seçili satır "✓" ile işaretlenir (menü stilinde göstergeler gizli; pencere menüsüyle aynı görünüm)."""
    return f"✓  {label}" if active else f"    {label}"


class LuminaTray(QObject):
    def __init__(
        self,
        app: QApplication,
        icon: QIcon,
        windows: Optional[Callable[[], list]] = None,
        ownership: Optional[TrayOwnership] = None,
        available: Optional[Callable[[], bool]] = None,
        pending_version: Optional[Callable[[], str]] = None,
        open_update: Optional[Callable[[], None]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._app = app
        self._icon = icon
        self._windows = windows or _session_windows
        self.ownership = ownership or TrayOwnership()
        self._available = available or QSystemTrayIcon.isSystemTrayAvailable
        self._pending_version = pending_version or _service_pending_version
        self._open_update = open_update or _open_pending_update
        self.state = "idle"  # idle | owner | waiting | unavailable | stopped
        self.tray: Optional[QSystemTrayIcon] = None
        self.menu: Optional[QMenu] = None
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._claim)

    # ------------------------------------------------------------------ yaşam döngüsü
    @property
    def shown(self) -> bool:
        return self.state == "owner" and self.tray is not None

    def start(self) -> bool:
        """Simgeyi göstermeyi dener. True: bu süreç tepsi sahibi ve simge görünür."""
        if self.state == "stopped":
            return False
        self._claim()
        return self.shown

    def _claim(self) -> None:
        if self.state in ("owner", "stopped"):
            return
        try:
            if not self._available():
                self.state = "unavailable"  # tepsi yok: sessizce simgesiz (yoklama da yok)
                self._poll.stop()
                return
            if not self.ownership.try_acquire():
                self.state = "waiting"  # başka süreç sahip: simgesiz sür, sahip çıkınca devral (POLL_MS <= 0: devralma yok)
                if POLL_MS > 0 and not self._poll.isActive():
                    self._poll.start()
                return
            self._create()
        except Exception:  # noqa: BLE001 — tepsi sorunu görüntüleyiciyi asla bozmaz: simgesiz sür, yeniden deneme yok
            self._poll.stop()
            self._drop_icon()
            self.ownership.release()
            self.state = "unavailable"
            return
        self._poll.stop()
        self.state = "owner"

    def shutdown(self) -> None:
        """Çıkışta: simgeyi kaldır (Windows'ta hayalet simge kalmasın) ve sahipliği bırak."""
        self.state = "stopped"
        self._poll.stop()
        self._drop_icon()
        self.ownership.release()

    def _drop_icon(self) -> None:
        tray, self.tray = self.tray, None
        menu, self.menu = self.menu, None
        if tray is not None:
            try:
                tray.hide()
                tray.setContextMenu(None)
                tray.deleteLater()
            except RuntimeError:
                pass
        if menu is not None:
            menu.deleteLater()

    # ------------------------------------------------------------------ simge ve menü
    def _create(self) -> None:
        self.tray = QSystemTrayIcon(self._icon, self)
        self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.refresh()
        self.tray.show()

    def _build_menu(self) -> None:
        """Menüyü (yeniden) kurar; durum (güncelleme satırı, seçili tema/dil, metinler) `refresh()` ile uygulanır."""
        old = self.menu
        menu = QMenu()
        menu.setObjectName("trayMenu")
        menu.setMinimumWidth(140)  # kısa etiketlerde ("Gizle", "Ayarlar") alt menü oklu satırlar ("Language") sıkışmasın
        self.menu = menu

        self.update_action = QAction(menu_icon("update"), "", menu)
        bold = self.update_action.font()
        bold.setBold(True)
        self.update_action.setFont(bold)
        # Menü stil sayfası (`QMenu::item { font-size }`) eylemin kendi yazı tipini ezer; kalınlık `:default` durumundan gelir.
        menu.setDefaultAction(self.update_action)
        self.update_action.triggered.connect(lambda *_: self._on_update())
        menu.addAction(self.update_action)
        self.update_separator = menu.addSeparator()

        # Göster/Gizle: pencere durumuna göre DİNAMİK (menü her açılışta yeniden okunur)
        self.show_action = QAction(menu_icon("view"), "", menu)
        self.show_action.triggered.connect(lambda *_: self.toggle_window())
        menu.addAction(self.show_action)

        self.settings_action = QAction(menu_icon("settings"), "", menu)
        self.settings_action.triggered.connect(lambda *_: self._on_settings())
        menu.addAction(self.settings_action)

        self.theme_menu = menu.addMenu("")
        self._theme_actions: dict[str, QAction] = {}
        group = QActionGroup(self.theme_menu)
        group.setExclusive(True)
        for key in THEME_KEYS:
            act = QAction(_THEME_ICONS[key](), "", self.theme_menu)
            act.setCheckable(True)
            act.triggered.connect(lambda checked=False, k=key: self.set_theme(k))
            group.addAction(act)
            self.theme_menu.addAction(act)
            self._theme_actions[key] = act

        self.lang_menu = menu.addMenu("")
        self.lang_menu.setIcon(menu_icon("globe"))
        self._lang_actions: dict[str, QAction] = {}
        lang_group = QActionGroup(self.lang_menu)
        lang_group.setExclusive(True)
        for code, _label in language_choices(include_auto=True, lang=self._lang()):
            act = QAction(language_flag_icon(code), "", self.lang_menu)
            act.setCheckable(True)
            act.triggered.connect(lambda checked=False, c=code: self.set_language(c))
            lang_group.addAction(act)
            self.lang_menu.addAction(act)
            self._lang_actions[code] = act

        menu.addSeparator()

        # Uygulamaya özel öğe (kendi ayırıcısıyla)
        self.open_action = QAction(menu_icon("file"), "", menu)
        self.open_action.triggered.connect(lambda *_: self._on_open())
        menu.addAction(self.open_action)

        menu.addSeparator()

        self.quit_action = QAction(menu_icon("exit"), "", menu)
        self.quit_action.triggered.connect(lambda *_: self.quit_app())
        menu.addAction(self.quit_action)

        menu.aboutToShow.connect(self._on_about_to_show)
        self.theme_menu.aboutToShow.connect(self.refresh)
        self.lang_menu.aboutToShow.connect(self.refresh)
        if self.tray is not None:
            self.tray.setContextMenu(menu)
        if old is not None:
            old.deleteLater()
        self.refresh()

    def rebuild_menu(self) -> None:
        """Menüyü baştan kurar (durum yine hizmetten/pencereden okunur: güncelleme satırı doğru kalır)."""
        if self.tray is not None:
            self._build_menu()

    def refresh(self, *_args) -> None:
        """Metinleri (etkin dilde), seçili tema/dili ve güncelleme satırını güncel duruma getirir."""
        if self.menu is None:
            return
        lang = self._lang()
        self.show_action.setText(t("tray_hide", lang) if self._window_shown() else t("tray_show", lang))
        self.settings_action.setText(t("settings", lang))
        self.open_action.setText(f"{t('open_file', lang)}…")
        self.quit_action.setText(t("tray_quit", lang))
        self.theme_menu.setTitle(t("theme", lang))
        self.lang_menu.setTitle(t("language", lang))
        theme = self._theme()
        self.theme_menu.setIcon(_THEME_ICONS.get(theme, theme_system_icon)())
        for key, act in self._theme_actions.items():
            act.setText(_choice_text(t(key, lang), key == theme))
            act.setChecked(key == theme)
        choice = self._lang_choice()
        labels = dict(language_choices(include_auto=True, lang=lang))
        for code, act in self._lang_actions.items():
            act.setText(_choice_text(labels.get(code, code), code == choice))
            act.setChecked(code == choice)
        self._apply_update_state(lang)
        if self.tray is not None:
            self.tray.setToolTip(t("title", lang))

    def _apply_update_state(self, lang: str) -> None:
        """"Güncelle · vX" satırı menünün EN ÜSTÜNDE, yalnızca güncelleme bekliyorsa (altındaki ayırıcıyla birlikte)."""
        try:
            version = str(self._pending_version() or "")
        except Exception:  # noqa: BLE001
            version = ""
        self.update_action.setText(t("tray_update_now", lang).format(version=version) if version else t("update_now", lang))
        self.update_action.setVisible(bool(version))
        self.update_separator.setVisible(bool(version))

    def _on_about_to_show(self) -> None:
        self.refresh()
        self._refresh_icons()
        self._fix_menu_pos()

    def _refresh_icons(self) -> None:
        """Vurgu değişmiş olabilir: menü simgeleri güncel vurguyla yeniden alınır (simge önbelleği vurguya göre tutulur)."""
        if self.menu is None:
            return
        self.update_action.setIcon(menu_icon("update"))
        self.show_action.setIcon(menu_icon("view"))
        self.settings_action.setIcon(menu_icon("settings"))
        self.open_action.setIcon(menu_icon("file"))
        self.quit_action.setIcon(menu_icon("exit"))
        self.lang_menu.setIcon(menu_icon("globe"))
        for key, act in self._theme_actions.items():
            act.setIcon(_THEME_ICONS[key]())
        theme = self._theme()
        self.theme_menu.setIcon(_THEME_ICONS.get(theme, theme_system_icon)())

    def _fix_menu_pos(self) -> None:
        """Yalnızca Windows: DPI ölçeklemesinde tepsi menüsü imleçten uzağa düşebilir; imleç yanına, ekran içine çek
        (FlexiKey/TransferTCP tepsisiyle aynı çözüm)."""
        if sys.platform != "win32" or self.menu is None:
            return
        pos = QCursor.pos()
        hint = self.menu.sizeHint() if self.menu is not None else QSize(160, 200)
        screen = QGuiApplication.screenAt(pos)
        if screen is not None:
            bounds = screen.availableGeometry()
            pos = QPoint(
                min(max(pos.x(), bounds.left()), max(bounds.left(), bounds.right() - hint.width())),
                min(max(pos.y(), bounds.top()), max(bounds.top(), bounds.bottom() - hint.height())),
            )
        menu = self.menu
        QTimer.singleShot(0, lambda p=pos: menu.move(p))

    # ------------------------------------------------------------------ durum okuma
    def _live_windows(self) -> list:
        out = []
        for win in self._windows():
            try:
                win.isVisible()  # silinmiş (C++ nesnesi gitmiş) pencereyi ele
            except RuntimeError:
                continue
            out.append(win)
        return out

    def target_window(self):
        """Menünün işlediği pencere: etkin pencere (bu sürecin ise), yoksa en eski açık pencere."""
        wins = self._live_windows()
        if not wins:
            return None
        active = self._app.activeWindow()
        for win in wins:
            if win is active:
                return win
        return wins[0]

    def _lang(self) -> str:
        win = self.target_window()
        lang = getattr(win, "lang", None)
        return lang if isinstance(lang, str) and lang else get_active_language()

    def _lang_choice(self) -> str:
        win = self.target_window()
        choice = getattr(win, "lang_choice", None)
        if isinstance(choice, str) and choice:
            return choice
        from app.core.settings_store import make_settings

        return str(make_settings().value("lang", "auto") or "auto")

    def _theme(self) -> str:
        win = self.target_window()
        theme = getattr(win, "theme", None)
        if theme not in THEME_KEYS:
            from app.core.settings_store import make_settings

            theme = str(make_settings().value("theme", "system") or "system")
        return theme if theme in THEME_KEYS else "system"

    # ------------------------------------------------------------------ eylemler
    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def _window_shown(self) -> bool:
        """Pencere görünür ve küçültülmemiş mi (menüde "Gizle" mi "Göster" mi yazacağını belirler)."""
        win = self.target_window()
        if win is None:
            return False
        try:
            minimized = getattr(win, "isMinimized", None)
            return bool(win.isVisible()) and not (callable(minimized) and minimized())
        except RuntimeError:
            return False

    def toggle_window(self) -> bool:
        """"Göster"/"Gizle": görünür pencereyi gizler (simgeden ya da menüden geri açılır), gizliyse/küçültülmüşse açar."""
        win = self.target_window()
        if win is None:
            return False
        if self._window_shown():
            try:
                win.hide()
            except RuntimeError:
                return False
            self.refresh()
            return True
        shown = self.show_window()
        self.refresh()
        return shown

    def show_window(self) -> bool:
        """Pencereyi öne getirir. Küçültülmüşse eski durumuna (normal/büyütülmüş/TAM EKRAN) döndürür; tam ekranı bozmaz."""
        win = self.target_window()
        if win is None:
            return False
        try:
            state = win.windowState()
            if state & Qt.WindowState.WindowMinimized:
                win.setWindowState((state & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
            if not win.isVisible():
                win.show()
            win.raise_()
            win.activateWindow()
        except RuntimeError:
            return False
        return True

    def _on_open(self) -> None:
        """"Dosya aç…": pencereyi öne getirip mevcut dosya seçicisini açar (menü kapandıktan sonra)."""
        win = self.target_window()
        if win is None:
            return
        self.show_window()
        QTimer.singleShot(0, lambda: self._call(win, "select_file"))

    def _on_settings(self) -> None:
        """"Ayarlar": pencereyi öne getirip ayarlar penceresini açar (menü kapandıktan sonra)."""
        win = self.target_window()
        if win is None:
            return
        self.show_window()
        QTimer.singleShot(0, lambda: self._call(win, "open_settings_dialog"))

    def _on_update(self) -> None:
        """Başlıktaki "Güncelle · vX" düğmesiyle aynı güncelleme penceresini açar (doğrudan kurmaz)."""
        self.show_window()
        QTimer.singleShot(0, self._open_update)

    @staticmethod
    def _call(win, name: str) -> None:
        try:
            getattr(win, name)()
        except RuntimeError:  # pencere bu arada kapandı
            pass

    def set_theme(self, mode: str) -> None:
        if mode not in THEME_KEYS:
            return
        for win in self._live_windows():
            self._call_with(win, "set_theme", mode)
        self.refresh()

    def set_language(self, code: str) -> None:
        for win in self._live_windows():
            self._call_with(win, "set_language", code)
        self.refresh()

    @staticmethod
    def _call_with(win, name: str, arg) -> None:
        try:
            getattr(win, name)(arg)
        except RuntimeError:
            pass

    def quit_app(self) -> None:
        """Çıkış = pencerelerin normal kapanış yolu (closeEvent: mpv, geçici dosyalar, geometri); son pencere
        yok olunca uygulama kendiliğinden çıkar (session._drop). Pencere yoksa doğrudan çıkılır."""
        QTimer.singleShot(0, self._close_all)

    def _close_all(self) -> None:
        wins = self._live_windows()
        for win in wins:
            try:
                win.close()
            except RuntimeError:
                pass
        if not wins:
            self._app.quit()


def attach_tray(app: QApplication, icon: QIcon) -> LuminaTray:
    """Süreç başına tek tepsi denetleyicisi; çıkışta simgeyi kaldırır ve sahipliği bırakır."""
    existing = getattr(app, "_lumina_tray", None)
    if isinstance(existing, LuminaTray):
        return existing
    tray = LuminaTray(app, icon, parent=app)
    app._lumina_tray = tray
    app.aboutToQuit.connect(tray.shutdown)
    tray.start()
    return tray
