from html import escape
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QSplitter, QListWidget, QLineEdit, QComboBox, QCheckBox, QTextEdit, QToolBar, QSizePolicy, QLayout, QStackedWidget, QScrollArea, QMenu
from app.ui.icons import duotone_icon, duotone_pixmap, icon, pixmap, note_symbol
from app.ui.editor import NoteEditor
from app.ui.app_menu import create_app_menu_button
from app.ui.folder_assignment import FolderAssignment
from app.utils.runtime_paths import resource_path
from app.widgets.companion_mascot import CompanionLauncher

STYLE = '''
QWidget {font-family: "Segoe UI"; font-size:14px; color:#17202c; background:#ffffff;}
QWidget#sidebar {background:#f8f6f3;}
QWidget#middle {background:#fcfcfc; border-right:1px solid #eeece8;}
QLabel {background:transparent; border:0;}
QLabel#brand {font-size:23px; font-weight:700; color:#141414;}
QLabel#section {color:#283343; font-size:13px; font-weight:600; padding-top:14px;}
QLineEdit,QComboBox,QTimeEdit,QDateTimeEdit {background:#f6f5f3; border:0; border-radius:8px; padding:9px 12px; color:#596170;}
QLineEdit#title {background:white; color:#101010; font-size:27px; font-weight:700; padding:0; border:0;}
QTextEdit {border:0; background:white; padding:0; color:#354b65; font-size:16px; selection-background-color:#ffe1be;}
QPushButton {border:1px solid #eeece8; background:white; border-radius:8px; padding:9px 12px;}
QPushButton:hover {background:#fff2e4; border-color:#ffc38d;}
QPushButton:disabled {color:#9098a1; background:#fafafa; border-color:#eeece8;}
QPushButton#primary {background:#ff890b; border:0; color:#291800; font-weight:600;}
QPushButton#createNote, QPushButton#new {background:#f3ebe2; border:1px solid #dfcebd; color:#513526;}
QPushButton#createNote {font-weight:600;}
QPushButton#new {border-radius:11px; padding:0;}
QPushButton#createNote:hover, QPushButton#new:hover {background:#ece0d3; border-color:#cbb097;}
QPushButton#createNote:pressed, QPushButton#new:pressed {background:#e5d5c5;}
QPushButton#chip {background:#f4f3f1; border:1px solid transparent; padding:5px 10px; border-radius:8px; font-size:12px;}
QPushButton#chip:checked {background:#efe7dd; border-color:#dccdbb; color:#533b28;}
QPushButton#chip:hover {border-color:#cbb097;}
QPushButton#createNote:focus, QPushButton#new:focus, QPushButton#chip:focus,
QPushButton#quickReminder:focus {border-color:#9c6a44;}
QPushButton#quickReminder {padding:0;}
QPushButton#quickReminder:hover {background:#f8f3ed; border-color:#dfcebd;}
QPushButton#quickReminder:pressed {background:#efe5da;}
QPushButton#quickReminder QLabel:disabled {color:#9098a1;}
QPushButton#flat {border:0; background:transparent; color:#777f89; text-align:left; padding:7px 0;}
QPushButton#reminder {background:#fff3e5; border:0; color:#855021; text-align:left; padding:20px; border-radius:16px; font-size:15px;}
QStatusBar#windowStatus {background:#fcfbf9; border-top:1px solid #eeece8;}
QStatusBar#windowStatus::item {border:none;}
QWidget#statusActions {background:transparent;}
QListWidget {border:0; background:transparent; outline:0;}
QListWidget#navigation::item {padding:8px 12px; margin:2px 0; border-radius:10px;}
QListWidget#navigation::item:selected {background:#ffead5; color:#1c2028;}
QPushButton#noteIconOption {text-align:left; padding:8px 12px;}
QPushButton#noteIconOption:checked {background:#f7ebdd; border-color:#bb875b;}
QListWidget#cards::item {border:1px solid #efede9; border-radius:10px; background:white; margin:4px 0;}
QListWidget#cards::item:selected {border:1px solid #e9cfb2; background:#fff5e8;}
QWidget#card {background:transparent; border:0;}
QLabel#cardTitle {font-size:15px; font-weight:600; color:#151a21;}
QLabel#snippet {font-size:13px; color:#737e8c;}
QLabel#cardReminder {font-size:13px; color:#ff7900;}
QLabel#tile {border-radius:13px;}
QPushButton#favorite {border:0;background:white;padding:5px;}
QToolBar {border:0; border-bottom:1px solid #f0eeeb; background:white; spacing:6px; padding:6px 2px;}
QToolButton {border:0; border-radius:6px; padding:5px; background:white; font-size:16px;}
QToolButton:hover {background:#fff0df;}
QToolButton:checked {background:#ffe1bd;color:#ae5207;border:1px solid #ffad59;}
QCheckBox {background:transparent; spacing:8px;}
QCheckBox::indicator {width:17px; height:17px; border:1px solid #bcc1c7; border-radius:8px; background:white;}
QCheckBox::indicator:checked {background:#ff890b; border-color:#ff890b;}
QCheckBox#day {background:#f5f4f2; border-radius:12px; padding:8px 5px; font-size:12px; spacing:0;}
QCheckBox#day:checked {background:#ff890b; color:white;}
QCheckBox#day::indicator {width:0; height:0; border:0;}
QScrollBar:vertical {background:#faf9f7;width:6px;border:0;}
QScrollBar::handle:vertical {background:#dedbd5;border-radius:3px;min-height:30px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QComboBox::drop-down {border:0;width:22px;}
QPushButton:focus,QToolButton:focus,QLineEdit:focus,QComboBox:focus,QTextEdit:focus {border:1px solid #e77613;}
QListWidget:focus {border:0; outline:0;}
QListWidget#cards::item:focus {outline:0;}
QSplitter::handle {background:#eeece8; width:1px;}
QWidget#reminderPanel {background:#fff; border-left:1px solid #ede9e3;}
QCalendarWidget QWidget {font-size:12px;}
QCalendarWidget QToolButton {font-size:13px; color:#253044;}
QCalendarWidget QAbstractItemView {selection-background-color:#ff890b; selection-color:white; border:0; outline:0;}
'''


def build(window):
    w = window
    w.setStyleSheet(STYLE)
    w.setWindowTitle('Заметки')
    screen=QApplication.primaryScreen().availableGeometry()
    w.setMinimumSize(min(900,screen.width()-40),min(540,screen.height()-60))
    w.resize(min(1250,screen.width()-40),min(760,screen.height()-60))
    w.splitter = QSplitter()
    w.splitter.setChildrenCollapsible(False)
    w.setCentralWidget(w.splitter)
    left = QWidget()
    w.sidebar=left
    left.setObjectName('sidebar')
    left.setMinimumWidth(228)
    left.setMaximumWidth(270)
    ll = QVBoxLayout(left)
    ll.setContentsMargins(16,24,16,16)
    ll.setSpacing(6)
    brand_row = QHBoxLayout()
    logo = QLabel()
    logo.setPixmap(w.icon.pixmap(36,36))
    logo.setAlignment(Qt.AlignCenter)
    logo.setFixedSize(36,36)
    brand_row.addWidget(logo)
    brand = QLabel('Заметки')
    brand.setObjectName('brand')
    brand_row.addWidget(brand,1)
    w.app_menu_button=create_app_menu_button(w)
    brand_row.addWidget(w.app_menu_button)
    ll.addLayout(brand_row)
    assistant=CompanionLauncher()
    assistant.clicked.connect(w.show_companion)
    w.companion_button=assistant
    ll.addWidget(assistant)
    w.pet_toggle = QCheckBox('Показывать питомца')
    w.pet_toggle.setObjectName('petToggle')
    w.pet_toggle.setAccessibleName('Показывать питомца на экране')
    w.pet_toggle.setToolTip('Показать питомца на экране')
    tick = resource_path('app/assets/checkbox-tick.svg').as_posix()
    w.pet_toggle.setStyleSheet(
        'QCheckBox {background:transparent; padding:4px 8px; spacing:8px; font-size:13px;}'
        'QCheckBox::indicator {width:15px; height:15px; border-radius:4px;}'
        f'QCheckBox::indicator:checked {{background:white;border-color:#a77b55;image:url("{tick}");}}'
    )
    w.pet_toggle.clicked.connect(w.set_pet_visible)
    ll.addWidget(w.pet_toggle,0,Qt.AlignLeft)
    ll.addSpacing(14)
    w.nav = QListWidget()
    w.nav.setObjectName('navigation')
    w.nav.setAccessibleName('Разделы, папки и метки')
    w.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    w.nav.currentItemChanged.connect(w.change_scope)
    ll.addWidget(w.nav,1)
    add = QPushButton('Добавить папку')
    add.setIcon(icon('plus','#818994',18))
    add.setObjectName('flat')
    add.clicked.connect(w.create_folder)
    ll.addWidget(add)
    w.splitter.addWidget(left)
    middle = QWidget()
    w.middle=middle
    middle.setObjectName('middle')
    middle.setMinimumWidth(260)
    ml = QVBoxLayout(middle)
    ml.setContentsMargins(18,24,18,18)
    ml.setSpacing(10)
    from app.ui.notification_fallback import NotificationFallback
    w.notification_fallback=NotificationFallback(w.show_attention_reminders,w.reminder_status,middle)
    ml.addWidget(w.notification_fallback)
    w.attention_button=QPushButton()
    w.attention_button.setAccessibleName('Напоминания, требующие внимания')
    w.attention_button.setStyleSheet('background:#fff0df;color:#a95008;border:1px solid #ffd3ac;text-align:left;')
    w.attention_button.setToolTip('Доставленное уведомление остаётся здесь до выполнения или откладывания.')
    w.attention_button.clicked.connect(w.show_attention_reminders)
    w.attention_button.hide()
    ml.addWidget(w.attention_button)
    searchrow = QHBoxLayout()
    w.search = QLineEdit()
    w.search.setPlaceholderText('Поиск по названию и тексту…')
    w.search.setClearButtonEnabled(True)
    w.search.setAccessibleName('Поиск заметок')
    w.search.setToolTip('Поиск в выбранном разделе, папке или метке')
    w.search.addAction(icon('search','#828a95',20),QLineEdit.LeadingPosition)
    w.search.textChanged.connect(lambda: w.apply_filters())
    searchrow.addWidget(w.search,1)
    w.filter = QComboBox()
    w.filter.addItem(icon('filter','#78818c'),'Все в разделе')
    w.filter.addItems(['С напоминанием','Без папки'])
    w.filter.setToolTip('Фильтр заметок')
    w.filter.setAccessibleName('Фильтр заметок')
    w.filter.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
    w.filter.currentIndexChanged.connect(lambda: w.apply_filters())
    ml.addLayout(searchrow)
    filter_row=QHBoxLayout()
    filter_row.addWidget(w.filter,1)
    w.search_scope=QLabel('Поиск: все заметки')
    w.search_scope.setWordWrap(True)
    w.search_scope.setStyleSheet('font-size:11px;color:#737e8c;')
    all_search=QPushButton('Искать везде')
    all_search.setToolTip('Искать во всех активных заметках')
    all_search.clicked.connect(w.search_all)
    filter_row.addWidget(all_search)
    ml.addLayout(filter_row)
    ml.addWidget(w.search_scope)
    w.day_suggestions_button=QPushButton('Предложения для моего дня')
    w.day_suggestions_button.setIcon(duotone_icon('lightbulb-filament','#9e621f',18))
    w.day_suggestions_button.clicked.connect(w.show_day_suggestions)
    w.day_suggestions_button.hide()
    ml.addWidget(w.day_suggestions_button)
    w.chips = QHBoxLayout()
    ml.addLayout(w.chips)
    w.empty = QLabel('Заметок пока нет. Нажмите +, чтобы создать первую.')
    w.empty.setWordWrap(True)
    w.empty.setStyleSheet('color:#89919b;padding:16px;')
    ml.addWidget(w.empty)
    w.reset_button=QPushButton('Сбросить поиск и фильтры')
    w.reset_button.clicked.connect(w.reset_filters)
    w.reset_button.hide()
    ml.addWidget(w.reset_button)
    w.notes = QListWidget()
    w.notes.setObjectName('cards')
    w.notes.setAccessibleName('Список заметок')
    w.notes.setSpacing(3)
    w.notes.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    w.notes.currentItemChanged.connect(w.select_note)
    ml.addWidget(w.notes,1)
    w.companion_dock=QWidget()
    w.companion_dock.setFixedHeight(180)
    w.companion_dock.setStyleSheet('background:transparent;')
    ml.addWidget(w.companion_dock)
    w.companion_dock.hide()
    footer = QHBoxLayout()
    w.count = QLabel()
    w.count.setStyleSheet('font-size:11px;color:#90969e;')
    footer.addWidget(w.count,1)
    new = QPushButton()
    new.setIcon(icon('plus','#69462e',22))
    new.setIconSize(QSize(22,22))
    new.setObjectName('new')
    new.setFixedSize(42,42)
    new.setToolTip('Новая заметка')
    new.setAccessibleName('Новая заметка')
    new.clicked.connect(w.new_note)
    footer.addWidget(new)
    ml.addLayout(footer)
    w.splitter.addWidget(middle)
    w.editor = QWidget()
    w.editor.setMinimumWidth(280)
    layout = QVBoxLayout(w.editor)
    layout.setContentsMargins(22,14,22,18)
    layout.setSpacing(16)
    toolbar = QToolBar()
    toolbar.setIconSize(QSize(20,20))
    w.editor_actions = {}
    for label,tip,callback,symbol in [('','Жирный (Ctrl+B)',w.bold,'bold'),('','Курсив (Ctrl+I)',w.italic,'italic'),('','Чек-лист: включить / выключить',w.checklist,'checklist'),('','Маркированный список',w.bullets,'list'),('','Нумерованный список',w.numbered,'numbered'),('','Отметить / снять отметку текущего пункта чек-листа',w.toggle_check,'tick'),('','Создать копию (Ctrl+Shift+D)',w.duplicate_note,'copy'),('','Архив / вернуть из архива',w.archive,'archive'),('','В корзину / удалить окончательно',w.delete,'trash'),('','Восстановить',w.restore,'restore')]:
        action = toolbar.addAction(icon(symbol),label) if symbol else toolbar.addAction(label)
        if symbol:
            action.setText(tip)
        action.setToolTip(tip)
        action.triggered.connect(callback)
        w.editor_actions[callback.__name__] = action
        if callback.__name__ in ('bold','italic','checklist','bullets','numbered'):
            action.setCheckable(True)
            setattr(w,callback.__name__+'_button',action)
    layout.addWidget(toolbar)
    toolbar.setAccessibleName('Форматирование и действия заметки')
    toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
    w.editor_actions['duplicate_note'].setToolTip('Создать копию (Ctrl+Shift+D). Напоминания не копируются.')
    for name,label in [('checklist','Чек-лист'),('toggle_check','Отметить пункт'),('duplicate_note','Создать копию')]:
        action=w.editor_actions[name]
        action.setText(label)
        button=toolbar.widgetForAction(action)
        if name == 'checklist':
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setAccessibleName(label)
    for name in ('archive','delete','restore'):
        toolbar.removeAction(w.editor_actions[name])
    actions_row=QHBoxLayout()
    for name,text in [('archive','В архив'),('delete','В корзину'),('restore','Восстановить')]:
        button=QPushButton(text)
        action=w.editor_actions[name]
        if name in ('archive', 'delete'):
            symbol = 'archive' if name == 'archive' else 'trash'
            action.setIcon(duotone_icon(symbol, '#58483B', 24))
            button.setIconSize(QSize(20,20))
        button.setIcon(action.icon())
        button.clicked.connect(action.trigger)
        action.changed.connect(lambda a=action,b=button:(b.setVisible(a.isVisible()),b.setEnabled(a.isEnabled()),b.setText(a.text() if a.text() in ('В архив','Из архива','Вернуть из архива','В корзину','Удалить навсегда','Удалить окончательно','Восстановить') else b.text())))
        actions_row.addWidget(button)
    layout.addLayout(actions_row)
    w.title = QLineEdit()
    w.title.setObjectName('title')
    w.title.setAccessibleName('Название заметки')
    w.title.setPlaceholderText('Название заметки')
    title_row=QHBoxLayout()
    title_row.addWidget(w.title,1)
    w.icon_button=QPushButton('Значок')
    w.icon_button.setIcon(duotone_icon('squares-four','#855021',20))
    w.icon_button.setIconSize(QSize(20,20))
    w.icon_button.setAccessibleName('Выбрать значок заметки')
    w.icon_button.setToolTip('Открыть коллекцию значков')
    w.icon_button.clicked.connect(w.choose_icon)
    title_row.addWidget(w.icon_button)
    layout.addLayout(title_row)
    meta = QHBoxLayout()
    w.folder = FolderAssignment()
    w.folder.setAccessibleName('Папка заметки')
    w.folder.setToolTip('Текущая папка заметки. Нажмите +, чтобы назначить другую.')
    w.folder.create_requested.connect(w.create_and_assign_folder)
    w.favorite = QPushButton()
    w.favorite.setObjectName('favorite')
    w.favorite.setCheckable(True)
    w.favorite.setIcon(icon('star','#8c949f'))
    w.favorite.setIconSize(QSize(24,24))
    w.favorite.toggled.connect(lambda checked:w.favorite.setIcon(icon('star','#ff890b' if checked else '#8c949f',24,checked)))
    w.favorite.setToolTip('Избранное')
    w.favorite.setAccessibleName('Избранное')
    w.tags = QLineEdit()
    w.tags.setAccessibleName('Метки через запятую')
    w.tags.setPlaceholderText('Метки через запятую')
    w.tags.setToolTip('Например: Важно, Работа. Метки создаются и сохраняются автоматически.')
    w.tags.addAction(icon('plus','#939ba5',16),QLineEdit.LeadingPosition)
    meta.addWidget(w.folder)
    meta.addWidget(w.tags,1)
    meta.addWidget(w.favorite)
    layout.addLayout(meta)
    w.body = NoteEditor()
    w.body.setAccessibleName('Текст заметки')
    w.body.setTabChangesFocus(True)
    w.body.setMinimumHeight(150)
    w.body.setAcceptRichText(False)
    w.body.setFont(QFont('Segoe UI',12))
    w.body.document().setDefaultFont(QFont('Segoe UI',12))
    w.body.setPlaceholderText('Текст заметки…')
    for signal in (w.body.currentCharFormatChanged,w.body.cursorPositionChanged,w.body.textChanged):
        signal.connect(lambda *args:w.sync_editor_format())
    layout.addWidget(w.body,1)
    w.note_workspace=QComboBox()
    for space in w.workspaces:w.note_workspace.addItem(space['name'],space['id'])
    w.note_workspace.setAccessibleName('Пространство заметки для ИИ-помощника')
    w.note_workspace.setToolTip('Личное и рабочее пространство разделены в чате помощника.')
    w.note_workspace.currentIndexChanged.connect(w.move_note_workspace)
    layout.addWidget(w.note_workspace)
    w.reminder_label = QPushButton('Добавить напоминание')
    w.reminder_label.setIcon(duotone_icon('bell-simple','#855021',26))
    w.reminder_label.setIconSize(QSize(26,26))
    w.reminder_label.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
    w.reminder_label.setObjectName('reminder')
    w.reminder_label.clicked.connect(w.edit_reminder)
    layout.addWidget(w.reminder_label)
    actions=QHBoxLayout()
    w.done_reminder_button=QPushButton('Выполнено')
    w.done_reminder_button.setIcon(icon('check','#ff890b',18))
    w.done_reminder_button.setToolTip('Выполнить ближайшее событие. Повторяющееся расписание сохранится.')
    w.done_reminder_button.clicked.connect(lambda:w.act_on_reminder('done'))
    actions.addWidget(w.done_reminder_button)
    w.snooze_reminder_button=QPushButton('Отложить на 10 минут')
    w.snooze_reminder_button.clicked.connect(lambda:w.act_on_reminder('snooze'))
    actions.addWidget(w.snooze_reminder_button)
    w.reminder_history_button=QPushButton('История')
    w.reminder_history_button.clicked.connect(w.show_reminder_history)
    actions.addWidget(w.reminder_history_button)
    layout.addLayout(actions)
    w.day_button=QPushButton('Добавить в мой день')
    w.day_button.setIcon(duotone_icon('sun','#9e621f',20))
    w.day_button.setIconSize(QSize(20,20))
    w.day_button.clicked.connect(w.toggle_my_day)
    layout.addWidget(w.day_button)
    label = QLabel('Быстрое время')
    label.setStyleSheet('font-weight:600;')
    layout.addWidget(label)
    quick = QHBoxLayout()
    w.quick_buttons = {}
    shortcuts = [
        ('Сегодня вечером', 'evening', 'moon-stars', '#675080'),
        ('Завтра утром', 'morning', 'sun-horizon', '#9e621f'),
        ('Через час', 'hour', 'clock-countdown', '#2b637a'),
    ]
    for text, kind, symbol, color in shortcuts:
        button = QPushButton()
        button.setObjectName('quickReminder')
        button.setAccessibleName(text)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        content = QVBoxLayout(button)
        content.setContentsMargins(5, 9, 5, 9)
        content.setSpacing(6)
        glyph = QLabel()
        glyph.setPixmap(duotone_pixmap(symbol, color, 22))
        caption = QLabel(text)
        caption.setWordWrap(True)
        for part in (glyph, caption):
            part.setAttribute(Qt.WA_TransparentForMouseEvents)
            part.setAlignment(Qt.AlignCenter)
            content.addWidget(part)
        button.clicked.connect(lambda checked=False,k=kind: w.quick_reminder(k))
        button.setToolTip({'evening':'Напоминание сегодня в 20:00. Если уже позже, напомнит через час. Заменяет текущее расписание.','morning':'Одноразовое напоминание завтра в 09:00. Заменяет текущее расписание.','hour':'Одноразовое напоминание через час. Заменяет текущее расписание.'}[kind])
        w.quick_buttons[kind] = button
        quick.addWidget(button, 1)
    layout.addLayout(quick)
    edit = QPushButton('Настроить расписание')
    w.edit_reminder_button = edit
    edit.setIcon(duotone_icon('clock-clockwise','#58483B',20))
    edit.setIconSize(QSize(20,20))
    edit.clicked.connect(w.edit_reminder)
    layout.addWidget(edit)
    w.saved = QLabel('Выберите или создайте заметку')
    w.saved.setStyleSheet('color:#959ca5;font-size:11px;')
    layout.addWidget(w.saved)
    w.editor_stack=QStackedWidget()
    w.welcome=QWidget()
    welcome_layout=QVBoxLayout(w.welcome)
    welcome_layout.setContentsMargins(32,32,32,32)
    welcome_layout.addStretch()
    heading=QLabel('Выберите заметку')
    heading.setWordWrap(True)
    heading.setStyleSheet('font-size:25px;font-weight:600;')
    welcome_layout.addWidget(heading)
    explanation=QLabel('Откройте заметку из списка слева или создайте новую.')
    explanation.setWordWrap(True)
    welcome_layout.addWidget(explanation)
    cta=QPushButton('Создать заметку')
    cta.setToolTip('Новая заметка (Ctrl+N)')
    cta.setObjectName('createNote')
    cta.clicked.connect(w.new_note)
    welcome_layout.addWidget(cta,0,Qt.AlignLeft)
    welcome_layout.addStretch()
    w.editor_stack.addWidget(w.welcome)
    w.editor_scroll=QScrollArea()
    w.editor_scroll.setFocusPolicy(Qt.NoFocus)
    w.editor_scroll.setWidgetResizable(True)
    w.editor_scroll.setFrameShape(QScrollArea.NoFrame)
    w.editor_scroll.setWidget(w.editor)
    w.editor_stack.addWidget(w.editor_scroll)
    w.splitter.addWidget(w.editor_stack)
    w.panel = None
    w.splitter.setSizes([230,390,550])


class NoteCard(QWidget):
    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:
            if self.star.geometry().contains(event.position().toPoint()) and self.on_favorite:
                self.on_favorite()
            elif self.on_activate:
                self.on_activate()
            else:
                event.ignore()
                return
            event.accept()
            return
        event.ignore()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if not hasattr(self,'heading'): return
        # Account for both icon columns, margins and the two gaps exactly.
        width=max(1,self.width()-112)
        for label in self.text_labels:
            label.setFixedWidth(width)
        self.heading.setText(self.heading.fontMetrics().elidedText(self.full_title,Qt.ElideRight,width))
        words=self.full_plain.split()
        first=[]
        metrics=self.snippet.fontMetrics()
        while words and metrics.horizontalAdvance(' '.join(first+[words[0]]))<=width:
            first.append(words.pop(0))
        if not first and words: first.append(words.pop(0))
        second=metrics.elidedText(' '.join(words),Qt.ElideRight,width)
        self.snippet.setText(' '.join(first)+ ('\n'+second if second else ''))
        self.snippet.setFixedHeight(metrics.lineSpacing()*2)
        if hasattr(self,'when'):
            self.when.setText(self.when.fontMetrics().elidedText(self.full_reminder,Qt.ElideRight,width))


def card(title,plain,reminder,favorite,on_favorite=None,icon_name=None,on_activate=None):
    widget = NoteCard()
    widget.setObjectName('card')
    row = QHBoxLayout(widget)
    row.setContentsMargins(10,10,10,10)
    row.setSpacing(12)
    row.setSizeConstraint(QLayout.SetNoConstraint)
    symbol,color,bg=note_symbol(title,icon_name)
    tile = QLabel()
    tile.setPixmap(duotone_pixmap(symbol,color,24))
    tile.setObjectName('tile')
    tile.setStyleSheet(f'background:{bg};border-radius:12px;')
    tile.setAlignment(Qt.AlignCenter)
    tile.setFixedSize(38,38)
    row.addWidget(tile,0,Qt.AlignTop)
    column = QVBoxLayout()
    column.setSpacing(4)
    heading = QLabel(title)
    heading.setTextFormat(Qt.PlainText)
    heading.setObjectName('cardTitle')
    widget.setToolTip('<qt>'+escape(title + ('\n'+reminder if reminder else '')).replace('\n','<br>')+'</qt>')
    heading.setWordWrap(True)
    heading.setMaximumHeight(24)
    heading.setMinimumWidth(0)
    heading.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
    column.addWidget(heading)
    snippet = QLabel(plain.replace('\n',' ')[:85] or 'Пустая заметка')
    snippet.setTextFormat(Qt.PlainText)
    snippet.setObjectName('snippet')
    snippet.setWordWrap(False)
    snippet.setMinimumWidth(0)
    snippet.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
    column.addWidget(snippet)
    widget.heading=heading
    widget.full_title=title
    widget.snippet=snippet
    widget.full_plain=plain.replace('\n',' ')[:150] or 'Пустая заметка'
    widget.text_labels=[heading,snippet]
    if reminder:
        when = QLabel(reminder)
        when.setTextFormat(Qt.PlainText)
        when.setObjectName('cardReminder')
        when.setWordWrap(False)
        when.setMinimumWidth(0)
        when.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        column.addWidget(when)
        widget.when=when
        widget.full_reminder=reminder
        widget.text_labels.append(when)
    row.addLayout(column,1)
    star = QLabel()
    star.setPixmap(pixmap('star','#ff890b' if favorite else '#a0a7af',18,bool(favorite)))
    row.addWidget(star,0,Qt.AlignTop)
    star.setFixedWidth(18)
    widget.star=star
    widget.on_favorite=on_favorite
    widget.on_activate=on_activate
    star.setToolTip('Убрать из избранного' if favorite else 'Добавить в избранное')
    for child in widget.findChildren(QLabel):
        child.setAttribute(Qt.WA_TransparentForMouseEvents)
    widget.setAttribute(Qt.WA_TransparentForMouseEvents,on_favorite is None and on_activate is None)
    return widget
