from datetime import datetime
from app.utils.timezones import event_scheduled_local, event_due_local
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton


class ReminderHistoryDialog(QDialog):
    def __init__(self,store,note_id,parent=None):
        super().__init__(parent)
        self.store,self.note_id=store,note_id
        self.setWindowTitle('История напоминаний')
        self.resize(540,430)
        layout=QVBoxLayout(self)
        description=QLabel('История относится к этой заметке. Выполнение события не удаляет текст и не отменяет будущие повторы.')
        description.setWordWrap(True)
        layout.addWidget(description)
        self.events=QListWidget()
        self.events.setWordWrap(True)
        self.events.setAccessibleName('События напоминаний и результаты доставки')
        layout.addWidget(self.events)
        refresh=QPushButton('Обновить')
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)
        self.refresh()

    def refresh(self):
        self.events.clear()
        labels={'pending':'Ожидает доставки','notified':'Доставлено · ещё не выполнено','done':'Выполнено','cancelled':'Отменено'}
        rows=self.store.rows('SELECT e.* FROM reminder_events e JOIN reminders r ON r.id=e.reminder_id WHERE r.note_id=? ORDER BY COALESCE(e.scheduled_utc,e.scheduled_at) DESC,e.id DESC LIMIT 200',(self.note_id,))
        for row in rows:
            scheduled=event_scheduled_local(row).strftime('%d.%m.%Y, %H:%M')
            status=labels.get(row['status'],row['status'])
            if row['status']=='pending' and row['due_at']!=row['scheduled_at']:
                status='Отложено до '+event_due_local(row).strftime('%d.%m.%Y, %H:%M')
            text=f'{scheduled} — {status}\n{row["title"] or "Без названия"}\n{(row["body"] or "")[:200]}'
            if row['status']=='pending' and row['last_error']:
                text+='\nДоставка будет повторена: '+row['last_error']
            item=QListWidgetItem(text)
            self.events.addItem(item)
        if not rows:
            self.events.addItem('Сработавших напоминаний пока нет.')

