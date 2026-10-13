"""Small workspace-scoped note picker; it never reads note bodies."""
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox, QPushButton, QScrollArea


class NoteContextPins(QWidget):
    changed = Signal()
    MAX_PINS = 8
    DEFAULT_HINT = 'Помощник сначала использует прикреплённые заметки. Из длинных заметок выбирает фрагменты по вопросу. Доступно только выбранное пространство.'

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.workspace_id = 1
        self.include_archive = True
        self.include_trash = False
        self._pins = {}
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(4)
        self.caption = QLabel('Прикреплённые заметки · 0/8'); layout.addWidget(self.caption)
        row = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText('Найти заметку по заголовку…')
        self.search.setAccessibleName('Поиск заметок для контекста'); row.addWidget(self.search, 1)
        self.candidates = QComboBox(); self.candidates.setAccessibleName('Заметка для прикрепления')
        self.candidates.setMinimumWidth(130); row.addWidget(self.candidates, 1)
        self.add_button = QPushButton('+'); self.add_button.setAccessibleName('Прикрепить выбранную заметку')
        self.add_button.setToolTip('Прикрепить заметку к следующим вопросам'); row.addWidget(self.add_button)
        layout.addLayout(row)
        self.pin_area = QScrollArea(); self.pin_area.setWidgetResizable(True)
        self.pin_area.setMaximumHeight(48); self.pin_area.setFrameShape(QScrollArea.NoFrame)
        self.pin_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pin_content = QWidget(); self.pin_row = QHBoxLayout(self.pin_content)
        self.pin_row.setContentsMargins(0,0,0,0)
        self.pin_area.setWidget(self.pin_content); self.pin_area.hide(); layout.addWidget(self.pin_area)
        self.hint = QLabel(self.DEFAULT_HINT)
        self.hint.setWordWrap(True); self.hint.setStyleSheet('color:#7e8793;font-size:11px;'); layout.addWidget(self.hint)
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(180)
        self._timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda: self._timer.start())
        self.search.returnPressed.connect(self._search_enter)
        self.add_button.clicked.connect(self.add_current)
        self.candidates.currentIndexChanged.connect(self._update_add)

    @property
    def note_ids(self): return tuple(self._pins)

    def set_scope(self, workspace_id, include_archive=True, include_trash=False):
        if workspace_id != self.workspace_id:
            self.clear(); self.search.clear()
        self.workspace_id = workspace_id
        self.include_archive = include_archive; self.include_trash = include_trash
        self.refresh()

    def _refresh_pins(self):
        previous = dict(self._pins)
        for note_id in list(self._pins):
            rows = self.store.rows('SELECT id,title FROM notes WHERE id=? AND workspace_id=? AND (? OR archived=0) AND (? OR deleted=0)',
                (note_id, self.workspace_id, int(self.include_archive), int(self.include_trash)))
            if not rows:
                self._pins.pop(note_id)
            else:
                self._pins[note_id] = rows[0]['title'].strip() or 'Без названия'
        removed = len(previous) - len(self._pins)
        if previous != self._pins:
            self._render(); self.changed.emit()
        return removed

    def refresh(self):
        self._timer.stop()
        removed = self._refresh_pins()
        from app.services.memory_store import MemoryStore
        memory = MemoryStore(self.store.path)
        try:
            rows = memory.list_context_notes(self.search.text().strip()[:200], self.workspace_id,
                include_archive=self.include_archive, include_trash=self.include_trash, limit=30)
        finally: memory.close()
        self.candidates.clear()
        for item in rows:
            if item['id'] not in self._pins:
                title = item['title'].strip() or 'Без названия'
                self.candidates.addItem(title[:65] + ' · #' + str(item['id']), item['id'])
                self.candidates.setItemData(self.candidates.count()-1, title, Qt.ToolTipRole)
        self.candidates.setPlaceholderText('Нет подходящих заметок')
        # A placeholder keeps the combo at -1 after clear(), even with new rows.
        # Always select an actual candidate so the quick-add action is usable.
        if self.candidates.count():
            self.candidates.setCurrentIndex(0)
        self.candidates.setEnabled(bool(self.candidates.count()))
        if removed:
            self.hint.setText('Убраны недоступные заметки: ' + str(removed) + '. Проверьте выбранный контекст.')
        elif len(self._pins) >= self.MAX_PINS:
            self.hint.setText('Прикреплено 8 заметок. Уберите одну кнопкой ×, чтобы добавить другую.')
        elif not self.candidates.count():
            self.hint.setText('Других подходящих заметок нет. Измените запрос или проверьте выбранное пространство и архив.')
        else:
            self.hint.setText(self.DEFAULT_HINT)
        self._update_add()

    def _search_enter(self): self.refresh(); self.candidates.setFocus()

    def _update_add(self, *args):
        self.add_button.setEnabled(len(self._pins) < self.MAX_PINS and self.candidates.currentData() is not None)

    def add_current(self):
        note_id = self.candidates.currentData()
        if note_id is None or note_id in self._pins or len(self._pins) >= self.MAX_PINS: return False
        rows = self.store.rows('SELECT id,title FROM notes WHERE id=? AND workspace_id=? AND (? OR archived=0) AND (? OR deleted=0)',
            (note_id, self.workspace_id, int(self.include_archive), int(self.include_trash)))
        if not rows: self.refresh(); return False
        self._pins[note_id] = rows[0]['title'].strip() or 'Без названия'
        self._render(); self.refresh(); self.changed.emit(); return True

    def remove(self, note_id):
        if note_id not in self._pins: return False
        self._pins.pop(note_id); self._render(); self.refresh(); self.changed.emit(); return True

    def clear(self):
        self._pins.clear(); self._render(); self.changed.emit()

    def _render(self):
        while self.pin_row.count():
            item = self.pin_row.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for note_id, title in self._pins.items():
            button = QPushButton(title[:16] + ' ×'); button.setToolTip(title + ' · #' + str(note_id))
            button.setAccessibleName('Убрать из контекста: ' + title)
            button.clicked.connect(lambda checked=False, value=note_id: self.remove(value)); self.pin_row.addWidget(button)
        self.pin_row.addStretch()
        self.pin_area.setVisible(bool(self._pins))
        self.caption.setText('Прикреплённые заметки · ' + str(len(self._pins)) + '/8')
        self._update_add()
