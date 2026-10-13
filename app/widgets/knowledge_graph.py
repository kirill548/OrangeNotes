"""Workspace-scoped knowledge UI; SQLite reads and indexing run off the GUI thread."""
import math
import re
from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt, QEvent, QStringListModel, QTimer
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


class GraphLayoutWorker(QThread):
    ready = Signal(int, object)

    def __init__(self, generation, ids, edges, initial, parent):
        super().__init__(parent)
        self.generation, self.ids, self.edges, self.initial = generation, ids, edges, initial

    def run(self):
        from app.services.graph_layout import force_layout
        result = force_layout(self.ids, self.edges, self.initial,
                              cancelled=self.isInterruptionRequested)
        if not self.isInterruptionRequested():
            self.ready.emit(self.generation, result)


class GraphView(QGraphicsView):
    note_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setAccessibleName('Граф знаний: колесо — масштаб, перетаскивание — перемещение')
        self._zoom = 1.0
        self._press = None
        self._positions = {}
        self._targets = {}
        self._node_items = {}
        self._edge_items = []
        self._workers = []
        self._pending_layout = None
        self._workspace = None
        self._signature = None
        self._generation = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._animate)

    def stop_layout(self):
        """Cancel computation and animation; safe before destroying the window."""
        self._generation += 1
        self._signature = None
        self._pending_layout = None
        self._timer.stop()
        for worker in list(self._workers):
            worker.requestInterruption()
            worker.wait()

    def closeEvent(self, event):
        self.stop_layout()
        super().closeEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self._targets:
            self._timer.start()

    def set_graph(self, graph, active_id=None):
        nodes = graph.get('nodes', [])
        signature = (tuple((n['id'], n.get('title'), n.get('workspace_id')) for n in nodes),
                     tuple((e['source_id'], e['target_id']) for e in graph.get('edges', [])),
                     graph.get('workspace_id'))
        if nodes and signature == self._signature:
            for key, (circle, label) in self._node_items.items():
                circle.setBrush(QBrush(QColor('#ff890b' if key == active_id else '#647d96')))
                label.setVisible(len(nodes) <= 200 or key == active_id)
            return
        self._signature = signature
        self._generation += 1
        self._pending_layout = None
        for worker in self._workers:
            worker.requestInterruption()
        self._timer.stop()
        self.scene().clear()
        workspace = graph.get('workspace_id', nodes[0].get('workspace_id') if nodes else self._workspace)
        if workspace != self._workspace:
            self._positions = {}
            self._workspace = workspace
        old_positions = self._positions
        positions = {}
        radius = max(100, math.sqrt(len(nodes)) * 35)
        for index, node in enumerate(nodes):
            angle = index * 2.399963229728653
            r = math.sqrt((index + 1) / max(1, len(nodes))) * radius
            positions[node['id']] = old_positions.get(node['id'], (r * math.cos(angle), r * math.sin(angle)))
        self._positions = positions
        # Fixed simulation bounds keep scrollbars from moving as nodes settle.
        extent = max(200, math.sqrt(len(nodes)) * 55) + 260
        self.scene().setSceneRect(-extent, -extent, extent * 2, extent * 2)
        self._targets = {}
        self._node_items = {}
        self._edge_items = []
        for edge in graph.get('edges', []):
            source, target = positions.get(edge['source_id']), positions.get(edge['target_id'])
            if source and target:
                line = self.scene().addLine(*source, *target, QPen(QColor('#b3b9c2')))
                self._edge_items.append((line, edge['source_id'], edge['target_id']))
        for node in nodes:
            x, y = positions[node['id']]
            circle = self.scene().addEllipse(-12, -12, 24, 24, QPen(Qt.NoPen), QBrush(QColor('#ff890b' if node['id'] == active_id else '#647d96')))
            circle.setPos(x, y)
            circle.setData(0, node['id'])
            circle.setToolTip(node['title'] or 'Без названия')
            label = self.scene().addText((node['title'] or 'Без названия')[:36])
            label.setPos(x+15, y-12)
            label.setData(0, node['id'])
            label.setToolTip(node['title'] or 'Без названия')
            # Large graphs keep labels in tooltips to avoid thousands of text paints.
            label.setVisible(len(nodes) <= 200 or node['id'] == active_id)
            self._node_items[node['id']] = (circle, label)
        if nodes and not old_positions:
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
            self._zoom = 1.0
        if nodes:
            self._pending_layout = (self._generation, list(positions),
                [(e['source_id'], e['target_id']) for e in graph.get('edges', [])], positions.copy())
            self._start_pending()

    def _start_pending(self):
        if self._workers or self._pending_layout is None:
            return
        worker = GraphLayoutWorker(*self._pending_layout, self)
        self._pending_layout = None
        self._workers.append(worker)
        worker.ready.connect(self._layout_ready)
        worker.finished.connect(self._worker_finished)
        worker.start()

    @Slot()
    def _worker_finished(self):
        worker = self.sender()
        if worker in self._workers:
            self._workers.remove(worker)
        worker.deleteLater()
        self._start_pending()

    @Slot(int, object)
    def _layout_ready(self, generation, positions):
        if generation != self._generation:
            return
        self._targets = positions
        if self.isVisible():
            self._timer.start()

    def _animate(self):
        moving = False
        for key, target in self._targets.items():
            if key not in self._positions:
                continue
            x, y = self._positions[key]
            dx, dy = target[0] - x, target[1] - y
            if abs(dx) + abs(dy) > .2:
                moving = True
                x, y = x + dx * .18, y + dy * .18
            else:
                x, y = target
            self._positions[key] = (x, y)
            circle, label = self._node_items[key]
            circle.setPos(x, y)
            label.setPos(x + 15, y - 12)
        for line, source, target in self._edge_items:
            line.setLine(*self._positions[source], *self._positions[target])
        if not moving:
            self._timer.stop()

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

