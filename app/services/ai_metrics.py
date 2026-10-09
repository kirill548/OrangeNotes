"""Request-scoped AI telemetry with a bounded, successful-request window."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from statistics import median
import time
from typing import Callable

from PySide6.QtCore import QObject, Signal


@dataclass
class _Request:
    started: float
    model: str
    first_token_at: float | None = None
    grounding: bool = False


class AIMetricsCollector(QObject):
    changed = Signal(object)

    def __init__(self, parent=None, *, clock: Callable[[], float] = time.monotonic):
        super().__init__(parent)
        self._clock = clock
        self._enabled = True
        self._requests: dict[str, _Request] = {}
        self._samples: deque[tuple[float, float | None]] = deque(maxlen=20)
        self._model: str | None = None
        self._hardware: dict = {}

    @staticmethod
    def _valid(value: float) -> bool:
        return math.isfinite(value) and value >= 0

    def _now(self, at=None) -> float | None:
        try:
            value = float(self._clock() if at is None else at)
        except (ValueError, TypeError, OverflowError):
            return None
        return value if self._valid(value) else None

    def snapshot(self) -> dict:
        latencies = [sample[0] for sample in self._samples]
        ttfts = [sample[1] for sample in self._samples if sample[1] is not None]
        state = "disabled" if not self._enabled else (
            "grounding" if any(r.grounding for r in self._requests.values()) else
            "active" if self._requests else "idle"
        )
        return {
            **self._hardware,
            "enabled": self._enabled,
            "state": state,
            "model": self._model,
            "active_count": len(self._requests),
            "sample_count": len(self._samples),
            "latency_p50_ms": median(latencies) if latencies else None,
            "ttft_p50_ms": median(ttfts) if ttfts else None,
        }

    def _emit(self):
        self.changed.emit(self.snapshot())

    def begin(self, request_id: str, model: str, at=None):
        if not self._enabled or request_id in self._requests:
            return
        now = self._now(at)
        if now is None:
            return
        self._requests[request_id] = _Request(now, model)
        self._model = model
        self._emit()

    def first_token(self, request_id: str, at=None):
        request = self._requests.get(request_id)
        if request is None or request.first_token_at is not None:
            return
        now = self._now(at)
        if now is None or now < request.started:
            return
        request.first_token_at = now
        request.grounding = False
        self._emit()

    def grounding(self, request_id: str):
        request = self._requests.get(request_id)
        if request is not None and not request.grounding:
            request.grounding = True
            self._emit()

    def complete(self, request_id: str, success: bool = True, at=None):
        request = self._requests.pop(request_id, None)
        if request is None:
            return
        now = self._now(at)
        if success and now is not None:
            latency = (now - request.started) * 1000
            ttft = None if request.first_token_at is None else (
                request.first_token_at - request.started
            ) * 1000
            if self._valid(latency) and (ttft is None or self._valid(ttft) and ttft <= latency):
                self._samples.append((latency, ttft))
        self._emit()

    def abort(self, request_id: str):
        self.complete(request_id, success=False)

    def set_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if not enabled:
            self._requests.clear()
        self._emit()

    def set_model(self, model):
        self._model = str(model) if model else None
        self._emit()

    def update_hardware(self, hardware: dict):
        self._hardware = dict(hardware)
        self._emit()
