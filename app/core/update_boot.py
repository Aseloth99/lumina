"""Açılışta güncelleme tarafı: komut satırı bayrakları, çöken yeni sürümü geri alma, sağlıklı sayma, "güncellendi" bildirimi.

Başlatıcı (launcher/) `--wait-pid N` çiftini KENDİSİ işler (N çıkana dek bekler, eski sürüm klasörünü çalışan süreçten
silmez) ve çocuğa iletmez; uygulama `--updated <eski sürüm>` ile `--show-window` / `--minimized` görür. Lumina'nın tepsi
simgesi yalnızca çalışırken görünür (kalıcı tepsi uygulaması ve "simge durumunda başlat" yoktur): pencere her zaman
açılır, bu iki bayrak yalnızca dosya yolu sanılmasın diye tanınır.

Hafif tutulur (açılış yolu): `self_update` (zipfile/subprocess) yalnızca güncelleme izi varsa içe aktarılır.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core.paths import launcher_path
from app.core.version import get_version

VALUE_FLAGS = ("--wait-pid", "--updated")
BOOL_FLAGS = ("--show-window", "--minimized")

def flag_value(argv: list[str], name: str) -> Optional[str]:
    for i, arg in enumerate(argv):
        if arg == name and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(name + "="):
            return arg[len(name) + 1:]
    return None


def positional_args(argv: list[str]) -> list[str]:
    """Bayrakları (ve değerlerini) atlayarak dosya yolu adaylarını verir: `--updated 1.0.0 --show-window foto.jpg` -> ['foto.jpg']."""
    out: list[str] = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in VALUE_FLAGS:
            i += 2
            continue
        if arg in BOOL_FLAGS or any(arg.startswith(flag + "=") for flag in VALUE_FLAGS):
            i += 1
            continue
        out.append(arg)
        i += 1
    return out


def launch_path(argv: list[str]) -> Optional[str]:
    """Açılacak dosya: ilk dosya yolu argümanı (varsa ve duruyorsa)."""
    for arg in positional_args(argv)[:1]:
        if arg and os.path.exists(arg):
            return str(Path(arg))
    return None


@dataclass
class BootState:
    started: float
    guard: str = "normal"  # self_update.Guard: normal | probation | rolled_back
    updated_from: Optional[str] = None  # bu açılış bir güncellemenin sonucuysa eski sürüm
    show_window: bool = False
    rolled_back: bool = False
    launcher: Optional[str] = None


def _su():
    from app.core import self_update

    return self_update


def begin(argv: list[str]) -> BootState:
    """QApplication'dan ÖNCE: önceki süreci bekle (yalnızca `--wait-pid` doğrudan verildiyse), güncelleme denemesini denetle.

    Yeni sürüm açılışta iki kez ölürse eski sürüm geri gelir: `rolled_back=True` döner, çağıran eski sürümü başlatıp ÇIKMALI."""
    state = BootState(
        started=time.monotonic(),
        updated_from=flag_value(argv, "--updated"),
        show_window="--show-window" in argv,
    )
    try:
        if flag_value(argv, "--wait-pid") is not None:
            _su().wait_for_previous_instance(argv)
        launcher = launcher_path()
        if launcher is None:
            return state
        state.launcher = str(launcher)
        if not os.path.exists(state.launcher + ".old"):
            return state  # olağan yol: güncelleme denemesi yok, modül içe aktarılmaz
        su = _su()
        state.guard = su.startup_guard(state.launcher, get_version())
        if state.guard == su.Guard.ROLLED_BACK:
            state.rolled_back = True
            path = launch_path(argv)
            try:
                from app.core.dll_dir import default_dll_directory

                with default_dll_directory():  # geri alınan başlatıcı çöken sürümün DLL dizinini miras almasın
                    su.spawn(state.launcher, [path] if path else [])
            except su.PackageError:
                pass
    except Exception:  # noqa: BLE001 — güncelleme izi asla uygulamanın açılmasını engellemez
        pass
    return state


def _update_in_flight() -> bool:
    """Bu süreçte yeni exe yerine kondu ya da çıkışta kurulacak: o `.old` yeni sürümün kurtarma noktasıdır, silinmemeli."""
    module = sys.modules.get("app.ui.updates")
    service = module.get_update_service() if module is not None else None
    if service is None:
        return False
    return bool(service.installer.swapped or service._installing_on_exit or service._installing)


def _arm_bad_cleanup(app, launcher: str) -> None:
    """Geri almadan sonra: `.bad` (çöken sürümün başlatıcı kopyası) yalnızca sağlıklı süre sonunda ya da temiz çıkışta silinir.
    `.old/.new/.boot` ve `.skip`'e DOKUNULMAZ: normal açılan süreç başka bir güncellemenin kurtarma noktasını silmemeli."""
    bad = launcher + ".bad"
    if not os.path.exists(bad):  # olağan yol: tek `stat`, modül içe aktarılmaz
        return
    from PyQt6.QtCore import QTimer

    seconds = _su().HEALTHY_AFTER_SECONDS
    done: list[bool] = []

    def discard() -> None:
        if done:
            return
        done.append(True)
        try:
            os.remove(bad)
        except OSError:
            pass  # kilitli/yok: bir sonraki açılışta yeniden denenir

    QTimer.singleShot(int(seconds * 1000), discard)
    app.aboutToQuit.connect(discard)


def arm(app, state: BootState) -> None:
    """Yeni sürüm (deneme sürecinde) 60 sn çalışınca VE temiz çıkışta kendini kanıtlar: eski exe ve yan dosyalar silinir.

    Yalnızca DENEME sürecindeki açılış doğrular: güncellemeden önce açılmış eski bir süreç (ya da güncellemeyi kendisi
    kuran süreç) kapanırken yeni sürümün kurtarma noktasını silmemeli.

    Geri alınan eski sürüm NORMAL açılır (`.old` yok) ama bozuk sürümün tam başlatıcı kopyası `.bad` (~139 MB) kalmıştır: normal
    açılışta `.bad` varsa aynı sağlıklı süre / temiz çıkışta YALNIZCA `.bad` silinir (`.skip` kalır: bozuk sürüm bir daha önerilmez)."""
    if state.launcher is None:
        return
    if state.guard == "normal":
        _arm_bad_cleanup(app, state.launcher)
        return
    if state.guard != "probation":
        return
    from PyQt6.QtCore import QTimer

    su = _su()
    done: list[bool] = []

    def confirm() -> None:
        if done or _update_in_flight():
            return
        done.append(True)
        su.confirm_healthy(state.launcher)

    QTimer.singleShot(int(su.HEALTHY_AFTER_SECONDS * 1000), confirm)
    # Temiz çıkış (kullanıcı kapattı) "çökme" değildir: Lumina'da kısa oturumlar normaldir (bir fotoğrafa bakıp kapatmak);
    # güncellemeden sonra art arda iki-üç kısa açılış sağlam yeni sürümü geri aldırmamalı. Çöken süreç aboutToQuit'e hiç gelmez.
    # service.shutdown'dan ÖNCE bağlanır: çıkış kurulumu yeni `.old`'u yazmadan çalışır.
    app.aboutToQuit.connect(confirm)


def announce(window, state: BootState) -> None:
    """Güncellemeden sonraki ilk açılışta kısa "vX sürümüne güncellendi" bildirimi (toast)."""
    old = state.updated_from
    if not old or old == get_version():
        return
    try:
        from app.resources.locales import t

        lang = getattr(window, "lang", None)
        window._flash(t("update_installed_toast", lang).format(app="Lumina", version=get_version()), msec=6000)
    except Exception:  # noqa: BLE001 — bildirim asla açılışı bozmaz
        pass
