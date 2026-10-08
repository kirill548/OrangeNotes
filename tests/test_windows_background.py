"""Task XML and ownership safeguards; tests never register real Windows tasks."""
import base64
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import windows_background as background


class BackgroundTaskTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[3] / 'work'
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='background-tests-', dir=scratch)
        self.root = Path(self.temp.name) / "A&B '中文 folder"
        self.root.mkdir()
        self.entry = self.root / 'main script.py'
        self.entry.write_text('print(1)', encoding='utf8')
        self.sid = 'S-1-5-21-123-456-789-1001'

    def tearDown(self):
        self.temp.cleanup()

    def owned(self):
        return {'installed': True, 'user_id': self.sid, 'description': background.TASK_DESCRIPTION}

    def test_xml_has_recovery_triggers_and_interactive_nonadmin_principal(self):
        tree = ET.fromstring(background.task_xml(sys.executable, self.entry, self.sid, self.root / 'data.sqlite3', self.root, datetime(2026,10,3,9)))
        ns = {'t': background.TASK_NS}
        def value(path): return tree.find(path, ns).text
        self.assertEqual(value('t:RegistrationInfo/t:URI'), '\\' + background.TASK_NAME)
        self.assertEqual(value('t:Triggers/t:LogonTrigger/t:UserId'), self.sid)
        self.assertEqual(value('t:Triggers/t:TimeTrigger/t:Repetition/t:Interval'), 'PT1M')
        self.assertEqual(value('t:Triggers/t:TimeTrigger/t:StartBoundary'), '2026-10-03T09:01:00')
        self.assertEqual(value('t:Principals/t:Principal/t:LogonType'), 'InteractiveToken')
        self.assertEqual(value('t:Principals/t:Principal/t:RunLevel'), 'LeastPrivilege')
        self.assertEqual(value('t:Settings/t:MultipleInstancesPolicy'), 'IgnoreNew')
        self.assertEqual(value('t:Settings/t:StartWhenAvailable'), 'true')
        self.assertEqual(value('t:Settings/t:ExecutionTimeLimit'), 'PT0S')
        self.assertEqual(value('t:Settings/t:RestartOnFailure/t:Interval'), 'PT1M')
        self.assertEqual(value('t:Settings/t:RestartOnFailure/t:Count'), '999')
        self.assertEqual(value('t:Settings/t:DisallowStartIfOnBatteries'), 'false')
        arguments = value('t:Actions/t:Exec/t:Arguments')
        command = value('t:Actions/t:Exec/t:Command')
        expected_python=Path(sys.executable).resolve()
        expected_pythonw=expected_python.with_name('pythonw.exe')
        self.assertEqual(Path(command),expected_pythonw if expected_pythonw.is_file() else expected_python)
        self.assertIn('--background --database',arguments)
        self.assertIn('-c ',arguments)
        self.assertIn('sys.path.insert',arguments)
        self.assertIn('中文 folder',arguments)
        self.assertNotIn('EncodedCommand',arguments)
        self.assertNotIn('powershell',command.casefold())

    def test_missing_runtime_and_dependencies_are_rejected(self):
        with self.assertRaises(background.BackgroundTaskError):
            background.task_xml(self.root / 'not.exe', self.entry, self.sid)
        with self.assertRaises(background.BackgroundTaskError):
            background.task_xml(sys.executable, self.entry, self.sid, dependencies_path=self.root / 'missing')

    def test_source_uses_pythonw_and_discovered_dependencies_without_bootstrap(self):
        python=self.root/'python.exe'; python.write_bytes(b'placeholder')
        pythonw=self.root/'pythonw.exe'; pythonw.write_bytes(b'placeholder')
        project=self.root/'project'
        entry=project/'outputs/notes_app/app/main.py'
        entry.parent.mkdir(parents=True); entry.write_text('pass',encoding='utf-8')
        dependencies=project/'work/dependencies'; dependencies.mkdir(parents=True)
        tree=ET.fromstring(background.task_xml(python,entry,self.sid,dependencies_path=dependencies,frozen=False))
        ns={'t':background.TASK_NS}
        self.assertEqual(tree.find('t:Actions/t:Exec/t:Command',ns).text,str(pythonw))
        arguments=tree.find('t:Actions/t:Exec/t:Arguments',ns).text
        self.assertIn('main.py',arguments)
        self.assertNotIn('-c ',arguments)
        self.assertNotIn('EncodedCommand',arguments)

    def test_source_custom_dependencies_use_windowless_python_bootstrap(self):
        python=self.root/'python.exe'; python.write_bytes(b'placeholder')
        pythonw=self.root/'pythonw.exe'; pythonw.write_bytes(b'placeholder')
        tree=ET.fromstring(background.task_xml(python,self.entry,self.sid,dependencies_path=self.root,frozen=False))
        ns={'t':background.TASK_NS}
        self.assertEqual(tree.find('t:Actions/t:Exec/t:Command',ns).text,str(pythonw))
        arguments=tree.find('t:Actions/t:Exec/t:Arguments',ns).text
        self.assertIn('-c ',arguments)
        self.assertIn('runpy.run_path',arguments)
        self.assertIn('sys.path.insert',arguments)
        self.assertNotIn('powershell',arguments.casefold())

    def test_install_uses_utf16_xml_and_cleans_temporary_file(self):
        commands=[]
        def powershell(script):
            commands.append(script)
            self.assertIn('Register-ScheduledTask', script)
            marker='[IO.File]::ReadAllText('
            argument=script.split(marker)[1].split('))')[0]
            path=Path(argument[1:-1].replace("''", "'"))
            commands.append(path)
            text=path.read_text(encoding='utf-16')
            self.assertIn('encoding="UTF-16"', text)
            ET.fromstring(text)
            return ''
        with patch.object(background, '_current_sid', return_value=self.sid), patch.object(background, 'inspect_background', side_effect=[{'installed':False},self.owned()]), patch.object(background, '_powershell', side_effect=powershell), patch.object(background, 'start_background') as start:
            result=background.install_background(sys.executable, self.entry, database_path=self.root/'database.sqlite3')
        self.assertTrue(result['installed'])
        start.assert_called_once()
        self.assertFalse(commands[1].exists())
        self.assertNotIn('-RunLevel Highest', commands[0])

    def test_external_installer_can_explicitly_select_frozen_executable(self):
        executable=self.root/'OrangeNotes.exe'; executable.write_bytes(b'placeholder')
        with patch.object(background,'_current_sid',return_value=self.sid),patch.object(background,'inspect_background',side_effect=[{'installed':False},self.owned()]),patch.object(background,'task_xml',return_value='<Task/>') as xml,patch.object(background,'_powershell',return_value=''):
            background.install_background(executable,None,database_path=self.root/'database.sqlite3',start=False,frozen=True)
        self.assertTrue(xml.call_args.kwargs['frozen'])
        self.assertIsNone(xml.call_args.args[1])

    def test_install_error_cleans_file_and_does_not_start(self):
        paths=[]
        def failure(script):
            argument=script.split('[IO.File]::ReadAllText(')[1].split('))')[0]
            paths.append(Path(argument[1:-1].replace("''", "'")))
            raise background.BackgroundTaskError('registration denied')
        with patch.object(background, '_current_sid', return_value=self.sid), patch.object(background, 'inspect_background', return_value={'installed':False}), patch.object(background, '_powershell', side_effect=failure), patch.object(background, 'start_background') as start:
            with self.assertRaises(background.BackgroundTaskError):
                background.install_background(sys.executable, self.entry)
        start.assert_not_called()
        self.assertFalse(paths[0].exists())

    def test_foreign_task_is_never_changed(self):
        for status in [{'installed':True,'description':'different application','user_id':self.sid}, {'installed':True,'description':background.TASK_DESCRIPTION,'user_id':'S-1-5-18'}]:
            with patch.object(background, '_current_sid', return_value=self.sid), patch.object(background, 'inspect_background', return_value=status), patch.object(background, '_powershell') as executor:
                for call in [lambda: background.install_background(sys.executable,self.entry),background.start_background,background.remove_background]:
                    with self.assertRaises(background.BackgroundTaskError): call()
                executor.assert_not_called()

    def test_remove_missing_task_has_no_effect(self):
        with patch.object(background, 'inspect_background', return_value={'installed':False}), patch.object(background, '_powershell') as executor:
            self.assertFalse(background.remove_background()['installed'])
            executor.assert_not_called()

    def test_remove_owned_task_only_stops_and_unregisters_fixed_name(self):
        with patch.object(background, '_current_sid', return_value=self.sid), patch.object(background, 'inspect_background', side_effect=[self.owned(),{'installed':False}]), patch.object(background, '_powershell', return_value='') as executor:
            self.assertFalse(background.remove_background()['installed'])
            script=executor.call_args.args[0]
            self.assertIn("Stop-ScheduledTask -TaskName 'OrangeNotes.Reminders'",script)
            self.assertIn("Unregister-ScheduledTask -TaskName 'OrangeNotes.Reminders'",script)
            self.assertNotIn('*',script)

    def test_inspection_malformed_output_is_clear_error(self):
        with patch.object(background, '_powershell', return_value='not json'):
            with self.assertRaises(background.BackgroundTaskError): background.inspect_background()

    @unittest.skipUnless(sys.platform=='win32','Windows subprocess transport')
    def test_powershell_error_preserves_unicode_message(self):
        message='Отказано в доступе 🧡'
        payload=b'ORANGE_NOTES_ERROR:'+base64.b64encode(message.encode('utf8'))
        result=Mock(returncode=1,stdout=payload,stderr=b'other encoding')
        with patch.object(background.subprocess,'run',return_value=result):
            with self.assertRaisesRegex(background.BackgroundTaskError,'Отказано в доступе') as raised:
                background._run(['powershell.exe'])
        self.assertEqual(str(raised.exception),message)


if __name__=='__main__':
    unittest.main(verbosity=2)
