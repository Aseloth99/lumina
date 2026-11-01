"""Uygulama ayarları (QSettings) tek yerden.

`QSettings("Lumina", "LuminaViewer")` kurucusu varsayılan biçimi YOK sayıp daima yerel biçimi (Windows'ta
kayıt defteri) kullanır; bu yüzden testlerin `QSettings.setDefaultFormat(IniFormat)` çağrısı etkisizdi ve
testler kullanıcının gerçek ayarlarını (pencere konumu, döngü, şerit…) bozuyordu. Biçim burada açıkça
`defaultFormat()` olarak verilir: üretimde yerel, testlerde (INI'ye yönlendirilmişse) yalıtılmış.
"""

from __future__ import annotations

from PyQt6.QtCore import QSettings

ORGANIZATION = "Lumina"
APPLICATION = "LuminaViewer"


def make_settings() -> QSettings:
    return QSettings(QSettings.defaultFormat(), QSettings.Scope.UserScope, ORGANIZATION, APPLICATION)
