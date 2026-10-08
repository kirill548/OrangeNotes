"""Service contracts on Windows; no external manager is invoked by these tests."""
import io
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.services import platform_background as services
from app.services import portable_notifications as notifications
from app.utils import data_paths, shortcuts


class PlatformContracts(unittest.TestCase):
    def test_windows_path_preserves_existing_location(self):
        with patch.object(sys, 'platform', 'win32'), patch.dict('os.environ', {'LOCALAPPDATA': 'C:/Users/Example/AppData/Local'}):
            self.assertEqual(data_paths.database_path(), Path('C:/Users/Example/AppData/Local/OrangeNotes/notes.sqlite3'))

    def test_mac_shortcut_uses_qt_control_and_cmd_label(self):
        with patch.object(sys, 'platform', 'darwin'):
            self.assertEqual(shortcuts.shortcut('Ctrl+Shift+D'), 'Ctrl+Shift+D')
            self.assertEqual(shortcuts.shortcut_label('Ctrl+Shift+D'), 'Cmd+Shift+D')

    def test_launchd_arguments_are_not_shell_interpolated(self):
        arguments = ['/Applications/Orange Notes.app/Contents/MacOS/OrangeNotes', '--background', '--database', '/Users/Alice/Notes $x;".sqlite3']
        document = plistlib.loads(services.launchd_plist(arguments, arguments[-1]))
        self.assertEqual(document['ProgramArguments'], arguments)
        self.assertTrue(document['KeepAlive'])
        self.assertTrue(document['RunAtLoad'])
        self.assertTrue(document['OrangeNotesManaged'])

    def test_systemd_escapes_specifiers_environment_quotes_and_newlines(self):
        text = services.systemd_unit(['/opt/Orange Notes/OrangeNotes', '--database', '/home/a/%n/$PATH/"quoted".db'])
        self.assertIn('%%n', text)
        self.assertIn('$$PATH', text)
        self.assertIn('\\"quoted\\"', text)
        self.assertIn('Restart=always', text)
        with self.assertRaises(services.BackgroundServiceError):
            services.systemd_unit(['/tmp/executable\nExecStart=evil'])

    def test_install_linux_never_uses_shell_and_rejects_foreign_unit(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / 'python'; executable.write_text('stub')
            entry = directory / 'entry.py'; entry.write_text('stub')
            unit = directory / services.UNIT
            with patch.object(sys, 'platform', 'linux'), patch.object(services, 'service_path', return_value=unit), patch.object(services, '_run', return_value=Mock(returncode=0)) as run:
                result = services.install_background(executable, entry, database_path=directory/'notes.db')
                self.assertTrue(result['installed'])
                self.assertTrue(unit.read_text().startswith(services.MARKER))
                self.assertTrue(all(isinstance(call.args[0], list) for call in run.call_args_list))
                unit.write_text('[Service]\nExecStart=other\n')
                with self.assertRaises(services.BackgroundServiceError):
                    services.install_background(executable, entry, database_path=directory/'notes.db')

    def test_restore_unrelated_database_does_not_stop_windows_task(self):
        state = {'installed': True, 'state': 'Running'}
        with patch.object(sys, 'platform', 'win32'), patch.object(services, 'inspect_background', return_value=state), \
             patch('app.services.windows_background._assert_owned'), patch('app.services.windows_background._current_sid', return_value='S-1-5-1'), \
             patch('app.services.windows_background._powershell', return_value='--background --database unrelated.sqlite3') as powershell, \
             patch('app.services.reminder_worker.WorkerLock') as lock:
            lock.return_value.acquire.return_value = True
            with services.pause_database_worker('temporary.sqlite3'):
                pass
            self.assertEqual(powershell.call_count, 1)
            lock.return_value.release.assert_called_once()

    def test_stop_failure_reenables_windows_task(self):
        database = Path('temporary.sqlite3').resolve()
        arguments = '--background ' + subprocess.list2cmdline(['--database', str(database)])
        calls = []
        def powershell(script):
            calls.append(script)
            if '.Actions.Arguments' in script:
                return arguments
            if 'Stop-ScheduledTask' in script:
                raise RuntimeError('stop failed')
            return ''
        with patch.object(sys, 'platform', 'win32'), patch.object(services, 'inspect_background', return_value={'installed': True, 'state': 'Stopped'}), \
             patch('app.services.windows_background._assert_owned'), patch('app.services.windows_background._current_sid', return_value='S-1-5-1'), \
             patch('app.services.windows_background._powershell', side_effect=powershell):
            with self.assertRaisesRegex(RuntimeError, 'stop failed'):
                with services.pause_database_worker(database):
                    self.fail('database must not be replaced')
        self.assertTrue(any('Enable-ScheduledTask' in script for script in calls))

    def test_linux_notification_requires_server_id_and_escapes_body(self):
        process = Mock()
        process.stdout = io.StringIO('42\n')
        with patch.object(notifications.shutil, 'which', return_value='/usr/bin/notify-send'), patch.object(notifications.subprocess, 'Popen', return_value=process) as popen, \
             patch.object(notifications.LinuxNotifications, '_action'):
            transport = notifications.LinuxNotifications()
            receipt = transport.show('-title', '<b>literal & body</b>', 8, 'token')
        self.assertTrue(receipt['deliveryVerified'])
        self.assertFalse(receipt['historyVerified'])
        arguments = popen.call_args.args[0]
        self.assertEqual(arguments[-3:], ['--', '-title', '&lt;b&gt;literal &amp; body&lt;/b&gt;'])
        self.assertNotIn('shell', popen.call_args.kwargs)

    def test_linux_notification_rejected_without_id(self):
        process = Mock()
        process.stdout = io.StringIO('')
        process.communicate.return_value = ('', 'D-Bus unavailable')
        with patch.object(notifications.shutil, 'which', return_value='/usr/bin/notify-send'), patch.object(notifications.subprocess, 'Popen', return_value=process):
            with self.assertRaisesRegex(OSError, 'D-Bus unavailable'):
                notifications.LinuxNotifications().show('title', 'body', 8, 'token')

    def test_linux_missing_notify_send_is_explicit_error(self):
        with patch.object(notifications.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(OSError, 'libnotify'):
                notifications.LinuxNotifications().register()

    def test_mac_native_payload_actions_and_delegate_reuse(self):
        class NSObject:
            @classmethod
            def alloc(cls): return cls()
            def init(self): return self
        center = Mock()
        center.requestAuthorizationWithOptions_completionHandler_.side_effect = lambda options, callback: callback(True, None)
        center.addNotificationRequest_withCompletionHandler_.side_effect = lambda request, callback: callback(None)
        content = Mock()
        UN = SimpleNamespace(UNUserNotificationCenter=Mock(currentNotificationCenter=Mock(return_value=center)),
                             UNNotificationAction=Mock(), UNNotificationCategory=Mock(),
                             UNMutableNotificationContent=Mock(alloc=Mock(return_value=Mock(init=Mock(return_value=content)))),
                             UNNotificationRequest=Mock(), UNNotificationSound=Mock(),
                             UNAuthorizationOptionAlert=1, UNAuthorizationOptionSound=2,
                             UNNotificationPresentationOptionBanner=16, UNNotificationPresentationOptionSound=2)
        foundation = SimpleNamespace(NSObject=NSObject, NSBundle=Mock(mainBundle=Mock(return_value=Mock(bundleIdentifier=Mock(return_value='com.orangenotes.desktop')))))
        with patch.dict(sys.modules, {'objc': Mock(protocolNamed=Mock(return_value='protocol')), 'Foundation': foundation, 'UserNotifications': UN}), \
             patch.object(notifications, '_mac_delegate_type', None):
            transport = notifications.MacNotifications('notes.sqlite3')
            transport.register()
            receipt = transport.show('Title', 'Body', 9, 'secret')
            content.setUserInfo_.assert_called_once_with({'event': 9, 'token': 'secret'})
            content.setCategoryIdentifier_.assert_called_once_with('OrangeNotesReminder')
            self.assertTrue(receipt['deliveryVerified'])
            self.assertFalse(receipt['historyVerified'])
            second = notifications.MacNotifications()
            second.register()
            self.assertIs(type(transport.delegate), type(second.delegate))
            self.assertIs(transport.delegate.owner, transport)
            self.assertIs(second.delegate.owner, second)

    def test_mac_rejects_unbundled_source_run(self):
        foundation = SimpleNamespace(NSBundle=Mock(mainBundle=Mock(return_value=Mock(bundleIdentifier=Mock(return_value=None)))))
        with patch.dict(sys.modules, {'objc': Mock(), 'Foundation': foundation, 'UserNotifications': Mock()}):
            with self.assertRaisesRegex(OSError, 'Bundle Identifier'):
                notifications.MacNotifications().register()


if __name__ == '__main__':
    unittest.main()


class NotificationLifecycleTests(unittest.TestCase):
    def test_linux_remove_closes_actual_desktop_notification(self):
        process = Mock(); process.poll.return_value = None
        with patch.object(notifications.shutil, 'which', return_value='/usr/bin/gdbus'), patch.object(notifications.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            transport = notifications.LinuxNotifications()
            transport.processes[3] = process
            transport.identifiers[3] = 42
            transport.remove(3)
            self.assertEqual(run.call_args.args[0][-2:], ['org.freedesktop.Notifications.CloseNotification', '42'])
            self.assertNotIn('shell', run.call_args.kwargs)
            process.terminate.assert_called_once()
            self.assertNotIn(3, transport.identifiers)

    def test_linux_missing_close_transport_preserves_receipt(self):
        with patch.object(notifications.shutil, 'which', return_value=None):
            transport = notifications.LinuxNotifications()
            transport.identifiers[3] = 42
            with self.assertRaisesRegex(OSError, 'gdbus'):
                transport.remove(3)
            self.assertEqual(transport.identifiers[3], 42)

    def test_mac_remove_pending_and_delivered(self):
        transport = notifications.MacNotifications()
        transport.center = Mock()
        transport.remove(7)
        transport.center.removePendingNotificationRequestsWithIdentifiers_.assert_called_once_with(['OrangeNotes.7'])
        transport.center.removeDeliveredNotificationsWithIdentifiers_.assert_called_once_with(['OrangeNotes.7'])


class ServiceStateTests(unittest.TestCase):
    def test_linux_stopped_disabled_unit_is_reported_without_enabling(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / services.UNIT
            path.write_text(services.MARKER, encoding='utf-8')
            results = [SimpleNamespace(returncode=3, stdout='inactive', stderr=''), SimpleNamespace(returncode=1, stdout='disabled', stderr='')]
            with patch.object(sys, 'platform', 'linux'), patch.object(services, 'service_path', return_value=path), patch.object(services, '_run', side_effect=results) as run:
                state = services.inspect_background()
            self.assertEqual(state['state'], 'Stopped')
            self.assertFalse(state['enabled'])
            self.assertEqual(run.call_args_list[-1].args[0], ['systemctl', '--user', 'is-enabled', services.UNIT])

    def test_mac_loaded_job_without_pid_is_stopped_not_running(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'agent.plist'
            path.write_bytes(plistlib.dumps({'OrangeNotesManaged': True}))
            with patch.object(sys, 'platform', 'darwin'), patch.object(services.os, 'getuid', return_value=501, create=True), patch.object(services, 'service_path', return_value=path), patch.object(services, '_run', return_value=SimpleNamespace(returncode=0, stdout='state = waiting', stderr='')):
                state = services.inspect_background()
            self.assertTrue(state['loaded'])
            self.assertEqual(state['state'], 'Stopped')
