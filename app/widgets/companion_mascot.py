"""A small bee companion, drawn locally with a bundled transparent illustration."""
import math
import time
from collections import deque
from weakref import ref
from app.ui.icons import duotone_icon
from app.utils.runtime_paths import resource_path
from PySide6.QtCore import Qt, QRect, QRectF, QSize, QPoint, QEvent, QTimer, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPen, QIcon, QPixmap, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import QApplication, QPushButton, QWidget, QLabel, QMenu, QStyle, QStyleOptionButton


def _bee_pixmap():
    return QPixmap(str(resource_path('app/assets/bee_companion_v2.png')))


def _draw_bee(painter):
    """Friendly vector fallback while a bundled illustration is unavailable."""
    painter.setPen(QPen(QColor('#62442b'),1.5));painter.setBrush(QColor('#eaf5ff'))
    painter.drawEllipse(QRectF(7,5,20,23));painter.drawEllipse(QRectF(24,4,18,24))
    painter.setBrush(QColor('#ffcf45'));painter.drawEllipse(QRectF(7,14,35,29))
    painter.setPen(QPen(QColor('#60402d'),5));painter.drawArc(QRectF(13,14,22,29),70*16,215*16)
    painter.setPen(Qt.NoPen);painter.setBrush(QColor('#ffe087'));painter.drawEllipse(QRectF(8,13,23,26))
    painter.setBrush(QColor('#473326'));painter.drawEllipse(QRectF(14,22,4,7));painter.drawEllipse(QRectF(24,22,4,7))
    painter.setBrush(QColor('white'));painter.drawEllipse(QRectF(15,23,1.5,2));painter.drawEllipse(QRectF(25,23,1.5,2))
    painter.setPen(QPen(QColor('#62442b'),1.3));painter.drawArc(QRectF(18,28,8,5),185*16,170*16)
    painter.drawLine(15,15,12,9);painter.drawLine(24,14,27,8)


def companion_icon(size=48):
    bee=_bee_pixmap()
    if not bee.isNull():return QIcon(bee)
    image=QPixmap(size,size);image.fill(Qt.transparent)
    painter=QPainter(image);painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size/48,size/48)
    _draw_bee(painter)
    painter.end();return QIcon(image)


class CompanionLauncher(QPushButton):
    """Root window can place this launcher in its navigation column."""
    def __init__(self,parent=None):
        super().__init__('Помощник',parent)
        self.setIcon(companion_icon());self.setIconSize(QSize(28,28))
        self.setMinimumHeight(44)
        self.setAccessibleName('Открыть ИИ-помощника по заметкам')
        self.setToolTip('Задайте вопрос о своих заметках или обсудите идею')
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('QPushButton {text-align:left;background:transparent;border:1px solid #e8dfd4;border-radius:8px;padding:6px 8px;color:#293648;font-size:13px;} QPushButton:hover {background:#ffebd2;border-color:#ffad55;} QPushButton:focus {border:2px solid #ff890b;}')

    def sizeHint(self):
        # Native button hints can measure a multiline label as one long line.
        # Measure the displayed lines separately while retaining style padding.
        self.ensurePolished()
        metrics = self.fontMetrics()
        lines = self.text().split('\n')
        content = QSize(max(metrics.horizontalAdvance(line) for line in lines),
                        metrics.height() * len(lines))
        if not self.icon().isNull():
            content.setWidth(content.width() + self.iconSize().width() + 4)
            content.setHeight(max(content.height(), self.iconSize().height()))
        option = QStyleOptionButton()
        self.initStyleOption(option)
        return self.style().sizeFromContents(QStyle.CT_PushButton, option, content, self).expandedTo(self.minimumSize())


class MiniCompanion(QWidget):
    """Non-activating desktop pet. Only a click opens the chat."""
    activated=Signal()
    dismissRequested=Signal()

    def __init__(self,parent):
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
        super().__init__(None, flags)
        self._owner = ref(parent)
        self.setFixedWidth(200)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setAccessibleName('Питомец Orange Notes')
        self.setToolTip('Нажмите, чтобы поговорить. Перетащите, чтобы передвинуть.')
        self.setCursor(Qt.PointingHandCursor)
        self._start=None;self._origin=None;self._dragged=False;self._placed=False
        self._enabled=False
        self._bee=_bee_pixmap()
        if not self._bee.isNull():
            self._bee = self._bee.scaled(120, 120, Qt.KeepAspectRatio, Qt.FastTransformation)
        self._idle_since = time.monotonic()
        self._bank = 0
        self._recent_events=deque(maxlen=16)
        parent.installEventFilter(self)
        parent.destroyed.connect(self._dispose)
        self.timer=QTimer(self);self.timer.setInterval(40);self.timer.timeout.connect(self.update)
        self._flight = QVariantAnimation(self)
        self._flight.setStartValue(0.0)
        self._flight.setEndValue(0.0)
        self._flight.setEasingCurve(QEasingCurve.OutCubic)
        self._flight.valueChanged.connect(lambda _: self.update())
        self._position_timer=QTimer(self);self._position_timer.setSingleShot(True);self._position_timer.timeout.connect(self.reposition)
        self._bubble_timer=QTimer(self);self._bubble_timer.setSingleShot(True);self._bubble_timer.setInterval(8000);self._bubble_timer.timeout.connect(self.bubble_expired)
        self.bubble=QLabel('Привет! Помочь?',self);self.bubble.setGeometry(1,0,198,47)
        self.bubble.setTextFormat(Qt.PlainText);self.bubble.setWordWrap(True)
        self.bubble.setAlignment(Qt.AlignCenter);self.bubble.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.bubble.setStyleSheet('QLabel {background:#fff8ed;border:1px solid #ffddb1;border-radius:12px;color:#795027;font-size:12px;}')
        self._pet_menu = QMenu(self)
        self._pet_menu.addAction(duotone_icon('eye-slash', '#58483B', 18), 'Скрыть питомца', self._dismiss)
        self._layout_bubble()
        self._position_timer.start(0)

    def _layout_bubble(self):
        self.bubble.ensurePolished()
        bounds=QFontMetrics(self.bubble.font()).boundingRect(QRect(0,0,self.bubble.width()-12,1000),Qt.TextWordWrap,self.bubble.text())
        height=max(47,bounds.height()+8)
        self.bubble.setFixedHeight(height)
        self.setFixedHeight(height+140)
        self.reposition()

    def reposition(self):
        owner = self._owner()
        if owner is None:
            return
        screen = QGuiApplication.screenAt(self.geometry().center()) if self._placed else None
        screen = screen or owner.screen()
        bounds = screen.availableGeometry()
        point = self.pos() if self._placed else QPoint(bounds.right()-self.width()-23,
                                                       bounds.bottom()-self.height()-23)
        self._move_on_screen(point, bounds)

    def _move_on_screen(self, point, bounds):
        right = max(bounds.left(), bounds.right() - self.width() + 1)
        bottom = max(bounds.top(), bounds.bottom() - self.height() + 1)
        self.move(max(bounds.left(), min(point.x(), right)),
                  max(bounds.top(), min(point.y(), bottom)))

    def set_pet_visible(self, visible):
        self._enabled = visible
        self._sync_visibility()

    def _dismiss(self):
        self.set_pet_visible(False)
        self.dismissRequested.emit()

    def contextMenuEvent(self, event):
        self._pet_menu.popup(event.globalPos())
        event.accept()

    def _sync_visibility(self):
        owner = self._owner()
        if self._enabled and owner is not None and (owner.isVisible() or owner.isMinimized()):
            self.reposition()
            self.show()
        else:
            self.hide()

    def _dispose(self):
        self._position_timer.stop()
        self.hide()
        self.deleteLater()

    def eventFilter(self,watched,event):
        if watched is self._owner():
            if event.type() in (QEvent.Hide, QEvent.WindowStateChange, QEvent.Show):
                self._sync_visibility()
            elif event.type() == QEvent.ScreenChangeInternal:
                self.reposition()
        return super().eventFilter(watched,event)

    def _animate_flight(self, destination, duration):
        progress = self._flight.currentValue()
        self._flight.stop()
        self._flight.setStartValue(progress)
        self._flight.setEndValue(destination)
        self._flight.setDuration(duration)
        self._flight.start()

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.setRenderHint(QPainter.SmoothPixmapTransform,False)
        phase = (time.monotonic() - self._idle_since) * 2.8
        lift = self._flight.currentValue()
        p.translate(100 + math.sin(phase / 2) * 2,
                    self.bubble.height() + 71 + math.sin(phase) * 3 - lift * 10)
        p.rotate(math.sin(phase / 2) * 2 + self._bank * lift)
        p.translate(-60, -60)
        if not self._bee.isNull():
            p.drawPixmap((120-self._bee.width())//2,(120-self._bee.height())//2,self._bee)
        else:p.save();p.scale(2.5,2.5);_draw_bee(p);p.restore()

    def notify_event(self,text,kind='info'):
        """Present real app events; bounded dedupe prevents autosave chatter."""
        text=' '.join(str(text or '').split())[:240]
        if not text:return False
        now=time.monotonic();key=(kind,text)
        if any(old==key and now-stamp<30 for old,stamp in self._recent_events):return False
        self._recent_events.append((key,now))
        if not self.isVisible():return False
        display=text if len(text)<=56 else text[:53].rstrip()+'…'
        self.bubble.setText(display);self.bubble.setToolTip(text);self._layout_bubble();self.bubble.show();self._bubble_timer.start();return True

    def bubble_expired(self):self.bubble.hide()

    def showEvent(self,event):
        super().showEvent(event)
        self.timer.start()
        if not self.bubble.isHidden():
            self._bubble_timer.start()

    def hideEvent(self,event):
        self._start = None
        self.setCursor(Qt.PointingHandCursor)
        self._pet_menu.hide()
        self._flight.stop()
        self._flight.setStartValue(0.0)
        self._flight.setEndValue(0.0)
        self.timer.stop();self._bubble_timer.stop();super().hideEvent(event)

    def mousePressEvent(self,event):
        if event.button() == Qt.LeftButton:
            self._start = event.globalPosition().toPoint()
            self._origin = self.pos()
            self._dragged = False
            event.accept()

    def mouseMoveEvent(self,event):
        if self._start is None:
            return
        cursor = event.globalPosition().toPoint()
        offset = cursor - self._start
        if self._dragged or offset.manhattanLength() >= QApplication.startDragDistance():
            if not self._dragged:
                self._bank = -6 if offset.x() < 0 else 6
                self._animate_flight(1.0, 160)
            self._dragged = True
            self._placed = True
            self.setCursor(Qt.ClosedHandCursor)
            screen = QGuiApplication.screenAt(cursor) or self.screen()
            self._move_on_screen(self._origin + offset, screen.availableGeometry())

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton and self._start is not None:
            self.mouseMoveEvent(event)
            self._start = None
            self.setCursor(Qt.PointingHandCursor)
            if self._dragged:
                self._animate_flight(0.0, 260)
            else:
                self.activated.emit()
            event.accept()
