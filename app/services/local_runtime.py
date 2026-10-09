"""Local runtime discovery. Only explicit assistant requests start it."""
import os
import sys
from pathlib import Path
import subprocess
import threading
import time
import platform
import re

_lock=threading.Lock()
_process=None


def runtime_candidates(root, target=None, machine=None):
    target=target or sys.platform
    machine=(machine or os.environ.get('PROCESSOR_ARCHITECTURE') or platform.machine()).lower()
    if target not in ('win32', 'darwin', 'linux'):
        return []
    if machine in ('arm64', 'aarch64'):
        arch='arm64'
    elif machine in ('amd64', 'x86_64', 'x64'):
        arch='amd64'
    else:
        return []  # Never launch an incompatible x64 binary on an unknown CPU.
    names=['ollama.exe'] if target=='win32' else ([f'ollama-darwin-{arch}','ollama'] if target=='darwin' else [f'ollama-linux-{arch}','ollama'])
    roots=[root/'runtime']
    if target=='darwin' and root.name=='MacOS':roots.insert(0,root.parent/'Resources/runtime')
    if not getattr(sys,'frozen',False) and len(root.parents)>1:roots.append(root.parents[1]/'work')
    return [(r/'ollama'/name,r/('ai_models' if r.name=='work' else 'models')) for r in roots for name in names]


def resource_advisory():
    # RAM is an advisory only: GPU offload and system configuration vary.
    available=None
    if os.name=='nt':
        import ctypes
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(name,ctypes.c_ulonglong) for name in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
        state=Memory();state.length=ctypes.sizeof(state)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):available=state.available
    else:
        try:available=os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
        except (ValueError,OSError,AttributeError):pass
    if available is None and sys.platform=='darwin':
        try:
            info=subprocess.run(['vm_stat'],capture_output=True,text=True,timeout=2,check=True)
            page=re.search(r'page size of (\d+) bytes',info.stdout)
            free=re.search(r'Pages free:\s+(\d+)',info.stdout)
            inactive=re.search(r'Pages inactive:\s+(\d+)',info.stdout)
            if page and free:available=int(page[1])*(int(free[1])+(int(inactive[1]) if inactive else 0))
        except (OSError,subprocess.SubprocessError):pass
    vram=None
    try:
        info=subprocess.run(['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=2,check=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        values=[int(line.strip())*1024**2 for line in info.stdout.splitlines() if line.strip().isdigit()]
        if values:vram=max(values)
    except (OSError,subprocess.SubprocessError):pass
    return {'available_ram_bytes':available,'vram_bytes':vram,'warning':('Мало свободной памяти: ответы могут быть медленными. Закройте тяжёлые программы или используйте поиск.' if available is not None and available<4*1024**3 else None)}


def ensure_runtime(client,cancel_event=None,runtime_root=None):
    global _process
    override=runtime_root or getattr(client,'runtime_root',None)
    if not isinstance(override,(str,os.PathLike)):
        if runtime_root is not None:raise ValueError('Проверьте путь к ИИ-комплекту.')
        override=None
    if client.probe()['available']:
        return not bool(runtime_root)
    if client.base_url!='http://127.0.0.1:11434':
        return False
    root=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]
    if override:
        root=Path(override).resolve()
        if root.name=='runtime':root=root.parent
    candidates=runtime_candidates(root)
    if override:
        candidates=[(Path(exe).resolve(),Path(models).resolve()) for exe,models in candidates]
        candidates=[(exe,models) for exe,models in candidates if exe.is_relative_to(root) and models.is_relative_to(root)]
    selected=next(((exe,models) for exe,models in candidates if exe.is_file()),None)
    if not selected:
        return False
    with _lock:
        if client.probe()['available']:
            return not bool(runtime_root)
        # A server we started can still be loading GPU libraries. Reuse it rather
        # than launching another daemon when the health probe briefly times out.
        if _process is not None and _process.poll() is None:
            if runtime_root:return False
            for _ in range(30):
                if cancel_event and cancel_event.is_set():return False
                if isinstance(getattr(client,'deadline',None),(int,float)) and time.monotonic()>=client.deadline:return False
                if client.probe()['available']:return True
                if _process.poll() is not None:break
                time.sleep(.2)
            if _process.poll() is None:return False
        exe,models=selected
        models.mkdir(parents=True,exist_ok=True)
        env=os.environ.copy()
        profile=models.parent/'ollama_profile'
        profile.mkdir(parents=True,exist_ok=True)
        env.update(OLLAMA_HOST='127.0.0.1:11434',OLLAMA_MODELS=str(models),OLLAMA_NO_CLOUD='1',USERPROFILE=str(profile),HOME=str(profile))
        log_path=models.parent/'ollama.log'
        with log_path.open('ab') as log:
            _process=subprocess.Popen([str(exe),'serve'],cwd=exe.parent,env=env,stdout=log,stderr=log,
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        for _ in range(30):
            if isinstance(getattr(client,'deadline',None),(int,float)) and time.monotonic()>=client.deadline:return False
            if cancel_event and cancel_event.is_set():return False
            if client.probe()['available']:return True
            if _process.poll() is not None:return False
            time.sleep(.2)
    return False
