"""Asynchronous management of application-owned local AI models."""
import threading
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QProgressBar, QTableWidget, QTableWidgetItem,
    QMessageBox, QAbstractItemView)


def _size(value):
    value = float(value or 0)
    for unit in ('Б', 'КиБ', 'МиБ', 'ГиБ', 'ТиБ'):
        if value < 1024 or unit == 'ТиБ':
            return f'{value:.1f} {unit}'
        value /= 1024


class _PackWorker(QThread):
    completed = Signal(str, object, object)
    progress = Signal(object)

    def __init__(self, manager, operation, model='', parent=None):
        super().__init__(parent)
        self.manager, self.operation, self.model = manager, operation, model
        self.cancel_event = threading.Event()

    def run(self):
        try:
            if self.operation == 'scan':
                result = self.manager.scan()
            elif self.operation == 'download':
                result = self.manager.download(self.model, self.cancel_event, self.progress.emit)
            elif self.operation == 'delete':
                result = self.manager.delete(self.model)
            else:
                result = self.manager.switch_active_model(self.model, self.cancel_event)
            self.completed.emit(self.operation, result, None)
        except Exception as error:
            self.completed.emit(self.operation, None, str(error))


class AIPackSettingsTab(QWidget):
    configured = Signal(object)
    idle = Signal()

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self._worker = None
        self._installed = []
        self._rescan = False
        layout = QVBoxLayout(self)
        hint = QLabel('Модели приложения хранятся отдельно. Чужие модели Ollama не удаляются. Размеры ОЗУ ниже — оценки, а не измерение потребления.')
        hint.setWordWrap(True); layout.addWidget(hint)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Модель', 'На диске', 'Состояние', 'ОЗУ (оценка)'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.catalogue = QComboBox()
        catalogue = manager.available_models
        if callable(catalogue): catalogue = catalogue()
        if isinstance(catalogue, dict): catalogue = list(catalogue.values())
        for item in catalogue:
            if isinstance(item, str): name, label = item, item
            else:
                name = str(item.get('name') or item.get('model') or item.get('id') or '')
                ram = item.get('ram_bytes')
                label = str(item.get('label') or name) + (f' · загрузка ≈ {_size(item.get("download_bytes"))} · ОЗУ ≈ {_size(ram)}' if ram else '')
            if name: self.catalogue.addItem(label, name)
        layout.addWidget(self.catalogue)
        self.custom_model = QLineEdit(); self.custom_model.setPlaceholderText('Или название другой модели Ollama (необязательно)')
        layout.addWidget(self.custom_model)
        row = QHBoxLayout()
        self.download_button = QPushButton('Скачать'); self.switch_button = QPushButton('Сделать активной')
        self.delete_button = QPushButton('Удалить'); self.refresh_button = QPushButton('Обновить')
        for button in (self.download_button, self.switch_button, self.delete_button, self.refresh_button): row.addWidget(button)
        layout.addLayout(row)
        self.progress_bar = QProgressBar(); self.progress_bar.hide(); layout.addWidget(self.progress_bar)
        self.status = QLabel(''); self.status.setWordWrap(True); self.status.setTextFormat(Qt.PlainText); layout.addWidget(self.status)
        self.cancel_button = QPushButton('Отменить операцию'); self.cancel_button.hide(); layout.addWidget(self.cancel_button)
        self.download_button.clicked.connect(lambda:self._start('download', self.custom_model.text().strip() or self.catalogue.currentData() or ''))
        self.switch_button.clicked.connect(self._switch)
        self.delete_button.clicked.connect(self._delete)
        self.refresh_button.clicked.connect(lambda:self._start('scan'))
        self.cancel_button.clicked.connect(self.cancel)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self._start('scan')

    @property
    def busy(self): return self._worker is not None

    def _selected(self):
        index = self.table.currentRow()
        return self._installed[index] if 0 <= index < len(self._installed) else None

    def _selection_changed(self):
        item = self._selected()
        self.switch_button.setEnabled(not self.busy and bool(item))
        self.delete_button.setEnabled(not self.busy and bool(item) and item.get('managed', True) and not item.get('active', False))

    def _switch(self):
        item = self._selected()
        if item: self._start('switch', item['name'])

    def _delete(self):
        item = self._selected()
        if not item or not item.get('managed', True) or item.get('active'): return
        answer = QMessageBox.question(self, 'Удалить модель?', f"Удалить {item['name']} из комплекта приложения?\nОсвободится примерно {_size(item.get('reclaimable_bytes', item.get('size_bytes')))}.\nДля повторного использования потребуется скачать модель.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes: self._start('delete', item['name'])

    def _start(self, operation, model=''):
        if self.busy: return
        if operation != 'scan' and not model:
            self.status.setText('Выберите модель.'); return
        self._worker = _PackWorker(self.manager, operation, model, self)
        self._worker.completed.connect(self._completed)
        self._worker.progress.connect(self._progress)
        self._worker.finished.connect(self._finished)
        for control in (self.download_button, self.switch_button, self.delete_button, self.refresh_button, self.catalogue, self.custom_model): control.setEnabled(False)
        self.cancel_button.setVisible(operation in ('download', 'switch')); self.cancel_button.setEnabled(True)
        self.progress_bar.setRange(0, 0); self.progress_bar.show()
        self.status.setText({'scan':'Проверяю установленные модели…','download':'Скачиваю модель…','delete':'Удаляю модель…','switch':'Подключаю модель…'}[operation])
        self._worker.start()

    def cancel(self):
        if self._worker:
            self._worker.cancel_event.set(); self.cancel_button.setEnabled(False)
            self.status.setText('Отменяю операцию. Дождитесь завершения…')

    def _progress(self, packet):
        if isinstance(packet, dict):
            total, done = packet.get('total', 0), packet.get('completed', 0)
            if total:
                self.progress_bar.setRange(0, 100); self.progress_bar.setValue(min(100, int(done * 100 / total)))
            elif isinstance(packet.get('percent'),(int,float)):
                self.progress_bar.setRange(0,100);self.progress_bar.setValue(max(0,min(100,int(packet['percent']))))
            self.status.setText(str(packet.get('status') or 'Скачиваю модель…'))
        else: self.status.setText(str(packet))

    def _completed(self, operation, result, error):
        if error: self.status.setText(error)
        elif operation == 'scan': self._render(result)
        else:
            if operation == 'switch' and isinstance(result, dict): self.configured.emit(result)
            self.status.setText('Операция завершена.'); self._rescan = not self._worker.cancel_event.is_set()

    def _render(self, result):
        items = result.get('installed', result.get('models', [])) if isinstance(result, dict) else result
        self._installed = []
        for value in items or []:
            item = dict(value) if isinstance(value, dict) else {'name': str(value)}
            item['name'] = str(item.get('name') or item.get('model') or '')
            self._installed.append(item)
        self.table.setRowCount(len(self._installed))
        for row, item in enumerate(self._installed):
            ram = item.get('ram_bytes') or next((entry.get('ram_bytes') for entry in self.manager.available_models if entry.get('name') == item['name']), None)
            values = [item['name'], _size(item.get('size_bytes')), 'Активна' if item.get('active') else ('Установлена' if item.get('managed', True) else 'Внешняя'), f'≈ {_size(ram)}' if ram else 'Нет оценки']
            for column, value in enumerate(values): self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.status.setText('Установлено моделей: ' + str(len(self._installed)))

    def _finished(self):
        worker = self._worker; self._worker = None
        if worker: worker.deleteLater()
        self.progress_bar.hide(); self.cancel_button.hide()
        for control in (self.download_button, self.refresh_button, self.catalogue, self.custom_model): control.setEnabled(True)
        self._selection_changed()
        if self._rescan and not (worker and worker.cancel_event.is_set()):
            self._rescan = False; self._start('scan')
        else:
            self._rescan = False; self.idle.emit()


class AIPackSettingsDialog(QDialog):
    """Keep workers alive until cancellation has actually finished."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pack_tab = None
        self._close_pending = False

    def reject(self): self.close()

    def closeEvent(self, event):
        if self.pack_tab and self.pack_tab.busy:
            self._close_pending = True; self.pack_tab.cancel(); event.ignore()
        else:
            self._close_pending=False
            event.accept()
            QDialog.reject(self)

    def finish_pending_close(self):
        if self._close_pending: self.close()
