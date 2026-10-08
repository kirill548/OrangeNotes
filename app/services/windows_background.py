"""Per-user Task Scheduler integration for the independent reminder worker."""
import base64
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from app.database.store import Store

TASK_NAME = 'OrangeNotes.Reminders'
TASK_DESCRIPTION = 'Orange Notes reminder worker (managed by Orange Notes).'
TASK_NS = 'http://schemas.microsoft.com/windows/2004/02/mit/task'


class BackgroundTaskError(RuntimeError):
    pass


def _powershell_path():
    return str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')


def _ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _encoded_command(script):
    return base64.b64encode(script.encode('utf-16le')).decode('ascii')


def _run(arguments):
    if os.name != 'nt':
        raise BackgroundTaskError('Фоновый запуск через планировщик доступен только в Windows.')
    settings = {'capture_output': True, 'timeout': 30, 'creationflags': subprocess.CREATE_NO_WINDOW}
    try:
        result = subprocess.run(arguments, **settings)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BackgroundTaskError('Не удалось обратиться к планировщику Windows: ' + str(error)) from error
    # PowerShell writes redirected Unicode output consistently after setting its encoding.
    stdout = result.stdout.decode('utf-8', errors='replace').strip()
    stderr = result.stderr.decode('utf-8', errors='replace').strip()
    if result.returncode:
        # Native Windows PowerShell error streams can use an OEM code page even
        # when stdout is UTF-8. Our catches transport messages as ASCII base64.
        for line in stdout.splitlines():
            if line.startswith('ORANGE_NOTES_ERROR:'):
                try:
                    message=base64.b64decode(line.split(':',1)[1]).decode('utf-8')
                except (ValueError,UnicodeError):
                    break
                raise BackgroundTaskError(message)
        raise BackgroundTaskError(stderr or stdout or 'Планировщик Windows вернул ошибку ' + str(result.returncode))
    return stdout


def _powershell(script):
    encoding = "$ProgressPreference='SilentlyContinue'; $OutputEncoding=[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false); "
    script=script.replace('[Console]::Error.WriteLine($_.Exception.Message)', "[Console]::Out.WriteLine('ORANGE_NOTES_ERROR:'+ [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($_.Exception.Message)))")
    return _run([_powershell_path(), '-NoLogo', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-EncodedCommand', _encoded_command(encoding + script)])


def _current_sid():
    value = _powershell('[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value')
    if not value.startswith('S-1-') or any(char not in 'S-0123456789' for char in value):
        raise BackgroundTaskError('Не удалось определить текущего пользователя Windows.')
    return value


def _existing_file(value, label):
    path = Path(value).resolve()
    if not path.is_file():
        raise BackgroundTaskError(label + ' не найден: ' + str(path))
    return path


def task_xml(python_path, entry_path, user_sid, database_path=None, dependencies_path=None, now=None, frozen=None):
    """Build XML without changing Task Scheduler; useful for review and tests."""
    interpreter = _existing_file(python_path, 'Python')
    frozen = bool(getattr(sys,'frozen',False)) if frozen is None else bool(frozen)
    if not frozen:
        pythonw = interpreter.with_name('pythonw.exe')
        if pythonw.is_file():
            interpreter = pythonw
    entry = None if frozen else _existing_file(entry_path, 'Файл приложения')
    working_directory = interpreter.parent if frozen else entry.parent.parent
    database = Path(database_path or Store.default_path()).resolve()
    dependencies = None
    if dependencies_path and not frozen:
        dependencies = Path(dependencies_path).resolve()
        if not dependencies.is_dir():
            raise BackgroundTaskError('Папка зависимостей не найдена: ' + str(dependencies))
    arguments = ([str(entry)] if entry else []) + ['--background', '--database', str(database)]
    if dependencies:
        discovered = entry.parent.parent.parents[1] / 'work' / 'dependencies' if len(entry.parent.parent.parents)>1 else None
        if dependencies != discovered:
            # Custom source runtimes need a Python path, not a console-producing
            # PowerShell parent. pythonw executes this bootstrap without a window.
            bootstrap = ('import sys,runpy;sys.path.insert(0,' + repr(str(dependencies)) + ');'
                         'sys.argv=[' + repr(str(entry)) + ']+sys.argv[1:];'
                         'runpy.run_path(' + repr(str(entry)) + ",run_name='__main__')")
            arguments = ['-c',bootstrap,'--background','--database',str(database)]
    args = subprocess.list2cmdline(arguments)
    ET.register_namespace('', TASK_NS)
    root = ET.Element('{' + TASK_NS + '}Task', {'version': '1.2'})
    def node(parent, name, text=None, **attributes):
        item = ET.SubElement(parent, '{' + TASK_NS + '}' + name, attributes)
        if text is not None: item.text = str(text)
        return item
    registration = node(root, 'RegistrationInfo')
    node(registration, 'Author', user_sid)
    node(registration, 'Description', TASK_DESCRIPTION)
    node(registration, 'URI', '\\' + TASK_NAME)
    triggers = node(root, 'Triggers')
    logon = node(triggers, 'LogonTrigger')
    node(logon, 'Enabled', 'true')
    node(logon, 'UserId', user_sid)
    timer = node(triggers, 'TimeTrigger')
    repetition = node(timer, 'Repetition')
    node(repetition, 'Interval', 'PT1M')
    node(repetition, 'StopAtDurationEnd', 'false')
    node(timer, 'StartBoundary', ((now or datetime.now()) + timedelta(minutes=1)).isoformat(timespec='seconds'))
    node(timer, 'Enabled', 'true')
    principals = node(root, 'Principals')
    principal = node(principals, 'Principal', id='CurrentUser')
    node(principal, 'UserId', user_sid)
    node(principal, 'LogonType', 'InteractiveToken')
    node(principal, 'RunLevel', 'LeastPrivilege')
    settings = node(root, 'Settings')
    for name, value in [('MultipleInstancesPolicy', 'IgnoreNew'), ('DisallowStartIfOnBatteries', 'false'),
                        ('StopIfGoingOnBatteries', 'false'), ('AllowHardTerminate', 'true'),
                        ('StartWhenAvailable', 'true'), ('RunOnlyIfNetworkAvailable', 'false')]:
        node(settings, name, value)
    idle = node(settings, 'IdleSettings')
    node(idle, 'StopOnIdleEnd', 'false')
    node(idle, 'RestartOnIdle', 'false')
    for name, value in [('AllowStartOnDemand', 'true'), ('Enabled', 'true'), ('Hidden', 'true'),
                        ('RunOnlyIfIdle', 'false'), ('WakeToRun', 'false'),
                        ('ExecutionTimeLimit', 'PT0S'), ('Priority', '7')]:
        node(settings, name, value)
    restart = node(settings, 'RestartOnFailure')
    node(restart, 'Interval', 'PT1M')
    node(restart, 'Count', '999')
    actions = node(root, 'Actions', Context='CurrentUser')
    action = node(actions, 'Exec')
    # The task monitors the actual long-running windowless worker. A console
    # wrapper would otherwise be started by every recovery trigger.
    node(action, 'Command', interpreter)
    node(action, 'Arguments', args)
    node(action, 'WorkingDirectory', working_directory)
    return ET.tostring(root, encoding='unicode', xml_declaration=False)


def inspect_background():
    script = '''$ErrorActionPreference='Stop'; try {
$t=Get-ScheduledTask -ErrorAction Stop | Where-Object {$_.TaskName -eq __TASK_LITERAL__ -and $_.TaskPath -eq '\\'};
if ($null -eq $t) { @{installed=$false;task_name=__TASK_LITERAL__} | ConvertTo-Json -Compress; exit 0 }
$i=Get-ScheduledTaskInfo -InputObject $t -ErrorAction Stop;
$userId=$t.Principal.UserId;
if ($userId -notmatch '^S-1-') { $account=New-Object System.Security.Principal.NTAccount -ArgumentList $userId; $userId=$account.Translate([System.Security.Principal.SecurityIdentifier]).Value }
@{installed=$true;task_name=$t.TaskName;state=[string]$t.State;description=$t.Description;user_id=$userId;last_result=$i.LastTaskResult;last_run=$i.LastRunTime.ToString('s');next_run=$i.NextRunTime.ToString('s')} | ConvertTo-Json -Compress
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }'''
    try:
        status = json.loads(_powershell(script.replace('__TASK_LITERAL__',_ps_literal(TASK_NAME))))
        if not isinstance(status, dict) or type(status.get('installed')) is not bool:
            raise ValueError('Invalid task status')
        return status
    except (ValueError, TypeError) as error:
        raise BackgroundTaskError('Планировщик вернул некорректный ответ.') from error


def _assert_owned(status, sid):
    if status.get('installed') and (status.get('description') != TASK_DESCRIPTION or status.get('user_id') != sid):
        raise BackgroundTaskError('Задача с таким именем принадлежит другому приложению или пользователю. Она не изменена.')


def install_background(python_path, entry_path, dependencies_path=None, database_path=None, start=True,frozen=None):
    sid = _current_sid()
    status = inspect_background()
    _assert_owned(status, sid)
    xml = task_xml(python_path, entry_path, sid, database_path, dependencies_path,frozen=frozen)
    task_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix='orange-notes-task-', suffix='.xml', delete=False) as handle:
            task_path = Path(handle.name)
            handle.write(('<?xml version="1.0" encoding="UTF-16"?>\n' + xml).encode('utf-16'))
        # Registration uses the current interactive user's rights; no passwords or elevation.
        _powershell('$ErrorActionPreference="Stop"; try { Register-ScheduledTask -TaskName ' + _ps_literal(TASK_NAME) + ' -TaskPath "\\" -Xml ([IO.File]::ReadAllText(' + _ps_literal(task_path) + ')) -Force -ErrorAction Stop | Out-Null } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }')
    finally:
        if task_path is not None: task_path.unlink(missing_ok=True)
    if start:
        if status.get('installed'):
            _powershell('$ErrorActionPreference="Stop"; try { Stop-ScheduledTask -TaskName ' + _ps_literal(TASK_NAME) + ' -TaskPath "\\" -ErrorAction Stop } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }')
        start_background()
    return inspect_background()


def start_background():
    status = inspect_background()
    if not status.get('installed'):
        raise BackgroundTaskError('Фоновая задача напоминаний ещё не установлена.')
    _assert_owned(status, _current_sid())
    _powershell('$ErrorActionPreference="Stop"; try { Start-ScheduledTask -TaskName ' + _ps_literal(TASK_NAME) + ' -TaskPath "\\" -ErrorAction Stop } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }')
    return inspect_background()


def remove_background():
    status = inspect_background()
    if not status.get('installed'):
        return status
    _assert_owned(status, _current_sid())
    _powershell('$ErrorActionPreference="Stop"; try { Stop-ScheduledTask -TaskName ' + _ps_literal(TASK_NAME) + ' -TaskPath "\\" -ErrorAction Stop; Unregister-ScheduledTask -TaskName ' + _ps_literal(TASK_NAME) + ' -TaskPath "\\" -Confirm:$false -ErrorAction Stop } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }')
    return inspect_background()
