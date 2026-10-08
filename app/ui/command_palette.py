"""Keyboard-first discovery of existing application actions, without model calls."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QLineEdit, QListWidget, QListWidgetItem

from app.ui.help_dialog import StyledDialog
from app.ui.icons import icon


COMMANDS = (
    ('new_note', 'Создать заметку', 'plus', 'новая запись добавить'),
    ('search', 'Найти заметку', 'search', 'поиск текст'),
    ('attention', 'Напоминания, требующие внимания', 'bell', 'пропущенные просроченные уведомления очередь'),
    ('companion', 'Открыть помощника', 'note', 'ии пчёлка чат помощь'),
    ('archive', 'Открыть архив', 'archive', 'архивированные'),
    ('trash', 'Открыть корзину', 'trash', 'удалённые восстановить'),
    ('help', 'Помощь и горячие клавиши', 'book', 'справка инструкция'),
    ('reminder_status', 'Состояние напоминаний', 'bell', 'диагностика уведомления'),
    ('backup', 'Резервная копия базы', 'archive', 'сохранить бэкап данные'),
)


class CommandPalette(StyledDialog):
    commandTriggered = Signal(str)

    def __init__(self, parent=None):
        super().__init__('Быстрые действия', 'Найдите действие по названию и нажмите Enter.', parent, 'search')
        self.close_button.setAccessibleName('Закрыть быстрые действия')
        self.close_button.setToolTip('Закрыть · Escape')
        self.done_button.hide()
        self.footer_layout.itemAt(0).widget().setText('↑ ↓ — выбрать · Enter — выполнить · Escape — закрыть')
        self.query = QLineEdit()
        self.query.setPlaceholderText('Например: новая заметка, корзина, помощь…')
        self.query.setAccessibleName('Поиск действий')
        self.actions = QListWidget()
        self.actions.setAccessibleName('Доступные действия')
        self.actions.setSelectionMode(QAbstractItemView.SingleSelection)
        self.actions.setMinimumHeight(270)
        self.cards.addWidget(self.query)
        self.cards.addWidget(self.actions, 1)
        self.setStyleSheet(self.styleSheet() + '''
            QLineEdit {background:white;color:#293648;border:1px solid #e9e3dc;
                border-radius:11px;padding:12px;}
            QLineEdit:focus {border-color:#ff890b;}
            QListWidget {background:transparent;border:none;color:#293648;outline:0;}
            QListWidget::item {padding:12px;border-radius:9px;margin:2px 0;}
            QListWidget::item:selected {background:#fff0df;color:#a95008;}
            QListWidget::item:hover {background:#fff5e9;}
        ''')
        self.query.installEventFilter(self)
        self.query.textChanged.connect(self.filter_actions)
        self.actions.itemActivated.connect(self.activate_current)
        self.actions.itemClicked.connect(self.activate_current)
        self.filter_actions('')
        self.resize(580, 530)

    def filter_actions(self, text):
        words=text.casefold().replace('ё','е').split()
        self.actions.clear()
        for identity, title, symbol, aliases in COMMANDS:
            haystack=(title+' '+aliases).casefold().replace('ё','е')
            if all(word in haystack for word in words):
                item=QListWidgetItem(icon(symbol,'#e8841b',20),title)
                item.setData(Qt.UserRole,identity)
                self.actions.addItem(item)
        if self.actions.count():
            self.actions.setCurrentRow(0)
        else:
            item=QListWidgetItem('Ничего не найдено. Попробуйте другое слово.')
            item.setFlags(Qt.NoItemFlags)
            self.actions.addItem(item)

    def activate_current(self, *_):
        item=self.actions.currentItem()
        identity=item.data(Qt.UserRole) if item else None
        if identity:
            self.accept()
            self.commandTriggered.emit(identity)

    def eventFilter(self, source, event):
        if source is self.query and event.type()==QEvent.KeyPress:
            if event.key() in (Qt.Key_Return,Qt.Key_Enter):
                self.activate_current()
                return True
            if event.key() in (Qt.Key_Down,Qt.Key_Up):
                delta=1 if event.key()==Qt.Key_Down else -1
                row=max(0,min(self.actions.count()-1,self.actions.currentRow()+delta))
                self.actions.setCurrentRow(row)
                return True
        return super().eventFilter(source,event)

    def showEvent(self, event):
        super().showEvent(event)
        self.query.setFocus()
