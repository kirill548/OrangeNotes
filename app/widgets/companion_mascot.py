"""A small bee companion, drawn locally with a bundled transparent illustration."""
import math
import time
from collections import deque
from app.utils.runtime_paths import resource_path
from PySide6.QtCore import Qt, QRect, QRectF, QSize, QPoint, QEvent, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QIcon, QPixmap, QFontMetrics
from PySide6.QtWidgets import QPushButton, QWidget, QLabel, QVBoxLayout


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
        super().__init__('Помощник\nЧат по заметкам',parent)
        self.setIcon(companion_icon());self.setIconSize(QSize(44,44))
        self.setMinimumHeight(68)
        self.setAccessibleName('Открыть ИИ-помощника по заметкам')
        self.setToolTip('Задайте вопрос о своих заметках или обсудите идею')
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('QPushButton {text-align:left;background:#fff5e9;border:1px solid #ffe0bb;border-radius:16px;padding:8px;color:#70451e;font-size:13px;} QPushButton:hover {background:#ffebd2;border-color:#ffad55;} QPushButton:focus {border:2px solid #ff890b;}')


class MiniCompanion(QWidget):
    """Bounded, draggable bee. Click/Enter opens chat; dragging never opens it."""
    activated=Signal()

    def __init__(self,parent):
        super().__init__(parent)
        self.setFixedSize(200,172)
        self.setAttribute(Qt.WA_StyledBackground,False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName('Мини-помощник. Нажмите Enter, чтобы открыть чат')
        self.setToolTip('Нажмите, чтобы поговорить. Перетащите, чтобы передвинуть.')
        self.setCursor(Qt.PointingHandCursor)
        self._start=None;self._origin=None;self._dragged=False;self._placed=False;self._blink=False
        self._bee=_bee_pixmap();self._phase=0.;self._recent_events=deque(maxlen=16)
        parent.installEventFilter(self)
        self.timer=QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self._animate)
        self._blink_timer=QTimer(self);self._blink_timer.setSingleShot(True);self._blink_timer.setInterval(150);self._blink_timer.timeout.connect(self._open_eyes)
        self._position_timer=QTimer(self);self._position_timer.setSingleShot(True);self._position_timer.timeout.connect(self.reposition)
        self._bubble_timer=QTimer(self);self._bubble_timer.setSingleShot(True);self._bubble_timer.setInterval(8000);self._bubble_timer.timeout.connect(self.bubble_expired)
        self.bubble=QLabel('Привет! Помочь?',self);self.bubble.setGeometry(1,0,198,47)
        self.bubble.setTextFormat(Qt.PlainText);self.bubble.setWordWrap(True)
        self.bubble.setAlignment(Qt.AlignCenter);self.bubble.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.bubble.setStyleSheet('QLabel {background:#fff8ed;border:1px solid #ffddb1;border-radius:12px;color:#795027;font-size:12px;}')
        self.dismiss=QPushButton('×',self);self.dismiss.setGeometry(178,49,22,22);self.dismiss.setAccessibleName('Скрыть мини-помощника');self.dismiss.setToolTip('Скрыть персонажа. Чат останется доступен в боковой панели.');self.dismiss.clicked.connect(self.hide)
        self.dismiss.setStyleSheet('QPushButton {background:#fff8ed;border:1px solid #ffddb1;border-radius:11px;color:#795027;padding:0;}')
        self._layout_bubble()
        self.show();self._position_timer.start(0);self._bubble_timer.start()

    def _layout_bubble(self):
        self.bubble.ensurePolished()
        bounds=QFontMetrics(self.bubble.font()).boundingRect(QRect(0,0,self.bubble.width()-12,1000),Qt.TextWordWrap,self.bubble.text())
        height=max(47,bounds.height()+8)
        self.bubble.setFixedHeight(height)
        self.dismiss.move(178,height+2)
        self.setFixedHeight(height+125)
        self.reposition()

    def reposition(self):
        parent=self.parentWidget()
        if not parent:return
        point=self.pos() if self._placed else QPoint(parent.width()-self.width()-24,parent.height()-self.height()-24)
        self.move(max(0,min(point.x(),max(0,parent.width()-self.width()))),max(0,min(point.y(),max(0,parent.height()-self.height()))))
        self.raise_()

    def eventFilter(self,watched,event):
        if watched is self.parentWidget() and event.type() in (QEvent.Resize,QEvent.Show):self.reposition()
        return super().eventFilter(watched,event)

    def _animate(self):
        self._phase=(self._phase+.16)%(math.pi*2);self._blink=True;self.update();self._blink_timer.start()

    def _open_eyes(self):self._blink=False;self.update()

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.setRenderHint(QPainter.SmoothPixmapTransform,False)
        p.translate(40,self.bubble.height()+3+math.sin(self._phase)*3)
        if not self._bee.isNull():
            scaled=self._bee.scaled(120,120,Qt.KeepAspectRatio,Qt.FastTransformation)
            p.drawPixmap((120-scaled.width())//2,(120-scaled.height())//2,scaled)
        else:p.save();p.scale(2.5,2.5);_draw_bee(p);p.restore()
        if self.hasFocus():p.setPen(QPen(QColor('#ff890b'),2));p.setBrush(Qt.NoBrush);p.drawRoundedRect(QRectF(0,0,120,118),16,16)

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
        super().showEvent(event);self.timer.start()

    def hideEvent(self,event):
        self.timer.stop();self._blink_timer.stop();self._bubble_timer.stop();super().hideEvent(event)

    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:self._start=event.globalPosition().toPoint();self._origin=self.pos();self._dragged=False;self.setFocus()

    def mouseMoveEvent(self,event):
        if self._start is None:return
        offset=event.globalPosition().toPoint()-self._start
        if offset.manhattanLength()>6:self._dragged=True;self._placed=True;self.move(self._origin+offset);self.reposition()

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton and self._start is not None:
            if not self._dragged:self.activated.emit()
            self._start=None

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key_Return,Qt.Key_Enter,Qt.Key_Space):self.activated.emit();event.accept()
        else:super().keyPressEvent(event)
