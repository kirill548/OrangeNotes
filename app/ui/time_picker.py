"""Two-column wheel picker for local clock time."""
from PySide6.QtCore import Qt, QTime, Signal, QPoint, QEvent
from PySide6.QtGui import QPainter, QColor, QFont
from PySide6.QtWidgets import QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTimeEdit, QDateTimeEdit, QAbstractSpinBox, QLineEdit
from app.ui.icons import icon


class TimeWheel(QWidget):
    valueChanged = Signal(int)
    row_height = 40

    def __init__(self, count, value, label, parent=None):
        super().__init__(parent)
        self.count, self.value = count, value
        self._wheel_delta = 0
        self._drag_y = None
        self._dragged = False
        self.setFixedSize(110, 200)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(label)
        self.setToolTip('Прокрутите колесом мыши, перетащите или используйте ↑ и ↓')

    def setValue(self, value):
        value %= self.count
        if value != self.value:
            self.value = value
            self.valueChanged.emit(value)
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self.height() // 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#fff0df'))
        painter.drawRoundedRect(2, center-20, self.width()-4, 40, 12, 12)
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QColor('#ff890b'))
            painter.drawRoundedRect(2, center-20, self.width()-4, 40, 12, 12)
        for offset in range(-2, 3):
            painter.setFont(QFont('Segoe UI', 22 if offset == 0 else 17, QFont.DemiBold if offset == 0 else QFont.Normal))
            painter.setPen(QColor('#ed7926' if offset == 0 else '#828b98' if abs(offset) == 1 else '#bfc4cb'))
            painter.drawText(0, center-20+offset*40, self.width(), 40, Qt.AlignCenter, f'{(self.value+offset)%self.count:02}')

    def wheelEvent(self, event):
        self._wheel_delta += event.angleDelta().y() or event.pixelDelta().y()*4
        steps = int(self._wheel_delta/120)
        if steps:
            self.setValue(self.value-steps)
            self._wheel_delta -= steps*120
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setFocus()
            self._drag_y = event.position().y()
            self._dragged = False
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_y is not None:
            steps = int((self._drag_y-event.position().y())/self.row_height)
            if steps:
                self.setValue(self.value+steps)
                self._drag_y -= steps*self.row_height
                self._dragged = True
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_y is not None and event.button() == Qt.LeftButton:
            if not self._dragged:
                offset = round((event.position().y()-self.height()/2)/self.row_height)
                self.setValue(self.value+offset)
            self._drag_y = None
            event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            self.setValue(self.value+(-1 if event.key() == Qt.Key_Up else 1))
            event.accept()
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event); self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event); self.update()


class TimePopup(QDialog):
    def __init__(self, value, parent):
        super().__init__(parent, Qt.Popup)
        self.setWindowTitle('Выбор времени')
        self.setStyleSheet('QDialog{background:white;border:1px solid #e9e3dc;border-radius:16px;} QLabel{color:#737e8c;background:transparent;} QPushButton{padding:10px;border-radius:10px;background:#fff0df;color:#ed7926;border:0;} QPushButton#timeApply{background:#ff890b;color:white;font-weight:600;}')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16,16,16,16)
        layout.addWidget(QLabel('Прокрутите часы и минуты'))
        wheels = QHBoxLayout()
        self.hours = TimeWheel(24, value.hour(), 'Часы', self)
        self.minutes = TimeWheel(60, value.minute(), 'Минуты', self)
        for label, wheel in [('Часы',self.hours),('Минуты',self.minutes)]:
            column = QVBoxLayout()
            heading = QLabel(label); heading.setAlignment(Qt.AlignCenter)
            column.addWidget(heading); column.addWidget(wheel)
            wheels.addLayout(column)
        layout.addLayout(wheels)
        actions = QHBoxLayout()
        cancel = QPushButton('Отмена'); cancel.setAutoDefault(False); cancel.clicked.connect(self.reject)
        apply = QPushButton('Готово'); apply.setObjectName('timeApply'); apply.setDefault(True); apply.clicked.connect(self.accept)
        actions.addWidget(cancel); actions.addWidget(apply); layout.addLayout(actions)

    def selectedTime(self):
        return QTime(self.hours.value,self.minutes.value)


def pick_time(edit):
    popup = TimePopup(edit.time(), edit)
    popup.adjustSize()
    area = edit.screen().availableGeometry()
    anchor = edit.mapToGlobal(QPoint(0,edit.height()+4))
    x = max(area.left()+4,min(anchor.x(),area.right()-popup.width()-4))
    y = anchor.y()
    if y+popup.height()>area.bottom()-4:
        y = edit.mapToGlobal(QPoint(0,0)).y()-popup.height()-4
    popup.move(x,max(area.top()+4,min(y,area.bottom()-popup.height()-4)))
    popup.hours.setFocus()
    try:
        if popup.exec() == QDialog.Accepted:
            edit.setTime(popup.selectedTime())
    finally:
        popup.deleteLater()


class WheelTimeEdit(QTimeEdit):
    def __init__(self, value, parent=None):
        super().__init__(value,parent)
        self.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.setToolTip('Нажмите, чтобы выбрать часы и минуты прокруткой')
        self.lineEdit().installEventFilter(self)
        action=self.lineEdit().addAction(icon('chevron_down','#939ba5',16),QLineEdit.TrailingPosition)
        action.setToolTip('Выбрать время прокруткой')
        action.triggered.connect(lambda:pick_time(self))

    def eventFilter(self,watched,event):
        if watched is self.lineEdit() and event.type()==QEvent.MouseButtonPress and event.button()==Qt.LeftButton and not self.isReadOnly():
            self.setFocus()
            pick_time(self)
            return True
        return super().eventFilter(watched,event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.isReadOnly():
            pick_time(self); event.accept()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self,event):
        if not self.isReadOnly() and (event.key() == Qt.Key_Space or (event.key() == Qt.Key_Down and event.modifiers() & Qt.AltModifier)):
            pick_time(self)
        else:
            super().keyPressEvent(event)


class WheelDateTimeEdit(QDateTimeEdit):
    def __init__(self,value,parent=None):
        super().__init__(value,parent)
        self.lineEdit().installEventFilter(self)
        self.setToolTip('Нажмите на часы или минуты, чтобы выбрать время прокруткой')

    def eventFilter(self,watched,event):
        if watched is self.lineEdit() and event.type()==QEvent.MouseButtonPress and event.button()==Qt.LeftButton and not self.isReadOnly():
            # The reminder uses dd.MM.yyyy HH:mm: the time starts at character 11.
            position=self.lineEdit().cursorPositionAt(event.position().toPoint())
            if self.displayFormat()=='dd.MM.yyyy HH:mm' and position>=11:
                self.setCurrentSection(QDateTimeEdit.Section.HourSection if position<14 else QDateTimeEdit.Section.MinuteSection)
                self.setFocus()
                pick_time(self)
                return True
        return super().eventFilter(watched,event)

    def mousePressEvent(self,event):
        on_text = self.lineEdit().geometry().contains(event.position().toPoint())
        super().mousePressEvent(event)
        if on_text and event.button() == Qt.LeftButton and not self.isReadOnly() and self.currentSection() in (QDateTimeEdit.Section.HourSection,QDateTimeEdit.Section.MinuteSection):
            pick_time(self)

    def keyPressEvent(self,event):
        if not self.isReadOnly() and self.currentSection() in (QDateTimeEdit.Section.HourSection,QDateTimeEdit.Section.MinuteSection) and (event.key() == Qt.Key_Space or (event.key() == Qt.Key_Down and event.modifiers() & Qt.AltModifier)):
            pick_time(self)
        else:
            super().keyPressEvent(event)
