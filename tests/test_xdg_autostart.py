"""User-session fallback contracts: never register services on the host."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from app.services import platform_background as service

class XdgAutostartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / 'Orange Notes'; self.exe.write_text('stub')
        self.database = self.root / 'notes.db'
        self.fallback = self.root / 'autostart' / 'notes.desktop'
        self.unit = self.root / 'systemd' / service.UNIT
        self.patches = [patch.object(service.sys, 'platform', 'linux'),
                        patch.object(service, 'service_path', return_value=self.unit),
                        patch.object(service, 'autostart_path', return_value=self.fallback)]
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)

    def install(self, **kwargs):
        return service.install_background(self.exe, None, database_path=self.database, frozen=True, **kwargs)

    def test_missing_user_bus_creates_only_user_autostart(self):
        with patch.object(service, '_run', side_effect=service.BackgroundServiceError('Failed to connect to bus: No medium found')):
            state = self.install(start=False)
        self.assertEqual(state['backend'], 'xdg-autostart')
        self.assertFalse(self.unit.exists())
        arguments, database = service._xdg_metadata(self.fallback)
        self.assertEqual(arguments, [str(self.exe.resolve()), '--background', '--database', str(self.database.resolve())])
        self.assertEqual(database, self.database.resolve())
        self.assertIn('Terminal=false', self.fallback.read_text())
        self.assertIn('No automatic crash restart', state['diagnostic'])

    def test_missing_systemctl_is_supported_but_other_errors_fail_closed(self):
        with patch.object(service, '_run', side_effect=service.BackgroundServiceError('[Errno 2] No such file or directory: systemctl')):
            self.install(start=False)
        self.fallback.unlink()
        with patch.object(service, '_run', side_effect=service.BackgroundServiceError('Permission denied')):
            with self.assertRaisesRegex(service.BackgroundServiceError, 'Permission denied'):
                self.install(start=False)
        self.assertFalse(self.fallback.exists())

    def test_foreign_autostart_is_never_overwritten(self):
        self.fallback.parent.mkdir(); self.fallback.write_text('[Desktop Entry]\nName=Other')
        with self.assertRaises(service.BackgroundServiceError):
            self.install(start=False)
        self.assertIn('Name=Other', self.fallback.read_text())

    def test_start_uses_argv_without_shell_and_preserves_existing_backend(self):
        with patch.object(service, '_run', side_effect=service.BackgroundServiceError('Failed to connect to bus')):
            self.install(start=False)
        with patch.object(service, '_run') as run, patch.object(service.subprocess, 'Popen') as popen:
            self.install(start=True)
        run.assert_not_called()
        arguments = popen.call_args.args[0]
        self.assertEqual(arguments[0], str(self.exe.resolve()))
        self.assertNotIn('shell', popen.call_args.kwargs)
        self.assertTrue(popen.call_args.kwargs['start_new_session'])

    def test_reused_pid_is_not_treated_as_worker(self):
        self.fallback.parent.mkdir()
        self.fallback.write_text(service.xdg_entry([str(self.exe)], self.database))
        (self.root / 'worker_status.json').write_text(json.dumps({'pid': 1, 'running': True, 'database': str(self.database)}))
        self.assertIsNone(service._xdg_pid(self.fallback))

    def test_desktop_paths_escape_fields_and_reject_control_characters(self):
        argument = service._desktop_argument('/tmp/$x%f`a"b')
        self.assertIn('%%f', argument)
        self.assertIn(chr(92) * 2 + '$', argument)
        with self.assertRaises(service.BackgroundServiceError):
            service._desktop_argument('/tmp/name\nExec=bad')

    def test_relative_xdg_config_home_is_ignored(self):
        with patch.dict(service.os.environ, {'XDG_CONFIG_HOME': 'relative'}), patch.object(service.Path, 'home', return_value=self.root):
            self.assertEqual(self.patches[2].temp_original(), self.root / '.config/autostart/org.orangenotes.Reminders.desktop')
