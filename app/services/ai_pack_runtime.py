"""An isolated Ollama daemon owned by one marked AI pack directory."""
import atexit
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from app.services.ai_pack_storage import AIPackStorage
from app.services.local_runtime import runtime_candidates


class PackRuntimeError(RuntimeError):
    pass


_registry = {}
_lock = threading.RLock()


def _process_alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            # Access denied also means we cannot establish that the PID is dead.
            return ctypes.get_last_error() != 87
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _listener_pids(port):
    """Fail closed when the OS cannot prove ownership of a listener."""
    if sys.platform == 'win32':
        result = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True,
                                text=True, timeout=5, check=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        return {int(parts[-1]) for line in result.stdout.splitlines()
                if len(parts := line.split()) >= 5 and parts[0] == 'TCP'
                and parts[1] == f'127.0.0.1:{port}' and parts[3] == 'LISTENING'}
    if sys.platform.startswith('linux'):
        inodes = set()
        for table in ('tcp', 'tcp6'):
            with open('/proc/net/' + table, encoding='ascii') as stream:
                for line in stream:
                    parts = line.split()
                    if len(parts) > 9 and parts[1].endswith(f':{port:04X}') and parts[3] == '0A':
                        inodes.add(parts[9])
        owners = set()
        for directory in Path('/proc').iterdir():
            if directory.name.isdigit():
                try:
                    for fd in (directory / 'fd').iterdir():
                        try:
                            target = os.readlink(fd)
                            if target.startswith('socket:[') and target[8:-1] in inodes:
                                owners.add(int(directory.name))
                        except OSError:
                            pass
                except OSError:
                    pass
        return owners
    result = subprocess.run(['lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-t'],
                            capture_output=True, text=True, timeout=5)
    if result.returncode not in (0, 1):
        raise PackRuntimeError('Cannot verify managed Ollama listener ownership')
    return {int(line) for line in result.stdout.splitlines() if line.isdigit()}


class ManagedPackRuntime:
    def __init__(self, root, runtime_root=None):
        self.root = Path(os.path.abspath(os.fspath(root)))
        self.runtime_root = Path(runtime_root) if runtime_root is not None else (
            Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False)
            else Path(__file__).resolve().parents[2])
        self._key = os.path.normcase(str(self.root.resolve()))

    def _check(self):
        AIPackStorage(self.root / 'models')._check()
        for name in ('ollama.log', '.notes-ai-pack-process'):
            AIPackStorage(self.root)._safe(self.root / name)
        profile = self.root / 'ollama_profile'
        if profile.exists() or profile.is_symlink():
            AIPackStorage(profile)._check()

    @property
    def _record_path(self):
        return self.root / '.notes-ai-pack-process'

    def _record(self, state=None):
        self._check()
        if not self._record_path.exists():
            return None
        try:
            record = json.loads(self._record_path.read_text(encoding='utf-8'))
            if set(record) != {'owner_pid', 'pid', 'port'} or any(type(record[k]) is not int for k in record):
                raise ValueError('Invalid ownership record')
            if record['owner_pid'] <= 0 or record['pid'] < 0 or not 0 < record['port'] < 65536 or record['port'] == 11434:
                raise ValueError('Invalid ownership record')
        except (ValueError, OSError, TypeError) as exc:
            raise PackRuntimeError('Cannot safely read managed process ownership record') from exc
        if state is not None and record == {'owner_pid': os.getpid(), 'pid': state[0].pid, 'port': state[1]}:
            return record
        if (record['pid'] and _process_alive(record['pid'])) or _process_alive(record['owner_pid']) or _listener_pids(record['port']):
            raise PackRuntimeError('Another application instance or orphan owns this AI pack runtime')
        # A stale record can only be removed after proving both processes and its listener are gone.
        self._record_path.unlink()
        return None

    def _write_record(self, pid, port, exclusive=False):
        self._check()
        with self._record_path.open('x' if exclusive else 'w', encoding='utf-8') as stream:
            json.dump({'owner_pid': os.getpid(), 'pid': pid, 'port': port}, stream)

    @staticmethod
    def _cancel(cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise PackRuntimeError('Managed Ollama operation cancelled')

    def _owned(self, state):
        self._record(state)
        process, port = state
        if process.poll() is not None or _listener_pids(port) != {process.pid}:
            raise PackRuntimeError('Managed Ollama listener is not owned by this application')

    @staticmethod
    def _request(port, path, payload=None):
        request = urllib.request.Request(f'http://127.0.0.1:{port}{path}',
                    data=json.dumps(payload).encode() if payload is not None else None,
                    headers={'Content-Type': 'application/json'})
        # Explicitly bypass proxies: pack traffic must stay on loopback.
        from app.services.local_ai import _NoRedirects
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirects()).open(request, timeout=2) as response:
            payload=response.read(4*1024*1024+1)
            if len(payload)>4*1024*1024:raise PackRuntimeError('Managed response is too large')
            return payload

    def ensure(self, cancel_event=None):
        with _lock:
            self._cancel(cancel_event)
            self._check()
            state = _registry.get(self._key)
            self._record(state)
            if state is None or state[0].poll() is not None:
                _registry.pop(self._key, None)
                if self._record_path.exists():
                    self._record_path.unlink()
                candidates = runtime_candidates(self.runtime_root.parent if self.runtime_root.name == 'runtime' else self.runtime_root)
                exe = next((Path(exe) for exe, _ in candidates if Path(exe).is_file()), None)
                if exe is None:
                    raise PackRuntimeError('Ollama runtime binary is not installed')
                with socket.socket() as reservation:
                    reservation.bind(('127.0.0.1', 0))
                    port = reservation.getsockname()[1]
                if port == 11434:
                    raise PackRuntimeError('Cannot allocate an isolated Ollama port')
                models = self.root / 'models'
                profile = self.root / 'ollama_profile'
                AIPackStorage(profile).initialize()
                self._check()
                env = os.environ.copy()
                env.update(OLLAMA_HOST=f'127.0.0.1:{port}', OLLAMA_MODELS=str(models),
                           OLLAMA_NO_CLOUD='1', HOME=str(profile), USERPROFILE=str(profile),
                           XDG_CONFIG_HOME=str(profile / 'config'), XDG_CACHE_HOME=str(profile / 'cache'))
                self._write_record(0, port, exclusive=True)
                try:
                    with (self.root / 'ollama.log').open('ab') as log:
                        process = subprocess.Popen([str(exe.resolve()), 'serve'], cwd=exe.parent, env=env,
                             stdout=log, stderr=log,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                except Exception:
                    self._record_path.unlink()
                    raise
                state = (process, port)
                _registry[self._key] = state
                try:
                    self._write_record(process.pid, port)
                except Exception:
                    process.terminate()
                    process.wait(timeout=5)
                    _registry.pop(self._key, None)
                    raise
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    self._cancel(cancel_event)
                    if state[0].poll() is not None:
                        raise PackRuntimeError('Managed Ollama exited before startup')
                    owners = _listener_pids(state[1])
                    if owners and owners != {state[0].pid}:
                        raise PackRuntimeError('Another process owns the selected Ollama port')
                    if owners:
                        self._owned(state)
                        try:
                            self._request(state[1], '/api/tags')
                            self._owned(state)
                            return f'http://127.0.0.1:{state[1]}'
                        except (OSError, TimeoutError):
                            pass
                    time.sleep(.1)
                raise PackRuntimeError('Managed Ollama startup timed out')
            except Exception:
                self.stop()
                raise

    def verify_owned(self):
        with _lock:
            state = _registry.get(self._key)
            if state is None:
                raise PackRuntimeError('Managed Ollama has not been started')
            self._owned(state)
            return f'http://127.0.0.1:{state[1]}'

    def unload(self, model, cancel_event=None):
        AIPackStorage._name(model)
        with _lock:
            self._cancel(cancel_event)
            state = _registry.get(self._key)
            if state is None:
                return
            self._owned(state)
            self._request(state[1], '/api/generate', {'model': model, 'keep_alive': 0, 'stream': False})
            self._owned(state)
            self._cancel(cancel_event)

    def switch_active_model(self, old_model, new_model, cancel_event=None):
        if new_model:
            AIPackStorage._name(new_model)
        if old_model and old_model != new_model:
            self.unload(old_model, cancel_event)
        return self.ensure(cancel_event)

    def stop(self):
        with _lock:
            state = _registry.get(self._key)
            self._record(state)
            if state is None:
                return
            if state[0].poll() is not None:
                _registry.pop(self._key, None)
                if self._record_path.exists():
                    self._record_path.unlink()
                return
            # Popen is our original child handle; never terminate an arbitrary PID.
            state[0].terminate()
            try:
                state[0].wait(timeout=5)
            except subprocess.TimeoutExpired:
                state[0].kill()
                state[0].wait(timeout=5)

            _registry.pop(self._key, None)
            if self._record_path.exists():
                self._record_path.unlink()


def _shutdown_owned():
    for key in list(_registry):
        try:
            ManagedPackRuntime(key).stop()
        except Exception:
            # Keep an ownership record when shutdown cannot establish safety.
            pass


atexit.register(_shutdown_owned)
