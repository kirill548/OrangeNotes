"""Clean, build and install a developer bundle without touching user data.

Usage: python scripts/deploy_local.py --target work/local-install
The target contains a managed package/ directory; all other files are preserved.
Running instances must be closed before updating (they are never force killed).
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
MARKER = '.orange-notes-install.json'
PROTECTED = {'notes.db', 'notes.sqlite3', 'settings.json', 'ai_settings.json',
             'ai_packs', 'models', 'ollama_profile', 'runtime'}


def default_database_path():
    if sys.platform == 'win32':
        folder = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'OrangeNotes'
    elif sys.platform == 'darwin':
        folder = Path.home() / 'Library/Application Support/OrangeNotes'
    else:
        folder = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'OrangeNotes'
    return folder / 'notes.sqlite3'


def _process_alive(pid):
    """Probe process existence without sending terminating signals on Windows."""
    if pid <= 0:
        return False
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, no mutation rights
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # No such PID
                return False
            if error == 5:  # Insufficient rights: conservatively treat as alive
                return True
            raise OSError(error, 'Cannot safely inspect application process')
        try:
            status = kernel.WaitForSingleObject(handle, 0)
            if status == 258:
                return True
            if status == 0:
                return False
            raise OSError(ctypes.get_last_error(), 'Cannot safely inspect application process')
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def assert_application_closed(database=None):
    database = Path(database) if database is not None else default_database_path()
    lock = database.parent / 'app.lock'
    if not lock.exists():
        return
    try:
        with lock.open('r', encoding='utf-8') as source:
            line = source.readline(64).strip()
        pid = int(line)
    except (OSError, ValueError) as error:
        raise ValueError('Cannot inspect app.lock safely; close Orange Notes before updating') from error
    if _process_alive(pid):
        raise ValueError('Close Orange Notes, including its tray window, before updating (PID ' + str(pid) + ')')


def _protected(path):
    return any(part.lower() in PROTECTED or part.lower().endswith(
        ('.sqlite', '.sqlite-wal', '.sqlite-shm', '.sqlite3', '.sqlite3-wal', '.sqlite3-shm',
         '.db', '.db-wal', '.db-shm'))
        for part in Path(path).parts)


def _files(folder):
    result = []
    boundary = Path(folder).resolve()
    for current, directories, files in os.walk(folder, followlinks=False):
        for name in list(directories) + files:
            path = Path(current) / name
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                # PyInstaller macOS frameworks contain relative links; only links
                # resolving within the same bundle are legitimate managed assets.
                if not path.resolve().is_relative_to(boundary) or not path.exists():
                    raise ValueError('External or broken link in managed files: ' + str(path))
                if name in directories:
                    result.append(path.relative_to(folder).as_posix())
                    directories.remove(name)
        result.extend((Path(current) / name).relative_to(folder).as_posix() for name in files)
    return sorted(result)


def _remove(folder, boundary):
    folder, boundary = Path(folder), Path(boundary).resolve()
    resolved = folder.resolve()
    if resolved == boundary or not resolved.is_relative_to(boundary) or folder.is_symlink():
        raise ValueError('Unsafe cleanup path: ' + str(folder))
    _files(folder)
    shutil.rmtree(folder)


def clean_workspace(root=ROOT, *, preserve=()):
    """Remove generated caches/build only, never application data or arbitrary logs."""
    root = Path(root).resolve()
    candidates = []
    protected_targets = [Path(path).resolve() for path in preserve]
    excluded = {'.git', 'dist', 'ai_packs', 'models', 'runtime', 'qa-runtime', 'local-install', '.venv', 'venv'}
    for name in ('__pycache__', '.pytest_cache'):
        path = root / name
        if path.exists():
            candidates.append(path)
    for scope in ('app', 'tests', 'tools', 'scripts'):
        source = root / scope
        if source.is_symlink() or getattr(source, 'is_junction', lambda: False)():
            continue
        if any(source.resolve() == saved or source.resolve().is_relative_to(saved) for saved in protected_targets):
            continue
        for current, directories, _ in os.walk(source, followlinks=False):
            for name in list(directories):
                path = Path(current) / name
                if path.is_symlink() or getattr(path, 'is_junction', lambda: False)() or name in excluded or any(
                        saved == path.resolve() or path.resolve().is_relative_to(saved) for saved in protected_targets):
                    directories.remove(name)
                elif name in {'__pycache__', '.pytest_cache'}:
                    candidates.append(path)
                    directories.remove(name)
    build = root / 'build'
    if build.exists():
        if build.is_symlink() or getattr(build, 'is_junction', lambda: False)():
            raise ValueError('Linked build directory: cleanup refused')
        if any(_protected(name) for name in _files(build)):
            raise ValueError('User data found in build/: cleanup refused')
        candidates.append(build)
    for path in candidates:
        if any(saved == path.resolve() or saved.is_relative_to(path.resolve()) for saved in protected_targets):
            continue
        contents = _files(path)
        if path.name == '__pycache__' and any(not name.endswith('.pyc') for name in contents):
            raise ValueError('Unexpected files in __pycache__: cleanup refused')
        if any(_protected(name) for name in contents):
            raise ValueError('User data found in generated cache: ' + str(path))
        _remove(path, root)
    logs = root / 'work' / 'deploy-local'
    if logs.exists() and logs.resolve().is_relative_to(root) and not any(
            p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
            for p in [logs, logs.parent]):
        for log in logs.glob('deploy-*.log'):
            if not log.is_symlink() and log.is_file() and time.time() - log.stat().st_mtime > 7 * 86400:
                log.unlink()
    return candidates


def executable_path(bundle, platform_name=None):
    platform_name = platform_name or sys.platform
    bundle = Path(bundle)
    return bundle / ('Contents/MacOS/OrangeNotes' if platform_name == 'darwin'
                     else 'OrangeNotes.exe' if platform_name == 'win32' else 'OrangeNotes')


def deploy_bundle(bundle, target, platform_name=None, *, verify=None):
    """Stage and atomically swap our payload, retaining a rollback copy on errors."""
    bundle, target = Path(bundle), Path(target).absolute()
    if bundle.is_symlink() or getattr(bundle, 'is_junction', lambda: False)():
        raise ValueError('Build source must not be a linked directory')
    bundle = bundle.resolve()
    platform_name = platform_name or sys.platform
    for parent in [target, *target.parents]:
        if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)():
            if sys.platform == 'darwin' and parent in (Path('/var'), Path('/tmp')) and parent.resolve() == Path('/private') / parent.name:
                continue
            raise ValueError('Install target must not traverse links: ' + str(parent))
    target = target.resolve()
    if target == bundle or target.is_relative_to(bundle) or bundle.is_relative_to(target):
        raise ValueError('Build source and install target must be separate')
    if not executable_path(bundle, platform_name).is_file():
        raise ValueError('Missing bundled executable')
    inventory = _files(bundle)
    if any(_protected(name) for name in inventory):
        raise ValueError('Build contains user data; deployment refused')
    target.mkdir(parents=True, exist_ok=True)
    package = target / 'package'
    marker = target / MARKER
    if package.is_symlink() or getattr(package, 'is_junction', lambda: False)():
        raise ValueError('Managed package must not be a linked directory')
    if package.exists():
        if not marker.is_file() or marker.is_symlink():
            raise ValueError('Existing package has no ownership manifest')
        previous = json.loads(marker.read_text(encoding='utf-8'))
        actual = _files(package)
        expected = previous.get('files', [])
        if previous.get('version') != 1 or actual != sorted(expected) or any(_protected(n) for n in actual):
            raise ValueError('Managed package contains unexpected files; move user data outside package/ first')
    elif marker.exists():
        raise ValueError('Incomplete install manifest; inspect target before updating')
    suffix = uuid.uuid4().hex
    stage, backup = target / ('.stage-' + suffix), target / ('.backup-' + suffix)
    old_marker = marker.read_bytes() if marker.exists() else None
    swapped = False
    try:
        stage.mkdir()
        shutil.copytree(bundle, stage / bundle.name, symlinks=True)
        if _files(stage) != [bundle.name + '/' + name for name in inventory]:
            raise RuntimeError('Staged inventory verification failed')
        if package.exists():
            package.rename(backup)
        stage.rename(package)
        swapped = True
        temporary_marker = target / ('.manifest-' + suffix)
        temporary_marker.write_text(json.dumps({'version': 1, 'bundle': bundle.name,
                                    'files': _files(package)}, indent=2), encoding='utf-8')
        temporary_marker.replace(marker)
        if verify is not None:
            verify(package / bundle.name)
    except BaseException:
        if swapped and package.exists():
            _remove(package, target)
        if backup.exists():
            backup.rename(package)
        if old_marker is not None:
            marker.write_bytes(old_marker)
        elif marker.exists():
            marker.unlink()
        raise
    finally:
        if stage.exists():
            _remove(stage, target)
        temporary_marker = target / ('.manifest-' + suffix)
        if temporary_marker.exists():
            temporary_marker.unlink()
    if backup.exists():
        try:
            _remove(backup, target)
        except OSError:
            print('Previous bundle retained because files are in use: ' + str(backup))
    return package / bundle.name


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, default=ROOT / 'work/local-install')
    parser.add_argument('--bundle', type=Path, help='Use an already built bundle instead of building')
    parser.add_argument('--no-launch', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--database', type=Path, help='Optional explicit database for isolated smoke testing')
    args = parser.parse_args(argv)
    output_directory = ROOT / 'work/deploy-local/dist'
    bundle = args.bundle or output_directory / ('OrangeNotes.app' if sys.platform == 'darwin' else 'OrangeNotes')
    if args.dry_run:
        print('Build: ' + str(ROOT / 'tools/build.py') + ' --clean --dist-dir ' + str(output_directory))
        print('Install: ' + str(bundle) + ' -> ' + str(args.target / 'package'))
        print('User data: preserved outside managed package/; no running process is stopped')
        return 0
    try:
        assert_application_closed(args.database)
        target = args.target.absolute()
        if target.is_relative_to(ROOT) and any(part in {'build', '__pycache__', '.pytest_cache'}
                                               for part in target.relative_to(ROOT).parts):
            raise ValueError('Install target is inside a generated cleanup directory')
        resolved_bundle = bundle.resolve()
        resolved_target = target.resolve()
        if resolved_target == resolved_bundle or resolved_target.is_relative_to(resolved_bundle) or resolved_bundle.is_relative_to(resolved_target):
            raise ValueError('Build source and install target must be separate')
        if not args.bundle:
            if bundle.exists() and any(_protected(name) for name in _files(bundle)):
                raise ValueError('User data found in dist bundle; clean build refused')
            clean_workspace(ROOT, preserve=[args.target])
            logs = ROOT / 'work/deploy-local'
            logs.mkdir(parents=True, exist_ok=True)
            log = logs / ('deploy-' + time.strftime('%Y%m%d-%H%M%S') + '.log')
            with log.open('w', encoding='utf-8') as output:
                try:
                    subprocess.run([sys.executable, '-u', str(ROOT / 'tools/build.py'), '--clean',
                                    '--dist-dir', str(output_directory)],
                                   cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, check=True)
                except subprocess.CalledProcessError as error:
                    raise RuntimeError('Build failed; see ' + str(log)) from error
            print('Build log: ' + str(log))
        def launch(installed):
            command = [str(executable_path(installed))]
            if args.database:
                command += ['--database', str(args.database.resolve())]
            kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
            launch_env = os.environ.copy()
            for key in ('PYTHONPATH', 'PYTHONHOME', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QT_QPA_PLATFORM'):
                launch_env.pop(key, None)
            process = subprocess.Popen(command, cwd=installed, env=launch_env, **kwargs)
            time.sleep(2)
            if process.poll() is not None:
                raise RuntimeError('Application exited during startup: ' + str(process.returncode))
            print('Launch accepted; PID ' + str(process.pid) + '. Verify the visible window manually.')
        installed = deploy_bundle(bundle, args.target, verify=None if args.no_launch else launch)
        print('Installed: ' + str(installed))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Deployment failed: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
