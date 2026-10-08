"""Opt-in real OS tests. Request acceptance is not proof of visible banners.

ORANGE_NATIVE_NOTIFICATION_TESTS=1 enables tests on disposable CI desktop sessions.
macOS additionally needs a bundled .app and explicitly granted notification permission.
"""
import os
import shutil
import subprocess
import sys
import unittest
import uuid


@unittest.skipUnless(os.environ.get('ORANGE_NATIVE_NOTIFICATION_TESTS') == '1', 'Real OS notifications require explicit opt-in')
class NativeNotificationIntegration(unittest.TestCase):
    def test_linux_real_daemon_accepts_and_closes_notification(self):
        if sys.platform != 'linux':
            self.skipTest('Linux notification daemon requires Linux')
        if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
            self.fail('Native tests opted in but DBUS_SESSION_BUS_ADDRESS is missing; use tools/test_linux_notifications.sh')
        gdbus = shutil.which('gdbus')
        if not gdbus or not shutil.which('notify-send'):
            self.fail('Native tests opted in: install libnotify-bin and libglib2.0-bin')
        capabilities = subprocess.run([gdbus, 'call', '--session', '--dest', 'org.freedesktop.Notifications', '--object-path', '/org/freedesktop/Notifications', '--method', 'org.freedesktop.Notifications.GetCapabilities'], capture_output=True, text=True, timeout=10)
        self.assertEqual(capabilities.returncode, 0, capabilities.stderr)
        self.assertIn('actions', capabilities.stdout, 'Daemon must support actionable reminders')
        from app.services.portable_notifications import LinuxNotifications
        transport = LinuxNotifications()
        event_id = 900000000 + uuid.uuid4().int % 1000000
        try:
            receipt = transport.show('Orange Notes integration test', 'Disposable test notification', event_id, 'test-only-token')
            self.assertTrue(receipt['deliveryVerified'])
            self.assertGreater(transport.identifiers[event_id], 0)
        finally:
            transport.remove(event_id)

    def test_macos_real_framework_accepts_request(self):
        if sys.platform != 'darwin':
            self.skipTest('UserNotifications requires macOS')
        import Foundation
        if not Foundation.NSBundle.mainBundle().bundleIdentifier():
            self.fail('Native tests opted in: run tools/test_mac_bundle.sh inside a signed .app')
        from app.services.portable_notifications import MacNotifications
        transport = MacNotifications()
        transport.register()  # Denied permission is a real failure after explicit opt-in.
        event_id = 900000000 + uuid.uuid4().int % 1000000
        try:
            self.assertTrue(transport.show('Orange Notes integration test', 'Disposable test notification', event_id, 'test-only-token')['submitted'])
        finally:
            transport.remove(event_id)
