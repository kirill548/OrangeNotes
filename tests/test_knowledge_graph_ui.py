"""Knowledge widgets use isolated data and never launch a user database."""
import unittest
import math
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QTextEdit
from app.widgets.knowledge_graph import BacklinksPanel, GraphView, WikiLinkCompleter
from app.services.graph_layout import force_layout


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

    def test_force_layout_is_deterministic_bounded_and_non_circular(self):
        ids = list(range(60))
        edges = [(i, i + 1) for i in range(59)]
        positions = force_layout(ids, edges, iterations=12)
        self.assertEqual(positions, force_layout(ids, edges, iterations=12))
        bound = max(200, math.sqrt(len(ids)) * 55)
        self.assertTrue(all(math.isfinite(value) and abs(value) <= bound
                            for point in positions.values() for value in point))
        radii = {round(math.hypot(*point)) for point in positions.values()}
        self.assertGreater(len(radii), 10)

    def test_force_layout_cancel_and_empty(self):
        self.assertEqual(force_layout([], []), {})
        initial = {1: (4., 5.), 2: (9., 10.)}
        self.assertEqual(force_layout([1, 2], [(1, 999)], initial, cancelled=lambda: True), initial)

    def test_filter_keeps_positions_and_ignores_stale_layout(self):
        view = GraphView()
        graph = {'nodes': [{'id': i, 'title': str(i), 'workspace_id': 1} for i in (1, 2)], 'edges': []}
        view.set_graph(graph, 1)
        before = view._positions[1]
        old_generation = view._generation
        view.set_graph({'nodes': graph['nodes'][:1], 'edges': []}, 1)
        self.assertEqual(view._positions[1], before)
        view._layout_ready(old_generation, {1: (999, 999)})
        self.assertEqual(view._targets, {})
        self.assertLessEqual(len(view._workers), 1)
        view.close()

    def test_animation_converges_and_close_stops_workers(self):
        view = GraphView()
        view.show()
        view.set_graph({'nodes': [{'id': 1, 'title': 'A'}], 'edges': []})
        view.stop_layout()
        view._targets = {1: (20., 30.)}
        for _ in range(100):
            view._animate()
        self.assertEqual(view._positions[1], (20., 30.))
        view._timer.start()
        view.close()
        self.assertFalse(view._timer.isActive())
        self.assertTrue(all(not worker.isRunning() for worker in view._workers))

    def test_active_change_does_not_restart_layout_and_hide_stops_animation(self):
        view = GraphView()
        view.show()
        graph = {'nodes': [{'id': i, 'title': str(i)} for i in (1, 2)], 'edges': []}
        view.set_graph(graph, 1)
        generation = view._generation
        positions = view._positions.copy()
        view.set_graph(graph, 2)
        self.assertEqual(view._generation, generation)
        self.assertEqual(view._positions, positions)
        self.assertEqual(view._node_items[2][0].brush().color().name(), '#ff890b')
        view._timer.start()
        view.hide()
        self.assertFalse(view._timer.isActive())
        view.close()

    def test_force_layout_separates_coincident_nodes_and_caps_iterations(self):
        ids = [1, 2, 3]
        initial = {key: (0., 0.) for key in ids}
        result = force_layout(ids, [], initial, iterations=5)
        self.assertEqual(len(set(result.values())), 3)
        self.assertEqual(force_layout(ids, [], initial, iterations=100),
                         force_layout(ids, [], initial, iterations=1000))
        single = force_layout([1], [], {1: (100., 0.)}, iterations=3)
        self.assertLess(abs(single[1][0]), 100)
