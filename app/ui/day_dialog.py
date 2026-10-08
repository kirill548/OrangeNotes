"""Optional note suggestions for the deliberately selected My Day list."""
import sqlite3
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget
from app.database.store import plain_body
from app.services.day_focus import DayFocus


class DaySuggestionsDialog(QDialog):
    changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.focus = DayFocus(store)
        self.add_buttons = {}
        self.setWindowTitle('Предложения для «Мой день»')
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setMinimumWidth(460)
        self.setMinimumHeight(340)
        self.resize(560, 520)
        self.setStyleSheet('''
            QDialog { background:#fffdfa; }
            QLabel { color:#243247; }
            QFrame#daySuggestion { background:white; border:1px solid #ece7df; border-radius:12px; }
            QPushButton { border:1px solid #eee5d9; border-radius:9px; padding:9px 15px; background:white; }
            QPushButton#addDayNote { color:white; background:#ff890b; border:none; font-weight:600; }
            QPushButton#addDayNote:hover { background:#ee7900; }
            QPushButton#addDayNote:disabled { background:#e4c5a5; }
            QScrollArea { border:none; background:transparent; }
        ''')
        layout=QVBoxLayout(self)
        layout.setContentsMargins(20,20,20,16)
        layout.setSpacing(14)
        self.heading=QLabel('Предложения')
        self.heading.setStyleSheet('font-size:20px;font-weight:600;')
        layout.addWidget(self.heading)
        help_text=QLabel('Добавляйте в «Мой день» только те заметки, которыми хотите заняться сегодня. '
                         'Список очищается в полночь. Исходные заметки и напоминания сохраняются.')
        help_text.setWordWrap(True)
        help_text.setTextFormat(Qt.PlainText)
        help_text.setStyleSheet('color:#657185;')
        self.help_text=help_text
        layout.addWidget(help_text)
        self.scroll=QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content=QWidget()
        self.rows_layout=QVBoxLayout(self.content)
        self.rows_layout.setContentsMargins(0,0,3,0)
        self.rows_layout.setSpacing(10)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll,1)
        footer=QHBoxLayout()
        footer.addStretch()
        close=QPushButton('Закрыть')
        close.clicked.connect(self.close)
        footer.addWidget(close)
        layout.addLayout(footer)
        self.refresh()

    def refresh(self):
        try:
            rows=self.focus.suggestions()
        except (sqlite3.Error,ValueError):
            QMessageBox.warning(self,'Предложения недоступны','Не удалось прочитать заметки. Повторите попытку после восстановления доступа к базе.')
            return False
        while self.rows_layout.count():
            item=self.rows_layout.takeAt(0)
            widget=item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        self.add_buttons={}
        self.heading.setText('Предложения · '+str(len(rows)))
        for note in rows:
            self._row(note)
        if not rows:
            empty=QLabel('Новых предложений пока нет. Можно добавить любую заметку в «Мой день» из редактора.')
            empty.setWordWrap(True)
            empty.setStyleSheet('color:#7a8596;padding:20px 4px;')
            self.rows_layout.addWidget(empty)
        self.rows_layout.addStretch()
        return True

    def _row(self,note):
        container=QFrame()
        container.setObjectName('daySuggestion')
        row=QHBoxLayout(container)
        row.setContentsMargins(14,12,12,12)
        text=QVBoxLayout()
        text.setSpacing(5)
        title=QLabel(str(note.get('title') or '').strip() or 'Без названия')
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        title.setStyleSheet('font-weight:600;font-size:14px;border:none;')
        text.addWidget(title)
        snippet=' '.join(plain_body(note.get('body') or '').split())
        if snippet:
            preview=QLabel(snippet[:220]+('…' if len(snippet)>220 else ''))
            preview.setTextFormat(Qt.PlainText)
            preview.setWordWrap(True)
            preview.setStyleSheet('color:#667488;border:none;')
            text.addWidget(preview)
        reason=QLabel(str(note.get('reason') or 'Можно выбрать на сегодня'))
        reason.setTextFormat(Qt.PlainText)
        reason.setWordWrap(True)
        reason.setStyleSheet('color:#cc6d0a;font-size:12px;border:none;')
        text.addWidget(reason)
        row.addLayout(text,1)
        add=QPushButton('Добавить')
        add.setObjectName('addDayNote')
        add.setAccessibleName('Добавить в «Мой день»: '+title.text())
        note_id=note['id']
        add.clicked.connect(lambda checked=False,nid=note_id:self._add_note(nid))
        row.addWidget(add,0,Qt.AlignVCenter)
        self.add_buttons[note_id]=add
        self.rows_layout.addWidget(container)

    def _add_note(self,note_id):
        button=self.add_buttons.get(note_id)
        if button:button.setEnabled(False)
        try:
            changed=self.focus.add(note_id)
        except (sqlite3.Error,ValueError):
            if button:button.setEnabled(True)
            QMessageBox.warning(self,'Не удалось добавить заметку','Заметка могла быть удалена или база данных сейчас недоступна. Исходные заметки не изменены.')
            return False
        self.refresh()
        if changed:self.changed.emit()
        return bool(changed)
