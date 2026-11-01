"""message_box.py birim testleri (offscreen). Gerçek platform sığma denetimi RealPlatformFit'te alt süreçle yapılır."""

import os
import subprocess
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QGuiApplication, QIcon, QMouseEvent, QPixmap  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QAbstractButton, QApplication, QLineEdit, QMessageBox, QPushButton, QWidget  # noqa: E402

from app.ui import message_box as mb  # noqa: E402
from _fitcheck import audit_box  # noqa: E402
from app.ui.message_box import InputDialog, MessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])
SB = MessageBox.StandardButton
BR = MessageBox.ButtonRole

MC_DARK = {"base": "#1a1a1a", "raised": "#2a2a2a", "sunken": "#1f1f1f", "surface": "#252525", "border": "#1a1a1a",
           "hi": "#3a3a3a", "lo": "#0f0f0f", "text": "#e8e8e8", "text_muted": "#9e9e9e", "accent": "#00ACC1",
           "accent2": "#00838F", "danger": "#d32f2f", "warn": "#FFC107"}
MC_LIGHT = {"base": "#e0e0e0", "raised": "#f5f5f5", "sunken": "#d8d8d8", "surface": "#ececec", "border": "#c5c5c5",
            "hi": "#ffffff", "lo": "#bdbdbd", "text": "#1a1a1a", "text_muted": "#616161", "accent": "#00ACC1",
            "accent2": "#00838F", "danger": "#e53935", "warn": "#8A5300"}
FK_DARK = {"base": "#161616", "surface": "#1e1e1e", "surface2": "#262626", "border": "#2e2e2e", "text": "#f5f5f5",
           "text_muted": "#9c9c9c", "danger": "#ef4444", "warn": "#f59e0b", "hi": "#3c3c3c", "lo": "#0e0e0e",
           "accent": "#00ACC1", "accent2": "#00838F", "is_dark": True}


def wait_closed(box, ms: int = 5000) -> None:
    """Güvenlik: test takılırsa kutuyu zorla kapat. (Kutu o zamana dek silinmiş olabilir; olay döngüsü başka bir testte
    işlenirken süresi dolmuş zamanlayıcı silinmiş nesneye dokunursa PyQt6 süreci abort ediyordu: RuntimeError yutulur.)"""

    def force() -> None:
        try:
            if box.isVisible():
                box.done(-99)
        except RuntimeError:  # C++ nesnesi zaten silinmiş
            pass

    QTimer.singleShot(ms, force)


def exec_with(box, action, delay: int = 0):
    QTimer.singleShot(delay, action)
    wait_closed(box)
    return box.exec()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        mb.reset_configuration()
        self.state = {"theme": MC_DARK, "lang": "tr"}
        mb.configure(palette_provider=lambda: self.state["theme"], lang_provider=lambda: self.state["lang"])
        self.addCleanup(mb.reset_configuration)

    def box(self, text="Merhaba", icon=None, buttons=SB.Ok, parent=None, title="Başlık") -> MessageBox:
        box = MessageBox(parent)
        box.setWindowTitle(title)
        box.setText(text)
        if icon is not None:
            box.setIcon(icon)
        box.setStandardButtons(buttons)
        self.addCleanup(box.deleteLater)
        return box

    @staticmethod
    def texts(box) -> list[str]:
        return [b.text() for b in box.buttons()]


class ConfigurationTests(Base):
    def test_unconfigured_defaults_are_neutral_grey_with_blue_accent(self) -> None:
        mb.reset_configuration()
        for dark, base in ((True, "#1a1a1a"), (False, "#e0e0e0")):
            with mock.patch.object(mb, "_system_is_dark", return_value=dark):
                pal = mb.current_palette()
            self.assertEqual(pal["base"], base)
            self.assertEqual(pal["accent"], "#2196f3")
            self.assertEqual(pal["is_dark"], dark)

    def test_mediacompressor_style_palette_is_used(self) -> None:
        pal = mb.current_palette()
        self.assertEqual((pal["base"], pal["raised"], pal["accent"], pal["accent2"]),
                         ("#1a1a1a", "#2a2a2a", "#00acc1", "#00838f"))
        self.assertTrue(pal["is_dark"])
        self.state["theme"] = MC_LIGHT
        pal = mb.current_palette()
        self.assertFalse(pal["is_dark"])
        self.assertEqual(pal["text"], "#1a1a1a")

    def test_palette_is_read_every_time_so_theme_switch_applies(self) -> None:
        first = self.box()
        first.prepare()
        self.state["theme"] = MC_LIGHT
        second = self.box()
        second.prepare()
        self.assertTrue(first._pal["is_dark"])
        self.assertFalse(second._pal["is_dark"])
        self.assertIn("#f5f5f5", second.styleSheet())

    def test_flexikey_style_palette_without_raised_key(self) -> None:
        self.state["theme"] = FK_DARK
        pal = mb.current_palette()
        self.assertEqual(pal["raised"], "#262626")  # surface2
        self.assertEqual(pal["surface"], "#1e1e1e")
        self.assertTrue(pal["is_dark"])
        self.assertEqual(pal["border"], "#2e2e2e")

    def test_accent_provider_overrides_and_derives_shades(self) -> None:
        mb.configure(accent_provider=lambda: "#E91E63")
        pal = mb.current_palette()
        self.assertEqual(pal["accent"], "#e91e63")
        self.assertNotEqual(pal["accent2"], MC_DARK["accent2"])
        mb.configure(accent_provider=lambda: "bozuk renk")
        self.assertEqual(mb.current_palette()["accent"], "#00acc1")

    def test_broken_providers_fall_back_to_defaults(self) -> None:
        mb.configure(palette_provider=lambda: 1 / 0)
        pal = mb.current_palette()
        self.assertEqual(pal["accent"], "#2196f3")
        mb.configure(palette_provider=lambda: {"base": "yok", "text": 5, "accent": None})
        self.assertEqual(mb.current_palette()["accent"], "#2196f3")
        mb.configure(palette_provider=lambda: "sözlük değil")
        box = self.box()
        box.prepare()  # çökmemeli
        self.assertTrue(box._built)

    def test_invisible_border_gets_a_visible_edge(self) -> None:
        pal = mb.current_palette()  # MC koyu: border == base
        self.assertNotEqual(pal["edge"], pal["base"])

    def test_on_accent_is_dark_for_yellow_and_white_for_teal(self) -> None:
        self.state["theme"] = dict(MC_DARK, accent="#F9A825", accent2="#F57F17")
        self.assertEqual(mb.current_palette()["on_accent"], "#1a1a1a")
        self.state["theme"] = MC_DARK
        self.assertEqual(mb.current_palette()["on_accent"], "#ffffff")

    def test_reset_configuration_clears_everything(self) -> None:
        mb.configure(app_icon_provider=lambda: QIcon(), button_texts={"ok": "Çok Tamam"})
        mb.reset_configuration()
        self.assertTrue(all(v is None for v in mb._CFG.values()))
        self.assertIsNone(mb._TEST_HOOK)

    def test_app_icon_provider_feeds_the_title_bar(self) -> None:
        pix = QPixmap(32, 32)
        pix.fill(Qt.GlobalColor.red)
        mb.configure(app_icon_provider=lambda: QIcon(pix))
        box = self.box()
        box.prepare()
        label = box.findChild(QWidget, "mb_title_icon")
        self.assertIsNotNone(label)
        self.assertEqual((label.width(), label.height()), (16, 16))
        box.setWindowIcon(QIcon())
        mb.configure(app_icon_provider=lambda: None)
        mb.reset_configuration()
        mb.configure(palette_provider=lambda: MC_DARK)
        box2 = self.box()
        box2.prepare()
        if QApplication.windowIcon().isNull():
            self.assertIsNone(box2.findChild(QWidget, "mb_title_icon"))


class LocalizationTests(Base):
    def test_default_table_tr_and_en(self) -> None:
        expected = {SB.Ok: ("Tamam", "OK"), SB.Cancel: ("İptal", "Cancel"), SB.Yes: ("Evet", "Yes"),
                    SB.No: ("Hayır", "No"), SB.Close: ("Kapat", "Close"), SB.Save: ("Kaydet", "Save"),
                    SB.Retry: ("Tekrar Dene", "Retry")}
        for std, (tr, en) in expected.items():
            self.assertEqual(mb.button_text(std, "tr"), tr)
            self.assertEqual(mb.button_text(std, "en"), en)
        self.assertEqual(mb.button_text("delete", "tr"), "Sil")
        self.assertEqual(mb.button_text("delete", "en"), "Delete")

    def test_every_word_is_capitalised(self) -> None:
        small = {"to"}
        for lang, table in mb._TEXTS.items():
            for key, text in table.items():
                if key in ("copy", "select_all", "close_tip", "details_show", "details_hide") or True:
                    for word in text.split():
                        if word.lower() in small:
                            continue
                        self.assertTrue(word[0].isupper(), f"{lang}.{key}: {text!r}")

    def test_box_uses_language_provider_and_updates_on_next_build(self) -> None:
        box = self.box(buttons=SB.Yes | SB.No | SB.Cancel)
        box.prepare()
        self.assertEqual(self.texts(box), ["Evet", "Hayır", "İptal"])
        self.state["lang"] = "en"
        box2 = self.box(buttons=SB.Yes | SB.No | SB.Cancel)
        box2.prepare()
        self.assertEqual(self.texts(box2), ["Yes", "No", "Cancel"])
        box.setText("yeniden kur")  # kirlenir; yeni dil uygulanır
        box.prepare()
        self.assertEqual(self.texts(box), ["Yes", "No", "Cancel"])

    def test_language_codes(self) -> None:
        for code, want in (("tr", "tr"), ("tr_TR", "tr"), ("TR", "tr"), ("en", "en"), ("de", "en"), ("", None),
                           ("auto", None)):
            self.state["lang"] = code
            if want is None:
                for system, lang in (("tr_TR", "tr"), ("en_US", "en")):
                    fake = mock.Mock()
                    fake.name.return_value = system
                    with mock.patch.object(mb.QLocale, "system", return_value=fake):
                        self.assertEqual(mb._lang(), lang)
            else:
                self.assertEqual(mb._lang(), want)

    def test_button_texts_callable_and_mapping_override(self) -> None:
        mb.configure(button_texts=lambda std: "Peki" if std == SB.Ok else None)
        self.assertEqual(mb.button_text(SB.Ok), "Peki")
        self.assertEqual(mb.button_text(SB.Cancel, "tr"), "İptal")
        mb.configure(button_texts={SB.Yes: "Elbette", "no": "Asla"})
        self.assertEqual(mb.button_text(SB.Yes), "Elbette")
        self.assertEqual(mb.button_text(SB.No), "Asla")
        mb.configure(button_texts=lambda std: 1 / 0)
        self.assertEqual(mb.button_text(SB.Ok, "tr"), "Tamam")  # bozuk sağlayıcı çökertmez

    def test_details_toggle_and_close_tooltip_are_localised(self) -> None:
        box = self.box()
        box.setDetailedText("ayrıntı")
        box.prepare()
        self.assertEqual(box._link_btn.text(), "Ayrıntıları Göster")
        box._toggle_details()
        self.assertEqual(box._link_btn.text(), "Ayrıntıları Gizle")
        self.assertEqual(box.findChild(QAbstractButton, "mb_close").toolTip(), "Kapat")
        self.state["lang"] = "en"
        box2 = self.box()
        box2.setDetailedText("x")
        box2.prepare()
        self.assertEqual(box2._link_btn.text(), "Show Details")
        self.assertEqual(box2.findChild(QAbstractButton, "mb_close").toolTip(), "Close")


class ButtonTests(Base):
    def test_standard_buttons_roundtrip_and_order(self) -> None:
        box = self.box(buttons=SB.Cancel | SB.No | SB.Yes)
        self.assertEqual(box.standardButtons(), SB.Yes | SB.No | SB.Cancel)
        self.assertEqual([box.standardButton(b) for b in box.buttons()], [SB.Yes, SB.No, SB.Cancel])
        box.setStandardButtons(SB.Save | SB.Discard | SB.Cancel)
        self.assertEqual([box.standardButton(b) for b in box.buttons()], [SB.Save, SB.Discard, SB.Cancel])
        self.assertIsNone(box.button(SB.Yes))
        self.assertIsInstance(box.button(SB.Save), QPushButton)

    def test_roles_follow_qt_mapping(self) -> None:
        box = self.box(buttons=SB.Ok | SB.Cancel | SB.Yes | SB.No | SB.Discard | SB.Help | SB.Apply | SB.Retry)
        expect = {SB.Ok: BR.AcceptRole, SB.Cancel: BR.RejectRole, SB.Yes: BR.YesRole, SB.No: BR.NoRole,
                  SB.Discard: BR.DestructiveRole, SB.Help: BR.HelpRole, SB.Apply: BR.ApplyRole,
                  SB.Retry: BR.AcceptRole}
        for std, role in expect.items():
            self.assertEqual(box.buttonRole(box.button(std)), role, std)

    def test_add_button_variants_and_custom_order(self) -> None:
        box = self.box(buttons=SB.NoButton)
        later = box.addButton("Sonra", BR.RejectRole)
        now = box.addButton("Şimdi Güncelle", BR.AcceptRole)
        own = QPushButton("Kendi Düğmem")
        added = box.addButton(own, BR.ActionRole)
        self.assertIs(added, own)
        self.assertIs(box.addButton(SB.Ok), box.addButton(SB.Ok))  # aynı standart düğme iki kez eklenmez
        self.assertEqual(box.standardButton(now), SB.NoButton)
        self.assertEqual(box.buttonRole(later), BR.RejectRole)
        self.assertEqual(self.texts(box), ["Şimdi Güncelle", "Tamam", "Kendi Düğmem", "Sonra"])  # Kabul grubunda eklenme sırası
        box.removeButton(own)
        self.assertNotIn("Kendi Düğmem", self.texts(box))

    def test_constructor_overloads(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        a = MessageBox(parent)
        b = MessageBox(MessageBox.Icon.Warning, "T", "Metin", SB.Ok | SB.Cancel, parent)
        c = MessageBox(parent=parent)
        for box in (a, b, c):
            self.assertIs(box.parentWidget(), parent)
        self.assertEqual((b.icon(), b.windowTitle(), b.text()), (MessageBox.Icon.Warning, "T", "Metin"))
        self.assertEqual(b.standardButtons(), SB.Ok | SB.Cancel)

    def test_enum_aliases_are_qmessagebox_enums(self) -> None:
        self.assertIs(MessageBox.StandardButton, QMessageBox.StandardButton)
        self.assertIs(MessageBox.Icon, QMessageBox.Icon)
        self.assertIs(MessageBox.ButtonRole, QMessageBox.ButtonRole)
        self.assertIs(mb.StandardButton, QMessageBox.StandardButton)

    def test_default_button_resolution(self) -> None:
        box = self.box(buttons=SB.Yes | SB.No)
        self.assertIs(box.defaultButton(), box.button(SB.Yes))  # Qt gibi ilk onaylayan
        box.setDefaultButton(SB.No)
        self.assertIs(box.defaultButton(), box.button(SB.No))
        box.setDefaultButton(box.button(SB.Yes))
        self.assertIs(box.defaultButton(), box.button(SB.Yes))
        box.setDefaultButton(SB.Cancel)  # kutuda yok -> yok sayılır
        self.assertIsNone(box._default)

    def test_escape_resolution(self) -> None:
        cases = [
            (SB.Ok, SB.Ok), (SB.Yes | SB.No, SB.No), (SB.Yes | SB.No | SB.Cancel, SB.Cancel),
            (SB.Save | SB.Discard | SB.Cancel, SB.Cancel), (SB.Retry | SB.Close, SB.Close),
            (SB.Ok | SB.Cancel, SB.Cancel),
        ]
        for buttons, want in cases:
            with self.subTest(buttons=buttons):
                box = self.box(buttons=buttons)
                self.assertIs(box.escapeButton(), box.button(want))
        box = self.box(buttons=SB.Yes | SB.No)
        box.setEscapeButton(SB.Yes)
        self.assertIs(box.escapeButton(), box.button(SB.Yes))
        custom = self.box(buttons=SB.NoButton)
        custom.addButton("Şimdi", BR.AcceptRole)
        later = custom.addButton("Sonra", BR.RejectRole)
        self.assertIs(custom.escapeButton(), later)
        ambiguous = self.box(buttons=SB.NoButton)
        ambiguous.addButton("A", BR.AcceptRole)
        ambiguous.addButton("B", BR.AcceptRole)
        self.assertIsNone(ambiguous.escapeButton())

    def test_primary_destructive_and_normal_variants(self) -> None:
        box = self.box(buttons=SB.Save | SB.Discard | SB.Cancel)
        box.prepare()
        variants = {box.standardButton(b): b.property("variant") for b in box.buttons()}
        self.assertEqual(variants, {SB.Save: "primary", SB.Discard: "danger", SB.Cancel: ""})
        question = self.box(buttons=SB.Yes | SB.No)
        question.setDefaultButton(SB.No)
        question.prepare()
        self.assertEqual(question.button(SB.No).property("variant"), "primary")
        self.assertEqual(question.button(SB.Yes).property("variant"), "")


class KeyboardAndCloseTests(Base):
    def test_enter_clicks_default_button(self) -> None:
        box = self.box(buttons=SB.Yes | SB.No)
        box.setDefaultButton(SB.No)
        code = exec_with(box, lambda: QTest.keyClick(box, Qt.Key.Key_Return))
        self.assertEqual(code, int(SB.No))
        self.assertIs(box.clickedButton(), box.button(SB.No))
        for key in (Qt.Key.Key_Enter,):
            box2 = self.box(buttons=SB.Yes | SB.No)
            self.assertEqual(exec_with(box2, lambda k=key, b=box2: QTest.keyClick(b, k)), int(SB.Yes))

    def test_enter_clicks_the_focused_button_when_another_is_focused(self) -> None:
        box = self.box(buttons=SB.Yes | SB.No)

        def act():
            box.button(SB.No).setFocus()
            QTest.keyClick(box, Qt.Key.Key_Return)

        self.assertEqual(exec_with(box, act), int(SB.No))

    def test_escape_picks_cancel_no_close_or_the_single_button(self) -> None:
        for buttons, want in ((SB.Yes | SB.No | SB.Cancel, SB.Cancel), (SB.Yes | SB.No, SB.No), (SB.Ok, SB.Ok),
                              (SB.Save | SB.Discard | SB.Cancel, SB.Cancel)):
            box = self.box(buttons=buttons)
            with self.subTest(buttons=buttons):
                self.assertEqual(exec_with(box, lambda b=box: QTest.keyClick(b, Qt.Key.Key_Escape)), int(want))
                self.assertEqual(box.standardButton(box.clickedButton()), want)

    def test_escape_with_no_resolvable_button_returns_minus_one_and_no_clicked_button(self) -> None:
        box = self.box(buttons=SB.NoButton)
        box.addButton("A", BR.AcceptRole)
        box.addButton("B", BR.AcceptRole)
        self.assertEqual(exec_with(box, lambda: QTest.keyClick(box, Qt.Key.Key_Escape)), -1)
        self.assertIsNone(box.clickedButton())

    def test_title_close_button_acts_like_escape(self) -> None:
        box = self.box(buttons=SB.Yes | SB.No)
        close = lambda: box.findChild(QAbstractButton, "mb_close").click()  # noqa: E731
        self.assertEqual(exec_with(box, close), int(SB.No))

    def test_clicking_buttons_and_exec_codes(self) -> None:
        box = self.box(buttons=SB.Yes | SB.No | SB.Cancel)
        self.assertEqual(exec_with(box, lambda: box.button(SB.Yes).click()), int(SB.Yes))
        custom = self.box(buttons=SB.NoButton)
        first = custom.addButton("Bir", BR.ActionRole)
        second = custom.addButton("İki", BR.ActionRole)
        self.assertEqual(exec_with(custom, second.click), 1)  # özel düğme: eklenme sırası (Qt kuralı)
        self.assertIs(custom.clickedButton(), second)
        self.assertEqual(MessageBox._exec_code_for(custom, first), 0)
        mapped = self.box(buttons=SB.Abort | SB.YesToAll | SB.NoToAll | SB.Close)
        self.assertEqual(MessageBox._exec_code_for(mapped, mapped.button(SB.Abort)), int(SB.Cancel))
        self.assertEqual(MessageBox._exec_code_for(mapped, mapped.button(SB.Close)), int(SB.Cancel))
        self.assertEqual(MessageBox._exec_code_for(mapped, mapped.button(SB.YesToAll)), int(SB.Yes))
        self.assertEqual(MessageBox._exec_code_for(mapped, mapped.button(SB.NoToAll)), int(SB.No))

    def test_button_clicked_signal(self) -> None:
        box = self.box(buttons=SB.Ok)
        seen = []
        box.buttonClicked.connect(seen.append)
        exec_with(box, lambda: box.button(SB.Ok).click())
        self.assertEqual(seen, [box.button(SB.Ok)])

    def test_exec_clears_previous_result(self) -> None:
        box = self.box(buttons=SB.Yes | SB.No)
        exec_with(box, lambda: box.button(SB.Yes).click())
        exec_with(box, lambda: QTest.keyClick(box, Qt.Key.Key_Escape))
        self.assertIs(box.clickedButton(), box.button(SB.No))

    def test_ctrl_c_copies_all_text(self) -> None:
        box = self.box("Ana metin", title="Başlık")
        box.setInformativeText("Ek metin")
        box.prepare()
        QApplication.clipboard().clear()
        QTest.keyClick(box, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(QApplication.clipboard().text(), "Başlık\n\nAna metin\n\nEk metin")

    def test_title_bar_drag_moves_the_window(self) -> None:
        box = self.box()
        box.prepare()
        box.move(100, 100)
        bar = box.findChild(QWidget, "mb_title")
        origin = bar.mapToGlobal(QPoint(60, 10))

        def send(kind, offset, button, buttons):
            glob = QPointF(origin.x() + offset[0], origin.y() + offset[1])
            local = QPointF(60 + offset[0], 10 + offset[1])
            event = QMouseEvent(kind, local, glob, button, buttons, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(bar, event)

        before = box.pos()
        send(QEvent.Type.MouseButtonPress, (0, 0), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        send(QEvent.Type.MouseMove, (40, 25), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        self.assertEqual(box.pos(), before + QPoint(40, 25))
        send(QEvent.Type.MouseButtonRelease, (40, 25), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        send(QEvent.Type.MouseMove, (90, 90), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)  # bırakıldıktan sonra sürüklenmez
        self.assertEqual(box.pos(), before + QPoint(40, 25))


class TestHookTests(Base):
    def test_hook_prevents_real_window_and_receives_prepared_box(self) -> None:
        seen = []

        def hook(box):
            seen.append((box.isVisible(), box.text(), box.windowTitle(), box._built, self.texts(box)))
            return SB.Yes

        MessageBox.set_test_hook(hook)
        box = self.box("Silinsin mi?", MessageBox.Icon.Question, SB.Yes | SB.No, title="Onay")
        self.assertEqual(box.exec(), int(SB.Yes))
        self.assertEqual(seen, [(False, "Silinsin mi?", "Onay", True, ["Evet", "Hayır"])])
        self.assertIs(box.clickedButton(), box.button(SB.Yes))
        self.assertFalse(box.isVisible())

    def test_hook_results(self) -> None:
        box = self.box(buttons=SB.Yes | SB.No | SB.Cancel)
        custom = box.addButton("Özel", BR.ActionRole)
        for result, want_clicked in ((SB.No, box.button(SB.No)), (custom, custom), (None, box.button(SB.Cancel))):
            MessageBox.set_test_hook(lambda b, r=result: r)
            box.exec()
            self.assertIs(box.clickedButton(), want_clicked)
        MessageBox.set_test_hook(lambda b: SB.Ok)  # kutuda olmayan düğme: sonuç yine de iletilir
        self.assertEqual(box.exec(), int(SB.Ok))
        self.assertEqual(box.result_button(), SB.Ok)
        self.assertIsNone(box.clickedButton())

    def test_hook_can_be_removed(self) -> None:
        MessageBox.set_test_hook(lambda b: SB.Ok)
        MessageBox.set_test_hook(None)
        box = self.box(buttons=SB.Ok)
        self.assertEqual(exec_with(box, lambda: box.button(SB.Ok).click()), int(SB.Ok))

    def test_hook_works_for_static_helpers(self) -> None:
        calls = []

        def hook(box):
            calls.append((box.icon(), box.windowTitle(), box.text(), box.standardButtons(),
                          box.standardButton(box.defaultButton())))
            return SB.Yes if box.icon() == MessageBox.Icon.Question else SB.Ok

        MessageBox.set_test_hook(hook)
        self.assertEqual(MessageBox.question(None, "Soru", "Emin misiniz?"), SB.Yes)
        self.assertEqual(MessageBox.information(None, "Bilgi", "Bitti"), SB.Ok)
        self.assertEqual(MessageBox.warning(None, "Uyarı", "Dikkat", SB.Ok | SB.Cancel, SB.Cancel), SB.Ok)
        self.assertEqual(MessageBox.critical(None, "Hata", "Oldu", default=SB.Ok), SB.Ok)
        self.assertEqual([c[0] for c in calls], [MessageBox.Icon.Question, MessageBox.Icon.Information,
                                                 MessageBox.Icon.Warning, MessageBox.Icon.Critical])
        self.assertEqual(calls[0][3], SB.Yes | SB.No)
        self.assertEqual(calls[2][4], SB.Cancel)  # defaultButton konumsal
        self.assertEqual(calls[3][4], SB.Ok)  # default anahtar sözcüğü

    def test_static_returns_standard_button_even_for_escape(self) -> None:
        MessageBox.set_test_hook(lambda b: None)
        self.assertEqual(MessageBox.question(None, "T", "x"), SB.No)
        self.assertEqual(MessageBox.question(None, "T", "x", SB.Yes | SB.No | SB.Cancel), SB.Cancel)
        self.assertEqual(MessageBox.information(None, "T", "x"), SB.Ok)

    def test_static_extra_text_arguments(self) -> None:
        seen = {}
        MessageBox.set_test_hook(lambda b: seen.update(info=b.informativeText(), detail=b.detailedText()) or SB.Ok)
        MessageBox.warning(None, "T", "x", informative="ek", detailed="ayrıntı")
        self.assertEqual(seen, {"info": "ek", "detail": "ayrıntı"})

    def test_about_helper(self) -> None:
        seen = {}
        MessageBox.set_test_hook(lambda b: seen.update(fmt=b.textFormat(), text=b.text(), icon=b.icon()) or SB.Ok)
        MessageBox.about(None, "Hakkında", "<b>Uygulama</b>")
        self.assertEqual(seen["fmt"], Qt.TextFormat.RichText)
        self.assertEqual(seen["icon"], MessageBox.Icon.NoIcon)


class InputDialogTests(Base):
    def test_hook_results_for_all_getters(self) -> None:
        MessageBox.set_test_hook(lambda d: ("Yeni Ad", True))
        self.assertEqual(InputDialog.getText(None, "T", "Ad", text="Eski"), ("Yeni Ad", True))
        MessageBox.set_test_hook(lambda d: (42, True))
        self.assertEqual(InputDialog.getInt(None, "T", "Sayı", 5, 1, 100), (42, True))
        MessageBox.set_test_hook(lambda d: (1.5, True))
        self.assertEqual(InputDialog.getDouble(None, "T", "Ondalık", 1.0, 0.0, 10.0, 2), (1.5, True))
        MessageBox.set_test_hook(lambda d: ("1080p", True))
        self.assertEqual(InputDialog.getItem(None, "T", "Seç", ["720p", "1080p"], 0, False), ("1080p", True))

    def test_cancel_returns_qt_style_values(self) -> None:
        MessageBox.set_test_hook(lambda d: SB.Cancel)
        self.assertEqual(InputDialog.getText(None, "T", "Ad", text="Eski"), ("", False))
        self.assertEqual(InputDialog.getInt(None, "T", "Sayı", 5, 1, 100), (5, False))
        self.assertEqual(InputDialog.getItem(None, "T", "Seç", ["a"], 0, False), ("", False))
        MessageBox.set_test_hook(lambda d: (None, False))
        self.assertEqual(InputDialog.getText(None, "T", "Ad"), ("", False))

    def test_ok_via_hook_returns_current_editor_value(self) -> None:
        MessageBox.set_test_hook(lambda d: SB.Ok)
        self.assertEqual(InputDialog.getText(None, "T", "Ad", text="Başlangıç"), ("Başlangıç", True))
        self.assertEqual(InputDialog.getInt(None, "T", "Sayı", 7, 1, 100), (7, True))
        self.assertEqual(InputDialog.getItem(None, "T", "Seç", ["a", "b"], 1, False), ("b", True))

    def test_hook_receives_dialog_with_editor_and_buttons(self) -> None:
        seen = {}

        def hook(d):
            seen.update(cls=type(d).__name__, title=d.windowTitle(), label=d.text(), value=d.value(),
                        buttons=self.texts(d), editor=type(d._extra).__name__, visible=d.isVisible())
            return SB.Cancel

        MessageBox.set_test_hook(hook)
        InputDialog.getText(None, "Yeniden Adlandır", "Yeni ad", text="foto")
        self.assertEqual(seen, {"cls": "InputDialog", "title": "Yeniden Adlandır", "label": "Yeni ad", "value": "foto",
                                "buttons": ["Tamam", "İptal"], "editor": "QLineEdit", "visible": False})

    def test_qt_style_positional_arguments(self) -> None:
        seen = []
        MessageBox.set_test_hook(lambda d: seen.append((d.value(), d._extra.echoMode())) or SB.Ok)
        InputDialog.getText(None, "T", "L", QLineEdit.EchoMode.Normal, "ilk")  # Lumina: echo konumsal, text 5.
        InputDialog.getText(None, "T", "L", "kısa biçim")
        InputDialog.getText(None, "T", "L", QLineEdit.EchoMode.Password)
        self.assertEqual(seen, [("ilk", QLineEdit.EchoMode.Normal), ("kısa biçim", QLineEdit.EchoMode.Normal),
                                ("", QLineEdit.EchoMode.Password)])

    def test_getint_keywords_and_range(self) -> None:
        seen = {}

        def hook(d):
            ed = d._extra
            seen.update(v=ed.value(), lo=ed.minimum(), hi=ed.maximum(), step=ed.singleStep())
            return SB.Ok

        MessageBox.set_test_hook(hook)
        InputDialog.getInt(None, "T", "px", value=1920, min=240, max=7680, step=10)  # MediaCompressor çağrısı
        self.assertEqual(seen, {"v": 1920, "lo": 240, "hi": 7680, "step": 10})

    def test_real_exec_typing_and_enter(self) -> None:
        def act():
            dlg = QApplication.activeModalWidget()
            self.assertIsInstance(dlg, InputDialog)
            dlg._extra.setText("Ofis PC")
            QTest.keyClick(dlg, Qt.Key.Key_Return)

        QTimer.singleShot(0, act)
        QTimer.singleShot(5000, lambda: (QApplication.activeModalWidget() or QWidget()).close())
        self.assertEqual(InputDialog.getText(None, "Cihaz", "Ad", text="eski"), ("Ofis PC", True))

    def test_real_exec_escape_and_cancel_button(self) -> None:
        QTimer.singleShot(0, lambda: QTest.keyClick(QApplication.activeModalWidget(), Qt.Key.Key_Escape))
        QTimer.singleShot(5000, lambda: (QApplication.activeModalWidget() or QWidget()).close())
        self.assertEqual(InputDialog.getText(None, "Cihaz", "Ad", text="eski"), ("", False))
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().button(SB.Cancel).click())
        self.assertEqual(InputDialog.getInt(None, "T", "Sayı", 3, 0, 9), (3, False))

    def test_real_exec_int_typed_value_is_read(self) -> None:
        def act():
            dlg = QApplication.activeModalWidget()
            dlg._extra.lineEdit().setText("2500")
            QTest.keyClick(dlg, Qt.Key.Key_Return)

        QTimer.singleShot(0, act)
        QTimer.singleShot(5000, lambda: (QApplication.activeModalWidget() or QWidget()).close())
        self.assertEqual(InputDialog.getInt(None, "T", "px", 1920, 240, 7680, 10), (2500, True))

    def test_get_item_editable_flag(self) -> None:
        seen = []
        MessageBox.set_test_hook(lambda d: seen.append(d._extra.isEditable()) or SB.Ok)
        InputDialog.getItem(None, "T", "L", ["a"], 0, False)
        InputDialog.getItem(None, "T", "L", ["a"])  # Qt'nin varsayılanı: düzenlenebilir
        self.assertEqual(seen, [False, True])


class LayoutTests(Base):
    LONG = "\n".join(f"{i}. satır: kesilmeden görünmesi gereken oldukça uzun bir ileti satırı örneği burada." for i in range(80))
    PATH = "Açılamadı: C:\\Users\\kullanici\\Belgeler\\Projeler\\çok_uzun_klasör_adı\\tatil_videosu_2026_kopya_son_hali_FINAL_v7.mp4"

    def avail(self):
        geo = QGuiApplication.primaryScreen().availableGeometry()
        return geo.height(), geo.width()

    def test_short_text_is_minimum_width_without_scrollbar(self) -> None:
        box = self.box("Tamam mı?")
        box.prepare()
        self.assertEqual(box.width() - 2 * mb._SHADOW, mb._MIN_W)
        self.assertEqual(box._scroll.height(), box._content.height())
        self.assertFalse(box._scroll.verticalScrollBar().isVisibleTo(box))

    def test_wide_text_is_capped_at_560(self) -> None:
        box = self.box("kelime " * 200)
        box.prepare()
        self.assertEqual(box.width() - 2 * mb._SHADOW, mb._MAX_W)

    def test_long_text_scrolls_instead_of_growing_beyond_70_percent(self) -> None:
        box = self.box(self.LONG, MessageBox.Icon.Critical)
        box.prepare()
        height, _ = self.avail()
        self.assertLessEqual(box.height(), height * 0.70 + 1)
        self.assertGreater(box._content.height(), box._scroll.height())  # kaydırma var
        self.assertEqual(box._blocks[0].height(), box._content.height())  # metin kesilmedi: blok tam yükseklikte

    def test_matrix_audit_offscreen(self) -> None:
        height, width = self.avail()
        texts = ["Tamam mı?", "Dosya sıkıştırılırken bir sorun oluştu ve işlem yarıda kesildi.", self.PATH, "A" * 300,
                 self.LONG, "<b>Kalın</b> başlık<br>ikinci satır <a href='https://example.com'>bağlantı</a>"]
        issues = []
        for theme in (MC_DARK, MC_LIGHT):
            for lang in ("tr", "en"):
                self.state.update(theme=theme, lang=lang)
                for text in texts:
                    for icon in (MessageBox.Icon.Information, MessageBox.Icon.Warning, MessageBox.Icon.Critical,
                                 MessageBox.Icon.Question, MessageBox.Icon.NoIcon):
                        for extra in (False, True):
                            box = self.box(text, icon, SB.Yes | SB.No | SB.Cancel if extra else SB.Ok)
                            if extra:
                                box.setInformativeText("Ayrıntılar için günlük dosyasına bakın. " * 4)
                                box.setDetailedText("satır\n" * 40)
                            box.prepare()
                            box.move(-6000, 0)
                            box.show()
                            QApplication.processEvents()
                            tag = f"{'dark' if theme is MC_DARK else 'light'}/{lang}/{text[:12]!r}/{icon.name}/{extra}"
                            issues += audit_box(box, height, width, tag)
                            if extra:
                                box._toggle_details()
                                QApplication.processEvents()
                                issues += audit_box(box, height, width, tag + "/ayrıntı")
                            box.close()
        self.assertEqual(issues, [])

    def test_long_unbreakable_word_wraps_instead_of_being_cut(self) -> None:
        box = self.box("A" * 500)
        box.prepare()
        blk = box._blocks[0]
        doc = blk._measure_doc(blk.width())
        self.assertLessEqual(doc.idealWidth(), blk.width() + 1)
        self.assertGreater(doc.size().height(), 3 * blk.fontMetrics().height())  # birden çok satıra bölündü

    def test_tiny_screen_is_respected(self) -> None:
        class FakeScreen:
            @staticmethod
            def availableGeometry():
                return QRect(0, 0, 500, 400)

        box = self.box(self.LONG)
        with mock.patch.object(MessageBox, "_screen", return_value=FakeScreen()):
            box.prepare()
        self.assertLessEqual(box.height(), 400 * 0.70 + 1)
        self.assertLessEqual(box.width(), 500)
        self.assertGreaterEqual(box.width() - 2 * mb._SHADOW, 240)

    def test_details_toggle_resizes_within_limits(self) -> None:
        box = self.box("Kısa")
        box.setDetailedText("satır\n" * 50)
        box.prepare()
        box.show()
        closed = box.height()
        box._toggle_details()
        self.assertGreater(box.height(), closed)
        self.assertLessEqual(box.height(), self.avail()[0] * 0.70 + 1)
        self.assertTrue(box._details_edit.isVisible())
        box._toggle_details()
        self.assertEqual(box.height(), closed)
        box.close()

    def test_title_is_single_line_elided_with_tooltip(self) -> None:
        box = self.box(title="Bu gerçekten çok uzun bir pencere başlığı örneğidir " * 3)
        box.prepare()
        box.show()
        QApplication.processEvents()
        label = box.findChild(QWidget, "mb_title_label")
        self.assertEqual(label.__class__.__name__, "ElidedLabel")
        self.assertTrue(label.toolTip().startswith("Bu gerçekten"))
        bar = box.findChild(QWidget, "mb_title")
        self.assertEqual(bar.height(), 38)
        box.close()

    def test_rich_text_and_plain_text_format(self) -> None:
        box = self.box("<b>Kalın</b> metin")
        box.prepare()
        self.assertEqual(box._blocks[0].toPlainText(), "Kalın metin")
        plain = self.box("Hata: <modül> bulunamadı")
        plain.setTextFormat(Qt.TextFormat.PlainText)
        plain.prepare()
        self.assertEqual(plain._blocks[0].toPlainText(), "Hata: <modül> bulunamadı")
        auto = self.box("Hata: <modül> bulunamadı")  # etiketle başlamayan metin düz kalır
        auto.prepare()
        self.assertEqual(auto._blocks[0].toPlainText(), "Hata: <modül> bulunamadı")

    def test_text_is_selectable_and_empty_text_makes_no_block(self) -> None:
        box = self.box("seçilebilir")
        box.prepare()
        flags = box._blocks[0].textInteractionFlags()
        self.assertTrue(flags & Qt.TextInteractionFlag.TextSelectableByMouse)
        empty = self.box("")
        empty.prepare()
        self.assertEqual(empty._blocks, [])
        self.assertEqual(empty.width() - 2 * mb._SHADOW, mb._MIN_W)

    def test_only_safe_link_schemes_are_opened(self) -> None:
        from PyQt6.QtCore import QUrl

        with mock.patch.object(mb.QDesktopServices, "openUrl") as opened:
            for url in ("https://example.com", "http://x.y", "mailto:a@b.c"):
                mb._open_link(QUrl(url))
            for url in ("file:///C:/Windows/System32/calc.exe", "javascript:alert(1)", "ms-settings:", "smb://x/y"):
                mb._open_link(QUrl(url))
        self.assertEqual(opened.call_count, 3)

    def test_vector_icons_for_each_kind_no_emoji(self) -> None:
        for icon, kind in ((MessageBox.Icon.Information, "information"), (MessageBox.Icon.Warning, "warning"),
                           (MessageBox.Icon.Critical, "critical"), (MessageBox.Icon.Question, "question")):
            box = self.box("x", icon)
            box.prepare()
            widget = box.findChild(QWidget, "mb_icon")
            self.assertEqual(widget.__class__.__name__, "_TypeIcon")
            self.assertEqual(widget.kind, kind)
            self.assertEqual((widget.width(), widget.height()), (40, 40))
            img = widget.grab().toImage()
            self.assertGreater(img.pixelColor(20, 20).alpha(), 0)
            colored = {img.pixelColor(x, y).name() for x in range(0, 40, 3) for y in range(0, 40, 3) if img.pixelColor(x, y).alpha() == 255}
            self.assertGreaterEqual(len(colored), 2)  # dolgu + işaret
        self.assertIsNone(self.box("x", MessageBox.Icon.NoIcon).findChild(QWidget, "mb_icon"))
        with open(mb.__file__, encoding="utf-8") as handle:
            text = handle.read()
        for emoji in ("⚠", "❌", "ℹ", "❓", "✔", "✖"):
            self.assertNotIn(emoji, text)

    def test_custom_icon_pixmap(self) -> None:
        box = self.box("x")
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.green)
        box.setIconPixmap(pix)
        box.prepare()
        self.assertEqual(box.findChild(QWidget, "mb_icon").width(), 40)


class FrameTests(Base):
    def test_window_flags_and_modality(self) -> None:
        box = self.box()
        flags = box.windowFlags()
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowType.Dialog)
        self.assertTrue(box.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertTrue(box.isModal())

    def test_stays_on_top_only_without_a_visible_owner(self) -> None:
        orphan = self.box()
        orphan.prepare()
        self.assertTrue(orphan.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        parent = QWidget()
        parent.show()
        self.addCleanup(parent.close)
        owned = self.box(parent=parent)
        owned.prepare()
        self.assertFalse(owned.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_corners_are_transparent_and_interior_is_the_base_colour(self) -> None:
        box = self.box("Gövde")
        box.prepare()
        img = box.grab().toImage()
        self.assertEqual(img.pixelColor(0, 0).alpha(), 0)  # pencere köşesi saydam (yuvarlak köşe)
        s = mb._SHADOW
        inner = img.pixelColor(s + 3, img.height() - s - 4)  # düğme satırı dolgusu
        self.assertEqual(inner.name(), MC_DARK["base"])
        accent = img.pixelColor(img.width() // 2, s + 1)
        self.assertEqual(accent.name(), MC_DARK["accent"].lower())  # 2 px vurgu üst çizgisi

    def test_application_wide_stylesheet_does_not_leak_into_the_box(self) -> None:
        app.setStyleSheet("QWidget { background-color: #ff0000; color: #00ff00; } QDialog { background: #ff0000; }"
                          " QPushButton { background-color: #ff00ff; }")
        self.addCleanup(lambda: app.setStyleSheet(""))
        box = self.box("Gövde")
        box.prepare()
        img = box.grab().toImage()
        s = mb._SHADOW
        self.assertEqual(img.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(img.pixelColor(s + 3, img.height() - s - 4).name(), MC_DARK["base"])
        self.assertEqual(box.findChild(QWidget, "mb_title_label").__class__.__name__, "ElidedLabel")

    def test_light_and_dark_render_differently_and_follow_accent(self) -> None:
        box = self.box()
        box.prepare()
        dark = box.grab().toImage()
        self.state["theme"] = MC_LIGHT
        box2 = self.box()
        box2.prepare()
        light = box2.grab().toImage()
        s = mb._SHADOW
        self.assertNotEqual(dark.pixelColor(s + 3, dark.height() - s - 4), light.pixelColor(s + 3, light.height() - s - 4))
        self.assertEqual(light.pixelColor(light.width() // 2, s + 1).name(), MC_LIGHT["accent"].lower())

    def test_centering_on_parent_and_clamping(self) -> None:
        parent = QWidget()
        parent.setGeometry(100, 100, 600, 400)
        parent.show()
        self.addCleanup(parent.close)
        box = self.box(parent=parent)
        box.prepare()
        box._center()
        c1, c2 = box.frameGeometry().center(), parent.frameGeometry().center()
        self.assertLessEqual(abs(c1.x() - c2.x()), 2)
        self.assertLessEqual(abs(c1.y() - c2.y()), 2)
        orphan = self.box()
        orphan.prepare()
        orphan._center()
        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.assertTrue(avail.contains(orphan.geometry().adjusted(mb._SHADOW, mb._SHADOW, -mb._SHADOW, -mb._SHADOW)))
        edge = QWidget()
        edge.setGeometry(avail.right() - 100, avail.bottom() - 60, 300, 200)  # ekran dışına taşan ebeveyn
        edge.show()
        self.addCleanup(edge.close)
        near = self.box(parent=edge)
        near.prepare()
        near._center()
        self.assertLessEqual(near.frameGeometry().right(), avail.right())
        self.assertLessEqual(near.frameGeometry().bottom(), avail.bottom())

    def test_minimised_or_hidden_parent_is_ignored(self) -> None:
        parent = QWidget()  # hiç gösterilmedi (tepsiye gizli ana pencere)
        self.addCleanup(parent.deleteLater)
        box = self.box(parent=parent)
        box.prepare()
        box._center()
        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.assertTrue(avail.intersects(box.frameGeometry()))
        self.assertTrue(box.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_setters_after_prepare_rebuild(self) -> None:
        box = self.box("ilk")
        box.prepare()
        first = box._inner
        box.setText("ikinci")
        box.prepare()
        self.assertIsNot(box._inner, first)
        self.assertEqual(box._blocks[0].toPlainText(), "ikinci")
        box.prepare()  # değişiklik yoksa yeniden kurulmaz
        self.assertIs(box._inner, box._inner)
        before = box._inner
        box.prepare()
        self.assertIs(box._inner, before)

    def test_persistent_buttons_survive_rebuilds(self) -> None:
        box = self.box(buttons=SB.NoButton)
        mine = box.addButton("Şimdi", BR.AcceptRole)
        box.prepare()
        box.setText("değişti")
        box.prepare()
        self.assertIs(box.buttons()[0], mine)
        self.assertEqual(mine.text(), "Şimdi")
        self.assertIsNotNone(mine.parent())
        self.assertEqual(exec_with(box, mine.click), 0)

    def test_hook_hides_nothing_when_no_boxes_leak(self) -> None:
        before = len([w for w in QApplication.topLevelWidgets() if isinstance(w, MessageBox) and w.isVisible()])
        MessageBox.set_test_hook(lambda b: SB.Ok)
        for _ in range(5):
            MessageBox.information(None, "T", "x")
        after = len([w for w in QApplication.topLevelWidgets() if isinstance(w, MessageBox) and w.isVisible()])
        self.assertEqual(before, after)


@unittest.skipUnless(sys.platform == "win32" and not os.environ.get("MB_SKIP_REAL"), "gerçek Windows platformu gerekir")
class RealPlatformFit(unittest.TestCase):
    def test_real_platform_audit(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
        script = os.path.join(HERE, "_real_fit.py")
        proc = subprocess.run([sys.executable, "-X", "utf8", "-B", script], capture_output=True, text=True,
                              timeout=300, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr[-1500:])
        self.assertIn("FIT-OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
