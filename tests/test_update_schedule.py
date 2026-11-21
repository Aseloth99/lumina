"""Güncelleme denetim takvimi: uzun süre açılmayan uygulama açılışta hemen denetler; açıkken birkaç saatte bir
denetler; zamanlayıcı sık "tıklar" (duvar saatine bakar), böylece uyku/hazırda bekleme sonrası kaçan denetim de yakalanır.

Kullanıcı: "arka planda güncelleme kontrolü yapmıyor ... uzun süre açmayıp sonra açanlarda direk kontrol yapılabilir."
Eskiden açılışta ancak 24 saat geçtiyse denetleniyor, zamanlayıcı da ancak 24 saat kesintisiz açık kalınca tetikleniyordu."""

from __future__ import annotations

import os
import pathlib
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.core import updater  # noqa: E402
from app.core.updater import UpdateChecker  # noqa: E402


class _FakeSettings:
    def __init__(self, values):
        self.values = values

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):  # noqa: N802
        self.values[key] = value


def make_checker(last, auto):
    values = {"auto_update_check": auto}
    if last is not None:
        values["last_update_check"] = last
    fake = _FakeSettings(values)
    patcher = mock.patch.object(updater, "_settings", lambda: fake)
    patcher.start()
    checker = UpdateChecker()
    _PATCHERS.append(patcher)
    return checker


_PATCHERS: list = []


def tearDownModule():
    for p in reversed(_PATCHERS):
        p.stop()


def _ago(**kw) -> str:
    return (datetime.now() - timedelta(**kw)).isoformat()


class UpdateScheduleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def run_start(self, last, auto=True):
        checker = make_checker(last, auto)
        self.addCleanup(checker.stop)
        with mock.patch.object(checker, "check_for_updates") as check:
            checker.start()
        return checker, check

    def test_policy_constants(self) -> None:
        self.assertEqual(UpdateChecker.STARTUP_STALE_HOURS, 1)
        self.assertEqual(UpdateChecker.CHECK_INTERVAL_HOURS, 4)
        self.assertEqual(UpdateChecker.TICK_MINUTES, 30)

    def test_start_checks_when_last_check_is_older_than_an_hour(self) -> None:
        checker, check = self.run_start(_ago(hours=2))
        check.assert_called_once_with(show_message=False)
        self.assertTrue(checker.check_timer.isActive())
        self.assertEqual(checker.check_timer.interval(), 30 * 60 * 1000)

    def test_start_checks_after_days_away_and_on_first_run(self) -> None:
        for last in (_ago(days=9), None, "bozuk tarih"):
            with self.subTest(last=last):
                _, check = self.run_start(last)
                check.assert_called_once_with(show_message=False)

    def test_start_skips_a_fresh_check(self) -> None:
        checker, check = self.run_start(_ago(minutes=10))
        check.assert_not_called()
        self.assertTrue(checker.check_timer.isActive(), "denetim atlansa da zamanlayıcı kurulmalı")

    def test_tick_checks_only_every_few_hours(self) -> None:
        for last, expected in ((_ago(hours=3), False), (_ago(hours=5), True), (_ago(days=2), True)):
            with self.subTest(last=last):
                checker = make_checker(last, True)
                with mock.patch.object(checker, "check_for_updates") as check:
                    checker._auto_check()
                self.assertEqual(check.called, expected)

    def test_disabled_auto_check_does_nothing(self) -> None:
        checker, check = self.run_start(_ago(days=9), auto=False)
        check.assert_not_called()
        self.assertFalse(checker.check_timer.isActive())


if __name__ == "__main__":
    unittest.main()
