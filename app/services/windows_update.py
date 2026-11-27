"""Windows installation scope and graceful shutdown for Restart Manager."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import threading


SETUP_MUTEX = 'OrangeNotes.Desktop.Windows.Setup'
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\OrangeNotes.Desktop.Windows_is1'
QUERY_END_SESSION = 0x0011
END_SESSION = 0x0016
END_SESSION_CLOSE_APP = 0x00000001


def install_scope(executable):
    import winreg

    executable = Path(executable).resolve()
    for hive, scope in ((winreg.HKEY_CURRENT_USER, 'user'), (winreg.HKEY_LOCAL_MACHINE, 'machine')):
        try:
            with winreg.OpenKey(hive, UNINSTALL_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                folder, _ = winreg.QueryValueEx(key, 'InstallLocation')
            if (Path(folder) / 'OrangeNotes.exe').resolve() == executable:
                return scope
        except (OSError, ValueError, TypeError):
            continue
    return None


def launch_installer(path, scope):
    # ShellExecute prompts for elevation according to the installer's manifest.
    # Clear PyInstaller's DLL search override while starting an external program.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.SetDllDirectoryW.argtypes = (wintypes.LPCWSTR,)
    kernel.SetDllDirectoryW.restype = wintypes.BOOL
    previous = ctypes.create_unicode_buffer(32768)
    if kernel.GetDllDirectoryW(len(previous), previous) >= len(previous):
        raise OSError('Could not read DLL search directory')
    if not kernel.SetDllDirectoryW(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        os.startfile(str(path), 'open', '/CURRENTUSER' if scope == 'user' else '', str(path.parent))
    finally:
        kernel.SetDllDirectoryW(previous.value or None)


class SetupStopEvent(threading.Event):
    """Stop the packaged reminder worker while Windows Setup replaces its files."""

    def __init__(self):
        super().__init__()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self._open = kernel.OpenMutexW
        self._open.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        self._open.restype = wintypes.HANDLE
        self._close = kernel.CloseHandle
        self._close.argtypes = (wintypes.HANDLE,)
        self._close.restype = wintypes.BOOL

    def is_set(self):
        if super().is_set():
            return True
        ctypes.set_last_error(0)
        handle = self._open(0x00100000, False, SETUP_MUTEX)  # SYNCHRONIZE
        if handle:
            self._close(handle)
            self.set()
        elif ctypes.get_last_error() == 5:
            self.set()  # A mutex created by elevated Setup can be access-denied.
        return super().is_set()


def handle_setup_message(window, event_type, address):
    if event_type != b'windows_generic_MSG':
        return None
    message = wintypes.MSG.from_address(int(address))
    if message.message not in (QUERY_END_SESSION, END_SESSION) or not message.lParam & END_SESSION_CLOSE_APP:
        return None
    if message.message == QUERY_END_SESSION:
        return int(window.prepare_for_update())
    if message.wParam and window.prepare_for_update():
        window.quit()
    return 0
