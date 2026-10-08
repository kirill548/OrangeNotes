import subprocess
import unittest
from unittest.mock import patch
from app.services.portal_notifications import PortalNotifications
from app.services import platform_background, portable_notifications


class PortalTests(unittest.TestCase):
    def test_sandbox_uses_portal_and_disables_host_service(self):
        with patch('sys.platform','linux'), patch.dict('os.environ',{'FLATPAK_ID':'org.orangenotes.desktop'}):
            self.assertIsInstance(portable_notifications.notification_transport(),PortalNotifications)
            self.assertFalse(platform_background.supported())

    def test_untrusted_content_is_one_escaped_gvariant_argument(self):
        with patch('shutil.which',return_value='/app/bin/gdbus'), patch('subprocess.run',return_value=subprocess.CompletedProcess([],0,'()','')) as run:
            transport=PortalNotifications()
            receipt=transport.show('"Title"','Text > \' ; $(command)\nnext',17,'secret-not-exported')
            args=run.call_args.args[0]
            self.assertIn('AddNotification',args[-3])
            self.assertEqual(args[-2],'event-17')
            self.assertIn('\\nnext',args[-1])
            self.assertNotIn('secret-not-exported',' '.join(args))
            self.assertTrue(receipt['submitted'])
            transport.remove(17)
            self.assertIn('RemoveNotification',run.call_args.args[0][-2])

    def test_portal_rejection_is_not_acknowledged(self):
        with patch('shutil.which',return_value='gdbus'), patch('subprocess.run',return_value=subprocess.CompletedProcess([],1,'','permission denied')):
            with self.assertRaisesRegex(OSError,'permission denied'):
                PortalNotifications().show('title','body',1,'token')
