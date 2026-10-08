"""User-session service adapters. Never run shell text assembled from user paths."""
from contextlib import contextmanager
import os
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time
import tempfile
import logging
import signal

LABEL = 'com.orangenotes.reminders'
UNIT = 'orangenotes-reminders.service'
MARKER = '# Managed by Orange Notes: reminder worker\n'


class BackgroundServiceError(RuntimeError):
    pass


def supported():
    from app.utils.sandbox import in_flatpak
    if in_flatpak():return False
    return sys.platform in ('win32', 'darwin', 'linux')


def _run(arguments, *, allow_failure=False):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BackgroundServiceError(str(error)) from error
    if result.returncode and not allow_failure:
        raise BackgroundServiceError(result.stderr.strip() or result.stdout.strip() or 'Не удалось изменить фоновую службу.')
    return result


def service_path():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'LaunchAgents' / (LABEL + '.plist')
    config = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    return config / 'systemd' / 'user' / UNIT



def autostart_path():
    config = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    if not config.is_absolute():
        config = Path.home() / '.config'
    return config / 'autostart' / 'org.orangenotes.Reminders.desktop'


def _desktop_argument(value):
    value = str(value)
    if any(c in value for c in ('\n', '\r', '\x00')):
        raise BackgroundServiceError('Недопустимый символ в пути автозапуска.')
    # Desktop Entry Exec has its own quoting rules; no command shell is used.
    quoted = value.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
    return '"' + quoted.replace('\\', '\\\\') + '"'


def xdg_entry(arguments, database):
    return (MARKER + '[Desktop Entry]\nType=Application\nName=Orange Notes reminders\n'
            'Comment=Deliver saved reminders in your desktop session\nTerminal=false\n'
            'X-GNOME-Autostart-enabled=true\nExec=' + ' '.join(_desktop_argument(a) for a in arguments) + '\n'
            'X-OrangeNotes-Arguments=' + json.dumps(arguments, ensure_ascii=False) + '\n'
            'X-OrangeNotes-Database=' + json.dumps(str(database), ensure_ascii=False) + '\n')


def _xdg_metadata(path):
    _assert_owned(path)
    fields = dict(line.split('=', 1) for line in path.read_text(encoding='utf-8').splitlines() if '=' in line)
    try:
        arguments = json.loads(fields['X-OrangeNotes-Arguments'])
        database = Path(json.loads(fields['X-OrangeNotes-Database']))
        if not isinstance(arguments, list) or not arguments or not all(isinstance(a, str) for a in arguments):
            raise ValueError('invalid arguments')
        return arguments, database
    except (KeyError, ValueError, TypeError) as error:
        raise BackgroundServiceError('Повреждены данные автозапуска Orange Notes.') from error


def _xdg_pid(path):
    arguments, database = _xdg_metadata(path)
    try:
        state = json.loads((database.parent / 'worker_status.json').read_text(encoding='utf-8'))
        pid = int(state['pid'])
        if pid <= 1 or not state.get('running') or state.get('database') != str(database):
            return None
        # Reject stale/reused PIDs, and never signal a process belonging to another user.
        proc = Path('/proc') / str(pid)
        if proc.stat().st_uid != os.getuid():
            return None
        command = proc.joinpath('cmdline').read_bytes().rstrip(b'\x00').split(b'\x00')
        if [os.fsdecode(part) for part in command] != arguments:
            return None
        return pid
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _inspect_xdg(path):
    pid = _xdg_pid(path)
    return {'installed': True, 'state': 'Running' if pid else 'Stopped',
            'task_name': path.name, 'service_path': str(path), 'enabled': True,
            'loaded': bool(pid), 'backend': 'xdg-autostart',
            'diagnostic': 'User systemd is unavailable; XDG starts reminders at desktop login. No automatic crash restart.'}


def _start_xdg(path):
    arguments, _ = _xdg_metadata(path)
    if not _xdg_pid(path):
        try:
            subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as error:
            raise BackgroundServiceError(str(error)) from error
    return _inspect_xdg(path)


def _manager_unavailable(error):
    text = str(error).lower()
    return any(word in text for word in ('failed to connect to bus', 'no medium found',
                                        'no such file or directory', 'not been booted with systemd',
                                        'transport endpoint is not connected'))


def worker_arguments(executable, entry_path, database_path, *, frozen=False):
    executable = Path(executable).resolve()
    if not executable.is_file():
        raise BackgroundServiceError('Исполняемый файл приложения не найден.')
    arguments = [str(executable)]
    if not frozen:
        entry = Path(entry_path).resolve()
        if not entry.is_file():
            raise BackgroundServiceError('Файл запуска приложения не найден.')
        arguments.append(str(entry))
    return arguments + ['--background', '--database', str(Path(database_path).resolve())]


def launchd_plist(arguments, database_path):
    directory = Path(database_path).resolve().parent
    return plistlib.dumps({'Label': LABEL, 'ProgramArguments': arguments,
                          'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 15,
                          'ProcessType': 'Background',
                          'StandardErrorPath': str(directory / 'background-service.log'),
                          'OrangeNotesManaged': True, 'OrangeNotesDatabase': str(Path(database_path).resolve())}, sort_keys=False)


def _unit_argument(value):
    # systemd performs % specifier and $ environment expansion even without a shell.
    value = str(value)
    if any(character in value for character in ('\n', '\r', '\x00')):
        raise BackgroundServiceError('Недопустимый символ в пути фоновой службы.')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def systemd_unit(arguments, database_path=None):
    command = ' '.join(_unit_argument(argument) for argument in arguments)
    metadata = '# Database: ' + json.dumps(str(Path(database_path).resolve()), ensure_ascii=False) + '\n' if database_path else ''
    return (MARKER + metadata + '[Unit]\nDescription=Orange Notes reminders\nAfter=graphical-session.target\n'
            '[Service]\nType=simple\nExecStart=' + command + '\nRestart=always\nRestartSec=15\n'
            'TimeoutStopSec=20\n[Install]\nWantedBy=default.target\n')


def _assert_owned(path):
    if path.is_symlink():
        raise BackgroundServiceError("Файл службы является символической ссылкой и не изменён.")
    if not path.exists():
        return
    try:
        if sys.platform == 'darwin':
            owned = plistlib.loads(path.read_bytes()).get('OrangeNotesManaged') is True
        else:
            owned = path.read_text(encoding='utf-8').startswith(MARKER)
    except (OSError, ValueError, plistlib.InvalidFileException):
        owned = False
    if not owned:
        raise BackgroundServiceError('Файл службы принадлежит другому приложению и не изменён.')


def _atomic_write(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.orangenotes-service-', delete=False) as output:
            temporary = Path(output.name)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def inspect_background():
    if sys.platform == 'win32':
        from app.services.windows_background import inspect_background as inspect
        return inspect()
    if sys.platform == 'linux' and autostart_path().is_file():
        return _inspect_xdg(autostart_path())
    path = service_path()
    _assert_owned(path)
    if sys.platform == 'darwin':
        result = _run(['launchctl', 'print', f'gui/{os.getuid()}/{LABEL}'], allow_failure=True)
        loaded = result.returncode == 0
        active = loaded and bool(re.search(r'(?m)^\s*pid = [1-9][0-9]*\s*$', result.stdout))
    elif sys.platform == 'linux':
        result = _run(['systemctl', '--user', 'is-active', UNIT], allow_failure=True)
        active = result.returncode == 0
        enabled_result = _run(['systemctl', '--user', 'is-enabled', UNIT], allow_failure=True)
        enabled = enabled_result.stdout.strip() in ('enabled', 'enabled-runtime')
    else:
        raise BackgroundServiceError('Фоновая служба не поддерживается этой ОС.')
    return {'installed': path.is_file(), 'state': 'Running' if active else 'Stopped',
            'task_name': LABEL if sys.platform == 'darwin' else UNIT,
            'service_path': str(path), 'enabled': enabled if sys.platform == 'linux' else True,
            'loaded': loaded if sys.platform == 'darwin' else active}


def install_background(executable, entry_path, dependencies_path=None, database_path=None, start=True, frozen=None):
    if sys.platform == 'win32':
        from app.services.windows_background import install_background as install
        return install(executable, entry_path, dependencies_path, database_path, start, frozen)
    if not supported():
        raise BackgroundServiceError('Фоновая служба не поддерживается этой ОС.')
    from app.utils.data_paths import database_path as default_database
    database = Path(database_path or default_database()).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    frozen = bool(getattr(sys, 'frozen', False)) if frozen is None else bool(frozen)
    arguments = worker_arguments(executable, entry_path, database, frozen=frozen)
    if dependencies_path and not frozen:
        # Fixed Python code, literal repr paths; no command shell is involved.
        code = ('import sys,runpy;sys.path.insert(0,' + repr(str(Path(dependencies_path).resolve())) + ');'
                'sys.argv=' + repr(arguments[1:]) + ';runpy.run_path(' + repr(arguments[1]) + ',run_name="__main__")')
        arguments = [arguments[0], '-c', code]
    path = service_path()
    _assert_owned(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == 'darwin':
        _run(['launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], allow_failure=True)
        _atomic_write(path, launchd_plist(arguments, database))
    else:
        fallback = autostart_path()
        _assert_owned(fallback)
        if fallback.is_file():
            _atomic_write(fallback, xdg_entry(arguments, database).encode('utf-8'))
            return _start_xdg(fallback) if start else _inspect_xdg(fallback)
        try:
            _run(['systemctl', '--user', 'daemon-reload'])
        except BackgroundServiceError as error:
            if not _manager_unavailable(error):
                raise
            logging.getLogger(__name__).warning('User systemd unavailable; using XDG Autostart: %s', error)
            fallback.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(fallback, xdg_entry(arguments, database).encode('utf-8'))
            return _start_xdg(fallback) if start else _inspect_xdg(fallback)
        _atomic_write(path, systemd_unit(arguments, database).encode('utf-8'))
        _run(['systemctl', '--user', 'enable', UNIT])
    if start:
        start_background()
    return inspect_background()


def start_background():
    if sys.platform == 'win32':
        from app.services.windows_background import start_background as start
        return start()
    if sys.platform == 'linux' and autostart_path().is_file():
        return _start_xdg(autostart_path())
    path = service_path()
    _assert_owned(path)
    if not path.is_file():
        raise BackgroundServiceError('Фоновая служба не установлена.')
    if sys.platform == 'darwin':
        if not inspect_background().get('loaded', False):
            _run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(path)])
        _run(['launchctl', 'kickstart', f'gui/{os.getuid()}/{LABEL}'])
    elif sys.platform == 'linux':
        _run(['systemctl', '--user', 'start', UNIT])
    return inspect_background()


@contextmanager
def pause_database_worker(database_path):
    """Prevent automatic restart and wait for exclusive database-worker lock."""
    state = inspect_background()
    installed = state.get('installed') is True
    running = state.get('state') == 'Running'
    loaded = state.get('loaded', running)
    disabled = state.get('state') == 'Disabled'
    from app.services.reminder_worker import WorkerLock
    path = Path(database_path).resolve()
    lock = WorkerLock(path.with_name(path.name + '.worker.lock'))
    changed = False
    try:
        if installed:
            if sys.platform == 'win32':
                from app.services import windows_background as windows
                windows._assert_owned(state, windows._current_sid())
                name = windows._ps_literal(windows.TASK_NAME)
                arguments = windows._powershell('$ErrorActionPreference="Stop"; (Get-ScheduledTask -TaskName ' + name + ' -TaskPath "\\").Actions.Arguments')
                expected = subprocess.list2cmdline(['--database', str(path)])
                if not arguments.strip().endswith(expected):
                    installed = False  # This registered task serves another database.
                else:
                    windows._powershell('$ErrorActionPreference="Stop"; Disable-ScheduledTask -TaskName ' + name + ' -TaskPath "\\" | Out-Null')
                    changed = True
                    windows._powershell('$ErrorActionPreference="Stop"; Stop-ScheduledTask -TaskName ' + name + ' -TaskPath "\\"')
            elif sys.platform == 'darwin':
                if plistlib.loads(service_path().read_bytes()).get('OrangeNotesDatabase') != str(path):
                    installed = False
                else:
                    _run(['launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], allow_failure=not running)
                    changed = True
            elif sys.platform == 'linux' and state.get('backend') == 'xdg-autostart':
                fallback = Path(state['service_path'])
                arguments, registered_database = _xdg_metadata(fallback)
                if registered_database != path:
                    installed = False
                else:
                    pid = _xdg_pid(fallback)
                    if running and pid is None:
                        raise BackgroundServiceError('Не удалось проверить процесс напоминаний; база не заменена.')
                    if pid:
                        os.kill(pid, signal.SIGTERM)
                    changed = True
            else:
                metadata = '# Database: ' + json.dumps(str(path), ensure_ascii=False)
                if metadata not in service_path().read_text(encoding='utf-8').splitlines():
                    installed = False
                else:
                    _run(['systemctl', '--user', 'stop', UNIT])
                    changed = True
        deadline = time.monotonic() + 20
        while not lock.acquire():
            if time.monotonic() >= deadline:
                raise BackgroundServiceError('Фоновый процесс не остановился; база не заменена.')
            time.sleep(0.1)
        yield
    finally:
        lock.release()
        if installed and changed:
            if sys.platform == 'win32':
                if not disabled:
                    windows._powershell('$ErrorActionPreference="Stop"; Enable-ScheduledTask -TaskName ' + name + ' -TaskPath "\\" | Out-Null')
                if running:
                    start_background()
            elif sys.platform == 'darwin':
                if loaded:
                    _run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(service_path())])
            elif running:
                start_background()
