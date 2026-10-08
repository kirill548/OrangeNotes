from PySide6.QtCore import QDateTime, QTime, Qt, QLocale, QTimer
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QDateTimeEdit, QCheckBox, QPushButton, QTimeEdit, QMessageBox, QLabel, QCalendarWidget, QWidget, QScrollArea
from app.ui.icons import icon, pixmap
from PySide6.QtGui import QPalette, QColor, QTextCharFormat
from app.ui.time_picker import WheelTimeEdit, WheelDateTimeEdit


class DayCheckBox(QCheckBox):
    def hitButton(self,position):
        return self.rect().contains(position)


class ReminderDialog(QDialog):
    def __init__(self, reminder, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Расписание напоминаний')
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16,20,16,16)
        layout.setSpacing(12)
        header = QHBoxLayout()
        heading = QLabel('Напоминание')
        heading.setStyleSheet('font-size:17px;font-weight:600;')
        header.addWidget(heading,1)
        close = QPushButton()
        close.setIcon(icon('close','#838b95',18))
        close.setFixedSize(30,30)
        close.setToolTip('Закрыть расписание · Escape')
        close.setAccessibleName('Закрыть расписание')
        close.clicked.connect(self.reject)
        header.addWidget(close)
        layout.addLayout(header)
        from app.utils.timezones import device_zone
        self.timezone_hint=QLabel('Часовой пояс расписания: '+str((reminder or {}).get('timezone_id') or device_zone())+'\nПосле смены пояса расписание сохранит это местное время.')
        self.timezone_hint.setWordWrap(True)
        self.timezone_hint.setToolTip('При повторяющемся часе — одно событие в первый момент (fold=0). Несуществующее время переносится на первую допустимую минуту.')
        self.timezone_hint.setStyleSheet('color:#737e8c;font-size:11px;')
        layout.addWidget(self.timezone_hint)
        outer = layout
        scroll = QScrollArea()
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(12)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self.date_section=self.section(layout,'calendar','Дата и время')
        self.mode = QComboBox()
        self.mode.addItems(['Отключено', 'Один раз', 'Ежедневно', 'По дням недели'])
        self.mode.setAccessibleName('Тип напоминания')
        self.mode.setToolTip('Одноразовое напоминание или повторяющееся расписание')
        self.mode.setCurrentIndex(1)
        self.date = WheelDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat('dd.MM.yyyy HH:mm')
        self.date.setAccessibleName('Дата и время одноразового напоминания')
        layout.addWidget(self.date)
        self.calendar = QCalendarWidget()
        self.calendar.setLocale(QLocale(QLocale.Russian))
        self.calendar.setFirstDayOfWeek(Qt.Monday)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.calendar.setGridVisible(False)
        palette=QPalette()
        for role,color in [(QPalette.Window,'#ffffff'),(QPalette.Base,'#ffffff'),(QPalette.AlternateBase,'#ffffff'),(QPalette.Text,'#17202c'),(QPalette.WindowText,'#17202c'),(QPalette.Button,'#ffffff'),(QPalette.ButtonText,'#17202c'),(QPalette.Highlight,'#ff890b'),(QPalette.HighlightedText,'#ffffff')]:
            palette.setColor(role,QColor(color))
        self.calendar.setPalette(palette)
        header_format=QTextCharFormat()
        header_format.setForeground(QColor('#78818e'))
        header_format.setBackground(QColor('#ffffff'))
        for day in [Qt.Monday,Qt.Tuesday,Qt.Wednesday,Qt.Thursday,Qt.Friday,Qt.Saturday,Qt.Sunday]:
            self.calendar.setWeekdayTextFormat(day,header_format)
        self.calendar.setMinimumWidth(260)
        self.calendar.setMaximumHeight(240)
        self.calendar.selectionChanged.connect(lambda: self.date.setDate(self.calendar.selectedDate()))
        self.date.dateChanged.connect(self.calendar.setSelectedDate)
        layout.addWidget(self.calendar)
        self.section(layout,'repeat','Повтор')
        layout.addWidget(self.mode)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.day_section=QLabel('Дни недели')
        layout.addWidget(self.day_section)
        self.day_container=QWidget()
        self.day_box = QHBoxLayout(self.day_container)
        self.day_box.setContentsMargins(0,0,0,0)
        self.day_box.setSpacing(4)
        self.days = []
        for name in ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']:
            cb = DayCheckBox(name)
            cb.setObjectName('day')
            cb.setAccessibleName('Повторять: '+name)
            self.days.append(cb)
            self.day_box.addWidget(cb)
        layout.addWidget(self.day_container)
        self.time_section=self.section(layout,'clock','Время')
        self.time_box = QVBoxLayout()
        layout.addLayout(self.time_box)
        self.times = []
        self.scroll_timer = QTimer(self)
        self.scroll_timer.setSingleShot(True)
        self.scroll_timer.timeout.connect(self.scroll_to_last_time)
        self.add_button = QPushButton('+ Добавить время')
        self.add_button.clicked.connect(self.add_next_time)
        outer.addWidget(self.add_button)
        layout.addStretch()
        save = QPushButton('Сохранить')
        save.setObjectName('primary')
        save.setAccessibleName('Сохранить расписание')
        save.clicked.connect(self.validate)
        outer.addWidget(save)
        if reminder:
            self.mode.setCurrentIndex(1 if reminder['mode']=='once' else (2 if len(reminder['days'])==7 else 3))
            if not reminder['enabled']:
                self.mode.setCurrentIndex(0)
            if reminder['once_at']:
                self.date.setDateTime(QDateTime.fromString(reminder['once_at'], 'yyyy-MM-ddTHH:mm:ss'))
            for i in reminder['days']:
                self.days[i].setChecked(True)
        for value in (reminder['times'] if reminder and reminder['times'] else ['09:00']):
            self.add_time(value)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.calendar.setSelectedDate(self.date.date())
        self.update_mode()
        self.initial_values=self.values()
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    def has_changes(self):
        return self.values()!=self.initial_values

    def discard_and_reject(self):
        """Close after a caller has already confirmed discarding changes."""
        super().reject()

    def reject(self):
        if hasattr(self,'initial_values') and self.has_changes():
            answer=QMessageBox.question(self,'Расписание не сохранено',
                'Сохранить изменения расписания?',
                QMessageBox.Save|QMessageBox.Discard|QMessageBox.Cancel,QMessageBox.Save)
            if answer==QMessageBox.Cancel:
                return
            if answer==QMessageBox.Save:
                self.validate()
                return
        self.discard_and_reject()

    def closeEvent(self,event):
        self.reject()
        event.setAccepted(not self.isVisible())

    def section(self,layout,symbol,text):
        container=QWidget()
        row=QHBoxLayout(container)
        row.setContentsMargins(0,0,0,0)
        image=QLabel()
        image.setPixmap(pixmap(symbol,'#17202c',20))
        image.setFixedWidth(24)
        row.addWidget(image)
        row.addWidget(QLabel(text),1)
        layout.addWidget(container)
        return container

    def add_time(self, value):
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        edit = WheelTimeEdit(QTime.fromString(value, 'HH:mm'))
        edit.setDisplayFormat('HH:mm')
        edit.setAccessibleName('Время повторяющегося напоминания')
        edit.setToolTip('Нажмите на время: выберите часы и минуты прокруткой. Все времена применяются к выбранным дням.')
        remove = QPushButton()
        remove.setIcon(icon('close','#838b95',16))
        remove.setToolTip('Удалить время')
        remove.setAccessibleName('Удалить время '+value)
        remove.setFixedWidth(35)
        remove.setAutoDefault(False)
        row.addWidget(edit)
        row.addWidget(remove)
        self.time_box.addWidget(container)
        self.times.append(edit)
        edit.setEnabled(self.mode.currentIndex()>=2)
        container.setVisible(self.mode.currentIndex()>=2)
        def delete():
            if edit not in self.times:
                return
            self.times.remove(edit)
            self.time_box.removeWidget(container)
            container.deleteLater()
        remove.clicked.connect(delete)
        remove.setEnabled(self.mode.currentIndex()>=2)

    def add_next_time(self):
        if self.mode.currentIndex()<2:
            self.mode.setFocus()
            self.mode.showPopup()
            return
        used = {edit.time().toString('HH:mm') for edit in self.times}
        candidate = self.times[-1].time().addSecs(3600) if self.times else QTime(9,0)
        for _ in range(1440):
            if candidate.toString('HH:mm') not in used:
                self.add_time(candidate.toString('HH:mm'))
                self.times[-1].setFocus()
                self.scroll_timer.start(0)
                return
            candidate = candidate.addSecs(60)
        QMessageBox.information(self, 'Расписание', 'Уже добавлены все 1440 возможных времён суток.')

    def scroll_to_last_time(self):
        if self.times:
            self.scroll.ensureWidgetVisible(self.times[-1])

    def update_mode(self):
        index = self.mode.currentIndex()
        self.date.setEnabled(index==1)
        self.calendar.setEnabled(index==1)
        for widget in [self.date_section,self.date,self.calendar]: widget.setVisible(index==1)
        for widget in [self.day_section,self.day_container]: widget.setVisible(index==3)
        self.time_section.setVisible(index>=2)
        self.hint.setText(['Напоминания для этой заметки отключены.', 'Одно уведомление в выбранную дату и время. Для нескольких времён выберите повтор.', 'Каждый день, включая выходные, во все указанные времена.', 'Все времена применяются к каждому выбранному дню недели.'][index])
        self.add_button.setText('+ Добавить время' if index>=2 else 'Настроить повтор…')
        self.add_button.setAccessibleName(self.add_button.text().lstrip('+ '))
        self.add_button.setToolTip('Добавить ещё одно ежедневное время' if index==2 else 'Добавить время для выбранных дней недели' if index==3 else 'Выбрать ежедневный повтор или дни недели')
        for cb in self.days:
            cb.setEnabled(index==3)
            if index==2: cb.setChecked(True)
        self.add_button.setEnabled(index!=0)
        for edit in self.times:
            edit.setEnabled(index>=2)
            edit.parentWidget().setVisible(index>=2)
            for button in edit.parentWidget().findChildren(QPushButton):
                button.setEnabled(index>=2)

    def validate(self):
        index = self.mode.currentIndex()
        if index==1 and self.date.dateTime() <= QDateTime.currentDateTime():
            QMessageBox.warning(self, 'Дата', 'Выберите дату и время в будущем.')
            return
        if index>=2 and (not self.times or (index==3 and not any(cb.isChecked() for cb in self.days))):
            QMessageBox.warning(self, 'Расписание', 'Выберите дни и добавьте хотя бы одно время.')
            return
        values = [edit.time().toString('HH:mm') for edit in self.times]
        if index>=2 and len(set(values)) != len(values):
            QMessageBox.warning(self, 'Расписание', 'Времена повторяются. Измените или удалите повторяющееся время.')
            return
        self.accept()

    def values(self):
        index = self.mode.currentIndex()
        return (None if index==0 else 'once' if index==1 else 'repeat', self.date.dateTime().toString('yyyy-MM-ddTHH:mm:ss') if index==1 else None, list(range(7)) if index==2 else [i for i,cb in enumerate(self.days) if cb.isChecked()], sorted(e.time().toString('HH:mm') for e in self.times) if index>=2 else [])
