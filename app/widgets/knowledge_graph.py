"""Workspace-scoped knowledge UI; SQLite reads and indexing run off the GUI thread."""
import math
import re
from PySide6.QtCore import QObject, QThread, Signal, Qt, QEvent, QStringListModel
from PySide6.QtGui import QColor, QBrush, QPen, QTextCursor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QGraphicsView, QGraphicsScene, QCompleter


class KnowledgeWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, path, note_id, workspace_id, parent=None):
        super().__init__(parent)
        self.path, self.note_id, self.workspace_id = path, note_id, workspace_id

    def run(self):
        graph = None
        try:
            from app.services.knowledge_graph import KnowledgeGraph
            graph = KnowledgeGraph(self.path)
            if hasattr(graph, 'reindex_workspace'):
                graph.reindex_workspace(self.workspace_id)
            data = {'workspace_id': self.workspace_id, 'note_id': self.note_id,
                    'graph': graph.graph(self.workspace_id)}
            for name in ('backlinks', 'linked_notes', 'unlinked_mentions'):
                data[name] = [dict(row) for row in getattr(graph, name)(self.note_id, self.workspace_id)] if self.note_id else []
            self.ready.emit(data)
        except Exception:
            self.failed.emit('Не удалось обновить связи заметок. Попробуйте ещё раз.')
        finally:
            if graph is not None and hasattr(graph, 'close'):
                graph.close()


class BacklinksPanel(QWidget):
    note_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.heading = QLabel('Обратные ссылки')
        layout.addWidget(self.heading)
        self.links = QListWidget()
        self.links.setMaximumHeight(160)
        self.links.itemActivated.connect(self._open)
        self.links.itemClicked.connect(self._open)
        layout.addWidget(self.links)
        self.mentions_label = QLabel('Упоминания без ссылки')
        layout.addWidget(self.mentions_label)
        self.mentions = QListWidget()
        self.mentions.setMaximumHeight(100)
        self.mentions.itemActivated.connect(self._open)
        self.mentions.itemClicked.connect(self._open)
        layout.addWidget(self.mentions)

    def _open(self, item):
        value = item.data(Qt.UserRole)
        if value is not None:
            self.note_requested.emit(value)

    def update_links(self, rows, mentions=()):
        self.heading.setText('Обратные ссылки · ' + str(len(rows)))
        for widget, entries in ((self.links, rows), (self.mentions, mentions)):
            widget.clear()
            for row in entries:
                title = row.get('title') or row.get('source_title') or 'Без названия'
                snippet = row.get('context_snippet', '')
                item = QListWidgetItem(title + ('\n' + snippet if snippet else ''))
                item.setData(Qt.UserRole, row.get('id', row.get('source_id', row.get('note_id'))))
                widget.addItem(item)
            if not entries:
                item = QListWidgetItem('Пока нет')
                item.setFlags(Qt.NoItemFlags)
                widget.addItem(item)
        self.mentions_label.setVisible(bool(mentions))
        self.mentions.setVisible(bool(mentions))


class GraphView(QGraphicsView):
    note_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setAccessibleName('Граф знаний: колесо — масштаб, перетаскивание — перемещение')
        self._zoom = 1.0
        self._press = None

    def set_graph(self, graph, active_id=None):
        self.scene().clear()
        nodes = graph.get('nodes', [])
        positions = {}
        radius = max(100, min(600, len(nodes) * 18))
        for index, node in enumerate(nodes):
            angle = 2 * math.pi * index / max(1, len(nodes))
            positions[node['id']] = (radius * math.cos(angle), radius * math.sin(angle))
        for edge in graph.get('edges', []):
            source, target = positions.get(edge['source_id']), positions.get(edge['target_id'])
            if source and target:
                self.scene().addLine(*source, *target, QPen(QColor('#b3b9c2')))
        for node in nodes:
            x, y = positions[node['id']]
            circle = self.scene().addEllipse(x-12, y-12, 24, 24, QPen(Qt.NoPen), QBrush(QColor('#ff890b' if node['id'] == active_id else '#647d96')))
            circle.setData(0, node['id'])
            circle.setToolTip(node['title'] or 'Без названия')
            label = self.scene().addText((node['title'] or 'Без названия')[:36])
            label.setPos(x+15, y-12)
            label.setData(0, node['id'])
            label.setToolTip(node['title'] or 'Без названия')
        if nodes:
            self.fitInView(self.scene().itemsBoundingRect().adjusted(-30,-30,30,30), Qt.KeepAspectRatio)
        self._zoom = 1.0

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        if .2 <= self._zoom * factor <= 8:
            self.scale(factor, factor)
            self._zoom *= factor
        event.accept()

    def mousePressEvent(self, event):
        self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        point = event.position().toPoint()
        if self._press is not None and (point-self._press).manhattanLength() < 5:
            item = self.itemAt(point)
            if item is not None and item.data(0) is not None:
                self.note_requested.emit(item.data(0))
        self._press = None
        super().mouseReleaseEvent(event)


class WikiLinkCompleter(QObject):
    """Completes only workspace titles supplied by the asynchronous reader."""
    note_requested = Signal(int)
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.completer = QCompleter([], self)
        self._model = QStringListModel(self.completer)
        self.completer.setModel(self._model)
        self.completer.setWidget(editor)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.activated.connect(self.insert_title)
        editor.textChanged.connect(self.update_popup)
        editor.cursorPositionChanged.connect(self.update_popup)
        self.start = None
        self._notes = {}
        editor.viewport().installEventFilter(self)
        editor.setToolTip('Связь с заметкой: [[Название]] или [[Название|Подпись]]. Ctrl+нажатие — открыть.')

    def set_notes(self, rows):
        self._notes = {}
        for row in rows:
            if row.get('title'):
                self._notes.setdefault(row['title'].strip().casefold(), []).append(row['id'])
        self._model.setStringList(sorted({row['title'] for row in rows if row.get('title')}))
        self.completer.popup().hide()
        self.start = None

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonRelease and event.modifiers() & Qt.ControlModifier:
            position = self.editor.cursorForPosition(event.position().toPoint()).position()
            for match in re.finditer(r'\[\[([^\[\]\n]+)\]\]', self.editor.toPlainText()):
                if match.start() <= position < match.end():
                    ids = self._notes.get(match.group(1).split('|', 1)[0].strip().casefold(), [])
                    if len(ids) == 1:
                        self.note_requested.emit(ids[0])
                        return True
        return super().eventFilter(watched, event)

    def update_popup(self):
        cursor = self.editor.textCursor()
        before = self.editor.toPlainText()[:cursor.position()]
        match = re.search(r'\[\[([^\[\]\n|]*)$', before)
        if self.editor.isReadOnly() or not match:
            self.completer.popup().hide()
            self.start = None
            return
        self.start = match.start()
        self.completer.setCompletionPrefix(match.group(1))
        self.completer.complete(self.editor.cursorRect())

    def insert_title(self, title):
        if self.start is None or self.editor.isReadOnly():
            return
        cursor = self.editor.textCursor()
        end = cursor.position()
        cursor.setPosition(self.start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText('[[' + title + ']]')
        self.editor.setTextCursor(cursor)
        self.completer.popup().hide()

