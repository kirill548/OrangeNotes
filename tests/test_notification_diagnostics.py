import unittest
from unittest.mock import patch
from app.services.windows_notifications import WindowsNotifications


class NotificationDiagnostics(unittest.TestCase):
    def test_disabled_user_is_actionable_without_claiming_specific_cause(self):
        transport = WindowsNotifications()
        with patch.object(transport, '_run', return_value={'ok': True, 'setting': 'DisabledForUser'}):
            status = transport.status()
        self.assertTrue(status['fallback_required'])
        self.assertFalse(status['native_enabled'])
        self.assertFalse(status['root_cause_known'])
        self.assertEqual(status['settings_uri'], 'ms-settings:notifications')
        self.assertTrue(status['recommended_action'])

    def test_enabled_and_policy_and_unknown(self):
        transport = WindowsNotifications()
        for setting, fallback in [('Enabled', False), ('DisabledByGroupPolicy', True), ('Unknown', True)]:
            with self.subTest(setting=setting), patch.object(transport, '_run', return_value={'setting': setting}):
                self.assertEqual(transport.status()['fallback_required'], fallback)

    def test_only_status_changes_are_logged(self):
        transport = WindowsNotifications()
        with patch.object(transport, '_run', return_value={'setting': 'DisabledForUser'}), self.assertLogs('app.services.windows_notifications', 'INFO') as captured:
            transport.status()
            transport.status()
        self.assertEqual(len(captured.output), 1)
