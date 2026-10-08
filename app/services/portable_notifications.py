"""Native POSIX notifications; submission is not proof that a user saw a banner."""
import html
import json
import re
import queue
import shutil
import subprocess
import sys
import threading
import time

_mac_delegate_type = None

def notification_transport(database_path=None):
    if sys.platform == 'win32':
        from app.services.windows_notifications import WindowsNotifications
        return WindowsNotifications()
    if sys.platform == 'darwin':
        return MacNotifications(database_path)
    if sys.platform == 'linux':
        from app.utils.sandbox import in_flatpak
        if in_flatpak():
            from app.services.portal_notifications import PortalNotifications
            return PortalNotifications(database_path)
        return LinuxNotifications(database_path)
    raise OSError('Системные уведомления не поддерживаются этой ОС.')


class LinuxNotifications:
    def __init__(self, database_path=None):
        self.database_path = database_path
        self.executable = shutil.which('notify-send')
        self.processes = {}
        self.identifiers = {}
        self.legacy_client = False
        self.last_action_error = None

    def register(self, database_path=None):
        self.database_path = database_path or self.database_path
        if not self.executable:
            raise OSError('Установите пакет libnotify (notify-send) для системных уведомлений.')

    def status(self):
        return {'setting': 'Unknown' if self.executable else 'Unavailable',
                'history_supported': False, 'adapter': 'FreeDesktop/libnotify',
                'last_action_error': self.last_action_error,
                'actions_supported': not self.legacy_client}

    def show(self, title, body, event_id, token):
        self.register()
        if event_id in self.identifiers:
            self.remove(event_id)
        if len(self.identifiers) >= 64:
            raise OSError('Очередь активных уведомлений заполнена; событие будет доставлено позже.')
        if self.legacy_client:
            return self._show_legacy(title, body, event_id)
        arguments = [self.executable, '--app-name=Orange Notes', '--urgency=critical',
                     '--expire-time=0', '--wait', '--print-id', '--action=done=Выполнено',
                     '--action=snooze=Отложить на 10 минут', '--', str(title), html.escape(str(body))]
        try:
            process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError as error:
            raise OSError('Не удалось обратиться к службе уведомлений: ' + str(error)) from error
        # notify-send prints the D-Bus notification ID after the server accepts it.
        # Read on a daemon thread so an unresponsive notification daemon is bounded.
        acknowledgement = queue.Queue(maxsize=1)
        threading.Thread(target=lambda: acknowledgement.put(process.stdout.readline()), daemon=True).start()
        try:
            identifier = acknowledgement.get(timeout=5).strip()
        except queue.Empty:
            process.kill()
            process.wait(timeout=5)
            raise OSError('Служба уведомлений не ответила на запрос.')
        if not identifier.isdecimal() or int(identifier) < 1:
            process.kill()
            _, stderr = process.communicate(timeout=5)
            if ('unknown option' in stderr.lower() or 'unrecognized option' in stderr.lower()) and any(
                    flag in stderr for flag in ('--wait', '--print-id', '--action')):
                self.legacy_client = True
                return self._show_legacy(title, body, event_id)
            raise OSError(stderr.strip() or 'Служба уведомлений не подтвердила приём сообщения.')
        self.processes[event_id] = process
        self.identifiers[event_id] = int(identifier)
        threading.Thread(target=self._action, args=(process, event_id, token), daemon=True).start()
        return {'submitted': True, 'deliveryVerified': True, 'historyVerified': False,
                'actionsSupported': True,
                'verification': 'libnotify request accepted; banner/history controlled by desktop'}

    def _show_legacy(self, title, body, event_id):
        """libnotify <0.8 has no wait/actions CLI; retain a real closable D-Bus ID."""
        executable = shutil.which('gdbus')
        if not executable:
            raise OSError('Для старой версии libnotify нужен gdbus (GLib).')
        result = subprocess.run([
            executable, 'call', '--session', '--dest', 'org.freedesktop.Notifications',
            '--object-path', '/org/freedesktop/Notifications', '--method',
            'org.freedesktop.Notifications.Notify',
            json.dumps('Orange Notes'), '0', json.dumps(''),
            json.dumps(str(title), ensure_ascii=False),
            json.dumps(html.escape(str(body)), ensure_ascii=False),
            '[]', "{'urgency': <byte 2>, 'resident': <true>}", '0'],
            capture_output=True, text=True, timeout=5)
        identifier = re.fullmatch(r'\(uint32 ([1-9][0-9]*),\)\s*', result.stdout.strip())
        if result.returncode or not identifier:
            raise OSError(result.stderr.strip() or 'D-Bus не подтвердил приём уведомления.')
        self.identifiers[event_id] = int(identifier.group(1))
        return {'submitted': True, 'deliveryVerified': True, 'historyVerified': False,
                'actionsSupported': False,
                'verification': 'D-Bus request accepted; legacy libnotify has no action callbacks'}

    def _action(self, process, event_id, token):
        try:
            output, _ = process.communicate(timeout=3600)
            if process.returncode == 0:
                self._apply(output.strip(), event_id, token)
        except (OSError, subprocess.TimeoutExpired) as error:
            self.last_action_error = str(error)
            process.kill()
            process.communicate()
        finally:
            if self.processes.get(event_id) is process:
                self.processes.pop(event_id, None)
                self.identifiers.pop(event_id, None)

    def _apply(self, action, event_id, token):
        if action not in ('done', 'snooze') or not self.database_path:
            return
        from app.database.store import Store
        store = Store(self.database_path)
        try:
            store.handle_notification_action(event_id, token, action)
        except Exception as error:
            self.last_action_error = str(error)
        finally:
            store.db.close()

    def remove(self, event_id):
        identifier = self.identifiers.get(event_id)
        if identifier:
            executable = shutil.which('gdbus')
            if not executable:
                raise OSError('Для удаления системного уведомления нужен gdbus (GLib).')
            result = subprocess.run([executable, 'call', '--session', '--dest', 'org.freedesktop.Notifications',
                                     '--object-path', '/org/freedesktop/Notifications', '--method',
                                     'org.freedesktop.Notifications.CloseNotification', str(identifier)],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode:
                raise OSError(result.stderr.strip() or 'Не удалось удалить системное уведомление.')
        process = self.processes.pop(event_id, None)
        self.identifiers.pop(event_id, None)
        if process and process.poll() is None:
            process.terminate()
        # FreeDesktop does not guarantee a persistent notification history.

    def pump(self):
        pass


class MacNotifications:
    def __init__(self, database_path=None):
        self.database_path = database_path
        self.center = None
        self.delegate = None
        self.authorized = False
        self.authorization_setting = 'Unknown'
        self.last_action_error = None

    def register(self, database_path=None):
        global _mac_delegate_type
        self.database_path = database_path or self.database_path
        if self.authorized and self.center is not None and self.delegate is not None:
            return
        try:
            import objc
            import Foundation
            import UserNotifications as UN
        except ImportError as error:
            raise OSError('В macOS-сборке отсутствует адаптер UserNotifications (PyObjC).') from error
        if not Foundation.NSBundle.mainBundle().bundleIdentifier():
            raise OSError('Уведомления macOS требуют установленного .app с Bundle Identifier.')
        if _mac_delegate_type is None:
            class OrangeNotesNotificationDelegate(Foundation.NSObject):
                __pyobjc_protocols__ = [objc.protocolNamed('UNUserNotificationCenterDelegate')]

                def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(self, center, response, completion):
                    try:
                        action = str(response.actionIdentifier())
                        info = response.notification().request().content().userInfo()
                        if action in ('done', 'snooze') and self.owner.database_path:
                            from app.database.store import Store
                            store = Store(self.owner.database_path)
                            try:
                                store.handle_notification_action(int(info['event']), str(info['token']), action)
                            finally:
                                store.db.close()
                    except Exception as error:
                        # Never allow a Python exception to cross the Objective-C callback boundary.
                        self.owner.last_action_error = str(error)
                    finally:
                        completion()

                def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, center, notification, completion):
                    completion(UN.UNNotificationPresentationOptionBanner | UN.UNNotificationPresentationOptionSound)
            _mac_delegate_type = OrangeNotesNotificationDelegate

        self.center = UN.UNUserNotificationCenter.currentNotificationCenter()
        self.delegate = _mac_delegate_type.alloc().init()
        self.delegate.owner = self
        self.center.setDelegate_(self.delegate)
        done = UN.UNNotificationAction.actionWithIdentifier_title_options_('done', 'Выполнено', 0)
        snooze = UN.UNNotificationAction.actionWithIdentifier_title_options_('snooze', 'Отложить на 10 минут', 0)
        category = UN.UNNotificationCategory.categoryWithIdentifier_actions_intentIdentifiers_options_('OrangeNotesReminder', [done, snooze], [], 0)
        self.center.setNotificationCategories_({category})
        result = {}
        self.center.requestAuthorizationWithOptions_completionHandler_(UN.UNAuthorizationOptionAlert | UN.UNAuthorizationOptionSound, lambda granted, error: result.update(granted=bool(granted), error=error))
        self._wait(result, timeout=120)
        if result.get('error'):
            self._raise_authorization_error(result['error'])
        self.authorization_setting = 'Enabled' if result.get('granted') else 'Denied'
        if not result.get('granted'):
            raise OSError('macOS запрещает уведомления Orange Notes. Разрешите их в настройках системы.')
        self.authorized = True

    def _raise_authorization_error(self, error):
        # Apple's permission rejection may arrive as NSError, before granted=False.
        # Do not mistake other framework/service failures for a user denial.
        try:
            denied = str(error.domain()) == 'UNErrorDomain' and int(error.code()) == 1
        except (AttributeError, TypeError, ValueError):
            denied = False
        self.authorized = False
        self.authorization_setting = 'Denied' if denied else 'Unknown'
        raise OSError('macOS notification authorization request failed: '+str(error))

    def _wait(self, result, timeout=5):
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            self.pump()
        if not result:
            raise OSError('macOS не ответила на запрос уведомлений.')

    def pump(self):
        import Foundation
        Foundation.NSRunLoop.currentRunLoop().runUntilDate_(Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.05))

    def status(self):
        if self.center is not None:
            result = {}
            try:
                self.center.getNotificationSettingsWithCompletionHandler_(
                    lambda settings: result.update(status=int(settings.authorizationStatus())))
                self._wait(result)
                self.authorization_setting = {0: 'NotDetermined', 1: 'Denied', 2: 'Enabled',
                                              3: 'Provisional', 4: 'Ephemeral'}.get(result['status'], 'Unknown')
                self.authorized = result['status'] in (2, 3, 4)
            except (OSError, AttributeError, TypeError, ValueError):
                self.authorization_setting = 'Unknown'
                self.authorized = False
        return {'setting': self.authorization_setting, 'adapter': 'Apple UserNotifications', 'history_supported': True,
                'last_action_error': self.last_action_error}

    def show(self, title, body, event_id, token):
        if not self.authorized:
            self.register()
        import UserNotifications as UN
        content = UN.UNMutableNotificationContent.alloc().init()
        content.setTitle_(str(title))
        content.setBody_(str(body))
        content.setCategoryIdentifier_('OrangeNotesReminder')
        content.setUserInfo_({'event': event_id, 'token': token})
        content.setSound_(UN.UNNotificationSound.defaultSound())
        request = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_('OrangeNotes.' + str(event_id), content, None)
        result = {}
        self.center.addNotificationRequest_withCompletionHandler_(request, lambda error: result.update(error=error))
        self._wait(result)
        if result['error']:
            raise OSError(str(result['error']))
        return {'submitted': True, 'deliveryVerified': True, 'historyVerified': False,
                'verification': 'UserNotifications request accepted; display controlled by macOS'}

    def remove(self, event_id):
        if self.center is None:
            import UserNotifications as UN
            self.center = UN.UNUserNotificationCenter.currentNotificationCenter()
        if self.center:
            identifiers = ['OrangeNotes.' + str(event_id)]
            self.center.removePendingNotificationRequestsWithIdentifiers_(identifiers)
            self.center.removeDeliveredNotificationsWithIdentifiers_(identifiers)
