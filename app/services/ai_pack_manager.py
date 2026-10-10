"""Model operations confined to the application's explicitly owned store."""
from dataclasses import asdict
from pathlib import Path
import shutil
import threading
import time

from app.services.ai_pack_storage import AIPackStorage
from app.services.local_ai import OllamaClient, LocalAIError, LocalAICancelled

_LOCKS = {}
_LOCK_GUARD = threading.Lock()


class AIPackManager:
    available_models = [
        {'name': 'qwen3:4b', 'download_bytes': 3*1024**3, 'ram_bytes': 6*1024**3, 'ram_gb': 6},
        {'name': 'qwen3:8b', 'download_bytes': 6*1024**3, 'ram_bytes': 10*1024**3, 'ram_gb': 10},
        {'name': 'llama3.2:3b', 'download_bytes': 3*1024**3, 'ram_bytes': 5*1024**3, 'ram_gb': 5},
        {'name': 'qwen3-embedding:0.6b', 'download_bytes': 1024**3, 'ram_bytes': 2*1024**3, 'ram_gb': 2},
    ]

    def __init__(self, root, config=None, runtime=None):
        self.root = Path(root).absolute()
        self.config = dict(config or {})
        self.storage = AIPackStorage(self.root/'models')
        if runtime is None:
            from app.services.ai_pack_runtime import ManagedPackRuntime
            runtime = ManagedPackRuntime(self.root, self.config.get('runtime_root') or None)
        self.runtime = runtime
        with _LOCK_GUARD:
            self._lock = _LOCKS.setdefault(str(self.root.resolve()), threading.Lock())

    @staticmethod
    def _cancel(event):
        if event is not None and event.is_set():
            raise LocalAICancelled('Операция отменена.')

    def _name(self, name):
        model, tag = self.storage._name(name)
        OllamaClient._local_model(name)
        return model + ':' + tag

    def scan(self):
        if not self.storage.root.exists():
            return []
        with self._lock:
            entries = self.storage.scan()
            estimates = {item['name']: item for item in self.available_models}
            return [{**asdict(item), 'managed': True,
                     'active': bool(self.config.get('managed_pack')) and item.name == self.config.get('model'),
                     'ram_gb': estimates.get(item.name, {}).get('ram_gb')}
                    for item in entries]

    def download(self, model, cancel_event=None, progress=None):
        from app.services.ai_onboarding import _pull
        name = self._name(model)
        self._cancel(cancel_event)
        with self._lock:
            self.storage.initialize()
            estimate = next((item['download_bytes'] for item in self.available_models if item['name']==name), None)
            if estimate and shutil.disk_usage(self.storage.root).free < estimate:
                raise LocalAIError('Недостаточно свободного места для модели (размер оценочный).')
            snapshot = self.storage.begin_operation()
            writer_owned=False
            try:
                base_url = self.runtime.ensure(cancel_event)
                verify = getattr(self.runtime, 'verify_owned', None)
                if verify: verify()
                writer_owned=True
                client = OllamaClient(base_url, name)
                _pull(client, name, cancel_event, progress, time.monotonic()+1800)
                self._cancel(cancel_event)
            except BaseException:
                # Stop the sole managed writer before claiming operation-created
                # partials. Pre-existing incomplete files are deliberately retained.
                self.runtime.stop()
                if writer_owned:self._cleanup(snapshot)
                else:self.storage.cleanup_partial(snapshot)
                raise
            self._cleanup(snapshot)
        return self.scan()

    def _cleanup(self, snapshot):
        for path in (self.storage.root/'blobs').glob('*-partial*'):
            relative = path.relative_to(self.storage.root).as_posix()
            if relative not in snapshot.existing:
                self.storage.register_partial(snapshot, relative)
        self.storage.cleanup_partial(snapshot)

    def delete(self, model):
        name = self._name(model)
        with self._lock:
            if self.config.get('managed_pack') and name == self.config.get('model'):
                raise LocalAIError('Сначала активируйте другую модель или выберите обычный поиск.')
            self.runtime.stop()
            return self.storage.delete_model(name)

    def switch_active_model(self, model, cancel_event=None):
        name = self._name(model)
        self._cancel(cancel_event)
        with self._lock:
            if name not in [item.name for item in self.storage.scan()]:
                raise LocalAIError('Сначала скачайте модель в комплект приложения.')
            old = self.config.get('model') if self.config.get('managed_pack') else None
            base_url = self.runtime.switch_active_model(old, name, cancel_event)
            self._cancel(cancel_event)
            result = {**self.config, 'provider': 'ollama', 'model': name,
                      'base_url': base_url, 'managed_pack': True}
            self.config = result
            return dict(result)
