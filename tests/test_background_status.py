"""Status signalling only: no task installation, registry changes or notifications."""
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import setup_background


class BackgroundStatusTests(unittest.TestCase):
    def invoke(self,setting):
        result={}
        with patch('sys.platform', 'win32'), patch('app.services.windows_notifications.WindowsNotifications.register',return_value={'ok':True}) as register,patch('app.services.windows_notifications.WindowsNotifications.status',return_value={'ok':True,'setting':setting}) as status,patch('app.services.windows_background.install_background',return_value={'installed':True}) as install:
            setup_background(Path('synthetic-status.sqlite3'),result)
        register.assert_called_once()
        install.assert_called_once()
        status.assert_called_once()
        return result

    def test_disabled_for_user_reports_persistent_block_instead_of_success(self):
        result=self.invoke('DisabledForUser')
        self.assertTrue(result['ready'])
        self.assertTrue(result['notifications_disabled'])
        self.assertEqual(result['error'],'notifications_disabled')
        self.assertIn('Windows блокирует',result['message'])
        self.assertTrue(result['task']['installed'])

    def test_enabled_reports_background_success_without_disabled_flag(self):
        result=self.invoke('Enabled')
        self.assertTrue(result['ready'])
        self.assertNotIn('error',result)
        self.assertFalse(result.get('notifications_disabled',False))
        self.assertIn('Фоновые напоминания включены',result['message'])


if __name__=='__main__':
    unittest.main()
