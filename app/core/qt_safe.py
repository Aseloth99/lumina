"""Qt sinyal yardımcıları."""

from __future__ import annotations


def safe_emit(owner, name: str, *args) -> bool:
    """Arka plan işçisinden `owner.<name>` sinyalini yay.

    `owner` (ör. işçinin `signals` QObject'i) uygulama kapanırken, havuz iş parçacığı hâlâ
    çalışırken çoktan silinmiş olabilir; bu durumda hem `owner.<name>` erişimi hem `emit`
    `RuntimeError` verir. İkisi de burada korunur ve False döner. (Eskiden bu hata çökme
    günlüğüne binlerce kez "wrapped C/C++ object ... has been deleted" diye yazılıyordu.)

    Not: sinyalin kendisi değil, SAHİBİ ve ADI verilir — `self.signals.loaded` ifadesi
    çağrıdan önce değerlendirildiği için silinmiş nesnede zaten patlardı.
    """
    try:
        from PyQt6 import sip

        if sip.isdeleted(owner):
            return False
    except Exception:
        pass
    try:
        getattr(owner, name).emit(*args)
        return True
    except RuntimeError:
        return False
