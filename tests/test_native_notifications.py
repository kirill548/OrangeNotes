"""Native transport contract tests; no OS registrations or notifications."""
import sys,unittest,json,subprocess,xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services.windows_notifications import WindowsNotifications

class NativeContract(unittest.TestCase):
 def test_unicode_escaping_and_action_tokens(self):
  n=WindowsNotifications();xml=ET.fromstring(n.payload('Без названия <&>','Посмотреть почту — 東京',31,'x&y=1'))
  self.assertEqual(xml.find('./visual/binding/text').text,'Без названия <&>');actions=xml.findall('./actions/action');self.assertEqual(len(actions),2)
  self.assertEqual(xml.attrib['scenario'],'reminder')
  for action,name in zip(actions,['done','snooze']):
   q=parse_qs(urlparse(action.attrib['arguments']).query);self.assertEqual(q,{'action':[name],'event':['31'],'token':['x&y=1']});self.assertEqual(action.attrib['activationType'],'protocol')
 def test_stable_duplicate_identity(self):
  n=WindowsNotifications()
  with patch.object(n,'_run',return_value={'ok':True}) as run:
   n.show('A','B',7,'token');first=run.call_args;n.show('A','B',7,'token');self.assertEqual(first,run.call_args);self.assertEqual(run.call_args.kwargs['tag'],'7');self.assertEqual(run.call_args.kwargs['group'],'reminders')
 def test_registration_can_skip_protocol(self):
  n=WindowsNotifications()
  with patch.object(n,'_run',return_value={'ok':True}) as run:n.register(register_protocol=False);self.assertFalse(run.call_args.kwargs['register_protocol'])
 def test_registration_pins_explicit_database(self):
  n=WindowsNotifications()
  database=Path('test database.sqlite3').resolve()
  with patch.object(n,'_run',return_value={'ok':True}) as run:
   n.register(database_path=database)
   self.assertEqual(run.call_args.kwargs['database'],str(database))
 def test_os_error_and_timeout_cleanup(self):
  n=WindowsNotifications()
  with patch('app.services.windows_notifications.subprocess.run',return_value=subprocess.CompletedProcess([],1,json.dumps({'ok':False,'error':'DisabledForUser'}),'')):
   with self.assertRaisesRegex(OSError,'DisabledForUser'):n.history()
  with patch('app.services.windows_notifications.subprocess.run',side_effect=subprocess.TimeoutExpired('ps',25)):
   with self.assertRaisesRegex(OSError,'timed out'):n.history()

if __name__=='__main__':unittest.main()
