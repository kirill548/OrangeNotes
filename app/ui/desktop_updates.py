"""Windows update notification and explicit installation action."""

from http.client import HTTPException
from queue import Empty, SimpleQueue
import threading

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from app.services.desktop_updates import check_for_update, discard_installer, download_update
from app.services.windows_update import install_scope, launch_installer
from app.ui.app_menu import MENU_INK
from app.ui.icons import duotone_icon


class DesktopUpdates(QObject):
    def __init__(self, window, executable):
        super().__init__(window)
        self.window = window
        self.scope = install_scope(executable)
        self.release = None
        self.pending = None
        self.cancelled = threading.Event()
        self.results = SimpleQueue()
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.receive)
        QApplication.instance().aboutToQuit.connect(self.cancelled.set)
        self.button = QPushButton(window)
        self.button.hide()
        self.button.clicked.connect(self.offer_update)
        window.status_actions.addWidget(self.button)
        menu = window.app_menu_button.menu()
        self.action = QAction(duotone_icon('arrow-counter-clockwise', MENU_INK, 18), 'Проверить обновления', menu)
        self.action.triggered.connect(self.manual_check)
        menu.insertAction(menu.status_action, self.action)
        QTimer.singleShot(2500, self, self.startup_check)

    def startup_check(self):
        if self.pending is None and self.release is None:
            self._start(check_for_update, manual=False)

    def manual_check(self):
        self._start(check_for_update, manual=True)

    def _start(self, operation, *, manual):
        if self.pending is not None:
            return
        checking = operation is check_for_update
        self.pending = (checking, manual)
        self.action.setEnabled(False)
        self.button.setEnabled(False)
        results, cancelled = self.results, self.cancelled

        def run():
            try:
                result = operation()
                if not cancelled.is_set():
                    results.put((True, result))
                elif not checking:
                    discard_installer(result)
            except (OSError, ValueError, HTTPException) as error:
                results.put((False, error))

        threading.Thread(target=run, daemon=True).start()
        self.timer.start()

    def receive(self):
        try:
            ok, result = self.results.get_nowait()
        except Empty:
            return
        self.timer.stop()
        checking, manual = self.pending
        self.pending = None
        self.action.setEnabled(True)
        self.button.setEnabled(True)
        if checking:
            if ok:
                self.release = result
                self.button.setVisible(result is not None)
                if result:
                    self.button.setText('Обновление ' + result.version)
                    if manual:
                        self.offer_update()
                elif manual:
                    QMessageBox.information(self.window, 'Обновления', 'У Вас актуальная версия Orange Notes.')
            elif manual:
                QMessageBox.warning(self.window, 'Обновления', 'Не удалось проверить обновления. Попробуйте позже.')
            return
        self.button.setText('Обновление ' + self.release.version)
        if ok:
            try:
                if self._can_install():
                    launch_installer(result, self.scope)
                    return
            except OSError as error:
                if getattr(error, 'winerror', None) != 1223:
                    QMessageBox.warning(self.window, 'Обновление', 'Не удалось запустить установщик.')
            discard_installer(result)
        else:
            QMessageBox.warning(self.window, 'Обновление', 'Не удалось скачать или проверить установщик. Попробуйте ещё раз.')

    def _can_install(self):
        if self.window.prepare_for_update():
            return True
        QMessageBox.information(
            self.window, 'Обновление',
            'Сохраните изменения в заметке, завершите работу помощника или закройте расписание и попробуйте снова.')
        return False

    def offer_update(self):
        release = self.release
        if release is None or self.pending is not None:
            return
        if not self.scope or not release.installer:
            QDesktopServices.openUrl(QUrl(release.page))
            return
        if not self._can_install():
            return
        dialog = QMessageBox(self.window)
        dialog.setWindowTitle('Обновление Orange Notes')
        dialog.setText(f'Доступна версия {release.version}.')
        dialog.setInformativeText('Мастер обновит приложение в текущей папке. После установки можно сразу открыть Заметки.')
        install = dialog.addButton('Скачать и установить', QMessageBox.AcceptRole)
        later = dialog.addButton('Позже', QMessageBox.RejectRole)
        dialog.setDefaultButton(later)
        dialog.exec()
        if dialog.clickedButton() == install:
            self.button.setText('Скачиваю обновление…')
            self._start(lambda: download_update(release, self.cancelled), manual=False)
