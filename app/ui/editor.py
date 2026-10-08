from PySide6.QtCore import Qt, QRectF, QPoint
from PySide6.QtGui import QTextCursor, QTextListFormat, QPainter, QPen, QColor, QTextCharFormat
from PySide6.QtWidgets import QTextEdit


class NoteEditor(QTextEdit):
    """Plain checklist markers with normal rich text and undo behaviour."""

    def setPlainText(self, text):
        self.setCurrentCharFormat(QTextCharFormat())
        super().setPlainText(text)

    def selected_blocks(self):
        cursor = self.textCursor()
        block = self.document().findBlock(cursor.selectionStart())
        blocks = []
        while block.isValid():
            blocks.append(block)
            block = block.next()
            if not cursor.hasSelection() or not block.isValid() or block.position() >= cursor.selectionEnd(): break
        return blocks

    def toggle_list(self, style):
        if self.isReadOnly(): return
        original = self.textCursor()
        blocks = self.selected_blocks()
        remove = all(b.textList() and b.textList().format().style() == style for b in blocks)
        original.beginEditBlock()
        for block in blocks:
            cursor = QTextCursor(block)
            if block.text().startswith(('☐ ', '☑ ')):
                cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, 2)
                cursor.removeSelectedText()
            if block.textList(): block.textList().remove(block)
            fmt = cursor.blockFormat()
            fmt.setIndent(0)
            cursor.setBlockFormat(fmt)
        if not remove:
            selection = QTextCursor(blocks[0])
            selection.setPosition(blocks[-1].position()+blocks[-1].length()-1, QTextCursor.KeepAnchor)
            fmt = QTextListFormat()
            fmt.setStyle(style)
            fmt.setIndent(1)
            selection.createList(fmt)
        original.endEditBlock()
        self.setTextCursor(original)
        self.setFocus()

    def mousePressEvent(self, event):
        if not self.isReadOnly() and event.button() == Qt.LeftButton:
            cursor = self.cursorForPosition(event.position().toPoint())
            block = cursor.block()
            if block.text().startswith(('☐ ', '☑ ')):
                start = QTextCursor(block)
                end = QTextCursor(block)
                end.movePosition(QTextCursor.Right)
                left, right = self.cursorRect(start), self.cursorRect(end)
                point = event.position().toPoint()
                if left.top() <= point.y() <= left.bottom() and left.x() - 2 <= point.x() <= right.x() + 2:
                    start.beginEditBlock()
                    start.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
                    start.insertText('☑' if block.text().startswith('☐') else '☐')
                    start.endEditBlock()
                    start.movePosition(QTextCursor.Right)
                    self.setTextCursor(start)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        block = self.cursorForPosition(QPoint(0,0)).block()
        while block.isValid():
            if self.cursorRect(QTextCursor(block)).top() > self.viewport().height(): break
            if block.text().startswith(('☐ ', '☑ ')):
                start = QTextCursor(block)
                rect = self.cursorRect(start)
                end = QTextCursor(block)
                end.movePosition(QTextCursor.Right)
                width = self.cursorRect(end).x()-rect.x()
                if rect.bottom() >= 0 and rect.top() <= self.viewport().height():
                    painter.fillRect(QRectF(rect.x(),rect.y(),width,rect.height()),self.palette().base())
                    size = min(width-2,18,rect.height()-4)
                    box = QRectF(rect.x()+1,rect.y()+(rect.height()-size)/2,size,size)
                    checked = block.text().startswith('☑')
                    painter.setPen(QPen(QColor('#ff8a00' if checked else '#9ba5b1'),1.5))
                    painter.setBrush(QColor('#ff8a00') if checked else self.palette().base())
                    painter.drawRoundedRect(box,3,3)
                    if checked:
                        painter.setPen(QPen(QColor('white'),1.7))
                        painter.drawLine(box.left()+2,box.center().y(),box.center().x()-1,box.bottom()-2)
                        painter.drawLine(box.center().x()-1,box.bottom()-2,box.right()-2,box.top()+2)
            block = block.next()
        painter.end()

    def keyPressEvent(self, event):
        cursor = self.textCursor()
        if (not self.isReadOnly() and not cursor.hasSelection() and cursor.block().textList()
                and not cursor.block().text().strip() and event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() == Qt.NoModifier):
            cursor.beginEditBlock()
            cursor.block().textList().remove(cursor.block())
            fmt = cursor.blockFormat()
            fmt.setIndent(0)
            cursor.setBlockFormat(fmt)
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            event.accept()
            return
        if (not self.isReadOnly() and not cursor.hasSelection()
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and not event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier)
                and cursor.block().text().startswith(('☐ ', '☑ '))
                and cursor.positionInBlock() >= 2):
            cursor.beginEditBlock()
            if not cursor.block().text()[2:].strip():
                cursor.movePosition(QTextCursor.StartOfBlock)
                cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
                self.setTextCursor(cursor)
            else:
                super().keyPressEvent(event)
                self.textCursor().insertText('☐ ')
            cursor.endEditBlock()
            event.accept()
            return
        super().keyPressEvent(event)
