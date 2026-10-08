from PySide6.QtWidgets import QPushButton
from app.ui.help_dialog import StyledDialog


class RestoreDialog(StyledDialog):
    def __init__(self, details, callback, parent):
        super().__init__('Восстановление базы', 'Замена текущих заметок резервной копией', parent, 'archive')
        self._card('archive','Выбранная копия', [('Файл',str(details['path'])),('Заметок',str(details['notes']))])
        self._card('note','Перед восстановлением', [('Важно','Текущие заметки и расписания будут заменены содержимым копии. Автоматическая защитная копия текущей базы сохранится рядом с базой. Фоновая доставка временно остановится. Просроченные события из копии могут снова потребовать внимания.')])
        self.done_button.setText('Отмена')
        self.restore_button=QPushButton('Восстановить из копии')
        self.restore_button.setObjectName('helpDone')
        self.footer_layout.addWidget(self.restore_button)
        def perform():
            self.restore_button.setEnabled(False)
            if callback(): self.close()
            else: self.restore_button.setEnabled(True)
        self.restore_button.clicked.connect(perform)
