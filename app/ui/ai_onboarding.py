"""First-run local AI setup. Downloads and runtime starts require a button click."""
import threading
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QLineEdit, QFileDialog, QProgressBar, QScrollArea, QWidget)

_ACTIVE_WIZARDS=set()


class AIOnboardingWizard(QDialog):
    configured=Signal(dict)
    skipped=Signal()

    def __init__(self,config,parent=None,worker_factory=None,initial_status=None):
        super().__init__(parent)
        self.config=dict(config)
        self.worker_factory=worker_factory
        self._thread=None
        self._closing=False
        self._skip_pending=False
        self._status={}
        self.setWindowTitle('Настройка локального ИИ')
        self.setModal(False)
        self.setMinimumSize(420,400)
        screen=self.screen().availableGeometry()
        self.resize(min(570,screen.width()-24),min(730,screen.height()-40))
        self.setStyleSheet('''QDialog {background:#fffdfa;} QWidget {font-family:"Segoe UI";font-size:13px;color:#29384a;}
            QPushButton {background:white;border:1px solid #e9dfd1;border-radius:9px;padding:10px 14px;}
            QPushButton:disabled {color:#9ca3ab;background:#f7f4ef;}
            QPushButton#setupPrimary {background:#ff890b;color:white;border:none;font-weight:600;}
            QPushButton#setupPrimary:disabled {background:#dac5b0;color:#fff;}
            QScrollArea,QWidget#setupContent {background:#fffdfa;}
            QLabel#setupTitle {font-size:21px;font-weight:600;}
            QLineEdit {background:white;border:1px solid #e9dfd1;border-radius:8px;padding:9px;}
            QProgressBar {border:1px solid #eee4d8;border-radius:6px;background:#fff;min-height:16px;text-align:center;}
            QProgressBar::chunk {background:#ff931f;border-radius:5px;}''')
        outer=QVBoxLayout(self);outer.setContentsMargins(16,16,16,16)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QScrollArea.NoFrame)
        content=QWidget();content.setObjectName('setupContent');scroll.setWidget(content);outer.addWidget(scroll,1)
        layout=QVBoxLayout(content);layout.setContentsMargins(8,8,8,12);layout.setSpacing(12)
        title=QLabel('Помощник на вашем компьютере');title.setObjectName('setupTitle');title.setWordWrap(True);layout.addWidget(title)
        intro=QLabel('Поиск, подсказки и создание заметок уже работают без модели. Для свободного разговора подключите локальный ИИ. Заметки не отправляются в облако.');intro.setWordWrap(True);layout.addWidget(intro)
        self.status_label=QLabel('Проверяю подключение…');self.status_label.setWordWrap(True);self.status_label.setTextFormat(Qt.PlainText);layout.addWidget(self.status_label)
        self.server_label=QLabel('Сервер: ещё не проверен')
        self.model_label=QLabel('Диалог: qwen3:4b — ещё не проверен')
        self.embedding_label=QLabel('Поиск по смыслу: qwen3-embedding:0.6b — ещё не проверен')
        self.details_label=QLabel('Транспорт: NDJSON stream=true');self.details_label.setWordWrap(True)
        for label in (self.server_label,self.model_label,self.embedding_label,self.details_label):
            label.setTextFormat(Qt.PlainText);label.setWordWrap(True);layout.addWidget(label)
        row=QHBoxLayout();self.check_button=QPushButton('Проверить ещё раз');self.check_button.clicked.connect(lambda:self._start('detect'));row.addWidget(self.check_button)
        self.install_button=QPushButton('Установить Ollama');self.install_button.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('https://ollama.com/download')));row.addWidget(self.install_button);layout.addLayout(row)
        pack_title=QLabel('1. Уже есть распакованный AI-pack?');pack_title.setStyleSheet('font-weight:600;');layout.addWidget(pack_title)
        row=QHBoxLayout();self.pack_path=QLineEdit(str(config.get('ai_pack_path') or ''));self.pack_path.setPlaceholderText('Папка с runtime или папка AI-pack');self.pack_path.setAccessibleName('Путь к AI-pack');row.addWidget(self.pack_path,1)
        browse=QPushButton('Обзор…');browse.clicked.connect(self._browse);self.browse_button=browse;row.addWidget(browse);layout.addLayout(row)
        self.pack_button=QPushButton('Подключить AI-pack');self.pack_button.clicked.connect(lambda:self._start('pack',self.pack_path.text().strip()));layout.addWidget(self.pack_button)
        download_title=QLabel('2. Ollama запущен, но моделей нет?');download_title.setStyleSheet('font-weight:600;');layout.addWidget(download_title)
        warning=QLabel('Загрузим недостающие qwen3:4b и qwen3-embedding:0.6b. Нужен интернет и несколько ГБ свободного места. Скачивание начинается только по кнопке; его можно отменить.');warning.setWordWrap(True);layout.addWidget(warning)
        self.pull_button=QPushButton('Скачать недостающие модели');self.pull_button.clicked.connect(lambda:self._start('pull'));layout.addWidget(self.pull_button)
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setValue(0);self.progress.hide();layout.addWidget(self.progress)
        row=QHBoxLayout();self.cancel_button=QPushButton('Отменить операцию');self.cancel_button.clicked.connect(self.cancel);self.cancel_button.hide();row.addWidget(self.cancel_button)
        self.skip_button=QPushButton('Пока пользоваться поиском');self.skip_button.clicked.connect(self.skip);row.addWidget(self.skip_button)
        self.finish_button=QPushButton('Начать разговор');self.finish_button.setObjectName('setupPrimary');self.finish_button.clicked.connect(self.finish);row.addWidget(self.finish_button);outer.addLayout(row)
        if initial_status is not None:self._apply_status(initial_status)
        else:QTimer.singleShot(0,lambda:self._start('detect'))

    @property
    def busy(self):return self._thread is not None

    def _browse(self):
        path=QFileDialog.getExistingDirectory(self,'Папка распакованного AI-pack',self.pack_path.text())
        if path:self.pack_path.setText(path)

    def _apply_status(self,status):
        self._status=dict(status)
        available=bool(status.get('available'));ready=bool(status.get('model_ready'))
        self.server_label.setText('Сервер Ollama: '+('подключён' if available else 'не найден'))
        self.model_label.setText('Диалог: '+str(self.config.get('model','qwen3:4b'))+' — '+('готов' if ready else 'модель не найдена'))
        self.embedding_label.setText('Поиск по смыслу: '+str(self.config.get('embedding_model','qwen3-embedding:0.6b'))+' — '+('готов' if status.get('embedding_ready') else 'модель не найдена; обычный поиск доступен'))
        latency=status.get('latency_ms')
        self.details_label.setText('NDJSON stream=true'+(' · проверка соединения '+str(round(latency))+' мс' if isinstance(latency,(int,float)) else ''))
        if ready:message='Локальный ИИ подключён. Можно начать с «Привет» или попросить помочь с заметкой.'
        elif available:message='Сервер найден. Скачайте недостающие модели или подключите готовый AI-pack.'
        else:message='Локальный ИИ-помощник не подключён. Установите и запустите Ollama, затем проверьте ещё раз. Или выберите готовый AI-pack.'
        if status.get('error'):message+='\n'+str(status['error'])
        self.status_label.setText(message)
        self.pull_button.setEnabled(available and bool(status.get('missing_models')) and not self.busy)
        self.finish_button.setEnabled(ready and not self.busy)
        self.install_button.setVisible(not available)

    def _start(self,kind,path=''):
        if self.busy or self._closing:return False
        if kind=='pack' and not path:
            self.status_label.setText('Выберите папку с распакованным AI-pack.');return False
        if self.worker_factory is None:
            self.status_label.setText('Не удалось запустить настройку. Закройте мастер и попробуйте снова.');return False
        config=dict(self.config)
        if kind=='pack':config['ai_pack_path']=path
        thread=self.worker_factory(kind,config,path)
        self._thread=thread;_ACTIVE_WIZARDS.add(self)
        self._pending_config=config
        thread.completed.connect(self._completed)
        thread.progress.connect(self._progress)
        thread.finished.connect(self._finished)
        for button in (self.check_button,self.pack_button,self.pull_button,self.finish_button,self.browse_button):button.setEnabled(False)
        self.pack_path.setEnabled(False);self.cancel_button.setEnabled(True);self.cancel_button.show()
        self.progress.setRange(0,0);self.progress.show()
        self.status_label.setText({'detect':'Проверяю локальный сервер…','pack':'Подключаю AI-pack…','pull':'Скачиваю модели. Это может занять несколько минут…'}[kind])
        thread.start();return True

    def _progress(self,identity,value):
        if self._closing or not self.busy or self._thread.cancel_event.is_set():return
        if not isinstance(value,dict):return
        self.status_label.setText(str(value.get('model',''))+' · '+str(value.get('status','Загрузка…')))
        total=value.get('total');completed=value.get('completed')
        if isinstance(total,(int,float)) and total>0 and isinstance(completed,(int,float)):
            self.progress.setRange(0,100);self.progress.setValue(max(0,min(100,int(completed*100/total))))
        elif isinstance(value.get('percent'),(int,float)):
            self.progress.setRange(0,100);self.progress.setValue(max(0,min(100,int(value['percent']))))

    def _completed(self,identity,status,error):
        if self._closing or self._skip_pending or (self._thread and self._thread.cancel_event.is_set()):return
        if error:
            self.status_label.setText('Не удалось завершить настройку. '+str(error));return
        if not isinstance(status,dict):return
        if status.get('available'):
            self.config=dict(self._pending_config)
            self.config['provider']='ollama'
            if status.get('runtime_root'):self.config['runtime_root']=status['runtime_root']
            if status.get('managed_pack'):
                self.config['managed_pack']=True;self.config['base_url']=status['base_url']
        self._apply_status(status)

    def _finished(self):
        thread=self._thread;self._thread=None
        cancelled=bool(thread and thread.cancel_event.is_set())
        if thread:thread.deleteLater()
        _ACTIVE_WIZARDS.discard(self)
        self.progress.hide();self.cancel_button.hide();self.pack_path.setEnabled(True)
        for button in (self.check_button,self.pack_button,self.browse_button):button.setEnabled(True)
        self.pull_button.setEnabled(bool(self._status.get('available') and self._status.get('missing_models')))
        self.finish_button.setEnabled(bool(self._status.get('model_ready')))
        if cancelled and not self._skip_pending and not self._closing:
            self.status_label.setText('Операция отменена. Можно продолжить позже; поиск доступен без модели.')
        if self._skip_pending:
            self.skipped.emit();self.accept()
        elif self._closing:self.reject()

    def cancel(self):
        if self._thread:
            self._thread.cancel_event.set();self.cancel_button.setEnabled(False)
            self.status_label.setText('Отменяю операцию. Новые неполные файлы комплекта будут очищены после остановки загрузки.')

    def skip(self):
        if self.busy:
            self._skip_pending=True;self.cancel();return
        self.skipped.emit();self.accept()

    def finish(self):
        if self.busy or not self._status.get('model_ready'):return
        self.config['provider']='ollama';self.config['onboarding_seen']=True
        self.configured.emit(dict(self.config));self.accept()

    def closeEvent(self,event):
        if self.busy:
            self._closing=True;self.cancel();self.hide();event.ignore()
        else:super().closeEvent(event)
