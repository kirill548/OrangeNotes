import argparse
import json
import os
import sqlite3
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs

FROZEN = bool(getattr(sys, 'frozen', False))
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))
# Support the local development runtime when launched by Windows protocol activation.
DEPS = None if FROZEN else ROOT.parents[1] / 'work' / 'dependencies'
if DEPS and DEPS.is_dir():
    sys.path.insert(0, str(DEPS))
from app.database.store import Store


def set_application_identity():
    if os.name == 'nt':
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('OrangeNotes.Desktop')
        except (AttributeError, OSError):
            pass


def notification_action(store, uri):
    parsed = urlparse(uri)
    if parsed.scheme != 'orange-notes' or parsed.netloc != 'notification':
        raise ValueError('Некорректное действие уведомления')
    values = parse_qs(parsed.query, strict_parsing=True)
    if set(values) != {'action', 'event', 'token'} or any(len(v) != 1 for v in values.values()):
        raise ValueError('Некорректные параметры уведомления')
    return store.handle_notification_action(int(values['event'][0]), values['token'][0], values['action'][0])


def setup_background(database, result):
    try:
        if sys.platform == 'win32':
            from app.services.windows_notifications import WindowsNotifications
            from app.services.windows_background import install_background
            transport = WindowsNotifications()
        else:
            from app.services.platform_background import install_background
            from app.services.portable_notifications import notification_transport
            transport = notification_transport(database)
        transport.register(database_path=database)
        result['task'] = install_background(sys.executable, None if FROZEN else Path(__file__), database_path=database,
                                             dependencies_path=DEPS if DEPS and DEPS.is_dir() else None)
        result['message'] = 'Фоновые напоминания включены · запуск при входе в систему'
        notification_status=transport.status()
        if sys.platform == 'win32' and notification_status.get('setting')!='Enabled':
            result['error']='notifications_disabled'
            result['notifications_disabled']=True
            result['message']='Windows блокирует уведомления. Включите их в системных настройках.'
    except Exception as error:
        result['error'] = str(error)
        result['message'] = 'Не удалось включить фоновые напоминания: ' + str(error)
    result['ready'] = True


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--notification-self-test', action='store_true')
    parser.add_argument('--background', action='store_true')
    parser.add_argument('--database', type=Path)
    parser.add_argument('--notification')
    args = parser.parse_args(argv)
    if args.notification_self_test:
        from app.services.native_probe import run
        return run()
    database = (args.database or Store.default_path()).resolve()
    if args.background:
        from app.services.reminder_worker import run_worker
        return run_worker(database)
    from app.utils.qt_runtime import configure_platform
    try:
        plugin_directory=configure_platform()
    except RuntimeError as error:
        if os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,str(error),'Orange Notes: неполная сборка',0x10)
        return 1
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
    from PySide6.QtGui import QFont, QPalette, QColor, QIcon, QDesktopServices
    from PySide6.QtCore import QLocale, QLockFile, QTimer, QUrl, QCoreApplication, qInstallMessageHandler
    if plugin_directory:
        QCoreApplication.setLibraryPaths([str(plugin_directory)])
        log_path=database.parent/'startup_qt.log'
        log_path.parent.mkdir(parents=True,exist_ok=True)
        def qt_message(kind,context,message):
            try:
                with log_path.open('a',encoding='utf-8') as output:output.write(str(message)+'\n')
            except OSError:pass
        qInstallMessageHandler(qt_message)
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from app.ui.window import Window
    import hashlib
    server_name = 'OrangeNotes.' + hashlib.sha256(str(database).encode()).hexdigest()[:20]
    set_application_identity()
    app = QApplication([sys.argv[0]])
    from app.ui.localization import install_russian_dialogs
    install_russian_dialogs(app)
    app.setApplicationName('Orange Notes')
    from app.utils.runtime_paths import resource_path
    app_icon = resource_path('app/assets/app.ico')
    if app_icon.is_file():
        app.setWindowIcon(QIcon(str(app_icon)))
    app.setStyle('Fusion')
    app.setFont(QFont('Segoe UI',10))
    palette = QPalette()
    for role,color in [(QPalette.Window,'#ffffff'),(QPalette.Base,'#ffffff'),(QPalette.AlternateBase,'#f8f6f3'),(QPalette.Text,'#17202c'),(QPalette.WindowText,'#17202c'),(QPalette.Button,'#f8f6f3'),(QPalette.ButtonText,'#17202c'),(QPalette.Highlight,'#ff890b'),(QPalette.HighlightedText,'#ffffff')]:
        palette.setColor(role,QColor(color))
    app.setPalette(palette)
    QLocale.setDefault(QLocale(QLocale.Russian))
    store = None
    lock = None
    try:
        store = Store(database)
        selected = None
        if args.notification:
            action = notification_action(store, args.notification)
            if not action:
                return 0  # An obsolete notification must never modify a new task.
            from app.services.portable_notifications import notification_transport
            if action['action'] != 'open':
                try: notification_transport(database).remove(action['event_id'])
                except OSError: pass
                return 0
            selected = action['note_id']
        lock = QLockFile(str(database.parent / 'app.lock'))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            socket = QLocalSocket()
            socket.connectToServer(server_name)
            if socket.waitForConnected(1500):
                socket.write(json.dumps({'note_id': selected}).encode())
                socket.waitForBytesWritten(1500)
                socket.disconnectFromServer()
            else:
                QMessageBox.information(None,'Orange Notes уже работает','Откройте приложение через значок в системном трее.')
            return 0
        from app.services.platform_background import supported
        from app.utils.sandbox import in_flatpak
        sandboxed=in_flatpak()
        window = Window(store, background_managed=sandboxed or (supported() and database == Store.default_path().resolve()))
        if sandboxed:
            # No access to host systemd/launchd: retain delivery while the app is running.
            from app.services.reminder_worker import run_worker
            stop_worker=threading.Event()
            app.aboutToQuit.connect(stop_worker.set)
            threading.Thread(target=run_worker,args=(database,),kwargs={'stop_event':stop_worker},daemon=True).start()
            window.statusBar().showMessage('Flatpak: для напоминаний оставьте приложение работающим. Пропущенные события появятся при следующем запуске.')
        server = QLocalServer(window)
        QLocalServer.removeServer(server_name)
        server.listen(server_name)
        def accept():
            connection = server.nextPendingConnection()
            def read():
                try:
                    message = json.loads(bytes(connection.readAll()).decode())
                    if message.get('note_id') is not None:
                        window.notification_note = int(message['note_id'])
                        window.open_notification()
                    else: window.open_window()
                except (ValueError, TypeError): pass
                connection.disconnectFromServer()
            connection.readyRead.connect(read)
            connection.disconnected.connect(connection.deleteLater)
            if connection.bytesAvailable(): read()
        server.newConnection.connect(accept)
        result = {}
        if supported() and database == Store.default_path().resolve():
            threading.Thread(target=setup_background,args=(database,result),daemon=True).start()
            timer = QTimer(window)
            timer.setInterval(500)
            def status():
                if result.get('ready'):
                    window.statusBar().showMessage(result['message'], 0 if result.get('error') else 15000)
                    if result.get('notifications_disabled'):
                        settings_button=QPushButton('Настройки уведомлений',window)
                        settings_button.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('ms-settings:notifications')))
                        window.statusBar().addPermanentWidget(settings_button)
                    timer.stop()
            timer.timeout.connect(status)
            timer.start()
        window.show()
        if selected:
            window.notification_note = selected
            QTimer.singleShot(0,window.open_notification)
        return app.exec()
    except (OSError, sqlite3.Error, ValueError) as error:
        QMessageBox.critical(None,'Не удалось открыть заметки',str(error))
        return 1
    finally:
        if store: store.db.close()
        if lock and lock.isLocked(): lock.unlock()


if __name__ == '__main__':
    sys.exit(main())
