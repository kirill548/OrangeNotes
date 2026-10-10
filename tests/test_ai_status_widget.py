"""Passive AI status presentation; synthetic metrics, no model or GPU launch."""
import unittest

from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMenu

from app.services.ai_metrics import AIMetricsCollector
from app.widgets.ai_status import AIStatusStatusBarWidget


class AIStatusWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widget = AIStatusStatusBarWidget()

    def tearDown(self):
        for menu in self.widget.findChildren(QMenu):
            menu.close()
        self.widget.close()
        self.widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def test_real_measurements_are_rendered_in_text_and_tooltip(self):
        self.widget.update_metrics(dict(state='idle', mode='gpu', model='test-model',
            latency_p50_ms=125, ttft_p50_ms=45, gpu_utilization_pct=37,
            vram_used_bytes=512 * 1024**2, vram_total_bytes=2048 * 1024**2))
        self.assertIn('Готов · GPU · 125 мс', self.widget.text())
        tooltip = self.widget.toolTip()
        for value in ('Модель: test-model', '125 мс', '45 мс',
                      'Загрузка GPU: 37%', 'VRAM: 512 МиБ / 2048 МиБ'):
            self.assertIn(value, tooltip)

    def test_cpu_and_unavailable_measurements_are_honest(self):
        self.widget.update_metrics(dict(state='idle', mode='cpu'))
        self.assertIn('CPU Mode', self.widget.text())
        for line in ('Модель: N/A', 'Первый токен p50: N/A',
                     'Загрузка GPU: N/A', 'VRAM: N/A'):
            self.assertIn(line, self.widget.toolTip())
        self.assertNotIn('0 мс', self.widget.text())
        self.widget.update_metrics(dict(state='idle', mode='unknown'))
        self.assertIn('Готов · N/A', self.widget.text())

    def test_invalid_numeric_values_are_not_presented_as_measurements(self):
        self.widget.update_metrics(dict(state='idle', mode='gpu',
            latency_p50_ms=float('nan'), ttft_p50_ms=-1,
            gpu_utilization_pct=101, vram_used_bytes=True,
            vram_total_bytes=float('inf')))
        self.assertNotIn('мс', self.widget.text())
        for line in ('Первый токен p50: N/A', 'Загрузка GPU: N/A', 'VRAM: N/A'):
            self.assertIn(line, self.widget.toolTip())

    def test_collector_drives_idle_active_grounding_idle_transition(self):
        clock = [1.0]
        collector = AIMetricsCollector(clock=lambda: clock[0])
        collector.changed.connect(self.widget.update_metrics)
        self.widget.update_metrics(collector.snapshot())
        self.assertEqual(self.widget.property('aiState'), 'idle')
        collector.begin('request', 'test-model')
        self.assertEqual(self.widget.property('aiState'), 'active')
        self.assertIn('Генерация', self.widget.text())
        collector.grounding('request')
        self.assertEqual(self.widget.property('aiState'), 'grounding')
        self.assertIn('Проверка фактов', self.widget.text())
        clock[0] = 1.25
        finished = QSignalSpy(self.widget._transition.finished)
        collector.complete('request')
        self.assertEqual(self.widget.property('aiState'), 'idle')
        self.assertIn('250 мс', self.widget.text())
        # Wait for the animation's final frame, including on slower CI event loops.
        self.assertTrue(finished.count() or finished.wait(1000),
                        'Idle color animation did not finish within one second')
        self.assertEqual(self.widget._transition.state(), QAbstractAnimation.Stopped)
        self.assertEqual(self.widget._color.name(), self.widget.COLORS['idle'])

    def test_click_opens_settings_menu_and_action_emits_signal(self):
        requested = []
        self.widget.settings_requested.connect(lambda: requested.append(True))
        self.widget.show()
        self.app.processEvents()
        QTest.mouseClick(self.widget, Qt.LeftButton)
        self.app.processEvents()
        menus = self.widget.findChildren(QMenu)
        self.assertEqual(len(menus), 1)
        self.assertTrue(menus[0].isVisible())
        actions = menus[0].actions()
        self.assertEqual([action.text() for action in actions], ['Настройки ИИ'])
        self.assertEqual(requested, [])
        actions[0].trigger()
        self.assertEqual(requested, [True])


if __name__ == '__main__':
    unittest.main()
