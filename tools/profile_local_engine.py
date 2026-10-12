"""Sample an explicitly owned Ollama process tree without generating inference."""
import argparse
import ctypes
from ctypes import wintypes
import json
import ipaddress
import os
from pathlib import Path
import platform
import time
import threading
from urllib.request import build_opener, ProxyHandler
from urllib.parse import urlsplit


class ProcessEntry(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]


class MemoryCounters(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
        'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
        'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]


def windows_tree_memory(root):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    entry = ProcessEntry(); entry.dwSize = ctypes.sizeof(entry)
    processes = {}
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            processes[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    selected = {root}
    while True:
        additions = {pid for pid, (parent, _) in processes.items() if parent in selected}
        if additions <= selected:
            break
        selected |= additions
    readings = []
    for pid in sorted(selected):
        handle = kernel.OpenProcess(0x1000 | 0x10, False, pid)
        if not handle:
            continue
        try:
            counters = MemoryCounters(); counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                readings.append({'pid': pid, 'name': processes.get(pid, (None, None))[1],
                                 'rss_bytes': counters.WorkingSetSize,
                                 'private_bytes': counters.PrivateUsage,
                                 'process_lifetime_peak_rss_bytes': counters.PeakWorkingSetSize})
        finally:
            kernel.CloseHandle(handle)
    return readings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=3600)
    args = parser.parse_args()
    endpoint = urlsplit(args.endpoint)
    if (endpoint.scheme != 'http' or endpoint.username or endpoint.password
            or endpoint.path not in ('', '/') or endpoint.query or endpoint.fragment
            or not ipaddress.ip_address(endpoint.hostname).is_loopback):
        parser.error('Only literal loopback HTTP endpoints are permitted')
    if platform.system() != 'Windows':
        parser.error('This profiler measures Windows working sets only')
    args.output.mkdir(parents=True, exist_ok=True)
    stop = args.output / 'profile.stop'
    opener = build_opener(ProxyHandler({}))
    started = time.time(); peaks = {'rss_bytes': 0, 'private_bytes': 0}
    ps_samples = []; samples = 0; errors = []; finished = threading.Event()
    def poll_placement():
        while not finished.is_set():
            try:
                with opener.open(args.endpoint + '/api/ps', timeout=.2) as response:
                    ps_samples.append({'epoch_seconds': time.time(), 'data': json.loads(response.read(1024*1024))})
            except (OSError, ValueError) as error:
                errors.append('api_ps:' + type(error).__name__)
            finished.wait(1)
    placement = threading.Thread(target=poll_placement, daemon=True)
    placement.start()
    with (args.output / 'hardware_samples.jsonl').open('w', encoding='utf-8') as stream:
        while time.time() - started < args.seconds and not stop.exists():
            tick = time.monotonic()
            try:
                readings = windows_tree_memory(args.pid)
                item = {'epoch_seconds': time.time(), 'processes': readings}
                for metric in peaks:
                    item[metric] = sum(p[metric] for p in readings)
                    peaks[metric] = max(peaks[metric], item[metric])
                stream.write(json.dumps(item) + '\n'); stream.flush(); samples += 1
            except OSError as error:
                errors.append(type(error).__name__)
            time.sleep(max(0, .1 - (time.monotonic() - tick)))
    finished.set(); placement.join(timeout=1)
    result = {'platform': platform.platform(), 'cpu': platform.processor(),
              'logical_cpus': os.cpu_count(), 'root_pid': args.pid, 'endpoint': args.endpoint,
              'started_epoch_seconds': started, 'ended_epoch_seconds': time.time(),
              'samples': samples, 'sampled_tree_peaks_bytes': peaks, 'api_ps': ps_samples,
              'errors': sorted(set(errors)), 'nominal_interval_ms': 100,
              'limitations': ['RSS is Windows working set; private bytes are commit, not RSS.',
                              'Sampled aggregate peak is not absolute peak; shared pages may be double-counted.',
                              'Scheduling and process enumeration can exceed the nominal 100 ms interval.',
                              'Only explicitly owned server and observed descendants are sampled.']}
    inventory = args.output / 'hardware_inventory.json'
    if inventory.exists():
        result['hardware_inventory'] = json.loads(inventory.read_text(encoding='utf-8'))
    (args.output / 'hardware_profile.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'samples': samples, 'peaks': peaks}))


if __name__ == '__main__':
    main()
