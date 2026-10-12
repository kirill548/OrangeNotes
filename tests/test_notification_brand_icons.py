"""Notification identity payloads without connecting to a desktop daemon."""
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.portable_notifications import LinuxNotifications
from app.services.portal_notifications import PortalNotifications
from app.utils.runtime_paths import resource_path


class NotificationBrandIconsTests(unittest.TestCase):
    def test_modern_libnotify_receives_packaged_png(self):
        process = SimpleNamespace(stdout=io.StringIO('42\n'))
        with patch('app.services.portable_notifications.shutil.which', return_value='/usr/bin/notify-send'), patch('app.services.portable_notifications.subprocess.Popen', return_value=process) as launch, patch.object(LinuxNotifications, '_action'):
            transport = LinuxNotifications()
            transport.show('Напоминание', 'Текст', 1, 'token')
        self.assertIn('--icon=' + str(resource_path('app/assets/app.png')), launch.call_args.args[0])

    def test_legacy_dbus_receives_packaged_png_in_app_icon_slot(self):
        result = SimpleNamespace(returncode=0, stdout='(uint32 42,)\n', stderr='')
        with patch('app.services.portable_notifications.shutil.which', return_value='/usr/bin/gdbus'), patch('app.services.portable_notifications.subprocess.run', return_value=result) as call:
            LinuxNotifications()._show_legacy('Title', 'Body', 1)
        args = call.call_args.args[0]
        offset = args.index('org.freedesktop.Notifications.Notify')
        self.assertEqual(json.loads(args[offset + 3]), str(resource_path('app/assets/app.png')))

    def test_portal_receives_serialized_themed_icon(self):
        with patch('app.services.portal_notifications.shutil.which', return_value='/usr/bin/gdbus'), patch.object(PortalNotifications, '_call') as call:
            PortalNotifications().show('Title', 'Body', 1, 'token')
        self.assertIn("'icon': <('themed', <['org.orangenotes.OrangeNotes']>)>", call.call_args.args[2])
