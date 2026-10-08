"""One tray icon, with an unread-event count (not a note count)."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont


def badge_text(count):
    return '99+' if count>99 else str(count)


def counted_icon(base,count):
    if count<=0: return base
    result=QIcon()
    for size in (16,22,32,48,64):
        pix=base.pixmap(size,size)
        if pix.isNull():
            pix=QPixmap(size,size);pix.fill(Qt.transparent)
        painter=QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        width=size*.68
        rect=QRectF(size-width,size-width,width,width)
        painter.setPen(Qt.NoPen);painter.setBrush(QColor('#d8491e'));painter.drawEllipse(rect)
        font=QFont('Segoe UI');font.setBold(True);font.setPixelSize(max(6,int(size*(.23 if count>99 else .30))))
        painter.setFont(font);painter.setPen(QColor('white'));painter.drawText(rect,Qt.AlignCenter,badge_text(count))
        painter.end();result.addPixmap(pix)
    return result
