"""Independent reminder process: no Qt imports and no editor dependency."""
from datetime import datetime, timedelta, timezone
import logging
import json
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sqlite3
import sys
import threading
import tempfile
import time

from app.database.store import Store
from app.services.scheduler import Scheduler
from app.services.windows_notifications import WindowsNotifications
from app.services.portable_notifications import notification_transport
from app.utils.timezones import event_scheduled_local


class WorkerLock:
    """Keep one worker per database; persistent file avoids unlink/reopen races."""
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        if os.name == 'nt':
            import msvcrt
        else:
            import fcntl
        handle = self.path.open('a+b')
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            try:
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                return False
        except Exception:
            handle.close()
            raise
        self.handle = handle
        return True

    def release(self):
        if self.handle is None: return
        try:
            if os.name == 'nt':
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def _logger(directory):
    logger=logging.Logger('OrangeNotes.ReminderWorker', level=logging.INFO)
    handler=RotatingFileHandler(directory/'reminder_worker.log', maxBytes=1024*1024, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    return logger


def notification_callback(store, transport, on_delivery=None):
    def notify(title, note_id, event_id):
        event=store.notification_event(event_id)
        if not event:
            raise OSError('Событие напоминания больше не доступно.')
        # Scheduler stores a plaintext snapshot; parsing it again would remove
        # literal angle brackets that the user typed into the note.
        body=str(event.get('body') or '')
        scheduled=event_scheduled_local(event)
        if scheduled < datetime.now()-timedelta(seconds=60):
            body='Пропущенное напоминание от '+scheduled.strftime('%d.%m, %H:%M')+'.\n'+body
        receipt=transport.show(event.get('title') or title or 'Без названия', body, event_id, event['token'])
        verified = (receipt.get('historyVerified') is True or
                    sys.platform != 'win32' and receipt.get('deliveryVerified') is True) if isinstance(receipt, dict) else False
        if not isinstance(receipt,dict) or receipt.get('submitted') is not True or not verified:
            raise OSError('Система не подтвердила принятие уведомления.')
        if on_delivery: on_delivery(event_id)
        return receipt
    return notify


def _write_status(directory, status):
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.worker-status-', suffix='.json', dir=directory, delete=False) as handle:
            temporary=Path(handle.name)
            json.dump(status,handle,ensure_ascii=False)
        os.replace(temporary,directory/'worker_status.json')
        temporary=None
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def reconcile_notifications(store, transport, platform_name):
    """Withdraw completed/snoozed/cancelled receipts, including GUI changes.

    Receipts deliberately have no foreign key: deleting a note must leave enough
    information to withdraw its native notification on the next worker tick.
    """
    receipts = store.rows('SELECT event_id,native_id FROM native_notification_receipts WHERE platform=?', (platform_name,))
    identifiers = getattr(transport, 'identifiers', None)
    for event_id, native_id in receipts:
        if isinstance(identifiers, dict) and native_id:
            identifiers.setdefault(event_id, int(native_id))
        active = store.rows("""SELECT 1 FROM reminder_events e JOIN reminders r ON r.id=e.reminder_id
            JOIN notes n ON n.id=r.note_id WHERE e.id=? AND e.status='notified'
            AND e.revision=r.revision AND r.enabled=1 AND n.deleted=0 AND n.archived=0""", (event_id,))
        if not active:
            transport.remove(event_id)
            store.execute('DELETE FROM native_notification_receipts WHERE platform=? AND event_id=?', (platform_name,event_id))


def run_worker(database_path=None, *, stop_event=None, poll_interval=2.0, transport=None, max_iterations=None):
    """Run until process termination; test hooks do not change production defaults."""
    path=Path(database_path or Store.default_path()).resolve()
    logger=None
    store=None
    lock=None
    stop_event=stop_event or threading.Event()
    status={'pid':os.getpid(),'database':str(path),'running':True,'last_error':None,'last_delivery':None}
    def heartbeat():
        status['heartbeat_utc']=datetime.now(timezone.utc).isoformat(timespec='seconds')
        status['heartbeat_local']=datetime.now().astimezone().isoformat(timespec='seconds')
        try:
            _write_status(path.parent,status)
        except OSError:
            if logger: logger.exception('Не удалось обновить состояние фонового процесса.')
    def delivered(event_id):
        if sys.platform in ('darwin', 'linux'):
            identifiers = getattr(transport, 'identifiers', None)
            native_id = identifiers.get(event_id) if isinstance(identifiers, dict) else None
            store.execute('INSERT OR REPLACE INTO native_notification_receipts(platform,event_id,native_id) VALUES(?,?,?)',
                          (sys.platform, event_id, str(native_id) if native_id is not None else None))
        status['last_delivery']={'event_id':event_id,'at':datetime.now(timezone.utc).isoformat(timespec='seconds')}
        heartbeat()
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        logger=_logger(path.parent)
        lock=WorkerLock(path.with_name(path.name+'.worker.lock'))
        if not lock.acquire():
            logger.info('Другой процесс уже обслуживает эту базу.')
            return 0
        store=Store(path)
        store.execute('CREATE TABLE IF NOT EXISTS native_notification_receipts(platform TEXT NOT NULL,event_id INTEGER NOT NULL,native_id TEXT,PRIMARY KEY(platform,event_id))')
        transport=transport or (WindowsNotifications() if sys.platform == 'win32' else notification_transport(path))
        scheduler=Scheduler(store,notification_callback(store,transport,delivered),batch_size=10)
        logger.info('Фоновый процесс напоминаний запущен.')
        iterations=0
        next_status_check=0.0
        while not stop_event.is_set():
            try:
                if time.monotonic()>=next_status_check and hasattr(transport,'status'):
                    next_status_check=time.monotonic()+60
                    try:
                        diagnostic=transport.status()
                        if isinstance(diagnostic,dict):
                            previous=status.get('notification_status') or {}
                            if diagnostic.get('setting')!=previous.get('setting'):
                                logger.info('Native notification status: setting=%s fallback=%s; worker uses OS transport, GUI reads heartbeat diagnostics.',diagnostic.get('setting','Unknown'),diagnostic.get('fallback_required',False))
                            status['notification_status']=diagnostic
                    except OSError as error:
                        status['notification_status']={'setting':'Unknown','probe_error':str(error)}
                scheduler.tick()
                if sys.platform in ('darwin', 'linux'):
                    reconcile_notifications(store, transport, sys.platform)
                if hasattr(transport, 'pump'):
                    transport.pump()
                status['last_error']=None
            except (sqlite3.Error,OSError,ValueError) as error:
                status['last_error']=str(error)
                from app.services.notification_health import BLOCKED_SETTINGS
                setting=next((value for value in BLOCKED_SETTINGS if value in str(error)),None)
                if setting:
                    status['notification_status']={'setting':setting,'native_enabled':False,'fallback_required':True}
                logger.exception('Не удалось обработать напоминания; повторная попытка через %.1f с.',poll_interval)
            heartbeat()
            iterations+=1
            if max_iterations is not None and iterations>=max_iterations: break
            stop_event.wait(poll_interval)
        return 0
    except (sqlite3.Error,OSError,ValueError) as error:
        status['last_error']=str(error)
        if logger:
            logger.exception('Не удалось запустить фоновый процесс напоминаний.')
        elif sys.stderr is not None:
            print('Не удалось открыть папку данных фонового процесса.',file=sys.stderr)
        return 1
    finally:
        if store is not None: store.db.close()
        if lock is not None and lock.handle is not None:
            status['running']=False
            heartbeat()
        if lock is not None: lock.release()
        if logger:
            for handler in tuple(logger.handlers):
                handler.close(); logger.removeHandler(handler)
