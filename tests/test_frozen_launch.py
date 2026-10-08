"""Command construction tests; no real tasks, shortcuts or protocols are installed."""
import base64
import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

from app.services import windows_background as background
from app.services.windows_notifications import WindowsNotifications


class FrozenLaunchTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.root=Path(self.directory.name)/"Orange Notes & 中文 ' folder"
        self.root.mkdir()
        self.exe=self.root/'OrangeNotes.exe'
        self.exe.write_bytes(b'test executable placeholder')
        self.database=self.root/'private data.sqlite3'

    def tearDown(self):
        self.directory.cleanup()

    def action(self,xml):
        tree=ET.fromstring(xml)
        arguments=tree.find('{'+background.TASK_NS+'}Actions/{'+background.TASK_NS+'}Exec/{'+background.TASK_NS+'}Arguments').text
        command=tree.find('{'+background.TASK_NS+'}Actions/{'+background.TASK_NS+'}Exec/{'+background.TASK_NS+'}Command').text
        return command,arguments,tree

    def test_frozen_task_uses_exe_directly_without_python_entry_or_dependencies(self):
        xml=background.task_xml(self.exe,None,'S-1-5-21-1001',self.database,self.root/'nonexistent deps',frozen=True)
        command,arguments,tree=self.action(xml)
        self.assertEqual(command,str(self.exe))
        self.assertIn('--background --database',arguments)
        self.assertNotIn('main.py',arguments)
        self.assertNotIn('powershell',command.casefold())
        self.assertNotIn('EncodedCommand',arguments)
        working=tree.find('{'+background.TASK_NS+'}Actions/{'+background.TASK_NS+'}Exec/{'+background.TASK_NS+'}WorkingDirectory').text
        self.assertEqual(working,str(self.root))

    def test_runtime_frozen_flag_selects_direct_command(self):
        with patch.object(sys,'frozen',True,create=True):
            command,arguments,_=self.action(background.task_xml(self.exe,self.root/'missing main.py','S-1-5-21-1001',self.database))
        self.assertEqual(command,str(self.exe))
        self.assertNotIn('main.py',arguments)

    def test_frozen_registration_helper_resource_and_icon(self):
        bundle=self.root/'_internal'
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'_MEIPASS',str(bundle),create=True),patch.object(sys,'executable',str(self.exe)):
            notifications=WindowsNotifications()
            self.assertEqual(notifications.helper,bundle/'app/services/windows_toast.ps1')
            self.assertEqual(notifications.app_root,self.root)
            with patch.object(notifications,'_run',return_value={'ok':True}) as run:
                notifications.register(database_path=self.database)
        request=run.call_args.kwargs
        self.assertIsNone(request['main'])
        self.assertTrue(request['frozen'])
        self.assertEqual(request['icon'],str(self.exe))
        self.assertNotIn('main.py',request['launch_args'])
        self.assertNotIn('main.py',request['protocol_command'])
        self.assertIn('--notification "%1"',request['protocol_command'])
        self.assertIn('private data.sqlite3',request['launch_args'])

    def test_source_registration_preserves_main_entry_and_icon_resource(self):
        notifications=WindowsNotifications(app_root=self.root)
        with patch.object(sys,'frozen',False,create=True),patch.object(notifications,'_run',return_value={'ok':True}) as run:
            notifications.register(python_executable=sys.executable,database_path=self.database)
        request=run.call_args.kwargs
        self.assertEqual(request['main'],str(self.root/'app/main.py'))
        self.assertIn('main.py',request['launch_args'])
        self.assertIn('main.py',request['protocol_command'])
        self.assertTrue(request['icon'].endswith('app.ico'))

    def test_frozen_setup_background_does_not_pass_source_path(self):
        import app.main as entry
        try:
            with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(self.exe)):
                importlib.reload(entry)
                self.assertIsNone(entry.DEPS)
                with patch.object(sys, 'platform', 'win32'), patch('app.services.windows_notifications.WindowsNotifications.register'),patch('app.services.windows_notifications.WindowsNotifications.status',return_value={'setting':'Enabled'}),patch('app.services.windows_background.install_background',return_value={'installed':True}) as install:
                    result={}
                    entry.setup_background(self.database,result)
                    self.assertTrue(result['ready'])
                    self.assertNotIn('error',result)
                    self.assertIsNone(install.call_args.args[1])
                    self.assertIsNone(install.call_args.kwargs['dependencies_path'])
        finally:
            importlib.reload(entry)


if __name__=='__main__':
    unittest.main()
