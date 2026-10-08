"""Readable reminder diagnostics using the same chrome as the user guide."""
import json
import os
import sys
from datetime import datetime, timezone
from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QWidget, QVBoxLayout, QPushButton
from app.ui.help_dialog import StyledDialog
from app.ui.icons import icon


class ReminderStatusDialog(StyledDialog):
    def __init__(self, store, parent=None):
        super().__init__('Напоминания', 'Доставка уведомлений и фоновая служба', parent, symbol='bell')
        self.store = store
        self.resize(640, 650)
        self.close_button.setAccessibleName('Закрыть состояние напоминаний')
        self.close_button.setToolTip('Закрыть окно · Escape')
        hint=self.footer_layout.takeAt(0)
        if hint and hint.widget():
            hint.widget().hide()
            hint.widget().deleteLater()
        self.service = self._section('Фоновая служба')
        self.delivery = self._section('Доставка')
        self.counts = self._section('История событий')
        self._section('Как это работает', '«Выполнено» закрывает одно событие. Повторяющееся расписание сохраняется. '
                      '«Отложить» переносит событие на 10 минут. Пропущенные напоминания проверяются при следующем входе в систему. '
                      'Компьютер должен быть включён, а уведомления — разрешены системой.')
        refresh = QPushButton('Обновить')
        refresh.setIcon(icon('repeat', '#66717f', 18))
        refresh.clicked.connect(self.refresh)
        self.footer_layout.insertWidget(0, refresh)
        self.footer_layout.insertStretch(1)
        if os.name == 'nt':
            settings = QPushButton('Настройки Windows')
            settings.setIcon(icon('bell', '#ff890b', 18))
            settings.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('ms-settings:notifications')))
            self.footer_layout.insertWidget(1, settings)
        self.refresh()

    def _section(self, title, text=''):
        frame = QWidget()
        frame.setStyleSheet('QWidget#statusCard {background:#faf8f5;border:1px solid #eee9e2;border-radius:14px;}')
        frame.setObjectName('statusCard')
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 16, 18, 16)
        heading = QLabel(title)
        heading.setStyleSheet('font-weight:600;color:#202b38;background:transparent;')
        layout.addWidget(heading)
        body = QLabel(text)
        body.setTextFormat(Qt.PlainText)
        body.setWordWrap(True)
        body.setStyleSheet('color:#647082;background:transparent;')
        body.setAccessibleName(title)
        layout.addWidget(body)
        self.body_layout.addWidget(frame)
        return body

    def refresh(self):
        message = 'Нет данных от службы. Откройте приложение для настройки фонового запуска.'
        error = None
        diagnostic = {}
        try:
            state = json.loads((self.store.path.parent / 'worker_status.json').read_text(encoding='utf-8'))
            heartbeat = datetime.fromisoformat(state['heartbeat_utc'])
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
            fresh = state.get('running') is True and 0 <= age <= 90
            message = ('Служба работает' if fresh else 'Нет свежего отклика от службы')
            message += '\nПоследняя проверка: ' + heartbeat.astimezone().strftime('%d.%m.%Y, %H:%M:%S')
            error = state.get('last_error')
            diagnostic = state.get('notification_status') or {}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.service.setText(message)
        if sys.platform != 'win32':
            from app.services.platform_background import service_path
            self.service.setText(message + '\nКонфигурация службы: ' +
                                 ('сохранена' if service_path().is_file() else 'не установлена'))
        rows = self.store.rows('SELECT status, COUNT(*) AS total FROM reminder_events GROUP BY status')
        totals = {row['status']: row['total'] for row in rows}
        self.counts.setText('   ·   '.join(f'{label}: {totals.get(key, 0)}' for key, label in
                           [('pending', 'Ожидают'), ('notified', 'Доставлены'), ('done', 'Выполнены'), ('cancelled', 'Отменены')]))
        errors = self.store.rows("SELECT last_error FROM reminder_events WHERE status='pending' AND last_error IS NOT NULL ORDER BY id DESC LIMIT 1")
        if errors:
            error = errors[0]['last_error']
        blocked = False
        if os.name == 'nt':
            import winreg
            for path, name in [(r'Software\Microsoft\Windows\CurrentVersion\PushNotifications', 'ToastEnabled'),
                               (r'Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\OrangeNotes.Desktop', 'Enabled')]:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                        blocked |= winreg.QueryValueEx(key, name)[0] == 0
                except OSError:
                    pass
        from app.services.notification_health import BLOCKED_SETTINGS
        if blocked or diagnostic.get('setting') in BLOCKED_SETTINGS or error and any(value in str(error) for value in BLOCKED_SETTINGS):
            text = 'Windows блокирует уведомления. '+str(diagnostic.get('explanation') or 'Проверьте общий переключатель и разрешение Orange Notes в настройках системы.')+'\nНапоминания сохраняются в «Требуют внимания». При открытом приложении их текст показывается в резервной карточке; при сворачивании остаётся индикация в трее. После полного выхода резервная карточка появится при следующем запуске приложения.'
        elif error:
            text = 'Последняя попытка доставки не удалась:\n' + str(error)
        else:
            center = ('Центре уведомлений Windows (Win+N)' if sys.platform == 'win32'
                      else 'Центре уведомлений macOS' if sys.platform == 'darwin' else 'системе уведомлений рабочего стола Linux')
            text = 'Уведомления передаются в ' + center + '. Доставка и выполнение задачи — разные события. '
            if sys.platform != 'win32':
                text += 'Подтверждение приёма системой не гарантирует показ баннера или сохранение в истории.'
        self.delivery.setText(text)
