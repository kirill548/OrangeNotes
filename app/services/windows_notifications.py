"""Native Windows toast transport; registration is explicit, never on import."""
import os
import sys
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
from app.utils.runtime_paths import resource_path


class WindowsNotifications:
    def __init__(self, app_root=None, appid='OrangeNotes.Desktop'):
        default_root = Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]
        self.app_root = Path(app_root).resolve() if app_root else default_root
        self.appid = appid
        self.helper = resource_path('app/services/windows_toast.ps1')
        self._last_setting = None

    def _run(self, operation, **values):
        if os.name != 'nt':
            raise OSError('Native notifications require Windows.')
        filename = None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',suffix='.json',delete=False) as handle:
                json.dump({'operation':operation,'appid':self.appid,**values},handle,ensure_ascii=False)
                filename=handle.name
            powershell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
            try:
                result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(self.helper),'-RequestFile',filename],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=25,creationflags=subprocess.CREATE_NO_WINDOW)
            except subprocess.TimeoutExpired as error:
                raise OSError('Windows notification operation timed out.') from error
            try:
                response=json.loads(result.stdout.strip().lstrip('\ufeff'))
            except ValueError as error:
                raise OSError('Windows notification helper returned no valid receipt: '+result.stderr.strip()[:500]) from error
            if result.returncode or not response.get('ok'):
                raise OSError(response.get('error','Windows notification operation failed.'))
            return response
        finally:
            if filename:Path(filename).unlink(missing_ok=True)

    def register(self, python_executable=None, register_protocol=True, database_path=None):
        from app.database.store import Store
        database=Path(database_path).resolve() if database_path else Store.default_path().resolve()
        executable=Path(python_executable or sys.executable).resolve()
        frozen=bool(getattr(sys,'frozen',False))
        if not frozen:
            pythonw=executable.with_name('pythonw.exe')
            if pythonw.exists():executable=pythonw
        main=None if frozen else str(self.app_root/'app/main.py')
        arguments=([main] if main else [])+['--database',str(database)]
        icon=executable if frozen else resource_path('app/assets/app.ico')
        return self._run('register',python=str(executable),main=main,root=str(self.app_root),
                         launch_args=subprocess.list2cmdline(arguments),
                         protocol_command=subprocess.list2cmdline([str(executable),*arguments,'--notification'])+' "%1"',
                         icon=str(icon),frozen=frozen,register_protocol=bool(register_protocol),database=str(database))

    @staticmethod
    def activation_uri(action,event_id,token):
        if action not in ('open','done','snooze'):raise ValueError('Unknown notification action')
        return 'orange-notes://notification?'+urlencode({'action':action,'event':int(event_id),'token':token})

    def payload(self,title,body,event_id,token):
        toast=ET.Element('toast',{'activationType':'protocol','launch':self.activation_uri('open',event_id,token),'duration':'long','scenario':'reminder'})
        binding=ET.SubElement(ET.SubElement(toast,'visual'),'binding',{'template':'ToastGeneric'})
        ET.SubElement(binding,'text').text=str(title or 'Заметка')[:300]
        ET.SubElement(binding,'text').text=str(body or 'Откройте заметку, чтобы посмотреть подробности.')[:1200]
        actions=ET.SubElement(toast,'actions')
        for action,label in [('done','Выполнено'),('snooze','Отложить на 10 минут')]:
            ET.SubElement(actions,'action',{'content':label,'activationType':'protocol','arguments':self.activation_uri(action,event_id,token)})
        return ET.tostring(toast,encoding='unicode')

    def show(self,title,body,event_id,token):
        return self._run('show',xml=self.payload(title,body,event_id,token),tag=str(int(event_id)),group='reminders')

    def history(self):return self._run('history')['items']

    def status(self):
        response = self._run('status')
        setting = response.get('setting', 'Unknown')
        reasons = {
            'Enabled': ('Windows разрешает отправку уведомлений; показ баннера зависит от настроек концентрации.', ''),
            'DisabledForUser': ('Windows отключила уведомления для текущего пользователя. Точный переключатель API не сообщает.', 'Включите уведомления в Параметры → Система → Уведомления.'),
            'DisabledForApplication': ('Уведомления Orange Notes отключены в Windows.', 'Разрешите уведомления Orange Notes в настройках Windows.'),
            'DisabledByGroupPolicy': ('Уведомления запрещены групповой политикой Windows.', 'Обратитесь к администратору устройства.'),
            'DisabledByManifest': ('Windows не разрешает уведомления для текущей регистрации приложения.', 'Переустановите или восстановите регистрацию Orange Notes.'),
        }
        explanation, action = reasons.get(setting, ('Статус уведомлений Windows не определён.', 'Проверьте диагностику и настройки уведомлений.'))
        response.update(adapter='Windows.UI.Notifications', native_enabled=setting == 'Enabled',
                        fallback_required=setting != 'Enabled', reason_code=setting,
                        explanation=explanation, recommended_action=action,
                        settings_uri='ms-settings:notifications', evidence='ToastNotifier.Setting',
                        root_cause_known=setting in ('DisabledByGroupPolicy', 'DisabledByManifest'))
        if setting != self._last_setting:
            logging.getLogger(__name__).info('Notification transport status: appid=%s setting=%s fallback=%s evidence=ToastNotifier.Setting', self.appid, setting, response['fallback_required'])
            self._last_setting = setting
        return response

    def remove(self,event_id):return self._run('remove',tag=str(int(event_id)),group='reminders')
