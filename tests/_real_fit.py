"""GERÇEK platformda (offscreen DEĞİL) sığma denetimi: pencere x=-6000'de gösterilir, ekranda görünmez.

test_message_box.RealPlatformFit tarafından alt süreç olarak çalıştırılır; sorun yoksa son satır "FIT-OK" olur.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.ui import message_box as mb  # noqa: E402
from _fitcheck import audit_box  # noqa: E402
from app.ui.message_box import MessageBox  # noqa: E402

app = QApplication([])
if app.platformName() == "offscreen":
    print("gerçek platform gerekir")
    os._exit(3)

PALETTES = {
    "dark": {"base": "#1a1a1a", "raised": "#2a2a2a", "surface": "#252525", "border": "#1a1a1a", "hi": "#3a3a3a",
             "lo": "#0f0f0f", "text": "#e8e8e8", "text_muted": "#9e9e9e", "accent": "#00ACC1", "accent2": "#00838F",
             "danger": "#d32f2f", "warn": "#FFC107"},
    "light": {"base": "#e0e0e0", "raised": "#f5f5f5", "surface": "#ececec", "border": "#c5c5c5", "hi": "#ffffff",
              "lo": "#bdbdbd", "text": "#1a1a1a", "text_muted": "#616161", "accent": "#00ACC1", "accent2": "#00838F",
              "danger": "#e53935", "warn": "#8A5300"},
}
STATE = {"theme": "dark", "lang": "tr"}
mb.configure(palette_provider=lambda: PALETTES[STATE["theme"]], lang_provider=lambda: STATE["lang"])


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


TEXTS = {
    "kısa": "Tamam mı?",
    "iki satır": "Dosya sıkıştırılırken bir sorun oluştu ve işlem yarıda kesildi, lütfen tekrar deneyin.",
    "yol": "Açılamadı: C:\\Users\\kullanici\\Belgeler\\Projeler\\çok_uzun_klasör_adı\\tatil_videosu_2026_kopya_son_hali_FINAL_v7.mp4",
    "boşluksuz": "A" * 400,
    "çok uzun": "\n".join(f"{i}. satır: kesilmeden görünmesi gereken oldukça uzun bir ileti satırı örneği burada." for i in range(60)),
    "zengin": "<b>Kalın</b> başlık<br>ikinci satır <i>eğik</i> ve <a href='https://example.com'>bağlantı</a>",
}

issues = []
count = 0
for theme in ("dark", "light"):
    for lang in ("tr", "en"):
        STATE.update(theme=theme, lang=lang)
        for name, text in TEXTS.items():
            for kind, buttons in ((MessageBox.Icon.Information, MessageBox.StandardButton.Ok),
                                  (MessageBox.Icon.Warning, MessageBox.StandardButton.Save | MessageBox.StandardButton.Discard | MessageBox.StandardButton.Cancel),
                                  (MessageBox.Icon.Question, MessageBox.StandardButton.Yes | MessageBox.StandardButton.No),
                                  (MessageBox.Icon.NoIcon, MessageBox.StandardButton.Ok)):
                for extra in ("", "ek"):
                    box = MessageBox()
                    box.setWindowTitle("Medya Sıkıştırıcı — " + name)
                    box.setIcon(kind)
                    box.setText(text)
                    if extra:
                        box.setInformativeText("Ayrıntılar için günlük dosyasına bakabilirsiniz. " * 3)
                        box.setDetailedText("satır\n" * 30)
                    box.setStandardButtons(buttons)
                    box.prepare()
                    box.move(-6000, 0)
                    box.show()
                    wait(40)
                    avail = box.screen().availableGeometry()
                    tag = f"{theme}/{lang}/{name}/{kind.name}/{extra or '-'}"
                    issues += audit_box(box, avail.height(), avail.width(), tag)
                    if box.frameGeometry().x() > -3000:
                        issues.append(f"[{tag}] pencere ekranda görünür konumda: x={box.frameGeometry().x()}")
                    if extra:
                        box._toggle_details()
                        wait(30)
                        issues += audit_box(box, avail.height(), avail.width(), tag + "/ayrıntı açık")
                    box.close()
                    count += 1
print(f"{count} kutu denetlendi")
if issues:
    print("\n".join(issues[:60]))
    sys.stdout.flush()
    os._exit(1)
print("FIT-OK")
sys.stdout.flush()
os._exit(0)
