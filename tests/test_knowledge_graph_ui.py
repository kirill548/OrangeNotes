"""Knowledge widgets use isolated data and never launch a user database."""
import unittest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QTextEdit
from app.widgets.knowledge_graph import BacklinksPanel, GraphView, WikiLinkCompleter


class KnowledgeGraphUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_backlinks_navigation_and_empty_state(self):
        panel = BacklinksPanel()
        spy = QSignalSpy(panel.note_requested)
        panel.update_links([{'id': 7, 'title': 'План', 'context_snippet': 'См. [[Проект]]'}])
        panel._open(panel.links.item(0))
        self.assertEqual(spy.at(0), [7])
        panel.update_links([])
        panel._open(panel.links.item(0))
        self.assertEqual(spy.count(), 1)
        panel.close()

    def test_graph_nodes_edges_and_navigation(self):
        view = GraphView()
        view.resize(600, 400)
        view.show()
        view.set_graph({'nodes': [{'id': 1, 'title': 'Проект'}, {'id': 2, 'title': 'План'}],
                        'edges': [{'source_id': 1, 'target_id': 2}]}, 1)
        self.app.processEvents()
        spy = QSignalSpy(view.note_requested)
        node = next(item for item in view.scene().items() if item.data(0) == 1 and hasattr(item, 'brush'))
        self.assertEqual(node.brush().color().name(), '#ff890b')
        point = view.mapFromScene(node.sceneBoundingRect().center())
        QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=point)
        self.assertEqual(spy.at(0), [1])
        view.close()

    def test_completion_replaces_only_open_link(self):
        editor = QTextEdit()
        completer = WikiLinkCompleter(editor)
        completer.set_notes([{'id': 1, 'title': 'Проект'}])
        editor.setPlainText('См. [[Про')
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        editor.setTextCursor(cursor)
        completer.update_popup()
        completer.insert_title('Проект')
        self.assertEqual(editor.toPlainText(), 'См. [[Проект]]')
        editor.close()

    def test_completion_workspace_replacement_drops_old_titles(self):
        editor = QTextEdit()
        completer = WikiLinkCompleter(editor)
        completer.set_notes([{'id': 1, 'title': 'Личное'}])
        completer.set_notes([{'id': 2, 'title': 'Рабочее'}])
        self.assertEqual(completer.completer.model().stringList(), ['Рабочее'])
        self.assertNotIn('личное', completer._notes)
        editor.close()
