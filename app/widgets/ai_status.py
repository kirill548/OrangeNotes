"""Small, passive AI diagnostics view; collection belongs to background services."""
import math

from PySide6.QtCore import Signal, QVariantAnimation, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QPushButton, QMenu


class AIStatusStatusBarWidget(QPushButton):
    settings_requested = Signal()
    COLORS = {'idle': '#38a169', 'active': '#3182ce',
              'grounding': '#d69e2e', 'disabled': '#929aa5'}
    LABELS = {'idle': 'Готов', 'active': 'Генерация',
              'grounding': 'Проверка фактов', 'disabled': 'Отключён'}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('aiStatusWidget')
        self.setAccessibleName('Состояние ИИ')
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('QPushButton#aiStatusWidget {background:transparent;'
                           'border:0;padding:4px 8px 4px 21px;color:#536174;}'
                           'QPushButton#aiStatusWidget:hover {background:#fff0dc;'
                           'border-radius:6px;}')
        self._color = QColor(self.COLORS['disabled'])
        self._transition = QVariantAnimation(self)
        self._transition.setDuration(140)
        self._transition.valueChanged.connect(self._change_color)
        self.clicked.connect(self._open_menu)
        self.update_metrics({'state': 'disabled'})

    def _change_color(self, color):
        self._color = color
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(7, (self.height()-8)//2, 8, 8)

    @staticmethod
    def _number(value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if math.isfinite(value) and value >= 0:
                return value
        return None

    def update_metrics(self, metrics):
        self.metrics = dict(metrics)
        self.state = metrics.get('state', 'disabled')
        if self.state not in self.COLORS:
            self.state = 'disabled'
        self.setProperty('aiState', self.state)
        target = QColor(self.COLORS[self.state])
        if target != self._color:
            self._transition.stop()
            self._transition.setStartValue(self._color)
            self._transition.setEndValue(target)
            self._transition.start()
        mode = metrics.get('mode', 'unknown')
        mode_text = {'cpu': 'CPU Mode', 'gpu': 'GPU', 'unknown': 'N/A'}.get(mode, 'N/A')
        latency = self._number(metrics.get('latency_p50_ms'))
        ttft = self._number(metrics.get('ttft_p50_ms'))
        utilization = self._number(metrics.get('gpu_utilization_pct'))
        used = self._number(metrics.get('vram_used_bytes'))
        total = self._number(metrics.get('vram_total_bytes'))
        def milliseconds(value):
            return f'{value:.0f} мс' if value is not None else 'N/A'
        text = f'ИИ · {self.LABELS[self.state]} · {mode_text}'
        if latency is not None:
            text += f' · {milliseconds(latency)}'
        if mode == 'gpu':
            text += f' · {utilization:.0f}%' if utilization is not None and utilization <= 100 else ' · N/A'
            if used is not None:text += f' · {used / 1024**3:.1f} ГиБ'
        self.setText(text)
        memory = f'{used / 1024**2:.0f} МиБ' if used is not None else 'N/A'
        if total is not None and used is not None:
            memory += f' / {total / 1024**2:.0f} МиБ'
        gpu = f'{utilization:.0f}%' if utilization is not None and utilization <= 100 else 'N/A'
        self.setToolTip('\n'.join([
            f'Модель: {metrics.get("model") or "N/A"}',
            f'Состояние: {self.LABELS[self.state]}', f'Режим: {mode_text}',
            f'Задержка p50 (завершённые запросы): {milliseconds(latency)}',
            f'Первый токен p50: {milliseconds(ttft)}',
            f'Загрузка GPU: {gpu}', f'VRAM: {memory}',
            f'Выборка: {metrics.get("sample_count", 0)} из 20 завершённых запросов',
            f'Источник GPU: {metrics.get("provider") or "N/A"}',
            str(metrics.get('detail') or ''),
            'GPU — общесистемные измерения, а не доказательство режима модели.',
            'Нажмите для перехода к настройкам ИИ.']))

    def _open_menu(self):
        menu = QMenu(self)
        menu.addAction('Настройки ИИ', self.settings_requested.emit)
        menu.setAttribute(Qt.WA_DeleteOnClose)
        menu.popup(self.mapToGlobal(self.rect().topLeft()))
