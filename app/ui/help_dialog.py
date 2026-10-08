from app.utils.shortcuts import shortcut_label
"""Compact, nonmodal getting-started guide with no native title bar."""
from PySide6.QtCore import Qt,QPoint
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog,QFrame,QGraphicsDropShadowEffect,QGridLayout,QHBoxLayout,QLabel,QPushButton,QScrollArea,QSizePolicy,QVBoxLayout,QWidget)
from app.ui.icons import icon


class StyledDialog(QDialog):
    def __init__(self,title,subtitle,parent=None,symbol='note'):
        super().__init__(parent,Qt.Dialog|Qt.FramelessWindowHint|Qt.NoDropShadowWindowHint)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(420,340)
        self._drag_position=None
        self.setStyleSheet('''
            QWidget {font-family:"Segoe UI";font-size:14px;}
            QDialog {background:transparent;}
            QWidget#helpHeader {background:transparent;}
            QLabel#helpTitle {font-size:22px;font-weight:700;}
            QFrame#helpSurface {background:#fffdfa;border:1px solid #ece5db;border-radius:20px;}
            QFrame#helpCard {background:white;border:1px solid #eee8e0;border-radius:14px;}
            QLabel {color:#293648;background:transparent;border:none;}
            QLabel#helpSecondary {color:#6b7788;}
            QLabel#helpKey {background:#fff1df;color:#9d570f;border:1px solid #ffe0b8;border-radius:7px;padding:5px 9px;}
            QPushButton#helpClose {background:transparent;border:none;border-radius:9px;}
            QPushButton#helpClose:hover {background:#fff0df;}
            QPushButton#helpDone {background:#ff890b;color:white;border:none;border-radius:10px;padding:10px 24px;font-weight:600;}
            QPushButton#helpDone:hover {background:#ef7b06;}
            QScrollArea {background:transparent;border:none;}
            QScrollBar:vertical {background:transparent;width:8px;margin:0;}
            QScrollBar::handle:vertical {background:#d8d3cb;min-height:32px;border-radius:4px;}
            QScrollBar::handle:vertical:hover {background:#ffb164;}
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;border:none;}
            QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:transparent;}
        ''')
        outer=QVBoxLayout(self);outer.setContentsMargins(10,10,10,10)
        self.surface=QFrame();self.surface.setObjectName('helpSurface');outer.addWidget(self.surface)
        shadow=QGraphicsDropShadowEffect(self.surface);shadow.setBlurRadius(18);shadow.setOffset(0,3);shadow.setColor(Qt.lightGray);self.surface.setGraphicsEffect(shadow)
        layout=QVBoxLayout(self.surface);layout.setContentsMargins(20,20,20,16);layout.setSpacing(14)
        self.header=QWidget();self.header.setObjectName('helpHeader');header=QHBoxLayout(self.header);header.setContentsMargins(0,0,0,0);header.setSpacing(12)
        mark=QLabel();mark.setPixmap(icon(symbol,'#ff890b',36).pixmap(36,36));header.addWidget(mark,0,Qt.AlignTop)
        titles=QVBoxLayout();titles.setSpacing(4)
        title=self._label(title,bold=True);title.setObjectName('helpTitle')
        title_font=QFont(title.font());title_font.setPointSizeF(max(16,title_font.pointSizeF()*1.5));title.setFont(title_font)
        titles.addWidget(title);subtitle=self._label(subtitle);subtitle.setObjectName('helpSecondary');titles.addWidget(subtitle)
        header.addLayout(titles,1)
        self.close_button=QPushButton();self.close_button.setObjectName('helpClose');self.close_button.setIcon(icon('close','#737e8e',18));self.close_button.setFixedSize(34,34)
        self.close_button.setToolTip('Закрыть справку · Escape');self.close_button.setAccessibleName('Закрыть справку');self.close_button.clicked.connect(self.close)
        header.addWidget(self.close_button,0,Qt.AlignTop);layout.addWidget(self.header)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content=QWidget();self.content.setObjectName('helpContent');self.content.setStyleSheet('QWidget#helpContent {background:#fffdfa;}')
        self.cards=QVBoxLayout(self.content);self.cards.setContentsMargins(0,0,3,0);self.cards.setSpacing(10);self.body_layout=self.cards
        self.scroll.setWidget(self.content);layout.addWidget(self.scroll,1)
        footer=QHBoxLayout();self.footer_layout=footer;hint=self._label('Это окно не мешает работе с заметками.');hint.setObjectName('helpSecondary');footer.addWidget(hint,1)
        self.done_button=QPushButton('Понятно');self.done_button.setObjectName('helpDone');self.done_button.clicked.connect(self.close);footer.addWidget(self.done_button);layout.addLayout(footer)
        area=(parent.screen() if parent else self.screen()).availableGeometry()
        self.setMaximumSize(max(420,area.width()-24),max(340,area.height()-24))
        self.resize(min(640,self.maximumWidth()),min(760,self.maximumHeight()))

    @staticmethod
    def _label(text,bold=False):
        label=QLabel(text);label.setTextFormat(Qt.PlainText);label.setWordWrap(True);label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Minimum)
        if bold:
            font=QFont(label.font());font.setBold(True);label.setFont(font)
        return label

    def _card(self,symbol,title,items):
        card=QFrame();card.setObjectName('helpCard');layout=QVBoxLayout(card);layout.setContentsMargins(14,14,14,14);layout.setSpacing(8)
        heading=QHBoxLayout();heading.setSpacing(8);image=QLabel();image.setPixmap(icon(symbol,'#ef840e',22).pixmap(22,22));heading.addWidget(image,0,Qt.AlignTop);heading.addWidget(self._label(title,True),1);layout.addLayout(heading)
        for name,text in items:
            layout.addWidget(self._label(name,True));body=self._label(text);body.setObjectName('helpSecondary');layout.addWidget(body)
        self.cards.addWidget(card);return layout

    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton and self.header.geometry().translated(self.surface.pos()).contains(event.position().toPoint()):
            self._drag_position=event.globalPosition().toPoint()-self.frameGeometry().topLeft();event.accept()
        else:super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self._drag_position is not None and event.buttons()&Qt.LeftButton:
            self.move(event.globalPosition().toPoint()-self._drag_position);event.accept()
        else:super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        self._drag_position=None;super().mouseReleaseEvent(event)


class HelpDialog(StyledDialog):
    def __init__(self,parent=None):
        super().__init__('Быстрый старт','Заметки, планы и напоминания — спокойно и понятно.',parent)
        self._card('note','С чего начать',[
            ('Создайте заметку','Нажмите + или Ctrl+N. Название и текст сохраняются автоматически.'),
            ('Форматирование и списки','Выделенные кнопки B и I показывают активное форматирование. Повторное нажатие отключает его. Доступны чек-лист, точки и нумерация. Enter добавляет пункт; Enter в пустом пункте завершает список. Нажатие на квадрат отмечает дело.')])
        self._card('bell','Напоминания',[
            ('Одно время или гибкий повтор','Выберите дату либо дни недели и несколько времён. Например: Пн, Ср, Пт в 09:00 и 18:30. Затем нажмите «Сохранить».'),
            ('Выполнить или отложить','Ближайшее событие можно выполнить или отложить на 10 минут. Повторяющееся расписание при этом сохраняется.'),
            ('Если пропустили уведомление','Раздел «Требуют внимания» сохраняет события до выполнения или откладывания. Закрытие баннера и его доставка не означают выполнение задачи.'),
            ('После закрытия окна','Если фоновая служба настроена, напоминания продолжают работать и после выхода из приложения. Компьютер должен быть включён, а пользователь — войти в Windows.'),
            ('Если уведомление не пришло','Откройте «Состояние напоминаний» в меню приложения. Проверьте общий переключатель уведомлений Windows и разрешение для Orange Notes.')])
        self._card('folder','Порядок в заметках',[
            ('Папки и метки','Под названием видна текущая папка. Кнопка + рядом позволяет назначить, сменить или создать папку. Метки вводятся через запятую. Правой кнопкой по папке или метке слева можно открыть действия с ней.'),
            ('Значок заметки','Нажмите «Иконка» рядом с названием. Выберите значок или «Автоматически», чтобы вернуть подбор по названию.'),
            ('Мой день','Выберите заметки, которыми хотите заняться сегодня. В полночь список начнётся заново; сами заметки сохранятся.'),
            ('Архив и корзина','Архив убирает заметку из активного списка. Из корзины её можно восстановить; окончательное удаление требует подтверждения.')])
        self._card('search','Помощник и ваша память',[
            ('Без облака','Поиск по заметкам доступен без ИИ-комплекта. Для полноценного разговора нужен отдельный локальный комплект.'),
            ('Только выбранное пространство','Личные и рабочие заметки не смешиваются. Архив можно исключить, а корзина участвует только при явном выборе.')])
        shortcuts=self._card('laptop','Быстрые клавиши',[])
        grid=QGridLayout();grid.setHorizontalSpacing(14);grid.setVerticalSpacing(8)
        for index,(description,key) in enumerate([('Новая заметка','Ctrl+N'),('Поиск','Ctrl+F'),('Сохранить','Ctrl+S'),('Жирный / курсив','Ctrl+B / Ctrl+I'),('Копия заметки','Ctrl+Shift+D'),('Закрыть это окно','Esc')]):
            text=self._label(description);grid.addWidget(text,index,0)
            chip=self._label(shortcut_label(key));chip.setObjectName('helpKey');chip.setAlignment(Qt.AlignCenter);grid.addWidget(chip,index,1)
        grid.setColumnStretch(0,1);shortcuts.addLayout(grid)
        self.cards.addStretch()
