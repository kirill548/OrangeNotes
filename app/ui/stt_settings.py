"""Explicit, cancellable installation of portable offline speech components."""
import threading

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar, QFileDialog


def _audio_diagnostics():
    from app.services.audio_capture import audio_diagnostics
    return audio_diagnostics()


class _STTWorker(QThread):
    progress = Signal(int, int)
    result = Signal(str)
    verified = Signal(object)
    diagnosed = Signal(object)

    def __init__(self, manager, operation, parent=None, paths=None, diagnostic_provider=None):
        super().__init__(parent)
        self.manager = manager
        self.operation = operation
        self.paths = paths
        self.diagnostic_provider = diagnostic_provider
        self.cancel_event = threading.Event()

    def run(self):
        try:
            if self.operation == 'install':
                self.manager.install(self.cancel_event, lambda done, total: self.progress.emit(done, total))
            elif self.operation == 'import':
                self.manager.save_manual_paths(*self.paths)
            if not self.cancel_event.is_set():
                self.verified.emit(self.manager.status())
                try:
                    self.diagnosed.emit(self.diagnostic_provider())
                except Exception:
                    self.diagnosed.emit({'backend_available':False,'microphone_available':False,
                                         'message':'Не удалось проверить микрофон. Текстовый ввод продолжает работать.'})
            else:self.result.emit('Загрузка отменена.')
        except Exception as error:
            self.result.emit(str(error) or 'Не удалось проверить голосовой ввод.')


class STTSettingsTab(QWidget):
    configured = Signal()
    idle = Signal()

    def __init__(self, manager, parent=None, diagnostic_provider=None):
        super().__init__(parent)
        self.manager = manager
        self._worker = None
        self._state = None
        self.diagnostic_provider = diagnostic_provider or _audio_diagnostics
        layout = QVBoxLayout(self)
        description = QLabel('Распознавание выполняется на компьютере. Компоненты сохраняются в stt/ внутри папки приложения и переносятся вместе с ней.')
        description.setWordWrap(True)
        layout.addWidget(description)
        self.status_label = QLabel(); self.status_label.setWordWrap(True); layout.addWidget(self.status_label)
        self.audio_warning = QLabel(); self.audio_warning.setWordWrap(True)
        self.audio_warning.setStyleSheet('color:#86520c;');self.audio_warning.hide();layout.addWidget(self.audio_warning)
        self.progress_bar = QProgressBar(); self.progress_bar.hide(); layout.addWidget(self.progress_bar)
        buttons = QHBoxLayout()
        self.install_button = QPushButton('Скачать и подключить голосовой ввод')
        self.cancel_button = QPushButton('Отменить'); self.cancel_button.hide()
        buttons.addWidget(self.install_button); buttons.addWidget(self.cancel_button); layout.addLayout(buttons)
        self.manual_button = QPushButton('Выбрать готовые файлы…'); layout.addWidget(self.manual_button)
        self.install_button.clicked.connect(self.install)
        self.cancel_button.clicked.connect(self.cancel)
        self.manual_button.clicked.connect(self.choose_manual)
        layout.addStretch()
        self.refresh()

    @property
    def busy(self):
        return self._worker is not None

    def refresh(self):
        if not self.busy:self._start('verify')

    def _verified(self, state):
        self._state = state
        self.status_label.setText(('Компоненты готовы к работе' if state['ready'] else 'Требуется загрузка') + '\n' + state.get('message', ''))
        self.status_label.setStyleSheet('color:#286b42;' if state['ready'] else 'color:#86520c;')
        if state['ready']:self.configured.emit()

    def _diagnosed(self, diagnostic):
        available = diagnostic.get('backend_available',False) and diagnostic.get('microphone_available',False)
        if available:
            self.audio_warning.clear();self.audio_warning.hide()
        else:
            self.audio_warning.setText(diagnostic.get('message') or 'Микрофон недоступен. Подключите его и проверьте разрешение на запись. Текстовый ввод продолжает работать.')
            self.audio_warning.show()

    def install(self):
        if not self.busy:self._start('install')

    def _start(self, operation, paths=None):
        self._worker = _STTWorker(self.manager, operation, self, paths, self.diagnostic_provider)
        self._worker.progress.connect(self._progress)
        self._worker.result.connect(self.status_label.setText)
        self._worker.verified.connect(self._verified)
        self._worker.diagnosed.connect(self._diagnosed)
        self._worker.finished.connect(self._finished)
        self.install_button.setEnabled(False); self.manual_button.setEnabled(False)
        self.cancel_button.show(); self.progress_bar.setRange(0, 0); self.progress_bar.show()
        self.status_label.setText({'verify':'Проверяю компоненты…', 'install':'Загружаю голосовой ввод…', 'import':'Копирую компоненты в папку приложения…'}[operation])
        self._worker.start()

    def _progress(self, done, total):
        if total > 0:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(min(1000, int(done * 1000 / total)))

    def cancel(self):
        if self._worker:
            self._worker.cancel_event.set()
            self.status_label.setText('Отменяю загрузку…')
            self.cancel_button.setEnabled(False)

    def _finished(self):
        worker = self._worker; self._worker = None
        if worker:worker.deleteLater()
        self.progress_bar.hide(); self.cancel_button.hide(); self.cancel_button.setEnabled(True)
        self.manual_button.setEnabled(True)
        self.install_button.setEnabled(bool(self._state and self._state.get('supported', False)))
        self.idle.emit()

    def choose_manual(self):
        if self.busy:return
        executable, _ = QFileDialog.getOpenFileName(self, 'Выберите whisper-cli')
        if not executable:return
        model, _ = QFileDialog.getOpenFileName(self, 'Выберите модель Whisper', '', 'Модель Whisper (ggml-*.bin)')
        if not model:return
        self._start('import', (executable, model))
