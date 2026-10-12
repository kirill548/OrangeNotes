"""Small explicit editor for the selected workspace's search vocabulary."""
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                              QComboBox, QLineEdit, QPushButton)
from app.services.memory_store import MemoryStore


class SynonymDictionaryDialog(QDialog):
    def __init__(self, database_path, workspace_id, workspace_name, parent=None):
        super().__init__(parent)
        self.database_path = database_path
        self.workspace_id = workspace_id
        self.setWindowTitle('Словарь синонимов — ' + workspace_name)
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Ваши слова помогают найти заметки, если точного совпадения нет.\n'
                               'Пример: «итерация, спринт». Словарь действует только в этом пространстве.'))
        self.groups = QComboBox()
        layout.addWidget(self.groups)
        self.name = QLineEdit(); self.name.setPlaceholderText('Название группы, например «Работа»')
        self.terms = QLineEdit(); self.terms.setPlaceholderText('Синонимы через запятую: итерация, спринт')
        layout.addWidget(self.name); layout.addWidget(self.terms)
        self.message = QLabel(); self.message.setWordWrap(True); layout.addWidget(self.message)
        actions = QHBoxLayout(); layout.addLayout(actions)
        for text, callback in [('Сохранить', self.save), ('Удалить группу', self.delete), ('Закрыть', self.accept)]:
            button = QPushButton(text); button.clicked.connect(callback); actions.addWidget(button)
        self.groups.currentIndexChanged.connect(self.selected)
        self.reload()

    def reload(self):
        memory = MemoryStore(self.database_path)
        try: groups = memory.list_synonyms(self.workspace_id)
        finally: memory.close()
        self.groups.blockSignals(True); self.groups.clear(); self.groups.addItem('Новая группа', None)
        for group in groups: self.groups.addItem(group['name'], group)
        self.groups.blockSignals(False); self.selected()

    def selected(self):
        group = self.groups.currentData() or {}
        self.name.setText(group.get('name', ''))
        self.terms.setText(', '.join(group.get('terms', [])))

    def save(self):
        memory = MemoryStore(self.database_path)
        try:
            memory.set_synonyms(self.name.text(), [term.strip() for term in self.terms.text().split(',')], self.workspace_id)
            self.message.setText('Сохранено. Следующий поиск использует словарь.'); self.reload()
        except ValueError as error: self.message.setText(str(error))
        finally: memory.close()

    def delete(self):
        group = self.groups.currentData()
        if not group:
            self.message.setText('Выберите сохранённую группу.'); return
        memory = MemoryStore(self.database_path)
        try: memory.delete_synonyms(group['name'], self.workspace_id)
        finally: memory.close()
        self.message.setText('Группа удалена. Заметки сохранены.'); self.reload()
