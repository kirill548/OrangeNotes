"""Low-frequency hardware sampling outside the application's GUI thread."""
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

_WORKERS = set()


class _HardwareThread(QThread):
    sampled = Signal(object)

    def run(self):
        from app.services.ai_hardware import collect_hardware
        try:
            sample = collect_hardware()
        except Exception:
            sample = {'mode': 'unknown', 'detail': 'Hardware telemetry unavailable'}
        if not self.isInterruptionRequested():
            self.sampled.emit(sample)


class AIHardwareMonitor(QObject):
    def __init__(self, collector, parent=None, *, interval_ms=15000):
        super().__init__(parent)
        self.collector = collector
        self._worker = None
        self._stopped = False
        self.timer = QTimer(self)
        self.timer.setInterval(max(10000, interval_ms))
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    @Slot()
    def poll(self):
        if self._stopped or self._worker is not None:
            return
        if not self.collector.snapshot().get('enabled', False):
            return
        worker = _HardwareThread()
        self._worker = worker
        _WORKERS.add(worker)
        worker.sampled.connect(self.collector.update_hardware)
        worker.finished.connect(self._finished)
        # A retained, parentless worker cannot be destroyed with its window
        # while a bounded external probe is still returning.
        worker.finished.connect(lambda: _WORKERS.discard(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    @Slot()
    def _finished(self):
        self._worker = None

    @Slot()
    def stop(self):
        self._stopped = True
        self.timer.stop()
        if self._worker is not None:
            self._worker.requestInterruption()
            self._worker.wait(1500)
