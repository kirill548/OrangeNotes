"""Persistent, nonmodal fallback; it never claims native OS delivery."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton


class NotificationFallback(QFrame):
    def __init__(self, open_events, open_diagnostics, parent=None):
        super().__init__(parent)
        self.setObjectName('notificationFallback')
        self.setStyleSheet('QFrame#notificationFallback {background:#fff4e7;border:1px solid #ffc889;border-radius:12px;} QLabel {background:transparent;color:#754618;} QPushButton {padding:6px 10px;}')
        layout=QVBoxLayout(self)
        layout.setContentsMargins(12,10,12,10)
        self.heading=QLabel('Уведомления Windows отключены')
        self.heading.setStyleSheet('font-weight:600;')
        self.heading.setWordWrap(True)
        self.preview=QLabel()
        self.preview.setTextFormat(Qt.PlainText)
        self.preview.setWordWrap(True)
        self.preview.setAccessibleName('Резервное напоминание')
        layout.addWidget(self.heading)
        layout.addWidget(self.preview)
        actions=QHBoxLayout()
        self.open_button=QPushButton('Посмотреть')
        self.open_button.clicked.connect(open_events)
        self.settings_button=QPushButton('Почему?')
        self.settings_button.clicked.connect(open_diagnostics)
        actions.addWidget(self.open_button)
        actions.addWidget(self.settings_button)
        layout.addLayout(actions)
        self.hide()

    def update_events(self, blocked, events, count=None):
        self.setVisible(bool(blocked and events))
        if not blocked or not events:
            return
        event=events[0]
        title=str(event['title'] or 'Без названия').strip()
        body=str(event['body'] or 'Откройте заметку, чтобы посмотреть подробности.').strip()
        text=(title+'\n' if title!='Без названия' else '')+body[:180]
        self.preview.setText('Ожидают внимания: '+str(count if count is not None else len(events))+'\n'+text)
