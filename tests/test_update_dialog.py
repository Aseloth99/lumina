"""Güncelleme penceresi durumları: boşta -> indirme -> kurma -> hata -> tekrar dene; açılır kutu yok; Esc/X davranışı."""

from __future__ import annotations

import os
import pathlib
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QCoreApplication, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton, QTextEdit  # noqa: E402

from app.ui.message_box import MessageBox  # noqa: E402

from app.ui.update_dialog import UpdateDialog  # noqa: E402

LONG_NOTES = "Yenilikler:\n" + "\n".join(f"• Çok uzun bir sürüm notu satırı numara {i}: pencereye sığmayabilir" for i in range(40))


class DialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        for box in ("warning", "information", "question", "critical", "about"):
            patcher = mock.patch.object(MessageBox, box, side_effect=AssertionError("güncelleme penceresinde açılır kutu olmamalı"))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch("app.ui.update_dialog.supports_auto_install", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, notes="Notlar", mandatory=False, size=0, lang="tr", version="1.2.3") -> UpdateDialog:
        dialog = UpdateDialog(version, notes, mandatory, None, size=size, lang=lang)
        self.addCleanup(dialog.deleteLater)
        self.events: list[str] = []
        dialog.update_accepted.connect(lambda: self.events.append("accepted"))
        dialog.install_confirmed.connect(lambda: self.events.append("confirmed"))
        dialog.update_deferred.connect(lambda: self.events.append("deferred"))
        dialog.update_cancelled.connect(lambda: self.events.append("cancelled"))
        return dialog

    @staticmethod
    def shown(dialog: UpdateDialog) -> UpdateDialog:
        dialog.show()
        QCoreApplication.processEvents()
        return dialog

    # ---- içerik
    def test_idle_shows_version_size_notes_and_the_two_buttons_in_turkish(self):
        dialog = self.shown(self.make(size=12_500_000))
        labels = [w.text() for w in dialog.findChildren(type(dialog.size_label))]
        self.assertIn("Lumina Sürüm 1.2.3 Mevcut", labels)
        self.assertTrue(any("→ Yeni sürüm: 1.2.3" in text for text in labels))
        self.assertEqual(dialog.size_label.text(), "İndirme boyutu: 11.92 MB")
        self.assertFalse(dialog.size_label.isHidden())
        self.assertEqual((dialog.later_btn.text(), dialog.update_btn.text()), ("Daha Sonra", "Şimdi Güncelle"))
        self.assertEqual(dialog.state, "idle")
        self.assertTrue(dialog.cancel_btn.isHidden() and dialog.error_label.isHidden() and dialog.progress_bar.isHidden())

    def test_english(self):
        dialog = self.shown(self.make(size=2048, lang="en"))
        self.assertEqual(dialog.size_label.text(), "Download size: 2.00 KB")
        self.assertEqual((dialog.later_btn.text(), dialog.update_btn.text()), ("Later", "Update Now"))
        dialog.show_error("disk full")
        self.assertEqual(dialog.error_label.text(), "The update could not be completed: disk full")
        self.assertEqual(dialog.update_btn.text(), "Try Again")

    def test_unknown_size_hides_the_size_line(self):
        for size in (0, None, -1):
            self.assertTrue(self.shown(self.make(size=size)).size_label.isHidden())

    def test_size_above_four_gigabytes_is_shown_without_overflow(self):
        self.assertEqual(self.make(size=5 * 1024**3).size_label.text(), "İndirme boyutu: 5.00 GB")

    def test_long_notes_scroll_instead_of_growing_the_window(self):
        dialog = self.shown(self.make(notes=LONG_NOTES))
        box = dialog.findChild(QTextEdit)
        self.assertGreater(box.verticalScrollBar().maximum(), 0, "uzun notlar kaydırılır")
        self.assertLess(dialog.height(), 900)
        dialog.show_error("x " * 200)
        QCoreApplication.processEvents()
        self.assertGreater(box.height(), 40, "hata satırı varken de notlar görünür kalır")

    def test_html_and_plain_notes(self):
        self.assertIn("madde", self.make(notes="<ul><li>madde</li></ul>").findChild(QTextEdit).toPlainText())
        self.assertEqual(self.make(notes="a\nb").findChild(QTextEdit).toPlainText(), "a\nb")

    # ---- durum akışı
    def test_full_flow_idle_download_install(self):
        dialog = self.shown(self.make())
        dialog.update_btn.click()
        self.assertEqual(self.events, ["accepted"])
        self.assertEqual(dialog.state, "downloading")
        self.assertTrue(dialog.is_downloading)
        self.assertFalse(dialog.update_btn.isEnabled(), "ikinci indirme başlatılamaz")
        self.assertTrue(dialog.later_btn.isHidden())
        self.assertFalse(dialog.cancel_btn.isHidden(), "indirme sürerken İptal görünür")
        self.assertFalse(dialog.progress_bar.isHidden())
        dialog.update_btn.click()  # kilitli düğme: sinyal yok
        self.assertEqual(self.events, ["accepted"])
        dialog.update_download_progress(1024 * 1024, 4 * 1024 * 1024, 2.5)
        self.assertIn("25%", dialog.progress_label.text())
        self.assertIn("2.50 MB/s", dialog.progress_label.text())
        self.assertEqual(dialog.progress_bar.value(), 25)
        dialog.update_download_progress(512, 0, 0.0)  # toplam bilinmiyor: belirsiz çubuk
        self.assertEqual(dialog.progress_bar.maximum(), 0)
        dialog.show_download_complete()
        dialog.show_installing_state()
        self.assertEqual(dialog.state, "installing")
        self.assertEqual(dialog.status_label.text(), "Kuruluyor, lütfen bekleyin…")
        self.assertTrue(dialog.update_btn.isHidden() and dialog.later_btn.isHidden() and dialog.cancel_btn.isHidden())
        dialog.show_install_complete()
        self.assertIn("yeniden başlatılıyor", dialog.status_label.text())

    def test_progress_handles_sizes_beyond_32_bits(self):
        dialog = self.make()
        dialog.update_download_progress(5 * 1024**3, 6 * 1024**3, 1.0)
        self.assertIn("83%", dialog.progress_label.text())

    def test_error_is_inline_keeps_the_window_open_and_retry_works(self):
        dialog = self.shown(self.make())
        dialog.update_btn.click()
        dialog.show_error("Dosya bütünlüğü doğrulanamadı (checksum).")
        self.assertEqual(dialog.state, "error")
        self.assertFalse(dialog.error_label.isHidden())
        self.assertEqual(dialog.error_label.text(),
                         "Güncelleme tamamlanamadı: Dosya bütünlüğü doğrulanamadı (checksum).")
        self.assertTrue(dialog.progress_bar.isHidden() and dialog.progress_label.isHidden() and dialog.cancel_btn.isHidden())
        self.assertEqual(dialog.update_btn.text(), "Tekrar Dene")
        self.assertTrue(dialog.update_btn.isEnabled())
        self.assertFalse(dialog.later_btn.isHidden())
        self.assertTrue(dialog.isVisible(), "hata pencereyi kapatmaz")
        self.assertFalse(dialog.is_downloading or dialog.is_installing)
        dialog.update_btn.click()
        self.assertEqual(self.events, ["accepted", "accepted"], "tekrar dene yeniden başlatır")
        self.assertEqual(dialog.state, "downloading")
        self.assertTrue(dialog.error_label.isHidden(), "yeni deneme eski hatayı temizler")

    def test_error_text_is_reddish_in_both_themes_and_not_clipped(self):
        for theme in ("dark", "light"):
            dialog = self.make()
            dialog._theme = theme
            self.shown(dialog)
            dialog.show_error("Lumina'nın bulunduğu klasöre yazılamıyor (C:/Program Files/Lumina). Uygulamayı taşıyın. " * 3)
            QCoreApplication.processEvents()
            color = dialog.error_label.styleSheet()
            self.assertTrue("#f87171" in color if theme == "dark" else "#b91c1c" in color, (theme, color))
            label = dialog.error_label
            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()) - 1, "hata metni kırpılmıyor")
            self.assertLessEqual(label.geometry().bottom(), dialog.height(), "pencerenin içinde")

    def test_unverified_package_asks_inline(self):
        dialog = self.shown(self.make())
        dialog.update_btn.click()
        dialog.show_unverified()
        self.assertEqual(dialog.state, "unverified")
        self.assertIn("sağlama toplamı", dialog.error_label.text())
        self.assertEqual(dialog.update_btn.text(), "Yine de Kur")
        dialog.update_btn.click()
        self.assertEqual(self.events[-1], "confirmed")

    def test_no_message_box_in_any_state(self):
        dialog = self.shown(self.make())
        dialog.update_btn.click()
        dialog.update_download_progress(1, 2, 0.1)
        dialog.show_error("x")
        dialog.show_unverified()
        dialog.show_installing_state()
        dialog.show_install_complete()  # (açılır kutu çağrısı AssertionError verirdi)

    # ---- kapatma
    def test_escape_while_downloading_cancels_the_download_and_closes(self):
        dialog = self.shown(self.make())
        dialog.update_btn.click()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        self.assertEqual(self.events, ["accepted", "cancelled"])
        self.assertFalse(dialog.isVisible())
        self.assertFalse(dialog.is_downloading)

    def test_close_button_while_downloading_cancels_too_and_cancel_button_closes(self):
        dialog = self.shown(self.make())
        dialog.update_btn.click()
        dialog.close()  # başlıktaki X
        self.assertEqual(self.events, ["accepted", "cancelled"])
        self.assertFalse(dialog.isVisible())
        dialog = self.shown(self.make())
        dialog.update_btn.click()
        dialog.cancel_btn.click()
        self.assertEqual(self.events, ["accepted", "cancelled"])
        self.assertFalse(dialog.isVisible())

    def test_later_and_escape_when_idle_defer_once(self):
        dialog = self.shown(self.make())
        dialog.later_btn.click()
        self.assertEqual(self.events, ["deferred"])
        dialog = self.shown(self.make())
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        self.assertEqual(self.events, ["deferred"])
        dialog = self.shown(self.make())
        dialog.close()
        self.assertEqual(self.events, ["deferred"])

    def test_closing_is_refused_while_installing_until_the_app_is_quitting(self):
        dialog = self.shown(self.make())
        dialog.update_btn.click()
        dialog.show_installing_state()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        dialog.close()
        self.assertTrue(dialog.isVisible(), "kurulum sürerken kapatılamaz")
        self.assertEqual(self.events, ["accepted"], "iptal/ertele sinyali de yok")
        dialog.allow_close()
        dialog.close()
        self.assertFalse(dialog.isVisible())

    def test_mandatory_has_no_later_button_but_can_be_retried_and_closed(self):
        dialog = self.shown(self.make(mandatory=True))
        self.assertTrue(dialog.later_btn.isHidden())
        dialog.update_btn.click()
        dialog.show_error("ağ")
        self.assertTrue(dialog.later_btn.isHidden(), "zorunlu güncellemede 'Daha sonra' hatadan sonra da yok")
        self.assertEqual(dialog.update_btn.text(), "Tekrar Dene")
        dialog.reject()
        self.assertEqual(self.events, ["accepted"], "zorunluda ertelendi sayılmaz")

    def test_non_windows_button_opens_the_download_page_and_accepts(self):
        with mock.patch("app.ui.update_dialog.supports_auto_install", return_value=False):
            dialog = self.shown(self.make())
            self.assertEqual(dialog.update_btn.text(), "İndirme Sayfasını Aç")
            dialog.update_btn.click()
        self.assertEqual(self.events, ["accepted"])
        self.assertFalse(dialog.isVisible())
        self.assertEqual(dialog.state, "idle", "indirme durumuna geçmez")

    def test_buttons_never_overlap_in_any_state(self):
        dialog = self.shown(self.make(notes=LONG_NOTES, size=1000))
        for step in (lambda: None, lambda: dialog.update_btn.click(), lambda: dialog.show_error("x " * 80),
                     lambda: dialog.show_unverified()):
            step()
            QCoreApplication.processEvents()
            buttons = [b for b in dialog.findChildren(QPushButton) if not b.isHidden()]
            for i, a in enumerate(buttons):
                self.assertTrue(dialog.rect().contains(a.geometry()), a.text())
                for b in buttons[i + 1:]:
                    self.assertFalse(a.geometry().intersects(b.geometry()), (a.text(), b.text()))


if __name__ == "__main__":
    unittest.main()
