"""notify_pc: primeira notificação de um app sem instalador (o Windows ainda não conhece o AUMID)."""
import sys
import types
import unittest
from unittest import mock

from sm import desktop


class _Setting:
    ENABLED, DISABLED_FOR_APPLICATION = 0, 1


def _winrt(setting):
    shown = []

    class Notifier:
        @property
        def setting(self):
            if isinstance(setting, Exception):
                raise setting
            return setting

        def show(self, toast):
            shown.append(toast)

    notif = types.SimpleNamespace(
        NotificationSetting=_Setting, ToastNotification=lambda doc: doc,
        ToastNotificationManager=types.SimpleNamespace(create_toast_notifier_with_id=lambda aumid: Notifier()))
    xml = types.SimpleNamespace(XmlDocument=lambda: types.SimpleNamespace(load_xml=lambda s: None))
    mods = {"winrt.windows.ui.notifications": notif, "winrt.windows.data.xml.dom": xml}
    return mock.patch.dict(sys.modules, mods), shown


def _not_found():
    e = OSError("Elemento não encontrado.")
    e.winerror = desktop.ERROR_NOT_FOUND
    return e


class NotifyTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(desktop, "_aumid_done", True)
        p.start()
        self.addCleanup(p.stop)

    def check(self, setting, expected, n_shown):
        patch, shown = _winrt(setting)
        with patch:
            self.assertIs(desktop.notify_pc("t", "b"), expected)
        self.assertEqual(len(shown), n_shown)

    def test_enabled(self):
        self.check(_Setting.ENABLED, True, 1)

    def test_disabled_by_user(self):
        self.check(_Setting.DISABLED_FOR_APPLICATION, False, 0)

    def test_first_notification_not_registered_yet(self):
        self.check(_not_found(), True, 1)

    def test_other_error(self):
        self.check(OSError(5, "Acesso negado"), False, 0)


if __name__ == "__main__":
    unittest.main()
