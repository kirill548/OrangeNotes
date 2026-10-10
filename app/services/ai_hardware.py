"""Best-effort physical hardware samples; call only from a worker thread.

These are device-wide readings, not proof that the selected LLM uses that GPU.
No optional hardware package is required and unavailable values remain None.
"""
import importlib
import os
from pathlib import Path
import platform
import subprocess


def _snapshot(provider=None, detail='GPU telemetry unavailable'):
    return dict(mode='unknown', gpu_utilization_pct=None, vram_used_bytes=None,
                vram_total_bytes=None, provider=provider, detail=detail)


def _number(value, maximum=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not 0 <= number < float('inf') or (maximum is not None and number > maximum):
        return None
    return number


def _nvml():
    nvml = importlib.import_module('pynvml')
    initialized = False
    try:
        nvml.nvmlInit()
        initialized = True
        count = nvml.nvmlDeviceGetCount()
        if count < 1:
            return None
        used = total = 0
        utilization = []
        measured = 0
        for index in range(count):
            device = nvml.nvmlDeviceGetHandleByIndex(index)
            memory = nvml.nvmlDeviceGetMemoryInfo(device)
            device_used = _number(memory.used)
            device_total = _number(memory.total)
            if (device_used is None or device_total is None or
                    device_total <= 0 or device_used > device_total):
                continue
            used += int(device_used)
            total += int(device_total)
            measured += 1
            try:
                value = _number(nvml.nvmlDeviceGetUtilizationRates(device).gpu, 100)
                if value is not None:
                    utilization.append(value)
            except Exception:
                pass
        if total <= 0 or used < 0 or used > total:
            return None
        result = _snapshot('NVML', f'{measured} NVIDIA device(s); device-wide telemetry')
        result.update(mode='gpu', gpu_utilization_pct=max(utilization) if utilization else None,
                      vram_used_bytes=int(used), vram_total_bytes=int(total))
        return result
    finally:
        if initialized:
            try:
                nvml.nvmlShutdown()
            except Exception:
                pass


def _nvidia_smi():
    process = subprocess.run(
        ['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,memory.total',
         '--format=csv,noheader,nounits'], capture_output=True, text=True,
        timeout=1, check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
    rows = []
    for line in process.stdout.splitlines():
        fields = line.split(',')
        if len(fields) != 3:
            continue
        utilization = _number(fields[0].strip(), 100)
        used, total = (_number(field.strip()) for field in fields[1:])
        if used is None or total is None or total <= 0 or used > total:
            continue
        rows.append((utilization, int(used * 1048576), int(total * 1048576)))
    if not rows:
        return None
    loads = [row[0] for row in rows if row[0] is not None]
    result = _snapshot('nvidia-smi', f'{len(rows)} NVIDIA device(s); device-wide telemetry')
    result.update(mode='gpu', gpu_utilization_pct=max(loads) if loads else None,
                  vram_used_bytes=sum(row[1] for row in rows),
                  vram_total_bytes=sum(row[2] for row in rows))
    return result


def _amd_sysfs(root=Path('/sys/class/drm')):
    rows = []
    seen = set()
    for device in root.glob('card[0-9]*/device'):
        try:
            resolved = device.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if (device / 'vendor').read_text().strip().lower() != '0x1002':
                continue
            used = _number((device / 'mem_info_vram_used').read_text().strip())
            total = _number((device / 'mem_info_vram_total').read_text().strip())
            try:
                load = _number((device / 'gpu_busy_percent').read_text().strip(), 100)
            except OSError:
                load = None
            if used is not None and total is not None and total > 0 and used <= total:
                rows.append((load, int(used), int(total)))
        except OSError:
            continue
    if not rows:
        return None
    loads = [row[0] for row in rows if row[0] is not None]
    result = _snapshot('AMD sysfs', f'{len(rows)} AMD device(s); device-wide telemetry')
    result.update(mode='gpu', gpu_utilization_pct=max(loads) if loads else None,
                  vram_used_bytes=sum(row[1] for row in rows),
                  vram_total_bytes=sum(row[2] for row in rows))
    return result


def collect_hardware():
    """Return measured device telemetry, never fabricated zero/CPU fallbacks.

    Apple unified-memory hardware has no portable VRAM/load counter here.
    CPU execution must be established separately from the LLM provider.
    """
    if platform.system() == 'Darwin':
        return _snapshot(detail='Metal GPU/VRAM telemetry unavailable; unified memory is not VRAM')
    for probe in (_nvml, _nvidia_smi):
        try:
            result = probe()
            if result is not None:
                return result
        except Exception:
            continue
    if platform.system() == 'Linux':
        try:
            result = _amd_sysfs()
            if result is not None:
                return result
        except Exception:
            pass
    return _snapshot()
