"""External Windows smoke test. Uses disposable data; never installs a task."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time


def visible_windows(pid,only_app=True):
    user32=ctypes.WinDLL('user32',use_last_error=True)
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user32.EnumWindows.argtypes=[callback_type,wintypes.LPARAM]
    user32.IsWindowVisible.argtypes=[wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextLengthW.argtypes=[wintypes.HWND]
    user32.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user32.SendMessageTimeoutW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM,wintypes.UINT,wintypes.UINT,ctypes.POINTER(ctypes.c_size_t)]
    class_long=getattr(user32,'GetClassLongPtrW',user32.GetClassLongW)
    class_long.argtypes=[wintypes.HWND,ctypes.c_int]
    class_long.restype=ctypes.c_size_t
    windows=[]
    @callback_type
    def visit(hwnd,_):
        owner=wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
        if owner.value!=pid or not user32.IsWindowVisible(hwnd):
            return True
        length=user32.GetWindowTextLengthW(hwnd)
        buffer=ctypes.create_unicode_buffer(length+1)
        user32.GetWindowTextW(hwnd,buffer,len(buffer))
        if only_app and not any(title in buffer.value.casefold() for title in ['orange notes','заметки']):
            return True
        icons=[]
        for kind in [1,0,2]:
            icon=ctypes.c_size_t()
            user32.SendMessageTimeoutW(hwnd,0x007F,kind,0,0x0002,500,ctypes.byref(icon))
            icons.append(icon.value)
        icons += [class_long(hwnd,-14),class_long(hwnd,-34)]
        windows.append({'title':buffer.value,'hwnd':int(hwnd),'has_icon':any(icons)})
        return True
    user32.EnumWindows(visit,0)
    return windows


def wait_for(process,check,timeout,label):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if process.poll() is not None:
            raise RuntimeError(f'{label} exited early: {process.returncode}')
        value=check()
        if value:
            return value
        time.sleep(0.15)
    raise RuntimeError(label+' did not become ready before timeout')


def stop_owned(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(5)


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser()
    parser.add_argument('executable',type=Path)
    parser.add_argument('--timeout',type=float,default=25)
    parser.add_argument('--background-only',action='store_true')
    parser.add_argument('--hostile-qt-env',action='store_true',help='Run from a temporary directory with foreign Qt plugins/backend and only Windows on PATH.')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    if os.name!='nt':
        raise SystemExit('Windows is required for the native-window smoke test.')
    executable=args.executable.resolve()
    if not executable.is_file():
        raise SystemExit('Executable not found: '+str(executable))
    environment=os.environ.copy()
    environment['PYTHONPATH']=''
    for name in ['PYTHONHOME','QT_QPA_PLATFORM']:
        environment.pop(name,None)
    report={'executable':str(executable),'registration_attempted':False,'pythonpath':''}
    with tempfile.TemporaryDirectory(prefix='OrangeNotes-frozen-smoke-') as directory:
        root=Path(directory)
        launch_cwd=executable.parent
        if args.hostile_qt_env:
            foreign_plugins=root/'foreign Qt5'/'plugins'
            environment.update(QT_PLUGIN_PATH=str(foreign_plugins),
                               QT_QPA_PLATFORM_PLUGIN_PATH=str(foreign_plugins/'platforms'),
                               QT_QPA_PLATFORM='wayland',QT_QPA_PLATFORMTHEME='qt5ct',
                               QT_QPA_GENERIC_PLUGINS='foreign-plugin')
            windows=Path(environment.get('SystemRoot',r'C:\Windows'))
            environment['PATH']=os.pathsep.join(str(path) for path in [windows/'System32',windows,windows/'System32'/'Wbem'])
            launch_cwd=root
        report['hostile_qt_env']=args.hostile_qt_env
        report['launch_cwd']=str(launch_cwd)
        database=root/'smoke notes.sqlite3'
        # This explicit synthetic path differs from Store.default_path(), so the
        # application does not enter its per-user registration/setup branch.
        processes=[]
        try:
            with (root/'gui.log').open('wb') as gui_log,(root/'worker.log').open('wb') as worker_log:
                if not args.background_only:
                    gui=subprocess.Popen([str(executable),'--database',str(database)],cwd=launch_cwd,env=environment,stdout=gui_log,stderr=subprocess.STDOUT)
                    processes.append(gui)
                    windows=wait_for(gui,lambda:visible_windows(gui.pid),args.timeout,'GUI')
                    if not any(window['has_icon'] for window in windows):
                        raise RuntimeError('Visible application window has no native icon.')
                    report['gui']={'pid':gui.pid,'windows':windows}
                else:
                    report['gui']={'tested':False}
                worker=subprocess.Popen([str(executable),'--background','--database',str(database)],cwd=launch_cwd,env=environment,stdout=worker_log,stderr=subprocess.STDOUT)
                processes.append(worker)
                def heartbeat():
                    try:
                        value=json.loads((root/'worker_status.json').read_text(encoding='utf-8'))
                    except (OSError,ValueError):
                        return None
                    return value if value.get('pid')==worker.pid and value.get('running') is True and value.get('heartbeat_utc') else None
                status=wait_for(worker,heartbeat,args.timeout,'background worker')
                if status.get('last_error'):
                    raise RuntimeError('Worker reported an error: '+status['last_error'])
                report['worker']={'pid':worker.pid,'heartbeat':status['heartbeat_utc'],'last_error':status.get('last_error')}
                if visible_windows(worker.pid):
                    raise RuntimeError('Background worker unexpectedly displayed a main window.')
                for process in reversed(processes):
                    stop_owned(process)
            connection=sqlite3.connect(database)
            try:
                integrity=connection.execute('PRAGMA integrity_check').fetchone()[0]
                foreign_keys=connection.execute('PRAGMA foreign_key_check').fetchall()
                if integrity!='ok' or foreign_keys:
                    raise RuntimeError('Synthetic database integrity check failed.')
                report['database']={'integrity':integrity,'foreign_key_errors':len(foreign_keys)}
            finally:
                connection.close()
            report['status']='passed'
            if args.report:
                args.report.parent.mkdir(parents=True,exist_ok=True)
                args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False,indent=2))
        except Exception:
            for process in processes:
                if process.poll() is None:
                    print('Visible windows for PID '+str(process.pid)+': '+json.dumps(visible_windows(process.pid,only_app=False),ensure_ascii=False))
            for process in reversed(processes):
                stop_owned(process)
            for filename in ['gui.log','worker.log','reminder_worker.log']:
                path=root/filename
                if path.exists():
                    print(filename+': '+path.read_text(encoding='utf-8',errors='replace')[-4000:])
            raise
        finally:
            for process in reversed(processes):
                stop_owned(process)


if __name__=='__main__':
    main()
