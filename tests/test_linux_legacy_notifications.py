import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from app.services import portable_notifications as notifications


class LegacyNotificationTests(unittest.TestCase):
    def test_waiting_client_line_buffers_id_and_keeps_action_subscription(self):
        process = Mock()
        process.stdout = io.StringIO('42\n')
        with patch.object(notifications.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name), \
             patch.object(notifications.subprocess, 'Popen', return_value=process) as launch, \
             patch.object(notifications.LinuxNotifications, '_action'):
            transport = notifications.LinuxNotifications()
            receipt = transport.show('Title', 'Body', 7, 'secret')
            arguments = launch.call_args.args[0]
            self.assertEqual(arguments[:4], ['/usr/bin/stdbuf', '--output=L', '--error=L', '/usr/bin/notify-send'])
            self.assertIn('--wait', arguments)
            self.assertIn('--print-id', arguments)
            self.assertIn('--action=done=Выполнено', arguments)
            self.assertTrue(receipt['deliveryVerified'])
            self.assertEqual(transport.identifiers[7], 42)
            self.assertIs(transport.processes[7], process)
            process.kill.assert_not_called()

    def test_missing_line_buffer_utility_does_not_invent_acceptance(self):
        process = Mock()
        process.stdout = io.StringIO('')
        process.communicate.return_value = ('', 'D-Bus unavailable')
        with patch.object(notifications.shutil, 'which', side_effect=lambda name: '/usr/bin/notify-send' if name == 'notify-send' else None), \
             patch.object(notifications.subprocess, 'Popen', return_value=process) as launch:
            transport = notifications.LinuxNotifications()
            with self.assertRaisesRegex(OSError, 'D-Bus unavailable'):
                transport.show('Title', 'Body', 7, 'secret')
            self.assertEqual(launch.call_args.args[0][0], '/usr/bin/notify-send')
            self.assertNotIn(7, transport.identifiers)

    def legacy_process(self):
        process = Mock()
        process.stdout = io.StringIO('')
        process.communicate.return_value = ('', 'Unknown option --wait')
        return process

    def test_legacy_real_id_no_fake_actions_and_cached_fallback(self):
        process = self.legacy_process()
        result = SimpleNamespace(returncode=0, stdout='(uint32 42,)\n', stderr='')
        with patch.object(notifications.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name), \
             patch.object(notifications.subprocess, 'Popen', return_value=process) as popen, \
             patch.object(notifications.subprocess, 'run', return_value=result) as run:
            transport = notifications.LinuxNotifications()
            receipt = transport.show('Title with "quotes"', '<b>literal</b>', 7, 'secret')
            self.assertTrue(receipt['deliveryVerified'])
            self.assertFalse(receipt['actionsSupported'])
            self.assertFalse(transport.status()['actions_supported'])
            self.assertEqual(transport.identifiers[7], 42)
            arguments = run.call_args.args[0]
            self.assertIn('org.freedesktop.Notifications.Notify', arguments)
            self.assertIn('"&lt;b&gt;literal&lt;/b&gt;"', arguments)
            self.assertNotIn('secret', arguments)
            self.assertNotIn('shell', run.call_args.kwargs)
            transport.show('Second', 'Body', 8, 'secret')
            self.assertEqual(popen.call_count, 1)
            transport.remove(7)
            self.assertNotIn(7, transport.identifiers)
            self.assertEqual(run.call_args.args[0][-2:], ['org.freedesktop.Notifications.CloseNotification', '42'])

    def test_legacy_rejected_receipt_does_not_mark_delivered(self):
        with patch.object(notifications.shutil, 'which', return_value='/usr/bin/gdbus'), \
             patch.object(notifications.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='No D-Bus session')):
            transport = notifications.LinuxNotifications()
            transport.legacy_client = True
            with self.assertRaisesRegex(OSError, 'No D-Bus session'):
                transport.show('Title', 'Body', 7, 'secret')
            self.assertNotIn(7, transport.identifiers)

    def test_legacy_missing_gdbus_does_not_fake_success(self):
        transport = notifications.LinuxNotifications()
        transport.executable = '/usr/bin/notify-send'
        transport.legacy_client = True
        with patch.object(notifications.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(OSError, 'gdbus'):
                transport.show('Title', 'Body', 7, 'secret')
