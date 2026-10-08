from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from PySide6.QtCore import Qt, QTimer, QSize, QSettings
from app.utils.shortcuts import shortcut as native_shortcut
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QTextCharFormat, QTextCursor, QTextDocument, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QListWidget, QListWidgetItem, QLineEdit, QTextEdit, QLabel, QPushButton, QComboBox, QCheckBox, QToolBar, QSystemTrayIcon, QMenu, QMessageBox, QInputDialog, QDialog, QFileDialog)
from app.services.scheduler import Scheduler, next_occurrence
from app.database.store import Store
from app.ui.reminders import ReminderDialog
from app.ui.design import build, card
from app.ui.icons import icon, note_symbol
from app.services.day_focus import DayFocus
from app.utils.timezones import utc_stamp, event_due_local, event_scheduled_local, device_zone


class Window(QMainWindow):
    def __init__(self, store, background_managed=False):
        super().__init__()
        from app.ui.localization import install_russian_dialogs
        install_russian_dialogs(QApplication.instance())
        self.store = store
        # The GUI uses a short lock wait; background connections retain 5 seconds.
        # Failed writes preserve dirty text and can be retried without freezing typing.
        self.store.db.execute('PRAGMA busy_timeout=250')
        self.background_managed = background_managed
        self.day_focus = DayFocus(store)
        self._day_dialog = None
        self._history_dialog = None
        self._companion_dialog = None
        self._help_dialog = None
        self._status_dialog = None
        self._command_dialog = None
        from app.services.memory_store import MemoryStore
        memory=MemoryStore(store)
        self.workspaces=memory.list_workspaces()
        memory.close()
        self.current = None
        self.loading = False
        self._dirty = False
        self._save_error_reported = False
        self._last_search=''
        self._last_filter=0
        self._plain_cache={}
        self._list_signature=None
        self._card_keys={}
        self._closing_panel=False
        self.scope = ('all', None)
        self.quitting = False
        self.notification_note = None
        self._attention_count=0
        self.setWindowTitle('Orange Notes')
        self.resize(1250, 800)
        pix = QPixmap(64,64)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setBrush(QColor('#ed7926'))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(4,4,56,56,14,14)
        painter.setPen(QColor('white'))
        painter.setFont(QFont('Segoe UI',28,QFont.Bold))
        painter.drawText(pix.rect(),Qt.AlignCenter,'N')
        painter.end()
        self.icon = QIcon(pix)
        from app.utils.runtime_paths import resource_path
        icon_path=resource_path('app/assets/app.ico')
        if icon_path.is_file():self.icon=QIcon(str(icon_path))
        self.setWindowIcon(self.icon)
        build(self)
        from app.widgets.companion_mascot import MiniCompanion
        self._mini_companion=MiniCompanion(self.companion_dock)
        self._mini_companion.activated.connect(self.show_companion)
        self._mini_companion.dismiss.clicked.connect(self.companion_dock.hide)
        self.nav.setContextMenuPolicy(Qt.CustomContextMenu)
        self.nav.customContextMenuRequested.connect(self.collection_menu)
        self._undo = None
        self.undo_button = QPushButton('Отменить последнее действие')
        self.undo_button.clicked.connect(self.undo_change)
        self.statusBar().addPermanentWidget(self.undo_button)
        self.undo_button.hide()
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(600)
        self.debounce.timeout.connect(self.save)
        for signal in [self.title.textChanged,self.body.textChanged,self.tags.textChanged,self.folder.currentIndexChanged,self.favorite.toggled]:
            signal.connect(self.dirty)
        self.tray = QSystemTrayIcon(self.icon,self)
        self.tray.setToolTip('Orange Notes — напоминания')
        menu = QMenu(self)
        for name, callback in [('Открыть',self.open_window),('Новая заметка',self.new_note),('Требуют внимания',self.show_attention_reminders),('Ближайшие напоминания',self.upcoming),('Выход',self.quit)]:
            menu.addAction(name,callback)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.open_window() if reason==QSystemTrayIcon.Trigger else None)
        self.tray.messageClicked.connect(self.open_notification)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
            QApplication.instance().setQuitOnLastWindowClosed(False)
        self.scheduler = Scheduler(store,self.notify)
        self.clock = QTimer(self)
        self.clock.setInterval(15000)
        self.clock.timeout.connect(self.tick)
        self.clock.start()
        self.refresh_nav()
        self.editor.setEnabled(False)
        self.refresh_list()
        self.preferences=None
        if getattr(store,'path',None)==Store.default_path().resolve():
            self.preferences=QSettings('OrangeNotes','OrangeNotes')
            geometry=self.preferences.value('geometry')
            if geometry: self.restoreGeometry(geometry)
            state=self.preferences.value('splitter')
            if state: self.splitter.restoreState(state)
        for keys,callback in [('Ctrl+K',self.show_command_palette),('Ctrl+N',self.new_note),('Ctrl+S',self.save),('Ctrl+F',self.search.setFocus),('Ctrl+B',self.bold),('Ctrl+I',self.italic),('Ctrl+Shift+D',self.duplicate_note),('Escape',self.close_reminder_panel)]:
            shortcut=QShortcut(QKeySequence(native_shortcut(keys)),self)
            shortcut.activated.connect(callback)
        self.startup_tick = QTimer(self)
        self.startup_tick.setSingleShot(True)
        self.startup_tick.timeout.connect(self.tick)
        self.startup_tick.start(1000)

    def refresh_nav(self):
        self.nav.blockSignals(True)
        self.nav.clear()
        items = [('Все заметки','all',None),('Мой день','myday',None),('Напоминания','reminders',None),('Требуют внимания','attention',None),('Сегодня','today',None),('Избранное','favorite',None),('Архив','archive',None),('Корзина','trash',None),('Папки','heading',None)]
        folders = self.store.rows('SELECT * FROM folders ORDER BY name')
        items += [(r['name'],'folder',r['id']) for r in folders]
        items += [('Метки','heading',None)]
        items += [(r['name'],'tag',r['id']) for r in self.store.rows('SELECT * FROM tags ORDER BY name')]
        for name,kind,value in items:
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole,(kind,value))
            symbol={'all':'folder','myday':'sun','reminders':'bell','attention':'bell','today':'calendar','favorite':'star','archive':'archive','trash':'trash','folder':'folder','tag':'dot'}.get(kind)
            color={'Личные':'#ff9800','Работа':'#6960ff','Учёба':'#16c995','Путешествия':'#16c995','Покупки':'#ff3885','Важно':'#ff3885','Идеи':'#ffb300','Встречи':'#258cff','Финансы':'#16c995'}.get(name,'#ff9800' if kind in ('folder','all') else '#17202c')
            if symbol:
                item.setIcon(icon(symbol,color,22,kind in ('all','folder','tag')))
                item.setSizeHint(QSize(0,36))
            if kind=='heading':
                item.setFlags(Qt.NoItemFlags)
                item.setFont(QFont('Segoe UI',10,QFont.Bold))
                item.setForeground(QColor('#7b8188'))
            self.nav.addItem(item)
            if (kind,value)==self.scope:
                self.nav.setCurrentItem(item)
        self.nav.blockSignals(False)
        self.nav.setIconSize(QSize(22,22))
        selected=self.nav.currentItem()
        self.search_scope.setText('Поиск: '+(selected.text() if selected else 'Все заметки'))
        if hasattr(self,'day_suggestions_button'):
            self.day_suggestions_button.setVisible(self.scope[0]=='myday')
        while self.chips.count():
            entry = self.chips.takeAt(0)
            if entry.widget():
                entry.widget().hide()
                entry.widget().deleteLater()
        for name,scope in [('Все',('all',None))]+[(f['name'],('folder',f['id'])) for f in folders[:3]]:
            button = QPushButton(name)
            button.setObjectName('chip')
            button.setCheckable(True)
            button.setChecked(self.scope==scope)
            button.clicked.connect(lambda checked=False,s=scope: self.chip_scope(s))
            self.chips.addWidget(button)
        self.chips.addStretch()

    def chip_scope(self,scope):
        if not self.close_reminder_panel(): return
        if not self.save(): return
        self.scope=scope
        self.refresh_nav()
        self.refresh_list()

    def apply_filters(self):
        if not hasattr(self,'notes'): return
        if not self.close_reminder_panel():
            self.search.blockSignals(True)
            self.filter.blockSignals(True)
            self.search.setText(self._last_search)
            self.filter.setCurrentIndex(self._last_filter)
            self.search.blockSignals(False)
            self.filter.blockSignals(False)
            return
        self._last_search=self.search.text()
        self._last_filter=self.filter.currentIndex()
        self.refresh_list()

    def change_scope(self, item, previous=None):
        if item:
            scope = item.data(Qt.UserRole)
            if not scope or scope[0]=='heading': return
            if not self.close_reminder_panel():
                self.refresh_nav()
                return
            if not self.save():
                self.refresh_nav()
                return
            self.scope = scope
            self.refresh_nav()
            self.refresh_list()

    def refresh_list(self):
        if not hasattr(self,'notes'):
            return
        if self.panel and self.panel.isVisible():
            return
        had_changes=self._dirty
        if not self.save(refresh=False): return
        if had_changes: self.refresh_nav()
        kind,value = self.scope
        query = self.search.text().casefold()
        attention_ids=self.update_attention()
        self.reset_button.setVisible(bool(query or self.filter.currentIndex()))
        rows = self.store.rows('SELECT * FROM notes ORDER BY updated_at DESC,id DESC')
        existing_ids={row['id'] for row in rows}
        self._plain_cache={nid:value for nid,value in self._plain_cache.items() if nid in existing_ids}
        schedules={row['id']:self.store.reminder(row['id']) for row in rows}
        day_ids=set(self.day_focus.selected_ids())
        self.day_button.setText('Убрать из моего дня' if self.current in day_ids else 'Добавить в мой день')
        events={}
        for event in self.store.rows("SELECT e.*,r.note_id FROM reminder_events e JOIN reminders r ON r.id=e.reminder_id WHERE e.revision=r.revision AND e.status IN ('pending','notified') ORDER BY COALESCE(e.due_utc,e.scheduled_utc,e.due_at,e.scheduled_at),e.id"):
            events.setdefault(event['note_id'],event)
        upcoming_by_id={nid:event_due_local(events[nid]) if nid in events else next_occurrence(r) for nid,r in schedules.items()}
        signature=(self.scope,query,self.filter.currentIndex(),datetime.now().date(),tuple(sorted(attention_ids)),tuple(sorted(day_ids)),tuple(tuple(row) for row in rows),
                   tuple(tuple(row) for row in self.store.rows('SELECT note_id,tag_id FROM note_tags ORDER BY note_id,tag_id')),
                   tuple((nid,r['mode'] if r else None,r['once_at'] if r else None,tuple(r['days']) if r else (),tuple(r['times']) if r else (),upcoming_by_id[nid],events[nid]['status'] if nid in events else None) for nid,r in schedules.items()))
        if signature==self._list_signature:
            selected=next((self.notes.item(i) for i in range(self.notes.count()) if self.notes.item(i).data(Qt.UserRole)==self.current),None)
            if selected:
                self.notes.blockSignals(True)
                self.notes.setCurrentItem(selected)
                self.notes.blockSignals(False)
            elif self.current:
                self.clear_editor(refresh=False)
            if not self.current and self.notes.count():
                self.notes.setCurrentRow(0)
                if not self.current: self.load_note(self.notes.item(0).data(Qt.UserRole))
            return
        self._list_signature=signature
        if kind in ('reminders','today','attention'):
            rows = sorted(rows,key=lambda row: upcoming_by_id[row['id']] or datetime.max)
        entries=[]
        for row in rows:
            r = schedules[row['id']]
            upcoming = upcoming_by_id[row['id']]
            if bool(row['deleted']) != (kind=='trash'):
                continue
            if kind!='trash' and bool(row['archived']) != (kind=='archive'):
                continue
            if kind=='favorite' and not row['favorite']: continue
            if kind=='myday' and row['id'] not in day_ids: continue
            if kind=='folder' and row['folder_id']!=value: continue
            if kind=='tag' and not self.store.rows('SELECT 1 FROM note_tags WHERE note_id=? AND tag_id=?',(row['id'],value)): continue
            if kind=='reminders' and not upcoming: continue
            if kind=='attention' and row['id'] not in attention_ids: continue
            if kind=='today' and (not upcoming or upcoming.date()>datetime.now().date()): continue
            if self.filter.currentIndex()==1 and not upcoming: continue
            if self.filter.currentIndex()==2 and row['folder_id'] is not None: continue
            cached=self._plain_cache.get(row['id'])
            if cached and cached[0]==row['body']:
                plain=cached[1]
            else:
                doc=QTextDocument()
                doc.setHtml(row['body'])
                plain=doc.toPlainText()
                self._plain_cache[row['id']]=(row['body'],plain)
            if query and query not in (row['title']+' '+plain).casefold(): continue
            reminder_text = self.event_text(events[row['id']]) if row['id'] in events else self.schedule_text(r) if upcoming else ''
            entries.append((row,plain,reminder_text,upcoming))
        self.notes.blockSignals(True)
        wanted={entry[0]['id'] for entry in entries}
        for index in range(self.notes.count()-1,-1,-1):
            item=self.notes.item(index)
            if item.data(Qt.UserRole) not in wanted:
                self._card_keys.pop(item.data(Qt.UserRole),None)
                self.notes.removeItemWidget(item)
                self.notes.takeItem(index)
        selected = None
        for index,(row,plain,reminder_text,upcoming) in enumerate(entries):
            item=self.notes.item(index)
            if not item or item.data(Qt.UserRole)!=row['id']:
                previous=next((position for position in range(index,self.notes.count()) if self.notes.item(position).data(Qt.UserRole)==row['id']),None)
                if previous is not None:
                    item=self.notes.item(previous)
                    self.notes.removeItemWidget(item)
                    item=self.notes.takeItem(previous)
                    self._card_keys.pop(row['id'],None)
                else:
                    item=QListWidgetItem()
                    item.setData(Qt.UserRole,row['id'])
                self.notes.insertItem(index,item)
            item.setSizeHint(QSize(0,125 if upcoming else 115))
            key=(row['title'],plain[:150],reminder_text,bool(row['favorite']),row['icon_name'])
            if self._card_keys.get(row['id'])!=key:
                self.notes.setItemWidget(item,card(row['title'] or 'Без названия',plain,reminder_text,row['favorite'],on_favorite=lambda nid=row['id']:self.card_favorite(nid),icon_name=row['icon_name']))
                self._card_keys[row['id']]=key
            if row['id']==self.current:
                selected = item
        if selected:
            self.notes.setCurrentItem(selected)
        self.notes.blockSignals(False)
        if self.current and selected is None:
            self.clear_editor(refresh=False)
        if not self.current and self.notes.count():
            self.notes.setCurrentRow(0)
        ordering = 'По времени напоминания' if kind in ('reminders','today') else 'По дате изменения'
        self.count.setText(f'Заметок: {self.notes.count()} · {ordering}')
        self.empty.setVisible(self.notes.count()==0)
        self.empty.setText('Ничего не найдено. Измените поиск или фильтр.' if query or self.filter.currentIndex() else 'Корзина пуста.' if kind=='trash' else 'Начните день с чистого листа. Откройте предложения или добавьте заметку в мой день. Завтра этот список начнётся заново; заметки сохранятся.' if kind=='myday' else 'Заметок пока нет. Нажмите +, чтобы создать заметку.')

    def select_note(self,item,previous=None):
        if not item: return
        note_id = item.data(Qt.UserRole)
        if not self.close_reminder_panel():
            self.notes.blockSignals(True)
            self.notes.setCurrentItem(previous)
            self.notes.blockSignals(False)
            return
        had_changes=self._dirty
        if not self.save(refresh=False):
            self.notes.blockSignals(True)
            self.notes.setCurrentItem(previous)
            self.notes.blockSignals(False)
            return
        self.load_note(note_id)
        if had_changes:
            self.refresh_nav()
            self.refresh_list()

    def load_note(self,note_id):
        if self.panel:
            self.panel.hide()
        rows = self.store.rows('SELECT * FROM notes WHERE id=?',(note_id,))
        if not rows: return
        row = rows[0]
        self.loading = True
        self._dirty = False
        self.current = note_id
        self.note_workspace.blockSignals(True)
        self.note_workspace.setCurrentIndex(max(0,self.note_workspace.findData(row['workspace_id'])))
        self.note_workspace.blockSignals(False)
        self.note_workspace.setEnabled(not row['deleted'])
        self.editor.setEnabled(True)
        self.editor_stack.setCurrentIndex(1)
        self.title.setText(row['title'])
        self.body.setHtml(row['body'])
        self.favorite.setChecked(bool(row['favorite']))
        self.folder.clear()
        self.folder.addItem('Без папки',None)
        for f in self.store.rows('SELECT * FROM folders ORDER BY name'):
            self.folder.addItem(icon('folder','#ff890b',18),f['name'],f['id'])
        self.folder.setCurrentIndex(max(0,self.folder.findData(row['folder_id'])))
        self.tags.setText(', '.join(r[0] for r in self.store.rows('SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id=t.id WHERE nt.note_id=? ORDER BY t.name',(note_id,))))
        self.update_reminder_label()
        self.saved.setText('Сохранено · '+row['updated_at'].replace('T',' '))
        deleted=bool(row['deleted'])
        self.day_button.setEnabled(not deleted and not row['archived'])
        self.icon_button.setEnabled(not deleted)
        symbol,color,_=note_symbol(row['title'],row['icon_name'])
        self.icon_button.setIcon(icon(symbol,color))
        self.icon_button.setToolTip('Иконка выбрана вручную' if row['icon_name'] else 'Иконка подбирается автоматически по названию. Нажмите, чтобы выбрать другую.')
        self.title.setReadOnly(deleted)
        self.body.setReadOnly(deleted)
        for widget in [self.folder,self.tags,self.favorite,self.reminder_label,self.edit_reminder_button]:
            widget.setEnabled(not deleted)
        for button in self.quick_buttons.values(): button.setEnabled(not deleted)
        for name,action in self.editor_actions.items():
            action.setEnabled(not deleted or name in ('restore','delete'))
        self.editor_actions['restore'].setVisible(deleted)
        self.editor_actions['archive'].setVisible(not deleted)
        self.editor_actions['archive'].setToolTip('Вернуть из архива' if row['archived'] else 'В архив')
        self.editor_actions['delete'].setToolTip('Удалить окончательно' if deleted else 'В корзину')
        self.editor_actions['archive'].setText('Из архива' if row['archived'] else 'В архив')
        self.editor_actions['delete'].setText('Удалить навсегда' if deleted else 'В корзину')
        self.editor_actions['restore'].setText('Восстановить')
        self.loading = False

    def dirty(self,*args):
        if not self.loading and self.current:
            self._dirty = True
            self.saved.setText('Сохранение…')
            self.debounce.start()

    def save(self,refresh=True):
        if self.loading or not self.current or not self._dirty: return True
        self.debounce.stop()
        try:
            self.store.save_note(self.current,self.title.text(),self.body.toHtml(),self.folder.currentData(),self.favorite.isChecked(),self.tags.text())
        except (sqlite3.Error,ValueError):
            self.saved.setText('Не удалось сохранить — Ctrl+S для повторной попытки')
            self._mini_companion.notify_event('Не удалось сохранить. Изменения остались в редакторе.','error')
            if not self._save_error_reported:
                self._save_error_reported=True
                QMessageBox.warning(self,'Ошибка сохранения','Изменения остались в редакторе. Проверьте доступ к базе данных и повторите сохранение нажатием Ctrl+S.')
            return False
        self._save_error_reported=False
        self._dirty = False
        self.saved.setText('Сохранено · '+datetime.now().strftime('%H:%M:%S'))
        self._mini_companion.notify_event('Изменения заметки сохранены.','saved')
        selected_icon=self.store.rows('SELECT icon_name FROM notes WHERE id=?',(self.current,))[0][0]
        symbol,color,_=note_symbol(self.title.text(),selected_icon)
        self.icon_button.setIcon(icon(symbol,color))
        if refresh:
            self.refresh_nav()
            self.refresh_list()
        return True

    def new_note(self):
        if not self.close_reminder_panel(): return
        if not self.save(): return
        target_folder=self.scope[1] if self.scope[0]=='folder' else None
        target_tag=self.scope[1] if self.scope[0]=='tag' else None
        target_favorite=self.scope[0]=='favorite'
        target_day=self.scope[0]=='myday'
        self.scope = self.scope if self.scope[0] in ('folder','tag','favorite','myday') else ('all',None)
        self.search.blockSignals(True)
        self.filter.blockSignals(True)
        self.search.clear()
        self.filter.setCurrentIndex(0)
        self._last_search,self._last_filter='',0
        self.search.blockSignals(False)
        self.filter.blockSignals(False)
        try:
            self.current = self.store.create_note(target_folder,tag=target_tag,favorite=target_favorite)
            if target_day:
                self.day_focus.add(self.current)
        except sqlite3.Error:
            QMessageBox.warning(self,'Не удалось создать заметку','База данных сейчас недоступна. Повторите создание позже.')
            return
        self.refresh_nav()
        self.refresh_list()
        self.load_note(self.current)
        self.open_window()
        self.title.setFocus()

    def duplicate_note(self):
        if not self.current: return
        note_id=self.current
        if not self.close_reminder_panel() or not self.save(refresh=False): return
        try:
            with self.store.db:
                self.store.db.execute('BEGIN IMMEDIATE')
                rows=self.store.rows('SELECT * FROM notes WHERE id=? AND deleted=0',(note_id,))
                if not rows: return
                source=rows[0]
                new_id=self.store.db.execute('INSERT INTO notes(title,body,folder_id,updated_at,icon_name) VALUES (?,?,?,?,?)',((source['title'] or 'Без названия')+' — копия',source['body'],source['folder_id'],datetime.now().isoformat(timespec='seconds'),source['icon_name'])).lastrowid
                self.store.db.execute('INSERT INTO note_tags(note_id,tag_id) SELECT ?,tag_id FROM note_tags WHERE note_id=?',(new_id,note_id))
        except sqlite3.Error:
            QMessageBox.warning(self,'Не удалось дублировать заметку','База данных недоступна. Исходная заметка не изменена.')
            return
        self.scope=('all',None)
        self.search.blockSignals(True)
        self.filter.blockSignals(True)
        self.search.clear()
        self.filter.setCurrentIndex(0)
        self._last_search,self._last_filter='',0
        self.search.blockSignals(False)
        self.filter.blockSignals(False)
        self.current=new_id
        self.refresh_nav()
        self.load_note(new_id)
        self.refresh_list()
        self.title.setFocus()

    def create_folder(self):
        if not self.close_reminder_panel(): return
        name,ok = QInputDialog.getText(self,'Новая папка','Название:')
        if ok and name.strip():
            if not self.save(): return
            if not self.write_change(lambda:self.store.execute('INSERT OR IGNORE INTO folders(name) VALUES (?)',(name.strip(),))): return
            self.refresh_nav()
            if self.current: self.load_note(self.current)

    def create_and_assign_folder(self):
        if not self.current or not self.save(refresh=False): return
        name,ok=QInputDialog.getText(self,'Папка заметки','Название новой папки:')
        if not ok or not name.strip(): return
        name=name.strip()
        if not self.write_change(lambda:self.store.execute('INSERT OR IGNORE INTO folders(name) VALUES (?)',(name,))): return
        folder_id=self.store.rows('SELECT id FROM folders WHERE name=?',(name,))[0][0]
        self.folder.addItem(icon('folder','#ff890b',18),name,folder_id)
        self.folder.setCurrentIndex(self.folder.findData(folder_id))
        self.save()
        self.refresh_nav()

    def choose_icon(self):
        if not self.current or not self.close_reminder_panel() or not self.save(refresh=False): return
        note_id=self.current
        dialog=QDialog(self)
        dialog.setWindowTitle('Иконка заметки')
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('Выберите иконку. Она сохранится при изменении названия.'))
        options=[(None,'Автоматически'),('note','Заметка'),('phone','Телефон'),('laptop','Работа'),('plane','Поездка'),('book','Книга'),('gift','Покупки'),('team','Встреча'),('calendar','Календарь'),('star','Звезда'),('check','Задача')]
        from PySide6.QtWidgets import QGridLayout
        grid=QGridLayout()
        selected=self.store.rows('SELECT icon_name FROM notes WHERE id=?',(note_id,))[0][0]
        def choose(name):
            if not self.write_change(lambda:self.store.set_note_icon(note_id,name)): return
            self.load_note(note_id)
            self.refresh_list()
            dialog.accept()
        for index,(name,label) in enumerate(options):
            button=QPushButton(label)
            symbol,color,_=note_symbol(self.title.text(),name)
            button.setIcon(icon(symbol,color,28))
            button.setIconSize(QSize(28,28))
            button.setMinimumSize(130,52)
            button.setCheckable(True)
            button.setChecked(name==selected)
            button.setAutoDefault(False)
            button.clicked.connect(lambda checked=False,n=name:choose(n))
            grid.addWidget(button,index//3,index%3)
        layout.addLayout(grid)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def reset_filters(self):
        if not self.close_reminder_panel() or not self.save(refresh=False): return
        self.search.blockSignals(True)
        self.filter.blockSignals(True)
        self.search.clear()
        self.filter.setCurrentIndex(0)
        self._last_search,self._last_filter='',0
        self.search.blockSignals(False)
        self.filter.blockSignals(False)
        self.refresh_list()

    def search_all(self):
        if not self.close_reminder_panel() or not self.save(refresh=False): return
        self.scope=('all',None)
        self.filter.blockSignals(True)
        self.filter.setCurrentIndex(0)
        self._last_filter=0
        self.filter.blockSignals(False)
        self.refresh_nav()
        self.refresh_list()

    def card_favorite(self,note_id):
        if not self.close_reminder_panel() or not self.save(refresh=False): return
        if not self.write_change(lambda:self.store.execute('UPDATE notes SET favorite=1-favorite,updated_at=? WHERE id=? AND deleted=0',(datetime.now().isoformat(timespec='seconds'),note_id))): return
        if self.current==note_id:
            self.load_note(note_id)
        self.refresh_list()

    def collection_menu(self,position):
        item=self.nav.itemAt(position)
        if not item: return
        kind,value=item.data(Qt.UserRole)
        if kind not in ('folder','tag'): return
        menu=QMenu(self)
        rename=menu.addAction('Переименовать')
        remove=menu.addAction('Удалить папку' if kind=='folder' else 'Удалить метку')
        chosen=menu.exec(self.nav.mapToGlobal(position))
        if chosen not in (rename,remove): return
        if not self.close_reminder_panel() or not self.save(refresh=False): return
        if chosen==rename:
            name,ok=QInputDialog.getText(self,'Переименовать','Новое название:',text=item.text())
            if not ok or not name.strip(): return
            try:
                self.store.rename_collection(kind,value,name)
            except sqlite3.IntegrityError:
                QMessageBox.warning(self,'Название уже используется','Выберите другое название для папки или метки.')
                return
            except (sqlite3.Error,ValueError):
                QMessageBox.warning(self,'Не удалось переименовать','Проверьте название и доступ к базе данных.')
                return
        else:
            message='Заметки сохранятся без этой папки.' if kind=='folder' else 'Метка исчезнет из заметок. Сами заметки сохранятся.'
            if QMessageBox.question(self,'Удалить «'+item.text()+'»?',message,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return
            if not self.write_change(lambda:self.store.delete_collection(kind,value)): return
            if self.scope==(kind,value): self.scope=('all',None)
        self.refresh_nav()
        if self.current: self.load_note(self.current)
        self.refresh_list()

    def show_help(self):
        if self._help_dialog is None:
            from app.ui.help_dialog import HelpDialog
            self._help_dialog=HelpDialog(self)
        self._help_dialog.show()
        self._help_dialog.raise_()
        self._help_dialog.activateWindow()

    def show_command_palette(self):
        if self._command_dialog is not None:
            self._command_dialog.show()
            self._command_dialog.raise_()
            self._command_dialog.activateWindow()
            self._command_dialog.query.setFocus()
            return
        from app.ui.command_palette import CommandPalette
        dialog=CommandPalette(self)
        self._command_dialog=dialog
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda:self.clear_dialog('_command_dialog',dialog))
        def execute(command):
            actions={'new_note':self.new_note,'search':self.search.setFocus,
                     'companion':self.show_companion,'help':self.show_help,
                     'reminder_status':self.reminder_status,'backup':self.backup_database,
                     'archive':lambda:self.chip_scope(('archive',None)),
                     'trash':lambda:self.chip_scope(('trash',None)),
                     'attention':self.show_attention_reminders}
            callback=actions.get(command)
            if callback: callback()
        dialog.commandTriggered.connect(execute)
        dialog.show()

    def show_companion(self):
        if not self.close_reminder_panel() or not self.save():return
        self.companion_dock.show()
        self._mini_companion.show()
        self._mini_companion.reposition()
        if self._companion_dialog is None:
            from app.ui.companion import CompanionDialog
            self._companion_dialog=CompanionDialog(self.store,self)
            self._companion_dialog.open_note.connect(self.open_companion_note)
            self._companion_dialog.note_created.connect(self.companion_note_created)
        self._companion_dialog.show()
        self._companion_dialog.raise_()
        self._companion_dialog.activateWindow()

    def companion_note_created(self,note_id):
        self.refresh_nav()
        self.refresh_list()
        message='Заметка и напоминание созданы.' if self.store.reminder(note_id) else 'Заметка создана.'
        self.statusBar().showMessage(message,6000)
        self._mini_companion.notify_event(message,'created')
        self.open_companion_note(note_id)

    def open_companion_note(self,note_id):
        if not self._companion_dialog:return
        rows=self.store.rows('SELECT workspace_id,deleted FROM notes WHERE id=?',(note_id,))
        if not rows or rows[0]['workspace_id']!=self._companion_dialog.workspace_id:return
        if rows[0]['deleted']:
            if not self._companion_dialog.include_trash.isChecked():return
            if not self.close_reminder_panel() or not self.save():return
            self.scope=('trash',None)
            self.search.blockSignals(True);self.filter.blockSignals(True)
            self.search.clear();self.filter.setCurrentIndex(0)
            self._last_search,self._last_filter='',0
            self.search.blockSignals(False);self.filter.blockSignals(False)
            self.refresh_nav();self.load_note(note_id);self.refresh_list();self.open_window()
            return
        self.notification_note=note_id
        self.open_notification()

    def move_note_workspace(self):
        if self.loading or not self.current:return
        if not self.close_reminder_panel() or not self.save(refresh=False):return
        from app.services.memory_store import MemoryStore
        memory=MemoryStore(self.store)
        try:
            memory.move_note(self.current,self.note_workspace.currentData())
            self.statusBar().showMessage('Пространство изменено. Помощник использует только выбранный контекст.',6000)
        except (sqlite3.Error,ValueError) as error:
            QMessageBox.warning(self,'Не удалось изменить пространство',str(error))
            self.load_note(self.current)
        finally:memory.close()

    def toggle_my_day(self):
        if not self.current or not self.close_reminder_panel() or not self.save(refresh=False):
            return
        note_id=self.current
        present=self.day_focus.contains(note_id)
        change=lambda:self.day_focus.remove(note_id) if present else self.day_focus.add(note_id)
        if not self.write_change(change):
            return
        self.statusBar().showMessage('Убрано из моего дня. Заметка сохранена.' if present else 'Добавлено в мой день.',5000)
        self.refresh_list()
        if self.current:
            self.update_reminder_label()

    def show_day_suggestions(self):
        if not self.close_reminder_panel() or not self.save():
            return
        if self._day_dialog is None:
            from app.ui.day_dialog import DaySuggestionsDialog
            self._day_dialog=DaySuggestionsDialog(self.store,self)
            self._day_dialog.setAttribute(Qt.WA_DeleteOnClose)
            self._day_dialog.changed.connect(self.refresh_list)
            dialog=self._day_dialog
            self._day_dialog.destroyed.connect(lambda:self.clear_dialog('_day_dialog',dialog))
        else:
            self._day_dialog.refresh()
        self._day_dialog.show()
        self._day_dialog.raise_()
        self._day_dialog.activateWindow()

    def act_on_reminder(self,action):
        if not self.current or not self.close_reminder_panel() or not self.save(refresh=False):
            return
        events=self.active_reminder_events()
        if not events:
            self.update_reminder_label()
            return
        event=events[0]
        result={}
        def apply():
            result['value']=self.store.handle_notification_action(event['id'],event['token'],action)
        if not self.write_change(apply) or not result.get('value'):
            return
        if self.background_managed:
            import threading
            def remove():
                from app.services.portable_notifications import notification_transport
                try: notification_transport(self.store.path).remove(event['id'])
                except OSError: pass
            threading.Thread(target=remove,daemon=True).start()
        self.statusBar().showMessage('Событие выполнено. Повторяющееся расписание сохранено.' if action=='done' else 'Напомним снова через 10 минут.',6000)
        self._mini_companion.notify_event('Событие отмечено выполненным.' if action=='done' else 'Напоминание отложено на 10 минут.','reminder_action')
        self.update_reminder_label()
        self.refresh_list()

    def show_reminder_history(self):
        if not self.current or not self.close_reminder_panel() or not self.save():
            return
        from app.ui.reminder_history import ReminderHistoryDialog
        if self._history_dialog:
            self._history_dialog.close()
        self._history_dialog=ReminderHistoryDialog(self.store,self.current,self)
        self._history_dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog=self._history_dialog
        self._history_dialog.destroyed.connect(lambda:self.clear_dialog('_history_dialog',dialog))
        self._history_dialog.show()

    def clear_dialog(self,attribute,dialog):
        if getattr(self,attribute,None) is dialog:
            setattr(self,attribute,None)

    def active_reminder_events(self):
        return self.store.rows("SELECT e.* FROM reminder_events e JOIN reminders r ON r.id=e.reminder_id JOIN notes n ON n.id=r.note_id WHERE r.note_id=? AND r.enabled=1 AND n.archived=0 AND n.deleted=0 AND e.revision=r.revision AND e.status IN ('pending','notified') ORDER BY COALESCE(e.due_utc,e.scheduled_utc,e.due_at,e.scheduled_at),e.id LIMIT 1",(self.current,))

    def reminder_status(self):
        if self._status_dialog is None:
            from app.ui.reminder_status import ReminderStatusDialog
            self._status_dialog=ReminderStatusDialog(self.store,self)
        else:
            self._status_dialog.refresh()
        self._status_dialog.show()
        self._status_dialog.raise_()
        self._status_dialog.activateWindow()

    def export_note(self):
        if not self.current:
            QMessageBox.information(self,'Экспорт','Сначала выберите заметку.')
            return
        if not self.save(refresh=False): return
        filename,_=QFileDialog.getSaveFileName(self,'Экспорт заметки','Заметка.txt','Текст (*.txt);;HTML (*.html)')
        if not filename: return
        path=Path(filename)
        if path.resolve()==self.store.path:
            QMessageBox.warning(self,'Выберите другой файл','Экспорт не может перезаписать рабочую базу заметок.')
            return
        html=path.suffix.lower() in ('.html','.htm')
        try:
            import html as markup
            import re
            content=self.title.text()+'\n\n'+self.body.toPlainText()
            if html:
                content=self.body.toHtml()
                content=content.replace('<head>','<head><title>'+markup.escape(self.title.text())+'</title>',1)
                content=re.sub(r'(<body\b[^>]*>)',lambda match:match[0]+'<h1>'+markup.escape(self.title.text())+'</h1>',content,count=1)
            path.write_text(content,encoding='utf-8')
            self.statusBar().showMessage('Заметка экспортирована',6000)
        except OSError:
            QMessageBox.warning(self,'Экспорт не выполнен','Не удалось записать файл. Выберите другую папку.')

    def backup_database(self):
        if not self.close_reminder_panel() or not self.save(refresh=False): return
        filename,_=QFileDialog.getSaveFileName(self,'Резервная копия','OrangeNotes-'+datetime.now().strftime('%Y-%m-%d')+'.sqlite3','База SQLite (*.sqlite3)')
        if not filename: return
        try:
            self.store.backup_to(filename)
            self.statusBar().showMessage('Резервная копия сохранена',6000)
        except (OSError,sqlite3.Error,ValueError):
            QMessageBox.warning(self,'Копия не сохранена','Выберите доступную папку и файл, отличный от рабочей базы.')

    def restore_database(self):
        from app.services.database_restore import validate_backup
        from app.ui.database_restore_dialog import RestoreDialog
        if self._companion_dialog and self._companion_dialog.busy:
            QMessageBox.warning(self,'Дождитесь помощника','Завершите или отмените запрос ИИ перед восстановлением.')
            return
        if not self.close_reminder_panel() or not self.save(refresh=False): return
        filename,_=QFileDialog.getOpenFileName(self,'Восстановить базу','','База SQLite (*.sqlite3);;Все файлы (*)')
        if not filename: return
        try:
            details=validate_backup(filename)
        except (OSError,sqlite3.Error,ValueError) as error:
            QMessageBox.warning(self,'Копия не подходит',str(error));return
        self._restore_dialog=RestoreDialog(details,lambda:self._perform_database_restore(filename),self)
        self._restore_dialog.show()

    def _perform_database_restore(self,filename):
        from app.services.database_restore import restore_database
        from app.services.platform_background import pause_database_worker
        if self._companion_dialog and self._companion_dialog.busy:
            QMessageBox.warning(self,'Дождитесь помощника','Завершите или отмените запрос ИИ.');return False
        if not self.close_reminder_panel() or not self.save(refresh=False):return False
        self.clock.stop();self.debounce.stop();self.startup_tick.stop()
        try:
            with pause_database_worker(self.store.path):
                for attribute in ('_day_dialog','_history_dialog','_status_dialog','_companion_dialog'):
                    dialog=getattr(self,attribute,None)
                    if dialog:dialog.close();dialog.deleteLater();setattr(self,attribute,None)
                safety=restore_database(self.store,filename)
                self.day_focus=DayFocus(self.store)
                from app.services.memory_store import MemoryStore
                memory=MemoryStore(self.store)
                try:self.workspaces=memory.list_workspaces()
                finally:memory.close()
                self.scheduler=Scheduler(self.store,self.notify)
                self.current=None;self._dirty=False;self._undo=None
                self.undo_button.hide();self._plain_cache.clear();self._list_signature=None;self._card_keys.clear()
                self.scope=('all',None)
                self.clear_editor(refresh=False)
                self.refresh_nav();self.refresh_list()
                self.statusBar().showMessage('База восстановлена. Защитная копия: '+str(safety),15000)
            return True
        except Exception as error:
            QMessageBox.warning(self,'Восстановление не завершено',str(error))
            return False
        finally:
            self.clock.start()

    def offer_undo(self,note_id,archived,deleted,message):
        self._undo=(note_id,archived,deleted)
        self.undo_button.show()
        self.statusBar().showMessage(message,10000)

    def undo_change(self):
        if not self._undo or not self.close_reminder_panel() or not self.save(refresh=False): return
        note_id,archived,deleted=self._undo
        if not self.write_change(lambda:self.store.execute('UPDATE notes SET archived=?,deleted=?,updated_at=? WHERE id=?',(archived,deleted,datetime.now().isoformat(timespec='seconds'),note_id))): return
        self._undo=None
        self.undo_button.hide()
        self.scope=('trash',None) if deleted else ('archive',None) if archived else ('all',None)
        self.current=note_id if self.store.rows('SELECT 1 FROM notes WHERE id=?',(note_id,)) else None
        self.reset_filters()
        self.refresh_nav()
        if self.current: self.load_note(self.current)
        self.refresh_list()
        self.statusBar().showMessage('Действие отменено',6000)

    def edit_reminder(self):
        if not self.current: return
        note_id=self.current
        rows=self.store.rows('SELECT deleted FROM notes WHERE id=?',(note_id,))
        if not rows or rows[0][0]: return
        if not self.save(refresh=False): return
        if self.panel:
            if self.panel.isVisible(): return
            self.panel.hide()
            self.panel.deleteLater()
        dialog = ReminderDialog(self.store.reminder(note_id),self)
        inline=self.width()>=1450
        dialog.setWindowFlags(Qt.Widget if inline else Qt.Tool)
        dialog.setObjectName('reminderPanel')
        dialog.setMinimumWidth(330)
        dialog.setMaximumWidth(370)
        self.panel = dialog
        if inline:
            self.splitter.addWidget(dialog)
        else:
            available=self.screen().availableGeometry()
            dialog.resize(360,min(730,available.height()-40))
            corner=self.mapToGlobal(self.rect().topRight())
            dialog.move(max(available.left()+10,min(corner.x()-370,available.right()-370)),max(available.top()+20,min(corner.y()+35,available.bottom()-dialog.height()-10)))
        def finish(result):
            if result==QDialog.Accepted:
                try:
                    self.store.save_reminder(note_id,*dialog.values())
                    self._mini_companion.notify_event('Расписание напоминаний сохранено.','reminder')
                except (sqlite3.Error,ValueError):
                    dialog.show()
                    QMessageBox.warning(self,'Не удалось сохранить расписание','Изменения остались в панели. Повторите сохранение после устранения ошибки доступа к базе.')
                    return
            dialog.hide()
            self.update_reminder_label()
            if not self._closing_panel:
                self.refresh_list()
        dialog.finished.connect(finish)
        dialog.show()
        if inline: self.splitter.setSizes([210,370,430,330])

    def close_reminder_panel(self):
        if not self.panel or not self.panel.isVisible(): return True
        if self.panel.has_changes():
            answer=QMessageBox.question(self,'Расписание не сохранено','Сохранить изменения расписания перед переходом?',QMessageBox.Save|QMessageBox.Discard|QMessageBox.Cancel,QMessageBox.Save)
            if answer==QMessageBox.Cancel: return False
            if answer==QMessageBox.Save:
                self._closing_panel=True
                try:
                    self.panel.validate()
                finally:
                    self._closing_panel=False
                return not self.panel.isVisible()
        self.panel.hide()
        return True

    def schedule_text(self,r):
        if not r: return ''
        suffix=' · '+r['timezone_id'] if r.get('timezone_id') and r['timezone_id']!=device_zone() else ''
        if r['mode']=='repeat':
            days='Ежедневно' if set(r['days'])==set(range(7)) else ', '.join(['Пн','Вт','Ср','Чт','Пт','Сб','Вс'][d] for d in sorted(r['days']))
            return days+' · '+', '.join(r['times'])+suffix
        return datetime.fromisoformat(r['once_at']).strftime('%d.%m.%Y, %H:%M')+suffix

    def event_text(self,event):
        due=event_due_local(event)
        if due>datetime.now():
            prefix='Отложено до ' if event['due_at']!=event['scheduled_at'] else 'Напоминание: '
        else:
            prefix='Ожидает доставки: ' if event['status']=='pending' else 'Не отмечено выполненным: '
        return prefix+due.strftime('%d.%m.%Y, %H:%M')

    def quick_reminder(self,kind):
        if not self.current: return
        if not self.close_reminder_panel(): return
        note_id=self.current
        if not self.save(refresh=False): return
        now = datetime.now()
        at = now+timedelta(hours=1)
        if kind=='morning': at=(now+timedelta(days=1)).replace(hour=9,minute=0,second=0,microsecond=0)
        if kind=='evening':
            at=now.replace(hour=20,minute=0,second=0,microsecond=0)
            if at<=now: at=now+timedelta(hours=1)
        previous=self.store.reminder(note_id)
        if previous and previous['mode']=='repeat':
            if QMessageBox.question(self,'Заменить повтор?','Быстрое время заменит повторяющееся расписание одним напоминанием. Продолжить?',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
                return
        if not self.write_change(lambda:self.store.save_reminder(note_id,'once',at.isoformat(timespec='seconds'),[],[])):
            return
        self._mini_companion.notify_event('Напоминание назначено на '+at.strftime('%d.%m в %H:%M')+'.','reminder')
        self.update_reminder_label()
        self.refresh_list()

    def update_reminder_label(self):
        r = self.store.reminder(self.current)
        events=self.active_reminder_events()
        self.done_reminder_button.setEnabled(bool(events))
        self.snooze_reminder_button.setEnabled(bool(events))
        self.day_button.setText('Убрать из моего дня' if self.current and self.day_focus.contains(self.current) else 'Добавить в мой день')
        if events:
            text=self.event_text(events[0])
            backlog=self.store.rows("SELECT COUNT(*) FROM reminder_events WHERE reminder_id=? AND revision=? AND status IN ('pending','notified')",(r['id'],r['revision']))[0][0]
            if backlog>1:
                text+='\nНезавершённых событий: '+str(backlog)+'. Кнопки относятся к ближайшему.'
            self.reminder_label.setText(text)
            self.reminder_label.setToolTip(text)
            return
        at = next_occurrence(r)
        text = 'Добавить напоминание'
        if at:
            text = 'Напоминание:\n'+self.schedule_text(r)
        elif r and not r['enabled']:
            text = 'Напоминание отключено'
        elif r and r['mode']=='once':
            text = 'Прошедшее напоминание:\n'+self.schedule_text(r)
        self.reminder_label.setText(text)
        self.reminder_label.setToolTip(text)

    def archive(self):
        if self.current:
            if not self.close_reminder_panel(): return
            note_id=self.current
            if not self.save(refresh=False): return
            previous=self.store.rows('SELECT archived,deleted FROM notes WHERE id=?',(note_id,))
            if not previous: return
            if not self.write_change(lambda:self.store.execute('UPDATE notes SET archived=1-archived,updated_at=? WHERE id=?',(datetime.now().isoformat(timespec='seconds'),note_id))): return
            self.offer_undo(note_id,*previous[0],'Перемещение выполнено. Можно отменить.')
            self.clear_editor()

    def delete(self):
        if not self.current: return
        if not self.close_reminder_panel(): return
        note_id=self.current
        if not self.save(refresh=False): return
        rows=self.store.rows('SELECT deleted FROM notes WHERE id=?',(note_id,))
        if not rows: return
        deleted = rows[0][0]
        if deleted:
            if QMessageBox.question(self,'Удалить окончательно?','Заметка и её напоминания будут удалены без возможности восстановления.',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return
            if not self.write_change(lambda:self.store.execute('DELETE FROM notes WHERE id=?',(note_id,))): return
        else:
            previous=self.store.rows('SELECT archived,deleted FROM notes WHERE id=?',(note_id,))[0]
            if not self.write_change(lambda:self.store.execute('UPDATE notes SET deleted=1,updated_at=? WHERE id=?',(datetime.now().isoformat(timespec='seconds'),note_id))): return
            self.offer_undo(note_id,*previous,'Заметка перемещена в корзину. Можно отменить.')
        if deleted and self._undo and self._undo[0]==note_id:
            self._undo=None
            self.undo_button.hide()
        self.clear_editor()

    def restore(self):
        if self.current:
            note_id=self.current
            if not self.save(refresh=False): return
            rows=self.store.rows('SELECT deleted FROM notes WHERE id=?',(note_id,))
            if not rows or not rows[0][0]: return
            if not self.write_change(lambda:self.store.execute('UPDATE notes SET deleted=0,archived=0,updated_at=? WHERE id=?',(datetime.now().isoformat(timespec='seconds'),note_id))): return
            self.clear_editor()

    def write_change(self,callback):
        try:
            callback()
            return True
        except (sqlite3.Error,ValueError):
            self._mini_companion.notify_event('Действие не выполнено. Проверьте доступ к базе.','error')
            QMessageBox.warning(self,'Не удалось выполнить действие','База данных сейчас недоступна. Данные не изменены; повторите действие после устранения ошибки.')
            return False

    def clear_editor(self,refresh=True):
        if self.panel: self.panel.hide()
        self.current = None
        self._dirty = False
        self.debounce.stop()
        self.loading = True
        self.title.clear()
        self.body.clear()
        self.tags.clear()
        self.reminder_label.setText('Добавить напоминание')
        self.loading = False
        self.editor.setEnabled(False)
        self.editor_stack.setCurrentIndex(0)
        if refresh: self.refresh_list()

    def bold(self):
        if not self.current or self.body.isReadOnly(): return
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Normal if self.body.fontWeight()==QFont.Bold else QFont.Bold)
        self.body.mergeCurrentCharFormat(fmt)
        self.body.setFocus()

    def italic(self):
        if not self.current or self.body.isReadOnly(): return
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.body.fontItalic())
        self.body.mergeCurrentCharFormat(fmt)
        self.body.setFocus()

    def checklist(self):
        if not self.current or self.body.isReadOnly(): return
        self.edit_checklist(False)

    def toggle_check(self):
        if not self.current or self.body.isReadOnly(): return
        self.edit_checklist(True)

    def edit_checklist(self,toggle):
        original=self.body.textCursor()
        block=self.body.document().findBlock(original.selectionStart())
        end=original.selectionEnd()
        blocks=[]
        while block.isValid():
            blocks.append(block)
            block=block.next()
            if not original.hasSelection() or not block.isValid() or block.position()>=end: break
        remove = not toggle and all(b.text().startswith(('☐ ','☑ ')) for b in blocks)
        mark='☐ ' if toggle and all(b.text().startswith('☑ ') for b in blocks) else '☑ '
        original.beginEditBlock()
        for block in blocks:
            cursor=QTextCursor(block)
            present=block.text().startswith(('☐ ','☑ '))
            if block.textList(): block.textList().remove(block)
            fmt=cursor.blockFormat()
            fmt.setIndent(0)
            cursor.setBlockFormat(fmt)
            if remove:
                cursor.movePosition(QTextCursor.Right,QTextCursor.KeepAnchor,2)
                cursor.removeSelectedText()
            elif toggle:
                if present: cursor.movePosition(QTextCursor.Right,QTextCursor.KeepAnchor,2)
                cursor.insertText(mark)
            elif not present:
                cursor.insertText('☐ ')
        original.endEditBlock()
        if original.hasSelection():
            original.setPosition(blocks[0].position())
            original.setPosition(blocks[-1].position()+blocks[-1].length()-1,QTextCursor.KeepAnchor)
        self.body.setTextCursor(original)
        self.body.setFocus()

    def bullets(self):
        if not self.current or self.body.isReadOnly(): return
        from PySide6.QtGui import QTextListFormat
        self.body.toggle_list(QTextListFormat.ListDisc)

    def numbered(self):
        if not self.current or self.body.isReadOnly(): return
        from PySide6.QtGui import QTextListFormat
        self.body.toggle_list(QTextListFormat.ListDecimal)

    def sync_editor_format(self):
        from PySide6.QtGui import QTextListFormat
        blocks = self.body.selected_blocks()
        values = {
            'bold_button': self.body.fontWeight() >= QFont.Bold,
            'italic_button': self.body.fontItalic(),
            'checklist_button': all(b.text().startswith(('☐ ', '☑ ')) for b in blocks),
            'bullets_button': all(b.textList() and b.textList().format().style() == QTextListFormat.ListDisc for b in blocks),
            'numbered_button': all(b.textList() and b.textList().format().style() == QTextListFormat.ListDecimal for b in blocks),
        }
        for name, value in values.items():
            button = getattr(self, name, None)
            if button is not None:
                button.blockSignals(True)
                button.setChecked(bool(value))
                button.blockSignals(False)

    def notify(self,title,note_id,event_id):
        # Delivery belongs to the independent worker, never a modal GUI dialog.
        raise OSError('Системные уведомления доставляет фоновая служба напоминаний.')

    def open_notification(self):
        if not self.notification_note: return
        self.open_window()
        if not self.close_reminder_panel() or not self.save(): return
        rows=self.store.rows('SELECT archived,deleted FROM notes WHERE id=?',(self.notification_note,))
        if not rows or rows[0]['deleted']:
            QMessageBox.information(self,'Напоминание','Эта заметка уже удалена или находится в корзине.')
            return
        self.scope=('archive',None) if rows[0]['archived'] else ('all',None)
        self.search.blockSignals(True)
        self.filter.blockSignals(True)
        self.search.clear()
        self.filter.setCurrentIndex(0)
        self._last_search,self._last_filter='',0
        self.search.blockSignals(False)
        self.filter.blockSignals(False)
        self.refresh_nav()
        self.load_note(self.notification_note)
        self.refresh_list()

    def tick(self):
        try:
            if not self.background_managed:
                self.scheduler.tick()
            if self.current: self.update_reminder_label()
            self.refresh_list()
        except (sqlite3.Error, OSError):
            self.statusBar().showMessage('База временно недоступна. Напоминания повторят попытку через 15 секунд.',15000)

    def update_attention(self):
        condition="""e.revision=r.revision AND e.status IN ('pending','notified')
            AND r.enabled=1 AND n.deleted=0 AND n.archived=0 AND (
            (COALESCE(e.due_utc,e.scheduled_utc) IS NOT NULL AND COALESCE(e.due_utc,e.scheduled_utc)<=?) OR
            (COALESCE(e.due_utc,e.scheduled_utc) IS NULL AND COALESCE(e.due_at,e.scheduled_at)<=?))"""
        params=(utc_stamp(),datetime.now().isoformat(timespec='seconds'))
        joins=' FROM reminder_events e JOIN reminders r ON r.id=e.reminder_id JOIN notes n ON n.id=r.note_id WHERE '
        rows=self.store.rows('SELECT DISTINCT r.note_id'+joins+condition,params)
        count=len(rows)
        self.attention_button.setText('Требуют внимания · заметок: '+str(count)+' · Открыть')
        self.attention_button.setVisible(count>0)
        from app.services.notification_health import read_notification_health
        health=read_notification_health(self.store)
        blocked=health['blocked']
        events=[]
        event_count=self.store.rows('SELECT COUNT(*)'+joins+condition,params)[0][0] if count else 0
        self._attention_event_count=event_count
        if blocked and count:
            events=self.store.rows('SELECT e.title,e.body'+joins+condition+' ORDER BY COALESCE(e.due_utc,e.scheduled_utc,e.due_at,e.scheduled_at),e.id LIMIT 1',params)
        self.notification_fallback.update_events(blocked,events,event_count)
        if hasattr(self,'tray'):
            self.tray.setToolTip('Orange Notes'+(' · Требуют внимания: '+str(count)+' заметок' if count else ' — напоминания')+(' · Уведомления Windows отключены' if blocked else ''))
            from app.ui.tray_badge import counted_icon
            badge_state=(event_count,blocked)
            if getattr(self,'_tray_badge_state',None)!=badge_state:
                base=icon('bell','#ff890b',32) if blocked and count else self.windowIcon()
                self.tray.setIcon(counted_icon(base,event_count))
                self._tray_badge_state=badge_state
            if event_count:
                self.tray.setToolTip(self.tray.toolTip()+' · Событий: '+str(event_count))
        if count>self._attention_count:
            self._mini_companion.notify_event('Есть напоминания, требующие внимания.','attention')
        self._attention_count=count
        return {row[0] for row in rows}

    def show_attention_reminders(self):
        if not self.close_reminder_panel() or not self.save():return
        self.scope=('attention',None)
        self.search.clear();self.filter.setCurrentIndex(0)
        self.refresh_nav();self.refresh_list();self.open_window()

    def upcoming(self):
        if not self.close_reminder_panel(): return
        if not self.save(): return
        self.scope = ('reminders',None)
        self.search.clear()
        self.filter.setCurrentIndex(0)
        self.refresh_nav()
        self.refresh_list()
        self.open_window()

    def open_window(self):
        if self.isMinimized():
            if self.windowState() & Qt.WindowMaximized:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def quit(self):
        if not self.close_reminder_panel(): return
        if not self.save(): return
        if self._companion_dialog and self._companion_dialog.busy:
            self._companion_dialog.shutdown_ready.connect(self.quit,Qt.UniqueConnection)
            self._companion_dialog.request_close()
            self.statusBar().showMessage('Завершаю запрос помощника перед выходом…')
            return
        self.quitting = True
        if self.preferences:
            self.preferences.setValue('geometry',self.saveGeometry())
            self.preferences.setValue('splitter',self.splitter.saveState())
        self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self,event):
        if not self.close_reminder_panel():
            event.ignore()
            return
        if not self.save():
            event.ignore()
            return
        if self.preferences:
            self.preferences.setValue('geometry',self.saveGeometry())
            self.preferences.setValue('splitter',self.splitter.saveState())
        if not self.quitting and self.tray.isVisible():
            event.ignore()
            settings=self.preferences
            if settings and not settings.value('tray_explained',False,type=bool):
                QMessageBox.information(self,'Заметки остаются в трее','Приложение продолжит работать и показывать напоминания.\nЧтобы открыть окно, нажмите значок Orange Notes в системном трее.\nЧтобы завершить приложение, выберите там «Выход».')
                settings.setValue('tray_explained',True)
            self.hide()
        else:
            if self._companion_dialog and self._companion_dialog.busy:
                event.ignore()
                self.quit()
                return
            event.accept()
