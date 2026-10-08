import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from app.database.store import Store
from app.main import notification_action
from app.services.scheduler import Scheduler
from app.services.windows_notifications import WindowsNotifications

class ProtocolTests(unittest.TestCase):
    def test_done_and_snooze_target_actual_event_and_reject_stale_links(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'notes.sqlite3')
            try:
                note=store.create_note()
                store.save_note(note,'','<p>Посмотреть почту</p>',None,False,'')
                now=datetime.now()
                store.save_reminder(note,'once',(now+timedelta(seconds=1)).isoformat(),[],[])
                store.execute('UPDATE reminders SET created_utc=NULL,created_at=?',( (now-timedelta(minutes=1)).isoformat(),))
                store.execute('UPDATE reminders SET once_at=?',((now-timedelta(seconds=1)).isoformat(),))
                Scheduler(store,lambda *a:None).tick()
                row=store.rows('SELECT id FROM reminder_events')[0]
                event=store.notification_event(row['id'])
                self.assertEqual(event['body'],'Посмотреть почту')
                uri=WindowsNotifications.activation_uri('snooze',event['id'],event['token'])
                result=notification_action(store,uri)
                self.assertEqual(result['status'],'pending')
                self.assertIsNone(notification_action(store,uri))
                updated=store.notification_event(event['id'])
                result=notification_action(store,WindowsNotifications.activation_uri('done',updated['id'],updated['token']))
                self.assertEqual(result['status'],'done')
                self.assertIsNone(notification_action(store,WindowsNotifications.activation_uri('done',updated['id'],'invalid')))
            finally:store.db.close()
    def test_untrusted_protocol_fields_rejected(self):
        for uri in ['https://example.com','orange-notes://notification?action=open&event=1','orange-notes://notification?action=open&action=done&event=1&token=t']:
            with self.assertRaises(ValueError):notification_action(None,uri)

if __name__=='__main__':unittest.main()
