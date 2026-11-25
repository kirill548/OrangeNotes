"""Look up the executable behind a Windows process ID."""

import ctypes
from ctypes import wintypes
from pathlib import Path


def executable_path(pid):
    if not 0 < pid <= 0xFFFFFFFF:
        return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    open_process = kernel.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    query_image = kernel.QueryFullProcessImageNameW
    query_image.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                            ctypes.POINTER(wintypes.DWORD))
    query_image.restype = wintypes.BOOL
    close_handle = kernel.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if query_image(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).resolve()
    finally:
        close_handle(handle)
