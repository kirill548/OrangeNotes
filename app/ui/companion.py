"""Local companion UI. Model calls run only after an explicit user action."""
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QThread, Signal, QSize, QDateTime, QTimer, Slot, QPropertyAnimation, QEasingCurve
from app.utils.shortcuts import shortcut, shortcut_label
from PySide6.QtGui import QShortcut, QKeySequence, QPainter
from PySide6.QtWidgets import (QCheckBox,QComboBox,QDialog,QDateTimeEdit,QFormLayout,QHBoxLayout,QLabel,QLineEdit,QMessageBox,QPlainTextEdit,QPushButton,QScrollArea,QVBoxLayout,QWidget,QTabWidget)
from app.services.memory_store import MemoryStore
from app.widgets.companion_mascot import companion_icon
from app.utils.app_icon import application_icon
from app.services.audio_capture import VoiceInputController
from app.services.app_paths import get_app_dir, ai_dir, stt_dir, resolve_app_path, portable_app_path, check_app_writable


class _AssistantAvatarMargin(QWidget):
    """Paint the local bee avatar alongside plain text, never parse model HTML."""
    def paintEvent(self, event):
        chat=self.parent()
        painter=QPainter(self)
        for y in chat.assistant_avatar_positions():
            chat.avatar_icon.paint(painter,3,round(y),20,20)


class BrandedChatEdit(QPlainTextEdit):
    """Keep grounding rollback and source excerpts strictly plain text."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.avatar_icon=companion_icon()
        self.avatar_margin=_AssistantAvatarMargin(self)
        self.setViewportMargins(28,0,0,0)
        self.updateRequest.connect(lambda rect,dy:self.avatar_margin.update())
        self.textChanged.connect(self.avatar_margin.update)

    def assistant_avatar_positions(self):
        block=self.firstVisibleBlock()
        while block.isValid():
            top=self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
            if top>self.viewport().height():break
            if block.isVisible() and block.text()=='Помощник':yield top
            block=block.next()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        rect=self.contentsRect()
        self.avatar_margin.setGeometry(rect.left(),rect.top(),28,rect.height())

_ACTIVE_DIALOGS=set()
DEFAULT_CONFIG={'provider':'ollama','base_url':'http://127.0.0.1:11434','model':'qwen3:4b','embedding_model':'qwen3-embedding:0.6b',
                'runtime_root':'','ai_pack_path':'','onboarding_seen':False,'managed_pack':False,'runtime_profile':'default'}


def _engine_factory(path):
    from app.services.companion import CompanionEngine
    return CompanionEngine(path)


def validate_config(config):
    result=dict(DEFAULT_CONFIG)
    result.update({key:config[key] for key in DEFAULT_CONFIG if key in config})
    if result['provider'] not in ('ollama','search'):
        raise ValueError('Выберите локальный ИИ или поиск в заметках.')
    url=urlsplit(str(result['base_url']).strip())
    try:port=url.port
    except ValueError as error:raise ValueError('Проверьте адрес локального ИИ.') from error
    if url.scheme!='http' or url.hostname not in ('localhost','127.0.0.1','::1') or url.username or url.password or url.path not in ('','/') or url.query or url.fragment or port==0:
        raise ValueError('Можно подключиться только к ИИ на этом компьютере: http://127.0.0.1:11434.')
    for key in ('model','embedding_model'):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}',str(result[key]).strip()):
            raise ValueError('Укажите название локальной модели без пробелов.')
        result[key]=str(result[key]).strip()
    result['base_url']=str(result['base_url']).strip().rstrip('/')
    for key in ('runtime_root','ai_pack_path'):
        if not isinstance(result[key],str) or len(result[key])>4096 or '\x00' in result[key]:
            raise ValueError('Проверьте путь к ИИ-комплекту.')
    if type(result['onboarding_seen']) is not bool:raise ValueError('Неверный статус первой настройки.')
    if type(result['managed_pack']) is not bool:raise ValueError('Неверный статус ИИ-комплекта.')
    from app.services.runtime_profiles import RUNTIME_PROFILES
    if result['runtime_profile'] not in RUNTIME_PROFILES:raise ValueError('Неизвестный профиль запуска ИИ.')
    return result


class _RequestThread(QThread):
    completed=Signal(int,object,object)
    advisory=Signal(int,str)
    progress=Signal(int,object)

    def __init__(self,request_id,factory,path,query,workspace_id,mode,history,config,include_archive,include_trash,cancel_event,kind='ask',attached_note_ids=()):
        super().__init__()
        self.request_id=request_id
        self.factory=factory
        self.path=path
        self.query=query
        self.workspace_id=workspace_id
        self.mode=mode
        self.history=history
        self.config=config
        self.include_archive=include_archive
        self.include_trash=include_trash
        self.cancel_event=cancel_event
        self.kind=kind
        self.attached_note_ids=tuple(attached_note_ids)

    def run(self):
        engine=None
        try:
            if self.cancel_event.is_set():return
            if self.kind=='ask' and self.config.get('provider')=='ollama':
                from app.services.local_runtime import resource_advisory
                warning=resource_advisory().get('warning')
                if warning:self.advisory.emit(self.request_id,warning)
            if self.kind in ('detect','pack','pull'):
                from app.services.ai_onboarding import ModelManager
                bundled_config=None
                if (self.kind=='detect' and not (ai_dir()/'ai_settings.json').exists()
                    and not self.config.get('runtime_root') and not self.config.get('managed_pack')
                    and not self.config.get('ai_pack_path')
                    and self.config.get('base_url','http://127.0.0.1:11434')=='http://127.0.0.1:11434'):
                    from app.services.bundled_ai import find_complete_seed, install_complete_seed
                    from app.utils.runtime_paths import resource_path
                    import sys
                    seed=find_complete_seed(resource_path(''),cancel_event=self.cancel_event)
                    if seed is None and getattr(sys,'frozen',False):
                        executable_root=Path(sys.executable).parent
                        seed=find_complete_seed(executable_root.parent.parent if sys.platform=='darwin' else executable_root,cancel_event=self.cancel_event)
                    if seed is not None:
                        bundled_config=install_complete_seed(seed[0],get_app_dir(),self.cancel_event)
                        from app.services.ai_pack_runtime import ManagedPackRuntime
                        runtime=ManagedPackRuntime(ai_dir(),bundled_config['runtime_root'])
                        bundled_config['base_url']=runtime.ensure(self.cancel_event)
                        self.config={**self.config,**bundled_config}
                manager=ModelManager({**self.config,'_managed_root':str(ai_dir())})
                if self.kind=='pack':response=manager.connect_pack(self.query,self.cancel_event)
                elif self.kind=='pull':response=manager.pull_missing(self.cancel_event,lambda value:self.progress.emit(self.request_id,value))
                else:response=manager.detect(self.cancel_event)
                if bundled_config is not None:response['_bundled_config']=bundled_config
            else:
                engine=self.factory(self.path)
            if self.kind=='status':
                response=engine.status(self.config)
            elif self.kind=='ask':
                stream_options={}
                if getattr(engine,'supports_stream_segments',False):
                    stream_options['on_segment']=lambda packet:self.progress.emit(self.request_id,packet)
                if getattr(engine,'supports_note_context',False):
                    stream_options['attached_note_ids']=self.attached_note_ids
                response=engine.ask(self.query,self.workspace_id,mode=self.mode,
                                    include_archive=self.include_archive,include_trash=self.include_trash,
                                    history=self.history,config=self.config,cancel_event=self.cancel_event,**stream_options)
            if not isinstance(response,dict):raise ValueError('Помощник вернул непонятный ответ.')
            if self.kind=="ask":response["_metrics_finished"]=time.monotonic()
            self.completed.emit(self.request_id,response,None)
        except Exception as error:
            self.completed.emit(self.request_id,None,str(error))
        finally:
            if engine is not None:
                close=getattr(engine,'close',None)
                if callable(close):
                    try:close()
                    except Exception:pass


class CompanionDialog(QDialog):
    open_note=Signal(int)
    note_created=Signal(int)
    shutdown_ready=Signal()

    def __init__(self,store,parent=None,engine_factory=None,metrics_collector=None):
        super().__init__(parent)
        self.store=store
        self.metrics_collector=metrics_collector
        self._metrics_identity=None
        self.database_path=Path(store.path).resolve()
        self.engine_factory=engine_factory or _engine_factory
        self._auto_detect=engine_factory is None
        self._detected_once=False
        self._onboarding_dialog=None
        self._connection_status=None
        self._open_onboarding_pending=False
        self._thread=None
        self._deadline_timer=QTimer(self)
        self._deadline_timer.setSingleShot(True)
        self._deadline_timer.timeout.connect(self._deadline_expired)
        self._cancel_event=None
        self._request_id=0
        self._history=[]
        self._pending_query=''
        self._stream_baseline=None
        self._stream_scope=None
        self._voice_active=False
        self._voice_scope=None
        self._voice_discard=False
        self._closing=False
        self._settings_dialog=None
        self.source_buttons=[]
        self.settings_path=ai_dir()/'ai_settings.json'
        self.config=self._load_config()
        self._sync_metrics_config()
        self._pending_config=dict(self.config)
        memory=MemoryStore(self.database_path)
        try:self.workspaces=memory.list_workspaces()
        finally:memory.close()
        self.setWindowTitle('Помощник Nopen')
        self.setWindowIcon(application_icon())
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setMinimumSize(520,480)
        screen=self.screen().availableGeometry()
        self.resize(min(640,screen.width()-32),min(700,screen.height()-48))
        self.setStyleSheet('''QWidget {font-family:"Segoe UI";font-size:13px;color:#29384a;} QDialog {background:#fffdfa;} QLabel {color:#29384a;}
            QPlainTextEdit,QLineEdit,QComboBox {background:white;border:1px solid #e9e4dc;border-radius:9px;padding:8px;}
            QComboBox::drop-down {border:none;width:22px;}
            QCheckBox::indicator {width:16px;height:16px;border:1px solid #c9cfd5;border-radius:5px;background:white;}
            QCheckBox::indicator:checked {background:#ff931f;border-color:#ff931f;}
            QPushButton {background:white;border:1px solid #eee4d8;border-radius:9px;padding:8px 12px;}
            QPushButton#companionSend {background:#ff890b;color:white;border:none;font-weight:600;}
            QPushButton#companionSend:disabled {background:#dac5b0;}
            QScrollArea {border:none;background:transparent;}
        ''')
        layout=QVBoxLayout(self);layout.setContentsMargins(20,18,20,16);layout.setSpacing(12)
        header=QHBoxLayout()
        mascot=QLabel();mascot.setPixmap(companion_icon().pixmap(QSize(48,48)));header.addWidget(mascot)
        headings=QVBoxLayout()
        title=QLabel('Помощник по заметкам');title.setStyleSheet('font-size:18px;font-weight:600;');headings.addWidget(title)
        subtitle=QLabel('Спросите, найдите заметку или создайте напоминание.');subtitle.setWordWrap(True);headings.addWidget(subtitle)
        header.addLayout(headings,1)
        self.new_chat_button=QPushButton('Новый разговор');self.new_chat_button.clicked.connect(self.new_conversation);header.addWidget(self.new_chat_button)
        layout.addLayout(header)
        connection=QHBoxLayout()
        self.connection_banner=QLabel('Проверяю ИИ…')
        self.connection_banner.setTextFormat(Qt.PlainText);self.connection_banner.setWordWrap(True)
        self.connection_banner.setStyleSheet('background:#fff0dc;border-radius:8px;padding:8px;color:#86520c;')
        connection.addWidget(self.connection_banner,1)
        self.setup_button=QPushButton('Настройки ИИ');self.setup_button.clicked.connect(self.show_settings);connection.addWidget(self.setup_button)
        layout.addLayout(connection)
        controls=QHBoxLayout()
        controls.addWidget(QLabel('Пространство'))
        self.workspace=QComboBox()
        self.workspace_combo=self.workspace
        self.workspace.setToolTip('Помощник использует только заметки выбранного пространства.')
        for item in self.workspaces:self.workspace.addItem(item['name'],item['id'])
        controls.addWidget(self.workspace,1)
        self.attach_notes_button=QPushButton('Прикрепить заметки');self.attach_notes_button.setCheckable(True)
        controls.addWidget(self.attach_notes_button)
        self.search_options_button=QPushButton('Параметры');self.search_options_button.setCheckable(True)
        controls.addWidget(self.search_options_button);layout.addLayout(controls)
        self.advanced_panel=QWidget();advanced=QVBoxLayout(self.advanced_panel);advanced.setContentsMargins(0,0,0,0)
        advanced_row=QHBoxLayout()
        self.mode=QComboBox()
        for label,value in [('Найти в заметках','memory'),('Обсудить','discuss'),('Проверить','audit'),('Подготовить черновик','draft')]:self.mode.addItem(label,value)
        self.mode.setCurrentIndex(self.mode.findData('discuss'));self.mode.hide()
        self.settings_button=QPushButton('Настройки ИИ');self.settings_button.clicked.connect(self.show_settings);self.settings_button.hide()
        self.synonyms_button=QPushButton('Синонимы');self.synonyms_button.setToolTip('Настроить свои слова для поиска в выбранном пространстве')
        self.synonyms_button.clicked.connect(self.show_synonyms);advanced_row.addWidget(self.synonyms_button);advanced.addLayout(advanced_row)
        self.mode_hint=QLabel();self.mode_hint.setWordWrap(True);self.mode_hint.setTextFormat(Qt.PlainText);self.mode_hint.hide()
        self.mode.currentIndexChanged.connect(self._mode_changed);self._mode_changed()
        self.scope_hint=QLabel();self.scope_hint.setWordWrap(True);self.scope_hint.hide()
        options=QHBoxLayout()
        self.include_archive=QCheckBox('Включать архив');self.include_archive.setChecked(True)
        self.include_trash=QCheckBox('Искать в корзине');self.include_trash.setChecked(False)
        options.addWidget(self.include_archive);options.addWidget(self.include_trash);options.addStretch();advanced.addLayout(options)
        self.fuzzy_identifiers=QCheckBox('Искать похожие номера при опечатке');self.fuzzy_identifiers.setChecked(True)
        self.fuzzy_identifiers.setToolTip('Только если точных совпадений нет. Похожие номера показываются отдельно.')
        advanced.addWidget(self.fuzzy_identifiers);layout.addWidget(self.advanced_panel);self.advanced_panel.hide()
        self.search_options_button.toggled.connect(self.advanced_panel.setVisible)
        from app.widgets.note_context_pins import NoteContextPins
        self.context_pins=NoteContextPins(self.store,self)
        self.context_pins.set_scope(self.workspace_id,self.include_archive.isChecked(),self.include_trash.isChecked())
        self.include_archive.toggled.connect(self._context_policy_changed)
        self.include_trash.toggled.connect(self._context_policy_changed)
        layout.addWidget(self.context_pins);self.context_pins.hide()
        self.attach_notes_button.toggled.connect(self._toggle_context_panel)
        self.context_pins.changed.connect(self._context_pins_changed)
        self.chat=BrandedChatEdit();self.chat.setReadOnly(True);self.chat.setAccessibleName('Разговор с помощником');layout.addWidget(self.chat,1)
        self._scroll_animation=QPropertyAnimation(self.chat.verticalScrollBar(),b'value',self)
        self._scroll_animation.setDuration(100)
        self._scroll_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.chat.verticalScrollBar().sliderPressed.connect(self._scroll_animation.stop)
        self.chat.verticalScrollBar().actionTriggered.connect(lambda action:self._scroll_animation.stop())
        self.help_actions_panel=QWidget();self.help_actions_layout=QHBoxLayout(self.help_actions_panel)
        self.help_actions_layout.setContentsMargins(0,0,0,0);self.help_action_buttons=[]
        self.help_actions_panel.hide();layout.addWidget(self.help_actions_panel)
        self._action_draft=None
        self.draft_panel=QWidget();draft_layout=QVBoxLayout(self.draft_panel);draft_layout.setContentsMargins(0,0,0,0)
        draft_header=QHBoxLayout()
        self.draft_brand=QLabel();self.draft_brand.setPixmap(application_icon().pixmap(QSize(24,24)))
        self.draft_brand.setAccessibleName('Orange Notes');draft_header.addWidget(self.draft_brand)
        self.draft_heading=QLabel('Проверьте заметку перед созданием');draft_header.addWidget(self.draft_heading,1);draft_layout.addLayout(draft_header)
        self.draft_recurrence=QLabel();self.draft_recurrence.setTextFormat(Qt.PlainText);self.draft_recurrence.setWordWrap(True);draft_layout.addWidget(self.draft_recurrence)
        draft_layout.addWidget(QLabel('Заголовок'))
        self.draft_title=QLineEdit();self.draft_title.setAccessibleName('Название новой заметки');draft_layout.addWidget(self.draft_title)
        draft_layout.addWidget(QLabel('Текст'))
        self.draft_body=QPlainTextEdit();self.draft_body.setMaximumHeight(100);self.draft_body.setAccessibleName('Текст новой заметки');draft_layout.addWidget(self.draft_body)
        row=QHBoxLayout();self.draft_reminder=QCheckBox('Напомнить');row.addWidget(self.draft_reminder)
        self.draft_time=QDateTimeEdit();self.draft_time.setDisplayFormat('dd.MM.yyyy HH:mm');self.draft_time.setCalendarPopup(True);row.addWidget(self.draft_time);draft_layout.addLayout(row)
        self.draft_actions=QWidget();actions=QHBoxLayout(self.draft_actions);actions.setContentsMargins(0,0,0,0)
        self.draft_create=QPushButton('Сохранить в заметки');self.draft_create.clicked.connect(self._commit_draft);actions.addWidget(self.draft_create,1)
        self.draft_discard=QPushButton('Убрать');self.draft_discard.clicked.connect(self._clear_draft);actions.addWidget(self.draft_discard);self.draft_actions.hide()
        self.draft_reminder.toggled.connect(self.draft_time.setEnabled);self.draft_panel.hide();self.draft_area=QScrollArea();self.draft_area.setWidgetResizable(True);self.draft_area.setWidget(self.draft_panel);self.draft_area.setMinimumHeight(190);self.draft_area.setMaximumHeight(340);self.draft_area.hide();layout.addWidget(self.draft_area);layout.addWidget(self.draft_actions)
        self.sources_area=QScrollArea();self.sources_area.setWidgetResizable(True);self.sources_area.setMaximumHeight(140)
        self.sources_content=QWidget();self.sources_layout=QVBoxLayout(self.sources_content);self.sources_layout.setContentsMargins(0,0,2,0);self.sources_layout.setSpacing(5)
        self.sources_area.setWidget(self.sources_content);self.sources_area.hide();layout.addWidget(self.sources_area)
        self.quick_actions=QWidget();prompts=QHBoxLayout(self.quick_actions)
        for label,text,mode in [('Создать заметку','Создай заметку: позвонить завтра в 10:00.','discuss'),('Найти','Найди, что я планировал сделать.','memory'),('Обсудить','Помоги разобраться в ситуации. Сначала уточни, чего я хочу добиться.','discuss'),('Проверить','Проверь план по моим заметкам: что упущено и какие есть противоречия?','audit')]:
            button=QPushButton(label);button.clicked.connect(lambda checked=False,q=text,m=mode:self._suggest(q,m));prompts.addWidget(button)
        layout.addWidget(self.quick_actions);self.quick_actions.hide()
        self.input=QPlainTextEdit();self.input.setPlaceholderText(shortcut_label('Например: «Создай заметку: позвонить завтра в 10:00»\nCtrl+Enter — отправить, Enter — новая строка'));self.input.setMaximumHeight(75);self.input.setAccessibleName('Ваш вопрос')
        voice_row=QHBoxLayout();voice_row.addWidget(self.input,1)
        self.microphone_button=QPushButton('🎙');self.microphone_button.setAccessibleName('Голосовой ввод');self.microphone_button.setFixedWidth(48)
        self.microphone_button.setToolTip('Надиктовать текст. Запись остаётся на компьютере. Нажмите ещё раз, чтобы распознать; «Отменить» — чтобы удалить запись.')
        self.microphone_button.clicked.connect(self._toggle_voice);voice_row.addWidget(self.microphone_button);layout.addLayout(voice_row)
        from app.services.stt_installer import STTManager
        self.stt_manager=STTManager(stt_dir())
        executable,voice_model=self.stt_manager.component_paths()
        self.voice_input=VoiceInputController(parent=self,model_path=voice_model,executable_path=executable)
        self.voice_input.text_ready.connect(self._voice_text_ready)
        self.voice_input.state_changed.connect(self._voice_state_changed)
        self.voice_input.error.connect(self._voice_error)
        self.voice_input.finished.connect(self._voice_finished)
        self.send_shortcut=QShortcut(QKeySequence(shortcut('Ctrl+Return')),self);self.send_shortcut.activated.connect(self.send)
        self.send_shortcut_enter=QShortcut(QKeySequence(shortcut('Ctrl+Enter')),self);self.send_shortcut_enter.activated.connect(self.send)
        self.typing_indicator=QLabel('');self.typing_indicator.setTextFormat(Qt.PlainText)
        self.typing_indicator.setAccessibleName('Состояние ответа помощника')
        self.typing_indicator.hide()
        self._typing_frame=0
        self._typing_timer=QTimer(self);self._typing_timer.setInterval(320)
        self._typing_timer.timeout.connect(self._animate_typing)
        layout.addWidget(self.typing_indicator)
        footer=QHBoxLayout();self.status=QLabel('');self.status.setTextFormat(Qt.PlainText);self.status.setWordWrap(True);footer.addWidget(self.status,1)
        self.cancel_button=QPushButton('Отменить');self.cancel_button.setEnabled(False);self.cancel_button.clicked.connect(self.cancel);footer.addWidget(self.cancel_button)
        self.send_button=QPushButton('Отправить');self.send_button.setObjectName('companionSend');self.send_button.clicked.connect(self.send);footer.addWidget(self.send_button);layout.addLayout(footer)
        self.workspace.currentIndexChanged.connect(self._scope_changed)
        self._welcome()

    def _toggle_voice(self):
        if self._voice_active:
            self.voice_input.stop();return
        if self.busy:return
        if getattr(self.voice_input,'adapter',None) is not None and not self.voice_input.adapter.available:
            self.status.setText('Скачать голосовой ввод в папку приложения')
            self.show_settings()
            if self._settings_dialog:self._settings_dialog.tabs.setCurrentWidget(self._settings_dialog.stt_tab)
            return
        self._voice_active=True;self._voice_discard=False;self._voice_scope=self.workspace_id
        self.send_button.setEnabled(False);self.cancel_button.setEnabled(True)
        try:self.voice_input.start()
        except Exception:
            self.status.setText('Не удалось начать запись. Проверьте микрофон и настройки голосового ввода.')
            self._voice_finished()

    def _voice_state_changed(self,state):
        if not self._voice_active or self._voice_discard:return
        recording=state=='recording'
        self.microphone_button.setText('■' if recording else '🎙')
        self.microphone_button.setEnabled(recording)
        self.status.setText('Слушаю…' if recording else 'Распознаю…' if state=='transcribing' else 'Готов')

    def _voice_text_ready(self,text):
        if self._voice_discard or self._closing or self._voice_scope!=self.workspace_id:return
        text=str(text).strip()
        if text:
            previous=self.input.toPlainText()
            self.input.setPlainText(previous+('\n' if previous.strip() else '')+text)
            self.input.setFocus()
            self.status.setText('Запись распознана. Проверьте текст, особенно дату и время, затем нажмите «Отправить».')

    def _voice_error(self,message):
        if not self._voice_discard and not self._closing:self.status.setText(str(message))

    def _cancel_voice(self):
        self._voice_discard=True;self.voice_input.cancel()

    def _voice_finished(self):
        self._voice_active=False;self.microphone_button.setText('🎙');self.microphone_button.setEnabled(True)
        self.send_button.setEnabled(self._thread is None);self.cancel_button.setEnabled(self._thread is not None)
        if self._closing:self.close();self.shutdown_ready.emit();_ACTIVE_DIALOGS.discard(self)

    @property
    def busy(self):return self._voice_active or getattr(self,'_pending_pack_probe',False) or self._thread is not None or getattr(self,'_pending_render',False) or bool(self._onboarding_dialog and self._onboarding_dialog.busy) or self._settings_busy()

    def _settings_busy(self):
        return bool(self._settings_dialog and any(getattr(self._settings_dialog,name,None) and getattr(self._settings_dialog,name).busy for name in ('pack_tab','stt_tab')))

    def showEvent(self,event):
        super().showEvent(event)
        if not self.busy:
            self.context_pins.refresh()
        if self._auto_detect and not self._detected_once:
            self._detected_once=True
            QTimer.singleShot(0,self._detect_first_open)

    def _detect_first_open(self):
        if self._closing:return
        if not self.isVisible():self._detected_once=False;return
        if self.busy:
            QTimer.singleShot(100,self._detect_first_open);return
        self._start(kind='detect')

    def _setup_worker(self,kind,config,path=''):
        return _RequestThread(1,self.engine_factory,self.database_path,path,self.workspace_id,'discuss',[],
                              config,True,False,threading.Event(),kind)

    def _connection_changed(self,response):
        self._connection_status=dict(response)
        if response.get('model_ready'):
            message='Локальный ИИ подключён. Модель: '+self.config['model']
            latency=response.get('latency_ms')
            if isinstance(latency,(int,float)):message+=' · соединение проверено за '+str(round(latency))+' мс'
            if not response.get('embedding_ready'):message+=' · поиск по смыслу ещё не настроен'
            if self.config.get('provider')=='search':message='Локальный ИИ найден. Сейчас выбран поиск без модели; нажмите «Настройки ИИ», чтобы включить разговор.'
        else:message='Локальный ИИ-помощник не подключён. Поиск и создание заметок доступны без модели.'
        self.connection_banner.setToolTip(message)
        self.connection_banner.setText('ИИ готов' if response.get('model_ready') and self.config.get('provider')!='search' else 'Поиск без ИИ')
        self.connection_banner.setStyleSheet('background:#eaf7ee;border-radius:8px;padding:8px;color:#286b42;' if response.get('model_ready') and self.config.get('provider')!='search' else 'background:#fff0dc;border-radius:8px;padding:8px;color:#86520c;')
        if self._settings_dialog:self._settings_dialog.result_label.setText(message)

    def show_onboarding(self):
        if self.busy:return
        if self._onboarding_dialog and self._onboarding_dialog.isVisible():
            self._onboarding_dialog.raise_();return
        from app.ui.ai_onboarding import AIOnboardingWizard
        wizard=AIOnboardingWizard(self.config,self,self._setup_worker,self._connection_status)
        self._onboarding_dialog=wizard
        wizard.configured.connect(self._onboarding_configured)
        wizard.skipped.connect(self._onboarding_skipped)
        wizard.finished.connect(self._setup_closed)
        wizard.show()

    def _setup_closed(self,*args):
        if self._closing and self._thread is None:
            self.close();_ACTIVE_DIALOGS.discard(self);self.shutdown_ready.emit()

    def _onboarding_configured(self,config):
        try:self._save_config(config)
        except (OSError,ValueError) as error:
            self.status.setText('Не удалось сохранить настройки: '+str(error));return
        if self._onboarding_dialog:self._connection_changed(self._onboarding_dialog._status)
        self.status.setText('ИИ подключён. Напишите «Привет» или попросите помочь с заметкой.')
        self.input.setFocus()

    def _onboarding_skipped(self):
        try:self._save_config({**self.config,'provider':'search','onboarding_seen':True})
        except (OSError,ValueError) as error:self.status.setText('Не удалось сохранить выбор: '+str(error));return
        self.status.setText('Поиск доступен без модели. Подключить ИИ можно кнопкой «Настроить».')

    @property
    def workspace_id(self):return self.workspace.currentData()

    def set_workspace(self,workspace_id):
        index=self.workspace.findData(workspace_id)
        if index>=0:self.workspace.setCurrentIndex(index)

    def _load_config(self):
        try:
            config=json.loads(self.settings_path.read_text(encoding='utf-8'))
            if not isinstance(config,dict):return dict(DEFAULT_CONFIG)
            validated=validate_config({key:config[key] for key in DEFAULT_CONFIG if key in config})
            # Upgrade the former bundled default only when its replacement is installed.
            if validated['provider']=='ollama' and validated['base_url']==DEFAULT_CONFIG['base_url'] and validated['model']=='qwen3:1.7b':
                import sys
                root=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]
                candidates=[root/'runtime/models',root.parents[1]/'work/ai_models']
                if any((path/'manifests/registry.ollama.ai/library/qwen3/4b').is_file() for path in candidates):
                    validated['model']=DEFAULT_CONFIG['model']
            for key in ('runtime_root','ai_pack_path'):
                if validated[key]:validated[key]=str(resolve_app_path(validated[key]))
            return validated
        except (OSError,ValueError):return dict(DEFAULT_CONFIG)

    def _save_config(self,config):
        config=validate_config(config)
        temporary=None
        persisted=dict(config)
        for key in ('runtime_root','ai_pack_path'):
            if persisted[key]:persisted[key]=portable_app_path(persisted[key])
        try:
            check_app_writable(self.settings_path.parent)
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',prefix='.ai-settings-',suffix='.json',dir=self.settings_path.parent,delete=False) as handle:
                temporary=Path(handle.name);json.dump(persisted,handle,ensure_ascii=False,indent=2)
            os.replace(temporary,self.settings_path);temporary=None
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
        self.config=config
        self._sync_metrics_config()

    def _sync_metrics_config(self):
        if self.metrics_collector is not None:
            self.metrics_collector.set_enabled(self.config.get("provider")=="ollama")
            self.metrics_collector.set_model(self.config.get("model"))

    def _welcome(self):
        self.chat.setPlainText('Помощник\nПривет! Я ваш маленький помощник. Можно просто поздороваться, спросить, как работает приложение, или попросить создать заметку.\n\n'
                               'Попробуйте: «Создай заметку: посмотреть почту завтра в 09:00». Я покажу черновик: вы проверите текст и время, затем нажмёте «Сохранить в заметки».\n\n'
                               'Чтобы обсудить конкретные записи, нажмите «Прикрепить заметки». Ваши заметки остаются на компьютере.')
        self.scope_hint.setText('Использую только пространство «'+self.workspace.currentText()+'». Личные и рабочие заметки не смешиваются.')
        self.status.setText('Готов')
        if self._connection_status:self.status.setText(self.connection_banner.text())

    def _mode_changed(self,*args):
        descriptions={'memory':'Найду записи и покажу источники. Чем конкретнее вопрос, тем легче вспомнить нужное.',
                      'discuss':'Обсудим вашу ситуацию. Можно уточнять ответ и продолжать разговор.',
                      'audit':'Проверю найденные заметки на пробелы и противоречия. Это не проверка всей базы.',
                      'draft':'Подготовлю текст по вашим заметкам. Черновик можно проверить и скопировать.'}
        self.mode_hint.setText(descriptions.get(self.mode.currentData(),''))
        self.mode.setToolTip(self.mode_hint.text())

    def _toggle_context_panel(self,checked):
        self.context_pins.setVisible(checked or bool(self.context_pins.note_ids))
        if checked:self.context_pins.search.setFocus()

    def _context_pins_changed(self):
        count=len(self.context_pins.note_ids)
        self.attach_notes_button.setText('Заметки · '+str(count) if count else 'Прикрепить заметки')
        self.context_pins.setVisible(self.attach_notes_button.isChecked() or bool(count))

    def new_conversation(self):
        if self._voice_active:self._cancel_voice()
        if self.busy and not self._voice_active:return False
        self.context_pins.clear();self.context_pins.refresh()
        self._history=[];self._pending_query='';self.input.clear();self._clear_sources();self._clear_draft();self._welcome();self.input.setFocus();return True

    def _clear_draft(self):
        self._action_draft=None;self.draft_panel.hide();self.draft_area.hide();self.draft_actions.hide();self._clear_help_actions()

    def _show_draft(self,draft):
        if not isinstance(draft,dict) or draft.get('workspace_id')!=self.workspace_id:return
        self.draft_heading.setText('Черновик напоминания · создаётся только после подтверждения' if draft.get('once_at') else 'Черновик заметки · создаётся только после подтверждения')
        self.draft_create.setText('Сохранить в заметки')
        from app.services.assistant_actions import recurrence_label
        rule = draft.get('recurrence') or draft.get('rrule')
        self.draft_recurrence.setText(recurrence_label(rule))
        self.draft_recurrence.setToolTip('Часовой пояс: ' + str(draft.get('timezone_id') or 'устройства'))
        self._action_draft=dict(draft);self.draft_title.setText(str(draft.get('title') or ''))
        self.draft_body.setPlainText(str(draft.get('body') or ''))
        when=QDateTime.fromString(str(draft.get('once_at') or ''),Qt.ISODate)
        self.draft_reminder.setChecked(when.isValid());self.draft_time.setDateTime(when if when.isValid() else QDateTime.currentDateTime().addSecs(3600))
        self.draft_time.setEnabled(when.isValid());self.draft_panel.show();self.draft_area.show();self.draft_actions.show()
        self.context_pins.hide();self.attach_notes_button.setChecked(False)
        self.advanced_panel.hide();self.search_options_button.setChecked(False)
        self.draft_area.verticalScrollBar().setValue(0)

    def _commit_draft(self):
        if self.busy or not self._action_draft:return False
        if self._action_draft.get('workspace_id')!=self.workspace_id:
            self._clear_draft();self.status.setText('Пространство изменилось. Подготовьте новый черновик.');return False
        from app.services.assistant_actions import commit_action_draft
        draft={**self._action_draft,'title':self.draft_title.text(),'body':self.draft_body.toPlainText(),
               'once_at':self.draft_time.dateTime().toString(Qt.ISODate) if self.draft_reminder.isChecked() else None,
               'recurrence':self._action_draft.get('recurrence') if self.draft_reminder.isChecked() else None}
        try:note_id=commit_action_draft(self.store,draft)
        except (ValueError,sqlite3.Error,OSError) as error:self.status.setText('Не удалось создать заметку: '+str(error));return False
        self._clear_draft();self.status.setText('Сохранено');self.chat.appendPlainText('\nПомощник\nЗаметка сохранена. Нажмите её в списке, чтобы продолжить редактирование.')
        self._history.append({'role':'assistant','content':'Заметка сохранена.','workspace_id':self.workspace_id,'sources':[]})
        self._history=self._history[-20:]
        self.note_created.emit(note_id);return True

    def _suggest(self,text,mode):
        index=self.mode.findData(mode)
        if index>=0:self.mode.setCurrentIndex(index)
        self.input.setPlainText(text);self.input.setFocus()

    def _clear_sources(self):
        while self.sources_layout.count():
            item=self.sources_layout.takeAt(0);widget=item.widget()
            if widget:widget.setParent(None);widget.deleteLater()
        self.source_buttons=[];self.sources_area.hide()

    def _context_policy_changed(self,*args):
        self.context_pins.set_scope(self.workspace_id,self.include_archive.isChecked(),self.include_trash.isChecked())

    def _scope_changed(self,*args):
        if self._voice_active:self._cancel_voice()
        self._context_policy_changed()
        self._discard_stream_preview()
        self._discard_stream_preview()
        self._set_typing(False)
        if self.metrics_collector is not None and self._metrics_identity is not None:
            self.metrics_collector.abort(self._metrics_identity);self._metrics_identity=None
        self._request_id+=1
        if self._cancel_event:self._cancel_event.set()
        self._history=[];self._pending_query='';self.input.clear();self._clear_sources();self._clear_draft();self._welcome()
        if self.busy:self.status.setText('Пространство изменено. Завершаю предыдущий запрос без переноса его ответа.')

    def send(self):
        query=self.input.toPlainText().strip()
        if self.busy:return False
        if not query:
            self.status.setText('Сначала напишите вопрос.');self.input.setFocus();return False
        self._pending_query=query
        self.chat.appendPlainText('\nВы\n'+query)
        self.input.clear();self._clear_sources();self._clear_draft()
        self._start(query=query,kind='ask')
        return True

    @staticmethod
    def _automatic_mode(query):
        """Choose a retrieval task without asking users for a technical mode."""
        text=str(query).casefold()
        if re.search(r'\b(проверь|проверить|противоречия|упущено)\b',text):return 'audit'
        if re.search(r'\b(найди|найти|поищи|поиск|отыщи)\b',text):return 'memory'
        return 'discuss'

    def _start(self,query='',kind='ask',config=None):
        if self.busy:return False
        self._request_id+=1;identity=self._request_id
        event=threading.Event();self._cancel_event=event
        self._pending_config=dict(config or self.config)
        request_config=dict(config or self.config)
        if kind=='ask': request_config['fuzzy_identifiers']=self.fuzzy_identifiers.isChecked()
        selected_mode=self._automatic_mode(query) if kind=='ask' else self.mode.currentData()
        self.mode.setCurrentIndex(self.mode.findData(selected_mode))
        thread=_RequestThread(identity,self.engine_factory,self.database_path,query,self.workspace_id,selected_mode,
                              [dict(message) for message in self._history],request_config,self.include_archive.isChecked(),self.include_trash.isChecked(),event,kind,attached_note_ids=self.context_pins.note_ids)
        self._thread=thread;_ACTIVE_DIALOGS.add(self)
        thread.advisory.connect(self._request_advisory, Qt.QueuedConnection)
        thread.completed.connect(self._request_completed, Qt.QueuedConnection)
        thread.progress.connect(self._request_progress, Qt.QueuedConnection)
        thread.finished.connect(self._request_finished, Qt.QueuedConnection)
        self.context_pins.setEnabled(False)
        self.include_archive.setEnabled(False);self.include_trash.setEnabled(False)
        self.send_button.setEnabled(False);self.cancel_button.setEnabled(True);self.settings_button.setEnabled(False);self.new_chat_button.setEnabled(False)
        self.status.setText('Проверяю подключение…' if kind=='status' else 'Генерирует…')
        if kind=='ask':self._set_typing(True)
        self._deadline_timer.start(30000)
        thread.start();return True

    def _set_typing(self, active):
        self.typing_indicator.setVisible(active)
        if active:
            self._typing_frame=0;self._animate_typing();self._typing_timer.start()
        else:
            self._typing_timer.stop();self.typing_indicator.clear()

    def _animate_typing(self):
        self._typing_frame=(self._typing_frame+1)%4
        self.typing_indicator.setText('Помощник готовит ответ'+'.'*self._typing_frame)

    def _follow_verified_scroll(self, follow, previous):
        bar=self.chat.verticalScrollBar()
        if not follow:
            self._scroll_animation.stop();bar.setValue(min(previous,bar.maximum()));return
        self._scroll_animation.stop()
        self._scroll_animation.setStartValue(bar.value())
        self._scroll_animation.setEndValue(bar.maximum())
        self._scroll_animation.start()

    def _discard_stream_preview(self):
        if self._stream_baseline is not None and self._stream_scope==self.workspace_id:
            previous=self.chat.verticalScrollBar().value()
            self.chat.setPlainText(self._stream_baseline)
            self._follow_verified_scroll(False,previous)
        self._stream_baseline=None;self._stream_scope=None

    @Slot(int, object)
    def _request_progress(self,identity,packet):
        if identity!=self._request_id or self._closing or not isinstance(packet,dict):return
        collector=self.metrics_collector
        key=(id(self),identity)
        if collector is not None and packet.get('kind')=='metrics_start':
            self._metrics_identity=key
            collector.begin(key,self._pending_config.get('model'),at=packet.get('at'))
            return
        if collector is not None and packet.get('kind')=='first_token':
            collector.first_token(key,at=packet.get('at'))
            return
        if collector is not None and packet.get('kind') in ('segment','grounding'):
            collector.grounding(key)
            if packet.get('kind')=='grounding':return
        if packet.get('kind')=='reset':
            self._discard_stream_preview();return
        if packet.get('kind')!='segment':return
        if not self._sources_current(packet):
            self._discard_stream_preview()
            self.cancel();self.status.setText('Источник изменился. Ответ не показан; повторите вопрос.');return
        text=packet.get('text')
        if not isinstance(text,str) or not text:return
        bar=self.chat.verticalScrollBar();previous=bar.value()
        follow=self._scroll_animation.state()==self._scroll_animation.State.Running or bar.maximum()-previous<=24
        if self._stream_baseline is None:
            self._stream_baseline=self.chat.toPlainText();self._stream_scope=self.workspace_id
            self.chat.appendPlainText('\nПомощник\n')
        cursor=self.chat.textCursor();cursor.movePosition(cursor.MoveOperation.End);cursor.insertText(text)
        self._follow_verified_scroll(follow,previous)
        self.status.setText('Проверяет источники…')

    @Slot(int, str)
    def _request_advisory(self, identity, message):
        if identity == self._request_id and not self._closing:
            self.status.setText(message)

    @Slot(int, object, object)
    def _request_completed(self, identity, response, error):
        thread = self.sender()
        if self.metrics_collector is not None and self._metrics_identity is not None:
            success=(identity==self._request_id and not self._closing and not error
                     and isinstance(response,dict) and response.get('status')=='answered'
                     and not self._cancel_event.is_set())
            self.metrics_collector.complete(self._metrics_identity,success=success,
                                           at=response.get('_metrics_finished') if isinstance(response,dict) else None)
            self._metrics_identity=None
        self._completed(identity, response, error, thread.kind)

    @Slot()
    def _request_finished(self):
        self._finished(self.sender())

    def _deadline_expired(self):
        if not self.busy:return
        self._discard_stream_preview()
        self._set_typing(False)
        if self.metrics_collector is not None and self._metrics_identity is not None:
            self.metrics_collector.abort(self._metrics_identity);self._metrics_identity=None
        self._request_id+=1
        if self._cancel_event:self._cancel_event.set()
        message='Ответ не получен за 30 секунд. Можно использовать поиск в заметках или закрыть тяжёлые программы и попробовать снова.'
        self.chat.appendPlainText('\nПомощник\n'+message)
        self.status.setText(message)
        self.cancel_button.setEnabled(False)

    def _completed(self,identity,response,error,kind):
        self._discard_stream_preview()
        # The worker returns a fully grounded packet, never raw model tokens.
        # Queue immediately; presentation animation begins only after validation.
        self._pending_render = True
        QTimer.singleShot(0, lambda:self._begin_verified_render(identity,response,error,kind))

    def _sources_current(self,response):
        memory=None
        try:
            sources=response.get('sources') or []
            if sources:memory=MemoryStore(self.database_path)
            for source in sources:
                if (not isinstance(source,dict) or type(source.get('note_id')) is not int
                        or type(source.get('revision')) is not int
                        or source.get('workspace_id',self.workspace_id)!=self.workspace_id
                        or memory.resolve(source['note_id'],source['revision'],workspace_id=self.workspace_id,
                            include_archive=self.include_archive.isChecked(),include_trash=self.include_trash.isChecked()) is None):
                    return False
            return True
        except (sqlite3.Error,ValueError,OSError):
            return False
        finally:
            if memory:memory.close()

    def _begin_verified_render(self,identity,response,error,kind):
        # Search excerpts are source material, never assistant-generated speech.
        if isinstance(response,dict) and response.get('status')=='search_only':
            try:self._render_completed(identity,response,error,kind)
            finally:self._pending_render=False
            return
        if (identity!=self._request_id or self._closing or error or kind!='ask'
                or not self._sources_current(response)):
            try:self._render_completed(identity,response,error,kind)
            finally:self._pending_render=False
            return
        text=str(response.get('text') or '')
        baseline=self.chat.toPlainText()
        scope=self.workspace_id
        position=0
        self._set_typing(True)
        self.status.setText('Показываю проверенный ответ…')
        bar=self.chat.verticalScrollBar();previous=bar.value();follow=bar.maximum()-previous<=24
        self.chat.appendPlainText('\nПомощник\n')
        self._follow_verified_scroll(follow,previous)
        # Bound duration even for a large response; ordinary replies use small
        # readable portions. This animates verified text, not transport tokens.
        step=max(12,(len(text)+39)//40)
        def tick():
            nonlocal position
            valid=(identity==self._request_id and not self._closing
                   and scope==self.workspace_id and self._sources_current(response))
            if not valid:
                if scope==self.workspace_id:
                    previous=self.chat.verticalScrollBar().value()
                    self.chat.setPlainText(baseline);self._follow_verified_scroll(False,previous)
                self._set_typing(False)
                self._pending_render=False
                if identity==self._request_id:self.status.setText('Источник изменился или больше не доступен. Задайте вопрос ещё раз.')
                return
            if position<len(text):
                bar=self.chat.verticalScrollBar();previous=bar.value()
                follow=self._scroll_animation.state()==self._scroll_animation.State.Running or bar.maximum()-previous<=24
                cursor=self.chat.textCursor();cursor.movePosition(cursor.MoveOperation.End)
                cursor.insertText(text[position:position+step])
                self._follow_verified_scroll(follow,previous)
                position+=step
                QTimer.singleShot(16,tick)
                return
            # Publish history, sources and action drafts only after completion.
            bar=self.chat.verticalScrollBar();previous=bar.value()
            follow=self._scroll_animation.state()==self._scroll_animation.State.Running or bar.maximum()-previous<=24
            self.chat.setPlainText(baseline)
            self._set_typing(False)
            try:
                self._render_completed(identity,response,error,kind)
                self._follow_verified_scroll(follow,previous)
            finally:self._pending_render=False
        tick()

    def _render_completed(self,identity,response,error,kind):
        self._set_typing(False)
        if identity!=self._request_id or self._closing:return
        if error:
            self.status.setText('Не удалось получить ответ. Попробуйте ещё раз или выберите поиск в заметках.')
            self.chat.appendPlainText('\nПомощник\nЗапрос не выполнен. Ответ и источники не были сохранены в контексте разговора.')
            if self._settings_dialog:self._settings_dialog.result_label.setText(self.status.text())
            return
        if kind=='detect':
            if response.get('_bundled_config') and not self.settings_path.exists():
                self._save_config({**self.config,**response['_bundled_config']})
            self._connection_changed(response)
            self.status.setText(self.connection_banner.text())
            self._open_onboarding_pending=not response.get('model_ready') and not self.config.get('onboarding_seen')
            return
        if kind=='status':
            if response.get('message') or response.get('text'):
                message=str(response.get('message') or response.get('text'))
            else:
                message=self._setup_message(response,self._pending_config)
            self.status.setText(message)
            if self._settings_dialog:self._settings_dialog.result_label.setText(message)
            return
        if any(isinstance(source,dict) and source.get('workspace_id',self.workspace_id)!=self.workspace_id for source in response.get('sources') or []):
            self.status.setText('Ответ отклонён: источник относится к другому пространству.')
            return
        # Recheck the current database before publishing the final response. A source
        # may have moved, changed, or been deleted since worker validation.
        memory=None
        try:
            response_sources=response.get('sources') or []
            if response_sources:
                memory=MemoryStore(self.database_path)
                for source in response_sources:
                    if (not isinstance(source,dict) or type(source.get('note_id')) is not int
                            or type(source.get('revision')) is not int
                            or memory.resolve(source['note_id'],source['revision'],workspace_id=self.workspace_id,
                                include_archive=self.include_archive.isChecked(),include_trash=self.include_trash.isChecked()) is None):
                        self.status.setText('Источник изменился или больше не доступен. Задайте вопрос ещё раз.')
                        return
        except (sqlite3.Error,ValueError,OSError):
            self.status.setText('Не удалось проверить источники. Ответ не показан; повторите вопрос.')
            return
        finally:
            if memory:memory.close()
        text=str(response.get('text') or '')
        search_only=response.get('status')=='search_only'
        if search_only:
            notice=('ИИ не сформировал подтверждённый ответ. Ниже — исходные фрагменты заметок, а не ответ помощника.'
                    if response.get('sources') else 'В ваших заметках нет информации об этом')
            self.chat.appendPlainText('\nРезультаты поиска · без генерации\n'+notice)
        else:
            self.chat.appendPlainText('\nПомощник\n'+text)
        sources=[{'note_id':source['note_id'],'revision':source.get('revision')} for source in response.get('sources') or [] if isinstance(source,dict) and 'note_id' in source]
        # Do not promote untrusted note excerpts into assistant-role history.
        if not search_only:
            self._history.extend([{'role':'user','content':self._pending_query,'workspace_id':self.workspace_id},
                                  {'role':'assistant','content':text,'workspace_id':self.workspace_id,'sources':sources}])
        self._history=self._history[-20:]
        self.status.setText('Поиск без генерации' if search_only else 'Готов')
        if not search_only and response.get('action_draft'):self._show_draft(response['action_draft'])
        self._show_help_actions(response.get('help_actions') if not search_only else [])
        for source in response.get('sources') or []:
            if not isinstance(source,dict) or 'note_id' not in source:continue
            title=str(source.get('title') or '').strip() or 'Без названия'
            if search_only:
                card=QWidget();card.setObjectName('searchSourceCard')
                card.setStyleSheet('QWidget#searchSourceCard {background:#f5f3ee;border:1px solid #e3ddd3;border-radius:8px;}')
                card_layout=QVBoxLayout(card);card_layout.setContentsMargins(9,7,9,7)
                label=QLabel('Источник: '+title[:100]+' · исходный текст заметки')
                label.setTextFormat(Qt.PlainText);label.setWordWrap(True);card_layout.addWidget(label)
                excerpt=QLabel(str(source.get('excerpt') or '')[:1600])
                excerpt.setObjectName('searchSourceExcerpt');excerpt.setTextFormat(Qt.PlainText)
                excerpt.setWordWrap(True);excerpt.setTextInteractionFlags(Qt.TextSelectableByMouse)
                card_layout.addWidget(excerpt);self.sources_layout.addWidget(card)
            button=QPushButton('Открыть: '+title[:100]);button.setToolTip(str(source.get('excerpt') or '')[:500])
            button.clicked.connect(lambda checked=False,s=dict(source),scope=self.workspace_id:self._open_source(s,scope))
            (card_layout if search_only else self.sources_layout).addWidget(button);self.source_buttons.append(button)
        self.sources_area.setVisible(bool(self.source_buttons))

    def _clear_help_actions(self):
        while self.help_actions_layout.count():
            item=self.help_actions_layout.takeAt(0)
            if item.widget():item.widget().deleteLater()
        self.help_action_buttons=[];self.help_actions_panel.hide()

    def _show_help_actions(self,actions):
        self._clear_help_actions()
        labels={'synonyms':'Настроить синонимы','ai_settings':'Настройки ИИ','attach_notes':'Прикрепить заметки','quick_actions':'Быстрые действия'}
        if not isinstance(actions,list):return
        seen=set()
        for item in actions[:8]:
            action=item.get('action') if isinstance(item,dict) else None
            if action not in labels or action in seen:continue
            seen.add(action)
            button=QPushButton(labels[action]);button.setIcon(application_icon());button.setProperty('helpAction',action)
            button.clicked.connect(lambda checked=False,a=action:self._activate_help_action(a))
            self.help_actions_layout.addWidget(button);self.help_action_buttons.append(button)
        self.help_actions_panel.setVisible(bool(self.help_action_buttons))

    def _activate_help_action(self,action):
        if self.busy:return False
        if action=='synonyms':self.show_synonyms()
        elif action=='ai_settings':self.show_settings()
        elif action=='attach_notes':
            self.attach_notes_button.setChecked(True)
            self.context_pins.search.setFocus();self.context_pins.search.selectAll()
        elif action=='quick_actions':
            callback=getattr(self.parent(),'show_command_palette',None)
            if callable(callback):callback()
            else:self.status.setText('Быстрые действия открываются клавишами Ctrl+K в главном окне.')
        else:return False
        return True

    def _open_source(self,source,workspace_id):
        if workspace_id!=self.workspace_id:return
        memory=None
        try:
            memory=MemoryStore(self.database_path)
            resolved=memory.resolve(source['note_id'],source.get('revision'),workspace_id=workspace_id,
                                    include_archive=self.include_archive.isChecked(),include_trash=self.include_trash.isChecked())
            if not resolved:
                self.status.setText('Источник изменился или больше не доступен. Задайте вопрос ещё раз.');return
            self.open_note.emit(int(source['note_id']))
        except (sqlite3.Error,ValueError,OSError):
            self.status.setText('Не удалось открыть источник. Проверьте доступ к базе заметок.')
        finally:
            if memory:memory.close()

    def cancel(self):
        if self._voice_active:
            self._cancel_voice();self.status.setText('Запись отменена');return
        if self._settings_busy():
            for name in ('pack_tab','stt_tab'):
                tab=getattr(self._settings_dialog,name,None)
                if tab and tab.busy:tab.cancel()
            return
        if self._onboarding_dialog and self._onboarding_dialog.busy:
            self._onboarding_dialog.cancel();return
        if not self.busy:return
        self._discard_stream_preview()
        self._set_typing(False)
        self._scroll_animation.stop()
        if self.metrics_collector is not None and self._metrics_identity is not None:
            self.metrics_collector.abort(self._metrics_identity);self._metrics_identity=None
        self._request_id+=1
        if self._cancel_event:self._cancel_event.set()
        self.status.setText('Запрос отменён. Завершаю текущую операцию…');self.cancel_button.setEnabled(False)

    def _finished(self,thread):
        if getattr(self,'_pending_render',False):
            QTimer.singleShot(10,lambda:self._finished(thread))
            return
        self._deadline_timer.stop()
        if self._thread is thread:self._thread=None;self._cancel_event=None
        thread.deleteLater()
        self.context_pins.setEnabled(True)
        self.include_archive.setEnabled(True);self.include_trash.setEnabled(True)
        self.send_button.setEnabled(True);self.cancel_button.setEnabled(False);self.settings_button.setEnabled(True);self.new_chat_button.setEnabled(True)
        if self._closing:
            self.close();self.shutdown_ready.emit()
        _ACTIVE_DIALOGS.discard(self)
        if self._open_onboarding_pending and not self._closing:
            self._open_onboarding_pending=False
            QTimer.singleShot(0,self.show_onboarding)

    def request_close(self):self.close()

    def closeEvent(self,event):
        if self._voice_active:self._cancel_voice()
        self._pending_pack_probe=False
        if self.metrics_collector is not None and self._metrics_identity is not None:
            self.metrics_collector.abort(self._metrics_identity);self._metrics_identity=None
        self._discard_stream_preview()
        self._set_typing(False)
        self._scroll_animation.stop()
        if self._settings_busy():
            self._closing=True;_ACTIVE_DIALOGS.add(self);self._settings_dialog.close()
            self.hide();event.ignore();return
        if self._onboarding_dialog and self._onboarding_dialog.busy:
            self._closing=True;_ACTIVE_DIALOGS.add(self)
            self._onboarding_dialog.close()
            # A setup worker owns its event loop until cancellation completes.
            self.hide();event.ignore();return
        if self._onboarding_dialog and self._onboarding_dialog.isVisible():self._onboarding_dialog.close()
        if self.busy:
            self._closing=True;self._request_id+=1
            if self._cancel_event:self._cancel_event.set()
            if self._settings_dialog:self._settings_dialog.close()
            self.hide();event.ignore();_ACTIVE_DIALOGS.add(self)
        else:
            self._closing=False;event.accept();_ACTIVE_DIALOGS.discard(self)

    def show_synonyms(self):
        if self.busy:return
        from app.ui.synonyms import SynonymDictionaryDialog
        SynonymDictionaryDialog(self.database_path, self.workspace_id, self.workspace.currentText(), self).exec()

    def show_settings(self):
        if self.busy:return
        if self._settings_dialog and self._settings_dialog.isVisible():self._settings_dialog.raise_();return
        if self._settings_dialog:self._settings_dialog.deleteLater();self._settings_dialog=None
        from app.widgets.ai_pack_settings import AIPackSettingsDialog,AIPackSettingsTab
        from app.services.ai_pack_manager import AIPackManager
        dialog=AIPackSettingsDialog(self);dialog.setWindowTitle('Локальный помощник');dialog.setMinimumWidth(460)
        shell=QVBoxLayout(dialog);tabs=QTabWidget();dialog.tabs=tabs;shell.addWidget(tabs)
        connection=QWidget();outer=QVBoxLayout(connection);tabs.addTab(connection,'Подключение')
        pack_tab=AIPackSettingsTab(AIPackManager(ai_dir(),self.config),dialog)
        dialog.pack_tab=pack_tab;tabs.addTab(pack_tab,'Модели приложения')
        from app.ui.stt_settings import STTSettingsTab
        stt_tab=STTSettingsTab(self.stt_manager,dialog);dialog.stt_tab=stt_tab;tabs.addTab(stt_tab,'Голосовой ввод')
        def refresh_voice():
            from app.services.local_stt import WhisperCppAdapter
            executable,voice_model=self.stt_manager.component_paths()
            self.voice_input.adapter=WhisperCppAdapter(executable,voice_model)
        stt_tab.configured.connect(refresh_voice)
        stt_tab.idle.connect(dialog.finish_pending_close)
        pack_tab.idle.connect(dialog.finish_pending_close)
        def pack_idle():
            if self._closing and not self.busy:self.close();self.shutdown_ready.emit()
        pack_tab.idle.connect(pack_idle)
        stt_tab.idle.connect(pack_idle)
        def configured(config):
            try:self._save_config(config)
            except (OSError,ValueError) as error:pack_tab.status.setText(str(error));return
            self._history=[];self._pending_query='';self._clear_sources();self._clear_draft();self._welcome()
            model.setText(self.config['model']);address.setText(self.config['base_url'])
            provider.setCurrentIndex(max(0,provider.findData(self.config['provider'])))
        pack_tab.configured.connect(configured)
        heading=QLabel('Настройка локального ИИ');heading.setStyleSheet('font-size:18px;font-weight:600;');outer.addWidget(heading)
        explanation=QLabel('Поиск по заметкам работает сразу, даже без ИИ-комплекта. Для разговора и поиска по смыслу нужен отдельный готовый комплект моделей. '
                           'Подключите папку с распакованным комплектом или скачайте модели через мастер настройки. '
                           'Ваши заметки не отправляются в облако.');explanation.setTextFormat(Qt.PlainText);explanation.setWordWrap(True);outer.addWidget(explanation)
        setup=QPushButton('Открыть мастер настройки');setup.clicked.connect(lambda:(dialog.close(),self.show_onboarding()));outer.addWidget(setup)
        form=QFormLayout();provider=QComboBox();provider.addItem('Локальный ИИ','ollama');provider.addItem('Поиск в заметках','search');provider.setCurrentIndex(max(0,provider.findData(self.config['provider'])))
        model=QLineEdit(self.config['model']);address=QLineEdit(self.config['base_url'])
        form.addRow('Как отвечать',provider);outer.addLayout(form)
        advanced_toggle=QCheckBox('Дополнительные настройки модели');outer.addWidget(advanced_toggle)
        advanced=QWidget();advanced_form=QFormLayout(advanced);advanced_form.setContentsMargins(0,0,0,0)
        advanced_form.addRow('Модель на компьютере',model);advanced_form.addRow('Локальный адрес',address)
        advanced.hide();advanced_toggle.toggled.connect(advanced.setVisible);outer.addWidget(advanced)
        dialog.result_label=QLabel('Комплект ещё не проверен. Модель разговора: '+self.config['model']+'. Модель памяти: '+self.config['embedding_model']+'.');dialog.result_label.setTextFormat(Qt.PlainText);dialog.result_label.setWordWrap(True);outer.addWidget(dialog.result_label)
        if self._connection_status:dialog.result_label.setText(self.connection_banner.toolTip())
        buttons=QHBoxLayout();probe=QPushButton('Проверить ИИ-комплект');save=QPushButton('Сохранить');close=QPushButton('Закрыть');buttons.addWidget(probe);buttons.addStretch();buttons.addWidget(save);buttons.addWidget(close);outer.addLayout(buttons)
        def fields():
            values={**self.config,'provider':provider.currentData(),'model':model.text(),'base_url':address.text()}
            if model.text().strip()!=self.config['model'] or address.text().strip().rstrip('/')!=self.config['base_url']:
                values['managed_pack']=False;values['ai_pack_path']='';values['runtime_root']=''
            return validate_config(values)
        def apply():
            if pack_tab.busy:return
            try:self._save_config(fields())
            except (OSError,ValueError) as error:dialog.result_label.setText(str(error));return
            self.status.setText('Настройки сохранены.');dialog.close()
        def check():
            if stt_tab.busy:
                if not getattr(self,'_pending_pack_probe',False):
                    self._pending_pack_probe=True
                    def resume_check():
                        self._pending_pack_probe=False
                        check()
                    stt_tab.idle.connect(resume_check,Qt.SingleShotConnection)
                return
            if pack_tab.busy:
                if pack_tab._worker.operation=='scan' and not getattr(self,'_pending_pack_probe',False):
                    self._pending_pack_probe=True
                    pack_tab.idle.connect(check,Qt.SingleShotConnection)
                return
            self._pending_pack_probe=False
            if self._closing:return
            try:config=fields()
            except ValueError as error:dialog.result_label.setText(str(error));return
            if self._start(kind='status',config=config):dialog.result_label.setText('Проверяю модель…')
        probe.clicked.connect(check);save.clicked.connect(apply);close.clicked.connect(dialog.close)
        self._settings_dialog=dialog;dialog.show()

    @staticmethod
    def _setup_message(response,config):
        if not response.get('available'):
            return ('Локальный ИИ пока недоступен. Добавьте готовый ИИ-комплект в папку приложения и проверьте ещё раз. '
                    'Поиск по заметкам продолжает работать без модели.')
        models=set(response.get('models') or [])
        missing=[]
        if not response.get('model_ready'):missing.append('разговор — '+config['model'])
        if config['embedding_model'] not in models:missing.append('поиск по смыслу — '+config['embedding_model'])
        if missing:
            prefix='Разговор готов. ' if response.get('model_ready') else 'Локальный сервис работает. '
            return prefix+'Не найдены модели: '+'; '.join(missing)+'. Добавьте полный ИИ-комплект и повторите проверку.'
        return 'ИИ-комплект готов: можно разговаривать и искать по смыслу. Всё выполняется на этом компьютере.'
