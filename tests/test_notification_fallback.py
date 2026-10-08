import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.services.notification_health import read_notification_health
from app.ui.window import Window
from app.utils.timezones import utc_stamp


class NotificationFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'notes.sqlite3')
        with patch.object(Window,'tick',lambda self:None):
            self.window=Window(self.store,background_managed=True)
        self.window.clock.stop()
        self.window.startup_tick.stop()

    def tearDown(self):
        self.window.quitting=True
        self.window.tray.hide()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.store.db.close()
        self.tmp.cleanup()

    def health(self,setting,age=0):
        state={'running':True,'heartbeat_utc':(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat(),
               'notification_status':{'setting':setting,'fallback_required':setting!='Enabled'}}
        (self.store.path.parent/'worker_status.json').write_text(json.dumps(state),encoding='utf-8')

    def event(self,body='Посмотреть почту <literal>',offset=-2):
        note=self.store.create_note()
        self.store.save_note(note,'','<p>'+body+'</p>',None,False,'')
        self.store.save_reminder(note,'once',(datetime.now()+timedelta(days=1)).isoformat(),[],[])
        r=self.store.reminder(note)
        due=datetime.now()+timedelta(minutes=offset)
        event=self.store.execute("INSERT INTO reminder_events(reminder_id,scheduled_at,due_at,scheduled_utc,due_utc,status,revision,title,body,token) VALUES (?,?,?,?,?,'pending',?,'Без названия',?,?)",
            (r['id'],due.isoformat(),due.isoformat(),utc_stamp(due),utc_stamp(due),r['revision'],body,'test-token-'+str(note))).lastrowid
        return note,event

    def test_disabled_preview_plaintext_no_modal_and_pending_retained(self):
        self.event()
        self.health('DisabledForUser')
        self.window.show()
        self.window.update_attention()
        self.assertFalse(self.window.notification_fallback.isHidden())
        self.assertIn('Посмотреть почту <literal>',self.window.notification_fallback.preview.text())
        self.assertIsNone(self.app.activeModalWidget())
        self.assertEqual(self.store.rows('SELECT status FROM reminder_events')[0][0],'pending')
        self.assertIn('отключены',self.window.tray.toolTip())

    def test_fifty_events_grouped_without_fifty_popups(self):
        for i in range(50):
            self.event('Задача '+str(i))
        self.health('DisabledForApplication')
        self.window.update_attention()
        self.assertIn('50',self.window.notification_fallback.preview.text())
        self.assertIn('50',self.window.tray.toolTip())
        self.assertIsNone(self.app.activeModalWidget())

    def test_snooze_clears_fallback_until_due(self):
        _,event=self.event()
        self.health('DisabledForUser')
        self.window.update_attention()
        current=self.store.notification_event(event)
        self.store.handle_notification_action(event,current['token'],'snooze')
        self.window.update_attention()
        self.assertTrue(self.window.notification_fallback.isHidden())
        self.assertTrue(self.window.attention_button.isHidden())

    def test_enabled_fresh_status_overrides_previous_error(self):
        _,event=self.event()
        self.store.execute('UPDATE reminder_events SET last_error=? WHERE id=?',('DisabledForUser',event))
        self.health('Enabled')
        self.assertFalse(read_notification_health(self.store)['blocked'])

    def test_stale_or_corrupt_health_not_reported_as_permission_denial(self):
        self.health('DisabledForUser',age=200)
        self.assertFalse(read_notification_health(self.store)['blocked'])
        (self.store.path.parent/'worker_status.json').write_text('broken',encoding='utf-8')
        self.assertFalse(read_notification_health(self.store)['blocked'])

    def test_persisted_failure_survives_worker_restart(self):
        _,event=self.event()
        self.store.execute('UPDATE reminder_events SET last_error=? WHERE id=?',('Windows notifications unavailable: DisabledForUser',event))
        self.assertTrue(read_notification_health(self.store)['blocked'])
        self.window.hide()
        self.window.update_attention()
        self.assertFalse(self.window.isVisible())
        self.assertIn('отключены',self.window.tray.toolTip())

    def test_attention_uses_utc_after_travel_not_old_wall_clock(self):
        note,event=self.event()
        self.store.execute('UPDATE reminder_events SET due_at=? WHERE id=?',((datetime.now()+timedelta(days=2)).isoformat(),event))
        self.assertIn(note,self.window.update_attention())

    def test_foreign_zone_is_visible_and_event_time_converts_for_viewer(self):
        r={'mode':'repeat','days':[2],'times':['09:00'],'timezone_id':'Europe/Berlin'}
        with patch('app.ui.window.device_zone',return_value='America/New_York'):
            self.assertIn('Europe/Berlin',self.window.schedule_text(r))
        event={'due_utc':'2026-10-07T07:00:00','due_at':'2026-10-07T09:00:00',
               'scheduled_at':'2026-10-07T09:00:00','status':'pending'}
        with patch('app.utils.timezones.device_zone',return_value='America/New_York'):
            self.assertIn('03:00',self.window.event_text(event))

    def test_tray_badge_counts_events_not_notes_and_clears(self):
        _,event=self.event()
        self.store.execute("INSERT INTO reminder_events(reminder_id,scheduled_at,scheduled_utc,due_utc,status,revision,title,body,token) SELECT reminder_id,scheduled_at||'x',scheduled_utc,due_utc,status,revision,title,body,'second' FROM reminder_events WHERE id=?",(event,))
        self.window.update_attention()
        self.assertEqual(self.window._tray_badge_state[0],2)
        self.assertIn('Событий: 2',self.window.tray.toolTip())
        self.store.execute("UPDATE reminder_events SET status='done'")
        self.window.update_attention()
        self.assertEqual(self.window._tray_badge_state[0],0)
        self.assertNotIn('Событий:',self.window.tray.toolTip())
        from app.ui.tray_badge import badge_text, counted_icon
        self.assertEqual(badge_text(101),'99+')
        self.assertFalse(counted_icon(self.window.windowIcon(),101).isNull())

    def test_hidden_window_timer_refreshes_attention_after_live_zone_change(self):
        from PySide6.QtTest import QTest
        note,event=self.event(offset=60)
        self.window.hide()
        self.window.scope=('attention',None)
        self.window.clock.timeout.disconnect()
        self.window.clock.timeout.connect(self.window.tick)
        with patch('app.utils.timezones.device_zone',return_value='Europe/Berlin'), patch('app.ui.window.device_zone',return_value='Europe/Berlin'):
            self.window.refresh_list()
            self.assertEqual(self.window.notes.count(),0)
        # Independent worker connection delivers an overdue event during travel.
        worker=Store(self.store.path)
        try:
            worker.execute("UPDATE reminder_events SET due_utc='2026-01-01T00:00:00',due_at='2099-01-01T00:00:00' WHERE id=?",(event,))
        finally: worker.db.close()
        with patch('app.utils.timezones.device_zone',return_value='America/New_York'), patch('app.ui.window.device_zone',return_value='America/New_York'):
            self.window.clock.start(10)
            QTest.qWait(80)
            self.window.clock.stop()
            self.assertEqual(self.window.notes.count(),1)
            self.assertEqual(self.window._attention_event_count,1)
            self.assertFalse(self.window.isVisible())
